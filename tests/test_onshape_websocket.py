# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Protocol and resource-boundary tests for the local Onshape WebSocket server."""
import base64
import json
import ssl
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
        client_frame(declared_len=ob._MAX_WS_MESSAGE + 1),
    )
    with pytest.raises(ob._WSProtocolError):
        reader.read_text()
    assert close_code(sent[-1]) == 1009


def test_reader_accepts_current_onshape_sized_unfragmented_message():
    payload = b"x" * 380_204
    reader, sent = reader_for(client_frame(payload))

    assert reader.read_text() == payload
    assert sent == []


def test_reader_rejects_oversized_fragmented_message():
    chunk = b"x" * (ob._MAX_WS_MESSAGE // 2)
    frames = [client_frame(chunk, fin=False)]
    frames.append(client_frame(chunk, opcode=0x0, fin=False))
    frames.append(client_frame(b"x", opcode=0x0, fin=True))
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


def test_discovery_advertises_the_fixed_listener_port():
    connection, sock = handshake_for(websocket_request(path="/3dconnexion/nlproxy"))

    assert connection.handshake_http() is False
    response = sock.sent[-1]
    assert response.startswith(b"HTTP/1.1 200")
    assert json.loads(response.split(b"\r\n\r\n", 1)[1]) == {
        "port": ob.BRIDGE_PORT,
        "version": ob.NLPROXY_VERSION,
    }


def _reset_pointer_chain_state():
    with ob._BRIDGE_STATS_LOCK:
        for key in ob._BRIDGE_STATS:
            ob._BRIDGE_STATS[key] = "" if key == "last_tls_rejection" else 0
    with ob._PAGE_POINTER_LOCK:
        ob._PAGE_POINTER.update({"t": 0.0, "ndc_x": 0.0, "ndc_y": 0.0, "on": False, "version": ""})


def http_request(method, path, body=b"", ctype="application/json"):
    head = ("%s %s HTTP/1.1\r\nOrigin: https://cad.onshape.com\r\n"
            "Content-Type: %s\r\nContent-Length: %d\r\n\r\n" % (method, path, ctype, len(body)))
    return head.encode("ascii") + body


def _status_json():
    connection, sock = handshake_for(http_request("GET", "/trackball/pointer"))
    assert connection.handshake_http() is False
    return json.loads(sock.sent[-1].split(b"\r\n\r\n", 1)[1])


def test_status_page_diagnoses_the_first_broken_chain_link():
    """The machine with the problem is rarely the machine being debugged from, and the status page
    is what its user actually shares. So the page itself must say which link is broken, not leave
    four counters to be cross-referenced by hand."""
    _reset_pointer_chain_state()
    try:
        snap = _status_json()
        assert "never been downloaded" in snap["diagnosis"]
        assert ob.POINTER_SCRIPT_URL in snap["diagnosis"]

        connection, _sock = handshake_for(http_request("GET", "/trackball/pointer.user.js"))
        assert connection.handshake_http() is False
        snap = _status_json()
        assert "downloaded 1" in snap["diagnosis"]
        assert snap["script_downloads"] == 1

        body = json.dumps({"ndc_x": 0.1, "ndc_y": 0.2, "on_canvas": True,
                           "v": ob.USERSCRIPT_VERSION}).encode()
        connection, sock = handshake_for(http_request("POST", "/trackball/pointer", body))
        assert connection.handshake_http() is False
        assert sock.sent[-1].startswith(b"HTTP/1.1 204")
        snap = _status_json()
        assert snap["diagnosis"] == "receiving pointer samples"
        assert snap["posts_received"] == 1
        assert snap["userscript_version"] == ob.USERSCRIPT_VERSION
    finally:
        _reset_pointer_chain_state()


def test_status_page_separates_a_blocked_browser_from_a_misinstalled_script():
    """Script fetched, page totally silent: no discovery probe and no preflight either.

    A wrong or disabled script still lets Onshape's own client probe and still produces preflights.
    Silence on every channel is the browser refusing to let the page off the machine, so the
    diagnosis has to send the user to the permission rather than back to the userscript manager."""
    _reset_pointer_chain_state()
    try:
        connection, _sock = handshake_for(http_request("GET", "/trackball/pointer.user.js"))
        assert connection.handshake_http() is False

        snap = _status_json()
        assert "nothing from the Onshape page has reached this daemon" in snap["diagnosis"]
        assert "Remember my choice for this site" in snap["diagnosis"]
        assert snap["cors_preflights"] == 0

        # One preflight is proof the page can reach us, so the advice must change.
        connection, _sock = handshake_for(http_request("OPTIONS", "/trackball/pointer"))
        assert connection.handshake_http() is False
        snap = _status_json()
        assert snap["cors_preflights"] == 1
        assert "exactly one copy is enabled" in snap["diagnosis"]
    finally:
        _reset_pointer_chain_state()


def test_status_page_reports_unparseable_posts_as_their_own_link():
    _reset_pointer_chain_state()
    try:
        connection, sock = handshake_for(
            http_request("POST", "/trackball/pointer", b"not-json"))
        assert connection.handshake_http() is False
        assert sock.sent[-1].startswith(b"HTTP/1.1 400")
        snap = _status_json()
        assert "none parsed (1 rejected)" in snap["diagnosis"]
    finally:
        _reset_pointer_chain_state()


def test_status_page_reports_certificate_rejection_and_names_the_firefox_store():
    """An explicit TLS certificate alert is the one failure the page's own load cannot show: the
    status page renders fine in a browser that trusts the cert while the userscript's browser
    rejects it. Firefox keeping its own store is exactly that split, so the diagnosis names it."""
    _reset_pointer_chain_state()
    try:
        bridge = ob.OnshapeBridge(on_health_changed=lambda _h: None)
        bridge._enabled.set()

        class Raw:
            def settimeout(self, _timeout):
                pass

            def close(self):
                pass

        class Context:
            @staticmethod
            def wrap_socket(_raw, server_side):
                raise ssl.SSLError(
                    "[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca")

        bridge._handle_raw(Raw(), Context())
        snap = _status_json()
        assert snap["tls_rejections"] == 1
        assert "unknown ca" in snap["last_tls_rejection"]
        assert "rejected this daemon's TLS certificate" in snap["diagnosis"]
        assert "Firefox keeps its own certificate store" in snap["diagnosis"]
    finally:
        _reset_pointer_chain_state()


@pytest.mark.parametrize("path", ["/trackball/pointer.user.js", "/trackball/pointer.js"])
def test_userscript_is_served_from_both_the_recognised_and_legacy_paths(path):
    """`.user.js` is what a userscript manager will install from; `.js` is what older builds
    published and what an already-installed copy may still poll."""
    connection, sock = handshake_for(websocket_request(path=path))

    assert connection.handshake_http() is False
    response = sock.sent[-1]
    assert response.startswith(b"HTTP/1.1 200")
    head, body = response.split(b"\r\n\r\n", 1)
    assert b"Content-Type: application/javascript" in head
    assert body.startswith(b"// ==UserScript==")
    assert ob.USERSCRIPT_VERSION.encode() in body


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


def test_tls_health_requires_explicit_certificate_alert_and_never_demotes_connection():
    assert ob._tls_certificate_rejected(
        ssl.SSLError("[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca"))
    assert not ob._tls_certificate_rejected(
        ssl.SSLEOFError("EOF occurred in violation of protocol"))

    health = []
    bridge = ob.OnshapeBridge(on_health_changed=health.append)
    bridge._enabled.set()
    bridge._connected = True
    bridge._set_health(
        ob.ServiceHealthState.HEALTHY,
        "Onshape controller connected",
    )

    class Raw:
        closed = False

        def settimeout(self, _timeout):
            pass

        def close(self):
            self.closed = True

    class Context:
        @staticmethod
        def wrap_socket(_raw, server_side):
            assert server_side is True
            raise ssl.SSLError(
                "[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca")

    raw = Raw()
    bridge._handle_raw(raw, Context())

    assert raw.closed
    assert health[-1].state is ob.ServiceHealthState.HEALTHY


def test_cert_generation_reports_the_cause_it_actually_hit(tmp_path, monkeypatch):
    """A failure has to name itself. Reporting one canned guess is what made a missing package and
    an unwritable path indistinguishable to the person reading the setup dialog."""
    cert = str(tmp_path / "c.pem")
    key = str(tmp_path / "k.pem")

    def no_openssl(*_args, **_kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(ob, "_HAVE_CRYPTOGRAPHY", False)
    monkeypatch.setattr(ob.subprocess, "run", no_openssl)
    ok, reason = ob.ensure_cert(cert, key)
    assert ok is False
    assert "'cryptography' package is missing" in reason
    assert "no 'openssl' executable is on PATH" in reason

    monkeypatch.setattr(ob.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        ob.subprocess.CalledProcessError(1, "openssl", stderr="unknown option -addext\n")))
    ok, reason = ob.ensure_cert(cert, key)
    assert ok is False
    assert "openssl exited 1: unknown option -addext" in reason


def test_cert_generation_succeeds_and_is_idempotent(tmp_path):
    cert = str(tmp_path / "c.pem")
    key = str(tmp_path / "k.pem")

    assert ob.ensure_cert(cert, key) == (True, "")
    minted = open(cert, "rb").read()
    assert b"BEGIN CERTIFICATE" in minted

    # A second call must not re-mint: the trust the user granted is bound to this exact cert.
    assert ob.ensure_cert(cert, key) == (True, "")
    assert open(cert, "rb").read() == minted


def test_normal_onshape_controller_close_returns_to_waiting_health():
    health = []
    bridge = ob.OnshapeBridge(on_health_changed=health.append)
    bridge._enabled.set()
    conn = ob._OnshapeConn(bridge, FakeSocket(b""))
    bridge._conn = conn
    bridge._connected = True

    bridge._on_conn_closed(conn, "client WebSocket close 1001")

    assert health[-1].state is ob.ServiceHealthState.WAITING
    assert "waiting for reconnect" in health[-1].detail


def test_subscription_without_focus_update_waits_for_explicit_viewport_focus():
    focus_changes = []
    bridge = ob.OnshapeBridge(on_focus_changed=focus_changes.append)
    conn = ob._OnshapeConn(bridge, FakeSocket(b""))
    conn.version_str = lambda: "web"

    conn._dispatch([ob._WAMP.SUBSCRIBE, "topic"])

    assert bridge.is_connected()
    assert not bridge.is_viewport_focused()
    assert focus_changes == []


def test_explicit_focus_before_subscription_is_preserved():
    focus_changes = []
    bridge = ob.OnshapeBridge(on_focus_changed=focus_changes.append)
    conn = ob._OnshapeConn(bridge, FakeSocket(b""))
    conn.version_str = lambda: "web"

    conn._handle_call([
        ob._WAMP.CALL, "focus-early", "3dx_rpc:update", None, {"focus": True}])
    conn._dispatch([ob._WAMP.SUBSCRIBE, "topic"])

    assert conn.focus_reported
    assert bridge.is_connected()
    assert bridge.is_viewport_focused()
    assert focus_changes == [True]


def test_explicit_unfocus_before_subscription_is_not_promoted():
    bridge = ob.OnshapeBridge()
    conn = ob._OnshapeConn(bridge, FakeSocket(b""))
    conn.version_str = lambda: "web"

    conn._handle_call([
        ob._WAMP.CALL, "focus-early", "3dx_rpc:update", None, {"focus": False}])
    conn._dispatch([ob._WAMP.SUBSCRIBE, "topic"])

    assert conn.focus_reported
    assert not bridge.is_viewport_focused()


def test_explicit_focus_updates_publish_fine_grained_bridge_state():
    focus_changes = []
    bridge = ob.OnshapeBridge(on_focus_changed=focus_changes.append)
    conn = ob._OnshapeConn(bridge, FakeSocket(b""))
    conn.version_str = lambda: "web"
    conn.subscribed = True
    bridge._on_conn_ready(conn)

    conn._handle_call([
        ob._WAMP.CALL, "focus-1", "3dx_rpc:update", None, {"focus": True}])
    assert bridge.is_viewport_focused()
    conn._handle_call([
        ob._WAMP.CALL, "focus-2", "3dx_rpc:update", None, {"focus": False}])

    assert not bridge.is_viewport_focused()
    assert focus_changes == [True, False]


def test_replacement_connection_clears_previous_tabs_focused_state():
    focus_changes = []
    bridge = ob.OnshapeBridge(on_focus_changed=focus_changes.append)
    first = ob._OnshapeConn(bridge, FakeSocket(b""))
    first.version_str = lambda: "first"
    first.subscribed = True
    bridge._on_conn_ready(first)
    first._set_focus(True)

    replacement = ob._OnshapeConn(bridge, FakeSocket(b""))
    replacement.version_str = lambda: "replacement"
    replacement.subscribed = True
    bridge._on_conn_ready(replacement)

    assert bridge._conn is replacement
    assert not bridge.is_viewport_focused()
    assert focus_changes == [True, False]
