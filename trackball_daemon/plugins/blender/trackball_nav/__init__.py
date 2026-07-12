"""Trackball Nav -- Blender add-on.

Connects to the Trackball Daemon's local nav broker (127.0.0.1) and drives the active 3D
viewport (`RegionView3D`) with the orbit/pan/zoom deltas it streams. A background socket
thread reads the broker into a queue; a `bpy.app.timers` callback drains it and applies the
view change on Blender's main thread (bpy is main-thread-only -- the analogue of Fusion's
CustomEvent hop).

Why Blender gets its own, richer driver than the CAD add-ins: Blender's viewport is a
`RegionView3D` (view_location / view_rotation / view_distance / view_perspective) rather than
an eye+look-at camera, and it natively supports many navigation styles -- turntable vs
trackball, fly/walk, view roll, dolly-vs-zoom, lock-horizon, screen-center, orbit-around-
selection, camera-lock. Those are exposed in the daemon's Blender "Advanced" settings and
arrive here as an additive `"adv"` object on each broker frame (Fusion ignores it).

Install: the daemon copies this package to
  %APPDATA%\Blender Foundation\Blender\<ver>\scripts\addons\trackball_nav\
and (with your confirmation) drops scripts/startup/trackball_nav_startup.py to auto-enable it
on launch. Otherwise enable it manually: Preferences -> Add-ons -> search "Trackball".

The math helpers (`_view_axes`, `_apply_world_rotation`, `_orbit_R`, `_pan`, `_zoom`, ...)
take a duck-typed view object (anything with .view_location / .view_rotation / .view_distance)
so they can be unit-tested headless (`blender --background --python`) without a GUI viewport.
"""
bl_info = {
    "name": "Trackball Nav",
    "author": "Trackball Daemon",
    "version": (0, 1, 17),                # keep in sync with version.json + ADDIN_VERSION
    "blender": (4, 2, 0),
    "location": "View3D (driven by the Trackball Daemon)",
    "description": "Drive the 3D viewport from the Trackball Daemon (orbit/pan/zoom/roll/fly/walk).",
    "category": "3D View",
}

import json
import os
import queue
import socket
import threading
import time
import traceback

import bpy
from mathutils import Quaternion, Vector, Matrix

ADDIN_VERSION = "0.1.17"                   # 0.1.17: level horizon on fixed-horizon mode entry
                                           # (adv.level_horizon_on_entry; issue #2).
                                           # 0.1.16: immutable host baseline profile.
                                           # 0.1.15: per-action X/Y/Z source routing.
                                           # 0.1.12: selection_overrides_pivot is functional.
                                           # 0.1.10: 3D-cursor pivot value renamed cursor->cursor_3d
                                           # (daemon config v3; "cursor" now means under-the-mouse).
                                           # 0.1.11: under-mouse "cursor" pivot implemented via a
                                           # passive modal-operator mouse tracker (no on-demand
                                           # mouse getter exists in the bpy API -- verified).

# Host correction arrives in ``adv.host_baseline`` from the daemon's immutable profile registry.
# Camera math stays neutral so corrections cannot be double-applied here and in the daemon.
ORBIT_SCALE = (1.0, 1.0, 1.0)
PAN_SIGN = (1.0, 1.0)
PAN_SCALE = 1.0
ZOOM_SCALE = 1.0
ZOOM_SIGN = 1.0
DOLLY_SCALE = 1.0
FLY_MOVE = 1.0
WALK_MOVE = 1.0

DIST_MIN, DIST_MAX = 1e-3, 1e6  # view_distance clamp (Blender's own range is wide)
PIVOT_HOLD_IDLE = 0.35          # s without frames that ends a gesture -> re-raycast the screen-center pivot

_DEFAULT_PORT = 47900

# --- runtime state ---------------------------------------------------------------------
_stop = threading.Event()
_q = queue.Queue()
_reader_thread = None
_TIMER_INTERVAL = 1.0 / 90.0    # main-thread poll rate (cheap queue drain)

# Screen Center (`screen_center`): raycast the first surface under the viewport center once and
# HOLD it (so the point under the crosshair stays put during orbit). Invalidated on pan/zoom/idle.
# The under-mouse "cursor" pivot shares this hold slot (only one pivot is active at a time).
_gesture = {"t": 0.0, "pivot": None, "invalid": True}
# Fixed-horizon transition tracker (issue #2): None until the first frame (an add-on that starts
# up already in a fixed mode must NOT level -- only a real free->fixed switch does).
_horizon = {"fixed": None}
# Under-mouse "cursor" pivot: Blender has NO on-demand mouse getter (verified 2026-07-06 -- no
# mouse/cursor/pointer property on Window/Screen/Area/Region/RegionView3D/Context; Event.mouse_* only
# exists INSIDE a modal operator/event handler; Window has cursor SETTERS only). So a passive,
# window-wide modal operator (TRACKBALL_NAV_OT_mouse_tracker) caches the cursor's WINDOW-space
# position on every MOUSEMOVE and the timer maps it into the target region on demand. `win` is the
# window's stable C pointer (as_pointer(), identity-stable across Python wrapper churn). `ok` flips
# true after the first MOUSEMOVE. _tracker["gen"] supersedes stale trackers: modal operators are
# cancelled on file load, so load_post restarts one and bumps gen so any straggler self-cancels.
_cursor = {"win": None, "x": 0.0, "y": 0.0, "t": 0.0, "ok": False}
_tracker = {"gen": 0}
_last_target = {"key": None}    # stabilise which VIEW_3D we drive across ties
_last_scheme = {"v": None}
_host = "?"                     # Blender version string, captured on the main thread in register()
# Blender-shortcut nav-mode override: None => follow the daemon's Mode; "orbit"/"fly"/"walk" => the
# in-Blender shortcut (Alt+`) overrode it. Cleared automatically when the daemon's Mode changes, so
# the daemon dropdown re-takes control. _daemon_nav tracks the last Mode the daemon sent.
_mode_override = {"v": None}
_daemon_nav = {"v": None}
_addon_keymaps = []


