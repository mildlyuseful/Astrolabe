"""Headless unit tests for the FreeCAD add-on's PURE camera math (tbnav_camera).

tbnav_camera has NO FreeCAD/pivy/PySide imports, so unlike the Blender add-on (which needs
`blender --background`) this runs under plain pytest. The add-on reads the live Coin camera
into the duck-typed Camera, calls these helpers, and writes the fields back; here we drive the
helpers directly and assert the geometry.

Conventions verified live against FreeCAD 1.1 / Coin3D (docs/freecad_driver_notes.md):
  quaternions are (x,y,z,w); q_rotate matches Coin SbRotation.multVec; Z is world up.
"""
import math
import os
import sys

# Don't write a __pycache__ into the bundled add-on dir (it's shipped package data).
sys.dont_write_bytecode = True

# Make the bundled add-on's pure-math module importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "trackball_daemon", "plugins", "freecad", "TrackballNav"))

import tbnav_camera as cam  # noqa: E402


def vclose(u, v, eps=1e-6):
    return all(abs(u[i] - v[i]) <= eps for i in range(3))


# --- quaternion core: q_rotate must match Coin's multVec --------------------------------
def test_q_rotate_matches_coin_90_about_z():
    # +90 deg about Z: (1,0,0) -> (0,1,0). Coin SbRotation(axis=Z,90deg).getValue() == (0,0,0.7071,0.7071)
    q = cam.q_axis_angle((0.0, 0.0, 1.0), math.pi / 2)
    assert vclose(q[:3], (0.0, 0.0, math.sin(math.pi / 4))) and abs(q[3] - math.cos(math.pi / 4)) < 1e-6
    assert vclose(cam.q_rotate(q, (1.0, 0.0, 0.0)), (0.0, 1.0, 0.0))


def test_q_mul_compose_order():
    # q_rotate(q_mul(a,b), v) == q_rotate(a, q_rotate(b, v))
    a = cam.q_axis_angle((0.0, 0.0, 1.0), 0.5)
    b = cam.q_axis_angle((1.0, 0.0, 0.0), 0.3)
    v = (0.3, -0.7, 1.1)
    assert vclose(cam.q_rotate(cam.q_mul(a, b), v), cam.q_rotate(a, cam.q_rotate(b, v)))


def _ortho_cam():
    # identity orientation: right=+X, up=+Y, fwd=-Z; eye at z=10 looking at origin
    return cam.Camera(position=(0.0, 0.0, 10.0), orientation=(0.0, 0.0, 0.0, 1.0),
                      focal=10.0, height=20.0, is_ortho=True)


# --- axes / look-at ---------------------------------------------------------------------
def test_axes_identity():
    right, up, fwd, back = cam.axes(_ortho_cam())
    assert vclose(right, (1, 0, 0)) and vclose(up, (0, 1, 0))
    assert vclose(fwd, (0, 0, -1)) and vclose(back, (0, 0, 1))


def test_look_at_identity():
    assert vclose(cam.look_at(_ortho_cam()), (0, 0, 0))


# --- orbit is TRUE 1:1 at the default scale (not halved) --------------------------------
def test_orbit_scale_is_unity_full_angle():
    # Sensitivity 1.0 must rotate the view by the FULL ball angle -- matches the --debug cube and the
    # other eye+target apps (Fusion/SolidWorks/Onshape all use orbit magnitude 1.0). Guards against
    # re-introducing Blender's RegionView3D-specific 0.5 halving (which made FreeCAD orbit half-speed).
    assert cam.ORBIT_SCALE == (1.0, 1.0, 1.0)
    c = _ortho_cam()
    theta = 0.3
    R = cam.orbit_R(c, 0.0, theta, 0.0, False)            # pure yaw of theta
    angle = 2.0 * math.acos(max(-1.0, min(1.0, R[3])))    # rotation angle of the quaternion
    assert abs(angle - theta) < 1e-9                      # full theta, NOT theta/2


# --- orbit about a pivot: eye rotates rigidly about P, focal preserved -------------------
def test_orbit_about_pivot_is_rigid():
    c = _ortho_cam()
    P = (0.0, 0.0, 0.0)                       # orbit about the look-at
    eye0 = tuple(c.position)
    R = cam.orbit_R(c, 0.0, 0.4, 0.0, False)  # yaw
    cam.apply_world_rotation(c, R, P)
    # |eye - P| preserved and eye-P == R*(eye0-P)
    assert abs(cam.v_len(cam.v_sub(tuple(c.position), P)) - cam.v_len(cam.v_sub(eye0, P))) < 1e-6
    assert vclose(cam.v_sub(tuple(c.position), P), cam.q_rotate(R, cam.v_sub(eye0, P)))
    assert abs(c.focal - 10.0) < 1e-9         # focalDistance unchanged by rotation


def test_orbit_about_pivot_keeps_pivot_as_lookat():
    # orbiting about the current look-at must keep the look-at fixed in world space
    c = _ortho_cam()
    L0 = cam.look_at(c)
    cam.orbit(c, (0.2, -0.3, 0.0), False, L0)
    assert vclose(cam.look_at(c), L0, eps=1e-5)


