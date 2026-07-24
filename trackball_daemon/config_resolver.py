# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Pure System -> Global -> app setting resolution for config v9."""
import copy
from dataclasses import dataclass
from enum import Enum

from .app_registry import APP_IDS, APP_SPECS_BY_ID
from .settings_schema import (
    SETTING_SPECS,
    SETTING_SPECS_BY_ID,
    SettingScope,
    setting_value_valid_for_app,
)
from .system_defaults import SYSTEM_DEFAULTS


class ResolutionLayer(str, Enum):
    SYSTEM = "system"
    SYSTEM_APP = "system_app"
    GLOBAL = "global"
    APP = "app"


@dataclass(frozen=True)
class ResolvedSetting:
    setting_id: str
    value: object
    layer: ResolutionLayer
    app_id: str = None

    @property
    def follows_global(self):
        return self.app_id is not None and self.layer is not ResolutionLayer.APP


def _is_canonical_value(setting_id, value):
    if value == "default":
        return False
    if setting_id == "navigation.refresh_rate" and value == 0:
        return False
    if setting_id == "input.mode.default" and value in {"cube", "cursor"}:
        return False
    return True


def validate_override_maps(global_overrides, app_overrides):
    """Validate sparse v9 user layers without interpreting absence as a value."""
    if not isinstance(global_overrides, dict) or not isinstance(app_overrides, dict):
        raise ValueError("Global and app override maps must be objects")
    if set(app_overrides) != set(APP_IDS):
        raise ValueError("app override suite mismatch")
    for setting_id, value in global_overrides.items():
        spec = SETTING_SPECS_BY_ID.get(setting_id)
        if spec is None or spec.scope is SettingScope.DEVICE:
            raise ValueError(f"invalid Global override setting: {setting_id}")
        if not spec.validates(value) or not _is_canonical_value(setting_id, value):
            raise ValueError(f"invalid Global override value for {setting_id}: {value!r}")
    for app_id, overrides in app_overrides.items():
        if not isinstance(overrides, dict):
            raise ValueError(f"app overrides for {app_id} must be an object")
        app = APP_SPECS_BY_ID[app_id]
        for setting_id, value in overrides.items():
            spec = SETTING_SPECS_BY_ID.get(setting_id)
            if (spec is None or not _is_canonical_value(setting_id, value) or
                    not setting_value_valid_for_app(spec, app, value)):
                raise ValueError(f"invalid app override for {app_id}.{setting_id}: {value!r}")


def resolve_global(setting_id, global_overrides, *, system_defaults=SYSTEM_DEFAULTS):
    """Resolve one Global value. Device identity uses its separate System/User path."""
    spec = SETTING_SPECS_BY_ID.get(setting_id)
    if spec is None:
        raise KeyError(setting_id)
    if spec.scope is SettingScope.DEVICE:
        raise ValueError(f"{setting_id} is a device setting, not a Global setting")
    if setting_id in global_overrides:
        return ResolvedSetting(
            setting_id, copy.deepcopy(global_overrides[setting_id]), ResolutionLayer.GLOBAL)
    return ResolvedSetting(setting_id, system_defaults.global_value(setting_id), ResolutionLayer.SYSTEM)


def resolve_app(setting_id, app_id, global_overrides, app_overrides, *,
                system_defaults=SYSTEM_DEFAULTS):
    """Resolve app > Global > app System > global System for one applicable setting.

    A Global value that the host cannot represent is skipped in favor of its app-specific System
    value. The app still has no override and therefore remains linked; a later compatible Global
    value takes effect immediately.
    """
    spec = SETTING_SPECS_BY_ID.get(setting_id)
    if spec is None:
        raise KeyError(setting_id)
    app = APP_SPECS_BY_ID.get(app_id)
    if app is None:
        raise KeyError(app_id)
    if not spec.applies_to(app):
        raise ValueError(f"{setting_id} does not apply to {app_id}")
    explicit = app_overrides.get(setting_id)
    if setting_id in app_overrides:
        return ResolvedSetting(
            setting_id, copy.deepcopy(explicit), ResolutionLayer.APP, app_id)
    if setting_id in global_overrides and setting_value_valid_for_app(
            spec, app, global_overrides[setting_id]):
        return ResolvedSetting(
            setting_id, copy.deepcopy(global_overrides[setting_id]), ResolutionLayer.GLOBAL, app_id)
    if setting_id in system_defaults.app_overrides[app_id]:
        return ResolvedSetting(
            setting_id, system_defaults.app_value(app_id, setting_id),
            ResolutionLayer.SYSTEM_APP, app_id)
    return ResolvedSetting(
        setting_id, system_defaults.global_value(setting_id), ResolutionLayer.SYSTEM, app_id)


def resolve_all_globals(global_overrides, *, system_defaults=SYSTEM_DEFAULTS):
    return {
        spec.setting_id: resolve_global(
            spec.setting_id, global_overrides, system_defaults=system_defaults)
        for spec in SETTING_SPECS if spec.scope is not SettingScope.DEVICE
    }


def resolve_all_for_app(app_id, global_overrides, app_overrides, *,
                        system_defaults=SYSTEM_DEFAULTS):
    app = APP_SPECS_BY_ID[app_id]
    return {
        spec.setting_id: resolve_app(
            spec.setting_id, app_id, global_overrides, app_overrides,
            system_defaults=system_defaults)
        for spec in SETTING_SPECS if spec.applies_to(app)
    }
