"""Entry point:  python -m trackball_daemon   (use pythonw for no console).

The tray app replaces the old pygame loop as `main`. --debug additionally opens the cube
verification window; it is OFF by default.
"""
import argparse

from .app import App
from .instance_lock import SingleInstanceGuard, notify_already_running


def main(argv=None, *, instance_guard=None):
    parser = argparse.ArgumentParser(
        prog="trackball_daemon",
        description="Trackball system-tray daemon (headless by default).",
    )
    parser.add_argument("--debug", action="store_true",
                        help="also open the rotating-cube verification window (default off)")
    args = parser.parse_args(argv)
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
