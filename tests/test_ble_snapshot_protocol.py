"""BLE snapshot vectors, sequence ordering, and normalized provider lifecycle."""

import pytest

from trackball_daemon.devices import (
    SequenceDisposition,
    SequenceGate,
    SnapshotInputProvider,
    SnapshotPacketError,
    builtin_device_descriptors,
    decode_input_state_snapshot,
    encode_input_state_snapshot,
)
from trackball_daemon.input import InputAggregator, InputPhase, ProviderStatus


def _descriptor(device_id="astrolabe_5way"):
    return next(item for item in builtin_device_descriptors() if item.device_id == device_id)


def _provider(device_id="astrolabe_5way", required=None):
    aggregator = InputAggregator()
    provider = SnapshotInputProvider(
        _descriptor(device_id), aggregator.accept_many, aggregator.update_health)
    aggregator.register_provider(provider)
    if required is None:
        required = [control.control_id for control in provider.descriptor.controls]
    provider.configure(required)
    return provider, aggregator


def test_input_snapshot_packet_vector_is_versioned_little_endian_and_round_trips():
    packet = encode_input_state_snapshot(0x1234, b"\x15")
    assert packet == b"\x01\x01\x34\x12\x01\x15"
    decoded = decode_input_state_snapshot(packet)
    assert decoded.sequence == 0x1234
    assert decoded.state == b"\x15"
    assert decoded.pressed_mask == 0x15


@pytest.mark.parametrize("packet, message", [
    (b"", "shorter"),
    (b"\x02\x01\x00\x00\x01\x00", "version"),
    (b"\x01\x02\x00\x00\x01\x00", "kind"),
    (b"\x01\x01\x00\x00\x00", "supported range"),
    (b"\x01\x01\x00\x00\x02\x00", "does not match"),
    (b"\x01\x01\x00\x00\x01\x00\x00", "does not match"),
])
def test_malformed_snapshot_vectors_are_rejected(packet, message):
    with pytest.raises(SnapshotPacketError, match=message):
        decode_input_state_snapshot(packet)


def test_unsigned_sequence_gate_handles_duplicate_stale_half_range_and_wraparound():
    gate = SequenceGate()
    assert gate.classify(0xFFFE) is SequenceDisposition.FIRST
    assert gate.classify(0xFFFE) is SequenceDisposition.DUPLICATE
    assert gate.classify(0xFFFD) is SequenceDisposition.STALE
    assert gate.classify(0xFFFF) is SequenceDisposition.NEWER
    assert gate.classify(0x0000) is SequenceDisposition.NEWER
    assert gate.classify(0x8000) is SequenceDisposition.STALE
    assert gate.last == 0


def test_full_snapshots_repair_missed_edges_in_one_atomic_transition():
    provider, aggregator = _provider()
    provider.begin_session("AA:BB#1", supports_input=True)
    transitions = []
    aggregator.add_listener(transitions.append)

    assert provider.accept_snapshot(encode_input_state_snapshot(10, b"\x11")) \
        is SequenceDisposition.FIRST  # Up + Center
    assert aggregator.snapshot().pressed_tokens == (
        "ble.astrolabe:fiveway.center", "ble.astrolabe:fiveway.up")

    assert provider.accept_snapshot(encode_input_state_snapshot(13, b"\x02")) \
        is SequenceDisposition.NEWER  # missed 11/12; now Down only
    change = transitions[-1]
    assert change.reason == "ble_input_snapshot"
    assert [(event.control_id, event.phase) for event in change.events] == [
        ("fiveway.center", InputPhase.RELEASED),
        ("fiveway.up", InputPhase.RELEASED),
        ("fiveway.down", InputPhase.PRESSED),
    ]
    assert change.snapshot.pressed_tokens == ("ble.astrolabe:fiveway.down",)


def test_duplicate_stale_malformed_and_undefined_bits_do_not_mutate_pressed_state():
    provider, aggregator = _provider()
    provider.begin_session("device#1", supports_input=True)
    provider.accept_snapshot(encode_input_state_snapshot(100, b"\x01"))
    revision = aggregator.snapshot().revision

    assert provider.accept_snapshot(encode_input_state_snapshot(100, b"\x00")) \
        is SequenceDisposition.DUPLICATE
    assert provider.accept_snapshot(encode_input_state_snapshot(99, b"\x00")) \
        is SequenceDisposition.STALE
    assert provider.accept_snapshot(b"bad") is None
    assert provider.health.status is ProviderStatus.DEGRADED
    assert provider.accept_snapshot(encode_input_state_snapshot(101, b"\x80")) is None
    assert aggregator.snapshot().pressed_tokens == ("ble.astrolabe:fiveway.up",)
    assert aggregator.snapshot().revision > revision  # health is observable; input state is stable

    # The undefined-bits packet did not consume sequence 101, so a valid packet at 101 is accepted.
    assert provider.accept_snapshot(encode_input_state_snapshot(101, b"\x00")) \
        is SequenceDisposition.NEWER
    assert aggregator.snapshot().pressed_tokens == ()


def test_reconnect_resets_sequence_baseline_and_disconnect_releases_every_hold():
    provider, aggregator = _provider()
    provider.begin_session("device#1", supports_input=True)
    provider.accept_snapshot(encode_input_state_snapshot(0xFFFE, b"\x05"))
    assert len(aggregator.snapshot().pressed_tokens) == 2
    transitions = []
    aggregator.add_listener(transitions.append)

    provider.disconnect()

    disconnected = transitions[-2]
    assert [event.phase for event in disconnected.events] == [
        InputPhase.RELEASED, InputPhase.RELEASED, InputPhase.DISCONNECTED]
    assert aggregator.snapshot().pressed_tokens == ()
    assert provider.health.status is ProviderStatus.SUSPENDED

    provider.begin_session("device#2", supports_input=True)
    assert provider.accept_snapshot(encode_input_state_snapshot(3, b"\x02")) \
        is SequenceDisposition.FIRST
    assert aggregator.snapshot().pressed_tokens == ("ble.astrolabe:fiveway.down",)


def test_required_control_filter_and_legacy_session_do_not_expose_phantom_presses():
    provider, aggregator = _provider(required=("fiveway.center",))
    provider.begin_session("legacy#1", supports_input=False)
    assert provider.health.status is ProviderStatus.SUSPENDED
    assert provider.accept_snapshot(encode_input_state_snapshot(1, b"\x1f")) is None
    assert aggregator.snapshot().pressed_tokens == ()

    provider.begin_session("modern#1", supports_input=True)
    provider.accept_snapshot(encode_input_state_snapshot(1, b"\x1f"))
    assert aggregator.snapshot().pressed_tokens == ("ble.astrolabe:fiveway.center",)


def test_provider_rejects_unknown_control_configuration():
    provider, _aggregator = _provider(required=())
    with pytest.raises(ValueError, match="unknown astrolabe_5way controls"):
        provider.configure(("fiveway.nonexistent",))