# ======================================================================================
# Logging (file-based; rate-limited so we never spam every frame) -- mirrors the Fusion add-in
# ======================================================================================
def _log(msg):
    try:
        path = os.path.join(os.environ.get("APPDATA", ""), "TrackballDaemon", "blender_addin.log")
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
# Pure view math (duck-typed view object: .view_location Vector, .view_rotation Quaternion,
# .view_distance float). No bpy here -> unit-testable headless.
# ======================================================================================
def _view_axes(rv):
    """World-space right/up/forward(into screen)/back(toward eye) for the current view."""
    q = rv.view_rotation
    return (q @ Vector((1.0, 0.0, 0.0)),
            q @ Vector((0.0, 1.0, 0.0)),
            q @ Vector((0.0, 0.0, -1.0)),
            q @ Vector((0.0, 0.0, 1.0)))


def _eye(rv):
    """World-space eye position."""
    return rv.view_location + (rv.view_rotation @ Vector((0.0, 0.0, 1.0))) * rv.view_distance


def _orbit_R(rv, pitch, yaw, roll, turntable):
    """The per-frame world-space rotation Quaternion for an orbit step.
      free      -> rotate about the view's own right/up/fwd axes (roll allowed)
      turntable -> azimuth about WORLD Z, elevation about world-right, roll suppressed (horizon level)
    """
    right, up, fwd, _back = _view_axes(rv)
    if turntable:
        return Quaternion(Vector((0.0, 0.0, 1.0)), yaw) @ Quaternion(right, pitch)
    return Quaternion(fwd, roll) @ Quaternion(up, yaw) @ Quaternion(right, pitch)


def _level_horizon(rv):
    """Remove existing roll: rebuild view_rotation so camera-right is horizontal (perpendicular to
    world Z) while the view direction is unchanged. view_location (the orbit point) and
    view_distance are untouched, so the eye stays put too -- only the roll goes. Returns False in
    the degenerate straight-up/straight-down view, where 'roll' is indistinguishable from yaw and
    leveling is undefined (native turntable has the same singularity)."""
    _right, _up, fwd, _back = _view_axes(rv)
    right = fwd.cross(Vector((0.0, 0.0, 1.0)))
    if right.length < 1e-6:
        return False
    right.normalize()
    up = right.cross(fwd)
    # Column basis (X=right, Y=up, Z=back): rows below are (right_i, up_i, back_i).
    rv.view_rotation = Matrix((
        (right.x, up.x, -fwd.x),
        (right.y, up.y, -fwd.y),
        (right.z, up.z, -fwd.z),
    )).to_quaternion()
    return True


def _maybe_level_horizon(rv, nav_mode, style, adv):
    """Level ONCE when the effective mode transitions into a fixed-horizon mode (turntable orbit,
    lock-horizon, or walk) and the daemon's level_horizon_on_entry toggle is on. Transitions only:
    ordinary fixed-mode frames never re-level, so a horizon tilted by other means stays locked --
    the same contract as every other host (see docs)."""
    fixed = (nav_mode == "walk") or (
        nav_mode == "orbit" and (style == "turntable" or bool(adv.get("lock_horizon", False))))
    prev = _horizon["fixed"]
    _horizon["fixed"] = fixed
    if fixed and prev is False and bool(adv.get("level_horizon_on_entry", True)):
        if _level_horizon(rv):
            _log("horizon: leveled on fixed-horizon mode entry (nav=%s style=%s)"
                 % (nav_mode, style))


def _apply_world_rotation(rv, R, pivot):
    """Rotate the view by world-space R about `pivot` (a Vector, or None == view_location).
    Holds `pivot` fixed on screen: the eye = view_location + back*distance follows automatically."""
    rv.view_rotation = (R @ rv.view_rotation).normalized()
    if pivot is not None:
        rv.view_location = pivot + R @ (rv.view_location - pivot)


