# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Validated data-only device descriptor loading."""

import importlib.resources
import json
from pathlib import Path

from .model import DeviceControl, DeviceDescriptor


def _parse_descriptor(data):
    if not isinstance(data, dict):
        raise ValueError("device descriptor root must be an object")
    required = {
        "schema_version", "device_id", "source_id", "label", "match",
        "service_uuid", "motion_characteristic", "input_characteristic", "controls",
    }
    unknown = set(data) - required - {"metadata"}
    missing = required - set(data)
    if missing or unknown:
        raise ValueError(
            f"device descriptor keys invalid; missing={sorted(missing)}, unknown={sorted(unknown)}")
    match = data["match"]
    if not isinstance(match, dict) or set(match) != {"advertised_names"}:
        raise ValueError("descriptor match must contain only advertised_names")
    controls = data["controls"]
    if not isinstance(controls, list):
        raise ValueError("descriptor controls must be a list")
    parsed_controls = []
    for row in controls:
        if not isinstance(row, dict):
            raise ValueError("descriptor control entries must be objects")
        unknown_control = set(row) - {"id", "label", "bit", "kind", "metadata"}
        missing_control = {"id", "label", "bit"} - set(row)
        if missing_control or unknown_control:
            raise ValueError("descriptor control keys are invalid")
        parsed_controls.append(DeviceControl(
            row["id"], row["label"], row["bit"], row.get("kind", "switch"),
            row.get("metadata", {})))
    return DeviceDescriptor(
        data["schema_version"], data["device_id"], data["source_id"], data["label"],
        tuple(match["advertised_names"]), data["service_uuid"],
        data["motion_characteristic"], data["input_characteristic"],
        tuple(parsed_controls), data.get("metadata", {}))


def load_device_descriptor(source):
    """Load one JSON descriptor from a path or package traversable."""
    if isinstance(source, (str, Path)):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = source.read_text(encoding="utf-8")
    return _parse_descriptor(json.loads(text))


def builtin_device_descriptors():
    root = importlib.resources.files("trackball_daemon.devices").joinpath("descriptor_data")
    descriptors = tuple(load_device_descriptor(item) for item in root.iterdir()
                        if item.name.endswith(".json"))
    if len({item.device_id for item in descriptors}) != len(descriptors):
        raise ValueError("built-in device descriptor IDs must be unique")
    if len({item.source_id for item in descriptors}) != len(descriptors):
        raise ValueError("built-in device descriptor source IDs must be unique")
    return tuple(sorted(descriptors, key=lambda item: item.device_id))
