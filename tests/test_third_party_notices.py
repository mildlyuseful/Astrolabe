# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Bundled-component attribution is complete, internally consistent, and honest.

The environment-dependent half of the audit — does this record match the distributions actually
installed for a release? — is `tools/audit_notices.py`, which CI runs against the real release
runtime environment. These checks cover what is true from the checkout alone.
"""

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "third_party.json").read_text(encoding="utf-8"))
NOTICES = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").replace("\r\n", "\n")
FLAT = " ".join(NOTICES.split())
SHIPPED = DATA["python_distributions"] + DATA["bundled_runtime"]


def test_every_shipped_component_has_a_complete_record():
    for entry in SHIPPED:
        for field in ("name", "license", "copyright", "source"):
            assert str(entry.get(field, "")).strip(), f"{entry.get('name')} is missing {field}"


@pytest.mark.parametrize("entry", SHIPPED, ids=lambda entry: entry["name"])
def test_every_shipped_component_is_attributed(entry):
    name = entry["name"]
    attributed = name in NOTICES or (
        name.startswith("winrt-Windows.") and "winrt-Windows.*" in NOTICES)

    assert attributed, f"{name} has no attribution in THIRD_PARTY_NOTICES.md"


def test_every_referenced_license_text_exists():
    """A notices file that points at a missing text attributes nothing."""
    missing = []
    for target in _relative_links(NOTICES):
        if not (ROOT / target).is_file():
            missing.append(target)

    assert not missing, f"THIRD_PARTY_NOTICES.md links to missing files: {missing}"


def test_copyleft_components_carry_their_required_texts():
    """Shipping an LGPL component obliges a prominent notice plus the LGPL and GPL texts."""
    copyleft = [entry for entry in SHIPPED if "GPL" in entry["license"].upper()]

    assert [entry["name"] for entry in copyleft] == ["pystray"], (
        "a new copyleft component changes what a release must carry; update this expectation "
        "deliberately after confirming the obligations")
    assert (ROOT / "LICENSES" / "LGPL-3.0.txt").is_file()
    assert (ROOT / "LICENSES" / "GPL-3.0.txt").is_file()
    assert "Prominent notice" in NOTICES
    assert "right to modify pystray and relink it" in FLAT


def test_notices_are_kept_out_of_the_apache_notice_file():
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")

    for entry in DATA["python_distributions"]:
        assert entry["name"] not in notice, (
            f"{entry['name']} belongs in THIRD_PARTY_NOTICES.md, not in the propagating NOTICE")


def test_metadata_disagreements_are_recorded_rather_than_smoothed_over():
    """Where a wheel's classifier and its shipped text disagree, the record must say so."""
    for entry in DATA["python_distributions"]:
        if entry["name"] in {"pywin32", "cryptography"}:
            assert entry.get("license_choice_note"), entry["name"]


def _relative_links(markdown):
    targets = set()
    for chunk in markdown.split("](")[1:]:
        target = chunk.split(")", 1)[0].split("#", 1)[0]
        if target and "://" not in target and not target.startswith("<"):
            targets.add(target)
    return sorted(targets)
