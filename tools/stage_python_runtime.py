# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Discover and stage the pinned Windows interpreter's dynamic runtime files."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


RUNTIME_FAMILIES = (
    ("stable-ABI runtime", ("python3.dll",)),
    ("Visual C++ exception runtime", ("vcruntime140_1.dll",)),
    ("Tcl runtime", ("tcl86t.dll",)),
    ("Tk runtime", ("tk86t.dll",)),
    ("OpenSSL crypto runtime", ("libcrypto-3*.dll",)),
    ("OpenSSL TLS runtime", ("libssl-3*.dll",)),
    ("libffi runtime", ("libffi-8.dll",)),
)


def interpreter_base(python):
    result = subprocess.run(
        [str(python), "-c", "import sys; print(sys.base_prefix)"],
        check=True,
        capture_output=True,
        text=True,
    )
    base = Path(result.stdout.strip())
    if not base.is_dir():
        raise ValueError(f"interpreter base directory does not exist: {base}")
    return base


def discover_runtime_files(base):
    """Return one interpreter-owned file for every required semantic runtime family."""
    base = Path(base)
    search_roots = (base, base / "DLLs")
    resolved = []
    for family, patterns in RUNTIME_FAMILIES:
        matches = {
            candidate.resolve()
            for root in search_roots
            if root.is_dir()
            for pattern in patterns
            for candidate in root.glob(pattern)
            if candidate.is_file()
        }
        if not matches:
            expected = ", ".join(patterns)
            raise ValueError(f"{family} was not found under {base} (expected {expected})")
        if len(matches) != 1:
            found = ", ".join(str(path) for path in sorted(matches))
            raise ValueError(f"{family} is ambiguous under {base}: {found}")
        resolved.append(next(iter(matches)))

    destinations = {}
    for source in resolved:
        key = source.name.casefold()
        previous = destinations.setdefault(key, source)
        if previous != source:
            raise ValueError(
                f"runtime files would collide when staged: {previous} and {source}")
    return tuple(resolved)


def stage_runtime_files(files, destination):
    destination = Path(destination)
    if not destination.is_dir():
        raise ValueError(f"staging destination does not exist: {destination}")
    for source in files:
        shutil.copy2(source, destination / source.name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True,
                        help="pinned python.exe whose base runtime is packaged")
    parser.add_argument("--destination", type=Path,
                        help="existing onedir root to receive the discovered files")
    args = parser.parse_args(argv)

    try:
        base = interpreter_base(args.python)
        files = discover_runtime_files(base)
        if args.destination is not None:
            stage_runtime_files(files, args.destination)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Python runtime staging failed: {exc}", file=sys.stderr)
        return 1

    action = "Staged" if args.destination is not None else "Found"
    for source in files:
        print(f"{action} {source.name}: {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
