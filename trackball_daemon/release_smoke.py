"""Side-effect-free packaged-resource smoke used by release verification."""

import importlib.resources
import json

from .autocad_artifact import validate_bundled_autocad_artifact
from .devices import builtin_device_descriptors
from .input.bindings import (
    compile_binding_profile,
    load_system_binding_profiles,
)


_ROOT_JSON = (
    "default_profiles.json",
    "host_profiles.json",
    "system_defaults.json",
    "system_keybinding_profiles.json",
)
_SCHEMAS = (
    "binding-profile-catalog-v1.schema.json",
    "declarative-actions-v1.schema.json",
    "device-descriptor-v1.schema.json",
)
_EXAMPLES = (
    "profile-catalog.example.json",
    "declarative-actions.example.json",
    "device-descriptor.example.json",
)


def _read_json(resource):
    return json.loads(resource.read_text(encoding="utf-8"))


def run_release_smoke():
    """Load every release contract without starting transports or writing user state."""
    root = importlib.resources.files("trackball_daemon")
    for name in _ROOT_JSON:
        _read_json(root.joinpath(name))
    for name in _SCHEMAS:
        data = _read_json(root.joinpath("schemas", name))
        if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError(f"unexpected schema dialect: {name}")
    for name in _EXAMPLES:
        _read_json(root.joinpath("examples", name))

    autocad = validate_bundled_autocad_artifact(root)
    catalog = load_system_binding_profiles()
    compiled = {
        profile_id: len(compile_binding_profile(profile, catalog).bindings)
        for profile_id, profile in catalog.profiles.items()
    }
    devices = [descriptor.device_id for descriptor in builtin_device_descriptors()]
    return {
        "status": "ok",
        "profiles": compiled,
        "devices": devices,
        "schemas": list(_SCHEMAS),
        "examples": list(_EXAMPLES),
        "autocad_plugin": {
            "version": autocad["version"],
            "sha256": autocad["dll_sha256"],
        },
    }
