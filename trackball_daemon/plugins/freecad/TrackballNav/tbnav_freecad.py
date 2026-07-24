# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""TrackballNav -- FreeCAD add-on (the FreeCAD-coupled half).

Connects to the Trackball Daemon's local nav broker (127.0.0.1) and drives the active 3D
view's Coin camera with the orbit/pan/zoom deltas it streams. A background socket thread
reads the broker into a queue; a PySide ``QTimer`` on FreeCAD's MAIN thread drains it and
touches the camera (Coin/Qt are main-thread-only).

This module is imported by ``InitGui.py`` (a thin shim) and started via :func:`start`. The
pure camera math lives in :mod:`tbnav_camera` (no FreeCAD imports -> unit-testable headless).

Two FreeCAD gotchas shaped this file (see docs/apps/freecad.md):
  * FreeCAD execs ``InitGui.py`` with SEPARATE globals/locals, so a function defined there
    can't see module-level names. The fix is to keep ALL logic in this imported module (a
    normal namespace) and have InitGui.py only ``import`` + call :func:`start`.
  * Doing Qt work before ``FreeCAD.GuiUp`` is True can crash FreeCAD, so :func:`start`
    schedules :func:`_boot` via ``QTimer.singleShot`` and ``_boot`` re-checks ``GuiUp``.
