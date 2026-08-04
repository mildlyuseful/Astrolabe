# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the compatibility-frozen rotation-only Astrolabe protocol."""

from functools import partial
import threading

from .model import MotionSample, NotificationSubscription


class AstrolabeLegacyAdapter:
    def __init__(self, rotation_characteristic, motion_callback, *, provider=None,
                 source_id="ble.astrolabe.legacy", protocol_error_callback=None,
                 supports_input=False):
        if not callable(motion_callback):
            raise TypeError("motion callback must be callable")
        self.rotation_characteristic = rotation_characteristic
        self.motion_callback = motion_callback
        self.provider = provider
        self.source_id = source_id
        self.protocol_error_callback = protocol_error_callback or (lambda _message: None)
        self._supports_input = bool(supports_input)
        self._lock = threading.RLock()
        self._session = None
        self._lease = None

    @property
    def label(self):
        return "legacy Astrolabe rotation"

    @property
    def subscriptions(self):
        with self._lock:
            session = self._session
            lease = self._lease
        return (self._rotation_subscription(session, lease),)

    def _rotation_subscription(self, session, lease):
        return NotificationSubscription(
            self.rotation_characteristic,
            partial(self._on_rotation, session, lease))

    def connected(self, session):
        with self._lock:
            lease = object() if self.provider is None else self.provider.begin_session(
                session.instance_id, supports_input=self._supports_input)
            self._session = session
            self._lease = lease
            return lease

    def disconnected(self, reason="device_disconnect", *, lease):
        with self._lock:
            if lease is None or lease is not self._lease:
                return False
            self._session = None
            self._lease = None
        if self.provider is not None:
            return self.provider.disconnect(reason, lease=lease) is not None
        return True

    def _on_rotation(self, session, lease, _sender, data):
        # Check the lease under the adapter lock, then release it before publishing: this runs at
        # the rotation notification rate, and holding the lock across the motion pipeline would
        # serialize it against connect/disconnect. The lease stays the gate.
        with self._lock:
            if lease is None or lease is not self._lease:
                return
        if self.provider is not None:
            self.provider.run_if_current(
                lease, partial(self._publish_rotation, session, data))
            return
        self._publish_rotation(session, data)

    def _publish_rotation(self, session, data):
        try:
            sample = MotionSample(
                self.source_id,
                bytes(data),
                metadata={
                    "adapter": "astrolabe_legacy",
                    "device_instance": session.instance_id,
                },
            )
        except ValueError as exc:
            self.protocol_error_callback(f"invalid rotation notification: {exc}")
            return
        self.motion_callback(sample)