def _pan(rv, px, py, scale_with_distance):
    """Translate view_location in the view plane (feel is zoom-independent when scaled by distance)."""
    right, up, _fwd, _back = _view_axes(rv)
    k = PAN_SCALE * (rv.view_distance if scale_with_distance else 1.0)
    dx = PAN_SIGN[0] * px * k
    dy = PAN_SIGN[1] * py * k
    rv.view_location = rv.view_location + right * dx + up * dy


def _zoom(rv, z, pivot=None):
    """Scale view_distance (ortho scale follows). If `pivot` is given (zoom-to-point), shift
    view_location so that point stays put on screen."""
    factor = 1.0 - ZOOM_SIGN * z * ZOOM_SCALE
    factor = max(0.05, min(20.0, factor))
    if pivot is not None:
        rv.view_location = pivot + (rv.view_location - pivot) * factor
    rv.view_distance = max(DIST_MIN, min(DIST_MAX, rv.view_distance * factor))


def _dolly(rv, z):
    """Translate the eye along the view forward axis (a 'move through space' feel; perspective)."""
    _right, _up, fwd, _back = _view_axes(rv)
    rv.view_location = rv.view_location + fwd * (DOLLY_SCALE * z * rv.view_distance)


def _roll(rv, roll):
    """Roll about the view forward axis (pivot = view_location)."""
    _right, _up, fwd, _back = _view_axes(rv)
    _apply_world_rotation(rv, Quaternion(fwd, roll), None)


def _look(rv, pitch, yaw, roll, horizon_lock):
    """First-person look (fly/walk): rotate about the EYE so the camera turns in place. The roll/bank
    direction is set by the per-mode invert applied upstream in _apply (config invert.fly.bank etc.)."""
    eye = _eye(rv)
    R = _orbit_R(rv, pitch, yaw, 0.0 if horizon_lock else roll, horizon_lock)
    _apply_world_rotation(rv, R, eye)


def _horizontal(v):
    """v projected onto the world XY plane, normalised (zero vector if degenerate)."""
    h = Vector((v.x, v.y, 0.0))
    return h.normalized() if h.length > 1e-9 else Vector((0.0, 0.0, 0.0))


def _selection_median_from(locations):
    """Mean of a list of Vectors, or None if empty (pure helper for testing)."""
    if not locations:
        return None
    acc = Vector((0.0, 0.0, 0.0))
    for v in locations:
        acc = acc + v
    return acc / float(len(locations))


# ======================================================================================
# bpy-dependent helpers (main thread only)
# ======================================================================================
def _resolve_target():
    """The VIEW_3D to drive: the largest open 3D viewport (last-used wins ties), with its WINDOW
    region + region_3d + space. None when no 3D viewport is open. (Targeting 'under the cursor'
    needs a live mouse position unavailable from a timer -- see notes; we drive the active view.)"""
    best = None
    best_area = 0
    try:
        for win in bpy.context.window_manager.windows:
            screen = win.screen
            if not screen:
                continue
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                space = area.spaces.active
                rv = getattr(space, "region_3d", None)
                if rv is None:
                    continue
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region is None:
                    continue
                key = (id(win), id(area))
                score = area.width * area.height + (1 if key == _last_target["key"] else 0)
                if score > best_area:
                    best_area = score
                    best = (win, area, region, rv, space, key)
    except Exception:
        _log_rl("target", "target resolve FAILED: " + traceback.format_exc().strip().replace("\n", " | "))
        return None
    if best is not None:
        _last_target["key"] = best[5]
    return best


def _raycast_pixel(rv, region, x, y):
    """Raycast the surface under region pixel (x, y) (region bottom-left origin, like Blender's own
    `region_2d_*`). Returns a world Vector (a real geometry hit -- `scene.ray_cast` returns no
    sentinel, so no bbox gate is needed), or None. Shared by the screen-center centre and the
    under-mouse cursor pivots."""
    try:
        from bpy_extras import view3d_utils as v3d
        coord = (x, y)
        origin = v3d.region_2d_to_origin_3d(region, rv, coord)
        direction = v3d.region_2d_to_vector_3d(region, rv, coord)
        if origin is None or direction is None:
            return None
        depsgraph = bpy.context.evaluated_depsgraph_get()
        result, location, _n, _i, _o, _m = bpy.context.scene.ray_cast(depsgraph, origin, direction)
        if result:
            return location.copy()
    except Exception:
        _log_rl("ray", "ray_cast FAILED: " + traceback.format_exc().strip().replace("\n", " | "))
    return None


def _raycast_screen_center(rv, region):
    """Screen Center pivot: raycast the surface under the region centre. World Vector or None."""
    hit = _raycast_pixel(rv, region, region.width * 0.5, region.height * 0.5)
    _log_rl("vpivot", ("screen-center hit -> (%.2f,%.2f,%.2f)" % (hit.x, hit.y, hit.z)) if hit is not None
            else "screen-center: no surface under centre -> continue configured chain")
    return hit


