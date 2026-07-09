"""In-process Onshape bridge -- the browser analogue of the SolidWorks COM driver.

Onshape runs in a browser and has NATIVE 3Dconnexion SpaceMouse support: its page ships the
3Dconnexion client library, which (on Windows/Mac) connects to a LOCAL service -- the "NL-Proxy"
/ "3DxWare for web" bridge -- at the loopback endpoint ``127.51.68.120:8181`` over a TLS
WebSocket. We don't own a SpaceMouse, so instead of synthesizing mouse drags we stand up OUR OWN
server impersonating that service: Onshape connects to us, hands us its camera, and we feed in the
trackball's orbit/pan/zoom. See ``docs/apps/onshape.md`` for the full reverse-engineered
protocol + the cert/trust setup (and the prior art it is based on: RmStorm/spacenav-ws).

This driver lives inside the daemon process, parallel to the broker and the SolidWorks driver, and
mirrors ``SolidWorksDriver``'s public surface so ``app.py`` drives it identically:
``submit/set_rate/set_scheme/start/stop/is_connected/version`` + the
``on_connection_changed(connected, version)`` callback.

Threading model (mirrors SolidWorksDriver exactly):
  * submit() is called on the BLE thread and ONLY accumulates the per-frame orbit/pan/zoom delta --
    it never blocks and never touches a socket.
  * a SERVER thread runs the TLS accept loop on 127.51.68.120:8181. Each accepted connection gets a
    READER thread (parse WAMP frames, answer the handshake, resolve read/write replies by call id).
  * a WORKER thread waits until a browser client is subscribed+focused, then at rate_hz coalesces
    the accumulated delta and runs ONE navigation step: read the camera (view.affine), apply the
    orbit/pan/zoom math, write the new camera back -- wrapped in the protocol's motion/transaction
    framing. Every network round-trip happens here, never on the BLE thread.
  * on_connection_changed(connected, version) fires when a client completes the handshake / drops,
    so the tray/UI status line updates -- the analogue of the SW driver's connect/disconnect.

The wire protocol is WAMP v1 (JSON arrays over a "wamp"-subprotocol WebSocket). The server speaks
first (WELCOME); the client registers prefixes, creates a 3dmouse then a 3dcontroller, subscribes
to the controller topic, and reports focus. To navigate, the SERVER reads/writes the app's scene
accessors -- it sends an EVENT to the controller topic carrying a nested CALL (self:read /
self:update) and the client answers with a CALLRESULT (or a CALLERROR for an unsupported property,
which is harmless and best-effort). Camera math: view.affine is a 4x4 camera-to-world matrix
(row-major flat, translation in the last column); we decode it to eye + a right/up/forward basis,
apply the SAME orbit/pan/zoom-about-a-pivot used by the Fusion add-in, and re-encode.

Degrades gracefully: if the cert can't be generated/loaded the server simply doesn't start (logged
once) and submit()/flush become harmless no-ops -- the rest of the daemon runs normally. Only
stdlib ``ssl`` + a tiny hand-rolled WebSocket are used (no FastAPI/uvicorn/numpy); cert GENERATION
uses ``cryptography`` if importable else the ``openssl`` CLI, both guarded.
"""
import base64
import hashlib
import json
import math
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

from .paths import user_config_dir
from .util import get_logger

# Cert generation is optional and guarded -- the WSS server itself needs only stdlib ssl. We mint a
# unique self-signed cert (CN + IP SAN) at setup; cryptography is the clean path, openssl the
# fallback. _HAVE_CRYPTOGRAPHY gates the in-process path; the package still imports without either.
try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    import ipaddress
    _HAVE_CRYPTOGRAPHY = True
except Exception:                       # pragma: no cover - exercised only without cryptography
    _HAVE_CRYPTOGRAPHY = False


# --- the loopback endpoint Onshape's 3Dconnexion client probes (see notes doc) ----------------
BRIDGE_HOST = "127.51.68.120"
BRIDGE_PORT = 8181
# Reported by GET /3dconnexion/nlproxy so Onshape thinks a recent 3DxWare service is present.
NLPROXY_VERSION = "1.4.8.21486"
# WAMP WELCOME server-ident. Free-form (the real proxy sends a 3Dconnexion copyright string; the
# spacenav-ws bridge sends its own and Onshape accepts it), so we send a clear, honest one.
WELCOME_IDENT = "NLProxy v%s (Trackball Daemon bridge)" % NLPROXY_VERSION

# --- tuning: baseline sign/scale + scene orientation (mirrors the Fusion add-in / SW driver) ----
# The nav-delta contract feeds (ox,oy,oz) = orbit about (camera right, up, forward) in radians,
# (px,py) = pan, zoom = zoom; values ARRIVE ALREADY SCALED by the active app's bindings. These bake
# in the baseline feel and are expected to need a sign/up-axis pass once live (see notes doc).
ORBIT_SIGN = (-1.0, -1.0, 1.0)    # (ox=pitch about right, oy=yaw about up, oz=roll about forward)
# turntable azimuth axis. Onshape's scene up may be Y or Z -- VERIFY live (only affects turntable:
# toggle it and watch whether verticals stay vertical). (0,1,0) = Y-up (WebGL convention) as a start.
WORLD_UP = (0.0, 1.0, 0.0)
PAN_SIGN = (1.0, -1.0)            # pan along (camera-right, camera-up); up negated like the add-in
PAN_SCALE = 0.14                  # pan delta -> fraction of the view half-extent (Fusion baseline)
ZOOM_SCALE = 0.25                 # zoom delta -> fraction (perspective: dolly; ortho: extent scale)
ZOOM_SIGN = 1.0                   # twist -> zoom direction

# view.affine layout. False = the ROW-vector layout real ONSHAPE emits (verified live: the last
# column at indices 3,7,11 is [0,0,0], so the homogeneous column is [0,0,0,1] and the translation/
# eye is in the last ROW at indices 12,13,14 -- the convention spacenav-ws uses). True would be the
# column-translation layout (eye at indices 3,7,11) the 3Dconnexion three.js sample emits. If orbit/
# pan ever come out transposed or coupled against a different app, flip this.
AFFINE_TRANSLATION_IN_COLUMN = False

# "view"/"selection"/"cursor" orbit pivot uses Onshape's navlib hit-test: raycast a screen point
# and pivot about the first surface hit (like Onshape's own right-click orbit). These are the ray
# aperture (cone diameter) as fractions of the view half-extent, tried smallest-first -- a narrow
# ray, widening until something is hit. If none hit at the widest, we fall back to the model
# centre (i.e. model orbit). Onshape does the actual raycast; we just set the ray + read the result.
HIT_APERTURES = (0.03, 0.1, 0.3)

