# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

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
were verified live -- see docs/apps/unreal.md. The signs in tbnav_unreal_camera are
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

ADDIN_VERSION = "0.2.15"         # keep in sync with version.json and TrackballNav.uplugin
_DEFAULT_PORT = 47900
PIVOT_HOLD_IDLE = 0.5            # fallback for adv.orbit_hold_sec / adv.zoom_hold_sec
OBJECT_GESTURE_IDLE = 0.5        # coalesce one continuous actor transform into one undo step
OBJ_CACHE_SEC = 0.5              # selection bounding-box centre cache lifetime
BBOX_MARGIN = 0.10               # accept a hit inside the model bbox grown by this * diagonal
TRACE_BIG = 1.0e7               # cm: raycast length along camera forward / deprojected ray

# --- runtime state ---------------------------------------------------------------------
_stop = threading.Event()
_q = queue.Queue()
_started = False
_reader_thread = None
_tick_handle = None
_host = "?"
_subsystem = None                # cached UnrealEditorSubsystem (None => use EditorLevelLibrary)
_level_subsystem = None          # LevelEditorSubsystem owns per-viewport FOV

# `screen_center`/`cursor`: raycast once per gesture and hold the hit so the surface stays put
# while orbiting. Invalidated on pan/zoom or after an idle gap.
_gesture = {"t": 0.0, "pivot": None, "invalid": True}
_zoom_gesture = {"pivot": None}  # "to_cursor" zoom's own per-gesture hold (reset on orbit/pan)
_obj_cache = {"t": 0.0, "center": None, "bbox": None}
_scene_cache = {"t": 0.0, "center": None}
# Fixed-horizon transition tracker: None until the first frame so startup in a fixed mode does not
# level the view; only a real free->fixed switch does.
_horizon = {"fixed": None}
_focus = {"dist": cammath.DIST_DEFAULT}   # eye->focus distance (cm), scales pan/zoom; updated on orbit
_last_scheme = {"v": None}
_georef_logged = {"missing": False}      # one-shot warn if GeoReferencing Python type is absent
_object_transaction = {"scope": None, "t": 0.0}


# ======================================================================================
# Logging (file-based, rate-limited) -- mirrors the Fusion/Blender/FreeCAD add-ons
# ======================================================================================
# The daemon's configuration root, then the root earlier daemon builds wrote. Probing both is what
# lets this add-on and the daemon be updated independently: whichever of the two is older, they
# still meet at the same discovery file. Keep in step with trackball_daemon/product.py.
_CONFIG_ROOTS = (("Mildly Useful", "Astrolabe"), ("TrackballDaemon",))


def _config_file(name):
    """Path to `name` under the daemon's configuration root.

    Prefers a file that already exists, then the first root that exists, then the current root.
    """
    roots = [os.path.join(os.environ.get("APPDATA", ""), *parts) for parts in _CONFIG_ROOTS]
    for root in roots:
        if os.path.isfile(os.path.join(root, name)):
            return os.path.join(root, name)
    for root in roots:
        if os.path.isdir(root):
            return os.path.join(root, name)
    return os.path.join(roots[0], name)


def _log(msg):
    try:
        path = _config_file("unreal_addin.log")
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
        with open(_config_file("bridge.json"), "r") as f:
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


def _les():
    global _level_subsystem
    if _level_subsystem is None:
        try:
            _level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        except Exception:
            _level_subsystem = None
    return _level_subsystem


def _viewport_fov():
    sub = _les()
    if sub is None:
        return None
    try:
        key = sub.get_active_viewport_config_key()
        getter = getattr(sub, "get_level_viewport_fov", None) or \
            getattr(sub, "get_level_viewport_field_of_view", None)
        result = getter(key) if getter is not None else None
        values = result if isinstance(result, tuple) else (result,)
        return next((float(v) for v in values
                     if isinstance(v, (int, float)) and not isinstance(v, bool) and 1.0 < v < 179.0), None)
    except Exception:
        return None


