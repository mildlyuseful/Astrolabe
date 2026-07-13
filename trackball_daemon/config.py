"""The single source of truth: a JSON config in the per-user config dir.

Defaults are chosen so behavior is byte-identical to the original cube_test.py
constants -- moving the numbers into config must not change the math. The UI and the
output engine both read/write this one object.
"""
import copy
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .paths import config_path

# --- User-facing 3D-navigation overrides. Gains are neutral 1.0 scalers and inversion defaults
#     are off. Developer-owned software alignment lives only in host_profiles.json. -------------
_DEFAULT_3D_BINDINGS = {
    # orbit: AXIS_SOURCE / AXIS_SIGN / ANGLE_SCALE
    "orbit": {"axis_source": [0, 1, 2], "axis_sign": [1.0, 1.0, 1.0], "sensitivity": 1.0},
    # pan: PAN_X_* / PAN_Y_* / PAN_GAIN (1.0 = the add-on's baseline pan feel)
    "pan":   {"x_src": 1, "x_sign": 1.0, "y_src": 0, "y_sign": -1.0, "gain": 1.0},
    # zoom: ZOOM_* / DIST_* (1.0 = the add-on's baseline zoom feel)
    "zoom":  {"src": 2, "sign": 1.0, "gain": 1.0, "deadzone": 0.004, "dominance": 1.7,
              "dist_min": 2.5, "dist_max": 30.0, "dist_default": 6.0},
    # which modifier switches orbit -> pan/zoom ("shift" == original behavior, "none" = always orbit)
    "toggle": "shift",
    # per-axis direction flips (user preference, on top of the base signs). All default off.
    "invert": {"orbit": [False, False, False], "pan": [False, False], "zoom": False},
    # per-app control scheme; "default" inherits the General default (see DEFAULTS["general"]).
    # orbit_pivot: screen_center | camera | cursor | selection | cursor_3d | object | origin.
    # "cursor"/"to_cursor" target the surface under the MOUSE CURSOR; when an app cannot resolve
    # a pivot method, orbit resolution continues through the configured global chain. "selection"
    # is the selected-items pivot; "object" is the model/project bounds centre;
    # "cursor_3d" is Blender's 3D cursor. (v3 migration renamed pointer->cursor,
    # cursor->selection/cursor_3d, to_pointer->to_cursor; the retired to_cursor alias of
    # to_center migrated to to_center.) effective_scheme passes values through untouched, so
    # no daemon-side whitelist gates these.
    "scheme": {"orbit_pivot": "default", "orbit_style": "default", "zoom_mode": "default"},
}

# Physical device orientation. ``source[i]`` says which raw sensor axis becomes logical X/Y/Z;
# it must remain a permutation so no physical axis is accidentally duplicated or lost. Inversion
# is applied after the permutation and before both cursor and 3D routing.
DEFAULT_AXIS_ORIENTATION = {"source": [0, 1, 2], "invert": [False, False, False]}


