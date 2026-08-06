# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Validated data-only device descriptor loading."""

import importlib.resources
import json
from pathlib import Path

from .model import DeviceControl, DeviceDescriptor, UsbHidMatch


def _parse_descriptor(data):
    if not isinstance(data, dict):
        raise ValueError("device descriptor root must be an object")
    required = {
        "schema_version", "device_id", "source_id", "label", "match",
        "service_uuid", "motion_characteristic", "input_characteristic", "controls",
    }
    unknown = set(data) - required - {"metadata", "keepalive_characteristic"}
    missing = required - set(data)
    if missing or unknown:
        raise ValueError(
            f"device descriptor keys invalid; missing={sorted(missing)}, unknown={sorted(unknown)}")
    match = data["match"]
    if not isinstance(match, dict):
        raise ValueError("descriptor match must be an object")
    unknown_match = set(match) - {"advertised_names", "usb_hid"}
    if "advertised_names" not in match or unknown_match:
        raise ValueError(
            "descriptor match must contain advertised_names and optional usb_hid")
    usb_hid = match.get("usb_hid")
    parsed_usb_hid = None
    if usb_hid is not None:
        required_usb = {"vendor_id", "product_id", "usage_page", "usage", "product"}
        if not isinstance(usb_hid, dict) or set(usb_hid) != required_usb:
            raise ValueError("descriptor USB HID match keys are invalid")
        parsed_usb_hid = UsbHidMatch(**usb_hid)
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
        schema_version=data["schema_version"],
        device_id=data["device_id"],
        source_id=data["source_id"],
        label=data["label"],
        advertised_names=tuple(match["advertised_names"]),
        service_uuid=data["service_uuid"],
        motion_characteristic=data["motion_characteristic"],
        input_characteristic=data["input_characteristic"],
        controls=tuple(parsed_controls),
        metadata=data.get("metadata", {}),
        usb_hid=parsed_usb_hid,
        keepalive_characteristic=data.get("keepalive_characteristic"),
    )


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
