"""Adapter for the compatibility-frozen rotation-only Astrolabe protocol."""

from .model import MotionSample, NotificationSubscription


class AstrolabeLegacyAdapter:
    def __init__(self, rotation_characteristic, motion_callback, *, provider=None,
                 source_id="ble.astrolabe.legacy", protocol_error_callback=None):
        if not callable(motion_callback):
            raise TypeError("motion callback must be callable")
        self.rotation_characteristic = rotation_characteristic
        self.motion_callback = motion_callback
        self.provider = provider
        self.source_id = source_id
        self.protocol_error_callback = protocol_error_callback or (lambda _message: None)
        self._session = None

    @property
    def label(self):
        return "legacy Astrolabe rotation"

    @property
    def subscriptions(self):
        return (NotificationSubscription(
            self.rotation_characteristic, self._on_rotation),)

    def connected(self, session):
        self._session = session
        if self.provider is not None:
            self.provider.begin_session(session.instance_id, supports_input=False)

    def disconnected(self, reason="device_disconnect"):
        if self.provider is not None:
            self.provider.disconnect(reason)
        self._session = None

    def _on_rotation(self, _sender, data):
        session = self._session
        try:
            sample = MotionSample(
                self.source_id,
                bytes(data),
                metadata={
                    "adapter": "astrolabe_legacy",
                    "device_instance": session.instance_id if session else None,
                },
            )
        except ValueError as exc:
            self.protocol_error_callback(f"invalid rotation notification: {exc}")
            return
        self.motion_callback(sample)
