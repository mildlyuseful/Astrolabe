"""Unreal integration registry: it IS a bundled add-on (so auto_update manages it), install
copies the content-only plugin into the engine's Engine/Plugins dir and marks it enabled, the
plugin dir resolves under <engine>/Engine/Plugins, a copy failure (admin needed) returns manual
steps without marking installed, and detection status.

Mirrors tests/test_integrations_freecad.py. detect_unreal / _all_unreal_engines are monkeypatched
to a fake engine tree under tmp_path so these run deterministically on any machine/CI; the per-user
config dir is isolated into a temp APPDATA by the `isolated_config` fixture.
"""
import json
import sys

import pytest

from trackball_daemon import integrations
from trackball_daemon.config import Config


def _fake_engine(tmp_path, label="UE_9.9"):
    """Create a fake UnrealEditor.exe under a writable temp 'engine' tree and return its path."""
    exe = tmp_path / "EpicGames" / label / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")                                  # touch
    return str(exe)


def _patch_unreal(monkeypatch, exe):
    monkeypatch.setattr(integrations, "detect_unreal", lambda: exe)
    monkeypatch.setattr(integrations, "_all_unreal_engines", lambda: ([exe] if exe else []))


def test_unreal_is_a_bundled_addin():
    assert "unreal" in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("unreal") == "0.2.8"
    assert integrations.APPS_BY_KEY["unreal"].setup is integrations.install_unreal


def test_plugin_dir_under_engine_plugins(isolated_config, tmp_path, monkeypatch):
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)
    p = integrations.unreal_plugin_dir()
    assert p.name == "TrackballNav"
    assert p.parent.name == "Plugins"
    assert p.parent.parent.name == "Engine"             # <engine>/Engine/Plugins/TrackballNav


def test_install_copies_plugin_and_enables(isolated_config, tmp_path, monkeypatch):
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["unreal"]
    ok, _msg = integrations.install(appdef, cfg)
    assert ok is True
    u = cfg.data["apps"]["unreal"]
    assert u["installed"] is True and u["enabled"] is True
    assert u["addin_version"] == "0.2.8"
    dest = integrations.unreal_plugin_dir()
    # the whole plugin must be copied: the .uplugin descriptor, the version manifest the daemon
    # reads, and the Content/Python payload Unreal auto-runs.
    assert (dest / "TrackballNav.uplugin").exists()
    assert (dest / "version.json").exists()
    for name in ("init_unreal.py", "trackball_nav.py", "tbnav_unreal_camera.py"):
        assert (dest / "Content" / "Python" / name).exists(), f"missing {name}"
    uplugin = (dest / "TrackballNav.uplugin").read_text(encoding="utf-8")
    assert "GeoReferencing" in uplugin          # under-cursor orbit Half A
    assert integrations.installed_addin_version("unreal") == "0.2.8"


def test_install_fails_when_unreal_absent(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_unreal", lambda: None)
    monkeypatch.setattr(integrations, "_all_unreal_engines", lambda: [])
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["unreal"]
    ok, msg = integrations.install(appdef, cfg)
    assert ok is False
    assert "not found" in msg.lower()
    assert cfg.data["apps"]["unreal"]["enabled"] is False


def test_install_reports_manual_steps_when_unwritable(isolated_config, tmp_path, monkeypatch):
    # Engine detected but the Plugins copy fails (Program Files needs admin) -> ok=False with manual
    # steps, and the app is NOT marked installed (the file-based version check then reports None).
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)

    def _denied(*a, **k):
        raise OSError("Access is denied")

    monkeypatch.setattr(integrations.shutil, "copytree", _denied)
    cfg = Config().load()
    ok, msg, copies = integrations.normalize_install_result(
        integrations.install(integrations.APPS_BY_KEY["unreal"], cfg))
    assert ok is False
    assert "admin" in msg.lower()
    assert cfg.data["apps"]["unreal"]["installed"] is False
    assert copies and "bundled" in copies[0][0].lower()


def test_update_not_offered_before_install(isolated_config, tmp_path, monkeypatch):
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)
    assert integrations.installed_addin_version("unreal") is None
    assert integrations.update_available("unreal") is False


def test_reinstall_does_not_re_enable(isolated_config, tmp_path, monkeypatch):
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["unreal"]
    integrations.install(appdef, cfg)
    cfg.data["apps"]["unreal"]["enabled"] = False        # user disabled it
    ok, _msg = integrations.install(appdef, cfg)          # a reinstall/update
    assert ok is True
    assert cfg.data["apps"]["unreal"]["enabled"] is False  # not silently re-enabled


def test_auto_update_recopies_on_version_bump(isolated_config, tmp_path, monkeypatch):
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["unreal"], cfg)
    # Simulate the installed copy being OLDER than the bundled version.
    dest = integrations.unreal_plugin_dir()
    with open(dest / "version.json", "w", encoding="utf-8") as f:
        json.dump({"version": "0.0.1"}, f)
    assert integrations.update_available("unreal") is True
    updated = integrations.auto_update(cfg)
    assert any(key == "unreal" for key, _old, _new in updated)
    assert integrations.installed_addin_version("unreal") == integrations.bundled_addin_version("unreal")


def test_auto_update_skips_when_not_installed(isolated_config, tmp_path, monkeypatch):
    exe = _fake_engine(tmp_path)
    _patch_unreal(monkeypatch, exe)
    cfg = Config().load()
    # never installed -> auto_update must not touch it (installed version is None)
    assert all(key != "unreal" for key, _old, _new in integrations.auto_update(cfg))


@pytest.mark.skipif(sys.platform != "win32", reason="status detection is Windows-only")
def test_status_line_reflects_detection(monkeypatch):
    appdef = integrations.APPS_BY_KEY["unreal"]
    monkeypatch.setattr(
        appdef, "detect",
        lambda: r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe")
    assert "detected" in integrations.status_line(appdef).lower()
    monkeypatch.setattr(appdef, "detect", lambda: None)
    assert integrations.status_line(appdef) == "not detected"
