# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Generic BLE transport and legacy/five-way adapter selection."""

import asyncio
from types import SimpleNamespace
import struct
import threading

from trackball_daemon.devices import (
    BleConnectionConfig,
    BleTransport,
    DeviceAdapterRegistry,
    DeviceSession,
    GattInventory,
    SnapshotInputProvider,
    builtin_device_descriptors,
    encode_input_state_snapshot,
)
from trackball_daemon.input import InputAggregator, ProviderStatus


SERVICE = "2cad0001-6e64-0146-b139-9cf2a4cd57fc"
ROTATION = "2cad0002-6e64-0146-b139-9cf2a4cd57fc"
INPUT = "2cad0003-6e64-0146-b139-9cf2a4cd57fc"
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"


def _registry():
    aggregator = InputAggregator()
    descriptors = builtin_device_descriptors()
    providers = {}
    for descriptor in descriptors:
        provider = SnapshotInputProvider(
            descriptor, aggregator.accept_many, aggregator.update_health)
        aggregator.register_provider(provider)
        provider.configure(control.control_id for control in descriptor.controls)
        providers[descriptor.source_id] = provider
    return DeviceAdapterRegistry(descriptors, providers), providers, aggregator


def _session(name, characteristics=(ROTATION, INPUT), instance_id="AA:BB#1"):
    return DeviceSession(
        instance_id, name, "AA:BB",
        GattInventory(frozenset((SERVICE,)), frozenset(characteristics)))


def test_registry_selects_fiveway_by_data_descriptor_and_legacy_without_input_char():
    registry, providers, _aggregator = _registry()
    samples = []
    config = BleConnectionConfig("Trackball BLE", "", ROTATION)

    modern = registry.select(config, _session("Trackball BLE"), samples.append)
    modern_lease = modern.connected(_session("Trackball BLE"))
    assert modern.label == "XIAO3389 three-button test bench"
    assert [item.characteristic_uuid for item in modern.subscriptions] == [ROTATION, INPUT]
    assert providers["ble.xiao3389"].health.status is ProviderStatus.STARTING
    modern.disconnected(lease=modern_lease)

    legacy_session = _session("Trackball BLE", (ROTATION,))
    legacy = registry.select(config, legacy_session, samples.append)
    legacy.connected(legacy_session)
    assert legacy.label == "legacy Astrolabe rotation"
    assert [item.characteristic_uuid for item in legacy.subscriptions] == [ROTATION]
    assert providers["ble.xiao3389"].health.status is ProviderStatus.SUSPENDED


def test_unknown_device_name_does_not_guess_an_input_bit_mapping():
    registry, providers, _aggregator = _registry()
    adapter = registry.select(
        BleConnectionConfig("Community Device", "AA:BB", ROTATION),
        _session("Community Device"), lambda _sample: None)
    adapter.connected(_session("Community Device"))
    assert len(adapter.subscriptions) == 1
    assert all(provider.session is None for provider in providers.values())


def test_fiveway_adapter_emits_exact_motion_and_normalized_input_events():
    registry, _providers, aggregator = _registry()
    samples = []
    adapter = registry.select(
        BleConnectionConfig("Astrolabe", "", ROTATION),
        _session("Astrolabe"), samples.append)
    adapter.connected(_session("Astrolabe"))
    transitions = []
    aggregator.add_listener(transitions.append)

    callbacks = {item.characteristic_uuid: item.callback for item in adapter.subscriptions}
    payload = struct.pack("<fff", 1.0, -2.0, 0.5)
    callbacks[ROTATION](None, payload)
    callbacks[INPUT](None, encode_input_state_snapshot(7, b"\x11"))

    assert samples[0].payload == payload
    assert samples[0].rotation == (1.0, -2.0, 0.5)
    assert aggregator.snapshot().pressed_tokens == (
        "ble.astrolabe:fiveway.center", "ble.astrolabe:fiveway.up")
    assert transitions[-1].reason == "device_input_snapshot"


def test_replaced_adapter_ignores_old_notifications_and_late_disconnect():
    registry, providers, aggregator = _registry()
    samples = []
    config = BleConnectionConfig("Astrolabe", "", ROTATION)

    old_session = _session("Astrolabe", instance_id="AA:BB#old")
    old_adapter = registry.select(config, old_session, samples.append)
    old_lease = old_adapter.connected(old_session)
    old_callbacks = {
        item.characteristic_uuid: item.callback for item in old_adapter.subscriptions}

    new_session = _session("Astrolabe", instance_id="AA:BB#new")
    new_adapter = registry.select(config, new_session, samples.append)
    new_lease = new_adapter.connected(new_session)
    new_callbacks = {
        item.characteristic_uuid: item.callback for item in new_adapter.subscriptions}
    new_callbacks[ROTATION](None, struct.pack("<fff", 1.0, 2.0, 3.0))
    new_callbacks[INPUT](None, encode_input_state_snapshot(1, b"\x02"))
    revision = aggregator.snapshot().revision

    old_callbacks[ROTATION](None, struct.pack("<fff", 9.0, 9.0, 9.0))
    old_callbacks[INPUT](None, encode_input_state_snapshot(100, b"\x10"))
    assert old_adapter.disconnected("late_old_disconnect", lease=old_lease) is False

    assert [sample.rotation for sample in samples] == [(1.0, 2.0, 3.0)]
    assert providers["ble.astrolabe"].session == "AA:BB#new"
    assert aggregator.snapshot().revision == revision
    assert aggregator.snapshot().pressed_tokens == ("ble.astrolabe:fiveway.down",)

    new_adapter.disconnected(lease=new_lease)


