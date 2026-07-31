# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Side-effect-free packaged-resource smoke used by release verification."""

import importlib
import importlib.resources
import json
import sys

from .app_registry import APP_IDS_BY_TIER
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
# Every payload that setup copies outside the application tree. Each carries its own licence and
# notice so an add-on sitting in a host's folders, detached from this distribution, still says what
# it is and under what terms.
_PAYLOAD_DIRECTORIES = (
    ("autocad",),
    ("blender", "trackball_nav"),
    ("freecad", "TrackballNav"),
    ("fusion360", "TrackballNav"),
    ("rhino", "TrackballNav"),
    ("sketchup", "trackball_nav"),
    ("unity", "com.astrolabe.trackball-nav"),
    ("unreal", "TrackballNav"),
)
_PAYLOAD_NOTICES = ("LICENSE", "NOTICE")


def _read_json(resource):
    return json.loads(resource.read_text(encoding="utf-8"))


def run_release_smoke():
    """Load every release contract without starting transports or writing user state."""
    runtime_imports = []
    if sys.platform == "win32":
        # PyWinRT materializes projected collection types through a dynamic import when the first
        # BLE advertisement arrives. Static freezer analysis cannot see that edge.
        module = "winrt.windows.foundation.collections"
        importlib.import_module(module)
        runtime_imports.append(module)

    root = importlib.resources.files("trackball_daemon")
    for name in _ROOT_JSON:
        _read_json(root.joinpath(name))
    for name in _SCHEMAS:
        data = _read_json(root.joinpath("schemas", name))
        if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError(f"unexpected schema dialect: {name}")
    for name in _EXAMPLES:
        _read_json(root.joinpath("examples", name))

    payload_notices = 0
    for parts in _PAYLOAD_DIRECTORIES:
        for name in _PAYLOAD_NOTICES:
            resource = root.joinpath("plugins", *parts, name)
            if not resource.is_file() or not resource.read_text(encoding="utf-8").strip():
                raise ValueError(
                    f"payload {'/'.join(parts)} is missing its {name}; setup would copy it into a "
                    "host application without one")
            payload_notices += 1

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
        "runtime_imports": runtime_imports,
        # What this build commits to per integration, from the registry rather than a release note.
        # The machine-readable release manifest will project the same mapping.
        "support_tiers": {tier.value: list(app_ids) for tier, app_ids in APP_IDS_BY_TIER.items()},
        "payload_notices": payload_notices,
        "autocad_plugin": {
            "version": autocad["version"],
            "sha256": autocad["dll_sha256"],
        },
    }