"""
import json
import os
import queue
import socket
import threading
import time
import traceback

import tbnav_camera as cammath

ADDIN_VERSION = "0.1.14"         # keep in sync with version.json
_DEFAULT_PORT = 47900
STARTUP_DELAY_MS = 1500          # defer boot so the GUI is fully up (FreeCAD.GuiUp race)
PUMP_MS = 11                     # ~90 Hz main-thread queue drain
PIVOT_HOLD_IDLE = 0.5            # fallback for adv.orbit_hold_sec / adv.zoom_hold_sec
OBJ_CACHE_SEC = 0.5              # object bounding-box centre cache lifetime
BBOX_MARGIN = 0.10               # accept a Screen Center hit inside bbox + this * diagonal
CURSOR_HOOK_CHECK_SEC = 0.5         # how often the pump re-checks the cursor observer's view binding
CURSOR_Y_FLIP = False           # SoLocation2Event.getPosition() and getObjectInfo() both use Coin's
                                 # BOTTOM-left pixel origin, so the cached pixel feeds getObjectInfo
                                 # unflipped. Flip to True (y' = getSize()[1] - y) if a FreeCAD/Coin
                                 # change ever makes cursor hits land vertically mirrored.

# --- runtime state ---------------------------------------------------------------------
_stop = threading.Event()
_q = queue.Queue()
_started = False
_reader_thread = None
_timer = None
_QtCore = None
_QT_FLAVOUR = "?"
_host = "?"

# `screen_center`/`cursor`: raycast under the viewport center / mouse cursor once per
# gesture and HOLD it, so that point stays put while orbiting. Invalidated on pan/zoom or after an
# idle gap.
_gesture = {"t": 0.0, "pivot": None}
_zoom_gesture = {"pivot": None}   # "to_cursor" zoom's own per-gesture hold (reset on orbit/pan)
# Fixed-horizon transition tracker: None until the first frame so startup in turntable does not
# level the view; only a real free->turntable switch does.
_horizon = {"fixed": None}
_obj_cache = {"t": 0.0, "center": None, "bbox": None}
_last_scheme = {"v": None}

# Live cursor pixel (Coin viewport coords, bottom-left origin), cached by a passive
# SoLocation2Event observer on the active view -- the pump reads it on demand. `view` is the view
# the observer is currently registered on (View3DInventorPy identity is stable: FreeCAD caches one
# Python object per MDI view, so `is` detects a view change).
_cursor = {"px": None, "t": 0.0}
_cursor_hook = {
    "view": None,
    "checked": 0.0,
    "leave_filter": None,
    "leave_widgets": [],
}


# ======================================================================================
# Logging (file-based, rate-limited) -- mirrors the Fusion/Blender add-ons
# ======================================================================================
def _log(msg):
    try:
        path = os.path.join(os.environ.get("APPDATA", ""), "TrackballDaemon", "freecad_addin.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


_last_err = {"t": 0.0, "s": ""}


def _log_apply_error(tb):
    now = time.time()
    if tb != _last_err["s"] or now - _last_err["t"] > 5.0:
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
            return int(json.load(f).get("port", _DEFAULT_PORT))
    except Exception:
        return _DEFAULT_PORT


# ======================================================================================
# FreeCAD view / camera plumbing (MAIN THREAD ONLY)
# ======================================================================================
def _active_view():
    """The active 3D view (View3DInventorPy), or None. Guards against no document and against
    the active MDI view being a spreadsheet / TechDraw page (no camera node)."""
    try:
        import FreeCADGui as Gui
    except Exception:
        return None
    gdoc = Gui.ActiveDocument
    if gdoc is None:
        return None
    try:
        view = gdoc.ActiveView
    except Exception:
        return None
    if view is None or not hasattr(view, "getCameraNode"):
        return None
    try:
        if view.getCameraNode() is None:
            return None
    except Exception:
        _log_rl("getcam", "getCameraNode failed (Coin SWIG library not loaded?) -> no view")
        return None
    return view


def _read_camera(view):
    """Read the live Coin camera into a pure tbnav_camera.Camera + return the Coin node."""
    node = view.getCameraNode()
    pos = node.position.getValue().getValue()            # SbVec3f -> (x,y,z)
    ori = node.orientation.getValue().getValue()         # SbRotation -> (x,y,z,w)
    focal = float(node.focalDistance.getValue())
    try:
        is_ortho = (view.getCameraType() == "Orthographic")
    except Exception:
        is_ortho = hasattr(node, "height")
    height = float(node.height.getValue()) if (is_ortho and hasattr(node, "height")) else None
    hangle = (float(node.heightAngle.getValue())
              if (not is_ortho and hasattr(node, "heightAngle")) else None)
    return cammath.Camera(pos, ori, focal, height, hangle, is_ortho), node


def _write_camera(view, node, camera):
    """Write a tbnav_camera.Camera back onto the Coin node and force a repaint (an idle view
    won't refresh on its own when driven from a timer -- the Fusion vp.refresh() lesson)."""
    node.position.setValue(camera.position[0], camera.position[1], camera.position[2])
    o = camera.orientation
    node.orientation.setValue(o[0], o[1], o[2], o[3])
    node.focalDistance.setValue(camera.focal)
    if camera.is_ortho and camera.height is not None and hasattr(node, "height"):
        node.height.setValue(camera.height)
    elif (not camera.is_ortho) and camera.height_angle is not None and hasattr(node, "heightAngle"):
        node.heightAngle.setValue(camera.height_angle)
    try:
        view.redraw()
    except Exception:
        pass


# ======================================================================================
# Pivot resolution (object bbox / selection / screen-centre raycast)
# ======================================================================================
def _obj_bbox(obj):
    """A FreeCAD BoundBox for one object (Part Shape or Mesh), or None."""
    try:
        sh = getattr(obj, "Shape", None)
        if sh is not None and not sh.isNull():
            return sh.BoundBox
    except Exception:
        pass
    try:
        m = getattr(obj, "Mesh", None)
        if m is not None:
            return m.BoundBox
    except Exception:
        pass
    return None


def _is_nested_geo_object(obj):
    """True when ``obj`` is geometrically owned by a Part/Body container.

    A nested feature's Shape.BoundBox is in the container's local coordinates, while the top-level
    container exposes the correctly transformed aggregate shape. Including both is what pulled the
    project bounds toward the origin for placed Parts/Bodies.
    """
    try:
        return obj.getParentGeoFeatureGroup() is not None
    except Exception:
        return False


def _bbox_limits(obj, world=False):
    """Return an object's bbox as ``(min_xyz, max_xyz)``.

    Top-level Shape/Mesh bounds already include their placement. For a selected nested feature,
    apply only its parent-container transform so selection pivots use world coordinates too.
    """
    b = _obj_bbox(obj)
    if b is None:
        return None
    try:
        if not b.isValid():
            return None
    except Exception:
        pass
    lo = (float(b.XMin), float(b.YMin), float(b.ZMin))
    hi = (float(b.XMax), float(b.YMax), float(b.ZMax))
    if not world or not _is_nested_geo_object(obj):
        return lo, hi
    try:
        import FreeCAD as App
        parent_placement = obj.getGlobalPlacement() * obj.Placement.inverse()
        points = []
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    p = parent_placement.multVec(App.Vector(x, y, z))
                    points.append((float(p.x), float(p.y), float(p.z)))
        return (tuple(min(p[i] for p in points) for i in range(3)),
                tuple(max(p[i] for p in points) for i in range(3)))
    except Exception:
        return lo, hi


def _doc_object_bbox(doc):
    """Aggregate (center, (min,max)) over every object with a bounding box, or (None, None)."""
    mn = [None, None, None]
    mx = [None, None, None]
    found = False
    for obj in getattr(doc, "Objects", []) or []:
        # App::Part / PartDesign::Body already expose a transformed aggregate Shape. Their child
        # features expose local-space bounds and must not be unioned a second time.
        if _is_nested_geo_object(obj):
            continue
        limits = _bbox_limits(obj)
        if limits is None:
            continue
        lo3, hi3 = limits
        found = True
        for i, (lo, hi) in enumerate(zip(lo3, hi3)):
            mn[i] = lo if mn[i] is None else min(mn[i], lo)
            mx[i] = hi if mx[i] is None else max(mx[i], hi)
    if not found:
        return None, None
    center = ((mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5)
    return center, (tuple(mn), tuple(mx))


def _object_center(doc):
    """Cached model bbox centre + bbox, recomputed at most every OBJ_CACHE_SEC."""
    now = time.time()
    if _obj_cache["center"] is not None and now - _obj_cache["t"] < OBJ_CACHE_SEC:
        return _obj_cache["center"], _obj_cache["bbox"]
    c, bb = _doc_object_bbox(doc)
    _obj_cache.update(t=now, center=c, bbox=bb)
    return c, bb


def _in_bbox(p, bbox):
    if bbox is None:
        return True
    mn, mx = bbox
    diag = ((mx[0] - mn[0]) ** 2 + (mx[1] - mn[1]) ** 2 + (mx[2] - mn[2]) ** 2) ** 0.5
    m = BBOX_MARGIN * diag
    return all(mn[i] - m <= p[i] <= mx[i] + m for i in range(3))


def _screen_center_pivot(view, bbox):
    """Raycast the surface under the screen centre via getObjectInfo (FreeCAD does the pick).
    Returns a world point, or None to fall back to the model centre."""
    try:
        size = view.getSize()
        info = view.getObjectInfo((int(size[0] / 2), int(size[1] / 2)))
    except Exception:
        info = None
    if not info:
        _log_rl("vpivot", "screen-center-pivot: nothing under screen centre -> fallback chain")
        return None
    try:
        p = (float(info["x"]), float(info["y"]), float(info["z"]))
    except Exception:
        return None
    if not _in_bbox(p, bbox):
        _log_rl("vpivot", "screen-center-pivot: hit outside model bbox -> fallback chain")
        return None
    _log_rl("vpivot", "screen-center-pivot: surface hit -> (%.2f,%.2f,%.2f)" % p)
    return p


def _cursor_event_cb(event_cb):
    """Passive SoLocation2Event observer: cache the cursor's viewport pixel. Runs inside Coin's
    event traversal on the GUI thread -- do NOTHING here but read + store (never touch the camera,
    never raise, never setHandled(), so FreeCAD's own navigation still sees the event)."""
    try:
        pos = event_cb.getEvent().getPosition().getValue()
        _cursor["px"] = (int(pos[0]), int(pos[1]))
        _cursor["t"] = time.time()
    except Exception:
        pass


def _invalidate_cursor(reason):
    """Drop cursor-derived samples and holds when their viewport ownership is lost."""
    had_target = (_cursor["px"] is not None or _gesture["pivot"] is not None or
                  _zoom_gesture["pivot"] is not None)
    _cursor.update(px=None, t=0.0)
    _gesture["pivot"] = None
    _zoom_gesture["pivot"] = None
    if had_target:
        _log_rl("cursor-invalid", "cursor: invalidated (%s) -> fallback until re-entry" % reason)


def _qt_leave_event_type():
    if _QtCore is None:
        return None
    qevent = _QtCore.QEvent
    return getattr(getattr(qevent, "Type", qevent), "Leave", None)


def _make_cursor_leave_filter():
    """Create the Qt event filter after the host's PySide flavour has been selected."""
    if _QtCore is None:
        return None

    class CursorLeaveFilter(_QtCore.QObject):
        def eventFilter(self, watched, event):  # noqa: N802 - Qt virtual method spelling
            try:
                leave_type = _qt_leave_event_type()
                if leave_type is not None and event.type() == leave_type:
                    _invalidate_cursor("viewport_leave")
            except Exception:
                pass
            return False

    return CursorLeaveFilter()


def _detach_cursor_leave_filter():
    event_filter = _cursor_hook.get("leave_filter")
    if event_filter is not None:
        for widget in list(_cursor_hook.get("leave_widgets") or []):
            try:
                widget.removeEventFilter(event_filter)
            except Exception:
                pass
    _cursor_hook["leave_widgets"] = []
    _cursor_hook["leave_filter"] = None


def _ensure_cursor_leave_filter():
    """Watch FreeCAD's real Quarter viewport receivers for Qt Leave events.

    Coin publishes pointer movement only while the mouse is inside the 3D view. Qt owns the missing
    leave boundary, so install one passive filter on each live 3D viewport receiver. Clearing extra
    state is fail-safe: the next in-view SoLocation2Event immediately repopulates the pixel.
    """
    if _QtCore is None:
        return
    try:
        import FreeCADGui as Gui
        main_window = Gui.getMainWindow()
        candidates = []
        for obj in main_window.findChildren(_QtCore.QObject):
            try:
                class_name = str(obj.metaObject().className())
            except Exception:
                continue
            if "Quarter" not in class_name and "View3DInventor" not in class_name:
                continue
            try:
                receiver = obj.viewport() if callable(getattr(obj, "viewport", None)) else obj
            except Exception:
                receiver = obj
            if receiver is not None and not any(receiver is item for item in candidates):
                candidates.append(receiver)
        current = list(_cursor_hook.get("leave_widgets") or [])
        if (len(current) == len(candidates) and
                all(any(old is new for new in candidates) for old in current)):
            return
        _detach_cursor_leave_filter()
        if not candidates:
            return
        event_filter = _make_cursor_leave_filter()
        if event_filter is None:
            return
        installed = []
        for widget in candidates:
            try:
                widget.installEventFilter(event_filter)
                installed.append(widget)
            except Exception:
                pass
        if installed:
            _cursor_hook["leave_filter"] = event_filter
            _cursor_hook["leave_widgets"] = installed
            _log("cursor: Qt leave filter registered on %d 3D viewport(s)" % len(installed))
    except Exception:
        _log_rl("ptrleave", "cursor: could not register the viewport-leave filter")


def _ensure_cursor_hook(view):
    """Keep the SoLocation2Event observer registered on the CURRENT active view (main thread).
    Re-registers when the active view changes; a cached pixel from the old view is dropped
    (its coordinates are meaningless in the new one)."""
    _ensure_cursor_leave_filter()
    if view is _cursor_hook["view"]:
        return
    try:
        from pivy import coin
    except Exception:
        return                                # no Coin -> no cursor pivot (falls back)
    old = _cursor_hook["view"]
    if old is not None:
        try:
            old.removeEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), _cursor_event_cb)
        except Exception:
            pass
    _invalidate_cursor("view_change")
    try:
        view.addEventCallbackPivy(coin.SoLocation2Event.getClassTypeId(), _cursor_event_cb)
        _cursor_hook["view"] = view
        _log("cursor: SoLocation2Event observer registered on the active 3D view")
    except Exception:
        _cursor_hook["view"] = None
        _log_rl("ptrhook", "cursor: could not register the SoLocation2Event observer "
                           "-> 'cursor' pivot will fall back")


