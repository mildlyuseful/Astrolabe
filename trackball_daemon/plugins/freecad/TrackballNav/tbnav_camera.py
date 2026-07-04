"""Pure-Python camera math for the FreeCAD Trackball Nav add-on.

NO FreeCAD / pivy / PySide imports live here, on purpose: this module is unit-testable
headless with a plain ``python`` interpreter (see tests/test_freecad_nav_math.py). The
add-on (``tbnav_freecad.py``) reads the live Coin ``SoCamera`` into a duck-typed
:class:`Camera`, calls these helpers, then writes the fields back.

Conventions (VERIFIED live against FreeCAD 1.1 / Coin3D -- see docs/apps/freecad.md):
  * Quaternions are ``(x, y, z, w)`` tuples -- the component order Coin's
    ``SbRotation.getValue()`` returns (w last).
  * ``q_rotate`` matches Coin's ``SbRotation.multVec`` (validated: +90 deg about Z maps
    (1,0,0) -> (0,1,0)).
  * The camera looks down its LOCAL -Z; up = LOCAL +Y; right = LOCAL +X (OpenGL/Coin).
    ``orientation`` is the camera-local -> world rotation.
  * Left-multiplying the orientation (``q_mul(R, orientation)``) applies R as a WORLD-space
    rotation -- the basis for orbit-about-a-world-pivot.
  * FreeCAD is Z-up, so the turntable azimuth axis ``WORLD_UP`` is (0, 0, 1).
"""
import math

# --- baseline sign/scale (the add-on's intrinsic feel). The daemon's Per-App Bindings
#     (gain 1.0 == this baseline) scale from here and the Invert checkboxes flip further, so
#     DO NOT also scale/invert in the daemon. SIGNS ARE STARTING GUESSES -- calibrate live with
#     the trackball (docs/apps/freecad.md "Live calibration"). ----------------------
ORBIT_SCALE = (1.0, 1.0, 1.0)   # (pitch o[0] about right, yaw o[1] about up, twist o[2] about fwd).
                                # 1.0 = rotate the view by the FULL broker angle, so Sensitivity 1.0 is
                                # a true 1:1 ball->view orbit (matches the --debug cube AND the other
                                # eye+target camera apps: Fusion/SolidWorks/Onshape all use magnitude
                                # 1.0). NOTE: do NOT copy Blender's 0.5 here -- that halving is specific
                                # to Blender's RegionView3D and made FreeCAD orbit at half speed.
PAN_SIGN = (-1.0, 1.0)          # pan along (camera-right, camera-up)
PAN_SCALE = 0.14                # broker pan delta * on-screen view height -> world units. Matches the
                                # Fusion add-in's proven baseline (the daemon sends the SAME pan deltas
                                # to every app), so panning feels like Fusion's; tune via the per-app
                                # Pan gain. (An earlier 0.0015 here made pan ~100x too small => "pan
                                # does nothing" -- the daemon DOES emit pan frames on Shift, output.py.)
ZOOM_SCALE = 0.25               # broker zoom delta -> fraction of view size per frame
ZOOM_SIGN = 1.0                 # twist -> zoom direction (positive twist zooms IN)
WORLD_UP = (0.0, 0.0, 1.0)      # FreeCAD is Z-up; turntable azimuth axis


# ---------------------------------------------------------------------------------------
# vector helpers (3-tuples)
# ---------------------------------------------------------------------------------------
def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_len(a):
    return math.sqrt(v_dot(a, a))


def v_normalize(a):
    n = v_len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------------------
