# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

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
    InputProvider,
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
        self._providers = {}
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

    def register_provider(self, provider):
        if not isinstance(provider, InputProvider):
            raise TypeError("registered provider must implement InputProvider")
        with self._lock:
            if provider.source_id in self._providers:
                raise ValueError(f"input provider already registered: {provider.source_id}")
            self.register_controls(provider.controls)
            self._providers[provider.source_id] = provider
        self.update_health(provider.health)

    def configure_provider(self, source_id, required_control_ids):
        with self._lock:
            try:
                provider = self._providers[source_id]
            except KeyError as exc:
                raise ValueError(f"unknown input provider: {source_id}") from exc
        return provider.configure(required_control_ids)

    def shutdown(self, reason="shutdown"):
        with self._lock:
            providers = tuple(self._providers.values())
        for provider in providers:
            try:
                provider.stop(reason)
            except Exception:
                logger.exception("Input provider %s failed to stop", provider.source_id)
                self.release_source(provider.source_id, f"{reason}_stop_failure")
        self.release_all(reason)

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
        return self.accept_many((event,), "event")

    def accept_many(self, events, reason="event"):
        """Apply a provider batch against one final pressed set and publish at most once."""
        events = tuple(events)
        if any(not isinstance(event, InputEvent) for event in events):
            raise TypeError("input aggregator accepts InputEvent values")
        if not events:
            return None
        with self._lock:
            accepted = []
            for event in events:
                if event.phase is InputPhase.DISCONNECTED:
                    now = event.timestamp
                    for token in sorted(
                            token for token, pressed in self._pressed.items()
                            if pressed.source_id == event.source_id):
                        pressed = self._pressed.pop(token)
                        accepted.append(InputEvent(
                            source_id=pressed.source_id,
                            control_id=pressed.control_id,
                            phase=InputPhase.RELEASED,
                            timestamp=now,
                            sequence=pressed.sequence,
                            metadata={"synthetic": True, "reason": reason},
                        ))
                    accepted.append(event)
                    continue
                if event.token not in self._descriptors:
                    raise ValueError(f"unregistered input control: {event.token}")
                if event.phase is InputPhase.PRESSED:
                    if event.token in self._pressed:
                        continue
                    self._pressed[event.token] = event
                else:
                    if event.token not in self._pressed:
                        continue
                    self._pressed.pop(event.token)
                accepted.append(event)
            if not accepted:
                return None
            transition = self._commit_unlocked(accepted, reason)
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
