# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""First-party licensing, ownership, and contribution policy are complete and unambiguous."""

import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess


ROOT = Path(__file__).resolve().parents[1]

# SHA-256 of each document with line endings normalized to LF, so the check survives the
# repository's `text=auto` normalization on every platform. These pin the published text: a
# failure here means the copy was edited, not that a copy is missing.
PUBLISHED_TEXTS = {
    "LICENSE": (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        "Apache License",
    ),
    "LICENSES/CERN-OHL-W-v2.txt": (
        "9682f98d4fe43f33e618a14da9b324f7b4c170fdc811ea261041898e4e0744ce",
        "CERN Open Hardware Licence Version 2 - Weakly Reciprocal",
    ),
    "LICENSES/GPL-3.0.txt": (
        "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
        "GNU GENERAL PUBLIC LICENSE",
    ),
    "LICENSES/LGPL-3.0.txt": (
        "e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118",
        "GNU LESSER GENERAL PUBLIC LICENSE",
    ),
    "DCO": (
        "f7ac75b443f4ca16b503241344b41aeff9503b0c30bedc2b119551d83cb0fa90",
        "Developer Certificate of Origin",
    ),
}

POLICY_FILES = ("LICENSING.md", "TRADEMARKS.md", "CONTRIBUTING.md", "SECURITY.md")


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")


def _flat(relative):
    """Prose with wrapping collapsed, so an assertion is about words rather than line breaks."""
    return " ".join(_text(relative).split())


def _tracked():
    listing = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [line for line in listing.splitlines() if line]


def _tracked_top_level():
    return {path.split("/", 1)[0] for path in _tracked()}


def _policy():
    return json.loads((ROOT / "licensing.json").read_text(encoding="utf-8"))


def _rule_for(policy, path):
    """Resolve a path the way licensing.json documents: ordered rules, first match wins.

    Deliberately a second implementation of the matcher in tools/apply_license_headers.py. The
    header applier is not trusted to grade its own work.
    """
    for rule in policy["rules"]:
        for pattern in rule["match"]:
            if pattern == "**":
                return rule
            if pattern.endswith("/**"):
                if path == pattern[:-3] or path.startswith(pattern[:-2]):
                    return rule
            elif fnmatch.fnmatchcase(path, pattern):
                return rule
    return None


def test_published_license_texts_are_unmodified():
    for relative, (digest, landmark) in PUBLISHED_TEXTS.items():
        body = _text(relative)
        assert landmark in body, relative
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == digest, (
            f"{relative} differs from the published text it must copy verbatim")


def test_policy_files_exist_and_are_substantive():
    for relative in POLICY_FILES:
        assert (ROOT / relative).is_file(), relative
        assert len(_text(relative).split()) > 40, relative


def test_notice_carries_the_project_copyright_without_third_party_texts():
    notice = _flat("NOTICE")

    assert "Astrolabe" in notice
    assert "Copyright 2026 Dylan Lee" in notice
    assert "Apache License, Version 2.0" in notice
    # Apache-2.0 section 4(d) propagates NOTICE into derivative works, so it stays an attribution
    # notice. Third-party license texts and attributions are kept in their own file.
    assert len(_text("NOTICE").splitlines()) < 20


def test_license_scope_claims_every_tracked_top_level_path():
    scope = _text("LICENSING.md")
    unmapped = sorted(name for name in _tracked_top_level() if name not in scope)

    assert not unmapped, f"LICENSING.md does not give these paths a license: {unmapped}"


def test_hardware_licence_is_pinned_to_exact_version_two():
    scope = _flat("LICENSING.md")

    assert "CERN-OHL-W-2.0" in scope
    assert "not an \"or later\" grant" in scope
    assert "or later" not in scope.replace("not an \"or later\" grant", "")


def test_firmware_is_licensed_as_software():
    software_section = _text("LICENSING.md").split("## CERN-OHL-W-2.0", 1)[0]

    assert "`firmware/`" in software_section
    assert "Firmware is software" in _flat("LICENSING.md")


def test_contribution_policy_uses_dco_sign_off_and_declines_a_cla():
    contributing = _flat("CONTRIBUTING.md")

    assert "Developer Certificate of Origin" in contributing
    assert "Signed-off-by" in contributing
    assert "git commit -s" in contributing
    assert "no Contributor License Agreement" in contributing


def test_security_policy_uses_private_reporting_without_a_response_sla():
    security = _flat("SECURITY.md")

    assert "security/advisories/new" in security
    assert "not in a public issue" in security
    assert "no committed response-time target" in security
    assert "Only the latest public release receives security fixes." in security
    for sensitive in ("certificates", "BLE device addresses", "absolute paths", "logs"):
        assert sensitive in security, sensitive


def test_every_tracked_path_resolves_to_one_license_disposition():
    policy = _policy()
    unresolved = [path for path in _tracked() if _rule_for(policy, path) is None]

    assert not unresolved, f"licensing.json gives these paths no disposition: {unresolved[:10]}"
    assert {rule["license"] for rule in policy["rules"]} == {"Apache-2.0", "external-verbatim"}


def test_every_header_exemption_states_why():
    """A directory can opt out of inline headers, but never silently."""
    for rule in _policy()["rules"]:
        assert rule["headers"] in {"auto", "mapped", "never"}, rule
        if rule["headers"] != "auto":
            assert len(rule.get("note", "").split()) > 10, rule["match"]


def test_every_source_file_that_needs_an_inline_header_has_the_right_one():
    policy = _policy()
    prefixes = policy["comment_prefixes"]
    missing, wrong = [], []

    for path in _tracked():
        rule = _rule_for(policy, path)
        prefix = prefixes.get(PurePosixPath(path).suffix)
        if rule["headers"] != "auto" or prefix is None:
            continue
        head = _text(path).splitlines()[:10]
        if not any(line.startswith(f"{prefix} SPDX-License-Identifier:") for line in head):
            missing.append(path)
        elif f"{prefix} SPDX-License-Identifier: {rule['license']}" not in head:
            wrong.append(path)

    assert not missing, (
        f"add '{list(prefixes.values())[0]} SPDX-License-Identifier: Apache-2.0' to: {missing[:10]}")
    assert not wrong, f"these declare a license the mapping does not give them: {wrong[:10]}"


def test_no_file_declares_a_license_the_mapping_does_not_grant():
    """Catches a stray identifier in a file that needs no header, such as a copied snippet."""
    policy = _policy()
    permitted = {rule["license"] for rule in policy["rules"]} - {"external-verbatim"}
    declared = []

    for path in _tracked():
        if PurePosixPath(path).suffix in {".png", ".dll", ".svg"}:
            continue
        for line in _text(path).splitlines()[:10]:
            _, _, identifier = line.partition("SPDX-License-Identifier:")
            if identifier and identifier.strip() not in permitted:
                declared.append((path, identifier.strip()))

    assert not declared, f"unexpected SPDX identifiers: {declared[:10]}"


def test_license_scope_document_and_machine_readable_mapping_agree():
    scope = _flat("LICENSING.md")
    policy = _policy()

    assert "licensing.json" in scope
    assert policy["copyright"] in _flat("NOTICE")
    for rule in policy["rules"]:
        if rule["headers"] == "auto":
            continue
        for pattern in rule["match"]:
            assert pattern.rstrip("/*") in scope, pattern


def test_trademark_policy_does_not_claim_a_registration():
    trademarks = _flat("TRADEMARKS.md")

    assert "No trademark registration is claimed at this time" in trademarks
    assert "Astrolabe" in trademarks and "Mildly Useful" in trademarks
