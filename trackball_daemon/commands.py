"""Typed runtime commands and the sole serialized dispatch path."""
from collections import deque
from dataclasses import dataclass
import threading

from .runtime_state import (
    FocusedContext,
    INPUT_MODES,
    NAVIGATION_LAYERS,
    NAVIGATION_MODES,
)
from .settings_schema import SETTING_SPECS_BY_ID


def _validate_runtime_setting(setting_id, value):
    spec = SETTING_SPECS_BY_ID.get(setting_id)
    if spec is None or not spec.keybindable:
        raise ValueError(f"setting is not runtime-keybindable: {setting_id}")
    if not spec.validates(value):
        raise ValueError(f"invalid runtime value for {setting_id}: {value!r}")


@dataclass(frozen=True)
class RuntimeCommand:
    origin: str = "unknown"
    command_id = "runtime.command"

    def apply(self, _draft, _previous):
        raise NotImplementedError


@dataclass(frozen=True)
class SetFocusedContext(RuntimeCommand):
    context: FocusedContext = FocusedContext()
    command_id = "runtime.context.set"

    def apply(self, draft, _previous):
        draft.set_context(self.context)


@dataclass(frozen=True)
class RefreshRuntimeBase(RuntimeCommand):
    command_id = "runtime.base.refresh"

    def apply(self, _draft, _previous):
        pass


@dataclass(frozen=True)
class ReportBindingActivity(RuntimeCommand):
    binding_id: str = ""
    activation_id: str = ""
    source: str = "binding"
    active: bool = True
    label: str = ""
    command_id = "binding.activity"

    def apply(self, draft, _previous):
        draft.report_binding_activity(
            binding_id=self.binding_id, activation_id=self.activation_id,
            source=self.source, active=self.active, label=self.label)


@dataclass(frozen=True)
class SetInputMode(RuntimeCommand):
    mode: str = "3d"
    command_id = "input.mode.set"

    def apply(self, draft, _previous):
        if self.mode not in INPUT_MODES:
            raise ValueError(f"invalid input mode: {self.mode}")
        draft.set_state_latch("input.mode", self.mode)


@dataclass(frozen=True)
class ToggleInputMode(RuntimeCommand):
    command_id = "input.mode.toggle"

    def apply(self, draft, previous):
        mode = "pointer" if previous.effective_input_mode == "3d" else "3d"
        draft.set_state_latch("input.mode", mode)


@dataclass(frozen=True)
class SetNavigationMode(RuntimeCommand):
    mode: str = "orbit"
    command_id = "navigation.mode.set"

    def apply(self, draft, _previous):
        if self.mode not in NAVIGATION_MODES:
            raise ValueError(f"invalid navigation mode: {self.mode}")
        draft.set_state_latch("navigation.mode", self.mode)
        draft.set_state_latch("input.mode", "3d")


@dataclass(frozen=True)
class CycleNavigationMode(RuntimeCommand):
    modes: tuple = NAVIGATION_MODES
    command_id = "navigation.mode.cycle"

    def apply(self, draft, previous):
        modes = tuple(self.modes)
        if (not modes or len(set(modes)) != len(modes) or
                any(mode not in NAVIGATION_MODES for mode in modes)):
            raise ValueError("navigation cycle modes must be distinct supported modes")
        current = previous.effective_navigation_mode
        index = modes.index(current) if current in modes else -1
        draft.set_state_latch("navigation.mode", modes[(index + 1) % len(modes)])
        draft.set_state_latch("input.mode", "3d")


@dataclass(frozen=True)
class SetNavigationLayer(RuntimeCommand):
    layer: str = "primary"
    command_id = "navigation.layer.set"

    def apply(self, draft, _previous):
        if self.layer not in NAVIGATION_LAYERS:
            raise ValueError(f"invalid navigation layer: {self.layer}")
        draft.set_state_latch("navigation.layer", self.layer)
        draft.set_state_latch("input.mode", "3d")


@dataclass(frozen=True)
class SetRuntimeSetting(RuntimeCommand):
    setting_id: str = ""
    value: object = None
    command_id = "setting.runtime.set"

    def apply(self, draft, _previous):
        if not self.setting_id:
            raise ValueError("setting_id is required")
        _validate_runtime_setting(self.setting_id, self.value)
        draft.set_setting_latch(self.setting_id, self.value)


