# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Offline contracts for the PMW3610 forced-Rest wake diagnostic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = (ROOT / "firmware" / "PMW3610" / "PMW3610.ino").read_text(
    encoding="utf-8")
SPEC = importlib.util.spec_from_file_location(
    "pmw3610_wake_stress", ROOT / "tools" / "pmw3610_wake_stress.py")
assert SPEC and SPEC.loader
stress = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stress
SPEC.loader.exec_module(stress)


def test_production_firmware_uses_automatic_rest_and_isolates_forced_mode_stress():
    assert "#define PERFORMANCE_INIT        0x0D" in FIRMWARE
    assert "setForceAwake" not in FIRMWARE
    assert "PERFORMANCE_FORCE_AWAKE" not in FIRMWARE
    assert "#ifndef PMW_WAKE_STRESS" in FIRMWARE
    assert "if(command == 'C' || command == 'c') runWakeStressCycle();" in FIRMWARE


def test_parse_wake_event_preserves_transition_telemetry():
    event = stress.parse_wake_line(
        "WAKE,event,7,2300,3,10,-20,30,-40,51,52,1000,1100")

    assert event == stress.WakeEvent(
        cycle=7,
        elapsed_us=2300,
        motion_mask=3,
        dx_l=10,
        dy_l=-20,
        dx_r=30,
        dy_r=-40,
        squal_l=51,
        squal_r=52,
        shutter_l=1000,
        shutter_r=1100,
    )


def test_parse_wake_summary_and_ignore_unrelated_serial_text():
    summary = stress.parse_wake_line("WAKE,summary,7,1,1,0,0,4,100,40,0")

    assert summary == stress.WakeSummary(
        cycle=7,
        rest_mode_l=1,
        rest_mode_r=1,
        end_mode_l=0,
        end_mode_r=0,
        event_frames=4,
        total_abs_delta=100,
        peak_abs_delta=40,
        overflow=False,
    )
    assert stress.parse_wake_line("PMW3610 Astrolabe bring-up") is None


def test_saved_log_can_be_reanalyzed(tmp_path):
    path = tmp_path / "wake.log"
    path.write_text(
        "unrelated\n"
        "WAKE,event,1,1000,1,2,0,0,0,40,41,900,901\n"
        "WAKE,summary,1,1,1,1,1,1,2,2,0\n",
        encoding="utf-8",
    )

    events, summaries = stress.load_log(path)

    assert len(events) == len(summaries) == 1
    assert events[0].cycle == summaries[0].cycle == 1
