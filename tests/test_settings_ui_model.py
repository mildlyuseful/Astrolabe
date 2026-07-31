# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""UI-independent acceptance tests for Global and per-app settings semantics."""

from trackball_daemon.config_store import ConfigStore
from trackball_daemon.app_registry import APP_SPECS_BY_ID
from trackball_daemon.settings_schema import SETTING_SPECS, SettingScope
from trackball_daemon.settings_ui_model import SettingsUIModel
from trackball_daemon.system_defaults import SYSTEM_DEFAULTS


def _model(tmp_path):
    store = ConfigStore(tmp_path / "config.json").load()
    return store, SettingsUIModel(store)


def test_global_projection_is_exhaustive_and_distinguishes_system_defaults(tmp_path):
    store, model = _model(tmp_path)
    views = model.global_views()
    assert {view.spec.id for view in views} == {spec.id for spec in SETTING_SPECS}
    assert all(view.source_text == "System default" for view in views)

    model.set_global("pointer.cursor.gain", 2.5)
    view = next(item for item in model.global_views()
                if item.spec.id == "pointer.cursor.gain")
    assert view.value == 2.5
    assert view.source_text == "User override"
    model.reset_global(view.spec.id)
    assert store.snapshot().global_value(view.spec.id) == view.system_value


def test_device_settings_use_their_typed_override_layer(tmp_path):
    store, model = _model(tmp_path)
    model.set_global("device.name", "My Trackball")
    assert store.snapshot().device_value("device.name") == "My Trackball"
    assert "device.name" in store.snapshot().device_override_ids
    model.reset_global("device.name")
    assert "device.name" not in store.snapshot().device_override_ids


def test_reset_all_globals_preserves_device_identity(tmp_path):
    store, model = _model(tmp_path)
    model.set_global("pointer.cursor.gain", 3.0)
    model.set_global("device.name", "My Trackball")
    model.reset_all_globals()
    snapshot = store.snapshot()
    assert not snapshot.global_override_ids
    assert snapshot.device_value("device.name") == "My Trackball"


def test_link_edit_relink_and_reset_setting_transitions(tmp_path):
    store, model = _model(tmp_path)
    setting_id = "navigation.orbit.sensitivity"
    view = next(item for item in model.app_views("blender") if item.spec.id == setting_id)
    assert view.linked and view.show_reset

    model.set_global(setting_id, 4.0)
    assert next(item for item in model.app_views("blender")
                if item.spec.id == setting_id).value == 4.0
    model.set_app("blender", setting_id, 7.0)
    view = next(item for item in model.app_views("blender") if item.spec.id == setting_id)
    assert not view.linked and view.value == 7.0

    model.toggle_app_link("blender", setting_id)
    view = next(item for item in model.app_views("blender") if item.spec.id == setting_id)
    assert view.linked and view.value == 4.0

    model.reset_app_setting("blender", setting_id)
    view = next(item for item in model.app_views("blender") if item.spec.id == setting_id)
    assert not view.linked
    assert view.value == SYSTEM_DEFAULTS.app_value("blender", setting_id)


def test_unlink_materializes_effective_value_and_header_actions_are_exact(tmp_path):
    store, model = _model(tmp_path)
    model.set_global("navigation.pan.gain", 8.0)
    model.toggle_app_link("blender", "navigation.pan.gain")
    snapshot = store.snapshot()
    assert snapshot.app_value("blender", "navigation.pan.gain") == 8.0
    assert "navigation.pan.gain" in snapshot.app_override_ids["blender"]

    model.toggle_all_app_links("blender")
    assert model.all_app_settings_linked("blender")
    model.toggle_all_app_links("blender")
    app_ids = {spec.id for spec in SETTING_SPECS
               if spec.scope is SettingScope.GLOBAL_AND_APP and
               spec.applies_to(APP_SPECS_BY_ID["blender"])}
    assert store.snapshot().app_override_ids["blender"] == app_ids


def test_reset_app_pins_every_applicable_system_value(tmp_path):
    store, model = _model(tmp_path)
    model.set_global("navigation.orbit.sensitivity", 9.0)
    model.reset_app("blender")
    for view in model.app_views("blender"):
        assert not view.linked
        assert view.value == view.system_value


def test_app_projection_excludes_unsupported_settings_and_restricts_choices(tmp_path):
    _store, model = _model(tmp_path)
    freecad = {view.spec.id: view for view in model.app_views("freecad")}
    assert "navigation.fly.speed" not in freecad
    assert "navigation.mode" not in freecad
    godot = {view.spec.id: view for view in model.app_views("godot")}
    assert godot["navigation.orbit.style"].choices == ("turntable",)
    assert godot["navigation.mode"].choices == ("orbit", "fly", "walk")
    blender = {view.spec.id: view for view in model.app_views("blender")}
    assert blender["navigation.mode"].choices == ("orbit", "fly", "walk", "object")


def test_app_pivot_fallback_order_can_override_and_relink_to_global(tmp_path):
    store, model = _model(tmp_path)
    setting_id = "navigation.orbit.pivot_fallbacks"
    global_order = ("cursor_3d", "camera", "object", "origin")
    view = next(item for item in model.app_views("blender") if item.spec.id == setting_id)
    assert view.linked and view.value == global_order and view.global_compatible

    model.set_app("blender", setting_id, ["selection", "camera"])
    snapshot = store.snapshot()
    assert snapshot.app_value("blender", setting_id) == ("selection", "camera")
    assert snapshot.global_value(setting_id) == global_order

    model.toggle_app_link("blender", setting_id)
    assert store.snapshot().app_value("blender", setting_id) == global_order
    assert setting_id not in store.snapshot().app_override_ids["blender"]


def test_physical_axis_edit_remains_a_permutation(tmp_path):
    store, model = _model(tmp_path)
    before = [store.snapshot().global_value(
        f"input.axis_orientation.{axis}.source") for axis in ("x", "y", "z")]
    model.set_global("input.axis_orientation.x.source", before[1])
    after = [store.snapshot().global_value(
        f"input.axis_orientation.{axis}.source") for axis in ("x", "y", "z")]
    assert sorted(after) == [0, 1, 2]
    assert after[:2] == [before[1], before[0]]
    model.reset_global("input.axis_orientation.x.source")
    reset = [store.snapshot().global_value(
        f"input.axis_orientation.{axis}.source") for axis in ("x", "y", "z")]
    assert reset == before
