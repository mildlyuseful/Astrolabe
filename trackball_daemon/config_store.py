# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Transactional sparse config v9 store with immutable published snapshots."""
import copy
from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import threading
from types import MappingProxyType

from .app_registry import APP_IDS, APP_SPECS_BY_ID
from .config import (
    DEFAULTS,
    DEFAULT_ORBIT_PIVOT_FALLBACKS,
    LegacyConfig,
    _deep_merge,
    _mapping_shapes_match,
    default_app_profile,
    load_default_profiles,
    normalize_action_axis_sources,
    normalize_axis_inversions,
    normalize_axis_permutation,
    normalize_binding_axis_sources,
    normalize_orbit_pivot_fallbacks,
)
from .config_resolver import (
    resolve_all_for_app,
    resolve_all_globals,
    validate_override_maps,
)
from .paths import config_path
from .input.bindings import (
    SYSTEM_INPUT_PROFILE_IDS,
    validate_keybinding_override_suite,
)
from .settings_schema import (
    APP_INTERNAL_PROFILE_PATHS,
    SETTING_SPECS,
    SETTING_SPECS_BY_APP_PATH,
    SETTING_SPECS_BY_GLOBAL_PATH,
    SETTING_SPECS_BY_ID,
    SettingScope,
)
from .system_defaults import SYSTEM_DEFAULTS


CONFIG_VERSION = 9
DEPRECATED_V9_SETTING_IDS = frozenset({"navigation.legacy_layer_toggle"})
DEFAULT_INPUT_PROFILE = "astrolabe_5way"
INPUT_PROFILES = SYSTEM_INPUT_PROFILE_IDS
logger = logging.getLogger("trackball_daemon.config_store")
_FIXED_ONSHAPE_ENDPOINT = {
    key: DEFAULTS["onshape"][key] for key in ("address", "port")
}

APP_OPERATIONAL_FIELDS = ("enabled", "installed", "addin_version")
GLOBAL_INTERNAL_PATHS = {
    "pointer.cursor.x_source": ("general", "cursor", "x_src"),
    "pointer.cursor.x_sign": ("general", "cursor", "x_sign"),
    "pointer.cursor.y_source": ("general", "cursor", "y_src"),
    "pointer.cursor.y_sign": ("general", "cursor", "y_sign"),
    "pointer.scroll.source": ("general", "scroll", "src"),
    "pointer.scroll.sign": ("general", "scroll", "sign"),
}
APP_INTERNAL_PATH_IDS = {
    "navigation.orbit.axis_sign.x": ("bindings", "orbit", "axis_sign", 0),
    "navigation.orbit.axis_sign.y": ("bindings", "orbit", "axis_sign", 1),
    "navigation.orbit.axis_sign.z": ("bindings", "orbit", "axis_sign", 2),
    "navigation.pan.x_sign": ("bindings", "pan", "x_sign"),
    "navigation.pan.y_sign": ("bindings", "pan", "y_sign"),
    "navigation.zoom.sign": ("bindings", "zoom", "sign"),
    "navigation.zoom.deadzone": ("bindings", "zoom", "deadzone"),
    "navigation.zoom.distance_min": ("bindings", "zoom", "dist_min"),
    "navigation.zoom.distance_max": ("bindings", "zoom", "dist_max"),
    "navigation.zoom.distance_default": ("bindings", "zoom", "dist_default"),
}
assert set(APP_INTERNAL_PATH_IDS.values()) <= set(APP_INTERNAL_PROFILE_PATHS)


def _valid_internal_value(internal_id, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if internal_id.endswith("source"):
        return value in (0, 1, 2)
    if internal_id.endswith("sign") or ".axis_sign." in internal_id:
        return value in (-1, -1.0, 1, 1.0)
    if internal_id.endswith("deadzone"):
        return value >= 0
    return value > 0


def _repair_fixed_onshape_endpoint(state):
    onshape = state.get("onshape") if isinstance(state, dict) else None
    if not isinstance(onshape, dict):
        return frozenset()
    rejected = set()
    for key, expected in _FIXED_ONSHAPE_ENDPOINT.items():
        if key in onshape and onshape[key] != expected:
            onshape[key] = expected
            rejected.add(key)
            logger.error(
                "Rejected non-fixed Onshape %s; preserving source file", key)
    return frozenset(rejected)


def _get_path(root, path, default=None):
    value = root
    try:
        for part in path:
            value = value[part]
    except (KeyError, IndexError, TypeError):
        return default
    return value


def _set_path(root, path, value):
    node = root
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = copy.deepcopy(value)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return copy.deepcopy(value)


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(child) for child in value]
    return copy.deepcopy(value)


