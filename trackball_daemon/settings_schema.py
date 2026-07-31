# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Stable setting and command registry for config, bindings, and generated settings UI.

Public setting IDs and capability predicates remain stable while persistence uses sparse
System -> Global -> app resolution. Legacy paths are retained only where migration requires them.
"""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class SettingScope(str, Enum):
    GLOBAL_ONLY = "global_only"
    GLOBAL_AND_APP = "global_and_app"
    DEVICE = "device"


class ValueKind(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    STRING_LIST = "string_list"
    ENUM = "enum"


class SettingOperation(str, Enum):
    UI_PERSIST = "ui.persist"
    RUNTIME_SET = "runtime.set"
    RUNTIME_TOGGLE = "runtime.toggle"
    RUNTIME_CYCLE = "runtime.cycle"
    RUNTIME_ADD = "runtime.add"
    RUNTIME_MULTIPLY = "runtime.multiply"
    RUNTIME_RESTORE = "runtime.restore_previous"
    MACRO_PERSIST = "macro.persist"


@dataclass(frozen=True)
class CapabilityPredicate:
    """Small declarative predicate over capabilities owned by ``AppSpec``."""

    all_of: frozenset = frozenset()
    any_of: frozenset = frozenset()
    none_of: frozenset = frozenset()

    def matches(self, capabilities):
        capabilities = frozenset(capabilities)
        return (self.all_of <= capabilities and
                (not self.any_of or bool(self.any_of & capabilities)) and
                not bool(self.none_of & capabilities))

    @property
    def referenced_capabilities(self):
        return self.all_of | self.any_of | self.none_of


@dataclass(frozen=True)
class SystemDefaultSource:
    """Developer-owned source location in ``system_defaults.json``."""

    global_path: tuple = ()
    app_path: tuple = ()


@dataclass(frozen=True)
class SettingUI:
    label: str
    help: str
    control: str


@dataclass(frozen=True)
class SettingSpec:
    """Stable setting identity independent of the current v8 dictionary layout."""

    setting_id: str
    value_kind: ValueKind
    category: str
    scope: SettingScope
    capability: CapabilityPredicate
    system_default_source: SystemDefaultSource
    ui: SettingUI
    operations: frozenset
    app_path: tuple = ()
    global_path: tuple = ()
    choices: tuple = ()
    minimum: float = None
    maximum: float = None
    nullable: bool = False

    @property
    def id(self):
        return self.setting_id

    @property
    def keybindable(self):
        return any(operation.value.startswith("runtime.") for operation in self.operations)

    def applies_to(self, app_spec):
        return (self.scope is SettingScope.GLOBAL_AND_APP and
                self.capability.matches(app_spec.capabilities))

    def validates(self, value):
        if value is None:
            return self.nullable
        if self.value_kind is ValueKind.BOOLEAN:
            valid_type = type(value) is bool
        elif self.value_kind is ValueKind.INTEGER:
            valid_type = type(value) is int
        elif self.value_kind is ValueKind.NUMBER:
            valid_type = type(value) in (int, float)
        elif self.value_kind in (ValueKind.STRING, ValueKind.ENUM):
            valid_type = isinstance(value, str)
        elif self.value_kind is ValueKind.STRING_LIST:
            valid_type = (isinstance(value, (list, tuple)) and
                          all(isinstance(item, str) for item in value))
        else:
            valid_type = False
        if not valid_type:
            return False
        if self.choices:
            if self.value_kind is ValueKind.STRING_LIST:
                if any(item not in self.choices for item in value):
                    return False
            elif value not in self.choices:
                return False
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True


@dataclass(frozen=True)
class CommandSpec:
    """Allowlisted non-setting action available to the declarative binding compiler."""

    command_id: str
    title: str
    description: str
    category: str
    targets: tuple = ()
    value_kind: ValueKind = None

    @property
    def id(self):
        return self.command_id


@dataclass(frozen=True)
class BindingSection:
    """Compatibility presentation order for the current per-app settings renderer."""

    key: str
    title: str
    fields: tuple


BINDING_SECTIONS = (
    BindingSection("sensitivity", "Sensitivity & rate", (
        "rate", "orbit_sensitivity", "pan_gain", "zoom_gain", "zoom_dominance")),
    BindingSection("navigation", "Navigation mode", ("nav_mode", "fly_speed", "walk_speed")),
    BindingSection("object", "Object", ("object_translation_frame",)),
    BindingSection("orbit", "Orbit", (
        "orbit_style", "orbit_pivot", "orbit_hold", "twist_action", "lock_horizon",
        "level_horizon", "selection_override")),
    BindingSection("pan_zoom", "Pan / Zoom", (
        "zoom_target", "zoom_hold", "zoom_behavior", "pan_scales", "dynamic_clip",
        "pivot_extent")),
    BindingSection("camera", "Camera view", ("camera_lock",)),
    BindingSection("routing", "Action axes & directions", ("action_routing",)),
    BindingSection("host_extra", "Host-specific setup", ("onshape_userscript",)),
)


_PERSIST = frozenset({SettingOperation.UI_PERSIST, SettingOperation.MACRO_PERSIST})
_NUMBER_OPS = _PERSIST | frozenset({
    SettingOperation.RUNTIME_SET, SettingOperation.RUNTIME_TOGGLE, SettingOperation.RUNTIME_CYCLE,
    SettingOperation.RUNTIME_ADD, SettingOperation.RUNTIME_MULTIPLY,
    SettingOperation.RUNTIME_RESTORE,
})
_BOOL_OPS = _PERSIST | frozenset({
    SettingOperation.RUNTIME_SET, SettingOperation.RUNTIME_TOGGLE,
    SettingOperation.RUNTIME_RESTORE,
})
_ENUM_OPS = _PERSIST | frozenset({
    SettingOperation.RUNTIME_SET, SettingOperation.RUNTIME_CYCLE,
    SettingOperation.RUNTIME_RESTORE,
})


def _cap(*all_of, none_of=()):
    return CapabilityPredicate(frozenset(all_of), none_of=frozenset(none_of))


def _ui(label, help_text, control):
    return SettingUI(label, help_text or label, control)


def _app(setting_id, path, kind, label, capability, *, category, help_text="",
         control=None, choices=(), minimum=None, maximum=None, nullable=False,
         global_path=(), operations=None, extra_capabilities=(), excluded_capabilities=()):
    predicate = _cap(capability, *extra_capabilities, none_of=excluded_capabilities)
    return SettingSpec(
        setting_id=setting_id,
        value_kind=kind,
        category=category,
        scope=SettingScope.GLOBAL_AND_APP,
        capability=predicate,
        system_default_source=SystemDefaultSource(
            global_path=("system_defaults", "global", setting_id),
            app_path=("system_defaults", "apps", "*", setting_id)),
        ui=_ui(label, help_text, control or ("checkbox" if kind is ValueKind.BOOLEAN
                                             else "choice" if kind is ValueKind.ENUM else "entry")),
        operations=operations or (_BOOL_OPS if kind is ValueKind.BOOLEAN else
                                  _ENUM_OPS if kind is ValueKind.ENUM else _NUMBER_OPS),
        app_path=tuple(path),
        global_path=tuple(global_path),
        choices=tuple(choices),
        minimum=minimum,
        maximum=maximum,
        nullable=nullable,
    )


def _global(setting_id, path, kind, label, *, category, help_text="", control=None,
            choices=(), minimum=None, maximum=None, operations=None):
    return SettingSpec(
        setting_id=setting_id,
        value_kind=kind,
        category=category,
        scope=SettingScope.GLOBAL_ONLY,
        capability=CapabilityPredicate(),
        system_default_source=SystemDefaultSource(
            global_path=("system_defaults", "global", setting_id)),
        ui=_ui(label, help_text, control or ("checkbox" if kind is ValueKind.BOOLEAN
                                             else "choice" if kind is ValueKind.ENUM else "entry")),
        operations=operations or (_BOOL_OPS if kind is ValueKind.BOOLEAN else
                                  _ENUM_OPS if kind is ValueKind.ENUM else _NUMBER_OPS),
        global_path=tuple(path),
        choices=tuple(choices),
        minimum=minimum,
        maximum=maximum,
    )


_SPECS = [
    # Device identity remains editable but is deliberately not in the keybindable command surface.
    SettingSpec("device.name", ValueKind.STRING, "device", SettingScope.DEVICE,
                CapabilityPredicate(), SystemDefaultSource(
                    ("system_defaults", "device", "device.name")),
                _ui("Device name", "BLE advertised name; applies on reconnect.", "entry"),
                frozenset({SettingOperation.UI_PERSIST}), global_path=("device", "name")),
    SettingSpec("device.address", ValueKind.STRING, "device", SettingScope.DEVICE,
                CapabilityPredicate(), SystemDefaultSource(
                    ("system_defaults", "device", "device.address")),
                _ui("Device address", "Optional BLE address; applies on reconnect.", "entry"),
                frozenset({SettingOperation.UI_PERSIST}), global_path=("device", "address")),

    _global("hud.visible", (), ValueKind.BOOLEAN, "Show control panel",
            category="hud", help_text="Show the persistent text control panel.",
            operations=frozenset({SettingOperation.UI_PERSIST})),
    _global("hud.always_on_top", (), ValueKind.BOOLEAN, "Always on top",
            category="hud", help_text="Keep the control panel above ordinary windows.",
            operations=frozenset({SettingOperation.UI_PERSIST})),
    _global("hud.click_through", (), ValueKind.BOOLEAN, "Click through",
            category="hud", help_text="Allow pointer input to pass through the control panel.",
            operations=frozenset({SettingOperation.UI_PERSIST})),
    _global("hud.opacity", (), ValueKind.NUMBER, "Opacity", category="hud",
            minimum=0.2, maximum=1.0,
            help_text="Control-panel opacity from 0.2 to 1.0.",
            operations=frozenset({SettingOperation.UI_PERSIST})),
    _global("hud.margin", (), ValueKind.INTEGER, "Screen margin", category="hud",
            minimum=0, maximum=200,
            help_text="Distance in pixels from the monitor work-area edges.",
            operations=frozenset({SettingOperation.UI_PERSIST})),
    _global("hud.last_binding_timeout", (), ValueKind.NUMBER, "Last binding timeout",
            category="hud", minimum=0.0, maximum=30.0,
            help_text="Seconds to retain the last-used binding after release.",
            operations=frozenset({SettingOperation.UI_PERSIST})),

    _global("input.mode.default", ("general", "default_mode"), ValueKind.ENUM, "Default input mode",
            category="input", choices=("cube", "cursor", "3d", "pointer"),
            help_text="Input mode used when no runtime binding overrides it; legacy values migrate."),
    _global("pointer.acceleration.curve", (), ValueKind.ENUM, "Acceleration curve",
            category="pointer", choices=("off", "linear", "smooth"),
            help_text="Speed-based cursor acceleration; Off preserves constant sensitivity."),
    _global("pointer.acceleration.onset", (), ValueKind.NUMBER, "Acceleration onset",
            category="pointer", minimum=0.0,
            help_text="Planar ball speed in radians/second where acceleration begins."),
    _global("pointer.acceleration.ramp", (), ValueKind.NUMBER, "Acceleration ramp",
            category="pointer", minimum=0.001,
            help_text="Additional radians/second needed to reach the maximum multiplier."),
    _global("pointer.acceleration.max_gain", (), ValueKind.NUMBER,
            "Maximum acceleration", category="pointer", minimum=1.0, maximum=10.0,
            help_text="Maximum cursor-speed multiplier; 1 keeps sensitivity constant."),
    _global("pointer.cursor.gain", ("general", "cursor", "gain"), ValueKind.NUMBER,
            "Pointer sensitivity", category="pointer", minimum=0.0),
    _global("pointer.scroll.gain", ("general", "scroll", "gain"), ValueKind.NUMBER,
            "Scroll gain", category="pointer", minimum=0.0),
    _global("pointer.scroll.deadzone", ("general", "scroll", "deadzone"), ValueKind.NUMBER,
            "Scroll deadzone", category="pointer", minimum=0.0),
    _global("pointer.scroll.dominance", ("general", "scroll", "dominance"), ValueKind.NUMBER,
            "Scroll dominance", category="pointer", minimum=0.0),
    _app("navigation.orbit.pivot_fallbacks", (),
         ValueKind.STRING_LIST, "Orbit pivot fallback order", "orbit_pivot", category="orbit",
         choices=("camera", "screen_center", "cursor", "selection", "cursor_3d", "object",
                  "origin"), control="ordered_list",
         global_path=("general", "orbit_pivot_fallbacks"),
         operations=frozenset({SettingOperation.UI_PERSIST})),

    _app("navigation.refresh_rate", ("rate_hz",), ValueKind.INTEGER, "Viewport refresh rate",
         "rate", category="sensitivity", minimum=0, maximum=240,
         global_path=("bridge", "rate_hz")),
    _app("navigation.orbit.pivot_hold_seconds", ("orbit_pivot_hold_sec",), ValueKind.NUMBER,
         "Orbit pivot hold", "orbit_hold", category="orbit", minimum=0.0),
    _app("navigation.zoom.cursor_hold_seconds", ("zoom_cursor_hold_sec",), ValueKind.NUMBER,
         "Zoom cursor hold", "zoom_hold", category="pan_zoom", minimum=0.0),
    _app("navigation.orbit.selection_override", ("selection_overrides_pivot",), ValueKind.BOOLEAN,
         "Selection overrides orbit center", "selection_override", category="orbit"),
    _app("navigation.level_horizon_on_entry", ("level_horizon_on_entry",), ValueKind.BOOLEAN,
         "Level horizon on mode entry", "level_horizon", category="orbit", nullable=True,
         global_path=("general", "level_horizon_on_entry")),
    _app("navigation.orbit.sensitivity", ("bindings", "orbit", "sensitivity"), ValueKind.NUMBER,
         "Orbit sensitivity", "orbit_sensitivity", category="sensitivity", minimum=0.0),
    _app("navigation.pan.gain", ("bindings", "pan", "gain"), ValueKind.NUMBER,
         "Pan gain", "pan_gain", category="sensitivity", minimum=0.0),
    _app("navigation.zoom.gain", ("bindings", "zoom", "gain"), ValueKind.NUMBER,
         "Zoom gain", "zoom_gain", category="sensitivity", minimum=0.0),
    _app("navigation.zoom.dominance", ("bindings", "zoom", "dominance"), ValueKind.NUMBER,
         "Zoom dominance", "zoom_dominance", category="sensitivity", minimum=0.0),
    _app("navigation.orbit.style", ("bindings", "scheme", "orbit_style"), ValueKind.ENUM,
         "Orbit style", "orbit_style", category="orbit",
         choices=("default", "free", "turntable"), global_path=("general", "scheme", "orbit_style")),
    _app("navigation.orbit.pivot", ("bindings", "scheme", "orbit_pivot"), ValueKind.ENUM,
         "Orbit pivot", "orbit_pivot", category="orbit",
         choices=("default", "camera", "screen_center", "cursor", "selection", "cursor_3d",
                  "object", "origin"), global_path=("general", "scheme", "orbit_pivot")),
    _app("navigation.orbit.twist_action", ("advanced", "twist_action"), ValueKind.ENUM,
         "Twist action", "twist_action", category="orbit",
         choices=("roll", "zoom", "dolly", "none")),
    _app("navigation.zoom.target", ("bindings", "scheme", "zoom_mode"), ValueKind.ENUM,
         "Zoom target", "zoom_target", category="pan_zoom",
         choices=("default", "to_center", "to_object", "to_cursor"),
         global_path=("general", "scheme", "zoom_mode")),
    _app("navigation.mode", ("advanced", "nav_mode"), ValueKind.ENUM, "Navigation mode",
         "nav_mode", category="navigation", choices=("orbit", "fly", "walk", "object")),
    _app("navigation.fly.speed", ("advanced", "fly_speed"), ValueKind.NUMBER, "Fly speed",
         "fly_speed", category="navigation", minimum=0.0),
    _app("navigation.walk.speed", ("advanced", "walk_speed"), ValueKind.NUMBER, "Walk speed",
         "walk_speed", category="navigation", minimum=0.0),
    _app("navigation.object.translation_frame", ("advanced", "object_translation_frame"),
         ValueKind.ENUM, "Object movement frame", "object_translation_frame", category="object",
         choices=("view", "ground"),
         extra_capabilities=("object_manipulation",),
         help_text=("View moves in the viewport plane with twist for depth; Ground moves "
                    "right/world-up with twist for horizontal depth.")),
    _app("navigation.orbit.lock_horizon", ("advanced", "lock_horizon"), ValueKind.BOOLEAN,
         "Lock horizon", "lock_horizon", category="orbit"),
    _app("navigation.pan.scales_with_distance", ("advanced", "pan_scales_with_distance"),
         ValueKind.BOOLEAN, "Pan scales with view distance", "pan_scales", category="pan_zoom"),
    _app("navigation.zoom.behavior", ("advanced", "zoom_style"), ValueKind.ENUM,
         "Pan-mode zoom behavior", "zoom_behavior", category="pan_zoom",
         choices=("zoom", "dolly")),
    _app("navigation.camera.lock_to_view", ("advanced", "lock_camera_to_view"), ValueKind.BOOLEAN,
         "Lock camera to view", "camera_lock", category="camera"),
    _app("navigation.unity.override_dynamic_clip", ("advanced", "override_dynamic_clip"),
         ValueKind.BOOLEAN, "Override Unity Dynamic Clipping", "dynamic_clip", category="pan_zoom"),
    _app("navigation.unity.pivot_extent_multiplier", ("advanced", "pivot_extent_mult"),
         ValueKind.NUMBER, "Pivot extent limit", "pivot_extent", category="pan_zoom", minimum=0.0),
]


# Physical-orientation values are intentionally persistent-only: they define device installation,
# not a transient app action. Individual axes remain separately addressable for generated UI.
for index, axis in enumerate(("x", "y", "z")):
    _SPECS.append(_global(
        f"input.axis_orientation.{axis}.source",
        ("general", "axis_orientation", "source", index), ValueKind.INTEGER,
        f"Logical {axis.upper()} uses physical axis", category="transform", choices=(0, 1, 2),
        control="choice", operations=frozenset({SettingOperation.UI_PERSIST})))
    _SPECS.append(_global(
        f"input.axis_orientation.{axis}.invert",
        ("general", "axis_orientation", "invert", index), ValueKind.BOOLEAN,
        f"Invert logical {axis.upper()}", category="transform",
        operations=frozenset({SettingOperation.UI_PERSIST})))


# Lean integrations route six generic action channels. Rich integrations route named mode actions.
_LEAN_ROUTES = (
    ("orbit.x", ("bindings", "orbit", "axis_source", 0),
     ("bindings", "invert", "orbit", 0), "Orbit X"),
    ("orbit.y", ("bindings", "orbit", "axis_source", 1),
     ("bindings", "invert", "orbit", 1), "Orbit Y"),
    ("orbit.z", ("bindings", "orbit", "axis_source", 2),
     ("bindings", "invert", "orbit", 2), "Orbit Z"),
    ("pan.x", ("bindings", "pan", "x_src"),
     ("bindings", "invert", "pan", 0), "Pan X"),
    ("pan.y", ("bindings", "pan", "y_src"),
     ("bindings", "invert", "pan", 1), "Pan Y"),
    ("zoom", ("bindings", "zoom", "src"),
     ("bindings", "invert", "zoom"), "Zoom"),
)
for action_id, source_path, invert_path, label in _LEAN_ROUTES:
    _SPECS.append(_app(
        f"navigation.routing.{action_id}.source", source_path, ValueKind.INTEGER,
        f"{label} source", "action_routing", category="routing", choices=(0, 1, 2),
        control="choice", extra_capabilities=("lean_actions",)))
    _SPECS.append(_app(
        f"navigation.routing.{action_id}.invert", invert_path, ValueKind.BOOLEAN,
        f"Invert {label}", "action_routing", category="routing",
        extra_capabilities=("lean_actions",)))


_RICH_ACTIONS = {
    "orbit": ("pitch", "yaw", "twist", "pan_x", "pan_y", "zoom"),
    "camera": ("pitch", "yaw", "roll"),
    "fly": ("pitch", "yaw", "bank", "forward", "strafe", "vertical"),
    "walk": ("pitch", "yaw", "forward", "strafe", "vertical"),
    "object": ("pitch", "yaw", "roll", "translate_x", "translate_y", "translate_z"),
}
for mode, actions in _RICH_ACTIONS.items():
    for action in actions:
        requires_roll = (mode, action) in {("camera", "roll"), ("fly", "bank")}
        extras = (("rich_actions", "object_manipulation") if mode == "object" else
                  ("rich_actions", "roll") if requires_roll else ("rich_actions",))
        label = f"{mode.title()} {action.replace('_', ' ').title()}"
        _SPECS.append(_app(
            f"navigation.routing.{mode}.{action}.source",
            ("advanced", "axis_source", mode, action), ValueKind.INTEGER,
            f"{label} source", "action_routing", category="routing", choices=(0, 1, 2),
            control="choice", extra_capabilities=extras))
        _SPECS.append(_app(
            f"navigation.routing.{mode}.{action}.invert",
            ("advanced", "invert", mode, action), ValueKind.BOOLEAN,
            f"Invert {label}", "action_routing", category="routing",
            extra_capabilities=extras))


SETTING_SPECS = tuple(_SPECS)
SETTING_SPECS_BY_ID = MappingProxyType({spec.setting_id: spec for spec in SETTING_SPECS})
SETTING_SPECS_BY_GLOBAL_PATH = MappingProxyType(
    {spec.global_path: spec for spec in SETTING_SPECS if spec.global_path})
SETTING_SPECS_BY_APP_PATH = MappingProxyType(
    {spec.app_path: spec for spec in SETTING_SPECS if spec.app_path})


# Stored profile mechanics that are deliberately not public controls. Current v9 migration and
# runtime materialization still consume these paths, so keep them classified outside UI metadata.
APP_INTERNAL_PROFILE_PATHS = frozenset({
    # Compatibility-only materialized field; keybinding data owns layer activation.
    ("bindings", "toggle"),
    ("bindings", "orbit", "axis_sign", 0),
    ("bindings", "orbit", "axis_sign", 1),
    ("bindings", "orbit", "axis_sign", 2),
    ("bindings", "pan", "x_sign"),
    ("bindings", "pan", "y_sign"),
    ("bindings", "zoom", "sign"),
    ("bindings", "zoom", "deadzone"),
    ("bindings", "zoom", "dist_min"),
    ("bindings", "zoom", "dist_max"),
    ("bindings", "zoom", "dist_default"),
})
APP_OPERATIONAL_PATHS = frozenset({
    ("enabled",), ("installed",), ("addin_version",),
})
# v8 rendered these controls even though controller mode had no consumer for them. Keep them
# classified as deprecated migration input rather than public SettingSpec IDs.
V8_DEPRECATED_USER_PATHS = frozenset({
    ("general", "buttons", "left"),
    ("general", "buttons", "right"),
    ("general", "buttons", "middle"),
})
UI_ONLY_CAPABILITIES = frozenset({"onshape_userscript"})


COMMAND_SPECS = (
    CommandSpec("state.request", "Request control state",
                "Request a dependency-resolved runtime leaf state.", "state"),
    CommandSpec("state.release", "Release control state",
                "Release the current activation's state request.", "state"),
    CommandSpec("input.mode.set", "Set input mode", "Set Pointer or 3D mode.", "state",
                targets=("pointer", "3d"), value_kind=ValueKind.ENUM),
    CommandSpec("input.mode.toggle", "Toggle input mode", "Toggle Pointer and 3D mode.", "state"),
    CommandSpec("navigation.mode.set", "Set navigation mode",
                "Set a navigation mode for the focused app.", "navigation",
                targets=("orbit", "fly", "walk", "object"), value_kind=ValueKind.ENUM),
    CommandSpec("navigation.mode.cycle", "Cycle navigation mode",
                "Cycle supported navigation modes for the focused app.", "navigation"),
    CommandSpec("navigation.layer.set", "Set navigation layer",
                "Set the primary or secondary action layer.", "navigation",
                targets=("primary", "secondary"), value_kind=ValueKind.ENUM),
    CommandSpec("pointer.button.press", "Press pointer button",
                "Press one bounded OS pointer button through the bounded pointer output sink.", "pointer",
                targets=("left", "right", "middle", "x1", "x2"),
                value_kind=ValueKind.ENUM),
    CommandSpec("pointer.button.release", "Release pointer button",
                "Release one bounded OS pointer button through the bounded pointer output sink.", "pointer",
                targets=("left", "right", "middle", "x1", "x2"),
                value_kind=ValueKind.ENUM),
)
COMMAND_SPECS_BY_ID = MappingProxyType({spec.command_id: spec for spec in COMMAND_SPECS})

_APP_CHOICE_ATTRIBUTES = MappingProxyType({
    "navigation.orbit.style": "orbit_styles",
    "navigation.orbit.pivot": "pivots",
    "navigation.orbit.twist_action": "twist_actions",
    "navigation.zoom.target": "zoom_targets",
    "navigation.zoom.behavior": "zoom_behaviors",
})


def setting_specs_for_app(app_spec):
    return tuple(spec for spec in SETTING_SPECS if spec.applies_to(app_spec))


def setting_choices_for_app(setting_spec, app_spec):
    """Return capability-restricted choices, including the current inheritance sentinel."""
    if setting_spec.setting_id == "navigation.mode":
        return tuple(app_spec.supported_modes)
    attribute = _APP_CHOICE_ATTRIBUTES.get(setting_spec.setting_id)
    if attribute:
        choices = tuple(getattr(app_spec.binding_profile, attribute))
        if "default" in setting_spec.choices and "default" not in choices:
            choices = ("default",) + choices
        return choices
    return setting_spec.choices


def setting_value_valid_for_app(setting_spec, app_spec, value):
    if not setting_spec.applies_to(app_spec) or not setting_spec.validates(value):
        return False
    choices = setting_choices_for_app(setting_spec, app_spec)
    if setting_spec.value_kind is ValueKind.STRING_LIST:
        return True
    return not choices or value in choices


def classify_app_profile_path(app_spec, path):
    """Return the durable ownership classification for one resolved v8 profile leaf."""
    path = tuple(path)
    candidates = [spec for spec in SETTING_SPECS if spec.app_path == path]
    if any(spec.applies_to(app_spec) for spec in candidates):
        return "user_setting"
    if candidates:
        return "capability_inactive"
    if path in APP_INTERNAL_PROFILE_PATHS:
        return "internal_runtime_parameter"
    if path in APP_OPERATIONAL_PATHS:
        return "operational"
    return "unknown"
