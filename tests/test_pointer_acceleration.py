# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Pointer acceleration is bounded, time-based, and isolated from 3D navigation."""
import struct

import pytest

from trackball_daemon import output as output_mod
from trackball_daemon.config import Config
from trackball_daemon.output import OutputEngine, _pointer_acceleration_multiplier


def _pkt(rx, ry, rz):
    return struct.pack("<fff", rx, ry, rz)


def _configure(engine, curve, *, onset=0.5, ramp=0.5, max_gain=3.0):
    with engine.cfg.transaction() as tx:
        tx.set_global("pointer.acceleration.curve", curve)
        tx.set_global("pointer.acceleration.onset", onset)
        tx.set_global("pointer.acceleration.ramp", ramp)
        tx.set_global("pointer.acceleration.max_gain", max_gain)
    engine.apply_config()


@pytest.fixture
def engine(isolated_config):
    return OutputEngine(Config().load())


def test_curve_math_is_bounded_and_distinguishes_linear_from_smooth():
    assert _pointer_acceleration_multiplier("off", 10, 0.5, 1.0, 3.0) == 1.0
    assert _pointer_acceleration_multiplier("linear", 0.75, 0.5, 1.0, 3.0) == 1.5
    assert _pointer_acceleration_multiplier(
        "smooth", 0.75, 0.5, 1.0, 3.0) == pytest.approx(1.3125)
    assert _pointer_acceleration_multiplier("linear", 10, 0.5, 1.0, 3.0) == 3.0


def test_pointer_acceleration_uses_elapsed_time_and_preserves_fractional_motion(
        engine, monkeypatch):
    moves = []
    monkeypatch.setattr(
        output_mod, "send_mouse",
        lambda dx=0, dy=0, wheel=0: moves.append((dx, dy, wheel)))
    _configure(engine, "linear")
    engine.set_mode(OutputEngine.MODE_CURSOR)

    engine.handle_packet(_pkt(0.0, 0.01, 0.0), timestamp=10.0)
    engine.handle_packet(_pkt(0.0, 0.01, 0.0), timestamp=10.01)

    # The first sample establishes the clock (2.16 px); the second is 1 rad/s and reaches
    # the configured 3x cap (6.48 px). Fractional carry produces 2 then 6 whole pixels.
    assert moves == [(2, 0, 0), (6, 0, 0)]


def test_equal_displacement_over_a_longer_interval_gets_less_acceleration(
        engine, monkeypatch):
    moves = []
    monkeypatch.setattr(
        output_mod, "send_mouse",
        lambda dx=0, dy=0, wheel=0: moves.append((dx, dy, wheel)))
    _configure(engine, "linear")
    engine.set_mode(OutputEngine.MODE_CURSOR)
    engine.handle_packet(_pkt(0.0, 0.0, 0.0), timestamp=20.0)

    engine.handle_packet(_pkt(0.0, 0.01, 0.0), timestamp=20.04)

    assert moves == [(2, 0, 0)]


def test_live_pointer_sensitivity_scales_accelerated_motion(engine, monkeypatch):
    moves = []
    monkeypatch.setattr(
        output_mod, "send_mouse",
        lambda dx=0, dy=0, wheel=0: moves.append((dx, dy, wheel)))
    engine.cfg.add_listener(lambda _event: engine.apply_config())
    _configure(engine, "linear")
    engine.set_mode(OutputEngine.MODE_CURSOR)

    engine.cfg.set_global("pointer.cursor.gain", 64.0)
    engine.handle_packet(_pkt(0.0, 0.03125, 0.0), timestamp=30.0)
    engine.handle_packet(_pkt(0.0, 0.03125, 0.0), timestamp=30.01)

    engine.cfg.set_global("pointer.cursor.gain", 32.0)
    engine.handle_packet(_pkt(0.0, 0.03125, 0.0), timestamp=40.0)
    engine.handle_packet(_pkt(0.0, 0.03125, 0.0), timestamp=40.01)

    assert moves == [(2, 0, 0), (6, 0, 0), (1, 0, 0), (3, 0, 0)]


def test_pointer_acceleration_never_changes_3d_navigation(engine):
    _configure(engine, "linear", onset=0.0, ramp=0.001, max_gain=10.0)
    nav = []
    engine.nav_sink = lambda *values: nav.append(values)
    engine.set_mode(OutputEngine.MODE_CUBE)

    engine.handle_packet(_pkt(0.01, 0.02, 0.03), timestamp=30.0)
    engine.handle_packet(_pkt(0.01, 0.02, 0.03), timestamp=30.001)

    assert nav == [
        pytest.approx((0.01, 0.02, 0.03, 0.0, 0.0, 0.0)),
        pytest.approx((0.01, 0.02, 0.03, 0.0, 0.0, 0.0)),
    ]
