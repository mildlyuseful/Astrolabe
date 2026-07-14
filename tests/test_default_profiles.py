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


def test_user_config_never_serializes_developer_host_alignment(isolated_config):
    cfg = Config().load()
    assert "host_baseline" not in json.dumps(cfg.data)
    assert "host_baseline" not in cfg.path.read_text(encoding="utf-8")


def test_level_horizon_default_override_and_reset_semantics(isolated_config):
    cfg = Config().load()
    general = cfg.data["general"]
    app = cfg.data["apps"]["blender"]
    assert general["level_horizon_on_entry"] is True
    assert effective_level_horizon(general, app) is True
    general["level_horizon_on_entry"] = False
    assert effective_level_horizon(general, app) is False
    app["level_horizon_on_entry"] = True
    assert effective_level_horizon(general, app) is True
    cfg.reset_app_profile("blender")
    assert "level_horizon_on_entry" not in cfg.data["apps"]["blender"]
    assert effective_level_horizon(general, cfg.data["apps"]["blender"]) is False


def test_reset_restores_complete_profile_once_and_preserves_operational_state(isolated_config):
    cfg = Config().load()
    app = cfg.data["apps"]["blender"]
    expected = default_app_profile("blender")
    for field in APP_PROFILE_FIELDS:
        if field in app:
            app[field] = "changed" if field != "bindings" else {"changed": True}
    operational = {
        "enabled": True,
        "installed": True,
        "addin_version": "9.9.9",
    }
    app.update(operational)
    app["level_horizon_on_entry"] = False
    notifications = []
    cfg.add_listener(lambda: notifications.append("changed"))

    cfg.reset_app_profile("blender")

    assert {field: cfg.data["apps"]["blender"][field] for field in expected} == expected
    assert {field: cfg.data["apps"]["blender"][field] for field in operational} == operational
    assert "level_horizon_on_entry" not in cfg.data["apps"]["blender"]
    assert notifications == ["changed"]
    on_disk = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert {field: on_disk["apps"]["blender"][field] for field in expected} == expected


def test_reset_removes_non_profile_advanced_data_and_profiles_are_detached(isolated_config):
    cfg = Config().load()
    cfg.data["apps"]["fusion360"]["advanced"] = {"unexpected": True}
    cfg.reset_app_profile("fusion360")
    assert cfg.data["apps"]["fusion360"]["advanced"] == {
        "twist_action": "roll", "zoom_style": "zoom"}

    first = default_app_profile("fusion360")
    first["bindings"]["orbit"]["sensitivity"] = 999
    assert default_app_profile("fusion360")["bindings"]["orbit"]["sensitivity"] != 999


def test_default_profile_source_contains_every_navigation_field(isolated_config):
    cfg = Config().load()
    for key in DEFAULT_PROFILE_KEYS:
        profile = default_app_profile(key)
        expected_fields = {field for field in APP_PROFILE_FIELDS
                           if field in cfg.data["apps"][key]}
        assert set(profile) == expected_fields
        assert profile == {field: copy.deepcopy(cfg.data["apps"][key][field])
                           for field in expected_fields}


def test_python_bootstrap_contains_only_operational_app_state():
    for key in DEFAULT_PROFILE_KEYS:
        expected = {"enabled": False, "installed": False, "addin_version": ""}
        expected.update(default_app_profile(key))
        assert DEFAULTS["apps"][key] == expected
