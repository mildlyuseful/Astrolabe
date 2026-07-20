# FreeCAD navigation — maintainer's guide

This guide owns FreeCAD-specific setup, Coin camera behavior, threading, and compatibility warnings.
FreeCAD is a **broker-connected host add-on** integration (like Fusion and Blender), distinct from the
daemon-side direct transports used for SolidWorks COM and Onshape. Shared focus, state, mapping,
routing, and lifecycle contracts are defined in [`../architecture.md`](../architecture.md). Read both
before changing the add-on.

Current code versions come from `ADDIN_VERSION`, `version.json`, and
`trackball_daemon.__version__`; do not maintain a host or add-on version snapshot here.

---

## 0. First 15 minutes

```sh
# Pure camera math — runs under plain pytest (tbnav_camera.py has NO FreeCAD imports):
python -m pytest tests/test_freecad_nav_math.py -q
# Daemon wiring (install copies the add-on, auto_update, detection, mod-dir resolution):
python -m pytest tests/test_integrations_freecad.py -q
# Everything:
python -m pytest tests -q
```

To get the add-on into a running FreeCAD: edit `trackball_daemon/plugins/freecad/TrackballNav/`,
**bump the version in two places** (`ADDIN_VERSION` in `tbnav_freecad.py` + `version.json` — see §7),
then restart the daemon for `auto_update` or use **Settings → 3D Apps → FreeCAD → Update**.
**Restart FreeCAD** to reload it (FreeCAD has no add-on reload). See §8/§10 for the restart rules.

Live end-to-end without hardware: run a `NavBroker`, call `broker.activate_target("freecad")`,
write `%APPDATA%\TrackballDaemon\bridge.json`, launch FreeCAD with the add-on installed, and submit
with `broker.submit("freecad", ..., state_revision=...)`. Watch
`%APPDATA%\TrackballDaemon\freecad_addin.log` for `boot`, `rx orbit`, `screen-center-pivot`, and
`applied`; the daemon's BLE path is not required for this probe.

---

## 1. What the FreeCAD portion is

The daemon streams orbit/pan/zoom deltas over a localhost TCP socket (the "nav broker"). A bundled
**FreeCAD add-on** (`TrackballNav`) running inside FreeCAD connects to that broker and drives the
active 3D view's **Coin (pivy) `SoCamera`**. It is the analogue of the Fusion add-in — same
socket-reader + main-thread-marshal shape — adapted to FreeCAD's Coin scene-graph camera and its
embedded-interpreter quirks.

---

## 2. File map

