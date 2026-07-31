# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Single-authority runtime state and immutable snapshot publication.

Persistent configuration answers what the base state should be.  This module owns the live state
that commands temporarily or latchedly place above that base.  Providers and UI surfaces must use
the serialized command path in :mod:`trackball_daemon.commands`; they do not mutate this store.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import copy
import logging
import threading
from types import MappingProxyType

from .app_registry import APP_SPECS_BY_ID


logger = logging.getLogger("trackball_daemon.runtime_state")

INPUT_MODES = ("pointer", "3d")
NAVIGATION_MODES = ("orbit", "fly", "walk", "object")
NAVIGATION_LAYERS = ("primary", "secondary")


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return copy.deepcopy(value)


@dataclass(frozen=True)
class FocusedContext:
    """Foreground identity used for base resolution and binding-context matching."""

    app_id: str | None = None
    executable: str | None = None


@dataclass(frozen=True)
class RuntimeBaseState:
    input_mode: str
    navigation_mode: str = "orbit"
    navigation_layer: str = "primary"
    settings: object = field(default_factory=dict)
    supported_navigation_modes: tuple = NAVIGATION_MODES

    def __post_init__(self):
        if self.input_mode not in INPUT_MODES:
            raise ValueError(f"invalid base input mode: {self.input_mode}")
        if self.navigation_mode not in NAVIGATION_MODES:
            raise ValueError(f"invalid base navigation mode: {self.navigation_mode}")
        if self.navigation_layer not in NAVIGATION_LAYERS:
            raise ValueError(f"invalid base navigation layer: {self.navigation_layer}")
        modes = tuple(self.supported_navigation_modes)
        if (not modes or any(mode not in NAVIGATION_MODES for mode in modes) or
                self.navigation_mode not in modes):
            raise ValueError("base navigation mode must be included in its supported modes")
        object.__setattr__(self, "supported_navigation_modes", modes)
        object.__setattr__(self, "settings", _freeze(dict(self.settings)))


class ConfigRuntimeBaseResolver:
    """Resolve the live base from one immutable config snapshot and focused context."""

    def __init__(self, config_store):
        self._config_store = config_store

    def __call__(self, context):
        snapshot = self._config_store.snapshot()
        settings = dict(snapshot.global_values)
        app_values = snapshot.app_values.get(context.app_id) if context.app_id else None
        app_spec = APP_SPECS_BY_ID.get(context.app_id) if context.app_id else None
        supported_modes = app_spec.supported_modes if app_spec else NAVIGATION_MODES
        navigation_mode = (app_values or {}).get("navigation.mode", "orbit")
        if navigation_mode not in supported_modes:
            navigation_mode = supported_modes[0]
        if app_values is not None:
            settings.update(app_values)
        return RuntimeBaseState(
            input_mode=snapshot.global_value("input.mode.default"),
            navigation_mode=navigation_mode,
            navigation_layer="primary",
            settings=settings,
            supported_navigation_modes=supported_modes,
        )


@dataclass(frozen=True)
class BindingEvent:
    binding_id: str
    activation_id: str
    command_id: str
    source: str
    active: bool
    label: str = ""


@dataclass(frozen=True)
class HeldBinding:
    """One physically held binding exposed to passive runtime observers."""

    binding_id: str
    label: str


@dataclass(frozen=True)
class ControlHelp:
    """Semantic control descriptions independent of any particular renderer."""

    state_label: str
    primary_help: str
    secondary_help: str
    current_help: str


