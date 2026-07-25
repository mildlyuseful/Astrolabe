# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Compatibility facade for the generic BLE device transport."""

from .devices.transport import BleTransport, gatt_inventory, start_ble_thread

__all__ = ("BleTransport", "gatt_inventory", "start_ble_thread")
