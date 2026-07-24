# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""App._nav_sink routing and the merged connected-apps status.

solidworks frames must go to the in-process COM driver; every other app -- INCLUDING autocad,
whose NETLOADed plugin is a broker client and the sole AutoCAD transport (the COM transport is
archived) -- goes to the socket broker. The connected-apps list (which feeds the tray "Apps:"
line and the 3D-Apps rows) must merge broker add-ons with the SolidWorks driver's state.

App is built with __new__ so we don't spin up config/threads/GUI -- we exercise the real
methods against lightweight stubs.
"""
import struct
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from trackball_daemon.app import App
from trackball_daemon.app_registry import APP_SPECS_BY_ID
from trackball_daemon.config import Config, host_baseline_payload
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.commands import (
    RequestSettingOverride, RequestState, SerializedCommandQueue, SetFocusedContext)
from trackball_daemon.input import BindingController, load_system_binding_profiles
from trackball_daemon.output import OutputEngine
from trackball_daemon.navigation_router import NavigationRouter
from trackball_daemon.runtime_state import ConfigRuntimeBaseResolver, FocusedContext, RuntimeStore


def _scheme(pivot="default", style="default", zoom="default"):
    return {"scheme": {"orbit_pivot": pivot, "orbit_style": style, "zoom_mode": zoom}}


def test_saved_binding_override_recompiles_live_from_immutable_config_snapshot(tmp_path):
    app = App.__new__(App)
    app.config = ConfigStore(tmp_path / "config.json").load()
    app.config.set_global("input.mode.default", "pointer")
    app.binding_catalog = load_system_binding_profiles()
    app.runtime = RuntimeStore(ConfigRuntimeBaseResolver(app.config))
    app.commands = SerializedCommandQueue(app.runtime)
    app.binding_controller = BindingController(
        app._compiled_binding_profile(), app.commands, app.runtime)
    app.controls = []
    app.configure_keyboard_controls = lambda controls: app.controls.append(tuple(controls))
    app.configure_ble_controls = lambda _source, _controls: None
    app.ble_input_providers = {}
    app.engine = SimpleNamespace(apply_config=lambda: None)
    app.navigation = None
    app.sw_driver = app.onshape_bridge = app.acad_loader = None
    app.foreground_monitor = None
    app._last_scheme_pushed = {}
    app._last_runtime_rate = {}
    app.config.add_listener(app.on_config_changed)

    app.config.set_keybinding_override("astrolabe_5way", "user.binding.1", {
        "label": "A toggles Pointer / 3D",
        "enabled": True,
        "chord": ["keyboard:a"],
        "match": "exact",
        "activation": "hold",
        "priority": 0,
        "press": [{"command": "input.mode.toggle"}],
        "release": [],
    })

    assert "a" in app.binding_controller.compiled_profile.required_controls["keyboard"]
    app.binding_controller.update_pressed(("keyboard:a",))
    assert app.runtime.snapshot().effective_input_mode == "3d"
    app.binding_controller.update_pressed(())
    assert app.runtime.snapshot().effective_input_mode == "3d"


def _bare_app():
    app = App.__new__(App)
    app._engine_app = None
    app._last_scheme_pushed = None
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    app.engine = SimpleNamespace(bound=[])
    app.engine.set_active_bindings = lambda k: app.engine.bound.append(k)
    app.broker = SimpleNamespace(
        calls=[], targets=[], rates=[], schemes=[], active=[], discarded=[])
    def broker_submit(target, *values, state_revision):
        app.broker.targets.append((target, state_revision))
        app.broker.calls.append(values)
        return True
    app.broker.submit = broker_submit
    app.broker.activate_target = lambda target: app.broker.active.append(target)
    app.broker.discard_pending = lambda target: app.broker.discarded.append(target)
    app.broker.set_rate = lambda target, hz: app.broker.rates.append((target, hz))
    app.broker.set_scheme = lambda target, pivot, style, zoom, **kw: app.broker.schemes.append({
        "target": target, "orbit_pivot": pivot, "orbit_style": style,
        "zoom_mode": zoom, **kw})
    app.broker.delivery_state = lambda target: {"target": target}
    app.sw_driver = SimpleNamespace(calls=[], rates=[], schemes=[], holds=[], zoom_holds=[])
    app.sw_driver.submit = lambda *a: app.sw_driver.calls.append(a)
    app.sw_driver.set_rate = lambda hz: app.sw_driver.rates.append(hz)
    app.sw_driver.set_scheme = lambda pivot, style, zoom, **kw: app.sw_driver.schemes.append({
        "orbit_pivot": pivot, "orbit_style": style, "zoom_mode": zoom, **kw})
    app.sw_driver.set_pivot_hold = lambda s: app.sw_driver.holds.append(s)
    app.sw_driver.set_zoom_hold = lambda s: app.sw_driver.zoom_holds.append(s)
    app.sw_driver.discard_pending = lambda: None
    app.onshape_bridge = SimpleNamespace(
        calls=[], rates=[], schemes=[], holds=[], zoom_holds=[])
    app.onshape_bridge.submit = lambda *a: app.onshape_bridge.calls.append(a)
    app.onshape_bridge.set_rate = lambda hz: app.onshape_bridge.rates.append(hz)
    app.onshape_bridge.set_scheme = lambda pivot, style, zoom, **kw: (
        app.onshape_bridge.schemes.append({
            "orbit_pivot": pivot, "orbit_style": style, "zoom_mode": zoom, **kw}))
    app.onshape_bridge.set_pivot_hold = lambda s: app.onshape_bridge.holds.append(s)
    app.onshape_bridge.set_zoom_hold = lambda s: app.onshape_bridge.zoom_holds.append(s)
    app.onshape_bridge.discard_pending = lambda: None
    app.navigation = NavigationRouter(app.broker, app.sw_driver, app.onshape_bridge)
    app._config_tmp = tempfile.TemporaryDirectory()
    app.config = ConfigStore(Path(app._config_tmp.name) / "config.json").load()
    with app.config.transaction() as tx:
        tx.set_selected_app("fusion360")
        tx.set_app("solidworks", "navigation.refresh_rate", 60)
        tx.set_app("solidworks", "navigation.orbit.pivot", "object")
        tx.set_app("solidworks", "navigation.zoom.target", "to_object")
        tx.set_app("solidworks", "navigation.orbit.pivot_hold_seconds", 0.75)
        tx.set_app("solidworks", "navigation.zoom.cursor_hold_seconds", 1.25)
        tx.set_app("autocad", "navigation.refresh_rate", 45)
        tx.set_app("autocad", "navigation.orbit.pivot", "origin")
        tx.set_app("autocad", "navigation.orbit.style", "turntable")
    return app


def test_sensitive_services_require_setup_and_enabled():
    app = App.__new__(App)
    app._config_tmp = tempfile.TemporaryDirectory()
    app.config = ConfigStore(Path(app._config_tmp.name) / "config.json").load()
    with app.config.transaction() as tx:
        tx.set_app_operational("solidworks", installed=False, enabled=True)
        tx.set_app_operational("onshape", installed=True, enabled=False)
        tx.set_app_operational("autocad", installed=True, enabled=True)
    app.sw_driver = SimpleNamespace(states=[], set_enabled=lambda v: app.sw_driver.states.append(v))
    app.onshape_bridge = SimpleNamespace(
        states=[], set_enabled=lambda v: app.onshape_bridge.states.append(v))
    app.acad_loader = SimpleNamespace(states=[], set_enabled=lambda v: app.acad_loader.states.append(v))

    app._apply_service_gates()

    assert app.sw_driver.states == [False]
    assert app.onshape_bridge.states == [False]
    assert app.acad_loader.states == [True]


# --- routing --------------------------------------------------------------------------
def test_solidworks_routes_to_driver():
    app = _bare_app()
    app._active_app_key = lambda: "solidworks"
    app._nav_sink(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    assert app.sw_driver.calls == [(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)]
    assert app.broker.calls == []
    assert app.engine.bound == ["solidworks"]               # bindings switched to SW


def test_autocad_routes_to_broker():
    # AutoCAD is a broker app: its NETLOADed plugin (the sole transport since the COM transport
    # was archived) is a broker client, so frames go to the broker UNCONDITIONALLY -- even before
    # the plugin has connected (they are simply dropped then, like any other socket app).
    app = _bare_app()
    app._active_app_key = lambda: "autocad"
    app._nav_sink(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    assert app.broker.calls == [(0.1, 0.2, 0.3, 0.4, 0.5, 0.6)]
    assert app.sw_driver.calls == []
    assert app.engine.bound == ["autocad"]                  # bindings switched to AutoCAD


def test_fusion_routes_to_broker():
    app = _bare_app()
    app._active_app_key = lambda: "fusion360"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.sw_driver.calls == []


def test_freecad_routes_to_broker():
    # FreeCAD is a socket add-on like Fusion/Blender -> the else-branch sends it to the broker,
    # not the SolidWorks COM driver or the Onshape bridge.
    app = _bare_app()
    app._active_app_key = lambda: "freecad"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.sw_driver.calls == []
    assert app.engine.bound == ["freecad"]


def test_sketchup_routes_to_broker():
    # SketchUp is a Ruby socket extension; it uses the same generic broker branch as Fusion.
    app = _bare_app()
    app._active_app_key = lambda: "sketchup"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.sw_driver.calls == []
    assert app.engine.bound == ["sketchup"]


def test_sketchup_process_hint_is_registered():
    assert APP_SPECS_BY_ID["sketchup"].matches_process("sketchup.exe")


def test_unreal_routes_to_broker():
    # Unreal is a socket add-on (content-only plugin) like Fusion/Blender/FreeCAD -> the else-branch
    # sends it to the broker, not the SolidWorks COM driver or the Onshape bridge.
    app = _bare_app()
    app._active_app_key = lambda: "unreal"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.sw_driver.calls == []
    assert app.engine.bound == ["unreal"]


def test_unity_routes_to_broker():
    app = _bare_app()
    app._active_app_key = lambda: "unity"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.engine.bound == ["unity"]


def test_godot_routes_to_broker():
    app = _bare_app()
    app._active_app_key = lambda: "godot"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.engine.bound == ["godot"]


def test_rhino_routes_to_broker():
    app = _bare_app()
    app._active_app_key = lambda: "rhino"
    app._nav_sink(1, 2, 3, 4, 5, 6)
    assert app.broker.calls == [(1, 2, 3, 4, 5, 6)]
    assert app.engine.bound == ["rhino"]


def test_unity_godot_rhino_process_hints_registered():
    assert APP_SPECS_BY_ID["unity"].matches_process("unity.exe")
    assert APP_SPECS_BY_ID["godot"].matches_process("godot.exe")
    assert APP_SPECS_BY_ID["rhino"].matches_process("rhino.exe")


def test_no_focused_app_drops_frame():
    app = _bare_app()
    app._active_app_key = lambda: None
    app._nav_sink(9, 9, 9, 9, 9, 9)
    assert app.broker.calls == []
    assert app.sw_driver.calls == []


def test_first_packet_after_focus_switch_uses_new_app_mapping(isolated_config):
    """Focus selection must happen before OutputEngine transforms the packet."""
    cfg = Config().load()
    with cfg.transaction() as tx:
        for axis, source in zip("xyz", (0, 1, 2)):
            tx.set_app("fusion360", f"navigation.routing.orbit.{axis}.source", source)
        for axis, source in zip("xyz", (2, 0, 1)):
            tx.set_app("rhino", f"navigation.routing.orbit.{axis}.source", source)
    app = _bare_app()
    app.config = cfg
    app.engine = OutputEngine(cfg)
    app.engine.nav_sink = app._nav_sink
    focused = ["fusion360"]
    app._foreground_app_context = lambda: SimpleNamespace(
        app_id=focused[0], process_name=f"{focused[0]}.exe")
    app._active_app_key_from_context = lambda context: context.app_id
    packet = struct.pack("<fff", 0.01, 0.02, 0.03)

    app._handle_ble_packet(packet)
    focused[0] = "rhino"
    app._handle_ble_packet(packet)

    baseline = host_baseline_payload("rhino")["orbit"]
    assert app.broker.calls[-1][:3] == pytest.approx(
        (0.03 * baseline[0], 0.01 * baseline[1], 0.02 * baseline[2]))
    assert app.broker.targets[-1][0] == "rhino"
    assert app.engine._mapping.app_key == "rhino"


def test_packet_envelope_captures_runtime_revision():
    app = _bare_app()
    app.runtime = SimpleNamespace(snapshot=lambda: SimpleNamespace(
        revision=17, focused_context=SimpleNamespace(app_id="blender")))
    app._foreground_app_context = lambda: SimpleNamespace(
        app_id="blender", process_name="blender.exe")
    app._active_app_key_from_context = lambda context: context.app_id
    app._apply_runtime_navigation_profile = lambda _snapshot: None
    captured = []
    def handle(_data, runtime_snapshot=None):
        captured.append(runtime_snapshot.revision)
        app._nav_sink(1, 2, 3, 4, 5, 6)
    app.engine.handle_packet = handle

    app._handle_ble_packet(b"packet")

    assert app.broker.targets[-1] == ("blender", 17)
    assert captured == [17]


def test_packet_with_no_active_app_selects_no_navigation_target():
    app = _bare_app()
    app.navigation.activate("blender")
    app._foreground_app_context = lambda: SimpleNamespace(
        app_id=None, process_name="notes.exe")
    app._active_app_key_from_context = lambda _context: None
    app.engine.handle_packet = lambda _data, runtime_snapshot=None: None

    app._handle_ble_packet(b"packet")

    assert app.navigation.active_target is None
    assert app.broker.active[-1] is None


# --- per-app refresh rate -------------------------------------------------------------
def test_app_rate_override_and_fallback():
    app = _bare_app()
    assert app._app_rate("solidworks") == 60     # per-app override
    assert app._app_rate("fusion360") == 30      # 0 -> global default
    with pytest.raises(KeyError):
        app._app_rate("missing")


def test_all_targets_receive_independent_per_app_rates():
    app = _bare_app()
    app._apply_rates()
    assert app.sw_driver.rates[-1] == 60
    assert ("fusion360", 30) in app.broker.rates
    assert ("autocad", 45) in app.broker.rates


def test_all_targets_receive_independent_schemes():
    app = _bare_app()
    app._apply_schemes()
    # solidworks per-app scheme: pivot=object (override), style=default->general free, zoom=to_object
    assert app.sw_driver.schemes[-1] == {
        "orbit_pivot": "object", "orbit_style": "free", "zoom_mode": "to_object",
        "selection_overrides_pivot": True,
        "orbit_pivot_fallbacks": ["cursor_3d", "camera", "object", "origin"],
        "level_horizon_on_entry": True}
    assert app.sw_driver.holds[-1] == 0.75       # per-app screen-center-pivot hold pushed to the driver


def test_stationary_runtime_mode_and_settings_publish_to_focused_rich_target():
    app = _bare_app()
    app.runtime = RuntimeStore(ConfigRuntimeBaseResolver(app.config))
    commands = SerializedCommandQueue(app.runtime)
    commands.dispatch(SetFocusedContext(
        origin="test", context=FocusedContext("blender", "blender.exe")))
    commands.dispatch(RequestState(
        origin="test", source="binding", binding_id="fly", activation_id="1",
        target="navigation.fly"))
    commands.dispatch(RequestSettingOverride(
        origin="test", source="binding", binding_id="speed", activation_id="1",
        setting_id="navigation.fly.speed", value=2.5))
    commands.dispatch(RequestSettingOverride(
        origin="test", source="binding", binding_id="pivot", activation_id="1",
        setting_id="navigation.orbit.pivot", value="selection"))

    app._apply_runtime_navigation_profile(app.runtime.snapshot())

    sent = next(item for item in reversed(app.broker.schemes)
                if item["target"] == "blender")
    assert sent["orbit_pivot"] == "selection"
    assert sent["advanced"]["nav_mode"] == "fly"
    assert sent["advanced"]["fly_speed"] == 2.5
    assert ("blender", app.runtime.snapshot().effective_settings[
        "navigation.refresh_rate"]) in app.broker.rates


def test_autocad_profile_remains_targeted_to_its_broker_clients():
    app = _bare_app()
    app._apply_rates()
    app._apply_schemes()
    assert ("autocad", 45) in app.broker.rates
    sent = next(item for item in app.broker.schemes if item["target"] == "autocad")
    assert sent["orbit_pivot"] == "origin"
    assert sent["orbit_style"] == "turntable"
    assert sent["zoom_mode"] == "to_center"
    assert sent["advanced"]["level_horizon_on_entry"] is True


# --- merged connection state ----------------------------------------------------------
def _status_app():
    app = App.__new__(App)
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    app._apps_lock = threading.Lock()
    app._broker_apps = []
    app._sw_apps = []
    app._onshape_apps = []
    app._onshape_connection_version = None
    app.connected_apps = []
    app.observed_addin_versions = {}
    return app


def test_connection_merge_includes_solidworks():
    app = _status_app()
    app._on_clients_changed([("fusion360", "2.7", 1234)])
    assert ("fusion360", "2.7", 1234) in app.connected_apps

    app._on_sw_connection_changed(True, "32.3.0")
    keys = [a for a, _, _ in app.connected_apps]
    assert "solidworks" in keys and "fusion360" in keys

    app._on_sw_connection_changed(False, "")
    keys = [a for a, _, _ in app.connected_apps]
    assert "solidworks" not in keys and "fusion360" in keys  # SW removed, broker app stays


def test_sw_version_fallback_label():
    app = _status_app()
    app._on_sw_connection_changed(True, "")                 # driver reports no version string
    assert ("solidworks", "COM", 0) in app.connected_apps


def test_onshape_subscription_is_not_an_active_app_until_foreground_focus():
    app = _status_app()
    app.foreground_monitor = SimpleNamespace(refreshes=0, refresh=lambda: setattr(
        app.foreground_monitor, "refreshes", app.foreground_monitor.refreshes + 1))

    app._on_onshape_connection_changed(True, "1.4.8")
    assert app._onshape_connection_version == "1.4.8"
    assert app.connected_apps == []
    assert app.foreground_monitor.refreshes == 1

    app._sync_onshape_active_row(True)
    assert app.connected_apps == [("onshape", "1.4.8", 0)]

    app._sync_onshape_active_row(False)
    assert app.connected_apps == []
    assert app._onshape_connection_version == "1.4.8"

    app._on_onshape_connection_changed(False, "")
    assert app._onshape_connection_version is None


def test_connection_merge_includes_autocad_as_broker_client():
    # AutoCAD appears in the connected apps via the BROKER (the plugin handshakes like any
    # add-on) -- there is no separate COM transport entry anymore.
    app = _status_app()
    app._on_clients_changed([("fusion360", "2.7", 1234), ("autocad", "0.2.8", 777)])
    keys = [a for a, _, _ in app.connected_apps]
    assert "autocad" in keys and "fusion360" in keys
    app._on_clients_changed([("fusion360", "2.7", 1234)])
    keys = [a for a, _, _ in app.connected_apps]
    assert "autocad" not in keys and "fusion360" in keys    # AutoCAD removed, other app stays


def test_loaded_addin_version_is_remembered_after_disconnect():
    app = _status_app()
    app._on_clients_changed([("unity", "1.2.3", 42)])
    assert app.observed_addin_versions["unity"] == "1.2.3"

    app._on_clients_changed([])
    assert app.connected_apps == []
    assert app.observed_addin_versions["unity"] == "1.2.3"


def test_app_connection_summary():
    app = App.__new__(App)
    app.connected_apps = [("solidworks", "32.3.0", 0), ("fusion360", "2.7", 1)]
    assert app.app_connection_summary() == "solidworks v32.3.0, fusion360 v2.7"
    app.connected_apps = []
    assert app.app_connection_summary() == "none"