def _region_pixel_from_window(region_x, region_y, region_w, region_h, mouse_x, mouse_y):
    """Map a WINDOW-space mouse position (bottom-left origin, as Event.mouse_x/y report) into
    REGION-space pixels for a region at (region_x, region_y) sized region_w x region_h. Returns
    (rx, ry) if the cursor is inside the region (a small margin absorbs edge rounding), else None
    (cursor is over another area/region -> no under-mouse pivot). Pure -> unit-testable headless."""
    rx = mouse_x - region_x
    ry = mouse_y - region_y
    m = 2.0
    if -m <= rx <= region_w + m and -m <= ry <= region_h + m:
        return (rx, ry)
    return None


def _cursor_region_pixel(region, win):
    """The cached OS-cursor position as REGION pixels for `region`, or None (no cursor cached yet /
    cursor is in a different window / cursor is outside this region). `win` is the target window."""
    c = _cursor
    if not c["ok"] or win is None or c["win"] != win.as_pointer():
        return None
    return _region_pixel_from_window(region.x, region.y, region.width, region.height, c["x"], c["y"])


def _raycast_cursor(rv, region, win):
    """Under-mouse pivot: raycast the surface under the LIVE cursor (its cached window-space position
    mapped into the region). World Vector, or None (no cursor cached / cursor outside this region /
    nothing under it)."""
    pix = _cursor_region_pixel(region, win)
    if pix is None:
        _log_rl("cpivot", "under-cursor: no cached cursor in this region -> continue configured chain")
        return None
    hit = _raycast_pixel(rv, region, pix[0], pix[1])
    _log_rl("cpivot", ("under-cursor hit @px(%.0f,%.0f) -> (%.2f,%.2f,%.2f)"
                       % (pix[0], pix[1], hit.x, hit.y, hit.z)) if hit is not None
            else "under-cursor: no surface under cursor px(%.0f,%.0f) -> continue configured chain"
                 % (pix[0], pix[1]))
    return hit


def _selection_median():
    """Median (mean of world origins) of the current selection, or None."""
    try:
        objs = bpy.context.selected_objects
        return _selection_median_from([ob.matrix_world.translation.copy() for ob in objs])
    except Exception:
        return None


def _cursor_location():
    try:
        return bpy.context.scene.cursor.location.copy()
    except Exception:
        return None


def _orbit_pivot(op, rv, region, idle, win=None, sel_override=True, candidates=None):
    """Resolve the first available pivot Vector from the canonical candidate list, or None.
      camera -> the EYE: turns the camera in place (look around), independent of how far the orbit
                   point/view_location happens to be. (Earlier this orbited view_location, which sits
                   far in front after fly/look or at a large view distance -> felt like orbiting an
                   arbitrary point; rotating about the eye is "turn the camera".)
      screen_center -> first surface under the viewport center (per-gesture HOLD)
      cursor    -> raycast under the MOUSE (per-gesture HOLD; requires the modal mouse tracker's
                   cached position)
      selection/object -> selection median   cursor_3d -> 3D cursor   origin -> world origin
    When ``sel_override`` is enabled, a non-empty selection wins over every external pivot. The
    camera mode remains a true turn-in-place operation, matching Unity/Godot/Rhino. Unavailable
    methods are skipped; None means the configured chain was exhausted."""
    if sel_override and op != "camera":
        selected = _selection_median()
        if selected is not None:
            return selected
    if _gesture["pivot"] is not None and not _gesture["invalid"] and idle <= PIVOT_HOLD_IDLE:
        return _gesture["pivot"]
    for method in (candidates or [op]):
        if method == "camera":
            point = _eye(rv)
        elif method == "screen_center":
            point = _raycast_screen_center(rv, region)
        elif method == "cursor":
            point = _raycast_cursor(rv, region, win)
        elif method in ("selection", "object"):
            point = _selection_median()
        elif method == "cursor_3d":
            point = _cursor_location()
        elif method == "origin":
            point = Vector((0.0, 0.0, 0.0))
        else:
            continue
        if point is not None:
            _gesture["pivot"] = point
            _gesture["invalid"] = False
            return point
    return None


def _sync_camera_to_view(rv, scene):
    """Drive the scene camera object from the view (camera at the eye, view's orientation). Blender
    ignores rv3d rotation while *in* CAMERA view, so we write the camera directly."""
    cam = scene.camera
    if cam is None:
        return
    cam.matrix_world = Matrix.Translation(_eye(rv)) @ rv.view_rotation.to_matrix().to_4x4()


# ======================================================================================
# Frame application (main thread)
# ======================================================================================
def _apply_orbit(win, rv, region, o, frame, adv, idle):
    style = frame.get("os", "free")
    lock = bool(adv.get("lock_horizon", False))
    twist_action = adv.get("twist_action", "roll")
    op = frame.get("op", "camera")
    pitch = o[0] * ORBIT_SCALE[0]
    yaw = o[1] * ORBIT_SCALE[1]
    twist = o[2] * ORBIT_SCALE[2]

    roll = 0.0
    if twist:                                      # un-shifted twist: roll | zoom | dolly | none
        if twist_action == "roll" and not lock:
            roll = twist                           # direction set by the per-mode invert (upstream)
        elif twist_action == "zoom":
            _zoom(rv, twist * float((adv.get("host_baseline") or {}).get("zoom", 1.0)))
        elif twist_action == "dolly":
            _dolly(rv, twist * float((adv.get("host_baseline") or {}).get("zoom", 1.0)))
        # "none" (or roll-while-locked): ignore twist

    if pitch or yaw or roll:
        turntable = (style == "turntable") or lock
        R = _orbit_R(rv, pitch, yaw, roll, turntable)
        pivot = _orbit_pivot(
            op, rv, region, idle, win,
            sel_override=bool(adv.get("selection_overrides_pivot", True)),
            candidates=adv.get("orbit_pivot_candidates") or [op])
        if pivot is not None:
            _apply_world_rotation(rv, R, pivot)


