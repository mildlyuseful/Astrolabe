"""Adapter for Astrolabe rotation plus versioned input-state snapshots."""

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
        )
        self.descriptor = descriptor

    @property
    def label(self):
        return self.descriptor.label

    @property
    def subscriptions(self):
        return super().subscriptions + (NotificationSubscription(
            self.descriptor.input_characteristic, self._on_input),)

    def connected(self, session):
        self._session = session
        self.provider.begin_session(session.instance_id, supports_input=True)

    def _on_input(self, _sender, data):
        self.provider.accept_snapshot(bytes(data))
