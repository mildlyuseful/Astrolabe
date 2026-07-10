"""In-process SolidWorks COM driver -- the SolidWorks analogue of NavBroker.

Unlike the Fusion path (a socket add-in that connects to the nav broker), SolidWorks is
driven by *external COM automation*: SolidWorks add-ins are COM/.NET and need admin
registration, so we avoid that entirely. Instead this driver -- which lives inside the
daemon process, parallel to the broker -- attaches to a *running* SolidWorks instance via
its COM API (pywin32) and moves the active view's camera directly. There is no file to
install and the socket broker is not involved.

Threading model mirrors NavBroker exactly:
  * submit() is called on the BLE thread and ONLY accumulates the per-frame orbit/pan/zoom
    delta -- it never blocks and never touches COM (COM must be used on the thread that
    initialized it).
  * a single worker thread calls pythoncom.CoInitialize(), lazily attaches to
    SldWorks.Application via GetActiveObject (ATTACH only -- it never launches SolidWorks),
    retries periodically when SolidWorks isn't running, and at bridge.rate_hz flushes the
    accumulated delta to the camera, then zeroes it (so a slow viewport coalesces motion
    instead of losing it -- same idea as the broker / the firmware's float carry).
  * on_connection_changed(connected, version) fires on connect/disconnect (the analogue of
    the broker's on_clients_changed) so the tray/UI status line can update.

Degrades gracefully: if pywin32 is missing (e.g. non-Windows dev box) the driver disables
itself -- start() logs once and returns, submit()/flush become harmless no-ops, and the
daemon keeps running with the SolidWorks integration simply unavailable.

Camera math uses SolidWorks' NATIVE view methods (the same ones its own view tools use), so
each frame is a handful of COM calls -- not the slow/fragile manual-transform recipe:
  * IModelView.RotateAboutAxis(Angle, Ptx, Pty, Ptz, AxisVecX, AxisVecY, AxisVecZ)
        -- incremental view rotation, angle in radians, about a MODEL-space axis. NOTE (verified
        live): SolidWorks IGNORES the point argument and leaves Translation3/Scale2 untouched -- it
        only rotates the orientation, pivoting about the model ORIGIN (pinned at screen Translation3).
        To orbit about a chosen pivot we rotate, then PAN so that pivot keeps its screen position.
  * IModelView.Orientation3.ArrayData (IMathTransform, view->model) -- its 3x3 is row-major; the
        COLUMNS are the camera axes in model space (col0/col1/col2 = camera right/up/forward --
        verified live by a roll test). One property read yields all three, and a model point P's
        screen position is Scale2*(col.P)[xy] + Translation3 (the basis for the pivot pan + zoom).
  * IModelView.Translation3 (IMathVector, meters in the screen X,Y plane) -- pan by reading it,
        adding the delta, and SETTING it. (TranslateBy type-mismatches the vector under dynamic
        dispatch; the Translation3 setter accepts it. Verified against live SolidWorks.)
  * IModelView.Scale2 (double) -- the view scale; ZoomByFactor(f) multiplies it by f (f>1 = zoom in).
  * IModelView.ZoomByFactor(double) -- zoom about the view CENTER (no off-screen drift).
  * IModelDoc2.GraphicsRedraw2() -- force a viewport repaint after an automation change.
  * IPartDoc.GetPartBox / IAssemblyDoc.GetBox -- model bounding box (orbit pivot + zoom-to-object);
        both need _FlagAsMethod under late-bound dispatch.
  * IModelDocExtension.SelectByRay + ISelectionMgr.GetSelectionPoint2 -- screen-centre raycast for the
        'view' pivot (true surface depth under the crosshair). GOTCHAS (verified live): SelectByRay's
        Tol arg must be a VT_I4 INTEGER (a double silently selects nothing); the count method is
        GetSelectedObjectCount2 (not 'GetSelectionCount'); it mutates the selection set (save/restore).

Performance: out-of-process COM property reads are expensive (~17-20 ms each), so the worker CACHES
the model/view handles and TRACKS Translation3/Scale2 across its own sets (re-validating every
_VIEW_TTL). That, plus predicting the post-rotation axes analytically (Rodrigues, no Orientation3
re-read), is what lets the orbit pan to hold an arbitrary pivot every frame and STILL stay smooth.

Control scheme (mirrors the broker / Fusion add-in via set_scheme) -- all applied for SolidWorks:
  * orbit PIVOT:
      origin             -> rotate ONLY about the world origin, ZERO view translation (the original feel).
      object / selection -> hold the model bounding-box CENTRE (a fixed point -> exact).
      view            -> hold the SCREEN-CENTRE point at the TRUE surface depth under the crosshair,
                         found by a screen-centre raycast (SelectByRay; like SW's own middle-drag orbit),
                         falling back to the object-centre depth when the ray misses. Captured once and
                         HELD; recomputed only after the view is idle >= _pivot_hold_sec (set_pivot_hold,
                         default 0.5 s) OR when a pan/zoom moves it -- so it never chases a moving target.
      cursor          -> hold the surface point under the MOUSE CURSOR: GetCursorPos ->
                         ScreenToClient(GetViewHWnd) -> invert IModelView.Transform (the model->
                         CLIENT-relative PHYSICAL-pixel transform; the worker thread is made DPI
                         aware so the cursor px match) -> the in-plane (a, b) offsets -> the SAME
                         SelectByRay raycast as 'view', aimed through the cursor. Same
                         capture+hold; misses fall back to the object centre. (The IMouse
                         event-sink route was rejected: see the design note at _cursor_pivot.)
    Holding the pivot pans by dT = Scale2*((col_before - col_after).pivot), which exactly compensates
    the rotation (held point stays put to ~1e-16 -- no discrete-order error). Switching the pivot
    takes effect immediately (set_scheme drops any held pivot).
  * orbit STYLE: free | turntable.
  * zoom MODE: to_center | to_object (centre held) | to_cursor (the surface point under the mouse
    cursor, held per gesture; misses fall back to to_center).

pywin32 gotcha: GetActiveObject returns a late-bound (dynamic) dispatch that mis-resolves a few
SolidWorks members -- GetMathUtility()/CreateVector raise unless flagged with _FlagAsMethod, and
CreateVector needs its array as a VT_ARRAY|VT_R8 VARIANT. _get_mathutil()/_mkvec() handle that.

Every camera op is wrapped so a single failing COM call is logged once and skipped -- it can
never blank the viewport or trip a disconnect (only ActiveDoc/ActiveView access does that).
"""
import math
import threading
import time

from .util import get_logger

# pywin32 is Windows-only and optional. Import guarded so the daemon still runs (with this
# integration disabled) when it's missing. _PYWIN32 gates every COM code path below.
try:
    import pythoncom
    import win32com.client
    _PYWIN32 = True
except Exception:                       # pragma: no cover - exercised only without pywin32
    pythoncom = None
    win32com = None
    _PYWIN32 = False


