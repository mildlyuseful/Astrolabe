# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Static lifecycle contracts for the active PMW3610 validation firmware."""

import re
from pathlib import Path


FIRMWARE = Path("firmware/PMW3610/PMW3610.ino").read_text(encoding="utf-8")


def _define(name: str) -> int:
    match = re.search(rf"^#define {name}\s+(\d+)", FIRMWARE, re.MULTILINE)
    assert match
    return int(match.group(1))


def test_wake_motion_is_drained_until_a_bounded_quiet_tail():
    minimum = _define("WAKE_SETTLE_MIN_MS")
    quiet = _define("WAKE_SETTLE_QUIET_MS")
    maximum = _define("WAKE_SETTLE_MAX_MS")
    assert 0 < quiet <= minimum < maximum
    assert minimum + quiet < maximum

    guard_start = FIRMWARE.index("static bool rejectWakeMotion")
    guard_end = FIRMWARE.index("static void onConnect", guard_start)
    guard = FIRMWARE[guard_start:guard_end]
    assert "deadlinePending(nowMs, g_wakeSettleUntilMs)" in guard
    assert "nowMs + WAKE_SETTLE_QUIET_MS" in guard
    assert "g_wakeSettleMaxUntilMs" in guard

    reject = FIRMWARE.index("if(rejectWakeMotion(nowMs, a.isMotion || b.isMotion))")
    fuse = FIRMWARE.index("bool sawMotion = a.isMotion || b.isMotion;", reject)
    assert reject < fuse