def _cursor_pivot(view, bbox):
    """Raycast the surface under the LIVE mouse cursor (the observer-cached pixel) via
    getObjectInfo -- the same pick _screen_center_pivot does, fed the cursor pixel instead of
    the centre. Returns a world point, or None to fall back (no cursor seen over this view yet /
    off-model / outside the model bbox)."""
    px = _cursor["px"]
    if px is None:
        _log_rl("ppivot", "cursor-pivot: no cursor pixel cached yet -> fallback")
        return None
    x, y = px
    if CURSOR_Y_FLIP:
        try:
            y = int(view.getSize()[1]) - y
        except Exception:
            pass
    try:
        info = view.getObjectInfo((int(x), int(y)))
    except Exception:
        info = None
    if not info:
        _log_rl("ppivot", "cursor-pivot: nothing under the cursor -> fallback")
        return None
    try:
        p = (float(info["x"]), float(info["y"]), float(info["z"]))
    except Exception:
        return None
    if not _in_bbox(p, bbox):
        _log_rl("ppivot", "cursor-pivot: hit outside model bbox -> fallback")
        return None
    _log_rl("ppivot", "cursor-pivot: surface hit -> (%.2f,%.2f,%.2f)" % p)
    return p


def _cursor_depth_point(view):
    """FreeCAD's focal-plane unprojection for empty-space To Cursor zoom."""
    px = _cursor["px"]
    if px is None:
        return None
    x, y = px
    if CURSOR_Y_FLIP:
        try:
            y = int(view.getSize()[1]) - y
        except Exception:
            pass
    try:
        point = view.getPoint((int(x), int(y)))
        return (float(point[0]), float(point[1]), float(point[2]))
    except Exception:
        return None