def _move_scale(base, speed, rv):
    # Movement scale for fly/walk. Floored at view_distance>=1 so it never vanishes when you fly in
    # close (scaling purely by view_distance made movement imperceptible at small distances).
    return base * speed * max(rv.view_distance, 1.0)


def _sgn(flag):
    """+1, or -1 when the per-mode invert flag is set."""
    return -1.0 if flag else 1.0


def _routed(values, sources, inversions, action, default):
    try:
        source = int(sources.get(action, default))
    except (TypeError, ValueError):
        source = default
    if source not in (0, 1, 2):
        source = default
    return values[source] * _sgn(inversions.get(action))


def _apply_action_routing(nav_mode, op, o, p, z, adv):
    """Route each mode action from X/Y/Z, then apply its independent invert flag."""
    inv = adv.get("invert") or {}
    axes = adv.get("axis_source") or {}
    rotation = list(o)
    movement = [p[0], p[1], z]
    if nav_mode == "fly":
        f, a = inv.get("fly", {}), axes.get("fly", {})
        o = [_routed(rotation, a, f, "pitch", 0), _routed(rotation, a, f, "yaw", 1),
             _routed(rotation, a, f, "bank", 2)]
        p = [_routed(movement, a, f, "strafe", 0),
             _routed(movement, a, f, "forward", 1)]
        z = _routed(movement, a, f, "vertical", 2)
    elif nav_mode == "walk":
        w, a = inv.get("walk", {}), axes.get("walk", {})
        o = [_routed(rotation, a, w, "pitch", 0), _routed(rotation, a, w, "yaw", 1), rotation[2]]
        p = [_routed(movement, a, w, "strafe", 0),
             _routed(movement, a, w, "forward", 1)]
        z = _routed(movement, a, w, "vertical", 2)
    else:
        ob, oa = inv.get("orbit", {}), axes.get("orbit", {})
        if op == "camera":
            camera, ca = inv.get("camera", {}), axes.get("camera", {})
            o = [_routed(rotation, ca, camera, "pitch", 0),
                 _routed(rotation, ca, camera, "yaw", 1),
                 _routed(rotation, ca, camera, "roll", 2)]
        else:
            o = [_routed(rotation, oa, ob, "pitch", 0), _routed(rotation, oa, ob, "yaw", 1),
                 _routed(rotation, oa, ob, "twist", 2)]
        p = [_routed(movement, oa, ob, "pan_x", 0),
             _routed(movement, oa, ob, "pan_y", 1)]
        z = _routed(movement, oa, ob, "zoom", 2)
    return o, p, z


def _apply_host_baseline(nav_mode, o, p, z, adv):
    """Apply immutable daemon-supplied factors after the user's mode-specific routing."""
    baseline = adv.get("host_baseline") or {}
    orbit = baseline.get("orbit", [1.0, 1.0, 1.0])
    pan = baseline.get("pan", [1.0, 1.0])
    zoom = float(baseline.get("zoom", 1.0))
    move = float(baseline.get("move", 1.0))
    o = [o[i] * float(orbit[i]) for i in range(3)]
    if nav_mode == "orbit":
        p = [p[i] * float(pan[i]) for i in range(2)]
        z *= zoom
    else:
        p = [v * move for v in p]
        z *= move
    return o, p, z


def _apply_fly(rv, o, p, z, adv):
    speed = float(adv.get("fly_speed", 1.0))
    if o[0] or o[1] or o[2]:                       # un-shifted ball -> look
        _look(rv, o[0] * ORBIT_SCALE[0], o[1] * ORBIT_SCALE[1], o[2] * ORBIT_SCALE[2], False)
        return
    # Shift held -> MOVE. ball forward/back (p[1]) = thrust along view forward; ball sideways
    # (p[0]) = strafe; twist (z) = rise/fall. Signs tune via the Pan/Zoom Invert checkboxes.
    right, up, fwd, _back = _view_axes(rv)
    k = _move_scale(FLY_MOVE, speed, rv)
    move = right * (p[0] * k) + fwd * (p[1] * k) + up * (z * k)
    if move.length:
        rv.view_location = rv.view_location + move