def _set_viewport_fov(value):
    sub = _les()
    if sub is None:
        return False
    try:
        key = sub.get_active_viewport_config_key()
        setter = getattr(sub, "set_level_viewport_fov", None) or \
            getattr(sub, "set_level_viewport_field_of_view", None)
        if setter is None:
            return False
        setter(float(value), key)
        return True
    except Exception as exc:
        _log_rl("fov", "viewport FOV update failed; using dolly (%s)" % exc)
        return False


def _apply_lens_zoom(cam, z, toward=None):
    old_fov = _viewport_fov()
    if old_fov is None:
        return False
    old_location = list(cam.location)
    new_fov = cammath.lens_zoom(cam, z, old_fov, toward)
    if _set_viewport_fov(new_fov):
        return True
    cam.location = old_location
    return False


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


def _actor_key(actor):
    try:
        return actor.get_path_name()
    except Exception:
        return str(actor)


def _selected_actor_roots():
    """Selected actors excluding descendants of another selected actor."""
    actors = [actor for actor in (_selected_actors() or []) if actor is not None]
    selected = {_actor_key(actor) for actor in actors}
    roots = []
    for actor in actors:
        parent = None
        try:
            parent = actor.get_attach_parent_actor()
        except Exception:
            pass
        seen = set()
        while parent is not None and _actor_key(parent) not in seen:
            key = _actor_key(parent)
            seen.add(key)
            if key in selected:
                break
            try:
                parent = parent.get_attach_parent_actor()
            except Exception:
                parent = None
        else:
            roots.append(actor)
    return roots


def _end_object_transaction():
    scope = _object_transaction["scope"]
    _object_transaction["scope"] = None
    if scope is not None:
        try:
            scope.__exit__(None, None, None)
        except Exception:
            _log_rl("object_undo", "could not close the Object transform undo transaction")


def _begin_object_transaction(idle):
    if _object_transaction["scope"] is not None and idle > OBJECT_GESTURE_IDLE:
        _end_object_transaction()
    if _object_transaction["scope"] is None:
        transaction_type = getattr(unreal, "ScopedEditorTransaction", None)
        if transaction_type is not None:
            try:
                scope = transaction_type("Astrolabe Object Transform")
                scope.__enter__()
                _object_transaction["scope"] = scope
            except Exception:
                _log_rl("object_undo", "Object transform undo transaction is unavailable")
    _object_transaction["t"] = time.time()


def _all_actors():
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    except Exception:
        try:
            return unreal.EditorLevelLibrary.get_all_level_actors()
        except Exception:
            return []


def _scene_center():
    """Center of the aggregate level-actor bounds, excluding actors with no spatial extent."""
    now = time.time()
    if _scene_cache["center"] is not None and now - _scene_cache["t"] < OBJ_CACHE_SEC:
        return _scene_cache["center"]
    mn = [None, None, None]
    mx = [None, None, None]
    for actor in _all_actors() or []:
        try:
            origin, extent = actor.get_actor_bounds(False)
            oe = ((origin.x, extent.x), (origin.y, extent.y), (origin.z, extent.z))
            if max(abs(v[1]) for v in oe) <= 1e-6:
                continue
        except Exception:
            continue
        for i, (o, e) in enumerate(oe):
            lo, hi = o - e, o + e
            mn[i] = lo if mn[i] is None else min(mn[i], lo)
            mx[i] = hi if mx[i] is None else max(mx[i], hi)
    center = None if mn[0] is None else tuple((mn[i] + mx[i]) * 0.5 for i in range(3))
    _scene_cache.update(t=now, center=center)
    return center


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


