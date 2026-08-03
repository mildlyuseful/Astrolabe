# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""BLE device descriptors, protocol adapters, and transport boundaries."""

from .model import (
    BleConnectionConfig,
    DeviceControl,
    DeviceDescriptor,
    DeviceSession,
    GattInventory,
    MotionSample,
    NotificationSubscription,
    UsbHidMatch,
)
from .descriptors import builtin_device_descriptors, load_device_descriptor
from .protocol import (
    INPUT_PROTOCOL_VERSION,
    INPUT_STATE_KIND,
    SequenceDisposition,
    SequenceGate,
    SnapshotPacketError,
    decode_input_state_snapshot,
    encode_input_state_snapshot,
)
from .snapshot_provider import SnapshotInputProvider
from .registry import DeviceAdapterRegistry, UnsupportedDeviceProtocol
from .transport import BleTransport, gatt_inventory, start_ble_thread
from .usb_transport import HidApiBackend, UsbTransport, start_usb_thread

__all__ = (
    "BleConnectionConfig",
    "DeviceControl",
    "DeviceAdapterRegistry",
    "DeviceDescriptor",
    "DeviceSession",
    "GattInventory",
    "HidApiBackend",
    "MotionSample",
    "NotificationSubscription",
    "INPUT_PROTOCOL_VERSION",
    "INPUT_STATE_KIND",
    "SequenceDisposition",
    "SequenceGate",
    "SnapshotInputProvider",
    "SnapshotPacketError",
    "UnsupportedDeviceProtocol",
    "UsbHidMatch",
    "UsbTransport",
    "BleTransport",
    "builtin_device_descriptors",
    "decode_input_state_snapshot",
    "encode_input_state_snapshot",
    "gatt_inventory",
    "load_device_descriptor",
    "start_ble_thread",
    "start_usb_thread",
)
