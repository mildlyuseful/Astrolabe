# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Select built-in protocol adapters from discovered GATT and data descriptors."""

from .astrolabe_fiveway import AstrolabeFiveWayAdapter
from .astrolabe_legacy import AstrolabeLegacyAdapter
from .model import BleConnectionConfig, DeviceSession


class UnsupportedDeviceProtocol(RuntimeError):
    pass


class DeviceAdapterRegistry:
    def __init__(self, descriptors, providers):
        self._descriptors = tuple(descriptors)
        self._providers = dict(providers)
        if {item.source_id for item in self._descriptors} != set(self._providers):
            raise ValueError("adapter registry requires exactly one provider per descriptor")

    @property
    def providers(self):
        return tuple(self._providers[item.source_id] for item in self._descriptors)

    def discovery_service_uuids(self, config):
        """Advertised services expected for the configured name/rotation protocol."""
        config = BleConnectionConfig.from_value(config)
        return frozenset(
            descriptor.service_uuid for descriptor in self._descriptors
            if descriptor.matches_name(config.name)
            and descriptor.motion_characteristic == config.rotation_characteristic
        )

    def select(self, config, session, motion_callback, protocol_error_callback=None):
        config = BleConnectionConfig.from_value(config)
        if not isinstance(session, DeviceSession):
            raise TypeError("adapter selection requires a DeviceSession")
        characteristics = session.gatt.characteristic_uuids
        if config.rotation_characteristic not in characteristics:
            raise UnsupportedDeviceProtocol(
                f"rotation characteristic {config.rotation_characteristic} was not discovered")

        matched = [descriptor for descriptor in self._descriptors
                   if descriptor.matches_name(session.name or config.name)
                   and descriptor.service_uuid in session.gatt.service_uuids
                   and descriptor.motion_characteristic == config.rotation_characteristic]
        for descriptor in matched:
            if descriptor.input_characteristic in characteristics:
                return AstrolabeFiveWayAdapter(
                    descriptor,
                    self._providers[descriptor.source_id],
                    motion_callback,
                    protocol_error_callback=protocol_error_callback,
                )

        descriptor = matched[0] if matched else None
        provider = self._providers[descriptor.source_id] if descriptor else None
        source_id = f"{descriptor.source_id}.motion" if descriptor else "ble.astrolabe.legacy"
        return AstrolabeLegacyAdapter(
            config.rotation_characteristic,
            motion_callback,
            provider=provider,
            source_id=source_id,
            protocol_error_callback=protocol_error_callback,
        )
