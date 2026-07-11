# Unreal Engine navigation — maintainer's guide

The **"what you can't see by reading the code"** document for the Unreal side of the Trackball
Daemon: the architecture, the **verified** editor viewport-camera model + coordinate conventions,
and the Unreal-specific gotchas that would otherwise cost real debugging. Unreal is a **socket
add-on** integration (like Fusion/Blender/FreeCAD), not an in-process driver. Read this before
touching the add-on.

Verified live on the dev machine: **Unreal Engine 5.8** (`5.8.0-55116800+++UE5+Release-5.8`), the
built-in **Python Editor Script Plugin**. Add-on `0.2.4` (Blender-parity scheme: orbit/fly/walk,
camera pivot, twist action, lock-horizon, per-mode inverts; under-mouse `cursor` orbit /
`to_cursor` zoom via Epic's stock **GeoReferencing** editor BPLibrary — gotcha #13;
`selection_overrides_pivot` — gotcha #14).

---

## 0. First 15 minutes

```sh
# Pure camera math — runs under plain pytest (tbnav_unreal_camera.py has NO unreal import):
python -m pytest tests/test_unreal_nav_math.py -q
# Cursor / to_cursor pivot pipeline (stub unreal + GeoReferencing BPLibrary):
python -m pytest tests/test_unreal_cursor_pivot.py -q
# Daemon wiring (install copies the plugin, auto_update, detection, plugin-dir resolution, routing):
python -m pytest tests/test_integrations_unreal.py -q
# Everything:
python -m pytest tests -q
```

**Live-probe the editor Python API HEADLESS** (this is how every fact in §4 was verified — no GUI,
no clicking). A throwaway `.uproject` (JSON: `EngineAssociation` + `Plugins:[{PythonScriptPlugin,
true}]`) is enough; no content needed:

```sh
UE="/c/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
"$UE" /path/Probe.uproject -run=pythonscript -script="/path/probe.py" -unattended -nopause -nosplash -nullrhi -stdout
```

`unreal.MathLibrary` basis math + API introspection work headless; the viewport get/set does **not**
(no viewport in a commandlet → `get_level_viewport_camera_info()` returns **None**). The plugin
auto-load is *also* testable headless: drop the plugin into the project's `Plugins/`, enable it in
the `.uproject`, and the UE log shows `Running start-up script .../TrackballNav/.../init_unreal.py`.

To get the add-on into a running editor: edit
`trackball_daemon/plugins/unreal/TrackballNav/`, **bump the version in three places**
(`ADDIN_VERSION` in `Content/Python/trackball_nav.py` + `version.json` + `VersionName` in
`TrackballNav.uplugin` — see §7), then let the daemon's `auto_update` re-copy it, or call
`integrations.install_unreal(appdef, cfg)`. **Restart the editor** to reload it (Unreal has no
"reload Python add-on" — it's a restart). See §8/§10.

---

## 1. What the Unreal portion is

The daemon streams orbit/pan/zoom deltas over a localhost TCP socket (the "nav broker"). A bundled
**content-only Unreal plugin** (`TrackballNav`) running inside the editor connects to that broker
and drives the active **level-editor perspective viewport camera**. It is the analogue of the
FreeCAD/Fusion add-ons — same socket-reader + main-thread-marshal shape — adapted to Unreal's
free-fly editor camera and its plugin/`init_unreal.py` startup model.

---

## 2. File map

| Path | Role |
|---|---|
| `…/unreal/TrackballNav/TrackballNav.uplugin` | **Plugin descriptor.** Content-only (no C++ Modules), `CanContainContent:true`, depends on `PythonScriptPlugin` **and** `GeoReferencing` (enabling Trackball Nav enables Python + the stock editor viewport-cursor helpers). `EnabledByDefault:false` → the user enables it once. |
| `…/TrackballNav/version.json` | Version the daemon reads for `auto_update` (like Fusion's `.manifest`). Keep in sync with `ADDIN_VERSION` + the `.uplugin` `VersionName`. |
| `…/TrackballNav/Content/Python/init_unreal.py` | **Startup shim.** Unreal auto-runs this for every enabled plugin's `Content/Python` at editor startup. It only `import trackball_nav; trackball_nav.start()`. |
| `…/Content/Python/trackball_nav.py` | **The add-on.** Reader thread, Slate-post-tick main-thread pump, live camera read/write, pivot resolution, scheme, logging. Runs *inside the editor's Python*. |
| `…/Content/Python/tbnav_unreal_camera.py` | **Pure camera math** (vector + Rodrigues rotation, the duck-typed `Camera`, rotator↔basis, orbit/pan/zoom). **No `unreal` import** → unit-testable headless under plain `python`. |
| `trackball_daemon/integrations.py` | `detect_unreal`, `install_unreal` (copy the plugin into each engine's `Engine/Plugins`), `unreal_plugin_dir`, the `_ADDINS["unreal"]` entry, `auto_update`. |
| `navbroker.py` / `app.py` / `config.py` / `ui.py` | Generic broker-app plumbing — **no Unreal-specific code** beyond a one-line `_APP_PROC_HINTS` entry and `config.apps.unreal` default. |
| `tests/test_unreal_nav_math.py` / `tests/test_unreal_cursor_pivot.py` / `tests/test_integrations_unreal.py` | Pure-math + cursor-pivot (stubbed `unreal`) + wiring tests. |

The daemon process and the add-on are **two different Python interpreters** (daemon Python vs
Unreal's bundled Python). They only talk over the broker socket.

---

## 3. End-to-end data flow

```
BLE trackball → output.py (per-app sensitivity/sign + Shift gating) → App._nav_sink
  → (focused app == unreal, enabled) → NavBroker.submit  (accumulate; flush one coalesced frame/Hz)
        frame = {"o":[ox,oy,oz], "p":[px,py], "z":zoom, "op":…, "os":…, "zm":…  (+ Blender-only "adv", IGNORED)}
  ──────────────────── localhost TCP ────────────────────
  → add-on reader thread (background; newline-JSON → queue.Queue)
  → Slate post-tick pump _pump(delta)  (MAIN/game thread; drains the queue)
  → _apply(): read the live viewport camera → tbnav_unreal_camera.{orbit,pan,dolly} → write loc+rotator back
```

Routing is **generic**: `app.py::_APP_PROC_HINTS["unreal"] = ("unrealeditor","ue4editor")` (the one
needed change), and `_nav_sink`'s else-branch sends every non-SolidWorks/non-Onshape app to the
broker. Unreal is selected when the foreground process is `UnrealEditor.exe`/`UE4Editor.exe` and the
app is enabled. (Verified by `tests/test_app_routing.py::test_unreal_routes_to_broker`.)

**Contract:** the daemon already scaled o/p/z (per-app sensitivity/gain + the generic invert). The
add-on only bakes a **baseline sign/scale** (`ORBIT_*`/`PAN_*`/`ZOOM_*` in `tbnav_unreal_camera.py`)
+ the scheme. Don't re-scale in both places. The add-on reads only `o/p/z/op/os/zm` and **ignores**
the Blender-only `adv` key (like the Fusion add-in).

---

## 4. The verified editor camera model (the crux)

All of this was confirmed live (headless pythonscript probe) on UE 5.8 — **do not re-derive it from
matrix algebra, observe it** (the project's recurring lesson). The editor viewport camera is a
**free-fly eye + FRotator**, NOT a view-distance/look-at model, so orbit-about-a-pivot and zoom are
**synthesised** here and written back as location + rotation every frame.

```python
import unreal
sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)   # UE5; fallback: unreal.EditorLevelLibrary
info = sub.get_level_viewport_camera_info()        # -> (Vector loc, Rotator rot)  OR  None (no viewport/commandlet)
loc, rot = info
fwd = unreal.MathLibrary.get_forward_vector(rot)   # +X at identity
right = unreal.MathLibrary.get_right_vector(rot)   # +Y at identity
up  = unreal.MathLibrary.get_up_vector(rot)        # +Z at identity
# ... rotate/translate the basis + eye in pure Python ...
new_rot = unreal.MathLibrary.make_rot_from_xz(unreal.Vector(*fwd), unreal.Vector(*up))  # rebuild the FRotator
sub.set_level_viewport_camera_info(unreal.Vector(*loc), new_rot)
```

- **Coordinate system (observed):** Unreal is **left-handed, Z-up, centimetres**; FRotator is
  (Pitch about Y, Yaw about Z, Roll about X) in **degrees**.
    - identity rotator → forward **+X**, right **+Y**, up **+Z**
    - yaw **+90** → forward +X → **+Y** (== right-handed +90° about world Z), right +Y → −X
    - pitch **+90** (nose up) → forward +X → **+Z**
- **Read the basis** with `MathLibrary.get_forward/right/up_vector(rot)`; **rebuild** the rotator
  with `MathLibrary.make_rot_from_xz(forward, up)` (forward kept fixed, up resolves roll, Y computed)
  — verified to **round-trip exactly**. Doing it this way means the add-on never hand-derives
  Euler↔basis, sidestepping the entire FRotator-order minefield (see Gotcha #1).
- **Orbit about a pivot** (`tbnav_unreal_camera.orbit`/`_rotate_frame`): rotate the basis vectors AND
  the eye `(loc − P)` by the same world rotation (Rodrigues, radians). `pivot=None` → rotate the
  basis in place, eye fixed = free-fly "turn the camera" (the editor's normal feel).
- **Pan** translates the eye along camera right/up scaled by a tracked **focus distance** (cm).
- **Zoom** dollies the eye along forward (or toward the model for `to_object`), scaled by the focus
  distance. There is **no view-distance field** to scale — we track the eye→pivot distance ourselves
  (`_focus["dist"]`, updated on every orbit, default `DIST_DEFAULT = 1000 cm`).

**Orbit magnitude is 1.0** (full ball angle = true 1:1), like the other **eye+target** apps
(Fusion/SolidWorks/Onshape/FreeCAD). Do **not** copy Blender's `0.5` — that is specific to its
`RegionView3D` (HANDOFF §12.12). Locked by
`tests/test_unreal_nav_math.py::test_orbit_scale_is_unity_full_angle`.

---

## 5. The control model (Blender-parity, as of add-on 0.2.0)

The Unreal add-on carries the **same rich scheme as Blender** (an additive `"adv"` object on each
broker frame — see §5.1), interpreted for the editor's free-fly camera. `config.apps.unreal` uses
`_unreal_app()` (the lean shape **plus** a Blender-style `advanced` block).

- **Nav mode** (`advanced.nav_mode`): `orbit` | `fly` | `walk` — the daemon dropdown (or the toggle).
  - **orbit**: un-shifted ball orbits about the pivot; Shift → pan/zoom. Twist is routed by
    `advanced.twist_action` (`roll` | `zoom` | `dolly` | `none`; for Unreal `zoom` == `dolly` since the
    camera has no view-distance). `advanced.lock_horizon` forces turntable (horizon stays level).
  - **fly**: un-shifted ball = **free look that BANKS on twist**; Shift+ball = **6DOF move along the
    camera's own axes** (forward dives/climbs with pitch, vertical along camera-up). `advanced.fly_speed`.
  - **walk**: un-shifted ball = **horizon-locked look** (no bank, twist dropped); Shift+ball = **move
    in the ground plane** (forward stays level) + rise/fall along **world Z**. `advanced.walk_speed`.
  - **fly ≠ walk** (verified): they only coincide when the camera is level and you don't twist. The
    two real differences are (a) banking on look, (b) 3D-along-look vs horizontal-plane movement. Kept
    as separate modes for Blender parity.
- **Pivots** (`scheme.orbit_pivot`): `camera` → the **eye** (turn the camera in place / free-fly);
  `origin` → (0,0,0); `object` → median of the **selected actors'** bounding-box centres
  (`EditorActorSubsystem.get_selected_level_actors()` → `actor.get_actor_bounds(False)`), cached
  ~0.5 s; `selection` currently uses the same selected-actor centre — **Unreal has NO 3D cursor**
  (verified: no such Python
  API; all Blender-style `*cursor*` names are mouse/UI/gizmo, and no 3D-cursor option is shown for Unreal);
  the under-mouse `cursor` pivot (add-on **0.2.3**) raycasts the surface under the **level-viewport
  mouse** — Half A from stock `GeoReferencingEditorBPLibrary.get_viewport_cursor_information()`
  (gotcha #13), Half B `line_trace_single` along that world ray, bbox-validated and **held for the
  gesture** like `screen_center`; on miss / unfocused viewport it continues the configured chain;
  `screen_center` → the surface under the **screen centre** via `SystemLibrary.line_trace_single` down the
  camera forward axis into the **editor world** (`sub.get_editor_world()`), validated against the
  selection bbox and **held for the gesture** (re-raycast on pan/zoom or after a ~0.35 s idle).
  Every unavailable method continues through the configured global chain; `camera` is always the
  eye. If the chain is exhausted, that orbit frame is ignored rather than inventing another pivot.
- **Orbit style** (`scheme.orbit_style`): `free` (about the camera's own right/up/fwd, twist allowed)
  or `turntable` (yaw about WORLD Z + pitch about camera-right, **roll dropped**).
- **Zoom mode** (`scheme.zoom_mode`): `to_center` → dolly along forward; `to_object` → dolly toward the
  selection centre; `to_cursor` → dolly toward the under-mouse surface hit (same Geo ray + trace as
  cursor orbit, own `_zoom_gesture` hold; miss → forward dolly).
- **Per-mode action routes** (`advanced.axis_source` + `advanced.invert`): every action selects
  X/Y/Z and can invert independently in the add-on, where the active mode is known. Rotation uses
  `o`; shifted movement uses `(p.x,p.y,z)`, so twist can drive Walk Forward. Defaults preserve the
  old wiring. Unlike Blender, no roll/bank inversion is baked in.
- **`advanced.pan_scales_with_distance`**: pan scaled by the focus distance (zoom-stable) vs a fixed
  reference distance.

Dropped vs Blender (not applicable to Unreal): `zoom_style` (zoom IS a dolly), `zoom_to_mouse` (use
`zoom_mode`), `lock_camera_to_view` (no editor-camera-view equivalent), and the in-editor Alt+`
mode-cycle shortcut (no equivalent editor input hook wired — use the daemon dropdown).

### 5.1 How the advanced block reaches the add-on
`App._apply_schemes` attaches the **focused broker app's** `advanced` block as the frame's additive
`"adv"` object (Blender's when Blender is focused, Unreal's when Unreal is focused; apps without one —
Fusion/FreeCAD — get `None` and ignore the key). This generalised the old "always attach Blender's
adv" special-case (which couldn't coexist with a second advanced-carrying app). The add-on reads
`o/p/z/op/os/zm` **and** `adv`; the pure-math split keeps every camera op unit-testable headless.

---

## 6. The add-on internals

### 6.1 Bootstrap (simpler than FreeCAD's)
The `.uplugin` lists `PythonScriptPlugin` and `GeoReferencing` as dependencies, so enabling
**Trackball Nav** in *Edit → Plugins* also enables Python and Epic's geo editor helpers (under-cursor
mouse pixel / ray). At editor startup Unreal runs every enabled plugin's `Content/Python/init_unreal.py`
(**verified** in the UE log) → our shim does `import trackball_nav; trackball_nav.start()`. `start()`
is **synchronous** (no `GuiUp`/`QTimer` deferral like FreeCAD needs) — registering a Slate post-tick
callback and a daemon reader thread is safe at startup, and the pump simply no-ops until a
perspective viewport exists.

### 6.2 Threading
`unreal` API is **main(game)-thread-only**. The socket **reader thread** does I/O + `queue.put`
only. The **Slate post-tick pump** (`register_slate_post_tick_callback`, fires each editor tick on
the main thread) drains the queue and touches the camera — the Unreal analogue of Fusion's
CustomEvent hop / Blender's `bpy.app.timers` / FreeCAD's `QTimer`. `set_level_viewport_camera_info`
refreshes the viewport itself (no explicit repaint call needed, unlike Fusion's `vp.refresh()`).

### 6.3 Math helpers (pure; testable headless)
`tbnav_unreal_camera.py` operates on a **duck-typed `Camera`** (eye location + an orthonormal
forward/right/up basis) with hand-rolled vector + Rodrigues rotation, so
`tests/test_unreal_nav_math.py` runs it with plain `python` — **no Unreal needed** (a step better
than Blender). It also mirrors Unreal's **verified** FRotator convention in `rotator_to_basis` /
`basis_to_rotator` (a fallback if the MathLibrary helpers are ever missing, and the spec the tests
guard). Keep it `unreal`-free.

---

## 7. Install, versioning, update (the riskiest part)

Unreal is **project-centric** with **no global add-on dir** — the messy bit. We ship a **content-only
plugin** and `install_unreal` copies it into each detected engine's **`Engine/Plugins/TrackballNav`**.

- **Detection / dirs:** `detect_unreal()` globs `…\Epic Games\UE_*\Engine\Binaries\Win64\
  UnrealEditor.exe`; `unreal_plugin_dir()` = the **newest** engine's `Engine/Plugins/TrackballNav`
  (the `_ADDINS` dest_fn + version-read path); `install_unreal` loops **all** detected engines
  (multiple versions, like `install_blender`).
- **The admin reality (important):** writing under `C:\Program Files\…` needs **admin**, which the
  daemon usually lacks → the copy **fails**, and `install_unreal` returns **ok=False with clear
  manual steps** (re-run as admin, or copy the bundled folder into the engine's `Plugins` dir, or
  into `<YourProject>\Plugins\TrackballNav` — no admin). Because version tracking is **file-based**
  (`installed_addin_version` reads `version.json` at the dest), a failed copy correctly leaves the UI
  on "Set up". (`tests/test_integrations_unreal.py::test_install_reports_manual_steps_when_unwritable`.)
- **Enable once:** even after the files are in place, the plugin is inert until the user **enables it
  in *Edit → Plugins → "Trackball" → restart*** — the analogue of Fusion's one-time "Run on Startup".
- **What actually auto-loads (verified):** a *content-only* plugin (no C++ module of ours) **does**
  run its `init_unreal.py` once enabled — confirmed by the UE startup log. GeoReferencing is a
  stock engine plugin (ships with UE); enabling Trackball Nav pulls it in via the `.uplugin`
  dependency. No Trackball native build is needed.
- **Versioning:** bump **three** places that must match — `ADDIN_VERSION` in `trackball_nav.py`,
  `version.json`, and `VersionName` in `TrackballNav.uplugin`. `_ADDINS["unreal"]` reads `version.json`
  like Fusion's `.manifest`. On a bump, `auto_update` re-copies on the daemon's next launch (needs
  write access — i.e. admin for an engine dir). The hello handshake reports `ADDIN_VERSION` so the
  tray shows the **loaded** build (`Apps: unreal v0.1.0`) — your first check the editor picked up new
  code.

---

## 8. GOTCHAS (the non-obvious stuff — all found live)

1. **`unreal.Rotator(a,b,c)` POSITIONAL order is `(roll, pitch, yaw)`**, NOT `(pitch,yaw,roll)` —
   `Rotator(10,20,30)` yields pitch=20, yaw=30, roll=10. Always use keywords
   `Rotator(pitch=,yaw=,roll=)`, or avoid hand-constructing entirely (the add-on rebuilds via
   `make_rot_from_xz`). This silently mis-orients everything if you assume the obvious order.
2. **`MathLibrary.compose_rotators(A,B)` does NOT add Eulers** — `compose(yaw30, yaw60)` returned
   yaw **0**, not 90. Don't use it for orbit; rebuild the rotator from the rotated basis instead.
3. **HitResult fields are PROTECTED.** `hit.impact_point` / `hit.location` raise `AttributeError`,
   `get_editor_property("ImpactPoint")` raises "protected and cannot be read", and `break_hit_result`
   isn't exposed in 5.8. Read fields via **`hit.to_dict()`** (keys `impact_point`, `location`, … are
   `Vector`s) — what `_hit_point()` does.
4. **`get_level_viewport_camera_info()` returns `None`** when there's no level-editor perspective
   viewport (commandlet, or the editor not ready). The pump guards on `if not info: return`. (Headless
   it is *always* None — that's why the live camera move is the one thing tests can't cover.)
5. **Left-handed + degrees → several sign flips vs the other apps.** The basis/round-trip math is
   convention-safe (it uses Unreal's own helpers), but the **user-feel signs**
   (`ORBIT_SIGN`/`PAN_SIGN`/`ZOOM_SIGN`) are **best-guess defaults** — settle each by EYE on the device
   (Gotcha-adjacent: don't "prove" a sign with self-referential algebra).
5b. **Orbit baseline is `ORBIT_SCALE = 2.0`, NOT 1.0.** On real hardware the default orbit felt HALF
   of what it should be, so the baseline was doubled (verified on the device — see the 0.1.0→0.2.0
   note). There is no hidden `0.5` anywhere in the path (firmware emits true radians; `output.py` emits
   `recv * sensitivity`; the add-on emits `o * ORBIT_SCALE`), so `1.0` rotates by exactly the broker
   angle = the `--debug` cube — but the *feel* wanted ~2×. Set orbit **Sensitivity 0.5** to get back
   to the cube's literal 1:1. (This is the one place Unreal deviates from the §12.12 "eye-camera = 1.0"
   doctrine, on purpose, by hardware observation.)
5c. **Unreal has NO 3D cursor.** Probed the whole `unreal` namespace + `LevelEditorSubsystem`/
   `EditorActorSubsystem` — there is no queryable Blender-style 3D-cursor / editor-pivot point (every
   `*cursor*` name is the mouse cursor / a UI gizmo). The daemon therefore does not offer the
   **3D Cursor** pivot for Unreal. `selection` and `object` both resolve to the selected-actor centre
   in this integration.
5d. **fly ≠ walk** (don't collapse them). Verified: fly look BANKS on twist and moves along the
   camera's 3D forward (dives when pitched); walk look is horizon-locked (twist dropped) and moves in
   the ground plane + world-Z. They coincide only when level and un-twisted.
6. **Centimetres, scenes span thousands of units → pan/zoom scales are distance-relative.** We track a
   **focus distance** (eye→pivot, cm) and scale pan/zoom by it, so the effective move is ~100× the
   metre-based apps without a magic constant. `PAN_SCALE`/`ZOOM_SCALE` are dimensionless ratios.
7. **Editor-world line traces are finicky** (many editor meshes have no collision on the queried
   channel) → the `screen_center` pivot validates the hit and continues the configured chain on miss.
   Expect `screen_center` to behave like `object`/free-fly in scenes without collidable geometry under the
   crosshair.
8. **No native view-distance/pivot** → orbit-about-a-pivot and zoom are **synthesised** and we manage
   the distance ourselves. There is no "set orbit pivot" API.
9. **PIE guard.** `_in_pie()` checks `sub.get_game_world() is not None` and no-ops during Play-In-
   Editor so we don't fight the running game. Best-effort (wrapped in try/except).
10. **UE4 vs UE5 API drift.** `UnrealEditorSubsystem` is UE5; `EditorLevelLibrary` is the deprecated
    UE4.27/early-UE5 path. Both expose `get/set_level_viewport_camera_info` — the add-on tries the
    subsystem first, falls back to `EditorLevelLibrary`, so it spans UE4.27 → UE5.x.
11. **Python Editor Script Plugin must be enabled.** Our `.uplugin` lists it as a dependency so
    enabling Trackball Nav enables it too — but if a user force-disables Python, the add-on can't run.
12. **Two interpreters → two reload rules.** A change to the add-on (`Content/Python/*.py`) needs the
    **editor restarted** (no Python-add-on reload); a change to the daemon needs the **daemon
    restarted**. A change to both needs both.
13. **Under-cursor (`cursor`) orbit / `to_cursor` zoom — DONE in code (add-on 0.2.3) via stock
    GeoReferencing.** Earlier probes correctly found that PIE-only mouse APIs and
    `get_mouse_position_on_platform` can't localise into the level viewport, and that
    `EditorViewportClient` is absent from Python. The missing Half A was **already shipped by Epic**
    inside the **GeoReferencing** plugin (not under an obvious "Editor Scripting" name):
    - **Half A — mouse + optional world ray:** `unreal.GeoReferencingEditorBPLibrary.
      get_viewport_cursor_information()` → `(focused, screen_location, world_location, world_direction)`
      for `GCurrentLevelEditingViewportClient` (C++: `GetCursorWorldLocationFromMousePos`). Fallback:
      `get_viewport_cursor_location()` + `UnrealEditorSubsystem.screen_to_world`. Our `.uplugin` lists
      `GeoReferencing` as a dependency so enabling Trackball Nav enables it (and its SQLiteCore dep).
    - **Half B — surface hit:** `line_trace_single` along that world ray (same bbox + hold machinery as
      `screen_center`). `to_cursor` zoom uses the same `_cursor_pivot` with a separate `_zoom_gesture` hold.
    - **Focus gate (live UX):** Epic's getter sets `focused=false` when the **viewport widget** lacks
      Slate focus (Details / Content Browser / …). Daemon focus on `UnrealEditor.exe` is not enough —
      click the level viewport once. Unfocused / miss makes the method unavailable and resolution
      continues from the start of the configured chain. Logged as `cursor-pivot:` in
      `unreal_addin.log`.
    - **Rejected alternatives:** a custom Trackball C++ module; an EUW click-capturing overlay;
      `get_mouse_position_on_platform` + a calibrated viewport rect (no reliable screen origin).
    - **Live-GUI verify still TODO:** headless stubs cover the pipeline; confirm hover+orbit feel and
      the focus gate on a real editor + trackball.
14. **`selection_overrides_pivot` (add-on 0.2.4).** Config key `apps.unreal.selection_overrides_pivot`
    (default **True**; deep-merged, no config-version bump). The daemon folds it into the frame's
    `adv` object. When **True** and level actors are selected, orbit (`screen_center` / `cursor` / `origin`)
    and `to_cursor` zoom use the **selection centre** instead of the designated pivot. When
    **False**, the designated pivot is used even with a selection (raycast bbox gate disabled).
    `object` / `selection` pivots still mean selection centre. Placeholder toggles exist for every
    other 3D app in the UI; only Unreal applies it today.

---

## 9. How it was verified (so you can re-verify)

Everything in §4/§8 came from **headless** pythonscript probes driven against UE 5.8 (a hand-written
throwaway `.uproject` enabling `PythonScriptPlugin`; `UnrealEditor-Cmd.exe … -run=pythonscript
-script=…`):
- **API surface + conventions** (no viewport needed): `UnrealEditorSubsystem` vs `EditorLevelLibrary`
  presence + the camera methods; `MathLibrary.get_forward/right/up_vector` returning +X/+Y/+Z at
  identity; the yaw+90 → +Y and pitch+90 → +Z observations; `make_rot_from_xz` round-tripping exactly;
  the `Rotator(roll,pitch,yaw)` positional order; `compose_rotators` misbehaving;
  `register_slate_post_tick_callback`/`register_python_shutdown_callback`; `line_trace_single`/
  `get_actor_bounds`/`get_selected_level_actors` signatures; HitResult `to_dict` keys.
- **Plugin auto-load** (the riskiest install fact): the plugin enabled in the project's `Plugins/`,
  the UE log showing `Running start-up script .../TrackballNav/.../init_unreal.py` **before** the
  engine's own plugins, and a follow-up `-script` confirming `trackball_nav._started == True` and a
  registered Slate tick handle inside the editor.

The **one thing not verifiable headless:** whether `set_level_viewport_camera_info` *visibly* moves
the GUI viewport, and the user-feel sign/scale. That's the live-tune pass on a real editor (the same
deferral FreeCAD made for its sign calibration).

---

## 10. Testing

- **Headless (fast, no Unreal):** `tests/test_unreal_nav_math.py` covers the pure math — the verified
  conventions (identity / yaw90 / pitch90 / rotator↔basis round-trip), orbit free + turntable (incl.
  horizon-lock + twist-drop), orbit-about-pivot rigidity + free-fly in-place, pan (distance-scaled),
  and dolly (forward + toward-point). `tests/test_unreal_cursor_pivot.py` covers the GeoReferencing →
  ray → hold / fallback cursor and `to_cursor` pipeline with a stub `unreal`. `tests/test_integrations_unreal.py`
  covers the daemon wiring (Unreal is an `_ADDINS` app; install copies the plugin + marks enabled; the
  admin-needed copy failure returns manual steps without marking installed; `auto_update` re-copies on
  a bump; plugin-dir resolution; detection; `.uplugin` lists GeoReferencing). `python -m pytest tests -q`
  is green.
- **Live (the only thing tests can't cover):** install via the daemon's **Set up** (or drop the
  plugin into a project's `Plugins/`), enable it in *Edit → Plugins* + restart, run the daemon, open a
  level, switch to 3D mode, focus the editor, **click the level viewport**, set Orbit pivot =
  Under Cursor, and use the trackball. Lean on `%APPDATA%\TrackballDaemon\unreal_addin.log`
  (`start:` / `scheme:` / `rx orbit|pan|zoom` / `screen-center-pivot` / `cursor-pivot:` / `applied`).
  **Sign/scale calibration** (`ORBIT_SIGN`/`PAN_*`/`ZOOM_*` in `tbnav_unreal_camera.py`) still wants
  a real trackball — flip with the per-app Invert checkboxes or the constants.

---

## 11. Diagnostics & known limitations

- **Log:** `%APPDATA%\TrackballDaemon\unreal_addin.log` (rate-limited). Key lines: `start:` (loaded +
  engine version), `scheme:` (op/os/zm received), `rx orbit|pan|zoom` (which channel arrived —
  distinguishes a daemon/Shift issue from an add-on issue), `screen-center-pivot:` / `cursor-pivot:` (surface
  hit or fallback — cursor needs viewport Slate focus), `applied` (the camera actually changed),
  `Play-In-Editor active` (PIE guard), `no perspective viewport` (no level/viewport open),
  `GeoReferencingEditorBPLibrary missing` (dependency not enabled). The tray's `Apps: unreal v…`
  confirms the hello handshake.
- **Deferred / not done:** the **live GUI sign/scale calibration** (best-guess defaults); **live
  verify** of under-cursor orbit / `to_cursor` zoom feel + focus-gate; **discrete view ops** (Frame
  Selected, axis snaps) need a button-event channel the broker doesn't have yet.
- **Install caveat:** writing the plugin into an engine `Plugins` dir needs **admin**; without it the
  daemon prints manual steps (engine dir as admin, or the project `Plugins` dir no-admin). The plugin
  must be **enabled once** per project before it loads.
