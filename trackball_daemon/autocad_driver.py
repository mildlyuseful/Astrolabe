"""Daemon-side AutoCAD plugin loader -- COM staging/trust/NETLOAD only.

The compiled in-host plugin is the sole AutoCAD camera transport and connects to NavBroker.
This worker attaches to an already-running AutoCAD, never launches it, copies the bundled DLL and
manifest to the per-user runtime directory, extends ``TRUSTEDPATHS``, and issues ``NETLOAD`` once per
session. AutoCAD locks a loaded DLL, so an in-use runtime copy is a staged update for the next host
session. After loading, the worker only monitors liveness.

Without pywin32 the loader logs once and becomes a no-op; the plugin can still be loaded manually.
The retired COM navigation transport and its rationale live under ``archive/autocad_com_transport/``.
"""
import threading
from pathlib import Path

from .paths import user_config_dir
from .service_health import ServiceHealth, ServiceHealthState
from .util import get_logger

# pywin32 is Windows-only and optional. Import guarded so the daemon still runs (with this
# loader disabled) when it's missing. _PYWIN32 gates every COM code path below.
try:
    import pythoncom
    import win32com.client
    _PYWIN32 = True
except Exception:                       # pragma: no cover - exercised only without pywin32
    pythoncom = None
    win32com = None
    _PYWIN32 = False


_PLUGIN_DLL = "TrackballNavAcad.dll"
_POLL_PERIOD = 2.0                # seconds between attach attempts / liveness probes


def _bundled_plugin_path():
    return Path(__file__).resolve().parent / "plugins" / "autocad" / _PLUGIN_DLL


def _runtime_plugin_dir():
    """Where the plugin is COPIED before NETLOAD (see the module docstring for why the
    indirection exists). integrations.py imports this as the single source of truth."""
    return user_config_dir() / "acad_plugin"


def _trusted_path_present(current, candidate):
    """Exact, case-insensitive TRUSTEDPATHS entry check (never trust a parent by substring)."""
    wanted = str(Path(candidate)).rstrip("\\/").casefold()
    entries = [part.strip().strip('"').rstrip("\\/").casefold()
               for part in str(current or "").split(";") if part.strip()]
    return wanted in entries


