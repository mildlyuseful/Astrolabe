# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Frozen v8 inputs and effective behavior at the Phase 2 migration boundary."""
import json
from pathlib import Path
import shutil

import pytest

from trackball_daemon.config import (LegacyConfig as Config, CONFIG_VERSION, DEFAULTS,
                                     effective_level_horizon)


FIXTURES = Path(__file__).with_name("fixtures")


def _load_fixture(config_dir, name):
    directory = config_dir
    source = FIXTURES / name
    target = directory / "config.json"
    shutil.copyfile(source, target)
    return Config().load(), source, target


def test_materialized_v8_fixture_freezes_every_inheritance_form_and_operational_state(
        config_dir):
    cfg, _source, _target = _load_fixture(config_dir, "config_v8_migration.json")

    assert CONFIG_VERSION == cfg.data["version"] == 8
    assert cfg.data["general"]["default_mode"] == "cube"
    assert cfg.data["active_app"] == "fusion360"

    blender = cfg.data["apps"]["blender"]
    assert blender["rate_hz"] == 0
    assert blender["bindings"]["scheme"] == {
        "orbit_pivot": "default", "orbit_style": "default", "zoom_mode": "default"}
    assert "level_horizon_on_entry" not in blender
    assert effective_level_horizon(cfg.data["general"], blender) is True
    assert blender["bindings"]["orbit"]["sensitivity"] == 1.0
    assert {key: blender[key] for key in ("enabled", "installed", "addin_version")} == {
        "enabled": True, "installed": True, "addin_version": "8.1.0-loaded"}

    fusion = cfg.data["apps"]["fusion360"]
    assert fusion["rate_hz"] == 75
    assert fusion["bindings"]["orbit"]["sensitivity"] == 1.75
    assert fusion["bindings"]["scheme"] == {
        "orbit_pivot": "object", "orbit_style": "turntable", "zoom_mode": "to_object"}
    assert fusion["level_horizon_on_entry"] is False
    assert {key: fusion[key] for key in ("enabled", "installed", "addin_version")} == {
        "enabled": False, "installed": True, "addin_version": "7.4.2"}


def test_v8_cursor_alias_and_selected_app_are_frozen(config_dir):
    cfg, _source, _target = _load_fixture(config_dir, "config_v8_cursor.json")
    assert cfg.data["general"]["default_mode"] == "cursor"
    assert cfg.data["active_app"] == "rhino"


@pytest.mark.parametrize("fixture", ["config_v8_malformed.json", "config_v8_invalid_shape.json"])
def test_bad_v8_fixture_falls_back_without_overwriting_source(config_dir, fixture):
    cfg, source, target = _load_fixture(config_dir, fixture)
    original = source.read_bytes()
    assert cfg.data == DEFAULTS
    assert target.read_bytes() == original
