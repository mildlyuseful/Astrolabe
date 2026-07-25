# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Parse the bundled Godot add-on with a real Godot editor and fail on any error.

GDScript resolves types at parse time, so a value the parser cannot type is a *parse* error, not a
warning: the whole script fails to load and the add-on never runs. Nothing else in this repository
can see that. Python cannot evaluate GDScript, and the daemon-side tests only check that the payload
files exist and carry the right version.

Two details make this harder than pointing Godot at a file, and getting either wrong produces
confident nonsense:

* A script must be checked from inside a project. Outside one, ``res://`` resolves to nothing and
  every relative dependency is reported as missing.
* Global ``class_name`` declarations come from the project's script-class cache, which only an editor
  import pass builds. Skipping it reports every cross-file type in the add-on as undeclared, which
  buries the one error that is real.

So this stages the add-on into a throwaway project, runs one import pass, and only then checks each
script. Usage:

    python tools/godot_parse_check.py --godot "C:\\path\\to\\Godot_v4.x-stable_win64_console.exe"

Prefer the ``_console`` executable on Windows; the windowed one writes nothing to a pipe.
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "trackball_daemon" / "plugins" / "godot" / "trackball_nav"

PROJECT_FILE = """config_version=5

[application]

config/name="trackball-nav-parse-check"
"""

# Godot reports parse problems on stdout/stderr rather than through its exit code, which stays 0.
FAILURE_MARKERS = ("SCRIPT ERROR", "Parse Error", "with error \"Parse error\"")


def _run(godot: Path, project: Path, *arguments):
    completed = subprocess.run(
        [str(godot), "--headless", "--path", str(project), *arguments],
        capture_output=True, text=True, timeout=600)
    return completed.stdout + completed.stderr


def check(godot: Path) -> int:
    scripts = sorted(path.name for path in ADDON.glob("*.gd"))
    if not scripts:
        print(f"no GDScript found under {ADDON}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="tbnav_godot_") as workspace:
        project = Path(workspace)
        (project / "project.godot").write_text(PROJECT_FILE, encoding="utf-8")
        shutil.copytree(ADDON, project / "addons" / ADDON.name)

        # Build the script-class cache. Its own output is not a verdict: a first import legitimately
        # reports the add-on's cross-file types as unknown, which is exactly what it is fixing.
        _run(godot, project, "--editor", "--quit")
        if not (project / ".godot" / "global_script_class_cache.cfg").exists():
            print("Godot did not build a script-class cache; is this an editor build?",
                  file=sys.stderr)
            return 2

        failed = []
        for name in scripts:
            output = _run(godot, project, "--check-only",
                          "--script", f"res://addons/{ADDON.name}/{name}")
            if any(marker in output for marker in FAILURE_MARKERS):
                failed.append((name, output.strip()))
                print(f"FAIL {name}")
            else:
                print(f"ok   {name}")

    for name, output in failed:
        print(f"\n--- {name}\n{output}", file=sys.stderr)
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--godot", required=True, type=Path,
                        help="Godot editor executable (the console build on Windows)")
    arguments = parser.parse_args(argv)
    if not arguments.godot.exists():
        print(f"no Godot executable at {arguments.godot}", file=sys.stderr)
        return 2
    return check(arguments.godot)


if __name__ == "__main__":
    raise SystemExit(main())
