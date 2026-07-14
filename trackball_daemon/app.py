"""Orchestrator: wires config + output engine + BLE thread + tray + settings window +
the nav transports (broker / SolidWorks / Onshape / AutoCAD loader).

Threading model (Windows):
  * main thread       -> hidden Tk root + mainloop (owns all GUI; window shown/hidden on demand)
  * tray thread       -> pystray icon loop (menu callbacks marshalled to Tk via root.after)
  * ble thread        -> asyncio BLE loop and packet-boundary focus routing
  * broker threads    -> NavBroker accept + sender (streams frames to the socket add-ons)
  * SolidWorks worker -> its own CoInitialize'd COM thread (in-process driver)
  * AutoCAD worker    -> its own CoInitialize'd COM thread (plugin loader; delivery only)
  * Onshape threads   -> TLS accept + per-connection WAMP reader + nav worker (in-process bridge)
  * debug thread      -> optional pygame cube (--debug)
The process stays alive on the Tk mainloop and exits only when tray -> Quit tears it down.
"""
import json
import threading
import tkinter as tk

from . import integrations
from .app_registry import APP_SPECS, resolve_foreground_context
from .autocad_driver import AutoCADPluginLoader
from .ble import start_ble_thread
from .commands import SerializedCommandQueue, SetFocusedContext
from .config import (compose_advanced_with_host_baseline, host_baseline_payload,
                     normalize_orbit_pivot_fallbacks, orbit_pivot_candidates)
from .config_store import ConfigStore
from .devices import (
    DeviceAdapterRegistry,
    MotionSample,
    SnapshotInputProvider,
    builtin_device_descriptors,
)
from .input import InputAggregator
from .input.windows_raw_input import WindowsRawInputProvider
from .navbroker import NavBroker
from .navigation_router import NavigationEnvelope, NavigationRouter
from .onshape_bridge import OnshapeBridge
from .output import OutputEngine
from .paths import user_config_dir
from .solidworks_driver import SolidWorksDriver
from .runtime_state import ConfigRuntimeBaseResolver, FocusedContext, RuntimeStore
from .tray import TrayController
from .ui import SettingsWindow
from .util import get_logger
from .winfocus import ForegroundMonitor, foreground_process_name


_NO_PACKET_APP = object()
_NO_PACKET_REVISION = object()


