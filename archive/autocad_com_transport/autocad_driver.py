"""In-process AutoCAD COM driver -- the AutoCAD analogue of SolidWorksDriver.

Like SolidWorks (and unlike the Fusion/Blender/FreeCAD socket add-ons), AutoCAD is driven by
*external COM automation*: a smooth in-process view driver would otherwise be a .NET/ObjectARX
plugin that needs a per-version-built, NETLOAD'd assembly -- deliberately avoided, exactly like we
avoid the admin-registered SolidWorks add-in. Instead this driver -- which lives inside the daemon
process, parallel to the broker -- attaches to a *running* AutoCAD via its ActiveX/COM API (pywin32)
and moves the active viewport's 3D view directly. There is nothing to install into AutoCAD and the
nav broker is not involved.

Threading model mirrors SolidWorksDriver exactly:
  * submit() is called on the BLE thread and ONLY accumulates the per-frame orbit/pan/zoom delta --
    it never blocks and never touches COM (COM must be used on the thread that initialized it).
  * a single worker thread calls pythoncom.CoInitialize(), lazily attaches to AutoCAD.Application
    (ATTACH only -- it never launches AutoCAD), retries periodically while AutoCAD isn't running, and
    at bridge.rate_hz flushes the accumulated delta to the viewport, then zeroes it (a slow viewport
    coalesces motion instead of losing it -- same idea as the broker / the firmware's float carry).
  * on_connection_changed(connected, version) fires on attach/drop so the tray/UI status updates.

Degrades gracefully: no pywin32 (e.g. a non-Windows dev box) -> start() logs once and everything
becomes a no-op; the daemon runs with the AutoCAD integration simply unavailable.

======================================================================================
THE VERIFIED AutoCAD COM VIEW MODEL (probed live against AutoCAD 2026 / ACAD 25.1s; see
docs/autocad_driver_notes.md for the full write-up and the gotchas each fact cost).
======================================================================================
  * Attach: enumerate the Running Object Table (win32com GetActiveObject can return
    "Operation unavailable" even when AutoCAD is up). Each ROT dispatch's .Application
    normalizes to AcadApplication; filter by .Name == "AutoCAD" and pick the one with the
    most open Documents (verticals -- Civil 3D/Architecture/Mechanical -- are all acad.exe and
    expose the same AutoCAD.Application, so they work for free).
  * Version: acad.Version (e.g. "25.1s (LMS Tech)") -- NOT RevisionNumber (that's SolidWorks).
  * doc = acad.ActiveDocument; guard doc.ActiveSpace == 1 (acModelSpace). A freshly Documents.Add()ed
    doc mis-resolves properties under late dispatch -- re-fetch acad.ActiveDocument.
  * READ THE LIVE VIEW FROM SYSVARS, NOT THE VIEWPORT OBJECT. doc.ActiveViewport returns a clone
    whose Direction/Target/Center are DESYNCED from the displayed view (they read stale defaults).
    The ground truth is the system variables:
        VIEWDIR  (3 doubles, un-normalized, target->camera),
        TARGET   (3 doubles, the look-at point),
        VIEWSIZE (double, the view height in drawing units == AcadViewport.Height),
        EXTMIN / EXTMAX (3 doubles each, drawing extents -> object-pivot / zoom-to-object centre).
  * ORBIT (change the view DIRECTION) needs the reassign commit ritual -- it is the ONLY view op with
    no smooth Application method:  vp = doc.ActiveViewport;  vp.Direction = ...; vp.Target = pivot;
    vp.Height = VIEWSIZE;  doc.ActiveViewport = vp.  Modifying the viewport does nothing until the
    reassign (the commit). The reassign REGENs (~50 ms/frame vs ~3 ms for the Zoom* methods -- measured),
    and REGENMODE=0 does NOT suppress it (reassign ~34 ms either way; an explicit Regen of the same
    drawing is only ~10 ms -- verified live). That regen is the source of orbit flicker; there is NO
    redraw-only view-rotation over COM -- confirmed by a FULL sweep of the type library (461 types:
    SetView is inert until the same reassign and then the same regen pipeline at ~27 ms; Eval needs the
    absent VBA enabler; PostCommand only reaches the regen-ing command pipeline; every other Rotate* is
    entity rotation; and the ObjectARX AcGsView route needs a compiler this machine doesn't have). So
    ORBIT IS DEFERRED by default (_ORBIT_DEFER): accumulate, apply ONE reassign when the gesture
    pauses -- and the ORBIT_OVERLAY rotating-cube window (acad_overlay.py) shows the pending
    orientation LIVE over the AutoCAD window, so the gesture keeps continuous visual feedback while
    the drawing waits. Setting vp.Height in the reassign makes its intermediate repaint already the
    right zoom -- else it flashes the clone's default zoom before the trailing ZoomCenter.
  * NEVER set the AcadViewport.Center property. It is treacherous: setting Center=(0,0) (or any value)
    while the geometry is away from the WCS origin destroys the framing -- verified live it ballooned
    VIEWSIZE 27 -> 118. (vp.Height, by contrast, is safe.) Instead, after the Direction reassign we
    RE-CENTRE with ZoomCenter, which fixes VIEWCTR cleanly and doesn't touch Center.
  * PAN and ZOOM go through the smooth Application zoom methods -- NO reassign, NO regen (~3 ms):
      pan          -> acad.ZoomCenter(newCentreVARIANT, VIEWSIZE)  (moves the screen centre)
      zoom to_ctr  -> acad.ZoomScaled(factor, acZoomScaledRelative=1)  (factor>1 zooms IN, VIEWSIZE/=f)
      zoom to_obj  -> acad.ZoomCenter(bboxCentreVARIANT, VIEWSIZE/factor)
    Orbit's trailing acad.ZoomCenter(pivot, VIEWSIZE) re-centres the pivot. We track a 3D pivot point
    (_center) across these ZoomCenter writes so orbit/pan/zoom all keep the same point centred and
    compose without jumps (an earlier Center=(0,0)/ZoomScaled mix snapped the view on the zoom<->orbit
    transition -- fixed). _center is seeded from VIEWCTR (what's actually centred), NOT TARGET -- after
    a Zoom Extents the TARGET can sit far from the geometry, so ZoomCenter(TARGET) would centre empty
    space.
  * SAFEARRAY args: Direction/Target and every ZoomCenter point take a VARIANT(VT_ARRAY|VT_R8) of 3
    doubles. A plain tuple raises 0x80020009 under late dispatch (same class as SolidWorks' CreateVector).
  * AutoCAD AUTO-LEVELS the up vector to world Z, and VIEWTWIST is NOT settable via SetVariable, so
    free-ROLL is limited/approximate -- turntable (yaw about world Z + pitch about camera-right) is the
    natural fit; the roll channel is dropped (documented). WORLD_UP = (0,0,1) (verified: yaw about it
    keeps verticals vertical).

Performance: the ORBIT reassign REGENs + flickers (inherent to a 3D view change over COM; ~34 ms on a
108-solid drawing, unaffected by REGENMODE), so by default it runs ONCE per gesture (deferred) with the
overlay supplying the live feedback; _ORBIT_DEFER=False instead applies per frame (continuous but
flickery). Pan/zoom via the Zoom* methods are ~3 ms (redraw, no regen -- smooth, always immediate). The
worker CACHES the doc handle + TRACKS the view direction/size/pivot across its own writes, re-validating
direction+size from the sysvars (which doubles as the liveness probe and resyncs against a mouse-driven
view change) only every _VIEW_TTL -- the SolidWorks perf trick.

Every camera op is wrapped so one failing COM call is logged once and skipped -- it can never blank
the viewport or trip a disconnect (only the app-alive probe raising does that).
"""
import math
import shutil
import threading
import time
from pathlib import Path

