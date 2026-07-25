#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Enumerate dual-sensor mount/flip (and optional 90-degree pose) configs by residual.

The firmware least-squares fusion is over-determined (4 measurements, 3 rotation DOF).
A wrong mount/flip makes the two PMW3610s disagree, so ||A w - m|| stays large. The
true discrete config (mount multiples of 90°, flip 0/1) minimizes that residual across
varied ball motion.

This does NOT choose ROT_SIGN_* / CURSOR_* host signs — those are a separate "which way
is up" step after the sensors agree with each other.

Capture
-------
1. In firmware/PMW3610/PMW3610.ino set ``#define CALIB_SERIAL 1``, flash.
2. Open the SuperMini USB serial port at 115200.
3. Roll the ball through pitch, yaw, and roll for ~20–30 s (vary axes; avoid pure spin
   about one axis only).
4. Run::

     python tools/calibrate_sensor_mounts.py --serial auto --seconds 30
     python tools/calibrate_sensor_mounts.py --serial COM21 --seconds 30 --save-csv capture.csv
     python tools/calibrate_sensor_mounts.py --list-ports
     python tools/calibrate_sensor_mounts.py --csv capture.csv

Output is ranked configs plus the ``#define`` block to paste into the sketch.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# Nominal PCB geometry from the current PMW3610 sketch (degrees).
DEFAULT_L_PHI = 140.0
DEFAULT_L_THETA = 130.0
DEFAULT_R_PHI = 230.0
DEFAULT_R_THETA = 130.0
MOUNT_GRID = (0.0, 90.0, 180.0, 270.0)
PHI_OFFSETS = (0.0, 90.0, 180.0, 270.0)
THETA_OFFSETS = (0.0, -90.0, 90.0)

Vec3 = tuple[float, float, float]
Mat3 = list[list[float]]


@dataclass(frozen=True)
class SensorPose:
    phi: float
    theta: float
    mount: float
    flip: bool


@dataclass(frozen=True)
class Config:
    left: SensorPose
    right: SensorPose
    swapped: bool = False

    def firmware_defines(self) -> str:
        l, r = self.left, self.right
        return "\n".join([
            f"#define SENSOR_L_PHI    {l.phi:.1f}f",
            f"#define SENSOR_L_THETA  {l.theta:.1f}f",
            f"#define SENSOR_R_PHI    {r.phi:.1f}f",
            f"#define SENSOR_R_THETA  {r.theta:.1f}f",
            f"#define SENSOR_L_MOUNT_DEG  {l.mount:.1f}f",
            f"#define SENSOR_L_FLIP       {1 if l.flip else 0}",
            f"#define SENSOR_R_MOUNT_DEG  {r.mount:.1f}f",
            f"#define SENSOR_R_FLIP       {1 if r.flip else 0}",
            f"// swapped_channels={self.swapped}",
        ])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v: Vec3) -> float:
    return math.sqrt(_dot(v, v))


def _normed(v: Vec3) -> Vec3:
    n = _norm(v)
    if n < 1e-12:
        return v
    return (v[0] / n, v[1] / n, v[2] / n)


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _local_frame(n: Vec3) -> tuple[Vec3, Vec3]:
    e_az = _cross((0.0, 0.0, 1.0), n)
    if _norm(e_az) < 1e-6:
        e_az = (1.0, 0.0, 0.0)
    else:
        e_az = _normed(e_az)
    e_up = _normed(_cross(n, e_az))
    return e_az, e_up


def _sensor_axes(n: Vec3, mount_deg: float, flip: bool) -> tuple[Vec3, Vec3]:
    e_az, e_up = _local_frame(n)
    bx = _scale(e_az, -1.0)
    by = e_up
    a = math.radians(mount_deg)
    ca, sa = math.cos(a), math.sin(a)
    fl = -1.0 if flip else 1.0
    dx = _add(_scale(bx, ca), _scale(by, sa))
    dy = _scale(_add(_scale(bx, -sa), _scale(by, ca)), fl)
    return dx, dy


