# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Every 3D Apps card has honest compatibility/setup metadata and actions."""
from dataclasses import replace

import pytest

from trackball_daemon import integrations
from trackball_daemon.app_registry import APP_SPECS_BY_ID


def test_every_app_has_complete_copyable_setup_metadata():
    assert set(integrations.APPS_BY_KEY) == {app.key for app in integrations.APPS}
    for app in integrations.APPS:
        assert app.spec is APP_SPECS_BY_ID[app.key]
        assert app.install_model, app.key
        assert app.supported_versions, app.key
        assert app.setup_instructions, app.key
        assert app.manual_install, app.key
        assert app.health_check, app.key
        assert app.security_notes, app.key
        text = integrations.integration_instructions(app)
        assert "Install model\n" in text
        assert "Automatic setup\n" in text
        assert "Manual setup / restricted permissions\n" in text
        assert "Security and permissions\n" in text
        assert "Health check\n" in text


def test_sensitive_setups_have_explicit_preflight_confirmations():
    sensitive = {"unreal", "godot", "rhino", "onshape", "autocad"}
    assert sensitive <= {app.key for app in integrations.APPS if app.security_confirmation}


def test_known_supported_unverified_and_unsupported_versions_are_distinct():
    blender = integrations.APPS_BY_KEY["blender"]
    assert integrations.compatibility(
        blender, r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe").status == "supported"
    assert integrations.compatibility(
        blender, r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe").status == "unverified"
    assert integrations.compatibility(
        blender, r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe").status == "unsupported"

    autocad = integrations.APPS_BY_KEY["autocad"]
    assert integrations.compatibility(autocad, r"C:\Autodesk\AutoCAD 2026\acad.exe").status == "supported"
    assert integrations.compatibility(autocad, r"C:\Autodesk\AutoCAD 2028\acad.exe").status == "unverified"
    assert integrations.compatibility(autocad, r"C:\Autodesk\AutoCAD 2024\acad.exe").status == "unsupported"


def test_status_line_warns_for_unverified_and_unsupported_hosts(monkeypatch):
    blender = integrations.APPS_BY_KEY["blender"]
    monkeypatch.setattr(integrations.sys, "platform", "win32")
    unverified = replace(blender, detect=lambda: r"C:\Blender Foundation\Blender 5.2\blender.exe")
    unsupported = replace(blender, detect=lambda: r"C:\Blender Foundation\Blender 3.6\blender.exe")
    assert integrations.status_line(unverified).startswith("CAUTION")
    assert integrations.status_line(unsupported).startswith("WARNING")


def test_no_file_integrations_have_no_placebo_recheck_action():
    solidworks = integrations.APPS_BY_KEY["solidworks"]
    onshape = integrations.APPS_BY_KEY["onshape"]
    assert solidworks.needs_plugin is False
    assert solidworks.setup_required is False
    assert integrations.setup_action_label(solidworks, {"installed": False}) == "Enable"
    assert integrations.setup_action_label(solidworks, {"installed": True}) is None
    assert integrations.setup_action_label(onshape, {"installed": False}) == "Set up"
    assert integrations.setup_action_label(onshape, {"installed": True}) is None


def test_addin_actions_follow_real_install_and_update_state(monkeypatch):
    blender = integrations.APPS_BY_KEY["blender"]
    monkeypatch.setattr(integrations, "installed_addin_version", lambda _key: None)
    assert integrations.setup_action_label(blender, {}) == "Set up"

    monkeypatch.setattr(integrations, "installed_addin_version", lambda _key: "1.0")
    monkeypatch.setattr(integrations, "update_available", lambda _key, **_kwargs: False)
    assert integrations.setup_action_label(blender, {}) == "Reinstall"

    monkeypatch.setattr(integrations, "update_available", lambda _key, **_kwargs: True)
    monkeypatch.setattr(integrations, "bundled_addin_version", lambda _key: "2.0")
    assert integrations.setup_action_label(blender, {}) == "Update → v2.0"


def test_loaded_copy_version_overrides_primary_destination_for_update_state(monkeypatch):
    unity = integrations.APPS_BY_KEY["unity"]
    monkeypatch.setattr(integrations, "installed_addin_version", lambda _key: "2.0")
    monkeypatch.setattr(integrations, "bundled_addin_version", lambda _key: "2.0")

    assert integrations.setup_action_label(unity, {}, installed_version="1.0") == "Update → v2.0"
    assert integrations.setup_action_label(unity, {}, installed_version="2.0") == "Reinstall"
    assert integrations.update_available("unity", installed_version="1.0") is True


def test_rolling_web_apps_have_a_supported_policy_without_a_local_version():
    fusion = integrations.APPS_BY_KEY["fusion360"]
    onshape = integrations.APPS_BY_KEY["onshape"]
    assert integrations.compatibility(fusion, r"C:\Fusion\FusionLauncher.exe").status == "supported"
    assert integrations.compatibility(onshape, "browser-based (no local install)").status == "supported"


@pytest.mark.parametrize(("key", "detected_path"), [
    ("freecad", r"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe"),
    ("solidworks", r"C:\fake\SLDWORKS.exe"),
    ("autocad", r"C:\fake\acad.exe"),
    ("sketchup", r"C:\Program Files\SketchUp\SketchUp 2026\SketchUp.exe"),
    ("godot", r"C:\Godot\Godot_v4.3-stable_win64.exe"),
    ("rhino", r"C:\Program Files\Rhino 8\System\Rhino.exe"),
    ("unity", r"C:\Program Files\Unity\Hub\Editor\6000.0.0f1\Editor\Unity.exe"),
    ("unreal", r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"),
])
def test_status_line_reflects_detection(monkeypatch, key, detected_path):
    """All desktop integrations share one detection/status contract."""
    monkeypatch.setattr(integrations.sys, "platform", "win32")
    appdef = integrations.APPS_BY_KEY[key]
    monkeypatch.setattr(appdef, "detect", lambda: detected_path)
    assert "detected" in integrations.status_line(appdef).lower()
    monkeypatch.setattr(appdef, "detect", lambda: None)
    assert integrations.status_line(appdef) == "not detected"