from .paths import user_config_dir
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


# --- tuning: AutoCAD's intrinsic axis orientation + baseline sensitivity -------------------
# The nav-delta contract feeds (ox,oy,oz) = orbit about (camera right, up, forward) in radians,
# (px,py) = pan, zoom = zoom; values ARRIVE ALREADY SCALED by the active app's bindings. These bake
# in only the BASELINE feel and are expected to want a per-hardware sign/scale pass (see the notes
# doc + HANDOFF S12.5). Flip a sign if a channel goes the wrong way; do NOT re-scale/re-invert here.
#
# orbit: rotation about the CAMERA axes (right/up/forward), derived from the live VIEWDIR each frame.
# We ROTATE THE VIEW DIRECTION (the camera about the target), so the baseline sign is the opposite of
# the SolidWorks driver (which rotates the model) -- ORBIT_SIGN reflects that starting guess.
ORBIT_SIGN = (1.0, 1.0, 1.0)      # (ox=pitch about right, oy=yaw about up, oz=roll about forward)
# turntable azimuth axis: AutoCAD's WCS is Z-up (verified live -- yawing VIEWDIR about (0,0,1) keeps
# verticals vertical and never tumbles the box).
WORLD_UP = (0.0, 0.0, 1.0)
# pan: move the Target in the camera right/up plane, scaled by VIEWSIZE so pan feels constant at any
# zoom (like the Fusion add-in / Onshape bridge scale by the view extent). PAN_SCALE is the main knob.
PAN_SIGN = (1.0, -1.0)            # pan along (camera-right, camera-up); up negated like the add-ins
PAN_SCALE = 0.5                   # pan delta -> fraction of VIEWSIZE (drawing units vary wildly; tune)
# zoom: factor = 1 + ZOOM_SIGN*zoom*ZOOM_SCALE; factor > 1 zooms IN (ZoomScaled halves VIEWSIZE at 2).
ZOOM_SCALE = 0.5
ZOOM_SIGN = 1.0

