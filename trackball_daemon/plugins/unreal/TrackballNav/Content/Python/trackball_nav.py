"""TrackballNav -- Unreal Engine editor add-on (the Unreal-coupled half).

Connects to the Trackball Daemon's local nav broker (127.0.0.1) and drives the active level
editor PERSPECTIVE viewport camera with the orbit/pan/zoom deltas it streams. A background
socket thread reads the broker into a queue; a Slate post-tick callback on Unreal's MAIN
(game) thread drains it and moves the camera. The ``unreal`` API is main-thread-only -- the
reader thread does socket I/O only (the same split as the Blender/FreeCAD/Fusion add-ons).

This module is imported and started by ``init_unreal.py`` (a thin shim that Unreal auto-runs at
editor startup for every enabled plugin's Content/Python). The pure camera math lives in
:mod:`tbnav_unreal_camera` (no ``unreal`` import -> unit-testable headless).

The editor viewport camera is a FREE-FLY eye+rotator (``get/set_level_viewport_camera_info``),
NOT a view-distance/orbit model, so orbit-about-a-pivot and zoom are SYNTHESISED here and the
location+rotator are written back every frame. Conventions (left-handed, Z-up, cm, degrees)
were verified live -- see docs/unreal_driver_notes.md. The signs in tbnav_unreal_camera are
BASELINE GUESSES to settle on the device.
"""
import json
import os
import queue
import socket
import threading
import time
import traceback

import unreal

import tbnav_unreal_camera as cammath

ADDIN_VERSION = "0.2.0"          # reported in the hello handshake (shown in the daemon's tray); keep
                                 # in sync with version.json AND TrackballNav.uplugin VersionName.
                                 # 0.2.0: Blender-parity scheme (orbit/fly/walk modes, viewpoint pivot,
                                 # twist_action, lock_horizon, per-mode inverts) + orbit baseline x2.
_DEFAULT_PORT = 47900
PIVOT_HOLD_IDLE = 0.35           # s without frames that ends a gesture -> re-raycast the "view" pivot
OBJ_CACHE_SEC = 0.5              # selection bounding-box centre cache lifetime
BBOX_MARGIN = 0.10               # accept a "view" hit inside the model bbox grown by this * diagonal
TRACE_BIG = 1.0e7               # cm: "view" raycast length down the camera forward axis

# --- runtime state ---------------------------------------------------------------------
_stop = threading.Event()
_q = queue.Queue()
_started = False
_reader_thread = None
_tick_handle = None
_host = "?"
_subsystem = None                # cached UnrealEditorSubsystem (None => use EditorLevelLibrary)

# "view" pivot: raycast the surface under the screen centre ONCE per gesture and HOLD it, so the
# point under the crosshair stays put while orbiting. Invalidated on pan/zoom or after an idle gap.
_gesture = {"t": 0.0, "pivot": None, "invalid": True}
_obj_cache = {"t": 0.0, "center": None, "bbox": None}
_focus = {"dist": cammath.DIST_DEFAULT}   # eye->focus distance (cm), scales pan/zoom; updated on orbit
_last_scheme = {"v": None}


# ======================================================================================
# Logging (file-based, rate-limited) -- mirrors the Fusion/Blender/FreeCAD add-ons
# ======================================================================================
def _log(msg):
    try:
        path = os.path.join(os.environ.get("APPDATA", ""), "TrackballDaemon", "unreal_addin.log")
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
# Unreal editor viewport plumbing (MAIN THREAD ONLY)
#
# Support BOTH the UE5 UnrealEditorSubsystem (preferred) and the deprecated EditorLevelLibrary
# (UE4.27 / early UE5) so the add-on spans UE4.27 -> UE5.x. Both expose the same camera methods.
# ======================================================================================
def _ues():
    """The cached UnrealEditorSubsystem, or None if unavailable (=> fall back to EditorLevelLibrary)."""
    global _subsystem
    if _subsystem is None:
        try:
            _subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        except Exception:
            _subsystem = None
    return _subsystem


def _get_camera_info():
    """(location: Vector, rotation: Rotator) for the active perspective viewport, or None when there
    is no level-editor viewport (commandlet / not ready). Tries the subsystem, then EditorLevelLibrary."""
    sub = _ues()
    try:
        if sub is not None:
            return sub.get_level_viewport_camera_info()
        return unreal.EditorLevelLibrary.get_level_viewport_camera_info()
    except Exception:
        return None


def _set_camera_info(location, rotation):
    sub = _ues()
    if sub is not None:
        sub.set_level_viewport_camera_info(location, rotation)
    else:
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(location, rotation)


