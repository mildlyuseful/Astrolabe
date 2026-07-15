"""Exercise the production Raw Input provider without starting the daemon UI.

Automated mode uses the Phase 0 diagnostic's otherwise-unused F24 SendInput vector. Interactive
mode is the required physical-key path; keep another ordinary or elevated test application focused
while pressing the prompted controls so background delivery and pass-through can be assessed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trackball_daemon.input import InputAggregator
from trackball_daemon.input.windows_raw_input import WindowsRawInputProvider
from trackball_daemon.winfocus import ForegroundMonitor
from windows_input_spike import VK_F24, _send_key, user32


def _event_rows(transitions):
    return [
        {
            "reason": transition.reason,
            "pressed": list(transition.snapshot.pressed_tokens),
            "events": [
                {
                    "control": event.control_id,
                    "phase": event.phase.value,
                    "synthetic": bool(event.metadata.get("synthetic", False)),
                    "delivery": event.metadata.get("delivery"),
                }
                for event in transition.events
            ],
        }
        for transition in transitions
        if transition.events
    ]


def run(*, automated, boundary, seconds, controls):
    aggregator = InputAggregator()
    transitions = []
    aggregator.add_listener(transitions.append)
    provider = WindowsRawInputProvider(
        aggregator.accept_many, aggregator.update_health)
    aggregator.register_provider(provider)
    foreground_before = int(user32.GetForegroundWindow() or 0)
    monitor = None
    try:
        provider.configure(controls)
        if provider.health.status.value != "running":
            raise RuntimeError(provider.health.detail)
        monitor = ForegroundMonitor(
            lambda _process: provider.reconcile("foreground_change"), poll_interval=0.05)
        monitor.poll_once(force=True)
        monitor.start()
        if automated:
            _send_key(VK_F24)
            _send_key(VK_F24)  # deliberate repeat: must not create a second activation edge
            _send_key(VK_F24, released=True)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                phases = [
                    event.phase.value for transition in transitions
                    for event in transition.events if event.control_id == "f24"]
                if phases == ["pressed", "released"]:
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError(f"production provider F24 edges were {phases!r}")
        elif boundary:
            print(
                "Focus ordinary Notepad and hold Left Ctrl. While still holding it, click an "
                "Administrator/elevated Notepad with the mouse, wait two seconds, then release "
                "Ctrl. Leave elevated Notepad focused until this tool prints its report.",
                flush=True,
            )
            time.sleep(max(1.0, seconds))
        else:
            print(
                "Keep an ordinary test app focused. Press/release Left and Right Ctrl, Shift, "
                "and Alt; type A; then optionally press F12. Confirm A still reaches that app.",
                flush=True,
            )
            time.sleep(max(1.0, seconds))
            provider.reconcile("interactive_end")
    finally:
        if monitor is not None:
            monitor.stop()
        provider.stop("acceptance_complete")
    foreground_after = int(user32.GetForegroundWindow() or 0)
    rows = _event_rows(transitions)
    report = {
        "mode": ("automated_f24" if automated else
                 "elevated_boundary_hold" if boundary else "interactive_physical"),
        "configured_controls": sorted(controls),
        "foreground_unchanged": foreground_before == foreground_after,
        "final_health": provider.health.status.value,
        "final_pressed": list(aggregator.snapshot().pressed_tokens),
        "transitions": rows,
    }
    print(json.dumps(report, indent=2))
    if aggregator.snapshot().pressed_tokens:
        raise SystemExit("provider left a control pressed after shutdown")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--automated", action="store_true")
    mode.add_argument("--interactive", action="store_true")
    mode.add_argument("--access-boundary", action="store_true")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()
    controls = (("f24",) if args.automated else ("ctrl",) if args.access_boundary
                else ("ctrl", "shift", "alt", "a", "f12"))
    run(automated=args.automated, boundary=args.access_boundary,
        seconds=args.seconds, controls=controls)


if __name__ == "__main__":
    main()
