# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Packaged system input profiles and profile-scoped sparse override contracts."""

import copy
import json
from pathlib import Path

import pytest

from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import (
    compose_binding_profile,
    load_system_binding_profiles,
)
from trackball_daemon.validate_bindings import validate as validate_bindings_offline


PROFILE_PATH = Path("trackball_daemon/system_keybinding_profiles.json")


def _raw_profiles():
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def _binding(profile, binding_id):
    return next(item for item in profile.bindings if item.binding_id == binding_id)


def test_packaged_profiles_are_complete_immutable_and_keyboard_only_is_device_free():
    catalog = load_system_binding_profiles()
    assert tuple(catalog.profiles) == ("astrolabe_5way", "keyboard_only")
    hardware = catalog.profile("astrolabe_5way")
    keyboard = catalog.profile("keyboard_only")
    assert _binding(hardware, "astrolabe.center.toggle_mode").chord == (
        "ble.astrolabe:fiveway.center",)
    assert _binding(hardware, "xiao.left.pointer_button").release_actions[0].target == "left"
    assert all(token.startswith("keyboard:")
               for binding in keyboard.bindings for token in binding.chord)
    with pytest.raises(TypeError):
        catalog.profiles["new"] = keyboard


def test_sparse_overrides_patch_delete_and_add_without_mutating_system_data():
    catalog = load_system_binding_profiles()
    base = catalog.profile("astrolabe_5way")
    composed = compose_binding_profile(catalog, "astrolabe_5way", {
        "keyboard.ctrl.3d": {"enabled": False},
        "astrolabe.down.walk": {"deleted": True},
        "custom.f12.orbit": {
            "label": "F12 Orbit",
            "enabled": True,
            "chord": ["keyboard:f12"],
            "match": "exact",
            "activation": "hold",
            "priority": 3,
            "press": [{"command": "state.request", "target": "navigation.orbit"}],
            "release": [{"command": "state.release", "target": "navigation.orbit"}],
        },
    })
    assert not _binding(composed, "keyboard.ctrl.3d").enabled
    assert "astrolabe.down.walk" not in {item.binding_id for item in composed.bindings}
    assert _binding(composed, "custom.f12.orbit").priority == 3
    assert _binding(base, "keyboard.ctrl.3d").enabled
    assert "astrolabe.down.walk" in {item.binding_id for item in base.bindings}


@pytest.mark.parametrize("mutate, message", [
    (lambda raw: raw["profiles"][1]["bindings"].append(copy.deepcopy(
        raw["profiles"][0]["bindings"][3])), "keyboard_only"),
    (lambda raw: raw["profiles"][0]["bindings"][0]["chord"].append(
        "keyboard:ctrl.left"), "overlapping selectors"),
    (lambda raw: raw["profiles"][0]["bindings"][0]["press"].__setitem__(
        0, {"command": "python.eval", "target": "anything"}), "not allowlisted"),
    (lambda raw: raw["profiles"][0]["bindings"][8].__setitem__(
        "release", []), "paired press/release"),
])
def test_profile_loader_rejects_unsafe_or_ambiguous_data(tmp_path, mutate, message):
    raw = _raw_profiles()
    mutate(raw)
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_system_binding_profiles(path)


def test_pointer_buttons_cannot_be_persistent_toggles():
    catalog = load_system_binding_profiles()
    with pytest.raises(ValueError, match="momentary hold"):
        compose_binding_profile(catalog, "astrolabe_5way", {
            "xiao.left.pointer_button": {"activation": "toggle"},
        })


def test_config_persists_profile_scoped_overrides_and_switching_preserves_each_profile(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path).load()
    store.set_keybinding_override(
        "astrolabe_5way", "keyboard.ctrl.3d", {"enabled": False})
    store.set_keybinding_override(
        "keyboard_only", "keyboard.shift.pan", {"priority": 4})
    store.set_input_profile("keyboard_only")

    snapshot = store.snapshot()
    assert snapshot.input_profile == "keyboard_only"
    assert snapshot.keybinding_overrides["astrolabe_5way"]["keyboard.ctrl.3d"] == {
        "enabled": False}
    assert snapshot.keybinding_overrides["keyboard_only"]["keyboard.shift.pan"] == {
        "priority": 4}
    with pytest.raises(TypeError):
        snapshot.keybinding_overrides["keyboard_only"]["new"] = {}

    reloaded = ConfigStore(path).load().snapshot()
    assert reloaded.input_profile == "keyboard_only"
    assert reloaded.keybinding_overrides == snapshot.keybinding_overrides


def test_invalid_override_transaction_is_atomic_and_preserves_disk(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path).load()
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="not allowlisted"):
        store.set_keybinding_override("astrolabe_5way", "keyboard.ctrl.3d", {
            "press": [{"command": "shell.run", "target": "calc.exe"}],
        })
    assert path.read_text(encoding="utf-8") == before
    assert store.snapshot().keybinding_overrides["astrolabe_5way"] == {}


def test_system_profile_package_data_is_declared():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"system_keybinding_profiles.json"' in pyproject


def test_offline_validator_checks_both_profiles_without_starting_daemon():
    result = validate_bindings_offline()
    assert set(result) == {"astrolabe_5way", "keyboard_only"}
    assert all(not row["diagnostics"] for row in result.values())
