# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Loopback HTTP server for the settings UI.

The UI is a static page that talks to the daemon over three endpoints:

    GET  /api/state       everything the user configures
    GET  /api/events      Server-Sent Events: "state" and "live" invalidations, motion, input
    POST /api/action      {"action": name, "params": {...}}  ->  {"ok": bool, ...}

The listener binds 127.0.0.1 on an ephemeral port. API requests need the per-run token (header,
or query string for EventSource, which cannot set headers) and a Host header naming this exact
listener, so a web page in a browser cannot reach it through DNS rebinding. Static files are not
secret and carry no token.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import importlib.resources
import json
import logging
import queue
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from .api import UiApi

logger = logging.getLogger("trackball_daemon.webui")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
MOTION_HZ = 30
HEARTBEAT_S = 15


class EventHub:
    """Fan-out to SSE clients. Each client gets a bounded queue; a slow one drops, never blocks."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients = set()

    def subscribe(self):
        client = queue.Queue(maxsize=256)
        with self._lock:
            self._clients.add(client)
        return client

    def unsubscribe(self, client):
        with self._lock:
            self._clients.discard(client)

    @property
    def client_count(self):
        with self._lock:
            return len(self._clients)

    def publish(self, event, data=None):
        message = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.put_nowait(message)
            except queue.Full:
                pass


class MotionTap:
    """Sum rotation between pushes so the UI's ball sees every radian at a modest rate."""

    def __init__(self, hub):
        self.hub = hub
        self._lock = threading.Lock()
        self._sum = [0.0, 0.0, 0.0]
        self._pending = False
        self._last = 0.0

    def add(self, rotation):
        if not self.hub.client_count:
            return
        with self._lock:
            for index in range(3):
                self._sum[index] += rotation[index]
            now = time.monotonic()
            if now - self._last < 1.0 / MOTION_HZ:
                self._pending = True
                return
            total, self._sum, self._pending, self._last = self._sum, [0.0, 0.0, 0.0], False, now
        self.hub.publish("motion", total)

    def flush(self):
        with self._lock:
            if not self._pending:
                return
            total, self._sum, self._pending = self._sum, [0.0, 0.0, 0.0], False
            self._last = time.monotonic()
        self.hub.publish("motion", total)


def _static(name):
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    resource = importlib.resources.files("trackball_daemon.webui").joinpath("static", name)
    return resource.read_bytes() if resource.is_file() else None


class UiServer:
    def __init__(self, app):
        self.app = app
        self.hub = EventHub()
        self.motion = MotionTap(self.hub)
        self.api = UiApi(app, on_change=self.changed)
        self.token = secrets.token_urlsafe(24)
        self._httpd = None
        self._thread = None

    @property
    def port(self):
        return self._httpd.server_address[1] if self._httpd else None

    def url(self, page=""):
        return f"http://127.0.0.1:{self.port}/#t={self.token}" + (f"&p={page}" if page else "")

    def start(self):
        server = self

        class Handler(_Handler):
            ui = server

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="webui", daemon=True)
        self._thread.start()
        logger.info("settings UI listening on 127.0.0.1:%d", self.port)

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def changed(self, topic="state"):
        """Tell clients something changed; they fetch what they need. Safe from any thread."""
        self.hub.publish(topic)

    def publish_input(self, pressed):
        self.hub.publish("input", list(pressed))


class _Handler(BaseHTTPRequestHandler):
    ui = None
    protocol_version = "HTTP/1.1"
    server_version = "Astrolabe"

    def log_message(self, fmt, *args):
        logger.debug("webui %s", fmt % args)

    # --- gatekeeping ----------------------------------------------------------------------

    def _host_ok(self):
        port = self.ui.port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, query):
        supplied = self.headers.get("X-Astrolabe-Token") or query.get("t", [""])[0]
        return hmac.compare_digest(supplied.encode(), self.ui.token.encode())

    def _send(self, status, body=b"", content_type="application/json", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload).encode("utf-8"))

    # --- routes ---------------------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden host", "text/plain")
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path.startswith("/api/"):
            if not self._token_ok(query):
                return self._json(403, {"ok": False, "error": "bad token"})
            if url.path == "/api/state":
                self.ui.api.detect()
                return self._json(200, self.ui.api.state())
            if url.path == "/api/live":
                return self._json(200, self.ui.api.live())
            if url.path == "/api/events":
                return self._events()
            return self._json(404, {"ok": False, "error": "not found"})

        name = "index.html" if url.path in ("/", "/index.html") else url.path.lstrip("/")
        body = _static(name)
        if body is None:
            return self._send(404, b"not found", "text/plain")
        suffix = name[name.rfind("."):]
        extra = (("Content-Security-Policy", CSP), ("Referrer-Policy", "no-referrer"))
        self._send(200, body, CONTENT_TYPES.get(suffix, "application/octet-stream"), extra)

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, b"forbidden host", "text/plain")
        url = urlparse(self.path)
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{self.ui.port}",
                                     f"http://localhost:{self.ui.port}"):
            return self._json(403, {"ok": False, "error": "bad origin"})
        if url.path != "/api/action" or not self._token_ok({}):
            return self._json(403, {"ok": False, "error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("request body is missing or too large")
            request = json.loads(self.rfile.read(length))
            result = self.ui.api.run(request.get("action"), request.get("params") or {})
        except (TypeError, ValueError, KeyError) as exc:
            return self._json(200, {"ok": False, "error": str(exc)})
        except Exception as exc:  # an action failing must not take the server down
            logger.exception("UI action %r failed", self.path)
            return self._json(200, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        payload = result if isinstance(result, dict) and "ok" in result else {
            "ok": True, "result": result if isinstance(result, (dict, list, str, int, float,
                                                                  bool, type(None))) else None}
        # Not every action changes config (start at login lives in the registry), so always tell
        # clients to refresh rather than relying on a config event that may not come.
        self.ui.changed("state")
        return self._json(200, payload)

    def _events(self):
        client = self.ui.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = client.get(timeout=HEARTBEAT_S)
                except queue.Empty:
                    message = b": heartbeat\n\n"
                self.wfile.write(message)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            self.ui.hub.unsubscribe(client)
            self.close_connection = True