# AutoCAD enum/constant values (win32com.client.constants is empty under late-bound dispatch, so these
# are hard-coded -- verified live).
AC_MODEL_SPACE = 1                # AcActiveSpace.acModelSpace (paper space == 0)
AC_ZOOM_SCALED_RELATIVE = 1       # AcZoomScaledType.acZoomScaledRelative (verified: factor 2 halves VIEWSIZE)

DEFAULT_FLUSH_HZ = 30.0
_RETRY_PERIOD = 2.0               # seconds between attach attempts while AutoCAD isn't running
_OBJ_CACHE_TTL = 0.5             # seconds to cache the drawing-extents centre (object pivot / zoom)
# ActiveDocument / ActiveViewport reassign / Update are the costs (~47 ms/frame if VIEWDIR/TARGET/
# VIEWSIZE are re-read each frame). So we cache the doc handle + track the view state and re-validate
# only every _VIEW_TTL, which also probes liveness and resyncs against a mouse-driven view change.
_VIEW_TTL = 1.0

# ORBIT and the regen (the crux). Orbit is the ONE op that must change the view DIRECTION, which over
# COM is only possible via the ActiveViewport reassign -- and that reassign REGENs (a full, non-buffered
# repaint) EVERY time. Verified live: REGENMODE=0 does NOT suppress it (reassign ~34 ms either way; an
# explicit Regen of the same drawing is only ~10 ms), the view sysvars aren't SetVariable-able, and
# there is NO redraw-only view-rotation exposed to COM. So you can't "redraw smoothly + throttle the
# regen" -- that separation needs the ObjectARX AcGsView API (a compiled plugin, deliberately avoided).
# Two achievable modes, chosen by _ORBIT_DEFER:
#   False           -> apply every frame: CONTINUOUS movement, but each frame regens (flickers). Lower
#                      the app's Viewport refresh rate to trade flicker-frequency for smoothness.
#   True (default)  -> accumulate and apply ONE reassign when the gesture pauses (idle) or the max-defer
#                      cap hits: NO per-frame flicker. The rotating-cube OVERLAY below bridges the gap
#                      by showing the pending orientation live while the drawing waits.
_ORBIT_DEFER = True
_ORBIT_IDLE = 0.10          # (defer mode) apply the pending orbit this long after the last orbit input
_ORBIT_MAX_DEFER = 0.30     # (defer mode) never hold a pending orbit longer than this during a roll
# Defer-mode gap closer: a tiny click-through wireframe-cube overlay (acad_overlay.py), centred on
# the AutoCAD window, that rotates with the ACCUMULATED orbit while the real viewport waits for the
# gesture pause -- live orientation feedback with ZERO AutoCAD/COM traffic. False disables it.
ORBIT_OVERLAY = True


if _PYWIN32:
    VT_ARRAY, VT_R8 = pythoncom.VT_ARRAY, pythoncom.VT_R8
else:                                       # pragma: no cover - non-Windows; COM never runs
    VT_ARRAY = VT_R8 = 0

# --- the NETLOAD smooth-orbit plugin (see plugin_src/autocad + notes 8.14/8.15) ----------------
# In-process, the plugin drives the viewport's LIVE GraphicsSystem view (ObtainAcGsView with a
# "3D Drawing" kernel descriptor): ~1.6 ms/frame with ZERO regens, plus one regen-free DB sync per
# gesture (notes 8.15; its earlier SetCurrentView path regenerated every frame). So the bundled
# .NET plugin is the primary AutoCAD transport: this driver NETLOADs it on attach, the plugin
# connects to the nav broker like the other socket add-ons, and app.py routes autocad frames to
# the broker whenever that client is present -- this COM driver then serves only as the fallback.
_PLUGIN_DLL = "TrackballNavAcad.dll"


def _bundled_plugin_path():
    return Path(__file__).resolve().parent / "plugins" / "autocad" / _PLUGIN_DLL


def _runtime_plugin_dir():
    """Where the plugin is COPIED before NETLOAD. AutoCAD locks the loaded file for the whole
    session, so loading the bundled copy directly would break daemon updates (verified the hard
    way: a rebuild could not overwrite the bundled DLL while AutoCAD had it loaded)."""
    return user_config_dir() / "acad_plugin"


def _arr(values):
    """Wrap a coordinate list in an explicitly-typed SAFEARRAY VARIANT (VT_ARRAY|VT_R8). AutoCAD's
    Direction/Target/Center setters reject a plain Python tuple under late-bound dispatch (COM type
    error 0x80020009); the VARIANT marshals (verified live). Falls back to the raw list without
    pywin32 (non-Windows dev box; the call never runs there)."""
    vals = [float(v) for v in values]
    if not _PYWIN32:
        return vals
    return win32com.client.VARIANT(VT_ARRAY | VT_R8, vals)


