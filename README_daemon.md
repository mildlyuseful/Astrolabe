# Trackball Daemon

A background **system-tray app** that connects to the BLE trackball, injects mouse
movement, and (in 3D mode) drives 3D-app navigation. It replaces the old `cube_test.py`
pygame window with a real app shell + settings UI. The BLE data path and the
mouse/3D output math are unchanged — only the shell and the settings surface are new.

> **Taking over or new to the whole project?** Start with [`HANDOFF.md`](HANDOFF.md) — the
> master maintainer guide covering the firmware + daemon + every integration, the cross-cutting
> gotchas you can't see in the code, and the full list of unfinished/future work. This README is
> the user-facing run/setup guide.

## Run

```sh
pip install -r requirements.txt
python -m trackball_daemon            # tray app, no window on startup
pythonw -m trackball_daemon           # same, with NO console window (normal use)
python -m trackball_daemon --debug    # also opens the cube verification window
```

- The app starts **headless** with a **tray icon**. Right-click it for **Open Settings**,
  a **connection status** line, mode toggle, and **Quit**.
- **Closing the settings window hides it to the tray** — the app keeps running. It exits
  **only** via tray → **Quit**.
- Don't pair the trackball in Windows Bluetooth while the daemon runs (the daemon is the
  BLE consumer and synthesizes pointer input). The firmware's native HID mouse still works
  standalone with the daemon closed.

## Configuration

All settings live in one JSON file in the per-user config dir:

- Windows: `%APPDATA%\TrackballDaemon\config.json`

The UI and the output engine read/write this single file. Edits made in the UI apply live
(device name/address apply on the next reconnect). Hand-editing the file and restarting is
reflected in the UI. Defaults reproduce the original `cube_test.py` behavior exactly.

## 3D-app integrations

The daemon drives CAD apps like a 3Dconnexion SpaceMouse: a local **nav broker** streams
orbit/pan/zoom deltas over `127.0.0.1`, and a thin per-app **add-on** applies them to that
app's camera. Navigation is routed to whichever supported app is **focused** (and enabled),
and only flows in **3D mode** (tray → Mode), so the pointer is untouched in cursor mode.

