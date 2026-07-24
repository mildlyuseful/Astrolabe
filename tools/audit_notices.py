# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Fail when a bundled component lacks a resolved license and notice disposition.

The authority is the environment a release is actually built from, not the dependency lock: markers,
extras, and platform wheels decide what ships. Point ``--environment`` at the interpreter of a
release runtime environment (``uv sync --locked --no-editable --extra onshape``) and this compares
the distributions installed there against ``third_party.json`` and ``THIRD_PARTY_NOTICES.md``.

With no ``--environment`` it audits the interpreter running it, which is only meaningful when that
interpreter is itself a release runtime environment.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "third_party.json"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"

# The project's own distribution is first-party and covered by LICENSE and NOTICE.
FIRST_PARTY = {"trackball-daemon", "trackball_daemon", "astrolabe-daemon", "astrolabe_daemon"}

# Environment plumbing that a seeded virtualenv contains but no release artifact embeds: the
# freezer includes the application's imported packages, not the installer that put them there.
# What the onedir tree actually contains is confirmed by the artifact-level audit tracked in
# TODO.md; this list only stops a seeded environment from producing a false failure here.
ENVIRONMENT_TOOLING = {"pip", "setuptools", "wheel", "pkg_resources", "uv"}

_ENUMERATE = (
    "import json;"
    "from importlib.metadata import distributions;"
    "print(json.dumps(sorted("
    "{d.metadata['Name']: d.metadata['Version'] for d in distributions()"
    " if d.metadata['Name']}.items())))"
)


def _normalize(name):
    """PyPA name normalization, so `winrt-Windows.Foundation` and its wheel spelling agree."""
    return "".join("-" if character in "-_." else character for character in name.lower())


def installed_distributions(python):
    """``{normalized name: (reported name, version)}`` for one interpreter's environment."""
    if python is None:
        payload = subprocess.run(
            [sys.executable, "-c", _ENUMERATE], check=True, capture_output=True, text=True).stdout
    else:
        payload = subprocess.run(
            [str(python), "-c", _ENUMERATE], check=True, capture_output=True, text=True).stdout
    ignored = {_normalize(entry) for entry in FIRST_PARTY | ENVIRONMENT_TOOLING}
    found = {}
    for name, version in json.loads(payload):
        if _normalize(name) in ignored:
            continue
        found[_normalize(name)] = (name, version)
    return found


def declared_components(data):
    return {_normalize(entry["name"]): entry for entry in data["python_distributions"]}


def audit(python=None):
    """Return a list of human-readable problems; empty means the disposition is complete."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    notices = NOTICES.read_text(encoding="utf-8")
    declared = declared_components(data)
    installed = installed_distributions(python)
    problems = []

    for key, (name, version) in sorted(installed.items()):
        if key not in declared:
            problems.append(
                f"{name} {version} ships but has no disposition in third_party.json")

    for key, entry in sorted(declared.items()):
        if key not in installed:
            problems.append(
                f"{entry['name']} is declared in third_party.json but no longer ships; "
                "remove it so the notices describe the real artifact")

    for entry in data["python_distributions"] + data["bundled_runtime"]:
        for field in ("license", "copyright", "source"):
            if not str(entry.get(field, "")).strip():
                problems.append(f"{entry['name']} has no {field} recorded")

    attributed = _attributed_names(data, notices)
    for entry in data["python_distributions"] + data["bundled_runtime"]:
        if not attributed(entry["name"]):
            problems.append(
                f"{entry['name']} is not attributed in THIRD_PARTY_NOTICES.md")

    return problems


def _attributed_names(data, notices):
    """Match a component against the notices text, allowing one documented family entry.

    The `winrt-Windows.*` projection packages are one upstream project under one license and are
    attributed as a family rather than as eight identical rows.
    """
    families = ("winrt-Windows.",)

    def attributed(name):
        if name in notices:
            return True
        return any(name.startswith(family) and f"{family}*" in notices for family in families)

    return attributed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, default=None,
                        help="python.exe of the release runtime environment to audit")
    args = parser.parse_args(argv)

    problems = audit(args.environment)
    for problem in problems:
        print(f"notice audit: {problem}")
    if problems:
        print(f"{len(problems)} unresolved bundled-component disposition(s).")
        return 1
    print("Every bundled component has a resolved license and notice disposition.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