def _control_help(input_mode, navigation_mode, navigation_layer, settings):
    if input_mode == "pointer":
        help_text = "Ball: planar = pointer · twist = scroll"
        return ControlHelp("Pointer", help_text, help_text, help_text)

    if navigation_mode == "fly":
        primary = "Ball: planar = look · twist = bank"
        secondary = "Ball: planar = strafe / forward · twist = rise / fall"
        secondary_label = "Move"
    elif navigation_mode == "walk":
        primary = "Ball: planar = look · twist = unused"
        secondary = "Ball: planar = strafe / forward · twist = rise / fall"
        secondary_label = "Move"
    elif navigation_mode == "object":
        primary = "Ball: rotate selected objects in view axes"
        if settings.get("navigation.object.translation_frame", "view") == "ground":
            secondary = "Ball: move selection right / world up · twist = horizontal depth"
        else:
            secondary = "Ball: move selection right / up · twist = depth"
        secondary_label = "Move Selection"
    else:
        twist = str(settings.get("navigation.orbit.twist_action", "roll"))
        twist_label = {
            "roll": "roll", "zoom": "zoom", "dolly": "dolly", "none": "unused",
        }.get(twist, twist)
        zoom = str(settings.get("navigation.zoom.behavior", "zoom"))
        zoom_label = "dolly" if zoom == "dolly" else "zoom"
        primary = f"Ball: planar = orbit · twist = {twist_label}"
        secondary = f"Ball: planar = pan · twist = {zoom_label}"
        secondary_label = "Pan / Zoom"
    if navigation_layer == "secondary":
        return ControlHelp(secondary_label, primary, secondary, secondary)
    return ControlHelp(navigation_mode.title(), primary, secondary, primary)


@dataclass(frozen=True)
class StateNode:
    node_id: str
    assignments: object = field(default_factory=dict)
    requires: tuple = ()

    def __post_init__(self):
        if not self.node_id:
            raise ValueError("state node ID is required")
        object.__setattr__(self, "assignments", _freeze(dict(self.assignments)))
        object.__setattr__(self, "requires", tuple(self.requires))


class DependencyGraph:
    """Validated declarative state graph with transitive assignment closure."""

    def __init__(self, nodes):
        nodes = tuple(nodes)
        assignment_values = {
            "input.mode": INPUT_MODES,
            "navigation.mode": NAVIGATION_MODES,
            "navigation.layer": NAVIGATION_LAYERS,
        }
        self._nodes = {node.node_id: node for node in nodes}
        if len(self._nodes) != len(nodes):
            raise ValueError("duplicate state node ID")
        for node in nodes:
            for field_name, value in node.assignments.items():
                if field_name not in assignment_values or value not in assignment_values[field_name]:
                    raise ValueError(
                        f"invalid state assignment for {node.node_id}: {field_name}={value!r}")
            missing = set(node.requires) - set(self._nodes)
            if missing:
                raise ValueError(f"state node {node.node_id} has unknown dependencies: {missing}")
        self._closures = {}
        visiting = []

        def visit(node_id):
            if node_id in visiting:
                cycle = visiting[visiting.index(node_id):] + [node_id]
                raise ValueError("dependency cycle: " + " -> ".join(cycle))
            if node_id in self._closures:
                return self._closures[node_id]
            visiting.append(node_id)
            assignments = {}
            for dependency in self._nodes[node_id].requires:
                for field_name, value in visit(dependency).items():
                    if field_name in assignments and assignments[field_name] != value:
                        raise ValueError(
                            f"conflicting dependency assignments for {node_id}: {field_name}")
                    assignments[field_name] = value
            for field_name, value in self._nodes[node_id].assignments.items():
                if field_name in assignments and assignments[field_name] != value:
                    raise ValueError(f"state node {node_id} contradicts dependency {field_name}")
                assignments[field_name] = value
            visiting.pop()
            self._closures[node_id] = MappingProxyType(assignments)
            return self._closures[node_id]

        for node_id in self._nodes:
            visit(node_id)

    @property
    def node_ids(self):
        return tuple(self._nodes)

    def closure(self, node_id):
        try:
            return self._closures[node_id]
        except KeyError as exc:
            raise ValueError(f"unknown runtime state target: {node_id}") from exc


