# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Generic Bleak scan/connect/discover/subscribe/reconnect transport."""

import asyncio
from collections.abc import Mapping
import logging
import threading

from bleak import BleakClient, BleakScanner

from .model import BleConnectionConfig, DeviceSession, GattInventory


logger = logging.getLogger("trackball_daemon.ble")


def _iter_services(collection):
    if hasattr(collection, "services"):
        return tuple(collection.services.values())
    if isinstance(collection, Mapping):
        return tuple(collection.values())
    return tuple(collection)


def gatt_inventory(client):
    services = _iter_services(client.services)
    return GattInventory(
        frozenset(service.uuid for service in services),
        frozenset(characteristic.uuid for service in services
                  for characteristic in service.characteristics),
    )


class BleTransport:
    def __init__(self, get_params, adapter_registry, motion_callback, status_callback, stop_event,
                 *, scanner=None, client_factory=None, sleep=None):
        if not all(callable(value) for value in (
                get_params, motion_callback, status_callback)):
            raise TypeError("BLE transport callbacks must be callable")
        self.get_params = get_params
        self.adapter_registry = adapter_registry
        self.motion_callback = motion_callback
        self.status_callback = status_callback
        self.stop_event = stop_event
        self.scanner = scanner or BleakScanner
        self.client_factory = client_factory or BleakClient
        self.sleep = sleep or asyncio.sleep
        self.diagnostic_callback = lambda message: logger.warning("%s", message)
        self._generation = 0

    async def run(self):
        while not self.stop_event.is_set():
            config = BleConnectionConfig.from_value(self.get_params())
            target, scanned_name = await self._find_target(config)
            if target is None:
                continue
            await self._connect_once(config, target, scanned_name)

    async def _find_target(self, config):
        if config.address:
            self.status_callback(f"connecting to {config.address}...")
            return config.address, config.name
        self.status_callback(f'scanning for "{config.name}"...')
        expected_name = config.name.casefold()
        expected_services = {
            item.casefold() for item in
            self.adapter_registry.discovery_service_uuids(config)
        }
        service_candidates = {}
        matched_names = {}

        def match(device, advertisement):
            address = str(getattr(device, "address", "") or "")
            names = tuple(
                value for value in (
                    getattr(advertisement, "local_name", None),
                    getattr(device, "name", None),
                )
                if isinstance(value, str) and value
            )
            if any(value.casefold() == expected_name for value in names):
                matched_names[address] = next(
                    value for value in names if value.casefold() == expected_name)
                return True
            advertised_services = {
                str(value).casefold() for value in
                (getattr(advertisement, "service_uuids", ()) or ())
            }
            if expected_services & advertised_services:
                service_candidates[address] = (
                    device,
                    names[0] if names else None,
                )
            return False

        try:
            device = await self.scanner.find_device_by_filter(match, timeout=10.0)
        except Exception as exc:
            self.status_callback(f"scan error: {exc}")
            await self.sleep(2.0)
            return None, None
        if device is None:
            if len(service_candidates) == 1:
                address, (_candidate, observed_name) = next(iter(service_candidates.items()))
                identity = f' as "{observed_name}"' if observed_name else " without a name"
                self.status_callback(
                    f"compatible BLE service seen at {address}{identity}, but not as "
                    f'"{config.name}"; set the Device name or address')
            elif service_candidates:
                self.status_callback(
                    f"{len(service_candidates)} compatible BLE services found without a unique "
                    "name match; set the Device address")
            else:
                self.status_callback("device not found, retrying...")
            await self.sleep(1.0)
            return None, None
        address = str(getattr(device, "address", "") or "")
        return device, matched_names.get(address) or getattr(device, "name", None) or config.name

    async def _connect_once(self, config, target, scanned_name):
        adapter = None
        client = None
        subscribed = []
        try:
            async with self.client_factory(target) as client:
                address = str(getattr(client, "address", "") or getattr(target, "address", ""))
                name = scanned_name or config.name
                self._generation += 1
                session = DeviceSession(
                    f"{address or name}#{self._generation}", name, address,
                    gatt_inventory(client))
                self.status_callback(f"connected to {address or name}")
                adapter = self.adapter_registry.select(
                    config, session, self.motion_callback, self.diagnostic_callback)
                adapter.connected(session)
                try:
                    for subscription in adapter.subscriptions:
                        await client.start_notify(
                            subscription.characteristic_uuid, subscription.callback)
                        subscribed.append(subscription.characteristic_uuid)
                    self.status_callback(f"subscribed -- {adapter.label} is live")
                    while client.is_connected and not self.stop_event.is_set():
                        await self.sleep(0.3)
                finally:
                    for characteristic_uuid in reversed(subscribed):
                        try:
                            await client.stop_notify(characteristic_uuid)
                        except Exception:
                            pass
                    subscribed.clear()
        except Exception as exc:
            self.status_callback(f"connection error: {exc}, retrying...")
            if not self.stop_event.is_set():
                await self.sleep(2.0)
        finally:
            if adapter is not None:
                adapter.disconnected("device_disconnect")
        if not self.stop_event.is_set():
            self.status_callback("disconnected, reconnecting...")


def start_ble_thread(get_params, adapter_registry, motion_callback, status_callback, stop_event):
    transport = BleTransport(
        get_params, adapter_registry, motion_callback, status_callback, stop_event)

    def runner():
        try:
            asyncio.run(transport.run())
        except Exception as exc:
            status_callback(f"BLE thread stopped: {exc}")

    thread = threading.Thread(target=runner, name="ble", daemon=True)
    thread.start()
    return thread
