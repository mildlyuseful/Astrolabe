"""Data-only BLE device descriptors and normalized motion values."""

import json
import struct

import pytest

from trackball_daemon.devices import (
    BleConnectionConfig,
    MotionSample,
    builtin_device_descriptors,
    load_device_descriptor,
)


def test_builtin_descriptors_have_stable_distinct_control_namespaces():
    descriptors = {item.device_id: item for item in builtin_device_descriptors()}

    astrolabe = descriptors["astrolabe_5way"]
    assert [item.control_id for item in astrolabe.controls] == [
        "fiveway.up", "fiveway.down", "fiveway.left", "fiveway.right", "fiveway.center"]
    assert [item.bit for item in astrolabe.controls] == [0, 1, 2, 3, 4]
    assert [item.token for item in astrolabe.input_descriptors] == [
        "ble.astrolabe:fiveway.up",
        "ble.astrolabe:fiveway.down",
        "ble.astrolabe:fiveway.left",
        "ble.astrolabe:fiveway.right",
        "ble.astrolabe:fiveway.center",
    ]
    assert astrolabe.metadata["electrical"] == "active_low_internal_pullup"
    assert astrolabe.metadata["debounce"] == "firmware_defined"
    assert astrolabe.metadata["simultaneous_controls"] == \
        "mechanically_exclusive_not_enforced"

    bench = descriptors["xiao3389_3button"]
    assert bench.matches_name("TRACKBALL BLE")
    assert [item.control_id for item in bench.controls] == [
        "button.left", "button.right", "button.middle"]
    assert astrolabe.source_id != bench.source_id


def test_descriptor_loader_rejects_code_or_unknown_schema_fields(tmp_path):
    source = {
        "schema_version": 1,
        "device_id": "unsafe",
        "source_id": "ble.unsafe",
        "label": "Unsafe",
        "match": {"advertised_names": ["Unsafe"]},
        "service_uuid": "2cad0001-6e64-0146-b139-9cf2a4cd57fc",
        "motion_characteristic": "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
        "input_characteristic": None,
        "controls": [],
        "python_module": "arbitrary.code",
    }
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown=.*python_module"):
        load_device_descriptor(path)


def test_motion_sample_preserves_exact_rotation_payload_and_rejects_bad_values():
    payload = struct.pack("<fff", 0.25, -1.5, 3.0)
    sample = MotionSample("ble.astrolabe.motion", payload, timestamp=2.0)
    assert sample.payload == payload
    assert sample.rotation == pytest.approx((0.25, -1.5, 3.0))

    with pytest.raises(ValueError, match="exactly 12"):
        MotionSample("ble.astrolabe.motion", b"short")
    with pytest.raises(ValueError, match="finite"):
        MotionSample("ble.astrolabe.motion", struct.pack("<fff", float("nan"), 0, 0))


def test_connection_config_keeps_legacy_tuple_boundary_and_normalizes_uuid():
    config = BleConnectionConfig.from_value((
        "Trackball BLE", "", "2CAD0002-6E64-0146-B139-9CF2A4CD57FC"))
    assert config.name == "Trackball BLE"
    assert config.rotation_characteristic == "2cad0002-6e64-0146-b139-9cf2a4cd57fc"


def test_package_discovery_and_descriptor_data_are_declared():
    pyproject = open("pyproject.toml", encoding="utf-8").read()
    assert "include = [\"trackball_daemon*\"]" in pyproject
    assert "devices/descriptor_data/*.json" in pyproject
