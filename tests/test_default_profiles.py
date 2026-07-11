"""Immutable host alignment and resettable shipped navigation profiles."""
import copy
from dataclasses import FrozenInstanceError
import json

import pytest

from trackball_daemon import integrations
from trackball_daemon.config import (
    APP_PROFILE_FIELDS,
    CONFIG_VERSION,
    DEFAULT_PROFILE_KEYS,
    HOST_BASELINE_PROFILES,
    Config,
    compose_advanced_with_host_baseline,
    default_app_profile,
    host_baseline,
    host_baseline_payload,
)


def _write_config(root, data):
    folder = root / "TrackballDaemon"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_host_baselines_are_immutable_and_cover_the_supported_suite():
    app_keys = tuple(app.key for app in integrations.APPS)
    assert DEFAULT_PROFILE_KEYS == app_keys
    assert tuple(HOST_BASELINE_PROFILES) == app_keys
    with pytest.raises(TypeError):
        HOST_BASELINE_PROFILES["blender"] = host_baseline("fusion360")
    with pytest.raises(FrozenInstanceError):
        host_baseline("fusion360").zoom_scale = 99.0


def test_bundled_profile_contract_versions_cover_every_installed_addin():
    assert {key: integrations.bundled_addin_version(key) for key in integrations.ADDIN_KEYS} == {
        "fusion360": "0.1.19",
        "blender": "0.1.16",
        "freecad": "0.1.9",
        "sketchup": "0.2.7",
        "unreal": "0.2.8",
        "unity": "0.1.10",
        "godot": "0.1.8",
        "rhino": "0.1.13",
        "autocad": "0.3.10",
    }


def test_host_payloads_hold_developer_alignment_outside_user_preferences():
    assert host_baseline_payload("fusion360") == {
        "orbit": [-1.0, -1.0, 1.0],
        "pan": [-0.14, -0.14],
        "zoom": 0.25,
        "move": 1.0,
    }
    assert host_baseline_payload("blender") == {
        "orbit": [0.5, 0.5, 0.5],
        "pan": [0.5, -0.5],
        "zoom": 0.5,
        "move": 0.5,
    }
    # Unknown future hosts are safe and neutral without mutating the shipped registry.
    assert host_baseline_payload("unknown") == {
        "orbit": [1.0, 1.0, 1.0], "pan": [1.0, 1.0], "zoom": 1.0, "move": 1.0}


def test_v6_migration_preserves_effective_rich_app_inversions(isolated_config):
    old_blender = default_app_profile("blender")
    old_blender["advanced"]["invert"]["camera"]["roll"] = True
    old_blender["advanced"]["invert"]["fly"]["bank"] = False
    old_sketchup = default_app_profile("sketchup")
    old_sketchup["advanced"]["invert"]["camera"]["roll"] = False
    old_sketchup["advanced"]["invert"]["fly"]["bank"] = True
    _write_config(isolated_config, {
        "version": 5,
        "apps": {"blender": old_blender, "sketchup": old_sketchup},
    })

    cfg = Config().load()
    assert cfg.data["version"] == CONFIG_VERSION == 6
    blender_user = cfg.data["apps"]["blender"]["advanced"]
    sketchup_user = cfg.data["apps"]["sketchup"]["advanced"]
    assert blender_user["invert"]["camera"]["roll"] is False
    assert blender_user["invert"]["fly"]["bank"] is True
    assert sketchup_user["invert"]["camera"]["roll"] is True
    assert sketchup_user["invert"]["fly"]["bank"] is False
    # Baseline XOR user reproduces every pre-v6 effective direction.
    assert compose_advanced_with_host_baseline("blender", blender_user)["invert"]["camera"]["roll"] is True
    assert compose_advanced_with_host_baseline("blender", blender_user)["invert"]["fly"]["bank"] is False
    assert compose_advanced_with_host_baseline("sketchup", sketchup_user)["invert"]["camera"]["roll"] is False
    assert compose_advanced_with_host_baseline("sketchup", sketchup_user)["invert"]["fly"]["bank"] is True


def test_v6_migration_does_not_mistake_new_defaults_for_old_saved_values(isolated_config):
    _write_config(isolated_config, {"version": 5, "apps": {"blender": {}}})
    cfg = Config().load()
    user = cfg.data["apps"]["blender"]["advanced"]
    assert user["invert"]["camera"]["roll"] is False
    assert user["invert"]["fly"]["bank"] is False
    effective = compose_advanced_with_host_baseline("blender", user)
    assert effective["invert"]["camera"]["roll"] is True
    assert effective["invert"]["fly"]["bank"] is True


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
        "start_automatically": True,
        "addin_version": "9.9.9",
    }
    app.update(operational)
    notifications = []
    cfg.add_listener(lambda: notifications.append("changed"))

    cfg.reset_app_profile("blender")

    assert {field: cfg.data["apps"]["blender"][field] for field in expected} == expected
    assert {field: cfg.data["apps"]["blender"][field] for field in operational} == operational
    assert notifications == ["changed"]
    on_disk = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert {field: on_disk["apps"]["blender"][field] for field in expected} == expected


def test_reset_removes_non_profile_advanced_data_and_profiles_are_detached(isolated_config):
    cfg = Config().load()
    cfg.data["apps"]["fusion360"]["advanced"] = {"unexpected": True}
    cfg.reset_app_profile("fusion360")
    assert "advanced" not in cfg.data["apps"]["fusion360"]

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
