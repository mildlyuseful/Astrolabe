"""Daemon-side Onshape bridge -- the browser analogue of the SolidWorks COM driver.

Onshape runs in a browser and has NATIVE 3Dconnexion SpaceMouse support: its page ships the
3Dconnexion client library, which (on Windows/Mac) connects to a LOCAL service -- the "NL-Proxy"
/ "3DxWare for web" bridge -- at the loopback endpoint ``127.51.68.120:8181`` over a TLS
WebSocket. We don't own a SpaceMouse, so instead of synthesizing mouse drags we stand up OUR OWN
server impersonating that service: Onshape connects to us, hands us its camera, and we feed in the
trackball's orbit/pan/zoom. See ``docs/apps/onshape.md`` for the full reverse-engineered
protocol + the cert/trust setup (and the prior art it is based on: RmStorm/spacenav-ws).

This direct transport lives inside the daemon process, parallel to the broker and SolidWorks driver.
``NavigationRouter`` is the sole delivery boundary and sends it only active, revision-matched Onshape
motion.

Threading model (mirrors SolidWorksDriver exactly):
  * submit() is called on the navigation-delivery path and ONLY accumulates the per-frame
    orbit/pan/zoom delta -- it never blocks and never touches a socket.
  * a SERVER thread runs the TLS accept loop on 127.51.68.120:8181. Each accepted connection gets a
    READER thread (parse WAMP frames, answer the handshake, resolve read/write replies by call id).
  * a WORKER thread waits until a browser client is subscribed+focused, then at rate_hz coalesces
    the accumulated delta and runs ONE navigation step: read the camera (view.affine), apply the
    orbit/pan/zoom math, write the new camera back -- wrapped in the protocol's motion/transaction
    framing. Every network round-trip happens here, never on the producer/delivery path.
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
import binascii
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
from urllib.parse import urlsplit

from .config import ONSHAPE_BRIDGE_HOST, ONSHAPE_BRIDGE_PORT, orbit_pivot_candidates
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
BRIDGE_HOST = ONSHAPE_BRIDGE_HOST
BRIDGE_PORT = ONSHAPE_BRIDGE_PORT
_BRIDGE_ORIGIN = f"https://{BRIDGE_HOST}:{BRIDGE_PORT}"
# Reported by GET /3dconnexion/nlproxy so Onshape thinks a recent 3DxWare service is present.
NLPROXY_VERSION = "1.4.8.21486"
# WAMP WELCOME server-ident. Free-form (the real proxy sends a 3Dconnexion copyright string; the
# spacenav-ws bridge sends its own and Onshape accepts it), so we send a clear, honest one.
WELCOME_IDENT = "NLProxy v%s (Trackball Daemon bridge)" % NLPROXY_VERSION
_MAX_HTTP_BODY = 16 * 1024
# Current Onshape client messages can be about 380 KiB. Keep one bounded budget with
# enough headroom for client-version growth instead of imposing a second fragmentation-dependent cap.
_MAX_WS_MESSAGE = 1024 * 1024
_MAX_WS_FRAGMENTS = 64
_VALID_WS_CLOSE_CODES = frozenset({
    1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014,
})


def _allowed_web_origin(origin):
    """Only Onshape pages (or this bridge's own status page) may call the local service."""
    if not origin:
        return True                              # direct address-bar/status-page request
    try:
        parsed = urlsplit(origin)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            return False
        return (host == "onshape.com" or host.endswith(".onshape.com") or
                (host == BRIDGE_HOST and parsed.port == BRIDGE_PORT))
    except Exception:
        return False

# --- neutral camera math + scene orientation ---------------------------------------------------
# The nav-delta contract feeds (ox,oy,oz) = orbit about (camera right, up, forward) in radians,
# (px,py) = pan, zoom = zoom. OutputEngine already composes the immutable Onshape baseline with
# user bindings, so this implementation stays neutral.
ORBIT_SIGN = (1.0, 1.0, 1.0)
# Onshape's Top plane normal is world +Z. Turntable yaw and one-time horizon leveling must both use
# it; +Y is the Front-plane normal and produced a horizon parallel to Front instead of Top.
WORLD_UP = (0.0, 0.0, 1.0)
PAN_SIGN = (1.0, 1.0)
PAN_SCALE = 1.0
ZOOM_SCALE = 1.0
ZOOM_SIGN = 1.0                   # twist -> zoom direction

# view.affine layout. False = the ROW-vector layout real ONSHAPE emits (verified live: the last
# column at indices 3,7,11 is [0,0,0], so the homogeneous column is [0,0,0,1] and the translation/
# eye is in the last ROW at indices 12,13,14 -- the convention spacenav-ws uses). True would be the
# column-translation layout (eye at indices 3,7,11) the 3Dconnexion three.js sample emits. If orbit/
# pan ever come out transposed or coupled against a different app, flip this.
AFFINE_TRANSLATION_IN_COLUMN = False

# `screen_center`/`selection`/`cursor` use Onshape's navlib hit-test to raycast a screen point
# and pivot about the first surface hit (like Onshape's own right-click orbit). These are the ray
# aperture (cone diameter) as fractions of the view half-extent, tried smallest-first -- a narrow
# ray, widening until something is hit. If none hit at the widest, the method is unavailable and
# the configured chain continues. Onshape does the actual raycast; we set the ray and read the result.
HIT_APERTURES = (0.03, 0.1, 0.3)
# Onshape never signals a miss: on a no-hit it FABRICATES hit.lookat as a point on the pick ray at
# roughly the scene's distance from the camera. Two guards keep that from becoming an orbit pivot
# (the method must instead be unavailable so the configured chain continues): _valid_hit only
# accepts points essentially inside the model bbox, and _hit_ray re-casts the same ray from further
# back and requires the same world point (a real surface is invariant to the ray origin; a
# fabricated at-depth point tracks it). _CONFIRM_BACKOFF is the extra origin slide and _CONFIRM_TOL
# the agreement tolerance, both as fractions of the view half-extent.
_CONFIRM_BACKOFF = 4.0
_CONFIRM_TOL = 1e-3

# Under-cursor orbit: the page reports the pointer as a fraction of #canvas (exact DOM
# getBoundingClientRect). A tiny userscript POSTs that to /trackball/pointer on this bridge.
# view.extents' aspect does NOT match the canvas pixel aspect (verified live: ~0.98 vs ~1.72),
# so we never derive canvas size from extents. See docs/apps/onshape.md §8.14.
_POINTER_TTL = 0.75          # seconds; stale page reports make Under Cursor unavailable

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
_MOTION_IDLE = 0.5           # fallback; configured per app by set_pivot_hold
_PERSP_TTL = 1.0             # cache view.perspective this long (it changes rarely)
_OBJ_TTL = 0.5              # cache model.extents (orbit/zoom pivot) this long
_EXT_TTL = 0.2              # cache view.extents (pan/zoom scale) this long
_TGT_TTL = 0.3              # cache view.target this long
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


class _WSProtocolError(_ConnDead):
    """Raised after rejecting an invalid or oversized client WebSocket frame."""


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


def _level_horizon_basis(back, world_up=WORLD_UP):
    """Return leveled (right, up) for an unchanged camera-back vector, or None at the
    straight-up/down singularity. Pure helper shared by runtime and headless tests."""
    right = _v_cross(world_up, back)
    if _v_len(right) < 1e-6:
        return None
    right = _v_normalize(right)
    return right, _v_normalize(_v_cross(back, right))


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
    """Bounded RFC6455 reader for masked client text frames and control frames."""

    _DATA_OPCODES = {0x0, 0x1}
    _CONTROL_OPCODES = {0x8, 0x9, 0xA}

    def __init__(self, sock, send_fn):
        self._sock = sock
        self._send = send_fn          # send_fn(bytes) -- used for pong and close replies
        self._buf = b""

    def _need(self, n):
        while len(self._buf) < n:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue              # idle -- keep waiting; stop() closes the socket to unblock
            except OSError:
                raise _ConnDead("socket read failed")
            if not chunk:
                raise _ConnDead("client closed the socket")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _reject(self, reason, code=1002):
        payload = struct.pack(">H", code) + reason.encode("utf-8")[:123]
        try:
            self._send(_ws_encode(payload, opcode=0x8))
        except _ConnDead:
            pass
        raise _WSProtocolError(reason)

    def _read_frame(self):
        b0, b1 = self._need(2)
        fin = bool(b0 & 0x80)
        if b0 & 0x70:
            self._reject("reserved WebSocket bits are unsupported")
        opcode = b0 & 0x0F
        if opcode not in self._DATA_OPCODES | self._CONTROL_OPCODES:
            self._reject("unsupported WebSocket opcode")
        masked = bool(b1 & 0x80)
        if not masked:
            self._reject("client WebSocket frames must be masked")

        length_code = b1 & 0x7F
        ln = length_code
        if length_code == 126:
            ln = struct.unpack(">H", self._need(2))[0]
            if ln < 126:
                self._reject("non-minimal WebSocket payload length")
        elif length_code == 127:
            raw_len = self._need(8)
            if raw_len[0] & 0x80:
                self._reject("invalid WebSocket payload length")
            ln = struct.unpack(">Q", raw_len)[0]
            if ln < 65536:
                self._reject("non-minimal WebSocket payload length")
        if opcode in self._CONTROL_OPCODES and (not fin or ln > 125):
            self._reject("invalid WebSocket control frame")
        # A frame is all or part of one message, so the complete-message budget is also the
        # only meaningful per-frame budget.  A smaller frame ceiling rejects valid unfragmented
        # messages (including current Onshape controller updates) based solely on fragmentation.
        if ln > _MAX_WS_MESSAGE:
            self._reject(
                "WebSocket frame too large (%d bytes; message limit %d)" %
                (ln, _MAX_WS_MESSAGE),
                code=1009)

        mask = self._need(4)
        payload = self._need(ln) if ln else b""
        if payload:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return fin, opcode, payload

    def read_text(self):
        """Return one valid UTF-8 text message while handling control frames internally."""
        chunks = []
        total = 0
        fragments = 0
        fragmented = False
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 0x8:                       # close
                if len(payload) == 1:
                    self._reject("invalid WebSocket close payload")
                if payload:
                    code = struct.unpack(">H", payload[:2])[0]
                    if code not in _VALID_WS_CLOSE_CODES and not 3000 <= code < 5000:
                        self._reject("invalid WebSocket close code")
                    try:
                        payload[2:].decode("utf-8")
                    except UnicodeDecodeError:
                        self._reject("invalid UTF-8 WebSocket close reason", code=1007)
                try:
                    self._send(_ws_encode(payload, opcode=0x8))
                except _ConnDead:
                    pass
                detail = "client WebSocket close"
                if len(payload) >= 2:
                    code = struct.unpack(">H", payload[:2])[0]
                    reason = payload[2:].decode("utf-8") if len(payload) > 2 else ""
                    detail += " %d%s" % (code, " (%s)" % reason if reason else "")
                raise _ConnDead(detail)
            if opcode == 0x9:                       # ping -> pong
                self._send(_ws_encode(payload, opcode=0xA))
                continue
            if opcode == 0xA:                       # pong
                continue
            if opcode == 0x1:
                if fragmented:
                    self._reject("new text frame during fragmented message")
                fragmented = not fin
            elif not fragmented:
                self._reject("continuation without fragmented message")

            fragments += 1
            if fragments > _MAX_WS_FRAGMENTS:
                self._reject("too many WebSocket fragments", code=1009)
            total += len(payload)
            if total > _MAX_WS_MESSAGE:
                self._reject("WebSocket message too large", code=1009)
            chunks.append(payload)
            if fin:
                message = b"".join(chunks)
                try:
                    message.decode("utf-8")
                except UnicodeDecodeError:
                    self._reject("invalid UTF-8 WebSocket text", code=1007)
                return message


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
        self.focus_reported = False       # explicit 3dx_rpc:update seen, independent of message order
        self._opened_at = time.monotonic()

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
                raise _ConnDead("socket write failed")

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
        header_blob, _, leftover = head.partition(b"\r\n\r\n")
        lines = header_blob.split(b"\r\n")
        try:
            method, path, http_version = lines[0].decode("latin-1").split(" ", 2)
            version_text = http_version.removeprefix("HTTP/")
            version_parts = tuple(int(part) for part in version_text.split(".", 1))
            valid_http_version = len(version_parts) == 2 and version_parts >= (1, 1)
        except (ValueError, TypeError):
            return False
        headers = {}
        for ln in lines[1:]:
            if b":" in ln:
                k, v = ln.split(b":", 1)
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        origin = headers.get("origin", "")
        path_only = path.split("?", 1)[0]

        # A trusted loopback certificate would otherwise let an arbitrary web page talk to this
        # local WAMP service. Reject non-Onshape browser origins before CORS or WebSocket upgrade.
        if not _allowed_web_origin(origin):
            self._http(403, "origin not allowed", "", ctype="text/plain")
            return False

        # Read any request body (pointer POSTs are tiny JSON).
        try:
            content_len = int(headers.get("content-length", "0") or 0)
        except ValueError:
            content_len = 0
        if content_len < 0 or content_len > _MAX_HTTP_BODY:
            self._http(400, "request body too large", origin, ctype="text/plain")
            return False
        body_in = leftover
        while len(body_in) < content_len:
            try:
                chunk = self.sock.recv(min(4096, content_len - len(body_in)))
            except OSError:
                break
            if not chunk:
                break
            body_in += chunk
        body_in = body_in[:content_len]

        if method == "OPTIONS":
            self._http(204, "", origin, ctype=None)
            return False
        if path_only.startswith("/3dconnexion/nlproxy"):
            self._http(200, json.dumps({"port": BRIDGE_PORT, "version": NLPROXY_VERSION}),
                       origin, ctype="application/json")
            return False
        # Exact canvas pointer from the Onshape page (userscript). No screen capture.
        if path_only.startswith("/trackball/pointer.js") and method == "GET":
            self._http(200, _POINTER_USERSCRIPT, origin, ctype="application/javascript")
            return False
        if path_only.startswith("/trackball/pointer") and method in ("POST", "PUT"):
            parsed = _parse_pointer_body(body_in)
            if parsed is not None:
                _set_page_pointer(parsed[0], parsed[1], parsed[2])
                self._http(204, "", origin, ctype=None)
            else:
                self._http(400, "bad pointer json", origin, ctype="text/plain")
            return False
        if path_only.startswith("/trackball/pointer") and method == "GET":
            # Status for the cert-trust / install page.
            with _PAGE_POINTER_LOCK:
                age = (time.monotonic() - _PAGE_POINTER["t"]) if _PAGE_POINTER["t"] else None
                snap = {"age_s": age, "on_canvas": _PAGE_POINTER["on"],
                        "ndc_x": _PAGE_POINTER["ndc_x"], "ndc_y": _PAGE_POINTER["ndc_y"]}
            self._http(200, json.dumps(snap), origin, ctype="application/json")
            return False
        upgrades = {item.strip().lower() for item in
                    headers.get("upgrade", "").split(",") if item.strip()}
        if "websocket" in upgrades:
            connection_tokens = {item.strip().lower() for item in
                                 headers.get("connection", "").split(",") if item.strip()}
            protocols = {item.strip() for item in
                         headers.get("sec-websocket-protocol", "").split(",") if item.strip()}
            if (method != "GET" or path_only != "/" or not valid_http_version or
                    "upgrade" not in connection_tokens or
                    headers.get("sec-websocket-version") != "13" or "wamp" not in protocols):
                self._http(400, "invalid websocket upgrade", origin, ctype="text/plain")
                return False
            key = headers.get("sec-websocket-key", "")
            try:
                decoded_key = base64.b64decode(key.encode("ascii"), validate=True)
            except (UnicodeEncodeError, ValueError, binascii.Error):
                decoded_key = b""
            if len(decoded_key) != 16:
                self._http(400, "invalid websocket key", origin, ctype="text/plain")
                return False
            resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    "Sec-WebSocket-Accept: %s\r\n"
                    "Sec-WebSocket-Protocol: wamp\r\n\r\n" % _ws_accept(key))
            self._raw_send(resp.encode("latin-1"))
            self.sock.settimeout(1.0)               # short timeout so the reader can poll stop()
            return True
        # Plain GET / -> status + userscript install hint (also the one-time cert-trust visit).
        body = ("<html><body><h1>Trackball Daemon &mdash; Onshape bridge</h1>"
                "<p>This local NL-Proxy emulator is running. Onshape connects to it automatically; "
                "if you can read this with no certificate warning, the cert is trusted.</p>"
                "<h2>Under-cursor orbit</h2>"
                "<p>Install the userscript from "
                "<a href='/trackball/pointer.js'>/trackball/pointer.js</a> "
                "(Violentmonkey / Tampermonkey on <code>cad.onshape.com</code>). It posts the exact "
                "#canvas pointer to this bridge &mdash; no screen capture, no calibration.</p>"
                "</body></html>")
        self._http(200, body, origin, ctype="text/html")
        return False

    def _http(self, status, body, origin, ctype="text/plain"):
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found"}.get(status, "OK")
        data = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        # Access-Control-Allow-Private-Network: Chromium's Private Network Access preflight for a
        # public page (cad.onshape.com) talking to loopback. Harmless to Firefox.
        out = ["HTTP/1.1 %d %s" % (status, reason), "Connection: close"]
        if origin and _allowed_web_origin(origin):
            out.extend(["Access-Control-Allow-Origin: %s" % origin,
                        "Vary: Origin",
                        "Access-Control-Allow-Methods: GET, POST, PUT, OPTIONS",
                        "Access-Control-Allow-Headers: Content-Type",
                        "Access-Control-Allow-Private-Network: true"])
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
        close_reason = "bridge stopped"
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
        except _ConnDead as exc:
            close_reason = str(exc) or "connection closed"
        except Exception as exc:
            close_reason = "%s: %s" % (type(exc).__name__, str(exc) or "no detail")
            self._log.info(
                "onshape: unexpected connection error (%s)", close_reason,
                exc_info=_DEBUG)
        finally:
            self.alive = False
            for box in list(self._pending.values()):     # wake any worker waiting on a reply
                box["ev"].set()
            try:
                self.sock.close()
            except Exception:
                pass
            if self.subscribed:
                lifetime = time.monotonic() - self._opened_at
                message = "onshape: subscribed transport closed after %.3f s (%s)" % (
                    lifetime, close_reason)
                if _DEBUG:
                    self._log.info(message)
                else:
                    self.bridge._warn_once(
                        "transport-close", message + "; later repeats are suppressed")
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
            # Focus is a distinct controller update. Preserve an update that arrived before the
            # subscription, and otherwise wait for the page's viewport focus notification.
            self.bridge._on_conn_ready(self)
            self._log.info(
                "onshape: client subscribed (controller %s, focus=%s%s)",
                self.instance_id, self.focus,
                " explicit" if self.focus_reported else " awaiting update")
        elif t == _WAMP.UNSUBSCRIBE:
            self.subscribed = False
            self._set_focus(False)

    def _set_focus(self, focused):
        focused = bool(focused)
        if focused == self.focus:
            return
        self.focus = focused
        callback = getattr(self.bridge, "_on_conn_focus_changed", None)
        if callback is not None:
            callback(self, focused)

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
                self.focus_reported = True
                self._set_focus(payload["focus"])
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
            raise _ConnDead("navigation RPC timed out")  # client went silent -> treat as dropped
        self._pending.pop(cid, None)
        if not self.alive:
            raise _ConnDead("connection closed during navigation RPC")
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
        """Write an optional framing/UI property while preserving protocol acknowledgements."""
        if prop in self._unsupported:
            return
        try:
            self._rpc("self:update", [prop, value])
        except _PropUnsupported:
            self._unsupported.add(prop)

    def version_str(self):
        return NLPROXY_VERSION


