# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Every distributed form and every externally copied payload carries its notices.

A licence that only exists in the repository protects nobody. These checks follow the files into
the wheel, the source distribution, the onedir tree, the release archive, and the host folders that
integration setup writes into.
"""

import json
from pathlib import Path
import zipfile

import pytest

from tools.archive_release import build_archive
from trackball_daemon.release_smoke import _PAYLOAD_DIRECTORIES, _PAYLOAD_NOTICES


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "trackball_daemon" / "plugins"
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
MANIFEST = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
RELEASE_BUILD = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")

DISTRIBUTED_NOTICES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "LICENSING.md")


def _normalized(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("parts", _PAYLOAD_DIRECTORIES, ids=lambda parts: "/".join(parts))
@pytest.mark.parametrize("name", _PAYLOAD_NOTICES)
def test_each_copied_payload_carries_the_project_notices(parts, name):
    packaged = PLUGINS.joinpath(*parts, name)

    assert packaged.is_file(), f"{'/'.join(parts)} would be copied into a host without {name}"
    assert _normalized(packaged) == _normalized(ROOT / name), (
        f"{packaged.relative_to(ROOT)} has drifted from the canonical {name}")


def test_payload_notice_list_covers_every_directory_setup_copies():
    """A new integration must not be able to ship without its notices being considered.

    `blender/startup` holds a single-file shim rather than a copied directory; a lone .py file in
    Blender's own startup folder carries its SPDX header instead of an adjacent licence.
    """
    discovered = set()
    for host in sorted(PLUGINS.iterdir()):
        if not host.is_dir():
            continue
        roots = [child for child in sorted(host.iterdir())
                 if child.is_dir() and child.name != "startup"]
        # A host either contains its payload root (blender/trackball_nav) or is one (autocad).
        for root in roots or [host]:
            discovered.add(root.relative_to(PLUGINS).as_posix())

    assert discovered == {"/".join(parts) for parts in _PAYLOAD_DIRECTORIES}


def test_wheel_and_sdist_declare_every_notice_file():
    for name in DISTRIBUTED_NOTICES:
        assert f'"{name}"' in PYPROJECT, f"{name} is not in the wheel's license-files"
        assert name in MANIFEST, f"{name} is not included in the source distribution"
    assert '"LICENSES/**"' in PYPROJECT
    assert "recursive-include LICENSES *" in MANIFEST
    assert 'license = "Apache-2.0"' in PYPROJECT


def test_onedir_build_places_notices_beside_the_executable():
    for name in DISTRIBUTED_NOTICES:
        assert f"--include-data-files={name}={name}" in RELEASE_BUILD, name
    # The LGPL and GPL texts pystray obliges the artifact to carry live under LICENSES/.
    assert "--include-data-dir=LICENSES=LICENSES" in RELEASE_BUILD


def test_release_archive_carries_notices_out_of_the_onedir_tree(tmp_path):
    """The ZIP is made from the built tree, so notices reach it only if the build placed them."""
    source = tmp_path / "Astrolabe.dist"
    (source / "LICENSES").mkdir(parents=True)
    (source / "Astrolabe.exe").write_bytes(b"MZ\x00app")
    for name in DISTRIBUTED_NOTICES:
        (source / name).write_text(f"{name} body\n", encoding="utf-8")
    (source / "LICENSES" / "LGPL-3.0.txt").write_text("lgpl\n", encoding="utf-8")

    build_archive(source, tmp_path / "release.zip")

    with zipfile.ZipFile(tmp_path / "release.zip") as archive:
        names = set(archive.namelist())
    for name in DISTRIBUTED_NOTICES:
        assert f"Astrolabe.dist/{name}" in names
    assert "Astrolabe.dist/LICENSES/LGPL-3.0.txt" in names


def test_autocad_runtime_payload_copies_its_notices_explicitly(tmp_path, monkeypatch):
    """The compiled DLL cannot carry a header, and its runtime folder is outside the app tree."""
    from trackball_daemon import integrations

    runtime = tmp_path / "acad_plugin"
    monkeypatch.setattr(integrations, "_acad_runtime_plugin_dir", lambda: runtime)

    status, detail = integrations._copy_acad_plugin()

    assert status == "copied", detail
    assert (runtime / "TrackballNavAcad.dll").read_bytes().startswith(b"MZ")
    assert json.loads((runtime / "version.json").read_text(encoding="utf-8"))["schema"] == 1
    for name in _PAYLOAD_NOTICES:
        assert _normalized(runtime / name) == _normalized(ROOT / name), name
