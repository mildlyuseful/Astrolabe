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


DEFAULT_FLUSH_HZ = 30.0


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
    def __init__(self, port, on_clients_changed=None, rate_hz=DEFAULT_FLUSH_HZ):
        self.port = int(port)
        self.on_clients_changed = on_clients_changed
        self._lock = threading.Lock()
        self._clients = []
        self._targets = {}
        self._active_target = None
        self._default_period = 1.0 / self._clamp_rate(rate_hz)
        self._stop = threading.Event()
        self._srv = None

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

    # --- lifecycle -----------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._serve, name="navbroker-accept", daemon=True).start()
        threading.Thread(target=self._sender, name="navbroker-send", daemon=True).start()

    def stop(self):
        self._stop.set()
        try:
            if self._srv:
                self._srv.close()
        except Exception:
            pass

    # --- server --------------------------------------------------------------------
    def _serve(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.port))
            srv.listen(8)
            srv.settimeout(0.5)
            self._srv = srv
        except OSError:
            return
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
        try:
            conn.settimeout(5.0)
            line = self._read_line(conn)
            if line:
                try:
                    hello = json.loads(line)
                    client.app = str(hello.get("app", "?"))
                    client.version = str(hello.get("version", "?"))
                    client.pid = int(hello.get("pid", 0))
                except (ValueError, TypeError):
                    pass
            with self._lock:
                self._clients.append(client)
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
                if client in self._clients:
                    self._clients.remove(client)
            try:
                conn.close()
            except Exception:
                pass
            self._changed()

    @staticmethod
    def _read_line(conn):
        buf = b""
        while b"\n" not in buf and len(buf) < 4096:
            try:
                chunk = conn.recv(256)
            except OSError:
                return None
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\n", 1)[0].decode("utf-8", "replace")

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

    def _sender(self):
        while not self._stop.wait(self._next_wait()):
            self._flush_once()

    def _changed(self):
        if self.on_clients_changed:
            try:
                self.on_clients_changed(self.client_infos())
            except Exception:
                pass