# Under-cursor orbit: fractions of the *web-content* area eaten by Onshape's in-page chrome
# (header/toolbar/panels). Env defaults only -- config.onshape.canvas_inset / canvas_auto_left
# (via set_canvas) supersede these at runtime. The resizable left feature-tree is auto-tracked
# from view.extents' aspect when CANVAS_AUTO_LEFT is on, so usually only Top needs a value
# (~0.05-0.09 of the content height). A wrong Top *gains* the horizontal (see docs §8.14).
def _env_frac(name, default=0.0):
    try:
        return max(0.0, min(0.9, float(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return float(default)


CANVAS_INSET = (_env_frac("TB_ONSHAPE_CANVAS_LEFT", 0.0),
                _env_frac("TB_ONSHAPE_CANVAS_TOP", 0.0),
                _env_frac("TB_ONSHAPE_CANVAS_RIGHT", 0.0),
                _env_frac("TB_ONSHAPE_CANVAS_BOTTOM", 0.0))
CANVAS_AUTO_LEFT = os.environ.get("TB_ONSHAPE_CANVAS_AUTO_LEFT", "1").strip().lower() not in (
    "0", "false", "no", "off")

# Verbose diagnostics: set TB_ONSHAPE_DEBUG=1 to log focus changes, each gesture's camera
# read/write, and any CALLERROR -- off by default so normal runs don't spam the log.
_DEBUG = bool(os.environ.get("TB_ONSHAPE_DEBUG"))
# Self-test: set TB_ONSHAPE_SPIN=<radians> to inject a continuous synthetic orbit whenever a client
# is connected (also forces focus), so the bridge -> Onshape camera path can be validated end to end
# without the BLE device or the focus/routing gates. 0/unset = off.
try:
    _SPIN = float(os.environ.get("TB_ONSHAPE_SPIN") or 0.0)
except ValueError:
    _SPIN = 0.0

DEFAULT_FLUSH_HZ = 30.0
_RPC_TIMEOUT = 2.0           # seconds to wait for a client's reply to a self:read/self:update
_MOTION_IDLE = 0.35          # seconds of no motion before we end the gesture (motion=false)
_PERSP_TTL = 1.0             # cache view.perspective this long (it changes rarely)
_OBJ_TTL = 0.5              # cache model.extents (orbit/zoom pivot) this long
_EXT_TTL = 0.2              # cache view.extents (pan/zoom scale) this long
_TGT_TTL = 0.3              # cache view.target ("view" pivot) this long
# Firefox's top-level client rect INCLUDES the browser chrome (tabs/URL/bookmarks). Cache the
# measured content-top inset so we only BitBlt when the window size changes.
_FF_CONTENT_CACHE = {"key": None, "top": 0}


# --- WAMP v1 message-type tags (JSON arrays [TYPE, ...]) ---------------------------------------
class _WAMP:
    WELCOME = 0
    PREFIX = 1
    CALL = 2
    CALLRESULT = 3
    CALLERROR = 4
    SUBSCRIBE = 5
    UNSUBSCRIBE = 6
    PUBLISH = 7
    EVENT = 8


class _ConnDead(Exception):
    """Raised by a connection's rpc()/reader when the socket is gone or the client went silent."""


class _PropUnsupported(Exception):
    """Client answered a self:read/self:update with a CALLERROR ('... unknown property')."""


def _rand_token(n=16):
    import random
    import string
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def _rand_instance():
    # The real proxy returns a big numeric instance id; mimic that (a string id also works).
    import random
    return random.randint(10 ** 11, 10 ** 13)


# --- tiny vector helpers (3-tuples) -- avoids a numpy dependency -------------------------------
def _v_add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _v_sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _v_scale(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _v_neg(a): return (-a[0], -a[1], -a[2])
def _v_dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _v_len(a): return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _v_normalize(a):
    n = _v_len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 1.0)


def _rodrigues(u, theta, p):
    """Rotate vector p about UNIT axis u by theta (radians) -- Rodrigues' formula. Same helper the
    SolidWorks driver uses; here it rotates the camera basis + the eye-about-pivot offset."""
    ux, uy, uz = u
    px, py, pz = p
    cx = uy * pz - uz * py
    cy = uz * px - ux * pz
    cz = ux * py - uy * px
    d = ux * px + uy * py + uz * pz
    c = math.cos(theta)
    s = math.sin(theta)
    k = d * (1.0 - c)
    return (px * c + cx * s + ux * k, py * c + cy * s + uy * k, pz * c + cz * s + uz * k)


# --- turntable quaternion helpers (compose yaw + pitch into ONE rotation) ----------------------
def _q_from_axis_angle(ax, ay, az, angle):
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < 1e-12 or abs(angle) < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle * 0.5) / n
    return (math.cos(angle * 0.5), ax * s, ay * s, az * s)


def _q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _q_to_axis_angle(q):
    qw = max(-1.0, min(1.0, q[0]))
    angle = 2.0 * math.acos(qw)
    s = math.sqrt(1.0 - qw * qw)
    if s < 1e-9:
        return (0.0, 0.0, 1.0, 0.0)
    return (q[1] / s, q[2] / s, q[3] / s, angle)


# --- view.affine <-> (eye, right, up, back) ---------------------------------------------------
def _decode_affine(m):
    """Decode the 16-float view.affine into (eye, right, up, back) world-space vectors. The matrix
    is camera-to-world; back = camera +Z (points OUT of the screen), so forward = -back."""
    if AFFINE_TRANSLATION_IN_COLUMN:
        right = (m[0], m[4], m[8])
        up = (m[1], m[5], m[9])
        back = (m[2], m[6], m[10])
        eye = (m[3], m[7], m[11])
    else:                                          # row-vector layout (translation in last row)
        right = (m[0], m[1], m[2])
        up = (m[4], m[5], m[6])
        back = (m[8], m[9], m[10])
        eye = (m[12], m[13], m[14])
    return eye, right, up, back


def _encode_affine(eye, right, up, back):
    """Re-encode (eye, right, up, back) into a 16-float view.affine (same layout as _decode_affine)."""
    if AFFINE_TRANSLATION_IN_COLUMN:
        return [right[0], up[0], back[0], eye[0],
                right[1], up[1], back[1], eye[1],
                right[2], up[2], back[2], eye[2],
                0.0, 0.0, 0.0, 1.0]
    return [right[0], right[1], right[2], 0.0,
            up[0], up[1], up[2], 0.0,
            back[0], back[1], back[2], 0.0,
            eye[0], eye[1], eye[2], 1.0]


def _orthonormalize(right, up, back):
    """Re-orthonormalize the camera basis (right-handed: right x up = back) to fight float drift
    accumulated over many incremental rotations."""
    back = _v_normalize(back)
    right = _v_normalize(_v_cross(up, back))
    up = _v_cross(back, right)
    return right, up, back


# --- cert generation (one-time, into the per-user config dir) ----------------------------------
def default_cert_paths():
    d = user_config_dir()
    return str(d / "onshape_cert.pem"), str(d / "onshape_key.pem")


def ensure_cert(cert_path, key_path):
    """Make sure a self-signed cert (CN + IP SAN for 127.51.68.120) exists at the given paths.
    Returns True if present/created. Safe: only writes files in our own config dir -- it does NOT
    touch any trust store (that's a separate, user-confirmed step). Tries cryptography, then the
    openssl CLI."""
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return True
    if _HAVE_CRYPTOGRAPHY:
        try:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, BRIDGE_HOST)])
            san = x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(BRIDGE_HOST))])
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder()
                    .subject_name(name).issuer_name(name)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(days=1))
                    .not_valid_after(now + datetime.timedelta(days=3650))
                    .add_extension(san, critical=False)
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .sign(key, hashes.SHA256()))
            with open(key_path, "wb") as f:
                f.write(key.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()))
            with open(cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            return True
        except Exception:
            get_logger().info("onshape: cryptography cert generation failed", exc_info=False)
    # Fallback: shell out to openssl with an IP SAN (-addext needs OpenSSL >= 1.1.1).
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", key_path, "-out", cert_path, "-days", "3650",
             "-subj", "/CN=%s" % BRIDGE_HOST,
             "-addext", "subjectAltName=IP:%s" % BRIDGE_HOST],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(cert_path) and os.path.exists(key_path)
    except Exception:
        return False


