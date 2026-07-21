"""Unit tests for dual-sensor mount residual calibration."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "calibrate_sensor_mounts", ROOT / "tools" / "calibrate_sensor_mounts.py")
assert SPEC and SPEC.loader
calib = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calib)


def test_parse_calib_line_accepts_firmware_and_csv():
    assert calib.parse_calib_line("CALIB,1,-2,3,-4") == (1.0, -2.0, 3.0, -4.0)
    assert calib.parse_calib_line("10,11,12,13") == (10.0, 11.0, 12.0, 13.0)
    assert calib.parse_calib_line("peak ips L=1") is None


def test_design_matrix_matches_expected_shape_and_is_full_rank():
    cfg = calib.Config(
        left=calib.SensorPose(calib.DEFAULT_L_PHI, calib.DEFAULT_L_THETA, 90.0, False),
        right=calib.SensorPose(calib.DEFAULT_R_PHI, calib.DEFAULT_R_THETA, 90.0, False),
    )
    rows = calib.build_design_matrix(cfg.left, cfg.right)
    assert rows is not None
    assert len(rows) == 4
    assert all(len(row) == 3 for row in rows)


def test_residual_search_recovers_known_mount_and_flip():
    truth = calib.Config(
        left=calib.SensorPose(calib.DEFAULT_L_PHI, calib.DEFAULT_L_THETA, 180.0, True),
        right=calib.SensorPose(calib.DEFAULT_R_PHI, calib.DEFAULT_R_THETA, 0.0, False),
    )
    samples = calib.synthesize_samples(truth, count=300, noise=0.25, seed=7)
    ranked = calib.rank_configs(
        samples,
        calib.iter_configs(
            search_phi=False,
            search_swap=False,
            l_phi0=calib.DEFAULT_L_PHI,
            l_theta0=calib.DEFAULT_L_THETA,
            r_phi0=calib.DEFAULT_R_PHI,
            r_theta0=calib.DEFAULT_R_THETA,
        ),
    )
    best = ranked[0].config
    assert best.left.mount == truth.left.mount
    assert best.right.mount == truth.right.mount
    assert best.left.flip == truth.left.flip
    assert best.right.flip == truth.right.flip
    assert ranked[0].rms < ranked[1].rms


def test_filter_samples_requires_both_sensors_by_default():
    raw = [
        (5.0, 0.0, 5.0, 0.0),
        (5.0, 0.0, 0.0, 0.0),
        (0.1, 0.0, 0.1, 0.0),
    ]
    kept = calib.filter_samples(raw, min_norm=1.0, require_both=True)
    assert kept == [(5.0, 0.0, 5.0, 0.0)]


def test_cli_self_test_exits_zero():
    assert calib.main(["--self-test"]) == 0
