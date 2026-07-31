# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Developer-owned System Defaults are complete, concrete, and package-safe."""
import copy
import json
from pathlib import Path

import pytest

from trackball_daemon.app_registry import APP_IDS
from trackball_daemon.settings_schema import SETTING_SPECS, SettingScope
from trackball_daemon.system_defaults import (
    SYSTEM_DEFAULTS,
    SYSTEM_DEFAULTS_PATH,
    load_system_defaults,
)


def _write(tmp_path, raw):
    path = tmp_path / "system_defaults.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_system_defaults_cover_every_setting_and_app_and_are_packaged():
    expected_global = {
        spec.setting_id for spec in SETTING_SPECS if spec.scope is not SettingScope.DEVICE
    }
    expected_device = {
        spec.setting_id for spec in SETTING_SPECS if spec.scope is SettingScope.DEVICE
    }
    assert set(SYSTEM_DEFAULTS.global_values) == expected_global
    assert set(SYSTEM_DEFAULTS.device_values) == expected_device
    assert tuple(SYSTEM_DEFAULTS.app_overrides) == APP_IDS

    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"system_defaults.json"' in pyproject


def test_system_defaults_use_concrete_canonical_values():
    assert SYSTEM_DEFAULTS.global_value("input.mode.default") == "3d"
    assert SYSTEM_DEFAULTS.global_value("navigation.refresh_rate") == 30
    assert SYSTEM_DEFAULTS.global_value("navigation.object.translation_sensitivity") == 1.0
    for app_id, values in SYSTEM_DEFAULTS.app_overrides.items():
        assert 0 not in values.values(), app_id
        assert "default" not in values.values(), app_id


def test_app_system_defaults_fall_back_to_global_without_mutable_aliases():
    assert SYSTEM_DEFAULTS.app_value("blender", "navigation.orbit.pivot") == "camera"
    assert SYSTEM_DEFAULTS.app_value("freecad", "navigation.orbit.pivot") == "screen_center"
    fallbacks = SYSTEM_DEFAULTS.global_value("navigation.orbit.pivot_fallbacks")
    assert fallbacks == ("cursor_3d", "camera", "object", "origin")
    with pytest.raises(TypeError):
        SYSTEM_DEFAULTS.global_values["new"] = True


def test_loader_rejects_registry_drift_and_invalid_or_legacy_values(tmp_path):
    raw = json.loads(SYSTEM_DEFAULTS_PATH.read_text(encoding="utf-8"))
    missing = copy.deepcopy(raw)
    missing["global"].pop("pointer.cursor.gain")
    with pytest.raises(ValueError, match="global suite mismatch"):
        load_system_defaults(_write(tmp_path, missing))

    invalid = copy.deepcopy(raw)
    invalid["global"]["navigation.refresh_rate"] = "fast"
    with pytest.raises(ValueError, match="invalid System default"):
        load_system_defaults(_write(tmp_path, invalid))

    sentinel = copy.deepcopy(raw)
    sentinel["apps"]["blender"]["navigation.orbit.pivot"] = "default"
    with pytest.raises(ValueError, match="invalid System app default"):
        load_system_defaults(_write(tmp_path, sentinel))

    unknown = copy.deepcopy(raw)
    unknown["apps"]["blender"]["pointer.cursor.gain"] = 12
    with pytest.raises(ValueError, match="does not apply"):
        load_system_defaults(_write(tmp_path, unknown))
