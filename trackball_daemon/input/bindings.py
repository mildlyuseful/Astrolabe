"""Immutable system binding profiles and sparse profile-scoped composition."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import copy
from functools import lru_cache
import importlib.resources
import json
from pathlib import Path
from types import MappingProxyType

from ..app_registry import APP_IDS
from .macros import DeclarativeAction, parse_actions
from .windows_raw_input import KEYBOARD_CONTROLS


PROFILE_SCHEMA_VERSION = 1
SYSTEM_INPUT_PROFILE_IDS = ("astrolabe_5way", "keyboard_only")
MATCH_POLICIES = ("exact", "allow_extra_modifiers")
ACTIVATION_KINDS = ("hold", "toggle")


def _identifier(value, field_name):
    if (not isinstance(value, str) or not value or value != value.strip() or
            any(character.isspace() for character in value)):
        raise ValueError(f"{field_name} must be non-empty trimmed text without whitespace")
    return value


def _freeze_mapping(value):
    return MappingProxyType({key: child for key, child in value.items()})


@dataclass(frozen=True)
class BindingContext:
    apps: tuple = ()
    executables: tuple = ()
    input_profiles: tuple = ()

    @property
    def specificity(self):
        return sum(bool(value) for value in (
            self.apps, self.executables, self.input_profiles))

    @property
    def signature(self):
        return self.apps, self.executables, self.input_profiles


@dataclass(frozen=True)
class BindingDefinition:
    binding_id: str
    label: str
    enabled: bool
    context: BindingContext
    chord: tuple
    match_policy: str
    activation: str
    priority: int
    press_actions: tuple
    release_actions: tuple

    @property
    def id(self):
        return self.binding_id


@dataclass(frozen=True)
class SystemBindingProfile:
    profile_id: str
    label: str
    bindings: tuple

    @property
    def id(self):
        return self.profile_id


@dataclass(frozen=True)
class BindingProfileCatalog:
    schema_version: int
    profiles: object = field(default_factory=dict)
    control_selectors: object = field(default_factory=dict)

    def __post_init__(self):
        if self.schema_version != PROFILE_SCHEMA_VERSION:
            raise ValueError(f"unsupported system binding profile schema: {self.schema_version}")
        profiles = dict(self.profiles)
        if tuple(profiles) != SYSTEM_INPUT_PROFILE_IDS:
            raise ValueError("system input profile suite/order mismatch")
        object.__setattr__(self, "profiles", _freeze_mapping(profiles))
        object.__setattr__(
            self, "control_selectors",
            MappingProxyType({key: frozenset(value)
                              for key, value in dict(self.control_selectors).items()}))

    def profile(self, profile_id):
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise ValueError(f"unknown input profile: {profile_id}") from exc


def _control_selectors():
    # Local import avoids making the normalized input package and device package initialize each
    # other merely to expose their public model names.
    from ..devices import builtin_device_descriptors

    selectors = {}
    for descriptor in KEYBOARD_CONTROLS:
        selectors[descriptor.token] = {descriptor.token}
        for alias in descriptor.alias_tokens:
            selectors.setdefault(alias, set()).add(descriptor.token)
    for device in builtin_device_descriptors():
        for descriptor in device.input_descriptors:
            selectors[descriptor.token] = {descriptor.token}
            for alias in descriptor.alias_tokens:
                selectors.setdefault(alias, set()).add(descriptor.token)
    return selectors


def _parse_context(value, profile_id):
    if value is None:
        return BindingContext()
    if not isinstance(value, dict) or set(value) - {"apps", "executables", "input_profiles"}:
        raise ValueError("binding when must contain only apps, executables, and input_profiles")

    def values(key):
        rows = value.get(key, [])
        if (not isinstance(rows, list) or not all(isinstance(row, str) and row.strip()
                                                 for row in rows)):
            raise ValueError(f"binding when.{key} must be an array of non-empty strings")
        normalized = tuple(sorted({row.strip().casefold() for row in rows}))
        if len(normalized) != len(rows):
            raise ValueError(f"binding when.{key} contains duplicates")
        return normalized

    apps = values("apps")
    executables = values("executables")
    profiles = values("input_profiles")
    unknown_apps = set(apps) - set(APP_IDS)
    unknown_profiles = set(profiles) - set(SYSTEM_INPUT_PROFILE_IDS)
    if unknown_apps:
        raise ValueError(f"binding context has unknown apps: {sorted(unknown_apps)}")
    if unknown_profiles:
        raise ValueError(
            f"binding context has unknown input profiles: {sorted(unknown_profiles)}")
    if profiles and profile_id not in profiles:
        raise ValueError(f"binding in {profile_id} can never match its input-profile context")
    return BindingContext(apps, executables, profiles)


def _validate_paired_actions(binding_id, activation, press, release):
    pressed_buttons = [action.target for action in press
                       if action.command_id == "pointer.button.press"]
    released_buttons = [action.target for action in release
                        if action.command_id == "pointer.button.release"]
    misplaced = [action for action in release if action.command_id == "pointer.button.press"]
    misplaced += [action for action in press if action.command_id == "pointer.button.release"]
    if misplaced or pressed_buttons != released_buttons:
        raise ValueError(f"{binding_id} pointer buttons require paired press/release actions")
    if pressed_buttons and activation != "hold":
        raise ValueError(f"{binding_id} pointer buttons are momentary hold actions only")

    requests = [action.target for action in press if action.command_id == "state.request"]
    releases = [action.target for action in release if action.command_id == "state.release"]
    if requests != releases:
        raise ValueError(f"{binding_id} state requests require paired release actions")


def _parse_binding(row, profile_id, selectors):
    if not isinstance(row, dict):
        raise ValueError("binding entries must be objects")
    expected = {
        "id", "label", "enabled", "when", "chord", "match", "activation", "priority",
        "press", "release",
    }
    required = expected - {"when"}
    unknown = set(row) - expected
    missing = required - set(row)
    if missing or unknown:
        raise ValueError(
            f"binding fields invalid; missing={sorted(missing)}, unknown={sorted(unknown)}")
    binding_id = _identifier(row["id"], "binding ID")
    label = row["label"]
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"{binding_id} label must be non-empty text")
    if type(row["enabled"]) is not bool:
        raise ValueError(f"{binding_id} enabled must be boolean")
    chord = row["chord"]
    if not isinstance(chord, list) or not chord:
        raise ValueError(f"{binding_id} chord must be a non-empty array")
    chord = tuple(sorted(_identifier(token, "binding chord token") for token in chord))
    if len(set(chord)) != len(chord):
        raise ValueError(f"{binding_id} chord contains duplicate controls")
    unknown_tokens = set(chord) - set(selectors)
    if unknown_tokens:
        raise ValueError(f"{binding_id} chord has unknown controls: {sorted(unknown_tokens)}")
    for index, token in enumerate(chord):
        for other in chord[index + 1:]:
            if selectors[token] & selectors[other]:
                raise ValueError(
                    f"{binding_id} chord has overlapping selectors: {token}, {other}")
    match = row["match"]
    activation = row["activation"]
    if match not in MATCH_POLICIES:
        raise ValueError(f"{binding_id} has invalid match policy: {match!r}")
    if activation not in ACTIVATION_KINDS:
        raise ValueError(f"{binding_id} has invalid activation: {activation!r}")
    priority = row["priority"]
    if type(priority) is not int or priority < 0:
        raise ValueError(f"{binding_id} priority must be a non-negative integer")
    press = parse_actions(row["press"])
    release = parse_actions(row["release"])
    if not press and not release:
        raise ValueError(f"{binding_id} must contain at least one action")
    _validate_paired_actions(binding_id, activation, press, release)
    return BindingDefinition(
        binding_id, label.strip(), row["enabled"],
        _parse_context(row.get("when"), profile_id), chord, match, activation, priority,
        press, release,
    )


def _action_to_row(action):
    row = {"command": action.command_id}
    if action.target is not None:
        row["target"] = action.target
    if action.has_value:
        row["value"] = copy.deepcopy(action.value)
    return row


def _binding_to_row(binding):
    row = {
        "id": binding.binding_id,
        "label": binding.label,
        "enabled": binding.enabled,
        "chord": list(binding.chord),
        "match": binding.match_policy,
        "activation": binding.activation,
        "priority": binding.priority,
        "press": [_action_to_row(action) for action in binding.press_actions],
        "release": [_action_to_row(action) for action in binding.release_actions],
    }
    when = {}
    if binding.context.apps:
        when["apps"] = list(binding.context.apps)
    if binding.context.executables:
        when["executables"] = list(binding.context.executables)
    if binding.context.input_profiles:
        when["input_profiles"] = list(binding.context.input_profiles)
    if when:
        row["when"] = when
    return row


def _validate_profile(profile, selectors):
    ids = [binding.binding_id for binding in profile.bindings]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate binding ID in {profile.profile_id}")
    triggers = {}
    for binding in profile.bindings:
        signature = (
            binding.chord, binding.context.signature, binding.match_policy, binding.priority)
        if signature in triggers:
            raise ValueError(
                f"duplicate binding trigger in {profile.profile_id}: "
                f"{triggers[signature]} and {binding.binding_id}")
        triggers[signature] = binding.binding_id
    if profile.profile_id == "keyboard_only":
        invalid = [token for binding in profile.bindings for token in binding.chord
                   if not token.startswith("keyboard:")]
        if invalid:
            raise ValueError("keyboard_only profile cannot contain device controls")
    return profile


def _load_catalog(data):
    if not isinstance(data, dict) or set(data) != {"schema_version", "profiles"}:
        raise ValueError("system binding profile root must contain schema_version and profiles")
    if not isinstance(data["profiles"], list):
        raise ValueError("system binding profiles must be an array")
    selectors = _control_selectors()
    profiles = {}
    for row in data["profiles"]:
        if not isinstance(row, dict) or set(row) != {"id", "label", "bindings"}:
            raise ValueError("system binding profile fields are invalid")
        profile_id = _identifier(row["id"], "input profile ID")
        if profile_id in profiles:
            raise ValueError(f"duplicate input profile: {profile_id}")
        if not isinstance(row["label"], str) or not row["label"].strip():
            raise ValueError(f"{profile_id} label must be non-empty text")
        if not isinstance(row["bindings"], list):
            raise ValueError(f"{profile_id} bindings must be an array")
        profile = SystemBindingProfile(
            profile_id, row["label"].strip(),
            tuple(_parse_binding(binding, profile_id, selectors)
                  for binding in row["bindings"]),
        )
        profiles[profile_id] = _validate_profile(profile, selectors)
    return BindingProfileCatalog(data["schema_version"], profiles, selectors)


@lru_cache(maxsize=1)
def _builtin_catalog():
    source = importlib.resources.files("trackball_daemon").joinpath(
        "system_keybinding_profiles.json")
    return _load_catalog(json.loads(source.read_text(encoding="utf-8")))


def load_system_binding_profiles(source=None):
    if source is None:
        return _builtin_catalog()
    if isinstance(source, Mapping):
        data = copy.deepcopy(dict(source))
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    return _load_catalog(data)


def compose_binding_profile(catalog, profile_id, overrides):
    """Apply one sparse binding-ID-to-field-patch map without mutating the system profile."""
    if not isinstance(catalog, BindingProfileCatalog):
        raise TypeError("catalog must be a BindingProfileCatalog")
    if not isinstance(overrides, Mapping):
        raise ValueError("keybinding profile overrides must be an object")
    base = catalog.profile(profile_id)
    base_by_id = {binding.binding_id: binding for binding in base.bindings}
    composed = []
    consumed = set()
    patch_fields = {
        "label", "enabled", "when", "chord", "match", "activation", "priority",
        "press", "release", "deleted",
    }
    for binding in base.bindings:
        patch = overrides.get(binding.binding_id)
        if patch is None:
            composed.append(binding)
            continue
        consumed.add(binding.binding_id)
        if not isinstance(patch, Mapping) or set(patch) - patch_fields:
            raise ValueError(f"invalid override patch for {binding.binding_id}")
        patch = dict(patch)
        if patch.get("deleted") is True:
            if set(patch) != {"deleted"}:
                raise ValueError(f"deleted override cannot contain fields: {binding.binding_id}")
            continue
        if "deleted" in patch:
            raise ValueError(f"deleted must be true when present: {binding.binding_id}")
        row = _binding_to_row(binding)
        row.update(copy.deepcopy(patch))
        composed.append(_parse_binding(row, profile_id, catalog.control_selectors))

    for binding_id, patch in overrides.items():
        _identifier(binding_id, "override binding ID")
        if binding_id in consumed or binding_id in base_by_id:
            continue
        if not isinstance(patch, Mapping) or "deleted" in patch:
            raise ValueError(f"custom binding must be a complete binding object: {binding_id}")
        row = copy.deepcopy(dict(patch))
        row["id"] = binding_id
        composed.append(_parse_binding(row, profile_id, catalog.control_selectors))

    return _validate_profile(
        SystemBindingProfile(base.profile_id, base.label, tuple(composed)),
        catalog.control_selectors,
    )


def validate_keybinding_override_suite(suite, catalog=None):
    catalog = catalog or load_system_binding_profiles()
    if (not isinstance(suite, Mapping) or
            set(suite) != set(SYSTEM_INPUT_PROFILE_IDS)):
        raise ValueError("invalid keybinding override suite")
    for profile_id in SYSTEM_INPUT_PROFILE_IDS:
        compose_binding_profile(catalog, profile_id, suite[profile_id])
    return True
