"""Single-authority runtime state and immutable snapshot publication.

Persistent configuration answers what the base state should be.  This module owns the live state
that commands temporarily or latchedly place above that base.  Providers and UI surfaces must use
the serialized command path in :mod:`trackball_daemon.commands`; they do not mutate this store.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import copy
import logging
import threading
from types import MappingProxyType


logger = logging.getLogger("trackball_daemon.runtime_state")

INPUT_MODES = ("pointer", "3d")
NAVIGATION_MODES = ("orbit", "fly", "walk")
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

    def __post_init__(self):
        if self.input_mode not in INPUT_MODES:
            raise ValueError(f"invalid base input mode: {self.input_mode}")
        if self.navigation_mode not in NAVIGATION_MODES:
            raise ValueError(f"invalid base navigation mode: {self.navigation_mode}")
        if self.navigation_layer not in NAVIGATION_LAYERS:
            raise ValueError(f"invalid base navigation layer: {self.navigation_layer}")
        object.__setattr__(self, "settings", _freeze(dict(self.settings)))


@dataclass(frozen=True)
class BindingEvent:
    binding_id: str
    activation_id: str
    command_id: str
    source: str
    active: bool
    label: str = ""


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

    def clone(self):
        return _RuntimeDraft(
            focused_context=self.focused_context,
            latched_state=copy.deepcopy(self.latched_state),
            latched_settings=copy.deepcopy(self.latched_settings),
            last_binding_event=self.last_binding_event,
        )

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


class RuntimeStore:
    """Own live state and publish one immutable snapshot per command transaction."""

    def __init__(self, base_resolver):
        if not callable(base_resolver):
            raise TypeError("base_resolver must be callable")
        self._base_resolver = base_resolver
        self._lock = threading.RLock()
        self._listeners = []
        self._revision = 0
        self._draft = _RuntimeDraft(FocusedContext(), {}, {}, None)
        base = self._resolve_base(self._draft.focused_context)
        self._snapshot = self._build_snapshot(self._draft, base, self._revision)

    def _resolve_base(self, context):
        base = self._base_resolver(context)
        if not isinstance(base, RuntimeBaseState):
            raise TypeError("base_resolver must return RuntimeBaseState")
        return base

    @staticmethod
    def _build_snapshot(draft, base, revision):
        state = {
            "input.mode": base.input_mode,
            "navigation.mode": base.navigation_mode,
            "navigation.layer": base.navigation_layer,
        }
        state.update(draft.latched_state)
        settings = dict(base.settings)
        settings.update(draft.latched_settings)
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
            held_binding_ids=(),
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

