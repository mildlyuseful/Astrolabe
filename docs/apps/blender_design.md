# Blender 3D-navigation framework — design notes

> **Taking over this project?** Read [`blender.md`](blender.md) first — it's the
> maintainer's guide (architecture, gotchas, the full solved-problem history, testing). This file is
> the design rationale it refers back to.

Companion to the Fusion 360 add-in (`plugins/fusion360/TrackballNav/TrackballNav.py`).
Blender is the richest 3D target the daemon drives, so its **Per-App Bindings → blender** section is
much larger than the other apps' (it absorbed the former separate "Blender Advanced" tab as of v0.1.9,
and the bindings tab is scrollable to fit it).

Everything below was verified against the **installed** Blender (`blender --background --python`),
not assumed — see "Verified facts" at the end.

---

## 1. How Blender's viewport differs from a CAD camera

A CAD camera (Fusion, SolidWorks) is an eye + a *look-at target at an arbitrary depth* on the
optical axis. Blender's viewport is a `RegionView3D` with a different, simpler model:

| field               | meaning                                                            |
| ------------------- | ----------------------------------------------------------------- |
| `view_location`     | the point the view orbits around (Vector, world space)            |
| `view_rotation`     | view orientation (Quaternion). Camera looks down its local **−Z** |
| `view_distance`     | eye distance from `view_location` (also governs ortho zoom)       |
| `view_perspective`  | `'PERSP'` \| `'ORTHO'` \| `'CAMERA'`                               |

Derived (our `_view_axes` / `_eye`):

```
world_right = view_rotation @ (1,0,0)
world_up    = view_rotation @ (0,1,0)
world_fwd   = view_rotation @ (0,0,-1)   # into the screen
world_back  = view_rotation @ (0,0, 1)   # toward the eye
eye         = view_location + world_back * view_distance
```

Because orbit is **natively about `view_location`**, the trackball-native pivot ("viewpoint") is
trivial: rotate `view_rotation`, leave `view_location` alone. Every other pivot is handled by one
unified trick (see §4).

---

## 2. Catalog of trackball→Blender control modes (brief §3) and our mapping

