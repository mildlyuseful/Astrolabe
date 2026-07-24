# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""FreeCAD integration registry: it IS a bundled add-on (so auto_update manages it), install
copies the add-on into FreeCAD's user Mod dir and marks it enabled, the user Mod dir resolves
to the versioned layout (FreeCAD >= 1.0) or the legacy flat one, and detection status.

Mirrors tests/test_integrations_solidworks.py + the Blender wiring test. detect_freecad is
monkeypatched to a fake path so these run deterministically on any machine/CI; the per-user
config + Mod dir are isolated into a temp APPDATA by the `isolated_config` fixture.
"""
import json

from trackball_daemon import integrations
from trackball_daemon.config import Config

_FAKE_11 = r"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe"
_FAKE_021 = r"C:\Program Files\FreeCAD 0.21\bin\FreeCAD.exe"


def test_freecad_is_a_bundled_addin():
    assert "freecad" in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("freecad") == "0.1.14"
    assert integrations.APPS_BY_KEY["freecad"].setup is integrations.install_freecad


def test_user_mod_dir_versioned_layout(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_11)
    p = integrations.freecad_user_mod_dir()
    assert p.name == "TrackballNav"
    assert p.parent.name == "Mod"
    assert p.parent.parent.name == "v1-1"           # FreeCAD >= 1.0 uses %APPDATA%\FreeCAD\vMAJ-MIN\Mod


def test_user_mod_dir_legacy_flat_layout(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_021)
    p = integrations.freecad_user_mod_dir()
    assert p.name == "TrackballNav"
    assert p.parent.name == "Mod" and p.parent.parent.name == "FreeCAD"   # <= 0.21 flat layout


def test_install_copies_addon_and_enables(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_11)
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["freecad"]
    ok, msg = integrations.install(appdef, cfg)
    assert ok is True
    fc = cfg.snapshot().app_operational["freecad"]
    assert fc["installed"] is True and fc["enabled"] is True
    assert fc["addin_version"] == "0.1.14"
    dest = integrations.freecad_user_mod_dir()
    # the whole add-on must be copied -- both Init files (FreeCAD needs Init.py to load the Mod),
    # the logic module, the pure-math module, and the version manifest.
    for name in ("Init.py", "InitGui.py", "tbnav_freecad.py", "tbnav_camera.py", "version.json"):
        assert (dest / name).exists(), f"missing {name}"
    assert integrations.installed_addin_version("freecad") == "0.1.14"


def test_install_fails_when_freecad_absent(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: None)
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["freecad"]
    ok, msg = integrations.install(appdef, cfg)
    assert ok is False
    assert "not found" in msg.lower()
    assert cfg.snapshot().app_operational["freecad"]["enabled"] is False


def test_update_not_offered_before_install(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_11)
    assert integrations.installed_addin_version("freecad") is None
    assert integrations.update_available("freecad") is False


def test_reinstall_does_not_re_enable(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_11)
    cfg = Config().load()
    appdef = integrations.APPS_BY_KEY["freecad"]
    integrations.install(appdef, cfg)
    cfg.set_app_operational("freecad", enabled=False)        # user disabled it
    ok, _msg = integrations.install(appdef, cfg)           # a reinstall/update
    assert ok is True
    assert cfg.snapshot().app_operational["freecad"]["enabled"] is False


def test_auto_update_recopies_on_version_bump(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_11)
    cfg = Config().load()
    integrations.install(integrations.APPS_BY_KEY["freecad"], cfg)
    # Simulate the installed copy being OLDER than the bundled version.
    dest = integrations.freecad_user_mod_dir()
    with open(dest / "version.json", "w", encoding="utf-8") as f:
        json.dump({"version": "0.0.1"}, f)
    assert integrations.update_available("freecad") is True
    updated = integrations.auto_update(cfg)
    assert any(key == "freecad" for key, _old, _new in updated)
    assert integrations.installed_addin_version("freecad") == integrations.bundled_addin_version("freecad")


def test_auto_update_skips_when_not_installed(isolated_config, monkeypatch):
    monkeypatch.setattr(integrations, "detect_freecad", lambda: _FAKE_11)
    cfg = Config().load()
    # never installed -> auto_update must not touch it (installed version is None)
    assert all(key != "freecad" for key, _old, _new in integrations.auto_update(cfg))
