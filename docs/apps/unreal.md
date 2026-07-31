# Unreal Engine navigation — maintainer's guide

The **"what you can't see by reading the code"** document for the Unreal side of the Trackball
Daemon: the architecture, the **verified** editor viewport-camera model + coordinate conventions,
and the Unreal-specific gotchas that would otherwise cost real debugging. Unreal is a
**broker-connected host add-on** integration (like Fusion, Blender, and FreeCAD), distinct from the
daemon-side direct transports. Shared focus, state, mapping, routing, and lifecycle contracts are
defined in [`../architecture.md`](../architecture.md). Read both before touching the add-on.

Current versions come from `ADDIN_VERSION`, `version.json`, the `.uplugin`, and
`trackball_daemon.__version__`; do not maintain a host or add-on version snapshot here.

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

```powershell
$UnrealEditorCmd = '<Unreal-install>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe'
& $UnrealEditorCmd '<absolute-path>\Probe.uproject' -run=pythonscript `
  '-script=<absolute-path>\probe.py' -unattended -nopause -nosplash -nullrhi -stdout
```

`unreal.MathLibrary` basis math + API introspection work headless; the viewport get/set does **not**
(no viewport in a commandlet → `get_level_viewport_camera_info()` returns **None**). The plugin
auto-load is *also* testable headless: drop the plugin into the project's `Plugins/`, enable it in
the `.uproject`, and the UE log shows `Running start-up script .../TrackballNav/.../init_unreal.py`.

To get the add-on into a running editor: edit
`trackball_daemon/plugins/unreal/TrackballNav/`, **bump the version in three places**
(`ADDIN_VERSION` in `Content/Python/trackball_nav.py` + `version.json` + `VersionName` in
`TrackballNav.uplugin` — see §7), then restart the daemon for `auto_update` or use
**Settings → 3D Apps → Unreal → Update**. **Restart the editor** to reload it (Unreal has no
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
| `app_registry.py` / `navigation_router.py` / `navbroker.py` | Unreal identity, capabilities, and target-isolated broker delivery. |
| `config_store.py` / `runtime_state.py` / `settings_schema.py` / `system_defaults.json` | Sparse settings, live mode/layer state, validation, and current defaults. |
| `tests/test_unreal_nav_math.py` / `tests/test_unreal_cursor_pivot.py` / `tests/test_integrations_unreal.py` | Pure-math + cursor-pivot (stubbed `unreal`) + wiring tests. |

The daemon process and the add-on are **two different Python interpreters** (daemon Python vs
Unreal's bundled Python). They only talk over the broker socket.

---

## 3. End-to-end data flow

```
BLE trackball + immutable RuntimeSnapshot → output.py per-app mapping
  → App._nav_sink creates an Unreal-targeted envelope → NavigationRouter / NavBroker
  → per-target accumulation and configured-rate flush
        frame = {"o":[ox,oy,oz], "p":[px,py], "z":zoom, "op":…, "os":…, "zm":…, "adv":{…}}
  ──────────────────── localhost TCP ────────────────────
  → add-on reader thread (background; newline-JSON → queue.Queue)
  → Slate post-tick pump _pump(delta)  (MAIN/game thread; drains the queue)
  → _apply(): read the live viewport camera → tbnav_unreal_camera.{orbit,pan,dolly} → write loc+rotator back