Physical DOFs: ball **pitch** (X, `o[0]`), ball **yaw** (Y, `o[1]`), ball **twist** (Z, `o[2]`);
the Shift modifier (the daemon's `toggle`) switches orbit → pan/zoom. The daemon already scales
o/p/z by the per-app bindings and gates them mutually exclusive per frame, exactly like Fusion —
the add-on only bakes in a baseline sign/scale and the scheme.

| # | Mode | Implemented as | Selected by |
|---|------|----------------|-------------|
| A | Trackball orbit (free) | local-axis rotation of `view_rotation` (right-mul feel); twist→roll | `orbit_style="free"` |
| B | Turntable orbit | azimuth about **world Z**, elevation about world-right, roll suppressed | `orbit_style="turntable"` (or `lock_horizon`) |
| C | Pan | `view_location += right*dx + up*dy`, `k ∝ view_distance` | Shift + ball X/Y |
| D | Zoom | `view_distance *= factor` (ortho scale follows) | Shift + twist, `zoom_style="zoom"` |
| E | Dolly | translate `view_location` along `world_fwd` | `zoom_style="dolly"`, or `twist_action="dolly"` |
| F | View roll | rotate about `world_fwd` | `twist_action="roll"` (default) |
| G | Orbit pivot | unified rotate-about-P (§4): viewpoint / selection / auto-depth / 3D-cursor / origin | `orbit_pivot` (generic, +`viewpoint`) |
| H | Fly | look = orbit about the **eye**; Shift+ball forward/back = thrust, sideways = strafe, twist = rise/fall (scale floored so it never vanishes up close) | `nav_mode="fly"` (or the in-Blender toggle), `fly_speed` |
| I | Walk | like fly but horizon-locked look + horizontal-plane move (forward/strafe), twist = rise/fall | `nav_mode="walk"`, `walk_speed` |
| J | Camera view | `lock_camera_to_view` on → drive `scene.camera.matrix_world` from the view; **off → navigating exits camera view to PERSP** (v0.1.6, like Blender — else rv edits are invisible while the camera is rendered) | `lock_camera_to_view` |
| K | Discrete view ops | **deferred** — no button-event channel exists yet (see §6) | — |
| L | Multi-area targeting | drive the active VIEW_3D; no-op cleanly when none is focused | automatic |
| M | NDOF-style umbrella | realized by the combination of the above (nav_mode + orbit method + lock_horizon + per-mode/per-axis invert + deadzone/sensitivity) | the whole Blender bindings section |

### Twist (`o[2]`) in **orbit** mode — `twist_action`
`roll` (default) | `zoom` | `dolly` | `none`. Under Shift the daemon already routes twist to the
zoom channel (`z`), so Shift+twist always zooms/dollies per `zoom_style`; `twist_action` only
governs the *un-shifted* twist.

---

## 3. Settings model — reconciling generic scheme vs Blender-only "Advanced"

The brief's key constraint: **one source of truth**. The generic per-app scheme already carries
`orbit_pivot`, `orbit_style`, `zoom_mode` (with per-app `"default"` → General inheritance), and the
daemon already streams them as `op`/`os`/`zm`. So we **keep those as the source of truth** for the
parts that map, and put **only genuinely Blender-only** options in `apps.blender.advanced`.

### Reconciliation
| Blender concept | Stored in (single source) | Notes |
|-----------------|---------------------------|-------|
| Orbit method (Trackball/Turntable) | generic `orbit_style` (`free`/`turntable`) | relabelled "Orbit method" in the Blender UI |
| Orbit around | generic `orbit_pivot` | **extended** with a Blender-only value `viewpoint` (see below) |
| Zoom to | generic — *not used by Blender*; Blender uses `advanced.zoom_to_mouse` | Blender's native pref is literally a "Zoom to Mouse" checkbox |

`orbit_pivot` value → Blender pivot:

| generic `orbit_pivot` | Blender "Orbit around" | pivot point |
|-----------------------|------------------------|-------------|
| `viewpoint` *(new, Blender-only)* | Viewpoint (default) | the **eye** — turns the camera in place (look around), independent of the orbit-point distance. (v0.1.4; was `view_location`, which sits far in front after fly/look and felt like orbiting an arbitrary point.) |
| `view` | Auto Depth | **raycast** the surface under the screen centre (per-gesture hold) |
| `object` | Selection | median of `selected_objects` world origins |
| `cursor` | 3D Cursor | `scene.cursor.location` |
| `origin` | World origin | `(0,0,0)` |

> Decision: rather than duplicate an `orbit_around` key in `advanced` (which would create a second
> source of truth), we add the single extra value `"viewpoint"` to the generic `orbit_pivot` enum.
> It is exposed **only** in the Blender bindings section; the General/other-app combos keep their original
> four values, and the broker only ever streams the *focused* app's scheme, so Fusion/SolidWorks
> never see `viewpoint`. Any unknown/failed pivot falls back to `viewpoint` (orbit about
> `view_location`).

### `apps.blender.advanced` (Blender-only; additive, deep-merged — no CONFIG_VERSION bump)
```jsonc
"advanced": {
  "nav_mode": "orbit",            // orbit | fly | walk
  "lock_horizon": false,          // keep the horizon level even in trackball (NDOF "Lock Horizon")
  "twist_action": "roll",         // roll | zoom | dolly | none  (un-shifted twist in ORBIT mode)
  "zoom_style": "zoom",           // zoom (view_distance) | dolly (translate the eye)
  "zoom_to_mouse": false,         // zoom toward the screen-centre surface (see §6 limitation)
  "lock_camera_to_view": false,   // in CAMERA view, drive scene.camera from the trackball
  "pan_scales_with_distance": true,
  "fly_speed": 1.0,
  "walk_speed": 1.0,
  "invert": {                     // per-mode, per-axis direction flips (applied IN THE ADD-ON)
    "orbit":     {"pitch": false, "yaw": false, "twist": false, "pan_x": false, "pan_y": false, "zoom": false},
    "viewpoint": {"pitch": false, "yaw": false, "roll": true},   // shares orbit's pan/zoom inverts
    "fly":       {"pitch": false, "yaw": false, "bank": true, "forward": false, "strafe": false, "vertical": false},
    "walk":      {"pitch": false, "yaw": false, "forward": false, "strafe": false, "vertical": false}
  }
}
```

`invert` is per-mode because the same physical channel means different things per nav mode (ball
forward/back is orbit pan-Y but fly/walk *forward*), so a single invert set can't flip one without the
other. The add-on applies these to o/p/z per mode just before the dispatch, so e.g. flipping
`walk.forward` doesn't touch orbit. Defaults bake in the "inside-out" roll fix (`viewpoint.roll` and
`fly.bank` start inverted vs external-pivot orbit). This **replaced** the old single
`invert_viewpoint_roll` flag.
Dropped from the brief's starting shape because they are reconciled into the generic scheme:
`orbit_method` (→ `orbit_style`) and `orbit_around` (→ `orbit_pivot` + `viewpoint`).

