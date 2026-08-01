# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

import json

from tools import audit_native_binaries as native


def _records(tmp_path):
    data = {
        "python_distributions": [],
        "bundled_runtime": [
            {"name": "CPython"},
            {"name": "OpenSSL"},
        ],
        "native_artifacts": [
            {"component": "Astrolabe", "first_party": True,
             "include": ["plugins/owned.dll"]},
            {"component": "CPython", "include": ["*.pyd", "python*.dll"],
             "exclude": ["special.pyd"]},
            {"component": "OpenSSL", "include": ["libcrypto-3*.dll", "special.pyd"]},
        ],
    }
    data_path = tmp_path / "third_party.json"
    data_path.write_text(json.dumps(data), encoding="utf-8")
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    notices.write_text("CPython\nOpenSSL\n", encoding="utf-8")
    return data_path, notices


def _binary(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")


def test_native_audit_attributes_every_binary_exactly_once(tmp_path):
    data, notices = _records(tmp_path)
    root = tmp_path / "onedir"
    _binary(root, "plugins/owned.dll")
    _binary(root, "_ssl.pyd")
    _binary(root, "python313.dll")
    _binary(root, "libcrypto-3.dll")
    _binary(root, "special.pyd")

    attributed, problems = native.audit(root, data_path=data, notices_path=notices)

    assert not problems
    assert attributed["plugins/owned.dll"] == "Astrolabe"
    assert attributed["special.pyd"] == "OpenSSL"


def test_native_audit_rejects_an_unattributed_binary(tmp_path):
    data, notices = _records(tmp_path)
    root = tmp_path / "onedir"
    _binary(root, "plugins/owned.dll")
    _binary(root, "_ssl.pyd")
    _binary(root, "python313.dll")
    _binary(root, "libcrypto-3.dll")
    _binary(root, "special.pyd")
    _binary(root, "new_dependency.dll")

    _, problems = native.audit(root, data_path=data, notices_path=notices)

    assert "native binary has no attribution: new_dependency.dll" in problems


def test_native_audit_rejects_overlapping_rules(tmp_path):
    data, notices = _records(tmp_path)
    records = json.loads(data.read_text(encoding="utf-8"))
    records["native_artifacts"][1].pop("exclude")
    data.write_text(json.dumps(records), encoding="utf-8")
    root = tmp_path / "onedir"
    _binary(root, "plugins/owned.dll")
    _binary(root, "_ssl.pyd")
    _binary(root, "python313.dll")
    _binary(root, "libcrypto-3.dll")
    _binary(root, "special.pyd")

    _, problems = native.audit(root, data_path=data, notices_path=notices)

    assert any("ambiguous attribution" in problem for problem in problems)
