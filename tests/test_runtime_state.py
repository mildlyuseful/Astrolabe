"""Pure contracts for serialized runtime state and immutable publication."""
import threading

import pytest

from trackball_daemon.commands import (
    CommandBatch,
    RefreshRuntimeBase,
    ReleaseAll,
    ReleaseState,
    RequestSettingOverride,
    RequestState,
    SerializedCommandQueue,
    SetFocusedContext,
    SetInputMode,
    SetNavigationMode,
    SetRuntimeSetting,
    ToggleInputMode,
)
from trackball_daemon.runtime_state import (
    ConfigRuntimeBaseResolver,
    DependencyGraph,
    FocusedContext,
    RuntimeBaseState,
    RuntimeStore,
    StateNode,
)
from trackball_daemon.config_store import ConfigStore


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


def test_config_base_resolution_uses_the_focused_app_and_global_input_default(tmp_path):
    config = ConfigStore(tmp_path / "config.json").load()
    with config.transaction() as tx:
        tx.set_global("input.mode.default", "pointer")
        tx.set_app("blender", "navigation.mode", "fly")
        tx.set_app("blender", "navigation.orbit.sensitivity", 3.0)
    runtime = RuntimeStore(ConfigRuntimeBaseResolver(config))
    commands = SerializedCommandQueue(runtime)
    snapshot = commands.dispatch(SetFocusedContext(
        context=FocusedContext(app_id="blender", executable="blender.exe")))
    assert snapshot.base_input_mode == "pointer"
    assert snapshot.base_navigation_mode == "fly"
    assert snapshot.base_settings["navigation.orbit.sensitivity"] == 3.0
    desktop = commands.dispatch(SetFocusedContext(
        context=FocusedContext(executable="explorer.exe")))
    assert desktop.base_input_mode == "pointer"
    assert desktop.base_navigation_mode == "orbit"


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


def _request(binding_id, activation_id, target, **kwargs):
    return RequestState(
        origin="fake-provider", source="keyboard", binding_id=binding_id,
        activation_id=activation_id, target=target, **kwargs)


def _release(binding_id, activation_id, target=None):
    return ReleaseState(
        origin="fake-provider", source="keyboard", binding_id=binding_id,
        activation_id=activation_id, target=target)


def test_shift_alone_cascade_activates_pan_dependencies():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    snapshot = commands.dispatch(_request("shift-pan", "shift-1", "pan", label="Shift: Pan"))
    assert snapshot.effective_input_mode == "3d"
    assert snapshot.effective_navigation_mode == "orbit"
    assert snapshot.effective_navigation_layer == "secondary"
    assert snapshot.held_binding_ids == ("shift-pan",)
    assert snapshot.last_binding_event.label == "Shift: Pan"


@pytest.mark.parametrize("key_order", [("ctrl", "shift"), ("shift", "ctrl")])
def test_explicit_ctrl_shift_pan_is_order_independent(key_order):
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    pressed = set()
    for key in key_order:
        pressed.add(key)
        if pressed == {"ctrl", "shift"}:
            commands.dispatch(_request(
                "ctrl-shift-pan", "chord-1", "pan", chord_size=2, exact_match=True))
    snapshot = runtime.snapshot()
    assert snapshot.effective_input_mode == "3d"
    assert snapshot.effective_navigation_mode == "orbit"
    assert snapshot.effective_navigation_layer == "secondary"


def test_releasing_pan_reveals_ctrl_held_orbit_base():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(_request("ctrl-3d", "ctrl-1", "input.3d"))
    commands.dispatch(_request("shift-pan", "shift-1", "pan"))
    snapshot = commands.dispatch(_release("shift-pan", "shift-1", "pan"))
    assert snapshot.effective_input_mode == "3d"
    assert snapshot.effective_navigation_mode == "orbit"
    assert snapshot.effective_navigation_layer == "primary"
    assert snapshot.held_binding_ids == ("ctrl-3d",)


@pytest.mark.parametrize("order", [("orbit", "fly"), ("fly", "orbit")])
def test_contradictory_navigation_holds_use_most_recent_and_reveal_older(order):
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    for index, mode in enumerate(order):
        commands.dispatch(_request(mode, f"{mode}-{index}", f"navigation.{mode}"))
    assert runtime.snapshot().effective_navigation_mode == order[-1]
    commands.dispatch(_release(order[-1], f"{order[-1]}-1", f"navigation.{order[-1]}"))
    assert runtime.snapshot().effective_navigation_mode == order[0]