```

Routing is generic and target-isolated. `app_registry.AppSpec` owns the `UnrealEditor`/`UE4Editor`
process selectors, supported modes, and broker transport. The shipped Shift binding requests the
RuntimeStore secondary layer; `OutputEngine` consumes that immutable state, and `NavigationRouter`
sends the envelope only to clients whose hello identity is `unreal`.

**Contract:** the daemon sends Unreal's immutable correction in `adv.host_baseline`; the add-on
applies it after mode-specific user action routing. `tbnav_unreal_camera.py` is deliberately neutral
and `adv` is part of Unreal's supported wire contract. See
[`../default_profiles.md`](../default_profiles.md).

---

## 4. The verified editor camera model (the crux)

These contracts come from direct API probes: **do not re-derive them from matrix algebra; observe
host behavior when requalifying them.** The editor viewport camera is a
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
`RegionView3D` (see [`blender.md`](blender.md) and
[`../default_profiles.md`](../default_profiles.md)). Locked by
`tests/test_unreal_nav_math.py::test_orbit_scale_is_unity_full_angle`.

---

## 5. The control model

The Unreal add-on carries the **same rich scheme as Blender** (an additive `adv` object on each
targeted broker frame — see §5.1), interpreted for the editor's free-fly camera. Sparse v9 stable-ID
settings resolve from immutable Global/System state plus Unreal overrides; the runtime contributes
the daemon-authoritative effective navigation mode.

- **Nav mode** (`advanced.nav_mode`): `orbit` | `fly` | `walk` | `object` — the daemon setting or
  a persistent/held daemon binding.
  - **orbit**: un-shifted ball orbits about the pivot; Shift → pan/zoom. Twist is routed by
    `advanced.twist_action` (`roll` | `zoom` | `dolly` | `none`). Zoom changes the active level
    viewport FOV; Dolly moves the camera. `advanced.lock_horizon` forces turntable.
  - **fly**: un-shifted ball = **free look that BANKS on twist**; Shift+ball = **6DOF move along the
    camera's own axes** (forward dives/climbs with pitch, vertical along camera-up). `advanced.fly_speed`.
  - **walk**: un-shifted ball = **horizon-locked look** (no bank, twist dropped); Shift+ball = **move
    in the ground plane** (forward stays level) + rise/fall along **world Z**. `advanced.walk_speed`.
  - **object**: primary motion rotates selected root actors as one group around their shared
    location center using the current viewport axes. The secondary layer's **View** frame translates
    in viewport right/up with twist for depth; **Ground** matches Walk movement with planar
    sideways/forward on horizontal viewport axes and twist along world Z. Empty selection is a no-op, selected attached descendants are
    filtered out, and one `ScopedEditorTransaction` is retained per gesture so Undo restores the
    whole move. The level viewport camera is not written.
  - **fly ≠ walk** (verified): they only coincide when the camera is level and you don't twist. The
    two real differences are (a) banking on look, (b) 3D-along-look vs horizontal-plane movement. Kept
    as separate modes for Blender parity.
- **Pivots** (`scheme.orbit_pivot`): `camera` → the **eye** (turn the camera in place / free-fly);
  `origin` → (0,0,0); `object` → aggregate bounds center of the level's scene actors;
  `selection` → aggregate bounds center of selected actors — **Unreal has NO 3D cursor**
  (verified: no such Python
  API; all Blender-style `*cursor*` names are mouse/UI/gizmo, and no 3D-cursor option is shown for Unreal);
  the under-mouse `cursor` pivot raycasts the surface under the **level-viewport mouse** — Half A
  from stock `GeoReferencingEditorBPLibrary.get_viewport_cursor_information()`
  (gotcha #13), Half B `line_trace_single` along that world ray, bbox-validated and **held for the
  gesture** like `screen_center`; on miss / unfocused viewport it continues the configured chain;
  `screen_center` → the surface under the **screen centre** via `SystemLibrary.line_trace_single` down the
  camera forward axis into the **editor world** (`sub.get_editor_world()`), validated against the
  selection bbox and **held for the gesture** (re-raycast on pan/zoom or after the configured Pivot
  hold idle gap).
  Every unavailable method continues through the configured global chain; `camera` is always the
  eye. If the chain is exhausted, that orbit frame is ignored rather than inventing another pivot.
- **Orbit style** (`scheme.orbit_style`): `free` (about the camera's own right/up/fwd, twist allowed)
  or `turntable` (yaw about WORLD Z + pitch about camera-right, **roll dropped**).
- **Zoom target** (`scheme.zoom_mode`): `to_center`, `to_object`, or `to_cursor` controls the fixed
  point. **Pan-mode zoom** (`advanced.zoom_style`) selects native level-viewport FOV Zoom or camera
  Dolly. Under Cursor uses the same Geo ray and an independent held target; an empty-space miss
  synthesizes a point on that ray at the tracked focus depth.
- **Per-mode action routes** (`advanced.axis_source` + `advanced.invert`): every camera or object action selects
  X/Y/Z and can invert independently in the add-on, where the active mode is known. Rotation uses
  `o`; shifted movement uses `(p.x,p.y,z)`, so twist can drive Walk Forward. Defaults preserve the
  physical wiring. Object owns Pitch/Yaw/Roll and Translate X/Y/Z routes, and its immutable
  rotation baseline is the perceptual inverse of camera rotation.
- **`advanced.pan_scales_with_distance`**: pan scaled by the focus distance (zoom-stable) vs a fixed
  reference distance.

Entering Turntable, Lock Horizon, or Walk from a roll-capable state optionally calls
`cammath.level_horizon` once. It preserves location, view direction, focus distance, and the active
pivot. The first frame establishes state; ordinary fixed-mode frames do not repeatedly level.

Dropped vs Blender (not applicable to Unreal): `lock_camera_to_view` (no editor-camera-view
equivalent), and the in-editor Alt+` mode-cycle
shortcut (no equivalent editor input hook wired — use the daemon dropdown). Zoom targeting uses
the shared `bindings.scheme.zoom_mode` field.

### 5.1 How the advanced block reaches the add-on
`App._apply_schemes` publishes Unreal's detached profile through `NavigationRouter`, and
`_apply_runtime_navigation_profile` overlays the focused `RuntimeSnapshot`, including effective
navigation mode. `NavBroker` sends the additive `adv` object only to clients registered for the
active Unreal target. The add-on reads `o/p/z/op/os/zm` and `adv`; the pure-math split keeps every
camera operation unit-testable headless.

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
  tray shows the loaded build as `Apps: unreal v<loaded-version>` — your first check that the editor
  picked up new code.

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
5. **Left-handed + degrees need a host correction.** The basis/round-trip math uses Unreal's own
   helpers and remains convention-safe; user-feel signs belong in the immutable Unreal profile and
   should be settled by eye on the device, not inferred from self-referential algebra.
