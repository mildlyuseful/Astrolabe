"""TrackballNav -- Autodesk Fusion 360 add-in.

Connects to the Trackball Daemon's local nav broker (127.0.0.1) and drives the active
viewport camera with the orbit/pan/zoom deltas it streams. A background socket thread reads
the broker and fires a Fusion CustomEvent; the event handler applies the camera change on
Fusion's main thread (the Fusion API is main-thread-only).

Install: copy this folder into
  %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\
Then in Fusion: Utilities -> Add-Ins (Shift+S) -> select "TrackballNav" -> Run, and tick
"Run on Startup". The daemon's "Set up" button does the copy for you.
"""
import adsk.core
import adsk.fusion
import json
import os
import socket
import threading
import time
import traceback

app = adsk.core.Application.get()
ui = app.userInterface if app else None

_EVENT_ID = "TrackballNavFrame"
_handlers = []
_stop = threading.Event()
_custom_event = None
_reader_thread = None

# --- tuning: Fusion's intrinsic axis orientation + baseline sensitivity. These bake in the
#     known-good defaults; the daemon's Per-App Bindings (gain 1.0 = this baseline) scale
#     from here, and the Invert checkboxes flip further. -------------------------------------
ORBIT_SCALE = (-1.0, -1.0, 1.0)  # orbit X/Y inverted (o[0]=right, o[1]=up, o[2]=fwd)
PAN_SIGN = (-1.0, -1.0)          # pan along (camera-right, camera-up); up-down negated
PAN_SCALE = 0.14                 # broker pan delta -> fraction of view extents (baseline pan feel)
ZOOM_SCALE = 0.25                # broker zoom delta -> fraction of view extents (baseline zoom feel)
ZOOM_SIGN = 1.0                  # twist->zoom direction

ADDIN_VERSION = "0.1.15"         # reported in the handshake so the daemon shows the LOADED version.
                                 # 0.1.15: real selection/origin pivots + selection override.
                                 # 0.1.14: scheme values renamed (pointer->cursor, cursor->selection,
                                 # to_pointer->to_cursor) to match the daemon v3 config migration.
                                 # 0.1.11: "pointer" orbit pivot + "to_pointer" zoom (orbit/zoom about
                                 # the surface under the MOUSE POINTER; GetCursorPos + screenToView +
                                 # viewToModelSpace ray -- see the POINTER PIVOT block below).
                                 # 0.1.12: DPI fix -- GetCursorPos is PHYSICAL px but screenToView's
                                 # INPUT is LOGICAL, so at 125% hits landed down-right of the cursor;
                                 # now divided by the monitor's effective DPI scale.
                                 # 0.1.13: range-check fix -- screenToView's OUTPUT is PHYSICAL
                                 # viewport px while vp.width/height are LOGICAL, so the old bounds
                                 # check wrongly REJECTED the right/bottom ~20% band ("fails to
                                 # target" there, user-reported); validate against vp.size * scale
                                 # and feed viewToModelSpace the PHYSICAL pixel unscaled. Keep in
                                 # sync with TrackballNav.manifest.

_last_err = {"t": 0.0, "s": ""}
_last_scheme = {"v": None}


