# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Add authoritative dynamic project metadata to a generated CycloneDX SBOM."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


def finalize_sbom(path: Path, *, name: str, version: str) -> None:
    """Set the validated root application's name/version and rewrite deterministically."""
    path = Path(path).resolve()
    name = str(name).strip()
    version = str(version).strip()
    if not name or not version:
        raise ValueError("SBOM project name and version must be non-empty")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM is not CycloneDX")
    root = data.get("metadata", {}).get("component")
    if not isinstance(root, dict) or root.get("type") != "application":
        raise ValueError("SBOM has no application root component")
    generated_name = root.get("name")
    if generated_name not in (None, name):
        raise ValueError(
            f"SBOM root name {generated_name!r} does not match expected project {name!r}")
    root["name"] = name
    root["version"] = version

    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        # Explicit LF keeps the SBOM byte-identical across platforms.
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Finalize a CycloneDX root component with authoritative project metadata.")
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    finalize_sbom(args.sbom, name=args.name, version=args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