def _normal(phi_deg: float, theta_deg: float) -> Vec3:
    phi = math.radians(phi_deg)
    theta = math.radians(theta_deg)
    return (
        math.sin(theta) * math.cos(phi),
        math.sin(theta) * math.sin(phi),
        math.cos(theta),
    )


def _invert3(m: Mat3) -> Mat3 | None:
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    if abs(det) < 1e-9:
        return None
    iv = 1.0 / det
    return [
        [
            (m[1][1] * m[2][2] - m[1][2] * m[2][1]) * iv,
            -(m[0][1] * m[2][2] - m[0][2] * m[2][1]) * iv,
            (m[0][1] * m[1][2] - m[0][2] * m[1][1]) * iv,
        ],
        [
            -(m[1][0] * m[2][2] - m[1][2] * m[2][0]) * iv,
            (m[0][0] * m[2][2] - m[0][2] * m[2][0]) * iv,
            -(m[0][0] * m[1][2] - m[0][2] * m[1][0]) * iv,
        ],
        [
            (m[1][0] * m[2][1] - m[1][1] * m[2][0]) * iv,
            -(m[0][0] * m[2][1] - m[0][1] * m[2][0]) * iv,
            (m[0][0] * m[1][1] - m[0][1] * m[1][0]) * iv,
        ],
    ]


def build_design_matrix(left: SensorPose, right: SensorPose) -> list[Vec3] | None:
    """Return firmware rows of A (4 x 3), or None if singular."""
    n_l = _normal(left.phi, left.theta)
    n_r = _normal(right.phi, right.theta)
    l_dx, l_dy = _sensor_axes(n_l, left.mount, left.flip)
    r_dx, r_dy = _sensor_axes(n_r, right.mount, right.flip)
    rows = [
        _cross(n_l, l_dx),
        _cross(n_l, l_dy),
        _cross(n_r, r_dx),
        _cross(n_r, r_dy),
    ]
    ata: Mat3 = [[0.0, 0.0, 0.0] for _ in range(3)]
    for i in range(3):
        for j in range(3):
            ata[i][j] = sum(rows[r][i] * rows[r][j] for r in range(4))
    if _invert3(ata) is None:
        return None
    return rows


def _solve_w(rows: list[Vec3], measurement: Sequence[float]) -> Vec3 | None:
    """w = (A^T A)^{-1} A^T m  (same pinv path as firmware)."""
    ata: Mat3 = [[0.0, 0.0, 0.0] for _ in range(3)]
    atb = [0.0, 0.0, 0.0]
    for r in range(4):
        for i in range(3):
            atb[i] += rows[r][i] * measurement[r]
            for j in range(3):
                ata[i][j] += rows[r][i] * rows[r][j]
    inv = _invert3(ata)
    if inv is None:
        return None
    return (
        _dot(inv[0], atb),
        _dot(inv[1], atb),
        _dot(inv[2], atb),
    )


def sample_residual_sq(rows: list[Vec3], measurement: Sequence[float]) -> float:
    w = _solve_w(rows, measurement)
    if w is None:
        return float("inf")
    err = 0.0
    for r in range(4):
        pred = _dot(rows[r], w)
        d = pred - measurement[r]
        err += d * d
    return err


