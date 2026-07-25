# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Validation for the bundled AutoCAD plugin and its build-provenance manifest."""

import hashlib
import importlib.resources
import json
import re


_HEX_ID = re.compile(r"[0-9a-f]{40,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def validate_bundled_autocad_artifact(package_root=None):
    """Validate the exact DLL bytes and the provenance needed to attribute the build."""
    root = package_root or importlib.resources.files("trackball_daemon")
    plugin_dir = root.joinpath("plugins", "autocad")
    manifest = json.loads(plugin_dir.joinpath("version.json").read_text(encoding="utf-8"))
    dll = plugin_dir.joinpath("TrackballNavAcad.dll").read_bytes()

    if manifest.get("schema") != 1:
        raise ValueError("unsupported AutoCAD artifact manifest schema")
    version = manifest.get("version")
    source_revision = manifest.get("source_revision")
    source_tree = manifest.get("source_tree")
    dll_hash = manifest.get("dll_sha256")
    if not isinstance(version, str) or not version:
        raise ValueError("AutoCAD artifact version is missing")
    if not isinstance(source_revision, str) or not _HEX_ID.fullmatch(source_revision):
        raise ValueError("AutoCAD artifact source revision is invalid")
    if not isinstance(source_tree, str) or not _HEX_ID.fullmatch(source_tree):
        raise ValueError("AutoCAD artifact source tree is invalid")
    if not isinstance(dll_hash, str) or not _SHA256.fullmatch(dll_hash):
        raise ValueError("AutoCAD artifact DLL hash is invalid")
    if hashlib.sha256(dll).hexdigest() != dll_hash:
        raise ValueError("bundled AutoCAD DLL does not match its provenance manifest")
    if manifest.get("dll_size") != len(dll):
        raise ValueError("bundled AutoCAD DLL size does not match its provenance manifest")
    if manifest.get("assembly_version") != f"{version}.0":
        raise ValueError("AutoCAD assembly version does not match its release version")
    if manifest.get("informational_version") != f"{version}+{source_revision}":
        raise ValueError("AutoCAD informational version does not identify its source revision")
    if manifest.get("target_framework") != "net8.0-windows" or manifest.get("platform") != "x64":
        raise ValueError("unexpected AutoCAD plugin build target")
    if not str(manifest.get("dotnet_sdk", "")).strip():
        raise ValueError("AutoCAD .NET SDK provenance is missing")
    if not str(manifest.get("autocad_reference_family", "")).strip():
        raise ValueError("AutoCAD reference-family provenance is missing")
    build = manifest.get("build_properties")
    if not isinstance(build, dict) or build.get("configuration") != "Release" or not (
            build.get("continuous_integration_build") and build.get("deterministic")):
        raise ValueError("AutoCAD build properties are incomplete")

    references = manifest.get("autocad_references")
    if not isinstance(references, list) or not references:
        raise ValueError("AutoCAD reference provenance is incomplete")
    reference_names = []
    for reference in references:
        if not isinstance(reference, dict):
            raise ValueError("AutoCAD reference provenance is incomplete")
        name = reference.get("name")
        if not isinstance(name, str) or not name.lower().endswith(".dll"):
            raise ValueError(f"invalid AutoCAD reference name: {name}")
        reference_names.append(name.casefold())
        if not _SHA256.fullmatch(str(reference.get("sha256", ""))):
            raise ValueError(f"invalid AutoCAD reference hash: {name}")
        if not str(reference.get("file_version", "")).strip():
            raise ValueError(f"missing AutoCAD reference version: {name}")
    if len(reference_names) != len(set(reference_names)):
        raise ValueError("AutoCAD reference provenance contains duplicate names")

    if (source_revision.encode("utf-8") not in dll and
            source_revision.encode("utf-16le") not in dll):
        raise ValueError("bundled AutoCAD DLL is missing its source revision marker")
    return manifest
