# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""BLE adapter for the Astrolabe protocol: rotation notifications plus input-state snapshots."""

from functools import partial
import threading

from .model import MotionSample, NotificationSubscription


class AstrolabeAdapter:
    def __init__(self, descriptor, provider, motion_callback, *, protocol_error_callback=None):
        if not callable(motion_callback):
            raise TypeError("motion callback must be callable")
        self.descriptor = descriptor
        self.provider = provider
        self.motion_callback = motion_callback
        self.source_id = f"{descriptor.source_id}.motion"
        self.protocol_error_callback = protocol_error_callback or (lambda _message: None)
        self._lock = threading.RLock()
        self._session = None
        self._lease = None

    @property
    def label(self):
        return self.descriptor.label

    @property
    def subscriptions(self):
        with self._lock:
            session, lease = self._session, self._lease
        return (
            NotificationSubscription(
                self.descriptor.motion_characteristic, partial(self._on_rotation, session, lease)),
            NotificationSubscription(
                self.descriptor.input_characteristic, partial(self._on_input, lease)),
        )

    def connected(self, session):
        with self._lock:
            lease = self.provider.begin_session(session.instance_id, supports_input=True)
            self._session = session
            self._lease = lease
            return lease

    def disconnected(self, reason="device_disconnect", *, lease):
        with self._lock:
            if lease is None or lease is not self._lease:
                return False
            self._session = None
            self._lease = None
        return self.provider.disconnect(reason, lease=lease) is not None

    def _current(self, lease):
        with self._lock:
            return lease is not None and lease is self._lease

    def _on_rotation(self, session, lease, _sender, data):
        # The lease is checked under the lock, which is then released before publishing: this runs
        # at the notification rate and must not serialize against connect/disconnect.
        if self._current(lease):
            self.provider.run_if_current(lease, partial(self._publish_rotation, session, data))

    def _on_input(self, lease, _sender, data):
        if self._current(lease):
            self.provider.accept_snapshot(bytes(data), lease=lease)

    def _publish_rotation(self, session, data):
        try:
            sample = MotionSample(
                self.source_id, bytes(data),
                metadata={"device_instance": session.instance_id})
        except ValueError as exc:
            self.protocol_error_callback(f"invalid rotation notification: {exc}")
            return
        self.motion_callback(sample)