class App:
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
        device_descriptors = builtin_device_descriptors()
        self.ble_input_providers = {}
        for descriptor in device_descriptors:
            provider = SnapshotInputProvider(
                descriptor,
                self.input_aggregator.accept_many,
                self.input_aggregator.update_health,
            )
            self.input_aggregator.register_provider(provider)
            # Phase 6 observes the built-in hardware controls immediately because the BLE motion
            # connection already exists. Phase 7 will narrow these sets to compiled bindings.
            provider.configure(control.control_id for control in descriptor.controls)
            self.ble_input_providers[descriptor.source_id] = provider
        self.device_adapters = DeviceAdapterRegistry(
            device_descriptors, self.ble_input_providers)
        self.stop_event = threading.Event()
        self._status = "starting"
        self._last_pushed = None
        self.root = None
        self.ui = None
        self.tray = None
        self.broker = None
        self.sw_driver = None
        self.onshape_bridge = None
        self.acad_loader = None
        self.config.add_listener(self.on_config_changed)

        # 3D-app nav bridge (127.0.0.1). The engine forwards orbit/pan/zoom deltas here,
        # gated on the focused CAD app; socket add-ons connect and drive their app's camera.
        snapshot = self.config.snapshot()
        self.broker = NavBroker(snapshot.bridge_port, self._on_clients_changed,
                                rate_hz=snapshot.global_value("navigation.refresh_rate"))
        # SolidWorks is driven by external COM automation, not a socket add-in: this in-process
        # driver attaches to a running SolidWorks and moves its camera directly. It lives parallel
        # to the broker; _nav_sink routes solidworks frames here instead of to the broker.
        self.sw_driver = SolidWorksDriver(self._on_sw_connection_changed,
                                          rate_hz=snapshot.global_value("navigation.refresh_rate"))
        # Onshape (browser) is driven by an in-process bridge that impersonates the 3Dconnexion
        # local NL-Proxy service Onshape's page connects to (TLS WebSocket on 127.51.68.120:8181).
        # Like the SW driver it lives parallel to the broker; _nav_sink routes onshape frames here.
        ocfg = snapshot.onshape
        self.onshape_bridge = OnshapeBridge(
            self._on_onshape_connection_changed,
            rate_hz=snapshot.global_value("navigation.refresh_rate"),
            host=ocfg.get("address") or None, port=ocfg.get("port") or None,
            cert_path=ocfg.get("cert_path") or None, key_path=ocfg.get("key_path") or None)
        self.navigation = NavigationRouter(
            self.broker, self.sw_driver, self.onshape_bridge)
        # AutoCAD is a BROKER app: its compiled NETLOAD plugin (plugin_src/autocad) drives the
        # live GraphicsSystem view in-process and connects to the nav broker like the other
        # socket add-ons. COM's only remaining job is DELIVERY -- this loader NETLOADs the
        # bundled plugin into a running AutoCAD (the old COM nav transport is archived at
        # archive/autocad_com_transport/; it raced the plugin for the early frames).
        self.acad_loader = AutoCADPluginLoader()
        self.engine.nav_sink = self._nav_sink
        # Connected-app status feeds the tray "Apps:" line + the 3D-Apps rows. It merges the
        # broker's socket add-ons with the SolidWorks COM driver and the Onshape bridge states.
        self._apps_lock = threading.Lock()
        self._broker_apps = []                     # [(app, version, pid)] from the broker
        self._sw_apps = []                         # [(app, version, pid)] from the SW driver (0/1)
        self._onshape_apps = []                    # [(app, version, pid)] from the Onshape bridge
        self.connected_apps = []
        # The handshake version belongs to the copy actually loaded by the current/most recently
        # connected host document. Preserve it after disconnect so update UX does not fall back to
        # an unrelated "primary" install destination.
        self.observed_addin_versions = {}
        self._last_apps_pushed = None
        self._engine_app = None
        self._packet_app_key = _NO_PACKET_APP
        self._packet_state_revision = _NO_PACKET_REVISION
        self._last_scheme_pushed = {}
        self.foreground_monitor = ForegroundMonitor(self._on_foreground_process_changed)
        self._apply_rates()
        self._apply_schemes()

    # --- BLE wiring and packet-boundary routing -----------------------------------
    def get_ble_params(self):
        snapshot = self.config.snapshot()
        return (snapshot.device_value("device.name"),
                snapshot.device_value("device.address"), snapshot.device_char_uuid)

    def set_status(self, text):
        # Called from the BLE thread; just store + log. The Tk poll pushes it to the GUI.
        self._status = text
        self.log.info(text)

    def status_text(self):
        return self._status

    def is_connected(self):
        return self._status.startswith(("connected", "subscribed"))

    def on_config_changed(self, _event):
        self.engine.apply_config()
        self._apply_service_gates()
        self._apply_rates()
        self._apply_schemes()
        monitor = getattr(self, "foreground_monitor", None)
        if monitor is not None:
            monitor.refresh()

    def configure_keyboard_controls(self, control_ids):
        """Phase 7 compiler seam; an empty set keeps global keyboard reception unregistered."""
        return self.input_aggregator.configure_provider("keyboard", control_ids)

    def configure_ble_controls(self, source_id, control_ids):
        """Phase 7 compiler seam for one stable data-descriptor control namespace."""
        if source_id not in self.ble_input_providers:
            raise ValueError(f"unknown BLE input provider: {source_id}")
        return self.input_aggregator.configure_provider(source_id, control_ids)

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
        fallbacks = normalize_orbit_pivot_fallbacks(
            list(snapshot.global_value("navigation.orbit.pivot_fallbacks")))
        if not isinstance(getattr(self, "_last_scheme_pushed", None), dict):
            self._last_scheme_pushed = {}
        for spec in APP_SPECS:
            key = spec.app_id
            scheme = self._effective_scheme(key)
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
        return resolve_foreground_context(proc, onshape_connected=connected)

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
        self._apply_foreground_context(self._foreground_context_for_process(process_name))
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

    def _handle_ble_packet(self, data):
        """Choose one focused app for both mapping and routing of this complete BLE packet."""
        context = self._foreground_app_context()
        key = self._apply_foreground_context(context)
        previous = getattr(self, "_packet_app_key", _NO_PACKET_APP)
        previous_revision = getattr(self, "_packet_state_revision", _NO_PACKET_REVISION)
        self._packet_app_key = key
        runtime = getattr(self, "runtime", None)
        self._packet_state_revision = runtime.snapshot().revision if runtime is not None else 0
        try:
            self.engine.handle_packet(bytes(data))
        finally:
            self._packet_app_key = previous
            self._packet_state_revision = previous_revision

    def _handle_ble_motion(self, sample):
        if not isinstance(sample, MotionSample):
            raise TypeError("BLE device adapters must emit MotionSample values")
        self._handle_ble_packet(sample.payload)

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
        # Onshape browser bridge connected/disconnected (bridge server/reader thread).
        with self._apps_lock:
            self._onshape_apps = [("onshape", version or "web", 0)] if connected else []
            self._refresh_connected_apps()
        monitor = getattr(self, "foreground_monitor", None)
        if monitor is not None:
            monitor.refresh()

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
        self.root = tk.Tk()
        self.root.withdraw()                       # headless: no window on startup
        self.ui = SettingsWindow(self.root, self)

        self.tray = TrayController(self)
        self.tray.start()

        # Publish the bridge port for the add-ons, then start the broker.
        try:
            with open(user_config_dir() / "bridge.json", "w", encoding="utf-8") as f:
                json.dump({"port": self.config.snapshot().bridge_port}, f)
        except OSError:
            pass
        self.broker.start()
        # Workers may exist while disabled, but their gates prevent COM enumeration, certificate
        # creation, socket binding, TRUSTEDPATHS edits, and NETLOAD until setup has succeeded and
        # Enabled is checked. Config changes update these gates live.
        self._apply_service_gates()
        self.sw_driver.start()
        self.onshape_bridge.start()
        self.acad_loader.start()
        self.foreground_monitor.start()

        # One-click-free add-in refresh: re-copy any installed add-in the daemon now ships a
        # newer version of (e.g. this release's viewport-refresh fix). Takes effect on the
        # CAD app's next launch.
        for key, old, new in integrations.auto_update(self.config):
            self.log.info(f"updated {key} add-in {old} -> {new} (restart {key} to apply)")

        start_ble_thread(
            self.get_ble_params,
            self.device_adapters,
            self._handle_ble_motion,
            self.set_status,
            self.stop_event,
        )

        if self.debug:
            from .debugview import start_debug_view
            start_debug_view(self.engine, self, self.stop_event)

        if self.first_run:
            self.root.after(500, self.open_settings)
        self.root.after(300, self._poll)
        self.root.mainloop()

    def open_settings(self):
        # Safe from any thread: marshal the GUI work onto the Tk thread.
        if self.root is not None:
            self.root.after(0, self.ui.show)

    def _poll(self):
        if self._status != self._last_pushed:
            self._last_pushed = self._status
            if self.ui is not None:
                self.ui.update_status(self._status)
            if self.tray is not None:
                self.tray.refresh()
        summary = self.app_connection_summary()
        if summary != self._last_apps_pushed:
            self._last_apps_pushed = summary
            if self.tray is not None:
                self.tray.refresh()
            if self.ui is not None:
                self.ui.update_app_connections(self.connected_apps)
        if not self.stop_event.is_set():
            self.root.after(300, self._poll)

    def quit(self):
        # Called from the tray thread.
        self.stop_event.set()
        if getattr(self, "input_aggregator", None) is not None:
            self.input_aggregator.shutdown("daemon_shutdown")
        if getattr(self, "foreground_monitor", None) is not None:
            self.foreground_monitor.stop()
        if self.broker is not None:
            self.broker.stop()
        if self.sw_driver is not None:
            self.sw_driver.stop()
        if self.onshape_bridge is not None:
            self.onshape_bridge.stop()
        if self.acad_loader is not None:
            self.acad_loader.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.root is not None:
            self.root.after(0, self._shutdown)

    def _shutdown(self):
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
