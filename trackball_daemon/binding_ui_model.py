# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""UI-independent editing boundary for profile-scoped declarative keybindings."""

from dataclasses import dataclass
import copy
from collections.abc import Mapping

from .input.bindings import binding_to_row, compose_binding_profile
from .settings_schema import (
    SETTING_SPECS,
    SETTING_SPECS_BY_ID,
    SettingOperation,
    ValueKind,
)


_MODIFIERS = frozenset({
    "keyboard:ctrl", "keyboard:ctrl.left", "keyboard:ctrl.right",
    "keyboard:shift", "keyboard:shift.left", "keyboard:shift.right",
    "keyboard:alt", "keyboard:alt.left", "keyboard:alt.right",
    "keyboard:meta", "keyboard:meta.left", "keyboard:meta.right",
})


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(child) for child in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class InputCapabilityView:
    source_id: str
    required_controls: tuple
    status: str
    detail: str


@dataclass(frozen=True)
class SimpleActionView:
    target_id: str
    operation_id: str = ""
    value_text: str = ""
    advanced_only: bool = False


_COMMON_ACTIONS = (
    ("input.toggle", "Toggle Pointer / 3D",
     [{"command": "input.mode.toggle"}], []),
    ("input.hold_3d", "Hold 3D mode",
     [{"command": "state.request", "target": "input.3d"}],
     [{"command": "state.release", "target": "input.3d"}]),
    ("input.hold_pointer", "Hold Pointer mode",
     [{"command": "state.request", "target": "input.pointer"}],
     [{"command": "state.release", "target": "input.pointer"}]),
    ("navigation.hold_pan", "Hold Pan / Zoom controls",
     [{"command": "state.request", "target": "pan"}],
     [{"command": "state.release", "target": "pan"}]),
    ("navigation.hold_secondary", "Hold secondary Orbit controls",
     [{"command": "state.request", "target": "orbit.secondary"}],
     [{"command": "state.release", "target": "orbit.secondary"}]),
    ("navigation.set_orbit", "Switch navigation to Orbit",
     [{"command": "navigation.mode.set", "target": "orbit"}], []),
    ("navigation.set_fly", "Switch navigation to Fly",
     [{"command": "navigation.mode.set", "target": "fly"}], []),
    ("navigation.set_walk", "Switch navigation to Walk",
     [{"command": "navigation.mode.set", "target": "walk"}], []),
    ("navigation.set_object", "Switch navigation to Object",
     [{"command": "navigation.mode.set", "target": "object"}], []),
    ("navigation.hold_object", "Hold Object mode",
     [{"command": "state.request", "target": "navigation.object"}],
     [{"command": "state.release", "target": "navigation.object"}]),
    ("navigation.cycle", "Cycle supported navigation modes",
     [{"command": "navigation.mode.cycle"}], []),
    ("pointer.left", "Hold Left click",
     [{"command": "pointer.button.press", "target": "left"}],
     [{"command": "pointer.button.release", "target": "left"}]),
    ("pointer.right", "Hold Right click",
     [{"command": "pointer.button.press", "target": "right"}],
     [{"command": "pointer.button.release", "target": "right"}]),
    ("pointer.middle", "Hold Middle click",
     [{"command": "pointer.button.press", "target": "middle"}],
     [{"command": "pointer.button.release", "target": "middle"}]),
)
_COMMON_BY_ID = {action_id: (label, press, release)
                 for action_id, label, press, release in _COMMON_ACTIONS}
_POINTER_ACTION_IDS = frozenset({"pointer.left", "pointer.right", "pointer.middle"})


def _setting_editor_choices(spec):
    """Return canonical runtime choices without inheritance or migration sentinels."""
    excluded = {"default"}
    if spec.id == "input.mode.default":
        excluded.update({"cube", "cursor"})
    return tuple(value for value in spec.choices if value not in excluded)