### Fusion 360 (implemented)
1. Settings → **3D Apps** → Fusion 360 → **Set up**. This copies the `TrackballNav` add-in
   into `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\`.
2. In Fusion: **Utilities → Add-Ins (Shift+S)** → select **TrackballNav** → **Run**, and tick
   **Run on Startup** (Fusion won't let an installer set this for you — one-time click).
3. The add-in connects to the broker; the row flips to **active • connected**, and the tray
   shows `Apps: fusion360 v…`. Switch the daemon to **3D mode** and spin the ball with Fusion
   focused. Hold **Shift** to pan/zoom instead of orbit.
4. Tuning (all in the daemon):
   - **Per-App Bindings** → orbit/pan/zoom **gains**, **Invert axes** checkboxes (orbit
     X/Y/Z, pan X/Y, zoom), and **Viewport refresh rate (Hz)** — all **per app**.
   - **Viewport refresh rate (Hz)** (Per-App Bindings, per app): the rate the daemon pushes
     view updates to that app. Pick a preset (15–120) or type a value; **Default** uses the
     global rate. Raise it for smoother motion (e.g. 60), lower it if the app lags. Applies
     live, and the rate follows whichever app is focused.
   - **General → 3D navigation bridge → Default update rate (Hz)** — the global fallback used
     by any app whose per-app rate is **Default** (default 30).
   - Intrinsic per-axis orientation also lives in the add-in (`ORBIT_SCALE`/`ZOOM_SIGN`).
   - **Control scheme**: the usual **Orbit pivot / Orbit style / Zoom mode** dropdowns. `view`
     raycasts the surface under the screen centre (`findBRepUsingRay`) and holds it per gesture;
     **"cursor (under mouse)" (add-in 0.1.13+) raycasts the surface under the MOUSE CURSOR
     instead** — hover the feature you care about and spin the ball — and **"to_cursor (under
     mouse)"** zooms about it (the cursor is read fresh at each gesture start; if it isn't over
     the viewport, it falls back to the model centre). "selection" falls back to the model
     centre. (Internally these store the scheme values `pointer`/`to_pointer` — the dropdowns
     were relabelled; display-scaling is handled since 0.1.12/0.1.13: Fusion's screenToView takes
     logical screen px but returns physical viewport px, so both the input and the bounds check
     are scale-aware.)

**Add-in updates.** Each add-in carries a version (its `.manifest`). When a newer daemon
ships a newer add-in, the daemon **auto-copies it on startup** (preserving your enabled
state) and the 3D-Apps row also offers a one-click **Update → vX** button. New add-in code
takes effect on the CAD app's **next launch** (or stop/run the add-in). So after updating the
daemon: relaunch it, then restart Fusion.

### SolidWorks (implemented — external COM, no add-in)
SolidWorks is **not** driven by an add-in (those are COM/.NET and need admin registration).
Instead the daemon drives a **running** SolidWorks instance directly through its COM
automation API (pywin32). There is **nothing to install** and the socket broker is not
involved — an in-process driver (`solidworks_driver.py`, parallel to the broker) attaches
and moves the active view's camera.

1. Settings → **3D Apps** → SolidWorks → **Enable**. This just verifies SolidWorks is
   installed and `pywin32` is importable, then enables the integration (no file copy).
2. **Open SolidWorks with a part or assembly**, switch the daemon to **3D mode** (tray →
   Mode), and **focus** SolidWorks. The daemon attaches via
   `GetActiveObject("SldWorks.Application")` (it never *launches* SolidWorks); the row flips
   to **active • connected** and the tray shows `Apps: solidworks v…`. Spin the ball to
   orbit; hold **Shift** to pan/zoom.
3. Camera control uses SolidWorks' **native** view operations — `RotateAboutAxis` (orbit),
   setting `Translation3` (pan), and `ZoomByFactor` (zoom **about the view center**, so the
   model doesn't drift off-screen). Orbit is **camera-relative**: the camera axes are the
   **columns** of the view's `Orientation3` matrix every frame, so it tracks the current view
   instead of the global X/Y/Z, and all channels compose into **one** rotation per frame.
   *(Verified live against SolidWorks 2025 — columns are the camera axes, and orbit/pan/zoom
   each drive the viewport correctly.)* Tuning: the same **Per-App Bindings** (gains + Invert axes)
   as the other apps, plus intrinsic constants at the top of `solidworks_driver.py`:
   - `ORBIT_SIGN` — direction of each orbit channel (pitch/yaw/roll). Defaults mirror the
     Fusion add-in (X/Y inverted); flip any axis that spins the wrong way.
   - `PAN_SCALE` / `PAN_SIGN` — pan magnitude (raise if too slow, lower if it flies off) and
     direction. `Translation3` is screen-relative (meters in the graphics-area plane), so pan
     is **not** divided by the zoom scale.
   - `ZOOM_SCALE` / `ZOOM_SIGN` — zoom rate and direction.
   - `WORLD_UP` — turntable azimuth axis (SolidWorks Y-up → `(0,1,0)`).
   - `FORCE_REDRAW` — force a redraw each frame. If motion stays visible with it `False`,
     leaving it `False` raises the refresh rate.
4. **Control scheme** (the **Orbit pivot / Orbit style / Zoom mode** dropdowns in Per-App
   Bindings, same as the other apps — set per app or leave on **Default** to inherit General →
   3D control scheme). For SolidWorks **all three are applied**, verified live with **zero drift** of
   the held point and at the normal frame rate:
   - **Orbit pivot** — four modes:
     - `origin` rotates about the **model origin** with **zero view translation** — the original
       behaviour (pure rotation, the lightest path).
     - `object` (and `cursor`, which falls back to it) rotates about the model **bounding-box centre**
       (parts via `GetPartBox`, assemblies via `GetBox`). The centre is a fixed point, so it's held
       exactly every frame.
     - `view` rotates about the point on the surface **under the centre of the screen**, found by a
       screen-centre **raycast** (`IModelDocExtension.SelectByRay`) — exactly like SolidWorks' own
       middle-drag orbit. Why a raycast: the camera target sits on the optical axis at an *arbitrary*
       depth, so pinning the screen centre at the object-centre depth makes the model swing when that
       depth ≠ the surface you're looking at; the ray pins the pivot at the **true surface depth** under
       the crosshair. The aperture (ray cylinder) starts tiny and grows ×3 until it hits (so the
       smallest, most accurate hit wins), each hit is validated against the bounding box, and on a miss
       it **falls back to the object-centre depth** (the old behaviour). The ray runs **once per
       gesture** (~16 ms; then the pivot is held), and it saves/restores your current selection so it
       never disturbs your work. SolidWorks' `RotateAboutAxis` ignores its point argument and always
       pivots about the model origin, so `object`/`view` rotate **and then pan** to hold their point.
       The pan is `dT = Scale2·((col_before − col_after)·pivot)`, which *exactly* compensates the
       rotation — the held point stays put to ~1e-16 (machine precision, verified live), so there's no
       discrete-order drift. It's smooth because the expensive COM reads are cached
       (`ActiveDoc`/`ActiveView`, tracked `Translation3`/`Scale2`) and the post-rotation axes are
       predicted analytically, so the pan adds almost no round-trips.
     - The `view` pivot is **captured once and held** through a gesture (like native middle-drag sets
       the rotation centre on mouse-down), and re-raycast only when it actually changes: when the view
       is **panned or zoomed**, or after it's been **still** past the configurable hold time below.
   - **View-pivot hold (s)** (Per-App Bindings, `view` pivot only) — seconds the view must be still
     (no orbit/pan/zoom) before the `view` pivot re-raycasts the surface under the current centre.
     Default **0.5**; `0` recomputes at the start of every orbit. (A pan/zoom always recomputes it
     immediately, since those are guaranteed to move it.)
   - **Orbit style** — `free` (all three axes, with roll) or `turntable` (yaw about world-up +
     pitch about camera-right, **roll dropped** so the model never tilts). One rotation per frame.
   - **Zoom mode** — `to_center` (default) zooms about the view centre (one `ZoomByFactor`, the
     original behaviour). `to_object` keeps the model **bounding-box centre** fixed while zooming;
     drift-free. `to_cursor` **falls back to to_center** (no SW hit-test).
   - Switching the orbit pivot takes effect immediately (any held pivot is dropped).

   > pywin32 note: `GetActiveObject` returns a late-bound dispatch that mis-resolves a few
   > SolidWorks members. The driver handles this — `GetMathUtility`/`CreateVector`/`GetPartBox`/
   > `GetBox`/`SelectByRay`/`GetSelectionPoint2` are flagged as methods (`_FlagAsMethod`) and the
   > `CreateVector` array is passed as a `VT_ARRAY|VT_R8` VARIANT. `SelectByRay` is especially fussy:
   > its `Tol` argument must be an explicit **`VT_I4` integer** (a double silently selects nothing),
   > and the selection count comes from `GetSelectedObjectCount2` (`GetSelectionCount` isn't a
   > resolvable name on that dispatch) — both found by live probing.

   There is **no add-in** to update, so the daemon never auto-copies anything for SolidWorks.

> Requires `pywin32` (Windows). If it's missing the driver disables itself and logs once;
> the rest of the daemon runs normally.
>
> **Resilience.** Each camera op (orbit / pan / zoom / redraw) is applied independently, so one
> failing COM call is logged once to `daemon.log` and skipped — it can't blank the viewport or
> force a disconnect (only the running app actually closing does that). If one axis of control
> stops working, check `daemon.log` for a `solidworks: … step failed` line.
>
> **Smoothness / latency.** Each frame **freezes the viewport** (`IModelView.EnableGraphicsUpdate =
> False`) around the camera ops and re-enables + redraws once at the end, so the rotate + recenter pan
> show as **one** repaint instead of flickering through the intermediate origin-rotated state. That
> removes the jitter *and* cuts a whole repaint per frame — measured **~133 → ~76 ms/frame (≈2×)** on
> a heavy part, live. Orbit also composes all channels into one `RotateAboutAxis` (plus, for a
> non-origin pivot, one recenter pan) on a **fixed-cadence** worker loop (it sleeps only the time left
> in each period *after* the COM work). The remaining cost over out-of-process COM is **property
> reads** (`ActiveDoc`/`ActiveView`/`Translation3` ~17–20 ms *each*; the redraw is ~5 ms), so the
> driver **caches** the view handles and **tracks** `Translation3`/`Scale2` across its own writes
> (re-validating ~once a second) and predicts post-rotation axes analytically — no extra reads. For
> the lowest latency use the `origin` pivot (rotate only, no pan). The **Viewport refresh rate**
> (Per-App Bindings) sets the loop's target period, but actual throughput is bounded by SolidWorks, so
> setting it far above what the part allows just runs as fast as SolidWorks can; lower it (or set
> `FORCE_REDRAW = False`) if it
> ever lags. This out-of-process ceiling is below an in-process add-in (3Dconnexion's own SolidWorks
> SpaceMouse support is an add-in for exactly this reason). Building one is a **proven, viable path**
> — the AutoCAD integration ships exactly such a compiled add-in with zero user friction (pre-built
> DLL bundled with the daemon, auto-loaded, HKCU registration needs no admin) — and is the planned
> upgrade if the COM rate ever limits real use.

> Maintainer's guide (the verified COM view-transform model, every pywin32/late-dispatch gotcha, the
> `SelectByRay` raycast pitfalls, and how to test against live SolidWorks):
> [`docs/solidworks_driver_notes.md`](docs/solidworks_driver_notes.md). **Read it before changing the
> driver** — most of what matters there is not visible in the code.

### AutoCAD (implemented — bundled in-process plugin, the sole transport)
AutoCAD **is** driven by a compiled in-process plugin — and this turned out to be the *right* call,
not something to avoid: the pre-built `TrackballNavAcad.dll` ships inside the daemon and is loaded
**automatically with zero user steps** (copy → trust → `NETLOAD`), so the "needs a per-version .NET
build" objection reduces to a dev-time rebuild per AutoCAD binary era. COM's only remaining job is
that delivery: the **plugin loader** (`autocad_driver.py`, pywin32) attaches to a **running** AutoCAD
(it never launches one) and NETLOADs the plugin once per session. The old COM *nav transport* (the
`ActiveViewport`-reassign orbit + rotating-cube overlay) is **archived** at
`archive/autocad_com_transport/` — as a "fallback" it raced the plugin for the first frames of each
session and could apply a stale deferred orbit after the plugin took over, so AutoCAD frames now go
to the nav broker **unconditionally**, exactly like Fusion/Blender/FreeCAD. There is still
**nothing for the user to install** beyond the daemon itself. AutoCAD **verticals** (Civil 3D,
Architecture, Mechanical) are all `acad.exe` and expose the same automation object, so they work too.

1. Settings → **3D Apps** → AutoCAD → **Enable / Install**. This verifies AutoCAD + `pywin32` and
   stages the bundled plugin; the same row's **Update** button tracks the plugin version like the
   other add-ins (with AutoCAD running an update is staged and loads on AutoCAD's next start).
2. **Open AutoCAD with a 3D model-space drawing**, switch the daemon to **3D mode** (tray → Mode), and
   **focus** AutoCAD. The loader NETLOADs the plugin; it connects to the nav broker and the tray shows
   `Apps: autocad v…` (the plugin's version). Spin the ball to orbit; hold **Shift** to pan/zoom.
3. The plugin drives the viewport's **live graphics-kernel view** (`ObtainAcGsView` with the
   "3D Drawing" kernel descriptor — the same view AutoCAD's own 3DORBIT drives): **~1.6 ms/frame with
   zero regenerations during the gesture**, no flicker, free-roll included. At gesture end the drawing
   database is synced once — regen-free in every 3D visual style; in **"2D Wireframe"** (which
   presents from a projected 2D cache) the plugin instead runs **one real `REGEN` per gesture**
   (you'll see "Regenerating model." — that's the sync landing) and the HUD/ViewCube hide during the
   motion, the same interaction mode AutoCAD's own orbit uses there.
4. **Control scheme** (the **Orbit pivot / Orbit style / Zoom mode** dropdowns in Per-App Bindings,
   same as the other apps — per app or **Default** to inherit General):
   - **Orbit pivot** — `origin` (WCS origin), `object` (drawing-extents centre), `view` (the current
     view **target** — AutoCAD's native target orbit), `cursor` (falls back to object), and
     **"cursor (under mouse)" — orbit about the point under the MOUSE CURSOR** (stored as the
     scheme value `pointer`; plugin v0.3.0+: the plugin
     watches the cursor via `PointMonitor`, holds the point under it when a gesture starts, and
     orbits the view rigidly around it; `to_pointer` zoom keeps that point fixed while zooming. In a
     shaded visual style the depth comes from the entity under the cursor; in 2D wireframe faces
     don't pick, so mid-face hovers use the construction-plane point — still under the cursor).
   - **Orbit style** — `free` or `turntable` (yaw about world-up + pitch about camera-right; AutoCAD
     is **Z-up**). Unlike the old COM path, the plugin sets `VIEWTWIST` directly, so free-roll works.
   - **Zoom mode** — `to_center` (default), `to_object` (zoom about the drawing-extents centre), or
     `to_pointer` (zoom about the point under the mouse, parallel projection); `to_cursor` falls
     back to `to_center`.

> Requires `pywin32` (Windows) for the NETLOAD delivery only. If it's missing the loader disables
> itself and logs once; the plugin can still be loaded by hand (`NETLOAD` →
> `%APPDATA%\TrackballDaemon\acad_plugin\TrackballNavAcad.dll`) and everything else works.
>
> **Zero-friction delivery.** When the daemon sees a running AutoCAD it copies the plugin to
> `%APPDATA%\TrackballDaemon\acad_plugin\`, adds that folder to AutoCAD's trusted paths (no
> SECURELOAD prompt), and NETLOADs it; the plugin connects to the nav broker like the other add-ons.
> Type `TBNAV` in AutoCAD to check its status (`TBNAVTEST` runs a synthetic-orbit self-test); it logs
> to `%APPDATA%\TrackballDaemon\acad_plugin.log`. Requires AutoCAD 2025+ (.NET 8); developers rebuild
> with `dotnet build -c Release plugin_src/autocad/TrackballNavAcad` (needs the free .NET 8 SDK only).

> Maintainer's guide (the live-GS-view transport, the 2D-wireframe commit saga and its crash taxonomy,
> the verified ActiveX view model the archived COM transport used, the ROT attach, and how to test
> against live AutoCAD): [`docs/autocad_driver_notes.md`](docs/autocad_driver_notes.md). **Read it
> before changing the plugin or loader** — most of what matters there is not visible in the code.

### Onshape (implemented — browser bridge, no add-in, no extension)
Onshape runs in a browser and has **native 3Dconnexion SpaceMouse** support. Rather than fake
mouse drags, the daemon stands up its **own** local server impersonating the 3Dconnexion
**NL-Proxy** service that Onshape's page connects to — a TLS WebSocket on the loopback endpoint
`127.51.68.120:8181` speaking the WAMP-based "3DxWare for web" protocol. Onshape connects, hands us
its camera, and the in-process bridge (`onshape_bridge.py`, parallel to the broker) applies the
trackball's orbit/pan/zoom and writes the new camera back. (Full reverse-engineered protocol +
cert/trust details: [`docs/onshape_bridge_notes.md`](docs/onshape_bridge_notes.md).)

1. Settings → **3D Apps** → Onshape → **Enable**. This generates a self-signed TLS cert for
   `127.51.68.120` in `%APPDATA%\TrackballDaemon\` (it does **not** touch any trust store) and shows
   the two one-time steps below.
2. **Trust the cert** so Chrome/Edge will connect (they use the Windows cert store). Recommended,
   **no admin** — run in a terminal and click *Yes* on the Windows prompt:
   ```
   certutil -user -addstore Root "%APPDATA%\TrackballDaemon\onshape_cert.pem"
   ```
   (Undo later with `certutil -user -delstore Root 127.51.68.120`.) **Or**, zero system change: browse
   to `https://127.51.68.120:8181` once and accept the warning. *(Firefox uses its own trust store —
   use the in-browser exception there.)*
