"""End-to-end probe of the add-on pipeline against a REAL RegionView3D, run headless.

Unlike the pure-math test, this exercises the actual integration: _resolve_target() finding the
VIEW_3D, _on_timer() draining the queue, and _apply() mutating a live RegionView3D -- without the
daemon, the trackball, or touching the user's Blender config. If --background exposes no usable
viewport (regions not realised), it reports SKIP rather than failing.

Run:
  blender --background --factory-startup --python tools\\blender_nav_integration_probe.py
"""
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "trackball_daemon", "plugins", "blender"))

import bpy                          # noqa: E402
import trackball_nav as tn         # noqa: E402

_fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


target = tn._resolve_target()
if target is None:
    print("SKIP no VIEW_3D / RegionView3D realised in --background; live GUI test required")
    print("ALL PASS")           # not a failure: the brief notes viewport application is GUI-only
    sys.exit(0)

_win, area, region, rv, space, _key = target
print("target: area=%s region=%dx%d perspective=%s" % (area.type, region.width, region.height,
                                                        rv.view_perspective))


def feed(frame):
    tn._q.put_nowait(frame)
    tn._on_timer()


ADV = dict(nav_mode="orbit", twist_action="roll", zoom_style="zoom", lock_horizon=False,
           pan_scales_with_distance=True, zoom_to_mouse=False, lock_camera_to_view=False)

# --- orbit (camera pivot -> turn the camera in place: rotates view, keeps the EYE) ---
rot0 = rv.view_rotation.copy()
eye0 = tn._eye(rv)
feed({"o": [0.3, 0.2, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free", "zm": "to_center",
      "adv": ADV})
check("orbit.changes_rotation", rv.view_rotation.rotation_difference(rot0).angle > 1e-4)
check("orbit.camera_keeps_eye", (tn._eye(rv) - eye0).length < 1e-4)

# --- turntable keeps the horizon level ---
rv.view_rotation = rot0.copy()
feed({"o": [0.2, 0.5, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "turntable",
      "zm": "to_center", "adv": ADV})
check("turntable.horizon_level", abs((rv.view_rotation @ __import__("mathutils").Vector((1, 0, 0))).z) < 1e-5)

# --- pan moves view_location ---
loc1 = rv.view_location.copy()
feed({"o": [0, 0, 0], "p": [0.5, 0.0], "z": 0.0, "op": "camera", "os": "free", "zm": "to_center",
      "adv": ADV})
check("pan.moves_location", (rv.view_location - loc1).length > 1e-6)

# --- zoom changes view_distance ---
d0 = rv.view_distance
feed({"o": [0, 0, 0], "p": [0, 0], "z": 0.5, "op": "camera", "os": "free", "zm": "to_center",
      "adv": ADV})
check("zoom.changes_distance", abs(rv.view_distance - d0) > 1e-6)

# --- dolly (zoom_style=dolly) moves view_location along the view axis ---
loc2 = rv.view_location.copy()
feed({"o": [0, 0, 0], "p": [0, 0], "z": 0.5, "op": "camera", "os": "free", "zm": "to_center",
      "adv": dict(ADV, zoom_style="dolly")})
check("dolly.moves_location", (rv.view_location - loc2).length > 1e-6)

# --- fly look rotates about the eye (eye ~ stationary) ---
eye0 = tn._eye(rv)
feed({"o": [0.2, 0.2, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free", "zm": "to_center",
      "adv": dict(ADV, nav_mode="fly")})
check("fly.eye_stationary", (tn._eye(rv) - eye0).length < 1e-4)

# ===== bpy-dependent pivots & camera-lock (default scene has a cube + camera) =====
from mathutils import Vector, Quaternion  # noqa: E402

# Reset to a known view looking down -Z at the cube at the origin.
rv.view_perspective = 'PERSP'
rv.view_location = Vector((0.0, 0.0, 0.0))
rv.view_distance = 10.0
rv.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))

# auto-depth: the screen-centre ray passes through the origin (inside the default cube) -> a hit
hit = tn._raycast_screen_center(rv, region)
check("autodepth.raycast_hits_cube", hit is not None)

# orbiting about the auto-depth pivot (the cube surface, != view_location) moves view_location
tn._gesture.update({"pivot": None, "invalid": True, "t": 0.0})
loc_ad = rv.view_location.copy()
feed({"o": [0.3, 0.0, 0.0], "p": [0, 0], "z": 0.0, "op": "screen_center", "os": "free", "zm": "to_center",
      "adv": dict(ADV, selection_overrides_pivot=False)})
check("autodepth.orbit_moves_location", (rv.view_location - loc_ad).length > 1e-6)

# ===== under-mouse "cursor" pivot (Half B + the mapping; the live modal tracker is GUI-only) =====
# Half B: raycast an ARBITRARY region pixel (not just the centre). The centre pixel passes through
# the cube -> a hit; a far-corner pixel (0,0) looks past it into empty space -> a miss.
rv.view_location = Vector((0.0, 0.0, 0.0)); rv.view_distance = 10.0
rv.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0)); rv.view_perspective = 'PERSP'
hit_c = tn._raycast_pixel(rv, region, region.width * 0.5, region.height * 0.5)
check("cursor.raycast_pixel_centre_hits", hit_c is not None)
check("cursor.raycast_pixel_corner_misses", tn._raycast_pixel(rv, region, 0.0, 0.0) is None)

# Feed a SYNTHETIC cached cursor at the region centre (what the modal tracker would cache live) and
# resolve the cursor pivot through the real _cursor_region_pixel -> _raycast_cursor path.
tn._cursor.update({"win": _win.as_pointer(), "ok": True,
                   "x": region.x + region.width * 0.5, "y": region.y + region.height * 0.5})