# --- minimal RFC6455 WebSocket framing --------------------------------------------------------
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()


def _ws_encode(payload, opcode=0x1):
    """Server->client frame (never masked). payload is bytes."""
    n = len(payload)
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    return bytes(header) + payload


class _WSReader:
    """Buffered frame reader over a (TLS) socket. Reassembles fragmented text frames and answers
    ping with pong. Raises _ConnDead when the socket closes."""

    def __init__(self, sock, send_fn):
        self._sock = sock
        self._send = send_fn          # send_fn(bytes) -- used to reply to pings
        self._buf = b""

    def _need(self, n):
        while len(self._buf) < n:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue              # idle -- keep waiting; stop() closes the socket to unblock
            except OSError:
                raise _ConnDead()
            if not chunk:
                raise _ConnDead()
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self):
        b0, b1 = self._need(2)
        fin = b0 & 0x80
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._need(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._need(8))[0]
        mask = self._need(4) if masked else b""
        payload = self._need(ln) if ln else b""
        if masked and payload:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return fin, opcode, payload

    def read_text(self):
        """Return the next complete text message (bytes), handling control frames internally."""
        chunks = []
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 0x8:                       # close
                raise _ConnDead()
            if opcode == 0x9:                       # ping -> pong
                self._send(_ws_encode(payload, opcode=0xA))
                continue
            if opcode == 0xA:                       # pong
                continue
            chunks.append(payload)                  # 0x1 text or 0x0 continuation
            if fin:
                return b"".join(chunks)


# --- one browser WebSocket connection (handshake + WAMP read/write) ---------------------------
class _OnshapeConn:
    """Owns a single TLS WebSocket connection: routes the HTTP/WS handshake, runs the WAMP reader
    loop (answering the 3dmouse/3dcontroller/subscribe/focus handshake and resolving read/write
    replies), and exposes synchronous rpc() used by the worker to read/write scene accessors."""

    def __init__(self, bridge, sock):
        self.bridge = bridge
        self.sock = sock
        self._log = bridge._log
        self._send_lock = threading.Lock()
        self.alive = True

        self.session_id = _rand_token(16)
        self.prefixes = {}
        self.mouse_id = None
        self.instance_id = None
        self.topic = None                 # controller topic EVENTs are published to (short form)
        self.metadata = {}
        self.subscribed = False
        self.focus = False

        self._cid = 0
        self._pending = {}                # call_id -> {ev, result, error}
        self._cache = {}                  # prop -> (ts, value)
        self._unsupported = set()         # props the client answered "unknown property"
        self._hit_unsupported = False     # True once we learn this build lacks navlib hit-testing
        self._txn = 1000

    # --- low-level send (frames must not interleave -> guarded by a lock) ----------------------
    def _raw_send(self, data):
        with self._send_lock:
            try:
                self.sock.sendall(data)
            except OSError:
                self.alive = False
                raise _ConnDead()

    def _send_wamp(self, msg_list):
        self._raw_send(_ws_encode(json.dumps(msg_list).encode("utf-8")))

    # --- HTTP routing + WS upgrade. Returns True if upgraded to a WebSocket. -------------------
    def handshake_http(self):
        self.sock.settimeout(10.0)
        head = b""
        while b"\r\n\r\n" not in head:
            try:
                chunk = self.sock.recv(2048)
            except OSError:
                return False
            if not chunk:
                return False
            head += chunk
            if len(head) > 16384:
                return False
        lines = head.split(b"\r\n")
        try:
            method, path, _ = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            return False
        headers = {}
        for ln in lines[1:]:
            if b":" in ln:
                k, v = ln.split(b":", 1)
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        origin = headers.get("origin", "*")

        if method == "OPTIONS":
            self._http(204, "", origin, ctype=None)
            return False
        if path.startswith("/3dconnexion/nlproxy"):
            self._http(200, json.dumps({"port": BRIDGE_PORT, "version": NLPROXY_VERSION}),
                       origin, ctype="application/json")
            return False
        if "websocket" in headers.get("upgrade", "").lower():
            key = headers.get("sec-websocket-key", "")
            resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    "Sec-WebSocket-Accept: %s\r\n"
                    "Sec-WebSocket-Protocol: wamp\r\n\r\n" % _ws_accept(key))
            self._raw_send(resp.encode("latin-1"))
            self.sock.settimeout(1.0)               # short timeout so the reader can poll stop()
            return True
        # Plain GET / -> a tiny status page (handy for the one-time cert-trust visit).
        body = ("<html><body><h1>Trackball Daemon &mdash; Onshape bridge</h1>"
                "<p>This local NL-Proxy emulator is running. Onshape connects to it automatically; "
                "if you can read this with no certificate warning, the cert is trusted.</p>"
                "</body></html>")
        self._http(200, body, origin, ctype="text/html")
        return False

    def _http(self, status, body, origin, ctype="text/plain"):
        reason = {200: "OK", 204: "No Content", 404: "Not Found"}.get(status, "OK")
        data = body.encode("utf-8")
        # Access-Control-Allow-Private-Network: Chromium's Private Network Access preflight for a
        # public page (cad.onshape.com) talking to loopback. Harmless to Firefox.
        out = ["HTTP/1.1 %d %s" % (status, reason),
               "Access-Control-Allow-Origin: %s" % origin,
               "Access-Control-Allow-Methods: GET, OPTIONS",
               "Access-Control-Allow-Headers: *",
               "Access-Control-Allow-Private-Network: true",
               "Connection: close"]
        if ctype is not None:
            out.append("Content-Type: %s" % ctype)
            out.append("Content-Length: %d" % len(data))
        out.append("")
        out.append("")
        try:
            self._raw_send("\r\n".join(out).encode("latin-1") + (data if ctype else b""))
        except _ConnDead:
            pass

    # --- main serve loop ----------------------------------------------------------------------
    def serve(self):
        try:
            if not self.handshake_http():
                return                              # plain HTTP (nlproxy / options / page) -> done
            self._send_wamp([_WAMP.WELCOME, self.session_id, 1, WELCOME_IDENT])
            reader = _WSReader(self.sock, self._raw_send)
            while self.alive and not self.bridge._stop.is_set():
                raw = reader.read_text()
                try:
                    msg = json.loads(raw.decode("utf-8"))
                except Exception:
                    continue
                if isinstance(msg, list) and msg:
                    self._dispatch(msg)
        except _ConnDead:
            pass
        except Exception:
            self._log.info("onshape: connection error", exc_info=False)
        finally:
            self.alive = False
            for box in list(self._pending.values()):     # wake any worker waiting on a reply
                box["ev"].set()
            try:
                self.sock.close()
            except Exception:
                pass
            self.bridge._on_conn_closed(self)

    def _resolve(self, uri):
        if ":" not in uri:
            return uri
        prefix, rest = uri.split(":", 1)
        return self.prefixes.get(prefix, "") + rest

    def _dispatch(self, msg):
        t = msg[0]
        if t == _WAMP.CALLRESULT:
            box = self._pending.get(msg[1])
            if box:
                box["result"] = msg[2] if len(msg) > 2 else None
                box["ev"].set()
        elif t == _WAMP.CALLERROR:
            if _DEBUG:
                self._log.info("onshape WS< CALLERROR %s", msg[1:])
            box = self._pending.get(msg[1])
            if box:
                box["error"] = (msg[2] if len(msg) > 2 else "", msg[3] if len(msg) > 3 else "")
                box["ev"].set()
        elif t == _WAMP.PREFIX:
            if len(msg) >= 3:
                self.prefixes[msg[1]] = msg[2]
        elif t == _WAMP.CALL:
            self._handle_call(msg)
        elif t == _WAMP.SUBSCRIBE:
            self.subscribed = True
            self.focus = True
            self.bridge._on_conn_ready(self)
            self._log.info("onshape: client subscribed (controller %s)", self.instance_id)
        elif t == _WAMP.UNSUBSCRIBE:
            self.focus = False

    def _handle_call(self, msg):
        # [CALL, call_id, proc_uri, *args]
        call_id = msg[1]
        proc_uri = msg[2] if len(msg) > 2 else ""
        args = msg[3:]
        if proc_uri == "3dx_rpc:create":
            kind = args[0] if args else ""
            if kind == "3dconnexion:3dmouse":
                self.mouse_id = _rand_token(16)
                self._send_wamp([_WAMP.CALLRESULT, call_id, {"connexion": self.mouse_id}])
                self._log.info("onshape: created 3dmouse (client lib v%s)",
                               args[1] if len(args) > 1 else "?")
            elif kind == "3dconnexion:3dcontroller":
                self.metadata = args[2] if len(args) > 2 else {}
                self.instance_id = _rand_instance()
                self.topic = "3dconnexion:3dcontroller/%s" % self.instance_id
                self._send_wamp([_WAMP.CALLRESULT, call_id, {"instance": self.instance_id}])
                self._log.info("onshape: created 3dcontroller for client %r v%r",
                               self.metadata.get("name"), self.metadata.get("version"))
            else:
                self._send_wamp([_WAMP.CALLRESULT, call_id, {}])
        elif proc_uri == "3dx_rpc:update" or self._resolve(proc_uri).endswith("#update"):
            payload = args[1] if len(args) > 1 else {}
            if isinstance(payload, dict) and "focus" in payload:
                self.focus = bool(payload["focus"])
                if _DEBUG:
                    self._log.info("onshape: focus -> %s", self.focus)
            self._send_wamp([_WAMP.CALLRESULT, call_id, {}])
        else:
            self._send_wamp([_WAMP.CALLERROR, call_id, "wamp.error.not_found",
                             "RPC %r not registered" % proc_uri])

    # --- synchronous accessor read/write (called on the worker thread) -------------------------
    def _rpc(self, method, args):
        if not self.alive:
            raise _ConnDead()
        self._cid += 1
        cid = "tb.%d" % self._cid
        box = {"ev": threading.Event(), "result": None, "error": None}
        self._pending[cid] = box
        nested = [_WAMP.CALL, cid, method, ""] + list(args)
        try:
            self._send_wamp([_WAMP.EVENT, self.topic, nested])
        except _ConnDead:
            self._pending.pop(cid, None)
            raise
        if not box["ev"].wait(_RPC_TIMEOUT):
            self._pending.pop(cid, None)
            raise _ConnDead()                       # client went silent -> treat as dropped
        self._pending.pop(cid, None)
        if not self.alive:
            raise _ConnDead()
        if box["error"] is not None:
            raise _PropUnsupported(box["error"])
        return box["result"]

    def read(self, prop, ttl=0.0):
        """Read a scene accessor; None if the client doesn't support it (cached so we don't re-ask).
        Raises _ConnDead on a dead/silent connection."""
        if prop in self._unsupported:
            return None
        now = time.monotonic()
        if ttl > 0.0:
            c = self._cache.get(prop)
            if c is not None and now - c[0] < ttl:
                return c[1]
        try:
            val = self._rpc("self:read", [prop])
        except _PropUnsupported:
            self._unsupported.add(prop)
            return None
        if ttl > 0.0:
            self._cache[prop] = (now, val)
        return val

    def write(self, prop, value):
        """Write a scene accessor. Raises _ConnDead on a dead connection; _PropUnsupported if the
        client rejects the property."""
        return self._rpc("self:update", [prop, value])

    def write_best_effort(self, prop, value):
        """Write a UI-nicety property (motion/transaction/pivot) -- swallow 'unknown property' (so
        an app that doesn't support it just skips it), but let a dead connection propagate."""
        if prop in self._unsupported:
            return
        try:
            self._rpc("self:update", [prop, value])
        except _PropUnsupported:
            self._unsupported.add(prop)

    def version_str(self):
        return NLPROXY_VERSION


