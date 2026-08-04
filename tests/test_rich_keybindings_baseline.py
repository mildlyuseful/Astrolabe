# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Behavior contracts that rich-keybinding refactors must change deliberately.

These tests capture current ownership and known limitations at the Phase 0 boundary. They are not
the target architecture: later phases may replace a contract only when the corresponding new
behavior and migration tests land in the same change.
"""

import json
import struct
from types import SimpleNamespace

import pytest

from trackball_daemon import app as app_mod
from trackball_daemon import output as output_mod
from trackball_daemon.app import App
from trackball_daemon.config import Config, effective_scheme, host_baseline
from trackball_daemon.navbroker import NavBroker
from trackball_daemon.output import OutputEngine
from trackball_daemon import tray as tray_mod
from trackball_daemon.tray import TrayController
from trackball_daemon.ui import SettingsWindow


def _packet(rx=0.01, ry=0.02, rz=0.03):
    return struct.pack("<fff", rx, ry, rz)


def test_secondary_layer_is_runtime_owned_and_stationary(isolated_config):
    from trackball_daemon.commands import ReleaseState, RequestState

    engine = OutputEngine(Config().load())
    engine.set_mode(OutputEngine.MODE_CUBE)
    engine.commands.dispatch(RequestState(
        origin="test", source="keyboard", binding_id="shift.pan", activation_id="1",
        target="pan"))
    assert engine.runtime.snapshot().effective_navigation_layer == "secondary"
    assert engine.runtime.snapshot().effective_input_mode == "3d"

    engine.commands.dispatch(ReleaseState(
        origin="test", source="keyboard", binding_id="shift.pan", activation_id="1",
        target="pan"))
    assert engine.runtime.snapshot().effective_navigation_layer == "primary"


def test_runtime_mode_survives_config_refresh_but_new_engine_uses_startup_default(
        isolated_config):
    cfg = Config().load()
    cfg.set_global("input.mode.default", "pointer")
    engine = OutputEngine(cfg)
    assert engine.mode == OutputEngine.MODE_CURSOR

    assert engine.toggle_mode() == OutputEngine.MODE_CUBE
    engine.apply_config()
    assert engine.mode == OutputEngine.MODE_CUBE

    replacement = OutputEngine(cfg)
    assert replacement.mode == OutputEngine.MODE_CURSOR


def test_tray_does_not_duplicate_declarative_mode_controls(monkeypatch):
    """The HUD reports mode while configured bindings remain its only control surface."""
    engine = SimpleNamespace(
        mode=OutputEngine.MODE_CURSOR,
        reset_view=lambda: None,
    )
    controller = TrayController.__new__(TrayController)
    controller.app = SimpleNamespace(
        engine=engine,
        config=SimpleNamespace(snapshot=lambda: SimpleNamespace(
            global_value=lambda _setting_id: True)),
        is_connected=lambda: False,
        battery_status_text=lambda: "Battery: unavailable",
        app_connection_summary=lambda: "none",
        runtime_health_summary=lambda: "disabled",
        open_settings=lambda: None,
        open_onboarding=lambda: None,
        set_control_hud_visible=lambda _visible: None,
        quit=lambda: None,
    )

    class _Menu(tuple):
        SEPARATOR = object()

        def __new__(cls, *items):
            return tuple.__new__(cls, items)

    monkeypatch.setattr(tray_mod.pystray, "Menu", _Menu)
    monkeypatch.setattr(
        tray_mod.pystray, "MenuItem",
        lambda text, action, **kwargs: SimpleNamespace(text=text, action=action, **kwargs),
    )

    menu = controller._build_menu()
    labels = [item.text for item in menu if item is not _Menu.SEPARATOR]
    assert "Show control panel" in labels
    assert not any(isinstance(label, str) and label.startswith("Mode:") for label in labels)
    dynamic_labels = [
        item.text(None) for item in menu
        if item is not _Menu.SEPARATOR and callable(item.text) and item.action is None
    ]
    assert "Battery: unavailable" in dynamic_labels
    assert controller._title_text() == (
        "Astrolabe — Disconnected — Battery: unavailable")
    controller.icon = SimpleNamespace(title="", update_menu=lambda: None)
    controller.refresh()
    assert controller.icon.title == (
        "Astrolabe — Disconnected — Battery: unavailable")


def test_app_battery_status_preserves_last_known_value_across_disconnect():
    app = App.__new__(App)
    app._status = "subscribed -- Astrolabe is live"
    app._battery_state = (None, False)
    app.log = SimpleNamespace(info=lambda *_args: None)

    assert app.battery_status_text() == "Battery: unavailable"
    app.set_battery_level(73)
    assert app.battery_level() == 73
    assert app.battery_status_text() == "Battery: 73%"

    app.set_status("disconnected, reconnecting...")
    assert app.battery_status_text() == "Battery: 73% (last known)"
    app.set_status("subscribed -- Astrolabe is live")
    assert app.battery_status_text() == "Battery: 73% (last known)"
    app.set_battery_level(73)
    assert app.battery_status_text() == "Battery: 73%"


def test_app_external_power_is_explicit_without_fabricating_battery_charge():
    app = App.__new__(App)
    app._status = "subscribed -- Astrolabe over USB is live"
    app._battery_state = (73, True)
    app._external_power = False
    app.log = SimpleNamespace(info=lambda *_args: None)

    app.set_external_power(True)
    assert app.battery_level() == 73
    assert app.battery_status_text() == "Power: USB (Battery: 73% last known)"

    app.set_battery_level(None)
    assert app.battery_status_text() == "Power: USB (Battery unavailable)"
    app.set_external_power(False)
    assert app.battery_status_text() == "Battery: unavailable"

    with pytest.raises(TypeError, match="boolean"):
        app.set_external_power(1)


def test_settings_battery_status_update_is_tk_thread_projection():
    values = []
    ui = SettingsWindow.__new__(SettingsWindow)
    ui.win = SimpleNamespace(winfo_exists=lambda: True)
    ui.battery_var = SimpleNamespace(set=values.append)

    ui.update_battery_status("Battery: 42%")

    assert values == ["Battery: 42%"]


def test_scheme_inheritance_uses_only_the_explicit_default_sentinel():
    general = {
        "orbit_pivot": "screen_center",
        "orbit_style": "free",
        "zoom_mode": "to_cursor",
    }
    app = {
        "orbit_pivot": "default",
        "orbit_style": "turntable",
        "zoom_mode": "default",
    }

    assert effective_scheme(general, app) == {
        "orbit_pivot": "screen_center",
        "orbit_style": "turntable",
        "zoom_mode": "to_cursor",
    }


def test_malformed_json_falls_back_without_overwriting_source(config_dir):
    path = config_dir / "config.json"
    malformed = '{"version": 8, "general": '
    path.write_text(malformed, encoding="utf-8")

    cfg = Config().load()

    assert cfg.snapshot().global_value("input.mode.default") == "3d"
    assert path.read_text(encoding="utf-8") == malformed


def test_foreground_identity_does_not_fall_back_to_selected_settings_app(monkeypatch):
    app = App.__new__(App)
    app.onshape_bridge = SimpleNamespace(
        is_connected=lambda: False, is_viewport_focused=lambda: False)
    app.config = SimpleNamespace(data={
        "active_app": "blender",
        "apps": {"blender": {"enabled": True}},
    })
    monkeypatch.setattr(app_mod, "foreground_process_name", lambda: "notepad")

    assert app._foreground_app_key() is None
    assert app._active_app_key() is None


def test_onshape_foreground_identity_requires_browser_and_connected_bridge(monkeypatch):
    app = App.__new__(App)
    connected = [False]
    focused = [False]
    app.onshape_bridge = SimpleNamespace(
        is_connected=lambda: connected[0], is_viewport_focused=lambda: focused[0])
    monkeypatch.setattr(app_mod, "foreground_process_name", lambda: "chrome.exe")

    assert app._foreground_app_key() is None
    connected[0] = True
    assert app._foreground_app_key() is None
    focused[0] = True
    assert app._foreground_app_key() == "onshape"


def test_mapping_snapshot_owns_the_selected_apps_immutable_host_baseline(isolated_config):
    engine = OutputEngine(Config().load())

    engine.set_active_bindings("fusion360")
    fusion_mapping = engine._mapping
    engine.set_active_bindings("rhino")

    assert fusion_mapping.app_key == "fusion360"
    assert fusion_mapping.host_baseline is host_baseline("fusion360")
    assert engine._mapping.app_key == "rhino"
    assert engine._mapping.host_baseline is host_baseline("rhino")


def test_broker_isolates_navigation_to_the_active_target():
    """The Phase 4 regression replaces the frozen broadcast-bug characterization."""

    class _Connection:
        def __init__(self):
            self.payloads = []

        def sendall(self, payload):
            self.payloads.append(payload)

    broker = NavBroker(0)
    first = _Connection()
    second = _Connection()
    broker._clients = [
        SimpleNamespace(conn=first, app="blender", version="1", pid=1),
        SimpleNamespace(conn=second, app="fusion360", version="1", pid=2),
    ]
    broker.activate_target("blender")
    broker.submit("blender", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    broker._flush_once(force=True)

    assert second.payloads == []
    frame = json.loads(first.payloads[0])
    assert frame["o"] == [1.0, 2.0, 3.0]
    assert frame["p"] == [4.0, 5.0]
    assert frame["z"] == 6.0