# --- small vector helpers (3-tuples) -- avoids a numpy dependency -----------------------------
def _v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _v_len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _v_normalize(a):
    n = _v_len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 1.0)


def _rodrigues(u, theta, p):
    """Rotate vector p about UNIT axis u by theta (radians) -- Rodrigues' formula. Same helper the
    SolidWorks/Onshape drivers use; here it rotates the view direction (camera about the target)."""
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
    if s < 1e-9:                                  # ~no rotation -> any axis, zero angle
        return (0.0, 0.0, 1.0, 0.0)
    return (q[1] / s, q[2] / s, q[3] / s, angle)


def _camera_basis(vd):
    """Camera basis from the (normalized) view direction vd (target->camera, points OUT of the
    screen). Returns (right, up, forward) with forward = -vd (into the screen). AutoCAD is Z-up, so
    right = normalize(WORLD_UP x vd); a top/bottom view (vd parallel to WORLD_UP) is degenerate ->
    fall back to right = world +X."""
    right = _v_cross(WORLD_UP, vd)
    if _v_len(right) < 1e-9:                       # looking straight down/up: pick a stable right
        right = (1.0, 0.0, 0.0)
    right = _v_normalize(right)
    up = _v_normalize(_v_cross(vd, right))
    forward = (-vd[0], -vd[1], -vd[2])
    return right, up, forward


