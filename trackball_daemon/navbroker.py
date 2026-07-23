"""Target-isolated loopback navigation broker for socket-host add-ons.

The on-wire protocol remains newline-delimited JSON:

* add-on -> broker once: ``{"type":"hello","app":"fusion360",...}``
* broker -> matching add-on: the legacy ``o/p/z/op/os/zm`` frame plus optional ``adv``.

Target identity is deliberately internal and comes from the existing hello ``app`` field. Motion,
rate, scheme/profile revision, and delivery state are owned per target; no frame is broadcast.
"""
from dataclasses import dataclass, field
import copy
import json
import socket
import threading
import time

from .app_registry import APP_SPECS, TransportKind
from .service_health import ServiceHealth, ServiceHealthState


DEFAULT_FLUSH_HZ = 30.0
_BROKER_APP_IDS = frozenset(
    spec.app_id for spec in APP_SPECS if spec.transport is TransportKind.BROKER)


class _HandshakeError(ValueError):
    pass


class _Client:
    __slots__ = ("conn", "app", "version", "pid")

    def __init__(self, conn):
        self.conn = conn
        self.app = "?"
        self.version = "?"
        self.pid = 0


@dataclass
class _TargetState:
    period: float
    next_flush: float
    accumulator: list = field(default_factory=lambda: [0.0] * 6)
    scheme: dict = field(default_factory=lambda: {
        "op": "screen_center", "os": "free", "zm": "to_center", "adv": None,
    })
    profile_revision: int = 0
    accepted_state_revision: int = -1
    pending_state_revision: int = -1
    delivery_serial: int = 0
    discarded_samples: int = 0

    def discard(self):
        if any(self.accumulator):
            self.discarded_samples += 1
        self.accumulator = [0.0] * 6
        self.pending_state_revision = -1


