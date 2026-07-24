# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Immutable device and BLE-session values with no Bleak dependency."""
from __future__ import annotations


from dataclasses import dataclass, field
import math
import struct
import time
from types import MappingProxyType
import uuid

from ..input import InputControlDescriptor


def normalize_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid Bluetooth UUID: {value!r}") from exc


def _text(value, field_name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty trimmed text")
    return value


def _freeze_mapping(value, field_name):
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return MappingProxyType({key: child for key, child in value.items()})


@dataclass(frozen=True)
class DeviceControl:
    control_id: str
    label: str
    bit: int
    kind: str = "switch"
    metadata: object = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "control_id", _text(self.control_id, "control ID"))
        object.__setattr__(self, "label", _text(self.label, "control label"))
        if type(self.bit) is not int or not 0 <= self.bit <= 255 * 8 - 1:
            raise ValueError("control bit must fit the one-byte snapshot length protocol")
        if self.kind not in {"button", "switch"}:
            raise ValueError("BLE discrete controls must be buttons or switches")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "control metadata"))


@dataclass(frozen=True)
class DeviceDescriptor:
    schema_version: int
    device_id: str
    source_id: str
    label: str
    advertised_names: tuple
    service_uuid: str
    motion_characteristic: str
    input_characteristic: str | None
    controls: tuple
    metadata: object = field(default_factory=dict)

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError(f"unsupported device descriptor schema: {self.schema_version}")
        object.__setattr__(self, "device_id", _text(self.device_id, "device ID"))
        object.__setattr__(self, "source_id", _text(self.source_id, "source ID"))
        object.__setattr__(self, "label", _text(self.label, "device label"))
        names = tuple(_text(name, "advertised name") for name in self.advertised_names)
        if not names or len(set(name.casefold() for name in names)) != len(names):
            raise ValueError("advertised device names must be non-empty and unique")
        object.__setattr__(self, "advertised_names", names)
        object.__setattr__(self, "service_uuid", normalize_uuid(self.service_uuid))
        object.__setattr__(
            self, "motion_characteristic", normalize_uuid(self.motion_characteristic))
        if self.input_characteristic is not None:
            object.__setattr__(
                self, "input_characteristic", normalize_uuid(self.input_characteristic))
        controls = tuple(self.controls)
        if any(not isinstance(control, DeviceControl) for control in controls):
            raise TypeError("descriptor controls must be DeviceControl values")
        if len({control.control_id for control in controls}) != len(controls):
            raise ValueError("descriptor control IDs must be unique")
        if len({control.bit for control in controls}) != len(controls):
            raise ValueError("descriptor control bits must be unique")
        if controls and self.input_characteristic is None:
            raise ValueError("a descriptor with controls requires an input characteristic")
        object.__setattr__(self, "controls", controls)
        object.__setattr__(
            self, "metadata", _freeze_mapping(self.metadata, "descriptor metadata"))

    @property
    def input_descriptors(self):
        return tuple(InputControlDescriptor(
            self.source_id,
            control.control_id,
            control.label,
            control.kind,
            metadata={
                "device_id": self.device_id,
                "bit": control.bit,
                **control.metadata,
            },
        ) for control in self.controls)

    @property
    def allowed_bit_mask(self):
        return sum(1 << control.bit for control in self.controls)

    def matches_name(self, name):
        return isinstance(name, str) and name.casefold() in {
            candidate.casefold() for candidate in self.advertised_names}


@dataclass(frozen=True)
class BleConnectionConfig:
    name: str
    address: str
    rotation_characteristic: str

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("BLE device name must be non-empty")
        if not isinstance(self.address, str):
            raise ValueError("BLE address must be text")
        object.__setattr__(
            self, "rotation_characteristic", normalize_uuid(self.rotation_characteristic))

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        try:
            name, address, rotation_characteristic = value
        except (TypeError, ValueError) as exc:
            raise ValueError("BLE params must contain name, address, and rotation UUID") from exc
        return cls(name, address, rotation_characteristic)


@dataclass(frozen=True)
class GattInventory:
    service_uuids: frozenset
    characteristic_uuids: frozenset

    def __post_init__(self):
        object.__setattr__(self, "service_uuids", frozenset(
            normalize_uuid(value) for value in self.service_uuids))
        object.__setattr__(self, "characteristic_uuids", frozenset(
            normalize_uuid(value) for value in self.characteristic_uuids))


@dataclass(frozen=True)
class DeviceSession:
    instance_id: str
    name: str
    address: str
    gatt: GattInventory

    def __post_init__(self):
        object.__setattr__(self, "instance_id", _text(self.instance_id, "device instance ID"))
        if not isinstance(self.name, str) or not isinstance(self.address, str):
            raise ValueError("device session name and address must be text")
        if not isinstance(self.gatt, GattInventory):
            raise TypeError("device session GATT data must be a GattInventory")


@dataclass(frozen=True)
class MotionSample:
    source_id: str
    payload: bytes
    timestamp: float = field(default_factory=time.monotonic)
    metadata: object = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "source_id", _text(self.source_id, "motion source ID"))
        payload = bytes(self.payload)
        if len(payload) != 12:
            raise ValueError("rotation notification must contain exactly 12 bytes")
        values = struct.unpack("<fff", payload)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("rotation notification values must be finite")
        object.__setattr__(self, "payload", payload)
        if not math.isfinite(float(self.timestamp)) or self.timestamp < 0:
            raise ValueError("motion timestamp must be finite and non-negative")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "motion metadata"))

    @property
    def rotation(self):
        return struct.unpack("<fff", self.payload)


@dataclass(frozen=True)
class NotificationSubscription:
    characteristic_uuid: str
    callback: object

    def __post_init__(self):
        object.__setattr__(
            self, "characteristic_uuid", normalize_uuid(self.characteristic_uuid))
        if not callable(self.callback):
            raise TypeError("notification callback must be callable")