def normalize_axis_permutation(value):
    """Return a safe raw->logical XYZ permutation, or identity for malformed input."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return [0, 1, 2]
    try:
        out = [int(v) for v in value]
    except (TypeError, ValueError):
        return [0, 1, 2]
    return out if sorted(out) == [0, 1, 2] else [0, 1, 2]


def swap_axis_source(value, target, wanted):
    """Change one logical-axis source by swapping, preserving a valid permutation."""
    out = normalize_axis_permutation(value)
    target = normalize_axis_index(target, 0)
    wanted = normalize_axis_index(wanted, target)
    other = out.index(wanted)
    out[target], out[other] = out[other], out[target]
    return out


def normalize_axis_inversions(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return [False, False, False]
    return [bool(v) for v in value]


def normalize_axis_index(value, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return int(default)
    return value if value in (0, 1, 2) else int(default)


# Rich integrations first receive the app's ordinary orbit vector (X/Y/Z) and shifted movement
# vector (Pan X/Pan Y/Zoom). Each action can independently select one member of its vector. The
# defaults reproduce the pre-v5 fixed wiring exactly; duplication is intentionally allowed here.
DEFAULT_ACTION_AXIS_SOURCE = {
    "orbit":  {"pitch": 0, "yaw": 1, "twist": 2, "pan_x": 0, "pan_y": 1, "zoom": 2},
    "camera": {"pitch": 0, "yaw": 1, "roll": 2},
    "fly":    {"pitch": 0, "yaw": 1, "bank": 2,
               "forward": 1, "strafe": 0, "vertical": 2},
    "walk":   {"pitch": 0, "yaw": 1, "forward": 1, "strafe": 0, "vertical": 2},
}


@dataclass(frozen=True)
class HostBaseline:
    """Runtime-immutable developer correction loaded from host_profiles.json.

    Scale and sign are separate so dominance/deadzone decisions remain based on the unscaled sensor
    motion, preserving the established gesture classifier. User axis routing remains a separate
    config/UI concern.
    """
    orbit_sign: tuple = (1.0, 1.0, 1.0)
    orbit_scale: tuple = (1.0, 1.0, 1.0)
    pan_sign: tuple = (1.0, 1.0)
    pan_scale: float = 1.0
    zoom_sign: float = 1.0
    zoom_scale: float = 1.0
    move_scale: float = 1.0
    apply_in_daemon: bool = True
    # Effective inversion = immutable baseline XOR saved user preference.
    advanced_invert: tuple = ()


HOST_PROFILE_PATH = Path(__file__).with_name("host_profiles.json")
HOST_PROFILE_APP_KEYS = (
    "blender", "freecad", "sketchup", "unreal", "unity", "godot", "rhino", "fusion360",
    "solidworks", "onshape", "autocad",
)
HOST_PROFILE_FIELDS = {
    "orbit_sign", "orbit_scale", "pan_sign", "pan_scale", "zoom_sign", "zoom_scale",
    "move_scale", "apply_in_daemon", "advanced_invert",
}


def _host_vector(value, size, field, app_key, signs=False):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"host profile {app_key}.{field} must contain {size} numbers")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"host profile {app_key}.{field} must contain only numbers")
    out = tuple(float(item) for item in value)
    if signs and any(item not in (-1.0, 1.0) for item in out):
        raise ValueError(f"host profile {app_key}.{field} signs must be -1 or 1")
    if not signs and any(item <= 0.0 for item in out):
        raise ValueError(f"host profile {app_key}.{field} scales must be positive")
    return out


def _host_number(value, field, app_key, sign=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"host profile {app_key}.{field} must be a number")
    value = float(value)
    if sign and value not in (-1.0, 1.0):
        raise ValueError(f"host profile {app_key}.{field} sign must be -1 or 1")
    if not sign and value <= 0.0:
        raise ValueError(f"host profile {app_key}.{field} scale must be positive")
    return value


def load_host_baseline_profiles(path=HOST_PROFILE_PATH):
    """Validate developer-owned raw profiles and freeze them for this daemon process."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load host alignment profiles from {path}: {exc}") from exc
    if (not isinstance(raw, dict) or raw.get("schema") != 1 or
            not isinstance(raw.get("profiles"), dict)):
        raise ValueError("host_profiles.json must contain schema 1 and a profiles object")
    supplied = raw["profiles"]
    if set(supplied) != set(HOST_PROFILE_APP_KEYS):
        missing = sorted(set(HOST_PROFILE_APP_KEYS) - set(supplied))
        extra = sorted(set(supplied) - set(HOST_PROFILE_APP_KEYS))
        raise ValueError(f"host profile suite mismatch; missing={missing}, extra={extra}")

    profiles = {}
    for app_key in HOST_PROFILE_APP_KEYS:
        item = supplied[app_key]
        if not isinstance(item, dict):
            raise ValueError(f"host profile {app_key} must be an object")
        if set(item) != HOST_PROFILE_FIELDS:
            missing = sorted(HOST_PROFILE_FIELDS - set(item))
            extra = sorted(set(item) - HOST_PROFILE_FIELDS)
            raise ValueError(f"host profile {app_key} fields mismatch; missing={missing}, extra={extra}")
        advanced = item.get("advanced_invert", [])
        if (not isinstance(advanced, list) or
                any(not isinstance(path, str) or path.count(".") != 1 or
                    not all(part.strip() for part in path.split(".", 1)) for path in advanced)):
            raise ValueError(f"host profile {app_key}.advanced_invert must use mode.action strings")
        apply_in_daemon = item.get("apply_in_daemon")
        if not isinstance(apply_in_daemon, bool):
            raise ValueError(f"host profile {app_key}.apply_in_daemon must be true or false")
        profiles[app_key] = HostBaseline(
            orbit_sign=_host_vector(item.get("orbit_sign"), 3, "orbit_sign", app_key, signs=True),
            orbit_scale=_host_vector(item.get("orbit_scale"), 3, "orbit_scale", app_key),
            pan_sign=_host_vector(item.get("pan_sign"), 2, "pan_sign", app_key, signs=True),
            pan_scale=_host_number(item.get("pan_scale"), "pan_scale", app_key),
            zoom_sign=_host_number(item.get("zoom_sign"), "zoom_sign", app_key, sign=True),
            zoom_scale=_host_number(item.get("zoom_scale"), "zoom_scale", app_key),
            move_scale=_host_number(item.get("move_scale"), "move_scale", app_key),
            apply_in_daemon=apply_in_daemon,
            advanced_invert=tuple(tuple(path.split(".", 1)) for path in advanced),
        )
    return MappingProxyType(profiles)


HOST_BASELINE_PROFILES = load_host_baseline_profiles()