3. **Enable the SpaceMouse / 3Dconnexion option in Onshape** (Account → Preferences, or the view
   settings). On Windows + Chrome no userscript is needed — Onshape probes the local service on its
   own (it only does so when `navigator.platform` is Windows/Mac, which it already is).
4. Open an Onshape document in Chrome, switch the daemon to **3D mode**, and **focus** the Onshape
   tab. The 3D-Apps row flips to **active • connected** and the tray shows `Apps: onshape v…`. Spin
   the ball to orbit; hold **Shift** to pan/zoom. The same **Per-App Bindings** (gains + Invert axes
   + **Control scheme**) and **Viewport refresh rate** apply as for the other apps. Camera math is a
   reuse of the Fusion add-in's orbit/pan/zoom-about-a-pivot (the bridge decodes Onshape's
   `view.affine` to eye + a camera basis, applies the change, re-encodes), so **orbit pivot / orbit
   style / zoom mode** are all honored (`view` pivot uses `view.target` when Onshape exposes it, else
   the model centre; `to_cursor` falls back to `to_center`).
   - Intrinsic per-axis orientation lives at the top of `onshape_bridge.py`
     (`ORBIT_SIGN`/`PAN_SIGN`/`PAN_SCALE`/`ZOOM_SIGN`/`ZOOM_SCALE`, and `WORLD_UP` for the turntable
     azimuth — Onshape's scene up may be Y or Z, so verify turntable and flip `WORLD_UP` if verticals
     tilt). `AFFINE_TRANSLATION_IN_COLUMN` is the matrix-convention guard (flip it if orbit/pan come
     out transposed). Expect a one-time sign/scale pass on real hardware, like the other apps.