# --- under-cursor orbit: page-reported canvas NDC (exact DOM size, no screen capture) ----------
# navlib exposes no pointer accessor. Win32 GetCursorPos + window geometry cannot recover the
# WebGL canvas rect on Firefox (client includes chrome; no content HWND). Screen-DC measurement
# is forbidden. Instead a userscript on cad.onshape.com posts the pointer as a fraction of
# document.getElementById("canvas").getBoundingClientRect() to POST /trackball/pointer.


_PAGE_POINTER = {"t": 0.0, "ndc_x": 0.0, "ndc_y": 0.0, "on": False}
_PAGE_POINTER_LOCK = threading.Lock()

# Bookmarklet / Violentmonkey userscript body (also served as text/javascript from the bridge).
_POINTER_USERSCRIPT = r"""// ==UserScript==
// @name         Astrolabe Onshape cursor pivot
// @namespace    https://github.com/mildlyuseful/Astrolabe
// @version      0.1
// @description  Report the mouse position on Onshape's #canvas to the local trackball NL-Proxy.
// @match        https://cad.onshape.com/*
// @match        https://*.onshape.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==
(function () {
  "use strict";
  var ENDPOINT = "__ASTROLABE_ONSHAPE_ORIGIN__/trackball/pointer";
  var last = { t: 0, x: 0, y: 0, on: false };
  function canvasEl() {
    return document.getElementById("canvas") || document.querySelector("canvas");
  }
  function report(ev) {
    var c = canvasEl();
    if (!c) return;
    var r = c.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    var on = ev.clientX >= r.left && ev.clientX <= r.right &&
             ev.clientY >= r.top && ev.clientY <= r.bottom;
    var fx = (ev.clientX - r.left) / r.width;
    var fy = (ev.clientY - r.top) / r.height;
    // NDC: x right, y up, both in [-1,1] (matches the bridge's _pixel_ray).
    var ndcX = fx * 2 - 1;
    var ndcY = 1 - fy * 2;
    last = { t: Date.now(), x: ndcX, y: ndcY, on: on, cw: r.width, ch: r.height };
    // fire-and-forget; Private Network Access preflight is answered by the bridge OPTIONS handler
    try {
      fetch(ENDPOINT, {
        method: "POST",
        mode: "cors",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ndc_x: ndcX, ndc_y: ndcY, on_canvas: on,
                               canvas_w: r.width, canvas_h: r.height })
      }).catch(function () {});
    } catch (e) {}
  }
  window.addEventListener("mousemove", report, { passive: true, capture: true });
  // Keep the last sample fresh while the cursor is still (gesture start without a move).
  setInterval(function () {
    if (!last.t) return;
    try {
      fetch(ENDPOINT, {
        method: "POST",
        mode: "cors",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ndc_x: last.x, ndc_y: last.y, on_canvas: last.on,
                               canvas_w: last.cw, canvas_h: last.ch })
      }).catch(function () {});
    } catch (e) {}
  }, 200);
})();
""".replace("__ASTROLABE_ONSHAPE_ORIGIN__", _BRIDGE_ORIGIN)

