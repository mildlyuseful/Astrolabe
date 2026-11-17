# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Select the device descriptor that matches a discovered BLE session."""

from .adapter import AstrolabeAdapter
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
        """Advertised services expected for the configured device name."""
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
        # The GATT layout is the protocol; the name only disambiguates when several descriptors
        # share it. A device reached by remembered address may never have reported its name.
        matches = [
            descriptor for descriptor in self._descriptors
            if descriptor.service_uuid in session.gatt.service_uuids
            and descriptor.motion_characteristic in characteristics
            and descriptor.input_characteristic in characteristics
        ]
        named = [item for item in matches if item.matches_name(session.name or config.name)]
        if not matches:
            raise UnsupportedDeviceProtocol(
                f"{session.name or config.name!r} does not expose the Astrolabe device service")
        descriptor = (named or matches)[0]
        return AstrolabeAdapter(
            descriptor,
            self._providers[descriptor.source_id],
            motion_callback,
            protocol_error_callback=protocol_error_callback,
        )
