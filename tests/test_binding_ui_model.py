# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""UI-independent keybinding editor contracts."""

from types import SimpleNamespace

import pytest

from trackball_daemon.binding_ui_model import BindingUIModel
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import load_system_binding_profiles


def _model(tmp_path, aggregator=None):
    store = ConfigStore(tmp_path / "config.json").load()
    return store, BindingUIModel(
        store, load_system_binding_profiles(), aggregator=aggregator)


def test_profile_selection_preserves_scoped_overrides(tmp_path):
    store, model = _model(tmp_path)
    row = model.row("keyboard.ctrl.3d")
    row["enabled"] = False
    model.save(row)
    assert store.snapshot().keybinding_overrides["astrolabe_5way"][
        "keyboard.ctrl.3d"] == {"enabled": False}
    model.set_profile("keyboard_only")
    assert model.profile_id == "keyboard_only"
    assert store.snapshot().keybinding_overrides["astrolabe_5way"]


def test_editor_saves_custom_binding_and_validates_before_commit(tmp_path):
    store, model = _model(tmp_path)
    row = {
        "id": model.next_custom_id(),
        "label": "F12 toggle",
        "enabled": True,
        "chord": ["keyboard:f12"],
        "match": "exact",
        "activation": "hold",
        "priority": 0,
        "press": [{"command": "input.mode.toggle"}],
        "release": [],
    }
    model.save(row)
    assert model.row(row["id"])["label"] == "F12 toggle"
    before = store.snapshot().revision
    row["press"] = [{"command": "shell.run", "target": "calc.exe"}]
    with pytest.raises(ValueError, match="not allowlisted"):
        model.save(row)
    assert store.snapshot().revision == before


def test_delete_and_restore_follow_system_vs_custom_ownership(tmp_path):
    store, model = _model(tmp_path)
    model.delete("keyboard.ctrl.3d")
    assert "keyboard.ctrl.3d" not in {binding.id for binding in model.bindings()}
    model.restore_system("keyboard.ctrl.3d")
    assert "keyboard.ctrl.3d" in {binding.id for binding in model.bindings()}


def test_custom_chord_can_toggle_setting_presets_in_an_app_context(tmp_path):
    _store, model = _model(tmp_path)
    binding_id = model.next_custom_id()
    model.save({
        "id": binding_id,
        "label": "Blender sensitivity preset",
        "enabled": True,
        "when": {"apps": ["blender"]},
        "chord": ["keyboard:ctrl", "keyboard:shift"],
        "match": "exact",
        "activation": "toggle",
        "priority": 0,
        "press": [{
            "command": "setting.toggle_runtime",
            "target": "navigation.orbit.sensitivity",
            "value": [0.5, 2.0],
        }],
        "release": [],
    })
    row = model.row(binding_id)
    assert row["when"] == {"apps": ["blender"]}
    assert row["activation"] == "toggle"
    assert row["press"][0]["value"] == [0.5, 2.0]


def test_capability_status_and_pass_through_warning(tmp_path):
    health = SimpleNamespace(status=SimpleNamespace(value="running"), detail="ready")
    aggregator = SimpleNamespace(snapshot=lambda: SimpleNamespace(
        provider_health={"keyboard": health}))
    _store, model = _model(tmp_path, aggregator)
    views = {view.source_id: view for view in model.capability_views()}
    assert views["keyboard"].status == "running"
    assert views["ble.astrolabe"].status == "not present"
    assert not model.pass_through_warning(["keyboard:ctrl", "keyboard:shift"])
    assert "foreground application" in model.pass_through_warning(["keyboard:a"])


def test_every_shipped_binding_has_a_plain_language_simple_action(tmp_path):
    _store, model = _model(tmp_path)
    for profile_id, _label in model.profiles():
        for binding in model.bindings(profile_id):
            view = model.simple_action(model.row(binding.id, profile_id))
            assert not view.advanced_only, (profile_id, binding.id)
            assert view.target_id


def test_simple_setting_builder_owns_hold_release_and_toggle_semantics(tmp_path):
    _store, model = _model(tmp_path)
    activation, press, release = model.build_simple_action(
        "setting:navigation.orbit.sensitivity", "hold", "2.5")
    assert activation == "hold"
    assert press == [{
        "command": "setting.set_runtime",
        "target": "navigation.orbit.sensitivity",
        "value": 2.5,
    }]
    assert release == [{
        "command": "setting.restore_previous",
        "target": "navigation.orbit.sensitivity",
    }]

    activation, press, release = model.build_simple_action(
        "setting:navigation.orbit.sensitivity", "toggle", "0.5, 2")
    assert activation == "hold"  # physical press edges drive the logical toggle every time
    assert press[0]["command"] == "setting.toggle_runtime"
    assert press[0]["value"] == [0.5, 2.0]
    assert release == []

    activation, press, release = model.build_simple_action(
        "setting:navigation.orbit.lock_horizon", "toggle", "Automatic")
    assert activation == "hold"
    assert press == [{
        "command": "setting.toggle_runtime",
        "target": "navigation.orbit.lock_horizon",
    }]
    assert release == []


def test_low_level_latched_activation_remains_advanced_dsl_only(tmp_path):
    _store, model = _model(tmp_path)
    row = model.row("keyboard.ctrl.3d")
    row["activation"] = "toggle"
    assert model.simple_action(row).advanced_only

    row = model.row("keyboard.ctrl.3d")
    row["press"] = [{
        "command": "setting.cycle_runtime",
        "target": "navigation.orbit.style",
        "value": ["turntable", "free"],
    }]
    row["release"] = []
    assert model.simple_action(row).advanced_only
