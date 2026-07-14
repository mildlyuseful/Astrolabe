"""UI-independent editing boundary for profile-scoped declarative keybindings."""

from dataclasses import dataclass
import copy
from collections.abc import Mapping

from .input.bindings import binding_to_row, compose_binding_profile


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
