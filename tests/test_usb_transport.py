# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

import struct
import threading

import pytest

from trackball_daemon.devices import (
    SnapshotInputProvider,
    UsbTransport,
    builtin_device_descriptors,
    encode_input_state_snapshot,
)
from trackball_daemon.devices.usb_transport import UsbClaimRejected, UsbSessionLost
from trackball_daemon.input import InputAggregator


def _capability(*, keepalive_ms=1500):
    return bytes((
        4, 1, 1, 0b111, 0b011,
        keepalive_ms & 0xff, (keepalive_ms >> 8) & 0xff,
        1, 0,
    ))


def _ack(opcode, request_id, *, result=0, owner=2):
    return bytes((
        5, 1, opcode,
        request_id & 0xff, (request_id >> 8) & 0xff,
        result, owner,
    ))


class _Clock:
    def __init__(self):
        self.value = 1.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class _Device:
    def __init__(self, clock, on_command):
        self.clock = clock
        self.on_command = on_command
        self.reports = []
        self.writes = []
        self.closed = False

    def get_feature_report(self, report_id, length):
        assert (report_id, length) == (4, 9)
        return _capability()

    def write(self, report):
        report = bytes(report)
        self.writes.append(report)
        opcode = report[2]
        request_id = int.from_bytes(report[3:5], "little")
        self.on_command(self, opcode, request_id)
        return len(report)

    def read(self, length, timeout_ms):
        assert length == 13
        if self.reports:
            return self.reports.pop(0)
        self.clock.advance(timeout_ms / 1000.0)
        return b""

    def close(self):
        self.closed = True


class _Backend:
    def __init__(self, interfaces, device):
        self.interfaces = tuple(interfaces)
        self.device = device
        self.opened = []

    def enumerate(self):
        return self.interfaces

    def open_path(self, path):
        self.opened.append(path)
        return self.device


class _LoggedEvent:
    def __init__(self, log):
        self._set = True
        self.log = log

    def is_set(self):
        return self._set

    def set(self):
        self.log.append("gate:set")
        self._set = True

    def clear(self):
        self.log.append("gate:clear")
        self._set = False


class _Provider:
    def __init__(self, log):
        self.log = log
        self.lease = None

    def begin_session(self, instance_id, *, supports_input):
        assert supports_input is True
        self.log.append("provider:begin")
        self.lease = object()
        return self.lease

    def run_if_current(self, lease, callback):
        if lease is not self.lease:
            return False
        callback()
        return True

    def accept_snapshot(self, _packet, *, lease):
        assert lease is self.lease
        self.log.append("provider:snapshot")

    def disconnect(self, reason, *, lease):
        assert reason == "usb_disconnect"
        if lease is not self.lease:
            return False
        self.log.append("provider:disconnect")
        self.lease = None
        return True


def _descriptor():
    return next(
        descriptor for descriptor in builtin_device_descriptors()
        if descriptor.device_id == "astrolabe_5way")


def _interface(**overrides):
    interface = {
        "path": b"astrolabe-vendor-hid",
        "vendor_id": 0x1D50,
        "product_id": 0x615E,
        "usage_page": 0xFF00,
        "usage": 1,
        "product_string": "Astrolabe",
    }
    interface.update(overrides)
    return interface


def test_usb_claim_installs_provider_lease_before_pausing_ble_and_delivers_reports():
    log = []
    stop = threading.Event()
    gate = _LoggedEvent(log)
    provider = _Provider(log)
    clock = _Clock()
    samples = []

    def on_command(device, opcode, request_id):
        if opcode == 1:
            device.reports.extend((
                _ack(opcode, request_id - 1),
                _ack(opcode, request_id),
                bytes((2,)) + encode_input_state_snapshot(7, b"\x11"),
                bytes((1,)) + struct.pack("<fff", 1.0, -2.0, 0.5),
            ))

    device = _Device(clock, on_command)
    backend = _Backend((_interface(),), device)

    def on_motion(sample):
        samples.append(sample)
        log.append("motion")
        stop.set()

    transport = UsbTransport(
        (_descriptor(),),
        {"ble.astrolabe": provider},
        on_motion,
        lambda status: log.append(status),
        stop,
        enabled_event=gate,
        external_power_callback=lambda powered: log.append(f"power:{powered}"),
        backend=backend,
        clock=clock,
    )
    transport._run_candidate(_descriptor(), _interface())

    assert log.index("provider:begin") < log.index("gate:clear")
    assert log.index("provider:disconnect") < log.index("gate:set")
    assert log.index("gate:clear") < log.index("provider:snapshot") < log.index("motion")
    assert samples[0].source_id == "ble.astrolabe.motion"
    assert samples[0].rotation == pytest.approx((1.0, -2.0, 0.5))
    assert samples[0].metadata == {"transport": "usb", "device_id": "astrolabe_5way"}
    assert device.writes[0] == bytes((3, 1, 1, 1, 0))
    assert device.writes[-1][0:3] == bytes((3, 1, 2))
    assert device.closed is True
    assert gate.is_set()


