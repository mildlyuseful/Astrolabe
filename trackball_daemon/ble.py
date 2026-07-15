"""Compatibility facade for the generic BLE device transport."""

from .devices.transport import BleTransport, gatt_inventory, start_ble_thread

__all__ = ("BleTransport", "gatt_inventory", "start_ble_thread")