def _selection_center(doc):
    """Mean of the bounding-box centres of the current selection, or None."""
    try:
        import FreeCADGui as Gui
        sel = Gui.Selection.getSelection()
    except Exception:
        return None
    pts = []
    for obj in sel:
        limits = _bbox_limits(obj, world=True)
        if limits is not None:
            lo, hi = limits
            pts.append(tuple((lo[i] + hi[i]) * 0.5 for i in range(3)))
    if not pts:
        return None
    n = float(len(pts))
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)


def _orbit_pivot(op, view, doc, camera, idle, sel_override=True, candidates=None,
                 hold_sec=PIVOT_HOLD_IDLE):
    """Resolve the selected method followed by the daemon-expanded global candidate chain."""
    selected = _selection_center(doc) if sel_override and op != "camera" else None
    if selected is not None:
        return selected
    if _gesture["pivot"] is not None and idle <= hold_sec:
        return _gesture["pivot"]
    center, bbox = _object_center(doc)
    legacy = candidates is None
    for method in (candidates if candidates is not None else [op, "object"]):
        if method == "origin":
            point = (0.0, 0.0, 0.0)
        elif method == "screen_center":
            point = _screen_center_pivot(view, bbox)
        elif method == "cursor":
            point = _cursor_pivot(view, bbox)
        elif method == "selection":
            point = _selection_center(doc)
        elif method == "object":
            point = center
        else:                                      # camera / cursor_3d unsupported in FreeCAD
            continue
        if point is not None:
            _gesture["pivot"] = point
            return point
    return cammath.look_at(camera) if legacy else None


