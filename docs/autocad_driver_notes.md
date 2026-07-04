# AutoCAD 3D-navigation driver — implementation notes & handoff

This is the maintainer's guide to the **AutoCAD** integration in the Trackball Daemon. It documents
what the driver does, the **verified** COM/ActiveX view model it relies on, and — most importantly —
the **gotchas and solved problems you would not discover by reading the code alone** (§8). Every fact
here was **probed live against a running AutoCAD 2026 (ACAD 25.1s)** on the dev box, not assumed from
docs. **If you only read one section, read §8.**

Primary code: the compiled plugin [`plugin_src/autocad/TrackballNavAcad`](../plugin_src/autocad/TrackballNavAcad)
(`Plugin.cs` + `NavMath.cs`) and the loader [`trackball_daemon/autocad_driver.py`](../trackball_daemon/autocad_driver.py).
Tests: [`tests/test_autocad_loader.py`](../tests/test_autocad_loader.py),
[`tests/test_integrations_autocad.py`](../tests/test_integrations_autocad.py),
[`tests/test_app_routing.py`](../tests/test_app_routing.py). Wiring: `app.py`, `config.py`,
`integrations.py`, `ui.py`, `winfocus.py`. User-facing summary: the AutoCAD section of
[`README_daemon.md`](../README_daemon.md). (Current at daemon `__version__` 0.1.41, plugin 0.3.0.)

---

> **UPDATE (v0.1.41): the COM nav transport is RETIRED — the NETLOAD plugin is the SOLE AutoCAD
> transport.** The bundled plugin (`plugin_src/autocad` → `plugins/autocad/TrackballNavAcad.dll`,
> a first-class add-in in the settings UI with install/update + version tracking) drives the
> viewport's **live GraphicsSystem kernel view** (`ObtainAcGsView` + `SetView`/`Update`, ~1.6
> ms/frame, **zero regens**, free-roll included). The gesture-end DB sync is **also regen-free
> under 3D visual styles** (`SetViewportFromView(regenRequired:false)`); in the **"2D Wireframe"**
> style the 2D pipeline presents from a projected display list, so the commit instead writes the
> `*Active` VPORT record + `UpdateTiledViewportsFromDatabase` and queues a REAL **`_.REGEN`** —
> **one visible regen per gesture** (§8.16 — read the crash taxonomy there before touching it).
> It speaks the normal nav-broker protocol; `app.py` routes autocad frames to the broker
> **unconditionally**. `autocad_driver.py` is now only the **plugin loader** (ROT attach → copy →
> `TRUSTEDPATHS` → NETLOAD, once per AutoCAD session). The old COM transport this doc's §2–§7
> describe is **archived at `archive/autocad_com_transport/`** (§8.18 — why a "fallback" was a
> net negative); those sections plus §8.1–§8.13 remain the verified reference for ANY external
> AutoCAD COM automation. See **§8.15/§8.16** for the plugin transport (and §8.14 for the
> superseded v0.1.x SetCurrentView story — which regenerated per frame after all).

## 1. What it is, in one paragraph

AutoCAD's *smooth* in-process nav path is a **compiled .NET plugin** (`plugin_src/autocad`), and —
important correction to earlier revisions of this doc — **building and shipping one is a PROVEN,
zero-user-friction pattern, not something to avoid**: the pre-built DLL is bundled with the daemon,
auto-copied to a per-user dir, trusted via `TRUSTEDPATHS`, and `NETLOAD`ed silently on attach (§8.14);
in-process it drives the live graphics-kernel view with **zero regens** (§8.15). Do not let older
"avoid compiled add-ins" framing (written when this machine had no compiler and the auto-load recipe
was unknown) steer you away — the only real costs are a dev-time .NET 8 SDK and a per-binary-era
rebuild. COM's one remaining job is **delivery**: `AutoCADPluginLoader` (`autocad_driver.py`)
attaches to a running AutoCAD via `pywin32` and NETLOADs the plugin once per session, degrading
gracefully without `pywin32` (a non-Windows dev box → `start()` logs once and it's a no-op). The
full **COM nav transport** (`AutoCADDriver`) that §2–§7 describe is retired and archived at
`archive/autocad_com_transport/` (§8.18) — kept because its verified view model applies to any
external AutoCAD automation.

The hardest-won knowledge here is **that the `AcadViewport` object is desynced from the displayed
view** (§4/§8.3) and **the reassign commit ritual** (§8.2). The camera math is standard; the friction
is entirely at the COM boundary and in AutoCAD's viewport model. AutoCAD **verticals** (Civil 3D,
Architecture, Mechanical) are all `acad.exe` and expose the same `AutoCAD.Application`, so they work
for free.

---

## 2. Architecture & threading

Mirrors `SolidWorksDriver` exactly (intentionally):

- **`submit(ox,oy,oz,px,py,zoom)`** is called on the **BLE thread**. It only adds a 6-float delta to
  `self._acc` under a lock. It **never blocks and never touches COM** — COM must be used only on the
  thread that `CoInitialize`d it.
- **One worker thread** (`_run`) calls `pythoncom.CoInitialize()`, then loops at the configured rate:
  attach at most every `_RETRY_PERIOD` (2 s) while detached (dropping motion); otherwise atomically
  swap out the accumulated delta and, if non-zero, `_flush(delta)`. Any exception escaping `_flush` is
  treated as "AutoCAD closed" → `_handle_drop()`. `_sleep_remainder` sleeps only the time left in the
  period *after* the COM work, so the cadence stays even. Accumulate-then-flush **coalesces** motion.
- **`on_connection_changed(connected, version)`** fires on attach/drop so the tray/UI status updates.

---

## 3. Attaching to a running AutoCAD (`_attach`, `_find_running_acad`)

