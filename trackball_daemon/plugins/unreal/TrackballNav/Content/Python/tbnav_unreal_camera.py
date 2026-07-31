# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Pure-Python camera math for the Unreal Editor Trackball Nav add-on.

NO ``unreal`` import lives here, on purpose: this module is unit-testable headless with a
plain ``python`` interpreter (see tests/test_unreal_nav_math.py). The add-on
(``trackball_nav.py``) reads the live editor viewport camera into a duck-typed :class:`Camera`
(eye location + an orthonormal basis), calls these helpers, then writes location+rotator back.

Conventions (VERIFIED live against Unreal Engine 5.8 via a headless pythonscript probe -- see
docs/apps/unreal.md; do NOT re-derive from algebra, they were OBSERVED):
  * Unreal is LEFT-HANDED, Z-up, world units are CENTIMETRES.
      identity FRotator(pitch=0,yaw=0,roll=0)  ->  forward = +X, right = +Y, up = +Z
  * FRotator is (Pitch about Y, Yaw about Z, Roll about X) in DEGREES. Observed facts the
    convention below reproduces exactly:
        yaw  = +90  ->  forward +X -> +Y , right +Y -> -X     (right-handed +90 about +Z)
        pitch= +90  ->  forward +X -> +Z (nose up)            (LEFT-handed about +Y)
  * The camera looks down its forward (+X) axis; there is NO view-distance / look-at in the
    editor free-fly camera, so orbit-about-a-pivot and zoom are SYNTHESISED here (we manage the
    distance ourselves) and written back as location + rotation each frame.

Because vectors are rotated with a right-handed Rodrigues rotation (a pure geometric op), the
math never assumes a handedness -- the basis vectors come straight from Unreal and go straight
back via make_rot_from_xz, so the only things to settle live are the user-feel SIGNS below.
"""
import math

# Host correction is supplied by the daemon in frame.adv after the active action is known.
# Pure camera math stays neutral so it remains independently testable and cannot double-apply it.
ORBIT_SCALE = (1.0, 1.0, 1.0)
ORBIT_SIGN = (1.0, 1.0, 1.0)
PAN_SIGN = (1.0, 1.0)
PAN_SCALE = 1.0
ZOOM_SIGN = 1.0
ZOOM_SCALE = 1.0
MOVE_SCALE = 1.0
WORLD_UP = (0.0, 0.0, 1.0)      # Unreal is Z-up (verified); turntable azimuth axis.

DIST_DEFAULT = 1000.0           # cm: focus distance used to scale pan/zoom before the first orbit.
DIST_MIN, DIST_MAX = 1.0, 1.0e7 # clamp the tracked focus distance (cm); scenes span thousands of units.


# ---------------------------------------------------------------------------------------
# vector helpers (3-tuples, world space, centimetres)
# ---------------------------------------------------------------------------------------
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
    """Rodrigues rotation of vector ``v`` about ``axis`` by ``angle`` radians (right-handed about
    the axis; axis need not be unit). Pure geometry -- handedness-agnostic. Verified to match
    Unreal's yaw: a +angle about WORLD_UP=(0,0,1) sends forward +X toward +Y (== Unreal yaw+)."""
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


def rotate_object(frame, o, view, pivot):
    """Rotate a transform frame about ``pivot`` using the viewport's fixed right/up/forward axes."""
    axes = (view.right, view.up, view.forward)
    angles = tuple(o[i] * ORBIT_SCALE[i] * ORBIT_SIGN[i] for i in range(3))
    for axis, angle in zip(axes, angles):
        if abs(angle) < 1e-15:
            continue
        frame.forward = v_normalize(rotate_about_axis(frame.forward, axis, angle))
        frame.right = v_normalize(rotate_about_axis(frame.right, axis, angle))
        frame.up = v_normalize(rotate_about_axis(frame.up, axis, angle))
        relative = v_sub(tuple(frame.location), pivot)
        frame.location = list(v_add(pivot, rotate_about_axis(relative, axis, angle)))


def object_translation(p, z, view, dist, frame="view"):
    """Translate X/Y/Z in a view-aligned or horizon-aligned right/up/depth basis."""
    scale = MOVE_SCALE * _clamp_dist(dist)
    if frame == "ground":
        return v_add(
            v_add(v_scale(horizontal(view.right), p[0] * scale),
                  v_scale(WORLD_UP, p[1] * scale)),
            v_scale(horizontal(view.forward), z * scale),
        )
    return v_add(
        v_add(v_scale(view.right, p[0] * scale), v_scale(view.up, p[1] * scale)),
        v_scale(view.forward, z * scale),
    )