POINTER_SCRIPT_URL = f"{_BRIDGE_ORIGIN}/trackball/pointer.js"
POINTER_STATUS_URL = f"{_BRIDGE_ORIGIN}/trackball/pointer"


def pointer_userscript_source():
    """Full Violentmonkey/Tampermonkey userscript text (copy-paste install)."""
    return _POINTER_USERSCRIPT


def pointer_install_instructions():
    """User-facing steps to install the under-cursor userscript (shared by setup UI + docs)."""
    return (
        "Under-cursor orbit needs a tiny page script (exact #canvas size from the DOM):\n\n"
        "1) Install the Tampermonkey or Violentmonkey extension in the browser you use for Onshape.\n"
        "2) Open the extension → Create a new script (or \"+\" / Add new script).\n"
        "3) Delete the template, paste the Astrolabe userscript (use Copy userscript), then Save.\n"
        "4) Reload your Onshape tab and move the mouse over the 3D view.\n"
        "5) Optional check: open %s — ndc_x/ndc_y should update as you move.\n\n"
        "Script URL (daemon must be running): %s"
        % (POINTER_STATUS_URL, POINTER_SCRIPT_URL)
    )


def _set_page_pointer(ndc_x, ndc_y, on_canvas):
    """Record a page-reported canvas NDC sample (called from the HTTP accept thread)."""
    try:
        x = float(ndc_x); y = float(ndc_y)
    except (TypeError, ValueError):
        return
    if not (math.isfinite(x) and math.isfinite(y)):
        return
    with _PAGE_POINTER_LOCK:
        _PAGE_POINTER["t"] = time.monotonic()
        _PAGE_POINTER["ndc_x"] = max(-1.5, min(1.5, x))
        _PAGE_POINTER["ndc_y"] = max(-1.5, min(1.5, y))
        _PAGE_POINTER["on"] = bool(on_canvas)