We **attach only — never launch** AutoCAD. `_find_running_acad` **enumerates the Running Object
Table**, normalizes each dispatch to its `.Application` (both `AcadApplication` *and* `AcadDocument`
expose `.Application`), keeps the ones whose `.Name == "AutoCAD"`, and picks the instance with the most
open documents — falling back to `GetActiveObject("AutoCAD.Application")`. **Why the ROT and not just
`GetActiveObject`:** `GetActiveObject` was observed returning **`-2147221021` "Operation unavailable"**
even while AutoCAD was running and the ROT listed it (§8.1). On success it caches `acad.Version` (e.g.
`"25.1s (LMS Tech)"` — **not** `RevisionNumber`, that's a SolidWorks property) and fires the connected
callback.

`detect_autocad()` globs `%ProgramFiles%\Autodesk\AutoCAD*\acad.exe`; `setup_autocad()` only verifies
AutoCAD + `pywin32` are present and flips the app to enabled/installed — **there is nothing to copy or
register** (`addin_version` is always `""`), exactly like SolidWorks.

---

## 4. The verified view model (the math foundation — measured live, trust this)

AutoCAD's 3D view is driven through the **active viewport**, but with a crucial split between
**reading** and **writing**:

### Read the live view from **system variables**, never from the viewport object
`doc.ActiveViewport` returns a **clone that is desynced from the displayed view** — its
`Direction`/`Target`/`Center`/`Height` read stale *defaults* (verified live: after `SWISO`+`ZOOM E`
the sysvar `VIEWDIR` was `(-1,-1,1)` but the clone's `Direction` read `(0,0,1)` and its `Height` `9.0`
vs `VIEWSIZE` `9.877`). The ground truth is the sysvars (`doc.GetVariable(...)`):

| Sysvar | Type | Meaning |
|---|---|---|
| `VIEWDIR` | 3 doubles | View direction, **un-normalized**, from target→camera (points *out of the screen*). Normalize before use. |
| `TARGET` | 3 doubles | The look-at point (WCS). |
| `VIEWSIZE` | double | View height in drawing units — **equals `AcadViewport.Height`**; smaller = zoomed in. |
| `EXTMIN` / `EXTMAX` | 3 doubles each | Drawing extents → object-pivot / zoom-to-object centre. |
| `ACTIVESPACE` (property `doc.ActiveSpace`) | int | `1` == model space (`acModelSpace`); paper space == `0`. Guard on this. |

### Write the view — orbit reassigns (regens), pan/zoom use the Zoom methods (no regen)
Only the **view direction** has no smooth Application method, so **only orbit uses the reassign**, and
the reassign **REGENs** (~50 ms/frame vs ~3 ms for the Zoom methods — measured; §8.4). Everything else
goes through the regen-free `ZoomScaled`/`ZoomCenter` methods.

**Orbit** (change Direction) — the reassign commit ritual, then a `ZoomCenter` fix-up:
```python
vp = doc.ActiveViewport
vp.Direction = _arr(new_dir)     # SAFEARRAY VARIANT (VT_ARRAY|VT_R8), 3 doubles (plain tuple → 0x80020009)
vp.Target    = _arr(pivot)       # rotate about the pivot
doc.ActiveViewport = vp          # <-- THE COMMIT (regens; acad.Update() ALONE does nothing)
acad.ZoomCenter(_arr(pivot), float(view_size))   # re-centre the pivot + restore VIEWSIZE
```
`acad.Update()` alone does nothing — the reassignment is the commit (verified: setting Direction +
`Update()` with no reassign left `VIEWDIR` unchanged). The reassign leaves the **framing wrong**
(VIEWCTR/VIEWSIZE take the desynced clone's stale values), so the trailing `ZoomCenter(pivot, size)`
fixes both cleanly. **No `acad.Update()`** — `ZoomCenter` repaints on its own; an extra Update is a
redundant repaint.

**NEVER set the `AcadViewport.Center` property.** It is treacherous: setting `Center=(0,0)` (or any
value) while the geometry is away from the WCS origin **destroys the framing** — verified live it
ballooned `VIEWSIZE` **27 → 118**. The `ZoomCenter` fix-up above is the correct way to re-centre; it
never touches `Center`. (An earlier design set `Center=(0,0)` every frame and "worked" only because
the test box sat exactly at the origin — see §8.3.)

**Pan and zoom** — the smooth Application zoom methods, **no reassign, no regen** (~3 ms):
```python
pan          →  acad.ZoomCenter(newCentreVARIANT, VIEWSIZE)          # move what's centred
zoom to_ctr  →  acad.ZoomScaled(factor, acZoomScaledRelative=1)      # factor>1 zooms IN, VIEWSIZE/=f
zoom to_obj  →  acad.ZoomCenter(bboxCentreVARIANT, VIEWSIZE/factor)
```
`factor > 1` zooms **in** (verified: `ZoomScaled(2, 1)` halved `VIEWSIZE`). `acZoomScaledRelative` is
**1** (hard-coded — `win32com.client.constants` is empty under late-bound dispatch). `ZoomScaled`
zooms about `VIEWCTR` and does **not** move it (verified); `ZoomCenter` sets `VIEWCTR` and `VIEWSIZE`.

### The unified "keep one 3D point centred" model
We track a single 3D WCS point **`_center`** (the pivot kept at the screen centre), seeded from
`TARGET` on the first read (TARGET is on the optical axis, so `ZoomCenter(TARGET)` doesn't shift the
view) and carried across every `ZoomCenter` write. All ops keep the **same** point centred so they
compose without jumps:

- **Orbit** — rotate `Direction`, then `ZoomCenter(pivot, VIEWSIZE)`; `pivot` = the scheme's pivot
  (origin / extents-centre / tracked `_center`), which becomes the new `_center`.
- **Pan** — `ZoomCenter(_center + view-plane delta, VIEWSIZE)`; the shifted point becomes `_center`.
  (No reassign, so pan is regen-free; and because it moves `_center`, a following orbit keeps the
  panned point centred.)
- **Zoom** — `to_center` = `ZoomScaled` about `VIEWCTR` (== `_center`, since the last op ZoomCentered
  it there); `to_object` = `ZoomCenter(extents-centre)`, which becomes `_center`. `_size` is tracked
  as `VIEWSIZE /= factor`.

An earlier design mixed `Center=(0,0)`-reassign centring with `ZoomScaled` (VIEWCTR-centred) zoom; the
two disagreed and the reassign **snapped** the view ~40 units on the zoom→orbit transition. Routing
all centring through `_center`/`ZoomCenter` fixed that (verified live: the transition jump fell to
~0.2 units).

Camera basis (in `_camera_basis`): with `vd = normalize(VIEWDIR)` (out of screen),
`right = normalize(WORLD_UP × vd)`, `up = normalize(vd × right)`, `forward = −vd`. **`WORLD_UP =
(0,0,1)`** — AutoCAD's WCS is **Z-up** (verified: yawing `VIEWDIR` about `(0,0,1)` keeps verticals
vertical and never tumbles). A top/bottom view (`vd ∥ WORLD_UP`) is degenerate → `right` falls back to
world +X.

---

## 5. The per-frame camera ops (`_flush`)

Each non-empty frame, on the worker thread:

1. `_live(acad)` returns the cached `ActiveDocument`, re-validating at most every `_VIEW_TTL` (1 s).
   The re-validate is the **liveness probe** (`acad.Documents.Count` raises if the app is gone → `_run`
   drop), **skips when no drawing is open** (Start tab — `Count == 0` → idle, *not* a drop), records
   `doc.ActiveSpace == acModelSpace`, **resyncs** the tracked `VIEWDIR`/`VIEWSIZE` against any
   mouse-driven view change, and **seeds `_center` from `VIEWCTR`** on the first read (§8.12).
2. **Guard**: no drawing, or not model space, or view state unavailable → no-op.
3. **Orbit** (`_apply_orbit` = reassign(Direction+Target+Height) + `ZoomCenter`) applies **every frame
   by default** (continuous movement). The reassign regens (§8.10) — inherent, unavoidable over COM.
   The opt-in `_ORBIT_DEFER` mode instead accumulates into `_pending_orbit` and applies one reassign
   when the gesture pauses (`_flush_idle` on no-input cycles, once idle ≥ `_ORBIT_IDLE`; max-defer cap
   for a sustained roll) — no per-frame flicker but choppier.
4. **Pan/zoom are immediate** (`_apply_pan`/`_apply_zoom` via `ZoomCenter`/`ZoomScaled` — no regen);
   each first flushes any pending orbit so the view is consistent. Each op is in its own `try/except`
   (`_warn_once`, §8.5). **No `acad.Update()`** — the Zoom* writes repaint on their own.

**Performance:** the **orbit reassign REGENs / flickers** (~34 ms on a 108-solid drawing, unaffected by
REGENMODE — §8.10) and there is no redraw-only rotation over COM, so orbit can't be made
native-smooth; it runs per frame by default (continuous, flickery) or deferred (choppy, flicker-free).
Pan/zoom via the Zoom methods are ~3 ms (redraw, no regen — smooth). The worker **caches the doc handle
and tracks `_dir/_size/_center` across its own writes**, re-reading direction+size only every
`_VIEW_TTL` (which doubles as the liveness probe and the mouse-resync) — the SolidWorks perf trick.

---

## 6. Orbit pivots & styles (the control scheme — `_apply_orbit`, `_pivot_point`)

`set_scheme(orbit_pivot, orbit_style, zoom_mode)` is pushed from `app._apply_schemes()`. Pivots (the
3D point we `ZoomCenter` on each orbit frame):

- **`origin`** — the WCS origin `(0,0,0)`.
- **`object`** (and **`cursor`**, which falls back to it) — the **drawing-extents centre**
  (`EXTMIN`/`EXTMAX` midpoint, cached ~0.5 s), falling back to the tracked `_center` if extents are
  unavailable.
- **`view`** (the general default) — the tracked **`_center`** (the point currently screen-centred).
  AutoCAD has **no COM screen-centre raycast**, so unlike SolidWorks/Fusion/Onshape there is no true
  "surface under the crosshair" pivot; `view` orbits about whatever is centred. (See §8.6.)
- **`pointer`** — TRUE under-the-mouse orbit, but **only in the NETLOAD plugin** (v0.3.0+, §8.17: an
  in-process `Editor.PointMonitor` caches the cursor point; `to_pointer` zoom rides the same cache).
  This COM fallback has no cursor hit-test, so here `pointer` degrades to the extents centre like
  `cursor` — in practice the plugin owns the frames whenever it's loaded, so the fallback path is
  rarely what the user feels.

Orbit **style**: `free` rotates about the composed camera axis (`right·vx + up·vy + forward·vz`);
`turntable` yaws about `WORLD_UP` + pitches about camera-right (**roll dropped**), composed into one
rotation via quaternion. **AutoCAD auto-levels the up to world Z** and `VIEWTWIST` is **not settable**
via `SetVariable`, so **free-roll is limited/approximate** — turntable is the natural fit (§8.4). Both
rotate `VIEWDIR` by one Rodrigues rotation, then `ZoomCenter(pivot, VIEWSIZE)` keeps the pivot centred.

Pan (`_apply_pan`): `ZoomCenter` to `_center + PAN_SIGN * delta * PAN_SCALE * VIEWSIZE` along camera
right/up (so pan feels constant at any zoom; no reassign → no regen). Zoom (`_apply_zoom`):
`factor = 1 + ZOOM_SIGN * zoom * ZOOM_SCALE`, guarded `> 0`; `_size` tracked as `VIEWSIZE /= factor`.

---

## 7. Tuning knobs (top of `autocad_driver.py`)

Defaults were sanity-checked live (all four ops move the view correctly) but the **feel** is a
per-hardware pass; **flip a sign if a channel goes the wrong way** (HANDOFF §12.5). Drawing units vary
wildly, so `PAN_SCALE`/`ZOOM_SCALE` are strong live-tune items.

| Constant | Value | Meaning |
|---|---|---|
| `ORBIT_SIGN` | `(1,1,1)` | per-channel orbit direction (pitch about right / yaw about up / roll about forward). Baseline is the **opposite** of the SolidWorks driver's because we rotate the *view direction* (camera), not the model. |
| `WORLD_UP` | `(0,0,1)` | turntable azimuth axis — AutoCAD WCS is **Z-up** (verified). |
| `PAN_SIGN` / `PAN_SCALE` | `(1,-1)` / `0.5` | pan direction / magnitude (fraction of `VIEWSIZE`). |
| `ZOOM_SIGN` / `ZOOM_SCALE` | `1.0` / `0.5` | zoom direction / per-frame aggressiveness (`factor>1` zooms in). |
| `AC_MODEL_SPACE` | `1` | `acModelSpace` (guard: drive only model space). |
| `AC_ZOOM_SCALED_RELATIVE` | `1` | `acZoomScaledRelative` for `ZoomScaled` (verified). |
| `DEFAULT_FLUSH_HZ` | `30` | flush/refresh rate (per-app `rate_hz`; `0` ⇒ global `bridge.rate_hz`). Realistic AutoCAD ceiling ≈ 20 Hz (§5). |
| `_VIEW_TTL` | `1.0` | doc/view-state revalidation period (also the liveness probe + mouse-resync). |
| `_OBJ_CACHE_TTL` | `0.5` | drawing-extents cache lifetime. |
| `_RETRY_PERIOD` | `2.0` | seconds between attach attempts while AutoCAD isn't running. |

Per-app sensitivity / invert / scheme and the viewport rate come from config (Per-App Bindings → the
`autocad` app), same as the other apps.

---

## 8. GOTCHAS & SOLVED PROBLEMS (read this)

Each is *symptom → cause → fix*. All found by **live probing** against AutoCAD 2026.

### 8.1 `GetActiveObject` can be "Operation unavailable" → enumerate the ROT
- **Symptom:** `win32com.client.GetActiveObject("AutoCAD.Application")` raises `-2147221021`
  ("Operation unavailable") even though `acad.exe` is running and a drawing is open.
- **Cause:** AutoCAD's ROT registration / marshaling isn't always reachable through the class moniker
  `GetActiveObject` uses (observed intermittently; also happens when AutoCAD is momentarily busy).
- **Fix:** `_find_running_acad` enumerates the Running Object Table, normalizes each dispatch to its
  `.Application`, filters by `.Name == "AutoCAD"`, and picks the one with the most `Documents`. This is
  the same ROT-first pattern SolidWorks uses, and it's the reliable attach.

### 8.2 The reassign commit ritual — nothing repaints without `doc.ActiveViewport = vp`
- **Symptom:** you set `vp.Direction`/`Target` and call `acad.Update()`, but the view doesn't move; the
  sysvars don't change either.
- **Cause:** `doc.ActiveViewport` hands you a **working copy**. Mutating it is inert until it's
  reassigned back to the document. `Update()` alone repaints the *current* (unchanged) view.
- **Fix:** after setting the properties, **`doc.ActiveViewport = vp`** (the commit). Verified: reassign
  moved `VIEWDIR`; `Update`-without-reassign did not. **The reassign REGENs** (§8.10), so only *orbit*
  (which must change Direction) uses it; pan/zoom use the regen-free Zoom methods. Because the reassign
  leaves the framing wrong (§8.3), orbit follows it with `ZoomCenter(pivot, VIEWSIZE)` (which also
  supplies the repaint — no `acad.Update()` needed).

### 8.3 The `AcadViewport` clone is desynced — read the view from SYSVARS, and NEVER set `.Center`
- **Symptom:** `doc.ActiveViewport.Direction`/`Target`/`Center`/`Height` don't match what's on screen
  (they read defaults like `Direction=(0,0,1)`, `Height=9.0`), so orbiting "from" them jumps the view.
- **Cause:** the model-space active-viewport object's cached geometry is **not** kept in sync with the
  displayed view (updated by 3DORBIT/DVIEW/zoom sysvars, not the object).
- **Fix:** **never read the current view from the viewport object.** Read `VIEWDIR`/`TARGET`/`VIEWSIZE`
  via `doc.GetVariable`, and only *write* through it. The reassign applies the **whole** object, so the
  fields you don't set revert to the stale clone defaults (setting only Direction clobbered
  VIEWCTR/VIEWSIZE). **But do NOT "fix" that by setting `Center`** — see §8.9, it's destructive.
  Instead, orbit sets Direction+Target, reassigns, then re-frames with `ZoomCenter(pivot, VIEWSIZE)`,
  which restores VIEWCTR **and** VIEWSIZE cleanly.

