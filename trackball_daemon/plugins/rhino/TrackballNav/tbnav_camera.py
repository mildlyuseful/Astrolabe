"""Pure-Python eye+target camera math for the Rhino Trackball Nav add-on.

NO Rhino imports — unit-testable headless. Rhino is right-handed, Z-up (like FreeCAD /
SketchUp). Camera = eye + target + up; ORBIT_SCALE = 1.0 (eye+target family).
"""
import math

ORBIT_SCALE = (1.0, 1.0, 1.0)
ORBIT_SIGN = (1.0, 1.0, 1.0)
PAN_SIGN = (1.0, 1.0)
PAN_SCALE = 1.0
ZOOM_SIGN = 1.0
ZOOM_SCALE = 1.0
WORLD_UP = (0.0, 0.0, 1.0)
DIST_MIN = 1.0e-4


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_normalize(a):
    n = v_len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def rotate_about_axis(v, axis, angle):
    n = v_len(axis)
    if n < 1e-12 or abs(angle) < 1e-15:
        return v
    ax = (axis[0] / n, axis[1] / n, axis[2] / n)
    c, s = math.cos(angle), math.sin(angle)
    d = v_dot(ax, v)
    cr = v_cross(ax, v)
    omc = 1.0 - c
    return (v[0] * c + cr[0] * s + ax[0] * d * omc,
            v[1] * c + cr[1] * s + ax[1] * d * omc,
            v[2] * c + cr[2] * s + ax[2] * d * omc)


class Camera:
    """Eye + target + up (Rhino viewport camera)."""
    __slots__ = ("eye", "target", "up")

    def __init__(self, eye, target, up):
        self.eye = list(eye)
        self.target = list(target)
        self.up = list(up)

    def forward(self):
        return v_normalize(v_sub(tuple(self.target), tuple(self.eye)))

    def right(self):
        return v_normalize(v_cross(self.forward(), tuple(self.up)))

    def distance(self):
        return max(DIST_MIN, v_len(v_sub(tuple(self.target), tuple(self.eye))))


def _rotate_point(p, axis, angle, pivot):
    rel = v_sub(tuple(p), pivot)
    return list(v_add(pivot, rotate_about_axis(rel, axis, angle)))


def _rotate_vec(v, axis, angle):
    return list(rotate_about_axis(tuple(v), axis, angle))


def level_horizon(cam):
    """Remove existing roll: rebuild `up` so camera-right is horizontal (perpendicular to
    WORLD_UP) while the view direction is unchanged. eye and target are untouched, so the
    eye-target distance and the active orbit point are preserved -- only the roll goes. Returns
    False in the degenerate
    straight-up/straight-down view, where roll is indistinguishable from yaw."""
    fwd = cam.forward()
    right = v_cross(fwd, WORLD_UP)
    if v_len(right) < 1e-6:
        return False
    right = v_normalize(right)
    cam.up = list(v_cross(right, fwd))
    return True


def orbit(cam, o, turntable, pivot):
    """Orbit eye (and optionally target for camera) about pivot. pivot None => turn in place
    about the eye (target orbits with the look direction)."""
    pitch = o[0] * ORBIT_SCALE[0] * ORBIT_SIGN[0]
    yaw = o[1] * ORBIT_SCALE[1] * ORBIT_SIGN[1]
    twist = o[2] * ORBIT_SCALE[2] * ORBIT_SIGN[2]
    if pivot is None:
        # Camera: rotate look direction about the eye.
        pivot = tuple(cam.eye)
        eye_fixed = True
    else:
        eye_fixed = False

    def apply_axis(axis, angle):
        if abs(angle) < 1e-15:
            return
        if not eye_fixed:
            cam.eye = _rotate_point(cam.eye, axis, angle, pivot)
        cam.target = _rotate_point(cam.target, axis, angle, pivot)
        cam.up = _rotate_vec(cam.up, axis, angle)

    if turntable:
        apply_axis(WORLD_UP, yaw)
        apply_axis(cam.right(), pitch)
        return
    # Free trackball: combined axis in camera frame.
    r, u, f = cam.right(), v_normalize(tuple(cam.up)), cam.forward()
    axis = v_add(v_add(v_scale(r, pitch), v_scale(u, yaw)), v_scale(f, twist))
    angle = v_len(axis)
    if angle > 1e-12:
        apply_axis(axis, angle)


def pan(cam, px, py, dist):
    k = PAN_SCALE * max(DIST_MIN, dist)
    dx = PAN_SIGN[0] * px * k
    dy = PAN_SIGN[1] * py * k
    r, u = cam.right(), v_normalize(tuple(cam.up))
    delta = v_add(v_scale(r, dx), v_scale(u, dy))
    cam.eye = list(v_add(tuple(cam.eye), delta))
    cam.target = list(v_add(tuple(cam.target), delta))


def dolly(cam, z, dist, toward=None):
    """Zoom by moving eye along forward (or toward a point), keeping target if to_center-ish.
    Positive z dollies in (eye toward target)."""
    k = ZOOM_SIGN * z * ZOOM_SCALE * max(DIST_MIN, dist)
    if toward is not None:
        d = v_sub(toward, tuple(cam.eye))
        if v_len(d) > 1e-6:
            step = v_scale(v_normalize(d), k)
            cam.eye = list(v_add(tuple(cam.eye), step))
            # Keep looking at toward when zooming to a point.
            cam.target = list(toward)
            return
    f = cam.forward()
    cam.eye = list(v_add(tuple(cam.eye), v_scale(f, k)))
    # Keep distance feel: move target with eye for pure forward dolly so framing stays.
    cam.target = list(v_add(tuple(cam.target), v_scale(f, k)))