def host_baseline(app_key):
    """Return the immutable baseline for a supported app (neutral for unknown keys)."""
    return HOST_BASELINE_PROFILES.get(app_key, HostBaseline())


def host_baseline_payload(app_key):
    """JSON-safe factors consumed inside rich mode-aware add-ons."""
    baseline = host_baseline(app_key)
    return {
        "orbit": [baseline.orbit_sign[i] * baseline.orbit_scale[i] for i in range(3)],
        "pan": [baseline.pan_sign[i] * baseline.pan_scale for i in range(2)],
        "zoom": baseline.zoom_sign * baseline.zoom_scale,
        "move": baseline.move_scale,
    }


def compose_advanced_with_host_baseline(app_key, advanced):
    """Return wire-ready advanced settings: immutable host corrections XOR user preferences."""
    out = copy.deepcopy(advanced or {})
    for mode, action in host_baseline(app_key).advanced_invert:
        group = out.setdefault("invert", {}).setdefault(mode, {})
        group[action] = not bool(group.get(action, False))
    return out


def normalize_action_axis_sources(value):
    """Deep-normalize rich per-action source indices while preserving the complete schema."""
    value = value if isinstance(value, dict) else {}
    out = {}
    for mode, defaults in DEFAULT_ACTION_AXIS_SOURCE.items():
        supplied = value.get(mode) if isinstance(value.get(mode), dict) else {}
        out[mode] = {action: normalize_axis_index(supplied.get(action, default), default)
                     for action, default in defaults.items()}
    return out


def normalize_binding_axis_sources(bindings):
    """Normalize the ordinary per-app orbit/pan/zoom source indices in-place."""
    if not isinstance(bindings, dict):
        return
    orbit = bindings.get("orbit") or {}
    src = orbit.get("axis_source")
    if not isinstance(src, (list, tuple)) or len(src) != 3:
        src = [0, 1, 2]
    orbit["axis_source"] = [normalize_axis_index(src[i], i) for i in range(3)]
    pan = bindings.get("pan") or {}
    pan["x_src"] = normalize_axis_index(pan.get("x_src"), 1)
    pan["y_src"] = normalize_axis_index(pan.get("y_src"), 0)
    zoom = bindings.get("zoom") or {}
    zoom["src"] = normalize_axis_index(zoom.get("src"), 2)

SCHEME_FIELDS = ("orbit_pivot", "orbit_style", "zoom_mode")

# Canonical identifiers accepted by the global orbit-pivot fallback editor and sent to every
# integration. ``camera`` means turn the camera in place; ``screen_center`` means raycast the first
# surface hit under the center of the viewport. These names intentionally describe different jobs.
ORBIT_PIVOT_METHODS = ("camera", "screen_center", "cursor", "selection", "cursor_3d",
                       "object", "origin")
DEFAULT_ORBIT_PIVOT_FALLBACKS = ("cursor_3d", "camera", "object", "origin")
_LEGACY_ORBIT_PIVOT_NAMES = {"view": "screen_center", "viewpoint": "camera"}


def normalize_orbit_pivot_fallbacks(value):
    """Return a stable, duplicate-free fallback list.

    An explicit list may be empty (the user intentionally requested no fallbacks).  Malformed
    values use the shipped default; unknown method ids and duplicate entries are discarded.
    """
    if not isinstance(value, list):
        return list(DEFAULT_ORBIT_PIVOT_FALLBACKS)
    out = []
    for method in value:
        method = _LEGACY_ORBIT_PIVOT_NAMES.get(method, method)
        if method in ORBIT_PIVOT_METHODS and method not in out:
            out.append(method)
    return out


def orbit_pivot_candidates(primary, fallbacks):
    """Primary first, then the global chain from its beginning, with duplicates removed."""
    out = []
    primary = _LEGACY_ORBIT_PIVOT_NAMES.get(primary, primary)
    for method in [primary] + normalize_orbit_pivot_fallbacks(fallbacks):
        if method in ORBIT_PIVOT_METHODS and method not in out:
            out.append(method)
    return out

# --- Per-mode action routing for Blender. The add-on interprets the same inputs differently per nav
#     mode, so source selection and inversion are applied IN THE ADD-ON once the mode is known,
#     giving independent control. Defaults bake in the "inside-out" roll fix: camera roll + fly
#     bank are inverted vs external-pivot orbit. ("camera" shares orbit's pan/zoom inverts.)
_DEFAULT_BLENDER_INVERT = {
    "orbit":     {"pitch": False, "yaw": False, "twist": False,
                  "pan_x": False, "pan_y": False, "zoom": False},
    "camera":    {"pitch": False, "yaw": False, "roll": False},
    "fly":       {"pitch": False, "yaw": False, "bank": False,
                  "forward": False, "strafe": False, "vertical": False},
    "walk":      {"pitch": False, "yaw": False,
                  "forward": False, "strafe": False, "vertical": False},
}