def _trace_ray(origin, direction, bbox, log_key, msg_hit, msg_miss):
    """Trace from ``origin`` along ``direction`` (unit or not) for TRACE_BIG cm. Returns a world
    3-tuple validated against ``bbox``, or None on miss/failure."""
    world = _editor_world()
    if world is None:
        return None
    try:
        dx, dy, dz = direction
        n = (dx * dx + dy * dy + dz * dz) ** 0.5
        if n < 1e-12:
            return None
        scale = TRACE_BIG / n
        start = unreal.Vector(origin[0], origin[1], origin[2])
        end = unreal.Vector(origin[0] + dx * scale,
                            origin[1] + dy * scale,
                            origin[2] + dz * scale)
        hit = unreal.SystemLibrary.line_trace_single(
            world, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [],
            unreal.DrawDebugTrace.NONE, True)
    except Exception:
        _log_rl(log_key, "line_trace_single FAILED -> continue configured chain")
        return None
    if not hit:
        _log_rl(log_key, msg_miss)
        return None
    p = _hit_point(hit)
    if p is None or not _in_bbox(p, bbox):
        _log_rl(log_key, msg_miss)
        return None
    _log_rl(log_key, msg_hit % p)
    return p


def _screen_center_pivot(cam, bbox):
    """Raycast the editor world down the camera forward axis (screen centre) to the first surface,
    validated against the selection bbox. Editor traces are finicky (collision/visibility) -> any
    miss/failure returns None and the resolver continues through the configured candidate chain."""
    return _trace_ray(
        cam.location, cam.forward, bbox, "vpivot",
        "screen-center-pivot: surface hit -> (%.1f,%.1f,%.1f)",
        "screen-center-pivot: nothing under screen centre -> continue configured chain")


def _cursor_screen_ray():
    """Editor-viewport mouse as (pixel_xy, world_origin, world_direction), or None.

    Half A comes from Epic's stock ``GeoReferencingEditorBPLibrary`` (our `.uplugin` depends on
    ``GeoReferencing`` so enabling Trackball Nav enables it). That BPLibrary is the one place in
    stock UE Python that exposes the *level-editor* viewport mouse / cursor ray
    (`GCurrentLevelEditingViewportClient->GetCursorWorldLocationFromMousePos`). It returns
    focused=False when the viewport widget lacks Slate focus (Details panel, Content Browser, …)
    — call sites report the method unavailable rather than invent a desktop-cursor hack.

    Prefers ``get_viewport_cursor_information`` (pixel + world ray in one call). Falls back to
    ``get_viewport_cursor_location`` + ``UnrealEditorSubsystem.screen_to_world``. Returns
    ``((x, y), (ox, oy, oz), (dx, dy, dz))`` or None.
    """
    lib = getattr(unreal, "GeoReferencingEditorBPLibrary", None)
    if lib is None:
        if not _georef_logged["missing"]:
            _georef_logged["missing"] = True
            _log("cursor-pivot: GeoReferencingEditorBPLibrary missing — enable the GeoReferencing "
                 "plugin (TrackballNav.uplugin depends on it) and restart the editor")
        return None
    try:
        focused, screen, origin, direction = lib.get_viewport_cursor_information()
        if focused and screen is not None and origin is not None and direction is not None:
            return ((float(screen.x), float(screen.y)),
                    (float(origin.x), float(origin.y), float(origin.z)),
                    (float(direction.x), float(direction.y), float(direction.z)))
    except Exception:
        pass
    try:
        focused, screen = lib.get_viewport_cursor_location()
        if not focused or screen is None:
            return None
        px = (float(screen.x), float(screen.y))
    except Exception:
        return None
    sub = _ues()
    if sub is None:
        return None
    try:
        result = sub.screen_to_world(unreal.Vector2D(px[0], px[1]))
    except Exception:
        return None
    if not result:
        return None
    # UE Python may return (ok, world_pos, world_dir) or (world_pos, world_dir).
    if len(result) == 3:
        ok, world_pos, world_dir = result
        if not ok or world_pos is None or world_dir is None:
            return None
    elif len(result) == 2:
        world_pos, world_dir = result
        if world_pos is None or world_dir is None:
            return None
    else:
        return None
    return (px,
            (float(world_pos.x), float(world_pos.y), float(world_pos.z)),
            (float(world_dir.x), float(world_dir.y), float(world_dir.z)))


