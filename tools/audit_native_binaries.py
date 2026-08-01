# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Attribute every Windows native binary in a completed Nuitka onedir tree."""

import argparse
from pathlib import Path, PurePosixPath
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "third_party.json"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
NATIVE_SUFFIXES = {".dll", ".exe", ".pyd"}


def _matches(path, pattern):
    # A basename-only pattern is deliberately root-only. This lets CPython own root stdlib
    # extensions without silently swallowing a new extension inside a third-party package.
    if "/" not in pattern and "/" in path:
        return False
    return PurePosixPath(path).match(pattern)


def _rule_matches(path, rule):
    return (
        any(_matches(path, pattern) for pattern in rule["include"])
        and not any(_matches(path, pattern) for pattern in rule.get("exclude", ()))
    )


def audit(root, *, data_path=DATA, notices_path=NOTICES):
    """Return ``(attributions, problems)`` for one completed onedir root."""
    root = Path(root)
    data = json.loads(Path(data_path).read_text(encoding="utf-8"))
    notices = Path(notices_path).read_text(encoding="utf-8")
    rules = data.get("native_artifacts", ())
    declared = {
        entry["name"]
        for entry in data["python_distributions"] + data["bundled_runtime"]
    }
    problems = []

    if not root.is_dir():
        return {}, [f"onedir root does not exist: {root}"]
    if not rules:
        return {}, ["third_party.json has no native_artifacts attribution rules"]

    for rule in rules:
        component = rule.get("component", "")
        if not component or not rule.get("include"):
            problems.append("a native_artifacts rule is missing component or include patterns")
        elif not rule.get("first_party") and component not in declared:
            problems.append(f"native owner {component!r} has no bundled-component record")
        elif not rule.get("first_party") and component not in notices:
            problems.append(f"native owner {component!r} is absent from THIRD_PARTY_NOTICES.md")

    binaries = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in NATIVE_SUFFIXES)
    if not binaries:
        problems.append(f"onedir tree contains no native binaries: {root}")

    attributions = {}
    used_rules = set()
    for binary in binaries:
        relative = binary.relative_to(root).as_posix()
        owners = [
            (index, rule["component"])
            for index, rule in enumerate(rules)
            if rule.get("component") and rule.get("include") and _rule_matches(relative, rule)
        ]
        if not owners:
            problems.append(f"native binary has no attribution: {relative}")
            continue
        if len(owners) > 1:
            names = ", ".join(component for _, component in owners)
            problems.append(f"native binary has ambiguous attribution ({names}): {relative}")
            continue
        index, component = owners[0]
        used_rules.add(index)
        attributions[relative] = component

    for index, rule in enumerate(rules):
        if index not in used_rules:
            problems.append(
                "native attribution rule matched nothing in the artifact: "
                f"{rule.get('component', '<missing>')}")
    return attributions, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="completed Nuitka onedir root")
    args = parser.parse_args(argv)

    attributions, problems = audit(args.root)
    for path, component in sorted(attributions.items()):
        print(f"native attribution: {path} -> {component}")
    for problem in problems:
        print(f"native attribution audit: {problem}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} unresolved native attribution problem(s).", file=sys.stderr)
        return 1
    print(f"Attributed {len(attributions)} native binaries in the onedir tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