### 8.4 AutoCAD auto-levels the up; `VIEWTWIST` isn't settable → free-roll is limited
- **Symptom:** the roll channel (`oz`) has no effect; "free" orbit behaves like turntable (verticals
  re-level themselves).
- **Cause:** setting a new `Direction` makes AutoCAD **auto-level the up to world Z** (verified:
  `VIEWTWIST` stayed 0 across orbits). The only screen-twist lever is the `VIEWTWIST` sysvar, and
  `SetVariable("VIEWTWIST", …)` **raises "Error setting system variable"** (it's read-only via COM;
  set only by DVIEW/-VIEW/plan).
- **Fix:** **drop the roll channel** and document it. Turntable (yaw about world Z + pitch about
  camera-right) is the natural AutoCAD fit and is what `view/free` effectively become for direction.
  A true roll would need a `SendCommand('DVIEW … TWist …')` per frame — flickery, not worth it.

### 8.5 Per-op guards: one failing COM call must not blank the viewport
- Each op (`orbit`, `pan`, `zoom`) is caught, logged **once** via `_warn_once`, and skipped — the
  others still run. **Only the app-alive probe (`acad.Documents.Count`) raising causes a disconnect**
  (that genuinely means AutoCAD closed). A transient pywin32 "Property … can not be set" on `Direction`
  was observed **once**, immediately after a *failed* `SetVariable` left the dispatch in an error state
  (§8.4) — the driver never calls `SetVariable` on the view, and even if it recurred the guard logs it
  once and the next frame recovers.

### 8.6 No COM screen-centre raycast → `view`/`cursor` pivots are not true surface pivots
- Unlike SolidWorks (`SelectByRay`), Fusion (`findBRepUsingRay`), Onshape (navlib `hit.lookat`),
  Blender (`scene.ray_cast`) and FreeCAD (`getObjectInfo`), **AutoCAD exposes no cheap COM screen-centre
  pick** for the model-space viewport. So `view` orbits about the current **Target** (AutoCAD's native
  target orbit), and `cursor` falls back to **object** (extents centre). The trackball pipeline is
  relative anyway (no cursor pixel to unproject), so this matches the cross-app `cursor` limitation
  (HANDOFF §14).

