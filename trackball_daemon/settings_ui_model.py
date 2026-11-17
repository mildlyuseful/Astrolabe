# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""UI-independent settings projections, presentation metadata, and typed settings actions.

The settings UI is generated from SETTING_SPECS. A new setting needs only a SettingSpec with an
existing category; a new category needs one line in CATEGORIES saying where it is shown.
"""

from dataclasses import dataclass

from .app_registry import APP_SPECS_BY_ID
from .settings_schema import (
    SETTING_SPECS,
    SettingScope,
    setting_choices_for_app,
    setting_value_valid_for_app,
)
from .system_defaults import SYSTEM_DEFAULTS


@dataclass(frozen=True)
class Category:
    category_id: str
    title: str
    page: str          # "apps": per-app capable, shown per app; "device": device-wide settings
    summary: str = ""


# Display order is this order. Categories missing here still render, titled from their ID.
CATEGORIES = (
    Category("sensitivity", "Feel", "apps", "How fast each motion moves the view."),
    Category("orbit", "Orbit", "apps", "Where the view turns, and how."),
    Category("pan_zoom", "Zoom & pan", "apps", "Zoom targets, holds, and pan behaviour."),
    Category("camera", "Camera", "apps"),
    Category("navigation", "Fly & walk", "apps", "Speeds for the first-person modes."),
    Category("object", "Object", "apps", "Moving selected objects instead of the camera."),
    Category("routing", "Axes", "apps", "Which ball axis drives each action, and its direction."),
    Category("device", "Device", "device", "Applies on the next reconnect."),
    Category("transform", "Orientation", "device",
             "Map the sensor axes to the way the device sits on your desk."),
    Category("pointer", "Pointer", "device", "Cursor and scroll while in Pointer mode."),
    Category("input", "Startup", "device"),
    Category("hud", "Control panel", "device", "The small status panel in the screen corner."),
)
CATEGORY_TITLES = {category.category_id: category.title for category in CATEGORIES}

CHOICE_LABELS = {
    "default": "Default", "free": "Free", "turntable": "Turntable",
    "orbit": "Orbit", "fly": "Fly", "walk": "Walk", "object": "Object",
    "roll": "Roll", "zoom": "Zoom", "dolly": "Dolly", "none": "None",
    "3d": "3D", "pointer": "Pointer", "cube": "3D (legacy)", "cursor": "Pointer (legacy)",
    "camera": "Camera", "screen_center": "Screen center", "selection": "Selection",
    "cursor_3d": "3D cursor", "origin": "World origin",
    "to_center": "To center", "to_object": "To object", "to_cursor": "To cursor",
    "view": "View", "ground": "Ground",
    "off": "Off", "linear": "Linear", "smooth": "Smooth",
}
# Pivot names differ from the generic labels where the same word means something else.
PIVOT_LABELS = {
    "camera": "Camera", "screen_center": "Screen center", "cursor": "Under cursor",
    "selection": "Selection", "cursor_3d": "3D cursor", "object": "Model center",
    "origin": "World origin",
}


def free_orbit_needs_roll(style, twist_action, supports_roll=True):
    """Free orbit is three-axis only while twist rolls; anything else silently drops an axis."""
    return bool(supports_roll and style == "free" and twist_action != "roll")


def app_hints(snapshot, app_id):
    """Advice attached to a setting row, computed here so every UI shows the same thing."""
    app = APP_SPECS_BY_ID[app_id]
    hints = []
    style = snapshot.app_value(app_id, "navigation.orbit.style")
    if style == "default":
        style = snapshot.global_value("navigation.orbit.style")
    twist = snapshot.app_value(app_id, "navigation.orbit.twist_action")
    if free_orbit_needs_roll(style, twist, "roll" in app.capabilities):
        hints.append({
            "setting": "navigation.orbit.twist_action", "level": "warning",
            "text": "Free orbit only rotates on all three axes while twist is set to Roll.",
        })
    if "onshape_userscript" in app.capabilities and \
            snapshot.app_value(app_id, "navigation.orbit.pivot") == "cursor":
        hints.append({
            "setting": "navigation.orbit.pivot", "level": "info",
            "text": "Under-cursor orbit needs the Astrolabe userscript in your Onshape browser; "
                    "without a hit, the fallback order is used.",
            "action": "onshape.userscript",
        })
    return hints


@dataclass(frozen=True)
class GlobalSettingView:
    spec: object
    value: object
    system_value: object
    overridden: bool

    @property
    def source_text(self):
        return "User override" if self.overridden else "System default"


@dataclass(frozen=True)
class AppSettingView:
    spec: object
    app_id: str
    value: object
    system_value: object
    linked: bool
    global_compatible: bool
    choices: tuple

    @property
    def differs_from_system(self):
        return self.value != self.system_value

    @property
    def show_reset(self):
        return self.linked or self.differs_from_system


class SettingsUIModel:
    """Expose complete settings state without leaking mutable config dictionaries to Tk."""

    def __init__(self, store):
        self.store = store

    def global_views(self):
        snapshot = self.store.snapshot()
        views = []
        for spec in SETTING_SPECS:
            if spec.scope is SettingScope.DEVICE:
                value = snapshot.device_value(spec.id)
                system = SYSTEM_DEFAULTS.device_value(spec.id)
                overridden = spec.id in snapshot.device_override_ids
            else:
                value = snapshot.global_value(spec.id)
                system = SYSTEM_DEFAULTS.global_value(spec.id)
                overridden = spec.id in snapshot.global_override_ids
            views.append(GlobalSettingView(spec, value, system, overridden))
        return tuple(views)

    def app_views(self, app_id):
        snapshot = self.store.snapshot()
        app = APP_SPECS_BY_ID[app_id]
        views = []
        for spec in SETTING_SPECS:
            if not spec.applies_to(app):
                continue
            linked = spec.id not in snapshot.app_override_ids[app_id]
            value = snapshot.app_value(app_id, spec.id)
            system = SYSTEM_DEFAULTS.app_value(app_id, spec.id)
            choices = tuple(value for value in setting_choices_for_app(spec, app)
                            if value != "default")
            global_compatible = setting_value_valid_for_app(
                spec, app, snapshot.global_value(spec.id))
            views.append(AppSettingView(
                spec, app_id, value, system, linked, global_compatible, tuple(choices)))
        return tuple(views)

    def all_app_settings_linked(self, app_id):
        return not self.store.snapshot().app_override_ids[app_id]

    def set_global(self, setting_id, value):
        spec = next(spec for spec in SETTING_SPECS if spec.id == setting_id)
        if spec.scope is SettingScope.DEVICE:
            return self.store.transaction().set_device(setting_id, value).commit()
        if setting_id.startswith("input.axis_orientation.") and setting_id.endswith(".source"):
            return self._set_physical_axis_source(setting_id, value)
        return self.store.transaction().set_global(setting_id, value).commit()

    def reset_global(self, setting_id):
        spec = next(spec for spec in SETTING_SPECS if spec.id == setting_id)
        if setting_id.startswith("input.axis_orientation.") and setting_id.endswith(".source"):
            return self._reset_physical_axis_source(setting_id)
        tx = self.store.transaction()
        if spec.scope is SettingScope.DEVICE:
            tx.clear_device(setting_id)
        else:
            tx.clear_global(setting_id)
        return tx.commit()

    def reset_all_globals(self):
        return self.store.transaction().reset_all_globals().commit()

    def set_app(self, app_id, setting_id, value):
        return self.store.transaction().set_app(app_id, setting_id, value).commit()

    def toggle_app_link(self, app_id, setting_id):
        snapshot = self.store.snapshot()
        tx = self.store.transaction()
        if setting_id in snapshot.app_override_ids[app_id]:
            tx.link_app(app_id, setting_id)
        else:
            tx.set_app(app_id, setting_id, snapshot.app_value(app_id, setting_id))
        return tx.commit()

    def reset_app_setting(self, app_id, setting_id):
        return self.store.transaction().reset_app_setting_to_system(
            app_id, setting_id).commit()

    def toggle_all_app_links(self, app_id):
        tx = self.store.transaction()
        if self.all_app_settings_linked(app_id):
            tx.unlink_all_app(app_id)
        else:
            tx.link_all_app(app_id)
        return tx.commit()

    def reset_app(self, app_id):
        return self.store.transaction().reset_app_to_system(app_id).commit()

    def _set_physical_axis_source(self, setting_id, value):
        snapshot = self.store.snapshot()
        axis = setting_id.split(".")[-2]
        ids = {name: f"input.axis_orientation.{name}.source" for name in ("x", "y", "z")}
        current = {name: snapshot.global_value(identifier) for name, identifier in ids.items()}
        other = next(name for name, source in current.items() if source == value)
        with self.store.transaction() as tx:
            tx.set_global(ids[axis], value)
            tx.set_global(ids[other], current[axis])
        return None

    def _reset_physical_axis_source(self, setting_id):
        snapshot = self.store.snapshot()
        axis = setting_id.split(".")[-2]
        ids = {name: f"input.axis_orientation.{name}.source" for name in ("x", "y", "z")}
        current = {name: snapshot.global_value(identifier) for name, identifier in ids.items()}
        system_value = SYSTEM_DEFAULTS.global_value(setting_id)
        if current[axis] == system_value:
            return self.store.transaction().clear_global(setting_id).commit()
        other = next(name for name, source in current.items()
                     if source == system_value and name != axis)
        desired_other = current[axis]
        tx = self.store.transaction().clear_global(setting_id)
        if desired_other == SYSTEM_DEFAULTS.global_value(ids[other]):
            tx.clear_global(ids[other])
        else:
            tx.set_global(ids[other], desired_other)
        return tx.commit()
