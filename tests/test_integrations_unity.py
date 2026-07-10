"""Unity integration registry: bundled UPM package, install into project Packages/, auto_update."""
import json
import os
import sys

import pytest

from trackball_daemon import integrations
from trackball_daemon.config import Config


def _fake_project(tmp_path, name="DemoProject"):
    proj = tmp_path / name
    (proj / "Packages").mkdir(parents=True)
    (proj / "Assets").mkdir(parents=True)
    return proj


def _patch_unity(monkeypatch, project):
    monkeypatch.setattr(integrations, "detect_unity",
                        lambda: str(project / "Unity.exe") if project else None)
    monkeypatch.setattr(integrations, "_unity_project_candidates",
                        lambda: [str(project)] if project else [])


def test_unity_is_a_bundled_addin():
    assert "unity" in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("unity") == "0.1.6"
    assert integrations.APPS_BY_KEY["unity"].setup is integrations.install_unity


def test_install_copies_package_and_enables(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    _patch_unity(monkeypatch, proj)
    monkeypatch.setattr(integrations, "detect_unity", lambda: str(proj / "Unity.exe"))
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["unity"], cfg)
    assert ok is True
    u = cfg.data["apps"]["unity"]
    assert u["installed"] is True and u["enabled"] is True
    assert u["addin_version"] == "0.1.6"
    dest = proj / "Packages" / "com.astrolabe.trackball-nav"
    assert (dest / "package.json").exists()
    assert (dest / "version.json").exists()
    assert (dest / "Editor" / "TrackballNav.cs").exists()
    assert (dest / "Editor" / "TrackballNavCamera.cs").exists()
    assert integrations.installed_addin_version("unity") == "0.1.6"


def test_install_fails_without_project(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_unity", lambda: r"C:\Program Files\Unity\Editor\Unity.exe")
    monkeypatch.setattr(integrations, "_unity_project_candidates", lambda: [])
    cfg = Config().load()
    ok, msg, copies = integrations.normalize_install_result(
        integrations.install(integrations.APPS_BY_KEY["unity"], cfg))
    assert ok is False
    assert "project" in msg.lower()
    assert cfg.data["apps"]["unity"]["installed"] is False
    assert copies and "staged" in copies[0][0].lower()


def test_reinstall_does_not_re_enable(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    _patch_unity(monkeypatch, proj)
    monkeypatch.setattr(integrations, "detect_unity", lambda: str(proj / "Unity.exe"))
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["unity"], cfg)
    cfg.data["apps"]["unity"]["enabled"] = False
    ok, _msg = integrations.install(integrations.APPS_BY_KEY["unity"], cfg)
    assert ok is True
    assert cfg.data["apps"]["unity"]["enabled"] is False


def test_auto_update_recopies_on_version_bump(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    _patch_unity(monkeypatch, proj)
    monkeypatch.setattr(integrations, "detect_unity", lambda: str(proj / "Unity.exe"))
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["unity"], cfg)
    dest = proj / "Packages" / "com.astrolabe.trackball-nav"
    with open(dest / "version.json", "w", encoding="utf-8") as f:
        json.dump({"version": "0.0.1"}, f)
    assert integrations.update_available("unity") is True
    updated = integrations.auto_update(cfg)
    assert any(key == "unity" for key, _o, _n in updated)
    assert integrations.installed_addin_version("unity") == "0.1.6"


@pytest.mark.skipif(sys.platform != "win32", reason="status detection is Windows-only")
def test_status_line_reflects_detection(monkeypatch):
    appdef = integrations.APPS_BY_KEY["unity"]
    monkeypatch.setattr(appdef, "detect", lambda: r"C:\Program Files\Unity\Hub\Editor\6000.0.0f1\Editor\Unity.exe")
    assert "detected" in integrations.status_line(appdef).lower()
    monkeypatch.setattr(appdef, "detect", lambda: None)
    assert integrations.status_line(appdef) == "not detected"


def test_hub_projects_v1_unwraps_data(tmp_path, monkeypatch):
    """Unity Hub stores projects under schema_version + data{path: record}."""
    proj = _fake_project(tmp_path, "My project")
    hub = tmp_path / "UnityHub"
    hub.mkdir()
    (hub / "projects-v1.json").write_text(
        json.dumps({
            "schema_version": "v1",
            "data": {
                str(proj): {"title": "My project", "path": str(proj), "version": "6000.5.3f1"},
            },
        }),
        encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    found = integrations._unity_hub_recent_projects()
    assert any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(str(proj)))
               for p in found)


def test_install_from_hub_projects_json(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path, "My project")
    hub = tmp_path / "UnityHub"
    hub.mkdir()
    (hub / "projects-v1.json").write_text(
        json.dumps({
            "schema_version": "v1",
            "data": {str(proj): {"path": str(proj), "title": "My project"}},
        }),
        encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(integrations, "detect_unity", lambda: r"C:\Unity\Unity.exe")
    monkeypatch.setattr(integrations, "_unity_running_project_paths", lambda: [])
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["unity"], cfg)
    assert ok is True, msg
    assert (proj / "Packages" / "com.astrolabe.trackball-nav" / "package.json").exists()
