# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Fail when an installed distribution's metadata or notices are wrong.

Run with the interpreter of an environment the wheel is installed into, from outside the source
checkout. It reads installed metadata rather than the repository, so it establishes what a
recipient of the wheel actually receives, not what `pyproject.toml` asked for. Those differ more
often than they should: a declared file that was never written, a description the build backend
dropped, a URL that only exists in source.

Scope stops at metadata: the packaged add-on payloads are checked by
``python -m trackball_daemon --release-smoke``, which runs against the same installed package.
"""

import re
import sys
from importlib.metadata import PackageNotFoundError, distribution


DISTRIBUTIONS = ("astrolabe-daemon", "trackball-daemon")
REQUIRED_NOTICES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "LICENSING.md")
# pystray is LGPL-3.0-or-later, so a distribution carrying it must also carry these texts.
REQUIRED_TEXTS = ("LICENSES/LGPL-3.0.txt", "LICENSES/GPL-3.0.txt")

EXPECTED_NAME = "astrolabe-daemon"
REQUIRED_URL_LABELS = ("Homepage", "Source", "Issues", "Documentation", "Security")
REQUIRED_CLASSIFIER_PREFIXES = (
    "Development Status ::",
    "Operating System :: Microsoft :: Windows",
    "Programming Language :: Python :: 3.13",
)


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


def _check_metadata(dist, problems):
    """Project metadata a consumer of the built distribution actually sees."""
    metadata = dist.metadata

    if _normalize_name(metadata["Name"]) != EXPECTED_NAME:
        problems.append(f"distribution name is {metadata['Name']!r}, expected {EXPECTED_NAME!r}")
    if not (metadata.get("Summary") or "").strip():
        problems.append("Summary is empty")
    if "Astrolabe" not in (metadata.get("Summary") or ""):
        problems.append("Summary does not name the product")
    if not (metadata.get("Description") or dist.read_text("METADATA") or "").strip():
        problems.append("long description is missing; readme was not attached")
    if not (metadata.get("Requires-Python") or "").strip():
        problems.append("Requires-Python is not declared")
    if not (metadata.get("Author") or metadata.get("Author-email")):
        problems.append("no author is recorded")

    labels = {
        entry.split(",", 1)[0].strip()
        for entry in (metadata.get_all("Project-URL") or ())}
    for label in REQUIRED_URL_LABELS:
        if label not in labels:
            problems.append(f"Project-URL {label} is missing")

    classifiers = metadata.get_all("Classifier") or ()
    for prefix in REQUIRED_CLASSIFIER_PREFIXES:
        if not any(entry.startswith(prefix) for entry in classifiers):
            problems.append(f"no classifier starting with {prefix!r}")

    # The import package is deliberately not renamed with the distribution.
    if not any(str(path).startswith("trackball_daemon") for path in (dist.files or ())):
        problems.append("the trackball_daemon import package is not present in the distribution")


def _normalize_name(name):
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def main():
    dist = _installed()
    declared = _declared_files(dist)
    problems = []

    _check_metadata(dist, problems)

    expression = dist.metadata.get("License-Expression")
    if expression != "Apache-2.0":
        problems.append(f"License-Expression is {expression!r}, expected 'Apache-2.0'")

    for required in REQUIRED_NOTICES + REQUIRED_TEXTS:
        if required not in declared:
            problems.append(f"{required} is not declared as a License-File")
        elif not _on_disk(dist, required):
            problems.append(f"{required} is declared but was not installed")

    for problem in problems:
        print(f"installed metadata: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"{dist.metadata['Name']} {dist.version} metadata is complete and carries "
          f"{len(declared)} installed notice files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
