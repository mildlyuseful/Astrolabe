# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""SolidWorks integration registry: no-file COM setup and the fact that
it is deliberately NOT an add-in (so auto_update never touches it)."""

from trackball_daemon import integrations
from trackball_daemon.config import Config


def test_solidworks_is_not_an_addin():
    assert "solidworks" not in integrations.ADDIN_KEYS
    assert integrations.bundled_addin_version("solidworks") is None
    assert integrations.installed_addin_version("solidworks") is None
    assert integrations.update_available("solidworks") is False


def test_auto_update_ignores_solidworks(isolated_config):
    cfg = Config().load()
    cfg.set_app_operational("solidworks", installed=True)
    # auto_update only iterates the add-in registry, so SolidWorks is never auto-copied.
    assert all(key != "solidworks" for key, _old, _new in integrations.auto_update(cfg))


def test_setup_enables_without_file_copy(isolated_config, monkeypatch):
    cfg = Config().load()
    monkeypatch.setattr(integrations, "detect_solidworks", lambda: r"C:\fake\SLDWORKS.exe")
    appdef = integrations.APPS_BY_KEY["solidworks"]
    ok, msg = integrations.install(appdef, cfg)
    assert ok is True
    sw = cfg.snapshot().app_operational["solidworks"]
    assert sw["enabled"] is True and sw["installed"] is True
    assert sw["addin_version"] == ""                        # no add-in version recorded
    assert "COM" in msg and "nothing to install" in msg


def test_setup_fails_when_solidworks_absent(isolated_config, monkeypatch):
    cfg = Config().load()
    monkeypatch.setattr(integrations, "detect_solidworks", lambda: None)
    appdef = integrations.APPS_BY_KEY["solidworks"]
    ok, msg = integrations.install(appdef, cfg)
    assert ok is False
    assert "not found" in msg.lower()
    assert cfg.snapshot().app_operational["solidworks"]["enabled"] is False
