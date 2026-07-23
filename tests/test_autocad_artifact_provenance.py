"""Binary-level checks for the bundled AutoCAD plugin and its provenance manifest."""

import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from trackball_daemon.autocad_artifact import validate_bundled_autocad_artifact


ROOT = Path(__file__).parents[1]
PLUGIN_SOURCE = ROOT / "plugin_src" / "autocad" / "TrackballNavAcad"
BUNDLE = ROOT / "trackball_daemon" / "plugins" / "autocad"


def _project_version():
    project = ET.parse(PLUGIN_SOURCE / "TrackballNavAcad.csproj").getroot()
    return project.findtext("./PropertyGroup/Version")


def test_bundled_autocad_dll_matches_its_provenance_manifest():
    manifest = validate_bundled_autocad_artifact(ROOT / "trackball_daemon")
    plugin = (PLUGIN_SOURCE / "Plugin.cs").read_text(encoding="utf-8")

    assert manifest["version"] == _project_version()
    assert f'PluginVersion = "{manifest["version"]}"' in plugin
    assert manifest["autocad_reference_family"].startswith("AutoCAD 2026")


def test_controlled_autocad_build_injects_and_checks_provenance():
    script = (ROOT / "tools" / "build_autocad_plugin.ps1").read_text(encoding="utf-8")

    assert "git diff --quiet $SourceRevision" in script
    assert "git ls-files --others --exclude-standard" in script
    assert '"-p:SourceRevisionId=$SourceRevision"' in script
    assert '"-p:ContinuousIntegrationBuild=true"' in script
    assert "ProductVersion -ne $ExpectedInformationalVersion" in script
    assert 'SelectNodes("/Project/ItemGroup/Reference[HintPath]")' in script
    assert "Get-FileHash -Algorithm SHA256" in script


def test_bare_release_build_cannot_publish_the_bundled_artifact():
    project = ET.parse(PLUGIN_SOURCE / "TrackballNavAcad.csproj").getroot()

    assert project.find("./Target[@Name='CopyToPlugins']") is None


def test_autocad_artifact_verifier_accepts_the_checked_in_bundle():
    manifest = json.loads((BUNDLE / "version.json").read_text(encoding="utf-8"))
    result = subprocess.run(
        [sys.executable, "tools/verify_autocad_artifact.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"AutoCAD plugin {manifest['version']} verified" in result.stdout
