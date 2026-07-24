# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Machine-readable contributor contracts and packaged release-resource checks."""

import importlib.resources
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from trackball_daemon.devices import load_device_descriptor
from trackball_daemon.input.bindings import load_system_binding_profiles
from trackball_daemon.input.macros import parse_actions


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "trackball_daemon"
SCHEMAS = PACKAGE / "schemas"
EXAMPLES = PACKAGE / "examples"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(instance, schema_name):
    schema = _json(SCHEMAS / schema_name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)


def test_release_schemas_are_valid_draft_2020_12_and_examples_conform():
    catalog = _json(EXAMPLES / "profile-catalog.example.json")
    actions = _json(EXAMPLES / "declarative-actions.example.json")
    descriptor = _json(EXAMPLES / "device-descriptor.example.json")

    _validate(catalog, "binding-profile-catalog-v1.schema.json")
    _validate(actions, "declarative-actions-v1.schema.json")
    _validate(descriptor, "device-descriptor-v1.schema.json")


def test_profile_and_standalone_macro_schemas_share_the_same_action_contract():
    profile = _json(SCHEMAS / "binding-profile-catalog-v1.schema.json")
    actions = _json(SCHEMAS / "declarative-actions-v1.schema.json")
    assert profile["$defs"]["action"] == actions["$defs"]["action"]


def test_examples_also_pass_authoritative_runtime_validation():
    catalog = load_system_binding_profiles(EXAMPLES / "profile-catalog.example.json")
    assert tuple(catalog.profiles) == ("astrolabe_5way", "keyboard_only")
    actions = parse_actions(_json(EXAMPLES / "declarative-actions.example.json"))
    assert actions[0].target == "navigation.orbit.sensitivity"
    descriptor = load_device_descriptor(EXAMPLES / "device-descriptor.example.json")
    assert descriptor.source_id == "ble.community"
    assert [control.control_id for control in descriptor.controls] == [
        "button.primary", "button.secondary"]


def test_shipped_profile_and_device_data_conform_to_public_schemas():
    _validate(
        _json(PACKAGE / "system_keybinding_profiles.json"),
        "binding-profile-catalog-v1.schema.json",
    )
    for path in sorted((PACKAGE / "devices" / "descriptor_data").glob("*.json")):
        _validate(_json(path), "device-descriptor-v1.schema.json")


def test_release_contracts_are_declared_and_readable_as_package_data():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"schemas/*.schema.json"' in pyproject
    assert '"examples/*.example.json"' in pyproject

    package_root = importlib.resources.files("trackball_daemon")
    expected = {
        "schemas/binding-profile-catalog-v1.schema.json",
        "schemas/declarative-actions-v1.schema.json",
        "schemas/device-descriptor-v1.schema.json",
        "examples/profile-catalog.example.json",
        "examples/declarative-actions.example.json",
        "examples/device-descriptor.example.json",
    }
    for relative in expected:
        resource = package_root.joinpath(*relative.split("/"))
        assert resource.is_file(), relative
        assert resource.read_text(encoding="utf-8").strip().startswith(("{", "["))

    autocad = package_root.joinpath("plugins", "autocad")
    assert autocad.joinpath("TrackballNavAcad.dll").read_bytes().startswith(b"MZ")
    assert json.loads(autocad.joinpath("version.json").read_text(encoding="utf-8"))["schema"] == 1