def _zoom_pivot(zm, view, doc, idle, sel_override=True, hold_sec=PIVOT_HOLD_IDLE):
    if zm == "to_object":
        center, _bb = _object_center(doc)
        return center                      # may be None -> zoom about the look-at
    if zm == "to_cursor":
        if sel_override:
            selected = _selection_center(doc)
            if selected is not None:
                return selected
        # Keep the surface point under the MOUSE POINTER fixed on screen while zooming
        # (cammath.zoom already holds an off-centre pivot). Same per-gesture hold as the orbit
        # pivot, in its own slot so orbit/zoom gestures don't clobber each other's pivot.
        if _zoom_gesture["pivot"] is None or idle > hold_sec:
            _center, bbox = _object_center(doc)
            _zoom_gesture["pivot"] = (_cursor_pivot(view, bbox) or
                                        _cursor_depth_point(view))
        return _zoom_gesture["pivot"]
    return None                            # to_center -> look-at (screen centre)


# ======================================================================================
# Frame application (MAIN THREAD)
# ======================================================================================
def _apply(view, frame, idle):
    """Apply one nav frame to the active view's camera. Exactly one of orbit / pan / zoom is
    non-zero per frame (the daemon gates them on Shift)."""
    o = frame.get("o", [0.0, 0.0, 0.0])
    p = frame.get("p", [0.0, 0.0])
    z = float(frame.get("z", 0.0))
    op = frame.get("op", "screen_center")
    style = frame.get("os", "free")
    zm = frame.get("zm", "to_center")
    adv = frame.get("adv") or {}
    sel_override = bool(adv.get("selection_overrides_pivot", True))
    pivot_candidates = adv.get("orbit_pivot_candidates") or [op]
    orbit_hold = max(0.0, min(10.0, float(adv.get("orbit_hold_sec", PIVOT_HOLD_IDLE))))
    zoom_hold = max(0.0, min(10.0, float(adv.get("zoom_hold_sec", PIVOT_HOLD_IDLE))))

    sig = (op, style, zm)
    if sig != _last_scheme["v"]:
        _last_scheme["v"] = sig
        _log("scheme: pivot=%s style=%s zoom=%s" % sig)

    import FreeCAD as App
    doc = App.ActiveDocument
    camera, node = _read_camera(view)

    changed = False
    # Level ONCE when the style transitions free->turntable: remove existing roll
    # instead of locking the tilted horizon. Transitions only -- prev None (fresh add-on) never
    # levels, and ordinary turntable frames never re-level.
    fixed = (style == "turntable")
    prev = _horizon["fixed"]
    _horizon["fixed"] = fixed
    if fixed and prev is False and bool(adv.get("level_horizon_on_entry", True)):
        if cammath.level_horizon(camera):
            _log("horizon: leveled on turntable entry")
            changed = True
    if o[0] or o[1] or o[2]:
        _log_rl("rx_orbit", "rx orbit o=(%.4f,%.4f,%.4f) op=%s os=%s" % (o[0], o[1], o[2], op, style))
        _zoom_gesture["pivot"] = None            # view rotates -> next zoom re-raycasts its pivot
        pivot = _orbit_pivot(op, view, doc, camera, idle, sel_override=sel_override,
                             candidates=pivot_candidates, hold_sec=orbit_hold)
        if pivot is None:
            if changed:                      # deliver the entry-leveling even though the
                _write_camera(view, node, camera)   # pivot chain produced no orbit frame
            return
        cammath.orbit(camera, o, style == "turntable", pivot)
        changed = True
    elif p[0] or p[1]:
        _log_rl("rx_pan", "rx pan p=(%.4f,%.4f)" % (p[0], p[1]))
        _gesture["pivot"] = None                 # view moved -> next orbit re-raycasts its pivot
        cammath.pan(camera, p[0], p[1])
        changed = True
    elif z:
        _log_rl("rx_zoom", "rx zoom z=%.4f zm=%s" % (z, zm))
        _gesture["pivot"] = None
        cammath.zoom(camera, z, _zoom_pivot(zm, view, doc, idle, sel_override=sel_override,
                                            hold_sec=zoom_hold))
        changed = True

    if changed:
        _write_camera(view, node, camera)
        _log_rl("applied", "applied op=%s ortho=%s pos=(%.2f,%.2f,%.2f)"
                % (op, camera.is_ortho, camera.position[0], camera.position[1], camera.position[2]))


