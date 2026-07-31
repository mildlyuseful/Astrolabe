# Blender navigation — maintainer's guide

This guide owns Blender-specific setup, camera APIs, threading, and verification. Shared focus,
state, mapping, routing, and lifecycle contracts are defined in
[`../architecture.md`](../architecture.md). Read both before changing the add-on.

Do not copy a version snapshot into this guide. The current markers are `bl_info`, `ADDIN_VERSION`,
and `version.json`, and the daemon version is `trackball_daemon.__version__`.

---

## 0. First 15 minutes

```powershell
# Set this to the detected local Blender executable.
$BlenderExe = '<Blender-install>\blender.exe'
& $BlenderExe --background --factory-startup --python tools/blender_nav_math_test.py
& $BlenderExe --background --factory-startup --python tools/blender_nav_integration_probe.py
& $BlenderExe --background --factory-startup --python tools/blender_nav_socket_probe.py
python -m pytest tests -q
```
All four should pass. The integration/socket probes run the **real** add-on against a real
`RegionView3D` that Blender exposes even in `--background` (see Gotcha #1) — so you can verify almost
everything without a GUI or hardware.

To change the add-on and get it into a running Blender: edit
`trackball_daemon/plugins/blender/trackball_nav/`, **bump the version in three places** (see §7), then
restart the daemon for `auto_update` or use **Settings → 3D Apps → Blender → Update**. In Blender,
**F3 → "Reload Scripts"** picks up the new code. See §8/§11 for the restart rules.

---

## 1. What the Blender portion is

The daemon streams orbit/pan/zoom deltas from the BLE trackball over a localhost TCP socket (the
"nav broker"). A **Blender add-on** (`trackball_nav`) connects to that broker and drives the active 3D
viewport. Unlike the Fusion/SolidWorks integrations (which target a CAD eye+look-at camera), Blender
is a first-class, feature-rich target: trackball **orbit** (free/turntable, seven pivots), **pan/zoom/
dolly/roll**, **fly** and **walk** first-person modes, **camera-view** driving, per-mode/per-axis
direction inverts, with navigation mode supplied by the daemon runtime.

It is the analogue of the Fusion add-in
([`trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py`](../../trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py))
— the same socket-reader plus main-thread-marshal shape, but larger because Blender does much more.

---

## 2. File map

| Path | Role |
|---|---|
| `trackball_daemon/plugins/blender/trackball_nav/__init__.py` | **The add-on.** All nav logic, math, threading, and daemon-profile consumption. Runs *inside Blender's Python*. |
| `trackball_daemon/plugins/blender/trackball_nav/version.json` | Version string the daemon reads for `auto_update` (Blender add-ons have no JSON manifest; this is our parallel one). Keep in sync with `bl_info` + `ADDIN_VERSION`. |
| `trackball_daemon/plugins/blender/startup/trackball_nav_startup.py` | Auto-enable shim. Copied into Blender's `scripts/startup/`; enables the add-on on every launch (the analogue of Fusion's "Run on Startup"). |
| `trackball_daemon/navbroker.py` / `navigation_router.py` | Target-isolated localhost frames and Blender's additive `adv` profile. |
| `trackball_daemon/app.py` / `runtime_state.py` | Publish the focused runtime profile and target-tagged navigation envelopes. |
| `trackball_daemon/config_store.py` / `settings_schema.py` / `system_defaults.json` | Typed sparse settings, validation, and current concrete defaults. `default_profiles.json` is migration-only. |
| `trackball_daemon/integrations.py` | `install_blender` (multi-version copy + startup shim), the `_ADDINS["blender"]` registry entry, version readers, `auto_update`. |
| `trackball_daemon/app_registry.py` | Blender identity, process selectors, modes, and capabilities. |
| `trackball_daemon/ui.py` | The generated **Per-App** settings UI. |
| `tools/blender_nav_*.py` | Headless test scripts (math / integration / socket probes). Not part of the shipped package. |

The daemon process and the add-on are **two different Python interpreters** (daemon Python vs Blender's
bundled Python). They only talk over the broker socket. This split is the source of half the gotchas.

---

## 3. End-to-end data flow

