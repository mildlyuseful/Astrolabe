"""Headless unit tests for the Unreal add-on's PURE camera math (tbnav_unreal_camera).

That module has NO `unreal` import, so this runs under plain pytest (a step better than the
Blender add-on, which needs `blender --background`). The add-on reads the live editor viewport
camera (eye location + FRotator) into the duck-typed Camera, calls these helpers, then writes
location + rotator back; here we drive the helpers directly and assert the geometry.

Conventions VERIFIED live against Unreal Engine 5.8 (docs/apps/unreal.md) -- not derived
from algebra, OBSERVED via a headless pythonscript probe:
  left-handed, Z-up, centimetres, FRotator in degrees;
  identity rotator -> forward +X / right +Y / up +Z;  yaw+90 -> forward +Y;  pitch+90 -> forward +Z.
"""
import math
import os
import sys

# Don't write a __pycache__ into the bundled plugin dir (it's shipped package data).
sys.dont_write_bytecode = True

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "trackball_daemon", "plugins", "unreal",
                                "TrackballNav", "Content", "Python"))

import tbnav_unreal_camera as cam  # noqa: E402


def vclose(u, v, eps=1e-6):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


def _idcam(z=1000.0):
    """Identity-basis camera at (0,0,z): forward +X, right +Y, up +Z."""
    return cam.Camera((0.0, 0.0, z), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


# --- the VERIFIED Unreal conventions, reproduced by the pure rotator<->basis math -------
def test_identity_basis_matches_unreal():
    f, r, u = cam.rotator_to_basis(0.0, 0.0, 0.0)
    assert vclose(f, (1, 0, 0)) and vclose(r, (0, 1, 0)) and vclose(u, (0, 0, 1))


def test_yaw90_forward_to_plus_y():
    # Observed live: yaw +90 swings forward +X -> +Y (right-handed +90 about world Z).
    f, _r, _u = cam.rotator_to_basis(0.0, 90.0, 0.0)
    assert vclose(f, (0, 1, 0))


def test_pitch90_forward_to_plus_z():
    # Observed live: pitch +90 (nose up) swings forward +X -> +Z.
    f, _r, _u = cam.rotator_to_basis(90.0, 0.0, 0.0)
    assert vclose(f, (0, 0, 1))


def test_rotator_basis_roundtrip():
    # make_rot_from_xz semantics: forward fixed, up resolves roll. The pure pair must round-trip.
    for (p, y, r) in [(0, 0, 0), (0, 90, 0), (10, 20, 30), (-25, 170, -40), (44, -100, 12)]:
        f, _ri, u = cam.rotator_to_basis(p, y, r)
        p2, y2, r2 = cam.basis_to_rotator(f, u)
        f2, _ri2, u2 = cam.rotator_to_basis(p2, y2, r2)
        assert vclose(f, f2, 1e-6) and vclose(u, u2, 1e-6)


def test_camera_rotator_roundtrip():
    c = cam.Camera.from_rotator((1, 2, 3), 15.0, -40.0, 22.0)
    p, y, r = c.rotator()
    f2, _r2, u2 = cam.rotator_to_basis(p, y, r)
    assert vclose(c.forward, f2) and vclose(c.up, u2)


# --- orbit baseline is DOUBLED for hardware feel (default felt half on the device) -----------
def test_camera_math_is_neutral_and_daemon_profile_carries_doubled_orbit():
    from trackball_daemon.config import host_baseline_payload
    assert cam.ORBIT_SCALE == (1.0, 1.0, 1.0)
    assert host_baseline_payload("unreal")["orbit"] == [2.0, 2.0, 2.0]
    c = _idcam()
    theta = 0.2
    cam.orbit(c, (0.0, theta, 0.0), False, None)      # pure yaw, in place
    ang = math.atan2(c.forward[1], c.forward[0])
    assert abs(ang - theta) < 1e-9                    # camera math itself is neutral


# --- orbit about a pivot: eye rotates rigidly about P, basis stays orthonormal -----------
def test_orbit_about_pivot_is_rigid():
    c = _idcam()
    P = (0.0, 0.0, 0.0)
    eye0 = tuple(c.location)
    cam.orbit(c, (0.0, 0.4, 0.0), False, P)
    # |eye - P| preserved (rigid rotation about the pivot)
    assert abs(cam.v_len(cam.v_sub(tuple(c.location), P))
               - cam.v_len(cam.v_sub(eye0, P))) < 1e-6
    # basis stays orthonormal
    assert abs(cam.v_len(c.forward) - 1) < 1e-9
    assert abs(cam.v_dot(c.forward, c.up)) < 1e-9
    assert abs(cam.v_dot(c.forward, c.right)) < 1e-9


def test_orbit_none_pivot_turns_in_place():
    # pivot=None leaves the eye fixed (free-fly "turn the camera"), but rotates the orientation.
    c = _idcam()
    eye0 = tuple(c.location)
    fwd0 = c.forward
    cam.orbit(c, (0.3, 0.0, 0.0), False, None)
    assert vclose(tuple(c.location), eye0)
    assert not vclose(c.forward, fwd0)


def test_orbit_single_axis_roundtrip():
    # A pure yaw then its negation about the (unchanged) up axis returns exactly to start.
    c = _idcam()
    fwd0, up0 = c.forward, c.up
    cam.orbit(c, (0.0, 0.35, 0.0), False, None)
    cam.orbit(c, (0.0, -0.35, 0.0), False, None)
    assert vclose(c.forward, fwd0, 1e-9) and vclose(c.up, up0, 1e-9)


# --- turntable keeps the horizon level + drops twist; free orbit tilts it ----------------
def test_turntable_horizon_locked():
    import random
    random.seed(5)
    c = _idcam()
    for _ in range(12):
        cam.orbit(c, (random.uniform(-0.3, 0.3), random.uniform(-0.3, 0.3), 0.0), True, (0, 0, 0))
        assert abs(c.right[2]) < 1e-9        # world-right stays horizontal => verticals stay vertical


def test_turntable_drops_twist():
    a = _idcam()
    b = _idcam()
    cam.orbit(a, (0.2, 0.3, 0.0), True, (0, 0, 0))
    cam.orbit(b, (0.2, 0.3, 0.9), True, (0, 0, 0))   # twist (o2) must be ignored in turntable
    assert vclose(a.forward, b.forward) and vclose(a.up, b.up)


def test_free_orbit_tilts_horizon():
    c = _idcam()
    cam.orbit(c, (0.5, 0.0, 0.0), False, (0, 0, 0))
    cam.orbit(c, (0.0, 0.5, 0.0), False, (0, 0, 0))
    assert abs(c.right[2]) > 1e-3


# --- pan: in the camera right/up plane, scaled by the focus distance ---------------------
def test_pan_moves_in_camera_plane_scaled_by_dist():
    dist = 500.0
    k = cam.PAN_SCALE * dist
    c = _idcam()
    cam.pan(c, 1.0, 0.0, dist)                        # right = +Y
    assert vclose(tuple(c.location), (0.0, cam.PAN_SIGN[0] * k, 1000.0))
    c = _idcam()
    cam.pan(c, 0.0, 1.0, dist)                        # up = +Z
    assert vclose(tuple(c.location), (0.0, 0.0, 1000.0 + cam.PAN_SIGN[1] * k))


def test_pan_scales_with_distance():
    a = _idcam()
    b = _idcam()
    cam.pan(a, 1.0, 0.0, 100.0)
    cam.pan(b, 1.0, 0.0, 200.0)
    # twice the focus distance => twice the world move (zoom-stable feel)
    assert abs((b.location[1]) - 2.0 * (a.location[1])) < 1e-6


# --- zoom: dolly the eye along forward (or toward a point), scaled by the focus distance ----
def test_dolly_moves_along_forward_scaled_by_dist():
    dist = 400.0
    c = _idcam()
    cam.dolly(c, 1.0, dist)
    k = cam.ZOOM_SIGN * 1.0 * cam.ZOOM_SCALE * dist   # forward = +X
    assert vclose(tuple(c.location), (k, 0.0, 1000.0))
    assert c.location[0] > 0                           # positive z dollies forward (zoom IN)


def test_dolly_out_is_negative():
    c = _idcam()
    cam.dolly(c, -1.0, 400.0)
    assert c.location[0] < 0                           # negative z dollies back (zoom OUT)


def test_dolly_toward_point():
    c = _idcam()                                       # eye at (0,0,1000)
    cam.dolly(c, 1.0, 400.0, toward=(0.0, 0.0, 0.0))   # head straight down toward origin
    assert c.location[2] < 1000.0
    assert abs(c.location[0]) < 1e-9 and abs(c.location[1]) < 1e-9


def test_clamp_dist_floor_and_default():
    assert cam._clamp_dist(0.0) == cam.DIST_DEFAULT     # 0 -> default (avoids a dead pan/zoom)
    assert cam._clamp_dist(-5.0) == cam.DIST_DEFAULT
    assert cam._clamp_dist(1e12) == cam.DIST_MAX        # clamp huge distances
    assert cam._clamp_dist(500.0) == 500.0


# --- fly / walk (Blender-parity ports) -- the modes are NOT identical -------------------
def test_look_free_banks_on_twist():
    # FLY look (horizon_lock False): twist banks/rolls -> world-right tilts out of horizontal, and the
    # eye stays put (look turns the camera in place).
    c = _idcam()
    cam.look(c, (0.0, 0.0, 0.3), False)                 # pure twist
    assert abs(c.right[2]) > 1e-3                        # banked
    assert vclose(tuple(c.location), (0.0, 0.0, 1000.0))


def test_look_horizon_locked_drops_twist():
    # WALK look (horizon_lock True): twist is dropped and the horizon stays level (no bank).
    c = _idcam()
    cam.look(c, (0.2, 0.0, 0.9), True)                  # pitch + a big twist
    assert abs(c.right[2]) < 1e-9                        # right stays horizontal
    assert vclose(tuple(c.location), (0.0, 0.0, 1000.0))  # eye fixed


def test_fly_move_follows_camera_forward():
    c = _idcam()                                        # level: forward = +X
    cam.fly_move(c, [0.0, 1.0], 0.0, 400.0, 1.0)        # p[1] = forward thrust
    k = cam.MOVE_SCALE * 1.0 * 400.0
    assert vclose(tuple(c.location), (k, 0.0, 1000.0))


def test_fly_vs_walk_differ_when_pitched():
    # THE key difference (why they are not redundant): pitch the camera, then move forward.
    # Fly dives along the look direction (z changes); walk stays on the ground plane (z fixed).
    a = _idcam()
    b = _idcam()
    cam.orbit(a, (-0.3, 0.0, 0.0), False, None)         # pitch a
    cam.orbit(b, (-0.3, 0.0, 0.0), False, None)         # pitch b identically
    za, zb = a.location[2], b.location[2]
    cam.fly_move(a, [0.0, 1.0], 0.0, 400.0, 1.0)        # fly forward
    cam.walk_move(b, [0.0, 1.0], 0.0, 400.0, 1.0)       # walk forward
    assert abs(a.location[2] - za) > 1e-3               # fly: vertical component (dives/climbs)
    assert abs(b.location[2] - zb) < 1e-9               # walk: forward never changes height


def test_walk_vertical_is_world_z():
    c = _idcam()
    cam.orbit(c, (-0.3, 0.0, 0.0), False, None)         # pitch so camera-up != world-up
    z0 = c.location[2]
    cam.walk_move(c, [0.0, 0.0], 1.0, 400.0, 1.0)       # z = vertical
    k = cam.MOVE_SCALE * 1.0 * 400.0
    assert abs(c.location[2] - (z0 + k)) < 1e-6         # straight up world-Z
    assert abs(c.location[0]) < 1e-6 and abs(c.location[1]) < 1e-6


def test_move_scales_with_speed():
    a = _idcam()
    b = _idcam()
    cam.fly_move(a, [0.0, 1.0], 0.0, 400.0, 1.0)
    cam.fly_move(b, [0.0, 1.0], 0.0, 400.0, 2.0)        # double speed -> double move
    assert abs(b.location[0] - 2.0 * a.location[0]) < 1e-6


def test_horizontal_projects_to_xy():
    assert vclose(cam.horizontal((3.0, 0.0, 4.0)), (1.0, 0.0, 0.0))
    assert vclose(cam.horizontal((0.0, 0.0, 5.0)), (0.0, 0.0, 0.0))   # degenerate -> zero
