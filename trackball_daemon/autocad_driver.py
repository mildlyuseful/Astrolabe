"""AutoCAD plugin LOADER (COM) -- delivery only, no navigation.

Through daemon 0.1.37 this module was a full COM nav *transport* (ActiveViewport-reassign
orbit + Zoom* pan/zoom + the rotating-cube overlay). That transport is ARCHIVED at
archive/autocad_com_transport/ -- see its README for why. The compiled NETLOAD plugin
(plugin_src/autocad, notes 8.15/8.16) is now the ONLY AutoCAD transport: it drives the
viewport's live GraphicsSystem view in-process (~1.6 ms/frame, zero regens) and connects to
the nav broker like every socket add-on, so app.py routes autocad frames to the broker
unconditionally.

Why the transport had to go (not just tidiness): the COM driver attached FIRST -- before the
plugin finished NETLOADing and its broker handshake -- and claimed the early frames, so the
first gesture of a session went through the regen-per-frame reassign path (flicker + overlay
cube), and a deferred orbit accumulated in those frames could land ~100 ms AFTER the plugin
took over, fighting the GS view it now owned. With unconditional broker routing nothing else
can ever drive the viewport.

What remains here is the ONE job COM is still the right tool for: getting the plugin INTO a
running AutoCAD with zero user friction. One daemon worker thread: CoInitialize, attach to a
RUNNING AutoCAD via the Running Object Table (never launches one; GetActiveObject can return
"Operation unavailable" even when AutoCAD is up -- verified live), and once a document is
open, copy the bundled DLL + version.json to the runtime dir, extend TRUSTEDPATHS (so
SECURELOAD never prompts) and NETLOAD -- once per AutoCAD session. The copy-then-load
indirection matters: AutoCAD locks the loaded file for the whole session, so loading the
bundled copy directly would break daemon updates (verified the hard way). A locked runtime
copy just means this session already has the plugin -- the fresh DLL lands on AutoCAD's next
start (the "STAGED" update flow in integrations.install_autocad).

After the NETLOAD the worker only watches liveness (Documents.Count raises when the app is
gone) so a NEW AutoCAD session gets its own NETLOAD. NETLOADing an already-loaded assembly
is a no-op inside AutoCAD, so a daemon restart against a running session is harmless.

Degrades gracefully: no pywin32 (e.g. a non-Windows dev box) -> start() logs once and the
loader is a no-op; the plugin can still be NETLOADed by hand (APPLOAD / NETLOAD).
"""
import shutil
import threading
from pathlib import Path

from .paths import user_config_dir
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


class AutoCADPluginLoader:
    """Background NETLOAD delivery for the AutoCAD plugin. Public surface: start(), stop().
    No nav, no status entry in the connected-apps list -- "autocad" appears there only when
    the plugin itself handshakes with the nav broker, exactly like the other socket add-ons."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._log = get_logger()
        self._warned = set()                                  # one-time logs for failing ops
        # COM handle -- created and used ONLY on the worker thread.
        self._acad = None
        self._netload_done = False                            # NETLOAD attempted this session

    # --- lifecycle -----------------------------------------------------------------------
    def start(self):
        if not _PYWIN32:
            self._log.info("autocad: pywin32 not available; plugin loader disabled")
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="autocad-loader", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # --- worker thread (owns COM) ----------------------------------------------------------
    def _run(self):
        pythoncom.CoInitialize()
        try:
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception:
                    # AutoCAD closed / COM handle dropped -> re-attach (and re-NETLOAD) later.
                    if self._acad is not None:
                        self._log.info("autocad: session ended (loader will re-attach)")
                    self._acad = None
                self._stop.wait(_POLL_PERIOD)
        finally:
            self._acad = None
            pythoncom.CoUninitialize()

    def _tick(self):
        """One poll: attach if detached; probe liveness; NETLOAD once a document is open."""
        if self._acad is None:
            acad = self._find_running_acad()
            if acad is None:
                return
            self._acad = acad
            self._netload_done = False           # fresh attach (new acad session) -> NETLOAD
            self._warned.clear()
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
            src = _bundled_plugin_path()
            if not src.exists():
                return
            dst_dir = _runtime_plugin_dir()
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / _PLUGIN_DLL
            try:
                shutil.copy2(src, dst)       # locked == already loaded this session -> fine
            except OSError:
                pass
            else:                            # DLL fresh -> keep the version manifest in step
                ver = src.parent / "version.json"
                if ver.exists():
                    try:
                        shutil.copy2(ver, dst_dir / "version.json")
                    except OSError:
                        pass
            if not dst.exists():
                return
            try:                              # one-time trust so SECURELOAD loads silently
                cur = str(doc.GetVariable("TRUSTEDPATHS") or "")
                if str(dst_dir).lower() not in cur.lower():
                    doc.SetVariable("TRUSTEDPATHS", (cur + ";" if cur else "") + str(dst_dir))
            except Exception:
                pass
            doc.SendCommand('(command "_.NETLOAD" "%s")(princ) ' % str(dst).replace("\\", "/"))
            self._log.info(f"autocad: NETLOADed smooth-orbit plugin ({dst})")
        except Exception as exc:
            self._warn_once("netload", exc)

    def _warn_once(self, what, exc):
        if what not in self._warned:
            self._warned.add(what)
            self._log.info(f"autocad: {what} failed ({exc!r})")