### 8.7 A freshly `Documents.Add()`ed doc mis-resolves properties → re-fetch `ActiveDocument`
- **Symptom (probe/testing only):** the object returned by `acad.Documents.Add()` raises
  `AttributeError: Add.ActiveSpace` (pywin32 suggests `ActiveLayer`) on property access.
- **Cause:** late-bound dispatch doesn't populate the member map for the `Add()`-returned dispatch.
- **Fix:** after `Documents.Add()`, use `acad.ActiveDocument` (the new drawing becomes active) — that
  dispatch resolves properties correctly. The driver never creates drawings, but the probe scripts and
  anyone writing a live test must know this.

### 8.8 No add-in, by design
- AutoCAD's smooth path is a **.NET/ObjectARX plugin** (per-version build + `NETLOAD`). We deliberately
  avoid it, exactly like the admin-registered SolidWorks add-in. Everything is the in-process COM
  driver: nothing to install, copy, or auto-update (`autocad` is **not** in `integrations._ADDINS`, so
  `auto_update` never touches it; asserted in `tests/test_integrations_autocad.py`). The trade-off is
  the out-of-process COM ceiling + the orbit regen (§8.10) — an ObjectARX plugin would be flicker-free
  (3Dconnexion's own AutoCAD SpaceMouse support is exactly such a plugin).

### 8.9 `AcadViewport.Center` is destructive — the zoom→orbit "jump" saga
- **Symptom (reported from real use):** after a zoom gesture the model **jumps sideways** (a large
  instantaneous pan, no input), worse when zoomed in; and the model sits zoomed-out/oscillating.
- **Cause:** an earlier design set `vp.Center = (0,0)` on every orbit/pan commit to "keep the pivot
  centred." That is a **DCS coordinate, not a target-relative offset**: with the geometry away from the
  WCS origin, `Center=(0,0)` re-frames drastically — verified live it **ballooned `VIEWSIZE` 27 → 118**
  and threw `VIEWCTR` far off (even with `Height` explicitly set). It only "worked" in the initial live
  test because that throwaway box sat exactly at the origin. Compounding it, zoom used `ZoomScaled`
  (which centres on `VIEWCTR`) while the reassign forced `Center=(0,0)` — the two disagreed, so the
  first orbit after a zoom **snapped** the view (~40 units, measured).
- **Fix:** **never set `Center`.** Re-centre with `ZoomCenter(pivot, VIEWSIZE)` after the orbit
  reassign, and route pan+zoom through `ZoomCenter`/`ZoomScaled` too, all sharing one tracked 3D pivot
  (`_center`). Verified live on off-origin geometry: `VIEWSIZE` stays put and the zoom→orbit jump fell
  from ~40 to **~0.2**.

### 8.10 The orbit (Direction) reassign REGENs — and the regen can't be separated from the rotation
- **Symptom (reported from real use):** the model **regenerates and flickers every time the camera
  moves**; a user asked to keep the *view moving* (grid/ViewCube redraw smoothly) while throttling only
  the *regen* — as AutoCAD's own interactive orbit does.
- **Why that isn't achievable over COM (all verified live):**
  - The only way to change the view **direction** over COM is `doc.ActiveViewport = vp` (the reassign),
    and it does a full regen/repaint every time (~34 ms on a 108-solid drawing).
  - **`REGENMODE = 0` (REGENAUTO off) does NOT help** — the reassign was **34.2 ms with it off vs
    34.4 ms on** (an explicit `doc.Regen` of the same drawing is only **~10 ms**, so the regen isn't
    even the bulk of it — the reassign's marshaling + non-buffered repaint is). REGENMODE off cannot
    turn the reassign into a redraw.
  - The view sysvars that would sidestep the reassign (`VIEWDIR`/`TARGET`/`VIEWCTR`/`VIEWSIZE`) are
    **read-only** via `SetVariable` (like `VIEWTWIST`, §8.4), and there is **no redraw-only
    view-rotation** exposed to COM. Smooth "redraw the model, throttle the regen" needs the ObjectARX
    `AcGsView` graphics-system API — a compiled plugin, which is exactly why 3Dconnexion ships one.
- **What we do:** pan and zoom stay **off** the reassign (`ZoomCenter`/`ZoomScaled`, ~3 ms, redraw —
  smooth). For orbit, two modes chosen by the `_ORBIT_DEFER` constant:
  - **`True` (default)** — accumulate and apply **one** reassign when the gesture pauses (`_flush_idle`,
    idle `_ORBIT_IDLE` ≈ 0.1 s; max-defer `_ORBIT_MAX_DEFER` ≈ 0.3 s during a sustained roll): no
    per-frame flicker, and the **rotating-cube overlay** (§8.13) gives continuous visual feedback while
    the drawing waits.
  - **`False`** — apply **every frame**: continuous viewport movement, but each frame regens/flickers.
    Lower the app's **Viewport refresh rate** to trade flicker-frequency for smoothness.
  Neither is native-smooth; the only *fully* smooth orbit is the ObjectARX route (§8.8).

### 8.11 The orbit "flash of default zoom" — set `Height` in the reassign
- **Symptom (reported from real use):** orbiting at a non-default zoom **flashes the model at the
  default zoom** for a frame before snapping back to the set zoom.
- **Cause:** the reassign's intermediate repaint used the desynced clone's **default `Height`** (the
  trailing `ZoomCenter` only fixed it *after* that repaint), so each reassign flashed the wrong zoom.
- **Fix:** set `vp.Height = VIEWSIZE` **in** the reassign so its intermediate frame is already the
  right zoom. `Height` is safe to set (verified live: no blowup — unlike `Center`, §8.9). This removes
  the zoom-flash from every reassign (independent of the per-frame vs deferred choice, §8.10).

### 8.12 Seed the orbit pivot from `VIEWCTR`, not `TARGET`
- **Symptom:** after a **Zoom Extents**, the first orbit could swing the model off-centre / around
  empty space.
- **Cause:** the pivot was seeded from `TARGET`, but a Zoom Extents leaves `TARGET` wherever it was
  (verified live: box at (50,50,10) but `TARGET=(0,0,0)`, `VIEWCTR=(60,60,0)`) — so `ZoomCenter(TARGET)`
  centred empty space, not the geometry.
- **Fix:** seed the tracked pivot `_center` from **`VIEWCTR`** (the point actually at the screen
  centre), which is always on the optical axis, so `ZoomCenter(_center)` never shifts the view. (Both
  are read from sysvars; the earlier "TARGET is on the optical axis" reasoning held only *after* an
  orbit had set `Target`, not after a fresh fit.)

### 8.13 The second look: the WHOLE API was swept — and the overlay is the gap-closer
On a direct "ignore prior conclusions" challenge, the **entire type library** (461 types, enumerated
from the running instance's `ITypeInfo`) was dumped and every fresh lead probed live:
- **`IAcadViewport.SetView(view)`** — the one untried view-setter. Verified: **inert until the same
  `doc.ActiveViewport = vp` reassign** (VIEWDIR unchanged after `SetView` alone), then the same regen
  pipeline at ~27 ms/op (vs ~34 — marginally fewer round-trips, same flash/regen). Not an unlock.
- **`IAcadApplication.Eval`** — raises *"Problem in loading VBA"* (the VBA enabler isn't installed);
  and even with VBA it only reaches the same object model.
- **`IAcadDocument.PostCommand`** — exists and works (async `SendCommand`); the only command-line
  rotates (`VPOINT`/`DVIEW`/`PLAN`) regen by definition.
- Every other `Rotate*`/`Rotation` member in the library is **entity** rotation (`IAcad3DSolid.Rotate3D`
  etc.), not the view. **No Orbit or graphics-system member exists anywhere.**
- In-process load vectors DO exist — **`LoadArx`** (load an ObjectARX module via COM!) and
  **`GetInterfaceObject`** (instantiate a registered COM server inside acad.exe, HKCU registration
  needs no admin) — but both load a **compiled DLL**, and at the time of this sweep the machine had
  no toolchain. **UPDATE: this route is now OPEN and SHIPPED** — the .NET 8 SDK was installed (one
  winget command), the NETLOAD vector became the bundled plugin (§8.14), and the graphics-kernel
  view it unlocked delivers the true regen-free orbit (§8.15). Treat "needs a compiled DLL" as a
  build step, not a wall.
- **Verdict:** the reassign-regen is confirmed as the floor from every direction. The gap-closer is the
  **overlay** (`acad_overlay.py`, now at `archive/autocad_com_transport/`): in defer mode a small
  **click-through wireframe cube + RGB axis tripod** (X red / Y green / Z blue, like the UCS icon)
  floats centred on the AutoCAD window (`acad.HWND`) and rotates with the **pending** orbit — live
  orientation feedback with **zero AutoCAD/COM traffic** — then hides when the real view snaps at
  gesture end. Implementation: ctypes-only Win32 layered window (`WS_EX_LAYERED|TRANSPARENT|
  NOACTIVATE|TOOLWINDOW|TOPMOST`, colour-keyed, GDI into a memory bitmap), owned by the driver's
  worker thread; one failure disables it for the session (`_warn_once("overlay")`), never the driving.
  Knobs: `ORBIT_OVERLAY` (driver) and `OrbitOverlay(size/alpha/margin)`.
  **Verification note:** this session's screenshot/BitBlt(+CAPTUREBLT) captures are blind to this
  process's windows, so the overlay was verified by parts: projection pixels correct in the memory DC,
  the blitted window surface correct via `GetPixel(GetDC(hwnd))`, and the window visible/positioned/
  hit-testable via `WindowFromPoint` — everything short of photographing the glass.

### 8.14 The NETLOAD plugin, v0.1.x: in-process `SetCurrentView` (superseded by §8.15)
The user authorized a compiled plugin (the .NET 8 SDK was installed for it), which removed the COM
ceiling. The v0.1.x plugin applied every frame with `Editor.SetCurrentView(ViewTableRecord)`.
- **CORRECTION (see §8.15):** this section originally claimed SetCurrentView was "~0.4 ms, no
  regen". A `DrawableOverrule` `WorldDraw` counter later proved **it regens EVERY call** (~5–7
  ms/frame with real entities; the 0.4 ms figure came from a near-empty context where a regen is
  trivially cheap). The user caught this from the viewport: "the model still regenerates, it's just
  that the window appears to update at a higher refresh now" — exactly right. What stays true:
  works in every visual style, DB continuously in sync, `ViewTableRecord.ViewTwist` **writable**
  (free-roll restored — the COM driver had to drop it, §8.4). This path is now the plugin's
  FALLBACK (paper space / GS failure).
- **`Manager.GetCurrentAcGsView(vpn)` is a TRAP in 2D-wireframe viewports** (probed first):
  it hands back a **default camera** (pos (0,0,0) → tgt (0,0,1)), NOT the live view — orbiting it
  does nothing, committing it **clobbers the real view**, and `SetViewFromViewport` cannot even
  seed it (verified: camera unchanged after seeding). `GetCurrent3dAcGsView` returns **null** in
  2D wireframe. But this is a fact about THOSE ACCESSORS, not about the graphics system — the
  kernel-descriptor accessor returns the real live view (§8.15).
- **Plugin architecture** ([`plugin_src/autocad/TrackballNavAcad`](../plugin_src/autocad/TrackballNavAcad)):
  a .NET 8 class library referencing the INSTALLED AutoCAD's `acmgd/acdbmgd/accoremgd` (no ObjectARX
  SDK). Background socket thread speaks the standard nav-broker protocol (hello + o/p/z/op/os/zm
  frames, like Fusion/Blender/FreeCAD/Unreal); a WinForms 10 ms timer on the UI thread drains and
  applies (the FreeCAD socket+QTimer pattern). A `GetForegroundWindow` gate drops broadcast frames
  meant for other apps. Commands: `TBNAV` (status), `TBNAVTEST` (synthetic-orbit self-test through
  the real pipeline — how this was verified headless). Log: `%APPDATA%\TrackballDaemon\acad_plugin.log`.
- **Loading (zero user friction):** the plugin loader auto-NETLOADs once per AutoCAD session
  (`_netload_plugin`): **copy** the bundled DLL to `%APPDATA%\TrackballDaemon\acad_plugin\` (NEVER
  load the bundled path — AutoCAD **locks the loaded file all session**; a rebuild couldn't
  overwrite it, learned the hard way), **append that dir to `TRUSTEDPATHS`** (a writable per-user
  sysvar — verified; SECURELOAD=1 then loads silently, no prompt), then
  `SendCommand('(command "_.NETLOAD" "<path>")(princ) ')` (the LISP form: no file dialog). The
  plugin connects to the broker and `app.py` routes autocad frames there **unconditionally**
  (the COM fallback is retired — §8.18).
- **Rebuild/iterate gotchas:** .NET assemblies **cannot be reloaded** in a running AutoCAD — new build
  = restart AutoCAD. A fresh `Documents.Add()`-style probe pattern still applies. Build:
  `dotnet build -c Release plugin_src/autocad/TrackballNavAcad` (auto-copies into
  `trackball_daemon/plugins/autocad/`). AutoCAD 2025–2027 share the .NET 8 binary-compatible family;
  a future compatibility break means one rebuild. 2024-and-older (.NET Framework) would need a
  separate build.

### 8.15 The GS transport (plugin v0.2.0): ObtainAcGsView = the LIVE kernel view, ZERO regens (shipped)
Triggered by the user's (correct) report that v0.1.x still regenerated per frame. Everything below
was measured **live in the user's own session** (Drawing3.dwg, 2 solids) with a
`DrawableOverrule` on `Entity` counting `WorldDraw` calls — the authoritative regen detector
(entities only WorldDraw when their graphics are rebuilt from the DB; camera-only repaints
re-render the kernel's cached scene graph and touch nothing). Probe assemblies:
`scratchpad/probe_regen{2,3,4}` (TbProbeGs/2/3 — one assembly name per iteration, they never
unload).
- **Measured (60-frame orbit sweeps):** `SetCurrentView` per frame → **WD = 2/frame (= a full
  regen), ~5–7 ms**. `REGENMODE=0` changes nothing in-process either. Direct
  `ViewportTableRecord` write + `Editor.UpdateTiledViewportsFromDatabase()` → same 2 WD/frame and
  ~80 ms/op (worst of all worlds).
- **The unlock:** `doc.GraphicsManager.ObtainAcGsView(vpn, KernelDescriptor)` with
  `desc.addRequirement(Autodesk.AutoCAD.UniqueString.Intern("3D Drawing"))` returns **the real
  on-screen view** — camera EXACTLY matches VIEWDIR/VIEWSIZE (fw/fh = Width/Height to 4 dp).
  Modern AutoCAD renders every visual style **including "2D Wireframe" through the 3D kernel**
  ("2D Drawing" descriptor → NULL: the legacy 2D kernel isn't even in use). This is the
  ObjectARX `acgsObtainAcGsView` — the accessor native 3DORBIT uses; `GetCurrentAcGsView` is the
  legacy trap (§8.14).
- **Driving it:** seed `SetViewFromViewport(view, vpn)` (works on THIS view), optional
  `BeginInteractivity(hz)`, then per frame `view.SetView(pos, tgt, up, fw, fh, Projection)` +
  `view.Update()` → **~1.6 ms/frame, WD = 0, VD = 0 over 150 frames** — a true regen-free orbit.
  The DB stays untouched during the gesture (VIEWDIR stale by design).
- **The commit is ALSO regen-free — in 3D visual styles:** at gesture end (180 ms frame silence),
  `SetViewportFromView(vpn, view, regenRequired:false, rescaleRequired:false, syncRequired:true)`
  inside `doc.LockDocument()` → **WD = 0** and the DB lands EXACTLY on the driven camera
  (VIEWDIR dot = 1.000000, VIEWSIZE/TARGET err 0.00%). **Free-roll rides in the up vector and
  syncs into DB `VIEWTWIST` on commit** (pure-roll probe: Δtwist = +0.3000 for Σoz = +0.30,
  VIEWDIR unchanged). **CAVEAT (found by the user, §8.16): in the "2D Wireframe" style this
  commit updates every camera record but NOT the 2D pipeline's projected display list — the
  screen snaps back to the old view on the next repaint. There the commit must go through the
  classic `SetCurrentView` (one regen per gesture).**
- **Plugin v0.2.0 architecture:** `NavMath.cs` = pure camera math on a `CamState`
  (pos/tgt/up/fw/fh/persp — orbit about target, turntable relevel via `WorldUp×dir`, free-orbit
  composed axis incl. roll, pan shifts target+pos, zoom scales fw/fh or dollies when
  perspective). The probe (`TbProbeGs3`) **compiles NavMath.cs verbatim** from the plugin tree
  and ran the production gesture live — the shipped math is the verified math. Gesture lifecycle
  in `Plugin.cs`: obtain+seed+BeginInteractivity on first frame → drive per frame → idle-commit
  (`CommitIdleMs=180`); commit early on doc/viewport switch and on `Terminate`; any GS exception
  → `s_gsBroken` → per-frame `SetCurrentView` fallback (v0.1.x behavior). Paper space
  (`!db.TileMode` or CVPORT<2) uses the fallback. `TBNAV` reports which transport is active.
- **Instrument gotcha:** GDI `GetPixel` (window DC) AND `PrintWindow(PW_RENDERFULLCONTENT)` are
  both blind/unreliable against AutoCAD's GPU-composited drawing area — pixel sampling could not
  verify screen motion (control run: 0/25 pixels changed during a KNOWN-visible rotation). The
  WorldDraw counter + DB round-trip numbers are the evidence that matters; visual smoothness is
  confirmed by eye.

### 8.16 The mouse-move snap-back (user-reported on v0.2.0) and the v0.2.1 hardening
User report: after a trackball gesture, moving the mouse resets the view to the pre-gesture camera.
An extensive live reproduction campaign could NOT trigger it synthetically — all of these **kept the
committed camera** (dot = 1.000000): cursor movement via `SetCursorPos` AND via real `SendInput`
(during the drive, during the idle window, post-commit, AutoCAD foregrounded), wheel-zoom after an
orbit, wheel-zoom after a **pan** (VIEWCTR jump = 0.000 — `SetViewportFromView` moves `Target` and
keeps `CenterPoint` (0,0), so the §8.6 stale-centre trap does not fire), MMB-pan drag, ViewCube
hover, empty-click + ESC. One residual smell: the DB `*Active` VPORT record read via LISP
`(tblsearch "VPORT" "*Active")` group 16 sat ~10° behind the live camera after a commit — real but
apparently harmless in every native path tested.

v0.2.0 did, however, contain two REAL holes consistent with the symptom, both closed in **v0.2.1**:
1. **Per-tick `NavMath.Read(_gsView)`**: anything that externally re-synced the GS view mid-gesture
   (AutoCAD re-seeding it from the then-stale DB) would contaminate every subsequent frame — the
   gesture would visibly restart from the old camera. Fix: a **shadow `CamState`** seeded once per
   gesture; every tick applies to the shadow and re-imposes it (external resets self-heal in ≤10 ms,
   and a `gs-clobber` diagnostic line is logged once if that ever happens — check
   `acad_plugin.log` when investigating).
2. **Committing the view object as-is**: a clobber landing inside the 180 ms idle window would get
   COMMITTED (the old camera — persistently, exactly the reported symptom). Fix: the commit writes
   the shadow back first, then `SetViewportFromView(...regenRequired:false...)`, then
   belt-and-braces `ed.SetCurrentView(ed.GetCurrentView())` + `ed.UpdateTiledViewportsInDatabase()`
   — the editor-level current view, the GS view, and the DB VPORT records all end in agreement.
   **The whole ritual measured ZERO WorldDraws live** (the "old system" `SetCurrentView` costs no
   regen when the DB already holds the same camera — probed as "recipe 3", commit WD=0).

**RESOLUTION (v0.2.2) — the user found the real trigger: the "2D Wireframe" visual style.** Every
reproduction above ran blind to it because the verdicts read camera state (VIEWDIR/GS camera),
which the commit DOES update correctly. What it does not update in 2D wireframe is the **2D
pipeline's PROJECTED display list**: that style presents from a view-dependent 2D cache, our
driven '3D Drawing' kernel view is only the *interaction* view there (exactly like native
3DORBIT's — which is also why the HUD/ViewCube vanish mid-gesture and why AutoCAD's "fast mode"
badge flips: the 2D fast pipeline is bypassed while the 3D kernel renders), and a regen-free
commit leaves the 2D cache stale → the next repaint (any mouse move) re-presents the OLD
projection. In every 3D visual style the driven view IS the presentation, hence "works
beautifully". Fix in `Plugin.EndGesture`:
- Detect the style **per gesture start** via ViewportTable → `DBVisualStyle.Type ==
  VisualStyleType.Wireframe2D` (enum value 4). **Do NOT use `GetCurrent3dAcGsView` as the
  discriminator** — it goes permanently non-null once ANYONE (including us) has obtained the 3D
  view. **Do NOT read `VSCURRENT` via Get/SetSystemVariable** — it is not a plain sysvar and
  throws `eInvalidInput` in-process AND over COM (round-7 probe died on it).
- 2D wireframe commit: build the VTR from the shadow (`ViewDirection`/`Target`/`CenterPoint=(0,0)`
  /`Height`/`Width`/`ViewTwist = NavMath.TwistOf(cam)` — sign convention verified against the
  pure-roll probe) and push it through the classic `ed.SetCurrentView` while the current view
  still holds the OLD camera → the one path that rebuilds the 2D display list. **One regen per
  gesture** — the v0.1.x cost, paid once instead of per frame. 3D styles keep the WD=0 ritual.
The mid-gesture HUD disappearance in 2D wireframe is a known cosmetic (shared with the native
orbit's interaction mode); users who want the fully regen-free path can switch the viewport to
the 3D "Wireframe" style — visually near-identical, presented by the 3D kernel.

**v0.2.3 — the classic commit alone is NOT enough either (user-verified on v0.2.2):** after the
GS drive, the on-screen camera already equals the committed camera, so `ed.SetCurrentView` (and
`ed.Regen()`, and `SetViewportFromView(regenRequired:true)`) all skip the display-list rebuild —
no "Regenerating model.", wireframe still reverts on the next native view op, ViewCube frozen
until a repaint, HUD sometimes offset. The reliable rebuild is the REAL command pipeline: the 2D
commit sets `_regenPending` and the plugin queues **`SendStringToExecute("_.REGEN ")`** — gated on
`Editor.IsQuiescent` (injected text would otherwise feed an active command's prompt) and retried
from the idle tick. One VISIBLE "Regenerating model." per gesture in 2D wireframe — by design.
**v0.2.4 — the REGEN and the GS drive must NEVER overlap (v0.2.3 CRASHED AutoCAD).** Real
trackball streams have constant >180 ms micro-pauses, so v0.2.3's commit queued a REGEN and the
user was often already orbiting again when it executed — REGEN rebuilds the kernel's views under
the GS view being driven → **native access violation (0xc0000005 in coreclr, three crash dumps,
identical fault offset), NOT catchable from managed code**. Interlock: `_regenInFlight` tracked
via `CommandEnded/Cancelled/Failed` (global name "REGEN"), navigation frames are HELD
(re-accumulated) while a regen is pending or in flight and resume only after it fully finishes —
each gesture then starts on a freshly obtained view. `FireDeferredRegen` additionally refuses to
fire while `_gsActive` or non-quiescent; a 2 s watchdog un-wedges navigation if the event never
arrives; `Terminate` unhooks.
**v0.2.5→v0.2.6 — the first NATIVE view op after a gesture can displace the cached wireframe;
the repair watch survives, the DB-record sync experiment did NOT.** User-observed on v0.2.4:
after a committed+regenerated gesture, a native wheel zoom threw the model's cached projection
to a wrong position AND orientation; a regen AFTER repaired it, a PREEMPTIVE one did not — the
zoom consumes stale state that REGEN never refreshes. v0.2.5 tried two fixes and one CRASHED:
- **`ed.UpdateTiledViewportsInDatabase()` in the 2D commit = INSTANT CRASH (removed in
  v0.2.6).** It ERASES + RECREATES the `*Active` VPORT records, orphaning the kernel view bound
  to the viewport; the next gesture drives a dangling native view → the same uncatchable AV as
  v0.2.3 (crash 3 s after load, coreclr 0xc0000005, identical offset). The 3D branch keeps the
  call — its view IS the presentation view and gets re-bound (proven since v0.2.1) — but NEVER
  add it to the 2D path.
- **The native-op repair watch (kept, hardened in v0.2.6):** after each 2D commit, a one-shot
  watch (50 ms sysvar poll of VIEWDIR/VIEWSIZE/VIEWCTR on the idle tick) spots the first
  NON-trackball camera change, then WAITS — camera still for ~400 ms, no mouse button down
  (`GetAsyncKeyState`: MMB drags and wheel transitions can report a QUIESCENT editor!), editor
  quiescent — and only then queues ONE repair regen through the crash-safe interlock, i.e. it
  behaves exactly like the user typing REGEN after the zoom settles. Disarmed by the next
  gesture (whose commit re-arms it) or a doc switch.
- **v0.2.7 — plain field writes into the record ALSO crashed** (identically to v0.2.5): the
  snap's root cause is the stale `*Active` VPORT record (SetCurrentView doesn't update it, REGEN
  never touches it — hence preemptive regens were useless; native zoom consults it), but a record
  write left UN-applied is as fatal as the recreating API. §TBPRG differed in one respect —
  every write was immediately re-applied.
- **v0.2.8 — the ONE SAFE ORDER (shipped): record write + IMMEDIATE
  `ed.UpdateTiledViewportsFromDatabase()`.** The 2D commit writes the shadow camera into the
  EXISTING record(s) field by field (ForRead → UpgradeOpen → ViewDirection/Target/
  CenterPoint(0,0)/Height/Width/ViewTwist, one transaction in the doc lock) and re-applies the
  records in the same breath — the record becomes the SOURCE, so record, editor view, and
  display agree by construction, and native zoom finally reads the right camera (record-vs-live
  dot = 1.000000 after every gesture). The queued visible REGEN and the native-op repair watch
  stay as belt-and-braces. **Crash-tested BEFORE shipping in a throwaway instance** (the lesson
  of v0.2.3/0.2.5/0.2.7): 16 probe gesture/commit cycles + the real production pipeline
  (TBNAVTEST ×3 with interleaved real wheel zooms) — zero crashes, zero plugin error lines.
  SetCurrentView is no longer used in the 2D commit (FromDatabase does the full application).
**Plugin delivery/update UX (daemon 0.1.33):** `autocad` is now a first-class `_ADDINS` entry
(`plugins/autocad` + `version.json`, auto-generated by the Release build from the csproj
`<Version>`): the settings UI shows Install/Update + "update available", `auto_update` re-copies
on daemon start, and `install_autocad` STAGES the copy when the DLL is locked by a running
AutoCAD (it lands via the loader's copy-on-attach at the next AutoCAD start). The loader's
`_netload_plugin` copies `version.json` alongside the DLL.

### 8.17 The `pointer` orbit pivot / `to_pointer` zoom (plugin v0.3.0) — verified live

Orbit about the point **under the mouse cursor** (the SpaceMouse "rotation center = cursor"
behaviour). Two halves, both in the plugin (the retired COM transport had no cursor access — §6):

- **Half A (the live cursor point):** a passive **`Editor.PointMonitor`** subscription, kept bound
  to the ACTIVE document from the 10 ms timer (re-subscribes on doc switch, cache dropped — the
  AutoCAD analogue of FreeCAD's `SoLocation2Event` observer). The handler caches ONE WCS point per
  event with the best depth available, in priority order:
  1. **`ObjectSnappedPoint`** when `History & PointHistoryBits.ObjectSnapped` — a real point ON the
     entity;
  2. the picked entity's depth (`GetPickedEntities()`): slide `ComputedPoint` along the view ray to
     the entity — **exact** for top-level `Curve`s (`GetClosestPointTo(pt, viewDir, false)` =
     projected closest point), **bbox-centre depth** for everything else. Use `ids[0]` (the
     TOP-LEVEL entity) from the `FullSubentityPath` — deeper path entries of a block are in BLOCK
     space, not WCS;
  3. raw **`ComputedPoint`** — WCS but on the UCS construction plane (right screen position, plane
     depth).
  **Pick reality (verified live):** what `GetPickedEntities` returns follows the visual style's
  aperture behaviour, same as native rollover — in **2D Wireframe a solid's face interior does NOT
  pick** (only edges), so mid-face hovers cache the plane point (z=0 in plan); in **Realistic**
  the face picks and the same hover cached the box-depth point (z=10). `TBNAVPTR` dumps the live
  cache to the log to check exactly this.
- **Half B (the pivot):** `NavMath.Apply` grew optional `orbitPivot`/`zoomPivot` args (null = the
  old orbit-about-target exactly): a rigid rotation about P (`tgt' = P + m·(tgt−P)`; the eye
  follows via `tgt' + dir'·dist`, so P keeps its exact screen position), and parallel `to_pointer`
  zoom slides the target toward P by `1/factor` (perspective falls back to the plain dolly).
  Per-gesture hold in `TryApplyGs`: the pivot is captured ONCE at the first orbit frame of a
  gesture from the cache — validated against the drawing extents +10 % of the diagonal (a cursor
  over empty space intersects the UCS plane arbitrarily far away → out-of-bounds falls back to
  target orbit) — and held; a pan/zoom frame invalidates the orbit hold (re-captured at the live
  cursor on the next orbit frame), gesture end resets both. The legacy `SetCurrentView` fallback
  path does NOT support the pointer pivot (view-centre orbit as before).
- **Verification (throwaway instance, headless — no human mouse):** NETLOAD in a COM-launched
  fresh AutoCAD; the log showed the PointMonitor subscribe + **a real `SetCursorPos` sweep over
  the canvas caching points** (WM_MOUSEMOVE drives PointMonitor fine); **`TBNAVPTRTEST`** seeds a
  synthetic pivot inside the extents, forces `op=pointer`, injects 120 orbit frames through the
  EXACT production pipeline and asserts on the gesture's seed/end shadows: `|tgt−P|` and `|pos−P|`
  preserved (16.4412→16.4412 / 74.2704→74.2704), the target swept 16.7 units, **P's screen offset
  (5, 7.5)→(5, 7.5) exact, and the committed DB `TARGET` error = 0** — PASS in BOTH commit
  flavours (2D-Wireframe REGEN path and the regen-free Realistic path). The offline math twin
  lives in `plugin_src/autocad/NavMathTests` (NavMath.cs compiled VERBATIM against stub geometry
  types — acdbmgd can't load outside acad.exe; run by `tests/test_acad_navmath_pointer.py`,
  skips without the .NET SDK). **Not yet verified: the human feel** (hover + orbit with the real
  trackball); and osnap-assisted capture (path 1) is code-only — exercise it live when tuning.

### 8.18 The COM nav transport is RETIRED (daemon 0.1.41) — archived, and why "fallback" was a net negative

User-reported after v0.2.8 proved the plugin in daily use: *"There appear to be some conflicts
between it and the newer arch, as the COM connects first before the compiled add-in."* That is
exactly what the dual-transport wiring guaranteed:

- **The race is structural.** The COM driver attaches in ~one poll (2 s worker), while the plugin
  needs NETLOAD + a broker handshake. `app.py` routed autocad frames per-frame on "is the plugin a
  broker client *right now*", so the **first gesture of every session** (and any gesture during a
  broker reconnect) went through the COM path: regen-per-commit orbit, the overlay cube, different
  signs/feel — user-visible as "AutoCAD behaves differently for a moment, then changes".
