# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Each host add-on states its version in several places, and they have to agree.

The daemon reads a payload's version from its manifest to decide whether an installed copy is stale,
while the add-on reports its own in-source constant to the broker over the wire. When those two drift
apart, `auto_update` compares the wrong pair of numbers: it can decide an installed copy is current
while the host is running different code, and nothing observable goes wrong until a fix silently fails
to ship. Bumping a payload means touching two or three files, so drift is a matter of forgetting one.

The declaration sites are listed rather than discovered. Several payload manifests carry other version
fields -- an Unreal plugin's integer `Version`, an engine version, a package schema -- so a pattern
loose enough to find every site by itself would also match things that are not this add-on's version.
Adding a new declaration site means adding it here.
"""

from pathlib import Path
import re

import pytest

from trackball_daemon.integrations import ADDIN_KEYS, bundled_addin_version


PAYLOADS = Path(__file__).resolve().parents[1] / "trackball_daemon" / "plugins"

#: ``app id -> ((path relative to the payload root, pattern with one capturing group), ...)``.
#: Every capture must equal the version the daemon resolves for that add-on.
DECLARATION_SITES = {
    "blender": (
        ("blender/trackball_nav/__init__.py", r'ADDIN_VERSION = "([\d.]+)"'),
        ("blender/trackball_nav/__init__.py", r'"version": \((\d+, \d+, \d+)\)'),
    ),
    "freecad": (
        ("freecad/TrackballNav/tbnav_freecad.py", r'ADDIN_VERSION = "([\d.]+)"'),
    ),
    "fusion360": (
        ("fusion360/TrackballNav/TrackballNav.py", r'ADDIN_VERSION = "([\d.]+)"'),
        ("fusion360/TrackballNav/TrackballNav.manifest", r'"version": "([\d.]+)"'),
    ),
    "godot": (
        ("godot/trackball_nav/trackball_nav.gd", r'const ADDIN_VERSION := "([\d.]+)"'),
        ("godot/trackball_nav/plugin.cfg", r'version="([\d.]+)"'),
    ),
    "rhino": (
        ("rhino/TrackballNav/tbnav_rhino.py", r'ADDIN_VERSION = "([\d.]+)"'),
    ),
    "sketchup": (
        ("sketchup/trackball_nav/main.rb", r"ADDIN_VERSION = '([\d.]+)'"),
        ("sketchup/trackball_nav_loader.rb", r"ADDIN_VERSION = '([\d.]+)'"),
    ),
    "unity": (
        ("unity/com.astrolabe.trackball-nav/Editor/TrackballNav.cs",
         r'const string AddinVersion = "([\d.]+)"'),
        ("unity/com.astrolabe.trackball-nav/package.json", r'"version": "([\d.]+)"'),
    ),
    "unreal": (
        ("unreal/TrackballNav/Content/Python/trackball_nav.py", r'ADDIN_VERSION = "([\d.]+)"'),
        ("unreal/TrackballNav/TrackballNav.uplugin", r'"VersionName": "([\d.]+)"'),
    ),
}


def _declared(relative, pattern):
    text = (PAYLOADS / relative).read_text(encoding="utf-8")
    matches = re.findall(pattern, text)
    assert len(matches) == 1, (
        f"{relative} should declare its version exactly once for {pattern!r}, found {matches}")
    # Blender's bl_info spells the version as a tuple; normalize to the dotted form.
    return matches[0].replace(", ", ".")


def test_every_add_on_payload_has_its_declaration_sites_listed():
    """A payload with no listed site would pass every check below by having nothing to check."""
    payload_keys = ADDIN_KEYS - {"autocad"}          # the AutoCAD plugin is a compiled DLL

    assert set(DECLARATION_SITES) == payload_keys


@pytest.mark.parametrize("app_id", sorted(DECLARATION_SITES))
def test_declared_versions_match_the_version_the_daemon_resolves(app_id):
    resolved = bundled_addin_version(app_id)

    assert resolved, f"no bundled version resolved for {app_id}"
    for relative, pattern in DECLARATION_SITES[app_id]:
        assert _declared(relative, pattern) == resolved, relative


def test_the_autocad_plugin_version_comes_from_its_provenance_manifest():
    """Its version is a property of a compiled DLL, so it is asserted against provenance instead."""
    from trackball_daemon.autocad_artifact import validate_bundled_autocad_artifact

    artifact = validate_bundled_autocad_artifact()

    assert bundled_addin_version("autocad") == artifact["version"]
    assert artifact["informational_version"].startswith(f"{artifact['version']}+")