class NavBroker:
    def __init__(self, port, on_clients_changed=None, rate_hz=DEFAULT_FLUSH_HZ,
                 on_health_changed=None):
        self.port = int(port)
        self.on_clients_changed = on_clients_changed
        self.on_health_changed = on_health_changed
        self._lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._clients = []
        self._targets = {}
        self._active_target = None
        self._default_period = 1.0 / self._clamp_rate(rate_hz)
        self._stop = threading.Event()
        self._srv = None
        self._started = False
        self._health = ServiceHealth(
            "navigation-broker", ServiceHealthState.DISABLED, "broker has not started")
        self._publish_health(self._health)

    @staticmethod
    def _clamp_rate(hz):
        try:
            hz = float(hz)
        except (TypeError, ValueError):
            hz = DEFAULT_FLUSH_HZ
        return min(240.0, max(1.0, hz))

    def _target_unlocked(self, target):
        if not isinstance(target, str) or not target:
            raise ValueError("navigation target must be a non-empty app ID")
        state = self._targets.get(target)
        if state is None:
            state = _TargetState(self._default_period, time.monotonic() + self._default_period)
            self._targets[target] = state
        return state

    def set_rate(self, target, hz):
        """Set one target's live flush rate without affecting any other host."""
        period = 1.0 / self._clamp_rate(hz)
        with self._lock:
            state = self._target_unlocked(target)
            state.period = period
            state.next_flush = time.monotonic() + period

    def set_scheme(self, target, orbit_pivot, orbit_style, zoom_mode, advanced=None,
                   profile_revision=None):
        """Publish one target's scheme, discarding deltas accumulated under an older profile."""
        scheme = {
            "op": orbit_pivot,
            "os": orbit_style,
            "zm": zoom_mode,
            "adv": copy.deepcopy(advanced),
        }
        with self._lock:
            state = self._target_unlocked(target)
            revision = (state.profile_revision + 1 if profile_revision is None
                        else int(profile_revision))
            if scheme != state.scheme or revision != state.profile_revision:
                state.discard()
                state.scheme = scheme
                state.profile_revision = revision

    def activate_target(self, target):
        """Atomically select one socket target and discard the prior target's pending motion."""
        with self._lock:
            if target == self._active_target:
                return
            if self._active_target is not None:
                self._target_unlocked(self._active_target).discard()
            self._active_target = target
            if target is not None:
                self._target_unlocked(target).discard()

    def discard_pending(self, target):
        with self._lock:
            state = self._targets.get(target)
            if state is not None:
                state.discard()

    # --- producer side -------------------------------------------------------------
    def submit(self, target, ox, oy, oz, px, py, zoom, *, state_revision=0):
        """Associate one sample with its captured target and monotonic runtime revision."""
        revision = int(state_revision)
        with self._lock:
            if target != self._active_target:
                return False
            state = self._target_unlocked(target)
            if revision < state.accepted_state_revision:
                return False
            if revision > state.accepted_state_revision:
                state.discard()
                state.accepted_state_revision = revision
            state.pending_state_revision = revision
            a = state.accumulator
            a[0] += ox; a[1] += oy; a[2] += oz
            a[3] += px; a[4] += py; a[5] += zoom
        return True

    @staticmethod
    def _build_frame(acc, scheme):
        """Build the unchanged legacy wire frame plus optional additive advanced data."""
        obj = {"o": [acc[0], acc[1], acc[2]], "p": [acc[3], acc[4]], "z": acc[5],
               "op": scheme["op"], "os": scheme["os"], "zm": scheme["zm"]}
        if scheme.get("adv") is not None:
            obj["adv"] = scheme["adv"]
        return obj

    def delivery_state(self, target):
        """Detached diagnostics used by tests/HUD plumbing; never exposes mutable internals."""
        with self._lock:
            state = self._target_unlocked(target)
            return {
                "active": target == self._active_target,
                "period": state.period,
                "profile_revision": state.profile_revision,
                "accepted_state_revision": state.accepted_state_revision,
                "pending_state_revision": state.pending_state_revision,
                "delivery_serial": state.delivery_serial,
                "discarded_samples": state.discarded_samples,
                "pending": tuple(state.accumulator),
                "scheme": {
                    "op": state.scheme["op"],
                    "os": state.scheme["os"],
                    "zm": state.scheme["zm"],
                    "adv": copy.deepcopy(state.scheme["adv"]),
                },
            }

    def client_infos(self):
        with self._lock:
            return [(c.app, c.version, c.pid) for c in self._clients]

    def health(self):
        with self._health_lock:
            return self._health

    def _set_health(self, state, detail):
        health = ServiceHealth("navigation-broker", state, detail)
        with self._health_lock:
            if health == self._health:
                return
            self._health = health
        self._publish_health(health)

    def _publish_health(self, health):
        if self.on_health_changed:
            try:
                self.on_health_changed(health)
            except Exception:
                pass

    # --- lifecycle -----------------------------------------------------------------
    def start(self):
        if self._started:
            return
        self._started = True
        self._set_health(
            ServiceHealthState.WAITING,
            f"starting loopback listener on 127.0.0.1:{self.port}",
        )
        threading.Thread(target=self._serve, name="navbroker-accept", daemon=True).start()
        threading.Thread(target=self._sender, name="navbroker-send", daemon=True).start()

    def stop(self):
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass
        self._set_health(ServiceHealthState.DISABLED, "broker stopped")

    # --- server --------------------------------------------------------------------
    def _serve(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.port))
            srv.listen(8)
            srv.settimeout(0.5)
            self._srv = srv
        except OSError as exc:
            self._set_health(
                ServiceHealthState.FAILED,
                f"cannot listen on 127.0.0.1:{self.port}: {exc}",
            )
            return
        self._set_health(
            ServiceHealthState.WAITING,
            f"listening on 127.0.0.1:{self.port}; waiting for an add-on handshake",
        )
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn):
        client = _Client(conn)
        registered = False
        try:
            conn.settimeout(5.0)
            try:
                client.app, client.version, client.pid = self._parse_hello(
                    self._read_line(conn))
            except _HandshakeError as exc:
                self._set_health(
                    ServiceHealthState.DEGRADED,
                    f"rejected add-on handshake: {exc}",
                )
                return
            with self._lock:
                self._clients.append(client)
                registered = True
            self._changed()
            conn.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    data = conn.recv(256)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
        finally:
            with self._lock:
                if registered and client in self._clients:
                    self._clients.remove(client)
            try:
                conn.close()
            except Exception:
                pass
            if registered:
                self._changed()

    @staticmethod
    def _read_line(conn):
        buf = b""
        while b"\n" not in buf:
            if len(buf) >= 4096:
                raise _HandshakeError("hello exceeds 4096 bytes")
            try:
                chunk = conn.recv(256)
            except socket.timeout as exc:
                raise _HandshakeError("timed out waiting for hello") from exc
            except OSError as exc:
                raise _HandshakeError(f"could not read hello: {exc}") from exc
            if not chunk:
                raise _HandshakeError("connection closed before a complete hello")
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _HandshakeError("hello is not valid UTF-8") from exc

    @staticmethod
    def _parse_hello(line):
        try:
            hello = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise _HandshakeError("hello is not valid JSON") from exc
        if not isinstance(hello, dict):
            raise _HandshakeError("hello must be a JSON object")
        if hello.get("type") != "hello":
            raise _HandshakeError("message type must be 'hello'")
        app = hello.get("app")
        if not isinstance(app, str) or not app or app != app.strip().lower():
            raise _HandshakeError("app must be a normalized supported app ID")
        if app not in _BROKER_APP_IDS:
            raise _HandshakeError(f"unsupported app ID {app!r}")
        version = hello.get("version")
        if not isinstance(version, str) or not version.strip():
            raise _HandshakeError("version must be a non-empty string")
        pid = hello.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
            raise _HandshakeError("pid must be a non-negative integer")
        return app, version.strip(), pid

    def _next_wait(self):
        with self._lock:
            if not self._targets:
                return self._default_period
            now = time.monotonic()
            return max(0.001, min(state.next_flush - now for state in self._targets.values()))

    def _collect_due(self, now, force=False):
        deliveries = []
        with self._lock:
            for target, state in self._targets.items():
                if not force and now < state.next_flush:
                    continue
                state.next_flush = now + state.period
                if not any(state.accumulator):
                    continue
                clients = [client for client in self._clients if client.app == target]
                if not clients:
                    state.discard()
                    continue
                frame = json.dumps(self._build_frame(state.accumulator, state.scheme))
                revision = state.pending_state_revision
                state.accumulator = [0.0] * 6
                state.pending_state_revision = -1
                state.delivery_serial += 1
                deliveries.append((target, (frame + "\n").encode("utf-8"), clients, revision))
        return deliveries

    def _flush_once(self, *, now=None, force=False):
        dead = []
        for _target, payload, clients, _revision in self._collect_due(
                time.monotonic() if now is None else now, force=force):
            for client in clients:
                try:
                    client.conn.sendall(payload)
                except OSError:
                    dead.append(client)
        if dead:
            with self._lock:
                for client in dead:
                    if client in self._clients:
                        self._clients.remove(client)
            self._changed()
            self._set_health(
                ServiceHealthState.DEGRADED,
                f"lost {len(dead)} add-on connection(s) while sending navigation; awaiting reconnect",
            )

    def _sender(self):
        try:
            while not self._stop.wait(self._next_wait()):
                self._flush_once()
        except Exception as exc:
            self._set_health(
                ServiceHealthState.FAILED,
                f"navigation sender stopped unexpectedly: {exc}",
            )

    def _changed(self):
        infos = self.client_infos()
        if infos:
            self._set_health(
                ServiceHealthState.HEALTHY,
                f"{len(infos)} supported add-on connection(s) active",
            )
        else:
            self._set_health(
                ServiceHealthState.WAITING,
                f"listening on 127.0.0.1:{self.port}; waiting for an add-on handshake",
            )
        if self.on_clients_changed:
            try:
                self.on_clients_changed(infos)
            except Exception:
                pass
