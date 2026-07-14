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

from trackball_daemon import output as output_mod
from trackball_daemon.app import App
from trackball_daemon.app_registry import APP_SPECS_BY_ID
from trackball_daemon.config import Config, host_baseline_payload
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.output import OutputEngine


def _scheme(pivot="default", style="default", zoom="default"):
    return {"scheme": {"orbit_pivot": pivot, "orbit_style": style, "zoom_mode": zoom}}


def _bare_app():
    app = App.__new__(App)
    app._engine_app = None
    app._last_scheme_pushed = None
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    app.engine = SimpleNamespace(bound=[])
    app.engine.set_active_bindings = lambda k: app.engine.bound.append(k)
    app.broker = SimpleNamespace(calls=[], rates=[], schemes=[])
    app.broker.submit = lambda *a: app.broker.calls.append(a)
    app.broker.set_rate = lambda hz: app.broker.rates.append(hz)
    app.broker.set_scheme = lambda **kw: app.broker.schemes.append(kw)
    app.sw_driver = SimpleNamespace(calls=[], rates=[], schemes=[], holds=[], zoom_holds=[])
    app.sw_driver.submit = lambda *a: app.sw_driver.calls.append(a)
    app.sw_driver.set_rate = lambda hz: app.sw_driver.rates.append(hz)
    app.sw_driver.set_scheme = lambda **kw: app.sw_driver.schemes.append(kw)
    app.sw_driver.set_pivot_hold = lambda s: app.sw_driver.holds.append(s)
    app.sw_driver.set_zoom_hold = lambda s: app.sw_driver.zoom_holds.append(s)
    app.onshape_bridge = None                   # Onshape bridge not configured in these routing tests
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


def test_first_packet_after_focus_switch_uses_new_app_mapping(isolated_config, monkeypatch):
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
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    focused = ["fusion360"]
    app._active_app_key = lambda: focused[0]
    packet = struct.pack("<fff", 0.01, 0.02, 0.03)

    app._handle_ble_packet(packet)
    focused[0] = "rhino"
    app._handle_ble_packet(packet)

    baseline = host_baseline_payload("rhino")["orbit"]
    assert app.broker.calls[-1][:3] == pytest.approx(
        (0.03 * baseline[0], 0.01 * baseline[1], 0.02 * baseline[2]))
    assert app.engine._mapping.app_key == "rhino"


# --- per-app refresh rate -------------------------------------------------------------
def test_app_rate_override_and_fallback():
    app = _bare_app()
    assert app._app_rate("solidworks") == 60     # per-app override
    assert app._app_rate("fusion360") == 30      # 0 -> global default
    with pytest.raises(KeyError):
        app._app_rate("missing")


def test_focus_applies_per_app_rate():
    app = _bare_app()
    app._active_app_key = lambda: "solidworks"
    app._nav_sink(1, 1, 1, 1, 1, 1)              # focusing SolidWorks
    assert app.sw_driver.rates[-1] == 60         # SW driver runs at SolidWorks' rate
    assert app.broker.rates[-1] == 30            # broker falls back to active socket app (30)
    app._active_app_key = lambda: "fusion360"
    app._nav_sink(1, 1, 1, 1, 1, 1)              # switching focus to Fusion
    assert app.broker.rates[-1] == 30            # fusion 0 -> global default 30


def test_focus_applies_solidworks_scheme():
    app = _bare_app()
    app._active_app_key = lambda: "solidworks"
    app._nav_sink(1, 1, 1, 1, 1, 1)              # focusing SolidWorks pushes its scheme to the driver
    # solidworks per-app scheme: pivot=object (override), style=default->general free, zoom=to_object
    assert app.sw_driver.schemes[-1] == {
        "orbit_pivot": "object", "orbit_style": "free", "zoom_mode": "to_object",
        "selection_overrides_pivot": True,
        "orbit_pivot_fallbacks": ["cursor_3d", "camera", "object", "origin"],
        "level_horizon_on_entry": True}
    assert app.sw_driver.holds[-1] == 0.75       # per-app screen-center-pivot hold pushed to the driver


def test_focus_applies_autocad_rate_and_scheme():
    # Focusing AutoCAD makes the BROKER carry autocad's per-app rate + scheme (the NETLOADed
    # plugin is a broker client like any socket add-on).
    app = _bare_app()
    app._active_app_key = lambda: "autocad"
    app._nav_sink(1, 1, 1, 1, 1, 1)
    assert app.broker.rates[-1] == 45            # autocad's per-app rate now drives the broker
    # autocad per-app scheme: pivot=origin + style=turntable (overrides), zoom=default->general to_center
    assert app.broker.schemes[-1]["orbit_pivot"] == "origin"
    assert app.broker.schemes[-1]["orbit_style"] == "turntable"
    assert app.broker.schemes[-1]["zoom_mode"] == "to_center"
    assert app.broker.schemes[-1]["advanced"]["level_horizon_on_entry"] is True


# --- merged connection state ----------------------------------------------------------
def _status_app():
    app = App.__new__(App)
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    app._apps_lock = threading.Lock()
    app._broker_apps = []
    app._sw_apps = []
    app._onshape_apps = []
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
