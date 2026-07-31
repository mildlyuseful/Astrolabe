# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Headless unit tests for the Blender add-on's PURE view math.

The brief: "unit-test the PURE math (mathutils quaternion orbit/turntable/roll, pan, zoom/dolly,
selection median) via `blender --background --python test.py`. (RegionView3D needs a GUI, so the
viewport application is GUI-only -- test the math headless, the viewport live.)"

The add-on's math helpers take a duck-typed view object (anything with .view_location / .view_rotation
/ .view_distance), so we drive them with a tiny stand-in instead of a real RegionView3D.

Run:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe" --background --factory-startup \
      --python tools\\blender_nav_math_test.py
Exit code is non-zero if any assertion fails.
"""
import math
import os
import sys

# Make the add-on importable (it lives in scripts/addons as `trackball_nav`).
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "trackball_daemon", "plugins", "blender"))

import trackball_nav as tn          # noqa: E402
from mathutils import Vector, Quaternion  # noqa: E402

_fails = []
_total = 0


def check(name, cond):
    global _total
    _total += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


def vclose(u, v, eps=1e-5):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


def qclose(a, b, eps=1e-5):
    # quaternions q and -q are the same rotation
    return (all(abs(a[i] - b[i]) <= eps for i in range(4)) or
            all(abs(a[i] + b[i]) <= eps for i in range(4)))


class RV:
    """Stand-in for RegionView3D: just the three attributes the math touches."""
    def __init__(self, loc=(0, 0, 0), rot=(1, 0, 0, 0), dist=6.0):
        self.view_location = Vector(loc)
        self.view_rotation = Quaternion(rot)
        self.view_distance = float(dist)


# --- axes / eye -----------------------------------------------------------------------
rv = RV()
right, up, fwd, back = tn._view_axes(rv)
check("axes.right", vclose(right, (1, 0, 0)))
check("axes.up", vclose(up, (0, 1, 0)))
check("axes.fwd_into_screen", vclose(fwd, (0, 0, -1)))
check("axes.back_toward_eye", vclose(back, (0, 0, 1)))
check("eye.identity", vclose(tn._eye(RV(dist=6.0)), (0, 0, 6)))

# --- the core trick: rotate-about-pivot keeps the eye rigid about P --------------------
rv = RV(loc=(1, 2, 3), rot=Quaternion(Vector((0, 1, 0)), 0.3), dist=5.0)
P = Vector((4, 1, -2))
old_eye = tn._eye(rv)
R = Quaternion(Vector((0, 0, 1)), 0.2)
tn._apply_world_rotation(rv, R, P)
check("pivot.eye_rotates_rigidly_about_P", vclose(tn._eye(rv) - P, R @ (old_eye - P)))

# viewpoint pivot (None) leaves view_location alone, still rotates
rv = RV(loc=(1, 2, 3))
loc0 = rv.view_location.copy()
tn._apply_world_rotation(rv, Quaternion(Vector((0, 0, 1)), 0.5), None)
check("viewpoint.location_unchanged", vclose(rv.view_location, loc0))
check("viewpoint.rotation_applied", not qclose(rv.view_rotation, Quaternion((1, 0, 0, 0))))

# rotate then inverse-rotate returns to start
rv = RV(rot=Quaternion(Vector((1, 1, 0)).normalized(), 0.2))
q0 = rv.view_rotation.copy()
Rr = Quaternion(Vector((1, 2, 3)).normalized(), 0.4)
tn._apply_world_rotation(rv, Rr, None)
tn._apply_world_rotation(rv, Rr.inverted(), None)
check("orbit.roundtrip_identity", qclose(rv.view_rotation, q0))

# --- turntable keeps the horizon level over many steps; free orbit tilts it ------------
rv = RV()
ok = True
import random  # noqa: E402
random.seed(1)
for _ in range(8):
    R = tn._orbit_R(rv, random.uniform(-0.4, 0.4), random.uniform(-0.4, 0.4), 0.0, True)
    tn._apply_world_rotation(rv, R, None)
    if abs(tn._view_axes(rv)[0].z) > 1e-6:        # world_right must stay horizontal
        ok = False
check("turntable.horizon_locked", ok)

rv = RV()
tn._apply_world_rotation(rv, tn._orbit_R(rv, 0.5, 0.0, 0.0, False), None)
tn._apply_world_rotation(rv, tn._orbit_R(rv, 0.0, 0.5, 0.0, False), None)
check("free.tilts_horizon", abs(tn._view_axes(rv)[0].z) > 1e-3)

# --- pan ------------------------------------------------------------------------------
rv = RV(dist=6.0)
tn._pan(rv, 1.0, 0.0, True)                       # right * PAN_SIGN[0]*1*PAN_SCALE*dist
check("pan.scales_with_distance", vclose(rv.view_location, (tn.PAN_SIGN[0] * tn.PAN_SCALE * 6.0, 0, 0)))
rv = RV(dist=6.0)
tn._pan(rv, 1.0, 0.0, False)                      # no distance factor
check("pan.no_distance_scale", vclose(rv.view_location, (tn.PAN_SIGN[0] * tn.PAN_SCALE, 0, 0)))
rv = RV(dist=4.0)
tn._pan(rv, 0.0, 1.0, True)                       # up * PAN_SIGN[1]*1*PAN_SCALE*dist
check("pan.vertical_uses_up_and_sign", vclose(rv.view_location, (0, tn.PAN_SIGN[1] * tn.PAN_SCALE * 4.0, 0)))

# --- zoom / dolly ---------------------------------------------------------------------
rv = RV(dist=6.0)
tn._zoom(rv, 1.0)                                 # factor = 1 - 1*ZOOM_SIGN*ZOOM_SCALE
factor = max(0.05, min(20.0, 1.0 - tn.ZOOM_SIGN * tn.ZOOM_SCALE))
check("zoom.in_reduces_distance", abs(rv.view_distance - 6.0 * factor) < 1e-6)
rv = RV(dist=6.0)
tn._zoom(rv, -1.0)
check("zoom.out_increases_distance", rv.view_distance > 6.0)
# zoom toward a pivot moves view_location toward it by `factor`
rv = RV(loc=(0, 0, 0), dist=6.0)
Pz = Vector((10, 0, 0))
factor = max(0.05, min(20.0, 1.0 - tn.ZOOM_SIGN * tn.ZOOM_SCALE))
tn._zoom(rv, 1.0, Pz)
check("zoom.to_pivot_shifts_location", vclose(rv.view_location, Pz + (Vector((0, 0, 0)) - Pz) * factor))
rv = RV(dist=6.0)
tn._dolly(rv, 1.0)                                # along fwd=(0,0,-1)
check("dolly.moves_along_forward", vclose(rv.view_location, (0, 0, -tn.DOLLY_SCALE * 1.0 * 6.0)))

# --- roll -----------------------------------------------------------------------------
rv = RV()
loc0 = rv.view_location.copy()
tn._roll(rv, 0.5)
right, _u, _f, _b = tn._view_axes(rv)
check("roll.location_unchanged", vclose(rv.view_location, loc0))
check("roll.rotates_about_forward", abs(right.z) < 1e-6 and not vclose(right, (1, 0, 0)))

# --- under-cursor pivot: WINDOW-space mouse -> REGION pixel (pure mapping) -------------
# region at window offset (300, 120), size 1000 x 700.
check("cursorpx.centre", tn._region_pixel_from_window(300, 120, 1000, 700, 800, 470) == (500, 350))
check("cursorpx.origin", tn._region_pixel_from_window(300, 120, 1000, 700, 300, 120) == (0, 0))
check("cursorpx.inside_margin",                         # 1px outside -> absorbed by the 2px margin
      tn._region_pixel_from_window(300, 120, 1000, 700, 299, 120) == (-1, 0))
check("cursorpx.left_of_region",                        # well left of the region -> None (fallback)
      tn._region_pixel_from_window(300, 120, 1000, 700, 100, 470) is None)
check("cursorpx.above_region",                          # above the region -> None
      tn._region_pixel_from_window(300, 120, 1000, 700, 800, 900) is None)

# --- selection median / horizontal ----------------------------------------------------
check("median.mean", vclose(tn._selection_median_from([Vector((0, 0, 0)), Vector((2, 2, 2))]), (1, 1, 1)))
check("median.empty_is_none", tn._selection_median_from([]) is None)
h = tn._horizontal(Vector((3, 4, 5)))
check("horizontal.projects_and_normalizes", vclose(h, (0.6, 0.8, 0)) and abs(h.length - 1.0) < 1e-6)
check("horizontal.degenerate_is_zero", vclose(tn._horizontal(Vector((0, 0, 9))), (0, 0, 0)))

# View keeps planar motion in the viewport and twist on depth. Ground mirrors Walk controls:
# planar Y is horizontal forward/back and twist is world up/down.
rv = RV(rot=Quaternion(Vector((1, 0, 0)), -math.pi / 3), dist=10.0)
_right, _up, fwd, _back = tn._view_axes(rv)
view_depth = tn._object_translation(rv, (0.0, 0.0), 1.0, "view")
ground_forward = tn._object_translation(rv, (0.0, 1.0), 0.0, "ground")
ground_up = tn._object_translation(rv, (0.0, 0.0), 1.0, "ground")
scaled_view_depth = tn._object_translation(rv, (0.0, 0.0), 1.0, "view", 2.5)
check("object.view_twist_is_view_depth", vclose(view_depth, fwd * 10.0))
check("object.ground_planar_y_is_horizontal_forward",
      vclose(ground_forward, tn._horizontal(fwd) * 10.0) and abs(ground_forward.z) < 1e-6)
check("object.ground_twist_is_world_up", vclose(ground_up, (0.0, 0.0, 10.0)))
check("object.sensitivity_scales_translation", vclose(scaled_view_depth, view_depth * 2.5))

# --- look (fly): rotates about the eye, which stays put -------------------------------
rv = RV(loc=(1, 2, 3), dist=5.0)
eye0 = tn._eye(rv)
tn._look(rv, 0.2, 0.3, 0.0, False)
check("look.eye_stationary", vclose(tn._eye(rv), eye0))

print("\n%d checks, %d failures" % (_total, len(_fails)))
if _fails:
    print("FAILURES: " + ", ".join(_fails))
    sys.exit(1)
print("ALL PASS")