def test_orbit_none_pivot_rotates_in_place():
    # pivot=None leaves the eye fixed (look-around), but still rotates the orientation
    c = _ortho_cam()
    eye0 = tuple(c.position)
    ori0 = list(c.orientation)
    cam.orbit(c, (0.3, 0.0, 0.0), False, None)
    assert vclose(tuple(c.position), eye0)
    assert any(abs(c.orientation[i] - ori0[i]) > 1e-4 for i in range(4))


def test_orbit_roundtrip_identity():
    c = _ortho_cam()
    ori0 = list(c.orientation)
    R = cam.orbit_R(c, 0.3, -0.2, 0.1, False)
    cam.apply_world_rotation(c, R, None)
    cam.apply_world_rotation(c, cam.q_normalize((-R[0], -R[1], -R[2], R[3])), None)  # inverse
    assert (all(abs(c.orientation[i] - ori0[i]) < 1e-5 for i in range(4)) or
            all(abs(c.orientation[i] + ori0[i]) < 1e-5 for i in range(4)))


# --- turntable keeps the horizon level; free orbit tilts it -----------------------------
def test_turntable_horizon_locked():
    c = _ortho_cam()
    import random
    random.seed(3)
    for _ in range(10):
        R = cam.orbit_R(c, random.uniform(-0.4, 0.4), random.uniform(-0.4, 0.4), 0.0, True)
        cam.apply_world_rotation(c, R, (0, 0, 0))
        right = cam.axes(c)[0]
        assert abs(right[2]) < 1e-9          # world-right stays horizontal => verticals stay vertical


def test_free_orbit_tilts_horizon():
    c = _ortho_cam()
    cam.apply_world_rotation(c, cam.orbit_R(c, 0.5, 0.0, 0.0, False), (0, 0, 0))
    cam.apply_world_rotation(c, cam.orbit_R(c, 0.0, 0.5, 0.0, False), (0, 0, 0))
    assert abs(cam.axes(c)[0][2]) > 1e-3


def test_turntable_drops_twist():
    # o[2] (twist) must not affect a turntable orbit (roll suppressed)
    a = _ortho_cam()
    b = _ortho_cam()
    cam.apply_world_rotation(a, cam.orbit_R(a, 0.2, 0.3, 0.0, True), (0, 0, 0))
    cam.apply_world_rotation(b, cam.orbit_R(b, 0.2, 0.3, 0.9, True), (0, 0, 0))
    assert all(abs(a.orientation[i] - b.orientation[i]) < 1e-9 for i in range(4))


# --- pan --------------------------------------------------------------------------------
def test_pan_moves_in_view_plane_scaled_by_size():
    c = _ortho_cam()
    k = cam.PAN_SCALE * cam.view_size(c)     # view_size == height for ortho
    cam.pan(c, 1.0, 0.0)
    assert vclose(tuple(c.position), (cam.PAN_SIGN[0] * k, 0.0, 10.0))
    c = _ortho_cam()
    cam.pan(c, 0.0, 1.0)
    assert vclose(tuple(c.position), (0.0, cam.PAN_SIGN[1] * k, 10.0))
    assert abs(c.focal - 10.0) < 1e-9        # pan never changes focal


# --- zoom: ortho scales height; perspective dollies -------------------------------------
def test_zoom_ortho_scales_height_to_center():
    c = _ortho_cam()
    L0 = cam.look_at(c)
    cam.zoom(c, 1.0, None)                    # to_center -> pivot = look-at
    s = 1.0 - cam.ZOOM_SIGN * 1.0 * cam.ZOOM_SCALE
    assert abs(c.height - 20.0 * s) < 1e-6    # height scaled by s
    assert vclose(cam.look_at(c), L0, eps=1e-6)   # look-at unchanged for to_center
    assert c.height < 20.0                    # positive z zooms IN (smaller height)


def test_zoom_ortho_to_pivot_keeps_pivot_fixed():
    # zoom toward an off-centre pivot keeps that world point's screen projection fixed:
    # its offset from the look-at scales by the same factor s as the view height.
    c = _ortho_cam()
    P = (4.0, 0.0, 0.0)
    L0 = cam.look_at(c)
    off0 = cam.v_sub(P, L0)
    cam.zoom(c, 1.0, P)
    s = 1.0 - cam.ZOOM_SIGN * 1.0 * cam.ZOOM_SCALE
    off1 = cam.v_sub(P, cam.look_at(c))
    assert vclose(off1, cam.v_scale(off0, s), eps=1e-6)


def test_zoom_persp_dollies_along_forward():
    c = cam.Camera(position=(0.0, 0.0, 10.0), orientation=(0.0, 0.0, 0.0, 1.0),
                   focal=10.0, height=None, height_angle=math.radians(45.0), is_ortho=False)
    cam.zoom(c, 1.0, None)
    # eye dollied along fwd=(0,0,-1): z decreases; focal reduced so look-at stays put
    assert c.position[2] < 10.0
    assert c.focal < 10.0
    assert vclose(cam.look_at(c), (0, 0, 0), eps=1e-6)


def test_zoom_out_increases_size():
    c = _ortho_cam()
    cam.zoom(c, -1.0, None)
    assert c.height > 20.0                    # negative z zooms OUT