### Plumbing
The add-on only learns settings via the broker. The broker frame is **additively** extended with an
optional `"adv"` object carrying the Blender `advanced` dict:

```jsonc
{"o":[..],"p":[..],"z":..,"op":..,"os":..,"zm":..,"adv":{...}}   // "adv" present only for Blender
```

* `NavBroker.set_scheme(orbit_pivot, orbit_style, zoom_mode, advanced=None)` stores `advanced`;
  `_sender` includes `"adv"` **only when non-None**, so Fusion's frames are byte-for-byte unchanged
  (it reads only `o/p/z/op/os/zm`).
* `App._apply_schemes` attaches `advanced=apps.blender.advanced` (a live reference) to the broker
  scheme on **every** push, regardless of the focused app. Other add-ins ignore `"adv"`, and the
  Blender add-on only acts on frames while Blender is focused (when `op/os/zm` are Blender's too), so
  always attaching it makes `nav_mode` delivery robust against focus/active-app timing — gating it on
  "Blender focused" could miss a fly/walk switch and leave the add-on stuck in orbit. `_build_frame`
  still omits `"adv"` entirely when it is `None` (no Blender app configured).

Settings reach the add-on on the next motion frame (settings only matter while moving), matching the
existing per-frame additive design.

### In-Blender mode toggle (v0.1.7+)
Because the trackball cannot drive Blender's *native* Walk/Fly modal (that modal reads the
mouse/keyboard, not our view edits — the whole reason we DIY), the add-on registers its own operator
`trackball_nav.cycle_mode` (default key **Alt+`** in the 3D View, also under View menu) that cycles the
trackball between orbit → fly → walk. It sets a **local override** (`_mode_override`) that wins over the
daemon's `nav_mode`; the override is cleared automatically when the daemon's Mode dropdown changes, so
the dropdown re-takes control. Rebind via Preferences ▸ Keymap (search "Trackball").

---

## 4. Math (verified API; quaternion order to fine-tune live)

**Unified rotate-about-pivot** (`_apply_world_rotation`): build the per-frame world-space rotation
`R` (a `Quaternion`), then

```
view_rotation = (R @ view_rotation).normalized()
if pivot is not None:                       # pivot == None  ⇒ orbit about view_location (viewpoint)
    view_location = pivot + R @ (view_location - pivot)
```

This keeps the pivot fixed on screen for *every* non-viewpoint pivot, because the eye derives from
`view_location + back*distance` and `back = R@back` after the rotation (same algebra as Fusion's
"rotate then translate to hold the pivot").

* **Free/trackball** `R = Q(world_fwd, roll) @ Q(world_up, yaw) @ Q(world_right, pitch)`
* **Turntable / lock_horizon** `R = Q((0,0,1), yaw) @ Q(world_right, pitch)` (roll dropped → horizon
  stays level)
* **Roll** is just the `Q(world_fwd, roll)` term (pivot = view_location)
* **Pan** `view_location += world_right*dx + world_up*dy`, `dx,dy ∝ view_distance` if
  `pan_scales_with_distance`
* **Zoom** `view_distance = clamp(view_distance * (1 - sign·z·scale))`; `zoom_to_mouse` also shifts
  `view_location = P + (view_location-P)*factor`
* **Dolly** `view_location += world_fwd * (scale·z·view_distance)`
* **Selection median** mean of `selected_objects[i].matrix_world.translation`
* **Auto-depth** `region_2d_to_origin_3d` + `region_2d_to_vector_3d` at the region centre →
  `scene.ray_cast(evaluated_depsgraph_get(), origin, dir)` → `location` (held per gesture; recast on
  pan/zoom or after `PIVOT_HOLD_IDLE`)

Baseline signs/scales (`ORBIT_SCALE`, `PAN_SCALE`, …) are tuned live; the daemon's per-app Invert
checkboxes flip further, exactly like Fusion. **Orbit is calibrated to 1:1**: the add-on rotates the
view by exactly the broker delta `o` (measured gain 1.000), and the dual-sensor device reports ~2× the
physical angle, so `ORBIT_SCALE = 0.5` makes the daemon's orbit Sensitivity 1.0 a true 1:1 ball→view
orbit (v0.1.3). Pan/zoom keep their feel-based scales.

### Roll sign convention (now per-mode, v0.1.9)
The camera's *world-space* roll for a given twist is the same sign for every pivot, but rolling
**inside-out** (first-person: fly/walk, and the `viewpoint` pivot — you roll about the eye / the
point you're looking at) *feels* opposite to rolling **outside-in** (orbiting an external pivot:
object / auto-depth / cursor / origin). The defaults bake this in: `invert.viewpoint.roll` and
`invert.fly.bank` start **on**, so first-person roll matches external-pivot orbit out of the box. Both
(and every other axis) are independently toggleable in the merged Blender bindings UI. (`walk` has no
bank by design — horizon-locked.)

### Per-gesture pivot hold (auto-depth)
Mirrors Fusion's `_gesture`/`_screen_center_pivot`: the screen-centre surface point is raycast
**once** at the start of an orbit gesture and **held**, so the thing under the crosshair stays put.
It is invalidated when the view translates (pan/zoom/dolly set `pivot_invalid`) or after
`PIVOT_HOLD_IDLE` seconds with no frames (gesture ended).

---

## 5. Threading & lifecycle (bpy is main-thread-only)

Same shape as Fusion's socket-thread + CustomEvent, but Blender's main-thread hop is a timer:

* **reader thread** (`threading.Thread`, daemon): connect to `127.0.0.1:<bridge port>`, send the
  hello `{"type":"hello","app":"blender","version":ADDIN_VERSION,"host":<blender ver>,"pid":..}`,
  read newline JSON frames, push each onto a `queue.Queue`. Reconnects on drop (like Fusion).
* **main thread** (`bpy.app.timers.register(_on_timer, persistent=True)`): drain the queue, resolve
  the target VIEW_3D, apply each frame to its `region_3d`, then `area.tag_redraw()`. All bpy access
  happens here.
* `register()` starts both; `unregister()` stops the thread and removes the timer.

**Target VIEW_3D** (`_resolve_target`): iterate `window_manager.windows[].screen.areas[]` for
`type=='VIEW_3D'`, take the `WINDOW` region + `space.region_3d`. Prefer the largest (and remember the
last one used for stability). No-ops cleanly when none is open. (See §6 for "under the pointer".)

---

## 6. Known limitations / deferred (honest scope)

* **Discrete view ops (catalog K)** — Frame Selected, ortho axis snaps, 15° steps, recenter on
  cursor — are *button/gesture* actions, but the daemon currently has **no button-event channel** to
  the add-on (buttons are handled on-device in HID mode; the broker streams only continuous o/p/z).
  Wiring these needs a new broker message type; the add-on leaves clean hooks but does **not** fire
  them yet. Deferred, documented here.
* **"Under the pointer" targeting & true "zoom to mouse"** need the live mouse position, which is not
  available from a `bpy.app.timers` callback (only inside modal/event handlers). v1 targets the
  active/largest VIEW_3D and zooms toward the **screen-centre** surface. A future refinement can map
  the OS cursor (`GetCursorPos`) through `window.x/y` + area geometry to recover region coords.
* **Walk gravity/teleport** — not implemented (no physics step in the timer); walk does horizon-locked
  look + horizontal movement only.
* **Fly/walk movement uses Shift** (the daemon's orbit↔pan/zoom modifier): un-shifted ball = look,
  Shift+ball = strafe, Shift+twist = thrust. The add-on applies strafe/thrust correctly once the
  deltas arrive (verified headless), so if Shift+ball "does nothing" the cause is upstream — Shift not
  being detected as held during fly/walk (or the per-app `toggle` not set to "shift"). Note this means
  pushing the ball forward under Shift strafes *vertically*; forward motion is Shift+twist (thrust).
* **Camera-lock (J)** drives `scene.camera.matrix_world` from the view and sets `space.lock_camera`;
  verify the feel live (Blender ignores `rv3d` rotation while *in* camera view, so we write the
  camera directly).

---

## 7. Install / versioning (verified paths, Blender 5.1.1)

* User scripts dir (per version): `%APPDATA%\Blender Foundation\Blender\<maj.min>\scripts\`
  → add-on goes in `…\scripts\addons\trackball_nav\`, the auto-enable shim in `…\scripts\startup\`.
* `install_blender` copies to **every** detected version (existing `%APPDATA%\…\<ver>\scripts` dirs,
  plus the version parsed from each detected `blender.exe`). Creates the dir if Blender hasn't yet.
* **Auto-enable** (zero-click, the analogue of Fusion's "Run on Startup"): a tiny
  `scripts/startup/trackball_nav_startup.py` calls
  `addon_utils.enable("trackball_nav", default_set=True, persistent=True)` from a 0.1 s timer (out of
  the restricted startup context). The add-on also appears under Preferences → Add-ons for manual
  toggling. *Writing the startup shim is confirmed with the user first (it makes our code run on every
  Blender launch).*
* **Version source**: the add-on ships `version.json` (`{"version":"0.1.0"}`) kept in sync with
  `bl_info["version"]`; the daemon's `_ADDINS["blender"]` reads it exactly like Fusion's
  `TrackballNav.manifest`, so `auto_update` re-copies on a bump.

---

## Verified facts (this machine — `blender --background --python`)

```
version              5.1.1
user scripts         %APPDATA%\Blender Foundation\Blender\5.1\scripts
addons / startup     …\scripts\addons , …\scripts\startup
RegionView3D         view_location, view_rotation, view_distance, view_perspective{PERSP,ORTHO,CAMERA},
                     is_perspective, view_camera_zoom/offset, lock_rotation  (all present)
SpaceView3D          lock_camera, region_3d, camera  (all present)
scene.ray_cast       (depsgraph, origin, direction, distance) -> (result, location, normal, index, object, matrix)
view3d_utils         region_2d_to_origin_3d, region_2d_to_vector_3d, region_2d_to_location_3d, location_3d_to_region_2d
context              evaluated_depsgraph_get()  present
mathutils            q @ Vector and q1 @ q2 verified (Quaternion((0,0,1),0.5) @ (1,0,0) = (cos,sin,0))
Window               x, y, width, height, screen  (enables future cursor mapping)
addon_utils.enable   (module_name, *, default_set=False, persistent=False, refresh_handled=False, handle_error=None)
```
