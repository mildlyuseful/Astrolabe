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
    PUBLIC_CHANNEL,
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

#: Artifacts a signed release must carry an Authenticode signature for. The archive and the SBOM are
#: absent because neither is a signable Windows binary; they are covered by their recorded hashes.
SIGNABLE_ARTIFACTS = ("executable", "autocad_plugin", "installer")

#: What a signature report must state per artifact. `verified` is the signing step's own verdict on
#: whether the chain and timestamp checked out -- this file records it, and refuses to record a
#: signature that says otherwise, but does not re-derive it.
SIGNATURE_FIELDS = ("subject", "issuer", "thumbprint", "timestamp_authority", "verified")


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


def artifact_record(path: Path, root: Path) -> dict:
    """Describe one artifact, locating it by a path relative to the release output root.

    A basename alone would be ambiguous -- `Astrolabe.exe` sits inside the onedir tree while the
    archive sits beside it -- and an absolute path recorded on the build machine means nothing on the
    machine that verifies the manifest. An artifact outside the release output is refused rather than
    recorded with an escaping path, because a manifest should describe one self-contained tree.
    """
    path = Path(path).resolve()
    root = Path(root).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"declared release artifact is missing: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError(f"release artifact lies outside the output root {root}: {path}") from None
    return {
        "name": path.name,
        "path": relative.as_posix(),
        "size": path.stat().st_size,
        "sha256": _digest(path),
    }


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


def signature_record(report: dict, artifacts: dict, channel: str) -> dict:
    """Turn a signing step's report into the manifest's signature section.

    The report comes from whatever performed the signing; its `verified` flag is that step's verdict on
    the certificate chain and the timestamp. This records the verdict and refuses to record a false one
    -- but the check itself belongs where the certificate store is, not here.

    An unsigned result is stated rather than left out: an absent section reads as "not recorded yet"
    instead of "not signed". A public release with no signatures is refused outright, because that is
    the one combination that would ship an unsigned binary under a released name.
    """
    if not report:
        if channel == PUBLIC_CHANNEL:
            raise ValueError(
                "a public release must be signed; no signature report was supplied. Sign the staged "
                "artifacts and pass --signature-report, or build a pre-release version instead")
        return {
            "signed": False,
            "reason": f"the {channel} channel is unsigned; signing happens in a release staging "
                      "tree and is recorded separately from these development hashes",
            "artifacts": {},
        }

    unknown = sorted(set(report) - set(artifacts))
    if unknown:
        raise ValueError(f"signature report names artifacts this build did not produce: {unknown}")
    unsignable = sorted(set(report) - set(SIGNABLE_ARTIFACTS))
    if unsignable:
        raise ValueError(f"these artifacts are not Authenticode-signable: {unsignable}")
    expected = [key for key in SIGNABLE_ARTIFACTS if key in artifacts]
    missing = sorted(set(expected) - set(report))
    if missing:
        raise ValueError(
            f"every signable artifact must be signed, but no signature was reported for: {missing}")

    recorded = {}
    for key in sorted(report):
        entry = report[key]
        absent = [field for field in SIGNATURE_FIELDS if field not in entry]
        if absent:
            raise ValueError(f"signature report for {key} is missing: {sorted(absent)}")
        if entry["verified"] is not True:
            raise ValueError(
                f"the signature on {key} was reported as unverified; a release may not record a "
                "signature its own verification step rejected")
        recorded[key] = {field: entry[field] for field in SIGNATURE_FIELDS}
        for optional in ("not_before", "not_after", "timestamped_at"):
            if optional in entry:
                recorded[key][optional] = entry[optional]
    return {"signed": True, "artifacts": recorded}


def build_manifest(artifacts: dict, *, root: Path, version: str = __version__,
                   signature_report: dict = None) -> dict:
    lock = ROOT / "uv.lock"
    channel = release_channel(version)
    return {
        "schema": SCHEMA,
        "product": {"name": PRODUCT_NAME, "publisher": PUBLISHER,
                    "distribution": DISTRIBUTION_NAME},
        "version": version,
        "channel": channel,
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
        # `components.autocad_plugin.sha256` is the checked-in DLL's development hash and
        # `artifacts.autocad_plugin.sha256` is the hash of what actually shipped. They are equal only
        # while the release is unsigned: signing rewrites the staged copy, and the plan requires the
        # signed hash to be recorded separately from the source-build one rather than replacing it.
        "artifacts": {key: artifact_record(path, root)
                      for key, path in sorted(artifacts.items())},
        "signatures": signature_record(signature_report or {}, artifacts, channel),
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
    parser.add_argument("--root", type=Path,
                        help="release output root that artifact paths are recorded relative to "
                             "(default: the manifest's own directory)")
    for option in ARTIFACT_OPTIONS:
        parser.add_argument(f"--{option.replace('_', '-')}", type=Path,
                            help=f"path to the built {option.replace('_', ' ')}")
    parser.add_argument("--require-clean", action="store_true",
                        help="fail when the working tree has uncommitted changes")
    parser.add_argument("--signature-report", type=Path,
                        help="JSON mapping artifact key -> "
                             f"{{{', '.join(SIGNATURE_FIELDS)}}} from the signing step")
    arguments = parser.parse_args(argv)

    artifacts = {option: getattr(arguments, option) for option in ARTIFACT_OPTIONS
                 if getattr(arguments, option) is not None}
    missing = [name for name in REQUIRED_ARTIFACTS if name not in artifacts]
    if missing:
        parser.error(f"a release manifest must describe every built artifact; missing: "
                     f"{', '.join(missing)}")

    root = arguments.root if arguments.root is not None else arguments.output.resolve().parent
    report = (json.loads(arguments.signature_report.read_text(encoding="utf-8"))
              if arguments.signature_report is not None else None)
    manifest = build_manifest(artifacts, root=root, signature_report=report)
    if arguments.require_clean and manifest["source"]["dirty"]:
        print("refusing to write a release manifest from a dirty working tree", file=sys.stderr)
        return 1
    write_manifest(arguments.output, manifest)

    signing = "signed" if manifest["signatures"]["signed"] else "unsigned"
    print(f"{manifest['product']['name']} {manifest['version']} "
          f"({manifest['channel']}, {signing}) -> {arguments.output.name}")
    if manifest["source"]["dirty"]:
        print("WARNING: built from a dirty working tree; the recorded revision does not describe "
              "this artifact", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
