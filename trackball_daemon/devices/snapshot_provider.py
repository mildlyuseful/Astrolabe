"""Normalized input provider backed by versioned BLE state snapshots."""

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
        self._required = frozenset()
        self._pressed = set()
        self._session = None
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
        health = ProviderHealth(
            self.source_id, status, detail, generation=self._generation)
        with self._lock:
            self._health = health
        self._publish_health_callback(health)
        return health

    def configure(self, required_control_ids):
        requested = frozenset(str(value).strip() for value in required_control_ids)
        known = {control.control_id for control in self.descriptor.controls}
        unknown = requested - known
        if unknown:
            raise ValueError(f"unknown {self.descriptor.device_id} controls: {sorted(unknown)}")
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
                ProviderStatus.SUSPENDED, "connected firmware has no input-state characteristic")
        return self._set_health(ProviderStatus.STARTING, "awaiting initial input snapshot")

    def begin_session(self, instance_id, *, supports_input):
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError("BLE session instance ID must be non-empty text")
        with self._lock:
            replaced = self._session is not None
        if replaced:
            self.disconnect("session_replaced")
        with self._lock:
            self._generation += 1
            self._session = instance_id
            self._supports_input = bool(supports_input)
            self._sequence.reset()
            required = bool(self._required)
        if not required:
            return self._set_health(ProviderStatus.DISABLED, "no configured device controls")
        if not supports_input:
            return self._set_health(
                ProviderStatus.SUSPENDED, "connected firmware has no input-state characteristic")
        return self._set_health(ProviderStatus.STARTING, "awaiting initial input snapshot")

    def accept_snapshot(self, packet):
        try:
            snapshot = decode_input_state_snapshot(packet)
        except SnapshotPacketError as exc:
            with self._lock:
                active = bool(self._required and self._session and self._supports_input)
            if active:
                self._set_health(ProviderStatus.DEGRADED, f"malformed input snapshot: {exc}")
            return None
        with self._lock:
            if not self._required or self._session is None or not self._supports_input:
                return None
            if snapshot.pressed_mask & ~self.descriptor.allowed_bit_mask:
                invalid_bits = snapshot.pressed_mask & ~self.descriptor.allowed_bit_mask
                self._set_health(
                    ProviderStatus.DEGRADED,
                    f"input snapshot sets undefined controls: 0x{invalid_bits:x}")
                return None
            disposition = self._sequence.classify(snapshot.sequence)
            if not disposition.accepted:
                return disposition
            desired = {
                control.control_id for control in self.descriptor.controls
                if control.control_id in self._required
                and snapshot.pressed_mask & (1 << control.bit)
            }
            current = set(self._pressed)
            self._pressed = set(desired)
            instance_id = self._session
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
            self._publish_events(tuple(events), "ble_input_snapshot")
        return disposition

    def _release_pressed(self, reason):
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

    def disconnect(self, reason="device_disconnect"):
        with self._lock:
            had_session = self._session is not None
            sequence = self._sequence.last
            self._pressed.clear()
            self._session = None
            self._supports_input = False
            self._sequence.reset()
            enabled = bool(self._required)
        if had_session:
            self._publish_events((InputEvent(
                self.source_id, None, InputPhase.DISCONNECTED,
                sequence=sequence, metadata={"reason": reason}),), reason)
        return self._set_health(
            ProviderStatus.SUSPENDED if enabled else ProviderStatus.DISABLED,
            "device disconnected" if enabled else "no configured device controls")

    def reconcile(self, reason="manual"):
        # Snapshot devices have no host-side physical-state query. The next accepted full snapshot
        # repairs missed edges; disconnect is the fail-safe release boundary.
        return None

    def stop(self, reason="shutdown"):
        self.disconnect(reason)
        with self._lock:
            self._required = frozenset()
        return self._set_health(ProviderStatus.STOPPED, reason)


__all__ = (
    "SequenceDisposition",
    "SnapshotInputProvider",
)