# ---------------------------------------------------------------------------------------
# rotator <-> basis (Unreal's VERIFIED FRotator convention, mirrored in pure Python).
#
# The live add-on reads the basis from Unreal's own MathLibrary.get_forward/right/up_vector and
# rebuilds the rotator with make_rot_from_xz (authoritative). These pure functions reproduce the
# SAME convention so they can (a) be unit-tested headless and (b) serve as a fallback if those
# MathLibrary helpers are ever unavailable. Derived from FRotationMatrix and CHECKED against the
# live probe facts (identity / yaw90 / pitch90) -- see tests/test_unreal_nav_math.py.
# ---------------------------------------------------------------------------------------
def rotator_to_basis(pitch, yaw, roll):
    """(pitch, yaw, roll) in DEGREES -> (forward, right, up) world unit vectors, Unreal convention."""
    p, y, r = math.radians(pitch), math.radians(yaw), math.radians(roll)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    cr, sr = math.cos(r), math.sin(r)
    forward = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return forward, right, up


def basis_to_rotator(forward, up):
    """(forward, up) world vectors -> (pitch, yaw, roll) DEGREES. Matches make_rot_from_xz: forward
    is kept fixed, up is used to resolve roll (right = up x forward). Inverse of rotator_to_basis."""
    f = v_normalize(forward)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, f[2]))))
    yaw = math.degrees(math.atan2(f[1], f[0]))
    cp = math.cos(math.radians(pitch))
    if abs(cp) < 1e-6:                       # gimbal lock (looking straight up/down): roll undefined
        return pitch, yaw, 0.0
    right = v_cross(up, f)                    # Unreal: right = up x forward (verified at identity)
    roll = math.degrees(math.atan2(-right[2], up[2]))
    return pitch, yaw, roll


# ---------------------------------------------------------------------------------------
# the duck-typed camera + orbit/pan/zoom operations (mutate the camera in place)
# ---------------------------------------------------------------------------------------
class Camera:
    """Mirror of the Unreal editor free-fly viewport camera, as plain Python so it's testable
    without Unreal.

    location : eye, world space, CENTIMETRES (list of 3)
    forward  : world unit vector the camera looks along (+X at identity)
    right    : world unit vector to camera-right (+Y at identity)
    up       : world unit vector of camera-up (+Z at identity)

    The basis is kept orthonormal (every op rotates all three by the same rotation). There is no
    look-at / view distance -- orbit-about-a-pivot and zoom are synthesised by the helpers below.
    """
    __slots__ = ("location", "forward", "right", "up")

    def __init__(self, location, forward, right, up):
        self.location = list(location)
        self.forward = tuple(forward)
        self.right = tuple(right)
        self.up = tuple(up)

    @classmethod
    def from_rotator(cls, location, pitch, yaw, roll):
        f, r, u = rotator_to_basis(pitch, yaw, roll)
        return cls(location, f, r, u)

    def rotator(self):
        """(pitch, yaw, roll) DEGREES for the current basis."""
        return basis_to_rotator(self.forward, self.up)


def _rotate_frame(cam, axis, angle, pivot):
    """Rotate the whole rigid camera frame (basis + the eye about ``pivot``) by ``angle`` rad about
    ``axis``. ``pivot`` None => rotate the basis in place, eye fixed (free-fly "turn the camera")."""
    cam.forward = v_normalize(rotate_about_axis(cam.forward, axis, angle))
    cam.right = v_normalize(rotate_about_axis(cam.right, axis, angle))
    cam.up = v_normalize(rotate_about_axis(cam.up, axis, angle))
    if pivot is not None:
        rel = v_sub(tuple(cam.location), pivot)
        cam.location = list(v_add(pivot, rotate_about_axis(rel, axis, angle)))


def orbit(cam, o, turntable, pivot):
    """Apply one orbit step (baseline ORBIT_SCALE/ORBIT_SIGN baked in).
      free      -> rotate about the camera's own right/up/fwd axes (twist/roll allowed)
      turntable -> azimuth about WORLD_UP, elevation about camera-right, twist(o2) dropped (horizon
                   stays level)
    ``pivot`` is a world point to orbit around, or None to turn in place about the eye (free-fly)."""
    pitch = o[0] * ORBIT_SCALE[0] * ORBIT_SIGN[0]
    yaw = o[1] * ORBIT_SCALE[1] * ORBIT_SIGN[1]
    twist = o[2] * ORBIT_SCALE[2] * ORBIT_SIGN[2]
    if turntable:
        # azimuth about world up, then elevation about (current) camera-right. Either order keeps the
        # horizon level because the elevation axis IS camera-right and azimuth is about vertical;
        # twist is intentionally dropped so the model never tilts. (Mirrors the FreeCAD add-on.)
        if yaw:
            _rotate_frame(cam, WORLD_UP, yaw, pivot)
        if pitch:
            _rotate_frame(cam, cam.right, pitch, pivot)
        return
    axis = v_add(v_add(v_scale(cam.right, pitch), v_scale(cam.up, yaw)), v_scale(cam.forward, twist))
    angle = v_len(axis)
    if angle > 1e-12:
        _rotate_frame(cam, axis, angle, pivot)


def pan(cam, px, py, dist):
    """Translate the eye in the camera right/up plane, scaled by the focus distance (cm) so the feel
    is roughly zoom-stable. ``dist`` is the current eye->focus distance (cm)."""
    k = PAN_SCALE * _clamp_dist(dist)
    dx = PAN_SIGN[0] * px * k
    dy = PAN_SIGN[1] * py * k
    cam.location = list(v_add(tuple(cam.location),
                              v_add(v_scale(cam.right, dx), v_scale(cam.up, dy))))