# --- Blender-only "Advanced" nav options. Deep-merged in additively, so they appear on existing
#     configs WITHOUT a CONFIG_VERSION bump. The parts that map to the generic scheme are NOT
#     duplicated here -- orbit method <-> scheme.orbit_style (free/turntable) and orbit-around <->
#     scheme.orbit_pivot stay the single source of
#     truth. See docs/apps/blender_design.md for the reconciliation table.
_DEFAULT_BLENDER_ADVANCED = {
    "nav_mode": "orbit",            # orbit | fly | walk
    "lock_horizon": False,          # keep the horizon level even in trackball (NDOF "Lock Horizon")
    "twist_action": "roll",         # roll | zoom | dolly | none   (un-shifted twist in ORBIT mode)
    "zoom_style": "zoom",           # zoom (view_distance) | dolly (translate the eye)
    "lock_camera_to_view": False,   # in CAMERA view, drive scene.camera from the trackball
    "pan_scales_with_distance": True,
    "fly_speed": 1.0,
    "walk_speed": 1.0,
    "invert": _DEFAULT_BLENDER_INVERT,   # per-mode direction flips (applied in the add-on)
    "axis_source": DEFAULT_ACTION_AXIS_SOURCE,
}


# Every integration exposes Twist action. Rich integrations consume it after their mode-specific
# routing; ordinary integrations are routed by OutputEngine before host alignment.
_DEFAULT_COMMON_ADVANCED = {
    "twist_action": "roll",         # roll | zoom | dolly | none
}


# --- SketchUp: Blender-parity orbit/camera/fly/walk controls. SketchUp has an explicit
#     eye/target/up camera, so these options are applied by the Ruby extension. Per-mode routes
#     match Blender's names and inside-out roll/bank defaults; there is no 3D-cursor option. -------
_DEFAULT_SKETCHUP_INVERT = {
    "orbit":     {"pitch": False, "yaw": False, "twist": False,
                  "pan_x": False, "pan_y": False, "zoom": False},
    "camera":    {"pitch": False, "yaw": False, "roll": False},
    "fly":       {"pitch": False, "yaw": False, "bank": False,
                  "forward": False, "strafe": False, "vertical": False},
    "walk":      {"pitch": False, "yaw": False,
                  "forward": False, "strafe": False, "vertical": False},
}

_DEFAULT_SKETCHUP_ADVANCED = {
    "nav_mode": "orbit",            # orbit | fly | walk
    "lock_horizon": False,           # keep external-pivot free orbit level
    "twist_action": "roll",
    "zoom_style": "dolly",          # shifted zoom channel: native zoom | camera dolly
    "fly_speed": 1.0,
    "walk_speed": 1.0,
    "invert": _DEFAULT_SKETCHUP_INVERT,
    "axis_source": DEFAULT_ACTION_AXIS_SOURCE,
}


# --- Unreal: per-mode action routes + the "advanced" nav options, mirroring Blender's richer set
#     but adapted to the editor's free-fly eye+rotator camera. Dropped vs Blender:
#     lock_camera_to_view (no
#     editor-camera-view equivalent). Applied IN THE ADD-ON per mode (same reason as Blender, §12.9).
#     Signs default off (best-guess, live-tune) -- unlike Blender, no baked-in roll/bank inverts. ----
_DEFAULT_UNREAL_INVERT = {
    "orbit":     {"pitch": False, "yaw": False, "twist": False,
                  "pan_x": False, "pan_y": False, "zoom": False},
    "camera":    {"pitch": False, "yaw": False, "roll": False},
    "fly":       {"pitch": False, "yaw": False, "bank": False,
                  "forward": False, "strafe": False, "vertical": False},
    "walk":      {"pitch": False, "yaw": False,
                  "forward": False, "strafe": False, "vertical": False},
}

_DEFAULT_UNREAL_ADVANCED = {
    "nav_mode": "orbit",            # orbit | fly | walk
    "lock_horizon": False,          # keep the horizon level even in free orbit (force turntable)
    "twist_action": "roll",         # roll | zoom | dolly | none   (un-shifted twist in ORBIT mode)
    "zoom_style": "dolly",          # shifted zoom channel: viewport FOV/size zoom | camera dolly
    "pan_scales_with_distance": True,
    "fly_speed": 1.0,
    "walk_speed": 1.0,
    "invert": _DEFAULT_UNREAL_INVERT,   # paired with axis_source in the add-on
    "axis_source": DEFAULT_ACTION_AXIS_SOURCE,
}

