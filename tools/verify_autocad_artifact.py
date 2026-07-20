"""Fail when the bundled AutoCAD DLL is stale or lacks attributable build inputs."""

from pathlib import Path, PureWindowsPath
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trackball_daemon.autocad_artifact import validate_bundled_autocad_artifact  # noqa: E402


PLUGIN_SOURCE = "plugin_src/autocad/TrackballNavAcad"


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def main():
    manifest = validate_bundled_autocad_artifact(ROOT / "trackball_daemon")
    source_root = ROOT / PLUGIN_SOURCE
    project = ET.parse(source_root / "TrackballNavAcad.csproj").getroot()
    plugin = (source_root / "Plugin.cs").read_text(encoding="utf-8")
    project_version = project.findtext("./PropertyGroup/Version")
    if project_version != manifest["version"] or (
            f'PluginVersion = "{manifest["version"]}"' not in plugin):
        raise SystemExit("AutoCAD source and artifact versions are not synchronized.")

    project_references = {
        PureWindowsPath(hint_path).name.casefold()
        for reference in project.findall("./ItemGroup/Reference")
        if (hint_path := reference.findtext("HintPath"))
    }
    manifest_references = {
        reference["name"].casefold()
        for reference in manifest["autocad_references"]
    }
    if project_references != manifest_references:
        raise SystemExit(
            "AutoCAD reference inputs do not match the provenance manifest.")

    current_tree = _git("rev-parse", f"HEAD:{PLUGIN_SOURCE}")
    if current_tree != manifest["source_tree"]:
        raise SystemExit(
            "Bundled AutoCAD DLL is stale: committed plugin sources do not match its manifest."
        )
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", PLUGIN_SOURCE], cwd=ROOT
    )
    if dirty.returncode:
        raise SystemExit("AutoCAD plugin sources have uncommitted changes.")
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", PLUGIN_SOURCE)
    if untracked:
        raise SystemExit(f"Untracked AutoCAD plugin sources are present: {untracked}")
    print(
        f"AutoCAD plugin {manifest['version']} verified: "
        f"{manifest['dll_sha256']} from {manifest['source_revision']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