def _canonical(setting_id, value):
    if setting_id == "input.mode.default":
        return {"cube": "3d", "cursor": "pointer"}.get(value, value)
    return value


def _fresh_state():
    return {
        "version": CONFIG_VERSION,
        "global_overrides": {},
        "app_overrides": {app_id: {} for app_id in APP_IDS},
        "device_overrides": {},
        "internal_global_overrides": {},
        "internal_app_overrides": {app_id: {} for app_id in APP_IDS},
        "device_char_uuid": DEFAULTS["device"]["char_uuid"],
        "apps": {
            app_id: {"enabled": False, "installed": False, "addin_version": ""}
            for app_id in APP_IDS
        },
        "bridge": {"port": DEFAULTS["bridge"]["port"]},
        "onshape": copy.deepcopy(DEFAULTS["onshape"]),
        "ui_state": {"selected_app": "blender"},
        "input_profile": DEFAULT_INPUT_PROFILE,
        "keybinding_overrides": {profile: {} for profile in INPUT_PROFILES},
    }


def _remove_deprecated_v9_settings(state):
    """Drop settings whose runtime authority moved to declarative profile data."""
    changed = False
    for setting_id in DEPRECATED_V9_SETTING_IDS:
        if setting_id in state.get("global_overrides", {}):
            state["global_overrides"].pop(setting_id, None)
            changed = True
        for overrides in state.get("app_overrides", {}).values():
            if setting_id in overrides:
                overrides.pop(setting_id, None)
                changed = True
    return changed


def _repair_pointer_button_matches(state):
    """Let sparse System-binding click overrides inherit normal OS modifier behavior."""
    changed = False
    for overrides in state.get("keybinding_overrides", {}).values():
        for patch in overrides.values():
            if not isinstance(patch, dict) or "match" in patch:
                continue
            press = patch.get("press")
            if (isinstance(press, list) and
                    any(isinstance(action, dict) and
                        action.get("command") == "pointer.button.press"
                        for action in press)):
                patch["match"] = "allow_extra_modifiers"
                changed = True
    return changed


def _normalize_legacy(disk):
    """Run the established v1-v8 upgrades in memory without touching the source file."""
    raw_version = disk.get("version", 1) if isinstance(disk, dict) else None
    if (not isinstance(raw_version, int) or isinstance(raw_version, bool) or raw_version > 8 or
            not _mapping_shapes_match(DEFAULTS, disk)):
        raise ValueError("invalid legacy config shape or version")
    legacy = LegacyConfig()
    legacy.data = _deep_merge(DEFAULTS, disk)
    legacy._migrate(raw_version, disk)
    orientation = legacy.data["general"].get("axis_orientation") or {}
    legacy.data["general"]["axis_orientation"] = {
        "source": normalize_axis_permutation(orientation.get("source")),
        "invert": normalize_axis_inversions(orientation.get("invert")),
    }
    for app in legacy.data["apps"].values():
        app.pop("start_automatically", None)
        bindings = app.get("bindings") or {}
        normalize_binding_axis_sources(bindings)
        advanced = app.get("advanced")
        if isinstance(advanced, dict):
            advanced.pop("zoom_to_mouse", None)
            advanced["axis_source"] = normalize_action_axis_sources(advanced.get("axis_source"))
    raw_fallbacks = (disk.get("general") or {}).get(
        "orbit_pivot_fallbacks", list(DEFAULT_ORBIT_PIVOT_FALLBACKS))
    legacy.data["general"]["orbit_pivot_fallbacks"] = normalize_orbit_pivot_fallbacks(raw_fallbacks)
    return legacy.data


def _legacy_global_value(legacy, spec):
    return _canonical(spec.setting_id, _get_path(legacy, spec.global_path))