class AutoCADDriver:
    """Accumulates nav deltas (BLE thread) and applies them to a live AutoCAD viewport from a
    CoInitialized worker thread at a fixed rate. Public surface parallels SolidWorksDriver /
    OnshapeBridge: submit(), set_rate(), set_scheme(), start(), stop(), plus is_connected()/version()
    for status and the on_connection_changed(connected, version) callback."""

    def __init__(self, on_connection_changed=None, rate_hz=DEFAULT_FLUSH_HZ):
        self.on_connection_changed = on_connection_changed   # callback(connected: bool, version: str)
        self._lock = threading.Lock()
        self._acc = [0.0] * 6
        self._stop = threading.Event()
        self._period = 1.0 / self._clamp_rate(rate_hz)
        self._thread = None
        self._connected = False
        self._version = ""
        self._log = get_logger()
        self._warned = set()                                  # one-time logs for failing ops
        # Control scheme (orbit pivot / orbit style / zoom mode), set live via set_scheme. Mirrors
        # NavBroker; a dict ref-swap is atomic, so the worker reads it lock-free each flush.
        self._scheme = {"op": "view", "os": "free", "zm": "to_center"}
        # COM handles -- created and used ONLY on the worker thread.
        self._acad = None
        self._box_cache = (0.0, None)                         # (monotonic_ts, extents 6-tuple or None)
        # Cached doc handle + tracked view state (worker thread only) -- see _VIEW_TTL. _dir/_size are
        # re-validated from the sysvars periodically; _center (the 3D point we keep screen-centred, our
        # orbit/pan pivot) is our own state, seeded from TARGET once and carried across ZoomCenter writes.
        self._doc = None
        self._modelspace = True                               # doc.ActiveSpace == acModelSpace (cached)
        self._dir = None                                      # normalized VIEWDIR (target->camera) or None
        self._center = None                                   # tracked 3D screen-centred pivot or None
        self._size = None                                     # VIEWSIZE (view height) or None
        self._view_ts = 0.0                                   # last re-validate time (monotonic)
        # Deferred orbit: accumulate orbit deltas and apply (one regen) on gesture idle / max-defer.
        self._pending_orbit = [0.0, 0.0, 0.0]                 # summed (ox,oy,oz) not yet applied
        self._orbit_last_t = 0.0                              # monotonic time of the last orbit input
        self._orbit_since = None                              # when the pending batch started (max-defer)
        # Defer-mode overlay (worker thread only; see acad_overlay.py). One failure disables it.
        self._overlay = None
        self._overlay_shown = False
        self._overlay_dead = False
        self._netload_done = False                            # NETLOAD attempted for this attach

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

    def set_scheme(self, orbit_pivot, orbit_style, zoom_mode):
        """Set the control scheme applied on the next flush. Parallels SolidWorksDriver.set_scheme so
        app._apply_schemes() drives AutoCAD the same way.
          orbit_pivot: origin | object | view | cursor | pointer
              origin  -> orbit about the WCS origin (0,0,0);
              object  -> orbit about the drawing-extents (EXTMIN/EXTMAX) centre;
              view    -> orbit about the current view Target (AutoCAD's native target orbit; there is
                         no COM screen-centre raycast, so 'view' uses the Target -- see the notes doc);
              cursor  -> falls back to object (no COM cursor hit-test);
              pointer -> TRUE under-the-mouse orbit in the NETLOAD plugin (PointMonitor); in this
                         COM fallback it degrades to object (the plugin normally owns the frames).
          orbit_style: free | turntable  (AutoCAD auto-levels the up, so free-roll is approximate;
                       turntable = yaw about world Z + pitch about camera-right is the natural fit)
          zoom_mode:   to_center | to_object | to_cursor | to_pointer  (to_cursor/to_pointer fall
                       back to to_center here; to_pointer is honoured by the NETLOAD plugin)."""
        self._scheme = {"op": orbit_pivot, "os": orbit_style, "zm": zoom_mode}

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
            self._log.info("autocad: pywin32 not available; COM driver disabled")
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="autocad-driver", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # --- worker thread (owns COM) --------------------------------------------------------
    def _run(self):
        pythoncom.CoInitialize()
        last_attach = 0.0
        try:
            while not self._stop.is_set():
                cycle_start = time.monotonic()
                period = self._period               # re-read each loop so set_rate() applies live
                if self._acad is None:
                    if cycle_start - last_attach >= _RETRY_PERIOD:
                        last_attach = cycle_start
                        self._attach()
                    if self._acad is None:
                        self._drain()               # drop motion while detached
                        self._sleep_remainder(cycle_start, period)
                        continue
                with self._lock:
                    a = self._acc
                    has = any(a)
                    if has:
                        delta = tuple(a)
                        self._acc = [0.0] * 6
                try:
                    if has:
                        self._flush(delta)
                    elif self._orbit_since is not None:
                        self._flush_idle()          # no new input: maybe apply a paused orbit gesture
                except Exception:
                    # AutoCAD closed / COM handle dropped -> disconnect, re-attach later.
                    self._handle_drop()
                self._sleep_remainder(cycle_start, period)
        finally:
            if self._overlay is not None:        # the overlay window lives on this thread
                try:
                    self._overlay.destroy()
                except Exception:
                    pass
                self._overlay = None
            self._acad = None
            pythoncom.CoUninitialize()

    def _sleep_remainder(self, cycle_start, period):
        """Sleep only the time left in this period AFTER the work, so the flush cadence stays even
        regardless of how long each (variable-cost) COM flush took -- what keeps motion smooth instead
        of stuttering at period + flush_time. If we're over budget, fall through with a tiny yield."""
        remaining = period - (time.monotonic() - cycle_start)
        self._stop.wait(remaining if remaining > 0.0 else 0.001)

    def _drain(self):
        with self._lock:
            self._acc = [0.0] * 6

    def _attach(self):
        """Attach to a running AutoCAD (never launch one). Returns True on success."""
        acad = self._find_running_acad()
        if acad is None:
            return False
        self._acad = acad
        self._box_cache = (0.0, None)
        self._invalidate_view()
        self._warned.clear()
        self._netload_done = False           # fresh attach (new acad session) -> re-NETLOAD
        try:
            self._version = str(acad.Version)
        except Exception:
            self._version = "COM"
        self._set_connected(True)
        self._log.info(f"autocad: attached to running instance (v{self._version})")
        return True

    @staticmethod
    def _find_running_acad():
        """Return a running AutoCAD, PREFERRING one with a document open. GetActiveObject can return
        'Operation unavailable' even when AutoCAD is up (verified live), so enumerate the Running
        Object Table: normalize each dispatch to its .Application (both AcadApplication and
        AcadDocument expose it), keep the ones named 'AutoCAD', and pick the instance with the most
        open documents; fall back to GetActiveObject."""
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

    def _handle_drop(self):
        if self._connected:
            self._log.info("autocad: lost COM connection (app closed)")
        self._acad = None
        self._box_cache = (0.0, None)
        self._invalidate_view()
        self._overlay_hide()
        self._set_connected(False)

    def _invalidate_view(self):
        """Drop the cached doc handle + tracked view state so the next flush re-fetches/re-seeds them."""
        self._doc = None
        self._dir = None
        self._center = None
        self._size = None
        self._view_ts = 0.0
        self._modelspace = True
        self._pending_orbit = [0.0, 0.0, 0.0]
        self._orbit_since = None

    def _set_connected(self, connected):
        if connected == self._connected:
            return
        self._connected = connected
        if self.on_connection_changed:
            try:
                self.on_connection_changed(connected, self._version if connected else "")
            except Exception:
                pass

    # --- camera application (worker thread only; see module docstring for the verified API) --------
    def _flush(self, delta):
        ox, oy, oz, px, py, zoom = delta
        acad = self._acad
        if acad is None:
            return
        doc = self._live(acad)                       # cached doc + tracked view state; None => idle
        if doc is None or not self._modelspace:      # no drawing, or paper space / 2D -> no-op
            return
        if self._dir is None or self._center is None or self._size is None:
            return                                   # view state unavailable this cycle
        scheme = self._scheme
        now = time.monotonic()
        # ORBIT: apply every frame by default (continuous movement). If _ORBIT_DEFER is on, accumulate
        # and apply only when the gesture pauses (via _flush_idle) or the max-defer cap hits -- fewer
        # regens (no per-frame flicker) but choppier. The reassign regens either way (module docstring).
        if ox or oy or oz:
            self._pending_orbit[0] += ox
            self._pending_orbit[1] += oy
            self._pending_orbit[2] += oz
            self._orbit_last_t = now
            if self._orbit_since is None:
                self._orbit_since = now
            if not _ORBIT_DEFER or now - self._orbit_since >= _ORBIT_MAX_DEFER:
                # immediate mode / the mid-gesture cap: apply now, keep the overlay up (mid-gesture)
                self._apply_pending_orbit(acad, doc, hide_overlay=False)
            elif ORBIT_OVERLAY:
                self._overlay_preview(acad)      # live feedback while the real view waits (defer mode)
        # PAN and ZOOM are smooth (Zoom* methods -- no regen) and applied immediately. Flush any pending
        # orbit first so the view is consistent before we pan/zoom relative to it. Applied independently
        # (a coalesced frame may carry more than one). NO acad.Update(): the Zoom* writes self-repaint.
        if px or py:
            self._apply_pending_orbit(acad, doc)
            try:
                self._apply_pan(acad, px, py)
            except Exception as exc:
                self._warn_once("pan", exc)
        if zoom:
            self._apply_pending_orbit(acad, doc)
            try:
                self._apply_zoom(acad, doc, zoom, scheme)
            except Exception as exc:
                self._warn_once("zoom", exc)

    def _flush_idle(self):
        """Called each worker cycle when there is NO new input but an orbit gesture is pending: apply
        the accumulated orbit once it has been idle >= _ORBIT_IDLE (the gesture has stopped). This is
        what makes orbit regen ONCE per flick instead of every frame."""
        if self._orbit_since is None:
            return
        if time.monotonic() - self._orbit_last_t < _ORBIT_IDLE:
            return
        acad = self._acad
        if acad is None:
            return
        doc = self._live(acad)
        if doc is None or not self._modelspace or self._dir is None or self._size is None:
            self._pending_orbit = [0.0, 0.0, 0.0]    # can't apply (no view) -> drop it, don't get stuck
            self._orbit_since = None
            self._overlay_hide()
            return
        self._apply_pending_orbit(acad, doc)

    def _apply_pending_orbit(self, acad, doc, hide_overlay=True):
        """Apply + clear the accumulated orbit (one reassign = one regen). The summed per-frame deltas
        are applied as a single composed rotation (approximate for a large batch, but visually correct
        for the final orientation -- same coalescing idea the other drivers use). hide_overlay=False
        keeps the preview cube up when this is a MID-gesture apply (the max-defer cap)."""
        if hide_overlay:
            self._overlay_hide()
        po = self._pending_orbit
        self._pending_orbit = [0.0, 0.0, 0.0]
        self._orbit_since = None
        if not (po[0] or po[1] or po[2]):
            return
        try:
            self._apply_orbit(acad, doc, po[0], po[1], po[2], self._scheme)
        except Exception as exc:
            self._warn_once("orbit", exc)

    def _live(self, acad):
        """Return the cached ActiveDocument, re-validating at most every _VIEW_TTL. The re-validate is
        the liveness probe (acad.Documents.Count raises if the app is gone -> _run drop), skips when no
        drawing is open (Start tab), and resyncs the tracked view DIRECTION + SIZE against any external
        (mouse) view change. The tracked _center (the 3D point we keep screen-centred) is our OWN state,
        seeded from VIEWCTR on the first read after attach and then carried across our ZoomCenter writes
        -- it is NOT re-read each cycle, because a mouse PAN moves VIEWCTR and re-seeding it every cycle
        would fight our own pans. Between re-validations the doc handle + tracked state are reused (the
        COM round-trips are what cost, ~tens of ms)."""
        now = time.monotonic()
        if self._doc is not None and now - self._view_ts < _VIEW_TTL:
            return self._doc
        if int(acad.Documents.Count) == 0:           # app alive but no drawing -> idle (NOT a drop)
            self._invalidate_view()
            return None
        doc = acad.ActiveDocument
        try:
            self._modelspace = int(doc.ActiveSpace) == AC_MODEL_SPACE
        except Exception:
            self._modelspace = True
        try:                                         # resync direction + size from the sysvars
            vd = doc.GetVariable("VIEWDIR")
            self._dir = _v_normalize((vd[0], vd[1], vd[2]))
            self._size = float(doc.GetVariable("VIEWSIZE"))
            if self._center is None:                 # seed the pivot from what's ACTUALLY centred.
                ctr = doc.GetVariable("VIEWCTR")     # VIEWCTR (not TARGET -- after a Zoom Extents TARGET
                self._center = (ctr[0], ctr[1], ctr[2])   # can be far from the geometry / screen centre)
        except Exception:
            self._dir = self._size = None
        self._doc = doc
        self._view_ts = now
        if not self._netload_done:
            self._netload_plugin(doc)
        return doc

    def _netload_plugin(self, doc):
        """Copy the bundled smooth-orbit plugin to the runtime dir and NETLOAD it (once per attach).
        The plugin (see plugin_src/autocad) drives the viewport's live GraphicsSystem view in-process
        (~1.6 ms/frame, zero regens, one regen-free DB sync per gesture -- notes 8.15); once it
        connects to the nav broker, app.py routes autocad frames to it and this COM driver becomes
        the fallback. Copy-then-load keeps the bundled file unlocked (AutoCAD locks the loaded path
        for the whole session); TRUSTEDPATHS is extended so SECURELOAD never prompts. All
        best-effort -- any failure leaves the COM fallback driving."""
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

    def _rotate_dir(self, vd, ox, oy, oz, scheme):
        """The new normalized view direction after an (ox,oy,oz) orbit under the scheme's style --
        pure math, shared by the real apply (_apply_orbit) and the overlay preview (_overlay_preview).
        free rotates about the composed camera axis; turntable yaws about WORLD up + pitches about
        camera-right (roll dropped -- AutoCAD auto-levels the up)."""
        vd = _v_normalize(vd)
        right, up, forward = _camera_basis(vd)
        vx = ORBIT_SIGN[0] * ox
        vy = ORBIT_SIGN[1] * oy
        vz = ORBIT_SIGN[2] * oz
        if scheme.get("os") == "turntable":
            q = _q_mul(_q_from_axis_angle(right[0], right[1], right[2], vx),
                       _q_from_axis_angle(WORLD_UP[0], WORLD_UP[1], WORLD_UP[2], vy))
            ax, ay, az, angle = _q_to_axis_angle(q)
        else:                                        # free
            ax = right[0] * vx + up[0] * vy + forward[0] * vz
            ay = right[1] * vx + up[1] * vy + forward[1] * vz
            az = right[2] * vx + up[2] * vy + forward[2] * vz
            angle = math.sqrt(ax * ax + ay * ay + az * az)
            if angle >= 1e-12:
                ax, ay, az = ax / angle, ay / angle, az / angle
        if angle < 1e-12:
            return vd                                # e.g. turntable pure-roll -> no rotation
        return _v_normalize(_rodrigues((ax, ay, az), angle, vd))

    def _apply_orbit(self, acad, doc, ox, oy, oz, scheme):
        """Orbit = change the view DIRECTION (the one op with no smooth Application method, so it needs
        the ActiveViewport reassign -- which REGENs; see the module docstring/notes), then ZoomCenter
        the pivot back to the screen centre. The reassign alone leaves the framing wrong (VIEWCTR/
        VIEWSIZE take the desynced clone's stale values); ZoomCenter(pivot, _size) fixes BOTH cleanly.
        We deliberately do NOT set the Center property -- setting it destroys the framing whenever the
        model isn't near the WCS origin (verified live: Center=(0,0) ballooned VIEWSIZE 27->118)."""
        pivot = self._pivot_point(scheme, doc)
        new_dir = self._rotate_dir(self._dir, ox, oy, oz, scheme)
        vp = doc.ActiveViewport
        vp.Direction = _arr(new_dir)                 # SAFEARRAY VARIANT (a plain tuple throws)
        vp.Target = _arr(pivot)                      # rotate about the pivot
        vp.Height = float(self._size)                # set the zoom so the reassign's intermediate repaint
        #                                              is already at the right zoom (no default-zoom flash).
        #                                              Height (unlike Center) is safe -- verified no blowup.
        doc.ActiveViewport = vp                      # THE COMMIT (regens)
        acad.ZoomCenter(_arr(pivot), float(self._size))   # re-centre the pivot (+ reaffirm VIEWSIZE)
        self._dir = new_dir
        self._center = pivot

    def _pivot_point(self, scheme, doc):
        """The orbit pivot (the 3D point we keep screen-centred): origin -> WCS origin; object/cursor/
        pointer -> drawing-extents centre (falling back to the tracked centre if extents are
        unavailable); view/default -> the tracked centre (AutoCAD's native target orbit -- there is
        no COM screen-centre raycast, so 'view' orbits about whatever is currently centred).

        `pointer` is only TRUE under-the-mouse orbit in the NETLOAD plugin (its Editor.PointMonitor
        caches the cursor point in-process); this COM fallback has no cursor hit-test -- when the
        plugin isn't connected the pointer scheme degrades to the extents centre, like cursor."""
        op = scheme.get("op", "view")
        if op == "origin":
            return (0.0, 0.0, 0.0)
        if op in ("object", "cursor", "pointer"):
            return self._object_center(doc) or self._center
        return self._center                          # view / default

    def _apply_pan(self, acad, px, py):
        """Pan by ZoomCentering to a shifted centre (a smooth Application zoom -- no reassign, no
        regen), scaled by VIEWSIZE so it feels constant at any zoom. This moves what's centred AND our
        tracked pivot together, so a subsequent orbit keeps the panned point centred."""
        right, up, _ = _camera_basis(_v_normalize(self._dir))
        scale = PAN_SCALE * (self._size or 1.0)
        gx = PAN_SIGN[0] * px * scale
        gy = PAN_SIGN[1] * py * scale
        c = self._center
        new_c = (c[0] + right[0] * gx + up[0] * gy,
                 c[1] + right[1] * gx + up[1] * gy,
                 c[2] + right[2] * gx + up[2] * gy)
        acad.ZoomCenter(_arr(new_c), float(self._size))
        self._center = new_c

    def _apply_zoom(self, acad, doc, zoom, scheme):
        """Zoom via the smooth Application methods (no reassign, no regen). to_center = ZoomScaled about
        the view centre (== our tracked pivot, since every op ZoomCenters it there); to_object =
        ZoomCenter about the drawing-extents centre; to_cursor falls back to to_center (no COM
        hit-test). factor > 1 zooms IN (verified: ZoomScaled(2, relative) halves VIEWSIZE). We track
        _size across the write (VIEWSIZE /= factor) so orbit/pan preserve the new zoom; the _VIEW_TTL
        re-read corrects any accumulated drift."""
        factor = 1.0 + ZOOM_SIGN * zoom * ZOOM_SCALE
        if factor <= 1e-3:                           # guard a non-positive scale factor
            return
        if scheme.get("zm") == "to_object":
            center = self._object_center(doc)
            if center is not None:
                new_size = self._size / factor
                acad.ZoomCenter(_arr(center), float(new_size))
                self._center = center
                self._size = new_size
                return
        acad.ZoomScaled(float(factor), AC_ZOOM_SCALED_RELATIVE)   # to_center / to_cursor fallback
        self._size = self._size / factor

    # --- object pivot / zoom-to-object centre (drawing extents, cached) --------------------
    def _object_box(self, doc):
        """Drawing extents (EXTMIN..EXTMAX) as a 6-tuple, cached ~0.5s. None if unavailable. Never
        raises (it's called outside the per-op guards, so a throw would look like a disconnect)."""
        now = time.monotonic()
        ts, b = self._box_cache
        if b is not None and now - ts < _OBJ_CACHE_TTL:
            return b
        b = None
        try:
            mn = doc.GetVariable("EXTMIN")
            mx = doc.GetVariable("EXTMAX")
            b = (mn[0], mn[1], mn[2], mx[0], mx[1], mx[2])
        except Exception:
            b = None
        self._box_cache = (now, b)
        return b

    def _object_center(self, doc):
        """Centre of the drawing extents (WCS), or None."""
        box = self._object_box(doc)
        if box is None:
            return None
        return ((box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5, (box[2] + box[5]) * 0.5)

    # --- defer-mode overlay (worker thread only; pure feedback, never touches COM) ---------
    def _overlay_preview(self, acad):
        """Show/refresh the rotating-cube overlay with the orientation the view WILL have when the
        pending orbit lands (defer mode's live feedback -- the real viewport only regens on gesture
        pause; module docstring / notes 8.13). Any failure disables the overlay, never the driving."""
        if self._overlay_dead or self._dir is None:
            return
        try:
            if self._overlay is None:
                from .acad_overlay import OrbitOverlay
                self._overlay = OrbitOverlay()
            if not self._overlay_shown:
                try:
                    hwnd = int(acad.HWND)        # AcadApplication.HWND (verified live)
                except Exception:
                    hwnd = 0                     # overlay centres on the foreground window instead
                self._overlay.show(hwnd)
                self._overlay_shown = True
            po = self._pending_orbit
            self._overlay.update(self._rotate_dir(self._dir, po[0], po[1], po[2], self._scheme))
        except Exception as exc:
            self._overlay_dead = True
            self._overlay_shown = False
            self._warn_once("overlay", exc)

    def _overlay_hide(self):
        if self._overlay is not None and self._overlay_shown:
            try:
                self._overlay.hide()
            except Exception:
                pass
        self._overlay_shown = False

    def _warn_once(self, what, exc):
        """Log a camera op's first failure (so it's diagnosable) without spamming, and without
        disconnecting -- the other ops and the Update keep working."""
        if what not in self._warned:
            self._warned.add(what)
            self._log.info(f"autocad: {what} step failed and is being skipped ({exc!r})")
