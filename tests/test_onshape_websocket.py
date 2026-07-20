"""Protocol and resource-boundary tests for the local Onshape WebSocket server."""
import base64
import struct

import pytest

from trackball_daemon import onshape_bridge as ob


class FakeSocket:
    def __init__(self, data):
        self.data = bytearray(data)
        self.recv_sizes = []
        self.sent = []
        self.timeouts = []

    def recv(self, size):
        self.recv_sizes.append(size)
        if not self.data:
            return b""
        chunk = bytes(self.data[:size])
        del self.data[:size]
        return chunk

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, value):
        self.timeouts.append(value)


def client_frame(payload=b"", *, opcode=0x1, fin=True, masked=True, declared_len=None,
                 length_encoding=None):
    payload = bytes(payload)
    length = len(payload) if declared_len is None else declared_len
    first = (0x80 if fin else 0) | opcode
    mask_bit = 0x80 if masked else 0
    if length_encoding == 126:
        header = bytes((first, mask_bit | 126)) + struct.pack(">H", length)
    elif length_encoding == 127:
        header = bytes((first, mask_bit | 127)) + struct.pack(">Q", length)
    elif length < 126:
        header = bytes((first, mask_bit | length))
    elif length < 65536:
        header = bytes((first, mask_bit | 126)) + struct.pack(">H", length)
    else:
        header = bytes((first, mask_bit | 127)) + struct.pack(">Q", length)
    if not masked:
        return header + payload
    mask = b"\x11\x22\x33\x44"
    encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + encoded


def reader_for(*frames):
    sent = []
    reader = ob._WSReader(FakeSocket(b"".join(frames)), sent.append)
    return reader, sent


def close_code(frame):
    assert frame[0] == 0x88
    length = frame[1] & 0x7f
    assert length >= 2
    return struct.unpack(">H", frame[2:4])[0]


def test_reader_accepts_masked_text_and_fragmentation_with_ping():
    reader, sent = reader_for(
        client_frame(b'{"a":', fin=False),
        client_frame(b"ok", opcode=0x9),
        client_frame(b"1}", opcode=0x0),
    )

    assert reader.read_text() == b'{"a":1}'
    assert sent == [ob._ws_encode(b"ok", opcode=0xA)]


def test_reader_accepts_extended_length_text_within_limit():
    payload = b"x" * 1024
    reader, _ = reader_for(client_frame(payload))
    assert reader.read_text() == payload


@pytest.mark.parametrize(
    "frame",
    [
        client_frame(b"unmasked", masked=False),
        client_frame(b"orphan", opcode=0x0),
        client_frame(b"binary", opcode=0x2),
        client_frame(b"reserved", opcode=0x41),
        client_frame(b"fragmented ping", opcode=0x9, fin=False),
        client_frame(b"x" * 126, opcode=0x9),
        client_frame(b"\xff"),
    ],
)
def test_reader_rejects_invalid_client_frames(frame):
    reader, sent = reader_for(frame)
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) in {1002, 1007}


def test_reader_rejects_invalid_64_bit_payload_length():
    frame = b"\x81\xff\x80\x00\x00\x00\x00\x00\x00\x00"
    reader, sent = reader_for(frame)
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1002


@pytest.mark.parametrize(
    "frame",
    [
        client_frame(b"x", length_encoding=126),
        client_frame(b"x" * 126, length_encoding=127),
        client_frame(b"x", opcode=0x9, length_encoding=126),
    ],
)
def test_reader_rejects_non_minimal_payload_lengths(frame):
    reader, sent = reader_for(frame)
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1002


def test_reader_rejects_one_byte_close_payload():
    reader, sent = reader_for(client_frame(b"x", opcode=0x8))
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1002


@pytest.mark.parametrize("code", [999, 1004, 1005, 1006, 1015, 2999, 5000])
def test_reader_rejects_invalid_close_codes(code):
    reader, sent = reader_for(client_frame(struct.pack(">H", code), opcode=0x8))
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1002


