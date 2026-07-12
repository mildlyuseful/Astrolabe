"""Declarative per-app binding superstructure and presentation conventions."""
from pathlib import Path

from trackball_daemon import integrations
from trackball_daemon.binding_schema import APP_BINDING_PROFILES, BINDING_SECTIONS
from trackball_daemon.config import Config
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
    for key, app in cfg.data["apps"].items():
        assert app["advanced"]["twist_action"] in APP_BINDING_PROFILES[key].twist_actions
        assert app["bindings"]["scheme"]["zoom_mode"] == "default"
        assert APP_BINDING_PROFILES[key].supports("screen_hold")


def test_rich_profiles_get_shared_zoom_mode_and_capability_specific_fields():
    for key in ("blender", "sketchup", "unreal", "unity", "godot"):
        profile = APP_BINDING_PROFILES[key]
        assert profile.rich_actions
        assert profile.zoom_targets == ("default", "to_center", "to_object", "to_cursor")
    for key in ("blender", "fusion360", "sketchup", "unreal", "unity", "godot",
                "rhino", "autocad"):
        assert APP_BINDING_PROFILES[key].zoom_behaviors == ("zoom", "dolly")


def test_capabilities_do_not_expose_controls_without_distinct_runtime_behavior():
    assert all(profile.supports("screen_hold") for profile in APP_BINDING_PROFILES.values())
    # Godot remains turntable-only even though it now has distinct projection Zoom and Dolly.
    assert APP_BINDING_PROFILES["godot"].orbit_styles == ("turntable",)
    assert APP_BINDING_PROFILES["godot"].twist_actions == ("zoom", "dolly", "none")
    assert "dolly" not in APP_BINDING_PROFILES["fusion360"].twist_actions


def test_orbit_and_zoom_dropdown_labels_follow_one_convention():
    assert [_option_label(v) for v in ("default", "free", "turntable")] == [
        "Default", "Free", "Turntable"]
    assert [_option_label(v) for v in ("default", "to_center", "to_object", "to_cursor")] == [
        "Default", "To Center", "To Object", "To Cursor"]


def test_ui_uses_only_declarative_renderer_and_general_is_scrollable():
    source = (Path(__file__).parents[1] / "trackball_daemon" / "ui.py").read_text(encoding="utf-8")
    assert source.count("def _bindings_fields(") == 1
    for obsolete in ("_blender_bindings_fields", "_sketchup_bindings_fields",
                     "_unreal_bindings_fields", "_engine_bindings_fields"):
        assert obsolete not in source
    renderer = source[source.index("def _bindings_fields"):source.index("# --- tab c:")]
    assert "BINDING_SECTIONS" in renderer
    general = source[source.index("def _build_general_tab"):source.index("def _apply_default_mode")]
    assert "body = self._scrollable(outer)" in general


def test_ui_removes_developer_copy_and_uses_tooltips():
    source = (Path(__file__).parents[1] / "trackball_daemon" / "ui.py").read_text(encoding="utf-8")
    assert "Host alignment" not in source
    assert "developer-owned" not in source
    assert "User overrides" not in source
    assert "class _ToolTip" in source
    assert "foreground=\"#888\"" not in source