# --- tuning: SolidWorks' intrinsic axis orientation + baseline sensitivity ----------------
# Mirrors the tuning block at the top of the Fusion add-in. The nav-delta contract feeds
# (ox,oy,oz) = orbit about (camera right, up, forward) in radians, (px,py) = pan, zoom = zoom;
# values arrive already scaled by the active app's bindings. Defaults below were refined from
# on-hardware testing but signs/magnitudes may still want per-feel tweaks.
#
# orbit: rotation is about the CAMERA axes (right/up/forward), transformed into model space per
# frame via the view's Orientation3 -- so it tracks the current view instead of the global axes.
# ORBIT_SIGN flips each channel's direction. Defaults mirror the Fusion add-in's ORBIT_SCALE
# (X/Y inverted) as the best-guess starting point; flip any axis that spins the wrong way.
ORBIT_SIGN = (-1.0, -1.0, 1.0)    # (ox=pitch about right, oy=yaw about up, oz=roll about forward)
# turntable azimuth axis: SolidWorks is Y-up, so world up is +Y in model space (verified live --
# at every view the camera-up column is Y-dominant, and yawing about (0,1,0) keeps verticals vertical).
WORLD_UP = (0.0, 1.0, 0.0)
# pan: IModelView.TranslateBy moves the view by a vector in METERS along the graphics-area
# screen X,Y axes -- already screen-relative (do NOT divide by Scale2). PAN_SCALE is the main
# magnitude knob (raise if pan is too slow, lower if it flies off); PAN_SIGN flips each axis.
PAN_SIGN = (1.0, -1.0)
PAN_SCALE = 0.2
# zoom: IModelView.ZoomByFactor zooms about the view CENTER, so the model no longer drifts
# off-screen the way the old Scale2 approach did. factor > 1 zooms in; ZOOM_SIGN flips that and
# ZOOM_SCALE sets how aggressive each frame is.
ZOOM_SCALE = 0.5
ZOOM_SIGN = 1.0
# Force a viewport redraw each frame. The native view methods may already repaint when driven
# from automation; if motion stays visible with this False, leaving it False lifts the refresh
# rate (one fewer round-trip + no redundant full redraw per frame).
FORCE_REDRAW = True

DEFAULT_FLUSH_HZ = 30.0
_RETRY_PERIOD = 2.0           # seconds between attach attempts while SolidWorks isn't running
_OBJ_CACHE_TTL = 0.5         # seconds to cache the model bounding-box centre (recomputed lazily)
_SELECTION_CACHE_TTL = 0.15  # avoid a selection-manager COM round-trip on every orbit frame
# Out-of-process COM reads are expensive (~17-20 ms each for ActiveDoc/ActiveView/Translation3,
# measured live), so the worker caches the model/view handles + the view's Translation3/Scale2 and
# re-validates them only every _VIEW_TTL. That re-fetch doubles as the liveness probe (a closed app
# makes ActiveDoc raise) and resyncs the tracked Translation3/Scale2 against any external (mouse)
# view change. Caching these is what makes an object-centred orbit (which must also pan every frame
# to hold the pivot) as smooth as the old origin-only orbit.
_VIEW_TTL = 1.0
# "view" orbit pivot: seconds the view must be idle (no orbit/pan/zoom) before the screen-centre
# pivot is recomputed. Holds it steady through a gesture and re-settles to the current centre after
# a pause -- recomputing it every frame chases a moving target and drifts. Overridable per app.
DEFAULT_PIVOT_HOLD = 0.5

# "view" pivot raycast: instead of pinning the screen-centre point at the OBJECT-CENTRE depth (which
# makes the model swing when that depth != the surface you're looking at), shoot a ray down the
# screen-centre optical axis with IModelDocExtension.SelectByRay and pin the pivot at the TRUE
# surface depth under the crosshair -- exactly what SolidWorks' own middle-drag orbit does. Runs
# ONCE per gesture (then the pivot is held), so its few extra COM calls don't affect steady-state.
VIEW_PIVOT_RAYCAST = True
# Aperture (cylinder radius) sweep as a fraction of the bbox diagonal: start precise (the pierced
# centre surface) and grow x3 to catch thin/edge features when the exact centre is in a gap. The
# FIRST radius that yields a valid hit wins (smallest = most accurate). We use the bbox diagonal as
# the length scale because the viewport's on-screen extent isn't cheaply available over COM.
_RAY_APERTURE_FRACS = (0.005, 0.015, 0.045, 0.135)
_RAY_PUSH = 4.0              # ray origin pushed back toward the viewer by N x diagonal (starts outside)
_RAY_BBOX_MARGIN = 0.10     # accept a hit only if it lies within the bbox expanded by N x diagonal

# "cursor" orbit pivot / "to_cursor" zoom: the cursor pixel -> model mapping inverts
# IModelView.Transform (the model -> CLIENT-RELATIVE PHYSICAL-pixel transform; see the design
# note above _cursor_client_point). The transform's in-plane rows must align with the camera
# right/up axes for the inversion to be trustworthy -- this is the tolerance on |row . axis|
# (1.0 = perfectly aligned).
_CURSOR_XF_ALIGN_TOL = 0.05


if _PYWIN32:
    VT_R8, VT_I4, VT_BOOL = pythoncom.VT_R8, pythoncom.VT_I4, pythoncom.VT_BOOL
else:                                       # pragma: no cover - non-Windows; SelectByRay never runs
    VT_R8 = VT_I4 = VT_BOOL = 0


def _variant(vt, value):
    """Wrap a SelectByRay argument in an explicitly-typed VARIANT. Late-bound dispatch mis-marshals
    SelectByRay's args from plain Python numbers -- in particular the Tol parameter must be a VT_I4
    INTEGER or the call silently selects nothing (verified live; same class of gotcha as TranslateBy).
    Falls back to the raw value when pywin32 is absent (non-Windows dev box; the call never runs there)."""
    if not _PYWIN32:
        return value
    return win32com.client.VARIANT(vt, value)


# --- small quaternion helpers for the turntable orbit (compose yaw+pitch into ONE rotation) ----
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
    if s < 1e-9:                                  # ~no rotation -> any axis, zero angle
        return (0.0, 0.0, 1.0, 0.0)
    return (q[1] / s, q[2] / s, q[3] / s, angle)


def _rodrigues(u, theta, p):
    """Rotate vector p about UNIT axis u by theta (radians) -- Rodrigues' formula. Used to predict
    the post-rotation camera axes in Python instead of re-reading Orientation3 from SolidWorks (a
    ~20 ms COM read), so the orbit pivot pan costs one fewer round-trip per frame."""
    ux, uy, uz = u
    px, py, pz = p
    cx = uy * pz - uz * py            # u x p
    cy = uz * px - ux * pz
    cz = ux * py - uy * px
    d = ux * px + uy * py + uz * pz   # u . p
    c = math.cos(theta)
    s = math.sin(theta)
    k = d * (1.0 - c)
    return (px * c + cx * s + ux * k,
            py * c + cy * s + uy * k,
            pz * c + cz * s + uz * k)


