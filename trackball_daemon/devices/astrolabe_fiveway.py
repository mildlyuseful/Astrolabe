# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Adapter for Astrolabe rotation plus versioned input-state snapshots."""

from functools import partial

from .astrolabe_legacy import AstrolabeLegacyAdapter
from .model import NotificationSubscription


class AstrolabeFiveWayAdapter(AstrolabeLegacyAdapter):
    def __init__(self, descriptor, provider, motion_callback, *, protocol_error_callback=None):
        super().__init__(
            descriptor.motion_characteristic,
            motion_callback,
            provider=provider,
            source_id=f"{descriptor.source_id}.motion",
            protocol_error_callback=protocol_error_callback,
            supports_input=True,
        )
        self.descriptor = descriptor

    @property
    def label(self):
        return self.descriptor.label

    @property
    def subscriptions(self):
        with self._lock:
            session = self._session
            lease = self._lease
            return (
                self._rotation_subscription(session, lease),
                NotificationSubscription(
                    self.descriptor.input_characteristic, partial(self._on_input, lease)),
            )

    def _on_input(self, lease, _sender, data):
        with self._lock:
            if lease is None or lease is not self._lease:
                return
        self.provider.accept_snapshot(bytes(data), lease=lease)
