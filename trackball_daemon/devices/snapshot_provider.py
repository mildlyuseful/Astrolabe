# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Normalized input provider backed by versioned device-state snapshots."""

import threading
import time

from ..input import InputEvent, InputPhase, InputProvider, ProviderHealth, ProviderStatus
from .model import DeviceDescriptor
from .protocol import SequenceDisposition, SequenceGate, SnapshotPacketError, decode_input_state_snapshot


class SnapshotInputProvider(InputProvider):
    def __init__(self, descriptor, publish_events, publish_health):
        if not isinstance(descriptor, DeviceDescriptor):
            raise TypeError("snapshot provider requires a DeviceDescriptor")
        if not callable(publish_events) or not callable(publish_health):
            raise TypeError("snapshot provider callbacks must be callable")
        self.descriptor = descriptor
        self._publish_events = publish_events
        self._publish_health_callback = publish_health
        self._lock = threading.RLock()
        self._publish_lock = threading.RLock()
        self._required = frozenset()
        self._pressed = set()
        self._session = None
        self._lease = None
        self._supports_input = False
        self._sequence = SequenceGate()
        self._generation = 0
        self._health = ProviderHealth(
            descriptor.source_id, ProviderStatus.DISABLED, "no configured device controls")

    @property
    def source_id(self):
        return self.descriptor.source_id

    @property
    def controls(self):
        return self.descriptor.input_descriptors

    @property
    def health(self):
        with self._lock:
            return self._health

    @property
    def required_controls(self):
        with self._lock:
            return self._required

    @property
    def session(self):
        with self._lock:
            return self._session

    def _set_health(self, status, detail):
        with self._publish_lock:
            with self._lock:
                health = ProviderHealth(
                    self.source_id, status, detail, generation=self._generation)
                self._health = health
            self._publish_health_callback(health)
            return health

    def configure(self, required_control_ids):
        requested = frozenset(str(value).strip() for value in required_control_ids)
        known = {control.control_id for control in self.descriptor.controls}
        unknown = requested - known
        if unknown:
            raise ValueError(f"unknown {self.descriptor.device_id} controls: {sorted(unknown)}")
        with self._publish_lock:
            with self._lock:
                if requested == self._required:
                    return self._health
            self._release_pressed("profile_reload")
            with self._lock:
                self._required = requested
                session = self._session
                supports_input = self._supports_input
            if not requested:
                return self._set_health(ProviderStatus.DISABLED, "no configured device controls")
            if session is None:
                return self._set_health(ProviderStatus.SUSPENDED, "device disconnected")
            if not supports_input:
                return self._set_health(
                    ProviderStatus.SUSPENDED,
                    "connected firmware has no input-state characteristic")
            return self._set_health(ProviderStatus.STARTING, "awaiting initial input snapshot")

    def begin_session(self, instance_id, *, supports_input):
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError("device session instance ID must be non-empty text")
        lease = object()
        with self._publish_lock:
            with self._lock:
                replaced = self._lease
            if replaced is not None:
                self._disconnect(replaced, "session_replaced")
            with self._lock:
                self._generation += 1
                self._session = instance_id
                self._lease = lease
                self._supports_input = bool(supports_input)
                self._sequence.reset()
                required = bool(self._required)
            if not required:
                self._set_health(ProviderStatus.DISABLED, "no configured device controls")
            elif not supports_input:
                self._set_health(
                    ProviderStatus.SUSPENDED,
                    "connected firmware has no input-state characteristic")
            else:
                self._set_health(ProviderStatus.STARTING, "awaiting initial input snapshot")
        return lease

    def accept_snapshot(self, packet, *, lease):
        with self._publish_lock:
            with self._lock:
                if lease is None or lease is not self._lease:
                    return None
            try:
                snapshot = decode_input_state_snapshot(packet)
            except SnapshotPacketError as exc:
                with self._lock:
                    active = bool(self._required and self._supports_input)
                if active:
                    self._set_health(ProviderStatus.DEGRADED, f"malformed input snapshot: {exc}")
                return None
            with self._lock:
                if not self._required or not self._supports_input:
                    return None
                invalid_bits = snapshot.pressed_mask & ~self.descriptor.allowed_bit_mask
                if not invalid_bits:
                    disposition = self._sequence.classify(snapshot.sequence)
                    if disposition.accepted:
                        desired = {
                            control.control_id for control in self.descriptor.controls
                            if control.control_id in self._required
                            and snapshot.pressed_mask & (1 << control.bit)
                        }
                        current = set(self._pressed)
                        self._pressed = set(desired)
                        instance_id = self._session
            if invalid_bits:
                self._set_health(
                    ProviderStatus.DEGRADED,
                    f"input snapshot sets undefined controls: 0x{invalid_bits:x}")
                return None
            if not disposition.accepted:
                return disposition
            now = time.monotonic()
            by_id = {control.control_id: control for control in self.descriptor.controls}
            events = [InputEvent(
                self.source_id, control_id, InputPhase.RELEASED, now, sequence=snapshot.sequence,
                metadata={
                    "device_id": self.descriptor.device_id,
                    "device_instance": instance_id,
                    "bit": by_id[control_id].bit,
                    "snapshot": True,
                }) for control_id in sorted(current - desired)]
            events.extend(InputEvent(
                self.source_id, control_id, InputPhase.PRESSED, now, sequence=snapshot.sequence,
                metadata={
                    "device_id": self.descriptor.device_id,
                    "device_instance": instance_id,
                    "bit": by_id[control_id].bit,
                    "snapshot": True,
                }) for control_id in sorted(desired - current))
            self._set_health(ProviderStatus.RUNNING, f"input snapshot sequence {snapshot.sequence}")
            if events:
                self._publish_events(tuple(events), "device_input_snapshot")
            return disposition

    def run_if_current(self, lease, callback):
        # Motion carries no provider state, so this deliberately does not take _publish_lock: that
        # lock orders event and health publication, and holding it across a motion callback would
        # block begin_session/disconnect on the other transport during handover. At most one
        # in-flight sample can follow a lease replacement, which is harmless for rotation deltas.
        if not callable(callback):
            raise TypeError("session callback must be callable")
        with self._lock:
            if lease is None or lease is not self._lease:
                return False
        callback()
        return True

    def _release_pressed(self, reason):
        with self._publish_lock:
            with self._lock:
                pressed = tuple(sorted(self._pressed))
                self._pressed.clear()
                sequence = self._sequence.last
            if pressed:
                now = time.monotonic()
                self._publish_events(tuple(InputEvent(
                    self.source_id, control_id, InputPhase.RELEASED, now, sequence=sequence,
                    metadata={"synthetic": True, "reason": reason})
                    for control_id in pressed), reason)

    def _disconnect(self, lease, reason):
        with self._lock:
            if lease is None or lease is not self._lease:
                return None
            sequence = self._sequence.last
            self._pressed.clear()
            self._session = None
            self._lease = None
            self._supports_input = False
            self._sequence.reset()
            enabled = bool(self._required)
        self._publish_events((InputEvent(
            self.source_id, None, InputPhase.DISCONNECTED,
            sequence=sequence, metadata={"reason": reason}),), reason)
        return self._set_health(
            ProviderStatus.SUSPENDED if enabled else ProviderStatus.DISABLED,
            "device disconnected" if enabled else "no configured device controls")

    def disconnect(self, reason="device_disconnect", *, lease):
        with self._publish_lock:
            return self._disconnect(lease, reason)

    def reconcile(self, reason="manual"):
        # Snapshot devices have no host-side physical-state query. The next accepted full snapshot
        # repairs missed edges; disconnect is the fail-safe release boundary.
        return None

    def stop(self, reason="shutdown"):
        with self._publish_lock:
            with self._lock:
                lease = self._lease
            if lease is not None:
                self._disconnect(lease, reason)
            with self._lock:
                self._required = frozenset()
            return self._set_health(ProviderStatus.STOPPED, reason)


__all__ = (
    "SequenceDisposition",
    "SnapshotInputProvider",
)