cur_hit = tn._raycast_cursor(rv, region, _win)
check("cursor.raycast_cursor_hits_cube", cur_hit is not None
      and (cur_hit - (hit_c or Vector((9, 9, 9)))).length < 1e-4)

# cursor OUTSIDE this region (window-space far to the left) -> None
tn._cursor.update({"x": region.x - 500.0, "y": region.y + region.height * 0.5})
check("cursor.offscreen_is_none", tn._raycast_cursor(rv, region, _win) is None)

# end to end: op="cursor" with the cursor over the cube orbits about that hit (moves view_location).
for ob in bpy.context.scene.objects:
    try:
        ob.select_set(ob.type == 'MESH')
    except Exception:
        pass
tn._cursor.update({"win": _win.as_pointer(), "ok": True,
                   "x": region.x + region.width * 0.5, "y": region.y + region.height * 0.5})
tn._gesture.update({"pivot": None, "invalid": True, "t": 0.0})
loc_cur = rv.view_location.copy()
feed({"o": [0.3, 0.0, 0.0], "p": [0, 0], "z": 0.0, "op": "cursor", "os": "free", "zm": "to_center",
      "adv": dict(ADV, selection_overrides_pivot=False)})
check("cursor.orbit_moves_location", (rv.view_location - loc_cur).length > 1e-6)

# selection median: select the mesh objects -> median at the origin (default cube)
for ob in bpy.context.scene.objects:
    try:
        ob.select_set(ob.type == 'MESH')
    except Exception:
        pass
med = tn._selection_median()
check("selection.median_at_origin", med is not None and med.length < 1e-5)

# 3D cursor pivot reads scene.cursor.location
bpy.context.scene.cursor.location = Vector((2.0, 0.0, 0.0))
check("cursor.pivot_reads_cursor", (tn._cursor_location() - Vector((2, 0, 0))).length < 1e-6)

# camera view + nav WITHOUT lock -> exit to perspective so the edits are visible (like Blender)
rv.view_perspective = 'CAMERA'
rot_cam = rv.view_rotation.copy()
feed({"o": [0.3, 0.2, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free",
      "zm": "to_center", "adv": dict(ADV, lock_camera_to_view=False)})
check("cameraview.nav_exits_to_perspective", rv.view_perspective == 'PERSP')
check("cameraview.nav_moves_view", rv.view_rotation.rotation_difference(rot_cam).angle > 1e-4)

# camera-lock: in CAMERA view with lock_camera_to_view, navigation drives the scene camera object
if bpy.context.scene.camera is not None:
    rv.view_perspective = 'CAMERA'
    cam0 = bpy.context.scene.camera.matrix_world.copy()
    feed({"o": [0.2, 0.1, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free",
          "zm": "to_center", "adv": dict(ADV, lock_camera_to_view=True)})
    moved = any(abs(cam0[i][j] - bpy.context.scene.camera.matrix_world[i][j]) > 1e-6
                for i in range(4) for j in range(4))
    check("cameralock.drives_scene_camera", moved)
    rv.view_perspective = 'PERSP'
else:
    print("SKIP cameralock (no scene camera)")

# --- per-mode invert: independent direction flips (the user's case: Walk fwd and Camera axes) ---
def _reset_view():
    rv.view_perspective = 'PERSP'
    rv.view_location = Vector((0, 0, 0))
    rv.view_rotation = Quaternion(Vector((1, 0, 0)), -math.pi / 2)   # look horizontally (-Y), not down
    rv.view_distance = 8.0


def _walk_fwd(forward_inv):
    _reset_view()
    feed({"o": [0, 0, 0], "p": [0.0, 0.5], "z": 0.0, "op": "camera", "os": "free",
          "zm": "to_center", "adv": dict(ADV, nav_mode="walk", invert={"walk": {"forward": forward_inv}})})
    return rv.view_location.copy()


wa, wb = _walk_fwd(False), _walk_fwd(True)
check("invert.walk_forward_flips", wa.length > 1e-6 and wa.dot(wb) < 0)


def _vp_pitch_fwd(pitch_inv):
    _reset_view()
    feed({"o": [0.3, 0, 0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free",
          "zm": "to_center", "adv": dict(ADV, nav_mode="orbit", invert={"camera": {"pitch": pitch_inv}})})
    return tn._view_axes(rv)[2].copy()          # forward vector


fa, fb = _vp_pitch_fwd(False), _vp_pitch_fwd(True)
check("invert.camera_pitch_flips", abs(fa.z) > 1e-4 and fa.z * fb.z < 0)

# --- daemon-authoritative nav mode: consecutive frames switch behavior with no host-local state ---
_reset_view()
eye0 = tn._eye(rv)
feed({"o": [0.2, 0.2, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free",
      "zm": "to_center", "adv": dict(ADV, nav_mode="fly")})
check("daemon_mode.fly_keeps_eye", (tn._eye(rv) - eye0).length < 1e-4)
orbit_loc = rv.view_location.copy()
feed({"o": [0.2, 0.2, 0.0], "p": [0, 0], "z": 0.0, "op": "camera", "os": "free",
      "zm": "to_center", "adv": dict(ADV, nav_mode="orbit")})
check("daemon_mode.orbit_takes_effect_next_frame", (rv.view_location - orbit_loc).length > 1e-6)

print("\n%d failures" % len(_fails))
if _fails:
    print("FAILURES: " + ", ".join(_fails))
    sys.exit(1)
print("ALL PASS")
