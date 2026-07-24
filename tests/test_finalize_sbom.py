# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from tools.finalize_sbom import finalize_sbom


def _write_sbom(path, root):
    path.write_text(json.dumps({
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {"component": root},
        "components": [],
    }), encoding="utf-8")


def test_finalize_sbom_adds_dynamic_version_reproducibly(tmp_path):
    sbom = tmp_path / "release.cdx.json"
    _write_sbom(sbom, {
        "bom-ref": "root-component",
        "name": "trackball-daemon",
        "type": "application",
    })

    finalize_sbom(sbom, name="trackball-daemon", version="0.1.75")
    first = sbom.read_bytes()
    finalize_sbom(sbom, name="trackball-daemon", version="0.1.75")

    assert sbom.read_bytes() == first
    root = json.loads(first)["metadata"]["component"]
    assert root["name"] == "trackball-daemon"
    assert root["version"] == "0.1.75"


def test_finalize_sbom_rejects_wrong_or_missing_root(tmp_path):
    sbom = tmp_path / "release.cdx.json"
    _write_sbom(sbom, {"name": "other", "type": "application"})
    with pytest.raises(ValueError, match="does not match"):
        finalize_sbom(sbom, name="trackball-daemon", version="1")

    _write_sbom(sbom, {"name": "trackball-daemon", "type": "library"})
    with pytest.raises(ValueError, match="no application root"):
        finalize_sbom(sbom, name="trackball-daemon", version="1")
