# Blender navigation — maintainer's handoff guide

This is the Blender-side architecture, design-rationale, API-gotcha, and verification guide. Read
it before changing the add-on.

Do not copy a version snapshot into this guide. The current markers are `bl_info`, `ADDIN_VERSION`,
and `version.json`, and the daemon version is `trackball_daemon.__version__`.

---

## 0. First 15 minutes

```sh
# Run the headless tests (no daemon, no trackball, no GUI needed):
BL="/c/Program Files/Blender Foundation/Blender 5.1/blender.exe"
"$BL" --background --factory-startup --python tools/blender_nav_math_test.py          # pure math
"$BL" --background --factory-startup --python tools/blender_nav_integration_probe.py  # full apply on a real RegionView3D
"$BL" --background --factory-startup --python tools/blender_nav_socket_probe.py        # broker->socket->view, end to end
python -m pytest tests -q                                                              # daemon wiring + regressions
```
All four should pass. The integration/socket probes run the **real** add-on against a real
`RegionView3D` that Blender exposes even in `--background` (see Gotcha #1) — so you can verify almost
everything without a GUI or hardware.

To change the add-on and get it into a running Blender: edit
`trackball_daemon/plugins/blender/trackball_nav/`, **bump the version in three places** (see §7), then
either let the daemon's `auto_update` re-copy it on next launch, or run `integrations.install_blender`.
In Blender, **F3 → "Reload Scripts"** picks up the new code. See §8/§11 for the restart rules — they
trip everyone up.

---

## 1. What the Blender portion is

The daemon streams orbit/pan/zoom deltas from the BLE trackball over a localhost TCP socket (the
"nav broker"). A **Blender add-on** (`trackball_nav`) connects to that broker and drives the active 3D
viewport. Unlike the Fusion/SolidWorks integrations (which target a CAD eye+look-at camera), Blender
is a first-class, feature-rich target: trackball **orbit** (free/turntable, seven pivots), **pan/zoom/
dolly/roll**, **fly** and **walk** first-person modes, **camera-view** driving, per-mode/per-axis
direction inverts, and an in-viewport hotkey to switch modes.

It is the analogue of the Fusion add-in (`plugins/fusion360/TrackballNav/TrackballNav.py`) — same
socket-reader + main-thread-marshal shape — but much larger because Blender does much more.

---

## 2. File map

| Path | Role |
|---|---|
| `trackball_daemon/plugins/blender/trackball_nav/__init__.py` | **The add-on.** All nav logic, math, threading, and daemon-profile consumption. Runs *inside Blender's Python*. |
| `trackball_daemon/plugins/blender/trackball_nav/version.json` | Version string the daemon reads for `auto_update` (Blender add-ons have no JSON manifest; this is our parallel one). Keep in sync with `bl_info` + `ADDIN_VERSION`. |
| `trackball_daemon/plugins/blender/startup/trackball_nav_startup.py` | Auto-enable shim. Copied into Blender's `scripts/startup/`; enables the add-on on every launch (the analogue of Fusion's "Run on Startup"). |
| `trackball_daemon/navbroker.py` | `NavBroker`: localhost TCP, newline-JSON frames. Carries the optional additive `"adv"` object (Blender's extra settings). |
| `trackball_daemon/app.py` | `App._apply_schemes` pushes the control scheme + Blender's `advanced` block to the broker; `_nav_sink` routes the focused app's frames. |
| `trackball_daemon/config.py` + `default_profiles.json` | Validation/migrations plus shipped General and neutral Blender user defaults. |
| `trackball_daemon/integrations.py` | `install_blender` (multi-version copy + startup shim), the `_ADDINS["blender"]` registry entry, version readers, `auto_update`. |
| `trackball_daemon/app_registry.py` and `settings_schema.py` | App capabilities and the ordered stable setting contract. Blender enables its supported shared and rich-action fields here. |
| `trackball_daemon/ui.py` | The Tkinter UI. One declarative renderer builds every host section under *Per-App Bindings*; the tab is scrollable. |
| `tools/blender_nav_*.py` | Headless test scripts (math / integration / socket probes). Not part of the shipped package. |

The daemon process and the add-on are **two different Python interpreters** (daemon Python vs Blender's
bundled Python). They only talk over the broker socket. This split is the source of half the gotchas.

---

## 3. End-to-end data flow

```
BLE trackball
  → ble.py (unchanged ingestion)
  → output.py OutputEngine.handle_packet  (applies per-app axis source/sign/sensitivity + generic invert,
                                            gates orbit-vs-(pan/zoom) on Shift) → emits o/p/z deltas
  → App._nav_sink  (only when a supported, ENABLED, focused app is up; routes Blender → the broker)
  → NavBroker  (accumulates deltas; a sender thread flushes one coalesced frame at the configured Hz)
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
- **Verified headless** (`tools/blender_nav_math_test.py` + `_integration_probe.py`): the pure
  window→region mapping (in-range, margin, out-of-range → None), arbitrary-pixel raycast, the full
  `_cursor` → `_raycast_cursor` path with a synthetic cached position (centre hits the cube, off-region
  → None), and an end-to-end `op="cursor"` orbit that moves `view_location`. **Un-verified (needs the
  GUI):** that the modal operator actually receives `MOUSEMOVE` and tracks the live cursor while
  orbiting, and its lifecycle across real file loads. To verify live: set Orbit pivot = *Under Cursor
  (mouse)*, hover different faces while orbiting, and watch `blender_addin.log` for the
  `under-cursor hit @px(...)` line. Alternative Half A (not taken): Win32 `GetCursorPos` mapped via the
  Blender window's screen rect — but Blender doesn't expose the window's screen origin, so it'd need
  Win32 window-geometry too; the modal operator keeps everything in Blender's own coordinate space.

---

## 5. The control model

### 5.1 Modes (`advanced.nav_mode`: `orbit` | `fly` | `walk`)
- **orbit**: ball pitch/yaw rotate the view about the chosen pivot; twist = roll/zoom/dolly per
  `twist_action`. Shift → pan (ball plane) / zoom (twist).
- **fly**: ball = look (rotate about the **eye**, turn in place) + bank on twist; Shift → move
  (ball-forward = thrust, ball-sideways = strafe, twist = rise/fall).
- **walk**: like fly but horizon-locked look (no bank) and horizontal-plane movement.

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
Each action under `{orbit,camera,fly,walk}` selects source X/Y/Z and has its own invert. Routing is
applied **in the add-on**, in `_apply_action_routing`, just before dispatch because the active mode
can also be changed by Blender's local shortcut. Rotation actions select from `o`; shifted movement
actions select from `(p.x,p.y,z)`. Thus Walk Forward can select Z (twist) without changing Orbit.
Defaults reproduce the old fixed wiring. The immutable host baseline bakes in the "inside-out" fix
for `camera.roll` and `fly.bank`; the saved user defaults are neutral/off, and the wire value is
baseline XOR user preference.
`camera` shares orbit's pan/zoom routes. This **replaced** the old single `invert_camera_roll`
flag — that key is now ignored if present in an old config.

> The *generic* per-app invert (`bindings.invert` orbit/pan/zoom, applied in `output.py`) is **hidden
> for Blender in the UI** and left at default-off so it's a no-op; Blender's inverts are entirely the
> per-mode ones. Don't wire both or you'll double-invert.

### 5.5 Navigation-mode authority
Blender's *native* Walk/Fly cannot be driven by the trackball (Gotcha #5). Orbit/Fly/Walk selection
therefore belongs to the daemon runtime and may be changed by daemon keybindings or settings. Add-on
0.1.23 removed the old Alt+\` operator and local override so a frame cannot disagree with the control
state shown by the daemon.

---

## 6. Configuration reference (`apps.blender` in the JSON config)
- `bindings.*` — generic per-app: `orbit.sensitivity`, `pan.gain`, `zoom.gain`, `zoom.dominance`,
  `toggle` ("shift"/"none"), and `scheme` (`orbit_pivot`/`orbit_style`/`zoom_mode`). Blender defaults
  `scheme.orbit_pivot` to `"camera"`. `bindings.invert` exists but is unused for Blender (see §5.4).
- `advanced.*` — Blender-only: `nav_mode`, `lock_horizon`, `twist_action`, `zoom_style`,
  shared `bindings.scheme.zoom_mode`, `lock_camera_to_view`, `pan_scales_with_distance`, `fly_speed`, `walk_speed`, and
  the per-mode `axis_source` and `invert` blocks.
- `rate_hz`, `orbit_pivot_hold_sec`, `zoom_cursor_hold_sec` — per-app rate, orbit-pivot hold,
  and the independent To Cursor zoom hold.

Everything is deep-merged with shipped defaults, so new additive keys appear on existing configs.
Historical semantic changes are handled by the versioned migrations in `config.py`; do not add a
migration for a merely additive field. Explicit cleanup removes known retired keys where necessary.

How `advanced` reaches the add-on: `App._apply_schemes` builds a detached payload for the selected
broker app, folds in host alignment and shared controls, and gives it to the broker.
`NavBroker._build_frame` serializes it as `"adv"`; the add-on reads `frame["adv"]`. Runtime mode and
setting changes are published immediately, including while the trackball is stationary, and take
effect on the next motion frame.

---

## 7. Install, versioning, update

- **Install** (`integrations.install_blender`): copies `trackball_nav/` into
  `%APPDATA%\Blender Foundation\Blender\<maj.min>\scripts\addons\` for **every** detected version
  (existing `%APPDATA%` version dirs ∪ versions parsed from each detected `blender.exe` folder name,
  e.g. "Blender 5.1" → `5.1`), and (with the user's consent) drops the startup shim into
  `scripts/startup/`. `install_startup`: `True`=write shim, `False`=don't, `None`=refresh only where it
  already exists (used by `auto_update`).
- **Auto-enable**: the startup shim calls `addon_utils.enable("trackball_nav", default_set=True,
  persistent=True)` from a 0.1 s timer (out of the restricted startup context). The add-on also shows
  under Preferences → Add-ons → "Trackball" for manual toggling.
- **Versioning**: the add-on version lives in **three places that must stay in sync** —
  `bl_info["version"]`, `ADDIN_VERSION`, and `version.json`. `_ADDINS["blender"]` reads `version.json`
  exactly like Fusion's `.manifest`. On a bump, the daemon's `auto_update` re-copies the add-on on its
  next launch (and refreshes the startup shim where present). The dev loop: bump all three → restart
  the daemon (or call `integrations.auto_update(cfg)` / `install_blender(...)` from a Python shell) →
  F3 in Blender.

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
   modal operator / event handler) have **no live mouse position**: as of Blender 5.1.1 there is *no*
   `mouse`/`cursor`/`pointer` property on `Window`/`Screen`/`Area`/`Region`/`RegionView3D`/`Context`;
   `Event.mouse_*` exists **only inside a modal operator**; `Window` has cursor *setters* only
   (`cursor_warp`/`cursor_set`). So the **under-mouse `cursor` pivot** needs the passive modal mouse
   tracker (§4.5). *Viewport-under-the-cursor targeting* (`_resolve_target` picking the hovered area)
   *Viewport-under-the-cursor targeting* is still not done; that is separate from the implemented
   Under Cursor orbit and To Cursor zoom paths.
5. **You cannot drive Blender's *native* Walk/Fly.** `view3d.walk`/`view3d.fly` are modal operators
   that read the mouse/keyboard directly and ignore our `RegionView3D` edits — the whole reason the
   add-on DIYs fly/walk. Tell users to use the daemon's Mode or daemon keybindings, **not** Blender's
   Shift+\` Walk/Fly.
6. **Per-mode inverts must be in the add-on, not the daemon.** The daemon doesn't know the nav mode;
   the same channel maps to different actions per mode. So Blender's direction flips are applied in
   `_apply` (and shipped over `adv`), not folded into `output.py`'s generic invert.
7. **`adv` is per selected broker app.** `_apply_schemes` attaches Blender's payload when Blender is
   the selected socket host and preserves that host across temporary focus loss so a settings edit
   still reaches it. Never reintroduce the historical always-Blender payload; it cannot coexist with
   the other rich integrations.
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
12. **The broker broadcasts to all connected add-ins.** If both Fusion and Blender add-ins are
    connected, both receive every frame; the daemon only *sends* when a socket app is focused. Usually
    only one CAD app is open, but keep it in mind.
13. **F3 reload re-imports the module** → fresh globals (`_q`, `_stop`, gesture state, …). Blender
    calls the old `unregister()` then the new `register()`. State does not survive a reload by design.

---

## 9. SOLVED PROBLEMS (history — symptom → root cause → fix)

These were all found via live trackball testing; the fixes are in the code but the *reasoning* isn't.

1. **Fly/Walk wouldn't engage — add-on stayed in orbit (`nav=orbit` in the log).**
   *Root cause:* early focus timing dropped the payload carrying `nav_mode`. The first repair always
   attached Blender settings, which became wrong after other rich integrations arrived. The current
   design retains the selected broker app across temporary focus loss and builds that app's own
   payload (Gotcha #7). Phase 8 removed the former host-local Alt+\` override so this path has one
   authoritative mode.
2. **"Nothing moves, Shift does nothing," yet the log showed frames arriving and `_apply` running.**
   *Root cause:* the viewport was in **Camera view**, where Blender ignores `rv` edits (Gotcha #3).
   *Fix:* navigating now exits camera view to perspective (unless lock-camera is on). The
   `applied persp=…->… rotD=… locD=…` diagnostic was added to diagnose exactly this.
3. **Orbit felt 2× too fast at Sensitivity 1.0.**
   *Root cause:* the add-on rotates the view by exactly the broker delta (measured gain 1.000), and the
   **dual-sensor device reports ~2× the physical angle.** *Fix:* the immutable Blender host baseline
   uses orbit factor `0.5`, so Sensitivity 1.0 is a true 1:1 ball→view orbit.
4. **Twist-roll felt inverted in fly/walk/camera vs object orbit.**
   *Root cause:* the world-space roll is the *same* sign everywhere, but rolling **inside-out**
   (first-person / about the eye) *feels* opposite to rolling **outside-in** (about an external pivot).
   *Fix:* default-invert `camera.roll` + `fly.bank`. Later generalised to the full per-mode invert
   set (Problem #8).
5. **"Camera" orbit swung around a seemingly arbitrary point instead of turning the camera.**
   *Root cause:* it orbited `view_location`, which sits far in front after any fly/look or at a large
   view distance. *Fix:* the `camera` pivot now rotates about the **eye** — "turn the camera in
   place." (`screen_center`/`object`/`cursor_3d`/`origin` still orbit external points.)
6. **Shift-to-move did nothing in fly/walk.**
   *Two root causes:* (a) the camera-view bug (#2); (b) the mapping/scale — pushing the ball forward
   *strafed vertically* and movement was scaled purely by `view_distance` (→ ~0 when zoomed in close).
   *Fix:* remap so ball-forward = thrust along view forward, ball-sideways = strafe, twist = rise/fall;
   and floor the move scale at `view_distance≥1` (`_move_scale`).
7. **Mode could disagree between Blender and the daemon.**
   *Root cause:* the add-on's former Alt+\` override created a second authority. *Fix:* add-on 0.1.23
   consumes only the daemon runtime mode; users bind Orbit/Fly/Walk in the daemon (§5.5).
8. **User wanted granular control over every direction (e.g. invert walk-forward without touching
   orbit) and the 3 camera axes, not just roll; and the two Blender tabs were confusing.**
   *Fix:* the per-mode/per-axis `invert` structure (§5.4), and **merged** the separate "Blender
   Advanced" tab into *Per-App Bindings → blender* (now scrollable). The old `invert_camera_roll`
   flag was replaced by `invert.camera.roll` + `invert.fly.bank`.

---

## 10. Testing

- **Headless (fast, no hardware):** the three `tools/blender_nav_*.py` scripts (see §0). The math test
  covers pure quaternion/vector math; the integration probe drives the real apply pipeline incl.
  pivots, camera-lock, camera-exit, per-mode invert, and daemon-authoritative mode changes; the socket probe runs the
  *real* `NavBroker` + the add-on in one process and proves frames flow over TCP into the view. They
  exit non-zero on failure (CI-friendly).
- **Daemon side:** `python -m pytest tests -q` (config block, broker `adv` passthrough,
  `_apply_schemes` wiring, regressions). `tests/test_blender_nav_wiring.py` is the Blender-specific one.
- **Live (only thing not coverable headless):** open Blender, run the daemon, focus a 3D viewport,
  switch to 3D mode, and use the trackball. Use `blender_addin.log` (see §12) and Blender's F3 reload
  to iterate. Host-baseline sign/feel calibration needs a real trackball.

---

## 11. How to make common changes

- **Add a Blender option:** add its shipped value to `default_profiles.json`, validate/normalize it
  in `config.py` where needed, read it from `adv` in the add-on, and expose it through Blender's
  capability profile in `app_registry.py` and its `SettingSpec` in `settings_schema.py`. A merely additive field needs no version migration.
  Bump the add-on's three markers if its code changed; restart daemon and reload scripts.
- **Add an invertible axis:** add the key to the shipped advanced profile and action schema, apply it
  in `_apply_action_routing`, and expose it through the declarative binding profile.
- **Change suite alignment:** edit Blender's packaged `host_profiles.json` entry (§5.3), update
  the baseline tests/docs, and bump the daemon/add-on contract versions.
- **Ship a new add-on build:** bump `bl_info["version"]` + `ADDIN_VERSION` + `version.json`; the
  daemon's `auto_update` re-copies on next launch (compares `version.json`).

---

## 12. Diagnostics & known limitations

- **Log:** the add-on writes `%APPDATA%\TrackballDaemon\blender_addin.log` (rate-limited). Key lines:
  `scheme: nav=…` (what mode/options the add-on received), `rx orbit/pan/zoom …` (which channel is
  arriving — distinguishes daemon/Shift issues from add-on issues), `applied persp=…->… rotD=… locD=…`
  (did the view actually change, and where). The daemon log shows `scheme -> blender: … blender_nav=…`.
- **Known limitations:**
  - **Viewport-under-the-cursor selection** in multi-viewport layouts. Under Cursor orbit and To
    Cursor zoom are implemented, but `_resolve_target` still chooses the active/largest `VIEW_3D`.
  - **Walk gravity/teleport** — not implemented (no physics step); walk is horizon-locked look +
    horizontal move only.
- **Verify-live items:** passive mouse tracking and file-load restart, camera-lock feel,
  fixed-horizon entry, independent holds, and sign/scale feel. Track the current matrix in
  `TODO.md`.
