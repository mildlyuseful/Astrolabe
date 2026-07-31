# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Godot integration registry: EditorPlugin into project addons/, enable in project.godot."""
import json
import os

from trackball_daemon import integrations
from trackball_daemon.config import Config


def _fake_project(tmp_path, name="GodotDemo"):
    proj = tmp_path / name
    proj.mkdir(parents=True)
    (proj / "project.godot").write_text("; Engine configuration file.\n\n[application]\n\n", encoding="utf-8")
    return proj


def _patch_godot(monkeypatch, project):
    monkeypatch.setattr(integrations, "detect_godot",
                        lambda: str(project / "Godot.exe") if project else None)
    monkeypatch.setattr(integrations, "_godot_project_candidates",
                        lambda: [str(project)] if project else [])


def test_godot_is_a_bundled_addin():
    assert "godot" in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("godot") == "0.1.14"
    assert integrations.APPS_BY_KEY["godot"].setup is integrations.install_godot


def test_install_copies_addon_enables_and_marks(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    _patch_godot(monkeypatch, proj)
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["godot"], cfg)
    assert ok is True
    g = cfg.snapshot().app_operational["godot"]
    assert g["installed"] is True and g["enabled"] is True
    assert g["addin_version"] == "0.1.14"
    dest = proj / "addons" / "trackball_nav"
    assert (dest / "plugin.cfg").exists()
    assert (dest / "version.json").exists()
    assert (dest / "trackball_nav.gd").exists()
    assert (dest / "trackball_nav_camera.gd").exists()
    pg = (proj / "project.godot").read_text(encoding="utf-8")
    assert "res://addons/trackball_nav/plugin.cfg" in pg
    assert "[editor_plugins]" in pg


def test_install_fails_without_project(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_godot", lambda: r"C:\Godot\Godot.exe")
    monkeypatch.setattr(integrations, "_godot_project_candidates", lambda: [])
    cfg = Config().load()
    ok, msg, copies = integrations.normalize_install_result(
        integrations.install(integrations.APPS_BY_KEY["godot"], cfg))
    assert ok is False
    assert "project" in msg.lower()
    assert "manual install" in msg.lower()
    assert "addons\\trackball_nav" in msg.lower() or "addons/trackball_nav" in msg.lower()
    assert copies and "staged" in copies[0][0].lower()
    assert any("plugin.cfg" in label.lower() for label, _ in copies)


def test_enable_plugin_idempotent(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    _patch_godot(monkeypatch, proj)
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["godot"], cfg)
    integrations.install(integrations.APPS_BY_KEY["godot"], cfg)  # second time
    pg = (proj / "project.godot").read_text(encoding="utf-8")
    assert pg.count("res://addons/trackball_nav/plugin.cfg") == 1


def test_auto_update_recopies_on_version_bump(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    _patch_godot(monkeypatch, proj)
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["godot"], cfg)
    dest = proj / "addons" / "trackball_nav"
    with open(dest / "version.json", "w", encoding="utf-8") as f:
        json.dump({"version": "0.0.1"}, f)
    assert integrations.update_available("godot") is True
    updated = integrations.auto_update(cfg)
    assert any(key == "godot" for key, _o, _n in updated)


def test_recent_projects_parses_projects_cfg_section_headers(tmp_path, monkeypatch):
    """Godot 4 projects.cfg uses [C:/path/to/project] section names, not bare tokens."""
    proj = _fake_project(tmp_path, "astrolabe-test")
    # Match Godot's forward-slash absolute path in the section header.
    godot_path = str(proj).replace("\\", "/")
    godot_dir = tmp_path / "fake_appdata" / "Godot"
    godot_dir.mkdir(parents=True)
    (godot_dir / "projects.cfg").write_text(
        f"[{godot_path}]\n\nfavorite=false\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "fake_appdata"))
    found = integrations._godot_recent_projects()
    assert any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(str(proj)))
               for p in found)


def test_install_from_projects_cfg(isolated_config, tmp_path, monkeypatch):
    proj = _fake_project(tmp_path, "astrolabe-test")
    godot_path = str(proj).replace("\\", "/")
    godot_dir = tmp_path / "fake_appdata" / "Godot"
    godot_dir.mkdir(parents=True)
    (godot_dir / "projects.cfg").write_text(
        f"[{godot_path}]\n\nfavorite=false\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "fake_appdata"))
    monkeypatch.setattr(integrations, "detect_godot", lambda: r"C:\Godot\Godot.exe")
    # Do not stub _godot_project_candidates — exercise real recent-project parsing.
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["godot"], cfg)
    assert ok is True, msg
    assert (proj / "addons" / "trackball_nav" / "plugin.cfg").exists()