def _editor_world():
    sub = _ues()
    try:
        if sub is not None:
            return sub.get_editor_world()
        return unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        return None


def _in_pie():
    """Best-effort guard: a non-None game world means Play-In-Editor is running -> don't fight it."""
    sub = _ues()
    if sub is None:
        return False
    try:
        return sub.get_game_world() is not None
    except Exception:
        return False


def _basis_from_rotator(rot):
    """World (forward, right, up) for an FRotator. Uses Unreal's own MathLibrary (authoritative,
    verified) and falls back to the pure-Python convention if those helpers are ever missing."""
    try:
        ml = unreal.MathLibrary
        f = ml.get_forward_vector(rot)
        r = ml.get_right_vector(rot)
        u = ml.get_up_vector(rot)
        return (f.x, f.y, f.z), (r.x, r.y, r.z), (u.x, u.y, u.z)
    except Exception:
        return cammath.rotator_to_basis(rot.pitch, rot.yaw, rot.roll)


def _rotator_from_basis(forward, up):
    """Rebuild an FRotator from a (forward, up) basis. Uses make_rot_from_xz (authoritative,
    verified to round-trip) and falls back to the pure-Python convention + keyword Rotator."""
    try:
        return unreal.MathLibrary.make_rot_from_xz(unreal.Vector(*forward), unreal.Vector(*up))
    except Exception:
        p, y, r = cammath.basis_to_rotator(forward, up)
        return unreal.Rotator(pitch=p, yaw=y, roll=r)   # keyword: positional order is (roll,pitch,yaw)!


def _read_camera(info):
    """Live (location, rotation) tuple -> a pure tbnav_unreal_camera.Camera."""
    loc, rot = info
    fwd, right, up = _basis_from_rotator(rot)
    return cammath.Camera((loc.x, loc.y, loc.z), fwd, right, up)


def _write_camera(cam):
    """Write a pure Camera back onto the active perspective viewport."""
    rot = _rotator_from_basis(cam.forward, cam.up)
    _set_camera_info(unreal.Vector(cam.location[0], cam.location[1], cam.location[2]), rot)


# ======================================================================================
# Pivot resolution (world origin / selection bounds / screen-centre raycast)
# ======================================================================================
def _selected_actors():
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()
    except Exception:
        try:
            return unreal.EditorLevelLibrary.get_selected_level_actors()
        except Exception:
            return []


def _selection_center():
    """Median of the selected actors' bounding-box centres (cm), with the aggregate bbox, or
    (None, None). Cached ~OBJ_CACHE_SEC."""
    now = time.time()
    if _obj_cache["center"] is not None and now - _obj_cache["t"] < OBJ_CACHE_SEC:
        return _obj_cache["center"], _obj_cache["bbox"]
    centers = []
    mn = [None, None, None]
    mx = [None, None, None]
    for actor in _selected_actors() or []:
        try:
            origin, extent = actor.get_actor_bounds(False)
        except Exception:
            continue
        centers.append((origin.x, origin.y, origin.z))
        for i, (o, e) in enumerate(((origin.x, extent.x), (origin.y, extent.y), (origin.z, extent.z))):
            lo, hi = o - e, o + e
            mn[i] = lo if mn[i] is None else min(mn[i], lo)
            mx[i] = hi if mx[i] is None else max(mx[i], hi)
    if not centers:
        _obj_cache.update(t=now, center=None, bbox=None)
        return None, None
    n = float(len(centers))
    center = (sum(c[0] for c in centers) / n, sum(c[1] for c in centers) / n,
              sum(c[2] for c in centers) / n)
    bbox = (tuple(mn), tuple(mx))
    _obj_cache.update(t=now, center=center, bbox=bbox)
    return center, bbox


def _in_bbox(p, bbox):
    if bbox is None:
        return True
    mn, mx = bbox
    diag = ((mx[0] - mn[0]) ** 2 + (mx[1] - mn[1]) ** 2 + (mx[2] - mn[2]) ** 2) ** 0.5
    m = BBOX_MARGIN * diag
    return all(mn[i] - m <= p[i] <= mx[i] + m for i in range(3))


def _hit_point(hit):
    """World hit point from a HitResult. UE5 HitResult fields are PROTECTED against direct attribute
    and get_editor_property access (verified UE5.8), but ``to_dict()`` exposes them: keys
    'impact_point' / 'location' are Vector structs. Prefer impact_point (the surface), fall back to
    location. Returns a world 3-tuple, or None."""
    try:
        d = hit.to_dict()
    except Exception:
        return None
    for key in ("impact_point", "location"):
        v = d.get(key)
        if v is not None:
            try:
                return (v.x, v.y, v.z)
            except Exception:
                pass
    return None