def _cursor_pivot(bbox):
    """Surface under the editor-viewport mouse, or None (no focus / miss / no Geo API)."""
    ray = _cursor_screen_ray()
    if ray is None:
        _log_rl("cpivot", "cursor-pivot: no viewport mouse (click the level viewport, or "
                "GeoReferencing unavailable) -> continue configured chain")
        return None
    _px, origin, direction = ray
    return _trace_ray(
        origin, direction, bbox, "cpivot",
        "cursor-pivot: surface hit -> (%.1f,%.1f,%.1f)",
        "cursor-pivot: nothing under cursor -> continue configured chain")


def _cursor_depth_point():
    """Point on the cursor ray at the current focus depth, for empty-space To Cursor zoom."""
    ray = _cursor_screen_ray()
    if ray is None:
        return None
    _px, origin, direction = ray
    n = cammath.v_len(direction)
    if n < 1e-9:
        return None
    direction = tuple(v / n for v in direction)
    return tuple(origin[i] + direction[i] * cammath._clamp_dist(_focus["dist"])
                 for i in range(3))


def _forward_point(cam):
    """A synthetic pivot a focus-distance ahead of the eye (used when nothing better resolves);
    orbiting about it feels like turning around the thing in front of you."""
    d = cammath._clamp_dist(_focus["dist"])
    return (cam.location[0] + cam.forward[0] * d,
            cam.location[1] + cam.forward[1] * d,
            cam.location[2] + cam.forward[2] * d)


def _orbit_pivot(op, cam, idle, sel_override=True, candidates=None,
                 hold_sec=PIVOT_HOLD_IDLE):
    """Resolve the orbit pivot (a world point) for scheme ``op``, or None to turn in place about the
    eye (free-fly). When ``sel_override`` and actors are selected, the selection centre wins over
    view/cursor/origin (the designated pivot). Otherwise raycasts ignore the selection bbox gate
    and failures continue through the daemon-expanded global candidate chain."""
    center, bbox = _selection_center()
    if sel_override and op != "camera" and center is not None:
        return center
    ray_bbox = bbox if sel_override else None   # bbox gate only matters when override is on
    if _gesture["pivot"] is not None and not _gesture["invalid"] and idle <= hold_sec:
        return _gesture["pivot"]
    legacy = candidates is None
    for method in (candidates if candidates is not None else [op]):
        if method == "camera":
            point = tuple(cam.location)
        elif method == "origin":
            point = (0.0, 0.0, 0.0)
        elif method == "screen_center":
            point = _screen_center_pivot(cam, ray_bbox)
        elif method == "cursor":
            point = _cursor_pivot(ray_bbox)
        elif method == "object":
            point = _scene_center()
        elif method == "selection":
            point = center
        else:
            continue
        if point is not None:
            _gesture["pivot"] = point
            _gesture["invalid"] = False
            return point
    return _forward_point(cam) if legacy else None


def _zoom_toward(zm, idle=0.0, sel_override=True, hold_sec=PIVOT_HOLD_IDLE):
    """World point to dolly toward, or None for a straight-forward dolly."""
    center, bbox = _selection_center()
    if zm == "to_object":
        return _scene_center()                # may be None -> dolly straight along forward
    if zm == "to_cursor":
        if sel_override and center is not None:
            return center
        if _zoom_gesture["pivot"] is None or idle > hold_sec:
            ray_bbox = bbox if sel_override else None
            _zoom_gesture["pivot"] = _cursor_pivot(ray_bbox) or _cursor_depth_point()
        return _zoom_gesture["pivot"]
    return None                              # to_center -> dolly along forward