def _get_page_pointer(ttl=_POINTER_TTL):
    """Return (ndc_x, ndc_y) from a fresh on-canvas page report, or None."""
    with _PAGE_POINTER_LOCK:
        t = _PAGE_POINTER["t"]
        if t <= 0.0 or (time.monotonic() - t) > float(ttl):
            return None
        if not _PAGE_POINTER["on"]:
            return None
        return (_PAGE_POINTER["ndc_x"], _PAGE_POINTER["ndc_y"])


def _parse_pointer_body(raw):
    """Parse a /trackball/pointer JSON body. Returns (ndc_x, ndc_y, on_canvas) or None."""
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if "ndc_x" in data and "ndc_y" in data:
        on = data.get("on_canvas", True)
        return (data.get("ndc_x"), data.get("ndc_y"), bool(on))
    # Alternate: CSS-pixel offset inside the canvas + size (also exact).
    if all(k in data for k in ("x", "y", "w", "h")):
        try:
            w = float(data["w"]); h = float(data["h"])
            if w < 1e-6 or h < 1e-6:
                return None
            fx = float(data["x"]) / w
            fy = float(data["y"]) / h
            on = (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0)
            return (fx * 2.0 - 1.0, 1.0 - fy * 2.0, on)
        except (TypeError, ValueError):
            return None
    return None