def score_config(rows: list[Vec3], samples: Sequence[Sequence[float]]) -> tuple[float, float]:
    residuals = [sample_residual_sq(rows, row) for row in samples]
    residuals.sort()
    mean = sum(residuals) / len(residuals)
    mid = residuals[len(residuals) // 2]
    return math.sqrt(mean), math.sqrt(mid)


def parse_calib_line(line: str) -> tuple[float, float, float, float] | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("CALIB,"):
        # CALIB,dxL,dyL,dxR,dyR with optional trailing telemetry columns (SQUAL pair on
        # current firmware) — only the four deltas participate in the residual search.
        parts = line.split(",")
        if len(parts) < 5:
            return None
        try:
            return (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError:
            return None
    if "," in line and not line.lower().startswith("dx"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            return None
        try:
            return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
        except ValueError:
            return None
    return None


def load_csv(path: Path) -> list[tuple[float, float, float, float]]:
    rows: list[tuple[float, float, float, float]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        sample = fh.read(256)
        fh.seek(0)
        if "dx" in sample.lower():
            reader = csv.DictReader(fh)
            for row in reader:
                keys = {k.lower().strip(): k for k in row}

                def pick(*names: str) -> float:
                    for name in names:
                        if name in keys:
                            return float(row[keys[name]])
                    raise KeyError(names)

                rows.append((
                    pick("dx_l", "dxl", "a_dx", "dx_a"),
                    pick("dy_l", "dyl", "a_dy", "dy_a"),
                    pick("dx_r", "dxr", "b_dx", "dx_b"),
                    pick("dy_r", "dyr", "b_dy", "dy_b"),
                ))
        else:
            for line in fh:
                parsed = parse_calib_line(line)
                if parsed is not None:
                    rows.append(parsed)
    if not rows:
        raise SystemExit(f"no samples in {path}")
    return rows


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for serial capture (pip install pyserial)"
        ) from exc
    lines: list[str] = []
    for port in list_ports.comports():
        lines.append(f"  {port.device}: {port.description} [{port.hwid}]")
    return lines


def resolve_serial_port(requested: str | None) -> str:
    """Return an explicit port, or the SuperMini CDC port when --serial auto."""
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for serial capture (pip install pyserial)"
        ) from exc

    ports = list(list_ports.comports())
    if requested and requested.lower() != "auto":
        return requested

    # Adafruit/nRFMicro SuperMini USB CDC (boards.txt vid/pid).
    matches = [
        p for p in ports
        if "VID:PID=1209:5285" in (p.hwid or "").upper()
        or "VID:PID=1209:5284" in (p.hwid or "").upper()
    ]
    if len(matches) == 1:
        print(f"Auto-selected {matches[0].device} ({matches[0].description})")
        return matches[0].device
    if not matches and len(ports) == 1:
        print(f"Auto-selected sole port {ports[0].device} ({ports[0].description})")
        return ports[0].device

    listed = "\n".join(list_serial_ports()) or "  (none)"
    raise SystemExit(
        "Could not auto-select a serial port. Available ports:\n"
        f"{listed}\n"
        "Pass --serial COMx explicitly (close Arduino Serial Monitor first)."
    )


def capture_serial(port: str, seconds: float, baud: int = 115200) -> list[tuple[float, float, float, float]]:
    try:
        import serial  # type: ignore
        from serial import SerialException  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for --serial capture (pip install pyserial)"
        ) from exc

    rows: list[tuple[float, float, float, float]] = []
    print(f"Listening on {port} @ {baud} for {seconds:.0f}s — roll the ball on all axes…")
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except SerialException as exc:
        listed = "\n".join(list_serial_ports()) or "  (none)"
        raise SystemExit(
            f"could not open {port!r}: {exc}\n"
            f"Available ports:\n{listed}\n"
            "Close Arduino Serial Monitor / other apps using the port, then retry "
            "(SuperMini often renumerates after flash — try --serial auto)."
        ) from exc

    with ser:
        ser.reset_input_buffer()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore")
            parsed = parse_calib_line(line)
            if parsed is None:
                continue
            rows.append(parsed)
            if len(rows) % 50 == 0:
                print(f"  {len(rows)} samples", flush=True)
    if len(rows) < 20:
        raise SystemExit(
            f"only {len(rows)} samples — enable CALIB_SERIAL in firmware and keep moving"
        )
    print(f"Captured {len(rows)} samples")
    return rows


def _is_bus_garbage(dx_l: float, dy_l: float, dx_r: float, dy_r: float) -> bool:
    """True for classic SDIO failure patterns, not large real coalesced deltas."""
    def all_ones(dx: float, dy: float) -> bool:
        return dx == -1.0 and dy == -1.0

    def lone_exact_256(dx: float, dy: float, ox: float, oy: float) -> bool:
        # One sensor stuck at exactly +/-256 with the other quiet — framing glitch.
        quiet = abs(ox) < 1 and abs(oy) < 1
        hit = any(abs(abs(ax) - 256.0) <= 1.0 for ax in (dx, dy))
        return quiet and hit

    return (
        all_ones(dx_l, dy_l)
        or all_ones(dx_r, dy_r)
        or lone_exact_256(dx_l, dy_l, dx_r, dy_r)
        or lone_exact_256(dx_r, dy_r, dx_l, dy_l)
    )


def diagnose_samples(samples: Sequence[Sequence[float]]) -> None:
    """Print capture-quality hints that residual ranking cannot see."""
    n = len(samples)
    if n == 0:
        print("diagnose: no samples")
        return
    both = only_l = only_r = 0
    garbage = 0
    large = 0
    for dx_l, dy_l, dx_r, dy_r in samples:
        l = math.hypot(dx_l, dy_l)
        r = math.hypot(dx_r, dy_r)
        if l >= 2 and r >= 2:
            both += 1
        elif l >= 2:
            only_l += 1
        elif r >= 2:
            only_r += 1
        if _is_bus_garbage(dx_l, dy_l, dx_r, dy_r):
            garbage += 1
        if max(abs(dx_l), abs(dy_l), abs(dx_r), abs(dy_r)) >= 200:
            large += 1
    print("Capture diagnose:")
    print(f"  samples={n}  both>={2}:{both}  only_L={only_l}  only_R={only_r}")
    print(f"  bus-garbage patterns (ones/-1 or lone +/-256): {garbage} ({100.0 * garbage / n:.1f}%)")
    print(f"  large |delta|>=200 (often real coalesced motion): {large} ({100.0 * large / n:.1f}%)")
    if garbage > n * 0.02:
        print("  WARNING: frequent bus-garbage patterns — SDIO timing/wiring still suspect.")
    elif large > n * 0.05:
        print("  NOTE: many large deltas are normal after IRQ wake (counts accumulate). "
              "Do not treat |delta|~=256 alone as an SPI error.")
    if both < max(20, n // 5):
        print("  WARNING: few simultaneous L+R samples; residual mount search needs "
              "both sensors moving together.")


def filter_glitches(
    samples: Sequence[Sequence[float]],
    max_abs: float,
) -> list[tuple[float, float, float, float]]:
    kept: list[tuple[float, float, float, float]] = []
    for row in samples:
        if _is_bus_garbage(row[0], row[1], row[2], row[3]):
            continue
        if max_abs > 0 and max(abs(row[0]), abs(row[1]), abs(row[2]), abs(row[3])) > max_abs:
            continue
        kept.append((float(row[0]), float(row[1]), float(row[2]), float(row[3])))
    return kept


def filter_samples(
    samples: Sequence[Sequence[float]],
    min_norm: float,
    require_both: bool,
) -> list[tuple[float, float, float, float]]:
    kept: list[tuple[float, float, float, float]] = []
    for row in samples:
        left = math.hypot(row[0], row[1])
        right = math.hypot(row[2], row[3])
        if require_both:
            ok = left >= min_norm and right >= min_norm
        else:
            ok = (left + right) >= min_norm
        if ok:
            kept.append((float(row[0]), float(row[1]), float(row[2]), float(row[3])))
    return kept


def iter_configs(
    *,
    search_phi: bool,
    search_theta: bool,
    search_swap: bool,
    l_phi0: float,
    l_theta0: float,
    r_phi0: float,
    r_theta0: float,
) -> Iterable[Config]:
    phi_offsets = PHI_OFFSETS if search_phi else (0.0,)
    theta_offsets = THETA_OFFSETS if search_theta else (0.0,)
    swap_flags = (False, True) if search_swap else (False,)
    for swap in swap_flags:
        for dphi_l in phi_offsets:
            for dphi_r in phi_offsets:
                for dth_l in theta_offsets:
                    for dth_r in theta_offsets:
                        for mount_l in MOUNT_GRID:
                            for mount_r in MOUNT_GRID:
                                for flip_l in (False, True):
                                    for flip_r in (False, True):
                                        th_l = l_theta0 + dth_l
                                        th_r = r_theta0 + dth_r
                                        # Reject non-physical polar angles.
                                        if not (5.0 <= th_l <= 175.0 and 5.0 <= th_r <= 175.0):
                                            continue
                                        left = SensorPose(
                                            (l_phi0 + dphi_l) % 360.0,
                                            th_l,
                                            mount_l,
                                            flip_l,
                                        )
                                        right = SensorPose(
                                            (r_phi0 + dphi_r) % 360.0,
                                            th_r,
                                            mount_r,
                                            flip_r,
                                        )
                                        if swap:
                                            left, right = right, left
                                        yield Config(left=left, right=right, swapped=swap)


@dataclass(frozen=True)
class Ranked:
    config: Config
    rms: float
    median: float


def rank_configs(
    samples: Sequence[Sequence[float]],
    configs: Iterable[Config],
) -> list[Ranked]:
    ranked: list[Ranked] = []
    for config in configs:
        if config.swapped:
            data = [(row[2], row[3], row[0], row[1]) for row in samples]
        else:
            data = list(samples)  # type: ignore[arg-type]
        rows = build_design_matrix(config.left, config.right)
        if rows is None:
            continue
        rms, median = score_config(rows, data)
        ranked.append(Ranked(config, rms, median))
    ranked.sort(key=lambda item: (item.rms, item.median))
    return ranked


def synthesize_samples(
    config: Config,
    count: int = 200,
    noise: float = 0.5,
    seed: int = 0,
) -> list[tuple[float, float, float, float]]:
    rng = random.Random(seed)
    rows_a = build_design_matrix(config.left, config.right)
    assert rows_a is not None
    out: list[tuple[float, float, float, float]] = []
    for _ in range(count):
        w = (rng.gauss(0.0, 30.0), rng.gauss(0.0, 30.0), rng.gauss(0.0, 30.0))
        m = [_dot(rows_a[r], w) + rng.gauss(0.0, noise) for r in range(4)]
        out.append((m[0], m[1], m[2], m[3]))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--serial", nargs="?", const="auto",
                     help="Serial port (COMx) or omit/'auto' to pick SuperMini CDC")
    src.add_argument("--csv", type=Path, help="CSV or CALIB-line capture file")
    src.add_argument("--self-test", action="store_true", help="Recover a synthetic known mount")
    src.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    parser.add_argument("--seconds", type=float, default=30.0, help="Serial capture duration")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--min-norm", type=float, default=2.0,
                        help="Drop near-zero samples (counts)")
    parser.add_argument("--allow-single", action="store_true",
                        help="Keep samples where only one sensor moved")
    parser.add_argument("--search-phi", action="store_true",
                        help="Also try L/R phi offsets of 0/90/180/270 from nominal")
    parser.add_argument("--search-theta", action="store_true",
                        help="Also try L/R theta offsets of -90/0/+90 from nominal")
    parser.add_argument("--search-swap", action="store_true",
                        help="Also try swapping L/R measurement channels")
    parser.add_argument("--top", type=int, default=8, help="How many ranked configs to print")
    parser.add_argument("--save-csv", type=Path, help="Write captured samples to CSV")
    parser.add_argument("--max-abs", type=float, default=0.0,
                        help="Drop samples with any |delta| above this (0=disabled; "
                             "bus-garbage patterns are always dropped)")
    parser.add_argument("--l-phi", type=float, default=DEFAULT_L_PHI)
    parser.add_argument("--l-theta", type=float, default=DEFAULT_L_THETA)
    parser.add_argument("--r-phi", type=float, default=DEFAULT_R_PHI)
    parser.add_argument("--r-theta", type=float, default=DEFAULT_R_THETA)
    args = parser.parse_args(argv)

    if args.list_ports:
        listed = "\n".join(list_serial_ports()) or "  (none)"
        print("Available serial ports:")
        print(listed)
        return 0

    if args.self_test:
        truth = Config(
            left=SensorPose(DEFAULT_L_PHI, DEFAULT_L_THETA, 180.0, True),
            right=SensorPose(DEFAULT_R_PHI, DEFAULT_R_THETA, 90.0, False),
        )
        samples = synthesize_samples(truth)
        ranked = rank_configs(samples, iter_configs(
            search_phi=False, search_theta=False, search_swap=False,
            l_phi0=DEFAULT_L_PHI, l_theta0=DEFAULT_L_THETA,
            r_phi0=DEFAULT_R_PHI, r_theta0=DEFAULT_R_THETA,
        ))
        best = ranked[0].config
        ok = (
            best.left.mount == truth.left.mount
            and best.right.mount == truth.right.mount
            and best.left.flip == truth.left.flip
            and best.right.flip == truth.right.flip
        )
        print("self-test truth:", truth)
        print("self-test best: ", best, f"rms={ranked[0].rms:.4f}")
        if not ok:
            print("SELF-TEST FAILED", file=sys.stderr)
            return 1
        print("SELF-TEST OK")
        return 0

    if args.serial:
        port = resolve_serial_port(args.serial)
        samples = capture_serial(port, args.seconds, args.baud)
    else:
        samples = load_csv(args.csv)

    if args.save_csv:
        args.save_csv.parent.mkdir(parents=True, exist_ok=True)
        with args.save_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["dx_l", "dy_l", "dx_r", "dy_r"])
            writer.writerows(samples)
        print(f"Wrote {args.save_csv}")

    diagnose_samples(samples)
    before = len(samples)
    samples = filter_glitches(samples, args.max_abs)
    print(f"After garbage filter"
          f"{'' if args.max_abs <= 0 else f' + |delta|<={args.max_abs:g}'}: "
          f"kept {len(samples)}/{before}")
    samples = filter_samples(samples, args.min_norm, require_both=not args.allow_single)
    if len(samples) < 20:
        raise SystemExit(
            f"only {len(samples)} samples after filtering (min_norm={args.min_norm}); "
            "roll more / lower --min-norm / raise --max-abs"
        )
    print(f"Using {len(samples)} filtered samples")

    ranked = rank_configs(samples, iter_configs(
        search_phi=args.search_phi,
        search_theta=args.search_theta,
        search_swap=args.search_swap,
        l_phi0=args.l_phi,
        l_theta0=args.l_theta,
        r_phi0=args.r_phi,
        r_theta0=args.r_theta,
    ))
    if not ranked:
        raise SystemExit("no non-singular configs")

    print()
    print(f"{'rms':>10} {'median':>10}  mountL flipL  mountR flipR  phiL  phiR  swap")
    for item in ranked[: args.top]:
        c = item.config
        print(
            f"{item.rms:10.3f} {item.median:10.3f}  "
            f"{c.left.mount:6.0f} {int(c.left.flip):4d}  "
            f"{c.right.mount:6.0f} {int(c.right.flip):4d}  "
            f"{c.left.phi:5.1f} {c.right.phi:5.1f}  {int(c.swapped)}"
        )

    best = ranked[0]
    second = next((item for item in ranked[1:] if abs(item.rms - best.rms) > 1e-6), None)
    print()
    print("Best firmware defines:")
    print(best.config.firmware_defines())
    if second is not None and second.rms > 0:
        ratio = best.rms / second.rms
        print()
        print(f"Separation vs next distinct rms: best/next = {ratio:.3f} "
              f"(tied rows above are discrete mount symmetries, not disagreement)")
    print()
    print("After pasting mounts/flips: if the cursor still feels mirrored, flip "
          "ROT_SIGN_* / CURSOR_INVERT_* - residual search cannot choose world signs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
