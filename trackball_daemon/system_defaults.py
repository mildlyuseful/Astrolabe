"""Validated developer-owned defaults for the sparse v9 configuration model."""
from dataclasses import dataclass
import copy
import json
from pathlib import Path
from types import MappingProxyType

from .app_registry import APP_IDS, APP_SPECS_BY_ID
from .settings_schema import (
    SETTING_SPECS,
    SETTING_SPECS_BY_ID,
    SettingScope,
    setting_value_valid_for_app,
)


SYSTEM_DEFAULTS_PATH = Path(__file__).with_name("system_defaults.json")


def _freeze_value(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_value(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(child) for child in value)
    return copy.deepcopy(value)


def _freeze_mapping(values):
    return MappingProxyType({key: _freeze_value(value) for key, value in values.items()})


@dataclass(frozen=True)
class SystemDefaults:
    """Immutable validated System layer, keyed only by stable registry IDs."""

    global_values: object
    app_overrides: object
    device_values: object

    def global_value(self, setting_id):
        return copy.deepcopy(self.global_values[setting_id])

    def app_value(self, app_id, setting_id):
        overrides = self.app_overrides[app_id]
        return copy.deepcopy(overrides.get(setting_id, self.global_values[setting_id]))

    def device_value(self, setting_id):
        return copy.deepcopy(self.device_values[setting_id])


def load_system_defaults(path=SYSTEM_DEFAULTS_PATH):
    """Load the complete System layer and reject drift from either canonical registry."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load System defaults from {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema", "global", "apps", "device"}:
        raise ValueError("system_defaults.json must contain schema, global, apps, and device")
    if raw["schema"] != 1:
        raise ValueError("system_defaults.json must use schema 1")
    if not all(isinstance(raw[name], dict) for name in ("global", "apps", "device")):
        raise ValueError("System default sections must be objects")

    expected_global = {
        spec.setting_id for spec in SETTING_SPECS if spec.scope is not SettingScope.DEVICE
    }
    expected_device = {
        spec.setting_id for spec in SETTING_SPECS if spec.scope is SettingScope.DEVICE
    }
    if set(raw["global"]) != expected_global:
        missing = sorted(expected_global - set(raw["global"]))
        extra = sorted(set(raw["global"]) - expected_global)
        raise ValueError(f"System global suite mismatch; missing={missing}, extra={extra}")
    if set(raw["device"]) != expected_device:
        missing = sorted(expected_device - set(raw["device"]))
        extra = sorted(set(raw["device"]) - expected_device)
        raise ValueError(f"System device suite mismatch; missing={missing}, extra={extra}")
    if tuple(raw["apps"]) != APP_IDS:
        raise ValueError("System app suite or order mismatch")

    for setting_id, value in {**raw["global"], **raw["device"]}.items():
        if not SETTING_SPECS_BY_ID[setting_id].validates(value):
            raise ValueError(f"invalid System default for {setting_id}: {value!r}")
    frozen_apps = {}
    for app_id, overrides in raw["apps"].items():
        if not isinstance(overrides, dict):
            raise ValueError(f"System app defaults for {app_id} must be an object")
        app = APP_SPECS_BY_ID[app_id]
        for setting_id, value in overrides.items():
            spec = SETTING_SPECS_BY_ID.get(setting_id)
            if spec is None or not spec.applies_to(app):
                raise ValueError(f"{setting_id} does not apply to {app_id}")
            if not setting_value_valid_for_app(spec, app, value) or value == "default":
                raise ValueError(f"invalid System app default for {app_id}.{setting_id}: {value!r}")
        frozen_apps[app_id] = _freeze_mapping(overrides)

    return SystemDefaults(
        global_values=_freeze_mapping(raw["global"]),
        app_overrides=MappingProxyType(frozen_apps),
        device_values=_freeze_mapping(raw["device"]),
    )


SYSTEM_DEFAULTS = load_system_defaults()
