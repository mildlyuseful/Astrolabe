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
    command_id = "navigation.mode.cycle"

    def apply(self, draft, previous):
        index = NAVIGATION_MODES.index(previous.effective_navigation_mode)
        draft.set_state_latch("navigation.mode", NAVIGATION_MODES[(index + 1) % len(NAVIGATION_MODES)])
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

