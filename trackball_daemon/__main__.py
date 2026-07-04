"""Entry point:  python -m trackball_daemon   (use pythonw for no console).

The tray app replaces the old pygame loop as `main`. --debug additionally opens the cube
verification window; it is OFF by default.
"""
import argparse

from .app import App


def main():
    parser = argparse.ArgumentParser(
        prog="trackball_daemon",
        description="Trackball system-tray daemon (headless by default).",
    )
    parser.add_argument("--debug", action="store_true",
                        help="also open the rotating-cube verification window (default off)")
    args = parser.parse_args()
    App(debug=args.debug).start()


if __name__ == "__main__":
    main()