# quaternion helpers (x, y, z, w) -- Hamilton product, matching Coin's SbRotation
# ---------------------------------------------------------------------------------------
def q_mul(a, b):
    """Hamilton product a*b. q_rotate(q_mul(a,b), v) == q_rotate(a, q_rotate(b, v))."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_rotate(q, v):
    """Rotate vector v by quaternion q (matches Coin SbRotation.multVec)."""
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def q_normalize(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def q_axis_angle(axis, angle):
    """Quaternion for a rotation of `angle` rad about `axis` (axis need not be unit)."""
    n = v_len(axis)
    if n < 1e-12 or abs(angle) < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    s = math.sin(angle * 0.5) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle * 0.5))


# ---------------------------------------------------------------------------------------
# the duck-typed camera + the orbit/pan/zoom operations (mutate the camera in place)
# ---------------------------------------------------------------------------------------
class Camera:
    """Mirror of a FreeCAD Coin SoCamera, as plain Python so it's testable without FreeCAD.

    position     : eye, world space (list of 3)
    orientation  : camera-local -> world quaternion (list of 4, x,y,z,w)
    focal        : focalDistance (eye -> look-at distance); look-at = position + fwd*focal
    height       : ortho on-screen world height (SoOrthographicCamera.height), else None
    height_angle : perspective vertical FOV (SoPerspectiveCamera.heightAngle), else None
    is_ortho     : True for orthographic (FreeCAD's default), False for perspective
    """
    __slots__ = ("position", "orientation", "focal", "height", "height_angle", "is_ortho")

    def __init__(self, position, orientation, focal, height=None, height_angle=None, is_ortho=True):
        self.position = list(position)
        self.orientation = list(orientation)
        self.focal = float(focal)
        self.height = None if height is None else float(height)
        self.height_angle = None if height_angle is None else float(height_angle)
        self.is_ortho = bool(is_ortho)


def axes(cam):
    """World-space (right, up, forward-into-screen, back-toward-eye) for the camera."""
    q = cam.orientation
    return (q_rotate(q, (1.0, 0.0, 0.0)),
            q_rotate(q, (0.0, 1.0, 0.0)),
            q_rotate(q, (0.0, 0.0, -1.0)),
            q_rotate(q, (0.0, 0.0, 1.0)))


def look_at(cam):
    """The point the camera is aimed at: eye + forward * focalDistance."""
    _r, _u, fwd, _b = axes(cam)
    return v_add(tuple(cam.position), v_scale(fwd, cam.focal))


def view_size(cam):
    """On-screen world height: ortho -> height; perspective -> 2*focal*tan(heightAngle/2)."""
    if cam.is_ortho and cam.height is not None:
        return cam.height
    if cam.height_angle is not None:
        return 2.0 * cam.focal * math.tan(cam.height_angle * 0.5)
    return max(cam.focal, 1.0)


def orbit_R(cam, o0, o1, o2, turntable):
    """World-space rotation quaternion for one orbit step (baseline ORBIT_SCALE baked in).
      free      -> rotate about the camera's own right/up/fwd axes (twist/roll allowed)
      turntable -> azimuth about WORLD_UP, elevation about camera-right, roll(o2) dropped
    """
    right, up, fwd, _back = axes(cam)
    pitch = o0 * ORBIT_SCALE[0]
    yaw = o1 * ORBIT_SCALE[1]
    twist = o2 * ORBIT_SCALE[2]
    if turntable:
        # yaw about WORLD_UP is the OUTER rotation, pitch about camera-right the INNER one, so
        # camera-right stays horizontal (horizon locked). q_mul(A,B) applies B first then A, and
        # rotating old_right about itself (pitch) leaves it unchanged, then yaw about Z keeps it
        # in the horizontal plane. Swapping these tilts the horizon -- verified in the math test.
        return q_mul(q_axis_angle(WORLD_UP, yaw), q_axis_angle(right, pitch))
    axis = v_add(v_add(v_scale(right, pitch), v_scale(up, yaw)), v_scale(fwd, twist))
    angle = v_len(axis)
    if angle < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return q_axis_angle(axis, angle)


def apply_world_rotation(cam, R, pivot):
    """Rotate the camera by world-space quaternion R about `pivot` (a 3-tuple, or None ==
    rotate about the eye, leaving position fixed). The eye rotates rigidly about the pivot;
    focalDistance is preserved (rotation keeps eye->look-at length), so the look-at follows."""
    cam.orientation = list(q_normalize(q_mul(R, tuple(cam.orientation))))
    if pivot is not None:
        d = v_sub(tuple(cam.position), pivot)
        cam.position = list(v_add(pivot, q_rotate(R, d)))


def orbit(cam, o, turntable, pivot):
    """Apply an orbit step: rotate about `pivot` (None -> about the eye / look-around)."""
    apply_world_rotation(cam, orbit_R(cam, o[0], o[1], o[2], turntable), pivot)


def pan(cam, px, py):
    """Translate the eye in the camera right/up plane, scaled by the on-screen view height so
    the feel is zoom-stable. focalDistance preserved -> the look-at follows the eye."""
    right, up, _fwd, _back = axes(cam)
    k = PAN_SCALE * view_size(cam)
    dx = PAN_SIGN[0] * px * k
    dy = PAN_SIGN[1] * py * k
    cam.position = list(v_add(tuple(cam.position), v_add(v_scale(right, dx), v_scale(up, dy))))


def zoom(cam, z, pivot):
    """Zoom by broker delta z. ortho -> scale cam.height (and shift toward `pivot` so it stays
    put on screen); perspective -> dolly the eye along forward toward `pivot`. `pivot` None ==
    zoom about the current look-at (screen centre)."""
    s = 1.0 - ZOOM_SIGN * z * ZOOM_SCALE
    if s < 0.02:
        s = 0.02
    _r, _u, fwd, _b = axes(cam)
    L = v_add(tuple(cam.position), v_scale(fwd, cam.focal))     # current look-at
    P = pivot if pivot is not None else L
    if cam.is_ortho:
        delta = v_scale(v_sub(P, L), 1.0 - s)                  # keep P fixed on screen
        cam.position = list(v_add(tuple(cam.position), delta))
        if cam.height is not None:
            cam.height = max(1e-4, cam.height * s)
    else:
        dist = v_len(v_sub(P, tuple(cam.position)))
        if dist < 1e-6:
            dist = cam.focal if cam.focal > 1e-6 else 1.0
        move = ZOOM_SIGN * z * ZOOM_SCALE * dist
        cam.position = list(v_add(tuple(cam.position), v_scale(fwd, move)))
        cam.focal = max(1e-4, cam.focal - move)                # keep the look-at sane
