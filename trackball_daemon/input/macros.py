"""Validation primitives for the allowlisted declarative binding action language."""

from dataclasses import dataclass
import copy
import math
from types import MappingProxyType

from ..runtime_state import DEFAULT_DEPENDENCY_GRAPH
from ..settings_schema import (
    COMMAND_SPECS_BY_ID,
    SETTING_SPECS_BY_ID,
    SettingOperation,
    ValueKind,
)


_MISSING = object()
POINTER_BUTTONS = ("left", "right", "middle", "x1", "x2")
SETTING_ACTION_OPERATIONS = MappingProxyType({
    "setting.set_runtime": SettingOperation.RUNTIME_SET,
    "setting.toggle_runtime": SettingOperation.RUNTIME_TOGGLE,
    "setting.cycle_runtime": SettingOperation.RUNTIME_CYCLE,
    "setting.add_runtime": SettingOperation.RUNTIME_ADD,
    "setting.multiply_runtime": SettingOperation.RUNTIME_MULTIPLY,
    "setting.restore_previous": SettingOperation.RUNTIME_RESTORE,
    "setting.set_persistent": SettingOperation.MACRO_PERSIST,
})


@dataclass(frozen=True)
class DeclarativeAction:
    command_id: str
    target: str | None = None
    value: object = _MISSING

    @property
    def has_value(self):
        return self.value is not _MISSING


def _validate_setting_action(command_id, target, value):
    if not isinstance(target, str) or target not in SETTING_SPECS_BY_ID:
        raise ValueError(f"{command_id} requires a known setting target")
    spec = SETTING_SPECS_BY_ID[target]
    operation = SETTING_ACTION_OPERATIONS[command_id]
    if operation not in spec.operations:
        raise ValueError(f"{command_id} is not allowed for {target}")

    if command_id in {"setting.set_runtime", "setting.set_persistent"}:
        if value is _MISSING or not spec.validates(value):
            raise ValueError(f"{command_id} requires a valid value for {target}")
    elif command_id == "setting.toggle_runtime":
        if value is _MISSING:
            if spec.value_kind is not ValueKind.BOOLEAN:
                raise ValueError(f"{command_id} requires two explicit values for {target}")
        elif (not isinstance(value, (list, tuple)) or len(value) != 2 or
              value[0] == value[1] or not all(spec.validates(item) for item in value)):
            raise ValueError(f"{command_id} requires two distinct valid values for {target}")
    elif command_id == "setting.cycle_runtime":
        choices = spec.choices if value is _MISSING else value
        if (not isinstance(choices, (list, tuple)) or len(choices) < 2 or
                len(set(choices)) != len(choices) or
                not all(spec.validates(item) for item in choices)):
            raise ValueError(f"{command_id} requires at least two distinct values for {target}")
    elif command_id in {"setting.add_runtime", "setting.multiply_runtime"}:
        if (value is _MISSING or type(value) not in (int, float) or
                not math.isfinite(float(value))):
            raise ValueError(f"{command_id} requires a finite numeric value for {target}")
    elif value is not _MISSING:
        raise ValueError(f"{command_id} does not accept a value")


def parse_action(row):
    """Parse one data-only action and reject any executable or unregistered surface."""
    if not isinstance(row, dict):
        raise ValueError("binding actions must be objects")
    unknown = set(row) - {"command", "target", "value"}
    if unknown:
        raise ValueError(f"unknown binding action fields: {sorted(unknown)}")
    command_id = row.get("command")
    if not isinstance(command_id, str) or not command_id or command_id != command_id.strip():
        raise ValueError("binding action command must be non-empty trimmed text")
    target = row.get("target")
    value = copy.deepcopy(row["value"]) if "value" in row else _MISSING

    if command_id in SETTING_ACTION_OPERATIONS:
        _validate_setting_action(command_id, target, value)
    else:
        spec = COMMAND_SPECS_BY_ID.get(command_id)
        if spec is None:
            raise ValueError(f"binding action command is not allowlisted: {command_id}")
        if command_id in {"state.request", "state.release"}:
            if not isinstance(target, str):
                raise ValueError(f"{command_id} requires a state target")
            DEFAULT_DEPENDENCY_GRAPH.closure(target)
        elif spec.targets:
            if target not in spec.targets:
                raise ValueError(f"invalid {command_id} target: {target!r}")
        elif target is not None:
            raise ValueError(f"{command_id} does not accept a target")
        if value is not _MISSING:
            raise ValueError(f"{command_id} does not accept a value")

    return DeclarativeAction(command_id, target, value)


def parse_actions(rows):
    if not isinstance(rows, list):
        raise ValueError("binding action lists must be arrays")
    return tuple(parse_action(row) for row in rows)