class OnshapeBridge:
    """Accumulates routed nav deltas, serves the 3Dconnexion endpoint, and applies motion to the
    focused Onshape view from its worker thread."""

    def __init__(self, on_connection_changed=None, rate_hz=DEFAULT_FLUSH_HZ,
                 host=BRIDGE_HOST, port=BRIDGE_PORT, cert_path=None, key_path=None,
                 on_focus_changed=None):
        self.on_connection_changed = on_connection_changed
        self.on_focus_changed = on_focus_changed
        requested_host = BRIDGE_HOST if host is None else host
        if requested_host != BRIDGE_HOST:
            raise ValueError("Onshape bridge must bind the fixed loopback endpoint")
        requested_port = BRIDGE_PORT if port is None else port
        if requested_port != BRIDGE_PORT:
            raise ValueError(f"Onshape bridge port must remain fixed at {BRIDGE_PORT}")
        self._host = BRIDGE_HOST
        self._port = BRIDGE_PORT
        d_cert, d_key = default_cert_paths()
        self._cert_path = cert_path or d_cert
        self._key_path = key_path or d_key

        self._lock = threading.Lock()
        self._acc = [0.0] * 6
        self._stop = threading.Event()
        self._enabled = threading.Event()          # explicit Onshape setup/Enabled gate
        self._period = 1.0 / self._clamp_rate(rate_hz)
        self._server_thread = None
        self._worker_thread = None
        self._srv = None
        self._conn = None                 # current ready connection (or None)
        self._connected = False
        self._viewport_focused = False    # explicit focus state of the current controller
        self._version = ""
        self._in_motion = False
        self._held_pivot = None           # orbit pivot captured at gesture start and held (see _navigate)
        self._held_zoom_pivot = None      # To Object/To Cursor target, held for one zoom gesture
        self._held_zoom_resolved = False
        self._nav_logged = False          # debug: log one camera read/write per gesture
        self._force_focus = bool(_SPIN)   # spike/test: drive even if the client reports unfocused
        self._log = get_logger()
        self._warned = set()
        # Control scheme (orbit pivot / orbit style / zoom mode); a dict ref-swap is atomic, so the
        # worker reads it lock-free each flush (same pattern as the broker / SW driver).
        self._scheme = {"op": "screen_center", "os": "free", "zm": "to_center"}
        # _horizon_fixed is None until the first set_scheme so
        # a daemon that STARTS in turntable never levels -- only a real free->turntable switch
        # queues _level_pending, which the worker applies on the next navigation step.
        self._level_horizon = True
        self._horizon_fixed = None
        self._level_pending = False
        self._pivot_hold_sec = _MOTION_IDLE
        self._zoom_hold_sec = _MOTION_IDLE

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

    def set_pivot_hold(self, sec):
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            sec = _MOTION_IDLE
        self._pivot_hold_sec = min(10.0, max(0.0, sec))

    def set_zoom_hold(self, sec):
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            sec = _MOTION_IDLE
        self._zoom_hold_sec = min(10.0, max(0.0, sec))

    def set_scheme(self, orbit_pivot, orbit_style, zoom_mode, selection_overrides_pivot=True,
                   orbit_pivot_fallbacks=None, level_horizon_on_entry=True):
        self._scheme = {"op": orbit_pivot, "os": orbit_style, "zm": zoom_mode,
                        "sel_override": bool(selection_overrides_pivot),
                        "fallbacks": list(orbit_pivot_fallbacks or [])}
        self._held_pivot = None
        self._held_zoom_pivot = None
        self._held_zoom_resolved = False
        # A free->turntable switch queues one horizon-leveling pass,
        # applied by the worker on the next navigation step. Leaving turntable cancels it.
        self._level_horizon = bool(level_horizon_on_entry)
        fixed = (orbit_style == "turntable")
        if fixed and self._horizon_fixed is False and self._level_horizon:
            self._level_pending = True
        elif not fixed or not self._level_horizon:
            self._level_pending = False
        self._horizon_fixed = fixed

    def submit(self, ox, oy, oz, px, py, zoom):
        with self._lock:
            a = self._acc
            a[0] += ox; a[1] += oy; a[2] += oz
            a[3] += px; a[4] += py; a[5] += zoom

    def discard_pending(self):
        """Drop deltas captured before Onshape lost daemon-side foreground ownership."""
        self._drain()

    def is_connected(self):
        return self._connected

    def is_viewport_focused(self):
        """Whether the current controller explicitly reports viewport focus."""
        return self._viewport_focused

    def version(self):
        return self._version

    def set_enabled(self, enabled):
        """Bind/generate credentials only while the configured integration is enabled."""
        if enabled:
            self._enabled.set()
            return
        self._enabled.clear()
        self._set_viewport_focused(False)
        self._set_connected(False)
        self._drain()
        self._clear_gesture_state()
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

    def start(self):
        if self._server_thread is not None:
            return
        self._server_thread = threading.Thread(target=self._run_server, name="onshape-server",
                                               daemon=True)
        self._worker_thread = threading.Thread(target=self._run_worker, name="onshape-worker",
                                               daemon=True)
        self._server_thread.start()
        self._worker_thread.start()

    def stop(self):
        self._stop.set()
        self._set_viewport_focused(False)
        self._set_connected(False)
        self._drain()
        self._clear_gesture_state()
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
        while not self._stop.is_set():
            if not self._enabled.wait(0.25):
                continue
            if not ensure_cert(self._cert_path, self._key_path):
                self._log.info("onshape: no TLS cert (run Onshape 'Set up', or install "
                               "cryptography/openssl); bridge disabled")
                self._enabled.clear()
                continue
            self._serve_enabled()
            self._stop.wait(0.5)             # avoid a tight retry loop on bind/cert-load failure

    def _serve_enabled(self):
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
        while not self._stop.is_set() and self._enabled.is_set():
            try:
                raw, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_raw, args=(raw, ctx), daemon=True).start()
        try:
            srv.close()
        except Exception:
            pass
        if self._srv is srv:
            self._srv = None

    def _handle_raw(self, raw, ctx):
        if not self._enabled.is_set():
            try:
                raw.close()
            except Exception:
                pass
            return
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
        previous = self._conn
        if previous is not None and previous is not conn:
            previous.alive = False
            try:
                previous.sock.close()
            except Exception:
                pass
            self._clear_gesture_state()
        self._conn = conn
        self._version = conn.version_str()
        self._set_connected(True)
        self._set_viewport_focused(conn.focus)

    def _on_conn_focus_changed(self, conn, focused):
        if self._conn is conn:
            self._set_viewport_focused(focused)

    def _set_viewport_focused(self, focused):
        focused = bool(focused)
        if focused == self._viewport_focused:
            return
        self._viewport_focused = focused
        if self.on_focus_changed:
            try:
                self.on_focus_changed(bool(focused))
            except Exception:
                pass

    def _on_conn_closed(self, conn):
        if self._conn is conn:
            self._conn = None
            self._set_viewport_focused(False)
            self._set_connected(False)
            self._drain()
            self._clear_gesture_state()

    def _clear_gesture_state(self):
        self._in_motion = False
        self._held_pivot = None
        self._held_zoom_pivot = None
        self._held_zoom_resolved = False
        self._nav_logged = False

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
                    idle = cycle - last_motion if last_motion > 0.0 else float("inf")
                    self._navigate(conn, delta, idle)
                    last_motion = cycle
                except _ConnDead as exc:
                    self._warn_once(
                        "rpc-drop", "onshape: navigation connection dropped (%s)" %
                        (str(exc) or "connection closed"))
                    self._drop(conn)
            elif self._in_motion and (
                    not focused or cycle - last_motion > max(
                        self._pivot_hold_sec, self._zoom_hold_sec)):
                # End the gesture promptly when Onshape loses focus, or after an idle pause.
                try:
                    self._end_motion(conn)
                except _ConnDead as exc:
                    self._warn_once(
                        "rpc-drop", "onshape: navigation connection dropped (%s)" %
                        (str(exc) or "connection closed"))
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
    def _navigate(self, conn, delta, idle=0.0):
        ox, oy, oz, px, py, zoom = delta
        affine = conn.read("view.affine")
        scheme = self._scheme
        if not (isinstance(affine, list) and len(affine) >= 16):
            self._warn_once("affine_read",
                            "onshape: view.affine read returned %r (unexpected shape)" % (affine,))
            return
        eye, right, up, back = _decode_affine(affine)
        eye_in = eye
        if not self._in_motion:
            self._begin_motion(conn)
        changed_affine = False
        new_extents = None

        # Level ONCE on turntable entry (queued by set_scheme): rebuild right/up so
        # camera-right is horizontal while back (the view direction) and the eye stay put -- the
        # screen centre and zoom are untouched, only the roll goes. Skipped in the degenerate
        # straight-along-WORLD_UP view. Uses the same +Z Top-plane normal as the turntable itself.
        if self._level_pending:
            self._level_pending = False
            leveled = _level_horizon_basis(back)
            if leveled is not None:
                right, up = leveled
                changed_affine = True
                self._log.info("onshape: horizon leveled on turntable entry")

        if ox or oy or oz:
            self._held_zoom_pivot = None
            self._held_zoom_resolved = False
            # Orbit about the gesture pivot, captured ONCE at the start of the gesture and held (so
            # the hit-test runs once, not per frame, and the pivot doesn't chase the moving view).
            pivot = self._gesture_pivot(conn, scheme, eye, right, up, back, idle)
            if pivot is None:
                self._warn_once(
                    "orbit-pivot",
                    "onshape: orbit did not reach a camera write because no configured pivot "
                    "resolved")
                if changed_affine:              # still deliver the entry-leveling write below
                    ox = oy = oz = 0.0
                else:
                    return
            else:
                eye, right, up, back = self._orbit(ox, oy, oz, eye, right, up, back, pivot, scheme)
                changed_affine = True
        elif px or py:
            eye = self._pan(conn, eye, right, up, px, py)
            changed_affine = True
            self._held_pivot = None         # pan moved the screen centre -> re-hit on the next orbit
        elif zoom:
            cursor_zoom = scheme.get("zm", "to_center") == "to_cursor"
            if cursor_zoom and idle > self._zoom_hold_sec:
                self._held_zoom_pivot = None
                self._held_zoom_resolved = False
            if not cursor_zoom:
                self._held_zoom_pivot = self._zoom_target(conn, scheme, eye, right, up, back)
                self._held_zoom_resolved = False
            elif not self._held_zoom_resolved:
                self._held_zoom_pivot = self._zoom_target(conn, scheme, eye, right, up, back)
                self._held_zoom_resolved = True
            zpivot = self._held_zoom_pivot
            if self._perspective(conn):
                reference = zpivot or self._object_center(conn)
                eye = self._zoom_persp(eye, _v_neg(back), reference, zoom,
                                       hold_target=zpivot is not None)
                changed_affine = True
            else:
                new_extents = self._zoom_ortho(conn, zoom)
                if new_extents is not None and zpivot is not None:
                    eye = self._zoom_ortho_eye(eye, right, up, zpivot,
                                                self._zoom_factor(zoom))
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
        affine_written = False
        if changed_affine:
            try:
                conn.write("view.affine", _encode_affine(eye, right, up, back))
                affine_written = True
            except _PropUnsupported:
                self._warn_once("affine", "onshape: client rejected view.affine writes")
        if affine_written:
            operation = "orbit" if (ox or oy or oz) else "pan" if (px or py) else "zoom"
            self._warn_once(
                "camera-write-" + operation,
                "onshape: %s reached the view.affine camera write" % operation)
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

    def _gesture_pivot(self, conn, scheme, eye, right, up, back, idle=0.0):
        """The orbit pivot for this gesture -- computed once (this is where the hit-test happens),
        then HELD until a pan/zoom moves the view or the gesture ends. Also shows Onshape's on-screen
        pivot marker when it (re)computes."""
        if idle > self._pivot_hold_sec:
            self._held_pivot = None
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
        self._held_zoom_pivot = None
        self._held_zoom_resolved = False
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

    @staticmethod
    def _zoom_factor(zoom):
        return max(0.02, 1.0 - ZOOM_SIGN * zoom * ZOOM_SCALE)

    def _zoom_persp(self, eye, forward, pivot, zoom, hold_target=False):
        """Perspective To Object/To Cursor scales the eye about P; To Center dollies forward."""
        if hold_target and pivot is not None:
            return _v_add(pivot, _v_scale(_v_sub(eye, pivot), self._zoom_factor(zoom)))
        dist = _v_len(_v_sub(pivot, eye)) if pivot is not None else 1.0
        if dist < 1e-6:
            dist = 1.0
        return _v_add(eye, _v_scale(forward, ZOOM_SIGN * zoom * ZOOM_SCALE * dist))

    @staticmethod
    def _zoom_ortho_eye(eye, right, up, pivot, factor):
        """Shift an orthographic eye so P keeps its screen coordinate as extents scale."""
        offset = _v_sub(pivot, eye)
        planar = _v_add(_v_scale(right, _v_dot(offset, right)),
                        _v_scale(up, _v_dot(offset, up)))
        return _v_add(eye, _v_scale(planar, 1.0 - factor))

    def _zoom_ortho(self, conn, zoom):
        """Orthographic zoom = scale view.extents about the view centre (moving the eye does nothing
        under an orthographic projection). Returns the new 6-float extents, or None. Reads the
        extents FRESH (not cached) so successive zoom frames compound off the latest value."""
        ext = conn.read("view.extents")
        if not (isinstance(ext, list) and len(ext) >= 6):
            return None
        s = self._zoom_factor(zoom)
        return [ext[0] * s, ext[1] * s, ext[2], ext[3] * s, ext[4] * s, ext[5]]

    # --- scheme geometry ----------------------------------------------------------------------
    def _zoom_target(self, conn, scheme, eye, right, up, back):
        """Resolve the advertised zoom target. Empty space keeps the cursor's screen position by
        intersecting its ray with the current target-depth plane; orbit ray misses still use the
        explicit fallback chain instead."""
        zm = scheme.get("zm", "to_center")
        if zm == "to_object":
            return self._object_center(conn)
        if zm == "to_cursor":
            if scheme.get("sel_override", True):
                selected = self._selection_center(conn)
                if selected is not None:
                    return selected
            return (self._hit_cursor(conn, eye, right, up, back) or
                    self._cursor_depth_point(conn, eye, right, up, back))
        return None

    def _cursor_depth_point(self, conn, eye, right, up, back):
        """Synthetic To Cursor target on a view-facing plane at the current target/model depth."""
        ndc = _get_page_pointer()
        if ndc is None:
            return None
        half_x, half_y = self._view_halves(conn)
        origin, direction = self._pixel_ray(ndc[0], ndc[1], eye, right, up, back,
                                            half_x, half_y, 0.0)
        target = conn.read("view.target", ttl=_TGT_TTL)
        reference = None
        if isinstance(target, list) and len(target) >= 3:
            try:
                candidate = tuple(float(v) for v in target[:3])
                if all(math.isfinite(v) for v in candidate):
                    reference = candidate
            except (TypeError, ValueError):
                pass
        reference = reference or self._object_center(conn)
        forward = _v_neg(back)
        if reference is None:
            reference = _v_add(eye, _v_scale(forward, max(half_y, 1.0)))
        denom = _v_dot(direction, forward)
        if abs(denom) < 1e-9:
            return None
        distance = _v_dot(_v_sub(reference, origin), forward) / denom
        if distance <= 1e-6:
            distance = max(_v_len(_v_sub(reference, eye)), half_y, 1.0)
        return _v_add(origin, _v_scale(direction, distance))

    def _pivot(self, conn, scheme, eye, right, up, back):
        op = scheme.get("op", "screen_center")
        # A camera primary keeps its selection-override exemption even though the method
        # itself is unsupported here -- the same convention as Fusion/SolidWorks/FreeCAD,
        # whose resolvers also skip camera but never let selection hijack it.
        if scheme.get("sel_override", True) and op != "camera":
            selected = self._selection_center(conn)
            if selected is not None:
                return selected
        for method in orbit_pivot_candidates(op, scheme.get("fallbacks", [])):
            if method == "origin":
                return (0.0, 0.0, 0.0)
            if method == "cursor":
                point = self._hit_cursor(conn, eye, right, up, back)
            elif method == "screen_center":
                point = self._hit_screen_center(conn, eye, right, up, back)
            elif method == "selection":
                point = self._selection_center(conn)
            elif method == "object":
                point = self._object_center(conn)
            else:
                # camera / cursor_3d unsupported in Onshape: no 3D cursor exists, and the
                # default view is orthographic, where rotating about the eye just slides the
                # image around instead of turning in place. Skip to the next candidate.
                continue
            if point is not None:
                return point
        return None

    def _hit_screen_center(self, conn, eye, right, up, back):
        """navlib hit-test through the screen centre (NDC 0,0)."""
        half_x, half_y = self._view_halves(conn)
        vh = max(half_x, half_y)
        lookfrom, direction = self._pixel_ray(0.0, 0.0, eye, right, up, back, half_x, half_y, vh * 8.0)
        return self._hit_ray(conn, lookfrom, direction, vh)

    def _hit_cursor(self, conn, eye, right, up, back):
        """navlib hit-test through the page-reported canvas NDC, or None when no fresh sample."""
        ndc = _get_page_pointer()
        if ndc is None:
            return None
        half_x, half_y = self._view_halves(conn)
        if _DEBUG:
            self._log.info("onshape cursor: page-ndc=(%.3f,%.3f) half=(%.3f,%.3f)",
                           ndc[0], ndc[1], half_x, half_y)
        vh = max(half_x, half_y)
        lookfrom, direction = self._pixel_ray(ndc[0], ndc[1], eye, right, up, back,
                                              half_x, half_y, vh * 8.0)
        return self._hit_ray(conn, lookfrom, direction, vh)

    def _hit_ray(self, conn, lookfrom, direction, vh):
        """Write an arbitrary pick ray, widen the aperture until a bbox-valid, re-cast-confirmed
        hit.lookat lands. Shared by centre and cursor. Returns the hit point or None."""
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
        confirm_from = _v_sub(lookfrom, _v_scale(direction, _CONFIRM_BACKOFF * vh))
        for f in HIT_APERTURES:
            try:
                conn.write("hit.lookfrom", list(lookfrom))
                conn.write("hit.aperture", max(1e-5, vh * f))
                pt = conn._rpc("self:read", ["hit.lookat"])
            except _PropUnsupported:
                continue
            if not self._valid_hit(pt, bbox):
                continue
            # Onshape fabricates a no-hit as a point on the ray at the scene's camera
            # distance (it never signals a miss). Confirm the candidate by re-casting the
            # SAME ray from further back: a real surface point is invariant to the ray
            # origin, a fabricated at-depth point tracks it. Disagreement -> not a real
            # surface -> keep widening / let the configured chain continue.
            try:
                conn.write("hit.lookfrom", list(confirm_from))
                pt2 = conn._rpc("self:read", ["hit.lookat"])
            except _PropUnsupported:
                continue
            if (self._valid_hit(pt2, bbox)
                    and max(abs(float(pt[i]) - float(pt2[i])) for i in range(3))
                    <= _CONFIRM_TOL * vh):
                return (float(pt[0]), float(pt[1]), float(pt[2]))
            if _DEBUG:
                self._log.info("onshape hit: origin-dependent hit rejected as a fabricated "
                               "no-hit point (aperture %.3g)", vh * f)
        return None

    @staticmethod
    def _pixel_ray(ndc_x, ndc_y, eye, right, up, back, half_x, half_y, backoff):
        """Orthographic pick ray through a canvas NDC point (x right, y up, both in [-1,1]).
        NDC (0,0) is the screen-centre ray used by _hit_screen_center. lookfrom is pulled `backoff`
        along forward so the ray starts outside the model."""
        fwd = _v_normalize(_v_neg(back))
        offset = _v_add(_v_scale(right, ndc_x * half_x), _v_scale(up, ndc_y * half_y))
        lookfrom = _v_sub(_v_add(eye, offset), _v_scale(fwd, backoff))
        return lookfrom, fwd

    @staticmethod
    def _valid_hit(pt, bbox):
        """A hit counts only if it's a finite 3-point essentially INSIDE the model bounding box
        (0.1% of its diagonal of float/transport slop -- a real surface point always is). The old
        10%-of-diagonal margin let Onshape's fabricated no-hit points (a point on the ray at the
        scene's camera distance, emitted when the raycast misses) pass as pivots whenever they
        landed near the model; those must fail so the configured fallback chain continues."""
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
            m = 1e-3 * math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-9
            if not (bbox[0] - m <= x <= bbox[3] + m and bbox[1] - m <= y <= bbox[4] + m
                    and bbox[2] - m <= z <= bbox[5] + m):
                return False
        return True

    def _object_center(self, conn):
        ext = conn.read("model.extents", ttl=_OBJ_TTL)
        if isinstance(ext, list) and len(ext) >= 6:
            return ((ext[0] + ext[3]) * 0.5, (ext[1] + ext[4]) * 0.5, (ext[2] + ext[5]) * 0.5)
        return None

    def _selection_center(self, conn):
        """Selection-extents centre exposed by the navlib client, or None.

        Navlib models selection state separately from model extents. Some clients omit these
        optional properties; ``conn.read`` already turns an unsupported read into None, preserving
        the designated-pivot path when an Onshape build does not expose them.
        """
        empty = conn.read("selection.empty", ttl=_OBJ_TTL)
        if empty is True:
            return None
        ext = conn.read("selection.extents", ttl=_OBJ_TTL)
        if isinstance(ext, list) and len(ext) >= 6:
            try:
                values = [float(v) for v in ext[:6]]
            except (TypeError, ValueError):
                return None
            if all(math.isfinite(v) for v in values):
                return tuple((values[i] + values[i + 3]) * 0.5 for i in range(3))
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
    bridge.set_enabled(True)
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
