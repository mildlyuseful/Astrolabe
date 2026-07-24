# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Fail when an installed distribution is missing the notices its metadata must carry.

Run with the interpreter of an environment the wheel is installed into, from outside the source
checkout. It reads installed metadata rather than the repository, so it establishes what a
recipient of the wheel actually receives, not what the build was asked to include.

Scope is deliberately narrow: the packaged add-on payloads are checked by
``python -m trackball_daemon --release-smoke``, which runs against the same installed package. This
covers the part that only distribution metadata can answer.
"""

import sys
from importlib.metadata import PackageNotFoundError, distribution


DISTRIBUTIONS = ("astrolabe-daemon", "trackball-daemon")
REQUIRED_NOTICES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "LICENSING.md")
# pystray is LGPL-3.0-or-later, so a distribution carrying it must also carry these texts.
REQUIRED_TEXTS = ("LICENSES/LGPL-3.0.txt", "LICENSES/GPL-3.0.txt")


def _installed():
    for name in DISTRIBUTIONS:
        try:
            return distribution(name)
        except PackageNotFoundError:
            continue
    raise SystemExit(f"none of {DISTRIBUTIONS} is installed in this environment")


def _declared_files(dist):
    """License-File entries, normalized to forward slashes for comparison."""
    return {entry.replace("\\", "/") for entry in (dist.metadata.get_all("License-File") or ())}


def _on_disk(dist, relative):
    """True when a declared license file was actually written into the installed dist-info."""
    for path in dist.files or ():
        if path.as_posix().endswith(f"licenses/{relative}"):
            return dist.locate_file(path).is_file()
    return False


def main():
    dist = _installed()
    declared = _declared_files(dist)
    problems = []

    expression = dist.metadata.get("License-Expression")
    if expression != "Apache-2.0":
        problems.append(f"License-Expression is {expression!r}, expected 'Apache-2.0'")

    for required in REQUIRED_NOTICES + REQUIRED_TEXTS:
        if required not in declared:
            problems.append(f"{required} is not declared as a License-File")
        elif not _on_disk(dist, required):
            problems.append(f"{required} is declared but was not installed")

    for problem in problems:
        print(f"wheel notices: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"{dist.metadata['Name']} {dist.version} carries {len(declared)} installed notice files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