def _screen_center_pivot(cam, bbox):
    """Raycast the editor world down the camera forward axis (screen centre) to the first surface,
    validated against the selection bbox. Editor traces are finicky (collision/visibility) -> any
    miss/failure returns None and the caller falls back to the selection/forward point."""
    world = _editor_world()
    if world is None:
        return None
    start = unreal.Vector(cam.location[0], cam.location[1], cam.location[2])
    fwd = cam.forward
    end = unreal.Vector(cam.location[0] + fwd[0] * TRACE_BIG,
                        cam.location[1] + fwd[1] * TRACE_BIG,
                        cam.location[2] + fwd[2] * TRACE_BIG)
    try:
        hit = unreal.SystemLibrary.line_trace_single(
            world, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [],
            unreal.DrawDebugTrace.NONE, True)
    except Exception:
        _log_rl("vpivot", "line_trace_single FAILED -> selection/forward fallback")
        return None
    if not hit:
        _log_rl("vpivot", "view-pivot: nothing under screen centre -> selection/forward fallback")
        return None
    p = _hit_point(hit)
    if p is None or not _in_bbox(p, bbox):
        _log_rl("vpivot", "view-pivot: no valid hit point -> selection/forward fallback")
        return None
    _log_rl("vpivot", "view-pivot: surface hit -> (%.1f,%.1f,%.1f)" % p)
    return p


def _forward_point(cam):
    """A synthetic pivot a focus-distance ahead of the eye (used when nothing better resolves);
    orbiting about it feels like turning around the thing in front of you."""
    d = cammath._clamp_dist(_focus["dist"])
    return (cam.location[0] + cam.forward[0] * d,
            cam.location[1] + cam.forward[1] * d,
            cam.location[2] + cam.forward[2] * d)


def _orbit_pivot(op, cam, idle):
    """Resolve the orbit pivot (a world point) for scheme ``op``, or None to turn in place about the
    eye (free-fly). Everything falls back through selection centre -> a point ahead of the camera."""
    if op == "viewpoint":                    # Blender-style: orbit about the EYE = turn in place
        return None
    if op == "origin":
        return (0.0, 0.0, 0.0)
    center, bbox = _selection_center()
    if op == "view":
        if _gesture["pivot"] is None or _gesture["invalid"] or idle > PIVOT_HOLD_IDLE:
            _gesture["pivot"] = _screen_center_pivot(cam, bbox)
            if _gesture["pivot"] is None:
                _gesture["pivot"] = center if center is not None else _forward_point(cam)
            _gesture["invalid"] = False
        return _gesture["pivot"]
    if op in ("object", "cursor"):           # Unreal has NO 3D cursor (verified: no such Python API),
        return center if center is not None else _forward_point(cam)   # so cursor -> selection centre
    return _forward_point(cam)               # unknown -> a sane orbit point in front of the camera


def _zoom_toward(zm):
    if zm == "to_object":
        center, _bb = _selection_center()
        return center                        # may be None -> dolly straight along forward
    return None                              # to_center / to_cursor -> dolly along forward


# ======================================================================================
# Frame application (MAIN THREAD)
# ======================================================================================
def _sgn(flag):
    """+1, or -1 when a per-mode invert flag is set."""
    return -1.0 if flag else 1.0


def _apply_inverts(nav_mode, op, o, p, z, inv):
    """Per-mode, per-axis direction flips (config advanced.invert.<mode>.<axis>). Applied HERE, in the
    add-on, not in the daemon -- the same physical channel means different things per mode (ball
    forward/back is orbit pan-Y but fly/walk forward), so independent inverts are only possible once
    the mode is known. "viewpoint" gets its own rotation inverts and shares orbit's pan/zoom inverts.
    Mirrors the Blender add-on exactly."""
    if nav_mode == "fly":
        f = inv.get("fly", {})
        o = [o[0] * _sgn(f.get("pitch")), o[1] * _sgn(f.get("yaw")), o[2] * _sgn(f.get("bank"))]
        p = [p[0] * _sgn(f.get("strafe")), p[1] * _sgn(f.get("forward"))]
        z = z * _sgn(f.get("vertical"))
    elif nav_mode == "walk":
        w = inv.get("walk", {})
        o = [o[0] * _sgn(w.get("pitch")), o[1] * _sgn(w.get("yaw")), o[2]]
        p = [p[0] * _sgn(w.get("strafe")), p[1] * _sgn(w.get("forward"))]
        z = z * _sgn(w.get("vertical"))
    else:                                            # orbit
        ob = inv.get("orbit", {})
        if op == "viewpoint":
            vp = inv.get("viewpoint", {})
            o = [o[0] * _sgn(vp.get("pitch")), o[1] * _sgn(vp.get("yaw")), o[2] * _sgn(vp.get("roll"))]
        else:
            o = [o[0] * _sgn(ob.get("pitch")), o[1] * _sgn(ob.get("yaw")), o[2] * _sgn(ob.get("twist"))]
        p = [p[0] * _sgn(ob.get("pan_x")), p[1] * _sgn(ob.get("pan_y"))]
        z = z * _sgn(ob.get("zoom"))
    return o, p, z


