"""Immutable binding profiles, chord compilation, and live activation ownership."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import copy
from functools import lru_cache
import importlib.resources
import json
from pathlib import Path
from types import MappingProxyType
import threading

from ..app_registry import APP_IDS, APP_SPECS_BY_ID
from ..commands import (
    ClearRuntimeOverride,
    CommandBatch,
    CycleNavigationMode,
    ReleaseAll,
    ReleaseState,
    RequestSettingOverride,
    RequestState,
    SetInputMode,
    SetNavigationLayer,
    SetNavigationMode,
    SetRuntimeSetting,
    ToggleInputMode,
)
from ..settings_schema import SETTING_SPECS_BY_ID, SettingScope
from .macros import DeclarativeAction, parse_actions
from .windows_raw_input import KEYBOARD_CONTROLS


PROFILE_SCHEMA_VERSION = 1
SYSTEM_INPUT_PROFILE_IDS = ("astrolabe_5way", "keyboard_only")
MATCH_POLICIES = ("exact", "allow_extra_modifiers")
ACTIVATION_KINDS = ("hold", "toggle")
_MODIFIER_SELECTORS = frozenset({
    "keyboard:ctrl", "keyboard:shift", "keyboard:alt", "keyboard:meta",
})
_BINDING_SOURCE = "keybinding"


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

    for phase, actions in (("press", press), ("release", release)):
        persistent = [action for action in actions
                      if action.command_id == "setting.set_persistent"]
        if persistent and len(persistent) != len(actions):
            raise ValueError(
                f"{binding_id} {phase} cannot mix persistent and runtime action domains")
    setting_requests = [action.target for action in press
                        if action.command_id == "setting.set_runtime"]
    if len(set(setting_requests)) != len(setting_requests):
        raise ValueError(f"{binding_id} has duplicate runtime setting request targets")
    restores = [action.target for action in release
                if action.command_id == "setting.restore_previous"]
    if any(target not in setting_requests for target in restores):
        raise ValueError(f"{binding_id} restore_previous requires a matching press request")


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


def binding_to_row(binding):
    """Return a detached declarative row for UI/export consumers."""
    if not isinstance(binding, BindingDefinition):
        raise TypeError("binding must be a BindingDefinition")
    return _binding_to_row(binding)


def _validate_profile(profile, selectors):
    ids = [binding.binding_id for binding in profile.bindings]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate binding ID in {profile.profile_id}")
    triggers = {}
    for binding in profile.bindings:
        if not binding.enabled:
            continue
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


@dataclass(frozen=True)
class BindingDiagnostic:
    binding_id: str
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class CompiledBinding:
    definition: BindingDefinition
    selector_sets: tuple
    physical_tokens: frozenset
    covered_modifiers: frozenset

    @property
    def id(self):
        return self.definition.binding_id


@dataclass(frozen=True)
class CompiledBindingProfile:
    profile_id: str
    bindings: tuple
    by_physical_token: object
    modifier_tokens: frozenset
    diagnostics: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "by_physical_token", MappingProxyType({
            token: tuple(values) for token, values in dict(self.by_physical_token).items()
        }))

    @property
    def required_controls(self):
        controls = {}
        for binding in self.bindings:
            for token in binding.physical_tokens:
                source, control = token.split(":", 1)
                controls.setdefault(source, set()).add(control)
        return MappingProxyType({source: frozenset(values)
                                 for source, values in controls.items()})


def _binding_compile_diagnostic(binding):
    """Return a static capability diagnostic when a binding can never be valid."""
    app_ids = binding.context.apps
    if not app_ids:
        return None
    for action in binding.press_actions + binding.release_actions:
        if (action.command_id == "navigation.mode.set" or
                (action.command_id == "state.request" and
                 action.target.startswith("navigation."))):
            target = action.target.removeprefix("navigation.")
            unsupported = [app_id for app_id in app_ids
                           if target not in APP_SPECS_BY_ID[app_id].supported_modes]
            if unsupported:
                return BindingDiagnostic(
                    binding.id, "unsupported_navigation_mode",
                    f"{target} is unsupported by: {', '.join(unsupported)}")
        if action.command_id.startswith("setting."):
            spec = SETTING_SPECS_BY_ID[action.target]
            unsupported = [app_id for app_id in app_ids
                           if (spec.scope is SettingScope.GLOBAL_AND_APP and
                               not spec.applies_to(APP_SPECS_BY_ID[app_id]))]
            if unsupported:
                return BindingDiagnostic(
                    binding.id, "unsupported_setting",
                    f"{action.target} is unavailable in: {', '.join(unsupported)}")
    return None


def compile_binding_profile(profile, catalog=None):
    """Compile immutable selectors and indexes; invalid entries are isolated as diagnostics."""
    if not isinstance(profile, SystemBindingProfile):
        raise TypeError("profile must be a SystemBindingProfile")
    catalog = catalog or load_system_binding_profiles()
    bindings = []
    diagnostics = []
    index = {}
    modifier_tokens = frozenset().union(*(
        catalog.control_selectors.get(token, ()) for token in _MODIFIER_SELECTORS))
    seen_signatures = {}
    for definition in profile.bindings:
        if not definition.enabled:
            continue
        diagnostic = _binding_compile_diagnostic(definition)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            continue
        signature = (definition.chord, definition.context.signature,
                     definition.match_policy, definition.priority)
        if signature in seen_signatures:
            diagnostics.append(BindingDiagnostic(
                definition.id, "duplicate_trigger",
                f"duplicates trigger owned by {seen_signatures[signature]}"))
            continue
        seen_signatures[signature] = definition.id
        selector_sets = tuple(catalog.control_selectors[token] for token in definition.chord)
        physical = frozenset().union(*selector_sets)
        covered = frozenset().union(*(
            selector for token, selector in zip(definition.chord, selector_sets)
            if token in _MODIFIER_SELECTORS or selector & modifier_tokens))
        compiled = CompiledBinding(definition, selector_sets, physical, covered)
        position = len(bindings)
        bindings.append(compiled)
        for token in physical:
            index.setdefault(token, []).append(position)
    return CompiledBindingProfile(
        profile.profile_id, tuple(bindings), index, modifier_tokens, tuple(diagnostics))


def compile_binding_rows(rows, profile_id, *, label="User bindings", catalog=None):
    """Lenient editor/import boundary that disables malformed rows individually.

    Persisted transactions and developer-owned profiles remain strict. This boundary exists for a
    future editor/importer to report all row-local mistakes without dropping unrelated bindings.
    """
    catalog = catalog or load_system_binding_profiles()
    catalog.profile(profile_id)
    if not isinstance(rows, list):
        raise TypeError("binding rows must be an array")
    parsed = []
    diagnostics = []
    for index, row in enumerate(rows):
        binding_id = (row.get("id") if isinstance(row, dict) else None) or f"row[{index}]"
        try:
            binding = _parse_binding(row, profile_id, catalog.control_selectors)
            if (profile_id == "keyboard_only" and
                    any(not token.startswith("keyboard:") for token in binding.chord)):
                raise ValueError("keyboard_only profile cannot contain device controls")
            parsed.append(binding)
        except (KeyError, TypeError, ValueError) as exc:
            diagnostics.append(BindingDiagnostic(
                str(binding_id), "invalid_definition", str(exc)))
    compiled = compile_binding_profile(
        SystemBindingProfile(profile_id, label, tuple(parsed)), catalog)
    return replace_compiled_diagnostics(
        compiled, tuple(diagnostics) + compiled.diagnostics)


def replace_compiled_diagnostics(profile, diagnostics):
    return CompiledBindingProfile(
        profile.profile_id, profile.bindings, profile.by_physical_token,
        profile.modifier_tokens, tuple(diagnostics))


class PointerButtonSink:
    """Phase 7 ownership seam. Phase 8 supplies the actual bounded SendInput delivery."""

    def press(self, _button, _owner):
        pass

    def release(self, _button, _owner):
        pass


@dataclass(frozen=True)
class _Activation:
    binding: CompiledBinding
    activation_id: str


def _context_matches(context, focused_context, profile_id):
    app_id = (focused_context.app_id or "").casefold()
    executable = (focused_context.executable or "").replace("/", "\\").rsplit("\\", 1)[-1]
    executable = executable.casefold()
    return ((not context.apps or app_id in context.apps) and
            (not context.executables or executable in context.executables) and
            (not context.input_profiles or profile_id in context.input_profiles))


def _is_satisfied(binding, pressed, focused_context, profile):
    if not _context_matches(binding.definition.context, focused_context, profile.profile_id):
        return False
    if not all(selector & pressed for selector in binding.selector_sets):
        return False
    if binding.definition.match_policy == "exact":
        extra_modifiers = (pressed & profile.modifier_tokens) - binding.covered_modifiers
        if extra_modifiers:
            return False
        device_sources = {token.split(":", 1)[0] for token in binding.physical_tokens
                          if not token.startswith("keyboard:")}
        if any(token.split(":", 1)[0] in device_sources and
               token not in binding.physical_tokens for token in pressed):
            return False
    return True


class BindingController:
    """Recompute the complete binding set from atomic pressed snapshots.

    The controller owns activation identities and externally held pointer resources. It never
    performs OS output itself; the sink remains inert until Phase 8 supplies a delivery adapter.
    """

    def __init__(self, compiled_profile, command_queue, runtime_store, *, config_store=None,
                 pointer_sink=None):
        if not isinstance(compiled_profile, CompiledBindingProfile):
            raise TypeError("compiled_profile must be a CompiledBindingProfile")
        self._lock = threading.RLock()
        self._profile = compiled_profile
        self._commands = command_queue
        self._runtime = runtime_store
        self._config = config_store
        self._pointer_sink = pointer_sink or PointerButtonSink()
        self._pressed = frozenset()
        self._satisfied = set()
        self._active = {}
        self._pointer_owners = {}
        self._serial = 0
        self._diagnostics = list(compiled_profile.diagnostics)

    @property
    def compiled_profile(self):
        return self._profile

    @property
    def diagnostics(self):
        with self._lock:
            return tuple(self._diagnostics)

    @property
    def active_binding_ids(self):
        with self._lock:
            return tuple(self._active)

    def _new_activation(self, binding):
        self._serial += 1
        return _Activation(binding, f"{binding.id}:{self._serial}")

    def handle_transition(self, transition):
        return self.update_pressed(transition.snapshot.pressed_tokens)

    def update_pressed(self, pressed_tokens):
        with self._lock:
            self._pressed = frozenset(pressed_tokens)
            return self._recompute_unlocked()

    def context_changed(self):
        with self._lock:
            return self._recompute_unlocked()

    def reload(self, compiled_profile):
        if not isinstance(compiled_profile, CompiledBindingProfile):
            raise TypeError("compiled_profile must be a CompiledBindingProfile")
        with self._lock:
            if self._active or self._pointer_owners:
                self._release_all_unlocked("profile_reload")
            self._profile = compiled_profile
            self._diagnostics = list(compiled_profile.diagnostics)
            self._satisfied.clear()
            return self._recompute_unlocked()

    def release_all(self, reason="release_all"):
        with self._lock:
            return self._release_all_unlocked(reason)

    def _candidate_bindings(self):
        indexes = set()
        for token in self._pressed:
            indexes.update(self._profile.by_physical_token.get(token, ()))
        indexes.update(index for index, binding in enumerate(self._profile.bindings)
                       if binding.id in self._active or binding.id in self._satisfied)
        return tuple(self._profile.bindings[index] for index in sorted(indexes))

    def _recompute_unlocked(self):
        context = self._runtime.snapshot().focused_context
        matches = [binding for binding in self._candidate_bindings()
                   if _is_satisfied(binding, self._pressed, context, self._profile)]
        # A more-specific definition for the same logical chord suppresses its fallback. Chords
        # with different controls remain concurrent so Phase 3 can resolve contradictory holds by
        # explicit priority and activation recency.
        ranks = {}
        for binding in matches:
            definition = binding.definition
            rank = (definition.priority, definition.context.specificity,
                    definition.match_policy == "exact")
            ranks[definition.chord] = max(rank, ranks.get(definition.chord, rank))
        satisfied = {
            binding.id for binding in matches
            if (binding.definition.priority, binding.definition.context.specificity,
                binding.definition.match_policy == "exact") == ranks[binding.definition.chord]
        }
        previous_satisfied = self._satisfied
        self._satisfied = satisfied
        next_active = dict(self._active)
        by_id = {binding.id: binding for binding in self._profile.bindings}
        for binding_id, activation in tuple(next_active.items()):
            definition = activation.binding.definition
            if definition.activation == "hold" and binding_id not in satisfied:
                next_active.pop(binding_id)
        for binding_id in sorted(satisfied):
            binding = by_id[binding_id]
            if binding.definition.activation == "hold":
                if binding_id not in next_active:
                    next_active[binding_id] = self._new_activation(binding)
            elif binding_id not in previous_satisfied:
                if binding_id in next_active:
                    next_active.pop(binding_id)
                else:
                    next_active[binding_id] = self._new_activation(binding)
        return self._apply_active_set_unlocked(next_active)

    def _runtime_action(self, action, activation, snapshot, working_settings, phase):
        binding = activation.binding.definition
        metadata = dict(
            origin=_BINDING_SOURCE, binding_id=binding.id,
            activation_id=activation.activation_id, source=_BINDING_SOURCE,
            priority=binding.priority,
            context_specificity=binding.context.specificity,
            exact_match=binding.match_policy == "exact", chord_size=len(binding.chord),
            context_app_id=(snapshot.focused_context.app_id if binding.context.apps else None),
            label=binding.label,
        )
        command_id = action.command_id
        if command_id == "navigation.mode.set":
            self._require_action_capability(action, snapshot)
            return (SetNavigationMode(origin=_BINDING_SOURCE, mode=action.target),)
        if command_id == "navigation.mode.cycle":
            self._require_action_capability(action, snapshot)
            app_id = snapshot.focused_context.app_id
            modes = APP_SPECS_BY_ID[app_id].supported_modes if app_id else ("orbit", "fly", "walk")
            return (CycleNavigationMode(origin=_BINDING_SOURCE, modes=modes),)
        if command_id.startswith("setting."):
            self._require_action_capability(action, snapshot)
            current = working_settings.get(action.target, snapshot.effective_settings[action.target])
            if command_id == "setting.restore_previous":
                return (ReleaseState(
                    origin=_BINDING_SOURCE, binding_id=binding.id,
                    activation_id=activation.activation_id,
                    target=f"setting:{action.target}", source=_BINDING_SOURCE,
                    label=binding.label),)
            if command_id == "setting.set_runtime":
                working_settings[action.target] = action.value
                if phase == "press":
                    return (RequestSettingOverride(
                        setting_id=action.target, value=action.value, **metadata),)
                return (
                    ReleaseState(
                        origin=_BINDING_SOURCE, binding_id=binding.id,
                        activation_id=activation.activation_id,
                        target=f"setting:{action.target}", source=_BINDING_SOURCE,
                        label=binding.label),
                    SetRuntimeSetting(origin=_BINDING_SOURCE,
                                      setting_id=action.target, value=action.value),
                )
            if command_id == "setting.toggle_runtime":
                choices = action.value if action.has_value else (False, True)
                value = choices[1] if current == choices[0] else choices[0]
            elif command_id == "setting.cycle_runtime":
                spec = SETTING_SPECS_BY_ID[action.target]
                choices = action.value if action.has_value else spec.choices
                value = choices[(choices.index(current) + 1) % len(choices)] \
                    if current in choices else choices[0]
            elif command_id == "setting.add_runtime":
                value = current + action.value
            elif command_id == "setting.multiply_runtime":
                value = current * action.value
            else:
                return ()
            spec = SETTING_SPECS_BY_ID[action.target]
            if not spec.validates(value):
                raise ValueError(f"computed value for {action.target} is outside its valid range")
            working_settings[action.target] = value
            return (SetRuntimeSetting(origin=_BINDING_SOURCE,
                                      setting_id=action.target, value=value),)
        if command_id == "state.request":
            self._require_action_capability(action, snapshot)
        return () if command_id.startswith("pointer.") else (self._simple_runtime_action(
            command_id, action, activation, metadata),)

    @staticmethod
    def _simple_runtime_action(command_id, action, activation, metadata):
        binding = activation.binding.definition
        if command_id == "state.request":
            return RequestState(target=action.target, **metadata)
        if command_id == "state.release":
            return ReleaseState(
                origin=_BINDING_SOURCE, binding_id=binding.id,
                activation_id=activation.activation_id, target=action.target,
                source=_BINDING_SOURCE, label=binding.label)
        if command_id == "input.mode.set":
            return SetInputMode(origin=_BINDING_SOURCE, mode=action.target)
        if command_id == "input.mode.toggle":
            return ToggleInputMode(origin=_BINDING_SOURCE)
        if command_id == "navigation.mode.set":
            return SetNavigationMode(origin=_BINDING_SOURCE, mode=action.target)
        if command_id == "navigation.layer.set":
            return SetNavigationLayer(origin=_BINDING_SOURCE, layer=action.target)
        raise ValueError(f"unsupported runtime binding command: {command_id}")

    @staticmethod
    def _require_action_capability(action, snapshot):
        app_id = snapshot.focused_context.app_id
        navigation_target = None
        if action.command_id == "navigation.mode.set":
            navigation_target = action.target
        elif (action.command_id == "state.request" and
              action.target.startswith("navigation.")):
            navigation_target = action.target.removeprefix("navigation.")
        if action.command_id.startswith("navigation.") or navigation_target is not None:
            if app_id is None:
                return
            spec = APP_SPECS_BY_ID[app_id]
            if navigation_target is not None and navigation_target not in spec.supported_modes:
                raise ValueError(f"{navigation_target} navigation is unsupported by {app_id}")
        elif action.command_id.startswith("setting."):
            setting = SETTING_SPECS_BY_ID[action.target]
            if (setting.scope is SettingScope.GLOBAL_AND_APP and app_id is not None and
                    not setting.applies_to(APP_SPECS_BY_ID[app_id])):
                raise ValueError(f"{action.target} is unsupported by {app_id}")

    def _persistent_actions(self, actions, snapshot):
        if not any(action.command_id == "setting.set_persistent" for action in actions):
            return
        if self._config is None:
            raise RuntimeError("persistent binding actions require a config store")
        if any(action.command_id != "setting.set_persistent" for action in actions):
            raise ValueError("persistent actions cannot mix mutation domains in one atomic list")
        tx = self._config.transaction()
        app_id = snapshot.focused_context.app_id
        for action in actions:
            self._require_action_capability(action, snapshot)
            spec = SETTING_SPECS_BY_ID[action.target]
            if app_id is not None and spec.scope is SettingScope.GLOBAL_AND_APP:
                tx.set_app(app_id, action.target, action.value)
            else:
                tx.set_global(action.target, action.value)
        tx.commit()

    def _pointer_action(self, action, activation):
        owner = (activation.binding.id, activation.activation_id)
        if action.command_id == "pointer.button.press":
            previous = self._pointer_owners.get(action.target)
            if previous == owner:
                return
            if previous is not None:
                self._pointer_sink.release(action.target, previous)
            self._pointer_sink.press(action.target, owner)
            self._pointer_owners[action.target] = owner
        elif action.command_id == "pointer.button.release":
            if self._pointer_owners.get(action.target) == owner:
                self._pointer_sink.release(action.target, owner)
                self._pointer_owners.pop(action.target, None)

    def _apply_actions_unlocked(self, action_groups, extra_runtime=()):
        snapshot = self._runtime.snapshot()
        runtime_commands = list(extra_runtime)
        pointer_actions = []
        working_settings = dict(snapshot.effective_settings)
        for activation, actions, phase in action_groups:
            try:
                self._persistent_actions(actions, snapshot)
                for action in actions:
                    if action.command_id == "setting.set_persistent":
                        continue
                    if action.command_id.startswith("pointer.button."):
                        pointer_actions.append((action, activation))
                    else:
                        commands = self._runtime_action(
                            action, activation, snapshot, working_settings, phase)
                        runtime_commands.extend(commands)
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                self._diagnostics.append(BindingDiagnostic(
                    activation.binding.id, "action_rejected", str(exc)))
        if runtime_commands:
            self._commands.dispatch(CommandBatch(
                origin=_BINDING_SOURCE, commands=tuple(runtime_commands)))
        for action, activation in pointer_actions:
            self._pointer_action(action, activation)

    def _apply_active_set_unlocked(self, next_active):
        removed = [activation for binding_id, activation in self._active.items()
                   if binding_id not in next_active]
        added = [activation for binding_id, activation in next_active.items()
                 if binding_id not in self._active]
        if not removed and not added:
            return self._runtime.snapshot()
        groups = [(activation, activation.binding.definition.release_actions, "release")
                  for activation in removed]
        groups += [(activation, activation.binding.definition.press_actions, "press")
                   for activation in added]
        self._active = next_active
        self._apply_actions_unlocked(groups)
        return self._runtime.snapshot()

    def _release_all_unlocked(self, reason):
        groups = [(activation, activation.binding.definition.release_actions, "release")
                  for activation in self._active.values()]
        self._active = {}
        self._satisfied.clear()
        if groups:
            self._apply_actions_unlocked(
                groups, (ReleaseAll(origin=reason, source=_BINDING_SOURCE),))
        else:
            self._commands.dispatch(ReleaseAll(origin=reason, source=_BINDING_SOURCE))
        # Defensive catch-all for a malformed or replaced owner; identity checks happened above.
        for button, owner in tuple(self._pointer_owners.items()):
            self._pointer_sink.release(button, owner)
        self._pointer_owners.clear()
        return self._runtime.snapshot()