def _apply_walk(rv, o, p, z, adv):
    speed = float(adv.get("walk_speed", 1.0))
    if o[0] or o[1] or o[2]:                       # un-shifted ball -> look (horizon-locked)
        _look(rv, o[0] * ORBIT_SCALE[0], o[1] * ORBIT_SCALE[1], o[2] * ORBIT_SCALE[2], True)
        return
    # Shift held -> walk in the horizontal plane: ball forward/back (p[1]) = forward, sideways
    # (p[0]) = strafe; twist (z) = rise/fall (world Z).
    right, _up, fwd, _back = _view_axes(rv)
    right_h, fwd_h = _horizontal(right), _horizontal(fwd)
    k = _move_scale(WALK_MOVE, speed, rv)
    move = right_h * (p[0] * k) + fwd_h * (p[1] * k) + Vector((0.0, 0.0, z * k))
    if move.length:
        rv.view_location = rv.view_location + move


def _apply(target, frame, idle):
    """Apply one nav frame to the resolved target VIEW_3D. Runs on the main thread.
    Exactly one of orbit / pan / zoom is non-zero per frame (the daemon gates them)."""
    _win, area, region, rv, space, _key = target
    before_rot = rv.view_rotation.copy()
    before_loc = rv.view_location.copy()
    before_persp = rv.view_perspective
    o = frame.get("o", [0.0, 0.0, 0.0])
    p = frame.get("p", [0.0, 0.0])
    z = float(frame.get("z", 0.0))
    op = frame.get("op", "camera")
    style = frame.get("os", "free")
    zm = frame.get("zm", "to_center")
    adv = frame.get("adv") or {}
    # Effective nav mode: the Blender-shortcut override wins, but a change to the daemon's Mode
    # dropdown clears the override so the dropdown re-takes control.
    daemon_nav = adv.get("nav_mode", "orbit")
    if daemon_nav != _daemon_nav["v"]:
        _daemon_nav["v"] = daemon_nav
        _mode_override["v"] = None
    nav_mode = _mode_override["v"] or daemon_nav
    _maybe_level_horizon(rv, nav_mode, style, adv)

    sig = (nav_mode, op, style, zm, adv.get("twist_action"), adv.get("zoom_style"),
           adv.get("lock_horizon"), adv.get("lock_camera_to_view"))
    if sig != _last_scheme["v"]:                    # confirm live scheme changes are received
        _last_scheme["v"] = sig
        _log("scheme: nav=%s pivot=%s style=%s twist=%s zoom=%s horizon=%s camlock=%s"
             % (nav_mode, op, style, adv.get("twist_action"), adv.get("zoom_style"),
                adv.get("lock_horizon"), adv.get("lock_camera_to_view")))

    # Diagnostic (rate-limited): confirm which gesture channel is arriving. If holding Shift to
    # pan/move logs nothing here, the daemon isn't sending pan/zoom while Shift is down (Shift
    # detection / routing) rather than the add-on failing to apply it.
    if o[0] or o[1] or o[2]:
        _log_rl("rx_orbit", "rx orbit o=(%.4f,%.4f,%.4f) nav=%s" % (o[0], o[1], o[2], nav_mode))
    if p[0] or p[1]:
        _log_rl("rx_pan", "rx pan/move p=(%.4f,%.4f) nav=%s" % (p[0], p[1], nav_mode))
    if z:
        _log_rl("rx_zoom", "rx zoom/thrust z=%.4f nav=%s" % (z, nav_mode))

    # While the viewport is showing the CAMERA, Blender renders through the camera object and ignores
    # our rv (view_rotation/location/distance) edits -- so navigation would appear dead. Match
    # Blender's native behavior: navigating exits camera view to perspective so the edits are visible.
    # (When lock-camera-to-view is on we instead DRIVE the camera, handled below, so don't exit.)
    has_input = bool(o[0] or o[1] or o[2] or p[0] or p[1] or z)
    if has_input and before_persp == 'CAMERA' and not bool(adv.get("lock_camera_to_view", False)):
        rv.view_perspective = 'PERSP'

    o, p, z = _apply_action_routing(nav_mode, op, o, p, z, adv)
    o, p, z = _apply_host_baseline(nav_mode, o, p, z, adv)

    if nav_mode == "fly":
        _apply_fly(rv, o, p, z, adv)
        if p[0] or p[1] or z:
            _gesture["invalid"] = True
    elif nav_mode == "walk":
        _apply_walk(rv, o, p, z, adv)
        if p[0] or p[1] or z:
            _gesture["invalid"] = True
    else:                                           # orbit mode
        if o[0] or o[1] or o[2]:
            _apply_orbit(_win, rv, region, o, frame, adv, idle)
        elif p[0] or p[1]:
            _pan(rv, p[0], p[1], bool(adv.get("pan_scales_with_distance", True)))
            _gesture["invalid"] = True              # view moved -> recast screen-center next orbit
        elif z:
            if adv.get("zoom_style", "zoom") == "dolly":
                _dolly(rv, z)
            else:
                pivot = None
                if adv.get("zoom_to_mouse", False):
                    pivot = _raycast_screen_center(rv, region)    # best-effort: screen-centre surface
                _zoom(rv, z, pivot)
            _gesture["invalid"] = True

    # Camera view: optionally drive the real scene camera from the trackball.
    if rv.view_perspective == 'CAMERA':
        if bool(adv.get("lock_camera_to_view", False)):
            try:
                space.lock_camera = True
                _sync_camera_to_view(rv, bpy.context.scene)
            except Exception:
                _log_rl("cam", "camera sync FAILED: " + traceback.format_exc().strip().replace("\n", " | "))

    # Diagnostic (rate-limited): did the view actually change, and where? If rotD/locD are nonzero
    # but the screen doesn't move, the edits are landing on a non-displayed view -- persp=CAMERA
    # (Blender shows the camera, not rv) or the wrong viewport (check view3d_count / area size).
    if has_input:
        try:
            n3d = sum(1 for w in bpy.context.window_manager.windows if w.screen
                      for a in w.screen.areas if a.type == 'VIEW_3D')
        except Exception:
            n3d = -1
        _log_rl("applied", "applied persp=%s->%s area=%dx%d view3d=%d rotD=%.5f locD=%.5f vd=%.3f" % (
            before_persp, rv.view_perspective, area.width, area.height, n3d,
            before_rot.rotation_difference(rv.view_rotation).angle,
            (rv.view_location - before_loc).length, rv.view_distance))

    area.tag_redraw()