def _apply_orbit(cam, o, p, z, op, style, zm, twist_action, lock, pan_scales, idle):
    """ORBIT mode: orbit (with twist routed by twist_action), pan, or dolly. Exactly one channel is
    non-zero per frame (the daemon gates them on Shift)."""
    if o[0] or o[1] or o[2]:
        _log_rl("rx_orbit", "rx orbit o=(%.4f,%.4f,%.4f) op=%s os=%s" % (o[0], o[1], o[2], op, style))
        twist = o[2]
        orbit_o = [o[0], o[1], 0.0]
        did = False
        if twist:                                    # un-shifted twist: roll | zoom/dolly | none
            if twist_action == "roll" and not lock:
                orbit_o[2] = twist                   # keep twist in the orbit rotation (rolls/banks)
            elif twist_action in ("zoom", "dolly"):
                cammath.dolly(cam, twist, _focus["dist"])
                _gesture["invalid"] = True
                did = True
            # "none" (or roll while horizon-locked): twist ignored
        if orbit_o[0] or orbit_o[1] or orbit_o[2]:
            pivot = _orbit_pivot(op, cam, idle)
            if pivot is not None:
                _focus["dist"] = cammath._clamp_dist(
                    cammath.v_len(cammath.v_sub(tuple(cam.location), pivot)))
            cammath.orbit(cam, orbit_o, (style == "turntable") or lock, pivot)
            return True
        return did
    if p[0] or p[1]:
        _log_rl("rx_pan", "rx pan p=(%.4f,%.4f)" % (p[0], p[1]))
        _gesture["invalid"] = True                   # view moved -> next orbit re-raycasts its pivot
        cammath.pan(cam, p[0], p[1], _focus["dist"] if pan_scales else cammath.DIST_DEFAULT)
        return True
    if z:
        _log_rl("rx_zoom", "rx zoom z=%.4f zm=%s" % (z, zm))
        _gesture["invalid"] = True
        cammath.dolly(cam, z, _focus["dist"], _zoom_toward(zm))
        return True
    return False


def _apply_fly(cam, o, p, z, adv):
    """FLY mode: un-shifted ball -> free look (banks on twist); Shift+ball -> 3D move along the
    camera's own axes (forward dives/climbs with pitch)."""
    if o[0] or o[1] or o[2]:
        _log_rl("rx_orbit", "rx fly-look o=(%.4f,%.4f,%.4f)" % (o[0], o[1], o[2]))
        cammath.look(cam, o, False)
        return True
    if p[0] or p[1] or z:
        _log_rl("rx_pan", "rx fly-move p=(%.4f,%.4f) z=%.4f" % (p[0], p[1], z))
        cammath.fly_move(cam, p, z, _focus["dist"], adv.get("fly_speed", 1.0))
        _gesture["invalid"] = True
        return True
    return False


def _apply_walk(cam, o, p, z, adv):
    """WALK mode: un-shifted ball -> horizon-locked look (no bank); Shift+ball -> move in the ground
    plane (forward stays level) + rise/fall along world up."""
    if o[0] or o[1] or o[2]:
        _log_rl("rx_orbit", "rx walk-look o=(%.4f,%.4f,%.4f)" % (o[0], o[1], o[2]))
        cammath.look(cam, o, True)
        return True
    if p[0] or p[1] or z:
        _log_rl("rx_pan", "rx walk-move p=(%.4f,%.4f) z=%.4f" % (p[0], p[1], z))
        cammath.walk_move(cam, p, z, _focus["dist"], adv.get("walk_speed", 1.0))
        _gesture["invalid"] = True
        return True
    return False


