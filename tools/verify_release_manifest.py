# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Re-check a release manifest against the files it describes.

A manifest is a claim about bytes, and a claim is only worth anything if something re-derives it.
Between writing the manifest and publishing, an artifact can be rebuilt, re-signed, truncated by a
failed copy, or replaced by a stale file left in the output directory -- and every one of those leaves
the manifest looking perfectly valid.

So this hashes every artifact the manifest declares and fails on any disagreement. Run it after the
manifest is written and again immediately before publishing; the second run is what makes "reject the
release if an artifact was modified after its final hash was recorded" enforceable rather than a
policy sentence.

Signing is a modification. An artifact signed after its hash was recorded fails here, which is the
intended behaviour: the signed bytes must be hashed and recorded as their own fact rather than
inheriting the pre-signing record.

    python tools/verify_release_manifest.py --manifest <manifest.json> [--directory <where artifacts live>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

REQUIRED_TOP_LEVEL = ("schema", "product", "version", "channel", "target", "source", "build",
                      "components", "support_tiers", "licenses", "artifacts", "signatures")


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _locate(entry: dict, directory: Path, manifest_path: Path) -> Path:
    """Resolve an artifact's recorded path against the release output root.

    Paths are recorded relative to that root, because an absolute path from the build machine means
    nothing here. `--directory` names the root; by default it is the manifest's own directory, which is
    where the build writes it.
    """
    base = directory if directory is not None else manifest_path.parent
    return base / Path(entry["path"])


def verify(manifest_path: Path, directory: Path = None) -> list:
    """Return a list of human-readable problems; empty means the manifest still describes reality."""
    manifest_path = Path(manifest_path)
    problems = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest could not be read: {exc}"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in manifest:
            problems.append(f"manifest is missing {key!r}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        problems.append("manifest declares no artifacts")
        return problems

    for key in sorted(artifacts):
        entry = artifacts[key]
        if not isinstance(entry, dict) or not {"name", "path", "size", "sha256"} <= set(entry):
            problems.append(f"{key}: incomplete artifact record")
            continue
        path = _locate(entry, directory, manifest_path)
        if not path.is_file():
            problems.append(f"{key}: declared artifact is missing at {path}")
            continue
        size = path.stat().st_size
        if size != entry["size"]:
            problems.append(f"{key}: size is {size}, manifest recorded {entry['size']}")
            continue
        actual = _digest(path)
        if actual != entry["sha256"]:
            problems.append(
                f"{key}: {path.name} changed after its hash was recorded "
                f"(now {actual}, manifest recorded {entry['sha256']})")

    problems.extend(_signature_problems(manifest))
    return problems


def _signature_problems(manifest: dict) -> list:
    """Check the signature record against itself, not against a certificate store.

    Whether a signature chains to a trusted root is the signing step's job, and it records its verdict
    here. What this catches is the record contradicting itself -- a manifest claiming to be signed with
    nothing signed, or an artifact whose signature was recorded as unverified.
    """
    signatures = manifest.get("signatures")
    if not isinstance(signatures, dict) or "signed" not in signatures:
        return ["manifest does not state whether it is signed"]
    signed_artifacts = signatures.get("artifacts") or {}
    if not signatures["signed"]:
        if signed_artifacts:
            return ["manifest says it is unsigned but records signatures"]
        if not str(signatures.get("reason", "")).strip():
            return ["an unsigned manifest must say why"]
        return []
    if not signed_artifacts:
        return ["manifest says it is signed but records no signatures"]
    problems = []
    for key in sorted(signed_artifacts):
        record = signed_artifacts[key]
        if not isinstance(record, dict):
            problems.append(f"{key}: malformed signature record")
            continue
        if record.get("verified") is not True:
            problems.append(f"{key}: signature is recorded as unverified")
        for field in ("subject", "thumbprint", "timestamp_authority"):
            if not str(record.get(field, "")).strip():
                problems.append(f"{key}: signature record is missing {field}")
        if key not in (manifest.get("artifacts") or {}):
            problems.append(f"{key}: signed artifact is not one the manifest describes")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--directory", type=Path,
                        help="where the artifacts live (default: the manifest's own directory)")
    arguments = parser.parse_args(argv)

    problems = verify(arguments.manifest, arguments.directory)
    if problems:
        print(f"{arguments.manifest} no longer describes its artifacts:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    print(f"{manifest['product']['name']} {manifest['version']} ({manifest['channel']}): "
          f"{len(manifest['artifacts'])} artifacts match their recorded hashes"
          f"{'' if manifest['signatures']['signed'] else ', unsigned'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
