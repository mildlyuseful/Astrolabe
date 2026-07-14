"""Output / injection layer.

The SendInput primitive and quaternion helpers originate in cube_test.py. A validated global
raw-to-logical axis orientation is applied once at packet ingress; cursor and per-app mappings then
consume the same logical XYZ vector. Identity defaults preserve the original behavior exactly.
"""
from dataclasses import dataclass, replace
import math
import struct
import threading

from .config import host_baseline
from .app_registry import binding_profile
from .commands import SerializedCommandQueue, SetInputMode, ToggleInputMode
from .runtime_state import ConfigRuntimeBaseResolver, RuntimeStore
from .windows_pointer import send_mouse

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


@dataclass(frozen=True)
class _OutputMapping:
    """One immutable packet-mapping snapshot published as a single reference."""
    global_src: tuple
    global_sign: tuple
    c_xsrc: int
    c_xsign: float
    c_ysrc: int
    c_ysign: float
    c_gain: float
    s_src: int
    s_sign: float
    s_gain: float
    s_dead: float
    s_dom: float
    app_key: str
    binding_profile: object
    twist_action: str
    host_baseline: object
    o_src: tuple
    o_base_sign: tuple
    o_sign: tuple
    o_sens: float
    p_xsrc: int
    p_xbase_sign: float
    p_xsign: float
    p_ysrc: int
    p_ybase_sign: float
    p_ysign: float
    p_gain: float
    z_src: int
    z_base_sign: float
    z_sign: float
    z_gain: float
    z_dead: float
    z_dom: float
    dist_min: float
    dist_max: float
    dist_default: float
    toggle: str