- **The late deferred orbit.** The COM transport's `_ORBIT_DEFER` batching applies on gesture-idle
  (~100 ms). Frames accepted just before the plugin's handshake completed could therefore COMMIT
  (a reassign + regen, moving the record camera) *after* the plugin already owned the frames —
  fighting the GS view mid-gesture. This is the same class of stale-writer conflict as the §8.16
  crash saga, just across processes.
- **The fallback insured against almost nothing.** Its only real coverage was "plugin failed to
  NETLOAD" (unsupported pre-2025 AutoCAD, or a broken DLL) — cases better surfaced as *no
  transport + a log line* than as a silently different, flickery transport.

**Resolution (daemon 0.1.41):** autocad is a plain **broker app** (`_nav_sink` else-branch, no
exclusion-set special case, no `client_infos()` polling); `autocad_driver.py` now holds only
`AutoCADPluginLoader` (ROT attach → copy DLL+version.json → `TRUSTEDPATHS` → LISP NETLOAD, once
per AutoCAD session, liveness-probed via `Documents.Count`); the tray/apps line shows autocad
ONLY via the plugin's broker handshake. The full transport — `AutoCADDriver`, `acad_overlay.py`,
and their tests, exactly as shipped in 0.1.37/0.1.40 — is preserved at
**`archive/autocad_com_transport/`** with a README. §2–§7 and §8.1–§8.13 of this doc describe
that archived code and remain the verified ActiveX reference; resurrect the pattern only for a
target with no in-process path.

