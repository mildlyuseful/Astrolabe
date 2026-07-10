"""The single source of truth: a JSON config in the per-user config dir.

Defaults are chosen so behavior is byte-identical to the original cube_test.py
constants -- moving the numbers into config must not change the math. The UI and the
output engine both read/write this one object.
"""
import copy
import json
import threading

from .paths import config_path

# --- 3D-navigation bindings. Gains are neutral 1.0 scalers; the per-app add-on bakes in the
#     baseline orbit-orientation/pan/zoom feel, and these scale from there. -----------------
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
    # orbit_pivot: view | cursor | object | origin | selection (+ viewpoint in Blender/SketchUp/
    # Unreal, + cursor_3d in Blender); zoom_mode: to_center | to_object | to_cursor.
    # "cursor"/"to_cursor" target the surface under the MOUSE CURSOR; apps without a cursor
    # resolver fall back to their view/object pivot. "selection" is the selection/bbox pivot;
    # "cursor_3d" is Blender's 3D cursor. (v3 migration renamed pointer->cursor,
    # cursor->selection/cursor_3d, to_pointer->to_cursor; the retired to_cursor alias of
    # to_center migrated to to_center.) effective_scheme passes values through untouched, so
    # no daemon-side whitelist gates these.
    "scheme": {"orbit_pivot": "default", "orbit_style": "default", "zoom_mode": "default"},
}

SCHEME_FIELDS = ("orbit_pivot", "orbit_style", "zoom_mode")

# --- Per-mode, per-axis direction flips for Blender. The add-on interprets the same physical axes
#     differently per nav mode (e.g. ball forward = orbit-pan-vertical, but fly/walk-forward), so a
#     single invert set can't flip one without the other. These are applied IN THE ADD-ON per mode,
#     giving independent control. Defaults bake in the "inside-out" roll fix: viewpoint roll + fly
#     bank are inverted vs external-pivot orbit. ("viewpoint" shares orbit's pan/zoom inverts.)
_DEFAULT_BLENDER_INVERT = {
    "orbit":     {"pitch": False, "yaw": False, "twist": False,
                  "pan_x": False, "pan_y": False, "zoom": False},
    "viewpoint": {"pitch": False, "yaw": False, "roll": True},
    "fly":       {"pitch": False, "yaw": False, "bank": True,
                  "forward": False, "strafe": False, "vertical": False},
    "walk":      {"pitch": False, "yaw": False,
                  "forward": False, "strafe": False, "vertical": False},
}

# --- Blender-only "Advanced" nav options. Deep-merged in additively, so they appear on existing
#     configs WITHOUT a CONFIG_VERSION bump. The parts that map to the generic scheme are NOT
#     duplicated here -- orbit method <-> scheme.orbit_style (free/turntable) and orbit-around <->
#     scheme.orbit_pivot (with a Blender-only extra value "viewpoint") stay the single source of
#     truth. See docs/apps/blender_design.md for the reconciliation table.
_DEFAULT_BLENDER_ADVANCED = {
    "nav_mode": "orbit",            # orbit | fly | walk
    "lock_horizon": False,          # keep the horizon level even in trackball (NDOF "Lock Horizon")
    "twist_action": "roll",         # roll | zoom | dolly | none   (un-shifted twist in ORBIT mode)
    "zoom_style": "zoom",           # zoom (view_distance) | dolly (translate the eye)
    "zoom_to_mouse": False,         # zoom toward the screen-centre surface (see notes for the limit)
    "lock_camera_to_view": False,   # in CAMERA view, drive scene.camera from the trackball
    "pan_scales_with_distance": True,
    "fly_speed": 1.0,
    "walk_speed": 1.0,
    "invert": _DEFAULT_BLENDER_INVERT,   # per-mode direction flips (applied in the add-on)
}


# --- SketchUp: Blender-parity orbit/viewpoint/fly/walk controls. SketchUp has an explicit
#     eye/target/up camera, so these options are applied by the Ruby extension. Per-mode inverts
#     match Blender's names and inside-out roll/bank defaults; there is no 3D-cursor option. -------
_DEFAULT_SKETCHUP_INVERT = {
    "orbit":     {"pitch": False, "yaw": False, "twist": False,
                  "pan_x": False, "pan_y": False, "zoom": False},
    "viewpoint": {"pitch": False, "yaw": False, "roll": True},
    "fly":       {"pitch": False, "yaw": False, "bank": True,
                  "forward": False, "strafe": False, "vertical": False},
    "walk":      {"pitch": False, "yaw": False,
                  "forward": False, "strafe": False, "vertical": False},
}

_DEFAULT_SKETCHUP_ADVANCED = {
    "nav_mode": "orbit",            # orbit | fly | walk
    "lock_horizon": False,           # keep external-pivot free orbit level
    "fly_speed": 1.0,
    "walk_speed": 1.0,
    "invert": _DEFAULT_SKETCHUP_INVERT,
}


# --- Unreal: per-mode direction flips + the "advanced" nav options, mirroring Blender's richer set
#     but adapted to the editor's free-fly eye+rotator camera. Dropped vs Blender: zoom_style (Unreal
#     zoom IS a dolly -- no view-distance), zoom_to_mouse (use zoom_mode), lock_camera_to_view (no
#     editor-camera-view equivalent). Applied IN THE ADD-ON per mode (same reason as Blender, §12.9).
#     Signs default off (best-guess, live-tune) -- unlike Blender, no baked-in roll/bank inverts. ----
_DEFAULT_UNREAL_INVERT = {
    "orbit":     {"pitch": False, "yaw": False, "twist": False,
                  "pan_x": False, "pan_y": False, "zoom": False},
    "viewpoint": {"pitch": False, "yaw": False, "roll": False},
    "fly":       {"pitch": False, "yaw": False, "bank": False,
                  "forward": False, "strafe": False, "vertical": False},
    "walk":      {"pitch": False, "yaw": False,
                  "forward": False, "strafe": False, "vertical": False},
}

