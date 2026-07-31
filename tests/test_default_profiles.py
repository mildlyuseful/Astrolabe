# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Immutable host alignment and resettable shipped navigation profiles."""
import copy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from trackball_daemon import integrations
from trackball_daemon.app_registry import APP_BINDING_PROFILES
from trackball_daemon.config import (
    APP_PROFILE_FIELDS,
    DEFAULTS,
    DEFAULT_PROFILE_PATH,
    DEFAULT_PROFILE_KEYS,
    HOST_BASELINE_PROFILES,
    HOST_PROFILE_APP_KEYS,
    HOST_PROFILE_PATH,
    Config,
    default_app_profile,
    effective_level_horizon,
    host_baseline,
    host_baseline_payload,
    load_host_baseline_profiles,
    load_default_profiles,
)


def test_host_baselines_are_immutable_and_cover_the_supported_suite():
    app_keys = tuple(app.key for app in integrations.APPS)
    assert DEFAULT_PROFILE_KEYS == app_keys
    assert tuple(HOST_BASELINE_PROFILES) == app_keys
    with pytest.raises(TypeError):
        HOST_BASELINE_PROFILES["blender"] = host_baseline("fusion360")
    with pytest.raises(FrozenInstanceError):
        host_baseline("fusion360").zoom_scale = 99.0


def test_host_profiles_are_loaded_from_separate_packaged_raw_file():
    raw = json.loads(HOST_PROFILE_PATH.read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert tuple(raw["profiles"]) == HOST_PROFILE_APP_KEYS
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"host_profiles.json", "default_profiles.json", "system_defaults.json"' in pyproject


def test_host_alignment_has_exactly_one_owner_for_every_app():
    """Rich add-ons align after action routing; lean integrations are aligned by the daemon."""
    assert set(APP_BINDING_PROFILES) == set(HOST_BASELINE_PROFILES)
    for app_key, binding in APP_BINDING_PROFILES.items():
        assert binding.rich_actions is not HOST_BASELINE_PROFILES[app_key].apply_in_daemon, app_key


def test_object_rotation_baseline_is_opposite_camera_rotation():
    for app_key in ("blender", "unreal", "unity"):
        payload = host_baseline_payload(app_key)
        assert payload["object_rotation"] == [-value for value in payload["orbit"]]


def test_shipped_user_defaults_are_loaded_from_separate_packaged_file():
    raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert set(raw["common"]) == set(APP_PROFILE_FIELDS)
    assert tuple(raw["profiles"]) == DEFAULT_PROFILE_KEYS
    general, profiles = load_default_profiles()
    assert general["scheme"]["orbit_style"] == "free"
    assert profiles["godot"]["bindings"]["scheme"]["orbit_style"] == "turntable"
    assert profiles["blender"]["orbit_pivot_hold_sec"] == 0.5
    assert profiles["blender"]["zoom_cursor_hold_sec"] == 0.5


def test_host_profile_loader_rejects_incomplete_or_invalid_developer_data(tmp_path):
    raw = json.loads(HOST_PROFILE_PATH.read_text(encoding="utf-8"))
    raw["profiles"].pop("autocad")
    path = tmp_path / "host_profiles.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="suite mismatch"):
        load_host_baseline_profiles(path)

    raw = json.loads(HOST_PROFILE_PATH.read_text(encoding="utf-8"))
    raw["profiles"]["fusion360"]["orbit_sign"] = [0, -1, 1]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="signs must be -1 or 1"):
        load_host_baseline_profiles(path)


def test_default_profile_loader_rejects_incomplete_developer_data(tmp_path):
    raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
    raw["common"].pop("zoom_cursor_hold_sec")
    path = tmp_path / "default_profiles.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="common settings must contain every app profile field"):
        load_default_profiles(path)

    raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
    raw["profiles"].pop("onshape")
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="suite mismatch"):
        load_default_profiles(path)


def test_shipped_user_profiles_start_with_every_inversion_unchecked():
    for app_key in DEFAULT_PROFILE_KEYS:
        profile = default_app_profile(app_key)
        assert profile["rate_hz"] == 0
        assert profile["bindings"]["orbit"]["sensitivity"] == 1.0
        assert profile["bindings"]["pan"]["gain"] == 1.0
        assert profile["bindings"]["zoom"]["gain"] == 1.0
        generic = profile["bindings"]["invert"]
        assert generic == {"orbit": [False, False, False], "pan": [False, False], "zoom": False}
        advanced = profile.get("advanced", {}).get("invert", {})
        assert all(value is False for mode in advanced.values() for value in mode.values())
    for app_key in ("blender", "unreal", "unity"):
        advanced = default_app_profile(app_key)["advanced"]
        assert advanced["object_translation_frame"] == "view"
        assert tuple(advanced["axis_source"]["object"]) == (
            "pitch", "yaw", "roll", "translate_x", "translate_y", "translate_z")


def test_user_config_never_serializes_developer_host_alignment(isolated_config):
    cfg = Config().load()
    assert "host_baseline" not in cfg.path.read_text(encoding="utf-8")


def test_level_horizon_default_override_and_reset_semantics(isolated_config):
    cfg = Config().load()
    assert cfg.snapshot().global_value("navigation.level_horizon_on_entry") is True
    assert cfg.snapshot().app_value("blender", "navigation.level_horizon_on_entry") is True
    cfg.set_global("navigation.level_horizon_on_entry", False)
    assert cfg.snapshot().app_value("blender", "navigation.level_horizon_on_entry") is False
    cfg.set_app("blender", "navigation.level_horizon_on_entry", True)
    assert cfg.snapshot().app_value("blender", "navigation.level_horizon_on_entry") is True
    cfg.reset_app_profile("blender")
    assert cfg.snapshot().app_value("blender", "navigation.level_horizon_on_entry") is False


def test_reset_restores_complete_profile_once_and_preserves_operational_state(isolated_config):
    cfg = Config().load()
    operational = {
        "enabled": True,
        "installed": True,
        "addin_version": "9.9.9",
    }
    cfg.set_global("navigation.orbit.sensitivity", 1.5)
    cfg.set_app("blender", "navigation.orbit.sensitivity", 2.5)
    cfg.set_app_operational("blender", **operational)
    notifications = []
    cfg.add_listener(lambda event: notifications.append(event))

    cfg.reset_app_profile("blender")

    assert cfg.snapshot().app_value("blender", "navigation.orbit.sensitivity") == 1.5
    assert dict(cfg.snapshot().app_operational["blender"]) == operational
    assert len(notifications) == 1
    on_disk = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert on_disk["app_overrides"]["blender"] == {}


def test_reset_removes_non_profile_advanced_data_and_profiles_are_detached(isolated_config):
    first = default_app_profile("fusion360")
    first["bindings"]["orbit"]["sensitivity"] = 999
    assert default_app_profile("fusion360")["bindings"]["orbit"]["sensitivity"] != 999


def test_default_profile_source_contains_every_navigation_field(isolated_config):
    for key in DEFAULT_PROFILE_KEYS:
        profile = default_app_profile(key)
        assert set(profile) <= set(APP_PROFILE_FIELDS)
        assert "bindings" in profile and "advanced" in profile


def test_python_bootstrap_contains_only_operational_app_state():
    for key in DEFAULT_PROFILE_KEYS:
        expected = {"enabled": False, "installed": False, "addin_version": ""}
        expected.update(default_app_profile(key))
        assert DEFAULTS["apps"][key] == expected