> Requires a way to mint the cert — the Python `cryptography` package (recommended) or `openssl` on
> PATH. If neither is present, **Enable** explains how to install one. The WSS server itself uses only
> stdlib `ssl` + a tiny built-in WebSocket (no FastAPI/uvicorn/numpy). There is **no add-in** to
> update, so the daemon never auto-copies anything for Onshape.
>
> **Standalone test.** `python -m trackball_daemon.onshape_bridge --spin` runs just the bridge with a
> synthetic orbit, so you can confirm TLS trust + the Onshape handshake (watch `daemon.log`) before
> involving BLE/hardware.

### FreeCAD (implemented — socket add-on)
FreeCAD has an embedded Python interpreter and a `Mod/` add-on system, so it's driven by a bundled
**socket add-on** (`TrackballNav`) — the same mechanism as Fusion/Blender (not the in-process driver
used for SolidWorks/Onshape). The add-on runs **inside FreeCAD**, connects to the broker, and drives
the active 3D view's **Coin (pivy) camera**.

1. Settings → **3D Apps** → FreeCAD → **Set up**. This copies the `TrackballNav` add-on into
   FreeCAD's **user Mod folder** — on FreeCAD 1.x that's the *versioned* dir
   `%APPDATA%\FreeCAD\v<major>-<minor>\Mod\TrackballNav` (e.g. `…\FreeCAD\v1-1\Mod\` for FreeCAD 1.1;
   FreeCAD ≤ 0.21 used the flat `%APPDATA%\FreeCAD\Mod`). The daemon resolves the right one for you.
2. **Restart FreeCAD.** Unlike Fusion (which needs a one-time *Run on Startup* tick), the FreeCAD
   add-on **auto-starts** — FreeCAD runs every `Mod/*/InitGui.py` at launch, so there is **nothing to
   enable by hand**.
3. Open a document with a **3D view**, switch the daemon to **3D mode** (tray → Mode), and **focus**
   FreeCAD. The row flips to **active • connected** and the tray shows `Apps: freecad v…`. Spin the
   ball to orbit; hold **Shift** to pan/zoom. (With no document / 3D view open the add-on no-ops
   cleanly — open a part first.)
4. Tuning — the same **Per-App Bindings** (orbit/pan/zoom **gains**, **Invert axes**, **Viewport
   refresh rate**) and **Control scheme** (**Orbit pivot**, **Orbit style** free/turntable, **Zoom
   mode**) as the other apps. The `view` pivot raycasts the surface under the screen centre (via
   FreeCAD's `getObjectInfo`) and holds it for the gesture, exactly like native middle-drag orbit;
   the **"cursor (under mouse)" pivot raycasts the surface under the MOUSE CURSOR** instead (and
   **"to_cursor (under mouse)"** zooms about it) — hover the point you care about and spin the
   ball (stored as the scheme values `pointer`/`to_pointer`); "selection" pivots on the current
   selection's centre. FreeCAD is
   **Z-up**, so turntable keeps verticals vertical. Intrinsic per-axis orientation/feel lives in the
   add-on's `tbnav_camera.py` (`ORBIT_SCALE`/`PAN_SIGN`/`PAN_SCALE`/`ZOOM_SCALE`/`ZOOM_SIGN`); the
   **direction signs are best-guess defaults** — flip any axis that feels backwards with the per-app
   **Invert** checkboxes (or tune the constants), a quick one-time pass on real hardware.

**Add-on updates** work exactly like Fusion/Blender: the add-on carries a version (`version.json`),
the daemon **auto-copies a newer bundled version on startup**, and the 3D-Apps row offers a one-click
**Update → vX**. New add-on code takes effect on FreeCAD's **next launch**. So after updating the
daemon: relaunch it, then restart FreeCAD.

> Maintainer's guide (the verified Coin camera model, the FreeCAD-specific gotchas — `InitGui.py`'s
> separate-globals/locals exec, the `pivy.coin` SWIG-load requirement, the `GuiUp`/`QTimer` deferral,
> the versioned user-dir — and how to test against live FreeCAD):
> [`docs/freecad_driver_notes.md`](docs/freecad_driver_notes.md). **Read it before changing the
> add-on.**

### SketchUp Desktop (implemented — Ruby socket extension)
SketchUp Pro/Studio is driven by a bundled **Ruby extension** running inside the desktop app.
SketchUp for Web is not supported (it has no local scripting hook).

1. Settings → **3D Apps** → SketchUp → **Set up**. The daemon copies the extension into every
   detected annual Plugins folder:
   `%APPDATA%\SketchUp\SketchUp <year>\SketchUp\Plugins`.
2. **Restart SketchUp.** The top-level loader registers **Trackball Nav** in Extension Manager and
   auto-enables it at launch; there is nothing else to click.
3. Open a model, switch the daemon to **3D mode**, and **focus SketchUp**. The row flips to
   **active • connected** and the tray shows `Apps: sketchup v…`.
4. In **Per-App Bindings → sketchup**, choose:
   - **Orbit** — normal object/view-centred navigation. **Viewpoint** turns the camera in place;
     Auto Depth raycasts the surface under the viewport centre and holds it for the gesture.
   - **Fly** — unconstrained 6DOF look; hold **Shift** to strafe/advance and rise/fall.
   - **Walk** — horizon-locked look; hold **Shift** to move on the ground plane, with twist for
     rise/fall (useful for stairs/floors).
   Each mode has independent pitch/yaw/roll-or-bank and movement-direction **Invert** options,
   matching Blender's controls. SketchUp has no 3D-cursor target, so none is shown.

SketchUp is right-handed, Z-up, and stores model coordinates in **inches**. The bundled baseline
signs are starting defaults; use the per-app Invert checkboxes for the physical sign/feel pass.
Add-on updates are version-gated and take effect on SketchUp's next launch. Viewpoint/fly/walk were
added in extension `0.2.0`.

> Maintainer guide, verified live on SketchUp 2026.2.243:
> [`docs/sketchup_driver_notes.md`](docs/sketchup_driver_notes.md).

### Unreal Engine (implemented — socket add-on, a content-only editor plugin)
Unreal's **Editor** exposes Python (the built-in **Python Editor Script Plugin**), so it's driven by
a bundled **content-only plugin** (`TrackballNav`) — the same socket mechanism as
Fusion/Blender/FreeCAD. The plugin runs **inside the editor**, connects to the broker, and drives the
**level-editor perspective viewport camera** (a free-fly eye + rotation). Orbit-about-a-pivot and
zoom are **synthesised** (Unreal's editor camera has no view-distance), so it feels like a
SpaceMouse orbiting your model.

1. Settings → **3D Apps** → Unreal Engine → **Set up**. This copies the `TrackballNav` plugin into
   each detected engine's `…\Epic Games\UE_<ver>\Engine\Plugins\TrackballNav`.
   - **Writing there needs administrator rights.** If the copy fails, the daemon shows exact manual
     steps: re-run it **as administrator** and click Set up again, **or** copy the bundled
     `trackball_daemon\plugins\unreal\TrackballNav` folder into that engine `Plugins` dir yourself,
     **or** — no admin needed — drop it into **your project's** `Plugins\TrackballNav` folder instead.
2. **Enable it once:** in the editor, **Edit → Plugins → search "Trackball" → tick "Trackball Nav"**,
   then **restart the editor**. (This is the analogue of Fusion's one-time *Run on Startup*. Enabling
   Trackball Nav also enables the **Python Editor Script Plugin** it depends on — no separate step.)
3. Open a level, switch the daemon to **3D mode** (tray → Mode), and **focus** the Unreal Editor. The
   row flips to **active • connected** and the tray shows `Apps: unreal v…`. Spin the ball to orbit;
   hold **Shift** to pan/zoom. (During **Play-In-Editor** the add-on no-ops so it doesn't fight the
   game; it also no-ops cleanly with no perspective viewport open.)
4. Tuning — Unreal gets the **full Blender-style Per-App Bindings panel**:
   - **Navigation mode** — toggle **Orbit / Fly / Walk** (with Fly speed / Walk speed). *Fly* = free
     6DOF (banks on twist; forward follows pitch). *Walk* = horizon-locked look, movement stays on the
     ground plane. In **orbit** mode, un-shifted twist does **Twist action** (roll / zoom / dolly /
     none), and **Lock horizon** keeps the view level.
   - **Orbit around** — **Viewpoint** (turn the camera in place), **Auto Depth** (raycast the surface
     under screen-centre; falls back to selection → free-fly on a miss), **Selection** (selected
     actors' bounding box), **World Origin**, and **3D Cursor** (Unreal has no 3D cursor, so this
     orbits the selection too). **Orbit method** free/turntable.
   - **Invert directions** — independent per mode (Orbit / Viewpoint / Fly / Walk), plus orbit/pan/zoom
     **gains** and **Viewport refresh rate**.
   Unreal is **left-handed, Z-up, centimetres**, so expect to flip a few **Invert** checkboxes the
   first time — the add-on's intrinsic signs (`ORBIT_SIGN`/`PAN_*`/`ZOOM_*` in `tbnav_unreal_camera.py`)
   are **best-guess defaults**. The default **orbit sensitivity is doubled** vs the literal cube 1:1
   (it felt half on hardware); set **Orbit sensitivity 0.5** if you want exact 1:1.

**Add-on updates** work like Fusion/Blender/FreeCAD: the plugin carries a version (`version.json`),
the daemon **auto-copies a newer bundled version on startup** (when it can write the engine dir —
i.e. with admin), and the 3D-Apps row offers a one-click **Update → vX**. New code takes effect on
the editor's **next launch**. So after updating the daemon: relaunch it, then restart the editor.

> Maintainer's guide (the verified free-fly editor-camera model + conventions, the Unreal-specific
> gotchas — `Rotator(roll,pitch,yaw)` positional order, `make_rot_from_xz` for the rotator rebuild,
> `HitResult.to_dict()` for trace hits, the project-centric/admin install reality — and how to
> live-probe the editor Python API **headless**): [`docs/unreal_driver_notes.md`](docs/unreal_driver_notes.md).
> **Read it before changing the add-on.**

Blender is also **implemented** (a rich socket add-on — orbit/pan/zoom plus fly/walk, camera-view
driving, and more); see [`HANDOFF.md`](HANDOFF.md) §8 and
[`docs/blender_handoff.md`](docs/blender_handoff.md) for its setup and tuning.

## Project layout (packaging-ready)

```
trackball_daemon/
  ble.py          BLE / data ingestion        (unchanged behavior)
  output.py       SendInput + quaternion + routing math (unchanged; numbers from config)
  config.py       single source of truth (JSON)
  navbroker.py    127.0.0.1 nav broker (streams orbit/pan/zoom to socket add-ons)
  solidworks_driver.py  in-process SolidWorks COM driver (no add-in; drives a running SW)
  onshape_bridge.py     in-process Onshape bridge (TLS WebSocket NL-Proxy emulator for the browser)
  autocad_driver.py     AutoCAD plugin loader (COM NETLOAD delivery only; the compiled plugin is the transport)
  winfocus.py     foreground-window detection (route nav to the focused app)
  integrations.py 3D-app detect / set up (Fusion/Blender/FreeCAD/SketchUp/Unreal add-on installers; SolidWorks/Onshape/AutoCAD enable)
  tray.py         pystray tray icon + lifecycle
  ui.py           Tkinter settings window
  debugview.py    optional pygame cube (--debug)
  app.py          orchestrator
  __main__.py     entry point
  plugins/fusion360/TrackballNav/   bundled Fusion add-in (.py + .manifest)
  plugins/blender/trackball_nav/    bundled Blender add-on (__init__.py + version.json)
  plugins/freecad/TrackballNav/     bundled FreeCAD add-on (Init.py + InitGui.py + tbnav_*.py + version.json)
  plugins/sketchup/                 bundled SketchUp Ruby extension (loader + trackball_nav/{main,camera,version})
  plugins/unreal/TrackballNav/      bundled Unreal Editor plugin (.uplugin + version.json + Content/Python/{init_unreal.py, trackball_nav.py, tbnav_unreal_camera.py})
  plugins/autocad/                  bundled AutoCAD NETLOAD plugin (TrackballNavAcad.dll + version.json)
archive/
  autocad_com_transport/            RETIRED AutoCAD COM nav transport + overlay + tests (see its README)
```

## Packaging later

- **Nuitka onedir** (preferred; avoids the antivirus issues of PyInstaller onefile):

  ```sh
  python -m nuitka --standalone --enable-plugin=tk-inter \
      --windows-console-mode=disable --include-package=trackball_daemon \
      -m trackball_daemon
  ```

- Or install from GitHub: `pip install git+https://github.com/<you>/<repo>` then run
  `trackball-daemon` (gui-script, no console). End users on the exe path need neither git
  nor Python.

`pygame`/`PyOpenGL` are optional (only `--debug`); the shipping build can omit them.