def _legacy_app_value(legacy, spec, app_id):
    raw = _get_path(legacy["apps"][app_id], spec.app_path)
    if spec.setting_id == "navigation.refresh_rate" and raw == 0:
        return _legacy_global_value(legacy, spec)
    if raw == "default" and spec.global_path:
        return _legacy_global_value(legacy, spec)
    if spec.setting_id == "navigation.level_horizon_on_entry" and raw is None:
        return _legacy_global_value(legacy, spec)
    return _canonical(spec.setting_id, raw)


def migrate_v8_to_v9(disk):
    """Convert materialized legacy data to sparse link/pin semantics in memory."""
    legacy = _normalize_legacy(copy.deepcopy(disk))
    state = _fresh_state()
    old_general, old_profiles = load_default_profiles()
    old_profiles = dict(old_profiles)

    for spec in SETTING_SPECS:
        if spec.scope is SettingScope.DEVICE or not spec.global_path:
            continue
        current = _legacy_global_value(legacy, spec)
        if spec.global_path[0] == "general":
            shipped_root = {"general": old_general}
            shipped = _canonical(spec.setting_id, _get_path(shipped_root, spec.global_path))
        else:
            shipped = _canonical(spec.setting_id, _get_path(DEFAULTS, spec.global_path))
        if current != shipped:
            state["global_overrides"][spec.setting_id] = current

    for spec in SETTING_SPECS:
        if spec.scope is not SettingScope.DEVICE:
            continue
        current = _get_path(legacy, spec.global_path)
        if current != SYSTEM_DEFAULTS.device_value(spec.setting_id):
            state["device_overrides"][spec.setting_id] = copy.deepcopy(current)

    global_inherited_ids = {
        "navigation.refresh_rate", "navigation.level_horizon_on_entry",
        "navigation.orbit.style", "navigation.orbit.pivot", "navigation.zoom.target",
    }
    for app_id in APP_IDS:
        app = APP_SPECS_BY_ID[app_id]
        for spec in SETTING_SPECS:
            if not spec.applies_to(app) or not spec.app_path:
                continue
            actual = _legacy_app_value(legacy, spec, app_id)
            if spec.setting_id in global_inherited_ids:
                inherited = _legacy_global_value(legacy, spec)
            else:
                shipped_legacy = {"apps": {app_id: old_profiles[app_id]},
                                  "general": old_general, "bridge": DEFAULTS["bridge"]}
                inherited = _legacy_app_value(shipped_legacy, spec, app_id)
            if actual != inherited:
                state["app_overrides"][app_id][spec.setting_id] = copy.deepcopy(actual)

        current_app = legacy["apps"][app_id]
        shipped_app = old_profiles[app_id]
        for internal_id, path in APP_INTERNAL_PATH_IDS.items():
            current = _get_path(current_app, path)
            shipped = _get_path(shipped_app, path)
            if current != shipped:
                state["internal_app_overrides"][app_id][internal_id] = copy.deepcopy(current)
        for field in APP_OPERATIONAL_FIELDS:
            state["apps"][app_id][field] = copy.deepcopy(current_app.get(field, state["apps"][app_id][field]))

    for internal_id, path in GLOBAL_INTERNAL_PATHS.items():
        current = _get_path(legacy, path)
        shipped = _get_path({"general": old_general}, path)
        if current != shipped:
            state["internal_global_overrides"][internal_id] = copy.deepcopy(current)

    state["device_char_uuid"] = legacy["device"].get(
        "char_uuid", state["device_char_uuid"])
    state["bridge"]["port"] = legacy["bridge"].get("port", state["bridge"]["port"])
    state["onshape"] = copy.deepcopy(legacy.get("onshape") or state["onshape"])
    selected = legacy.get("active_app")
    state["ui_state"]["selected_app"] = selected if selected in APP_IDS else "blender"
    validate_v9_state(state)
    return state


