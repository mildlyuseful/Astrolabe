# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Generic Bleak scan/connect/discover/subscribe/reconnect transport."""

import asyncio
from collections.abc import Mapping
from functools import partial
import logging
import threading
import time

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .model import BleConnectionConfig, DeviceSession, GattInventory


logger = logging.getLogger("trackball_daemon.ble")

BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


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
                 *, battery_callback=None, enabled_event=None, scanner=None,
                 client_factory=None, sleep=None, handover_lock=None, clock=None,
                 address_learned=None):
        if not all(callable(value) for value in (
                get_params, motion_callback, status_callback)):
            raise TypeError("BLE transport callbacks must be callable")
        if battery_callback is not None and not callable(battery_callback):
            raise TypeError("BLE battery callback must be callable")
        if enabled_event is not None and not callable(getattr(enabled_event, "is_set", None)):
            raise TypeError("BLE enabled event must expose is_set()")
        self.get_params = get_params
        self.adapter_registry = adapter_registry
        self.motion_callback = motion_callback
        self.status_callback = status_callback
        self.battery_callback = battery_callback or (lambda _level: None)
        self.stop_event = stop_event
        self.enabled_event = enabled_event
        self.handover_lock = handover_lock or threading.RLock()
        self.scanner = scanner or BleakScanner
        self.client_factory = client_factory or BleakClient
        self.sleep = sleep or asyncio.sleep
        self.clock = clock or time.monotonic
        self.address_learned = address_learned or (lambda _address: None)
        self.diagnostic_callback = lambda message: logger.warning("%s", message)
        self._generation = 0
        self._session_lock = threading.Lock()
        self._active_lease = None
        # Address of the last device that selected an adapter. Used only as a scan fallback; see
        # _find_target for why a scan alone is not sufficient on Windows.
        self._known_address = None

    def _activate(self, lease):
        with self._session_lock:
            self._active_lease = lease

    def _deactivate(self, lease):
        with self._session_lock:
            if lease is not None and lease is self._active_lease:
                self._active_lease = None

    def _is_active(self, lease):
        with self._session_lock:
            active = lease is not None and lease is self._active_lease
        return active and self._is_enabled()

    def _is_enabled(self):
        return self.enabled_event is None or self.enabled_event.is_set()

    def _run_if_active(self, lease, callback):
        """Serialize non-provider side effects against a USB handover."""
        with self.handover_lock:
            if not self._is_active(lease):
                return False
            callback()
            return True

    def _publish_battery_level(self, level):
        try:
            self.battery_callback(level)
        except Exception as exc:
            self.diagnostic_callback(f"BLE battery callback failed: {exc}")

    def _handle_battery_level(self, _sender, data, *, lease=None):
        def publish():
            payload = bytes(data)
            if len(payload) != 1 or payload[0] > 100:
                self.diagnostic_callback(
                    f"ignored invalid BLE Battery Level payload: {payload.hex() or 'empty'}")
                return
            self._publish_battery_level(payload[0])

        if lease is None:
            publish()
        else:
            self._run_if_active(lease, publish)

    async def _subscribe_battery(self, client, inventory, subscribed, lease):
        if BATTERY_LEVEL_UUID not in inventory.characteristic_uuids:
            self._run_if_active(lease, lambda: self._publish_battery_level(None))
            return
        try:
            self._handle_battery_level(
                None, await client.read_gatt_char(BATTERY_LEVEL_UUID), lease=lease)
        except Exception as exc:
            self.diagnostic_callback(f"could not read BLE Battery Level: {exc}")
        try:
            await client.start_notify(
                BATTERY_LEVEL_UUID, partial(self._handle_battery_level, lease=lease))
            subscribed.append(BATTERY_LEVEL_UUID)
        except Exception as exc:
            self.diagnostic_callback(f"could not subscribe to BLE Battery Level: {exc}")

    async def _start_keepalive(self, client, adapter, inventory):
        """Return (uuid, interval, first_due) when the device requires keepalives, else None.

        A subscription alone used to mean the daemon owned the route for as long as the CCC said
        so. That outlives the daemon: the CCC is persisted into the bond and the link stays up for
        the HID mouse, so a daemon that dies leaves the route claimed with no disconnect to notice
        and no way back short of forgetting the bond. Firmware now expires an idle claim; these
        writes are what keep a live session from being expired with it.
        """
        descriptor = getattr(adapter, "descriptor", None)
        characteristic_uuid = getattr(descriptor, "keepalive_characteristic", None)
        if characteristic_uuid is None or characteristic_uuid not in inventory.characteristic_uuids:
            return None
        try:
            payload = await client.read_gatt_char(characteristic_uuid)
        except Exception as exc:
            self.diagnostic_callback(f"could not read the keepalive interval: {exc}")
            return None
        if len(payload) < 3 or payload[0] != 1:
            self.diagnostic_callback("keepalive characteristic reported an unsupported layout")
            return None
        timeout = int.from_bytes(payload[1:3], "little") / 1000.0
        if timeout <= 0.0:
            return None
        # A third of the window, matching the wired path: two writes may be lost before firmware
        # concludes the daemon is gone.
        interval = max(0.05, timeout / 3.0)
        return characteristic_uuid, interval, self.clock()

    async def run(self):
        while not self.stop_event.is_set():
            if not self._is_enabled():
                await self.sleep(0.1)
                continue
            config = BleConnectionConfig.from_value(self.get_params())
            target, scanned_name = await self._find_target(config)
            if target is None or not self._is_enabled():
                continue
            await self._connect_once(config, target, scanned_name)

    @staticmethod
    def _direct_target(address, name):
        """Wrap a known address so connecting to it does not require an advertisement.

        Passing a bare address string looks like it bypasses discovery and does not: bleak's WinRT
        client leaves its device handle unset and `connect()` then calls `find_device_by_address`,
        so the scan simply moves later. A device Windows has paired as a BLE HID mouse never
        advertises, so that scan always fails and the only cure is forgetting the device -- which
        restores advertising. Constructing a BLEDevice sets the handle from the address up front,
        which is what actually skips the scan.
        """
        return BLEDevice(address, name, None)

    async def _find_target(self, config):
        if config.address:
            self.status_callback(f"connecting to {config.address}...")
            return self._direct_target(config.address, config.name), config.name
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
            if self._is_enabled():
                self.status_callback(f"scan error: {exc}")
                await self.sleep(2.0)
            return None, None
        if device is None:
            if not self._is_enabled():
                return None, None
            # A scan cannot see a device the OS already holds a connection to: once Windows has
            # paired it as a BLE HID mouse it stops advertising, so the daemon looks for something
            # that is sitting right there, connected. Bleak can still reach it by address, so a
            # device we have reached before is worth trying directly before reporting failure.
            if self._known_address:
                self.status_callback(
                    f'"{config.name}" is not advertising; it may already be connected as a '
                    f"Bluetooth mouse. Trying {self._known_address} directly...")
                return self._direct_target(self._known_address, config.name), config.name
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
        adapter_lease = None
        client = None
        subscribed = []
        try:
            async with self.client_factory(target) as client:
                address = str(getattr(client, "address", "") or getattr(target, "address", ""))
                name = scanned_name or config.name
                self._generation += 1
                inventory = gatt_inventory(client)
                session = DeviceSession(
                    f"{address or name}#{self._generation}", name, address,
                    inventory)
                def publish_motion(sample):
                    if self._is_active(adapter_lease):
                        self.motion_callback(sample)

                def report_protocol_error(message):
                    if self._is_active(adapter_lease):
                        self.diagnostic_callback(message)

                adapter = self.adapter_registry.select(
                    config, session, publish_motion, report_protocol_error)
                with self.handover_lock:
                    if not self._is_enabled():
                        return
                    adapter_lease = adapter.connected(session)
                    self._activate(adapter_lease)
                if address and address != self._known_address:
                    # Persisted, not just cached: a scan cannot find this device once Windows has
                    # paired it as a HID mouse, so without a remembered address a fresh daemon
                    # start has nothing to connect to and never attempts one. Learning it during
                    # the one window where discovery works -- before the device is paired for HID
                    # -- is what keeps it reachable afterwards.
                    self._known_address = address
                    try:
                        self.address_learned(address)
                    except Exception as exc:
                        self.diagnostic_callback(f"could not remember the device address: {exc}")
                self._run_if_active(
                    adapter_lease,
                    lambda: self.status_callback(f"connected to {address or name}"),
                )
                try:
                    for subscription in adapter.subscriptions:
                        await client.start_notify(
                            subscription.characteristic_uuid, subscription.callback)
                        subscribed.append(subscription.characteristic_uuid)
                    await self._subscribe_battery(client, inventory, subscribed, adapter_lease)
                    self._run_if_active(
                        adapter_lease,
                        lambda: self.status_callback(f"subscribed -- {adapter.label} is live"),
                    )
                    keepalive = await self._start_keepalive(client, adapter, inventory)
                    while (client.is_connected and not self.stop_event.is_set()
                           and self._is_enabled()):
                        if keepalive is not None:
                            characteristic_uuid, interval, due = keepalive
                            now = self.clock()
                            if now >= due:
                                # A rejected write means the firmware handed the route to someone
                                # else -- drop the session rather than keep publishing into a route
                                # we no longer own.
                                await client.write_gatt_char(
                                    characteristic_uuid, b"\x01", response=False)
                                keepalive = (characteristic_uuid, interval, now + interval)
                        await self.sleep(0.3)
                finally:
                    for characteristic_uuid in reversed(subscribed):
                        try:
                            await client.stop_notify(characteristic_uuid)
                        except Exception:
                            pass
                    subscribed.clear()
        except Exception as exc:
            if self._is_enabled():
                self.status_callback(f"connection error: {exc}, retrying...")
            if not self.stop_event.is_set() and self._is_enabled():
                await self.sleep(2.0)
        finally:
            self._deactivate(adapter_lease)
            if adapter is not None and adapter_lease is not None:
                adapter.disconnected("device_disconnect", lease=adapter_lease)
        if not self.stop_event.is_set() and self._is_enabled():
            self.status_callback("disconnected, reconnecting...")


def start_ble_thread(get_params, adapter_registry, motion_callback, status_callback, stop_event,
                     *, battery_callback=None, enabled_event=None, handover_lock=None,
                     address_learned=None):
    transport = BleTransport(
        get_params, adapter_registry, motion_callback, status_callback, stop_event,
        battery_callback=battery_callback, enabled_event=enabled_event,
        handover_lock=handover_lock, address_learned=address_learned)

    def runner():
        try:
            asyncio.run(transport.run())
        except Exception as exc:
            status_callback(f"BLE thread stopped: {exc}")

    thread = threading.Thread(target=runner, name="ble", daemon=True)
    thread.start()
    return thread