class AutoCADPluginLoader:
    """Background NETLOAD delivery for the AutoCAD plugin. Public surface: start(), stop().
    No nav, no status entry in the connected-apps list -- "autocad" appears there only when
    the plugin itself handshakes with the nav broker, exactly like the other socket add-ons."""

    def __init__(self, on_health_changed=None):
        self.on_health_changed = on_health_changed
        self._stop = threading.Event()
        self._thread = None
        self._health_lock = threading.Lock()
        self._log = get_logger()
        self._warned = set()                                  # one-time logs for failing ops
        # COM handle -- created and used ONLY on the worker thread.
        self._acad = None
        self._netload_done = False                            # NETLOAD attempted this session
        self._enabled = threading.Event()                     # setup/Enabled gate; off by default
        self._health = ServiceHealth(
            "autocad", ServiceHealthState.DISABLED, "integration is disabled")
        self._publish_health(self._health)

    def health(self):
        with self._health_lock:
            return self._health

    def _set_health(self, state, detail):
        health = ServiceHealth("autocad", state, detail)
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

    # --- lifecycle -----------------------------------------------------------------------
    def start(self):
        if not _PYWIN32:
            self._log.info("autocad: pywin32 not available; plugin loader disabled")
            self._set_health(
                ServiceHealthState.FAILED,
                "pywin32 is unavailable; reinstall the Windows runtime",
            )
            return
        if self._thread is not None:
            return
        if self._enabled.is_set():
            self._set_health(
                ServiceHealthState.WAITING,
                "waiting for a running AutoCAD drawing",
            )
        self._thread = threading.Thread(target=self._run, name="autocad-loader", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._set_health(ServiceHealthState.DISABLED, "loader stopped")

    def set_enabled(self, enabled):
        """Allow COM attachment/NETLOAD only after the user enabled this integration."""
        if enabled:
            self._enabled.set()
            self._set_health(
                ServiceHealthState.WAITING,
                "waiting for a running AutoCAD drawing",
            )
        else:
            self._enabled.clear()
            self._set_health(ServiceHealthState.DISABLED, "integration is disabled")

    # --- worker thread (owns COM) ----------------------------------------------------------
    def _run(self):
        try:
            pythoncom.CoInitialize()
        except Exception as exc:
            self._set_health(
                ServiceHealthState.FAILED,
                f"could not initialize the AutoCAD COM worker: {exc}",
            )
            return
        try:
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception as exc:
                    # AutoCAD closed / COM handle dropped -> re-attach (and re-NETLOAD) later.
                    if self._acad is not None:
                        self._log.info("autocad: session ended (loader will re-attach)")
                    self._acad = None
                    if self._enabled.is_set():
                        self._set_health(
                            ServiceHealthState.DEGRADED,
                            f"AutoCAD loader lost its session ({exc}); waiting to reattach",
                        )
                self._stop.wait(_POLL_PERIOD)
        finally:
            self._acad = None
            pythoncom.CoUninitialize()

    def _tick(self):
        """One poll: attach if detached; probe liveness; NETLOAD once a document is open."""
        if not self._enabled.is_set():
            self._acad = None
            self._netload_done = False
            return
        if self._acad is None:
            acad = self._find_running_acad()
            if acad is None:
                return
            self._acad = acad
            self._netload_done = False           # fresh attach (new acad session) -> NETLOAD
            self._warned.clear()
            self._set_health(
                ServiceHealthState.WAITING,
                "attached to AutoCAD; waiting for an open drawing",
            )
            self._log.info("autocad: attached to running instance (plugin loader)")
        # Liveness probe AND the NETLOAD precondition in one call: SendCommand needs an open
        # document, and Documents.Count raises once the app is gone (-> _run drops the handle).
        if self._netload_done:
            _ = int(self._acad.Documents.Count)
            return
        if int(self._acad.Documents.Count) == 0:  # app alive but no drawing (Start tab) -> wait
            return
        self._netload_plugin(self._acad.ActiveDocument)

    @staticmethod
    def _find_running_acad():
        """Return a running AutoCAD, PREFERRING one with a document open. GetActiveObject can
        return 'Operation unavailable' even when AutoCAD is up (verified live), so enumerate the
        Running Object Table: normalize each dispatch to its .Application (both AcadApplication
        and AcadDocument expose it), keep the ones named 'AutoCAD', and pick the instance with
        the most open documents; fall back to GetActiveObject. Verticals (Civil 3D/Architecture/
        Mechanical) are all acad.exe exposing the same AutoCAD.Application, so they work free."""
        best, best_docs = None, -1
        try:
            rot = pythoncom.GetRunningObjectTable()
            for moniker in rot.EnumRunning():
                try:
                    disp = win32com.client.Dispatch(
                        rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
                    app = disp.Application              # AutoCAD-specific: normalizes doc/app -> app
                    if str(app.Name) != "AutoCAD":
                        continue
                    docs = int(app.Documents.Count)
                except Exception:
                    continue                           # not an AutoCAD app
                if docs > best_docs:
                    best, best_docs = app, docs
        except Exception:
            best = None
        if best is not None:
            return best
        try:
            return win32com.client.GetActiveObject("AutoCAD.Application")
        except Exception:
            return None

    def _netload_plugin(self, doc):
        """Copy the bundled plugin to the runtime dir and NETLOAD it (once per session). All
        best-effort -- a failure is logged once; the user can still APPLOAD/NETLOAD by hand."""
        self._netload_done = True
        try:
            from .integrations import _install_payloads_transactionally

            src = _bundled_plugin_path()
            if not src.exists():
                self._set_health(
                    ServiceHealthState.FAILED,
                    "bundled AutoCAD plugin is missing",
                )
                return
            dst_dir = _runtime_plugin_dir()
            dst = dst_dir / _PLUGIN_DLL
            manifest = src.parent / "version.json"
            payloads = [(src, dst)]
            if manifest.exists():
                payloads.append((manifest, dst_dir / "version.json"))
            update_error = None
            try:
                _install_payloads_transactionally(payloads)
            except OSError as exc:
                # A loaded DLL is expected to be locked. The transaction leaves both the DLL and
                # manifest at their previous version, and setup/next launch retries from bundle.
                self._warn_once("plugin update", exc)
                update_error = exc
            if not dst.exists():
                self._set_health(
                    ServiceHealthState.FAILED,
                    f"no runtime AutoCAD plugin is available at {dst}",
                )
                return
            try:                              # one-time trust so SECURELOAD loads silently
                cur = str(doc.GetVariable("TRUSTEDPATHS") or "")
                if not _trusted_path_present(cur, dst_dir):
                    doc.SetVariable("TRUSTEDPATHS", (cur + ";" if cur else "") + str(dst_dir))
            except Exception:
                pass
            doc.SendCommand('(command "_.NETLOAD" "%s")(princ) ' % str(dst).replace("\\", "/"))
            if update_error is not None:
                self._set_health(
                    ServiceHealthState.DEGRADED,
                    f"could not update the in-use AutoCAD plugin ({update_error}); "
                    "the prior copy was kept",
                )
            else:
                self._set_health(
                    ServiceHealthState.WAITING,
                    "NETLOAD requested; waiting for the AutoCAD plugin handshake",
                )
            self._log.info(f"autocad: NETLOADed smooth-orbit plugin ({dst})")
        except Exception as exc:
            self._set_health(
                ServiceHealthState.DEGRADED,
                f"AutoCAD NETLOAD failed ({exc}); use NETLOAD manually or retry setup",
            )
            self._warn_once("netload", exc)

    def _warn_once(self, what, exc):
        if what not in self._warned:
            self._warned.add(what)
            self._log.info(f"autocad: {what} failed ({exc!r})")
