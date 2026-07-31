# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from dataclasses import replace
from itertools import permutations
from types import MappingProxyType

from trackball_daemon.commands import SerializedCommandQueue
from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import (
    BindingController,
    InputAggregator,
    InputControlDescriptor,
    InputEvent,
    PointerButtonSink,
    SystemBindingProfile,
    compile_binding_profile,
    compile_binding_rows,
    compose_binding_profile,
    load_system_binding_profiles,
)
from trackball_daemon.windows_pointer import SendInputPointerButtonSink
from trackball_daemon.runtime_state import (
    FocusedContext,
    RuntimeBaseState,
    RuntimeStore,
)


def _runtime(*, app_id=None, executable=None):
    base = RuntimeBaseState(
        "pointer", settings={
            "navigation.orbit.sensitivity": 1.0,
            "navigation.pan.gain": 1.0,
            "navigation.orbit.style": "default",
            "navigation.level_horizon_on_entry": False,
        })
    runtime = RuntimeStore(lambda _context: base)
    runtime._draft.set_context(FocusedContext(app_id, executable))
    runtime._snapshot = runtime._build_snapshot(runtime._draft, base, 0)
    return runtime


def _controller(profile_id="keyboard_only", overrides=None, **kwargs):
    catalog = load_system_binding_profiles()
    profile = compose_binding_profile(catalog, profile_id, overrides or {})
    runtime = kwargs.pop("runtime", None) or _runtime()
    controller = BindingController(
        compile_binding_profile(profile, catalog), SerializedCommandQueue(runtime), runtime,
        **kwargs)
    return controller, runtime, catalog


def _binding_row(chord, press, release=(), *, activation="hold", match="exact",
                 when=None, priority=0):
    row = {
        "label": "Custom binding", "enabled": True, "chord": list(chord),
        "match": match, "activation": activation, "priority": priority,
        "press": list(press), "release": list(release),
    }
    if when is not None:
        row["when"] = when
    return row


def test_profile_composition_accepts_deeply_immutable_config_snapshot_overrides():
    catalog = load_system_binding_profiles()
    overrides = MappingProxyType({
        "keyboard.f12.toggle_mode": MappingProxyType({
            "label": "A toggles mode",
            "chord": ("keyboard:a",),
            "press": (MappingProxyType({"command": "input.mode.toggle"}),),
            "release": (),
        }),
    })
    profile = compose_binding_profile(catalog, "keyboard_only", overrides)
    binding = next(item for item in profile.bindings
                   if item.id == "keyboard.f12.toggle_mode")
    assert binding.label == "A toggles mode"
    assert binding.chord == ("keyboard:a",)


def test_cascading_dependencies_work_in_every_ctrl_shift_order():
    for sequence in (
            (("keyboard:ctrl.left",), ("keyboard:ctrl.left", "keyboard:shift.right")),
            (("keyboard:shift.right",), ("keyboard:ctrl.left", "keyboard:shift.right"))):
        controller, runtime, _catalog = _controller()
        controller.update_pressed(sequence[0])
        controller.update_pressed(sequence[1])
        snapshot = runtime.snapshot()
        assert snapshot.effective_input_mode == "3d"
        assert snapshot.effective_navigation_mode == "orbit"
        assert snapshot.effective_navigation_layer == "secondary"
        controller.update_pressed(())
        assert runtime.snapshot().effective_input_mode == "pointer"


def test_shipped_shift_binding_preserves_object_mode_while_selecting_secondary_layer():
    runtime = RuntimeStore(lambda _context: RuntimeBaseState(
        "3d", navigation_mode="object",
        supported_navigation_modes=("orbit", "fly", "walk", "object")))
    controller, runtime, _catalog = _controller(runtime=runtime)

    controller.update_pressed(("keyboard:shift.left",))
    snapshot = runtime.snapshot()

    assert snapshot.effective_navigation_mode == "object"
    assert snapshot.effective_navigation_layer == "secondary"


