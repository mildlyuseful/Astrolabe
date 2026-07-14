"""Pure contracts for serialized runtime state and immutable publication."""
import threading

import pytest

from trackball_daemon.commands import (
    CommandBatch,
    RefreshRuntimeBase,
    SerializedCommandQueue,
    SetFocusedContext,
    SetInputMode,
    SetNavigationMode,
    SetRuntimeSetting,
    ToggleInputMode,
)
from trackball_daemon.runtime_state import FocusedContext, RuntimeBaseState, RuntimeStore


def _base(context):
    app = context.app_id or "global"
    return RuntimeBaseState(
        input_mode="pointer" if app == "desktop" else "3d",
        navigation_mode="walk" if app == "game" else "orbit",
        settings={"pointer.cursor.gain": 100.0 if app == "desktop" else 200.0},
    )


def test_initial_snapshot_is_immutable_and_contains_base_and_effective_state():
    runtime = RuntimeStore(_base)
    snapshot = runtime.snapshot()
    assert snapshot.revision == 0
    assert snapshot.base_input_mode == snapshot.effective_input_mode == "3d"
    assert snapshot.base_navigation_mode == snapshot.effective_navigation_mode == "orbit"
    assert snapshot.held_binding_ids == ()
    with pytest.raises(TypeError):
        snapshot.effective_settings["pointer.cursor.gain"] = 999


def test_context_change_re_resolves_base_without_destroying_latched_state():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(SetInputMode(origin="tray", mode="3d"))
    changed = commands.dispatch(SetFocusedContext(
        origin="focus", context=FocusedContext(app_id="desktop", executable="explorer.exe")))
    assert changed.base_input_mode == "pointer"
    assert changed.effective_input_mode == "3d"
    assert changed.base_settings["pointer.cursor.gain"] == 100.0
    assert changed.focused_context.executable == "explorer.exe"


def test_command_batch_publishes_one_coherent_snapshot():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    events = []
    runtime.add_listener(events.append)
    snapshot = commands.dispatch(CommandBatch(origin="fake-provider", commands=(
        SetNavigationMode(mode="fly"),
        SetRuntimeSetting(setting_id="pointer.cursor.gain", value=75.0),
    )))
    assert len(events) == 1
    assert events[0].snapshot is snapshot
    assert snapshot.revision == 1
    assert snapshot.effective_input_mode == "3d"
    assert snapshot.effective_navigation_mode == "fly"
    assert snapshot.effective_settings["pointer.cursor.gain"] == 75.0


def test_failed_batch_is_atomic_and_does_not_publish():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    events = []
    runtime.add_listener(events.append)
    with pytest.raises(ValueError):
        commands.dispatch(CommandBatch(commands=(
            SetInputMode(mode="pointer"),
            SetNavigationMode(mode="invalid"),
        )))
    assert runtime.snapshot().revision == 0
    assert runtime.snapshot().effective_input_mode == "3d"
    assert events == []


def test_concurrent_dispatch_is_serialized_and_revisions_are_monotonic():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    revisions = []
    runtime.add_listener(lambda event: revisions.append(event.revision))
    barrier = threading.Barrier(3)

    def update(command):
        barrier.wait(timeout=5)
        commands.dispatch(command)

    first = threading.Thread(target=update, args=(SetInputMode(mode="pointer"),))
    second = threading.Thread(target=update, args=(ToggleInputMode(),))
    first.start()
    second.start()
    barrier.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert revisions == [1, 2]
    assert runtime.snapshot().revision == 2


def test_reentrant_listener_command_is_queued_after_current_publication():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    revisions = []

    def listener(event):
        revisions.append(event.revision)
        if event.revision == 1:
            commands.dispatch(RefreshRuntimeBase(origin="listener"))

    runtime.add_listener(listener)
    commands.dispatch(SetInputMode(origin="tray", mode="pointer"))
    assert revisions == [1, 2]
    assert runtime.snapshot().revision == 2