```
BLE trackball
  → ble.py ingestion + immutable RuntimeSnapshot
  → output.py OutputEngine.handle_packet  (applies per-app mapping; the declarative binding layer
                                            selects primary orbit/look or secondary pan/move + zoom)
  → App._nav_sink creates a Blender-targeted NavigationEnvelope
  → NavigationRouter / NavBroker  (per-target accumulation and configured-rate flush)
        frame = {"o":[ox,oy,oz], "p":[px,py], "z":zoom, "op":..., "os":..., "zm":..., "adv":{...}}
  ──────────────────── localhost TCP ────────────────────
  → add-on reader thread  (background; reads newline JSON → queue.Queue)
  → bpy.app.timers callback _on_timer  (MAIN THREAD; drains the queue)
  → _apply(...)  (resolves the target VIEW_3D, applies the delta to its RegionView3D, tag_redraw)
```

Key contract: the daemon applies global physical orientation and the saved user layer, then sends
Blender's immutable host correction in `adv.host_baseline`. The add-on applies that correction after
mode-specific action routing. Its camera-math constants are neutral so the baseline is never
double-applied. See [`../default_profiles.md`](../default_profiles.md).

---

## 4. The add-on internals

### 4.1 Threading & lifecycle
- `bpy` is **main-thread only.** The socket reader runs on a `threading.Thread` and only does I/O +
  `queue.put`. Everything touching `bpy` happens in `_on_timer`, a `bpy.app.timers` callback
  registered `persistent=True` (survives file loads), polling ~90 Hz (`_TIMER_INTERVAL`). This is the
  Blender analogue of the Fusion add-in's `CustomEvent` hop.
- `register()` starts the reader thread + timer, registers the passive mouse tracker, and resets
  gesture state. `unregister()` tears all of that down. Both are wrapped so a reload race can't
  crash Blender.
- The hello handshake sends `{"app":"blender","version":ADDIN_VERSION,"host":<blender ver>,"pid":...}`
  so the daemon tray shows the *loaded* add-on version — your first check that Blender picked up a new
  build.

### 4.2 Target resolution (`_resolve_target`)
Picks the **largest** open `VIEW_3D` area (last-used wins ties), returns its `WINDOW` region +
`region_3d` + space. No-ops cleanly when no 3D viewport exists. It **cannot** target "the viewport
under the mouse," because a timer callback has no live mouse position (see Gotcha #4).

### 4.3 The apply pipeline (`_apply`) — order matters
1. Snapshot `before_rot/before_loc/before_persp` (for the diagnostic).
2. Parse `o/p/z/op/os/zm/adv`.
3. **Effective nav mode**: consume `advanced.nav_mode` from the daemon runtime profile. The add-on
   has no second/local mode authority.
