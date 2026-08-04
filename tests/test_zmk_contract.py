# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

# Scope note: these assert invariants that can fail when something is actually wrong -- a manifest
# revision that is not a full commit, a source declared in CMakeLists that does not exist, an
# attribution entry with no license file on disk. They deliberately do not mirror ci.yml back at
# itself or grep firmware C source for substrings. A test that duplicates a value can only confirm
# the duplication: an earlier version of this file pinned the build-image digest as a constant and
# asserted the workflow contained it, and both copies carried the same malformed 63-character
# digest, so the suite stayed green while the job could not start. ci.yml remains the authority for
# what gets compiled, and the firmware compiler remains the authority for whether it compiles.

import hashlib
import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
ZMK_ROOT = ROOT / "firmware" / "zmk"
MANIFEST_PATH = ZMK_ROOT / "config" / "west.yml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
THIRD_PARTY_PATH = ROOT / "third_party.json"
NOTICES_PATH = ROOT / "THIRD_PARTY_NOTICES.md"
REVISION = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:([0-9a-fA-F]*)")
FIRMWARE_LICENSE_HASHES = {
    "LICENSES/third-party/zmk.txt":
        "05e785a4c222b9213b452f1a2cd8b3f8ce95a56c1b77a7ca8525d0f7eb7f77b1",
    "LICENSES/third-party/zephyr.txt":
        "c6596eb7be8581c18be736c846fb9173b69eccf6ef94c5135893ec56bd92ba08",
    "LICENSES/third-party/cmsis.txt":
        "b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1",
    "LICENSES/third-party/nrfx.txt":
        "0b1d649b90f668dac908a7bc184f6d0285aa38090ed281a7f94d53e036785911",
    "LICENSES/third-party/tinycrypt.txt":
        "ada970c9a00c9d6292d8230dc28b581030c4c23ce77f4e30f39f37fd68b4c746",
    "LICENSES/third-party/picolibc.txt":
        "5d055829035579bccba09f45a44c9ce10b1de929e17461e2fd4a9741c01e3fdf",
    "LICENSES/third-party/newlib.txt":
        "923f55bb9ce77e9c7f7e8a3a9ec143c83a4a49e3b4fa5cae8a69e5f19ffb2c8d",
    "LICENSES/third-party/gcc-gpl-3.0.txt":
        "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    "LICENSES/third-party/gcc-runtime-library-exception-3.1.txt":
        "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74",
}


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_west_manifest_pins_every_declared_project_to_an_exact_commit():
    manifest = _load_yaml(MANIFEST_PATH)["manifest"]
    projects = {project["name"]: project for project in manifest["projects"]}

    assert manifest["self"]["path"] == "config"
    assert projects["zmk"]["revision"] == "edf5c0814fd3ea202e43aad2d68fd32e882a518c"
    assert projects["zephyr"]["revision"] == "dacab4875df72109b96cc8977547a0dc04875bcd"
    assert all("revision" in project for project in manifest["projects"])
    assert all(
        REVISION.fullmatch(project["revision"])
        for project in manifest["projects"]
        if "revision" in project
    )


def test_out_of_tree_module_declares_sources_that_exist():
    module = _load_yaml(ZMK_ROOT / "module" / "zephyr" / "module.yml")
    build = module["build"]

    assert build["cmake"] == "."
    assert build["kconfig"] == "Kconfig"
    assert build["settings"]["board_root"] == "."
    assert build["settings"]["dts_root"] == "."

    cmake = (ZMK_ROOT / "module" / "CMakeLists.txt").read_text(encoding="utf-8")
    declared_sources = set(re.findall(r"(?:drivers|src)/[A-Za-z0-9_./-]+\.c", cmake))
    assert declared_sources
    assert not [
        source for source in declared_sources if not (ZMK_ROOT / "module" / source).is_file()
    ]

    for relative in (
        "config/astrolabe.conf",
        "config/astrolabe.keymap",
        "module/Kconfig",
        "module/boards/shields/astrolabe/astrolabe.overlay",
        "module/boards/shields/astrolabe/astrolabe.zmk.yml",
    ):
        assert (ZMK_ROOT / relative).is_file(), relative


def test_every_pinned_container_digest_is_a_wellformed_sha256():
    # Structural, not a mirror: it never states what the digest should be, so it cannot go stale
    # and cannot be satisfied by copying the wrong value into a second file. A truncated or
    # mistyped digest makes the container reference invalid and the job unable to start.
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    digests = DIGEST.findall(workflow)

    assert digests, "expected at least one pinned container digest"
    assert all(len(digest) == 64 for digest in digests), [
        digest for digest in digests if len(digest) != 64
    ]
    # The job pins one image; the provenance record must name that same image.
    assert len(set(digests)) == 1, sorted(set(digests))


def test_firmware_component_attribution_is_complete_and_separate_from_python_runtime():
    data = json.loads(THIRD_PARTY_PATH.read_text(encoding="utf-8"))
    components = {entry["name"]: entry for entry in data["firmware_components"]}
    expected_licenses = {
        "ZMK": "MIT",
        "Zephyr": "Apache-2.0",
        "nrfx": "BSD-3-Clause",
        "TinyCrypt": "BSD-3-Clause",
        "micro-ecc": "BSD-2-Clause",
        "CMSIS": "Apache-2.0",
        "Picolibc/Newlib runtime": "LicenseRef-Picolibc-Newlib-per-file",
        "GCC libgcc runtime": "GPL-3.0-only WITH GCC-exception-3.1",
    }

    assert {name: entry["license"] for name, entry in components.items()} == expected_licenses
    assert not set(components) & {entry["name"] for entry in data["python_distributions"]}
    notices = NOTICES_PATH.read_text(encoding="utf-8")
    for name, entry in components.items():
        assert name in notices
        for field in ("copyright", "source", "evidence"):
            assert str(entry[field]).strip(), (name, field)
        for relative in entry["license_files"]:
            assert (ROOT / relative).is_file(), relative


def test_canonical_firmware_license_texts_are_pinned_verbatim():
    for relative, expected in FIRMWARE_LICENSE_HASHES.items():
        body = (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == expected, relative