AutoCAD 2026 is installed and everything above was verified end-to-end from throwaway scripts.
For the ARCHIVED COM transport the recipe was:

- **Attach + drive the real code path** (the archived module uses relative imports, so copy
  `autocad_driver.py` + `acad_overlay.py` back into `trackball_daemon/` for the probe session):
  ```python
  import pythoncom
  from trackball_daemon.autocad_driver import AutoCADDriver   # restored from the archive
  pythoncom.CoInitialize()
  drv = AutoCADDriver(); drv._attach()
  drv.set_scheme("view", "turntable", "to_center")
  drv._flush((0.0, 0.1, 0.0, 0.0, 0.0, 0.0))   # one turntable-yaw frame
  print(drv._acad.ActiveDocument.GetVariable("VIEWDIR"))
  ```
  (Run with the repo on `PYTHONPATH`; the script's own dir is `sys.path[0]`, not the repo.)
- **Verify a fact by OBSERVING the viewport** (computer-use `request_access(["AutoCAD 2026 - English"])`
  + `screenshot`) *and/or* by reading the sysvars back — `VIEWDIR`/`VIEWSIZE`/`VIEWCTR` before/after an
  op are definitive for geometry, screenshots for sign/feel. Verified live this way: turntable orbit
  (elevation preserved), free orbit (elevation changes), pan (Target shifts along camera-right), zoom
  (`VIEWSIZE` halves on zoom-in), and the reassign repaint.
- **Never touch the user's drawings.** Probe on a **new** drawing (`acad.Documents.Add()`; then
  re-fetch `ActiveDocument`, §8.7) with a `ModelSpace.AddBox`, and **close it without saving**
  (`doc.Close(False)`) when done. If you must probe the user's drawing, save a named view first
  (`SendCommand('._-VIEW _S TMP\\n')`) and restore it (`_R`/`_D`) after.
