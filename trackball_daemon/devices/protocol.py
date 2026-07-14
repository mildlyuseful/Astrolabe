"""Versioned BLE input-state snapshot decoding and RFC1982-style serial ordering."""

from dataclasses import dataclass
from enum import Enum


INPUT_PROTOCOL_VERSION = 1
INPUT_STATE_KIND = 1
INPUT_HEADER_SIZE = 5
MAX_INPUT_PAYLOAD_BYTES = 32


class SnapshotPacketError(ValueError):
    pass


@dataclass(frozen=True)
class InputStateSnapshot:
    sequence: int
    state: bytes

    @property
    def pressed_mask(self):
        return int.from_bytes(self.state, "little")


def encode_input_state_snapshot(sequence, state):
    if type(sequence) is not int or not 0 <= sequence <= 0xFFFF:
        raise ValueError("input snapshot sequence must be an unsigned 16-bit integer")
    state = bytes(state)
    if not 1 <= len(state) <= MAX_INPUT_PAYLOAD_BYTES:
        raise ValueError("input snapshot state length is outside the supported range")
    return bytes((
        INPUT_PROTOCOL_VERSION,
        INPUT_STATE_KIND,
        sequence & 0xFF,
        sequence >> 8,
        len(state),
    )) + state


def decode_input_state_snapshot(packet):
    packet = bytes(packet)
    if len(packet) < INPUT_HEADER_SIZE:
        raise SnapshotPacketError("input snapshot is shorter than its header")
    version, kind, seq_low, seq_high, payload_length = packet[:INPUT_HEADER_SIZE]
    if version != INPUT_PROTOCOL_VERSION:
        raise SnapshotPacketError(f"unsupported input snapshot version: {version}")
    if kind != INPUT_STATE_KIND:
        raise SnapshotPacketError(f"unsupported input snapshot kind: {kind}")
    if not 1 <= payload_length <= MAX_INPUT_PAYLOAD_BYTES:
        raise SnapshotPacketError("input snapshot payload length is outside the supported range")
    if len(packet) != INPUT_HEADER_SIZE + payload_length:
        raise SnapshotPacketError("input snapshot payload length does not match the packet")
    return InputStateSnapshot(seq_low | (seq_high << 8), packet[INPUT_HEADER_SIZE:])


class SequenceDisposition(str, Enum):
    FIRST = "first"
    NEWER = "newer"
    DUPLICATE = "duplicate"
    STALE = "stale"

    @property
    def accepted(self):
        return self in {SequenceDisposition.FIRST, SequenceDisposition.NEWER}


class SequenceGate:
    def __init__(self):
        self._last = None

    @property
    def last(self):
        return self._last

    def reset(self):
        self._last = None

    def classify(self, incoming):
        if type(incoming) is not int or not 0 <= incoming <= 0xFFFF:
            raise ValueError("input sequence must be an unsigned 16-bit integer")
        if self._last is None:
            disposition = SequenceDisposition.FIRST
        else:
            delta = (incoming - self._last) & 0xFFFF
            if delta == 0:
                disposition = SequenceDisposition.DUPLICATE
            elif delta <= 0x7FFF:
                disposition = SequenceDisposition.NEWER
            else:
                disposition = SequenceDisposition.STALE
        if disposition.accepted:
            self._last = incoming
        return disposition