@dataclass(frozen=True)
class ClearRuntimeOverride(RuntimeCommand):
    target: str = ""
    command_id = "runtime.override.clear"

    def apply(self, draft, _previous):
        if self.target.startswith("setting:"):
            draft.clear_setting_latch(self.target.removeprefix("setting:"))
        else:
            draft.clear_state_latch(self.target)


@dataclass(frozen=True)
class RequestState(RuntimeCommand):
    binding_id: str = ""
    activation_id: str = ""
    target: str = ""
    source: str = "binding"
    priority: int = 0
    context_specificity: int = 0
    exact_match: bool = True
    chord_size: int = 1
    context_app_id: str | None = None
    label: str = ""
    command_id = "state.request"

    def apply(self, draft, _previous):
        draft.request(
            binding_id=self.binding_id, activation_id=self.activation_id,
            target=self.target, source=self.source, priority=self.priority,
            context_specificity=self.context_specificity, exact_match=self.exact_match,
            chord_size=self.chord_size, context_app_id=self.context_app_id,
            label=self.label,
        )


@dataclass(frozen=True)
class RequestSettingOverride(RuntimeCommand):
    binding_id: str = ""
    activation_id: str = ""
    setting_id: str = ""
    value: object = None
    source: str = "binding"
    priority: int = 0
    context_specificity: int = 0
    exact_match: bool = True
    chord_size: int = 1
    context_app_id: str | None = None
    label: str = ""
    command_id = "setting.runtime.request"

    def apply(self, draft, _previous):
        if not self.setting_id:
            raise ValueError("setting_id is required")
        _validate_runtime_setting(self.setting_id, self.value)
        draft.request(
            binding_id=self.binding_id, activation_id=self.activation_id,
            target=f"setting:{self.setting_id}", source=self.source,
            priority=self.priority, context_specificity=self.context_specificity,
            exact_match=self.exact_match, chord_size=self.chord_size,
            context_app_id=self.context_app_id, setting_id=self.setting_id,
            value=self.value, label=self.label,
        )


@dataclass(frozen=True)
class ReleaseState(RuntimeCommand):
    binding_id: str = ""
    activation_id: str | None = None
    target: str | None = None
    source: str | None = None
    label: str = ""
    command_id = "state.release"

    def apply(self, draft, _previous):
        if not self.binding_id:
            raise ValueError("binding_id is required")
        draft.release(
            binding_id=self.binding_id, activation_id=self.activation_id,
            target=self.target, source=self.source, label=self.label)


@dataclass(frozen=True)
class ReleaseAll(RuntimeCommand):
    source: str | None = None
    command_id = "state.release_all"

    def apply(self, draft, _previous):
        draft.release_all(self.source)


@dataclass(frozen=True)
class CommandBatch(RuntimeCommand):
    commands: tuple = ()
    command_id = "runtime.batch"

    def apply(self, draft, previous):
        for command in self.commands:
            if not isinstance(command, RuntimeCommand) or isinstance(command, CommandBatch):
                raise TypeError("a command batch contains only non-batch RuntimeCommand values")
            command.apply(draft, previous)


class SerializedCommandQueue:
    """FIFO command boundary shared by tray, settings, and future input providers.

    Dispatch is synchronous for ordinary and concurrent callers. A listener that dispatches
    reentrantly appends work to the current drain and receives the pre-queued snapshot immediately;
    the queued command is still committed before the outer dispatch returns.
    """

    def __init__(self, runtime_store):
        self.runtime_store = runtime_store
        self._lock = threading.RLock()
        self._queue = deque()
        self._draining = False

    def dispatch(self, command):
        if not isinstance(command, RuntimeCommand):
            raise TypeError("dispatch requires a RuntimeCommand")
        envelope = [command, None]
        with self._lock:
            self._queue.append(envelope)
            if self._draining:
                return self.runtime_store.snapshot()
            self._draining = True
            try:
                while self._queue:
                    current = self._queue.popleft()
                    current[1] = self.runtime_store._commit_command(current[0])
            finally:
                self._draining = False
        return envelope[1]