def test_reader_rejects_invalid_utf8_close_reason():
    payload = struct.pack(">H", 1000) + b"\xff"
    reader, sent = reader_for(client_frame(payload, opcode=0x8))
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1007


def test_reader_rejects_new_text_frame_during_fragmentation():
    reader, sent = reader_for(
        client_frame(b"first", fin=False),
        client_frame(b"second", fin=True),
    )
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1002


def test_reader_rejects_oversized_frame_before_reading_payload():
    reader, sent = reader_for(
        client_frame(declared_len=ob._MAX_WS_FRAME + 1),
    )
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1009


def test_reader_rejects_oversized_fragmented_message():
    chunk = b"x" * (ob._MAX_WS_FRAME - 1)
    frames = [client_frame(chunk, fin=False)]
    frames.extend(client_frame(chunk, opcode=0x0, fin=False) for _ in range(3))
    frames.append(client_frame(chunk, opcode=0x0, fin=True))
    reader, sent = reader_for(*frames)

    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1009


def test_reader_limits_fragment_count():
    frames = [client_frame(b"x", fin=False)]
    frames.extend(
        client_frame(b"x", opcode=0x0, fin=index == ob._MAX_WS_FRAGMENTS - 1)
        for index in range(ob._MAX_WS_FRAGMENTS)
    )
    reader, sent = reader_for(*frames)

    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1009


def test_reader_replies_to_valid_close_and_terminates():
    payload = struct.pack(">H", 1000)
    reader, sent = reader_for(client_frame(payload, opcode=0x8))
    with pytest.raises(ob._ConnDead):
        reader.read_text()
    assert sent == [ob._ws_encode(payload, opcode=0x8)]


def websocket_request(*, method="GET", path="/", http_version="HTTP/1.1",
                      connection="Upgrade", version="13", protocol="wamp",
                      key="dGhlIHNhbXBsZSBub25jZQ=="):
    return (
        f"{method} {path} {http_version}\r\n"
        "Origin: https://cad.onshape.com\r\n"
        "Upgrade: websocket\r\n"
        f"Connection: {connection}\r\n"
        f"Sec-WebSocket-Version: {version}\r\n"
        f"Sec-WebSocket-Protocol: {protocol}\r\n"
        f"Sec-WebSocket-Key: {key}\r\n\r\n"
    ).encode("ascii")


def handshake_for(request):
    sock = FakeSocket(request)
    bridge = type("Bridge", (), {"_log": None})()
    return ob._OnshapeConn(bridge, sock), sock


@pytest.mark.parametrize(
    "request_bytes",
    [
        websocket_request(method="POST"),
        websocket_request(path="/unexpected"),
        websocket_request(http_version="HTTP/1.0"),
        websocket_request(connection="keep-alive"),
        websocket_request(version="12"),
        websocket_request(protocol="other"),
        websocket_request(protocol="WAMP"),
    ],
)
def test_websocket_upgrade_requires_rfc6455_wamp_headers(request_bytes):
    connection, sock = handshake_for(request_bytes)
    assert connection.handshake_http() is False
    assert sock.sent[-1].startswith(b"HTTP/1.1 400")


@pytest.mark.parametrize(
    "key",
    [
        "not-base64!",
        base64.b64encode(b"too short").decode("ascii"),
        base64.b64encode(b"x" * 17).decode("ascii"),
    ],
)
def test_websocket_upgrade_requires_16_byte_base64_key(key):
    connection, sock = handshake_for(websocket_request(key=key))
    assert connection.handshake_http() is False
    assert sock.sent[-1].startswith(b"HTTP/1.1 400")


def test_valid_websocket_upgrade_selects_wamp():
    connection, sock = handshake_for(websocket_request(protocol="other, wamp"))
    assert connection.handshake_http() is True
    response = sock.sent[-1]
    assert response.startswith(b"HTTP/1.1 101")
    assert b"Sec-WebSocket-Protocol: wamp" in response
    assert sock.timeouts == [10.0, 1.0]