_DEFAULT_UNREAL_ADVANCED = {
    "nav_mode": "orbit",            # orbit | fly | walk
    "lock_horizon": False,          # keep the horizon level even in free orbit (force turntable)
    "twist_action": "roll",         # roll | zoom | dolly | none   (un-shifted twist in ORBIT mode)
    "pan_scales_with_distance": True,
    "fly_speed": 1.0,
    "walk_speed": 1.0,
    "invert": _DEFAULT_UNREAL_INVERT,   # per-mode direction flips (applied in the add-on)
}

# Unity reuses the Unreal advanced block, plus a Scene-view-only override for Dynamic Clipping
# (Camera overlay: near/far auto-fit from size — feels like zoom-to-fit while looking around).
# Godot: turntable-only, no roll (editor cursor is yaw/pitch only — free orbit / twist→roll
# cannot persist).
_DEFAULT_UNITY_ADVANCED = copy.deepcopy(_DEFAULT_UNREAL_ADVANCED)
_DEFAULT_UNITY_ADVANCED["override_dynamic_clip"] = True  # force SceneView.cameraSettings.dynamicClip off
# Soft max for under-cursor / auto-depth pivots: scene AABB radius × this multiplier.
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


def _app(enabled=False):
    return {
        "enabled": enabled,
        "installed": False,
        "start_automatically": False,
        "addin_version": "",                 # installed add-in version (set on Set up/Update)
        # Per-app viewport refresh / flush rate (Hz) sent to this CAD app. 0 = use the global
        # bridge.rate_hz default. Lets each app run at its own rate (e.g. SolidWorks at 60).
        "rate_hz": 0,
        # "view" orbit pivot only: seconds the view must be still (no orbit/pan/zoom) before the
        # screen-centre pivot is recomputed. Holds the pivot steady during a gesture; re-settles to
        # the current centre after a pause. (SolidWorks driver; other apps recompute per frame.)
        "view_pivot_hold_sec": 0.5,
        # When True and something is selected, orbit (and to_cursor zoom) use the selection centre
        # instead of the designated pivot (cursor / view / origin / …). Unreal implements this;
        # other apps expose the toggle as a placeholder until their add-ons read it. Deep-merged
        # onto existing configs (no CONFIG_VERSION bump).
        "selection_overrides_pivot": True,
        "bindings": copy.deepcopy(_DEFAULT_3D_BINDINGS),
    }


def _blender_app():
    """Blender's app config: the shared shape plus the Blender-only `advanced` block, with the
    Blender-native default orbit pivot 'viewpoint' (orbit about view_location)."""
    a = _app()
    a["bindings"]["scheme"]["orbit_pivot"] = "viewpoint"
    a["advanced"] = copy.deepcopy(_DEFAULT_BLENDER_ADVANCED)
    return a


def _unreal_app():
    """Unreal's app config: the shared shape plus the Unreal `advanced` block (orbit/fly/walk modes,
    twist action, lock-horizon, per-mode inverts), mirroring Blender's richer set. Default orbit
    pivot stays 'view' (raycast the surface, falling back to selection / free-fly)."""
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
    """SketchUp's shared app shape plus viewpoint/fly/walk and per-mode direction controls."""
    a = _app()
    a["advanced"] = copy.deepcopy(_DEFAULT_SKETCHUP_ADVANCED)
    return a


CONFIG_VERSION = 3

DEFAULTS = {
    "version": CONFIG_VERSION,
    "device": {
        "name": "Trackball BLE",
        "address": "",                                       # blank => scan by name
        "char_uuid": "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
    },
    "general": {
        "default_mode": "cube",                              # "cube" (3D nav) | "cursor" (pointer)
        # CURSOR-mode pointer mapping (MOUSE_* constants)
        "cursor": {"x_src": 1, "x_sign": 1.0, "y_src": 0, "y_sign": 1.0, "gain": 216.0},
        # CURSOR-mode wheel mapping (SCROLL_* constants)
        "scroll": {"src": 2, "sign": 1.0, "gain": 29.0, "deadzone": 0.004, "dominance": 1.7},
        # reserved: the device handles physical buttons in HID mode; kept here for the UI
        "buttons": {"left": "left", "right": "right", "middle": "middle"},
        # default 3D control scheme (per-app can override). Defaults == current behavior.
        "scheme": {"orbit_pivot": "view", "orbit_style": "free", "zoom_mode": "to_center"},
    },
    "apps": {
        "blender":    _blender_app(),
        "freecad":    _app(),
        "sketchup":   _sketchup_app(),
        "unreal":     _unreal_app(),
        "unity":      _unity_app(),
        "godot":      _godot_app(),
        "rhino":      _app(),
        "fusion360":  _app(),
        "solidworks": _app(),
        "onshape":    _app(),
        "autocad":    _app(),
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


def _deep_merge(base, override):
    """Overlay disk values onto defaults so new keys appear automatically on upgrade."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


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
            if self._migrate(int(disk.get("version", 1))):
                self._save_unlocked()
        return self

    def _migrate(self, from_version):
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
            # "to_pointer" became "cursor"/"to_cursor"; the old "cursor" (selection fallback,
            # 3D cursor in Blender) split into "selection" / Blender-only "cursor_3d"; the
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
        self.data["version"] = CONFIG_VERSION
        return changed

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
