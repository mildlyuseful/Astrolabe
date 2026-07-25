# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""A release record has to be true about the build it describes, or it is worse than nothing.

The manifest is what a future reader consults to answer "which revision, which interpreter, which
dependency set, what did this claim to support?" long after the build machine is gone. Every way it
could be confidently wrong is a way that question gets answered incorrectly, so the tests here are
about the ways it lies rather than the fields it has: a channel that disagrees with the version, a
revision presented as describing an artifact it does not, a declared hash that no longer matches the
file, and a missing signature that reads as "not recorded yet".
"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from trackball_daemon import __version__
from trackball_daemon import product
from trackball_daemon.app_registry import APP_IDS_BY_TIER, SupportTier
from trackball_daemon.integrations import ADDIN_KEYS, bundled_addin_version

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_release_manifest as manifest_tool                                   # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def artifacts(tmp_path):
    """Stand-in artifacts. The manifest's job is to describe files, not to judge their contents."""
    paths = {}
    for name, content in (("executable", b"MZ fake executable"),
                          ("autocad_plugin", b"fake managed dll"),
                          ("archive", b"PK fake archive"),
                          ("sbom", b'{"bomFormat": "CycloneDX"}')):
        path = tmp_path / name
        path.write_bytes(content)
        paths[name] = path
    return paths


# --- the channel follows the version ---------------------------------------------------------------

def test_the_shipped_version_is_an_internal_alpha():
    assert product.release_channel(__version__) == product.INTERNAL_ALPHA_CHANNEL
    release, phase, serial = product.parse_version(__version__)

    assert phase == "a" and serial >= 1
    assert release < product.FIRST_PUBLIC_VERSION


@pytest.mark.parametrize("version, channel", [
    ("0.2.0a1", product.INTERNAL_ALPHA_CHANNEL),
    ("0.2.0a2", product.INTERNAL_ALPHA_CHANNEL),
    ("0.11.0a7", product.INTERNAL_ALPHA_CHANNEL),
    ("1.0.0", product.PUBLIC_CHANNEL),
    ("1.4.2", product.PUBLIC_CHANNEL),
])
def test_the_channel_is_derived_from_the_version(version, channel):
    assert product.release_channel(version) == channel


def test_a_final_version_below_one_cannot_claim_the_public_channel():
    """1.0.0 is reserved for public V1, so no 0.x build may be published as one."""
    with pytest.raises(ValueError, match="reserved for public V1"):
        product.release_channel("0.9.9")


@pytest.mark.parametrize("version", ["1.0.0b1", "1.0.0rc1"])
def test_an_undefined_prerelease_channel_refuses_to_resolve(version):
    """Calling a release candidate an internal alpha understates it; guessing is not allowed."""
    with pytest.raises(ValueError, match="no channel is defined"):
        product.release_channel(version)


@pytest.mark.parametrize("version", ["0.2", "0.2.0.a1", "0.2.0alpha1", "v0.2.0a1", "0.2.0a", ""])
def test_version_shapes_this_project_does_not_publish_are_rejected(version):
    with pytest.raises(ValueError):
        product.parse_version(version)


def test_the_packaged_version_and_the_archive_name_agree():
    assert product.archive_name(__version__).startswith(f"{product.PRODUCT_NAME}-{__version__}-")


# --- the record describes the build ----------------------------------------------------------------

def test_every_declared_artifact_hash_matches_the_file(artifacts):
    import hashlib

    record = manifest_tool.build_manifest(artifacts)

    for key, path in artifacts.items():
        entry = record["artifacts"][key]
        assert entry["name"] == path.name
        assert entry["size"] == path.stat().st_size
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_missing_artifact_is_an_error_not_an_omission(artifacts, tmp_path):
    artifacts["archive"] = tmp_path / "never-built.zip"

    with pytest.raises(FileNotFoundError, match="never-built.zip"):
        manifest_tool.build_manifest(artifacts)


def test_the_manifest_must_describe_every_artifact_a_build_produces(artifacts, tmp_path, capsys):
    """A manifest silently missing the SBOM would still look complete to a reader."""
    incomplete = [str(value) for key, path in artifacts.items() if key != "sbom"
                  for value in (f"--{key.replace('_', '-')}", path)]

    with pytest.raises(SystemExit):
        manifest_tool.main(["--output", str(tmp_path / "m.json"), *incomplete])

    assert "sbom" in capsys.readouterr().err


def test_a_dirty_tree_is_recorded_as_not_describing_the_artifact(artifacts, monkeypatch):
    monkeypatch.setattr(manifest_tool, "_git",
                        lambda *arguments: ("M x.py" if arguments[0] == "status" else "a" * 40))

    source = manifest_tool.build_manifest(artifacts)["source"]

    assert source["dirty"] is True
    assert source["revision_describes_artifact"] is False


def test_a_clean_tree_is_recorded_as_describing_the_artifact(artifacts, monkeypatch):
    monkeypatch.setattr(manifest_tool, "_git",
                        lambda *arguments: ("" if arguments[0] == "status" else "b" * 40))

    source = manifest_tool.build_manifest(artifacts)["source"]

    assert source["dirty"] is False
    assert source["revision_describes_artifact"] is True
    assert source["revision"] == "b" * 40


