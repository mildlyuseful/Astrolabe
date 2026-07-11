"""Output / injection layer.

The SendInput primitive and quaternion helpers originate in cube_test.py. A validated global
raw-to-logical axis orientation is applied once at packet ingress; cursor and per-app mappings then
consume the same logical XYZ vector. Identity defaults preserve the original behavior exactly.
"""
import ctypes
import math
import struct
import sys
import threading

from .config import host_baseline

# ===========================================================================
# Windows SendInput (relative pointer move + wheel), pure ctypes -- no dependency
# ===========================================================================
_WIN = (sys.platform == "win32")
if _WIN:
    from ctypes import wintypes

    MOUSEEVENTF_MOVE  = 0x0001
    MOUSEEVENTF_WHEEL = 0x0800
    WHEEL_DELTA       = 120
    INPUT_MOUSE       = 0

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", _MOUSEINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _SendInput = ctypes.windll.user32.SendInput

    def send_mouse(dx=0, dy=0, wheel=0):
        flags = 0
        if dx or dy:
            flags |= MOUSEEVENTF_MOVE
        if wheel:
            flags |= MOUSEEVENTF_WHEEL
        if not flags:
            return
        mi = _MOUSEINPUT(int(dx), int(dy),
                         (int(wheel) * WHEEL_DELTA) & 0xFFFFFFFF,
                         flags, 0, None)
        inp = _INPUT(INPUT_MOUSE)
        inp.mi = mi
        _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    _GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState

    def shift_held():
        # VK_SHIFT = 0x10. System-wide (no window focus needed) -- this is how the headless
        # app reproduces the pygame window's "hold SHIFT to pan/zoom" behavior.
        return (_GetAsyncKeyState(0x10) & 0x8000) != 0
else:
    _warned = [False]

    def send_mouse(dx=0, dy=0, wheel=0):
        if not _warned[0]:
            print("[WARN] cursor mode needs Windows SendInput; pointer injection disabled")
            _warned[0] = True

    def shift_held():
        return False