5b. **Suite alignment belongs only in the immutable Unreal host profile.**
   `tbnav_unreal_camera.py` remains neutral; the mode-aware add-on consumes the factor from
   `adv.host_baseline`. Inspect `host_profiles.json` for the current value instead of copying it here.
5c. **Unreal has NO 3D cursor.** Probed the whole `unreal` namespace + `LevelEditorSubsystem`/
   `EditorActorSubsystem` — there is no queryable Blender-style 3D-cursor / editor-pivot point (every
   `*cursor*` name is the mouse cursor / a UI gizmo). The daemon therefore does not offer the
   **3D Cursor** pivot for Unreal. Selection and Model Center remain distinct: Model Center uses
   aggregate scene bounds.
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
13. **Under-cursor orbit and To Cursor zoom require stock GeoReferencing.** Earlier probes correctly
    found that PIE-only mouse APIs and
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
    - Headless stubs cover the pipeline; the real-editor hover/focus pass is tracked in
      [`TODO.md`](../../TODO.md).
14. **Selection override.** The registered `navigation.selection_overrides_pivot` setting resolves
    through sparse v9 Global/System/app state and is delivered in Unreal's `adv` profile. When
    **True** and level actors are selected, orbit (`screen_center` / `cursor` / `origin`)
    and `to_cursor` zoom use the **selection centre** instead of the designated pivot. When
    **False**, the designated pivot is used even with a selection (raycast bbox gate disabled).
    Model Center and Selection remain distinct. The same selection-override contract is implemented
    by every supported host; Unreal's bbox gate behavior is the host-specific detail here.

---

## 9. Verification boundary

A throwaway project with `PythonScriptPlugin` enabled is sufficient for unattended API-surface,
basis, rotator, trace, bounds, callback, and plugin-startup probes. Use the version-neutral command in
§0 and record completed run evidence under `archive/release-evidence/`.

Headless commandlets have no level-editor perspective viewport, so they cannot prove visible camera
movement, viewport focus behavior, or physical sign/feel. Those claims require a disposable GUI
editor session and remain tracked in [`TODO.md`](../../TODO.md).

---

## 10. Testing

- **Headless (fast, no Unreal):** `tests/test_unreal_nav_math.py` covers the pure math — the verified
  conventions (identity / yaw90 / pitch90 / rotator↔basis round-trip), orbit free + turntable (incl.
  horizon-lock + twist-drop), orbit-about-pivot rigidity + free-fly in-place, pan (distance-scaled),
  and dolly (forward + toward-point). `tests/test_unreal_cursor_pivot.py` covers the GeoReferencing →
  ray → hold / fallback cursor and `to_cursor` pipeline with a stub `unreal`. `tests/test_integrations_unreal.py`
  covers the daemon wiring (Unreal is an `_ADDINS` app; install copies the plugin + marks enabled; the
  admin-needed copy failure returns manual steps without marking installed; `auto_update` re-copies on
  a bump; plugin-dir resolution; detection; `.uplugin` lists GeoReferencing). Run these focused tests
  before the full suite; do not copy a passing count into this guide.
- **Live boundary:** install via the daemon's **Set up** (or drop the
  plugin into a project's `Plugins/`), enable it in *Edit → Plugins* + restart, run the daemon, open a
  level, switch to 3D mode, focus the editor, **click the level viewport**, set Orbit pivot =
  Under Cursor, and use the trackball. Lean on `%APPDATA%\Mildly Useful\Astrolabe\unreal_addin.log`
  (`start:` / `scheme:` / `rx orbit|pan|zoom` / `screen-center-pivot` / `cursor-pivot:` / `applied`).
  **Sign/scale calibration requires a real trackball.** Tune intrinsic suite alignment in
  `host_profiles.json`; use per-app inversion/gain only for user preferences. Keep add-on camera math
  and local multipliers neutral; current qualification status lives in [`TODO.md`](../../TODO.md).

---

## 11. Diagnostics & known limitations

- **Log:** `%APPDATA%\Mildly Useful\Astrolabe\unreal_addin.log` (rate-limited). Key lines: `start:` (loaded +
  engine version), `scheme:` (op/os/zm received), `rx orbit|pan|zoom` (which channel arrived —
  distinguishes a daemon/Shift issue from an add-on issue), `screen-center-pivot:` / `cursor-pivot:` (surface
  hit or fallback — cursor needs viewport Slate focus), `applied` (the camera actually changed),
  `Play-In-Editor active` (PIE guard), `no perspective viewport` (no level/viewport open),
  `GeoReferencingEditorBPLibrary missing` (dependency not enabled). The tray's `Apps: unreal v…`
  confirms the hello handshake.
- Current live-GUI qualification and discrete button-event work are tracked only in
  [`TODO.md`](../../TODO.md).
- **Install caveat:** writing the plugin into an engine `Plugins` dir needs **admin**; without it the
  daemon prints manual steps (engine dir as admin, or the project `Plugins` dir no-admin). The plugin
  must be **enabled once** per project before it loads.
