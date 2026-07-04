"""Local nav broker: a 127.0.0.1-only socket server that streams navigation deltas to CAD
add-ons (the thin per-app drivers).

Protocol (newline-delimited JSON, both directions):
  add-on -> broker (once):   {"type":"hello","app":"fusion360","version":"...","pid":1234}
  broker -> add-on (stream): {"o":[ox,oy,oz],"p":[px,py],"z":zoom}   # per-flush accumulated delta

Design notes:
  * submit() (called on the BLE thread) only accumulates -- never blocks on a socket.
  * a sender thread flushes the accumulated delta at a fixed rate and zeroes it, so a slow
    add-on coalesces motion instead of losing it (same idea as the firmware's float carry).
  * bound to 127.0.0.1 only -- never exposed off-box.
"""
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


class NavBroker:
    def __init__(self, port, on_clients_changed=None, rate_hz=DEFAULT_FLUSH_HZ):
        self.port = int(port)
        self.on_clients_changed = on_clients_changed       # callback(list_of_client_info)
        self._lock = threading.Lock()
        self._acc = [0.0] * 6
        self._clients = []
        self._stop = threading.Event()
        self._srv = None
        self._period = 1.0 / self._clamp_rate(rate_hz)     # flush/refresh interval
        # Control scheme sent each frame. "adv" is an optional app-specific extras dict (e.g.
        # Blender's richer nav options) that rides along additively; None => omitted from the frame.
        self._scheme = {"op": "view", "os": "free", "zm": "to_center", "adv": None}

    @staticmethod
    def _clamp_rate(hz):
        try:
            hz = float(hz)
        except (TypeError, ValueError):
            hz = DEFAULT_FLUSH_HZ
        return min(240.0, max(1.0, hz))

    def set_rate(self, hz):
        """Live-update the 3D update/refresh rate (Hz). Applied to the next flush."""
        self._period = 1.0 / self._clamp_rate(hz)

    def set_scheme(self, orbit_pivot, orbit_style, zoom_mode, advanced=None):
        """Set the control scheme forwarded to the add-on in each frame.

        `advanced` is an optional app-specific extras dict (Blender's nav_mode / twist_action /
        zoom_style / lock_horizon / speeds / ...). When non-None it rides along as an additive
        "adv" object on every frame; existing add-ons (Fusion) read only o/p/z/op/os/zm and ignore
        it, so passing it never changes what they see."""
        with self._lock:
            self._scheme = {"op": orbit_pivot, "os": orbit_style, "zm": zoom_mode, "adv": advanced}

    # --- producer side (BLE thread) ------------------------------------------------
    def submit(self, ox, oy, oz, px, py, zoom):
        with self._lock:
            a = self._acc
            a[0] += ox; a[1] += oy; a[2] += oz
            a[3] += px; a[4] += py; a[5] += zoom

    @staticmethod
    def _build_frame(acc, scheme):
        """Build one wire frame from the accumulated delta + the current scheme. The optional "adv"
        extras object is included ONLY when set (Blender), so other add-ons' frames are byte-for-byte
        unchanged (they read only o/p/z/op/os/zm)."""
        obj = {"o": [acc[0], acc[1], acc[2]], "p": [acc[3], acc[4]], "z": acc[5],
               "op": scheme["op"], "os": scheme["os"], "zm": scheme["zm"]}
        if scheme.get("adv") is not None:
            obj["adv"] = scheme["adv"]
        return obj

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
            # Hold the connection open; reads only detect disconnect.
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

    def _sender(self):
        while not self._stop.is_set():
            time.sleep(self._period)             # re-read each loop so set_rate() applies live
            with self._lock:
                if not self._clients:
                    self._acc = [0.0] * 6        # drop motion while nobody is listening
                    continue
                a = self._acc
                if a[0] == 0.0 and a[1] == 0.0 and a[2] == 0.0 and a[3] == 0.0 and a[4] == 0.0 and a[5] == 0.0:
                    continue
                frame = json.dumps(self._build_frame(a, self._scheme))
                self._acc = [0.0] * 6
                clients = list(self._clients)
            payload = (frame + "\n").encode("utf-8")
            dead = []
            for c in clients:
                try:
                    c.conn.sendall(payload)
                except OSError:
                    dead.append(c)
            if dead:
                with self._lock:
                    for c in dead:
                        if c in self._clients:
                            self._clients.remove(c)
                self._changed()

    def _changed(self):
        if self.on_clients_changed:
            try:
                self.on_clients_changed(self.client_infos())
            except Exception:
                pass
