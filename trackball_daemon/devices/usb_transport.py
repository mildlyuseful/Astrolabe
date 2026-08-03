# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Astrolabe vendor-HID discovery, ownership handoff, and report delivery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import threading
import time

from .model import DeviceDescriptor, MotionSample


logger = logging.getLogger("trackball_daemon.usb")

PROTOCOL_VERSION = 1
REPORT_ROTATION = 1
REPORT_SNAPSHOT = 2
REPORT_COMMAND = 3
REPORT_CAPABILITY = 4
REPORT_ACK = 5

COMMAND_ATTACH = 1
COMMAND_DETACH = 2
COMMAND_KEEPALIVE = 3

ACK_OK = 0
OWNER_USB = 2

_CAPABILITY_BYTES = 1 + 8
_ACK_BYTES = 1 + 6
_ROTATION_BYTES = 1 + 12
_SNAPSHOT_BYTES = 1 + 6
_MAX_REPORT_BYTES = _ROTATION_BYTES
_REQUIRED_REPORT_MASK = (1 << 0) | (1 << 1)
_REQUIRED_CAPABILITY_FLAGS = (1 << 0) | (1 << 1)


# Report lengths below are lower bounds, never equalities. Windows pads every interrupt-IN read to
# the interface's largest input report (here the 13-byte rotation report), so a 7-byte ACK or
# snapshot arrives as 13 bytes with trailing zeros. Dispatch on the report ID and slice the known
# payload; requiring exact lengths silently discards every report except rotation.


class UsbProtocolError(RuntimeError):
    pass


class UsbClaimRejected(UsbProtocolError):
    pass


class UsbSessionLost(UsbProtocolError):
    pass


@dataclass(frozen=True)
class UsbCapability:
    firmware_revision: int
    report_mask: int
    flags: int
    keepalive_ms: int
    usb_revision: int


@dataclass(frozen=True)
class UsbAck:
    opcode: int
    request_id: int
    result: int
    owner: int


class HidApiBackend:
    """Small compatibility boundary around the two Python hidapi object APIs."""

    def __init__(self, module=None):
        if module is None:
            import hid  # Imported lazily so non-USB composition can still be inspected.
            module = hid
        self._module = module

    def enumerate(self):
        return tuple(self._module.enumerate())

    def open_path(self, path):
        device_type = getattr(self._module, "Device", None)
        if device_type is not None:
            return device_type(path=path)
        device = self._module.device()
        device.open_path(path)
        return device


def _decode_capability(report):
    report = bytes(report)
    if len(report) < _CAPABILITY_BYTES or report[0] != REPORT_CAPABILITY:
        raise UsbProtocolError("invalid USB capability report length or ID")
    payload = report[1:_CAPABILITY_BYTES]
    if payload[0] != PROTOCOL_VERSION or payload[7] != 0:
        raise UsbProtocolError("unsupported USB capability version or reserved byte")
    capability = UsbCapability(
        firmware_revision=payload[1],
        report_mask=payload[2],
        flags=payload[3],
        keepalive_ms=int.from_bytes(payload[4:6], "little"),
        usb_revision=payload[6],
    )
    if capability.report_mask & _REQUIRED_REPORT_MASK != _REQUIRED_REPORT_MASK:
        raise UsbProtocolError("USB interface lacks rotation or snapshot reports")
    if capability.flags & _REQUIRED_CAPABILITY_FLAGS != _REQUIRED_CAPABILITY_FLAGS:
        raise UsbProtocolError("USB interface lacks attach or keepalive support")
    if capability.keepalive_ms <= 0 or capability.usb_revision != 1:
        raise UsbProtocolError("unsupported USB keepalive or protocol revision")
    return capability


def _decode_ack(report):
    report = bytes(report)
    if len(report) < _ACK_BYTES or report[0] != REPORT_ACK:
        return None
    payload = report[1:_ACK_BYTES]
    if payload[0] != PROTOCOL_VERSION:
        raise UsbProtocolError("unsupported USB ACK protocol version")
    return UsbAck(
        opcode=payload[1],
        request_id=int.from_bytes(payload[2:4], "little"),
        result=payload[4],
        owner=payload[5],
    )


def _path_text(path):
    if isinstance(path, bytes):
        return path.hex()
    return str(path)