def test_require_clean_refuses_a_dirty_build(artifacts, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(manifest_tool, "_git",
                        lambda *arguments: ("M x.py" if arguments[0] == "status" else "c" * 40))
    output = tmp_path / "manifest.json"
    declared = [str(value) for key, path in artifacts.items()
                for value in (f"--{key.replace('_', '-')}", path)]

    assert manifest_tool.main(["--output", str(output), "--require-clean", *declared]) == 1
    assert "dirty working tree" in capsys.readouterr().err
    assert not output.exists(), "a refused build must not leave a manifest behind"


def test_a_dirty_build_still_warns_when_it_is_allowed(artifacts, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(manifest_tool, "_git",
                        lambda *arguments: ("M x.py" if arguments[0] == "status" else "d" * 40))
    output = tmp_path / "manifest.json"
    declared = [str(value) for key, path in artifacts.items()
                for value in (f"--{key.replace('_', '-')}", path)]

    assert manifest_tool.main(["--output", str(output), *declared]) == 0
    assert "dirty working tree" in capsys.readouterr().err
    assert output.is_file()


def test_the_real_repository_revision_is_recorded():
    revision = subprocess.run(("git", "rev-parse", "HEAD"), cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()

    assert manifest_tool.source_revision()["revision"] == revision


# --- derived rather than restated ------------------------------------------------------------------

def test_the_support_tier_snapshot_comes_from_the_registry(artifacts):
    record = manifest_tool.build_manifest(artifacts)

    assert record["support_tiers"] == {
        tier.value: list(APP_IDS_BY_TIER[tier]) for tier in SupportTier}


def test_every_shipped_component_version_is_recorded(artifacts):
    components = manifest_tool.build_manifest(artifacts)["components"]

    # AutoCAD is recorded separately because its provenance manifest carries more than a version.
    assert set(components["host_addons"]) == ADDIN_KEYS - {"autocad"}
    for key, version in components["host_addons"].items():
        assert version == bundled_addin_version(key), key
    assert components["autocad_plugin"]["version"] == bundled_addin_version("autocad")
    assert len(components["autocad_plugin"]["source_revision"]) == 40


def test_the_identity_and_target_come_from_the_product_authority(artifacts):
    record = manifest_tool.build_manifest(artifacts)

    assert record["product"] == {"name": product.PRODUCT_NAME, "publisher": product.PUBLISHER,
                                 "distribution": product.DISTRIBUTION_NAME}
    assert record["target"] == product.BUILD_TARGET


def test_the_dependency_lock_is_identified_by_hash(artifacts):
    import hashlib

    lock = manifest_tool.build_manifest(artifacts)["build"]["dependency_lock"]

    assert lock["name"] == "uv.lock"
    assert lock["sha256"] == hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()


def test_the_embedded_python_is_the_interpreter_that_built_it(artifacts):
    import platform

    build = manifest_tool.build_manifest(artifacts)["build"]

    assert build["embedded_python"] == platform.python_version()


def test_the_recorded_licence_texts_are_the_ones_that_ship(artifacts):
    licenses = manifest_tool.build_manifest(artifacts)["licenses"]

    assert licenses["expression"] == "Apache-2.0"
    assert licenses["texts"] == sorted(path.name for path in (ROOT / "LICENSES").glob("*.txt"))
    for notice in licenses["notices"]:
        assert (ROOT / notice).is_file(), notice


# --- honesty about signing -------------------------------------------------------------------------

def test_unsigned_is_stated_rather_than_left_out(artifacts):
    """An absent signatures key would read as "not recorded yet" instead of "not signed"."""
    signatures = manifest_tool.build_manifest(artifacts)["signatures"]

    assert signatures["signed"] is False
    assert "unsigned" in signatures["reason"]


# --- the file on disk ------------------------------------------------------------------------------

def test_the_manifest_is_written_deterministically(artifacts, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    record = manifest_tool.build_manifest(artifacts)

    manifest_tool.write_manifest(first, record)
    manifest_tool.write_manifest(second, record)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert b"\r\n" not in first.read_bytes(), "LF keeps the record byte-identical across platforms"
    assert json.loads(first.read_text(encoding="utf-8")) == record


def test_rewriting_leaves_no_temporary_file_behind(artifacts, tmp_path):
    output = tmp_path / "manifest.json"
    record = manifest_tool.build_manifest(artifacts)

    manifest_tool.write_manifest(output, record)
    manifest_tool.write_manifest(output, record)

    assert output.is_file()
    assert [path.name for path in tmp_path.iterdir() if path.suffix == ".tmp"] == []


def test_the_release_build_generates_the_manifest_last():
    """It hashes the SBOM, which the finalization step rewrites in place."""
    build = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")

    assert "tools/build_release_manifest.py" in build
    assert build.index("finalize_sbom.py") < build.index("build_release_manifest.py")
    assert "--require-clean" in build
