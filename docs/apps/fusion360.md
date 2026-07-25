# Fusion 360 navigation — maintainer's guide

The **"what you can't see by reading the code"** document for the Fusion 360 side of the Trackball
Daemon. Fusion is a **broker-connected host add-in** integration (Blender, FreeCAD, SketchUp, and
Unreal follow the same boundary), distinct from the daemon-side direct transports used for
SolidWorks COM and Onshape. The add-in runs inside Fusion's embedded CPython, connects to the daemon's
nav broker, and drives
`app.activeViewport.camera`. Shared focus, mapping, routing, and lifecycle contracts are defined in
[`../architecture.md`](../architecture.md).

Primary code: [`trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py`](../../trackball_daemon/plugins/fusion360/TrackballNav/TrackballNav.py)
(+ `TrackballNav.manifest`). Tests: [`tests/test_fusion_cursor_pivot.py`](../../tests/test_fusion_cursor_pivot.py)
(pixel→ray→pivot math against a stubbed `adsk`). Wiring: `app_registry.py`,
`navigation_router.py`, `navbroker.py`, and `integrations.py` (`install_fusion` / auto-update). User-facing setup: the Fusion section of
[`README.md`](../../README.md). Current versions come from `ADDIN_VERSION`, the manifest, and
`trackball_daemon.__version__`; do not maintain a snapshot here.

---

## 1. What it is, in one paragraph

A background socket thread reads broker frames from `127.0.0.1` using the port in
`%APPDATA%\Mildly Useful\Astrolabe\bridge.json` (`47900` fallback) and fires a Fusion **CustomEvent**; the event handler applies the camera change on Fusion's **main thread**
(the Fusion API is main-thread-only — same marshalling problem every socket add-on solves, each
with its host's mechanism: Blender uses a timer, FreeCAD a `QTimer`, Unreal a Slate post-tick,
SketchUp `UI.start_timer`). The camera model is the classic **eye + target + up**: orbit rotates
eye about the pivot, pan translates along camera right/up, and zoom changes projection or dollies
according to the selected behavior. Intrinsic signs/scales live in `host_profiles.json` and are
applied by the daemon before the frame reaches this lean add-in; the add-in camera math is neutral.

## 2. Install / reload / versioning

- **Install:** daemon Settings → 3D Apps → Fusion 360 → **Set up** copies the folder into
  `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`. One-time in Fusion: *Utilities → Add-Ins
  (Shift+S) → TrackballNav → Run* + tick **Run on Startup** (Fusion won't let an installer set
  that flag).
- **Reload after an edit:** stop/run the add-in in the Add-Ins dialog, or restart Fusion. Daemon-side
  changes require a separate daemon restart.
- **Version bump = TWO places:** `ADDIN_VERSION` in `TrackballNav.py` **and** `version` in
  `TrackballNav.manifest` (must match — the manifest drives the daemon's version-gated
  auto-update, the constant drives the handshake shown in the tray). Forgetting the bump means
  the user's Fusion keeps the old code.

## 3. Gotchas (the ones that cost real debugging)

1. **Fusion has NO external automation API.** You cannot drive or probe Fusion from outside the
   process — no COM, no CLI, no headless mode that loads add-ins usefully. The ONLY way to see
   inside the add-in is its log: `%APPDATA%\Mildly Useful\Astrolabe\fusion_addin.log`. Lean on it; add
   log lines before asking a user to reproduce anything.
2. **`adsk.core.Point3DList` does not exist in the Python API** (despite documentation
   suggesting it). `findBRepUsingRay`'s hit-points argument takes an
   `adsk.core.ObjectCollection`.
3. **`app.activeProduct` isn't reliably the active Design.** Resolve the Design defensively
   (walk `app.documents`/`activeDocument.products` when `activeProduct` isn't a
   `fusion.Design`).