# --- under-cursor orbit: OS cursor -> web-content fraction -> canvas NDC -> world ray ------------
# navlib exposes no pointer accessor, so the daemon reads GetCursorPos and aims the existing
# hit-test through that pixel. The hard part is the browser: Firefox's top-level client rect
# INCLUDES tabs/URL/bookmarks, while Chromium's Chrome_RenderWidgetHostHWND does not. Getting
# the wrong content rect makes auto-left (aspect-derived panel width) scale-wrong horizontally
# and shifts the pivot down. See docs/apps/onshape.md §8.14.


def _make_thread_dpi_aware():
    """Per-monitor-v2 DPI awareness for THIS thread so GetCursorPos / client rects are physical px.
    Thread-local (SetThreadDpiAwarenessContext) -- does not touch the Tk UI thread."""
    try:
        import ctypes
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def _find_largest_descendant(hwnd, class_name):
    """Largest (by client area) descendant of `hwnd` whose class equals `class_name`, or None."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        best = [None, 0]
        EnumChildProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _cb(ch, _lp):
            buf = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(ch, buf, 256)
            if buf.value == class_name:
                rc = wintypes.RECT()
                if u32.GetClientRect(ch, ctypes.byref(rc)):
                    area = int(rc.right) * int(rc.bottom)
                    if area > best[1]:
                        best[0], best[1] = ch, area
            return True

        u32.EnumChildWindows(hwnd, EnumChildProc(_cb), 0)
        return best[0] if best[1] > 50000 else None
    except Exception:
        return None


def _firefox_content_top(hwnd, client_w, client_h):
    """Physical-px Y where the web page begins inside a Firefox top-level client rect.

    Firefox's MozillaWindowClass client area includes the tab strip / URL bar / bookmarks bar.
    We BitBlt the top of the client and find the first row that is Onshape's dark page chrome
    across most of the width. Cached per (hwnd, w, h) so a resize remeasures and a steady
    window does not. Returns 0 on failure (caller then treats the whole client as content)."""
    global _FF_CONTENT_CACHE
    key = (int(hwnd), int(client_w), int(client_h))
    if _FF_CONTENT_CACHE.get("key") == key:
        return int(_FF_CONTENT_CACHE["top"])
    top = 0
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        gdi = ctypes.windll.gdi32
        # Only need the top band -- browser chrome is well under ~300 px even with bookmarks.
        band = min(int(client_h), max(80, int(client_h * 0.35)))
        w = int(client_w)
        if w < 64 or band < 16:
            _FF_CONTENT_CACHE = {"key": key, "top": 0}
            return 0

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

        hdc = u32.GetDC(hwnd)
        mem = gdi.CreateCompatibleDC(hdc)
        bmp = gdi.CreateCompatibleBitmap(hdc, w, band)
        old = gdi.SelectObject(mem, bmp)
        gdi.BitBlt(mem, 0, 0, w, band, hdc, 0, 0, 0x00CC0020)  # SRCCOPY
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -band
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        buf = (ctypes.c_uint8 * (w * band * 4))()
        gdi.GetDIBits(mem, bmp, 0, band, buf, ctypes.byref(bmi), 0)
        gdi.SelectObject(mem, old)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(mem)
        u32.ReleaseDC(hwnd, hdc)

        # Onshape's page chrome is near-black (~28,27,34). Firefox chrome is light/blue/grey on
        # the LEFT (tabs/URL/bookmarks). The right edge can look dark early (window controls /
        # overlapping paint), so we key off the LEFT half only -- that's the reliable tell.
        xs = [max(0, min(w - 1, int(w * f))) for f in (0.04, 0.10, 0.18, 0.28, 0.40)]
        for y in range(band):
            dark = 0
            for x in xs:
                i = (y * w + x) * 4
                b, g, r = buf[i], buf[i + 1], buf[i + 2]
                if r < 50 and g < 50 and b < 55:
                    dark += 1
            if dark >= 4:                       # >=4 of 5 left-half samples -> Onshape page
                top = y
                break
    except Exception:
        top = 0
    _FF_CONTENT_CACHE = {"key": key, "top": int(top)}
    return int(top)


def _browser_content_rect(root_hwnd):
    """Screen-space (left, top, width, height) of the browser's *web content* area.

    Tier 1: largest Chrome_RenderWidgetHostHWND under the root (Chromium -- excludes browser chrome).
    Tier 2: Firefox -- top-level client rect with the measured chrome height subtracted.
    Tier 3: raw top-level client rect.
    Returns None on failure."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        # Chromium: the render widget is the page; pick the LARGEST (not the first -- tiny UI
        # widgets also use this class).
        render = _find_largest_descendant(root_hwnd, "Chrome_RenderWidgetHostHWND")
        if render is not None:
            rc = wintypes.RECT()
            if u32.GetClientRect(render, ctypes.byref(rc)) and rc.right > 64 and rc.bottom > 64:
                pt = wintypes.POINT(0, 0)
                if u32.ClientToScreen(render, ctypes.byref(pt)):
                    return (int(pt.x), int(pt.y), int(rc.right), int(rc.bottom))

        rc = wintypes.RECT()
        if not u32.GetClientRect(root_hwnd, ctypes.byref(rc)) or rc.right <= 0 or rc.bottom <= 0:
            return None
        pt = wintypes.POINT(0, 0)
        if not u32.ClientToScreen(root_hwnd, ctypes.byref(pt)):
            return None
        left, top, width, height = int(pt.x), int(pt.y), int(rc.right), int(rc.bottom)

        cbuf = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(root_hwnd, cbuf, 256)
        if cbuf.value == "MozillaWindowClass":
            chrome = _firefox_content_top(root_hwnd, width, height)
            if 0 < chrome < height - 64:
                top += chrome
                height -= chrome
        return (left, top, width, height)
    except Exception:
        return None