def _safe_box(model, method, arg):
    """model bounding box via `method`(arg), or None. Both GetPartBox (parts) and GetBox
    (assemblies) need _FlagAsMethod under late-bound dispatch. Never raises."""
    try:
        model._FlagAsMethod(method)
        box = tuple(getattr(model, method)(arg))
        return box if len(box) >= 6 else None
    except Exception:
        return None


class SolidWorksDriver:
    """Accumulates nav deltas (BLE thread) and applies them to a live SolidWorks view from a
    CoInitialized worker thread at a fixed rate. Public surface parallels NavBroker:
    submit(), set_rate(), start(), stop(), plus is_connected()/version() for status."""

    def __init__(self, on_connection_changed=None, rate_hz=DEFAULT_FLUSH_HZ):
        self.on_connection_changed = on_connection_changed   # callback(connected: bool, version: str)
        self._lock = threading.Lock()
        self._acc = [0.0] * 6
        self._stop = threading.Event()
        self._period = 1.0 / self._clamp_rate(rate_hz)       # flush/refresh interval
        self._thread = None
        self._connected = False
        self._version = ""
        self._log = get_logger()
        self._warned = set()                                  # one-time logs for failing ops
        # Control scheme (orbit pivot / orbit style / zoom mode), set live via set_scheme. Mirrors
        # NavBroker; a dict ref-swap is atomic, so the worker reads it lock-free each flush.
        self._scheme = {"op": "view", "os": "free", "zm": "to_center", "sel_override": True}
        # COM handles -- created and used ONLY on the worker thread.
        self._swApp = None
        self._mathUtil = None
        self._box_cache = (0.0, None)                         # (monotonic_ts, bbox 6-tuple or None)
        self._selection_cache = (None, 0.0, None)             # (selmgr, monotonic_ts, centre)
        # Cached view state (worker thread only) -- see _VIEW_TTL. _model/_view are re-validated
        # periodically; _trans/_scale are tracked across our own sets so we avoid re-reading them.
        self._model = None
        self._view = None
        self._trans = None                                    # tracked Translation3 [x,y,z] or None
        self._scale = None                                    # tracked Scale2 or None
        self._view_ts = 0.0                                   # last re-validate time (monotonic)
        # IModelDocExtension / ISelectionMgr handles for the 'view' pivot raycast (SelectByRay /
        # GetSelectionPoint2). Cached + method-flagged alongside the view; None when unavailable.
        self._ext = None
        self._selmgr = None
        # the graphics window's HWND (view.GetViewHWnd), tracked with the view handles -- the
        # 'cursor' pivot's is-the-cursor-over-this-view gate (WindowFromPoint must return it).
        self._view_hwnd = None
        # "view"/"cursor" orbit pivot held across a gesture: captured when orbit resumes after the
        # view has been idle for >= _pivot_hold_sec, then held (so it doesn't chase a moving
        # target). origin orbit takes NO pivot (pure rotation, zero translation).
        self._orbit_pivot = None
        # "to_cursor" zoom's own held pivot (reset on orbit/pan and by set_scheme).
        self._zoom_pivot = None
        self._pivot_hold_sec = DEFAULT_PIVOT_HOLD
        self._last_activity_t = 0.0                           # monotonic time of the last orbit/pan/zoom
        self._rl = {}                                         # rate-limited info-log timestamps

    @staticmethod
    def _clamp_rate(hz):
        try:
            hz = float(hz)
        except (TypeError, ValueError):
            hz = DEFAULT_FLUSH_HZ
        return min(240.0, max(1.0, hz))

    def set_rate(self, hz):
        """Live-update the flush/viewport-refresh rate (Hz). Applied on the next flush."""
        self._period = 1.0 / self._clamp_rate(hz)

    def set_scheme(self, orbit_pivot, orbit_style, zoom_mode, selection_overrides_pivot=True):
        """Set the control scheme applied on the next flush. Parallels NavBroker.set_scheme so
        app._apply_schemes() drives SolidWorks the same way it drives the socket add-ons.
          orbit_pivot: origin | object | view | selection | cursor
              origin    -> rotate about the model origin, no view translation (the original behaviour);
              object    -> rotate about the model bounding-box centre;
              view      -> rotate about the screen-centre point at the true surface depth (raycast; held);
              selection -> mean of selected-entity points, falling back to object;
              cursor    -> rotate about the surface point under the MOUSE CURSOR (the same
                           SelectByRay machinery aimed through the cursor pixel -- _cursor_pivot;
                           held per gesture); misses / unmappable cursor fall back to object.
          orbit_style: free | turntable
          zoom_mode:   to_center | to_object | to_cursor   (to_cursor = zoom about the surface
                       point under the mouse cursor, held per gesture; a miss falls back to
                       to_center)"""
        self._scheme = {"op": orbit_pivot, "os": orbit_style, "zm": zoom_mode,
                        "sel_override": bool(selection_overrides_pivot)}
        self._selection_cache = (None, 0.0, None)
        self._orbit_pivot = None             # drop any held pivot so a pivot switch takes effect now
        self._zoom_pivot = None

    def set_pivot_hold(self, sec):
        """Seconds the view must be idle before the 'view' orbit pivot is recomputed (see
        DEFAULT_PIVOT_HOLD). Clamped to [0, 10]; 0 recomputes every orbit start."""
        try:
            sec = float(sec)
        except (TypeError, ValueError):
            sec = DEFAULT_PIVOT_HOLD
        self._pivot_hold_sec = min(10.0, max(0.0, sec))

    # --- producer side (BLE thread) -- only accumulates, never blocks / touches COM ------
    def submit(self, ox, oy, oz, px, py, zoom):
        with self._lock:
            a = self._acc
            a[0] += ox; a[1] += oy; a[2] += oz
            a[3] += px; a[4] += py; a[5] += zoom

    # --- status (read from any thread) ---------------------------------------------------
    def is_connected(self):
        return self._connected

    def version(self):
        return self._version

    # --- lifecycle -----------------------------------------------------------------------
    def start(self):
        if not _PYWIN32:
            self._log.info("solidworks: pywin32 not available; COM driver disabled")
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="solidworks-driver", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # --- worker thread (owns COM) --------------------------------------------------------
    def _run(self):
        pythoncom.CoInitialize()
        self._make_thread_dpi_aware()               # so GetCursorPos/ScreenToClient return PHYSICAL
        last_attach = 0.0
        try:
            while not self._stop.is_set():
                cycle_start = time.monotonic()
                period = self._period               # re-read each loop so set_rate() applies live
                if self._swApp is None:
                    if cycle_start - last_attach >= _RETRY_PERIOD:
                        last_attach = cycle_start
                        self._attach()
                    if self._swApp is None:
                        self._drain()               # drop motion while detached
                        self._sleep_remainder(cycle_start, period)
                        continue
                with self._lock:
                    a = self._acc
                    has = any(a)
                    if has:
                        delta = tuple(a)
                        self._acc = [0.0] * 6
                if has:
                    try:
                        self._flush(delta)
                    except Exception:
                        # SolidWorks closed / COM handle dropped -> disconnect, re-attach later.
                        self._handle_drop()
                self._sleep_remainder(cycle_start, period)
        finally:
            self._swApp = None
            self._mathUtil = None
            pythoncom.CoUninitialize()

    def _sleep_remainder(self, cycle_start, period):
        """Sleep only the time left in this period AFTER the work, so the flush cadence stays
        even regardless of how long each (variable-cost) COM flush took -- this is what keeps
        motion smooth instead of stuttering at `period + flush_time` intervals. If we're already
        over budget (COM can't keep up), fall through with a tiny yield."""
        remaining = period - (time.monotonic() - cycle_start)
        self._stop.wait(remaining if remaining > 0.0 else 0.001)

    def _drain(self):
        with self._lock:
            self._acc = [0.0] * 6

    def _attach(self):
        """Attach to a running SolidWorks (never launch one). Returns True on success."""
        swApp = self._find_running_sw()
        if swApp is None:
            return False                            # SolidWorks not running yet
        self._swApp = swApp
        self._mathUtil = self._get_mathutil(swApp)  # pan disabled (only) if this fails
        self._box_cache = (0.0, None)               # fresh attach -> recompute the bounding box
        self._selection_cache = (None, 0.0, None)
        self._invalidate_view()                     # fresh attach -> re-fetch view + tracked state
        self._warned.clear()                        # let the first failure of each op log again
        try:
            self._version = str(swApp.RevisionNumber)
        except Exception:
            self._version = "COM"
        self._set_connected(True)
        self._log.info(f"solidworks: attached to running instance (v{self._version})")
        return True

    @staticmethod
    def _find_running_sw():
        """Return a running SolidWorks, PREFERRING one with a document open. GetActiveObject grabs
        whichever instance registered first in the Running Object Table, which can be a stray empty
        instance (then ActiveDoc is always None and nothing drives). So enumerate the ROT, identify
        SolidWorks instances by their GetDocumentCount method, and pick the one with the most docs;
        fall back to GetActiveObject."""
        best, best_docs = None, -1
        try:
            rot = pythoncom.GetRunningObjectTable()
            for moniker in rot.EnumRunning():
                try:
                    disp = win32com.client.Dispatch(
                        rot.GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch))
                    disp._FlagAsMethod("GetDocumentCount")
                    docs = disp.GetDocumentCount()      # SolidWorks-specific -> filters the ROT
                except Exception:
                    continue                            # not a SolidWorks app
                if docs > best_docs:
                    best, best_docs = disp, docs
        except Exception:
            best = None
        if best is not None:
            return best
        try:
            return win32com.client.GetActiveObject("SldWorks.Application")
        except Exception:
            return None

    @staticmethod
    def _get_mathutil(swApp):
        """Obtain IMathUtility. pywin32's late-bound (dynamic) dispatch mis-resolves a few
        SolidWorks members -- GetMathUtility() and CreateVector raise unless flagged as methods,
        so force a DISPATCH_METHOD invoke via _FlagAsMethod (verified against live SolidWorks)."""
        try:
            swApp._FlagAsMethod("GetMathUtility")
            mu = swApp.GetMathUtility()
            if mu is not None:
                mu._FlagAsMethod("CreateVector")
            return mu
        except Exception:
            return None

    def _mkvec(self, x, y, z):
        """Build an IMathVector. CreateVector needs a SAFEARRAY of doubles; a plain tuple trips a
        COM type error, so wrap it in a VT_ARRAY|VT_R8 VARIANT."""
        arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                      [float(x), float(y), float(z)])
        return self._mathUtil.CreateVector(arr)

    def _handle_drop(self):
        if self._connected:
            self._log.info("solidworks: lost COM connection (app or document closed)")
        self._swApp = None
        self._mathUtil = None
        self._box_cache = (0.0, None)
        self._selection_cache = (None, 0.0, None)
        self._invalidate_view()
        self._set_connected(False)

    def _invalidate_view(self):
        """Drop the cached view handles + tracked state so the next flush re-fetches them."""
        self._model = None
        self._view = None
        self._trans = None
        self._scale = None
        self._view_ts = 0.0
        self._ext = None
        self._selmgr = None
        self._view_hwnd = None
        self._orbit_pivot = None
        self._zoom_pivot = None
        self._last_activity_t = 0.0

    def _set_connected(self, connected):
        if connected == self._connected:
            return
        self._connected = connected
        if self.on_connection_changed:
            try:
                self.on_connection_changed(connected, self._version if connected else "")
            except Exception:
                pass

    # --- camera math (worker thread only; see module docstring for the verified API) ------
    def _flush(self, delta):
        ox, oy, oz, px, py, zoom = delta
        swApp = self._swApp
        if swApp is None:
            return
        if self._mathUtil is None:                  # pan/recenter need a MathUtility for the vector
            self._mathUtil = self._get_mathutil(swApp)
        # Cached model/view -- re-validated every _VIEW_TTL, which also probes liveness (a closed app
        # makes ActiveDoc raise -> _run treats it as a drop). The per-op calls below are each guarded
        # so one failing COM call can NEVER blank the viewport (the redraw still runs).
        model, view = self._live_view(swApp)
        if model is None or view is None:           # SolidWorks open but no part/assembly -> idle
            return
        scheme = self._scheme                       # atomic ref read: {op, os, zm}
        now = time.monotonic()
        idle = now - self._last_activity_t          # how long the view was still BEFORE this flush

        # Freeze viewport repaints for the whole frame so a multi-step camera change (rotate + recenter
        # pan) shows as ONE redraw instead of flickering through the intermediate origin-rotated state.
        # This both kills the jitter AND cuts ~1 repaint/frame (measured ~185->116 ms on a heavy part).
        frozen = self._set_graphics_update(view, False)
        try:
            if ox or oy or oz:
                try:
                    self._apply_orbit(view, ox, oy, oz, scheme, model, idle)
                except Exception as exc:
                    self._warn_once("orbit", exc)
            if px or py:
                try:
                    self._apply_pan(view, px, py)
                except Exception as exc:
                    self._warn_once("pan", exc)
            if zoom:
                try:
                    self._apply_zoom(view, zoom, scheme, model, idle)
                except Exception as exc:
                    self._warn_once("zoom", exc)
            if px or py or zoom:
                self._orbit_pivot = None            # pan/zoom move the screen centre -> the 'view'/
                #                                     'cursor' pivot must recompute on the next orbit
            if ox or oy or oz or px or py:
                self._zoom_pivot = None             # view rotated/moved under the cursor -> the
                #                                     'to_cursor' zoom pivot re-raycasts next zoom
        finally:
            if frozen:
                self._set_graphics_update(view, True)   # resume before the single redraw below
        if FORCE_REDRAW:
            try:
                model.GraphicsRedraw2()             # SW won't repaint an automation-driven idle view
            except Exception as exc:
                self._warn_once("redraw", exc)
        self._last_activity_t = now                 # any motion this flush resets the still-timer

    def _live_view(self, swApp):
        """Return the cached (model, view), re-fetching at most every _VIEW_TTL. The re-fetch is also
        the liveness probe (ActiveDoc raises if the app/doc is gone -> _run drop) and resyncs the
        tracked Translation3/Scale2 against any external (mouse) view change. Between re-validations
        the handles are reused, since each ActiveDoc/ActiveView/Translation3 read is ~17-20 ms over
        out-of-process COM (measured)."""
        now = time.monotonic()
        if self._view is not None and now - self._view_ts < _VIEW_TTL:
            return self._model, self._view
        model = swApp.ActiveDoc                      # raises if the app/doc is gone -> _run drop
        if model is None:
            self._invalidate_view()
            return None, None
        view = model.ActiveView
        if view is None:
            self._invalidate_view()
            return None, None
        self._model, self._view = model, view
        try:                                         # resync tracked view state
            self._trans = list(view.Translation3.ArrayData)
            self._scale = float(view.Scale2)
        except Exception:
            self._trans, self._scale = None, None
        try:                                         # the graphics window (the 'cursor' pivot's
            self._view_hwnd = int(view.GetViewHWnd)  # over-this-view gate); None if unavailable
        except Exception:
            self._view_hwnd = None
        self._ext, self._selmgr = self._get_pick_handles(model)   # for the 'view' pivot raycast
        self._view_ts = now
        return model, view

    @staticmethod
    def _get_pick_handles(model):
        """Fetch + method-flag IModelDocExtension and ISelectionMgr for the 'view' pivot raycast
        (SelectByRay / GetSelectionPoint2). Both need _FlagAsMethod under late-bound dispatch; note
        the count method is GetSelectedObjectCount2 -- 'GetSelectionCount' is NOT a resolvable name
        on this dispatch (verified live). Returns (ext, selmgr), or (None, None) if unavailable
        (then the raycast is skipped and the 'view' pivot falls back to the object-centre depth)."""
        try:
            ext = model.Extension
            selmgr = model.SelectionManager
            ext._FlagAsMethod("SelectByRay")
            for m in ("GetSelectionPoint2", "GetSelectedObjectCount2", "GetSelectedObject6"):
                selmgr._FlagAsMethod(m)
            return ext, selmgr
        except Exception:
            return None, None

    def _apply_orbit(self, view, ox, oy, oz, scheme, model, idle):
        """Orbit by ONE RotateAboutAxis. SolidWorks ignores its point arg and pivots about the model
        ORIGIN, leaving Translation3/Scale2 untouched (verified live), so to rotate about anything
        else we rotate and then PAN to hold that point on screen.

        Pivot modes:
          origin          -> rotate ONLY (no pan): the original behaviour -- the model spins about the
                             world origin with ZERO view translation.
          object / selection -> hold the model bounding-box CENTRE (a fixed point, so it's exact
                             every frame; no per-entity selection pivot over COM yet).
          view            -> hold the SCREEN-CENTRE point. Captured once and HELD through the gesture
                             (recomputed only after the view is idle >= _pivot_hold_sec, or when a pan/
                             zoom invalidates it -- see _flush), so it never chases a moving target.
          cursor          -> hold the surface point under the MOUSE CURSOR (_cursor_pivot: the same
                             SelectByRay raycast aimed through the cursor pixel), same capture+hold
                             as 'view'; a miss / unmappable cursor falls back to the object centre
                             for the rest of the gesture.

        The pan that holds the pivot is dT = Scale2*((col_before - col_after).pivot) with the
        post-rotation columns predicted analytically (Rodrigues) -- this exactly compensates the
        rotation (the held point stays put to ~1e-16, verified), so there is no discrete-order error.

        Style: free rotates about the composed camera-space axis (Orientation3 COLUMNS are the camera
        axes -- model axis = vx*col0 + vy*col1 + vz*col2); turntable yaws about WORLD up + pitches
        about camera-right (roll dropped), composed into one rotation via quaternion."""
        vx = ORBIT_SIGN[0] * ox
        vy = ORBIT_SIGN[1] * oy
        vz = ORBIT_SIGN[2] * oz
        ad = view.Orientation3.ArrayData
        c0 = (ad[0], ad[3], ad[6])                  # camera right (model space)
        c1 = (ad[1], ad[4], ad[7])                  # camera up
        c2 = (ad[2], ad[5], ad[8])                  # camera forward (out of screen)
        if scheme["os"] == "turntable":
            q = _q_mul(_q_from_axis_angle(c0[0], c0[1], c0[2], vx),
                       _q_from_axis_angle(WORLD_UP[0], WORLD_UP[1], WORLD_UP[2], vy))
            ax, ay, az, angle = _q_to_axis_angle(q)
        else:                                       # free
            angle = math.sqrt(vx * vx + vy * vy + vz * vz)
            if angle < 1e-12:
                return
            ax = vx * ad[0] + vy * ad[1] + vz * ad[2]
            ay = vx * ad[3] + vy * ad[4] + vz * ad[5]
            az = vx * ad[6] + vy * ad[7] + vz * ad[8]
            n = math.sqrt(ax * ax + ay * ay + az * az)
            if n < 1e-12:
                return
            ax, ay, az = ax / n, ay / n, az / n
        if angle < 1e-12:
            return

        op = scheme["op"]
        selected = self._selection_center(self._selmgr) if scheme.get("sel_override", True) else None
        if selected is not None:
            pivot = selected
        elif op == "origin":
            pivot = None                            # rotate only, no pan
        elif op == "view":
            if self._orbit_pivot is None or idle >= self._pivot_hold_sec:
                self._orbit_pivot = self._view_pivot(c0, c1, c2, model)   # capture + hold
            pivot = self._orbit_pivot
        elif op == "cursor":
            # the surface point under the MOUSE CURSOR, captured once + held like 'view';
            # unmappable cursor / ray miss -> the object centre for the rest of the gesture
            if self._orbit_pivot is None or idle >= self._pivot_hold_sec:
                self._orbit_pivot = (self._cursor_pivot(view, c0, c1, c2, model)
                                     or self._object_center(model))
            pivot = self._orbit_pivot
        elif op == "selection":
            pivot = self._selection_center(self._selmgr) or self._object_center(model)
        else:                          # object / selection -> bounding-box centre (fixed point)
            pivot = self._object_center(model)

        view.RotateAboutAxis(angle, 0.0, 0.0, 0.0, ax, ay, az)   # pivots about origin

        if pivot is None or self._mathUtil is None or self._trans is None or not self._scale:
            return                                  # origin pivot (or no view state) -> pure rotation
        # pan so `pivot` keeps its screen position: dT = Scale2*((col_before - col_after).pivot), with
        # the post-rotation columns predicted analytically (no Orientation3 re-read).
        n0 = _rodrigues((ax, ay, az), -angle, c0)
        n1 = _rodrigues((ax, ay, az), -angle, c1)
        s, t = self._scale, self._trans
        dtx = s * ((c0[0] - n0[0]) * pivot[0] + (c0[1] - n0[1]) * pivot[1] + (c0[2] - n0[2]) * pivot[2])
        dty = s * ((c1[0] - n1[0]) * pivot[0] + (c1[1] - n1[1]) * pivot[1] + (c1[2] - n1[2]) * pivot[2])
        new_t = (t[0] + dtx, t[1] + dty, t[2])
        view.Translation3 = self._mkvec(*new_t)
        self._trans = list(new_t)                   # keep the tracked Translation3 in sync

    def _view_pivot(self, c0, c1, c2, model):
        """The model point currently at the screen centre -- the pivot held by a 'view' orbit gesture.
        Its in-plane position is the screen centre; its DEPTH along the optical axis is the TRUE surface
        depth under the crosshair from a screen-centre raycast (SelectByRay), matching SolidWorks' own
        middle-drag orbit. Falls back to the object-centre depth when the ray misses or raycasting is
        off/unavailable (the old behaviour). None if the view scale/translation aren't known yet."""
        if not self._scale or self._trans is None:
            return None
        box = self._object_box(model)
        center = self._box_center(box)
        a = -self._trans[0] / self._scale           # in-plane screen-centre offset (Scale2*(col.P)+T=0)
        b = -self._trans[1] / self._scale
        obj_depth = (c2[0] * center[0] + c2[1] * center[1] + c2[2] * center[2]) if center else 0.0
        depth = obj_depth
        if VIEW_PIVOT_RAYCAST and box is not None:
            rd = self._raycast_depth(model, c0, c1, c2, a, b, obj_depth, box)
            if rd is not None:                      # true surface depth under the crosshair
                depth = rd
        return (a * c0[0] + b * c1[0] + depth * c2[0],
                a * c0[1] + b * c1[1] + depth * c2[1],
                a * c0[2] + b * c1[2] + depth * c2[2])

    def _raycast_depth(self, model, c0, c1, c2, a, b, anchor_depth, box):
        """Shoot the screen-centre optical-axis ray into the model and return the depth (c2.hit) of the
        NEAREST valid surface hit, or None. The ray starts outside the model on the viewer's side (+c2)
        and points in (-c2); the aperture grows until something is hit (first/smallest hit wins). Each
        hit is validated against the bounding box (+margin) to reject bogus values; among the hits at a
        radius we take the one nearest the viewer (largest c2.hit). SelectByRay mutates the selection
        set, so we SAVE/clear/RESTORE the user's selection around it. Fully guarded -- any failure
        returns None and the 'view' pivot keeps the object-centre depth. Runs once per orbit gesture."""
        ext, selmgr = self._ext, self._selmgr
        if ext is None or selmgr is None:
            return None
        diag = math.sqrt((box[3] - box[0]) ** 2 + (box[4] - box[1]) ** 2 + (box[5] - box[2]) ** 2)
        if diag <= 0.0:
            return None
        push = _RAY_PUSH * diag                      # a screen-centre optical-axis point, pushed back
        ox = a * c0[0] + b * c1[0] + anchor_depth * c2[0] + c2[0] * push
        oy = a * c0[1] + b * c1[1] + anchor_depth * c2[1] + c2[1] * push
        oz = a * c0[2] + b * c1[2] + anchor_depth * c2[2] + c2[2] * push
        dx, dy, dz = -c2[0], -c2[1], -c2[2]         # into the screen
        margin = _RAY_BBOX_MARGIN * diag
        saved = self._save_selection(selmgr)
        best = None
        try:
            for frac in _RAY_APERTURE_FRACS:
                self._clear_selection(model)        # isolate our hit (Append=False can leave others)
                try:
                    ok = ext.SelectByRay(
                        _variant(VT_R8, ox), _variant(VT_R8, oy), _variant(VT_R8, oz),
                        _variant(VT_R8, dx), _variant(VT_R8, dy), _variant(VT_R8, dz),
                        _variant(VT_R8, frac * diag),
                        _variant(VT_I4, 1), _variant(VT_I4, 0),        # Tol (MUST be int), SelectOption
                        _variant(VT_BOOL, False), _variant(VT_I4, 0))  # Append, Mark
                except Exception:
                    continue
                if not ok:
                    continue
                try:
                    n = int(selmgr.GetSelectedObjectCount2(-1))
                except Exception:
                    n = 0
                for i in range(1, n + 1):
                    try:
                        pt = selmgr.GetSelectionPoint2(i, -1)
                    except Exception:
                        continue
                    if not (box[0] - margin <= pt[0] <= box[3] + margin
                            and box[1] - margin <= pt[1] <= box[4] + margin
                            and box[2] - margin <= pt[2] <= box[5] + margin):
                        continue                    # bogus / off-model hit
                    d = c2[0] * pt[0] + c2[1] * pt[1] + c2[2] * pt[2]   # larger = nearer the viewer
                    if best is None or d > best:
                        best = d
                if best is not None:
                    break                           # smallest aperture that hit -> most accurate
        finally:
            self._restore_selection(model, selmgr, saved)
        return best

    # --- the "cursor" pivot: OS cursor -> IModelView.Transform inverse -> (a, b) -> raycast --
    # DESIGN NOTE (why not IModelView.GetMouse/IMouse): GetMouse EXISTS (COM-introspected live:
    # a property returning an IMouse dispatch), but SolidWorks dispatches carry NO typeinfo, so
    # sinking IMouse's mouse-move notification means makepy'ing the whole sldworks typelib AND a
    # cross-process COM callback marshaled into SolidWorks' UI thread for EVERY mouse move
    # (~100+ Hz while the user mouses). We only need the cursor ONCE per gesture, so we read it
    # ON-DEMAND instead (GetCursorPos is a local Win32 call -- the daemon IS a Windows process;
    # same decision as the Fusion add-in, where a mouse-tracking Command was rejected). The
    # gesture-start read also means the pixel is always FRESH -- no cache to go stale.
    #
    # The px -> model mapping inverts IModelView.Transform. TWO facts about its pixel space were
    # nailed down by a live GUI probe (2026-07-05, screenshot-confirmed against a box's corners --
    # the earlier "desktop-pixel" claim was WRONG; it only round-tripped because the test fed the
    # same space both ways, tautologically):
    #   1. Transform's pixels are relative to the GRAPHICS-WINDOW CLIENT top-left (GetViewHWnd),
    #      NOT the desktop -- there is a ~toolbar-height y offset (and an x offset when the window
    #      isn't full-width). So we map the cursor with ScreenToClient(GetViewHWnd) FIRST, and the
    #      inversion is then a bare (client_px - t)/s (Transform is already client-relative).
    #   2. Transform's pixels are PHYSICAL (device) pixels, while GetCursorPos returns pixels in
    #      the CALLING THREAD's DPI space. At 125% scaling a DPI-unaware reader sees LOGICAL px and
    #      the pivot lands up-and-left of the cursor. Fix: make the WORKER THREAD per-monitor DPI
    #      aware (_make_thread_dpi_aware, thread-local so the Tk UI is untouched), so GetCursorPos
    #      /ScreenToClient/GetClientRect all return physical px matching Transform.
    # Its column-convention 3x3 rows are the camera right / NEGATED camera up axes (scale/sign/y
    # direction all come from SolidWorks itself, re-read each gesture -- no DPI/origin guesswork
    # beyond the two facts above).
    @staticmethod
    def _make_thread_dpi_aware():
        """Set THIS thread (the COM worker) to per-monitor-v2 DPI awareness so GetCursorPos and
        ScreenToClient return PHYSICAL pixels (matching IModelView.Transform). Thread-local
        (SetThreadDpiAwarenessContext, Win10 1607+), so it never affects the daemon's Tk UI thread.
        Best-effort: if unavailable the cursor pivot still works when the process is already DPI
        aware, else it maps in logical px and the 'view'/'object' pivots are unaffected."""
        try:
            import ctypes
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass

    def _cursor_client_point(self):
        """The OS cursor as GetViewHWnd CLIENT-relative px -- the space IModelView.Transform lives
        in (screenshot-verified) -- or None when the cursor isn't over the graphics area. Gated by
        the cursor falling inside the client rect while the SW frame is foreground; NOT by
        WindowFromPoint identity (SolidWorks composites the 3D view with an inner render child, so
        WindowFromPoint returns a window that is neither GetViewHWnd nor a resolvable descendant --
        verified live). ScreenToClient handles the client-origin offset; the worker thread's DPI
        awareness (_make_thread_dpi_aware) makes these px physical, matching Transform."""
        if not self._view_hwnd:
            return None
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            hwnd = self._view_hwnd
            if u32.GetAncestor(hwnd, 2) != u32.GetForegroundWindow():   # 2 = GA_ROOT (the SW frame)
                return None
            pt = wintypes.POINT()
            if not u32.GetCursorPos(ctypes.byref(pt)):
                return None
            rc = wintypes.RECT()
            if not u32.GetClientRect(hwnd, ctypes.byref(rc)) or rc.right <= 0 or rc.bottom <= 0:
                return None
            if not u32.ScreenToClient(hwnd, ctypes.byref(pt)):
                return None
            if not (0 <= pt.x <= rc.right and 0 <= pt.y <= rc.bottom):
                return None                    # cursor is outside the graphics area
            return (float(pt.x), float(pt.y))
        except Exception:
            return None

    def _cursor_screen_ab(self, view, c0, c1):
        """The mouse cursor as the in-plane model-space offsets (a, b) along camera right/up --
        the coordinates _view_pivot/_raycast_depth already speak -- or None when the cursor can't
        be mapped. Inverts IModelView.Transform from the cursor's CLIENT px (see the design note):
        a point on the cursor ray satisfies row_i . P = (client_px_i - t_i)/s, and row0/row1 align
        with +/-c0 and +/-c1 (the sign is resolved per capture, so no baked-in y-flip constant).
        Rows that DON'T align mean the transform model changed -> None (fallback), not a wrong
        pivot."""
        px = self._cursor_client_point()
        if px is None:
            return None
        try:
            ad = view.Transform.ArrayData
        except Exception:
            return None
        s = float(ad[12])
        if abs(s) < 1e-12:
            return None
        n0 = (ad[0], ad[3], ad[6])              # column-convention rows of the px transform
        n1 = (ad[1], ad[4], ad[7])
        d0 = n0[0] * c0[0] + n0[1] * c0[1] + n0[2] * c0[2]
        d1 = n1[0] * c1[0] + n1[1] * c1[1] + n1[2] * c1[2]
        if abs(abs(d0) - 1.0) > _CURSOR_XF_ALIGN_TOL or abs(abs(d1) - 1.0) > _CURSOR_XF_ALIGN_TOL:
            self._info_rl("cursor-xf", "solidworks: view Transform rows don't align with the "
                                       "camera axes -> cursor pivot fallback")
            return None
        a = (px[0] - ad[9]) / s * (1.0 if d0 >= 0.0 else -1.0)
        b = (px[1] - ad[10]) / s * (1.0 if d1 >= 0.0 else -1.0)
        return (a, b)

    def _cursor_pivot(self, view, c0, c1, c2, model):
        """The model point under the MOUSE CURSOR: the same SelectByRay raycast as the 'view'
        pivot, aimed through the cursor's (a, b) instead of the screen centre's. Returns a model
        point, or None (unmappable cursor / no box / ray miss) -> the caller falls back to the
        object centre. Runs once per orbit gesture (then the pivot is held)."""
        ab = self._cursor_screen_ab(view, c0, c1)
        if ab is None:
            self._info_rl("cursor-pivot", "solidworks: cursor not over the viewport "
                                          "(or unmapped) -> object-centre fallback")
            return None
        a, b = ab
        box = self._object_box(model)
        if box is None:
            return None
        center = self._box_center(box)
        obj_depth = (c2[0] * center[0] + c2[1] * center[1] + c2[2] * center[2]) if center else 0.0
        depth = self._raycast_depth(model, c0, c1, c2, a, b, obj_depth, box)
        if depth is None:
            self._info_rl("cursor-pivot", "solidworks: nothing under the cursor -> "
                                          "object-centre fallback")
            return None
        self._info_rl("cursor-pivot", "solidworks: cursor pivot held at (%.4f, %.4f, %.4f)"
                      % (a * c0[0] + b * c1[0] + depth * c2[0],
                         a * c0[1] + b * c1[1] + depth * c2[1],
                         a * c0[2] + b * c1[2] + depth * c2[2]))
        return (a * c0[0] + b * c1[0] + depth * c2[0],
                a * c0[1] + b * c1[1] + depth * c2[1],
                a * c0[2] + b * c1[2] + depth * c2[2])

    def _info_rl(self, key, msg, period=2.0):
        """Rate-limited info log (the worker runs at rate_hz -- unthrottled logs would spam)."""
        now = time.monotonic()
        if now - self._rl.get(key, 0.0) > period:
            self._rl[key] = now
            self._log.info(msg)

    @staticmethod
    def _set_graphics_update(view, enabled):
        """Suspend/resume viewport repaints via IModelView.EnableGraphicsUpdate. With it False the
        rotate + recenter pan don't each repaint, so the frame shows as ONE redraw (no flicker through
        the intermediate state) and skips a redundant full repaint. Returns True if it toggled, so the
        caller only resumes what it suspended; swallows errors so an unsupported build just falls back
        to per-op repaints."""
        try:
            view.EnableGraphicsUpdate = enabled
            return True
        except Exception:
            return False

    def _apply_pan(self, view, px, py):
        """Pan by adding to IModelView.Translation3 (meters in the graphics-area screen X,Y plane,
        zoom-independent -- no /Scale2). TranslateBy type-mismatches the MathVector under dynamic
        dispatch, so we SET Translation3 (verified live). Uses the tracked Translation3 to avoid a
        ~19 ms read, and keeps it in sync. PAN_SCALE sets the feel; PAN_SIGN flips each axis."""
        if self._mathUtil is None:
            return
        t = self._trans if self._trans is not None else list(view.Translation3.ArrayData)
        new_t = (t[0] + PAN_SIGN[0] * px * PAN_SCALE, t[1] + PAN_SIGN[1] * py * PAN_SCALE, t[2])
        view.Translation3 = self._mkvec(*new_t)
        self._trans = list(new_t)

    def _apply_zoom(self, view, zoom, scheme, model, idle):
        """Zoom by factor. to_center (default) = native ZoomByFactor (zooms about the view centre).
        to_object keeps the model bounding-box centre fixed, to_cursor the surface point under the
        MOUSE CURSOR (captured once per gesture + held, like the orbit pivots; a miss falls back to
        to_center): ZoomByFactor, then pan the held point back by (Scale2_before - Scale2_after)*
        (col.P). ZoomByFactor changes Scale2 (and Translation3) itself, so we resync tracked state."""
        factor = 1.0 + ZOOM_SIGN * zoom * ZOOM_SCALE
        if factor <= 1e-3:                          # guard against a non-positive scale factor
            return
        zm = scheme["zm"]
        center = None
        can_hold = self._mathUtil is not None and self._trans is not None and self._scale
        if zm == "to_cursor" and scheme.get("sel_override", True):
            center = self._selection_center(self._selmgr)
        if center is None:
            if zm == "to_object":
                center = self._object_center(model)
            elif zm == "to_cursor" and can_hold:
                if self._zoom_pivot is None or idle >= self._pivot_hold_sec:
                    ad = view.Orientation3.ArrayData
                    self._zoom_pivot = self._cursor_pivot(
                        view, (ad[0], ad[3], ad[6]), (ad[1], ad[4], ad[7]),
                        (ad[2], ad[5], ad[8]), model)
                center = self._zoom_pivot           # None (miss) -> plain to_center zoom
        if center is not None and can_hold:
            ad = view.Orientation3.ArrayData
            c0 = (ad[0], ad[3], ad[6]); c1 = (ad[1], ad[4], ad[7])
            vcx = c0[0] * center[0] + c0[1] * center[1] + c0[2] * center[2]
            vcy = c1[0] * center[0] + c1[1] * center[1] + c1[2] * center[2]
            s_before, t_before = self._scale, self._trans
            view.ZoomByFactor(factor)
            s_after = float(view.Scale2)
            ds = s_before - s_after
            new_t = (t_before[0] + ds * vcx, t_before[1] + ds * vcy, t_before[2])
            view.Translation3 = self._mkvec(*new_t)
            self._scale, self._trans = s_after, list(new_t)   # tracked precisely (we set both)
        else:
            view.ZoomByFactor(factor)               # to_center: SW changes Scale2+Translation3 itself
            self._view_ts = 0.0                      # -> resync tracked state on the next flush

    # --- selection save/restore around the raycast (so we don't disturb the user's work) --
    def _selection_center(self, selmgr):
        """Mean of the current selection points in model space, or None.

        SolidWorks' late-bound COM surface does not expose one uniform bounding-box API across
        faces, edges, features, bodies, and components. ``GetSelectionPoint2`` is available for all
        of them and is already the live-verified point source used by the raycast path, so averaging
        those points gives a stable selection pivot without mutating the user's selection.
        """
        if selmgr is None:
            return None
        now = time.monotonic()
        cached_mgr, cached_at, cached_center = self._selection_cache
        if selmgr is cached_mgr and now - cached_at < _SELECTION_CACHE_TTL:
            return cached_center
        try:
            n = int(selmgr.GetSelectedObjectCount2(-1))
        except Exception:
            self._selection_cache = (selmgr, now, None)
            return None
        points = []
        for i in range(1, n + 1):
            try:
                p = selmgr.GetSelectionPoint2(i, -1)
                if p is not None and len(p) >= 3:
                    points.append((float(p[0]), float(p[1]), float(p[2])))
            except Exception:
                pass
        if not points:
            self._selection_cache = (selmgr, now, None)
            return None
        count = float(len(points))
        center = tuple(sum(p[axis] for p in points) / count for axis in range(3))
        self._selection_cache = (selmgr, now, center)
        return center

    @staticmethod
    def _save_selection(selmgr):
        """Snapshot the current selection (entity dispatches) so the raycast can restore it. Returns
        [] on any failure (and when nothing is selected -- the common case during navigation)."""
        saved = []
        try:
            n = int(selmgr.GetSelectedObjectCount2(-1))
        except Exception:
            return saved
        for i in range(1, n + 1):
            try:
                ent = selmgr.GetSelectedObject6(i, -1)
                if ent is not None:
                    saved.append(ent)
            except Exception:
                pass
        return saved

    @staticmethod
    def _clear_selection(model):
        try:
            model.ClearSelection2(True)
        except Exception:
            pass

    @staticmethod
    def _restore_selection(model, selmgr, saved):
        """Drop our ray pick and re-select the saved entities. Restores the selection SET (selection
        marks aren't preserved -- acceptable for a navigation gesture). Never raises."""
        SolidWorksDriver._clear_selection(model)
        for ent in saved:
            try:
                ent._FlagAsMethod("Select")
                ent.Select(True)                    # append
            except Exception:
                pass

    # --- control-scheme geometry (pure math + a guarded bounding-box read) ----------------
    @staticmethod
    def _box_center(box):
        """Centre of a 6-tuple bounding box, or None."""
        if box is None:
            return None
        return ((box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5, (box[2] + box[5]) * 0.5)

    def _object_box(self, model):
        """Model bounding box (6-tuple, model space), cached ~0.5s. None if unavailable. Never raises
        (it's called outside the per-op guards, so a throw would look like a disconnect)."""
        now = time.monotonic()
        ts, b = self._box_cache
        if b is not None and now - ts < _OBJ_CACHE_TTL:
            return b
        b = self._compute_object_box(model)
        self._box_cache = (now, b)
        return b

    def _object_center(self, model):
        """Model bounding-box centre (model space), cached via _object_box. None if unavailable."""
        return self._box_center(self._object_box(model))

    @staticmethod
    def _compute_object_box(model):
        """Bounding box via the doc-type-appropriate API: parts -> IPartDoc.GetPartBox(True) (tight
        geometry box), assemblies -> IAssemblyDoc.GetBox(0). Returns None for drawings/empty docs or
        if the call isn't available. Never raises."""
        try:
            dtype = model.GetType                   # 1=part, 2=assembly, 3=drawing
        except Exception:
            dtype = None
        return _safe_box(model, "GetBox", 0) if dtype == 2 else _safe_box(model, "GetPartBox", True)

    @staticmethod
    def _compute_object_center(model):
        """Bounding-box centre (kept for direct callers/tests). None if the box is unavailable."""
        return SolidWorksDriver._box_center(SolidWorksDriver._compute_object_box(model))

    def _warn_once(self, what, exc):
        """Log a camera op's first failure (so it's diagnosable) without spamming, and without
        disconnecting -- the other ops and the redraw keep working."""
        if what not in self._warned:
            self._warned.add(what)
            self._log.info(f"solidworks: {what} step failed and is being skipped ({exc!r})")
