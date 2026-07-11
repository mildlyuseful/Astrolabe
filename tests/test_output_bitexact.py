"""Lock the cube/cursor output math: it must stay bit-identical at default config.

The SolidWorks work is purely additive (a new nav-delta consumer + wiring), so
OutputEngine.handle_packet must still produce exactly the same orientation / pan / distance
and the same synthesized mouse input. The golden constants below were captured from the
pre-change engine at default config; an exact-equality compare catches any drift.
"""
import struct

import pytest

from trackball_daemon import output as output_mod
from trackball_daemon.config import Config
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
    engine.cfg.data["general"]["axis_orientation"] = {
        "source": [1, 0, 2], "invert": [False, True, False]}
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
    engine.cfg.data["general"]["axis_orientation"] = {
        "source": [1, 0, 2], "invert": [False, False, False]}
    engine.cfg.data["apps"]["fusion360"]["bindings"]["orbit"]["axis_source"] = [0, 1, 2]
    engine.cfg.data["apps"]["rhino"]["bindings"]["orbit"]["axis_source"] = [2, 0, 1]
    monkeypatch.setattr(output_mod, "shift_held", lambda: False)
    nav = []
    engine.nav_sink = lambda *a: nav.append(a)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.set_active_bindings("fusion360")
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))
    engine.set_active_bindings("rhino")
    engine.handle_packet(_pkt(0.01, 0.02, 0.03))

    assert nav[0][:3] == pytest.approx((0.02, 0.01, 0.03))
    assert nav[1][:3] == pytest.approx((0.03, 0.02, 0.01))
