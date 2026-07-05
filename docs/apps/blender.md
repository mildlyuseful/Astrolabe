# Blender navigation — maintainer's handoff guide

This is the **"what you can't see by reading the code"** document for the Blender side of the
Trackball Daemon: the architecture, the non-obvious gotchas, and the full history of problems we hit
and how/why we solved them. If you just want the design catalog (every nav mode, the
generic-scheme↔Blender reconciliation, the verified Blender-API facts), read its companion
[`blender_design.md`](blender_design.md). This file is the one to read **first** when taking over.

Current versions at handoff: **add-on `0.1.9`**, daemon `__version__` `0.1.19`, Blender on the dev
machine **5.1.1**.

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
is a first-class, feature-rich target: trackball **orbit** (free/turntable, 5 pivots), **pan/zoom/
dolly/roll**, **fly** and **walk** first-person modes, **camera-view** driving, per-mode/per-axis
direction inverts, and an in-viewport hotkey to switch modes.

It is the analogue of the Fusion add-in (`plugins/fusion360/TrackballNav/TrackballNav.py`) — same
socket-reader + main-thread-marshal shape — but much larger because Blender does much more.

---

## 2. File map

| Path | Role |
|---|---|
| `trackball_daemon/plugins/blender/trackball_nav/__init__.py` | **The add-on.** All nav logic, math, threading, the in-Blender mode-toggle operator + keymap. Runs *inside Blender's Python*. |
| `trackball_daemon/plugins/blender/trackball_nav/version.json` | Version string the daemon reads for `auto_update` (Blender add-ons have no JSON manifest; this is our parallel one). Keep in sync with `bl_info` + `ADDIN_VERSION`. |
| `trackball_daemon/plugins/blender/startup/trackball_nav_startup.py` | Auto-enable shim. Copied into Blender's `scripts/startup/`; enables the add-on on every launch (the analogue of Fusion's "Run on Startup"). |
| `trackball_daemon/navbroker.py` | `NavBroker`: localhost TCP, newline-JSON frames. Carries the optional additive `"adv"` object (Blender's extra settings). |
| `trackball_daemon/app.py` | `App._apply_schemes` pushes the control scheme + Blender's `advanced` block to the broker; `_nav_sink` routes the focused app's frames. |
| `trackball_daemon/config.py` | `_DEFAULT_BLENDER_ADVANCED`, `_DEFAULT_BLENDER_INVERT`, `_blender_app()`. The single source of truth. |
| `trackball_daemon/integrations.py` | `install_blender` (multi-version copy + startup shim), the `_ADDINS["blender"]` registry entry, version readers, `auto_update`. |
| `trackball_daemon/ui.py` | The Tkinter UI. The **merged** Blender section lives under *Per-App Bindings → blender* (`_blender_bindings_fields`), and the bindings tab is scrollable. |
| `tools/blender_nav_*.py` | Headless test scripts (math / integration / socket probes). Not part of the shipped package. |
| `docs/apps/blender_design.md` | Design rationale: full mode catalog, scheme reconciliation, verified Blender-API facts. |

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

Key contract: **the daemon already scales** o/p/z (per-app sensitivity/gain + the *generic* invert in
`output.py`). The add-on only bakes in a **baseline sign/scale** (`ORBIT_SCALE`, `PAN_SCALE`, …) and
the **scheme/advanced** behavior. Do not re-scale in both places. (The Blender-specific *per-mode*
inverts are the exception — they live in the add-on, see §5.4 and Gotcha #6.)

---

## 4. The add-on internals

### 4.1 Threading & lifecycle
- `bpy` is **main-thread only.** The socket reader runs on a `threading.Thread` and only does I/O +
  `queue.put`. Everything touching `bpy` happens in `_on_timer`, a `bpy.app.timers` callback
  registered `persistent=True` (survives file loads), polling ~90 Hz (`_TIMER_INTERVAL`). This is the
  Blender analogue of the Fusion add-in's `CustomEvent` hop.
- `register()` starts the reader thread + timer, registers the toggle operator + keymap + View-menu
  item, and resets the override/gesture state. `unregister()` tears all of that down. Both are wrapped
  so a reload race can't crash Blender.
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
3. **Effective nav mode**: `_mode_override` (the Alt+\` hotkey) wins over the daemon's `nav_mode`; a
   change to the daemon's Mode clears the override (see §5.5).
4. Rate-limited **`rx ...` diagnostic** log (proves which channel is arriving — invaluable for "it
   does nothing" reports; see Solved Problem #2/#6).
5. **Camera-view exit**: if the viewport is showing the camera and lock-camera is off, switch to
   `PERSP` so edits are visible (Solved Problem #2).
6. **Per-mode invert**: flip o/p/z per `advanced.invert.<mode>` (see §5.4).
7. **Dispatch** by nav mode → `_apply_fly` / `_apply_walk` / orbit (`_apply_orbit` / `_pan` /
   `_zoom`/`_dolly`).
8. **Camera-lock**: if in camera view *and* lock-camera is on, drive `scene.camera.matrix_world`.
9. **`applied ...` diagnostic** (did the view actually change? perspective/area/rotΔ/locΔ).
10. `area.tag_redraw()`.

### 4.4 The math helpers (pure; testable headless)
`_view_axes`, `_eye`, `_orbit_R`, `_apply_world_rotation`, `_pan`, `_zoom`, `_dolly`, `_roll`,
`_look`, `_horizontal`, `_selection_median_from` operate on a **duck-typed view object** (anything
with `.view_location`/`.view_rotation`/`.view_distance`). That's why `tools/blender_nav_math_test.py`
can test them with a tiny stand-in class — no GUI `RegionView3D` needed. Keep them bpy-free.

The core trick is `_apply_world_rotation(rv, R, pivot)`: rotate `view_rotation` by world-space `R`,
then `view_location = pivot + R @ (view_location - pivot)` so the eye rotates rigidly about `pivot`
(eye = `view_location + back*distance` follows automatically). `pivot=None` ⇒ orbit about
`view_location`.

---

## 5. The control model

### 5.1 Modes (`advanced.nav_mode`: `orbit` | `fly` | `walk`)
- **orbit**: ball pitch/yaw rotate the view about the chosen pivot; twist = roll/zoom/dolly per
  `twist_action`. Shift → pan (ball plane) / zoom (twist).
- **fly**: ball = look (rotate about the **eye**, turn in place) + bank on twist; Shift → move
  (ball-forward = thrust, ball-sideways = strafe, twist = rise/fall).
- **walk**: like fly but horizon-locked look (no bank) and horizontal-plane movement.

### 5.2 Pivots (`scheme.orbit_pivot`, relabelled "Orbit around" in the UI)
`viewpoint` → the **eye** (turn in place — see Solved Problem #5); `view` → **auto-depth** raycast
under the screen centre (held per gesture); `object` → selection median; `cursor_3d` → 3D cursor;
`origin` → world origin; unknown → `view_location` (Blender default). `viewpoint` is a Blender-only
value added to the generic `orbit_pivot` enum.

### 5.3 Baseline constants (top of the add-on — tune here, not in the daemon)
| Const | Meaning / why |
|---|---|
| `ORBIT_SCALE = (0.5,0.5,0.5)` | pitch/yaw/twist baseline. **0.5 makes daemon Sensitivity 1.0 a true 1:1 orbit** — the dual-sensor device reports ~2× the physical angle (Solved Problem #3). |
| `PAN_SIGN`, `PAN_SCALE=0.5` | pan direction + feel (× view_distance when `pan_scales_with_distance`). |
| `ZOOM_SCALE=0.5`, `ZOOM_SIGN` | zoom factor per delta. |
| `DOLLY_SCALE=0.5` | dolly distance per delta (× view_distance). |
| `FLY_MOVE`, `WALK_MOVE = 0.5` | fly/walk move scale. Floored at `view_distance≥1` (`_move_scale`) so movement never vanishes up close (Solved Problem #6). |
| `PIVOT_HOLD_IDLE=0.35` | seconds of no frames that ends a gesture → re-raycast the auto-depth pivot. |
| `_TIMER_INTERVAL=1/90` | main-thread poll rate. |
| `_DEFAULT_PORT=47900` | broker port fallback if `bridge.json` is missing. |

### 5.4 Per-mode, per-axis inverts (`advanced.invert`)
Structure: `invert.{orbit,viewpoint,fly,walk}.<axis>`. Applied **in the add-on**, in `_apply`, just
before the dispatch — because the same physical channel means different things per mode (ball
forward/back is *orbit pan-Y* but *fly/walk forward*), so a single invert set can't flip one without
the other (Gotcha #6, Solved Problem #4/#8). Defaults bake in the "inside-out" roll fix:
`viewpoint.roll` and `fly.bank` start **on** so first-person roll matches external-pivot orbit.
`viewpoint` shares orbit's pan/zoom inverts. This **replaced** the old single `invert_viewpoint_roll`
flag — that key is now ignored if present in an old config.

> The *generic* per-app invert (`bindings.invert` orbit/pan/zoom, applied in `output.py`) is **hidden
> for Blender in the UI** and left at default-off so it's a no-op; Blender's inverts are entirely the
> per-mode ones. Don't wire both or you'll double-invert.

### 5.5 In-Blender mode toggle (Alt+\`)
Blender's *native* Walk/Fly cannot be driven by the trackball (Gotcha #5), so the add-on registers its
own operator `trackball_nav.cycle_mode` (class **`TRACKBALL_NAV_OT_cycle_mode`** — the name must match
the bl_idname, Gotcha #8), bound to **Alt+\`** in the 3D View and added to the View menu. It sets a
**local** `_mode_override` that wins over the daemon's `nav_mode`; changing the daemon's Mode dropdown
clears the override so the dropdown re-takes control. This means the user can switch modes entirely
from inside Blender, independent of broker/`nav_mode`-delivery timing.

---

## 6. Configuration reference (`apps.blender` in the JSON config)
- `bindings.*` — generic per-app: `orbit.sensitivity`, `pan.gain`, `zoom.gain`, `zoom.dominance`,
  `toggle` ("shift"/"none"), and `scheme` (`orbit_pivot`/`orbit_style`/`zoom_mode`). Blender defaults
  `scheme.orbit_pivot` to `"viewpoint"`. `bindings.invert` exists but is unused for Blender (see §5.4).
- `advanced.*` — Blender-only: `nav_mode`, `lock_horizon`, `twist_action`, `zoom_style`,
  `zoom_to_mouse`, `lock_camera_to_view`, `pan_scales_with_distance`, `fly_speed`, `walk_speed`, and
  the per-mode `invert` block.
- `rate_hz`, `view_pivot_hold_sec` — per-app rate and the auto-depth hold.

Everything is **additive + deep-merged** (`config._deep_merge`), so new keys appear on existing
configs automatically — **no `CONFIG_VERSION` bump** when adding Blender options. Caveat: deep-merge
never *removes* keys, so retired keys (e.g. `invert_viewpoint_roll`) linger harmlessly on old configs.

How `advanced` reaches the add-on: `App._apply_schemes` attaches `advanced` (a **live reference** to
the config dict) to the broker scheme on every push, `NavBroker._build_frame` serialises it as `"adv"`
**only when non-None**, and the add-on reads `frame["adv"]`. Settings therefore take effect on the
**next motion frame** (they only matter while moving).

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
4. **No mouse position in a timer.** `bpy.app.timers` callbacks have no event/mouse context, so
   "viewport under the cursor" targeting and true "zoom to mouse" aren't possible; we use the active
   view and the screen centre. (A future option: `GetCursorPos` + `window.x/y` + area geometry.)
5. **You cannot drive Blender's *native* Walk/Fly.** `view3d.walk`/`view3d.fly` are modal operators
   that read the mouse/keyboard directly and ignore our `RegionView3D` edits — the whole reason the
   add-on DIYs fly/walk. Tell users to use the daemon's Mode / the Alt+\` toggle, **not** Blender's
   Shift+\` Walk/Fly.
6. **Per-mode inverts must be in the add-on, not the daemon.** The daemon doesn't know the nav mode;
   the same channel maps to different actions per mode. So Blender's direction flips are applied in
   `_apply` (and shipped over `adv`), not folded into `output.py`'s generic invert.
7. **`adv` delivery is "always-attach," not focus-gated.** `_apply_schemes` attaches Blender's
   `advanced` to the broker scheme regardless of which app is focused (other add-ins ignore unknown
   keys). This was deliberate — focus-gating it dropped `nav_mode` updates (Solved Problem #1).
8. **Operator class name must match its `bl_idname`.** `bl_idname="trackball_nav.cycle_mode"` requires
   the class to be `TRACKBALL_NAV_OT_cycle_mode` (CATEGORY + `_OT_` + name). Blender 5.1 registered a
   mismatched name with only a console warning, which masked the bug — watch for that.
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
13. **F3 reload re-imports the module** → fresh globals (`_q`, `_stop`, `_mode_override`, …). Blender
    calls the old `unregister()` then the new `register()`. State does not survive a reload by design.

---

## 9. SOLVED PROBLEMS (history — symptom → root cause → fix)

These were all found via live trackball testing; the fixes are in the code but the *reasoning* isn't.

1. **Fly/Walk wouldn't engage — add-on stayed in orbit (`nav=orbit` in the log).**
   *Root cause:* `_apply_schemes` only attached Blender's `advanced` (which carries `nav_mode`) when
   Blender happened to be the focused app at push time — fragile timing, so `nav_mode` updates were
   dropped. *Fix:* always-attach `advanced` to the broker scheme (Gotcha #7). Also why the Alt+\`
   override exists (§5.5) — it bypasses delivery entirely.
2. **"Nothing moves, Shift does nothing," yet the log showed frames arriving and `_apply` running.**
   *Root cause:* the viewport was in **Camera view**, where Blender ignores `rv` edits (Gotcha #3).
   *Fix:* navigating now exits camera view to perspective (unless lock-camera is on). The
   `applied persp=…->… rotD=… locD=…` diagnostic was added to diagnose exactly this.
3. **Orbit felt 2× too fast at Sensitivity 1.0.**
   *Root cause:* the add-on rotates the view by exactly the broker delta (measured gain 1.000), and the
   **dual-sensor device reports ~2× the physical angle.** *Fix:* `ORBIT_SCALE = 0.5` so Sensitivity 1.0
   is a true 1:1 ball→view orbit. Pan/zoom kept their feel-based scales.
4. **Twist-roll felt inverted in fly/walk/viewpoint vs object orbit.**
   *Root cause:* the world-space roll is the *same* sign everywhere, but rolling **inside-out**
   (first-person / about the eye) *feels* opposite to rolling **outside-in** (about an external pivot).
   *Fix:* default-invert `viewpoint.roll` + `fly.bank`. Later generalised to the full per-mode invert
   set (Problem #8).
5. **"Viewpoint" orbit swung around a seemingly arbitrary point instead of turning the camera.**
   *Root cause:* it orbited `view_location`, which sits far in front after any fly/look or at a large
   view distance. *Fix:* the `viewpoint` pivot now rotates about the **eye** — "turn the camera in
   place." (`view`/`object`/`cursor_3d`/`origin` still orbit external points.)
6. **Shift-to-move did nothing in fly/walk.**
   *Two root causes:* (a) the camera-view bug (#2); (b) the mapping/scale — pushing the ball forward
   *strafed vertically* and movement was scaled purely by `view_distance` (→ ~0 when zoomed in close).
   *Fix:* remap so ball-forward = thrust along view forward, ball-sideways = strafe, twist = rise/fall;
   and floor the move scale at `view_distance≥1` (`_move_scale`).
7. **User wanted to switch modes from inside Blender, like Blender's own Walk/Fly shortcut.**
   *Constraint:* can't drive Blender's native modal (Gotcha #5). *Fix:* the Alt+\` operator/override
   (§5.5) — switches the *trackball's* mode locally.
8. **User wanted granular control over every direction (e.g. invert walk-forward without touching
   orbit) and the 3 viewpoint axes, not just roll; and the two Blender tabs were confusing.**
   *Fix:* the per-mode/per-axis `invert` structure (§5.4), and **merged** the separate "Blender
   Advanced" tab into *Per-App Bindings → blender* (now scrollable). The old `invert_viewpoint_roll`
   flag was replaced by `invert.viewpoint.roll` + `invert.fly.bank`.

---

## 10. Testing

- **Headless (fast, no hardware):** the three `tools/blender_nav_*.py` scripts (see §0). The math test
  covers pure quaternion/vector math; the integration probe drives the real apply pipeline incl.
  pivots, camera-lock, camera-exit, per-mode invert, and the Alt+\` toggle; the socket probe runs the
  *real* `NavBroker` + the add-on in one process and proves frames flow over TCP into the view. They
  exit non-zero on failure (CI-friendly).
- **Daemon side:** `python -m pytest tests -q` (config block, broker `adv` passthrough,
  `_apply_schemes` wiring, regressions). `tests/test_blender_nav_wiring.py` is the Blender-specific one.
- **Live (only thing not coverable headless):** open Blender, run the daemon, focus a 3D viewport,
  switch to 3D mode, and use the trackball. Use `blender_addin.log` (see §12) and Blender's F3 reload
  to iterate. Sign/feel calibration (`ORBIT_SCALE` etc.) needs a real trackball.

---

## 11. How to make common changes

- **Add a Blender option:** add it to `_DEFAULT_BLENDER_ADVANCED` (config), read it from `adv` in the
  add-on, expose it in `_blender_bindings_fields` (ui). Deep-merge means no version migration. Bump the
  add-on version (3 places) if the add-on changed. Restart daemon + F3.
- **Add an invertible axis:** add the key to the right mode in `_DEFAULT_BLENDER_INVERT`, apply it in
  the `_apply` per-mode invert block, add a checkbox to the relevant `_invert_row` in ui.
- **Change feel/calibration:** edit the baseline constants at the top of the add-on (§5.3). Don't add a
  second scale in the daemon.
- **Ship a new add-on build:** bump `bl_info["version"]` + `ADDIN_VERSION` + `version.json`; the
  daemon's `auto_update` re-copies on next launch (compares `version.json`).

---

## 12. Diagnostics & known limitations

- **Log:** the add-on writes `%APPDATA%\TrackballDaemon\blender_addin.log` (rate-limited). Key lines:
  `scheme: nav=…` (what mode/options the add-on received), `rx orbit/pan/zoom …` (which channel is
  arriving — distinguishes daemon/Shift issues from add-on issues), `applied persp=…->… rotD=… locD=…`
  (did the view actually change, and where). The daemon log shows `scheme -> blender: … blender_nav=…`.
- **Deferred / not done:**
  - **Discrete view ops** (Frame Selected, axis snaps, 15° steps) — need a *button-event* channel the
    broker doesn't have yet (it streams only continuous o/p/z; buttons are on-device HID). Clean hooks
    exist but nothing fires them.
  - **"Under the cursor" targeting & true zoom-to-mouse** (the under-mouse `cursor` pivot) — need a live mouse position a timer can't
    get (Gotcha #4); we use the active view / screen centre.
  - **Walk gravity/teleport** — not implemented (no physics step); walk is horizon-locked look +
    horizontal move only.
- **Verify-live items:** camera-lock feel; sign/scale calibration of every axis (the per-mode invert
  defaults are best-guesses for this device).