DEFAULT_DEPENDENCY_GRAPH = DependencyGraph((
    StateNode("input.pointer", {"input.mode": "pointer"}),
    StateNode("input.3d", {"input.mode": "3d"}),
    StateNode("navigation.orbit", {"navigation.mode": "orbit"}, ("input.3d",)),
    StateNode("navigation.fly", {"navigation.mode": "fly"}, ("input.3d",)),
    StateNode("navigation.walk", {"navigation.mode": "walk"}, ("input.3d",)),
    StateNode("navigation.object", {"navigation.mode": "object"}, ("input.3d",)),
    StateNode("navigation.secondary", {"navigation.layer": "secondary"}, ("input.3d",)),
    StateNode("orbit.primary", {"navigation.layer": "primary"}, ("navigation.orbit",)),
    StateNode("orbit.secondary", {"navigation.layer": "secondary"}, ("navigation.orbit",)),
    StateNode("fly.primary", {"navigation.layer": "primary"}, ("navigation.fly",)),
    StateNode("fly.secondary", {"navigation.layer": "secondary"}, ("navigation.fly",)),
    StateNode("walk.primary", {"navigation.layer": "primary"}, ("navigation.walk",)),
    StateNode("walk.secondary", {"navigation.layer": "secondary"}, ("navigation.walk",)),
    StateNode("object.primary", {"navigation.layer": "primary"}, ("navigation.object",)),
    StateNode("object.secondary", {"navigation.layer": "secondary"}, ("navigation.object",)),
    StateNode("pan", {}, ("orbit.secondary",)),
    StateNode("zoom", {}, ("orbit.secondary",)),
    StateNode("fly.move", {}, ("fly.secondary",)),
    StateNode("walk.move", {}, ("walk.secondary",)),
    StateNode("object.move", {}, ("object.secondary",)),
))


@dataclass(frozen=True)
class RequestPrecedence:
    priority: int
    context_specificity: int
    exact_match: bool
    chord_size: int
    activation_serial: int

    @property
    def key(self):
        return (
            self.priority,
            self.context_specificity,
            1 if self.exact_match else 0,
            self.chord_size,
            self.activation_serial,
        )


@dataclass(frozen=True)
class RequestIdentity:
    binding_id: str
    activation_id: str
    target: str
    source: str


@dataclass(frozen=True)
class RequestToken:
    identity: RequestIdentity
    precedence: RequestPrecedence
    context_app_id: str | None = None
    setting_id: str | None = None
    value: object = None
    label: str = ""

    @property
    def is_setting(self):
        return self.setting_id is not None

    def matches_context(self, context):
        return (self.context_app_id is None or
                self.context_app_id.casefold() == (context.app_id or "").casefold())


@dataclass(frozen=True)
class RuntimeSnapshot:
    revision: int
    focused_context: FocusedContext
    base_input_mode: str
    effective_input_mode: str
    base_navigation_mode: str
    effective_navigation_mode: str
    base_navigation_layer: str
    effective_navigation_layer: str
    held_binding_ids: tuple
    held_bindings: tuple
    control_help: ControlHelp
    latched_overrides: object
    base_settings: object
    effective_settings: object
    last_binding_event: BindingEvent | None


@dataclass(frozen=True)
class RuntimeChangeEvent:
    revision: int
    command_id: str
    origin: str
    previous: RuntimeSnapshot
    snapshot: RuntimeSnapshot