class UsbTransport:
    def __init__(self, descriptors, providers, motion_callback, status_callback, stop_event, *,
                 enabled_event, external_power_callback=None, backend=None, clock=None,
                 ack_timeout=0.75, scan_interval=0.5, handover_lock=None):
        if not all(callable(value) for value in (motion_callback, status_callback)):
            raise TypeError("USB transport callbacks must be callable")
        if external_power_callback is not None and not callable(external_power_callback):
            raise TypeError("USB external-power callback must be callable")
        if not all(hasattr(enabled_event, name) for name in ("is_set", "set", "clear")):
            raise TypeError("USB transport requires a shared enabled event")
        if not hasattr(stop_event, "is_set") or not hasattr(stop_event, "wait"):
            raise TypeError("USB transport stop event must support is_set and wait")
        usb_descriptors = tuple(
            descriptor for descriptor in descriptors
            if isinstance(descriptor, DeviceDescriptor) and descriptor.usb_hid is not None)
        provider_map = dict(providers)
        missing = {descriptor.source_id for descriptor in usb_descriptors} - set(provider_map)
        if missing:
            raise ValueError(f"USB descriptors lack snapshot providers: {sorted(missing)}")
        if not isinstance(ack_timeout, (int, float)) or ack_timeout <= 0:
            raise ValueError("USB ACK timeout must be positive")
        if not isinstance(scan_interval, (int, float)) or scan_interval <= 0:
            raise ValueError("USB scan interval must be positive")

        self.descriptors = usb_descriptors
        self.providers = provider_map
        self.motion_callback = motion_callback
        self.status_callback = status_callback
        self.external_power_callback = external_power_callback or (lambda _powered: None)
        self.stop_event = stop_event
        self.enabled_event = enabled_event
        self.handover_lock = handover_lock or threading.RLock()
        self.backend = backend
        self.clock = clock or time.monotonic
        self.ack_timeout = float(ack_timeout)
        self.scan_interval = float(scan_interval)
        self.diagnostic_callback = lambda message: logger.warning("%s", message)
        self._request_id = 0
        self._generation = 0

    def _diagnose(self, message):
        try:
            self.diagnostic_callback(message)
        except Exception:
            pass

    def _publish_external_power(self, powered):
        try:
            self.external_power_callback(bool(powered))
        except Exception as exc:
            self._diagnose(f"USB external-power callback failed: {exc}")

    def _wait(self, seconds):
        return self.stop_event.wait(max(0.0, seconds))

    def _next_request_id(self):
        self._request_id = (self._request_id + 1) & 0xffff
        return self._request_id

    def _find_candidate(self):
        matches = []
        for interface in self.backend.enumerate():
            if not isinstance(interface, Mapping) or "path" not in interface:
                continue
            for descriptor in self.descriptors:
                if descriptor.usb_hid.matches(interface):
                    matches.append((descriptor, interface))
                    break
        if not matches:
            return None
        return min(matches, key=lambda item: _path_text(item[1]["path"]))

    @staticmethod
    def _read(device, timeout):
        timeout_ms = max(1, int(max(0.0, timeout) * 1000))
        report = device.read(_MAX_REPORT_BYTES, timeout_ms)
        return b"" if report is None else bytes(report)

    @staticmethod
    def _write_command(device, opcode, request_id):
        report = bytes((
            REPORT_COMMAND,
            PROTOCOL_VERSION,
            opcode,
            request_id & 0xff,
            (request_id >> 8) & 0xff,
        ))
        written = device.write(report)
        if written is not None and written != len(report):
            raise OSError(f"short USB HID write ({written}/{len(report)})")

    def _request(self, device, opcode, *, report_callback=None, timeout=None):
        request_id = self._next_request_id()
        self._write_command(device, opcode, request_id)
        deadline = self.clock() + (self.ack_timeout if timeout is None else timeout)
        while not self.stop_event.is_set():
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            report = self._read(device, min(remaining, 0.1))
            if not report:
                continue
            ack = _decode_ack(report)
            if ack is not None:
                if ack.request_id != request_id:
                    continue
                if ack.opcode != opcode:
                    raise UsbProtocolError("USB ACK opcode does not match its request")
                return ack
            if report_callback is not None:
                report_callback(report)
        raise TimeoutError(f"USB command {opcode} ACK timed out")

    def _get_capability(self, device):
        return _decode_capability(
            device.get_feature_report(REPORT_CAPABILITY, _CAPABILITY_BYTES))

    def _deliver_report(self, descriptor, provider, lease, report):
        if len(report) >= _ROTATION_BYTES and report[0] == REPORT_ROTATION:
            try:
                sample = MotionSample(
                    f"{descriptor.source_id}.motion",
                    report[1:_ROTATION_BYTES],
                    timestamp=self.clock(),
                    metadata={"transport": "usb", "device_id": descriptor.device_id},
                )
            except Exception as exc:
                self._diagnose(f"ignored invalid USB rotation report: {exc}")
                return
            try:
                provider.run_if_current(lease, lambda: self.motion_callback(sample))
            except Exception as exc:
                self._diagnose(f"USB motion callback failed: {exc}")
            return
        if len(report) >= _SNAPSHOT_BYTES and report[0] == REPORT_SNAPSHOT:
            provider.accept_snapshot(report[1:_SNAPSHOT_BYTES], lease=lease)
            return
        if report and report[0] != REPORT_ACK:
            self._diagnose(
                f"ignored invalid USB report {report[0]} ({len(report) - 1} payload bytes)")

    def _session_loop(self, device, descriptor, capability, provider, lease):
        keepalive_seconds = capability.keepalive_ms / 1000.0
        interval = max(0.05, keepalive_seconds / 3.0)
        next_keepalive = self.clock() + interval
        callback = lambda report: self._deliver_report(descriptor, provider, lease, report)

        while not self.stop_event.is_set():
            remaining = next_keepalive - self.clock()
            if remaining <= 0:
                ack = self._request(
                    device,
                    COMMAND_KEEPALIVE,
                    report_callback=callback,
                    timeout=min(self.ack_timeout, keepalive_seconds / 2.0),
                )
                if ack.result != ACK_OK or ack.owner != OWNER_USB:
                    raise UsbProtocolError("USB keepalive was rejected or lost ownership")
                next_keepalive = self.clock() + interval
                continue
            report = self._read(device, min(remaining, 0.1))
            if report:
                callback(report)

    def _run_candidate(self, descriptor, interface):
        device = self.backend.open_path(interface["path"])
        provider = self.providers[descriptor.source_id]
        lease = None
        claimed = False
        active = False
        try:
            capability = self._get_capability(device)
            ack = self._request(device, COMMAND_ATTACH)
            if ack.result != ACK_OK or ack.owner != OWNER_USB:
                raise UsbClaimRejected(
                    f"USB attach rejected (result={ack.result}, owner={ack.owner})")
            claimed = True

            self._generation += 1
            instance_id = f"usb:{_path_text(interface['path'])}#{self._generation}"
            # Replacing the provider lease first makes every BLE callback stale before its
            # transport observes the cleared gate and tears the old connection down.
            with self.handover_lock:
                lease = provider.begin_session(instance_id, supports_input=True)
                self.enabled_event.clear()
            active = True
            self._publish_external_power(True)
            self.status_callback(f"subscribed -- {descriptor.label} over USB is live")
            self._session_loop(device, descriptor, capability, provider, lease)
        except Exception as exc:
            if active:
                raise UsbSessionLost(str(exc)) from exc
            raise
        finally:
            # Teardown order matters: firmware keeps USB ownership until it sees the detach (or
            # the keepalive expires), and it rejects a BLE claim while that lease is live. Wait
            # once for the detach ACK before re-enabling BLE so fallback does not race the
            # keepalive window. One wait, no retry -- firmware keepalive is still the fail-safe.
            if claimed:
                try:
                    self._request(device, COMMAND_DETACH)
                except Exception as exc:
                    self._diagnose(f"USB detach was not acknowledged: {exc}")
            if active:
                # Release USB-owned controls before BLE is allowed to claim a fresh lease.
                with self.handover_lock:
                    try:
                        provider.disconnect("usb_disconnect", lease=lease)
                    except Exception as exc:
                        self._diagnose(f"USB provider cleanup failed: {exc}")
                    self._publish_external_power(False)
                    self.enabled_event.set()
            try:
                device.close()
            except Exception:
                pass

    def run(self):
        if not self.descriptors or self.stop_event.is_set():
            return
        if self.backend is None:
            self.backend = HidApiBackend()
        while not self.stop_event.is_set():
            if not self.enabled_event.is_set():
                self._wait(self.scan_interval)
                continue
            try:
                candidate = self._find_candidate()
                if candidate is None:
                    self._wait(self.scan_interval)
                    continue
                self._run_candidate(*candidate)
            except UsbClaimRejected as exc:
                self._diagnose(str(exc))
                self._wait(self.scan_interval)
            except UsbSessionLost as exc:
                self._diagnose(f"USB transport lost its session: {exc}")
                if not self.stop_event.is_set():
                    self.status_callback("USB disconnected; BLE fallback enabled")
                    self._wait(self.scan_interval)
            except Exception as exc:
                self._diagnose(f"USB transport error: {exc}")
                if not self.stop_event.is_set():
                    self._wait(self.scan_interval)


def start_usb_thread(descriptors, providers, motion_callback, status_callback, stop_event, *,
                     enabled_event, external_power_callback=None, backend=None,
                     handover_lock=None):
    transport = UsbTransport(
        descriptors,
        providers,
        motion_callback,
        status_callback,
        stop_event,
        enabled_event=enabled_event,
        external_power_callback=external_power_callback,
        backend=backend,
        handover_lock=handover_lock,
    )

    def runner():
        try:
            transport.run()
        except Exception as exc:
            status_callback(f"USB thread stopped: {exc}")

    thread = threading.Thread(target=runner, name="usb", daemon=True)
    thread.start()
    return thread


__all__ = (
    "HidApiBackend",
    "UsbCapability",
    "UsbClaimRejected",
    "UsbProtocolError",
    "UsbSessionLost",
    "UsbTransport",
    "start_usb_thread",
)