def validate_v9_state(state):
    expected = {
        "version", "global_overrides", "app_overrides", "device_overrides",
        "internal_global_overrides", "internal_app_overrides", "device_char_uuid", "apps",
        "bridge", "onshape", "ui_state", "input_profile", "keybinding_overrides",
    }
    if not isinstance(state, dict) or set(state) != expected or state.get("version") != CONFIG_VERSION:
        raise ValueError("config v9 top-level schema mismatch")
    validate_override_maps(state["global_overrides"], state["app_overrides"])
    if not isinstance(state["device_overrides"], dict):
        raise ValueError("device overrides must be an object")
    for setting_id, value in state["device_overrides"].items():
        spec = SETTING_SPECS_BY_ID.get(setting_id)
        if spec is None or spec.scope is not SettingScope.DEVICE or not spec.validates(value):
            raise ValueError(f"invalid device override for {setting_id}")
    if (not isinstance(state["internal_global_overrides"], dict) or
            not set(state["internal_global_overrides"]) <= set(GLOBAL_INTERNAL_PATHS)):
        raise ValueError("invalid internal Global overrides")
    if any(not _valid_internal_value(key, value)
           for key, value in state["internal_global_overrides"].items()):
        raise ValueError("invalid internal Global override value")
    if (not isinstance(state["internal_app_overrides"], dict) or
            set(state["internal_app_overrides"]) != set(APP_IDS)):
        raise ValueError("internal app override suite mismatch")
    for app_id, values in state["internal_app_overrides"].items():
        if not isinstance(values, dict) or not set(values) <= set(APP_INTERNAL_PATH_IDS):
            raise ValueError(f"invalid internal app overrides for {app_id}")
        if any(not _valid_internal_value(key, value) for key, value in values.items()):
            raise ValueError(f"invalid internal app override value for {app_id}")
    if not isinstance(state["device_char_uuid"], str) or not state["device_char_uuid"].strip():
        raise ValueError("device_char_uuid must be a non-empty string")
    if not isinstance(state["apps"], dict) or set(state["apps"]) != set(APP_IDS):
        raise ValueError("operational app suite mismatch")
    for app_id, values in state["apps"].items():
        if not isinstance(values, dict) or tuple(values) != APP_OPERATIONAL_FIELDS:
            raise ValueError(f"operational fields mismatch for {app_id}")
        if (type(values["enabled"]) is not bool or type(values["installed"]) is not bool or
                not isinstance(values["addin_version"], str)):
            raise ValueError(f"invalid operational values for {app_id}")
    if (not isinstance(state["bridge"], dict) or set(state["bridge"]) != {"port"} or
            type(state["bridge"]["port"]) is not int or
            not 1 <= state["bridge"]["port"] <= 65535):
        raise ValueError("invalid bridge settings")
    if (not isinstance(state["onshape"], dict) or
            set(state["onshape"]) != set(DEFAULTS["onshape"])):
        raise ValueError("invalid Onshape settings")
    for key, default in DEFAULTS["onshape"].items():
        if type(state["onshape"][key]) is not type(default):
            raise ValueError(f"invalid Onshape setting: {key}")
    for key, expected in _FIXED_ONSHAPE_ENDPOINT.items():
        if state["onshape"][key] != expected:
            raise ValueError(f"Onshape {key} must remain fixed at {expected}")
    if (not isinstance(state["ui_state"], dict) or set(state["ui_state"]) != {"selected_app"} or
            state["ui_state"].get("selected_app") not in APP_IDS):
        raise ValueError("invalid selected app")
    if state["input_profile"] not in INPUT_PROFILES:
        raise ValueError("invalid input profile")
    validate_keybinding_override_suite(state["keybinding_overrides"])
    resolved_globals = resolve_all_globals(state["global_overrides"])
    physical_sources = [
        resolved_globals[f"input.axis_orientation.{axis}.source"].value
        for axis in ("x", "y", "z")
    ]
    if sorted(physical_sources) != [0, 1, 2]:
        raise ValueError("physical axis sources must remain a permutation")


def _materialize_general(global_values, internal_overrides):
    general = copy.deepcopy(DEFAULTS["general"])
    general.pop("buttons", None)
    for spec in SETTING_SPECS:
        if spec.scope is not SettingScope.DEVICE and spec.global_path[:1] == ("general",):
            _set_path({"general": general}, spec.global_path, global_values[spec.setting_id])
    for internal_id, value in internal_overrides.items():
        _set_path({"general": general}, GLOBAL_INTERNAL_PATHS[internal_id], value)
    return general