- **A healthy attach logs:** `autocad: attached to running instance (v25.1s (LMS Tech))`. Op failures
  log `autocad: <orbit|pan|zoom> step failed and is being skipped (<repr>)` **once** each. "Connected
  but nothing moves" with no such line usually means AutoCAD isn't the focused app, or the drawing is
  in **paper space** (the driver no-ops outside model space), or no drawing is open.
- **NOTE — aggressive probing can destabilise AutoCAD.** Rapid reassigns + `SendCommand` in tight
  loops occasionally provoked `RPC_E_CALL_REJECTED` ("Call was rejected by callee") and even closed
  AutoCAD. Put small sleeps (~30 ms) between COM ops in throwaway probe loops. The real driver is
  gentle (rate-limited flushes, no `SendCommand`), so this only bites probe scripts.

The unit tests mock the COM boundary so **no AutoCAD is required**: `tests/test_autocad_loader.py`
covers the live loader (attach → one NETLOAD per session, doc-open gating, locked-DLL staging,
TRUSTEDPATHS dedup, session-drop → re-NETLOAD, no-pywin32 no-op); the archived transport's full
suite (orbit/pan/zoom/pivots/reassign ritual/worker loop) lives beside it at
`archive/autocad_com_transport/test_autocad_driver.py`. What unit tests **cannot** cover is the
real COM behaviour — that's what this section's live testing is for.

