"""Immutable normalized input records with no configuration-path knowledge."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
import copy
from dataclasses import dataclass, field
from enum import Enum
import math
import time
from types import MappingProxyType


def _identifier(value, field_name):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    if any(character.isspace() for character in value):
        raise ValueError(f"{field_name} cannot contain whitespace")
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(child) for child in value)
    return copy.deepcopy(value)


class InputPhase(str, Enum):
    PRESSED = "pressed"
    RELEASED = "released"
    DISCONNECTED = "disconnected"


class ProviderStatus(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    RUNNING = "running"
    SUSPENDED = "suspended"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def can_own_pressed_controls(self):
        return self in {ProviderStatus.RUNNING, ProviderStatus.DEGRADED}


@dataclass(frozen=True)
class InputControlDescriptor:
    source_id: str
    control_id: str
    label: str
    kind: str
    aliases: tuple = ()
    metadata: object = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        object.__setattr__(self, "control_id", _identifier(self.control_id, "control_id"))
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("control label must be non-empty")
        if self.kind not in {"key", "button", "axis", "switch"}:
            raise ValueError(f"unsupported input control kind: {self.kind}")
        aliases = tuple(_identifier(alias, "control alias") for alias in self.aliases)
        if self.control_id in aliases or len(set(aliases)) != len(aliases):
            raise ValueError("control aliases must be unique and exclude the control ID")
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "metadata", _freeze(dict(self.metadata)))

    @property
    def token(self):
        return f"{self.source_id}:{self.control_id}"

    @property
    def alias_tokens(self):
        return tuple(f"{self.source_id}:{alias}" for alias in self.aliases)


@dataclass(frozen=True)
class InputEvent:
    source_id: str
    control_id: str | None
    phase: InputPhase
    timestamp: float = field(default_factory=time.monotonic)
    sequence: int | None = None
    metadata: object = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        try:
            phase = InputPhase(self.phase)
        except ValueError as exc:
            raise ValueError(f"unsupported input phase: {self.phase}") from exc
        object.__setattr__(self, "phase", phase)
        if phase is InputPhase.DISCONNECTED:
            if self.control_id is not None:
                raise ValueError("a disconnected event cannot identify one control")
        else:
            object.__setattr__(
                self, "control_id", _identifier(self.control_id, "control_id"))
        timestamp = float(self.timestamp)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("input timestamp must be finite and non-negative")
        object.__setattr__(self, "timestamp", timestamp)
        if self.sequence is not None and (type(self.sequence) is not int or self.sequence < 0):
            raise ValueError("input sequence must be a non-negative int when present")
        object.__setattr__(self, "metadata", _freeze(dict(self.metadata)))

    @property
    def token(self):
        return None if self.control_id is None else f"{self.source_id}:{self.control_id}"


@dataclass(frozen=True)
class ProviderHealth:
    source_id: str
    status: ProviderStatus
    detail: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    generation: int = 0

    def __post_init__(self):
        object.__setattr__(self, "source_id", _identifier(self.source_id, "source_id"))
        object.__setattr__(self, "status", ProviderStatus(self.status))
        if not isinstance(self.detail, str):
            raise ValueError("provider health detail must be text")
        timestamp = float(self.timestamp)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("provider health timestamp must be finite and non-negative")
        object.__setattr__(self, "timestamp", timestamp)
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("provider generation must be a non-negative int")


@dataclass(frozen=True)
class InputSnapshot:
    revision: int
    pressed_tokens: tuple
    provider_health: object

    def __post_init__(self):
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("input snapshot revision must be a non-negative int")
        object.__setattr__(self, "pressed_tokens", tuple(self.pressed_tokens))
        object.__setattr__(self, "provider_health", _freeze(dict(self.provider_health)))


@dataclass(frozen=True)
class InputTransition:
    previous: InputSnapshot
    snapshot: InputSnapshot
    events: tuple
    reason: str = "event"

    def __post_init__(self):
        if self.snapshot.revision <= self.previous.revision:
            raise ValueError("input transition revision must advance")
        events = tuple(self.events)
        if any(not isinstance(event, InputEvent) for event in events):
            raise TypeError("input transition events must be InputEvent values")
        object.__setattr__(self, "events", events)


class InputProvider(ABC):
    """Lifecycle contract implemented by keyboard and future device providers."""

    @property
    @abstractmethod
    def source_id(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def controls(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def health(self):
        raise NotImplementedError

    @abstractmethod
    def configure(self, required_control_ids):
        """Apply the currently compiled controls; an empty set disables observation."""
        raise NotImplementedError

    @abstractmethod
    def reconcile(self, reason="manual"):
        raise NotImplementedError

    @abstractmethod
    def stop(self, reason="shutdown"):
        raise NotImplementedError
