#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Drive and summarize the PMW3610 forced-Rest wake diagnostic.

Set ``PMW_WAKE_STRESS`` to 1 in ``firmware/PMW3610/PMW3610.ino``, flash the
SuperMini, keep the ball stationary, close Arduino Serial Monitor, and run:

    python tools/pmw3610_wake_stress.py --serial auto --cycles 100 --log wake.log

Each command makes the diagnostic firmware force both sensors into Rest1, return
them to normal operation, and capture raw burst data through the transition.
This forced-mode path is deliberately separate from production power management.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Sequence


@dataclass(frozen=True)
class WakeEvent:
    cycle: int
    elapsed_us: int
    motion_mask: int
    dx_l: int
    dy_l: int
    dx_r: int
    dy_r: int
    squal_l: int
    squal_r: int
    shutter_l: int
    shutter_r: int


@dataclass(frozen=True)
class WakeSummary:
    cycle: int
    rest_mode_l: int
    rest_mode_r: int
    end_mode_l: int
    end_mode_r: int
    event_frames: int
    total_abs_delta: int
    peak_abs_delta: int
    overflow: bool


@dataclass(frozen=True)
class WakeReady:
    rest_ms: int
    sample_ms: int


def parse_wake_line(line: str) -> WakeEvent | WakeSummary | WakeReady | None:
    parts = [part.strip() for part in line.strip().split(",")]
    if len(parts) < 2 or parts[0] != "WAKE":
        return None
    try:
        values = [int(part) for part in parts[2:]]
    except ValueError as exc:
        raise ValueError(f"malformed WAKE record: {line.strip()}") from exc
    if parts[1] == "event" and len(values) == 11:
        return WakeEvent(*values)
    if parts[1] == "summary" and len(values) == 9:
        return WakeSummary(*values[:-1], overflow=bool(values[-1]))
    if parts[1] == "ready" and len(values) == 2:
        return WakeReady(*values)
    raise ValueError(f"unknown WAKE record shape: {line.strip()}")


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for serial capture (pip install pyserial)"
        ) from exc
    return [
        f"  {port.device}: {port.description} [{port.hwid}]"
        for port in list_ports.comports()
    ]


def resolve_serial_port(requested: str) -> str:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for serial capture (pip install pyserial)"
        ) from exc
    if requested.casefold() != "auto":
        return requested
    ports = list(list_ports.comports())
    matches = [
        port for port in ports
        if "VID:PID=1209:5285" in (port.hwid or "").upper()
        or "VID:PID=1209:5284" in (port.hwid or "").upper()
    ]
    if len(matches) == 1:
        print(f"Auto-selected {matches[0].device} ({matches[0].description})")
        return matches[0].device
    if not matches and len(ports) == 1:
        print(f"Auto-selected sole port {ports[0].device} ({ports[0].description})")
        return ports[0].device
    listed = "\n".join(list_serial_ports()) or "  (none)"
    raise SystemExit(
        "Could not auto-select the SuperMini serial port.\n"
        f"Available ports:\n{listed}\nPass --serial COMx explicitly."
    )