4. Rate-limited **`rx ...` diagnostic** log (proves which channel is arriving — invaluable for "it
   does nothing" reports; see Solved Problem #2/#6).
5. **Camera-view exit**: if the viewport is showing the camera and lock-camera is off, switch to
   `PERSP` so edits are visible (Solved Problem #2).
6. **Per-mode action routing**: select and user-invert o/p/z per `advanced` (see §5.4).
7. **Host alignment**: apply `advanced.host_baseline` after the action is known.
8. **Dispatch** by nav mode → `_apply_fly` / `_apply_walk` / orbit (`_apply_orbit` / `_pan` /
   `_zoom`/`_dolly`).
9. **Camera-lock**: if in camera view *and* lock-camera is on, drive `scene.camera.matrix_world`.
10. **`applied ...` diagnostic** (did the view actually change? perspective/area/rotΔ/locΔ).
11. `area.tag_redraw()`.

### 4.4 The math helpers (pure; testable headless)
`_view_axes`, `_eye`, `_orbit_R`, `_apply_world_rotation`, `_pan`, `_zoom`, `_dolly`, `_roll`,
`_look`, `_horizontal`, `_selection_median_from` operate on a **duck-typed view object** (anything
with `.view_location`/`.view_rotation`/`.view_distance`). That's why `tools/blender_nav_math_test.py`
can test them with a tiny stand-in class — no GUI `RegionView3D` needed. Keep them bpy-free.

The core trick is `_apply_world_rotation(rv, R, pivot)`: rotate `view_rotation` by world-space `R`,
then `view_location = pivot + R @ (view_location - pivot)` so the eye rotates rigidly about `pivot`
(eye = `view_location + back*distance` follows automatically). `pivot=None` ⇒ orbit about
`view_location`.

### 4.5 The under-mouse `cursor` pivot — the passive mouse tracker (⚠ live tracking needs the GUI)
The `cursor` pivot orbits about the surface **under the mouse** (a SpaceMouse's "rotation center =
cursor"). Two halves, like the other apps:
- **Half B (the raycast) — solid, headless-tested.** `region_2d_to_origin_3d` + `region_2d_to_vector_3d`
  + `scene.ray_cast` already unproject *any* region pixel; `_raycast_screen_center` delegates to
  `_raycast_pixel(rv, region, x, y)`. `scene.ray_cast` returns only real
  geometry hits (no sentinel), so no bbox gate is needed. The integration probe raycasts an arbitrary
  pixel (centre hits the cube, a corner misses) headlessly.
- **Half A (the live mouse) — the architectural friction.** Blender has no on-demand mouse getter
  (Gotcha #4), so a **passive, window-wide modal operator** `TRACKBALL_NAV_OT_mouse_tracker` caches
  the cursor's **window-space** position (`event.mouse_x/y`, bottom-left origin) + the window's stable
  `as_pointer()` on every `MOUSEMOVE`, and returns `{'PASS_THROUGH'}` so it **never consumes events**
  (normal select/orbit/draw are untouched). The timer maps the cache into the target region on demand:
  `_region_pixel_from_window` (pure: `mouse - region.x/y`, in-range check) → `_raycast_cursor`. Off the
  viewport / over empty space / not cached yet → orbit method unavailable; continue the global
  chain. To Cursor zoom instead synthesizes a point at the current view depth.
- **Lifecycle friction (real, documented, handled).** A modal operator (a) **can't be invoked from the
  restricted `register()` context** → deferred via a `0.2 s` timer; (b) is **cancelled on file load** →
  a `@persistent` `load_post` handler restarts it (and `_tracker["gen"]` supersedes any straggler so
  the cache never has two writers); (c) is **skipped in `--background`** (`bpy.app.background`) since
  there's no interactive event loop. It does **not fight the timer pump** — both run on Blender's main
  thread, never concurrently, sharing only the `_cursor` dict (modal writes, timer reads).
- **Verification boundary:** headless probes cover window→region mapping, arbitrary-pixel raycast,
  the full synthetic `_cursor` → `_raycast_cursor` path, and an end-to-end cursor orbit that changes
  `view_location`. Real modal `MOUSEMOVE` delivery and file-load lifecycle require a GUI session and
  remain tracked in [`TODO.md`](../../TODO.md). The rejected Win32 alternative needed unavailable
  Blender window-origin data; the modal operator keeps coordinates inside Blender's own model.

---

## 5. The control model

### 5.1 Modes (`advanced.nav_mode`: `orbit` | `fly` | `walk` | `object`)
- **orbit**: ball pitch/yaw rotate the view about the chosen pivot; twist = roll/zoom/dolly per
  `twist_action`. Shift → pan (ball plane) / zoom (twist).
- **fly**: ball = look (rotate about the **eye**, turn in place) + bank on twist; Shift → move
  (ball-forward = thrust, ball-sideways = strafe, twist = rise/fall).
- **walk**: like fly but horizon-locked look (no bank) and horizontal-plane movement.
- **object**: primary motion rotates selected root objects as one group around their shared origin
  center using the current view's right/up/forward axes. The secondary layer uses either **View**
  translation (viewport right/up plus twist depth) or **Ground** translation (horizontal
  right/forward plus twist on world Z). Empty selection is a no-op; selected descendants of another
  selected object are excluded so parenting cannot apply motion twice. Consecutive transform frames
  use Blender `UNDO_GROUPED` operators, with alternating operator slots separating gestures, so one
  gesture is one undo action and never moves the viewport.

Entering Turntable, Lock Horizon, or Walk from a free-roll state optionally calls `_level_horizon`
once. It removes roll while keeping the forward direction, location, distance, and active pivot.
`advanced.level_horizon_on_entry` controls the transition; the first frame establishes state and is
not treated as an entry. Ordinary fixed-mode frames do not repeatedly level the view.

### 5.2 Pivots (`scheme.orbit_pivot`, labelled "Orbit pivot" in the UI)
`camera` → the **eye** (turn in place — see Solved Problem #5); `screen_center` → screen-center
raycast; `cursor` → under-mouse raycast via the modal tracker (§4.5); `selection` → current selection
median; `object` → aggregate scene-bounds center; `cursor_3d` → 3D cursor; `origin` → world origin.
Unknown or unavailable methods are skipped by the configured candidate chain. Screen Center and
Under Cursor share the orbit-gesture slot because only one orbit pivot is active. To Cursor zoom has
a separate held target and timeout. `selection_overrides_pivot` applies to external pivots; Camera
remains true turn-in-place.

### 5.3 Immutable host baseline

Blender's intrinsic signs/scales live in `host_profiles.json`, not editable add-on constants. The
add-on camera math is neutral and `adv.host_baseline` is the only source of suite alignment. Do not
copy the numeric profile into this document; inspect the packaged JSON or
[`../default_profiles.md`](../default_profiles.md). Runtime constants that remain local include:

| Const | Meaning |
|---|---|
| `PIVOT_HOLD_IDLE` | compatibility fallback when a frame lacks daemon-supplied orbit/zoom holds. |
| `_TIMER_INTERVAL=1/90` | main-thread poll rate. |
| `_DEFAULT_PORT=47900` | broker port fallback if `bridge.json` is missing. |

### 5.4 Per-mode action routing (`advanced.axis_source` + `advanced.invert`)
Each action under `{orbit,camera,fly,walk,object}` selects source X/Y/Z and has its own invert.
Object owns Pitch/Yaw/Roll and Translate X/Y/Z routes; changing one does not affect Orbit or another
navigation mode. Routing is
applied **in the add-on**, in `_apply_action_routing`, just before dispatch because the daemon's
effective navigation mode determines which action each channel means. Rotation actions select from
`o`; secondary-layer movement actions select from `(p.x,p.y,z)`. Thus Walk Forward can select Z
(twist) without changing Orbit.
Defaults reproduce the physical wiring. The immutable host baseline bakes in the "inside-out" fix
for `camera.roll` and `fly.bank`, and object rotation receives the perceptual inverse of camera
rotation. Saved user defaults remain neutral/off, and the wire value composes baseline with user
preference.
`camera` shares orbit's pan/zoom routes. This **replaced** the old single `invert_camera_roll`
flag — that key is now ignored if present in an old config.

> The *generic* per-app invert (`bindings.invert` orbit/pan/zoom, applied in `output.py`) is **hidden
> for Blender in the UI** and left at default-off so it's a no-op; Blender's inverts are entirely the
> per-mode ones. Don't wire both or you'll double-invert.

### 5.5 Navigation-mode authority
Blender's *native* Walk/Fly cannot be driven by the trackball (Gotcha #5).
Orbit/Fly/Walk/Object selection
therefore belongs to the daemon runtime and may be changed by daemon keybindings or settings. The
add-on has no local mode override, so a frame cannot disagree with the control state shown by the
daemon.

---

## 6. Configuration and runtime profile

Config v9 stores sparse stable-ID Global and Blender overrides. Missing app overrides inherit
Global/System; `ConfigStore` validates complete candidates and publishes immutable snapshots.
`settings_schema.py` owns setting IDs, types, validation, scope, and presentation;
`system_defaults.json` owns current concrete defaults. `default_profiles.json` and materialized app
profiles exist only at the legacy migration boundary.

Blender-specific rich settings include daemon-authoritative navigation mode, horizon behavior,
Twist/Zoom actions, camera-view handling, fly/walk speeds, and per-mode axis source/inversion. Shared
settings include refresh rate, Orbit pivot/style, zoom target/style, independent gesture holds, and
selection override. Capability exposure is owned by `app_registry.py` and `settings_schema.py`.

`App._apply_schemes` and `NavigationRouter` publish Blender's detached target profile, host alignment,
and current runtime overlays. `NavBroker._build_frame` serializes that profile as `adv`; the add-on
reads `frame["adv"]`. Runtime mode and setting changes publish immediately, including while the ball
is stationary, and affect the next motion frame.

---

## 7. Install, versioning, update

- **Install** (`integrations.install_blender`): copies `trackball_nav/` into
  `%APPDATA%\Blender Foundation\Blender\<maj.min>\scripts\addons\` for **every** detected version
  (existing `%APPDATA%` version dirs ∪ versions parsed from each detected `blender.exe` folder name),
  and (with the user's consent) drops the startup shim into
  `scripts/startup/`. `install_startup`: `True`=write shim, `False`=don't, `None`=refresh only where it
  already exists (used by `auto_update`).
- **Auto-enable**: the startup shim calls `addon_utils.enable("trackball_nav", default_set=True,
  persistent=True)` from a 0.1 s timer (out of the restricted startup context). The add-on also shows
  under Preferences → Add-ons → "Trackball" for manual toggling.
- **Versioning**: the add-on version lives in **three places that must stay in sync** —
  `bl_info["version"]`, `ADDIN_VERSION`, and `version.json`. `_ADDINS["blender"]` reads `version.json`
  exactly like Fusion's `.manifest`. On a bump, the daemon's `auto_update` re-copies the add-on on its
  next launch (and refreshes the startup shim where present). The dev loop is: bump all three →
  restart the daemon or use **Settings → 3D Apps → Blender → Update** → F3 in Blender.

---

## 8. GOTCHAS (the non-obvious stuff)

1. **`--background` Blender exposes a real `RegionView3D`.** A full-size `VIEW_3D` area exists even
   headless, so the integration/socket probes drive the *real* apply pipeline (orbit/pan/zoom/pivots/
   camera-lock) without a GUI. Only visual redraw + live feel need a GUI. This is the single biggest
   testing lever — lean on it. (The timer does **not** auto-tick in `--background`; the probes call
   `_on_timer()` manually.)
2. **Two interpreters → two reload rules.** The add-on (`trackball_nav/`) runs in Blender → **F3
   "Reload Scripts"** (or restart Blender). Everything else (`ui.py`, `config.py`, `app.py`,
   `navbroker.py`, `integrations.py`) runs in the **daemon** → **restart the daemon**. A change to both
   needs both. This is the #1 cause of "I changed it and nothing happened."
3. **Camera view ignores `rv` edits.** When `view_perspective=='CAMERA'`, Blender renders through the
   `scene.camera` object and ignores `view_rotation/location/distance`. Navigating *looks dead*. The
   add-on detects this and either exits to perspective (lock off) or drives the camera (lock on).
4. **No on-demand mouse position — verified.** `bpy.app.timers` callbacks (and any code outside a
   modal operator / event handler) have **no live mouse position**: the supported API exposes no
   `mouse`/`cursor`/`pointer` property on `Window`/`Screen`/`Area`/`Region`/`RegionView3D`/`Context`;
   `Event.mouse_*` exists **only inside a modal operator**; `Window` has cursor *setters* only
   (`cursor_warp`/`cursor_set`). So the **under-mouse `cursor` pivot** needs the passive modal mouse
   tracker (§4.5). *Viewport-under-the-cursor targeting* (`_resolve_target` picking the hovered area)
   is still not done; that is separate from the implemented
   Under Cursor orbit and To Cursor zoom paths.
5. **You cannot drive Blender's *native* Walk/Fly.** `view3d.walk`/`view3d.fly` are modal operators
   that read the mouse/keyboard directly and ignore our `RegionView3D` edits — the whole reason the
   add-on DIYs fly/walk. Tell users to use the daemon's Mode or daemon keybindings, **not** Blender's
   Shift+\` Walk/Fly.
6. **Per-mode inverts belong at the action boundary.** The daemon runtime supplies the effective
   mode, but Blender must select the mode-specific action before applying its source/invert and host
   baseline. Do not fold those corrections into generic `output.py` mapping.
7. **`adv` is target-specific.** `NavigationRouter` publishes Blender's profile only for the Blender
   target and discards pending motion on target or runtime-revision changes. Never restore an
   always-Blender payload or selected-host fallback.
8. **Do not reintroduce a host-local mode override.** The daemon runtime owns the active nav mode;
   a Blender-only operator would make the add-on disagree with held-key state and the control panel.
9. **mathutils conventions.** `q @ v` rotates a vector; `q1 @ q2` composes. **Left-multiply = world
   axis, right-multiply = view-local.** Camera looks down view-local **−Z**;
   `eye = view_location + (view_rotation @ (0,0,1)) * view_distance`.
10. **`scene.ray_cast` is depsgraph-first**: `ray_cast(evaluated_depsgraph_get(), origin, dir)`.
    Screen-centre ray via `bpy_extras.view3d_utils.region_2d_to_origin_3d` + `region_2d_to_vector_3d`.
11. **Legacy `bl_info` add-on still works in Blender 4.2+/5.x** alongside the new extensions system —
    we install into `scripts/addons/`, not as an extension. Don't "upgrade" it to an extension without
    re-checking the install/auto-enable path.
12. **The broker is target-isolated.** Blender receives only frames whose target matches the
    client's `hello.app`. Do not add plugin-side cross-app filtering as a substitute for server-side
    routing.
13. **F3 reload re-imports the module** → fresh globals (`_q`, `_stop`, gesture state, …). Blender
    calls the old `unregister()` then the new `register()`. State does not survive a reload by design.

---

## 9. Load-bearing warnings from live debugging

- **Camera view:** unlocked Camera view ignores `RegionView3D` edits. Navigation must leave Camera
  view or drive `scene.camera.matrix_world` when camera lock is enabled.
- **Mode authority:** the daemon runtime is the only Orbit/Fly/Walk authority. Do not restore the
  removed Blender-local mode shortcut.
- **Baseline ownership:** suite alignment and inside/out corrections belong in `host_profiles.json`
  and are applied once after per-mode action routing. Do not compensate in both generic mapping and
  add-on math.
- **Camera pivot:** `camera` rotates about the eye. `view_location` is an external view target and is
  not a turn-in-place pivot.
- **Fly/Walk movement:** ball-forward is view-forward thrust, ball-sideways is strafe, and twist is
  rise/fall. Keep a minimum movement scale near the model.

The dated symptom/root-cause ledger is available in git history; current diagnostics below are the
supported way to distinguish routing, channel, and viewport failures.

---

## 10. Testing

- **Headless (fast, no hardware):** the three `tools/blender_nav_*.py` scripts (see §0). The math test
  covers pure quaternion/vector math; the integration probe drives the real apply pipeline incl.
  pivots, camera-lock, camera-exit, per-mode invert, and daemon-authoritative mode changes; the socket probe runs the
  *real* `NavBroker` + the add-on in one process and proves frames flow over TCP into the view. They
  exit non-zero on failure (CI-friendly).
- **Daemon side:** `python -m pytest tests -q` (config block, broker `adv` passthrough,
  `_apply_schemes` wiring, regressions). `tests/test_blender_nav_wiring.py` is the Blender-specific one.
- **Live boundary:** visible modal pointer delivery, viewport feel, and physical sign/scale require a
  GUI session and real input. Use `blender_addin.log` (see §12) to distinguish routing from viewport
  application; current qualification work belongs in [`TODO.md`](../../TODO.md).

---

## 11. How to make common changes

- **Add a Blender option:** add its concrete value to `system_defaults.json`; define its stable ID,
  type, validation, scope, and presentation in `settings_schema.py`; expose Blender capability through
  `app_registry.py`; and consume it from `adv` in the add-on. Add a migration only when persisted
  meaning or shape changes. Never add current defaults to frozen `default_profiles.json`. Bump the
  add-on's three markers if its code changed; restart the daemon and reload scripts.
- **Add an invertible axis:** add the key to the shipped advanced profile and action schema, apply it
  in `_apply_action_routing`, and expose it through the declarative binding profile.
- **Change suite alignment:** edit Blender's packaged `host_profiles.json` entry (§5.3), update
  the baseline tests/docs, and bump the daemon/add-on contract versions.
- **Ship a new add-on build:** bump `bl_info["version"]` + `ADDIN_VERSION` + `version.json`; the
  daemon's `auto_update` re-copies on next launch (compares `version.json`).

---

## 12. Diagnostics & known limitations

- **Log:** the add-on writes `%APPDATA%\Mildly Useful\Astrolabe\blender_addin.log` (rate-limited). Key lines:
  `scheme: nav=…` (what mode/options the add-on received), `rx orbit/pan/zoom …` (which channel is
  arriving — distinguishes daemon/Shift issues from add-on issues), `applied persp=…->… rotD=… locD=…`
  (did the view actually change, and where). The daemon log shows `scheme -> blender: … blender_nav=…`.
- **Known limitations:**
  - **Viewport-under-the-cursor selection** in multi-viewport layouts. Under Cursor orbit and To
    Cursor zoom are implemented, but `_resolve_target` still chooses the active/largest `VIEW_3D`.
  - **Walk gravity/teleport** — not implemented (no physics step); walk is horizon-locked look +
    horizontal move only.
- Live qualification and unresolved viewport-parity work are tracked only in
  [`TODO.md`](../../TODO.md).
