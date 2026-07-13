"""Rhino integration registry: scripts dir install + version-gated auto_update."""
import json

from trackball_daemon import integrations
from trackball_daemon.config import Config


def _patch_rhino(monkeypatch, tmp_path):
    exe = tmp_path / "Rhino 8" / "System" / "Rhino.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    scripts = tmp_path / "McNeel" / "Rhinoceros" / "8.0" / "scripts" / "TrackballNav"
    monkeypatch.setattr(integrations, "detect_rhino", lambda: str(exe))
    monkeypatch.setattr(integrations, "rhino_scripts_dir", lambda: scripts)
    monkeypatch.setattr(integrations, "_rhino_append_startup_command", lambda _cmd: True)
    return scripts


def test_rhino_is_a_bundled_addin():
    assert "rhino" in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("rhino") == "0.1.18"
    assert integrations.APPS_BY_KEY["rhino"].setup is integrations.install_rhino


def test_install_copies_scripts_and_enables(isolated_config, tmp_path, monkeypatch):
    scripts = _patch_rhino(monkeypatch, tmp_path)
    cfg = Config().load()
    ok, msg, copies = integrations.normalize_install_result(
        integrations.install(integrations.APPS_BY_KEY["rhino"], cfg))
    assert ok is True
    r = cfg.data["apps"]["rhino"]
    assert r["installed"] is True and r["enabled"] is True
    assert r["addin_version"] == "0.1.18"
    assert (scripts / "version.json").exists()
    assert (scripts / "tbnav_rhino.py").exists()
    assert (scripts / "tbnav_camera.py").exists()
    assert (scripts / "start.py").exists()
    assert integrations.installed_addin_version("rhino") == "0.1.18"
    assert "startup" in msg.lower() or "restart" in msg.lower()
    # auto-register mocked True -> no copy button needed
    assert copies == []


def test_install_offers_copy_when_startup_not_registered(isolated_config, tmp_path, monkeypatch):
    scripts = _patch_rhino(monkeypatch, tmp_path)
    monkeypatch.setattr(integrations, "_rhino_append_startup_command", lambda _cmd: False)
    cfg = Config().load()
    ok, msg, copies = integrations.normalize_install_result(
        integrations.install(integrations.APPS_BY_KEY["rhino"], cfg))
    assert ok is True
    assert "type Options" in msg or "type options" in msg.lower()
    assert "command line" in msg.lower()
    assert len(copies) == 1
    label, cmd = copies[0]
    assert "command" in label.lower()
    assert "RunPythonScript" in cmd
    assert str(scripts / "start.py") in cmd


def test_normalize_install_result_accepts_two_or_three_tuple():
    assert integrations.normalize_install_result((True, "hi")) == (True, "hi", [])
    assert integrations.normalize_install_result((False, "no", [("Copy", "x")])) == (
        False, "no", [("Copy", "x")])


def test_install_fails_when_rhino_absent(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_rhino", lambda: None)
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["rhino"], cfg)
    assert ok is False
    assert "not found" in msg.lower()


def test_reinstall_does_not_re_enable(isolated_config, tmp_path, monkeypatch):
    _patch_rhino(monkeypatch, tmp_path)
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["rhino"], cfg)
    cfg.data["apps"]["rhino"]["enabled"] = False
    ok, _msg = integrations.install(integrations.APPS_BY_KEY["rhino"], cfg)
    assert ok is True
    assert cfg.data["apps"]["rhino"]["enabled"] is False


def test_auto_update_recopies_on_version_bump(isolated_config, tmp_path, monkeypatch):
    scripts = _patch_rhino(monkeypatch, tmp_path)
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["rhino"], cfg)
    with open(scripts / "version.json", "w", encoding="utf-8") as f:
        json.dump({"version": "0.0.1"}, f)
    assert integrations.update_available("rhino") is True
    updated = integrations.auto_update(cfg)
    assert any(key == "rhino" for key, _o, _n in updated)
    assert integrations.installed_addin_version("rhino") == "0.1.18"
