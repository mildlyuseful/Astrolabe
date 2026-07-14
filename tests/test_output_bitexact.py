"""Lock the cube/cursor output math: it must stay bit-identical at default config.

The SolidWorks work is purely additive (a new nav-delta consumer + wiring), so
OutputEngine.handle_packet must still produce exactly the same orientation / pan / distance
and the same synthesized mouse input. The golden constants below were captured from the
pre-change engine at default config; an exact-equality compare catches any drift.
"""
import struct
import threading

import pytest

from trackball_daemon import output as output_mod
from trackball_daemon.config import Config, host_baseline_payload
from trackball_daemon.output import OutputEngine


def _pkt(rx, ry, rz):
    return struct.pack("<fff", rx, ry, rz)


@pytest.fixture
def engine(isolated_config):
    return OutputEngine(Config().load())


def test_cube_orbit_bit_exact(engine, monkeypatch):
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))
    assert engine.orientation == (
        0.9998250051119297, 0.004999708226698306,
        0.009999416453396612, 0.014999124680094919)
    assert nav[-1] == (
        0.009999999776482582, 0.019999999552965164,
        0.029999999329447746, 0.0, 0.0, 0.0)


def test_cube_pan_bit_exact(engine, monkeypatch):
    monkeypatch.setattr(output_mod, "shift_held", lambda: True)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)
    engine.handle_packet(_pkt(0.02, 0.03, 0.001))           # twist below deadzone -> pan
    assert engine.pan_x == 0.029999999329447746
    assert engine.pan_y == -0.019999999552965164
    assert nav[-1] == (0.0, 0.0, 0.0, 0.029999999329447746, -0.019999999552965164, 0.0)


def test_cube_zoom_bit_exact(engine, monkeypatch):
    monkeypatch.setattr(output_mod, "shift_held", lambda: True)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)
    engine.handle_packet(_pkt(0.001, 0.001, 0.05))          # twist dominant -> zoom
    assert engine.distance == 5.949999999254942
    assert nav[-1] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.05000000074505806)


def test_cursor_move_bit_exact(engine, monkeypatch):
    moves = []
    monkeypatch.setattr(output_mod, "send_mouse",
                        lambda dx=0, dy=0, wheel=0: moves.append((dx, dy, wheel)))
    engine.set_mode(OutputEngine.MODE_CURSOR)
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))
    assert moves == [(4, 2, 0)]


def test_cursor_scroll_bit_exact(engine, monkeypatch):
    moves = []
    monkeypatch.setattr(output_mod, "send_mouse",
                        lambda dx=0, dy=0, wheel=0: moves.append((dx, dy, wheel)))
    engine.set_mode(OutputEngine.MODE_CURSOR)
    engine.handle_packet(_pkt(0.0, 0.0, 0.05))
    assert moves == [(0, 0, 1)]


def test_global_orientation_precedes_both_3d_and_cursor_routing(engine, monkeypatch):
    # Logical XYZ <- raw Y, X, Z, with logical Y inverted (a 90-degree base reorientation).
    engine.cfg.set_ui_value(("general", "axis_orientation", "source"), [1, 0, 2])
    engine.cfg.set_ui_value(("general", "axis_orientation", "invert"), [False, True, False])
    engine.apply_config()

    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    engine.set_mode(OutputEngine.MODE_CUBE)
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))
    assert nav[-1][:3] == pytest.approx((0.02, -0.01, 0.03))

    moves = []
    monkeypatch.setattr(output_mod, "send_mouse",
                        lambda dx=0, dy=0, wheel=0: moves.append((dx, dy, wheel)))
    engine.set_mode(OutputEngine.MODE_CURSOR)
    engine.handle_packet(_pkt(0.01, 0.02, 0.0))
    assert moves == [(-2, 4, 0)]


