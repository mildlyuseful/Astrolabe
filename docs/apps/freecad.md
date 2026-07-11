# FreeCAD navigation — maintainer's guide

The **"what you can't see by reading the code"** document for the FreeCAD side of the Trackball
Daemon: the architecture, the verified Coin camera model, and the FreeCAD-specific gotchas that cost
real live debugging. FreeCAD is a **socket add-on** integration (like Fusion/Blender), not an
in-process driver (SolidWorks/Onshape). Read this before touching the add-on.

Verified live on the dev machine: **FreeCAD 1.1.1**, PySide6, Coin3D/pivy. Current add-on `0.1.6`,
daemon `__version__` `0.1.59`; the selection-override addition is offline-tested pending a feel pass.

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
then either let the daemon's `auto_update` re-copy it on next launch, or call
`integrations.install_freecad(appdef, cfg)`. **Restart FreeCAD** to reload it (FreeCAD has no
"reload add-on" — it's a restart). See §8/§10 for the restart rules.

Live end-to-end without hardware: run a `NavBroker` yourself, write `%APPDATA%\TrackballDaemon\
bridge.json`, launch FreeCAD with the add-on installed, and `broker.submit(...)` some orbit deltas —
watch `%APPDATA%\TrackballDaemon\freecad_addin.log` for `boot` / `rx orbit` / `view-pivot` / `applied`
lines. (That is exactly how this integration was verified; the daemon's BLE path isn't needed.)

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
| `trackball_daemon/navbroker.py` / `app.py` / `config.py` / `ui.py` | Generic broker-app plumbing — **no FreeCAD-specific code** (FreeCAD rides the same path as Fusion). |
| `tests/test_freecad_nav_math.py` / `tests/test_freecad_cursor_pivot.py` / `tests/test_integrations_freecad.py` | The pure-math + cursor-pivot + wiring tests. |
| `tools/freecad_cursor_probe.py` | Live GUI probe for the `cursor` pivot's event chain (synthetic QMouseEvents; see §9). |

The daemon process and the add-on are **two different Python interpreters** (daemon Python vs
FreeCAD's bundled Python). They only talk over the broker socket.

---

## 3. End-to-end data flow

```
BLE trackball → output.py (per-app sensitivity/sign + Shift gating) → App._nav_sink
  → (focused app == freecad, enabled) → NavBroker.submit  (accumulate; flush one coalesced frame/Hz)
        frame = {"o":[ox,oy,oz], "p":[px,py], "z":zoom, "op":…, "os":…, "zm":…}
  ──────────────────── localhost TCP ────────────────────
  → add-on reader thread (background; newline-JSON → queue.Queue)
  → QTimer pump _pump()  (MAIN THREAD; drains the queue)
  → _apply(): read the live Coin camera → tbnav_camera.{orbit,pan,zoom} → write it back → view.redraw()
```

Routing is **entirely generic** — `app.py::_APP_PROC_HINTS` already had `"freecad":("freecad",)`, and
`_nav_sink`'s else-branch sends every non-SolidWorks/non-Onshape app to the broker. So **no `app.py`
change was needed**; FreeCAD is selected when the foreground process is `freecad.exe` and the app is
enabled. (Verified by `tests/test_app_routing.py::test_freecad_routes_to_broker`.)

**Contract:** the daemon already scaled o/p/z (per-app sensitivity/gain + the generic invert). The
add-on only bakes a **baseline sign/scale** (`ORBIT_SCALE`/`PAN_*`/`ZOOM_*` in `tbnav_camera.py`) +
the scheme. Don't re-scale in both places.

---

## 4. The verified Coin camera model

All of this was confirmed live (console + GUI probes) on FreeCAD 1.1 — **do not re-derive it from
matrix algebra, observe it** (the project's recurring lesson):

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
  bbox centres (`Gui.Selection.getSelection()`), falling back to the object centre; `view` → the
  surface under the **screen centre** via `view.getObjectInfo((w/2, h/2))`, validated against the
  model bbox (+10 % of its diagonal) and **held for the whole gesture** (re-raycast on pan/zoom or
  after a ~0.35 s idle gap); `cursor` → the surface under the **live MOUSE CURSOR**:
  a passive `SoLocation2Event` observer on the active view caches the last viewport pixel
  (`_cursor_event_cb`, re-bound to the current view every 0.5 s by the pump via
  `_ensure_cursor_hook`), and `_cursor_pivot` feeds that pixel to the SAME `getObjectInfo` pick,
  bbox validation, and per-gesture hold as `view`. Everything ultimately falls back to the model
  centre, then the camera look-at, so orbit always has a sane pivot. (`view`/`cursor` is FreeCAD's
  *easiest* raycast of the apps — `getObjectInfo` does the pick and hands back world coords; no ray
  construction needed.) Add-on 0.1.5 applies `selection_overrides_pivot`: a non-empty selection
  wins over the designated orbit/to-cursor pivot; disabling it restores the requested pivot.
  Add-on 0.1.6 excludes nested Part/Body child bounds from the project aggregate because those are
  local-space duplicates of the correctly placed container Shape; this prevents placed models from
  pulling the computed object centre back toward the origin.
- **Orbit style** (`scheme.orbit_style`): `free` (rotate about the camera's own right/up/fwd, twist
  allowed) or `turntable` (yaw about WORLD Z + pitch about camera-right, **roll dropped** so the
  horizon stays level).
- **Zoom mode** (`scheme.zoom_mode`): `to_center` (about the look-at) / `to_object` (about the model
  centre) / `to_cursor` (about the surface under the mouse cursor — the same
  `_cursor_pivot` raycast with its own per-gesture hold `_zoom_gesture`, so the point under the
  cursor stays put on screen while zooming; a miss falls back to the look-at).

`config.apps.freecad` is the lean generic shape (`_app()`); there is **no** Blender-style `advanced`
block.

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
  dir**, resolved by `freecad_user_mod_dir()` — FreeCAD ≥ 1.0 uses the **versioned**
  `%APPDATA%\FreeCAD\v<maj>-<min>\Mod` (verified live on 1.1 via `App.getUserAppDataDir()` →
  `…\FreeCAD\v1-1\`), ≤ 0.21 used the flat `%APPDATA%\FreeCAD\Mod`. No startup shim is needed (unlike
  Blender) — FreeCAD auto-runs `InitGui.py`.
- **Versioning**: bump **two** places that must match — `ADDIN_VERSION` in `tbnav_freecad.py` **and**
  `version.json`. `_ADDINS["freecad"]` reads `version.json` exactly like Fusion's `.manifest`. On a
  bump, `auto_update` re-copies on the daemon's next launch. The hello handshake reports
  `ADDIN_VERSION` so the tray shows the **loaded** build (`Apps: freecad v0.1.0`) — your first check
  FreeCAD picked up new code.

---

## 8. GOTCHAS (the non-obvious stuff — all found live)

1. **`InitGui.py` runs with SEPARATE globals & locals.** FreeCAD execs each `Mod/*/InitGui.py` such
   that a function *defined* at its top level captures a `__globals__` that does **not** contain the
   file's own top-level names — so calling a helper defined in `InitGui.py` raises `NameError` on
   every module-level reference (and if that helper is a `try/except`-wrapped logger, it fails
   **silently**). This burned ~an hour of "InitGui isn't running" (it was; its writes were just
   `NameError`-ing). **Fix:** keep `InitGui.py` a *shim* that only `import tbnav_freecad;
   tbnav_freecad.start()` — an imported module gets a normal namespace where functions see module
   globals. Don't put logic in `InitGui.py`.
2. **A Mod folder needs `Init.py` or FreeCAD ignores it entirely** — including its `InitGui.py`. A
   folder with only `InitGui.py` is silently skipped. Ship a (no-op) `Init.py` too.
3. **`pivy.coin` must be imported before `view.getCameraNode()`** or touching the returned node
   raises `RuntimeError: No SWIG wrapped library loaded`. The add-on's `_active_view()` guard caught
   that and returned `None` every frame → `frames received but no active 3D view` while a view was
   plainly open. **Fix:** `from pivy import coin` once in `_boot()` (it loads the SWIG library
   process-wide). You don't have to *use* `coin.*` — just import it.
4. **Qt work before `FreeCAD.GuiUp` can crash FreeCAD.** Creating/using Qt objects while the GUI
   isn't fully up crashed FreeCAD on startup (and an unclean kill then left state that made the *next*
   launch flaky). **Fix:** defer with `QtCore.QTimer.singleShot(…, _boot)` and have `_boot()`
   re-check `FreeCAD.GuiUp`, rescheduling itself if False. (Pattern confirmed against the
   `spkane/freecad-addon-robust-mcp-server` add-on, which solves the same race.)
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
   against the model bbox and fall back to the object centre; **hold the pivot per gesture** (don't
   re-raycast every frame — it chases a moving target).
9. **PySide flavour:** FreeCAD 1.1 ships **PySide6** (and a `from PySide import QtCore` shim that also
   works). The add-on tries PySide6 → PySide2 → the shim.
10. **Two interpreters → two reload rules.** A change to the add-on (`tbnav_*.py`) needs **FreeCAD
    restarted**; a change to the daemon needs the **daemon restarted**. A change to both needs both.
11. **`SoLocation2Event.getPosition()` and `getObjectInfo()` share Coin's coordinate system** —
    **device pixels, BOTTOM-left origin** — so the cached cursor pixel feeds `getObjectInfo`
    **unflipped** (`CURSOR_Y_FLIP = False`), even at 125 % display scaling. Verified live
    (`tools/freecad_cursor_probe.py`): widget 1270×683 logical @ dpr 1.25 ↔ `view.getSize()`
    1587×853 device; a Qt event near the widget TOP (y=10) cached as y=840 (bottom-up); and a pixel
    ABOVE centre hit the box's TOP edge (z=10) unflipped while the flipped query hit mid-face —
    bottom-left on both sides. Don't "fix" the y axis; the flag exists in case a FreeCAD/Quarter
    change ever breaks this.
12. **Qt mouse events reach Coin via the viewer's `viewport()`.** The 3D widget is
    `Gui::View3DInventorViewer` (a `QGraphicsView`); events posted/delivered to the QGraphicsView
    itself do NOT produce `SoLocation2Event`s — its **`viewport()`** widget does (that's where real
    mouse moves land too). Only matters for synthetic-event probes; a physical mouse just works.
13. **`Gui.ActiveDocument.ActiveView` returns a STABLE Python object** (verified: `view is
    Gui.ActiveDocument.ActiveView` → True across reads), so `_ensure_cursor_hook` detects a view
    change with a plain `is` and re-binds the observer (dropping the cached pixel — the old view's
    coordinates are meaningless in the new one).
14. **The cursor cache has no "mouse left the viewport" signal.** `SoLocation2Event` only fires
    over the 3D view, so the cache keeps the last in-viewport pixel when the cursor leaves. The
    bbox validation bounds the damage (a stale pixel still resolves to a point ON the model or falls
    back). Live nuance, seen in the e2e run: after big orbits the model can rotate out from under a
    stationary cursor — the raycast then misses and falls back (by design).

---

## 9. How it was verified (so you can re-verify)

Everything in §4 came from two throwaway probes driven against live FreeCAD 1.1:
- a **console** probe (`freecadcmd.exe script.py`) for the headless facts: `getUserAppDataDir`, the
  Mod scan path, `pivy.coin` `SbRotation` order + `multVec`, `Shape.BoundBox`, Z-up.
- a **GUI** probe (`freecad.exe script.py`, work deferred via `QTimer.singleShot` so a real 3D view
  exists) for: `ActiveView`/`getCameraNode` types, the camera fields, `getCameraType`, `getSize`,
  `getObjectInfo` shape, and that `position/orientation` `setValue` + `redraw` actually move the view.

The **end-to-end** path was proven by running a real `NavBroker`, installing the add-on, launching
FreeCAD with a box, and submitting orbit/pan/zoom bursts: the log showed `boot: pivy.coin loaded` →
`scheme: …` → `rx orbit … view-pivot: surface hit → (…)` → `applied op=view ortho=True pos=(…)`, and
the camera orientation measurably changed. Pan and zoom land the same way (`rx pan` / `rx zoom` →
`applied`, no errors).

> **Lesson (0.1.0 → 0.1.1):** a `applied` log line proves the frame was *processed*, NOT that the view
> *visibly moved*. The first cut shipped `PAN_SCALE = 0.0015` — pan logged `applied` every frame but
> moved the camera ~0.002 world units/frame (sub-pixel → "pan does nothing"). The daemon **does** emit
> pan frames on Shift (`output.py` always `_emit_nav(0,0,0,pdx,pdy,0)` unless twist dominates → zoom),
> so the bug was purely the add-on's baseline scale. Fixed to `PAN_SCALE = 0.14` (matches the Fusion
> add-in's proven baseline — the daemon sends the *same* pan deltas to every app), ~0.2 units/frame on
> a framed part. **When calibrating a scale, measure the camera position/height delta, not just that
> `applied` fired.**

> **Cursor pivot (0.1.2 → 0.1.3): how it was verified without a human mouse.** Two live passes,
> both scripted (FreeCAD GUI opens briefly and self-closes):
> 1. `tools/freecad_cursor_probe.py` (`freecad.exe tools\freecad_cursor_probe.py`, log in
>    `%TEMP%\tbnav_cursor_probe.log`) drives **synthetic `QMouseEvent`s through the real
>    Qt → Quarter → Coin pipeline** — the same code path a physical mouse takes — and established
>    Gotchas #11–#13 (coordinate convention, viewport() target, ActiveView identity) plus
>    cursor-hit ≠ centre-hit on a real box.
> 2. An end-to-end run with the INSTALLED 0.1.3 add-on: a scratch `NavBroker` on 47900 with
>    `set_scheme("cursor", "free", "to_cursor")` + a FreeCAD-side script posting cursor moves at
>    pixel P1, then P2. `freecad_addin.log` showed the full chain: `cursor: SoLocation2Event
>    observer registered` → `scheme: pivot=cursor` → gesture 1 `cursor-pivot: surface hit ->
>    (10.00,4.55,9.83)` held for the burst → after the idle gap, gesture 2 re-cast at P2 to a
>    DIFFERENT point `(6.55,10.00,5.38)` → `applied` with the eye→pivot distance preserved
>    (~244 units before/after, eye moved ≈ 244·0.02 for a 0.02 rad yaw — rigid orbit about the
>    cursor hit). The `to_cursor` zoom gesture exercised the MISS fallback live (the model had
>    rotated out from under the stationary cursor → look-at zoom, by design).
> **Still wants a human pass:** the interactive feel of hovering the real mouse while orbiting with
> the real trackball (synthetic events are the same objects, but nobody has *felt* it yet).

> **Orbit scale (0.1.1 → 0.1.2): use 1.0, NOT Blender's 0.5.** The first cut copied Blender's
> `ORBIT_SCALE = (0.5, 0.5, 0.5)` → FreeCAD orbited at **half** the ball angle. But `ORBIT_SCALE` is
> **per-add-on and independent** (it never touches shared daemon code, so it can't "conflict" across
> apps), and the right value depends on the camera model: the **eye+target** camera apps —
> **Fusion, SolidWorks, Onshape — all use magnitude 1.0** (rotate by the full broker angle = the same
> as the `--debug` cube = true 1:1). Only Blender uses 0.5, and that is specific to its `RegionView3D`.
> FreeCAD's Coin `SoCamera` is an eye+look-at camera, so it belongs with the 1.0 group. Fixed to
> `ORBIT_SCALE = (1.0, 1.0, 1.0)`; `tests/test_freecad_nav_math.py::test_orbit_scale_is_unity_full_angle`
> locks it (a pure yaw of θ must rotate the view by θ, not θ/2).

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
  a bump; versioned vs flat Mod-dir resolution; detection). `python -m pytest tests -q` is green.
- **Live (the only thing tests can't cover):** install via the daemon's **Set up**, run the daemon,
  open a FreeCAD 3D view, switch to 3D mode, focus FreeCAD, and use the trackball. Lean on
  `%APPDATA%\TrackballDaemon\freecad_addin.log` (`scheme:` / `rx orbit|pan|zoom` / `view-pivot` /
  `applied`). **Sign/scale calibration** (`ORBIT_SCALE` etc. in `tbnav_camera.py`) is the one item
  that wants a real trackball — the magnitudes follow Blender's verified 1:1 reasoning, but the
  **direction signs are best-guess defaults**; flip with the per-app Invert checkboxes or the
  constants.

---

## 11. Diagnostics & known limitations

- **Log:** `%APPDATA%\TrackballDaemon\freecad_addin.log` (rate-limited). Key lines: `boot:` (loaded +
  PySide flavour + FreeCAD version), `scheme:` (op/os/zm received), `rx orbit|pan|zoom` (which channel
  arrived — distinguishes a daemon/Shift issue from an add-on issue), `view-pivot: surface hit|…
  fallback`, `applied` (the camera actually changed). The tray's `Apps: freecad v…` confirms the
  hello handshake.
- ~~True cursor-pixel pivot~~ — **DONE in add-on 0.1.3** as the under-mouse orbit pivot +
  zoom (FreeCAD is the FIRST app with it; born as `cursor`/`to_cursor`, renamed to
  `cursor`/`to_cursor` in add-on 0.1.4 / daemon config v3, when the old `cursor` value became
  `selection`). Known limitation: no "mouse left the viewport" signal
  (Gotcha #14) — the last in-viewport pixel is used, bounded by the bbox validation.
- **Deferred / not done:** **discrete view ops** (Frame Selected, axis snaps) need a button-event
  channel the broker doesn't have yet.
- **Verify-live items:** the per-axis **sign/scale** defaults; perspective-camera feel (FreeCAD
  defaults to ortho, so the persp path is lightly exercised live); the **human-feel pass** on the
  `cursor` pivot (hover + orbit with the physical trackball — the event chain itself is verified,
  §9).