@dataclass
class _RuntimeDraft:
    focused_context: FocusedContext
    latched_state: dict
    latched_settings: dict
    last_binding_event: BindingEvent | None
    request_tokens: dict
    pressed_bindings: dict
    activation_serial: int

    def clone(self):
        return _RuntimeDraft(
            focused_context=self.focused_context,
            latched_state=copy.deepcopy(self.latched_state),
            latched_settings=copy.deepcopy(self.latched_settings),
            last_binding_event=self.last_binding_event,
            request_tokens=copy.deepcopy(self.request_tokens),
            pressed_bindings=copy.deepcopy(self.pressed_bindings),
            activation_serial=self.activation_serial,
        )

    def report_binding_activity(self, *, binding_id, activation_id, source, active, label=""):
        if not binding_id or not activation_id or not source:
            raise ValueError("binding activity identity fields must be non-empty")
        event = BindingEvent(
            binding_id, activation_id, "binding.activity", source, bool(active), label)
        if active:
            self.pressed_bindings[binding_id] = event
        else:
            self.pressed_bindings.pop(binding_id, None)
        self.last_binding_event = event

    def set_context(self, context):
        if not isinstance(context, FocusedContext):
            raise TypeError("focused context must be a FocusedContext")
        self.focused_context = context

    def set_state_latch(self, field_name, value):
        allowed = {
            "input.mode": INPUT_MODES,
            "navigation.mode": NAVIGATION_MODES,
            "navigation.layer": NAVIGATION_LAYERS,
        }
        if field_name not in allowed or value not in allowed[field_name]:
            raise ValueError(f"invalid runtime state latch: {field_name}={value!r}")
        self.latched_state[field_name] = value

    def clear_state_latch(self, field_name):
        self.latched_state.pop(field_name, None)

    def set_setting_latch(self, setting_id, value):
        self.latched_settings[setting_id] = copy.deepcopy(value)

    def clear_setting_latch(self, setting_id):
        self.latched_settings.pop(setting_id, None)

    def request(self, *, binding_id, activation_id, target, source, priority,
                context_specificity, exact_match, chord_size, context_app_id=None,
                setting_id=None, value=None, label=""):
        if not binding_id or not activation_id or not target or not source:
            raise ValueError("request identity fields must be non-empty")
        if (type(priority) is not int or type(context_specificity) is not int or
                priority < 0 or context_specificity < 0):
            raise ValueError("request priority and context specificity must be non-negative ints")
        if type(chord_size) is not int or chord_size < 1:
            raise ValueError("request chord size must be a positive int")
        identity = RequestIdentity(binding_id, activation_id, target, source)
        if identity in self.request_tokens:
            return self.request_tokens[identity]
        self.activation_serial += 1
        token = RequestToken(
            identity=identity,
            precedence=RequestPrecedence(
                priority, context_specificity, bool(exact_match), chord_size,
                self.activation_serial),
            context_app_id=context_app_id,
            setting_id=setting_id,
            value=copy.deepcopy(value),
            label=label,
        )
        self.request_tokens[identity] = token
        self.last_binding_event = BindingEvent(
            binding_id, activation_id,
            "setting.runtime.request" if setting_id is not None else "state.request",
            source, True, label)
        return token

    def release(self, *, binding_id, activation_id=None, target=None, source=None, label=""):
        matches = [
            identity for identity in self.request_tokens
            if (identity.binding_id == binding_id and
                (activation_id is None or identity.activation_id == activation_id) and
                (target is None or identity.target == target) and
                (source is None or identity.source == source))
        ]
        removed = [self.request_tokens.pop(identity) for identity in matches]
        if removed:
            last = max(removed, key=lambda token: token.precedence.activation_serial)
            self.last_binding_event = BindingEvent(
                binding_id, activation_id or last.identity.activation_id, "state.release",
                source or last.identity.source, False, label or last.label)
        return tuple(removed)

    def release_all(self, source=None):
        matches = [identity for identity in self.request_tokens
                   if source is None or identity.source == source]
        removed = [self.request_tokens.pop(identity) for identity in matches]
        if removed:
            last = max(removed, key=lambda token: token.precedence.activation_serial)
            self.last_binding_event = BindingEvent(
                last.identity.binding_id, last.identity.activation_id, "state.release_all",
                source or last.identity.source, False, last.label)
        pressed = [event for event in self.pressed_bindings.values()
                   if source is None or event.source == source]
        for event in pressed:
            self.pressed_bindings.pop(event.binding_id, None)
        if pressed and not removed:
            last = pressed[-1]
            self.last_binding_event = BindingEvent(
                last.binding_id, last.activation_id, "binding.activity",
                source or last.source, False, last.label)
        return tuple(removed)