def test_global_orientation_composes_with_distinct_per_app_axis_routes(engine, monkeypatch):
    engine.cfg.set_ui_value(("general", "axis_orientation", "source"), [1, 0, 2])
    with engine.cfg.transaction() as tx:
        for axis, source in zip("xyz", (0, 1, 2)):
            tx.set_app("fusion360", f"navigation.routing.orbit.{axis}.source", source)
        for axis, source in zip("xyz", (2, 0, 1)):
            tx.set_app("rhino", f"navigation.routing.orbit.{axis}.source", source)
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.set_active_bindings("fusion360")
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))
    engine.set_active_bindings("rhino")
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))

    fusion = host_baseline_payload("fusion360")["orbit"]
    rhino = host_baseline_payload("rhino")["orbit"]
    assert nav[0][:3] == pytest.approx(
        (0.02 * fusion[0], 0.01 * fusion[1], 0.03 * fusion[2]))
    assert nav[1][:3] == pytest.approx(
        (0.03 * rhino[0], 0.02 * rhino[1], 0.01 * rhino[2]))


def test_lean_host_baseline_composes_with_user_inversion(engine, monkeypatch):
    engine.cfg.set_app("fusion360", "navigation.routing.orbit.x.invert", True)
    engine.set_active_bindings("fusion360")
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.handle_packet(_pkt(0.01, 0.02, 0.03))

    baseline = host_baseline_payload("fusion360")["orbit"]
    assert nav[-1][:3] == pytest.approx(
        (-0.01 * baseline[0], 0.02 * baseline[1], 0.03 * baseline[2]))


def test_rich_host_baseline_is_deferred_to_mode_aware_addon(engine, monkeypatch):
    engine.set_active_bindings("blender")
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.handle_packet(_pkt(0.01, 0.02, 0.03))

    # Rich integrations receive raw user-routed deltas; app.py supplies current host factors in adv.
    assert nav[-1][:3] == pytest.approx((0.01, 0.02, 0.03))


def test_ordinary_profile_twist_action_routes_before_host_alignment(engine, monkeypatch):
    engine.cfg.set_app("fusion360", "navigation.orbit.twist_action", "zoom")
    engine.set_active_bindings("fusion360")
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *args: nav.append(args)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.handle_packet(_pkt(0.01, 0.02, 0.03))

    baseline = host_baseline_payload("fusion360")
    assert nav[-1][:3] == pytest.approx(
        (0.01 * baseline["orbit"][0], 0.02 * baseline["orbit"][1], 0.0))
    assert nav[-1][5] == pytest.approx(0.03 * baseline["zoom"])


def test_ordinary_profile_twist_none_drops_twist(engine, monkeypatch):
    engine.cfg.set_app("rhino", "navigation.orbit.twist_action", "none")
    engine.set_active_bindings("rhino")
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *args: nav.append(args)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.handle_packet(_pkt(0.01, 0.02, 0.03))

    assert nav[-1][2] == 0.0
    assert nav[-1][5] == 0.0


def test_config_reload_and_focus_switch_publish_one_coherent_mapping(engine, monkeypatch):
    """A slow config refresh cannot overwrite a newer foreground-app switch."""
    engine.set_active_bindings("fusion360")
    original = engine._build_mapping
    reload_entered = threading.Event()
    release_reload = threading.Event()
    switch_started = threading.Event()
    switch_done = threading.Event()

    def blocking_build(app_key):
        if threading.current_thread().name == "mapping-reload":
            reload_entered.set()
            release_reload.wait(2.0)
        return original(app_key)

    monkeypatch.setattr(engine, "_build_mapping", blocking_build)
    reload_thread = threading.Thread(target=engine.apply_config, name="mapping-reload")

    def switch_app():
        switch_started.set()
        engine.set_active_bindings("rhino")
        switch_done.set()

    switch_thread = threading.Thread(target=switch_app, name="mapping-switch")
    reload_thread.start()
    assert reload_entered.wait(1.0)
    switch_thread.start()
    assert switch_started.wait(1.0)
    assert not switch_done.wait(0.05)       # serialized behind the in-progress snapshot build
    release_reload.set()
    reload_thread.join(2.0)
    switch_thread.join(2.0)

    assert not reload_thread.is_alive() and not switch_thread.is_alive()
    assert switch_done.is_set()
    assert engine._bindings_app == engine._mapping.app_key == "rhino"