| Path | Role |
|---|---|
| `trackball_daemon/plugins/freecad/TrackballNav/Init.py` | **Console-mode init — a deliberate no-op.** Exists ONLY so FreeCAD recognises the Mod folder (Gotcha #2). |
| `…/TrackballNav/InitGui.py` | **Thin GUI shim.** FreeCAD execs this at GUI startup. It does nothing but `import tbnav_freecad; tbnav_freecad.start()` — because of the exec-namespace gotcha (Gotcha #1). |
| `…/TrackballNav/tbnav_freecad.py` | **The add-on.** Reader thread, main-thread `QTimer` pump, live Coin camera read/write, pivot resolution, scheme, logging, the `GuiUp`/`pivy.coin` bootstrap. Runs *inside FreeCAD's Python*. |
| `…/TrackballNav/tbnav_camera.py` | **Pure camera math** (quaternion/vector, the duck-typed `Camera`, orbit/pan/zoom). **No FreeCAD/pivy/PySide imports** → unit-testable headless under plain `python`. |
| `…/TrackballNav/version.json` | Version the daemon reads for `auto_update` (like Fusion's `.manifest`). Keep in sync with `ADDIN_VERSION`. |
| `trackball_daemon/integrations.py` | `install_freecad` (resolve the user Mod dir + copytree), `freecad_user_mod_dir`, the `_ADDINS["freecad"]` registry entry, `auto_update`. |
| `trackball_daemon/app_registry.py` / `navigation_router.py` / `navbroker.py` | FreeCAD identity plus target-isolated generic broker delivery. |
| `trackball_daemon/config_store.py` / `settings_schema.py` / `system_defaults.json` | Sparse typed settings and current concrete defaults. |
| `tests/test_freecad_nav_math.py` / `tests/test_freecad_cursor_pivot.py` / `tests/test_integrations_freecad.py` | The pure-math + cursor-pivot + wiring tests. |
| `tools/freecad_cursor_probe.py` | Live GUI probe for the `cursor` pivot's event chain (synthetic QMouseEvents; see §9). |

The daemon process and the add-on are **two different Python interpreters** (daemon Python vs
FreeCAD's bundled Python). They only talk over the broker socket.

---

## 3. End-to-end data flow

```
BLE trackball + immutable RuntimeSnapshot → output.py per-app mapping
  → App._nav_sink creates a FreeCAD-targeted envelope → NavigationRouter / NavBroker
  → per-target accumulation and configured-rate flush
        frame = {"o":[ox,oy,oz], "p":[px,py], "z":zoom, "op":…, "os":…, "zm":…}
  ──────────────────── localhost TCP ────────────────────
  → add-on reader thread (background; newline-JSON → queue.Queue)
  → QTimer pump _pump()  (MAIN THREAD; drains the queue)
  → _apply(): read the live Coin camera → tbnav_camera.{orbit,pan,zoom} → write it back → view.redraw()
```

Routing is generic and target-isolated. `app_registry.AppSpec` owns FreeCAD's process selector and
broker transport. The shipped Shift binding requests the RuntimeStore secondary layer; `OutputEngine`
consumes that immutable state, and `NavigationRouter` sends the envelope only to clients whose hello
identity is `freecad`.

**Contract:** the daemon composes FreeCAD's immutable host alignment with the saved user mapping
before broker output. `tbnav_camera.py` is deliberately neutral to prevent double application. See
[`../default_profiles.md`](../default_profiles.md).

---

## 4. The verified Coin camera model

These contracts come from direct console and GUI probes: **do not re-derive them from matrix
algebra; observe host behavior when requalifying them:**

```python
import FreeCADGui as Gui
from pivy import coin                       # MUST be imported first — see Gotcha #3
view = Gui.ActiveDocument.ActiveView        # View3DInventorPy; None if no doc / not a 3D MDI view
cam  = view.getCameraNode()                 # SoOrthographicCamera (default) or SoPerspectiveCamera
cam.position.getValue().getValue()          # -> (x,y,z) eye in world space   (double .getValue()!)
cam.orientation.getValue().getValue()       # -> (x,y,z,w) quaternion, camera-local -> world (w LAST)
cam.focalDistance.getValue()                # eye -> look-at distance; look-at = position + fwd*focal
cam.height.getValue()                       # ORTHO zoom (SoOrthographicCamera only)
cam.heightAngle.getValue()                  # PERSP vertical FOV (SoPerspectiveCamera only)
view.getCameraType()                        # 'Orthographic' | 'Perspective'  (FreeCAD defaults ORTHO)
view.getSize()                              # (width, height) in widget pixels
view.getObjectInfo((cx, cy))                # dict {'x','y','z','Object',…} under pixel, or None
view.redraw()                               # force a repaint (needed when driven from a timer)
```

- **Axes from the orientation quaternion** (Coin's `SbRotation` ≡ our `tbnav_camera.q_rotate`):
  `right = R·(1,0,0)`, `up = R·(0,1,0)`, `fwd = R·(0,0,-1)`, `back = R·(0,0,1)`. The camera looks
  down its **local −Z**, up is local +Y, right is local +X (OpenGL/Coin). Verified: a +90° rotation
  about Z maps (1,0,0)→(0,1,0) for both Coin's `multVec` and our `q_rotate`.
- **Z is world up** (a `Part::Box` with `Height` grows along Z) → `WORLD_UP = (0,0,1)` for turntable.
- **Orbit about a pivot** (`tbnav_camera.apply_world_rotation`): `orientation = R⊗orientation`;
  `position = pivot + R·(position − pivot)`. `focalDistance` is **unchanged** — rotation preserves
  the eye→look-at length, so the look-at follows for free. Setting fields back via
  `cam.position.setValue(x,y,z)` / `cam.orientation.setValue(x,y,z,w)` works (verified).
- **Pan** translates `position` along right/up scaled by the on-screen view height (ortho `height`,
  persp `2·focal·tan(heightAngle/2)`) so the feel is zoom-stable.
- **Zoom**: ortho scales `cam.height` (and, for `to_object`, shifts so the pivot stays put on
  screen); perspective dollies the eye along `fwd` and reduces `focal`.

---

## 5. The control model

- **Pivots** (`scheme.orbit_pivot`): `origin` → (0,0,0); `object` → aggregate model bbox centre
  (Part `Shape.BoundBox` ∪ Mesh `Mesh.BoundBox`, cached ~0.5 s); `selection` → mean of the **selection**
  bbox centres (`Gui.Selection.getSelection()`); `screen_center` → the
  surface under the **screen centre** via `view.getObjectInfo((w/2, h/2))`, validated against the
  model bbox and held for the configured orbit gesture (re-raycast on pan/zoom or after
  `orbit_hold_sec`); `cursor` → the surface under the **live MOUSE CURSOR**:
  a passive `SoLocation2Event` observer on the active view caches the last viewport pixel
  (`_cursor_event_cb`, re-bound to the current view every 0.5 s by the pump via
  `_ensure_cursor_hook`), and `_cursor_pivot` feeds that pixel to the SAME `getObjectInfo` pick,
  bbox validation, and per-gesture hold as `screen_center`. Unavailable methods continue through the
  configured global chain. (`screen_center`/`cursor` is FreeCAD's
  *easiest* raycast of the apps — `getObjectInfo` does the pick and hands back world coords; no ray
  construction needed.) `selection_overrides_pivot` lets a non-empty selection replace the
  designated orbit/to-cursor pivot; disabling it restores the requested pivot. Project aggregation
  excludes nested Part/Body child bounds because they are local-space duplicates of the correctly
  placed container Shape; including them pulls the computed object centre toward the origin.
- **Orbit style** (`scheme.orbit_style`): `free` (rotate about the camera's own right/up/fwd, twist
  allowed) or `turntable` (yaw about WORLD Z + pitch about camera-right, **roll dropped** so the
  horizon stays level).
- **Zoom mode** (`scheme.zoom_mode`): `to_center` (about the look-at) / `to_object` (about the model
  centre) / `to_cursor` (about the surface under the mouse cursor — the same
  `_cursor_pivot` raycast with its own hold `_zoom_gesture`, so the point under the cursor stays put
  on screen while zooming; a miss synthesizes a cursor-ray point at the current focal/model depth).

`orbit_hold_sec` and `zoom_hold_sec` are independent. Pan/zoom invalidate the orbit pivot; pan
preserves the cursor-zoom target; orbit invalidates it. Entering Turntable from Free optionally
levels the horizon once while preserving position, focal point, focal distance, and the active
pivot. A world-up singularity is skipped.

FreeCAD uses the lean capability profile: it has the shared `advanced` values such as Twist action
and holds, but no Blender-style mode-specific Orbit/Fly/Walk action tree.

---

## 6. The add-on internals

### 6.1 Bootstrap (the FreeCAD-specific dance)
`InitGui.py` (thin shim) → `tbnav_freecad.start()` → `QTimer.singleShot(1500, _boot)` → `_boot()`:
re-checks `FreeCAD.GuiUp` (retries via `singleShot` if False), **imports `pivy.coin`** (loads the
SWIG library), starts the reader thread, and creates the persistent ~90 Hz pump `QTimer`. Each step
exists because of a gotcha below — none of it is incidental.

### 6.2 Threading
`bpy`-style rule: Coin/Qt are **main-thread-only**. The socket **reader thread** does I/O +
`queue.put` only. The **`QTimer` pump** (main thread) drains the queue and touches the camera — the
FreeCAD analogue of Fusion's CustomEvent hop / Blender's `bpy.app.timers`. After writing the camera
node, `view.redraw()` forces the repaint (an idle view won't refresh on its own from a timer — the
Fusion `vp.refresh()` lesson).

### 6.3 Math helpers (pure; testable headless)
`tbnav_camera.py` operates on a **duck-typed `Camera`** (position/orientation/focal/height/
height_angle/is_ortho) with hand-rolled quaternion math, so `tests/test_freecad_nav_math.py` runs it
with plain `python` — **no FreeCAD needed at all** (a step better than Blender, which needs
`blender --background` for `mathutils`). Keep it FreeCAD-free.

---

## 7. Install, versioning, update

- **Install** (`integrations.install_freecad`): copytree the bundled add-on into FreeCAD's **user Mod
  dir**, resolved by `freecad_user_mod_dir()`. FreeCAD 1.x uses the versioned
  `%APPDATA%\FreeCAD\v<maj>-<min>\Mod`; older releases used the flat `%APPDATA%\FreeCAD\Mod`. No
  startup shim is needed (unlike Blender) — FreeCAD auto-runs `InitGui.py`.
- **Versioning**: bump **two** places that must match — `ADDIN_VERSION` in `tbnav_freecad.py` **and**
  `version.json`. `_ADDINS["freecad"]` reads `version.json` exactly like Fusion's `.manifest`. On a
  bump, `auto_update` re-copies on the daemon's next launch. The hello handshake reports
  `ADDIN_VERSION` so the tray shows the loaded build as `Apps: freecad v<loaded-version>` — your first
  check that FreeCAD picked up new code.

---

## 8. Load-bearing FreeCAD warnings

1. **Keep `InitGui.py` as an import shim.** FreeCAD executes `Mod/*/InitGui.py` with separate globals
   and locals; helpers defined there can raise `NameError` when they access top-level names. Keep it to
   `import tbnav_freecad; tbnav_freecad.start()` and put all logic in the imported module.
2. **A Mod folder needs `Init.py` or FreeCAD ignores it entirely** — including its `InitGui.py`. A
   folder with only `InitGui.py` is silently skipped. Ship a (no-op) `Init.py` too.
3. **`pivy.coin` must be imported before `view.getCameraNode()`** or touching the returned node
   raises `RuntimeError: No SWIG wrapped library loaded`. The add-on's `_active_view()` guard caught
   that and returned `None` every frame → `frames received but no active 3D view` while a view was
   plainly open. **Fix:** `from pivy import coin` once in `_boot()` (it loads the SWIG library
   process-wide). You don't have to *use* `coin.*` — just import it.
4. **Do not create Qt objects before `FreeCAD.GuiUp`.** Early Qt access can crash startup. Defer with
   `QtCore.QTimer.singleShot(…, _boot)` and have `_boot()` re-check `FreeCAD.GuiUp`, rescheduling while
   it is false.
5. **Versioned user Mod dir on FreeCAD ≥ 1.0.** It's `%APPDATA%\FreeCAD\v1-1\Mod`, **not**
   `%APPDATA%\FreeCAD\Mod`. `freecad_user_mod_dir()` resolves it (newest existing `v*-*` dir, or
   derived from the detected install version; flat for ≤ 0.21). Get this wrong and "Set up" copies
   into a folder FreeCAD never scans.
6. **Coin quaternion is `(x, y, z, w)` — w LAST**, and field reads need a **double** `.getValue()`
   (`cam.position.getValue()` → `SbVec3f`, `.getValue()` again → tuple). Get the component order
   wrong and orbit tumbles; we verified it by observing that +90° about Z sends (1,0,0)→(0,1,0).
7. **Ortho vs perspective fields are mutually exclusive.** `SoOrthographicCamera` has `.height` and
   **no** `.heightAngle`; perspective is the reverse. Decide with `view.getCameraType()` (or
   `hasattr`) and only touch the field that exists. FreeCAD defaults to **orthographic**.
8. **`getObjectInfo` returns `None` off-model** (and a dict with `'x'/'y'/'z'` on a hit) → validate
   against the model bbox and continue through the configured chain on failure; **hold the resolved
   pivot per gesture** (don't re-raycast every frame — it chases a moving target).
9. **PySide flavour varies by host release.** The add-on tries PySide6 → PySide2 → FreeCAD's
   `from PySide import QtCore` compatibility shim; keep that fallback order.
10. **Two interpreters → two reload rules.** A change to the add-on (`tbnav_*.py`) needs **FreeCAD
    restarted**; a change to the daemon needs the **daemon restarted**. A change to both needs both.
11. **`SoLocation2Event.getPosition()` and `getObjectInfo()` use the same Coin coordinates:** device
    pixels with a bottom-left origin. Feed the cached cursor pixel to `getObjectInfo` unflipped
    (`CURSOR_Y_FLIP = False`). Do not add a Qt-style top-left flip; requalify with
    `tools/freecad_cursor_probe.py` if FreeCAD or Quarter changes this contract.
12. **Qt mouse events reach Coin via the viewer's `viewport()`.** The 3D widget is
    `Gui::View3DInventorViewer` (a `QGraphicsView`); events posted/delivered to the QGraphicsView
    itself do NOT produce `SoLocation2Event`s — its **`viewport()`** widget does (that's where real
    mouse moves land too). Only matters for synthetic-event probes; a physical mouse just works.
13. **`Gui.ActiveDocument.ActiveView` returns a STABLE Python object** (verified: `view is
    Gui.ActiveDocument.ActiveView` → True across reads), so `_ensure_cursor_hook` detects a view
    change with a plain `is` and re-binds the observer (dropping the cached pixel — the old view's
    coordinates are meaningless in the new one).
14. **The cursor cache has no "mouse left the viewport" signal.** `SoLocation2Event` fires only over
    the 3D view, so leaving the viewport retains the last in-view pixel. Bbox validation and the pivot
    fallback chain bound stale samples; a stationary cursor may legitimately miss after camera motion.

---

## 9. Verification boundary

Use `freecadcmd.exe` for API and math probes that do not need a viewport, and a disposable GUI session
for `ActiveView`, camera-field, redraw, pointer-event, and visible-motion claims. The maintained
cursor probe is `tools/freecad_cursor_probe.py`; it exercises the real Qt → Quarter → Coin event path
with synthetic pointer events.

> **Warning:** an `applied` log line proves only that a frame was processed. When qualifying camera
> motion or host alignment, measure camera position/orientation/height or observe the viewport; do not
> infer scale correctness from a self-referential log line.

`SoLocation2Event` and `getObjectInfo` use device pixels with a bottom-left origin. Events must target
the viewer's `viewport()` widget, and the observer must be rebound when `ActiveView` identity changes.
Those are durable invariants; dated probe transcripts and completed run evidence belong under
`archive/release-evidence/`.

FreeCAD's eye-and-target camera uses a neutral full-angle orbit scale. Do not copy Blender-specific
`RegionView3D` compensation into this add-on; suite alignment belongs in `host_profiles.json`.

---

## 10. Testing

- **Headless (fast, no FreeCAD):** `tests/test_freecad_nav_math.py` covers the pure math — orbit
  free + turntable (incl. horizon-lock and that turntable drops twist), orbit-about-pivot rigidity,
  pan, and zoom (ortho + perspective). `tests/test_freecad_cursor_pivot.py` covers the `cursor`
  pivot resolvers with a SYNTHETIC pixel (a stubbed `getObjectInfo`): the observer callback caches,
  the hook registers/re-binds (fake pivy) and is a no-op without pivy, cursor ≠ centre changes the
  pivot, the per-gesture hold + idle re-cast, bbox-validated fallbacks, the y-flip math, and the
  `to_cursor` zoom hold. `tests/test_integrations_freecad.py` covers the daemon wiring
  (FreeCAD is an `_ADDINS` app; install copies the add-on + marks enabled; `auto_update` re-copies on
  a bump; versioned vs flat Mod-dir resolution; detection). Run the focused tests before the full
  suite; do not copy a passing count into this guide.
- **Live boundary:** install via the daemon's **Set up**, run the daemon,
  open a FreeCAD 3D view, switch to 3D mode, focus FreeCAD, and use the trackball. Lean on
  `%APPDATA%\TrackballDaemon\freecad_addin.log` (`scheme:` / `rx orbit|pan|zoom` / `screen-center-pivot` /
  `applied`). Suite alignment lives in `host_profiles.json`; use neutral user settings during a
  live sign/scale pass and transfer host corrections there, never into camera-math constants.

---

## 11. Diagnostics & known limitations

- **Log:** `%APPDATA%\TrackballDaemon\freecad_addin.log` (rate-limited). Key lines: `boot:` (loaded +
  PySide flavour + FreeCAD version), `scheme:` (op/os/zm received), `rx orbit|pan|zoom` (which channel
  arrived — distinguishes a daemon/Shift issue from an add-on issue), `screen-center-pivot: surface hit|…
  fallback`, `applied` (the camera actually changed). The tray's `Apps: freecad v…` confirms the
  hello handshake.
- **Under Cursor stale-pixel warning:** FreeCAD exposes no reliable "mouse left the viewport" signal.
  The add-on can retain the last in-viewport pixel, bounded by hit/bbox validation. Keep this warning
  until the invalidation work in [`TODO.md`](../../TODO.md) is complete.
- **`camera` (turn-in-place) is unsupported** — the add-on's
  resolver skips it like `cursor_3d` and the configured chain continues (a `camera` primary keeps
  its selection-override exemption). FreeCAD's Coin camera is an eye+orientation model, so a real
  turn-in-place is implementable. Current camera parity and live qualification are tracked only in
  [`TODO.md`](../../TODO.md).
