"""SketchUp Desktop integration: annual-version detection, multi-version install, registry,
version-gated auto-update, config defaults, and status reporting.

The real licensed application is never launched by these tests. Install paths and APPDATA are
isolated with monkeypatch + the shared isolated_config fixture.
"""
import json
import sys
from pathlib import Path

import pytest

from trackball_daemon import integrations
from trackball_daemon.config import Config

_FAKE_2024 = r"C:\Program Files\SketchUp\SketchUp 2024\SketchUp\SketchUp.exe"
_FAKE_2026 = r"C:\Program Files\SketchUp\SketchUp 2026\SketchUp\SketchUp.exe"


def _patch_sketchup(monkeypatch, exes):
    monkeypatch.setattr(integrations, "_all_sketchup_exes", lambda: list(exes))
    monkeypatch.setattr(integrations, "detect_sketchup", lambda: (exes[-1] if exes else None))


def test_sketchup_is_a_bundled_addin():
    assert "sketchup" in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("sketchup") == "0.2.9"
    assert integrations.APPS_BY_KEY["sketchup"].setup is integrations.install_sketchup


def test_per_year_plugin_dirs_include_every_detected_version(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [_FAKE_2024, _FAKE_2026])
    dirs = integrations.sketchup_addon_dirs()
    assert [path.parents[2].name for path in dirs] == ["SketchUp 2024", "SketchUp 2026"]
    assert all(path.name == "trackball_nav" and path.parent.name == "Plugins" for path in dirs)


def test_existing_appdata_year_is_included(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [_FAKE_2026])
    old = integrations._sketchup_appdata_root() / "SketchUp 2025"
    old.mkdir(parents=True)
    years = [path.parents[2].name for path in integrations.sketchup_addon_dirs()]
    assert years == ["SketchUp 2025", "SketchUp 2026"]


def test_install_copies_loader_and_addon_to_all_years(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [_FAKE_2024, _FAKE_2026])
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["sketchup"], cfg)
    assert ok is True
    assert "2024" in msg and "2026" in msg
    su = cfg.data["apps"]["sketchup"]
    assert su["installed"] is True and su["enabled"] is True
    assert su["addin_version"] == "0.2.9"
    for addon in integrations.sketchup_addon_dirs():
        assert (addon.parent / "trackball_nav_loader.rb").exists()
        for name in ("main.rb", "camera.rb", "version.json"):
            assert (addon / name).exists(), f"missing {name} in {addon}"
    assert integrations.installed_addin_version("sketchup") == "0.2.9"


def test_install_fails_when_sketchup_absent(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [])
    cfg = Config().load()
    ok, msg = integrations.install(integrations.APPS_BY_KEY["sketchup"], cfg)
    assert ok is False
    assert "not found" in msg.lower()
    assert cfg.data["apps"]["sketchup"]["enabled"] is False


def test_reinstall_does_not_re_enable(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [_FAKE_2026])
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["sketchup"]
    integrations.install(appdef, cfg)
    cfg.data["apps"]["sketchup"]["enabled"] = False
    ok, _msg = integrations.install(appdef, cfg)
    assert ok is True
    assert cfg.data["apps"]["sketchup"]["enabled"] is False


def test_auto_update_recopies_on_version_bump(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [_FAKE_2026])
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["sketchup"], cfg)
    dest = integrations._sketchup_primary_addon_dir()
    with open(dest / "version.json", "w", encoding="utf-8") as file:
        json.dump({"version": "0.0.1"}, file)
    assert integrations.update_available("sketchup") is True
    updated = integrations.auto_update(cfg)
    assert any(key == "sketchup" for key, _old, _new in updated)
    assert integrations.installed_addin_version("sketchup") == "0.2.9"


def test_auto_update_skips_when_not_installed(isolated_config, monkeypatch):
    _patch_sketchup(monkeypatch, [_FAKE_2026])
    cfg = Config().load()
    assert all(key != "sketchup" for key, _old, _new in integrations.auto_update(cfg))


def test_sketchup_in_default_config(isolated_config):
    cfg = Config().load()
    assert "sketchup" in cfg.data["apps"]
    assert cfg.data["apps"]["sketchup"]["enabled"] is False


def test_sketchup_extension_consumes_selection_override():
    source = (Path(__file__).parents[1] / "trackball_daemon" / "plugins" / "sketchup" /
              "trackball_nav" / "camera.rb").read_text(encoding="utf-8")
    assert "advanced.fetch('selection_overrides_pivot', true)" in source
    assert "def selection_center(model)" in source
    assert "selected = selection_center(model)" in source


def test_sketchup_extension_consumes_shared_zoom_and_twist_controls():
    source = (Path(__file__).parents[1] / "trackball_daemon" / "plugins" / "sketchup" /
              "trackball_nav" / "camera.rb").read_text(encoding="utf-8")
    assert "advanced['twist_action']" in source
    assert "advanced['zoom_style']" in source
    assert "def zoom_pivot(" in source
    assert "camera.fov =" in source


@pytest.mark.skipif(sys.platform != "win32", reason="status detection is Windows-only")
def test_status_line_reflects_detection(monkeypatch):
    appdef = integrations.APPS_BY_KEY["sketchup"]
    monkeypatch.setattr(appdef, "detect", lambda: _FAKE_2026)
    assert "detected" in integrations.status_line(appdef).lower()
    monkeypatch.setattr(appdef, "detect", lambda: None)
    assert integrations.status_line(appdef) == "not detected"