# Unity reuses the Unreal advanced block, plus a Scene-view-only override for Dynamic Clipping
# (Camera overlay: near/far auto-fit from size — feels like zoom-to-fit while looking around).
# Godot: turntable-only, no roll (editor cursor is yaw/pitch only — free orbit / twist→roll
# cannot persist).
_DEFAULT_UNITY_ADVANCED = copy.deepcopy(_DEFAULT_UNREAL_ADVANCED)
_DEFAULT_UNITY_ADVANCED["override_dynamic_clip"] = True  # force SceneView.cameraSettings.dynamicClip off
# Soft max for under-cursor / screen-center pivots: scene AABB radius × this multiplier.
# Stops horizon-line hits from rocketing the camera to infinity.
_DEFAULT_UNITY_ADVANCED["pivot_extent_mult"] = 8.0
_DEFAULT_GODOT_ADVANCED = copy.deepcopy(_DEFAULT_UNREAL_ADVANCED)
_DEFAULT_GODOT_ADVANCED["twist_action"] = "zoom"
_DEFAULT_GODOT_ADVANCED["lock_horizon"] = True


def effective_scheme(general_scheme, app_scheme):
    """Resolve a per-app scheme against the general default (per-app 'default' => inherit)."""
    out = {}
    for f in SCHEME_FIELDS:
        v = (app_scheme or {}).get(f, "default")
        out[f] = (general_scheme or {}).get(f) if v == "default" else v
    return out


def effective_level_horizon(general_cfg, app_cfg):
    """Level-horizon-on-entry for one app: the per-app checkbox once the user has touched it,
    otherwise the General default. The per-app key is deliberately ABSENT from the shipped app
    shape so an untouched app keeps following the General checkbox; a per-app reset removes the
    override again (see APP_PROFILE_FIELDS)."""
    v = (app_cfg or {}).get("level_horizon_on_entry")
    if isinstance(v, bool):
        return v
    return bool((general_cfg or {}).get("level_horizon_on_entry", True))


def _app(enabled=False):
    return {
        "enabled": enabled,
        "installed": False,
        "addin_version": "",                 # installed add-in version (set on Set up/Update)
        # Per-app viewport refresh / flush rate (Hz) sent to this CAD app. 0 = use the global
        # bridge.rate_hz default. Lets each app run at its own rate (e.g. SolidWorks at 60).
        "rate_hz": 0,
        # Independent idle gaps for orbit pivots and under-cursor zoom targets. Pan invalidates
        # only the orbit pivot; cursor zoom retains its target until zoom_cursor_hold_sec expires.
        "orbit_pivot_hold_sec": 0.5,
        "zoom_cursor_hold_sec": 0.5,
        # When True and something is selected, orbit (and supported to_cursor zoom paths) use the
        # selection centre instead of the designated pivot. Every integration consumes this setting;
        # it is deep-merged onto existing configs without a CONFIG_VERSION bump.
        "selection_overrides_pivot": True,
        "bindings": copy.deepcopy(_DEFAULT_3D_BINDINGS),
        "advanced": copy.deepcopy(_DEFAULT_COMMON_ADVANCED),
    }


def _blender_app():
    """Blender's app config: the shared shape plus the Blender-only `advanced` block, with the
    Blender-native default orbit pivot 'camera' (turn in place about the eye)."""
    a = _app()
    a["bindings"]["scheme"]["orbit_pivot"] = "camera"
    a["advanced"] = copy.deepcopy(_DEFAULT_BLENDER_ADVANCED)
    return a


def _fusion_app():
    """Fusion supports both view-extents zoom and camera dolly."""
    a = _app()
    a["advanced"]["zoom_style"] = "zoom"
    return a


def _zoom_behavior_app(default="dolly"):
    """Ordinary integration with distinct native Zoom and camera Dolly paths."""
    a = _app()
    a["advanced"]["zoom_style"] = default
    return a


def _unreal_app():
    """Unreal's app config: the shared shape plus the Unreal `advanced` block (orbit/fly/walk modes,
    twist action, lock-horizon, per-mode action routes), mirroring Blender's richer set. Default orbit
    pivot stays 'screen_center' (raycast the first surface under the viewport center)."""
    a = _app()
    a["advanced"] = copy.deepcopy(_DEFAULT_UNREAL_ADVANCED)
    return a


def _unity_app():
    """Unity Scene view: Unreal-shaped advanced block (orbit/fly/walk + under-cursor + selection)."""
    a = _app()
    a["advanced"] = copy.deepcopy(_DEFAULT_UNITY_ADVANCED)
    return a


def _godot_app():
    """Godot editor 3D viewport: turntable-only (no free orbit / roll — editor limitation)."""
    a = _app()
    a["advanced"] = copy.deepcopy(_DEFAULT_GODOT_ADVANCED)
    a["bindings"]["scheme"]["orbit_style"] = "turntable"
    return a


def _sketchup_app():
    """SketchUp's shared app shape plus camera/fly/walk and per-mode direction controls."""
    a = _app()
    a["advanced"] = copy.deepcopy(_DEFAULT_SKETCHUP_ADVANCED)
    return a


CONFIG_VERSION = 8