def _log(msg):
    try:
        path = os.path.join(os.environ.get("APPDATA", ""), "TrackballDaemon", "fusion_addin.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _log_apply_error(tb):
    now = time.time()
    if tb != _last_err["s"] or now - _last_err["t"] > 5.0:    # rate-limit: don't spam every frame
        _last_err["s"], _last_err["t"] = tb, now
        _log("apply error: " + tb.strip().replace("\n", " | "))


_rl = {}


def _log_rl(key, msg, period=2.0):
    now = time.time()
    if now - _rl.get(key, 0.0) > period:
        _rl[key] = now
        _log(msg)


def _bridge_port():
    try:
        appdata = os.environ.get("APPDATA", "")
        with open(os.path.join(appdata, "TrackballDaemon", "bridge.json"), "r") as f:
            return int(json.load(f).get("port", 47900))
    except Exception:
        return 47900


_WORLD_UP = (0.0, 0.0, 1.0)              # Fusion is Z-up; turntable azimuth axis
_obj_cache = {"t": 0.0, "p": None}      # cached object bounding-box center (recomputed lazily)

# "view" orbit pivot: instead of the camera look-at target (which sits at an arbitrary depth on the
# optical axis), raycast down the screen centre to the REAL surface depth under the crosshair, so the
# thing you're looking at stays put during orbit -- like native right/middle-drag orbit. Computed ONCE
# per gesture and HELD; re-cast only after the view moves (pan/zoom) or the gesture ends (idle).
_gesture = {"t": 0.0, "pivot": None}    # last-frame time + held orbit pivot (Point3D | None)
_zoom_gesture = {"pivot": None}          # "to_cursor" zoom's own held pivot (reset on orbit/pan)
PIVOT_HOLD_IDLE = 0.35                   # s without frames that ends a gesture -> re-raycast next orbit
APERTURE_FRACS = (0.03, 0.10, 0.30)      # ray thickness tried, as a fraction of the view half-height
RAY_PUSHBACK = 8.0                       # start the ray this many half-heights behind screen-centre
BBOX_MARGIN = 0.10                       # accept a hit inside the bbox grown by this fraction of its diag

# --- CURSOR PIVOT (op == "cursor" / zm == "to_cursor"; pre-0.1.14 values "pointer"/"to_pointer"):
# orbit/zoom about the surface under the
# live MOUSE POINTER. Design decision -- Fusion's documented mouse-tracking hook is Command.mouseMove,
# but a Command is MODAL: while active it owns clicks, and the user activating ANY other tool (or
# Esc) terminates it, so an always-on tracker command would fight normal modeling (and auto-
# relaunching it would kill whatever tool the user just picked). Least-intrusive variant instead:
# read the cursor ON-DEMAND at gesture start -- the add-in runs inside Fusion's CPython, so ctypes
# GetCursorPos gives the screen pixel, Viewport.screenToView maps it into the viewport, and
# viewToModelSpace unprojects it for the ray. Zero UI hijack, and the pixel is always FRESH (no
# cache to go stale). If the GUI pass ever disproves the screenToView mapping, the fallbacks are the
# _client_view_pixel window mapping below, then a Command.mouseMove cache as the last resort.
#
# LIVE-GUI VERIFIED over two user passes at 125% scaling (2026-07-04). Pass 1 (0.1.11): the chain
# WORKS (tracking, per-gesture hold, fallbacks) but hits landed DOWN-RIGHT of the cursor. Pass 2
# (0.1.12): "works very precisely", EXCEPT the right/bottom band failed to target. Fitting the
# logged samples (view = 1.25*logical_in - physical_origin, exact across all of them) pinned the
# full coordinate model -- see _cursor_view_pixel's docstring: screenToView takes LOGICAL screen
# px and returns PHYSICAL viewport px; viewToModelSpace consumes PHYSICAL; vp.width/height are
# LOGICAL. 0.1.12 fixed the input scale; 0.1.13 fixed the OUTPUT bounds check (validate against
# vp.size * scale -- the logical bounds rejected correct physical values in the right/bottom ~20%,
# precisely the region the 0.1.11 bug used to map off-screen). STILL TO VERIFY LIVE: the 0.1.13
# right/bottom band re-check (hover near the right/bottom viewport edges and orbit; watch the
# "cursor map:" line), and mixed-DPI multi-monitor setups (the range check + object-centre
# fallback bound the damage).


def _active_design():
    """The active Design product, independent of the active workspace. app.activeProduct is None or a
    non-Design product outside the modeling workspace, so fall back to the active document's Design
    product. None if there's no design open."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None and app.activeDocument:
        design = adsk.fusion.Design.cast(
            app.activeDocument.products.itemByProductType("DesignProductType"))
    return design


def _object_center(fallback):
    """Model bounding-box center in world space, cached ~0.5 s. fallback if unavailable."""
    now = time.time()
    if _obj_cache["p"] is not None and now - _obj_cache["t"] < 0.5:
        return _obj_cache["p"]
    try:
        design = _active_design()
        if design is None:
            _log_rl("objc", "object-center: no Design product found -> using view target")
            return fallback
        bb = design.rootComponent.boundingBox
        if bb is None:
            _log_rl("objc", "object-center: rootComponent has no boundingBox -> using view target")
            return fallback
        c = adsk.core.Point3D.create((bb.minPoint.x + bb.maxPoint.x) * 0.5,
                                     (bb.minPoint.y + bb.maxPoint.y) * 0.5,
                                     (bb.minPoint.z + bb.maxPoint.z) * 0.5)
        _obj_cache["p"], _obj_cache["t"] = c, now
        return c
    except Exception:
        _log_rl("objc", "object-center FAILED: " + traceback.format_exc().strip().replace("\n", " | "))
        return fallback


def _selection_center():
    """Aggregate world-space bounding-box centre of Fusion's active selection, or None.

    ``activeSelections`` can contain bodies, occurrences, components, faces, sketches, and proxy
    objects. Most expose ``boundingBox`` directly; selection wrappers expose the selected object as
    ``entity``. Unsupported/non-geometric selections are skipped instead of stealing the pivot.
    """
    try:
        selections = app.activeSelections
        mn = [None, None, None]
        mx = [None, None, None]
        found = False
        for i in range(selections.count):
            item = selections.item(i)
            entity = getattr(item, "entity", item)
            bb = getattr(entity, "boundingBox", None)
            if bb is None:
                continue
            lo, hi = bb.minPoint, bb.maxPoint
            for axis, (a, b) in enumerate(((lo.x, hi.x), (lo.y, hi.y), (lo.z, hi.z))):
                mn[axis] = a if mn[axis] is None else min(mn[axis], a)
                mx[axis] = b if mx[axis] is None else max(mx[axis], b)
            found = True
        if found:
            xyz = [(mn[i] + mx[i]) * 0.5 for i in range(3)]
            return adsk.core.Point3D.create(xyz[0], xyz[1], xyz[2])
    except Exception:
        _log_rl("selc", "selection-center FAILED: " + traceback.format_exc().strip().replace("\n", " | "))
    return None


def _in_bbox(p, bb):
    """True if p lies within the model bbox grown by BBOX_MARGIN of its diagonal -- guards against a
    bogus/stray hit. Accepts the hit when the bbox is unavailable (can't validate)."""
    if bb is None:
        return True
    mn, mx = bb.minPoint, bb.maxPoint
    m = BBOX_MARGIN * (((mx.x - mn.x) ** 2 + (mx.y - mn.y) ** 2 + (mx.z - mn.z) ** 2) ** 0.5)
    return (mn.x - m <= p.x <= mx.x + m and
            mn.y - m <= p.y <= mx.y + m and
            mn.z - m <= p.z <= mx.z + m)


def _nearest_ray_hit(root, origin, direction, tol):
    """Nearest visible BRep-face hit along the ray origin + t*direction, within proximity `tol` (cm).
    Returns a cloned Point3D (safe to hold across frames) or None. Never raises -> a failed/absent
    pick API just falls back to the object centre.

    findBRepUsingRay(originPoint, rayDirection, entityType, proximityTolerance, visibleEntitiesOnly,
    hitPoints) -> ObjectCollection of faces; `hitPoints` is filled in parallel with the hit points.
    The Python API exposes NO Point3DList (the documented type), so pass an ObjectCollection -- it's
    accepted and .item(i) yields Point3D. proximityTolerance is the aperture (ray thickness); the
    origin is pushed well behind the model (see _screen_center_pivot), so the hit nearest the origin
    is the first/front surface."""
    try:
        hits = adsk.core.ObjectCollection.create()
        root.findBRepUsingRay(origin, direction,
                              adsk.fusion.BRepEntityTypes.BRepFaceEntityType,
                              tol, True, hits)
        best, best_d2 = None, None
        for i in range(hits.count):
            q = hits.item(i)
            dx, dy, dz = q.x - origin.x, q.y - origin.y, q.z - origin.z
            d2 = dx * dx + dy * dy + dz * dz
            if best_d2 is None or d2 < best_d2:
                best, best_d2 = q, d2
        return adsk.core.Point3D.create(best.x, best.y, best.z) if best else None
    except Exception:
        _log_rl("ray", "findBRepUsingRay FAILED: " + traceback.format_exc().strip().replace("\n", " | "))
        return None


def _raycast_pivot(design, origin, d, half_h, label):
    """Shared aperture-expanding raycast used by BOTH the screen-centre ("view") and the cursor
    pivots: try small->large ray thickness to catch thin/edge features, validate the hit against the
    model bbox, return the nearest surface Point3D or None (-> object-centre fallback)."""
    root = design.rootComponent
    bb = root.boundingBox
    for frac in APERTURE_FRACS:
        hit = _nearest_ray_hit(root, origin, d, frac * half_h)
        if hit is not None and _in_bbox(hit, bb):
            # rate-limit key = label, so a cursor re-cast is never silenced by a recent view-cast
            # log line (and vice versa) -- a suppressed hit line made a live log read confusingly
            _log_rl(label, "%s: surface hit (aperture=%.3f cm) -> (%.2f,%.2f,%.2f)"
                    % (label, frac * half_h, hit.x, hit.y, hit.z))
            return hit
    _log_rl(label + "_miss", "%s: no surface hit -> object-centre fallback" % label)
    return None


def _screen_center_pivot(cam):
    """Raycast down the screen centre (camera optical axis) to the real surface depth, like native
    right/middle-drag orbit. Returns the nearest surface point (Point3D) -- or None to fall back to
    the model centre."""
    design = _active_design()
    if design is None:
        return None
    eye, tgt = cam.eye, cam.target
    d = adsk.core.Vector3D.create(tgt.x - eye.x, tgt.y - eye.y, tgt.z - eye.z)  # view dir, into screen
    if d.length < 1e-9:
        return None
    d.normalize()
    half_h = max(cam.viewExtents * 0.5, 1e-4)          # on-screen half-extent in world units (cm)
    pb = RAY_PUSHBACK * half_h
    origin = adsk.core.Point3D.create(tgt.x - d.x * pb, tgt.y - d.y * pb, tgt.z - d.z * pb)  # behind, outside
    return _raycast_pivot(design, origin, d, half_h, "view-pivot")


def _cursor_screen_pos():
    """The OS mouse cursor in PHYSICAL screen pixels (ctypes; the add-in runs in-process), or None."""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (float(pt.x), float(pt.y))
    except Exception:
        pass
    return None


def _screen_scale(sx, sy):
    """Effective DPI scale of the monitor under (sx, sy), e.g. 1.25 at 125% display scaling.
    GetCursorPos speaks PHYSICAL pixels while Fusion's screenToView expects LOGICAL (Qt) screen
    coordinates -- feeding it physical px put the pivot a consistent distance DOWN-RIGHT of the
    cursor (user-observed live at 125%; error = 0.25 * the cursor's logical position)."""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT(int(sx), int(sy))
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)      # MONITOR_DEFAULTTONEAREST
        if hmon:
            dpix, dpiy = ctypes.c_uint(), ctypes.c_uint()
            if ctypes.windll.shcore.GetDpiForMonitor(               # MDT_EFFECTIVE_DPI = 0
                    hmon, 0, ctypes.byref(dpix), ctypes.byref(dpiy)) == 0 and dpix.value:
                return dpix.value / 96.0
    except Exception:
        pass
    return 1.0


def _client_view_pixel(sx, sy, vp, scale):
    """Fallback screen->viewport mapping: client coordinates of the WINDOW UNDER THE CURSOR,
    accepted only when that window's LOGICAL client size matches the viewport size (then it must
    be the 3D canvas). Guards against mapping into some other Fusion child window. Returns
    PHYSICAL client px -- viewToModelSpace consumes physical viewport pixels (see the coordinate
    model in _cursor_view_pixel)."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        hwnd = u32.WindowFromPoint(wintypes.POINT(int(sx), int(sy)))
        if not hwnd:
            return None
        rc = wintypes.RECT()
        if not u32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        if (abs(rc.right / scale - float(vp.width)) > 4 or
                abs(rc.bottom / scale - float(vp.height)) > 4):
            return None                    # not the 3D canvas
        pt = wintypes.POINT(int(sx), int(sy))
        if not u32.ScreenToClient(hwnd, ctypes.byref(pt)):
            return None
        return (float(pt.x), float(pt.y))
    except Exception:
        return None


def _cursor_view_pixel(vp):
    """The live mouse cursor as a viewport pixel (0,0 = viewport top-left; PHYSICAL px -- what
    viewToModelSpace consumes), or None when the cursor isn't over the viewport / no mapping
    works.

    FUSION'S MIXED COORDINATE MODEL -- fully determined by the user's two live passes at 125%
    scaling (fit view = 1.25*logical_in - physical_origin across every logged sample):
      * screenToView:      LOGICAL screen px IN -> PHYSICAL viewport px OUT
      * viewToModelSpace:  PHYSICAL viewport px IN ("works very precisely" only with these)
      * vp.width/height:   LOGICAL px
    So: divide the physical cursor by the monitor scale for screenToView's input (the 0.1.12
    down-right-offset fix), but range-validate its OUTPUT against vp.width*scale (0.1.13 -- the
    logical bounds wrongly rejected the right/bottom ~20% band: exactly the region the 0.1.11 bug
    used to map off-screen) and pass it through UNSCALED."""
    sp = _cursor_screen_pos()
    if sp is None:
        return None
    try:
        w, h = float(vp.width), float(vp.height)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    scale = _screen_scale(sp[0], sp[1])
    ws, hs = w * scale, h * scale          # PHYSICAL viewport bounds
    lx, ly = sp[0] / scale, sp[1] / scale
    try:
        v = vp.screenToView(adsk.core.Point2D.create(lx, ly))
        if 0.0 <= v.x <= ws and 0.0 <= v.y <= hs:
            _log_rl("ptrmap", "cursor map: screen=(%.0f,%.0f) scale=%.2f -> logical=(%.1f,%.1f) "
                              "-> view=(%.1f,%.1f) of %dx%d phys"
                    % (sp[0], sp[1], scale, lx, ly, v.x, v.y, ws, hs))
            return (float(v.x), float(v.y))
    except Exception:
        _log_rl("ptr_s2v", "cursor: screenToView failed -> trying the client-rect mapping")
    px = _client_view_pixel(sp[0], sp[1], vp, scale)
    if px is not None and 0.0 <= px[0] <= ws and 0.0 <= px[1] <= hs:
        _log_rl("ptrmap", "cursor map (client-rect): screen=(%.0f,%.0f) scale=%.2f -> view=(%.1f,%.1f) phys"
                % (sp[0], sp[1], scale, px[0], px[1]))
        return px
    return None


def _cursor_pivot(cam):
    """Raycast the surface under the LIVE mouse cursor -- the same pick as _screen_center_pivot,
    aimed through the cursor pixel instead of the optical axis. Ray construction: unproject the
    pixel with viewToModelSpace (its depth doesn't matter -- the point only AIMS the ray);
    perspective rays run from the eye through it, ortho rays run parallel to the view axis through
    it (pushed back like the centre ray). Returns a Point3D or None (-> object-centre fallback)."""
    design = _active_design()
    vp = app.activeViewport
    if design is None or not vp:
        return None
    px = _cursor_view_pixel(vp)
    if px is None:
        _log_rl("ppivot", "cursor-pivot: cursor not over the viewport (or mapping failed) -> fallback")
        return None
    try:
        pm = vp.viewToModelSpace(adsk.core.Point2D.create(px[0], px[1]))
    except Exception:
        _log_rl("ppivot", "cursor-pivot: viewToModelSpace FAILED -> fallback")
        return None
    eye, tgt = cam.eye, cam.target
    fwd = adsk.core.Vector3D.create(tgt.x - eye.x, tgt.y - eye.y, tgt.z - eye.z)
    if fwd.length < 1e-9:
        return None
    fwd.normalize()
    half_h = max(cam.viewExtents * 0.5, 1e-4)
    is_ortho = True
    try:
        is_ortho = cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType
    except Exception:
        pass
    if is_ortho:
        d = fwd                                          # parallel rays; pm sets the lateral offset
        pb = RAY_PUSHBACK * half_h
        origin = adsk.core.Point3D.create(pm.x - d.x * pb, pm.y - d.y * pb, pm.z - d.z * pb)
    else:
        d = adsk.core.Vector3D.create(pm.x - eye.x, pm.y - eye.y, pm.z - eye.z)
        if d.length < 1e-9:
            return None
        d.normalize()
        if d.x * fwd.x + d.y * fwd.y + d.z * fwd.z <= 1e-6:
            _log_rl("ppivot", "cursor-pivot: unprojected point behind the eye -> fallback")
            return None
        origin = adsk.core.Point3D.create(eye.x, eye.y, eye.z)
    return _raycast_pivot(design, origin, d, half_h, "cursor-pivot")


def _orbit_pivot(op, cam, tgt, idle, sel_override=True):
    """Pivot point for an orbit gesture:
      view            -> raycast down the screen centre to the real surface depth, computed ONCE per
                         gesture and HELD (so the point under the crosshair stays put) -- like native
                         right-drag orbit. Re-cast when the gesture ends (idle) or the view moves.
      cursor          -> raycast the surface under the LIVE MOUSE CURSOR (read fresh at gesture
                         start), same per-gesture hold + fallbacks as `view`.
      object / selection -> model bounding-box centre.
    Everything falls back to the model centre, then the view target, when nothing is available."""
    selected = _selection_center() if sel_override or op == "selection" else None
    if selected is not None:
        return selected
    if op == "origin":
        return adsk.core.Point3D.create(0.0, 0.0, 0.0)
    if op == "view":
        if _gesture["pivot"] is None or idle > PIVOT_HOLD_IDLE:
            _gesture["pivot"] = _screen_center_pivot(cam) or _object_center(tgt)
        return _gesture["pivot"]
    if op == "cursor":
        if _gesture["pivot"] is None or idle > PIVOT_HOLD_IDLE:
            _gesture["pivot"] = _cursor_pivot(cam) or _object_center(tgt)
        return _gesture["pivot"]
    if op in ("object", "selection"):
        return _object_center(tgt)
    return tgt


def _zoom_pivot(zm, cam, tgt, idle, sel_override=True):
    # "to_object" zooms toward the model center; "to_cursor" toward the surface under the mouse
    # cursor (the zoom branch already keeps an arbitrary P fixed on screen; per-gesture hold in its
    # own slot so orbit/zoom gestures don't clobber each other's pivot, miss -> view centre);
    # "to_center" keeps the view center.
    if zm == "to_object":
        return _object_center(tgt)
    if zm == "to_cursor":
        if sel_override:
            selected = _selection_center()
            if selected is not None:
                return selected
        if _zoom_gesture["pivot"] is None or idle > PIVOT_HOLD_IDLE:
            _zoom_gesture["pivot"] = _cursor_pivot(cam)
        return _zoom_gesture["pivot"] or tgt
    return tgt


def _apply(frame):
    """Apply one nav frame to the active viewport camera. Runs on the Fusion main thread.
    Exactly one of orbit / pan / zoom is non-zero per frame (the daemon gates them)."""
    try:
        o = frame.get("o", [0.0, 0.0, 0.0])
        p = frame.get("p", [0.0, 0.0])
        z = float(frame.get("z", 0.0))
        op = frame.get("op", "view")          # orbit pivot: view | object | cursor
        style = frame.get("os", "free")       # orbit style: free | turntable
        zm = frame.get("zm", "to_center")     # zoom mode:  to_center | to_object | to_cursor
        adv = frame.get("adv") or {}
        sel_override = bool(adv.get("selection_overrides_pivot", True))
        sig = (op, style, zm, sel_override)
        if sig != _last_scheme["v"]:           # confirm live scheme changes are received
            _last_scheme["v"] = sig
            _log("scheme received: pivot=%s style=%s zoom=%s sel_override=%s" % sig)

        vp = app.activeViewport
        if not vp:
            return
        cam = vp.camera
        eye, tgt, up = cam.eye, cam.target, cam.upVector

        fwd = adsk.core.Vector3D.create(tgt.x - eye.x, tgt.y - eye.y, tgt.z - eye.z)
        if fwd.length < 1e-9:
            return
        fwd.normalize()
        right = fwd.crossProduct(up)
        if right.length < 1e-9:
            return
        right.normalize()
        true_up = right.crossProduct(fwd)
        true_up.normalize()

        now = time.time()
        idle = now - _gesture["t"]            # frames only arrive during motion, so a gap = gesture end
        _gesture["t"] = now

        if o[0] or o[1] or o[2]:
            # ---- ORBIT: rotate eye + target + up about the chosen pivot ----
            _zoom_gesture["pivot"] = None     # view rotates -> the next zoom re-raycasts its pivot
            pivot = _orbit_pivot(op, cam, tgt, idle, sel_override=sel_override)
            dpt = ((pivot.x - tgt.x) ** 2 + (pivot.y - tgt.y) ** 2 + (pivot.z - tgt.z) ** 2) ** 0.5
            _log_rl("pivot", "orbit pivot=%s |P-T|=%.3f P=(%.2f,%.2f,%.2f) target=(%.2f,%.2f,%.2f)"
                    % (op, dpt, pivot.x, pivot.y, pivot.z, tgt.x, tgt.y, tgt.z))
            if style == "turntable":
                # azimuth about world up (from o[1]/yaw), elevation about camera right
                # (from o[0]/pitch); roll (o[2]) is dropped so the model never tilts.
                pairs = ((o[1] * ORBIT_SCALE[1], adsk.core.Vector3D.create(*_WORLD_UP)),
                         (o[0] * ORBIT_SCALE[0], right))
            else:
                pairs = ((o[0] * ORBIT_SCALE[0], right),
                         (o[1] * ORBIT_SCALE[1], true_up),
                         (o[2] * ORBIT_SCALE[2], fwd))
            rot = adsk.core.Matrix3D.create()
            for angle, axis in pairs:
                if angle:
                    m = adsk.core.Matrix3D.create()
                    m.setToRotation(angle, axis, pivot)
                    rot.transformBy(m)
            new_eye = eye.copy(); new_eye.transformBy(rot)
            new_tgt = tgt.copy(); new_tgt.transformBy(rot)
            new_up = up.copy(); new_up.transformBy(rot)
            cam.eye = new_eye
            cam.target = new_tgt
            cam.upVector = new_up

        elif p[0] or p[1]:
            # ---- PAN: shift eye + target in the view plane ----
            _gesture["pivot"] = None          # view moved -> the next orbit re-raycasts its pivot
            _zoom_gesture["pivot"] = None
            ps = cam.viewExtents * PAN_SCALE
            gx = PAN_SIGN[0] * p[0] * ps
            gy = PAN_SIGN[1] * p[1] * ps
            pan = adsk.core.Vector3D.create(
                right.x * gx + true_up.x * gy,
                right.y * gx + true_up.y * gy,
                right.z * gx + true_up.z * gy,
            )
            new_tgt = tgt.copy(); new_tgt.translateBy(pan)
            new_eye = eye.copy(); new_eye.translateBy(pan)
            cam.target = new_tgt
            cam.eye = new_eye

        elif z:
            # ---- ZOOM: scale view extents, keeping the pivot point P fixed on screen ----
            _gesture["pivot"] = None          # view moved -> the next orbit re-raycasts its pivot
            s = 1.0 - ZOOM_SIGN * z * ZOOM_SCALE
            if s < 0.01:
                s = 0.01
            P = _zoom_pivot(zm, cam, tgt, idle, sel_override=sel_override)
            ntgt = adsk.core.Point3D.create(P.x + (tgt.x - P.x) * s,
                                            P.y + (tgt.y - P.y) * s,
                                            P.z + (tgt.z - P.z) * s)
            d = adsk.core.Vector3D.create(ntgt.x - tgt.x, ntgt.y - tgt.y, ntgt.z - tgt.z)
            new_eye = eye.copy(); new_eye.translateBy(d)
            cam.target = ntgt
            cam.eye = new_eye
            cam.viewExtents = max(1e-4, cam.viewExtents * s)

        cam.isSmoothTransition = False
        vp.camera = cam
        # Fusion does not repaint an idle viewport when the camera is set from an add-in
        # (it would only update on the next user interaction), so force a redraw each frame.
        vp.refresh()
    except Exception:
        _log_apply_error(traceback.format_exc())


class _FrameHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            _apply(json.loads(args.additionalInfo))
        except Exception:
            pass


def _reader():
    port = _bridge_port()
    host = app.version if app else "?"
    # Report the ADD-IN version as "version" (shown in the daemon's tray) so you can confirm
    # which add-in build Fusion actually loaded; "host" carries the Fusion version.
    hello = json.dumps({"type": "hello", "app": "fusion360", "version": ADDIN_VERSION,
                        "host": host, "pid": os.getpid()}) + "\n"
    while not _stop.is_set():
        sock = None
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            sock.sendall(hello.encode("utf-8"))
            sock.settimeout(0.5)
            buf = b""
            while not _stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            app.fireCustomEvent(_EVENT_ID, line.decode("utf-8"))
                        except Exception:
                            pass
        except Exception:
            time.sleep(1.5)              # daemon not up yet / dropped -> retry
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass


def run(context):
    global _custom_event, _reader_thread
    try:
        _log("run: TrackballNav add-in v%s starting" % ADDIN_VERSION)
        _stop.clear()
        try:
            app.unregisterCustomEvent(_EVENT_ID)
        except Exception:
            pass
        _custom_event = app.registerCustomEvent(_EVENT_ID)
        handler = _FrameHandler()
        _custom_event.add(handler)
        _handlers.append(handler)
        _reader_thread = threading.Thread(target=_reader, daemon=True)
        _reader_thread.start()
    except Exception:
        if ui:
            ui.messageBox("TrackballNav failed to start:\n{}".format(traceback.format_exc()))


def stop(context):
    try:
        _stop.set()
        try:
            app.unregisterCustomEvent(_EVENT_ID)
        except Exception:
            pass
        _handlers.clear()
    except Exception:
        pass