def capture(
    port: str,
    cycles: int,
    baud: int,
    cycle_timeout: float,
    log_path: Path | None,
) -> tuple[list[WakeEvent], list[WakeSummary]]:
    try:
        import serial  # type: ignore
        from serial import SerialException  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for serial capture (pip install pyserial)"
        ) from exc
    try:
        stream = serial.Serial(port, baud, timeout=0.1)
    except SerialException as exc:
        listed = "\n".join(list_serial_ports()) or "  (none)"
        raise SystemExit(
            f"could not open {port!r}: {exc}\nAvailable ports:\n{listed}\n"
            "Close Arduino Serial Monitor and retry."
        ) from exc

    events: list[WakeEvent] = []
    summaries: list[WakeSummary] = []
    log_file = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("w", encoding="utf-8", newline="\n")
    try:
        with stream:
            time.sleep(2.2)  # opening CDC can reset the board
            stream.reset_input_buffer()
            print(f"Driving {cycles} stationary wake cycles on {port} @ {baud}.")
            for requested_cycle in range(1, cycles + 1):
                stream.write(b"C\n")
                deadline = time.monotonic() + cycle_timeout
                completed = None
                while time.monotonic() < deadline:
                    raw = stream.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if log_file is not None:
                        log_file.write(line + "\n")
                        log_file.flush()
                    record = parse_wake_line(line)
                    if isinstance(record, WakeEvent):
                        events.append(record)
                    elif isinstance(record, WakeSummary):
                        summaries.append(record)
                        completed = record
                        break
                if completed is None:
                    raise SystemExit(
                        f"cycle {requested_cycle} timed out; confirm "
                        "PMW_WAKE_STRESS=1 and close other serial clients")
                print(
                    f"  {requested_cycle}/{cycles}: sensor cycle {completed.cycle}, "
                    f"frames={completed.event_frames}, "
                    f"peak={completed.peak_abs_delta}, "
                    f"total={completed.total_abs_delta}",
                    flush=True,
                )
    finally:
        if log_file is not None:
            log_file.close()
    return events, summaries


def load_log(path: Path) -> tuple[list[WakeEvent], list[WakeSummary]]:
    if not path.is_file():
        raise SystemExit(f"wake log does not exist: {path}")
    events: list[WakeEvent] = []
    summaries: list[WakeSummary] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = parse_wake_line(line)
        if isinstance(record, WakeEvent):
            events.append(record)
        elif isinstance(record, WakeSummary):
            summaries.append(record)
    if not summaries:
        raise SystemExit(f"no WAKE summary records in {path}")
    return events, summaries


def report(events: Sequence[WakeEvent], summaries: Sequence[WakeSummary]) -> None:
    dirty = [row for row in summaries if row.event_frames or row.total_abs_delta]
    rest_modes = Counter(
        (row.rest_mode_l, row.rest_mode_r) for row in summaries)
    end_modes = Counter(
        (row.end_mode_l, row.end_mode_r) for row in summaries)
    print()
    print(f"Cycles: {len(summaries)}")
    print(f"Cycles with reported/nonzero frames: {len(dirty)}")
    print(f"Captured event records: {len(events)}")
    print(f"Peak absolute single-axis delta: "
          f"{max((row.peak_abs_delta for row in summaries), default=0)}")
    print(f"Total absolute delta: {sum(row.total_abs_delta for row in summaries)}")
    print(f"Overflow cycles: {sum(row.overflow for row in summaries)}")
    print(f"Forced-rest observations (L, R): {dict(rest_modes)}")
    print(f"End-of-window observations (L, R): {dict(end_modes)}")
    print()
    if dirty:
        print(
            "The capture contains candidate wake transients. If the ball stayed "
            "stationary, preserve the log and compare event timing, SQUAL, and shutter."
        )
    else:
        print(
            "No motion was reported during these forced transitions. This does not "
            "replace normal-mode sleep/wake and pointer testing."
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--serial", nargs="?", const="auto",
                        help="Serial port (COMx), or omit the value to auto-select")
    source.add_argument("--input", type=Path, help="Analyze a previously saved WAKE log")
    source.add_argument("--list-ports", action="store_true")
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--cycle-timeout", type=float, default=5.0)
    parser.add_argument("--log", type=Path, help="Save raw firmware records for comparison")
    args = parser.parse_args(argv)

    if args.cycles <= 0:
        parser.error("--cycles must be positive")
    if args.list_ports:
        print("Available serial ports:")
        print("\n".join(list_serial_ports()) or "  (none)")
        return 0
    if args.input is not None:
        events, summaries = load_log(args.input)
    else:
        port = resolve_serial_port(args.serial)
        events, summaries = capture(
            port, args.cycles, args.baud, args.cycle_timeout, args.log)
    report(events, summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
