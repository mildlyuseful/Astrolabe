# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Declarative per-app binding superstructure and presentation conventions."""

from trackball_daemon import integrations
from trackball_daemon.app_registry import APP_BINDING_PROFILES
from trackball_daemon.config import Config
from trackball_daemon.settings_schema import BINDING_SECTIONS
from trackball_daemon.ui import _option_label


def test_schema_covers_every_app_and_has_one_global_field_order():
    assert set(APP_BINDING_PROFILES) == set(integrations.APPS_BY_KEY)
    ordered = [field for section in BINDING_SECTIONS for field in section.fields]
    assert len(ordered) == len(set(ordered))
    allowed = set(ordered)
    for key, profile in APP_BINDING_PROFILES.items():
        assert profile.key == key
        assert profile.features <= allowed
        assert {"orbit_style", "orbit_pivot", "twist_action", "zoom_target"} <= profile.features


def test_all_apps_have_twist_config_and_zoom_target_config(isolated_config):
    cfg = Config().load()
    for key, app in cfg.snapshot().app_profiles.items():
        assert app["advanced"]["twist_action"] in APP_BINDING_PROFILES[key].twist_actions
        assert app["bindings"]["scheme"]["zoom_mode"] != "default"


def test_rich_profiles_get_shared_zoom_mode_and_capability_specific_fields():
    for key in ("blender", "sketchup", "unreal", "unity"):
        profile = APP_BINDING_PROFILES[key]
        assert profile.rich_actions
        assert profile.zoom_targets == ("default", "to_center", "to_object", "to_cursor")
    for key in ("blender", "fusion360", "sketchup", "unreal", "unity",
                "rhino", "autocad"):
        assert APP_BINDING_PROFILES[key].zoom_behaviors == ("zoom", "dolly")


def test_every_advanced_control_has_a_shipped_config_value(isolated_config):
    cfg = Config().load()
    config_key_by_field = {
        "nav_mode": "nav_mode",
        "fly_speed": "fly_speed",
        "walk_speed": "walk_speed",
        "object_translation_sensitivity": "object_translation_sensitivity",
        "object_translation_frame": "object_translation_frame",
        "lock_horizon": "lock_horizon",
        "pan_scales": "pan_scales_with_distance",
        "camera_lock": "lock_camera_to_view",
        "zoom_behavior": "zoom_style",
        "dynamic_clip": "override_dynamic_clip",
        "pivot_extent": "pivot_extent_mult",
    }
    for app_key, profile in APP_BINDING_PROFILES.items():
        advanced = cfg.snapshot().app_profile(app_key)["advanced"]
        for field, config_key in config_key_by_field.items():
            if profile.supports(field):
                assert config_key in advanced, f"{app_key}.{config_key} is required by {field}"


def test_capabilities_do_not_expose_controls_without_distinct_runtime_behavior():
    assert all(profile.supports("orbit_hold") for profile in APP_BINDING_PROFILES.values())
    assert all(profile.supports("zoom_hold") for profile in APP_BINDING_PROFILES.values())
    assert "dolly" not in APP_BINDING_PROFILES["fusion360"].twist_actions


def test_orbit_and_zoom_dropdown_labels_follow_one_convention():
    assert [_option_label(v) for v in ("default", "free", "turntable")] == [
        "Default", "Free", "Turntable"]
    assert [_option_label(v) for v in ("default", "to_center", "to_object", "to_cursor")] == [
        "Default", "To Center", "To Object", "To Cursor"]