class _FakeService:
    def __init__(self, uuid, characteristics):
        self.uuid = uuid
        self.characteristics = [SimpleNamespace(uuid=value) for value in characteristics]


class _FakeServices:
    def __init__(self):
        self.services = {SERVICE: _FakeService(SERVICE, (ROTATION, INPUT))}


class _BatteryFakeServices:
    def __init__(self):
        self.services = {
            SERVICE: _FakeService(SERVICE, (ROTATION, INPUT)),
            BATTERY_SERVICE: _FakeService(BATTERY_SERVICE, (BATTERY_LEVEL,)),
        }


class _FakeDevice:
    name = "Trackball BLE"
    address = "AA:BB"


class _FakeScanner:
    @staticmethod
    async def find_device_by_filter(filterfunc, timeout):
        assert timeout == 10.0
        device = _FakeDevice()
        advertisement = SimpleNamespace(local_name=None, service_uuids=(SERVICE,))
        return device if filterfunc(device, advertisement) else None


class _FakeClient:
    instance = None

    def __init__(self, target):
        assert isinstance(target, _FakeDevice)
        self.address = target.address
        self.services = _FakeServices()
        self.is_connected = True
        self.started = []
        self.stopped = []
        self.__class__.instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.is_connected = False

    async def start_notify(self, uuid, callback):
        self.started.append(uuid)
        if uuid == ROTATION:
            callback(None, struct.pack("<fff", 0.1, 0.2, 0.3))
        elif uuid == INPUT:
            callback(None, encode_input_state_snapshot(1, b"\x01"))

    async def stop_notify(self, uuid):
        self.stopped.append(uuid)


class _BatteryFakeClient(_FakeClient):
    async def __aenter__(self):
        self.services = _BatteryFakeServices()
        return self

    async def read_gatt_char(self, uuid):
        assert uuid == BATTERY_LEVEL
        return b"\x52"

    async def start_notify(self, uuid, callback):
        if uuid == BATTERY_LEVEL:
            self.started.append(uuid)
            callback(None, b"\x51")
            return
        await super().start_notify(uuid, callback)


def test_transport_discovers_and_subscribes_all_adapter_characteristics_then_disconnects():
    registry, _providers, aggregator = _registry()
    stop = threading.Event()
    statuses = []
    samples = []
    battery_levels = []

    async def sleep(_delay):
        stop.set()

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION),
        registry,
        samples.append,
        statuses.append,
        stop,
        battery_callback=battery_levels.append,
        scanner=_FakeScanner,
        client_factory=_FakeClient,
        sleep=sleep,
    )
    asyncio.run(transport.run())

    assert _FakeClient.instance.started == [ROTATION, INPUT]
    assert _FakeClient.instance.stopped == [INPUT, ROTATION]
    assert samples and samples[0].source_id == "ble.xiao3389.motion"
    assert battery_levels == [None]
    assert aggregator.snapshot().pressed_tokens == ()
    assert any("XIAO3389 three-button test bench is live" in text for text in statuses)


def test_transport_does_not_scan_while_ble_is_paused():
    class UnexpectedScanner:
        @staticmethod
        async def find_device_by_filter(_filterfunc, _timeout):
            raise AssertionError("paused BLE transport must not scan")

    registry, _providers, _aggregator = _registry()
    stop = threading.Event()
    enabled = threading.Event()
    statuses = []

    async def sleep(delay):
        assert delay == 0.1
        stop.set()

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION), registry,
        lambda _sample: None, statuses.append, stop,
        enabled_event=enabled, scanner=UnexpectedScanner, sleep=sleep)
    asyncio.run(transport.run())

    assert statuses == []


def test_inflight_ble_connection_cannot_replace_usb_session_after_gate_closes():
    registry, providers, _aggregator = _registry()
    stop = threading.Event()
    enabled = threading.Event()
    enabled.set()
    statuses = []

    class PausingClient(_FakeClient):
        async def __aenter__(self):
            enabled.clear()
            return self

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION),
        registry,
        lambda _sample: None,
        statuses.append,
        stop,
        enabled_event=enabled,
        client_factory=PausingClient,
    )
    asyncio.run(transport._connect_once(
        BleConnectionConfig("Trackball BLE", "", ROTATION),
        _FakeDevice(),
        "Trackball BLE",
    ))

    assert providers["ble.xiao3389"].session is None
    assert PausingClient.instance.started == []
    assert not any(status.startswith(("connected", "subscribed")) for status in statuses)


