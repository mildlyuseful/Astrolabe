# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Measure delivered pointer-report timing from the host, independent of firmware.

Samples the OS cursor position and records when it changes, so it reports what actually reached
Windows rather than what the firmware believes it sent. That makes it usable to tell a firmware
production problem apart from a delivery problem in a transport or HID stack.

Run it, then move the trackball. It waits for the first movement before it starts timing, so
there is nothing to synchronize:

    uv run python tools/pointer_timing.py

Reading the result: the median gap is the delivered report period. Compare it against the
firmware's configured send interval. A median far above that interval means reports are being
delayed downstream of the accumulator; a long tail with large px/update means motion is arriving
in batches rather than continuously.
"""

import ctypes
from ctypes import wintypes
import statistics
import sys
import time


ARM_TIMEOUT_S = 75.0
MEASURE_S = 12.0
_BUCKETS = ("<2ms", "2-5ms", "5-9ms", "9-20ms", "20-35ms", ">35ms")


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _bucket(gap_ms):
    if gap_ms < 2:
        return "<2ms"
    if gap_ms < 5:
        return "2-5ms"
    if gap_ms < 9:
        return "5-9ms"
    if gap_ms < 20:
        return "9-20ms"
    if gap_ms < 35:
        return "20-35ms"
    return ">35ms"


def main():
    if not sys.platform.startswith("win"):
        print("pointer_timing reads the Windows cursor position and only runs on Windows")
        return 1

    user32 = ctypes.WinDLL("user32")
    user32.GetCursorPos.argtypes = [ctypes.POINTER(_Point)]
    point = _Point()

    def position():
        user32.GetCursorPos(ctypes.byref(point))
        return (point.x, point.y)

    print(f"move the trackball to begin (waiting up to {ARM_TIMEOUT_S:.0f}s)...")
    last = position()
    deadline = time.perf_counter() + ARM_TIMEOUT_S
    while time.perf_counter() < deadline:
        current = position()
        if current != last:
            last = current
            break
        time.sleep(0.001)
    else:
        print("no cursor motion seen; nothing measured")
        return 1

    print(f"measuring {MEASURE_S:.0f}s -- keep moving")
    events = []
    end = time.perf_counter() + MEASURE_S
    while time.perf_counter() < end:
        current = position()
        if current != last:
            events.append((time.perf_counter(), current[0] - last[0], current[1] - last[1]))
            last = current
        time.sleep(0.0002)

    if len(events) < 10:
        print(f"only {len(events)} updates captured; move continuously for the whole window")
        return 1

    span = events[-1][0] - events[0][0]
    gaps = [(events[i][0] - events[i - 1][0]) * 1000.0 for i in range(1, len(events))]
    magnitudes = [abs(dx) + abs(dy) for _t, dx, dy in events]
    ordered = sorted(gaps)
    counts = {}
    for gap in gaps:
        name = _bucket(gap)
        counts[name] = counts.get(name, 0) + 1

    print(f"updates      : {len(events)} in {span:.1f}s = {len(events) / span:.0f} Hz")
    print(f"gap ms median: {statistics.median(gaps):.2f}")
    print(f"gap p90/p99  : {ordered[int(len(ordered) * 0.9)]:.2f} / "
          f"{ordered[int(len(ordered) * 0.99)]:.2f}   max {max(gaps):.1f}")
    print(f"px/update med: {statistics.median(magnitudes):.1f}   max {max(magnitudes)}")
    print("gap histogram:", {name: counts[name] for name in _BUCKETS if name in counts})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