def _pump():
    """Main-thread pump: drain queued frames and apply them to the active 3D view."""
    try:
        # Keep the passive cursor observer bound to the active view even while idle, so the
        # cursor pixel is already cached when the FIRST gesture of a session starts. Rate-limited
        # -- the common tick does one time comparison.
        now = time.time()
        if now - _cursor_hook["checked"] > CURSOR_HOOK_CHECK_SEC:
            _cursor_hook["checked"] = now
            v = _active_view()
            if v is not None:
                _ensure_cursor_hook(v)
        frames = []
        while True:
            try:
                frames.append(_q.get_nowait())
            except queue.Empty:
                break
        if not frames:
            return
        now = time.time()
        idle = now - _gesture["t"]                # frames arrive only during motion -> gap = gesture end
        _gesture["t"] = now
        view = _active_view()
        if view is None:
            _log_rl("noview", "frames received but no active 3D view -> ignoring")
            return
        for fr in frames:
            _apply(view, fr, idle)
            idle = 0.0                            # only the first frame of a burst ends the gesture
    except Exception:
        _log_apply_error(traceback.format_exc())


# ======================================================================================
# Broker reader (background thread) -- mirrors the Fusion/Blender socket loop
# ======================================================================================
def _reader():
    port = _bridge_port()
    hello = json.dumps({"type": "hello", "app": "freecad", "version": ADDIN_VERSION,
                        "host": _host, "pid": os.getpid()}) + "\n"
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
                            _q.put_nowait(json.loads(line.decode("utf-8")))
                        except Exception:
                            pass
        except Exception:
            time.sleep(1.5)                       # daemon not up yet / dropped -> retry
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass


# ======================================================================================
# Bootstrap (called from InitGui.py)
# ======================================================================================
def _import_qtcore():
    try:
        from PySide6 import QtCore
        return QtCore, "PySide6"
    except Exception:
        pass
    try:
        from PySide2 import QtCore
        return QtCore, "PySide2"
    except Exception:
        pass
    try:
        from PySide import QtCore                 # FreeCAD's compatibility shim
        return QtCore, "PySide(shim)"
    except Exception:
        pass
    return None, "none"


def _boot():
    """Start the reader thread + main-thread pump, once the GUI is fully up. Re-checks
    FreeCAD.GuiUp (Qt work before it is True can crash FreeCAD)."""
    global _reader_thread, _timer, _host
    try:
        import FreeCAD as App
    except Exception:
        return
    if not getattr(App, "GuiUp", False):
        _QtCore.QTimer.singleShot(300, _boot)     # not ready yet -> retry shortly
        return
    try:
        _host = ".".join(str(x) for x in App.Version()[:3])
    except Exception:
        _host = "?"
    # Load Coin's SWIG library NOW. view.getCameraNode() returns a pivy/Coin SWIG object, and
    # touching it raises "RuntimeError: No SWIG wrapped library loaded" unless pivy.coin has been
    # imported in this session first. Without this, _active_view() silently fails every frame.
    try:
        from pivy import coin  # noqa: F401
        _log("boot: pivy.coin loaded")
    except Exception:
        _log("boot: WARNING could not import pivy.coin -- camera access will fail")
    _log("boot: TrackballNav v%s starting (FreeCAD %s, %s)" % (ADDIN_VERSION, _host, _QT_FLAVOUR))
    _stop.clear()
    _reader_thread = threading.Thread(target=_reader, name="trackball-nav-reader", daemon=True)
    _reader_thread.start()
    _timer = _QtCore.QTimer()
    _timer.setInterval(PUMP_MS)
    _timer.timeout.connect(_pump)
    _timer.start()
    try:
        app = _QtCore.QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(stop)         # close the socket cleanly on FreeCAD quit
    except Exception:
        pass
    _log("boot: reader thread + %d ms pump timer started" % PUMP_MS)


def start():
    """Entry point called by InitGui.py. Idempotent (a FreeCAD 'Reload' won't double-start)."""
    global _started, _QtCore, _QT_FLAVOUR
    if _started:
        return
    _started = True
    _QtCore, _QT_FLAVOUR = _import_qtcore()
    if _QtCore is None:
        _log("start: no PySide (PySide6/PySide2/shim) available -> cannot run")
        return
    _log("start: scheduling boot in %d ms (TrackballNav v%s)" % (STARTUP_DELAY_MS, ADDIN_VERSION))
    _QtCore.QTimer.singleShot(STARTUP_DELAY_MS, _boot)


def stop():
    _stop.set()
    _detach_cursor_leave_filter()
    try:
        if _timer is not None:
            _timer.stop()
    except Exception:
        pass