# ======================================================================================
# Frame application (MAIN THREAD)
# ======================================================================================
def _sgn(flag):
    """+1, or -1 when a per-mode invert flag is set."""
    return -1.0 if flag else 1.0


def _routed(values, sources, inversions, action, default):
    """One independently routed action from a 3-axis semantic input vector."""
    try:
        source = int(sources.get(action, default))
    except (TypeError, ValueError):
        source = default
    if source not in (0, 1, 2):
        source = default
    return values[source] * _sgn(inversions.get(action))


def _apply_action_routing(nav_mode, op, o, p, z, adv):
    """Per-mode X/Y/Z source selection plus direction inversion.

    Rotation actions select from ``o``; shifted movement actions select from ``(p.x, p.y, z)``.
    This keeps mode semantics independent and permits mappings such as Walk Forward <- Z (twist).
    """
    inv = adv.get("invert") or {}
    axes = adv.get("axis_source") or {}
    rotation = list(o)
    movement = [p[0], p[1], z]
    if nav_mode == "fly":
        f = inv.get("fly", {})
        a = axes.get("fly", {})
        o = [_routed(rotation, a, f, "pitch", 0), _routed(rotation, a, f, "yaw", 1),
             _routed(rotation, a, f, "bank", 2)]
        p = [_routed(movement, a, f, "strafe", 0),
             _routed(movement, a, f, "forward", 1)]
        z = _routed(movement, a, f, "vertical", 2)
    elif nav_mode == "walk":
        w = inv.get("walk", {})
        a = axes.get("walk", {})
        o = [_routed(rotation, a, w, "pitch", 0), _routed(rotation, a, w, "yaw", 1), rotation[2]]
        p = [_routed(movement, a, w, "strafe", 0),
             _routed(movement, a, w, "forward", 1)]
        z = _routed(movement, a, w, "vertical", 2)
    else:                                            # orbit
        ob = inv.get("orbit", {})
        oa = axes.get("orbit", {})
        if nav_mode == "orbit" and op == "camera":
            vp = inv.get("camera", {})
            va = axes.get("camera", {})
            o = [_routed(rotation, va, vp, "pitch", 0), _routed(rotation, va, vp, "yaw", 1),
                 _routed(rotation, va, vp, "roll", 2)]
        else:
            o = [_routed(rotation, oa, ob, "pitch", 0), _routed(rotation, oa, ob, "yaw", 1),
                 _routed(rotation, oa, ob, "twist", 2)]
        p = [_routed(movement, oa, ob, "pan_x", 0),
             _routed(movement, oa, ob, "pan_y", 1)]
        z = _routed(movement, oa, ob, "zoom", 2)
    return o, p, z


def _apply_host_baseline(nav_mode, twist_action, o, p, z, adv):
    baseline = adv.get("host_baseline") or {}
    orbit = baseline.get("orbit", [1.0, 1.0, 1.0])
    pan = baseline.get("pan", [1.0, 1.0])
    zoom = float(baseline.get("zoom", 1.0))
    move = float(baseline.get("move", 1.0))
    if nav_mode == "orbit":
        twist_factor = zoom if twist_action in ("zoom", "dolly") else float(orbit[2])
        o = [o[0] * float(orbit[0]), o[1] * float(orbit[1]), o[2] * twist_factor]
        p = [p[i] * float(pan[i]) for i in range(2)]
        z *= zoom
    else:
        o = [o[i] * float(orbit[i]) for i in range(3)]
        p = [v * move for v in p]
        z *= move
    return o, p, z


