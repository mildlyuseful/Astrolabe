# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Entry point:  python -m trackball_daemon   (use pythonw for no console).

The tray app replaces the old pygame loop as `main`. --debug additionally opens the cube
verification window; it is OFF by default.
"""
import argparse
import json

from .app import App
from .instance_lock import SingleInstanceGuard, notify_already_running


def main(argv=None, *, instance_guard=None):
    parser = argparse.ArgumentParser(
        prog="trackball_daemon",
        description="Trackball system-tray daemon (headless by default).",
    )
    parser.add_argument("--debug", action="store_true",
                        help="also open the rotating-cube verification window (default off)")
    parser.add_argument(
        "--release-smoke",
        action="store_true",
        help="validate packaged resources and exit without starting the daemon",
    )
    args = parser.parse_args(argv)
    if args.release_smoke:
        from .release_smoke import run_release_smoke

        print(json.dumps(run_release_smoke(), indent=2))
        return 0
    guard = instance_guard or SingleInstanceGuard()
    if not guard.acquire():
        notify_already_running()
        return 2
    try:
        App(debug=args.debug).start()
    finally:
        guard.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