def dolly(cam, z, dist, toward=None):
    """Zoom by dollying the eye along its forward axis (the natural free-fly zoom), scaled by the
    focus distance (cm). If ``toward`` (a world point) is given, dolly along (toward - eye) instead
    so a "to_object" zoom heads at the model. Positive z dollies forward (ZOOM_SIGN)."""
    k = ZOOM_SIGN * z * ZOOM_SCALE * _clamp_dist(dist)
    if toward is not None:
        d = v_sub(toward, tuple(cam.location))
        if v_len(d) > 1e-6:
            cam.location = list(v_add(tuple(cam.location), v_scale(v_normalize(d), k)))
            return
    cam.location = list(v_add(tuple(cam.location), v_scale(cam.forward, k)))


def lens_zoom(cam, z, field_of_view, toward=None):
    """Return a new perspective FOV while keeping an optional world point fixed on screen."""
    old_fov = max(5.0, min(170.0, float(field_of_view)))
    factor = max(0.05, min(20.0, 1.0 - ZOOM_SIGN * z * ZOOM_SCALE))
    old_tan = math.tan(math.radians(old_fov) * 0.5)
    new_tan = max(math.tan(math.radians(2.5)), min(math.tan(math.radians(85.0)),
                                                       old_tan * factor))
    new_fov = math.degrees(2.0 * math.atan(new_tan))
    ratio = new_tan / old_tan
    if toward is not None:
        offset = v_sub(toward, tuple(cam.location))
        planar = v_add(v_scale(cam.right, v_dot(offset, cam.right)),
                       v_scale(cam.up, v_dot(offset, cam.up)))
        cam.location = list(v_add(tuple(cam.location), v_scale(planar, 1.0 - ratio)))
    return new_fov


# ---------------------------------------------------------------------------------------
# fly / walk (first-person) -- mode ports of the Blender add-on
# ---------------------------------------------------------------------------------------
def horizontal(v):
    """v projected onto the world XY plane, normalised (zero vector if degenerate). Used by walk so
    'forward' stays level no matter where the camera is pitched."""
    h = (v[0], v[1], 0.0)
    n = v_len(h)
    return (h[0] / n, h[1] / n, h[2] / n) if n > 1e-9 else (0.0, 0.0, 0.0)


def level_horizon(cam):
    """Remove existing roll: rebuild right/up so camera-right is horizontal (perpendicular to
    WORLD_UP) while forward is unchanged. The eye stays put, so the tracked focus distance and
    the synthesised orbit point (location + forward*dist) are preserved -- only the roll goes.
    Returns False in the degenerate
    straight-up/straight-down view, where roll is indistinguishable from yaw (the same
    singularity basis_to_rotator resolves by reporting roll 0)."""
    right = v_cross(WORLD_UP, cam.forward)       # Unreal: right = up x forward (verified)
    if v_len(right) < 1e-6:
        return False
    right = v_normalize(right)
    cam.right = right
    cam.up = v_cross(cam.forward, right)
    return True


def look(cam, o, horizon_lock):
    """First-person look (fly/walk): rotate the camera in place about the EYE. ``horizon_lock`` False
    (fly) is a free look that BANKS on twist; True (walk) keeps the horizon level and drops twist
    (turntable). Just an orbit about the eye -- same ORBIT_SCALE/SIGN as the orbit gesture."""
    orbit(cam, o, horizon_lock, None)


def fly_move(cam, p, z, dist, speed):
    """Fly movement: translate the eye along the camera's OWN axes -- strafe along right (p[0]),
    thrust along forward (p[1], dives/climbs with pitch), rise/fall along camera up (z). Scaled by the
    focus distance (cm) and the fly speed. Signs come from the per-mode inverts applied upstream."""
    k = MOVE_SCALE * float(speed) * _clamp_dist(dist)
    move = v_add(v_add(v_scale(cam.right, p[0] * k), v_scale(cam.forward, p[1] * k)),
                 v_scale(cam.up, z * k))
    cam.location = list(v_add(tuple(cam.location), move))


def walk_move(cam, p, z, dist, speed):
    """Walk movement: stay on the ground plane -- strafe along HORIZONTAL right (p[0]), forward along
    HORIZONTAL forward (p[1], level regardless of pitch), rise/fall along WORLD up (z). Scaled by the
    focus distance (cm) and the walk speed."""
    k = MOVE_SCALE * float(speed) * _clamp_dist(dist)
    rh, fh = horizontal(cam.right), horizontal(cam.forward)
    move = v_add(v_add(v_scale(rh, p[0] * k), v_scale(fh, p[1] * k)), (0.0, 0.0, z * k))
    cam.location = list(v_add(tuple(cam.location), move))


def _clamp_dist(dist):
    try:
        d = float(dist)
    except (TypeError, ValueError):
        d = DIST_DEFAULT
    return max(DIST_MIN, min(DIST_MAX, d if d > 0.0 else DIST_DEFAULT))