class OutputEngine:
    """Owns the cube/view state and routes each rotation packet -- identical math to the
    original on_rotation(), with the numbers pulled from config via apply_config()."""

    MODE_CUBE = MODE_CUBE
    MODE_CURSOR = MODE_CURSOR

    def __init__(self, config, runtime_store=None, command_queue=None):
        self.cfg = config
        self.runtime = runtime_store or RuntimeStore(ConfigRuntimeBaseResolver(config))
        self.commands = command_queue or SerializedCommandQueue(self.runtime)
        if self.commands.runtime_store is not self.runtime:
            raise ValueError("OutputEngine command queue must own the supplied runtime store")
        self.lock = threading.Lock()            # protects view state (orientation/pan/distance)
        self._mapping_lock = threading.RLock()  # serialize config reloads with foreground switches
        self._mapping = None
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
        self.apply_config(refresh_runtime=False)
        self._last_mode = self.mode

    def apply_config(self, *, refresh_runtime=True):
        """Build and atomically publish a complete mapping after a config change."""
        with self._mapping_lock:
            snapshot = self.cfg.snapshot()
            app_key = self._bindings_app or snapshot.selected_app
            self._mapping = self._build_mapping(app_key)
        if refresh_runtime:
            from .commands import RefreshRuntimeBase
            self.commands.dispatch(RefreshRuntimeBase(origin="config"))

    def _build_mapping(self, app_key):
        """Build a mapping without exposing a partially refreshed set of fields."""
        snapshot = self.cfg.snapshot()
        g = snapshot.general_profile
        orientation = g.get("axis_orientation") or {}
        global_src = tuple(int(v) for v in orientation.get("source", [0, 1, 2]))
        global_sign = tuple(-1.0 if v else 1.0
                            for v in orientation.get("invert", [False, False, False]))
        c, s = g["cursor"], g["scroll"]

        if app_key not in snapshot.app_profiles:
            app_key = snapshot.selected_app
        app = snapshot.app_profile(app_key)
        b = app["bindings"]
        o, p, z = b["orbit"], b["pan"], b["zoom"]
        # Fold the user's per-axis invert flags into the snapshot signs (default off => no-op,
        # so cube/cursor output stays bit-identical).
        inv = b.get("invert", {})
        oi = inv.get("orbit", [False, False, False])
        pi = inv.get("pan", [False, False])
        zi = bool(inv.get("zoom", False))
        return _OutputMapping(
            global_src=global_src,
            global_sign=global_sign,
            c_xsrc=int(c["x_src"]),
            c_xsign=float(c["x_sign"]),
            c_ysrc=int(c["y_src"]),
            c_ysign=float(c["y_sign"]),
            c_gain=float(c["gain"]),
            s_src=int(s["src"]),
            s_sign=float(s["sign"]),
            s_gain=float(s["gain"]),
            s_dead=float(s["deadzone"]),
            s_dom=float(s["dominance"]),
            app_key=app_key,
            binding_profile=binding_profile(app_key),
            twist_action=str((app.get("advanced") or {}).get("twist_action", "roll")),
            host_baseline=host_baseline(app_key),
            o_src=tuple(int(v) for v in o["axis_source"]),
            o_base_sign=tuple(float(value) for value in o["axis_sign"]),
            o_sign=tuple(float(o["axis_sign"][k]) * (-1.0 if oi[k] else 1.0)
                         for k in range(3)),
            o_sens=float(o["sensitivity"]),
            p_xsrc=int(p["x_src"]),
            p_xbase_sign=float(p["x_sign"]),
            p_xsign=float(p["x_sign"]) * (-1.0 if pi[0] else 1.0),
            p_ysrc=int(p["y_src"]),
            p_ybase_sign=float(p["y_sign"]),
            p_ysign=float(p["y_sign"]) * (-1.0 if pi[1] else 1.0),
            p_gain=float(p["gain"]),
            z_src=int(z["src"]),
            z_base_sign=float(z["sign"]),
            z_sign=float(z["sign"]) * (-1.0 if zi else 1.0),
            z_gain=float(z["gain"]),
            z_dead=float(z["deadzone"]),
            z_dom=float(z["dominance"]),
            dist_min=float(z["dist_min"]),
            dist_max=float(z["dist_max"]),
            dist_default=float(z["dist_default"]),
            toggle=b.get("toggle", "shift"),
        )

    @staticmethod
    def _mapping_for_runtime(mapping, runtime_snapshot):
        """Overlay registry-approved live values onto one immutable config mapping."""
        settings = runtime_snapshot.effective_settings
        values = {
            "c_gain": settings.get("pointer.cursor.gain", mapping.c_gain),
            "s_gain": settings.get("pointer.scroll.gain", mapping.s_gain),
            "s_dead": settings.get("pointer.scroll.deadzone", mapping.s_dead),
            "s_dom": settings.get("pointer.scroll.dominance", mapping.s_dom),
        }
        if runtime_snapshot.focused_context.app_id != mapping.app_key:
            return replace(mapping, **values)
        values.update({
            "o_sens": settings.get("navigation.orbit.sensitivity", mapping.o_sens),
            "p_gain": settings.get("navigation.pan.gain", mapping.p_gain),
            "z_gain": settings.get("navigation.zoom.gain", mapping.z_gain),
            "z_dom": settings.get("navigation.zoom.dominance", mapping.z_dom),
            "twist_action": settings.get(
                "navigation.orbit.twist_action", mapping.twist_action),
        })
        if not mapping.binding_profile.rich_actions:
            o_src = tuple(settings.get(
                f"navigation.routing.orbit.{axis}.source", mapping.o_src[index])
                for index, axis in enumerate("xyz"))
            o_sign = tuple(
                mapping.o_base_sign[index] * (-1.0 if settings.get(
                    f"navigation.routing.orbit.{axis}.invert",
                    mapping.o_sign[index] != mapping.o_base_sign[index]) else 1.0)
                for index, axis in enumerate("xyz"))
            values.update({
                "o_src": o_src,
                "o_sign": o_sign,
                "p_xsrc": settings.get("navigation.routing.pan.x.source", mapping.p_xsrc),
                "p_xsign": mapping.p_xbase_sign * (-1.0 if settings.get(
                    "navigation.routing.pan.x.invert",
                    mapping.p_xsign != mapping.p_xbase_sign) else 1.0),
                "p_ysrc": settings.get("navigation.routing.pan.y.source", mapping.p_ysrc),
                "p_ysign": mapping.p_ybase_sign * (-1.0 if settings.get(
                    "navigation.routing.pan.y.invert",
                    mapping.p_ysign != mapping.p_ybase_sign) else 1.0),
                "z_src": settings.get("navigation.routing.zoom.source", mapping.z_src),
                "z_sign": mapping.z_base_sign * (-1.0 if settings.get(
                    "navigation.routing.zoom.invert",
                    mapping.z_sign != mapping.z_base_sign) else 1.0),
            })
        return replace(mapping, **values)

    # --- runtime controls (thread-safe) -------------------------------------------
    @property
    def mode(self):
        return (MODE_CUBE if self.runtime.snapshot().effective_input_mode == "3d"
                else MODE_CURSOR)

    def set_mode(self, mode):
        canonical = {MODE_CUBE: "3d", MODE_CURSOR: "pointer", "3d": "3d",
                     "pointer": "pointer"}.get(mode)
        if canonical is None:
            raise ValueError(f"invalid output mode: {mode!r}")
        self.commands.dispatch(SetInputMode(origin="output-compat", mode=canonical))
        return self.mode

    def set_active_bindings(self, key):
        """Atomically switch to app ``key`` and publish its complete mapping snapshot."""
        with self._mapping_lock:
            app_key = key or self.cfg.snapshot().selected_app
            mapping = self._build_mapping(app_key)
            self._bindings_app = key
            self._mapping = mapping

    def toggle_mode(self):
        self.commands.dispatch(ToggleInputMode(origin="output-compat"))
        return self.mode

    def reset_view(self):
        with self.lock:
            self.orientation = (1.0, 0.0, 0.0, 0.0)
            self.pan_x = self.pan_y = 0.0
            self.distance = self._mapping.dist_default

    def get_view(self):
        with self.lock:
            return self.orientation, self.pan_x, self.pan_y, self.distance

    def _emit_nav(self, mapping, ox, oy, oz, px, py, zoom):
        sink = self.nav_sink
        if sink is not None:
            # Ordinary integrations do not have mode-aware routing inside their host add-on. Apply
            # the global Twist action here, before host alignment, so Turntable can use twist for
            # zoom (or ignore it) just like the rich integrations. Rich profiles consume the same
            # setting after their per-mode action routing and must not be transformed twice.
            if not mapping.binding_profile.rich_actions and oz:
                if mapping.twist_action in ("zoom", "dolly"):
                    zoom += oz
                    oz = 0.0
                elif mapping.twist_action == "none":
                    oz = 0.0
            # Developer-owned host alignment is applied only at the integration boundary, after
            # the global/body mapping and composably with the saved user mapping. The local debug
            # cube therefore remains a host-neutral calibration reference.
            h = mapping.host_baseline
            if not h.apply_in_daemon:  # rich add-on applies mode-aware baseline from frame.adv
                sink(ox, oy, oz, px, py, zoom)
                return
            orbit = (ox, oy, oz)
            movement = (px, py, zoom)
            aligned_o = tuple(orbit[i] * h.orbit_sign[i] * h.orbit_scale[i]
                              for i in range(3))
            aligned_p = tuple(movement[i] * h.pan_sign[i] * h.pan_scale
                              for i in range(2))
            aligned_z = movement[2] * h.zoom_sign * h.zoom_scale
            sink(aligned_o[0], aligned_o[1], aligned_o[2],
                 aligned_p[0], aligned_p[1], aligned_z)

    # --- the data path -------------------------------------------------------------
    def handle_packet(self, data, runtime_snapshot=None):
        if len(data) < 12:
            return
        runtime_snapshot = runtime_snapshot or self.runtime.snapshot()
        mapping = self._mapping_for_runtime(
            self._mapping, runtime_snapshot)  # one immutable mapping for this entire packet
        raw = struct.unpack_from("<fff", data, 0)
        # The one physical-orientation transform. Everything downstream (pointer, cube, broker,
        # per-app action routing) speaks this same body-relative logical XYZ frame.
        recv = tuple(mapping.global_sign[i] * raw[mapping.global_src[i]] for i in range(3))

        mode = (MODE_CUBE if runtime_snapshot.effective_input_mode == "3d" else MODE_CURSOR)
        if mode != self._last_mode:                 # reset cursor accumulators on any switch
            self._last_mode = mode
            self._mx = self._my = self._sc = 0.0

        if mode == MODE_CUBE:
            if runtime_snapshot.effective_navigation_layer == "secondary":
                # Secondary layer: pan/move + zoom/thrust, mutually exclusive via the same
                # dominance test cursor mode uses for move-vs-scroll. Primary orbit/look is paused.
                twist = mapping.z_sign * recv[mapping.z_src]
                plane = math.hypot(recv[mapping.p_xsrc], recv[mapping.p_ysrc])
                if abs(twist) > mapping.z_dead and abs(twist) > mapping.z_dom * plane:
                    zoom_d = twist * mapping.z_gain                  # same value as before
                    with self.lock:
                        self.distance = min(
                            mapping.dist_max, max(mapping.dist_min, self.distance - zoom_d))
                    self._emit_nav(mapping, 0.0, 0.0, 0.0, 0.0, 0.0, zoom_d)
                else:
                    pdx = mapping.p_xsign * recv[mapping.p_xsrc] * mapping.p_gain
                    pdy = mapping.p_ysign * recv[mapping.p_ysrc] * mapping.p_gain
                    with self.lock:
                        self.pan_x += pdx
                        self.pan_y += pdy
                    self._emit_nav(mapping, 0.0, 0.0, 0.0, pdx, pdy, 0.0)
            else:
                # Orbit: axis-angle increment -> delta quaternion, composed in the world frame.
                vx = mapping.o_sign[0] * recv[mapping.o_src[0]] * mapping.o_sens
                vy = mapping.o_sign[1] * recv[mapping.o_src[1]] * mapping.o_sens
                vz = mapping.o_sign[2] * recv[mapping.o_src[2]] * mapping.o_sens
                dq = quat_from_axis_angle(vx, vy, vz)
                with self.lock:
                    self.orientation = quat_normalize(quat_mul(dq, self.orientation))
                self._emit_nav(mapping, vx, vy, vz, 0.0, 0.0, 0.0)
            return

        # ---- CURSOR mode: convert this packet's rotation into pointer/wheel input ----
        yaw = mapping.s_sign * recv[mapping.s_src]
        plane = math.hypot(recv[mapping.c_xsrc], recv[mapping.c_ysrc])
        if abs(yaw) > mapping.s_dead and abs(yaw) > mapping.s_dom * plane:
            # yaw dominates -> scroll the wheel, carry the fractional remainder
            self._sc += yaw * mapping.s_gain
            notches = int(self._sc)
            if notches:
                send_mouse(wheel=notches)
                self._sc -= notches
        else:
            # otherwise -> move the pointer, carry sub-pixel remainder
            self._mx += mapping.c_xsign * recv[mapping.c_xsrc] * mapping.c_gain
            self._my += mapping.c_ysign * recv[mapping.c_ysrc] * mapping.c_gain
            ix, iy = int(self._mx), int(self._my)
            if ix or iy:
                send_mouse(dx=ix, dy=iy)
                self._mx -= ix
                self._my -= iy