def _apply_orbit(cam, o, p, z, op, style, zm, twist_action, zoom_style, lock, pan_scales, idle,
                 sel_override=True, pivot_candidates=None, orbit_hold=PIVOT_HOLD_IDLE,
                 zoom_hold=PIVOT_HOLD_IDLE):
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
                if twist_action == "zoom" and _apply_lens_zoom(cam, twist):
                    pass
                else:
                    cammath.dolly(cam, twist, _focus["dist"])
                _gesture["invalid"] = True
                did = True
            # "none" (or roll while horizon-locked): twist ignored
        if orbit_o[0] or orbit_o[1] or orbit_o[2]:
            pivot = _orbit_pivot(op, cam, idle, sel_override=sel_override,
                                 candidates=pivot_candidates or [op], hold_sec=orbit_hold)
            if pivot is None:
                return did
            if pivot is not None:
                _focus["dist"] = cammath._clamp_dist(
                    cammath.v_len(cammath.v_sub(tuple(cam.location), pivot)))
            cammath.orbit(cam, orbit_o, (style == "turntable") or lock, pivot)
            _zoom_gesture["pivot"] = None            # view rotates -> next zoom re-raycasts
            return True
        return did
    if p[0] or p[1]:
        _log_rl("rx_pan", "rx pan p=(%.4f,%.4f)" % (p[0], p[1]))
        _gesture.update({"pivot": None, "invalid": True})
        cammath.pan(cam, p[0], p[1], _focus["dist"] if pan_scales else cammath.DIST_DEFAULT)
        return True
    if z:
        _log_rl("rx_zoom", "rx zoom z=%.4f zm=%s" % (z, zm))
        _gesture.update({"pivot": None, "invalid": True})
        toward = _zoom_toward(zm, idle, sel_override=sel_override, hold_sec=zoom_hold)
        if zoom_style == "zoom" and _apply_lens_zoom(cam, z, toward):
            pass
        else:
            cammath.dolly(cam, z, _focus["dist"], toward)
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
        _gesture.update({"pivot": None, "invalid": True})
        _zoom_gesture["pivot"] = None
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
        _gesture.update({"pivot": None, "invalid": True})
        _zoom_gesture["pivot"] = None
        return True
    return False


def _actor_frame(actor):
    location = actor.get_actor_location()
    forward, right, up = _basis_from_rotator(actor.get_actor_rotation())
    return cammath.Camera(
        (location.x, location.y, location.z), forward, right, up)


def _write_actor_frame(actor, frame):
    rotation = _rotator_from_basis(frame.forward, frame.up)
    location = unreal.Vector(frame.location[0], frame.location[1], frame.location[2])
    actor.set_actor_location(location, False, False)
    actor.set_actor_rotation(rotation, False)


def _apply_object(cam, o, p, z, idle):
    """Rotate or translate the selected actor roots as one view-relative group."""
    if not (o[0] or o[1] or o[2] or p[0] or p[1] or z):
        return False
    actors = _selected_actor_roots()
    if not actors:
        _log_rl("object_selection", "Object mode input ignored: no level actors are selected")
        _end_object_transaction()
        return False

    _begin_object_transaction(idle)
    frames = []
    for actor in actors:
        try:
            actor.modify()
        except Exception:
            pass
        try:
            frames.append((actor, _actor_frame(actor)))
        except Exception:
            continue
    if not frames:
        return False

    if o[0] or o[1] or o[2]:
        count = float(len(frames))
        pivot = tuple(sum(frame.location[i] for _actor, frame in frames) / count
                      for i in range(3))
        for actor, frame in frames:
            cammath.rotate_object(frame, o, cam, pivot)
            _write_actor_frame(actor, frame)
    else:
        delta = cammath.object_translation(p, z, cam, _focus["dist"])
        for actor, frame in frames:
            frame.location = list(cammath.v_add(tuple(frame.location), delta))
            _write_actor_frame(actor, frame)

    _object_transaction["t"] = time.time()
    _obj_cache.update(t=0.0, center=None, bbox=None)
    _scene_cache.update(t=0.0, center=None)
    return True