def _on_timer():
    """Main-thread pump: drain queued frames and apply them to the active VIEW_3D."""
    try:
        frames = []
        while True:
            try:
                frames.append(_q.get_nowait())
            except queue.Empty:
                break
        if frames:
            now = time.time()
            idle = now - _gesture["t"]              # frames only arrive during motion -> gap = gesture end
            _gesture["t"] = now
            target = _resolve_target()
            if target is not None:
                for fr in frames:
                    _apply(target, fr, idle)
                    idle = 0.0                       # only the first frame of a burst ends the gesture
            else:
                _log_rl("notarget", "frames received but no VIEW_3D is open -> ignoring")
    except Exception:
        _log_apply_error(traceback.format_exc())
    return _TIMER_INTERVAL


# ======================================================================================
# Broker reader (background thread) -- mirrors the Fusion add-in's socket loop
# ======================================================================================
def _reader():
    port = _bridge_port()
    hello = json.dumps({"type": "hello", "app": "blender", "version": ADDIN_VERSION,
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
            time.sleep(1.5)                          # daemon not up yet / dropped -> retry
        finally:
            try:
                if sock:
                    sock.close()
            except Exception:
                pass


# ======================================================================================
# In-Blender nav-mode toggle (operator + keymap), so you can switch orbit/fly/walk from the
# viewport like Blender's own Walk/Fly shortcut -- it flips the TRACKBALL's mode (a local override),
# it does NOT start Blender's modal walk/fly (which the trackball can't drive).
# ======================================================================================
_NAV_CYCLE = ("orbit", "fly", "walk")


class TRACKBALL_NAV_OT_cycle_mode(bpy.types.Operator):
    """Cycle the Trackball navigation between Orbit, Fly and Walk. Overrides the daemon's Mode until
    you change the daemon Mode dropdown again."""
    bl_idname = "trackball_nav.cycle_mode"
    bl_label = "Trackball: Cycle Nav Mode"
    bl_options = {'REGISTER'}

    mode: bpy.props.EnumProperty(            # "CYCLE" or a specific mode (for menu items)
        name="Mode",
        items=[("CYCLE", "Cycle", ""), ("orbit", "Orbit", ""), ("fly", "Fly", ""),
               ("walk", "Walk", "")],
        default="CYCLE", options={'SKIP_SAVE'})

    def execute(self, context):
        if self.mode == "CYCLE":
            cur = _mode_override["v"] or _daemon_nav["v"] or "orbit"
            i = _NAV_CYCLE.index(cur) if cur in _NAV_CYCLE else 0
            nxt = _NAV_CYCLE[(i + 1) % len(_NAV_CYCLE)]
        else:
            nxt = self.mode
        _mode_override["v"] = nxt
        self.report({'INFO'}, "Trackball nav: %s" % nxt.capitalize())
        _log("mode override -> %s (Blender shortcut)" % nxt)
        return {'FINISHED'}


def _menu_func(self, context):
    self.layout.operator(TRACKBALL_NAV_OT_cycle_mode.bl_idname, text="Trackball: Cycle Nav Mode")


def _register_keymap():
    try:
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is None:                       # e.g. --background: no addon keyconfig
            return
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new(TRACKBALL_NAV_OT_cycle_mode.bl_idname, "ACCENT_GRAVE", "PRESS", alt=True)
        _addon_keymaps.append((km, kmi))
        _log("keymap: Alt+` -> cycle trackball nav mode (rebind in Preferences > Keymap, 'Trackball')")
    except Exception:
        _log("keymap registration failed: " + traceback.format_exc().strip().replace("\n", " | "))


def _unregister_keymap():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()


# ======================================================================================
# Passive mouse tracker (Half A of the under-mouse "cursor" pivot). Blender has no on-demand mouse
# getter, so a window-wide modal operator caches the cursor's window-space position on each
# MOUSEMOVE; it returns {'PASS_THROUGH'} so it never consumes events or blocks normal interaction.
# The timer pump reads _cursor and maps it into the region (see _cursor_region_pixel). Lifecycle
# friction (documented): modal operators can't be invoked from the restricted register() context
# (deferred via a timer) and are CANCELLED on file load (restarted via load_post). It does NOT fight
# the timer pump -- both run on Blender's main thread, never concurrently, sharing only the _cursor
# dict (modal writes, timer reads).
# ======================================================================================
class TRACKBALL_NAV_OT_mouse_tracker(bpy.types.Operator):
    """Internal: passively cache the cursor's window-space position for the under-mouse orbit pivot.
    Always PASS_THROUGH -- never consumes events."""
    bl_idname = "trackball_nav.mouse_tracker"
    bl_label = "Trackball: Mouse Tracker (internal)"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if context.window is None:                  # --background / no window -> nothing to track
            return {'CANCELLED'}
        _tracker["gen"] += 1                         # supersede any straggler from before a reload
        self._gen = _tracker["gen"]
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self._gen != _tracker["gen"]:            # superseded (reload) or shutting down -> stop
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            win = context.window
            if win is not None:
                _cursor["win"] = win.as_pointer()
                _cursor["x"] = float(event.mouse_x)
                _cursor["y"] = float(event.mouse_y)
                _cursor["t"] = time.time()
                _cursor["ok"] = True
        return {'PASS_THROUGH'}                      # let the event continue to normal handlers


def _start_tracker():
    """(Re)start the passive mouse tracker in an available window. Deferred out of register()/load
    (a modal operator needs a window + an unrestricted context). One-shot (returns None)."""
    try:
        if bpy.app.background:                       # headless: no interactive window / event loop
            return None
        wm = getattr(bpy.context, "window_manager", None)
        if wm is None or not wm.windows:
            return None                             # no window yet
        win = bpy.context.window or wm.windows[0]
        with bpy.context.temp_override(window=win):
            bpy.ops.trackball_nav.mouse_tracker('INVOKE_DEFAULT')
        _log("mouse tracker started (window-wide MOUSEMOVE cache for under-cursor pivot)")
    except Exception:
        _log("mouse tracker start FAILED: " + traceback.format_exc().strip().replace("\n", " | "))
    return None


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    """Blender cancels running modal operators on file load -> restart the mouse tracker (deferred,
    since load_post runs in a restricted context). Invalidate the stale cached cursor first."""
    _cursor["ok"] = False
    try:
        bpy.app.timers.register(_start_tracker, first_interval=0.1)
    except Exception:
        pass


# ======================================================================================
# Add-on registration
# ======================================================================================
def register():
    global _reader_thread, _host
    _host = bpy.app.version_string          # capture on the main thread; the reader thread reuses it
    _log("register: Trackball Nav v%s (Blender %s)" % (ADDIN_VERSION, _host))
    _stop.clear()
    # Drain any stale state from a previous enable.
    try:
        while True:
            _q.get_nowait()
    except queue.Empty:
        pass
    _gesture.update({"t": 0.0, "pivot": None, "invalid": True})
    _horizon["fixed"] = None
    _mode_override["v"] = None
    _daemon_nav["v"] = None
    _cursor.update({"win": None, "x": 0.0, "y": 0.0, "t": 0.0, "ok": False})
    for cls in (TRACKBALL_NAV_OT_cycle_mode, TRACKBALL_NAV_OT_mouse_tracker):
        try:
            bpy.utils.register_class(cls)
        except Exception:                    # already registered (e.g. reload) -> ignore
            pass
    try:
        bpy.types.VIEW3D_MT_view.append(_menu_func)
    except Exception:
        pass
    _register_keymap()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)   # restart the tracker after a file load
    _reader_thread = threading.Thread(target=_reader, name="trackball-nav-reader", daemon=True)
    _reader_thread.start()
    if not bpy.app.timers.is_registered(_on_timer):
        bpy.app.timers.register(_on_timer, persistent=True)
    # Start the passive mouse tracker deferred (register()'s context is too restricted to invoke a
    # modal operator; a timer runs it once a window/context is available -- no-op in --background).
    try:
        bpy.app.timers.register(_start_tracker, first_interval=0.2)
    except Exception:
        pass


def unregister():
    _stop.set()
    _tracker["gen"] += 1                     # supersede the running mouse tracker (self-cancels next event)
    try:
        if bpy.app.timers.is_registered(_on_timer):
            bpy.app.timers.unregister(_on_timer)
    except Exception:
        pass
    try:
        if _on_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_on_load_post)
    except Exception:
        pass
    _unregister_keymap()
    try:
        bpy.types.VIEW3D_MT_view.remove(_menu_func)
    except Exception:
        pass
    for cls in (TRACKBALL_NAV_OT_mouse_tracker, TRACKBALL_NAV_OT_cycle_mode):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    _log("unregister: Trackball Nav v%s" % ADDIN_VERSION)


if __name__ == "__main__":
    register()