def _cursor_client_fraction():
    """OS cursor as a fraction (fx, fy, win_w, win_h) across the focused browser's *web-content*
    area, or None when the cursor isn't over that area / the focused window isn't a browser.
    fx/fy are top-left origin in [0,1]; win_w/win_h are the content size in physical px (fed to
    auto-left). DPI-aware: caller must run on a per-monitor-v2 thread (the Onshape worker)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        pt = wintypes.POINT()
        if not u32.GetCursorPos(ctypes.byref(pt)):
            return None
        hwnd = u32.WindowFromPoint(pt)
        if not hwnd:
            return None
        root = u32.GetAncestor(hwnd, 2)             # GA_ROOT
        if not root or root != u32.GetForegroundWindow():
            return None
        rect = _browser_content_rect(root)
        if rect is None:
            return None
        left, top, width, height = rect
        if width <= 0 or height <= 0:
            return None
        fx = (pt.x - left) / float(width)
        fy = (pt.y - top) / float(height)
        if not (-0.05 <= fx <= 1.05 and -0.05 <= fy <= 1.05):
            return None                             # cursor outside the content (other monitor / etc.)
        return (fx, fy, float(width), float(height))
    except Exception:
        return None


class OnshapeBridge:
    """Accumulates nav deltas (BLE thread), serves the 3Dconnexion endpoint (server thread), and at
    a fixed rate applies the accumulated delta to the focused Onshape view (worker thread). Public
    surface parallels SolidWorksDriver so app.py drives it identically."""

    def __init__(self, on_connection_changed=None, rate_hz=DEFAULT_FLUSH_HZ,
                 host=BRIDGE_HOST, port=BRIDGE_PORT, cert_path=None, key_path=None):
        self.on_connection_changed = on_connection_changed
        self._host = host or BRIDGE_HOST
        self._port = int(port or BRIDGE_PORT)
        d_cert, d_key = default_cert_paths()
        self._cert_path = cert_path or d_cert
        self._key_path = key_path or d_key

        self._lock = threading.Lock()
        self._acc = [0.0] * 6
        self._stop = threading.Event()
        self._period = 1.0 / self._clamp_rate(rate_hz)
        self._server_thread = None
        self._worker_thread = None
        self._srv = None
        self._conn = None                 # current ready connection (or None)
        self._connected = False
        self._version = ""
        self._in_motion = False
        self._held_pivot = None           # orbit pivot captured at gesture start and held (see _navigate)
        self._nav_logged = False          # debug: log one camera read/write per gesture
        self._force_focus = bool(_SPIN)   # spike/test: drive even if the client reports unfocused
        self._log = get_logger()
        self._warned = set()
        # Control scheme (orbit pivot / orbit style / zoom mode); a dict ref-swap is atomic, so the
        # worker reads it lock-free each flush (same pattern as the broker / SW driver).
        self._scheme = {"op": "view", "os": "free", "zm": "to_center"}
        # Under-cursor canvas calibration (live via set_canvas / config.onshape.*).
        self._canvas_inset = tuple(CANVAS_INSET)
        self._canvas_auto_left = bool(CANVAS_AUTO_LEFT)

    @staticmethod
    def _clamp_rate(hz):
        try:
            hz = float(hz)
        except (TypeError, ValueError):
            hz = DEFAULT_FLUSH_HZ
        return min(240.0, max(1.0, hz))

    # --- public surface (mirrors SolidWorksDriver) --------------------------------------------
    def set_rate(self, hz):
        self._period = 1.0 / self._clamp_rate(hz)

    def set_scheme(self, orbit_pivot, orbit_style, zoom_mode):
        self._scheme = {"op": orbit_pivot, "os": orbit_style, "zm": zoom_mode}

    def set_canvas(self, inset=None, auto_left=None):
        """Live-update the under-cursor canvas calibration (no restart). `inset` is an iterable of
        4 fractions (L,T,R,B) or None to leave unchanged; `auto_left` is bool/None."""
        if inset is not None:
            try:
                vals = [max(0.0, min(0.9, float(v))) for v in list(inset)[:4]]
                while len(vals) < 4:
                    vals.append(0.0)
                self._canvas_inset = tuple(vals)
            except (TypeError, ValueError):
                pass
        if auto_left is not None:
            self._canvas_auto_left = bool(auto_left)

    def submit(self, ox, oy, oz, px, py, zoom):
        with self._lock:
            a = self._acc
            a[0] += ox; a[1] += oy; a[2] += oz
            a[3] += px; a[4] += py; a[5] += zoom

    def is_connected(self):
        return self._connected

    def version(self):
        return self._version

    def start(self):
        if self._server_thread is not None:
            return
        # Cert generation is safe (only writes our own files); it makes the bridge self-sufficient.
        # The sensitive step -- trusting the cert -- stays manual/confirmed (see setup_onshape).
        if not ensure_cert(self._cert_path, self._key_path):
            self._log.info("onshape: no TLS cert (run Onshape 'Set up', or install cryptography/"
                           "openssl); bridge disabled")
            return
        self._server_thread = threading.Thread(target=self._run_server, name="onshape-server",
                                               daemon=True)
        self._worker_thread = threading.Thread(target=self._run_worker, name="onshape-worker",
                                               daemon=True)
        self._server_thread.start()
        self._worker_thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        conn = self._conn
        if conn is not None:
            try:
                conn.sock.close()
            except Exception:
                pass

    # --- server thread ------------------------------------------------------------------------
    def _run_server(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.load_cert_chain(self._cert_path, self._key_path)
        except Exception as exc:
            self._log.info("onshape: failed to load TLS cert (%r); bridge disabled", exc)
            return
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self._host, self._port))
            srv.listen(8)
            srv.settimeout(0.5)
            self._srv = srv
        except OSError as exc:
            self._log.info("onshape: cannot bind %s:%d (%r) -- is 3DxWare installed?",
                           self._host, self._port, exc)
            return
        self._log.info("onshape: NL-Proxy bridge listening on https://%s:%d", self._host, self._port)
        while not self._stop.is_set():
            try:
                raw, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_raw, args=(raw, ctx), daemon=True).start()

    def _handle_raw(self, raw, ctx):
        try:
            raw.settimeout(10.0)
            tls = ctx.wrap_socket(raw, server_side=True)
        except Exception:
            # TLS handshake failed -- typically a browser probing an untrusted cert. Expected; the
            # user must trust the cert (setup_onshape). Don't spam: log once.
            self._warn_once("tls", "onshape: a client refused the TLS cert (not trusted yet?)")
            try:
                raw.close()
            except Exception:
                pass
            return
        _OnshapeConn(self, tls).serve()

    def _on_conn_ready(self, conn):
        self._conn = conn
        self._version = conn.version_str()
        self._set_connected(True)

    def _on_conn_closed(self, conn):
        if self._conn is conn:
            self._conn = None
            self._in_motion = False
            self._held_pivot = None
            self._set_connected(False)

    def _set_connected(self, connected):
        if connected == self._connected:
            return
        self._connected = connected
        if not connected:
            self._version = ""
        if self.on_connection_changed:
            try:
                self.on_connection_changed(connected, self._version if connected else "")
            except Exception:
                pass

    # --- worker thread (the navigation model) -------------------------------------------------
    def _run_worker(self):
        # Physical-px GetCursorPos / client rects for the under-cursor pivot (thread-local; Tk
        # stays unaware). Harmless when the cursor pivot isn't in use.
        _make_thread_dpi_aware()
        last_motion = 0.0
        while not self._stop.is_set():
            cycle = time.monotonic()
            period = self._period
            conn = self._conn
            if conn is None or not conn.alive or not conn.subscribed:
                self._drain()
                self._sleep_remainder(cycle, period)
                continue
            if _SPIN:                       # self-test: inject a continuous synthetic orbit
                self.submit(_SPIN, 0.0, 0.0, 0.0, 0.0, 0.0)
            with self._lock:
                a = self._acc
                has = any(a)
                if has:
                    delta = tuple(a)
                    self._acc = [0.0] * 6
            focused = conn.focus or self._force_focus
            if has and focused:
                try:
                    self._navigate(conn, delta)
                    last_motion = cycle
                except _ConnDead:
                    self._drop(conn)
            elif self._in_motion and (not focused or cycle - last_motion > _MOTION_IDLE):
                # End the gesture promptly when Onshape loses focus, or after an idle pause.
                try:
                    self._end_motion(conn)
                except _ConnDead:
                    self._drop(conn)
            self._sleep_remainder(cycle, period)

    def _sleep_remainder(self, cycle_start, period):
        remaining = period - (time.monotonic() - cycle_start)
        self._stop.wait(remaining if remaining > 0.0 else 0.001)

    def _drain(self):
        with self._lock:
            self._acc = [0.0] * 6

    def _drop(self, conn):
        conn.alive = False
        try:
            conn.sock.close()
        except Exception:
            pass

    # --- one navigation step (read camera -> apply -> write) ----------------------------------
    def _navigate(self, conn, delta):
        ox, oy, oz, px, py, zoom = delta
        affine = conn.read("view.affine")
        if not (isinstance(affine, list) and len(affine) >= 16):
            self._warn_once("affine_read",
                            "onshape: view.affine read returned %r (unexpected shape)" % (affine,))
            return
        eye, right, up, back = _decode_affine(affine)
        eye_in = eye
        scheme = self._scheme
        if not self._in_motion:
            self._begin_motion(conn)
        changed_affine = False
        new_extents = None

        if ox or oy or oz:
            # Orbit about the gesture pivot, captured ONCE at the start of the gesture and held (so
            # the hit-test runs once, not per frame, and the pivot doesn't chase the moving view).
            pivot = self._gesture_pivot(conn, scheme, eye, right, up, back)
            eye, right, up, back = self._orbit(ox, oy, oz, eye, right, up, back, pivot, scheme)
            changed_affine = True
        elif px or py:
            eye = self._pan(conn, eye, right, up, px, py)
            changed_affine = True
            self._held_pivot = None         # pan moved the screen centre -> re-hit on the next orbit
        elif zoom:
            if self._perspective(conn):
                zpivot = self._held_pivot or self._object_center(conn) or eye_in
                eye = self._zoom_persp(eye, _v_neg(back), zpivot, zoom)
                changed_affine = True
            else:
                new_extents = self._zoom_ortho(conn, zoom)
                # Re-assert the (unchanged) affine alongside the extents change. Onshape treats the
                # view.affine write as the frame's commit; writing extents alone makes it snap the
                # view back on the next frame (the zoom "rubber-bands"). This matches the real
                # NL-Proxy, which writes view.affine every motion frame.
                changed_affine = new_extents is not None
            self._held_pivot = None         # zoom changed framing -> re-hit on the next orbit

        if not changed_affine and new_extents is None:
            return
        conn.write_best_effort("transaction", self._next_txn(conn))
        if new_extents is not None:
            conn.write_best_effort("view.extents", new_extents)
        if changed_affine:
            try:
                conn.write("view.affine", _encode_affine(eye, right, up, back))
            except _PropUnsupported:
                self._warn_once("affine", "onshape: client rejected view.affine writes")
        if _DEBUG and not self._nav_logged:
            self._nav_logged = True
            self._log.info("onshape nav: delta=%s persp=%s eye_in=%s held_pivot=%s",
                           tuple(round(v, 4) for v in delta), self._perspective(conn),
                           tuple(round(v, 2) for v in eye_in),
                           tuple(round(v, 3) for v in self._held_pivot) if self._held_pivot else None)
        conn.write_best_effort("transaction", 0)

    def _next_txn(self, conn):
        conn._txn += 1
        return conn._txn

    def _begin_motion(self, conn):
        """Mark the start of a motion gesture. The pivot is captured lazily by _gesture_pivot on the
        first orbit frame (so a pure pan/zoom gesture never pays for a hit-test)."""
        self._in_motion = True
        conn.write_best_effort("motion", True)

    def _gesture_pivot(self, conn, scheme, eye, right, up, back):
        """The orbit pivot for this gesture -- computed once (this is where the hit-test happens),
        then HELD until a pan/zoom moves the view or the gesture ends. Also shows Onshape's on-screen
        pivot marker when it (re)computes."""
        if self._held_pivot is None:
            self._held_pivot = self._pivot(conn, scheme, eye, right, up, back)
            if self._held_pivot is not None:
                conn.write_best_effort("pivot.position", list(self._held_pivot))
                conn.write_best_effort("pivot.visible", True)
        return self._held_pivot

    def _end_motion(self, conn):
        if not self._in_motion:
            return
        self._in_motion = False
        self._held_pivot = None
        self._nav_logged = False              # re-arm the per-gesture debug log
        conn.write_best_effort("pivot.visible", False)
        conn.write_best_effort("motion", False)

    # --- camera math (reuses the Fusion add-in's orbit/pan/zoom-about-a-pivot) -----------------
    def _orbit(self, ox, oy, oz, eye, right, up, back, pivot, scheme):
        """Rotate eye (about the pivot) + the camera basis. free = rotate about the composed
        camera-axis; turntable = yaw about world-up + pitch about camera-right (roll dropped)."""
        vx = ORBIT_SIGN[0] * ox
        vy = ORBIT_SIGN[1] * oy
        vz = ORBIT_SIGN[2] * oz
        forward = _v_neg(back)
        if scheme.get("os") == "turntable":
            q = _q_mul(_q_from_axis_angle(right[0], right[1], right[2], vx),
                       _q_from_axis_angle(WORLD_UP[0], WORLD_UP[1], WORLD_UP[2], vy))
            ax, ay, az, angle = _q_to_axis_angle(q)
            axis = (ax, ay, az)
        else:                                       # free
            axis = _v_add(_v_add(_v_scale(right, vx), _v_scale(up, vy)), _v_scale(forward, vz))
            angle = _v_len(axis)
            if angle < 1e-9:
                return eye, right, up, back
            axis = _v_scale(axis, 1.0 / angle)
        if angle < 1e-12:
            return eye, right, up, back
        if pivot is not None:
            eye = _v_add(pivot, _rodrigues(axis, angle, _v_sub(eye, pivot)))
        right = _rodrigues(axis, angle, right)
        up = _rodrigues(axis, angle, up)
        back = _rodrigues(axis, angle, back)
        right, up, back = _orthonormalize(right, up, back)
        return eye, right, up, back

    def _pan(self, conn, eye, right, up, px, py):
        """Shift the eye in the camera right/up plane, scaled by the view half-extent so pan feels
        constant at any zoom (exactly like the Fusion add-in scales by viewExtents)."""
        vh = self._view_half(conn)
        gx = PAN_SIGN[0] * px * PAN_SCALE * vh
        gy = PAN_SIGN[1] * py * PAN_SCALE * vh
        return _v_add(eye, _v_add(_v_scale(right, gx), _v_scale(up, gy)))

    def _zoom_persp(self, eye, forward, pivot, zoom):
        """Perspective zoom = dolly the eye along forward, proportional to the pivot distance."""
        dist = _v_len(_v_sub(pivot, eye)) if pivot is not None else 1.0
        if dist < 1e-6:
            dist = 1.0
        return _v_add(eye, _v_scale(forward, ZOOM_SIGN * zoom * ZOOM_SCALE * dist))

    def _zoom_ortho(self, conn, zoom):
        """Orthographic zoom = scale view.extents about the view centre (moving the eye does nothing
        under an orthographic projection). Returns the new 6-float extents, or None. Reads the
        extents FRESH (not cached) so successive zoom frames compound off the latest value."""
        ext = conn.read("view.extents")
        if not (isinstance(ext, list) and len(ext) >= 6):
            return None
        s = 1.0 - ZOOM_SIGN * zoom * ZOOM_SCALE
        if s < 0.02:
            s = 0.02
        return [ext[0] * s, ext[1] * s, ext[2], ext[3] * s, ext[4] * s, ext[5]]

    # --- scheme geometry ----------------------------------------------------------------------
    def _pivot(self, conn, scheme, eye, right, up, back):
        op = scheme.get("op", "view")
        if op == "origin":
            return (0.0, 0.0, 0.0)
        if op == "cursor":
            # Under-mouse: aim the hit-test through the OS cursor. Off-canvas / miss -> screen
            # centre (same as "view"), then model centre. ⚠ mouse->canvas mapping needs live-GUI
            # verify (docs §8.14); the ray/hold/fallback path is offline-tested.
            hit = self._hit_cursor(conn, eye, right, up, back)
            if hit is not None:
                return hit
            hit = self._hit_center(conn, eye, right, up, back)
            if hit is not None:
                return hit
        elif op in ("view", "selection"):
            # Orbit about what's under the screen centre (like Onshape's own right-click orbit).
            hit = self._hit_center(conn, eye, right, up, back)
            if hit is not None:
                return hit
        center = self._object_center(conn)
        if center is not None:
            return center
        # last resort: a point in front of the camera, so orbit still has a sane pivot.
        return _v_add(eye, _v_scale(_v_neg(back), self._view_half(conn) * 4.0))

    def _hit_center(self, conn, eye, right, up, back):
        """navlib hit-test through the screen centre (NDC 0,0)."""
        half_x, half_y = self._view_halves(conn)
        vh = max(half_x, half_y)
        lookfrom, direction = self._pixel_ray(0.0, 0.0, eye, right, up, back, half_x, half_y, vh * 8.0)
        return self._hit_ray(conn, lookfrom, direction, vh)

    def _hit_cursor(self, conn, eye, right, up, back):
        """navlib hit-test through the OS cursor's canvas point, or None when the cursor can't be
        mapped into the canvas (off-panel / no window / mapping failed)."""
        frac = _cursor_client_fraction()
        if frac is None:
            return None
        fx, fy, win_w, win_h = frac
        half_x, half_y = self._view_halves(conn)
        inset = self._effective_canvas_inset(half_x, half_y, win_w, win_h,
                                             self._canvas_inset, self._canvas_auto_left)
        ndc = self._client_fraction_to_ndc(fx, fy, inset)
        if ndc is None:
            return None
        if _DEBUG:
            self._log.info("onshape cursor: frac=(%.3f,%.3f) win=%.0fx%.0f inset=(%.3f,%.3f,%.3f,%.3f) "
                           "ndc=(%.3f,%.3f)", fx, fy, win_w, win_h, inset[0], inset[1], inset[2],
                           inset[3], ndc[0], ndc[1])
        vh = max(half_x, half_y)
        lookfrom, direction = self._pixel_ray(ndc[0], ndc[1], eye, right, up, back,
                                              half_x, half_y, vh * 8.0)
        return self._hit_ray(conn, lookfrom, direction, vh)

    def _hit_ray(self, conn, lookfrom, direction, vh):
        """Write an arbitrary pick ray, widen the aperture until a bbox-valid hit.lookat lands.
        Shared by centre and cursor. Returns the hit point or None."""
        if conn._hit_unsupported:
            return None
        bbox = conn.read("model.extents", ttl=_OBJ_TTL)
        try:
            conn.write("hit.selectionOnly", False)
            conn.write("hit.lookfrom", list(lookfrom))
            conn.write("hit.direction", list(direction))
        except _PropUnsupported:
            conn._hit_unsupported = True
            return None
        for f in HIT_APERTURES:
            try:
                conn.write("hit.aperture", max(1e-5, vh * f))
                pt = conn._rpc("self:read", ["hit.lookat"])
            except _PropUnsupported:
                continue
            if self._valid_hit(pt, bbox):
                return (float(pt[0]), float(pt[1]), float(pt[2]))
        return None

    @staticmethod
    def _pixel_ray(ndc_x, ndc_y, eye, right, up, back, half_x, half_y, backoff):
        """Orthographic pick ray through a canvas NDC point (x right, y up, both in [-1,1]).
        NDC (0,0) is the screen-centre ray the old _hit_center used. lookfrom is pulled `backoff`
        along forward so the ray starts outside the model."""
        fwd = _v_normalize(_v_neg(back))
        offset = _v_add(_v_scale(right, ndc_x * half_x), _v_scale(up, ndc_y * half_y))
        lookfrom = _v_sub(_v_add(eye, offset), _v_scale(fwd, backoff))
        return lookfrom, fwd

    @staticmethod
    def _client_fraction_to_ndc(fx, fy, inset):
        """Map a top-left client-area fraction through canvas insets to NDC (x right, y up).
        Returns None when the point falls outside the canvas (with a small edge tolerance)."""
        left, top, right, bottom = inset
        cw = 1.0 - left - right
        ch = 1.0 - top - bottom
        if cw < 1e-6 or ch < 1e-6:
            return None
        ux = (fx - left) / cw
        uy = (fy - top) / ch
        if not (-0.02 <= ux <= 1.02 and -0.02 <= uy <= 1.02):
            return None
        ux = max(0.0, min(1.0, ux))
        uy = max(0.0, min(1.0, uy))
        return (ux * 2.0 - 1.0, 1.0 - uy * 2.0)          # y-flip: top-left -> NDC y=+1

    @staticmethod
    def _effective_canvas_inset(half_x, half_y, win_w, win_h, inset, auto_left):
        """Resolve (L,T,R,B). With auto_left, derive L from the view aspect so the resizable
        feature-tree panel is tracked live: Onshape keeps view.extents' aspect equal to the
        visible canvas pixel aspect. Assumes the canvas is flush to the content's right & bottom
        (set Right/Bottom manually when a right/bottom panel is open). Top MUST be correct -- a
        wrong top gains the horizontal (canvas_w_frac includes (1-top-bottom))."""
        left, top, right, bottom = (float(inset[0]), float(inset[1]),
                                    float(inset[2]), float(inset[3]))
        if not auto_left:
            return (left, top, right, bottom)
        if half_y < 1e-9 or win_w < 1.0 or win_h < 1.0:
            return (left, top, right, bottom)
        usable_h = max(1e-6, 1.0 - top - bottom)
        # canvas_w / canvas_h = half_x / half_y, canvas_h = usable_h * win_h
        canvas_w_frac = (half_x / half_y) * (win_h / win_w) * usable_h
        left = max(0.0, min(0.9, 1.0 - right - canvas_w_frac))
        return (left, top, right, bottom)

    @staticmethod
    def _valid_hit(pt, bbox):
        """A hit counts only if it's a finite 3-point inside the model bounding box (expanded ~10% of
        its diagonal) -- so a no-hit sentinel or a stale value can't masquerade as a real hit."""
        if not (isinstance(pt, list) and len(pt) >= 3):
            return False
        try:
            x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return False
        if isinstance(bbox, list) and len(bbox) >= 6:
            dx, dy, dz = bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]
            m = 0.1 * math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6
            if not (bbox[0] - m <= x <= bbox[3] + m and bbox[1] - m <= y <= bbox[4] + m
                    and bbox[2] - m <= z <= bbox[5] + m):
                return False
        return True

    def _object_center(self, conn):
        ext = conn.read("model.extents", ttl=_OBJ_TTL)
        if isinstance(ext, list) and len(ext) >= 6:
            return ((ext[0] + ext[3]) * 0.5, (ext[1] + ext[4]) * 0.5, (ext[2] + ext[5]) * 0.5)
        return None

    def _view_halves(self, conn):
        """(half_x, half_y) of view.extents -- the orthographic half-width/height in world units.
        Falls back to a unit square when extents are unavailable."""
        ext = conn.read("view.extents", ttl=_EXT_TTL)
        if isinstance(ext, list) and len(ext) >= 6:
            hx = max(1e-4, 0.5 * abs(ext[3] - ext[0]))
            hy = max(1e-4, 0.5 * abs(ext[4] - ext[1]))
            return (hx, hy)
        return (1.0, 1.0)

    def _view_half(self, conn):
        hx, hy = self._view_halves(conn)
        return max(hx, hy)

    def _perspective(self, conn):
        val = conn.read("view.perspective", ttl=_PERSP_TTL)
        return bool(val) if val is not None else False     # Onshape defaults to orthographic

    def _warn_once(self, what, msg):
        if what not in self._warned:
            self._warned.add(what)
            self._log.info(msg)


