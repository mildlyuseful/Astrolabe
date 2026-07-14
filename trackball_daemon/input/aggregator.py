"""Thread-safe pressed-set ownership and atomic provider lifecycle release."""

import logging
import threading
import time

from .model import (
    InputControlDescriptor,
    InputEvent,
    InputPhase,
    InputSnapshot,
    InputTransition,
    ProviderHealth,
    ProviderStatus,
)


logger = logging.getLogger("trackball_daemon.input")


class InputAggregator:
    """Normalize edge semantics and publish one final pressed set per input transaction."""

    def __init__(self):
        self._lock = threading.RLock()
        self._pressed = {}
        self._descriptors = {}
        self._health = {}
        self._listeners = []
        self._revision = 0
        self._snapshot = InputSnapshot(0, (), {})

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def add_listener(self, listener):
        if not callable(listener):
            raise TypeError("input listener must be callable")
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener):
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def register_controls(self, descriptors):
        descriptors = tuple(descriptors)
        if any(not isinstance(item, InputControlDescriptor) for item in descriptors):
            raise TypeError("registered controls must be InputControlDescriptor values")
        incoming = {descriptor.token: descriptor for descriptor in descriptors}
        if len(incoming) != len(descriptors):
            raise ValueError("duplicate input control token")
        with self._lock:
            conflicts = set(incoming) & set(self._descriptors)
            if conflicts:
                raise ValueError(f"input controls already registered: {sorted(conflicts)}")
            self._descriptors.update(incoming)

    def descriptors(self, source_id=None):
        with self._lock:
            values = self._descriptors.values()
            if source_id is not None:
                values = [item for item in values if item.source_id == source_id]
            return tuple(sorted(values, key=lambda item: item.token))

    def _build_snapshot_unlocked(self):
        return InputSnapshot(
            self._revision,
            tuple(sorted(self._pressed)),
            dict(self._health),
        )

    def _commit_unlocked(self, events, reason):
        previous = self._snapshot
        self._revision += 1
        snapshot = self._build_snapshot_unlocked()
        self._snapshot = snapshot
        return InputTransition(previous, snapshot, tuple(events), reason)

    def _publish(self, transition, listeners):
        for listener in listeners:
            try:
                listener(transition)
            except Exception:
                logger.exception(
                    "Input listener failed for revision %s", transition.snapshot.revision)

    def accept(self, event):
        if not isinstance(event, InputEvent):
            raise TypeError("input aggregator accepts InputEvent values")
        if event.phase is InputPhase.DISCONNECTED:
            return self.release_source(event.source_id, "disconnected", include_disconnect=True,
                                       disconnected_event=event)
        with self._lock:
            if event.token not in self._descriptors:
                raise ValueError(f"unregistered input control: {event.token}")
            if event.phase is InputPhase.PRESSED:
                if event.token in self._pressed:
                    return None
                self._pressed[event.token] = event
            else:
                if event.token not in self._pressed:
                    return None
                self._pressed.pop(event.token)
            transition = self._commit_unlocked((event,), "event")
            listeners = tuple(self._listeners)
        self._publish(transition, listeners)
        return transition

    def release_source(self, source_id, reason, *, include_disconnect=False,
                       disconnected_event=None):
        with self._lock:
            tokens = sorted(
                token for token, event in self._pressed.items() if event.source_id == source_id)
            events = []
            now = time.monotonic()
            for token in tokens:
                pressed = self._pressed.pop(token)
                events.append(InputEvent(
                    source_id=source_id,
                    control_id=pressed.control_id,
                    phase=InputPhase.RELEASED,
                    timestamp=now,
                    sequence=pressed.sequence,
                    metadata={"synthetic": True, "reason": reason},
                ))
            if include_disconnect:
                events.append(disconnected_event or InputEvent(
                    source_id=source_id,
                    control_id=None,
                    phase=InputPhase.DISCONNECTED,
                    timestamp=now,
                    metadata={"reason": reason},
                ))
            if not events:
                return None
            transition = self._commit_unlocked(events, reason)
            listeners = tuple(self._listeners)
        self._publish(transition, listeners)
        return transition

    def release_all(self, reason="shutdown"):
        with self._lock:
            tokens = sorted(self._pressed)
            if not tokens:
                return None
            now = time.monotonic()
            events = []
            for token in tokens:
                pressed = self._pressed.pop(token)
                events.append(InputEvent(
                    source_id=pressed.source_id,
                    control_id=pressed.control_id,
                    phase=InputPhase.RELEASED,
                    timestamp=now,
                    sequence=pressed.sequence,
                    metadata={"synthetic": True, "reason": reason},
                ))
            transition = self._commit_unlocked(events, reason)
            listeners = tuple(self._listeners)
        self._publish(transition, listeners)
        return transition

    def update_health(self, health):
        if not isinstance(health, ProviderHealth):
            raise TypeError("provider health must be a ProviderHealth value")
        with self._lock:
            if self._health.get(health.source_id) == health:
                return None
            self._health[health.source_id] = health
            events = []
            reason = "health"
            if not health.status.can_own_pressed_controls:
                reason = f"provider_{health.status.value}"
                now = time.monotonic()
                for token in sorted(
                        token for token, event in self._pressed.items()
                        if event.source_id == health.source_id):
                    pressed = self._pressed.pop(token)
                    events.append(InputEvent(
                        source_id=pressed.source_id,
                        control_id=pressed.control_id,
                        phase=InputPhase.RELEASED,
                        timestamp=now,
                        sequence=pressed.sequence,
                        metadata={"synthetic": True, "reason": reason},
                    ))
            transition = self._commit_unlocked(events, reason)
            listeners = tuple(self._listeners)
        self._publish(transition, listeners)
        return transition
