# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Exhaustive contracts for sparse System -> Global -> app resolution."""
import pytest

from trackball_daemon.app_registry import APP_IDS, APP_SPECS_BY_ID
from trackball_daemon.config_resolver import (
    ResolutionLayer,
    resolve_all_for_app,
    resolve_all_globals,
    resolve_app,
    resolve_global,
    validate_override_maps,
)
from trackball_daemon.settings_schema import SETTING_SPECS, SettingScope


def _empty_apps():
    return {app_id: {} for app_id in APP_IDS}


def test_resolution_precedence_and_link_state_are_explicit():
    app_maps = _empty_apps()
    base = resolve_app("navigation.orbit.pivot", "blender", {}, app_maps["blender"])
    assert (base.value, base.layer, base.follows_global) == (
        "camera", ResolutionLayer.SYSTEM_APP, True)

    inherited = resolve_app(
        "navigation.orbit.pivot", "blender",
        {"navigation.orbit.pivot": "selection"}, app_maps["blender"])
    assert (inherited.value, inherited.layer, inherited.follows_global) == (
        "selection", ResolutionLayer.GLOBAL, True)

    pinned = resolve_app(
        "navigation.orbit.pivot", "blender",
        {"navigation.orbit.pivot": "selection"},
        {"navigation.orbit.pivot": "origin"})
    assert (pinned.value, pinned.layer, pinned.follows_global) == (
        "origin", ResolutionLayer.APP, False)


def test_global_absence_means_system_and_never_a_sentinel():
    system = resolve_global("navigation.refresh_rate", {})
    explicit = resolve_global("navigation.refresh_rate", {"navigation.refresh_rate": 60})
    assert (system.value, system.layer) == (30, ResolutionLayer.SYSTEM)
    assert (explicit.value, explicit.layer) == (60, ResolutionLayer.GLOBAL)


def test_incompatible_global_value_uses_host_system_value_but_remains_linked():
    value = resolve_app(
        "navigation.orbit.style", "godot", {"navigation.orbit.style": "free"}, {})
    assert value.value == "turntable"
    assert value.layer is ResolutionLayer.SYSTEM_APP
    assert value.follows_global


def test_every_registered_value_resolves_and_every_app_value_is_supported():
    globals_ = resolve_all_globals({})
    expected = {
        spec.setting_id for spec in SETTING_SPECS if spec.scope is not SettingScope.DEVICE
    }
    assert set(globals_) == expected
    assert all(value.value != "default" for value in globals_.values())

    for app_id in APP_IDS:
        app = APP_SPECS_BY_ID[app_id]
        resolved = resolve_all_for_app(app_id, {}, {})
        expected_ids = {spec.setting_id for spec in SETTING_SPECS if spec.applies_to(app)}
        assert set(resolved) == expected_ids
        assert all(item.value != "default" for item in resolved.values())
        assert all(item.value != 0 for setting_id, item in resolved.items()
                   if setting_id == "navigation.refresh_rate")


def test_resolver_rejects_unknown_device_and_inapplicable_requests():
    with pytest.raises(KeyError):
        resolve_global("unknown", {})
    with pytest.raises(ValueError, match="device setting"):
        resolve_global("device.name", {})
    with pytest.raises(ValueError, match="does not apply"):
        resolve_app("navigation.camera.lock_to_view", "freecad", {}, {})
    with pytest.raises(KeyError):
        resolve_app("navigation.mode", "missing", {}, {})


@pytest.mark.parametrize("global_overrides,app_overrides", [
    ({"navigation.refresh_rate": 0}, _empty_apps()),
    ({"input.mode.default": "cube"}, _empty_apps()),
    ({"navigation.orbit.style": "default"}, _empty_apps()),
    ({"device.name": "wrong layer"}, _empty_apps()),
    ({"unknown": True}, _empty_apps()),
])
def test_v9_override_validation_rejects_sentinels_legacy_names_and_wrong_owners(
        global_overrides, app_overrides):
    with pytest.raises(ValueError):
        validate_override_maps(global_overrides, app_overrides)


def test_v9_override_validation_requires_complete_sparse_app_suite():
    with pytest.raises(ValueError, match="app override suite mismatch"):
        validate_override_maps({}, {})
    maps = _empty_apps()
    maps["godot"] = {"navigation.orbit.style": "free"}
    with pytest.raises(ValueError, match="invalid app override"):
        validate_override_maps({}, maps)
    validate_override_maps({}, _empty_apps())