# --- standalone spike / test harness ----------------------------------------------------------
def _main():
    """Run the bridge by itself for testing: `python -m trackball_daemon.onshape_bridge [--spin]`.
    Generates the cert, starts the server, and (with --spin) feeds a slow synthetic orbit so a
    connected Onshape model rotates -- proving TLS trust + handshake + camera writes end to end,
    with no BLE/hardware involved."""
    spin = "--spin" in sys.argv
    force = "--force" in sys.argv      # drive even if Onshape reports its view unfocused
    cert, key = default_cert_paths()
    if not ensure_cert(cert, key):
        print("Could not generate a TLS cert (install `cryptography` or have `openssl` on PATH).")
        return
    print("Cert: %s" % cert)
    print("Trust it (Chrome/Edge, no admin):  certutil -user -addstore Root \"%s\"" % cert)
    print("   ...or browse to https://%s:%d and accept the warning once." % (BRIDGE_HOST, BRIDGE_PORT))
    print("Then open an Onshape document (SpaceMouse enabled in Onshape settings) and focus it.\n",
          flush=True)

    bridge = OnshapeBridge(
        on_connection_changed=lambda c, v: print(">>> connected=%s version=%s" % (c, v), flush=True))
    bridge._force_focus = force
    bridge.start()
    try:
        while True:
            time.sleep(0.05)
            if spin and bridge.is_connected():
                bridge.submit(0.01, 0.0, 0.0, 0.0, 0.0, 0.0)   # gentle orbit about camera-right
    except KeyboardInterrupt:
        bridge.stop()


if __name__ == "__main__":
    _main()