def test_usb_rejected_attach_never_replaces_provider_or_pauses_ble():
    log = []
    stop = threading.Event()
    gate = _LoggedEvent(log)
    provider = _Provider(log)
    clock = _Clock()

    def on_command(device, opcode, request_id):
        if opcode == 1:
            device.reports.append(_ack(opcode, request_id, result=1, owner=0))

    device = _Device(clock, on_command)
    transport = UsbTransport(
        (_descriptor(),),
        {"ble.astrolabe": provider},
        lambda _sample: None,
        lambda _status: None,
        stop,
        enabled_event=gate,
        backend=_Backend((_interface(),), device),
        clock=clock,
    )

    with pytest.raises(UsbClaimRejected, match="result=1"):
        transport._run_candidate(_descriptor(), _interface())
    assert log == []
    assert provider.lease is None
    assert gate.is_set()
    assert device.closed is True


def test_usb_detaches_firmware_if_provider_session_cannot_start():
    class BrokenProvider:
        @staticmethod
        def begin_session(_instance_id, *, supports_input):
            assert supports_input is True
            raise RuntimeError("provider unavailable")

    stop = threading.Event()
    gate = _LoggedEvent([])
    clock = _Clock()

    def on_command(device, opcode, request_id):
        if opcode == 1:
            device.reports.append(_ack(opcode, request_id))

    device = _Device(clock, on_command)
    transport = UsbTransport(
        (_descriptor(),),
        {"ble.astrolabe": BrokenProvider()},
        lambda _sample: None,
        lambda _status: None,
        stop,
        enabled_event=gate,
        backend=_Backend((_interface(),), device),
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        transport._run_candidate(_descriptor(), _interface())

    assert [report[2] for report in device.writes] == [1, 2]
    assert gate.is_set()
    assert device.closed is True


def test_keepalive_loss_releases_pressed_controls_before_ble_fallback():
    descriptor = _descriptor()
    aggregator = InputAggregator()
    provider = SnapshotInputProvider(
        descriptor, aggregator.accept_many, aggregator.update_health)
    aggregator.register_provider(provider)
    provider.configure(control.control_id for control in descriptor.controls)
    transitions = []
    aggregator.add_listener(transitions.append)
    log = []
    gate = _LoggedEvent(log)
    stop = threading.Event()
    clock = _Clock()
    keepalives = 0

    def on_command(device, opcode, request_id):
        nonlocal keepalives
        if opcode == 1:
            device.reports.extend((
                _ack(opcode, request_id),
                bytes((2,)) + encode_input_state_snapshot(1, b"\x10"),
            ))
        elif opcode == 3:
            keepalives += 1
            if keepalives == 1:
                device.reports.append(_ack(opcode, request_id))

    device = _Device(clock, on_command)
    transport = UsbTransport(
        (descriptor,),
        {descriptor.source_id: provider},
        lambda _sample: None,
        lambda status: log.append(status),
        stop,
        enabled_event=gate,
        external_power_callback=lambda powered: log.append(f"power:{powered}"),
        backend=_Backend((_interface(),), device),
        clock=clock,
    )

    with pytest.raises(UsbSessionLost, match="timed out"):
        transport._run_candidate(descriptor, _interface())

    assert keepalives == 2
    assert provider.session is None
    assert aggregator.snapshot().pressed_tokens == ()
    state_transitions = [
        event for event in transitions
        if event.reason in {"device_input_snapshot", "usb_disconnect"}]
    assert [event.reason for event in state_transitions] == [
        "device_input_snapshot", "usb_disconnect"]
    assert state_transitions[0].snapshot.pressed_tokens == (
        "ble.astrolabe:fiveway.center",)
    assert state_transitions[1].snapshot.pressed_tokens == ()
    assert log.index("gate:clear") < log.index("gate:set")
    # Documented teardown order: release the lease, mark external power absent, then re-enable
    # BLE. Enabling BLE first lets it reconnect and publish a battery percentage that is then
    # reclassified as externally powered.
    assert log[-2:] == ["power:False", "gate:set"]


def test_usb_interface_selection_requires_every_identity_field():
    descriptor = _descriptor()
    clock = _Clock()
    device = _Device(clock, lambda *_args: None)
    interfaces = (
        _interface(path=b"wrong-product", product_string="Astrolabe Clone"),
        _interface(path=b"wrong-product-case", product_string="astrolabe"),
        _interface(path=b"wrong-usage", usage=2),
        _interface(path=b"right"),
    )
    backend = _Backend(interfaces, device)
    transport = UsbTransport(
        (descriptor,),
        {descriptor.source_id: _Provider([])},
        lambda _sample: None,
        lambda _status: None,
        threading.Event(),
        enabled_event=_LoggedEvent([]),
        backend=backend,
        clock=clock,
    )
    selected_descriptor, selected_interface = transport._find_candidate()
    assert selected_descriptor is descriptor
    assert selected_interface["path"] == b"right"