def test_exact_modifier_policy_and_generic_left_right_matching():
    controller, runtime, _catalog = _controller(overrides={
        "keyboard.ctrl.3d": {"match": "exact"},
        "keyboard.shift.pan": {"enabled": False},
        "keyboard.ctrl_shift.pan": {"enabled": False},
    })
    controller.update_pressed(("keyboard:ctrl.right",))
    assert runtime.snapshot().effective_input_mode == "3d"
    controller.update_pressed(("keyboard:ctrl.right", "keyboard:shift.left"))
    assert runtime.snapshot().effective_input_mode == "pointer"


def test_exact_request_outranks_permissive_and_newer_contradiction_falls_back():
    permissive = _binding_row(
        ("keyboard:a",), ({"command": "state.request", "target": "input.3d"},),
        ({"command": "state.release", "target": "input.3d"},),
        match="allow_extra_modifiers")
    exact = _binding_row(
        ("keyboard:a",), ({"command": "state.request", "target": "input.pointer"},),
        ({"command": "state.release", "target": "input.pointer"},), match="exact")
    newer = _binding_row(
        ("keyboard:f12",), ({"command": "state.request", "target": "input.3d"},),
        ({"command": "state.release", "target": "input.3d"},), match="exact")
    controller, runtime, _catalog = _controller(overrides={
        "keyboard.f12.toggle_mode": {"enabled": False},
        "custom.permissive": permissive,
        "custom.exact": exact,
        "custom.newer": newer,
    })
    controller.update_pressed(("keyboard:a",))
    assert runtime.snapshot().effective_input_mode == "pointer"
    controller.update_pressed(("keyboard:a", "keyboard:f12"))
    assert runtime.snapshot().effective_input_mode == "3d"
    controller.update_pressed(("keyboard:a",))
    assert runtime.snapshot().effective_input_mode == "pointer"


def test_cross_provider_chord_toggle_uses_rising_edges_and_ignores_repeats():
    custom = _binding_row(
        ("keyboard:ctrl", "ble.astrolabe:fiveway.center"),
        ({"command": "input.mode.toggle"},), activation="toggle",
        match="allow_extra_modifiers")
    controller, runtime, _catalog = _controller("astrolabe_5way", {
        "keyboard.ctrl.3d": {"enabled": False},
        "keyboard.shift.pan": {"enabled": False},
        "keyboard.ctrl_shift.pan": {"enabled": False},
        "astrolabe.center.toggle_mode": {"enabled": False},
        "custom.cross.toggle": custom,
    })
    chord = ("keyboard:ctrl.left", "ble.astrolabe:fiveway.center")
    controller.update_pressed(chord)
    assert runtime.snapshot().effective_input_mode == "3d"
    revision = runtime.snapshot().revision
    controller.update_pressed(chord)
    assert runtime.snapshot().revision == revision
    controller.update_pressed(("keyboard:ctrl.left",))
    controller.update_pressed(chord)
    assert runtime.snapshot().effective_input_mode == "3d"  # release list is intentionally empty
    assert controller.active_binding_ids == ()


def test_three_control_cross_provider_chord_is_order_independent():
    custom = _binding_row(
        ("keyboard:ctrl", "keyboard:shift", "ble.astrolabe:fiveway.center"),
        ({"command": "state.request", "target": "pan"},),
        ({"command": "state.release", "target": "pan"},),
        match="allow_extra_modifiers")
    controls = (
        "keyboard:ctrl.left", "keyboard:shift.right", "ble.astrolabe:fiveway.center")
    for order in permutations(controls):
        controller, runtime, _catalog = _controller("astrolabe_5way", {
            "keyboard.ctrl.3d": {"enabled": False},
            "keyboard.shift.pan": {"enabled": False},
            "keyboard.ctrl_shift.pan": {"enabled": False},
            "astrolabe.center.toggle_mode": {"enabled": False},
            "custom.three": custom,
        })
        pressed = []
        for token in order:
            pressed.append(token)
            controller.update_pressed(pressed)
        assert runtime.snapshot().effective_navigation_layer == "secondary"
        controller.update_pressed(())
        assert runtime.snapshot().effective_input_mode == "pointer"


def test_exact_device_binding_rejects_impossible_simultaneous_fiveway_state():
    controller, runtime, _catalog = _controller("astrolabe_5way", {
        "keyboard.ctrl.3d": {"enabled": False},
        "keyboard.shift.pan": {"enabled": False},
        "keyboard.ctrl_shift.pan": {"enabled": False},
    })
    controller.update_pressed((
        "ble.astrolabe:fiveway.up", "ble.astrolabe:fiveway.right"))
    assert runtime.snapshot().effective_input_mode == "pointer"
    assert controller.active_binding_ids == ()


