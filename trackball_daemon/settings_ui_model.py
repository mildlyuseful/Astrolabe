"""UI-independent projections and typed settings actions for the barebones settings UI."""

from dataclasses import dataclass

from .app_registry import APP_SPECS_BY_ID
from .settings_schema import (
    SETTING_SPECS,
    SettingScope,
    setting_choices_for_app,
)
from .system_defaults import SYSTEM_DEFAULTS


CATEGORY_TITLES = {
    "device": "Device",
    "input": "Input",
    "pointer": "Pointer",
    "orbit": "Orbit",
    "sensitivity": "Sensitivity & rate",
    "pan_zoom": "Pan / Zoom",
    "navigation": "Navigation",
    "camera": "Camera",
    "routing": "Axis routing",
}


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
            global_compatible = not choices or snapshot.global_value(spec.id) in choices
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