class BindingUIModel:
    def __init__(self, store, catalog, aggregator=None):
        self.store = store
        self.catalog = catalog
        self.aggregator = aggregator

    @property
    def profile_id(self):
        return self.store.snapshot().input_profile

    def profiles(self):
        return tuple((profile.id, profile.label)
                     for profile in self.catalog.profiles.values())

    @staticmethod
    def simple_target_options():
        common = tuple((action_id, label) for action_id, label, _press, _release
                       in _COMMON_ACTIONS)
        settings = tuple(
            (f"setting:{spec.id}", f"Setting — {spec.ui.label}")
            for spec in SETTING_SPECS if spec.keybindable)
        return common + settings

    @staticmethod
    def recommended_match_policy(target_id, current_policy):
        """Default simple pointer actions to normal Shift/Ctrl/Alt/Meta click semantics."""
        if current_policy not in {"exact", "allow_extra_modifiers"}:
            raise ValueError(f"unknown match policy: {current_policy}")
        return ("allow_extra_modifiers"
                if target_id in _POINTER_ACTION_IDS else current_policy)

    @staticmethod
    def setting_operation_options(setting_id):
        spec = SETTING_SPECS_BY_ID[setting_id]
        options = []
        if SettingOperation.RUNTIME_SET in spec.operations:
            options.append(("hold", "Hold a value; restore it on release"))
        if SettingOperation.RUNTIME_TOGGLE in spec.operations:
            label = ("Toggle On / Off" if spec.value_kind is ValueKind.BOOLEAN
                     else "Toggle between two values")
            options.append(("toggle", label))
        if SettingOperation.RUNTIME_CYCLE in spec.operations:
            options.append(("cycle", "Cycle through selected values"))
        if SettingOperation.RUNTIME_ADD in spec.operations:
            options.append(("add", "Add an amount"))
        if SettingOperation.RUNTIME_MULTIPLY in spec.operations:
            options.append(("multiply", "Multiply by a factor"))
        return tuple(options)

    @staticmethod
    def setting_value_options(setting_id):
        spec = SETTING_SPECS_BY_ID[setting_id]
        if spec.value_kind is ValueKind.BOOLEAN:
            values = ("True", "False")
            return (("None",) + values) if spec.nullable else values
        if spec.value_kind is ValueKind.ENUM:
            return tuple(str(value) for value in _setting_editor_choices(spec))
        return ()

    @staticmethod
    def simple_action(row):
        if row.get("activation") != "hold":
            return SimpleActionView("", advanced_only=True)
        press = row.get("press", [])
        release = row.get("release", [])
        for action_id, _label, expected_press, expected_release in _COMMON_ACTIONS:
            if press == expected_press and release == expected_release:
                return SimpleActionView(action_id)
        if len(press) != 1 or not press[0].get("command", "").startswith("setting."):
            return SimpleActionView("", advanced_only=True)
        action = press[0]
        setting_id = action.get("target")
        if setting_id not in SETTING_SPECS_BY_ID:
            return SimpleActionView("", advanced_only=True)
        command_to_operation = {
            "setting.toggle_runtime": "toggle",
            "setting.cycle_runtime": "cycle",
            "setting.add_runtime": "add",
            "setting.multiply_runtime": "multiply",
        }
        if action["command"] == "setting.set_runtime":
            expected = [{"command": "setting.restore_previous", "target": setting_id}]
            if release != expected:
                return SimpleActionView("", advanced_only=True)
            operation = "hold"
        elif not release and action["command"] in command_to_operation:
            operation = command_to_operation[action["command"]]
        else:
            return SimpleActionView("", advanced_only=True)
        value = action.get("value")
        if isinstance(value, list):
            value_text = ", ".join(str(item) for item in value)
        elif value is None:
            value_text = ""
        else:
            value_text = str(value)
        return SimpleActionView(f"setting:{setting_id}", operation, value_text)

    @staticmethod
    def build_simple_action(target_id, operation_id="", value_text=""):
        if target_id in _COMMON_BY_ID:
            _label, press, release = _COMMON_BY_ID[target_id]
            return "hold", copy.deepcopy(press), copy.deepcopy(release)
        if not target_id.startswith("setting:"):
            raise ValueError("select what this keybinding controls")
        setting_id = target_id.removeprefix("setting:")
        spec = SETTING_SPECS_BY_ID[setting_id]

        def one_value(text):
            text = text.strip()
            if spec.nullable and text.casefold() == "none":
                return None
            if spec.value_kind is ValueKind.BOOLEAN:
                if text.casefold() not in {"true", "false", "on", "off"}:
                    raise ValueError("choose True or False")
                return text.casefold() in {"true", "on"}
            if spec.value_kind is ValueKind.INTEGER:
                return int(text)
            if spec.value_kind is ValueKind.NUMBER:
                return float(text)
            return text

        command_by_operation = {
            "toggle": "setting.toggle_runtime",
            "cycle": "setting.cycle_runtime",
            "add": "setting.add_runtime",
            "multiply": "setting.multiply_runtime",
        }
        if operation_id == "hold":
            value = one_value(value_text)
            press = [{"command": "setting.set_runtime", "target": setting_id, "value": value}]
            release = [{"command": "setting.restore_previous", "target": setting_id}]
        elif operation_id == "toggle":
            press = [{"command": "setting.toggle_runtime", "target": setting_id}]
            if spec.value_kind is not ValueKind.BOOLEAN:
                values = [one_value(item) for item in value_text.split(",") if item.strip()]
                if len(values) != 2:
                    raise ValueError("toggle requires two comma-separated values")
                press[0]["value"] = values
            release = []
        elif operation_id == "cycle":
            raw = value_text.strip()
            if not raw or raw.casefold() == "automatic":
                choices = _setting_editor_choices(spec)
            else:
                choices = tuple(one_value(item) for item in raw.split(",") if item.strip())
            if (len(choices) < 2 or len(set(choices)) != len(choices) or
                    not all(spec.validates(value) for value in choices)):
                raise ValueError("cycle requires at least two distinct valid values")
            press = [{"command": "setting.cycle_runtime", "target": setting_id,
                      "value": list(choices)}]
            release = []
        elif operation_id in {"add", "multiply"}:
            value = float(value_text.strip())
            press = [{"command": command_by_operation[operation_id],
                      "target": setting_id, "value": value}]
            release = []
        else:
            raise ValueError("select a setting behavior")
        return "hold", press, release

    def bindings(self, profile_id=None):
        profile_id = profile_id or self.profile_id
        overrides = _thaw(self.store.snapshot().keybinding_overrides[profile_id])
        return compose_binding_profile(self.catalog, profile_id, overrides).bindings

    def row(self, binding_id, profile_id=None):
        for binding in self.bindings(profile_id):
            if binding.id == binding_id:
                return binding_to_row(binding)
        raise KeyError(binding_id)

    def is_system_binding(self, binding_id, profile_id=None):
        profile_id = profile_id or self.profile_id
        return any(binding.id == binding_id
                   for binding in self.catalog.profile(profile_id).bindings)

    def set_profile(self, profile_id):
        return self.store.transaction().set_input_profile(profile_id).commit()

    def save(self, row, profile_id=None):
        profile_id = profile_id or self.profile_id
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("binding DSL requires a string id")
        row = copy.deepcopy(row)
        binding_id = row.pop("id")
        overrides = _thaw(self.store.snapshot().keybinding_overrides[profile_id])
        if self.is_system_binding(binding_id, profile_id):
            base = next(binding for binding in self.catalog.profile(profile_id).bindings
                        if binding.id == binding_id)
            base_row = binding_to_row(base)
            base_row.pop("id")
            patch = {key: copy.deepcopy(value) for key, value in row.items()
                     if base_row.get(key) != value}
            if "when" in base_row and "when" not in row:
                patch["when"] = None
            overrides.pop(binding_id, None)
            if patch:
                overrides[binding_id] = patch
        else:
            patch = row
            overrides[binding_id] = patch
        compose_binding_profile(self.catalog, profile_id, overrides)
        tx = self.store.transaction()
        if patch:
            tx.set_keybinding_override(profile_id, binding_id, patch)
        else:
            tx.clear_keybinding_override(profile_id, binding_id)
        return tx.commit()

    def delete(self, binding_id, profile_id=None):
        profile_id = profile_id or self.profile_id
        tx = self.store.transaction()
        if self.is_system_binding(binding_id, profile_id):
            tx.set_keybinding_override(profile_id, binding_id, {"deleted": True})
        else:
            tx.clear_keybinding_override(profile_id, binding_id)
        return tx.commit()

    def restore_system(self, binding_id, profile_id=None):
        profile_id = profile_id or self.profile_id
        return self.store.transaction().clear_keybinding_override(
            profile_id, binding_id).commit()

    def next_custom_id(self, profile_id=None):
        existing = {binding.id for binding in self.bindings(profile_id)}
        index = 1
        while f"user.binding.{index}" in existing:
            index += 1
        return f"user.binding.{index}"

    def capability_views(self, profile_id=None):
        profile_id = profile_id or self.profile_id
        required = {}
        for binding in self.bindings(profile_id):
            if not binding.enabled:
                continue
            for token in binding.chord:
                for physical in self.catalog.control_selectors[token]:
                    source, control = physical.split(":", 1)
                    required.setdefault(source, set()).add(control)
        health = {} if self.aggregator is None else self.aggregator.snapshot().provider_health
        views = []
        for source, controls in sorted(required.items()):
            item = health.get(source)
            if item is None:
                status, detail = "not present", "No active provider reported this source."
            else:
                status, detail = item.status.value, item.detail
            views.append(InputCapabilityView(
                source, tuple(sorted(controls)), status, detail))
        return tuple(views)

    @staticmethod
    def pass_through_warning(chord):
        ordinary = [token for token in chord
                    if token.startswith("keyboard:") and token not in _MODIFIERS]
        if not ordinary:
            return ""
        return ("Keyboard chords are observed passively: non-modifier keys still reach the "
                "foreground application.")