def _materialize_app(app_id, values, internal_overrides):
    profile = default_app_profile(app_id)
    app = APP_SPECS_BY_ID[app_id]
    for spec in SETTING_SPECS:
        if spec.applies_to(app) and spec.app_path:
            _set_path(profile, spec.app_path, values[spec.setting_id])
    for internal_id, value in internal_overrides.items():
        _set_path(profile, APP_INTERNAL_PATH_IDS[internal_id], value)
    return profile


@dataclass(frozen=True)
class ConfigSnapshot:
    revision: int
    global_values: object
    global_override_ids: frozenset
    app_values: object
    app_override_ids: object
    device_values: object
    device_override_ids: frozenset
    device_char_uuid: str
    app_operational: object
    bridge_port: int
    onshape: object
    selected_app: str
    input_profile: str
    keybinding_overrides: object
    general_profile: object
    app_profiles: object

    def global_value(self, setting_id):
        return self.global_values[setting_id]

    def app_value(self, app_id, setting_id):
        values = self.app_values[app_id]
        if setting_id in values:
            return values[setting_id]
        return self.global_values[setting_id]

    def device_value(self, setting_id):
        return self.device_values[setting_id]

    def app_profile(self, app_id):
        return self.app_profiles[app_id]


@dataclass(frozen=True)
class ConfigChange:
    path: tuple
    before: object
    after: object


@dataclass(frozen=True)
class ConfigChangeEvent:
    revision: int
    changes: tuple
    snapshot: ConfigSnapshot