def _apply(info, frame, idle):
    """Apply one nav frame to the active perspective viewport camera. Exactly one of orbit / pan /
    zoom is non-zero per frame (the daemon gates them on Shift). Reads o/p/z/op/os/zm AND the
    advanced nav options ('adv': nav_mode/lock_horizon/twist_action/pan_scales/speeds/invert) the
    daemon attaches for Unreal (the same additive 'adv' object Blender uses)."""
    o = list(frame.get("o", [0.0, 0.0, 0.0]))
    p = list(frame.get("p", [0.0, 0.0]))
    z = float(frame.get("z", 0.0))
    op = frame.get("op", "view")
    style = frame.get("os", "free")
    zm = frame.get("zm", "to_center")
    adv = frame.get("adv") or {}
    nav_mode = adv.get("nav_mode", "orbit")
    lock = bool(adv.get("lock_horizon", False))
    twist_action = adv.get("twist_action", "roll")
    pan_scales = bool(adv.get("pan_scales_with_distance", True))

    sig = (nav_mode, op, style, zm, twist_action, lock)
    if sig != _last_scheme["v"]:
        _last_scheme["v"] = sig
        _log("scheme: nav=%s pivot=%s style=%s zoom=%s twist=%s horizon=%s" % sig)

    # Per-mode direction inverts (applied here, like Blender -- see _apply_inverts).
    o, p, z = _apply_inverts(nav_mode, op, o, p, z, adv.get("invert") or {})

    cam = _read_camera(info)
    if nav_mode == "fly":
        changed = _apply_fly(cam, o, p, z, adv)
    elif nav_mode == "walk":
        changed = _apply_walk(cam, o, p, z, adv)
    else:                                            # orbit
        changed = _apply_orbit(cam, o, p, z, op, style, zm, twist_action, lock, pan_scales, idle)

    if changed:
        _write_camera(cam)
        _log_rl("applied", "applied nav=%s op=%s pos=(%.1f,%.1f,%.1f) dist=%.0f"
                % (nav_mode, op, cam.location[0], cam.location[1], cam.location[2], _focus["dist"]))


def _pump(_delta_seconds=0.0):
    """Main-thread (Slate post-tick) pump: drain queued frames and apply them to the viewport."""
    try:
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
        if _in_pie():
            _log_rl("pie", "Play-In-Editor active -> ignoring nav frames")
            return
        info = _get_camera_info()
        if not info:
            _log_rl("noview", "frames received but no perspective viewport -> ignoring")
            return
        for fr in frames:
            _apply(info, fr, idle)
            idle = 0.0                            # only the first frame of a burst ends the gesture
    except Exception:
        _log_apply_error(traceback.format_exc())


# ======================================================================================
# Broker reader (background thread) -- mirrors the Fusion/Blender/FreeCAD socket loop verbatim
# ======================================================================================
def _reader():
    port = _bridge_port()
    hello = json.dumps({"type": "hello", "app": "unreal", "version": ADDIN_VERSION,
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
# Bootstrap (called from init_unreal.py)
# ======================================================================================
def start():
    """Entry point called by init_unreal.py. Idempotent (re-running init_unreal won't double-start).
    Registers the reader thread + a Slate post-tick callback (the main-thread pump) + a Python
    shutdown hook for clean teardown."""
    global _started, _reader_thread, _tick_handle, _host
    if _started:
        return
    _started = True
    try:
        _host = unreal.SystemLibrary.get_engine_version().split("-", 1)[0]   # e.g. "5.8.0"
    except Exception:
        _host = "?"
    _log("start: TrackballNav v%s starting (Unreal %s)" % (ADDIN_VERSION, _host))
    _stop.clear()
    _gesture.update({"t": 0.0, "pivot": None, "invalid": True})
    _focus["dist"] = cammath.DIST_DEFAULT
    _reader_thread = threading.Thread(target=_reader, name="trackball-nav-reader", daemon=True)
    _reader_thread.start()
    try:
        _tick_handle = unreal.register_slate_post_tick_callback(_pump)
        _log("start: reader thread + Slate post-tick pump registered")
    except Exception:
        _log("start: register_slate_post_tick_callback FAILED -> "
             + traceback.format_exc().strip().replace("\n", " | "))
    try:
        unreal.register_python_shutdown_callback(stop)
    except Exception:
        pass


def stop():
    global _tick_handle
    _stop.set()
    try:
        if _tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(_tick_handle)
            _tick_handle = None
    except Exception:
        pass
    _log("stop: TrackballNav v%s" % ADDIN_VERSION)
