"""Exhaustive stable setting and allowlisted command registry contracts."""
import copy
import json

import pytest

from trackball_daemon.app_registry import APP_SPECS, APP_SPECS_BY_ID
from trackball_daemon.config import DEFAULT_PROFILE_PATH, DEFAULTS
from trackball_daemon.settings_schema import (
    APP_INTERNAL_PROFILE_PATHS,
    APP_OPERATIONAL_PATHS,
    COMMAND_SPECS,
    COMMAND_SPECS_BY_ID,
    SETTING_SPECS,
    SETTING_SPECS_BY_APP_PATH,
    SETTING_SPECS_BY_GLOBAL_PATH,
    SETTING_SPECS_BY_ID,
    UI_ONLY_CAPABILITIES,
    SettingOperation,
    SettingScope,
    V8_DEPRECATED_USER_PATHS,
    classify_app_profile_path,
    setting_choices_for_app,
    setting_specs_for_app,
    setting_value_valid_for_app,
)


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaves(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, path + (index,))
    else:
        yield path, value


def _get_path(value, path):
    for part in path:
        value = value[part]
    return value


def test_setting_and_command_ids_are_unique_immutable_registry_keys():
    assert len(SETTING_SPECS) == len(SETTING_SPECS_BY_ID)
    assert len(COMMAND_SPECS) == len(COMMAND_SPECS_BY_ID)
    assert tuple(SETTING_SPECS_BY_ID) == tuple(spec.setting_id for spec in SETTING_SPECS)
    assert tuple(COMMAND_SPECS_BY_ID) == tuple(spec.command_id for spec in COMMAND_SPECS)
    assert len(SETTING_SPECS_BY_APP_PATH) == sum(bool(spec.app_path) for spec in SETTING_SPECS)
    assert len(SETTING_SPECS_BY_GLOBAL_PATH) == sum(bool(spec.global_path) for spec in SETTING_SPECS)
    with pytest.raises(TypeError):
        SETTING_SPECS_BY_ID["new"] = SETTING_SPECS[0]


def test_every_capability_predicate_references_registry_owned_capabilities():
    known = set().union(*(spec.capabilities for spec in APP_SPECS))
    for setting in SETTING_SPECS:
        assert setting.capability.referenced_capabilities <= known, setting.setting_id
        assert setting.system_default_source.global_path or setting.system_default_source.app_path
        assert setting.ui.label and setting.ui.control


def test_every_resolved_v8_app_profile_leaf_has_a_deliberate_classification():
    raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
    classifications = set()
    for app_id, override in raw["profiles"].items():
        app = APP_SPECS_BY_ID[app_id]
        profile = _merge(raw["common"], override)
        for path, _value in _leaves(profile):
            classification = classify_app_profile_path(app, path)
            assert classification != "unknown", f"unowned path: {app_id}.{path}"
            classifications.add(classification)
        for path in APP_OPERATIONAL_PATHS:
            assert classify_app_profile_path(app, path) == "operational"
    assert classifications == {
        "user_setting", "capability_inactive", "internal_runtime_parameter"}
    assert APP_INTERNAL_PROFILE_PATHS


def test_every_applicable_setting_has_a_valid_shipped_value():
    raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
    for app_id, override in raw["profiles"].items():
        app = APP_SPECS_BY_ID[app_id]
        leaves = dict(_leaves(_merge(raw["common"], override)))
        for setting in setting_specs_for_app(app):
            assert setting.app_path in leaves, f"missing {app_id}:{setting.setting_id}"
            assert setting_value_valid_for_app(setting, app, leaves[setting.app_path]), (
                app_id, setting.setting_id, leaves[setting.app_path])


def test_every_current_global_ui_path_is_registered_or_explicitly_deprecated():
    current_paths = {
        ("device", "name"), ("device", "address"), ("bridge", "rate_hz"),
        ("general", "default_mode"), ("general", "cursor", "gain"),
        ("general", "scroll", "gain"), ("general", "scroll", "deadzone"),
        ("general", "scroll", "dominance"),
        ("general", "scheme", "orbit_pivot"), ("general", "scheme", "orbit_style"),
        ("general", "scheme", "zoom_mode"), ("general", "orbit_pivot_fallbacks"),
        ("general", "level_horizon_on_entry"),
    }
    for index in range(3):
        current_paths.add(("general", "axis_orientation", "source", index))
        current_paths.add(("general", "axis_orientation", "invert", index))
    current_paths |= V8_DEPRECATED_USER_PATHS
    assert current_paths == set(SETTING_SPECS_BY_GLOBAL_PATH) | V8_DEPRECATED_USER_PATHS
    for path, setting in SETTING_SPECS_BY_GLOBAL_PATH.items():
        assert setting.validates(_get_path(DEFAULTS, path)), (setting.setting_id, path)


def test_per_app_choices_reject_unsupported_modes_and_options():
    godot = APP_SPECS_BY_ID["godot"]
    style = SETTING_SPECS_BY_ID["navigation.orbit.style"]
    twist = SETTING_SPECS_BY_ID["navigation.orbit.twist_action"]
    mode = SETTING_SPECS_BY_ID["navigation.mode"]
    assert setting_choices_for_app(style, godot) == ("default", "turntable")
    assert not setting_value_valid_for_app(style, godot, "free")
    assert not setting_value_valid_for_app(twist, godot, "roll")
    assert setting_value_valid_for_app(mode, godot, "walk")

    fusion = APP_SPECS_BY_ID["fusion360"]
    assert not setting_value_valid_for_app(mode, fusion, "fly")


def test_every_ui_capability_is_a_setting_predicate_or_deliberate_non_setting_action():
    registered = set().union(*(setting.capability.all_of for setting in SETTING_SPECS))
    for app in APP_SPECS:
        assert app.binding_profile.features <= registered | UI_ONLY_CAPABILITIES


def test_keybindable_setting_surface_is_explicit_and_excludes_device_identity():
    sensitivity = SETTING_SPECS_BY_ID["navigation.orbit.sensitivity"]
    assert sensitivity.keybindable
    assert SettingOperation.RUNTIME_SET in sensitivity.operations
    assert SettingOperation.RUNTIME_RESTORE in sensitivity.operations
    assert SettingOperation.MACRO_PERSIST in sensitivity.operations
    assert not SETTING_SPECS_BY_ID["device.name"].keybindable
    assert not SETTING_SPECS_BY_ID["device.address"].keybindable
    assert all(setting.scope is SettingScope.GLOBAL_AND_APP
               for setting in setting_specs_for_app(APP_SPECS_BY_ID["blender"]))


def test_command_allowlist_contains_no_setup_lifecycle_or_arbitrary_execution_surface():
    expected = {
        "state.request", "state.release", "input.mode.set", "input.mode.toggle",
        "navigation.mode.set", "navigation.mode.cycle", "navigation.layer.set",
        "pointer.button.press", "pointer.button.release",
    }
    assert set(COMMAND_SPECS_BY_ID) == expected
    forbidden = {"install", "setup", "certificate", "network", "shell", "python", "eval",
                 "startup", "device.address"}
    assert all(not any(word in command.command_id for word in forbidden)
               for command in COMMAND_SPECS)