def _apply(info, frame, idle):
    """Apply one nav frame to the active perspective viewport camera. Exactly one of orbit / pan /
    zoom is non-zero per frame (the daemon gates them on Shift). Reads o/p/z/op/os/zm AND the
    advanced nav options ('adv': nav_mode/lock_horizon/twist_action/pan_scales/speeds/invert) the
    daemon attaches for Unreal (the same additive 'adv' object Blender uses)."""
    o = list(frame.get("o", [0.0, 0.0, 0.0]))
    p = list(frame.get("p", [0.0, 0.0]))
    z = float(frame.get("z", 0.0))
    op = frame.get("op", "screen_center")
    style = frame.get("os", "free")
    zm = frame.get("zm", "to_center")
    adv = frame.get("adv") or {}
    nav_mode = adv.get("nav_mode", "orbit")
    lock = bool(adv.get("lock_horizon", False))
    twist_action = adv.get("twist_action", "roll")
    zoom_style = adv.get("zoom_style", "dolly")
    pan_scales = bool(adv.get("pan_scales_with_distance", True))
    sel_override = bool(adv.get("selection_overrides_pivot", True))
    orbit_hold = max(0.0, min(10.0, float(adv.get("orbit_hold_sec", PIVOT_HOLD_IDLE))))
    zoom_hold = max(0.0, min(10.0, float(adv.get("zoom_hold_sec", PIVOT_HOLD_IDLE))))

    sig = (nav_mode, op, style, zm, twist_action, zoom_style, lock, sel_override)
    if sig != _last_scheme["v"]:
        _last_scheme["v"] = sig
        _log("scheme: nav=%s pivot=%s style=%s zoom=%s twist=%s pan_zoom=%s horizon=%s sel_override=%s" % sig)

    o, p, z = _apply_action_routing(nav_mode, op, o, p, z, adv)
    o, p, z = _apply_host_baseline(nav_mode, twist_action, o, p, z, adv)

    cam = _read_camera(info)

    # Level ONCE when the effective mode transitions into a fixed-horizon mode (turntable orbit,
    # lock-horizon, or walk) and the daemon's toggle is on. Transitions only -- prev
    # None (fresh session) never levels, and ordinary fixed-mode frames never re-level.
    fixed = (nav_mode == "walk") or (
        nav_mode == "orbit" and (style == "turntable" or lock))
    prev = _horizon["fixed"]
    _horizon["fixed"] = fixed
    leveled = False
    if fixed and prev is False and bool(adv.get("level_horizon_on_entry", True)):
        leveled = cammath.level_horizon(cam)
        if leveled:
            _log("horizon: leveled on fixed-horizon mode entry (nav=%s style=%s)"
                 % (nav_mode, style))

    if nav_mode == "object":
        changed = _apply_object(cam, o, p, z, idle)
        leveled = False
    elif nav_mode == "fly":
        _end_object_transaction()
        changed = _apply_fly(cam, o, p, z, adv)
    elif nav_mode == "walk":
        _end_object_transaction()
        changed = _apply_walk(cam, o, p, z, adv)
    else:                                            # orbit
        _end_object_transaction()
        changed = _apply_orbit(cam, o, p, z, op, style, zm, twist_action, zoom_style,
                               lock, pan_scales, idle,
                               sel_override=sel_override,
                               pivot_candidates=adv.get("orbit_pivot_candidates") or [op],
                               orbit_hold=orbit_hold, zoom_hold=zoom_hold)

    if nav_mode == "object":
        if changed:
            _log_rl("applied", "applied nav=object actors=%d" % len(_selected_actor_roots()))
    elif changed or leveled:
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
            if (_object_transaction["scope"] is not None and
                    time.time() - _object_transaction["t"] > OBJECT_GESTURE_IDLE):
                _end_object_transaction()
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
    _zoom_gesture["pivot"] = None
    _focus["dist"] = cammath.DIST_DEFAULT
    _georef_logged["missing"] = False
    _end_object_transaction()
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
    _end_object_transaction()
    try:
        if _tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(_tick_handle)
            _tick_handle = None
    except Exception:
        pass
    _log("stop: TrackballNav v%s" % ADDIN_VERSION)