class RuntimeStore:
    """Own live state and publish one immutable snapshot per command transaction."""

    def __init__(self, base_resolver, dependency_graph=DEFAULT_DEPENDENCY_GRAPH):
        if not callable(base_resolver):
            raise TypeError("base_resolver must be callable")
        self._base_resolver = base_resolver
        if not isinstance(dependency_graph, DependencyGraph):
            raise TypeError("dependency_graph must be a DependencyGraph")
        self._dependency_graph = dependency_graph
        self._lock = threading.RLock()
        self._listeners = []
        self._revision = 0
        self._draft = _RuntimeDraft(FocusedContext(), {}, {}, None, {}, {}, 0)
        base = self._resolve_base(self._draft.focused_context)
        self._snapshot = self._build_snapshot(self._draft, base, self._revision)

    def _resolve_base(self, context):
        base = self._base_resolver(context)
        if not isinstance(base, RuntimeBaseState):
            raise TypeError("base_resolver must return RuntimeBaseState")
        return base

    def _state_token_assignments(self, tokens):
        return {token.identity: self._dependency_graph.closure(token.identity.target)
                for token in tokens if not token.is_setting}

    @staticmethod
    def _precedence_winners(tokens, assignments):
        winners = {}
        for token in tokens:
            for field_name, value in assignments[token.identity].items():
                existing = winners.get(field_name)
                if existing is None or token.precedence.key > existing[0].precedence.key:
                    winners[field_name] = (token, value)
        return winners

    def _viable_state_winners(self, tokens):
        """Resolve fields while removing a leaf whose prerequisite lost a conflict."""
        assignments = self._state_token_assignments(tokens)
        viable = list(tokens)
        while viable:
            winners = self._precedence_winners(viable, assignments)
            remaining = [
                token for token in viable
                if all(winners[field_name][1] == value
                       for field_name, value in assignments[token.identity].items())
            ]
            if len(remaining) == len(viable):
                return winners
            viable = remaining
        return {}

    def _build_snapshot(self, draft, base, revision):
        state = {
            "input.mode": base.input_mode,
            "navigation.mode": base.navigation_mode,
            "navigation.layer": base.navigation_layer,
        }
        state.update({
            field_name: value for field_name, value in draft.latched_state.items()
            if (field_name != "navigation.mode" or
                value in base.supported_navigation_modes)
        })
        settings = dict(base.settings)
        settings.update(draft.latched_settings)
        active_tokens = [token for token in draft.request_tokens.values()
                         if token.matches_context(draft.focused_context)]
        held_bindings = list(draft.pressed_bindings.values())
        held_ids = {event.binding_id for event in held_bindings}
        held_bindings.extend(BindingEvent(
            token.identity.binding_id, token.identity.activation_id, "state.request",
            token.identity.source, True, token.label)
            for token in sorted(active_tokens,
                                key=lambda item: item.precedence.activation_serial)
            if token.identity.binding_id not in held_ids)
        state_tokens = [
            token for token in active_tokens if not token.is_setting and
            self._dependency_graph.closure(token.identity.target).get(
                "navigation.mode", base.navigation_mode) in base.supported_navigation_modes
        ]
        for field_name, (_token, value) in self._viable_state_winners(state_tokens).items():
            state[field_name] = value
        setting_winners = {}
        for token in (token for token in active_tokens if token.is_setting):
            existing = setting_winners.get(token.setting_id)
            if existing is None or token.precedence.key > existing.precedence.key:
                setting_winners[token.setting_id] = token
        for setting_id, token in setting_winners.items():
            settings[setting_id] = copy.deepcopy(token.value)
        latched = dict(draft.latched_state)
        latched.update({f"setting:{key}": value
                        for key, value in draft.latched_settings.items()})
        return RuntimeSnapshot(
            revision=revision,
            focused_context=draft.focused_context,
            base_input_mode=base.input_mode,
            effective_input_mode=state["input.mode"],
            base_navigation_mode=base.navigation_mode,
            effective_navigation_mode=state["navigation.mode"],
            base_navigation_layer=base.navigation_layer,
            effective_navigation_layer=state["navigation.layer"],
            held_binding_ids=tuple(event.binding_id for event in held_bindings),
            held_bindings=tuple(HeldBinding(
                event.binding_id, event.label or event.binding_id) for event in held_bindings),
            control_help=_control_help(
                state["input.mode"], state["navigation.mode"],
                state["navigation.layer"], settings),
            latched_overrides=_freeze(latched),
            base_settings=_freeze(dict(base.settings)),
            effective_settings=_freeze(settings),
            last_binding_event=draft.last_binding_event,
        )

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def add_listener(self, listener):
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _commit_command(self, command):
        """Apply a typed command atomically. Called only by SerializedCommandQueue."""
        with self._lock:
            previous = self._snapshot
            draft = self._draft.clone()
            command.apply(draft, previous)
            base = self._resolve_base(draft.focused_context)
            self._revision += 1
            snapshot = self._build_snapshot(draft, base, self._revision)
            self._draft = draft
            self._snapshot = snapshot
            listeners = tuple(self._listeners)
            event = RuntimeChangeEvent(
                revision=snapshot.revision,
                command_id=command.command_id,
                origin=command.origin,
                previous=previous,
                snapshot=snapshot,
            )
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Runtime listener failed for revision %s", snapshot.revision)
        return snapshot
