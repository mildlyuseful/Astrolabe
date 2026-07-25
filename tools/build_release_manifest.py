# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Record what a release artifact actually is, in one machine-readable file.

A ZIP, an SBOM, and a checksum each answer part of "what did I install?" and none of them answers
"which source revision, which interpreter, which dependency set, and what does this build claim to
support?". This writes that record beside the artifacts it describes -- beside, not inside, because it
carries the archive's own hash.

Every field is derived from something already authoritative: the version from the package, the channel
from the version, component versions from the integration registry, the support-tier snapshot from
`app_registry`, hashes from the files on disk. Nothing here is typed in by hand at release time, so
there is no second place for it to drift.

Two honesty rules shape the output:

* Unsigned is stated, not omitted. An absent ``signatures`` key would read as "not recorded yet" to a
  consumer; ``"signed": false`` with a reason cannot be mistaken for anything else.
* A working tree with uncommitted changes is recorded as dirty and the recorded revision is marked as
  not describing the artifact, because in that case it does not. ``--require-clean`` refuses instead,
  for a pipeline that must not produce such a build at all.

Run it with the interpreter the release was built from: the embedded Python version it reports is the
one running it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trackball_daemon import __version__                                        # noqa: E402
from trackball_daemon.app_registry import APP_IDS_BY_TIER, SupportTier          # noqa: E402
from trackball_daemon.autocad_artifact import (                                 # noqa: E402
    validate_bundled_autocad_artifact,
)
from trackball_daemon.integrations import ADDIN_KEYS, bundled_addin_version     # noqa: E402
from trackball_daemon.product import (                                          # noqa: E402
    BUILD_TARGET,
    DISTRIBUTION_NAME,
    PRODUCT_NAME,
    PUBLISHER,
    release_channel,
)

SCHEMA = 1

#: Artifacts named on the command line. Each is hashed and recorded under this key; a missing file is
#: an error rather than a skipped entry, since a manifest that silently omits an artifact is worse
#: than no manifest.
ARTIFACT_OPTIONS = ("executable", "autocad_plugin", "archive", "sbom", "installer")

#: Artifacts a build must produce. The installer does not exist yet, so it stays optional.
REQUIRED_ARTIFACTS = ("executable", "autocad_plugin", "archive", "sbom")


def _git(*arguments):
    return subprocess.run(("git", *arguments), cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def source_revision():
    """The revision this build came from, and whether it actually describes the artifact."""
    revision = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    return {
        "revision": revision,
        "dirty": dirty,
        # The distinction a reader needs: a dirty build was made from files that are not in any
        # commit, so the revision identifies the starting point and nothing more.
        "revision_describes_artifact": not dirty,
    }


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def artifact_record(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"declared release artifact is missing: {path}")
    return {"name": path.name, "size": path.stat().st_size, "sha256": _digest(path)}


def component_versions() -> dict:
    """Every versioned thing this build ships, from the registries that own those versions."""
    autocad = validate_bundled_autocad_artifact()
    return {
        "host_addons": {key: bundled_addin_version(key) for key in sorted(ADDIN_KEYS)
                        if key != "autocad"},
        "autocad_plugin": {
            "version": autocad["version"],
            "source_revision": autocad["source_revision"],
            "sha256": autocad["dll_sha256"],
        },
    }


def support_tier_snapshot() -> dict:
    return {tier.value: list(APP_IDS_BY_TIER[tier]) for tier in SupportTier}


def license_record() -> dict:
    third_party = json.loads((ROOT / "third_party.json").read_text(encoding="utf-8"))
    return {
        "expression": "Apache-2.0",
        "texts": sorted(path.name for path in (ROOT / "LICENSES").glob("*.txt")),
        "notices": ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "LICENSING.md"],
        "bundled_components": {
            "python_distributions": len(third_party["python_distributions"]),
            "runtime_components": len(third_party["bundled_runtime"]),
        },
    }


def build_manifest(artifacts: dict, *, version: str = __version__) -> dict:
    lock = ROOT / "uv.lock"
    return {
        "schema": SCHEMA,
        "product": {"name": PRODUCT_NAME, "publisher": PUBLISHER,
                    "distribution": DISTRIBUTION_NAME},
        "version": version,
        "channel": release_channel(version),
        "target": BUILD_TARGET,
        "source": source_revision(),
        "build": {
            # The interpreter running this is the one Nuitka embedded, which is why this tool is run
            # by the release environment's Python rather than any convenient one.
            "embedded_python": platform.python_version(),
            "dependency_lock": {"name": lock.name, "sha256": _digest(lock)},
        },
        "components": component_versions(),
        "support_tiers": support_tier_snapshot(),
        "licenses": license_record(),
        "artifacts": {key: artifact_record(path) for key, path in sorted(artifacts.items())},
        "signatures": {
            "signed": False,
            "reason": "the internal-alpha channel is unsigned; signing happens in a release "
                      "staging tree and is recorded separately from these development hashes",
        },
    }


def write_manifest(path: Path, manifest: dict) -> None:
    """Write deterministically, and atomically so a reader never sees a partial record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True, help="manifest JSON to write")
    for option in ARTIFACT_OPTIONS:
        parser.add_argument(f"--{option.replace('_', '-')}", type=Path,
                            help=f"path to the built {option.replace('_', ' ')}")
    parser.add_argument("--require-clean", action="store_true",
                        help="fail when the working tree has uncommitted changes")
    arguments = parser.parse_args(argv)

    artifacts = {option: getattr(arguments, option) for option in ARTIFACT_OPTIONS
                 if getattr(arguments, option) is not None}
    missing = [name for name in REQUIRED_ARTIFACTS if name not in artifacts]
    if missing:
        parser.error(f"a release manifest must describe every built artifact; missing: "
                     f"{', '.join(missing)}")

    manifest = build_manifest(artifacts)
    if arguments.require_clean and manifest["source"]["dirty"]:
        print("refusing to write a release manifest from a dirty working tree", file=sys.stderr)
        return 1
    write_manifest(arguments.output, manifest)

    print(f"{manifest['product']['name']} {manifest['version']} "
          f"({manifest['channel']}) -> {arguments.output.name}")
    if manifest["source"]["dirty"]:
        print("WARNING: built from a dirty working tree; the recorded revision does not describe "
              "this artifact", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