def test_overlapping_setting_holds_restore_only_their_identity_owned_token():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    for binding_id, value in (("slow", 50.0), ("fast", 400.0)):
        commands.dispatch(RequestSettingOverride(
            origin="fake-provider", source="keyboard", binding_id=binding_id,
            activation_id=f"{binding_id}-1", setting_id="pointer.cursor.gain", value=value))
    assert runtime.snapshot().effective_settings["pointer.cursor.gain"] == 400.0
    commands.dispatch(_release("fast", "fast-1", "setting:pointer.cursor.gain"))
    assert runtime.snapshot().effective_settings["pointer.cursor.gain"] == 50.0
    commands.dispatch(_release("slow", "slow-1", "setting:pointer.cursor.gain"))
    assert runtime.snapshot().effective_settings["pointer.cursor.gain"] == 200.0


def test_precedence_tuple_orders_priority_context_match_policy_chord_then_recency():
    # Each pair differs only at the dimension under test; the documented greater component wins.
    comparisons = (
        (dict(priority=5), dict(priority=4)),
        (dict(context_specificity=2), dict(context_specificity=1)),
        (dict(exact_match=True), dict(exact_match=False)),
        (dict(chord_size=3), dict(chord_size=2)),
    )
    for index, (winner, loser) in enumerate(comparisons):
        runtime = RuntimeStore(_base)
        commands = SerializedCommandQueue(runtime)
        common = dict(priority=0, context_specificity=0, exact_match=False, chord_size=1)
        commands.dispatch(_request(
            f"winner-{index}", f"winner-{index}", "navigation.fly", **(common | winner)))
        commands.dispatch(_request(
            f"loser-{index}", f"loser-{index}", "navigation.orbit", **(common | loser)))
        assert runtime.snapshot().effective_navigation_mode == "fly"
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(_request("older", "older-1", "navigation.fly"))
    commands.dispatch(_request("newer", "newer-1", "navigation.walk"))
    assert runtime.snapshot().effective_navigation_mode == "walk"


def test_higher_precedence_conflict_suppresses_the_entire_dependent_leaf():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(_request("pan", "pan-1", "pan", priority=0))
    snapshot = commands.dispatch(_request(
        "pointer", "pointer-1", "input.pointer", priority=1))
    assert snapshot.effective_input_mode == "pointer"
    # Pan cannot leave an unreachable secondary layer behind when its 3D prerequisite loses.
    assert snapshot.effective_navigation_layer == "primary"


def test_context_specific_request_deactivates_and_reactivates_with_focus():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(SetFocusedContext(context=FocusedContext(app_id="game")))
    commands.dispatch(_request(
        "game-fly", "game-1", "navigation.fly", context_app_id="game",
        context_specificity=1))
    assert runtime.snapshot().effective_navigation_mode == "fly"
    commands.dispatch(SetFocusedContext(context=FocusedContext(app_id="desktop")))
    assert runtime.snapshot().effective_navigation_mode == "orbit"
    assert runtime.snapshot().held_binding_ids == ()
    commands.dispatch(SetFocusedContext(context=FocusedContext(app_id="game")))
    assert runtime.snapshot().effective_navigation_mode == "fly"


def test_release_all_can_reconcile_one_provider_without_touching_another():
    runtime = RuntimeStore(_base)
    commands = SerializedCommandQueue(runtime)
    commands.dispatch(_request("keyboard-fly", "key-1", "navigation.fly"))
    commands.dispatch(RequestState(
        origin="fake-ble", source="ble", binding_id="ble-walk",
        activation_id="ble-1", target="navigation.walk"))
    commands.dispatch(ReleaseAll(origin="keyboard-disconnect", source="keyboard"))
    assert runtime.snapshot().held_binding_ids == ("ble-walk",)
    assert runtime.snapshot().effective_navigation_mode == "walk"
    commands.dispatch(ReleaseAll(origin="shutdown"))
    assert runtime.snapshot().held_binding_ids == ()
    assert runtime.snapshot().effective_navigation_mode == "orbit"


def test_dependency_cycles_and_conflicting_closures_are_rejected_at_construction():
    with pytest.raises(ValueError, match="dependency cycle"):
        DependencyGraph((StateNode("a", requires=("b",)), StateNode("b", requires=("a",))))
    with pytest.raises(ValueError, match="conflicting dependency assignments"):
        DependencyGraph((
            StateNode("left", {"input.mode": "pointer"}),
            StateNode("right", {"input.mode": "3d"}),
            StateNode("broken", requires=("left", "right")),
        ))
    with pytest.raises(ValueError, match="invalid state assignment"):
        DependencyGraph((StateNode("bad", {"input.mode": "teleport"}),))