# ===========================================================================
# Quaternion helpers  (q = (w, x, y, z), unit quaternions) -- verbatim
# ===========================================================================
def quat_mul(a, b):
    """Hamilton product a*b."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_normalize(q):
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return (w / n, x / n, y / n, z / n)


def quat_from_axis_angle(vx, vy, vz):
    """Delta quaternion from an axis-angle vector: axis = direction, angle = magnitude."""
    angle = math.sqrt(vx * vx + vy * vy + vz * vz)
    if angle < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle * 0.5) / angle      # (sin(a/2)/angle) folds in the 1/|v| normalize
    return (math.cos(angle * 0.5), vx * s, vy * s, vz * s)


def quat_to_gl_matrix(q):
    """Unit quaternion -> 4x4 column-major matrix for glMultMatrixf."""
    w, x, y, z = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        1 - 2 * (yy + zz), 2 * (xy + wz),     2 * (xz - wy),     0.0,
        2 * (xy - wz),     1 - 2 * (xx + zz), 2 * (yz + wx),     0.0,
        2 * (xz + wy),     2 * (yz - wx),     1 - 2 * (xx + yy), 0.0,
        0.0,               0.0,               0.0,               1.0,
    ]


MODE_CUBE, MODE_CURSOR = 0, 1


class OutputEngine:
    """Owns the cube/view state and routes each rotation packet -- identical math to the
    original on_rotation(), with the numbers pulled from config via apply_config()."""

    MODE_CUBE = MODE_CUBE
    MODE_CURSOR = MODE_CURSOR

    def __init__(self, config):
        self.cfg = config
        self.lock = threading.Lock()            # protects view state (orientation/pan/distance)
        self.orientation = (1.0, 0.0, 0.0, 0.0)
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.distance = 6.0
        # cursor-mode sub-pixel/notch accumulators -- touched only on the BLE thread
        self._mx = 0.0
        self._my = 0.0
        self._sc = 0.0
        self._last_mode = MODE_CUBE
        # Optional sink for 3D-app driving: fn(ox, oy, oz, pan_x, pan_y, zoom). When set, the
        # same per-frame orbit/pan/zoom deltas that move the local cube are ALSO forwarded to
        # the nav broker (which streams them to the focused CAD app's add-on). Additive: with
        # nav_sink=None this class behaves exactly as before.
        self.nav_sink = None
        # Which app's 3D bindings to use (None => config "active_app"). The app sets this to
        # the focused CAD app so each app is driven with its own sensitivities.
        self._bindings_app = None
        self.apply_config()
        # default mode chosen only at startup (apply_config must NOT reset a live toggle)
        self.mode = MODE_CUBE if self.cfg.data["general"]["default_mode"] == "cube" else MODE_CURSOR
        self._last_mode = self.mode

    def apply_config(self):
        """Refresh the cached mapping numbers from config (call after any config change)."""
        g = self.cfg.data["general"]
        orientation = g.get("axis_orientation") or {}
        self.global_src = [int(v) for v in orientation.get("source", [0, 1, 2])]
        self.global_sign = [-1.0 if v else 1.0
                            for v in orientation.get("invert", [False, False, False])]
        c, s = g["cursor"], g["scroll"]
        self.c_xsrc, self.c_xsign = int(c["x_src"]), float(c["x_sign"])
        self.c_ysrc, self.c_ysign = int(c["y_src"]), float(c["y_sign"])
        self.c_gain = float(c["gain"])
        self.s_src, self.s_sign, self.s_gain = int(s["src"]), float(s["sign"]), float(s["gain"])
        self.s_dead, self.s_dom = float(s["deadzone"]), float(s["dominance"])

        apps = self.cfg.data["apps"]
        app_key = self._bindings_app or self.cfg.data["active_app"]
        app = apps.get(app_key) or apps[self.cfg.data["active_app"]]
        self.host_baseline = host_baseline(app_key)
        b = app["bindings"]
        o, p, z = b["orbit"], b["pan"], b["zoom"]
        # Fold the user's per-axis invert flags into the cached signs (default off => no-op,
        # so cube/cursor output stays bit-identical). handle_packet is unchanged.
        inv = b.get("invert", {})
        oi = inv.get("orbit", [False, False, False])
        pi = inv.get("pan", [False, False])
        zi = bool(inv.get("zoom", False))
        self.o_src = [int(v) for v in o["axis_source"]]
        self.o_sign = [float(o["axis_sign"][k]) * (-1.0 if oi[k] else 1.0) for k in range(3)]
        self.o_sens = float(o["sensitivity"])
        self.p_xsrc = int(p["x_src"]); self.p_xsign = float(p["x_sign"]) * (-1.0 if pi[0] else 1.0)
        self.p_ysrc = int(p["y_src"]); self.p_ysign = float(p["y_sign"]) * (-1.0 if pi[1] else 1.0)
        self.p_gain = float(p["gain"])
        self.z_src = int(z["src"]); self.z_sign = float(z["sign"]) * (-1.0 if zi else 1.0)
        self.z_gain = float(z["gain"])
        self.z_dead, self.z_dom = float(z["deadzone"]), float(z["dominance"])
        self.dist_min, self.dist_max = float(z["dist_min"]), float(z["dist_max"])
        self.dist_default = float(z["dist_default"])
        self.toggle = b.get("toggle", "shift")

    # --- runtime controls (thread-safe) -------------------------------------------
    def set_mode(self, mode):
        self.mode = mode

    def set_active_bindings(self, key):
        """Re-cache 3D bindings for app `key` (None => config active_app). Cheap; called on
        focus change so each CAD app is driven with its own orbit/pan/zoom sensitivities."""
        self._bindings_app = key
        self.apply_config()

    def toggle_mode(self):
        self.mode = MODE_CURSOR if self.mode == MODE_CUBE else MODE_CUBE
        return self.mode

    def reset_view(self):
        with self.lock:
            self.orientation = (1.0, 0.0, 0.0, 0.0)
            self.pan_x = self.pan_y = 0.0
            self.distance = self.dist_default

    def get_view(self):
        with self.lock:
            return self.orientation, self.pan_x, self.pan_y, self.distance

    def _emit_nav(self, ox, oy, oz, px, py, zoom):
        sink = self.nav_sink
        if sink is not None:
            # Developer-owned host alignment is applied only at the integration boundary, after
            # the global/body mapping and composably with the saved user mapping. The local debug
            # cube therefore remains a host-neutral calibration reference.
            h = self.host_baseline
            if not h.apply_in_daemon:  # rich add-on applies mode-aware baseline from frame.adv
                sink(ox, oy, oz, px, py, zoom)
                return
            orbit = (ox, oy, oz)
            movement = (px, py, zoom)
            aligned_o = tuple(orbit[h.orbit_source[i]] * h.orbit_sign[i] * h.orbit_scale[i]
                              for i in range(3))
            aligned_p = tuple(movement[h.pan_source[i]] * h.pan_sign[i] * h.pan_scale
                              for i in range(2))
            aligned_z = movement[h.zoom_source] * h.zoom_sign * h.zoom_scale
            sink(aligned_o[0], aligned_o[1], aligned_o[2],
                 aligned_p[0], aligned_p[1], aligned_z)

    # --- the data path -------------------------------------------------------------
    def handle_packet(self, data):
        if len(data) < 12:
            return
        raw = struct.unpack_from("<fff", data, 0)
        # The one physical-orientation transform. Everything downstream (pointer, cube, broker,
        # per-app action routing) speaks this same body-relative logical XYZ frame.
        recv = tuple(self.global_sign[i] * raw[self.global_src[i]] for i in range(3))

        mode = self.mode
        if mode != self._last_mode:                 # reset cursor accumulators on any switch
            self._last_mode = mode
            self._mx = self._my = self._sc = 0.0

        if mode == MODE_CUBE:
            if self.toggle == "shift" and shift_held():
                # SHIFT held: pan (move part) + zoom (twist part), mutually exclusive via the
                # same dominance test cursor mode uses for move-vs-scroll. Orbit is paused.
                twist = self.z_sign * recv[self.z_src]
                plane = math.hypot(recv[self.p_xsrc], recv[self.p_ysrc])
                if abs(twist) > self.z_dead and abs(twist) > self.z_dom * plane:
                    zoom_d = twist * self.z_gain                  # same value as before
                    with self.lock:
                        self.distance = min(self.dist_max, max(self.dist_min, self.distance - zoom_d))
                    self._emit_nav(0.0, 0.0, 0.0, 0.0, 0.0, zoom_d)
                else:
                    pdx = self.p_xsign * recv[self.p_xsrc] * self.p_gain
                    pdy = self.p_ysign * recv[self.p_ysrc] * self.p_gain
                    with self.lock:
                        self.pan_x += pdx
                        self.pan_y += pdy
                    self._emit_nav(0.0, 0.0, 0.0, pdx, pdy, 0.0)
            else:
                # Orbit: axis-angle increment -> delta quaternion, composed in the world frame.
                vx = self.o_sign[0] * recv[self.o_src[0]] * self.o_sens
                vy = self.o_sign[1] * recv[self.o_src[1]] * self.o_sens
                vz = self.o_sign[2] * recv[self.o_src[2]] * self.o_sens
                dq = quat_from_axis_angle(vx, vy, vz)
                with self.lock:
                    self.orientation = quat_normalize(quat_mul(dq, self.orientation))
                self._emit_nav(vx, vy, vz, 0.0, 0.0, 0.0)
            return

        # ---- CURSOR mode: convert this packet's rotation into pointer/wheel input ----
        yaw = self.s_sign * recv[self.s_src]
        plane = math.hypot(recv[self.c_xsrc], recv[self.c_ysrc])
        if abs(yaw) > self.s_dead and abs(yaw) > self.s_dom * plane:
            # yaw dominates -> scroll the wheel, carry the fractional remainder
            self._sc += yaw * self.s_gain
            notches = int(self._sc)
            if notches:
                send_mouse(wheel=notches)
                self._sc -= notches
        else:
            # otherwise -> move the pointer, carry sub-pixel remainder
            self._mx += self.c_xsign * recv[self.c_xsrc] * self.c_gain
            self._my += self.c_ysign * recv[self.c_ysrc] * self.c_gain
            ix, iy = int(self._mx), int(self._my)
            if ix or iy:
                send_mouse(dx=ix, dy=iy)
                self._mx -= ix
                self._my -= iy
