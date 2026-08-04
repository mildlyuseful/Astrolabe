# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Orchestrator: wires config + output engine + BLE thread + tray + settings window +
the nav transports (broker / SolidWorks / Onshape / AutoCAD loader).

Threading model (Windows):
  * main thread       -> hidden Tk root + mainloop (owns all GUI; window shown/hidden on demand)
  * tray thread       -> pystray icon loop (menu callbacks marshalled to Tk via root.after)
  * ble thread        -> asyncio BLE loop and packet-boundary focus routing
  * usb thread        -> vendor-HID hotplug, ownership keepalive, and packet routing
  * broker threads    -> NavBroker accept + sender (streams frames to the socket add-ons)
  * SolidWorks worker -> its own CoInitialize'd COM thread (daemon-side direct driver)
  * AutoCAD worker    -> its own CoInitialize'd COM thread (plugin staging/NETLOAD only)
  * Onshape threads   -> TLS accept + per-connection WAMP reader + nav worker (daemon-side bridge)
  * debug thread      -> optional pygame cube (--debug)
The process stays alive on the Tk mainloop and exits only when tray -> Quit tears it down.
"""
import copy
import threading
import tkinter as tk

from . import integrations
from .app_registry import APP_SPECS, APP_SPECS_BY_ID, resolve_foreground_context
from .autocad_driver import AutoCADPluginLoader
from .ble import start_ble_thread
from .commands import SerializedCommandQueue, SetFocusedContext
from .control_hud import ControlHUD
from .config import (compose_advanced_with_host_baseline, host_baseline_payload,
                     normalize_orbit_pivot_fallbacks, orbit_pivot_candidates)
from .config_store import ConfigStore
from .devices import (
    DeviceAdapterRegistry,
    MotionSample,
    SnapshotInputProvider,
    builtin_device_descriptors,
    start_usb_thread,
)
from .input import (
    BindingController,
    InputAggregator,
    compile_binding_profile,
    compose_binding_profile,
    load_system_binding_profiles,
)
from .input.windows_raw_input import WindowsRawInputProvider
from .navbroker import NavBroker
from .navigation_router import NavigationEnvelope, NavigationRouter
from .onshape_bridge import OnshapeBridge
from .output import OutputEngine
from .paths import publish_bridge_port
from .solidworks_driver import SolidWorksDriver
from .runtime_state import ConfigRuntimeBaseResolver, FocusedContext, RuntimeStore
from .service_health import ServiceHealth, ServiceHealthState
from .settings_schema import SETTING_SPECS
from .tray import TrayController, migrate_startup_entry
from .ui import SettingsWindow
from .util import get_logger
from .winfocus import ForegroundMonitor, foreground_process_name
from .windows_pointer import SendInputPointerButtonSink


_NO_PACKET_APP = object()
_NO_PACKET_REVISION = object()
_NO_BATTERY_LEVEL = object()


def _set_nested(container, path, value):
    """Set one already-shaped dict/list path in a detached materialized profile."""
    current = container
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = copy.deepcopy(value)


class App:
    # Class-level default so battery/power helpers stay readable. Tests build bare instances
    # via App.__new__(App) and read these before __init__ assigns them.
    _external_power = False

    def __init__(self, debug=False):
        self.debug = debug
        self.log = get_logger()
        self.config = ConfigStore().load()
        self.first_run = self.config.first_run
        self.runtime = RuntimeStore(ConfigRuntimeBaseResolver(self.config))
        self.commands = SerializedCommandQueue(self.runtime)
        self.engine = OutputEngine(self.config, self.runtime, self.commands)
        self.input_aggregator = InputAggregator()
        self.keyboard_provider = WindowsRawInputProvider(
            self.input_aggregator.accept_many, self.input_aggregator.update_health)
        self.input_aggregator.register_provider(self.keyboard_provider)
        self.device_descriptors = builtin_device_descriptors()
        self.ble_input_providers = {}
        for descriptor in self.device_descriptors:
            provider = SnapshotInputProvider(
                descriptor,
                self.input_aggregator.accept_many,
                self.input_aggregator.update_health,
            )
            self.input_aggregator.register_provider(provider)
            self.ble_input_providers[descriptor.source_id] = provider
        self.device_adapters = DeviceAdapterRegistry(
            self.device_descriptors, self.ble_input_providers)
        self.binding_catalog = load_system_binding_profiles()
        self.pointer_button_output = SendInputPointerButtonSink()
        self.binding_controller = BindingController(
            self._compiled_binding_profile(), self.commands, self.runtime,
            config_store=self.config, pointer_sink=self.pointer_button_output)
        self._binding_context = self.runtime.snapshot().focused_context
        self.input_aggregator.add_listener(self.binding_controller.handle_transition)
        self.runtime.add_listener(self._on_runtime_state_changed)
        self._configure_binding_controls()
        self.stop_event = threading.Event()
        self.ble_enabled_event = threading.Event()
        self.ble_enabled_event.set()
        self.transport_handover_lock = threading.RLock()
        self._status = "starting"
        self._battery_state = (None, False)
        self._external_power = False
        self._last_pushed = None
        self._last_battery_pushed = _NO_BATTERY_LEVEL
        self._health_lock = threading.Lock()
        self.service_health = {}
        self._last_health_pushed = None
        self.root = None
        self.ui = None
        self.hud = None
        self.tray = None
        self.broker = None
        self.sw_driver = None
        self.onshape_bridge = None
        self.acad_loader = None
        self.config.add_listener(self.on_config_changed)

        # 3D-app nav bridge (127.0.0.1). The engine forwards orbit/pan/zoom deltas here,
        # gated on the focused CAD app; socket add-ons connect and drive their app's camera.
        snapshot = self.config.snapshot()
        self.broker = NavBroker(
            snapshot.bridge_port,
            self._on_clients_changed,
            rate_hz=snapshot.global_value("navigation.refresh_rate"),
            on_health_changed=self._on_service_health_changed,
        )
        # SolidWorks is driven by external COM automation, not a socket add-in: this daemon-side
        # driver attaches to a running SolidWorks and moves its camera directly. It lives parallel
        # to the broker; NavigationRouter owns delivery to this direct transport.
        self.sw_driver = SolidWorksDriver(
            self._on_sw_connection_changed,
            rate_hz=snapshot.global_value("navigation.refresh_rate"),
            on_health_changed=self._on_service_health_changed,
        )
        # Onshape (browser) is driven by a daemon-side bridge that impersonates the 3Dconnexion
        # local NL-Proxy service Onshape's page connects to (TLS WebSocket on 127.51.68.120:8181).
        # Like the SW driver it lives parallel to the broker; NavigationRouter owns delivery.
        ocfg = snapshot.onshape
        self.onshape_bridge = OnshapeBridge(
            self._on_onshape_connection_changed,
            on_focus_changed=self._on_onshape_focus_changed,
            rate_hz=snapshot.global_value("navigation.refresh_rate"),
            cert_path=ocfg.get("cert_path") or None,
            key_path=ocfg.get("key_path") or None,
            on_health_changed=self._on_service_health_changed,
        )
        self.navigation = NavigationRouter(
            self.broker, self.sw_driver, self.onshape_bridge)
        # AutoCAD is a BROKER app: its compiled NETLOAD plugin (plugin_src/autocad) drives the
        # live GraphicsSystem view in-process and connects to the nav broker like the other
        # socket add-ons. COM is used only to stage, trust, and NETLOAD the bundled plugin into a
        # running AutoCAD (the old COM nav transport is archived at
        # archive/autocad_com_transport/; it raced the plugin for the early frames).
        self.acad_loader = AutoCADPluginLoader(
            on_health_changed=self._on_service_health_changed)
        self.engine.nav_sink = self._nav_sink
        # Connected-app status feeds the tray "Apps:" line + the 3D-Apps rows. It merges the
        # broker's socket add-ons with the SolidWorks COM driver and the Onshape bridge states.
        self._apps_lock = threading.Lock()
        self._broker_apps = []                     # [(app, version, pid)] from the broker
        self._sw_apps = []                         # [(app, version, pid)] from the SW driver (0/1)
        self._onshape_apps = []                    # focused Onshape viewport, if any
        self._onshape_connection_version = None    # subscribed transport, including background tabs
        self.connected_apps = []
        # The handshake version belongs to the copy actually loaded by the current/most recently
        # connected host document. Preserve it after disconnect so update UX does not fall back to
        # an unrelated "primary" install destination.
        self.observed_addin_versions = {}
        self._last_apps_pushed = None
        self._last_hud_visible = None
        self._engine_app = None
        self._packet_app_key = _NO_PACKET_APP
        self._packet_state_revision = _NO_PACKET_REVISION
        self._last_scheme_pushed = {}
        self._last_runtime_rate = {}
        self._quit_started = False
        self._shutdown_complete = False
        self.foreground_monitor = ForegroundMonitor(self._on_foreground_process_changed)
        self._apply_rates()
        self._apply_schemes()

    # --- Device wiring and packet-boundary routing --------------------------------
    def get_ble_params(self):
        snapshot = self.config.snapshot()
        return (snapshot.device_value("device.name"),
                snapshot.device_value("device.address"), snapshot.device_char_uuid)

    def set_status(self, text):
        # Called from transport threads; just store + log. The Tk poll pushes it to the GUI.
        self._status = text
        if not text.startswith("subscribed"):
            self._battery_state = (self._battery_state[0], False)
        self.log.info(text)

    def status_text(self):
        return self._status

    def is_connected(self):
        return self._status.startswith(("connected", "subscribed"))

    def set_battery_level(self, level):
        if level is not None and (type(level) is not int or not 0 <= level <= 100):
            raise ValueError("BLE battery level must be an integer from 0 to 100 or None")
        state = (level, level is not None and not self._external_power)
        if state == self._battery_state:
            return
        self._battery_state = state
        self.log.info(
            "BLE battery: %s", "unavailable" if level is None else f"{level}%")

    def battery_level(self):
        return self._battery_state[0]

    def battery_status_text(self):
        level, current = self._battery_state
        if self._external_power:
            if level is None:
                return "Power: USB (Battery unavailable)"
            return f"Power: USB (Battery: {level}% last known)"
        if level is None:
            return "Battery: unavailable"
        suffix = "" if self.is_connected() and current else " (last known)"
        return f"Battery: {level}%{suffix}"

    def set_external_power(self, powered):
        if type(powered) is not bool:
            raise TypeError("external-power state must be boolean")
        if powered == self._external_power:
            return
        self._external_power = powered
        if powered:
            self._battery_state = (self._battery_state[0], False)
        self.log.info("device power: %s", "USB" if powered else "battery/unknown")

    def _on_service_health_changed(self, health):
        if not isinstance(health, ServiceHealth):
            raise TypeError("service health callback requires a ServiceHealth value")
        with self._health_lock:
            if self.service_health.get(health.service_id) == health:
                return
            self.service_health[health.service_id] = health
        self.log.info(
            "health %s: %s (%s)",
            health.service_id,
            health.state.value,
            health.detail,
        )

    def runtime_health_snapshot(self):
        with self._health_lock:
            return dict(self.service_health)

    def runtime_health_summary(self):
        health = tuple(self.runtime_health_snapshot().values())
        for state in (
                ServiceHealthState.FAILED,
                ServiceHealthState.DEGRADED,
                ServiceHealthState.WAITING,
                ServiceHealthState.HEALTHY):
            matching = sorted(item.service_id for item in health if item.state is state)
            if matching:
                return f"{state.value}: {', '.join(matching)}"
        return "disabled"

    def on_config_changed(self, _event):
        if (_event.changes and all(
                change.path[:1] == ("global_overrides",) and
                len(change.path) > 1 and str(change.path[1]).startswith("hud.")
                for change in _event.changes)):
            # Presentation-only changes are consumed by ControlHUD's own immutable snapshot
            # subscriber. They must not release active bindings or rebuild output transports.
            return
        bindings = getattr(self, "binding_controller", None)
        if bindings is not None:
            # No activation survives a configuration generation. Compile only after the owned
            # runtime/external resources have been released.
            bindings.release_all("binding_config_reload")
            bindings.reload(self._compiled_binding_profile())
            self._configure_binding_controls()
        self.engine.apply_config()
        self._apply_service_gates()
        self._apply_rates()
        self._apply_schemes()
        monitor = getattr(self, "foreground_monitor", None)
        if monitor is not None:
            monitor.refresh()

    def configure_keyboard_controls(self, control_ids):
        """Register only keyboard controls referenced by the compiled binding profile."""
        return self.input_aggregator.configure_provider("keyboard", control_ids)

    def configure_ble_controls(self, source_id, control_ids):
        """Register referenced controls for one stable device-descriptor namespace."""
        if source_id not in self.ble_input_providers:
            raise ValueError(f"unknown BLE input provider: {source_id}")
        return self.input_aggregator.configure_provider(source_id, control_ids)

    def _compiled_binding_profile(self):
        snapshot = self.config.snapshot()
        profile = compose_binding_profile(
            self.binding_catalog, snapshot.input_profile,
            snapshot.keybinding_overrides[snapshot.input_profile])
        return compile_binding_profile(profile, self.binding_catalog)

    def _configure_binding_controls(self):
        required = self.binding_controller.compiled_profile.required_controls
        self.configure_keyboard_controls(required.get("keyboard", ()))
        for source_id in self.ble_input_providers:
            self.configure_ble_controls(source_id, required.get(source_id, ()))

    def _on_runtime_state_changed(self, event):
        context = event.snapshot.focused_context
        if context != self._binding_context:
            self._binding_context = context
            self.binding_controller.context_changed()
        self._apply_runtime_navigation_profile(event.snapshot)

    def _service_allowed(self, key):
        """Sensitive in-process services require both successful setup and Enabled=true."""
        cfg = self.config.snapshot().app_operational[key]
        return bool(cfg.get("installed") and cfg.get("enabled"))

    def _apply_service_gates(self):
        """Keep host attachment/listener side effects behind explicit per-app setup consent."""
        if self.sw_driver is not None:
            self.sw_driver.set_enabled(self._service_allowed("solidworks"))
        if self.onshape_bridge is not None:
            self.onshape_bridge.set_enabled(self._service_allowed("onshape"))
        if self.acad_loader is not None:
            self.acad_loader.set_enabled(self._service_allowed("autocad"))

    def _effective_scheme(self, key):
        snapshot = self.config.snapshot()
        return {
            "orbit_pivot": snapshot.app_value(key, "navigation.orbit.pivot"),
            "orbit_style": snapshot.app_value(key, "navigation.orbit.style"),
            "zoom_mode": snapshot.app_value(key, "navigation.zoom.target"),
        }

    def _apply_schemes(self):
        """Publish every app profile through the shared target-aware navigation boundary."""
        navigation = getattr(self, "navigation", None)
        if navigation is None:
            return
        snapshot = self.config.snapshot()
        if not isinstance(getattr(self, "_last_scheme_pushed", None), dict):
            self._last_scheme_pushed = {}
        for spec in APP_SPECS:
            key = spec.app_id
            scheme = self._effective_scheme(key)
            fallbacks = normalize_orbit_pivot_fallbacks(
                list(snapshot.app_value(key, "navigation.orbit.pivot_fallbacks")))
            # Socket integrations consume this additive profile from their matching frames. Direct
            # transports receive the shared fields below through the same router configuration API.
            appcfg = snapshot.app_profile(key)
            adv = compose_advanced_with_host_baseline(key, appcfg.get("advanced"))
            adv["host_baseline"] = host_baseline_payload(key)
            adv["selection_overrides_pivot"] = bool(
                appcfg.get("selection_overrides_pivot", True))
            adv["orbit_hold_sec"] = appcfg.get("orbit_pivot_hold_sec", 0.5)
            adv["zoom_hold_sec"] = appcfg.get("zoom_cursor_hold_sec", 0.5)
            adv["level_horizon_on_entry"] = snapshot.app_value(
                key, "navigation.level_horizon_on_entry")
            adv["orbit_pivot_fallbacks"] = fallbacks
            adv["orbit_pivot_candidates"] = orbit_pivot_candidates(
                scheme["orbit_pivot"], fallbacks)
            navigation.set_scheme(
                key, **scheme, advanced=adv,
                selection_overrides_pivot=bool(
                    appcfg.get("selection_overrides_pivot", True)),
                orbit_pivot_fallbacks=fallbacks,
                level_horizon_on_entry=snapshot.app_value(
                    key, "navigation.level_horizon_on_entry"),
                pivot_hold_sec=appcfg.get("orbit_pivot_hold_sec", 0.5),
                zoom_hold_sec=appcfg.get("zoom_cursor_hold_sec", 0.5))
            nav = (adv or {}).get("nav_mode")
            sig = (key, scheme["orbit_pivot"], scheme["orbit_style"], scheme["zoom_mode"], nav,
                   tuple(fallbacks))
            if sig != self._last_scheme_pushed.get(key):
                self._last_scheme_pushed[key] = sig
                self.log.info("scheme -> %s: pivot=%s style=%s zoom=%s nav=%s"
                              % (key, scheme["orbit_pivot"], scheme["orbit_style"],
                                 scheme["zoom_mode"], nav))
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            self._apply_runtime_navigation_profile(runtime.snapshot())

    def _apply_runtime_navigation_profile(self, runtime_snapshot):
        """Publish the focused app's runtime settings and daemon-authoritative nav mode."""
        navigation = getattr(self, "navigation", None)
        app_id = runtime_snapshot.focused_context.app_id
        if navigation is None or app_id not in APP_SPECS_BY_ID:
            return
        app_spec = APP_SPECS_BY_ID[app_id]
        settings = runtime_snapshot.effective_settings
        config_snapshot = self.config.snapshot()
        app_profile = config_snapshot.app_profile(app_id)
        advanced = compose_advanced_with_host_baseline(app_id, app_profile.get("advanced"))
        for setting in SETTING_SPECS:
            if (setting.applies_to(app_spec) and setting.app_path[:1] == ("advanced",) and
                    setting.setting_id in settings):
                _set_nested(advanced, setting.app_path[1:], settings[setting.setting_id])
        # Runtime state, not any host-local override or persisted navigation.mode value, is the
        # authority delivered to rich add-ons.
        advanced["nav_mode"] = runtime_snapshot.effective_navigation_mode
        selection_override = settings.get(
            "navigation.orbit.selection_override", True)
        pivot_hold = settings.get("navigation.orbit.pivot_hold_seconds", 0.5)
        zoom_hold = settings.get("navigation.zoom.cursor_hold_seconds", 0.5)
        level_horizon = settings.get("navigation.level_horizon_on_entry", True)
        fallbacks = normalize_orbit_pivot_fallbacks(
            list(settings.get(
                "navigation.orbit.pivot_fallbacks",
                config_snapshot.global_value("navigation.orbit.pivot_fallbacks"))))
        orbit_pivot = settings.get(
            "navigation.orbit.pivot", config_snapshot.app_value(
                app_id, "navigation.orbit.pivot"))
        orbit_style = settings.get(
            "navigation.orbit.style", config_snapshot.app_value(
                app_id, "navigation.orbit.style"))
        zoom_mode = settings.get(
            "navigation.zoom.target", config_snapshot.app_value(
                app_id, "navigation.zoom.target"))
        advanced["host_baseline"] = host_baseline_payload(app_id)
        advanced["selection_overrides_pivot"] = bool(selection_override)
        advanced["orbit_hold_sec"] = pivot_hold
        advanced["zoom_hold_sec"] = zoom_hold
        advanced["level_horizon_on_entry"] = level_horizon
        advanced["orbit_pivot_fallbacks"] = fallbacks
        advanced["orbit_pivot_candidates"] = orbit_pivot_candidates(
            orbit_pivot, fallbacks)
        navigation.set_scheme(
            app_id, orbit_pivot=orbit_pivot, orbit_style=orbit_style,
            zoom_mode=zoom_mode, advanced=advanced,
            selection_overrides_pivot=bool(selection_override),
            orbit_pivot_fallbacks=fallbacks,
            level_horizon_on_entry=level_horizon,
            pivot_hold_sec=pivot_hold, zoom_hold_sec=zoom_hold)
        rate = settings.get(
            "navigation.refresh_rate", config_snapshot.app_value(
                app_id, "navigation.refresh_rate"))
        rates = getattr(self, "_last_runtime_rate", None)
        if rates is None:
            self._last_runtime_rate = rates = {}
        if rates.get(app_id) != rate:
            rates[app_id] = rate
            navigation.set_rate(app_id, rate)

    def _app_rate(self, key):
        """Effective viewport/flush rate (Hz) for app `key`: its per-app override, or the global
        bridge default when the per-app value is 0/unset."""
        return self.config.snapshot().app_value(key, "navigation.refresh_rate")

    def _apply_rates(self):
        """Publish every target's independent effective refresh rate."""
        navigation = getattr(self, "navigation", None)
        if navigation is not None:
            for spec in APP_SPECS:
                navigation.set_rate(spec.app_id, self._app_rate(spec.app_id))

    # --- 3D-app nav routing -------------------------------------------------------
    def _foreground_app_context(self):
        return self._foreground_context_for_process(foreground_process_name())

    def _foreground_context_for_process(self, proc):
        connected = bool(self.onshape_bridge is not None and self.onshape_bridge.is_connected())
        viewport_focused = bool(
            connected and self.onshape_bridge is not None and
            self.onshape_bridge.is_viewport_focused())
        return resolve_foreground_context(
            proc,
            onshape_connected=connected,
            onshape_viewport_focused=viewport_focused,
        )

    def _foreground_app_key(self):
        return self._foreground_app_context().app_id

    def _active_app_key(self):
        return self._active_app_key_from_context(self._foreground_app_context())

    def _active_app_key_from_context(self, context):
        key = context.app_id
        if key is None:
            return None
        appcfg = self.config.snapshot().app_operational.get(key)
        if not appcfg or not appcfg["enabled"]:
            return None
        return key

    def _publish_runtime_context(self, app_id, executable=None):
        commands = getattr(self, "commands", None)
        if commands is None:
            return
        context = FocusedContext(app_id=app_id, executable=executable)
        if self.runtime.snapshot().focused_context != context:
            commands.dispatch(SetFocusedContext(origin="foreground", context=context))

    def _apply_foreground_context(self, context):
        key = self._active_app_key_from_context(context)
        self._publish_runtime_context(context.app_id, context.process_name)
        if key is not None:
            self._activate_nav_app(key)
        self.navigation.activate(key)
        return key

    def _on_foreground_process_changed(self, process_name):
        context = self._foreground_context_for_process(process_name)
        self._apply_foreground_context(context)
        self._sync_onshape_active_row(context.app_id == "onshape")
        keyboard = getattr(self, "keyboard_provider", None)
        if keyboard is not None:
            keyboard.reconcile("foreground_change")

    def _activate_nav_app(self, key):
        """Switch mappings before transforming the focused app's next complete packet."""
        if key != self._engine_app:
            runtime = getattr(self, "runtime", None)
            if runtime is not None and runtime.snapshot().focused_context.app_id != key:
                self._publish_runtime_context(key)
            self._engine_app = key
            self.engine.set_active_bindings(key)

    def _handle_ble_packet(self, data, *, timestamp=None):
        """Choose one focused app for both mapping and routing of this complete BLE packet."""
        context = self._foreground_app_context()
        key = self._apply_foreground_context(context)
        previous = getattr(self, "_packet_app_key", _NO_PACKET_APP)
        previous_revision = getattr(self, "_packet_state_revision", _NO_PACKET_REVISION)
        self._packet_app_key = key
        runtime = getattr(self, "runtime", None)
        runtime_snapshot = runtime.snapshot() if runtime is not None else None
        self._packet_state_revision = runtime_snapshot.revision if runtime_snapshot is not None else 0
        try:
            if runtime_snapshot is not None:
                self._apply_runtime_navigation_profile(runtime_snapshot)
            if timestamp is None:
                self.engine.handle_packet(bytes(data), runtime_snapshot=runtime_snapshot)
            else:
                self.engine.handle_packet(
                    bytes(data), runtime_snapshot=runtime_snapshot, timestamp=timestamp)
        finally:
            self._packet_app_key = previous
            self._packet_state_revision = previous_revision

    def _handle_ble_motion(self, sample):
        if not isinstance(sample, MotionSample):
            raise TypeError("BLE device adapters must emit MotionSample values")
        self._handle_ble_packet(sample.payload, timestamp=sample.timestamp)

    def _nav_sink(self, ox, oy, oz, px, py, zoom):
        # Called synchronously by _handle_ble_packet after that method selected the app whose
        # mapping produced these values. Direct callers (including tests/debug helpers) fall back
        # to a fresh foreground lookup.
        key = getattr(self, "_packet_app_key", _NO_PACKET_APP)
        if key is _NO_PACKET_APP:
            key = self._active_app_key()
        if key is None:
            return
        self._activate_nav_app(key)
        self.navigation.activate(key)
        revision = getattr(self, "_packet_state_revision", _NO_PACKET_REVISION)
        if revision is _NO_PACKET_REVISION:
            runtime = getattr(self, "runtime", None)
            revision = runtime.snapshot().revision if runtime is not None else 0
        self.navigation.submit(NavigationEnvelope(
            target_app=key,
            orbit=(ox, oy, oz),
            pan=(px, py),
            zoom=zoom,
            state_revision=revision))

    def _on_clients_changed(self, infos):
        # Broker add-on handshakes changed (any broker thread). Merge with the SW driver state.
        with self._apps_lock:
            self._broker_apps = list(infos)
            self._refresh_connected_apps()

    def _on_sw_connection_changed(self, connected, version):
        # SolidWorks COM driver connected/disconnected (driver worker thread).
        with self._apps_lock:
            self._sw_apps = [("solidworks", version or "COM", 0)] if connected else []
            self._refresh_connected_apps()

    def _on_onshape_connection_changed(self, connected, version):
        # Subscription presence is transport state, not foreground application state. Keep the
        # version while the tab is subscribed, then let the foreground monitor publish the row
        # only when that exact viewport is both focused and in the foreground browser.
        with self._apps_lock:
            self._onshape_connection_version = (version or "web") if connected else None
            if not connected:
                self._onshape_apps = []
            self._refresh_connected_apps()
        monitor = getattr(self, "foreground_monitor", None)
        if monitor is not None:
            monitor.refresh()

    def _on_onshape_focus_changed(self, _focused):
        # The WAMP focus flag is the fine-grained browser-tab/viewport gate. Re-resolve the
        # current foreground process even while the ball and desktop foreground are stationary so
        # Onshape-scoped bindings, HUD state, mappings, and delivery change as one context.
        monitor = getattr(self, "foreground_monitor", None)
        if monitor is not None:
            monitor.refresh()

    def _sync_onshape_active_row(self, active):
        """Expose Onshape as active only for the foreground, focused viewport."""
        lock = getattr(self, "_apps_lock", None)
        if lock is None:
            return
        with lock:
            version = getattr(self, "_onshape_connection_version", None)
            visible = [("onshape", version, 0)] if active and version else []
            if visible == self._onshape_apps:
                return
            self._onshape_apps = visible
            self._refresh_connected_apps()

    def _refresh_connected_apps(self):
        # Caller holds _apps_lock. Rebuild the combined list (a new object => lockless readers ok).
        # AutoCAD appears via _broker_apps: its plugin handshakes with the broker like any add-on.
        apps = self._broker_apps + self._sw_apps + self._onshape_apps
        self.connected_apps = apps
        observed = getattr(self, "observed_addin_versions", None)
        if observed is not None:
            for app_key, version, _pid in apps:
                if app_key in integrations.ADDIN_KEYS and version not in (None, "", "?"):
                    observed[app_key] = str(version)
        self.log.info("3D apps: " + (", ".join(f"{a} v{v}" for a, v, _ in apps) or "none"))

    def app_connection_summary(self):
        if not self.connected_apps:
            return "none"
        return ", ".join(f"{a} v{v}" for a, v, _ in self.connected_apps)

    # --- lifecycle ----------------------------------------------------------------
    def start(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()                       # headless: no window on startup
            self.ui = SettingsWindow(self.root, self)
            self.hud = ControlHUD(self.root, self.runtime, self.config)

            self.tray = TrayController(self)
            self.tray.start()

            # Carry an earlier build's login registration onto the current value name. Done after
            # the guard has already admitted this process, so it can never race a running copy.
            carried = migrate_startup_entry()
            if carried is not None:
                self.log.info(f"start at login: carried the previous registration ({carried})")

            # Publish the bridge port for the add-ons, then start the broker.
            for published in publish_bridge_port(self.config.snapshot().bridge_port):
                self.log.info(f"published the bridge port to {published}")
            self.broker.start()
            # Workers may exist while disabled, but their gates prevent COM enumeration,
            # certificate creation, socket binding, TRUSTEDPATHS edits, and NETLOAD until setup
            # has succeeded and Enabled is checked. Config changes update these gates live.
            self._apply_service_gates()
            self.sw_driver.start()
            self.onshape_bridge.start()
            self.acad_loader.start()
            self.foreground_monitor.start()

            # One-click-free add-in refresh: re-copy any installed add-in the daemon now ships a
            # newer version of. Takes effect on the CAD app's next launch.
            for key, old, new in integrations.auto_update(self.config):
                self.log.info(f"updated {key} add-in {old} -> {new} (restart {key} to apply)")

            start_usb_thread(
                self.device_descriptors,
                self.ble_input_providers,
                self._handle_ble_motion,
                self.set_status,
                self.stop_event,
                enabled_event=self.ble_enabled_event,
                external_power_callback=self.set_external_power,
                handover_lock=self.transport_handover_lock,
            )
            start_ble_thread(
                self.get_ble_params,
                self.device_adapters,
                self._handle_ble_motion,
                self.set_status,
                self.stop_event,
                battery_callback=self.set_battery_level,
                enabled_event=self.ble_enabled_event,
                handover_lock=self.transport_handover_lock,
            )

            if self.debug:
                from .debugview import start_debug_view
                start_debug_view(self.engine, self, self.stop_event)

            if self.first_run:
                self.root.after(500, self.open_onboarding)
            self.root.after(300, self._poll)
            self.root.mainloop()
        except BaseException:
            # main() cannot clean a partially started App whose start() raised. Stop every owned
            # boundary here, then destroy Tk synchronously because its event loop may never start.
            self.quit()
            self._shutdown()
            raise

    def open_settings(self):
        # Safe from any thread: marshal the GUI work onto the Tk thread.
        if self.root is not None:
            self.root.after(0, self.ui.show)

    def open_onboarding(self):
        # Reopenable from the tray; all UI ownership remains on the Tk thread.
        if self.root is not None:
            self.root.after(0, self.ui.show_onboarding)

    def set_control_hud_visible(self, visible):
        """Typed persistent visibility action safe for the tray thread."""
        self.config.set_global("hud.visible", bool(visible))

    def _poll(self):
        if self._status != self._last_pushed:
            self._last_pushed = self._status
            if self.ui is not None:
                self.ui.update_status(self._status)
                self.ui.update_battery_status(self.battery_status_text())
            if self.tray is not None:
                self.tray.refresh()
        battery_signature = (self._battery_state, self._external_power)
        if battery_signature != self._last_battery_pushed:
            self._last_battery_pushed = battery_signature
            if self.ui is not None:
                self.ui.update_battery_status(self.battery_status_text())
            if self.tray is not None:
                self.tray.refresh()
        summary = self.app_connection_summary()
        if summary != self._last_apps_pushed:
            self._last_apps_pushed = summary
            if self.tray is not None:
                self.tray.refresh()
            if self.ui is not None:
                self.ui.update_app_connections(self.connected_apps)
        health = self.runtime_health_snapshot()
        health_signature = tuple(sorted(
            (service_id, item.state.value, item.detail)
            for service_id, item in health.items()))
        if health_signature != self._last_health_pushed:
            self._last_health_pushed = health_signature
            if self.tray is not None:
                self.tray.refresh()
            if self.ui is not None:
                self.ui.update_service_health(health)
        hud_visible = self.config.snapshot().global_value("hud.visible")
        if hud_visible != self._last_hud_visible:
            self._last_hud_visible = hud_visible
            if self.tray is not None:
                self.tray.refresh()
        if not self.stop_event.is_set():
            self.root.after(300, self._poll)

    def quit(self):
        # Called from the tray thread.
        if getattr(self, "_quit_started", False):
            return
        self._quit_started = True
        self.stop_event.set()

        def stop_owned(label, callback):
            try:
                callback()
            except Exception:
                self.log.exception("%s failed during daemon shutdown", label)

        if getattr(self, "binding_controller", None) is not None:
            stop_owned(
                "binding release",
                lambda: self.binding_controller.release_all("daemon_shutdown"),
            )
        if getattr(self, "pointer_button_output", None) is not None:
            stop_owned("pointer-button release", self.pointer_button_output.release_all)
        if getattr(self, "input_aggregator", None) is not None:
            stop_owned(
                "input-provider shutdown",
                lambda: self.input_aggregator.shutdown("daemon_shutdown"),
            )
        if getattr(self, "foreground_monitor", None) is not None:
            stop_owned("foreground monitor", self.foreground_monitor.stop)
        if self.broker is not None:
            stop_owned("navigation broker", self.broker.stop)
        if self.sw_driver is not None:
            stop_owned("SolidWorks driver", self.sw_driver.stop)
        if self.onshape_bridge is not None:
            stop_owned("Onshape bridge", self.onshape_bridge.stop)
        if self.acad_loader is not None:
            stop_owned("AutoCAD loader", self.acad_loader.stop)
        if self.tray is not None:
            stop_owned("tray", self.tray.stop)
        if self.root is not None:
            try:
                self.root.after(0, self._shutdown)
            except Exception:
                self._shutdown()

    def _shutdown(self):
        if getattr(self, "_shutdown_complete", False):
            return
        self._shutdown_complete = True
        if self.hud is not None:
            try:
                self.hud.stop()
            except Exception:
                self.log.exception("control HUD failed during daemon shutdown")
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