DEFAULTS = {
    "version": CONFIG_VERSION,
    "device": {
        "name": "Trackball BLE",
        "address": "",                                       # blank => scan by name
        "char_uuid": "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
    },
    "general": {
        "default_mode": "cube",                              # "cube" (3D nav) | "cursor" (pointer)
        "axis_orientation": copy.deepcopy(DEFAULT_AXIS_ORIENTATION),
        # CURSOR-mode pointer mapping (MOUSE_* constants)
        "cursor": {"x_src": 1, "x_sign": 1.0, "y_src": 0, "y_sign": 1.0, "gain": 216.0},
        # CURSOR-mode wheel mapping (SCROLL_* constants)
        "scroll": {"src": 2, "sign": 1.0, "gain": 29.0, "deadzone": 0.004, "dominance": 1.7},
        # reserved: the device handles physical buttons in HID mode; kept here for the UI
        "buttons": {"left": "left", "right": "right", "middle": "middle"},
        # default 3D control scheme (per-app can override). Defaults == current behavior.
        "scheme": {"orbit_pivot": "screen_center", "orbit_style": "free", "zoom_mode": "to_center"},
        # If the selected pivot cannot resolve, every integration restarts here (it does not begin
        # after the failed method). Unsupported methods are skipped by that integration.
        "orbit_pivot_fallbacks": list(DEFAULT_ORBIT_PIVOT_FALLBACKS),
        # On switching INTO a fixed-horizon mode (Turntable orbit style, Lock Horizon, Walk),
        # remove any existing roll instead of locking the tilted horizon (issue #2). False keeps
        # the old lock-current-tilt behavior. Per-app override: apps.<key>.level_horizon_on_entry
        # (absent = follow this default; see effective_level_horizon). Deep-merged additively --
        # no CONFIG_VERSION bump.
        "level_horizon_on_entry": True,
    },
    "apps": {
        "blender":    _blender_app(),
        "freecad":    _app(),
        "sketchup":   _sketchup_app(),
        "unreal":     _unreal_app(),
        "unity":      _unity_app(),
        "godot":      _godot_app(),
        "rhino":      _zoom_behavior_app("dolly"),
        "fusion360":  _fusion_app(),
        "solidworks": _app(),
        "onshape":    _app(),
        "autocad":    _zoom_behavior_app("zoom"),
    },
    "active_app": "blender",
    # Local loopback bridge the CAD add-ons connect to (127.0.0.1 only). rate_hz is the
    # 3D viewport update/refresh rate -- lower it if the CAD app lags behind your motion.
    "bridge": {"port": 47900, "rate_hz": 30},
    # Onshape (browser) bridge: we impersonate the 3Dconnexion local NL-Proxy service that
    # Onshape's page connects to, at this fixed loopback endpoint (it MUST be 127.51.68.120:8181 --
    # that's the address Onshape's 3Dconnexion client probes). Blank cert/key paths => the driver
    # uses generated certs in the config dir (onshape_cert.pem / onshape_key.pem).
    # Under-cursor orbit uses a page userscript that POSTs exact #canvas NDC to /trackball/pointer
    # (see docs/apps/onshape.md §8.14). cursor_userscript_warn_dismissed suppresses the one-time
    # UI warning when the user picks Orbit pivot = cursor.
    "onshape": {"address": "127.51.68.120", "port": 8181, "cert_path": "", "key_path": "",
                "cursor_userscript_warn_dismissed": False},
}

# Shipped user-layer profiles. Operational/install state is intentionally excluded so resetting a
# navigation profile never disables an app or forgets an installed add-in version.
# level_horizon_on_entry is listed but absent from every shipped profile, so a reset REMOVES the
# per-app override and the app follows the General checkbox again.
APP_PROFILE_FIELDS = ("rate_hz", "orbit_pivot_hold_sec", "zoom_cursor_hold_sec",
                      "selection_overrides_pivot", "level_horizon_on_entry",
                      "bindings", "advanced")
DEFAULT_PROFILE_KEYS = tuple(DEFAULTS["apps"])


def _deep_merge(base, override):
    """Overlay disk values onto defaults so new keys appear automatically on upgrade."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


DEFAULT_PROFILE_PATH = Path(__file__).with_name("default_profiles.json")


def load_default_profiles(path=DEFAULT_PROFILE_PATH):
    """Load the developer-editable shipped user defaults, separate from host alignment."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load shipped default profiles from {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ValueError("default_profiles.json must use schema 1")
    general = raw.get("general")
    common = raw.get("common")
    overrides = raw.get("profiles")
    if not isinstance(general, dict) or set(general) != set(DEFAULTS["general"]):
        raise ValueError("default profile general settings must contain the complete General suite")
    if not isinstance(common, dict) or set(common) != set(APP_PROFILE_FIELDS):
        raise ValueError("default profile common settings must contain every app profile field")
    if not isinstance(overrides, dict) or set(overrides) != set(DEFAULT_PROFILE_KEYS):
        raise ValueError("default profile app suite mismatch")
    profiles = {}
    for key in DEFAULT_PROFILE_KEYS:
        override = overrides[key]
        if not isinstance(override, dict) or not set(override).issubset(APP_PROFILE_FIELDS):
            raise ValueError(f"default profile {key} override contains unsupported fields")
        profile = _deep_merge(common, override)
        # null means this app inherits the General checkbox rather than saving an override.
        if profile.get("level_horizon_on_entry") is None:
            profile.pop("level_horizon_on_entry", None)
        profiles[key] = profile
    return copy.deepcopy(general), MappingProxyType(profiles)