4. **A Fusion `Command` is modal — never use one for passive tracking.** The documented
   mouse-tracking hook is `Command.mouseMove`, but an active Command owns clicks and ANY tool
   activation (or Esc) terminates it. An always-on tracker command fights normal modeling. The
   cursor pivot instead reads the cursor **on-demand at gesture start** via ctypes
   `GetCursorPos` (the add-in runs in-process, so Win32 is available).
5. **Fusion's coordinate/DPI model mixes logical and physical pixels:**
   - `Viewport.screenToView` takes **LOGICAL** screen px in but returns **PHYSICAL** viewport px;
   - `Viewport.viewToModelSpace` consumes **PHYSICAL** px;
   - `vp.width/height` are **LOGICAL**.
   So the `GetCursorPos` pixel (physical) is **÷ the monitor's effective DPI scale** before
   `screenToView`, and the output bounds check validates against **`vp.size × scale`**. Unscaled input
   displaces hits down-right; checking against logical size rejects part of the physical viewport. A
   `cursor map:` log line prints screen px → scale →
   view px for diagnosis. Mixed-DPI multi-monitor is bounded by the range check + fallback but
   not fully verified.
6. **`findBRepUsingRay` on the root component may miss bodies inside assembly occurrences**
   (continues through the configured pivot chain). Revisit if assembly orbit feels off.

## 4. The screen-center-pivot / cursor-pivot raycast

Shared contract ([`architecture.md`](../architecture.md)): pivot on real surface depth, validate
against the model bounds,
continue through the configured fallback chain on a miss, hold the pivot for the whole gesture
(re-cast on pan/zoom or after the idle hold time). Fusion specifics:

- `screen_center` pivot: ray down the **screen centre** via `findBRepUsingRay` (aperture grows ×3 until a
  hit, smallest wins).
- `cursor` pivot / `to_cursor` zoom: `GetCursorPos` → DPI divide →
  `screenToView` → `viewToModelSpace` to aim the ray (perspective: eye→point; ortho: parallel,
  pushed back), then the same pick/validate/hold machinery. If `screenToView` output fails the
  physical-bounds check, a window-under-cursor client-rect mapping is the coordinate fallback. A
  failed orbit hit continues through the configured pivot chain; a failed To Cursor hit synthesizes
  a point on the cursor ray at the current target/model depth.
- The cursor is read fresh at each gesture start — there is deliberately **no cache to go
  stale**.
- `origin` resolves explicitly; `selection` uses the aggregate world-space bounds of
  `app.userInterface.activeSelections`. `selection_overrides_pivot` makes
  that centre replace the designated orbit/to-cursor pivot; disabling it restores the requested
  pivot.
- **`camera` (turn-in-place) is currently unsupported** — the resolver skips it like `cursor_3d`
  and the configured chain continues (a `camera` primary still keeps its selection-override
  exemption). This is a capability gap, not a geometric impossibility: Fusion's viewport is
  perspective-capable, so a real turn-in-place (rotate the camera basis about `camera.eye` with
  the eye fixed) is implementable. This parity item is tracked in [`TODO.md`](../../TODO.md).

Entering Turntable from Free optionally levels the horizon once. The transition rebuilds the up
vector against world up without moving eye/target or changing the view distance/pivot. The first
frame establishes state, the world-up singularity is skipped, and ordinary Turntable frames do not
re-level. `orbit_hold_sec` and `zoom_hold_sec` are independent; pan/zoom invalidate the orbit pivot,
pan preserves the To Cursor zoom target, and orbit invalidates the zoom target.

## 5. Testing

```sh
python -m pytest tests/test_fusion_cursor_pivot.py -q   # pixel->ray->pivot + hold, both DPI cases, stubbed adsk
python -m pytest tests -q                                # daemon wiring + regressions
```

Fusion has no headless viewport, so visible camera and monitor-DPI claims require an installed add-in
and a disposable GUI session. Use `fusion_addin.log` and its `cursor map:` line to distinguish
coordinate mapping from camera application. Current qualification work is tracked only in
[`TODO.md`](../../TODO.md).
