# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""A manifest that nothing re-derives is decoration.

Between writing a manifest and publishing, an artifact can be rebuilt, re-signed, truncated by a
failed copy, or shadowed by a stale file left in the output directory. Every one of those leaves the
manifest looking perfectly valid, so these tests are about the verifier noticing.

The interesting cases are the ones where the manifest and the tree disagree *quietly*: same size but
different bytes, a record that claims a signature nothing signed, and a nested artifact resolved to
the wrong place.
"""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_release_manifest as manifest_tool                                   # noqa: E402
import verify_release_manifest as verifier                                       # noqa: E402


@pytest.fixture
def release(tmp_path):
    """A release output tree with a nested executable, plus its written manifest."""
    root = tmp_path / "release"
    (root / "onedir" / "trackball_daemon" / "plugins" / "autocad").mkdir(parents=True)
    artifacts = {
        "executable": root / "onedir" / "Astrolabe.exe",
        "autocad_plugin": (root / "onedir" / "trackball_daemon" / "plugins" / "autocad"
                           / "TrackballNavAcad.dll"),
        "archive": root / "Astrolabe-0.2.0a1-windows-x64.zip",
        "sbom": root / "Astrolabe-0.2.0a1-windows-x64.cdx.json",
    }
    for key, path in artifacts.items():
        path.write_bytes(f"contents of {key}".encode())
    manifest_path = root / "Astrolabe-0.2.0a1-windows-x64.manifest.json"
    manifest_tool.write_manifest(
        manifest_path, manifest_tool.build_manifest(artifacts, root=root))
    return manifest_path, root, artifacts


def _rewrite(manifest_path, mutate):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(manifest)
    manifest_tool.write_manifest(manifest_path, manifest)


def test_an_untouched_release_verifies(release):
    manifest_path, root, _artifacts = release

    assert verifier.verify(manifest_path, root) == []


def test_a_nested_artifact_is_found_by_its_recorded_path(release):
    """A basename alone would be ambiguous, and an absolute build-machine path meaningless."""
    manifest_path, _root, _artifacts = release
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["artifacts"]["executable"]["path"] == "onedir/Astrolabe.exe"
    assert manifest["artifacts"]["archive"]["path"] == "Astrolabe-0.2.0a1-windows-x64.zip"


def test_the_manifests_own_directory_is_the_default_root(release):
    manifest_path, _root, _artifacts = release

    assert verifier.verify(manifest_path) == []


def test_a_rebuilt_artifact_of_the_same_length_is_caught(release):
    """The case a size check alone would miss: a recompile that happens to be the same length."""
    manifest_path, root, artifacts = release
    original = artifacts["executable"].read_bytes()
    artifacts["executable"].write_bytes(bytes(len(original)))

    problems = verifier.verify(manifest_path, root)

    assert len(problems) == 1
    assert "changed after its hash was recorded" in problems[0]


def test_a_truncated_artifact_is_caught(release):
    manifest_path, root, artifacts = release
    artifacts["archive"].write_bytes(b"")

    problems = verifier.verify(manifest_path, root)

    assert any("size is 0" in problem for problem in problems)


def test_a_deleted_artifact_is_caught(release):
    manifest_path, root, artifacts = release
    artifacts["sbom"].unlink()

    problems = verifier.verify(manifest_path, root)

    assert any("declared artifact is missing" in problem for problem in problems)


def test_a_stale_file_beside_the_tree_does_not_satisfy_a_nested_record(release):
    """Resolving by basename would let a leftover copy stand in for the real artifact."""
    manifest_path, root, artifacts = release
    (root / "Astrolabe.exe").write_bytes(artifacts["executable"].read_bytes())
    artifacts["executable"].unlink()

    problems = verifier.verify(manifest_path, root)

    assert any("onedir" in problem and "missing" in problem for problem in problems)


def test_an_unreadable_manifest_is_reported_rather_than_raised(tmp_path):
    broken = tmp_path / "manifest.json"
    broken.write_text("{ not json", encoding="utf-8")

    problems = verifier.verify(broken)

    assert len(problems) == 1 and "could not be read" in problems[0]


def test_a_missing_manifest_is_reported_rather_than_raised(tmp_path):
    problems = verifier.verify(tmp_path / "absent.json")

    assert len(problems) == 1 and "could not be read" in problems[0]


@pytest.mark.parametrize("key", list(verifier.REQUIRED_TOP_LEVEL))
def test_a_manifest_missing_any_required_section_is_rejected(release, key):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest.pop(key))

    assert any(key in problem for problem in verifier.verify(manifest_path, root))


def test_a_manifest_with_no_artifacts_is_rejected(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest.update(artifacts={}))

    assert any("declares no artifacts" in problem for problem in verifier.verify(manifest_path, root))


def test_an_incomplete_artifact_record_is_rejected(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest["artifacts"]["archive"].pop("sha256"))

    assert any("incomplete artifact record" in problem
               for problem in verifier.verify(manifest_path, root))


# --- signature self-consistency --------------------------------------------------------------------

def test_claiming_to_be_signed_with_nothing_signed_is_rejected(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path,
             lambda manifest: manifest["signatures"].update(signed=True, artifacts={}))

    assert any("records no signatures" in problem
               for problem in verifier.verify(manifest_path, root))


def test_recording_signatures_while_claiming_to_be_unsigned_is_rejected(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest["signatures"].update(
        artifacts={"executable": {"verified": True}}))

    assert any("unsigned but records signatures" in problem
               for problem in verifier.verify(manifest_path, root))


def test_an_unsigned_manifest_must_say_why(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest["signatures"].update(reason=""))

    assert any("must say why" in problem for problem in verifier.verify(manifest_path, root))


def test_a_signature_recorded_as_unverified_is_rejected(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest["signatures"].update(
        signed=True,
        artifacts={"executable": {"verified": False, "subject": "CN=x", "thumbprint": "0" * 40,
                                  "timestamp_authority": "http://t"}}))

    assert any("recorded as unverified" in problem
               for problem in verifier.verify(manifest_path, root))


def test_a_signature_for_an_undescribed_artifact_is_rejected(release):
    manifest_path, root, _artifacts = release
    _rewrite(manifest_path, lambda manifest: manifest["signatures"].update(
        signed=True,
        artifacts={"installer": {"verified": True, "subject": "CN=x", "thumbprint": "0" * 40,
                                 "timestamp_authority": "http://t"}}))

    assert any("not one the manifest describes" in problem
               for problem in verifier.verify(manifest_path, root))


# --- command line ----------------------------------------------------------------------------------

def test_the_command_reports_success_with_a_summary(release, capsys):
    manifest_path, root, _artifacts = release

    assert verifier.main(["--manifest", str(manifest_path), "--directory", str(root)]) == 0
    output = capsys.readouterr().out

    assert "4 artifacts match their recorded hashes" in output
    assert "unsigned" in output


def test_the_command_fails_and_names_every_problem(release, capsys):
    manifest_path, root, artifacts = release
    artifacts["archive"].write_bytes(b"tampered")
    artifacts["sbom"].unlink()

    assert verifier.main(["--manifest", str(manifest_path), "--directory", str(root)]) == 1
    errors = capsys.readouterr().err

    assert "archive" in errors and "sbom" in errors