def test_app_and_executable_contexts_recompute_stationary_hold():
    runtime = _runtime(app_id="blender", executable="blender.exe")
    custom = _binding_row(
        ("keyboard:a",), ({"command": "state.request", "target": "input.3d"},),
        ({"command": "state.release", "target": "input.3d"},),
        when={"apps": ["blender"], "executables": ["blender.exe"]})
    controller, _runtime_store, catalog = _controller(
        overrides={"custom.context": custom}, runtime=runtime)
    controller.update_pressed(("keyboard:a",))
    assert runtime.snapshot().effective_input_mode == "3d"
    runtime._draft.set_context(FocusedContext("freecad", "freecad.exe"))
    runtime._snapshot = runtime._build_snapshot(
        runtime._draft, RuntimeBaseState("pointer", settings=runtime.snapshot().base_settings),
        runtime.snapshot().revision)
    controller.context_changed()
    assert runtime.snapshot().effective_input_mode == "pointer"


def test_other_apps_context_unions_unregistered_apps_with_selected_integrations():
    custom = _binding_row(
        ("keyboard:a",), ({"command": "state.request", "target": "input.3d"},),
        ({"command": "state.release", "target": "input.3d"},),
        when={"apps": ["onshape"], "other_apps": True})

    for context, expected in (
            (FocusedContext(None, "notes.exe"), "3d"),
            (FocusedContext("onshape", "chrome.exe"), "3d"),
            (FocusedContext("blender", "blender.exe"), "pointer")):
        runtime = _runtime(app_id=context.app_id, executable=context.executable)
        controller, _runtime_store, _catalog = _controller(
            overrides={"custom.context": custom}, runtime=runtime)
        controller.update_pressed(("keyboard:a",))
        assert runtime.snapshot().effective_input_mode == expected


class _RecordingPointerSink(PointerButtonSink):
    def __init__(self):
        self.events = []

    def press(self, button, owner):
        self.events.append(("press", button, owner))

    def release(self, button, owner):
        self.events.append(("release", button, owner))


def test_pointer_resource_is_identity_owned_and_reload_synthesizes_release():
    sink = _RecordingPointerSink()
    controller, _runtime_store, catalog = _controller(
        "astrolabe_5way", pointer_sink=sink)
    controller.update_pressed(("ble.xiao3389:button.left",))
    assert sink.events[0][:2] == ("press", "left")
    controller.reload(compile_binding_profile(catalog.profile("keyboard_only"), catalog))
    assert sink.events[1][:2] == ("release", "left")
    assert sink.events[0][2] == sink.events[1][2]


def test_system_pointer_click_remains_active_with_pass_through_modifiers():
    sink = _RecordingPointerSink()
    controller, _runtime_store, _catalog = _controller(
        "astrolabe_5way", pointer_sink=sink)

    controller.update_pressed((
        "keyboard:shift.left", "ble.xiao3389:button.left"))
    controller.update_pressed(("keyboard:shift.left",))
    controller.update_pressed((
        "keyboard:ctrl.right", "ble.xiao3389:button.right"))
    controller.update_pressed(("keyboard:ctrl.right",))

    assert [(phase, button) for phase, button, _owner in sink.events] == [
        ("press", "left"), ("release", "left"),
        ("press", "right"), ("release", "right"),
    ]


def test_ble_disconnect_releases_a_real_pointer_output_sink():
    events = []
    sink = SendInputPointerButtonSink(
        lambda button, pressed: events.append((button, pressed)))
    controller, _runtime_store, _catalog = _controller(
        "astrolabe_5way", pointer_sink=sink)
    aggregator = InputAggregator()
    aggregator.register_controls((InputControlDescriptor(
        "ble.xiao3389", "button.left", "Left", "button"),))
    aggregator.add_listener(controller.handle_transition)

    aggregator.accept(InputEvent(
        "ble.xiao3389", "button.left", "pressed", 1.0))
    aggregator.accept(InputEvent(
        "ble.xiao3389", None, "disconnected", 2.0))

    assert events == [("left", True), ("left", False)]
    assert sink.held == {}