def test_transport_closes_active_ble_session_without_reconnect_churn_when_paused():
    registry, providers, aggregator = _registry()
    stop = threading.Event()
    enabled = threading.Event()
    enabled.set()
    statuses = []

    async def sleep(_delay):
        if enabled.is_set():
            enabled.clear()
        else:
            stop.set()

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION), registry,
        lambda _sample: None, statuses.append, stop,
        enabled_event=enabled,
        scanner=_FakeScanner,
        client_factory=_FakeClient,
        sleep=sleep,
    )
    asyncio.run(transport.run())

    assert _FakeClient.instance.stopped == [INPUT, ROTATION]
    assert providers["ble.xiao3389"].session is None
    assert aggregator.snapshot().pressed_tokens == ()
    assert "disconnected, reconnecting..." not in statuses


def test_transport_does_not_publish_live_status_after_usb_pauses_subscription_setup():
    registry, providers, _aggregator = _registry()
    stop = threading.Event()
    enabled = threading.Event()
    enabled.set()
    statuses = []

    class PausingSubscriptionClient(_FakeClient):
        async def start_notify(self, uuid, callback):
            await super().start_notify(uuid, callback)
            if uuid == INPUT:
                enabled.clear()

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION),
        registry,
        lambda _sample: None,
        statuses.append,
        stop,
        enabled_event=enabled,
        client_factory=PausingSubscriptionClient,
    )
    asyncio.run(transport._connect_once(
        BleConnectionConfig("Trackball BLE", "", ROTATION),
        _FakeDevice(),
        "Trackball BLE",
    ))

    assert providers["ble.xiao3389"].session is None
    assert not any(status.startswith("subscribed") for status in statuses)


def test_transport_ignores_battery_callback_after_its_ble_lease_is_paused():
    registry, _providers, _aggregator = _registry()
    enabled = threading.Event()
    enabled.set()
    levels = []
    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION),
        registry,
        lambda _sample: None,
        lambda _status: None,
        threading.Event(),
        battery_callback=levels.append,
        enabled_event=enabled,
    )
    lease = object()
    transport._activate(lease)

    transport._handle_battery_level(None, b"\x32", lease=lease)
    enabled.clear()
    transport._handle_battery_level(None, b"\x33", lease=lease)

    assert levels == [50]


def test_transport_reads_and_subscribes_optional_standard_battery_level():
    registry, _providers, _aggregator = _registry()
    stop = threading.Event()
    levels = []

    async def sleep(_delay):
        stop.set()

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION),
        registry,
        lambda _sample: None,
        lambda _status: None,
        stop,
        battery_callback=levels.append,
        scanner=_FakeScanner,
        client_factory=_BatteryFakeClient,
        sleep=sleep,
    )
    asyncio.run(transport.run())

    assert levels == [82, 81]
    assert _BatteryFakeClient.instance.started == [ROTATION, INPUT, BATTERY_LEVEL]
    assert _BatteryFakeClient.instance.stopped == [BATTERY_LEVEL, INPUT, ROTATION]


def test_transport_ignores_invalid_battery_payloads():
    registry, _providers, _aggregator = _registry()
    levels = []
    diagnostics = []
    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION),
        registry,
        lambda _sample: None,
        lambda _status: None,
        threading.Event(),
        battery_callback=levels.append,
    )
    transport.diagnostic_callback = diagnostics.append

    transport._handle_battery_level(None, b"")
    transport._handle_battery_level(None, b"\x65")
    transport._handle_battery_level(None, b"\x32")

    assert levels == [50]
    assert len(diagnostics) == 2


def test_transport_reports_scan_failure_without_constructing_a_client():
    class BrokenScanner:
        @staticmethod
        async def find_device_by_filter(_filterfunc, timeout):
            raise OSError(f"adapter unavailable after {timeout}")

    registry, _providers, _aggregator = _registry()
    stop = threading.Event()
    statuses = []

    async def sleep(_delay):
        stop.set()

    transport = BleTransport(
        lambda: ("Trackball BLE", "", ROTATION), registry,
        lambda _sample: None, statuses.append, stop,
        scanner=BrokenScanner, client_factory=lambda _target: None, sleep=sleep)
    asyncio.run(transport.run())
    assert any(text.startswith("scan error:") for text in statuses)


def test_transport_reports_compatible_service_when_name_is_missing():
    class UnnamedDevice:
        name = None
        address = "CC:DD"

    class ServiceOnlyScanner:
        @staticmethod
        async def find_device_by_filter(filterfunc, timeout):
            assert timeout == 10.0
            filterfunc(
                UnnamedDevice(),
                SimpleNamespace(local_name=None, service_uuids=(SERVICE,)),
            )
            return None

    registry, _providers, _aggregator = _registry()
    stop = threading.Event()
    statuses = []

    async def sleep(_delay):
        stop.set()

    transport = BleTransport(
        lambda: ("Astrolabe", "", ROTATION),
        registry,
        lambda _sample: None,
        statuses.append,
        stop,
        scanner=ServiceOnlyScanner,
        client_factory=lambda _target: None,
        sleep=sleep,
    )
    asyncio.run(transport.run())

    assert any(
        "compatible BLE service seen at CC:DD without a name" in text
        and "set the Device name or address" in text
        for text in statuses
    )