_SHIPPED_GENERAL, _DEFAULT_APP_PROFILES = load_default_profiles()
DEFAULTS["general"] = copy.deepcopy(_SHIPPED_GENERAL)
for _app_key, _profile in _DEFAULT_APP_PROFILES.items():
    DEFAULTS["apps"][_app_key].update(copy.deepcopy(_profile))


def default_app_profile(app_key):
    """A detached full navigation profile suitable for an atomic per-app reset."""
    if app_key not in _DEFAULT_APP_PROFILES:
        raise KeyError(app_key)
    return copy.deepcopy(_DEFAULT_APP_PROFILES[app_key])


class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self.path = config_path()
        self.data = copy.deepcopy(DEFAULTS)
        self.first_run = False
        self._listeners = []

    def load(self):
        with self._lock:
            if not self.path.exists():
                self.first_run = True
                self.data = copy.deepcopy(DEFAULTS)
                self._save_unlocked()
                return self
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Corrupt/unreadable -> fall back to defaults but keep the bad file untouched.
                self.data = copy.deepcopy(DEFAULTS)
                return self
            self.data = _deep_merge(DEFAULTS, disk)
            changed = self._migrate(int(disk.get("version", 1)), disk)
            orientation = self.data["general"].get("axis_orientation") or {}
            normalized_orientation = {
                "source": normalize_axis_permutation(orientation.get("source")),
                "invert": normalize_axis_inversions(orientation.get("invert")),
            }
            if orientation != normalized_orientation:
                self.data["general"]["axis_orientation"] = normalized_orientation
                changed = True
            for app in self.data["apps"].values():
                # Step 6 removed this never-wired per-app UI setting. A future run-at-login option
                # belongs at daemon scope, so clean the stale key from the one local config too.
                if "start_automatically" in app:
                    app.pop("start_automatically", None)
                    changed = True
                bindings = app.get("bindings") or {}
                before = copy.deepcopy(bindings)
                normalize_binding_axis_sources(bindings)
                if bindings != before:
                    changed = True
                advanced = app.get("advanced")
                if isinstance(advanced, dict):
                    # Superseded by the shared bindings.scheme.zoom_mode dropdown.
                    if "zoom_to_mouse" in advanced:
                        advanced.pop("zoom_to_mouse", None)
                        changed = True
                    sources = normalize_action_axis_sources(advanced.get("axis_source"))
                    if advanced.get("axis_source") != sources:
                        advanced["axis_source"] = sources
                        changed = True
            disk_general = disk.get("general") or {}
            raw_fallbacks = disk_general.get(
                "orbit_pivot_fallbacks", list(DEFAULT_ORBIT_PIVOT_FALLBACKS))
            fallbacks = normalize_orbit_pivot_fallbacks(raw_fallbacks)
            if ("orbit_pivot_fallbacks" not in disk_general or
                    self.data["general"].get("orbit_pivot_fallbacks") != fallbacks):
                self.data["general"]["orbit_pivot_fallbacks"] = fallbacks
                changed = True
            if changed:
                self._save_unlocked()
        return self

    def _migrate(self, from_version, disk=None):
        """One-time upgrades for config whose semantics changed. Returns True if changed."""
        changed = False
        if from_version < 2:
            # v2: per-app orbit-invert / pan-gain / zoom-gain corrections moved into the CAD
            # add-ons. Reset 3D bindings so the daemon gains scale from a neutral 1.0 baseline
            # (otherwise the old saved corrections would double the baked-in ones).
            for app in self.data["apps"].values():
                app["bindings"] = copy.deepcopy(_DEFAULT_3D_BINDINGS)
            changed = True
        if from_version < 3:
            # v3: scheme values renamed to match the UI labels. Under-mouse "pointer"/
            # "to_pointer" became "cursor"/"to_cursor"; the old "cursor" (a selection-style pivot,
            # and specifically the 3D cursor in Blender) split into "selection" / "cursor_3d"; the
            # retired legacy "to_cursor" (a to_center alias everywhere) maps to "to_center".
            # Order matters: retire old to_cursor BEFORE to_pointer takes that name.
            pivot_map = {"pointer": "cursor", "cursor": "selection"}
            zoom_map = {"to_cursor": "to_center", "to_pointer": "to_cursor"}

            def _remap(scheme, blender=False):
                if not isinstance(scheme, dict):
                    return
                op = scheme.get("orbit_pivot")
                if blender and op == "cursor":
                    scheme["orbit_pivot"] = "cursor_3d"   # Blender's old "cursor" = its 3D cursor
                elif op in pivot_map:
                    scheme["orbit_pivot"] = pivot_map[op]
                zm = scheme.get("zoom_mode")
                if zm in zoom_map:
                    scheme["zoom_mode"] = zoom_map[zm]

            _remap(self.data["general"].get("scheme"))
            for key, app in self.data["apps"].items():
                _remap(app.get("bindings", {}).get("scheme"), blender=(key == "blender"))
            changed = True
        if from_version < 4:
            # v4: disambiguate camera turn-in-place from the viewport-center raycast everywhere.
            # Also rename the related invert group and SolidWorks hold setting.
            def _rename_pivot(scheme):
                if isinstance(scheme, dict):
                    op = scheme.get("orbit_pivot")
                    scheme["orbit_pivot"] = _LEGACY_ORBIT_PIVOT_NAMES.get(op, op)

            _rename_pivot(self.data["general"].get("scheme"))
            for app in self.data["apps"].values():
                _rename_pivot(app.get("bindings", {}).get("scheme"))
                if "view_pivot_hold_sec" in app:
                    app["orbit_pivot_hold_sec"] = app.pop("view_pivot_hold_sec")
                invert = app.get("advanced", {}).get("invert")
                if isinstance(invert, dict) and "viewpoint" in invert:
                    invert["camera"] = _deep_merge(
                        invert.get("camera") or {}, invert.pop("viewpoint"))
            changed = True
        if from_version < 5:
            # v5 adds the global physical orientation and rich per-action axis routing. Defaults
            # are identity mappings, so migration is behavior-neutral; load() validates them.
            changed = True
        if from_version < 6:
            # v6 moves baked camera-roll/fly-bank corrections out of saved user preferences and
            # into immutable host baselines. XOR the stored value so every existing effective
            # direction is preserved. Missing old keys stay at the new neutral user default.
            disk_apps = (disk or {}).get("apps") or {}
            for app_key, baseline in HOST_BASELINE_PROFILES.items():
                disk_invert = (((disk_apps.get(app_key) or {}).get("advanced") or {}).get("invert") or {})
                user_invert = (((self.data["apps"].get(app_key) or {}).get("advanced") or {})
                               .get("invert") or {})
                for mode, action in baseline.advanced_invert:
                    old_group = disk_invert.get(mode) if isinstance(disk_invert.get(mode), dict) else {}
                    if action in old_group:
                        user_invert.setdefault(mode, {})[action] = not bool(old_group[action])
            changed = True
        if from_version == 6:
            # v7 completes the pre-alpha ownership split. Values used while calibrating hosts had
            # been saved as ordinary user overrides, so reset every per-app navigation profile once
            # to the neutral shipped user layer. Enable/install/startup/version state is untouched;
            # global physical orientation is also preserved because it belongs to the user/device.
            # Only v6 is reset: older configs skipping directly to v7 retain their established user
            # preferences after the historical migrations instead of being destructively cleared.
            for app_key in DEFAULT_PROFILE_KEYS:
                profile = default_app_profile(app_key)
                app = self.data["apps"][app_key]
                for field in APP_PROFILE_FIELDS:
                    if field in profile:
                        app[field] = copy.deepcopy(profile[field])
                    else:
                        app.pop(field, None)
            changed = True
        if from_version < 8:
            # v8 separates orbit-pivot and cursor-zoom gesture lifetimes. Preserve the old orbit
            # value, add the shipped zoom hold, and remove the obsolete screen-center-only name.
            for app in self.data["apps"].values():
                if "screen_center_pivot_hold_sec" in app:
                    app["orbit_pivot_hold_sec"] = app.pop("screen_center_pivot_hold_sec")
            changed = True
        self.data["version"] = CONFIG_VERSION
        return changed

    def reset_app_profile(self, app_key):
        """Atomically reset every navigation field for one app, preserving install/enable state."""
        profile = default_app_profile(app_key)
        with self._lock:
            app = self.data["apps"][app_key]
            for field in APP_PROFILE_FIELDS:
                if field in profile:
                    app[field] = copy.deepcopy(profile[field])
                else:
                    app.pop(field, None)
            self._save_unlocked()
        self._notify()

    def _save_unlocked(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        tmp.replace(self.path)            # atomic-ish replace so we never write a half file

    def save(self):
        with self._lock:
            self._save_unlocked()
        self._notify()

    # --- live-apply notification ---------------------------------------------------
    def add_listener(self, fn):
        self._listeners.append(fn)

    def _notify(self):
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    # --- convenience nav -----------------------------------------------------------
    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, keys, value):
        node = self.data
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