def _diff(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(set(before) | set(after)):
            if key not in before:
                changes.append(ConfigChange(path + (key,), None, copy.deepcopy(after[key])))
            elif key not in after:
                changes.append(ConfigChange(path + (key,), copy.deepcopy(before[key]), None))
            else:
                changes.extend(_diff(before[key], after[key], path + (key,)))
        return changes
    if before != after:
        return [ConfigChange(path, copy.deepcopy(before), copy.deepcopy(after))]
    return []


class ConfigTransaction:
    def __init__(self, store):
        self._store = store
        self._operations = []
        self._committed = False

    def set_global(self, setting_id, value):
        self._operations.append(("set_global", setting_id, copy.deepcopy(value)))
        return self

    def clear_global(self, setting_id):
        self._operations.append(("clear_global", setting_id))
        return self

    def reset_all_globals(self):
        self._operations.append(("reset_all_globals",))
        return self

    def set_app(self, app_id, setting_id, value):
        self._operations.append(("set_app", app_id, setting_id, copy.deepcopy(value)))
        return self

    def link_app(self, app_id, setting_id):
        self._operations.append(("link_app", app_id, setting_id))
        return self

    def link_all_app(self, app_id):
        self._operations.append(("link_all_app", app_id))
        return self

    def unlink_all_app(self, app_id):
        self._operations.append(("unlink_all_app", app_id))
        return self

    def reset_app_setting_to_system(self, app_id, setting_id):
        self._operations.append(("reset_app_setting", app_id, setting_id))
        return self

    def reset_app_to_system(self, app_id):
        self._operations.append(("reset_app", app_id))
        return self

    def set_device(self, setting_id, value):
        self._operations.append(("set_device", setting_id, copy.deepcopy(value)))
        return self

    def clear_device(self, setting_id):
        self._operations.append(("clear_device", setting_id))
        return self

    def set_app_operational(self, app_id, **values):
        self._operations.append(("set_app_operational", app_id, copy.deepcopy(values)))
        return self

    def set_selected_app(self, app_id):
        self._operations.append(("set_selected_app", app_id))
        return self

    def set_input_profile(self, profile_id):
        self._operations.append(("set_input_profile", profile_id))
        return self

    def set_keybinding_override(self, profile_id, binding_id, patch):
        self._operations.append((
            "set_keybinding_override", profile_id, binding_id, copy.deepcopy(patch)))
        return self

    def clear_keybinding_override(self, profile_id, binding_id):
        self._operations.append(("clear_keybinding_override", profile_id, binding_id))
        return self

    def set_bridge_port(self, port):
        self._operations.append(("set_bridge_port", port))
        return self

    def set_onshape(self, key, value):
        self._operations.append(("set_onshape", key, copy.deepcopy(value)))
        return self

    def commit(self):
        if self._committed:
            raise RuntimeError("transaction already committed")
        self._committed = True
        return self._store._commit(self._operations)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type is None:
            self.commit()


class ConfigStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else config_path()
        self.first_run = False
        self._lock = threading.RLock()
        self._listeners = []
        self._state = _fresh_state()
        self._revision = 0
        self._snapshot = self._build_snapshot()

    def _build_snapshot(self):
        globals_ = resolve_all_globals(self._state["global_overrides"])
        global_values = {key: item.value for key, item in globals_.items()}
        app_values = {}
        app_profiles = {}
        for app_id in APP_IDS:
            resolved = resolve_all_for_app(
                app_id, self._state["global_overrides"], self._state["app_overrides"][app_id])
            values = {key: item.value for key, item in resolved.items()}
            app_values[app_id] = values
            app_profiles[app_id] = _materialize_app(
                app_id, values, self._state["internal_app_overrides"][app_id])
        device_values = {
            setting_id: copy.deepcopy(self._state["device_overrides"].get(
                setting_id, SYSTEM_DEFAULTS.device_value(setting_id)))
            for setting_id in SYSTEM_DEFAULTS.device_values
        }
        general = _materialize_general(
            global_values, self._state["internal_global_overrides"])
        return ConfigSnapshot(
            revision=self._revision,
            global_values=_freeze(global_values),
            global_override_ids=frozenset(self._state["global_overrides"]),
            app_values=_freeze(app_values),
            app_override_ids=MappingProxyType({
                app_id: frozenset(values)
                for app_id, values in self._state["app_overrides"].items()
            }),
            device_values=_freeze(device_values),
            device_override_ids=frozenset(self._state["device_overrides"]),
            device_char_uuid=self._state["device_char_uuid"],
            app_operational=_freeze(self._state["apps"]),
            bridge_port=self._state["bridge"]["port"],
            onshape=_freeze(self._state["onshape"]),
            selected_app=self._state["ui_state"]["selected_app"],
            input_profile=self._state["input_profile"],
            keybinding_overrides=_freeze(self._state["keybinding_overrides"]),
            general_profile=_freeze(general),
            app_profiles=_freeze(app_profiles),
        )

    def load(self):
        with self._lock:
            if not self.path.exists():
                self.first_run = True
                self._state = _fresh_state()
                self._save_unlocked(self._state)
            else:
                try:
                    disk = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(disk, dict) and disk.get("version") == CONFIG_VERSION:
                        candidate = copy.deepcopy(disk)
                        cleaned = _remove_deprecated_v9_settings(candidate)
                        cleaned = _repair_pointer_button_matches(candidate) or cleaned
                        rejected_endpoint = _repair_fixed_onshape_endpoint(candidate)
                        validate_v9_state(candidate)
                        if cleaned and not rejected_endpoint:
                            self._save_unlocked(candidate)
                        self._state = candidate
                    else:
                        legacy = copy.deepcopy(disk)
                        rejected_endpoint = _repair_fixed_onshape_endpoint(legacy)
                        migrated = migrate_v8_to_v9(legacy)
                        if not rejected_endpoint:
                            self._save_unlocked(migrated)
                        self._state = migrated
                except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                    logger.error("Config load/migration failed; preserving source file: %s", exc)
                    self._state = _fresh_state()
            self._revision += 1
            self._snapshot = self._build_snapshot()
        return self

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def transaction(self):
        return ConfigTransaction(self)

    def ui_value(self, keys):
        """Compatibility path adapter for the existing settings UI."""
        keys = tuple(keys)
        snapshot = self.snapshot()
        if keys == ("active_app",):
            return snapshot.selected_app
        if keys[:1] == ("onshape",):
            return _get_path(snapshot.onshape, keys[1:])
        if keys == ("bridge", "port"):
            return snapshot.bridge_port
        if keys == ("bridge", "rate_hz"):
            return snapshot.global_value("navigation.refresh_rate")
        if keys[:1] == ("device",):
            setting_id = f"device.{keys[1]}"
            return snapshot.device_value(setting_id)
        if keys[:2] == ("general", "axis_orientation") and len(keys) == 3:
            field = keys[2]
            suffix = "source" if field == "source" else "invert"
            return [snapshot.global_value(f"input.axis_orientation.{axis}.{suffix}")
                    for axis in ("x", "y", "z")]
        global_spec = SETTING_SPECS_BY_GLOBAL_PATH.get(keys)
        if global_spec is not None:
            return snapshot.global_value(global_spec.setting_id)
        if keys[:1] == ("general",):
            return _get_path(snapshot.general_profile, keys[1:])
        if len(keys) >= 3 and keys[0] == "apps":
            app_id = keys[1]
            if len(keys) == 3 and keys[2] in APP_OPERATIONAL_FIELDS:
                return snapshot.app_operational[app_id][keys[2]]
            app_path = keys[2:]
            spec = SETTING_SPECS_BY_APP_PATH.get(app_path)
            if spec is not None and spec.applies_to(APP_SPECS_BY_ID[app_id]):
                with self._lock:
                    linked = spec.setting_id not in self._state["app_overrides"][app_id]
                if linked and spec.setting_id == "navigation.refresh_rate":
                    return 0
                if linked and spec.setting_id in {
                    "navigation.orbit.style", "navigation.orbit.pivot",
                    "navigation.zoom.target",
                }:
                    return "default"
                return snapshot.app_value(app_id, spec.setting_id)
            return _get_path(snapshot.app_profile(app_id), app_path)
        raise KeyError(keys)

    def set_ui_value(self, keys, value):
        """Map existing UI paths to typed v9 operations; never expose mutable config state."""
        keys = tuple(keys)
        if keys == ("active_app",):
            return self.set_selected_app(value)
        if keys[:1] == ("onshape",) and len(keys) == 2:
            return self.transaction().set_onshape(keys[1], value).commit()
        if keys == ("bridge", "port"):
            return self.transaction().set_bridge_port(value).commit()
        if keys == ("bridge", "rate_hz"):
            return self.set_global("navigation.refresh_rate", value)
        if keys[:1] == ("device",) and len(keys) == 2:
            return self.set_device(f"device.{keys[1]}", value)
        if keys[:2] == ("general", "axis_orientation") and len(keys) == 3:
            field = keys[2]
            suffix = "source" if field == "source" else "invert"
            with self.transaction() as tx:
                for axis, item in zip(("x", "y", "z"), value):
                    tx.set_global(f"input.axis_orientation.{axis}.{suffix}", item)
            return None
        global_spec = SETTING_SPECS_BY_GLOBAL_PATH.get(keys)
        if global_spec is not None:
            return self.set_global(global_spec.setting_id, _canonical(global_spec.setting_id, value))
        if len(keys) >= 3 and keys[0] == "apps":
            app_id = keys[1]
            if len(keys) == 3 and keys[2] in APP_OPERATIONAL_FIELDS:
                return self.set_app_operational(app_id, **{keys[2]: value})
            spec = SETTING_SPECS_BY_APP_PATH.get(keys[2:])
            if spec is not None and spec.applies_to(APP_SPECS_BY_ID[app_id]):
                if value == "default" or (spec.setting_id == "navigation.refresh_rate" and value == 0):
                    return self.link_app(app_id, spec.setting_id)
                return self.set_app(app_id, spec.setting_id, value)
        raise KeyError(keys)

    def reset_app_profile(self, app_id):
        """Compatibility spelling: the old Reset User Overrides action now links all settings."""
        return self.transaction().link_all_app(app_id).commit()

    def add_listener(self, listener):
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _apply(self, state, operation):
        kind = operation[0]
        if kind == "set_global":
            state["global_overrides"][operation[1]] = operation[2]
        elif kind == "clear_global":
            state["global_overrides"].pop(operation[1], None)
        elif kind == "reset_all_globals":
            state["global_overrides"] = {}
        elif kind == "set_app":
            state["app_overrides"][operation[1]][operation[2]] = operation[3]
        elif kind == "link_app":
            state["app_overrides"][operation[1]].pop(operation[2], None)
        elif kind == "link_all_app":
            state["app_overrides"][operation[1]] = {}
        elif kind == "unlink_all_app":
            app_id = operation[1]
            current = resolve_all_for_app(
                app_id, state["global_overrides"], state["app_overrides"][app_id])
            state["app_overrides"][app_id] = {
                setting_id: copy.deepcopy(item.value) for setting_id, item in current.items()
            }
        elif kind == "reset_app_setting":
            app_id, setting_id = operation[1:]
            state["app_overrides"][app_id][setting_id] = SYSTEM_DEFAULTS.app_value(
                app_id, setting_id)
        elif kind == "reset_app":
            app_id = operation[1]
            app = APP_SPECS_BY_ID[app_id]
            state["app_overrides"][app_id] = {
                spec.setting_id: SYSTEM_DEFAULTS.app_value(app_id, spec.setting_id)
                for spec in SETTING_SPECS if spec.applies_to(app)
            }
        elif kind == "set_device":
            state["device_overrides"][operation[1]] = operation[2]
        elif kind == "clear_device":
            state["device_overrides"].pop(operation[1], None)
        elif kind == "set_app_operational":
            app_id, values = operation[1:]
            unknown = set(values) - set(APP_OPERATIONAL_FIELDS)
            if unknown:
                raise ValueError(f"unknown operational fields: {sorted(unknown)}")
            state["apps"][app_id].update(values)
        elif kind == "set_selected_app":
            state["ui_state"]["selected_app"] = operation[1]
        elif kind == "set_input_profile":
            state["input_profile"] = operation[1]
        elif kind == "set_keybinding_override":
            profile_id, binding_id, patch = operation[1:]
            state["keybinding_overrides"][profile_id][binding_id] = patch
        elif kind == "clear_keybinding_override":
            profile_id, binding_id = operation[1:]
            state["keybinding_overrides"][profile_id].pop(binding_id, None)
        elif kind == "set_bridge_port":
            state["bridge"]["port"] = operation[1]
        elif kind == "set_onshape":
            key, value = operation[1:]
            if key not in state["onshape"]:
                raise ValueError(f"unknown Onshape setting: {key}")
            state["onshape"][key] = value
        else:
            raise ValueError(f"unknown config operation: {kind}")

    def _commit(self, operations):
        if not operations:
            return None
        with self._lock:
            before = copy.deepcopy(self._state)
            candidate = copy.deepcopy(self._state)
            for operation in operations:
                self._apply(candidate, operation)
            validate_v9_state(candidate)
            changes = tuple(_diff(before, candidate))
            if not changes:
                return None
            self._save_unlocked(candidate)
            self._state = candidate
            self._revision += 1
            self._snapshot = self._build_snapshot()
            event = ConfigChangeEvent(self._revision, changes, self._snapshot)
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Config subscriber failed: %r", listener)
        return event

    def _save_unlocked(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set_global(self, setting_id, value):
        return self.transaction().set_global(setting_id, value).commit()

    def clear_global(self, setting_id):
        return self.transaction().clear_global(setting_id).commit()

    def set_app(self, app_id, setting_id, value):
        return self.transaction().set_app(app_id, setting_id, value).commit()

    def link_app(self, app_id, setting_id):
        return self.transaction().link_app(app_id, setting_id).commit()

    def set_device(self, setting_id, value):
        return self.transaction().set_device(setting_id, value).commit()

    def set_app_operational(self, app_id, **values):
        return self.transaction().set_app_operational(app_id, **values).commit()

    def set_selected_app(self, app_id):
        return self.transaction().set_selected_app(app_id).commit()

    def set_input_profile(self, profile_id):
        return self.transaction().set_input_profile(profile_id).commit()

    def set_keybinding_override(self, profile_id, binding_id, patch):
        return self.transaction().set_keybinding_override(
            profile_id, binding_id, patch).commit()

    def clear_keybinding_override(self, profile_id, binding_id):
        return self.transaction().clear_keybinding_override(profile_id, binding_id).commit()


# Short compatibility spelling for imports while consumers move to the store boundary.
Config = ConfigStore