def test_reload_releases_all_runtime_requests_in_one_revision():
    custom = _binding_row(
        ("keyboard:a",),
        ({"command": "state.request", "target": "input.3d"},
         {"command": "setting.set_runtime", "target": "navigation.orbit.sensitivity",
          "value": 2.0}),
        ({"command": "state.release", "target": "input.3d"},
         {"command": "setting.restore_previous", "target": "navigation.orbit.sensitivity"}))
    controller, runtime, catalog = _controller(overrides={"custom.atomic": custom})
    controller.update_pressed(("keyboard:a",))
    before = runtime.snapshot().revision
    controller.reload(compile_binding_profile(catalog.profile("keyboard_only"), catalog))
    assert runtime.snapshot().revision == before + 1
    assert runtime.snapshot().effective_input_mode == "pointer"
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 1.0


def test_setting_hold_restores_by_activation_identity_and_numeric_macros_compose():
    set_hold = _binding_row(
        ("keyboard:a",),
        ({"command": "setting.set_runtime", "target": "navigation.orbit.sensitivity",
          "value": 2.0},),
        ({"command": "setting.restore_previous", "target": "navigation.orbit.sensitivity"},))
    add_multiply = _binding_row(
        ("keyboard:f12",),
        ({"command": "setting.add_runtime", "target": "navigation.orbit.sensitivity",
          "value": 1.0},
         {"command": "setting.multiply_runtime", "target": "navigation.orbit.sensitivity",
          "value": 2.0}))
    controller, runtime, _catalog = _controller(overrides={
        "keyboard.f12.toggle_mode": {"enabled": False},
        "custom.setting.hold": set_hold,
        "custom.setting.math": add_multiply,
    })
    controller.update_pressed(("keyboard:a",))
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 2.0
    controller.update_pressed(())
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 1.0
    controller.update_pressed(("keyboard:f12",))
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 4.0


def test_hold_can_apply_an_explicit_setting_value_on_release():
    binding = _binding_row(
        ("keyboard:a",),
        ({"command": "setting.set_runtime", "target": "navigation.orbit.sensitivity",
          "value": 2.0},),
        ({"command": "setting.set_runtime", "target": "navigation.orbit.sensitivity",
          "value": 0.5},))
    controller, runtime, _catalog = _controller(overrides={"custom.explicit": binding})
    controller.update_pressed(("keyboard:a",))
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 2.0
    controller.update_pressed(())
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 0.5


def test_explicit_two_value_toggle_and_enum_cycle_macros():
    toggle = _binding_row(
        ("keyboard:a",),
        ({"command": "setting.toggle_runtime", "target": "navigation.orbit.sensitivity",
          "value": [1.0, 2.0]},))
    cycle = _binding_row(
        ("keyboard:f12",),
        ({"command": "setting.cycle_runtime", "target": "navigation.orbit.style",
          "value": ["default", "free", "turntable"]},))
    controller, runtime, _catalog = _controller(overrides={
        "keyboard.f12.toggle_mode": {"enabled": False},
        "custom.toggle": toggle,
        "custom.cycle": cycle,
    })
    controller.update_pressed(("keyboard:a",))
    controller.update_pressed(())
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 2.0
    controller.update_pressed(("keyboard:a",))
    controller.update_pressed(())
    assert runtime.snapshot().effective_settings["navigation.orbit.sensitivity"] == 1.0
    controller.update_pressed(("keyboard:f12",))
    assert runtime.snapshot().effective_settings["navigation.orbit.style"] == "free"


def test_numeric_setting_cycle_supports_more_than_two_selected_values():
    cycle = _binding_row(
        ("keyboard:a",),
        ({"command": "setting.cycle_runtime", "target": "navigation.orbit.sensitivity",
          "value": [0.5, 2.0, 4.0]},))
    controller, runtime, _catalog = _controller(overrides={"custom.cycle": cycle})

    for expected in (0.5, 2.0, 4.0, 0.5):
        controller.update_pressed(("keyboard:a",))
        controller.update_pressed(())
        assert runtime.snapshot().effective_settings[
            "navigation.orbit.sensitivity"] == expected