---

## 10. Status & known limitations (at handoff)

- **Working & live-verified** (AutoCAD 2026 / ACAD 25.1s): the plugin transport end-to-end —
  regen-free GS orbit/pan/zoom/roll in 3D visual styles, the 2D-Wireframe record-write commit +
  one visible REGEN per gesture (§8.16, crash-tested), `pointer`/`to_pointer` (§8.17), broker
  handshake + focus gating; the loader's zero-friction delivery (ROT attach → copy → trust →
  NETLOAD, once per session); the settings-UI install/update flow with locked-DLL staging.
- **Needs a feel/sign pass on hardware** if anything feels off: the plugin's
  `OrbitSign`/`Pan*`/`Zoom*` constants in `Plugin.cs` (drawing units vary wildly, so pan/zoom
  scale especially).
- **Limitations:** plugin pivot modes `origin`/`object` still orbit like `view`, and `to_object`
  zoom is TODO; pre-2025 AutoCAD (no .NET 8 host) has **no transport** since the COM fallback was
  retired (§8.18) — it would need a .NET Framework plugin variant; paper space no-ops. The COM
  transport's own ceilings (reassign-regen per orbit commit, no `VIEWTWIST`, no raycast — §8.4/
  §8.6/§8.10/§8.13) now matter only if the archive is ever resurrected.

---

## 11. File / wiring map

- **`plugin_src/autocad/TrackballNavAcad/`** — the transport: `Plugin.cs` (socket client, WinForms
  timer, GS gesture engine, the §8.16 2D commit + regen interlock + native-op repair watch,
  `TBNAV`/`TBNAVTEST`/`TBNAVPTRTEST` commands) + `NavMath.cs` (pure camera math, twin-tested by
  `plugin_src/autocad/NavMathTests`). Release build copies the DLL + generated `version.json` to
  `trackball_daemon/plugins/autocad/`.
- **`autocad_driver.py`** — `AutoCADPluginLoader` only: worker poll (`_run`/`_tick`), ROT attach
  (`_find_running_acad`), and the delivery (`_netload_plugin` = copy DLL+manifest → `TRUSTEDPATHS`
  → LISP NETLOAD, once per session). `_runtime_plugin_dir()` is the single source of truth for the
  runtime dir (integrations imports it).
- **`archive/autocad_com_transport/`** — the retired COM transport (`AutoCADDriver` +
  `acad_overlay.py` + tests) with a README; see §8.18.
- **`app.py`** — constructs `self.acad_loader = AutoCADPluginLoader()` (start/stop lifecycle);
  autocad frames take the generic broker branch in `_nav_sink` (no exclusion-set special case);
  rate + scheme reach the plugin through the broker like any socket add-on. AutoCAD is matched as
  the foreground app by the **process name `acad`** (`_APP_PROC_HINTS`).
- **`config.py`** — the `autocad` app uses the shared `_app()` shape (`rate_hz`, `bindings.scheme`
  per-app override with `"default"` inheriting General). Deep-merge adds it to existing configs with no
  `CONFIG_VERSION` bump.
- **`integrations.py`** — `detect_autocad` (globs `Autodesk\AutoCAD*\acad.exe`) and
  `install_autocad` (verify AutoCAD + pywin32, copy/stage the bundled plugin, enable). AutoCAD IS
  a first-class `_ADDINS` entry (`version.json` manifest → install/update UI + auto_update).
- **`ui.py`** — no AutoCAD-specific code: the add-in row (Install/Update + version) renders
  generically from `_ADDINS`; the per-app Bindings + scheme dropdowns render generically.
- **`winfocus.py`** — `foreground_process_name` (used to route to AutoCAD when `acad` is frontmost).
- **`requirements.txt`** — `pywin32` (Windows). Missing ⇒ the loader disables itself and logs once
  (the plugin can still be NETLOADed by hand).