def test_persistent_macro_uses_one_config_transaction_without_runtime_latch(tmp_path):
    config = ConfigStore(tmp_path / "config.json").load()
    binding = _binding_row(
        ("keyboard:a",),
        ({"command": "setting.set_persistent", "target": "navigation.orbit.sensitivity",
          "value": 2.5},))
    controller, runtime, _catalog = _controller(
        overrides={"custom.persistent": binding}, config_store=config)
    before_runtime_revision = runtime.snapshot().revision
    before_config_revision = config.snapshot().revision
    controller.update_pressed(("keyboard:a",))
    assert config.snapshot().global_value("navigation.orbit.sensitivity") == 2.5
    assert config.snapshot().revision == before_config_revision + 1
    assert runtime.snapshot().revision == before_runtime_revision + 1
    assert runtime.snapshot().held_binding_ids == ("custom.persistent",)
    assert runtime.snapshot().last_binding_event.label == "Custom binding"


def test_static_invalid_entry_is_disabled_without_losing_valid_bindings():
    catalog = load_system_binding_profiles()
    base = catalog.profile("keyboard_only")
    invalid = replace(
        base.bindings[0], binding_id="bad.freecad.fly",
        context=replace(base.bindings[0].context, apps=("freecad",)),
        press_actions=(replace(base.bindings[1].press_actions[0],
                               command_id="navigation.mode.set", target="fly"),),
        release_actions=())
    profile = SystemBindingProfile(base.id, base.label, base.bindings + (invalid,))
    compiled = compile_binding_profile(profile, catalog)
    assert "bad.freecad.fly" not in {binding.id for binding in compiled.bindings}
    assert any(item.binding_id == "bad.freecad.fly" for item in compiled.diagnostics)
    assert "keyboard.ctrl.3d" in {binding.id for binding in compiled.bindings}


def test_context_scoped_hold_rejects_an_unsupported_navigation_mode():
    catalog = load_system_binding_profiles()
    base = catalog.profile("keyboard_only")
    invalid = replace(
        base.bindings[0], binding_id="bad.freecad.fly.hold",
        context=replace(base.bindings[0].context, apps=("freecad",)),
        press_actions=(replace(base.bindings[1].press_actions[0],
                               command_id="state.request", target="navigation.fly"),),
        release_actions=(replace(base.bindings[1].release_actions[0],
                                 command_id="state.release", target="navigation.fly"),))
    profile = SystemBindingProfile(base.id, base.label, base.bindings + (invalid,))

    compiled = compile_binding_profile(profile, catalog)

    assert "bad.freecad.fly.hold" not in {binding.id for binding in compiled.bindings}
    assert any(item.binding_id == "bad.freecad.fly.hold" and
               item.code == "unsupported_navigation_mode"
               for item in compiled.diagnostics)


def test_object_mode_binding_is_accepted_only_for_capable_hosts():
    catalog = load_system_binding_profiles()
    base = catalog.profile("keyboard_only")
    template = base.bindings[0]

    def binding(app_id):
        return replace(
            template, binding_id=f"{app_id}.object",
            context=replace(template.context, apps=(app_id,)),
            press_actions=(replace(
                base.bindings[1].press_actions[0],
                command_id="state.request", target="navigation.object"),),
            release_actions=(replace(
                base.bindings[1].release_actions[0],
                command_id="state.release", target="navigation.object"),))

    profile = SystemBindingProfile(
        base.id, base.label, base.bindings + (binding("blender"), binding("godot")))
    compiled = compile_binding_profile(profile, catalog)
    ids = {item.id for item in compiled.bindings}
    assert "blender.object" in ids
    assert "godot.object" not in ids


def test_malformed_editor_row_is_isolated_with_actionable_diagnostic():
    valid = _binding_row(
        ("keyboard:a",), ({"command": "input.mode.toggle"},))
    valid["id"] = "valid"
    invalid = dict(valid)
    invalid["id"] = "invalid"
    invalid["press"] = [{"command": "python.eval", "target": "unsafe"}]
    compiled = compile_binding_rows([invalid, valid], "keyboard_only")
    assert [binding.id for binding in compiled.bindings] == ["valid"]
    assert compiled.diagnostics[0].binding_id == "invalid"
    assert "not allowlisted" in compiled.diagnostics[0].message
