# SolidWorks 3D-navigation driver — implementation notes & handoff

This is the maintainer's guide to the **SolidWorks** integration in the Trackball Daemon. It
documents what the driver does, the **verified** COM view-transform model it relies on, and — most
importantly — the **gotchas and solved problems you would not discover by reading the code alone**
(§8). Almost every fact here cost real debugging time and/or live probing against SolidWorks. **If
you only read one section, read §8.**

Primary code: [`trackball_daemon/solidworks_driver.py`](../../trackball_daemon/solidworks_driver.py).
Tests: [`tests/test_solidworks_driver.py`](../../tests/test_solidworks_driver.py),
[`tests/test_app_routing.py`](../../tests/test_app_routing.py). Wiring: `app.py`, `config.py`,
`integrations.py`, `ui.py`, `winfocus.py`. User-facing summary: the SolidWorks section of
[`README.md`](../../README.md). (Current at daemon `__version__` 0.1.16.)

---

## 1. What it is, in one paragraph

SolidWorks add-ins are COM/.NET components that need **admin registration**, so we avoid that
entirely. Instead, the daemon drives SolidWorks by **external COM automation**: the in-process
`SolidWorksDriver` (the COM analogue of the socket `NavBroker`, parallel to it) **attaches to an
already-running SolidWorks** via `pywin32` and moves the active view's camera directly with
SolidWorks' own native view methods. **There is no file to install and the socket broker is not
involved.** It exposes the same surface as the broker / Onshape bridge
(`submit/set_rate/set_scheme/set_pivot_hold/start/stop/is_connected/version` +
`on_connection_changed`), so `app.py` wires and routes it identically. It degrades gracefully: no
`pywin32` (e.g. a non-Windows dev box) → `start()` logs once and everything becomes a no-op.

The hardest-won knowledge here is **how SolidWorks' COM view transform actually behaves** (§4) and
**how pywin32's late-bound dispatch mangles SolidWorks calls** (§8). The camera math is correct and
verified; the friction is entirely in the COM boundary.

---

## 2. Architecture & threading

Mirrors `NavBroker` exactly (intentionally):

- **`submit(ox,oy,oz,px,py,zoom)`** is called on the **BLE thread**. It only adds a 6-float delta to
  `self._acc` under a lock. It **never blocks and never touches COM** — COM must be used only on the
  thread that `CoInitialize`d it.
- **One worker thread** (`_run`) calls `pythoncom.CoInitialize()`, then loops at the configured rate:
  - If not attached, try `_attach()` at most every `_RETRY_PERIOD` (2 s); drop accumulated motion
    while detached.
  - Otherwise, atomically swap out the accumulated delta and, if non-zero, call `_flush(delta)`.
  - Any exception escaping `_flush` is treated as "SolidWorks/doc closed" → `_handle_drop()`
    (disconnect; re-attach later).
  - `_sleep_remainder` sleeps only the time left in the period *after* the COM work, so the cadence
    stays even regardless of how long each (variable-cost) flush took. This is what keeps motion
    smooth instead of stuttering at `period + flush_time` intervals.
- **`on_connection_changed(connected, version)`** fires on attach/drop so the tray/UI status line
  updates (the analogue of the broker's `on_clients_changed`).

The accumulate-then-flush design means a slow viewport **coalesces** motion (sums it) instead of
losing it — same idea as the firmware's float carry.

---

## 3. Attaching to a running SolidWorks (`_attach`, `_find_running_sw`)

We **attach only — never launch** SolidWorks. The subtlety (see §8.2): `GetActiveObject(
"SldWorks.Application")` returns whichever instance registered **first** in the Running Object Table
(ROT), which can be a **stray empty instance** (then `ActiveDoc` is always `None` and nothing ever
moves). So `_find_running_sw` **enumerates the ROT**, identifies SolidWorks instances by calling
their `GetDocumentCount` (a SW-specific method — doubles as the filter), and picks the one with the
**most open documents**, falling back to `GetActiveObject`.

On success it caches `RevisionNumber` as the version string, resets caches, and fires the connected
callback. `detect_solidworks()` (in `integrations.py`) just globs for `SLDWORKS.exe`;
`setup_solidworks()` only verifies SW + `pywin32` are present and flips the app to
enabled/installed — **there is nothing to copy or register** (`addin_version` is always `""`).

---

## 4. The verified view-transform model (the math foundation — trust this, it was measured live)

A model-space point **P** projects to a screen position as:

```
screen_xy = Scale2 * (col_k · P)[x,y] + Translation3
```

where `col0/col1/col2` are the **columns** of `IModelView.Orientation3`'s 3×3 (see below). The
camera/view properties, all **verified against live SolidWorks** (not assumed from docs):

| Property / method | Type | Verified behaviour |
|---|---|---|
| `IModelView.Orientation3.ArrayData` | 16 doubles | 3×3 is **row-major**, but the **COLUMNS are the camera axes** in model space: `col0`=right, `col1`=up, `col2`=forward **out of the screen** (toward the viewer). One read yields all three. **See §8.4.** |
| `IModelView.Translation3` | `IMathVector` | Pan offset, **meters in the screen X,Y plane**, zoom-independent (do **not** divide by `Scale2`). Pan by **reading, adding, and SETTING** it — `TranslateBy` is broken under dynamic dispatch (§8.3). |
| `IModelView.Scale2` | double | View scale (model→screen). `ZoomByFactor(f)` **multiplies** it by `f`; `f>1` = zoom in. |
| `IModelView.RotateAboutAxis(angle, px,py,pz, ax,ay,az)` | — | Incremental rotation, radians, about a **model-space** axis. **IGNORES the point `(px,py,pz)` entirely** and leaves `Translation3`/`Scale2` untouched — it only rotates the orientation, pivoting about the **model ORIGIN**. **See §8.5** — this single fact dictates the whole pivot design. |
| `IModelView.ZoomByFactor(f)` | — | Zoom about the **view centre** (no off-screen drift). Changes both `Scale2` and `Translation3`. |
| `IModelView.EnableGraphicsUpdate` | bool (set) | `False` suspends viewport repaints; `True` resumes. Used to make a multi-step frame one repaint (§8.7). |
| `IModelDoc2.GraphicsRedraw2()` | — | Force a repaint after an automation change. ~5 ms (cheap — the redraw is **not** the bottleneck). |
| `IPartDoc.GetPartBox(True)` / `IAssemblyDoc.GetBox(0)` | 6 doubles | Bounding box `(minx,miny,minz, maxx,maxy,maxz)`. **Both need `_FlagAsMethod`** (§8.1). `IModelDocExtension.GetBox` is **NOT reachable** via late dispatch. |
| `IModelDocExtension.SelectByRay(...)` + `ISelectionMgr.GetSelectionPoint2` | — | Screen-centre raycast for the `view` pivot (§7). Extremely fussy via late dispatch (§8.9). |

**Holding a pivot P fixed on screen during orbit.** Because `RotateAboutAxis` always pivots about
the origin, to orbit about an arbitrary P we **rotate, then pan** so P keeps its screen position.
The required pan is, exactly:

```
ΔTranslation3 = Scale2 * ((col_before − col_after) · P)   # x,y components
```

This **exactly compensates** the rotation — the held point stays put to ~1e-16 (machine precision,
verified live over 100 frames). The post-rotation columns `col_after` are predicted **analytically**
in Python with Rodrigues' formula (`_rodrigues`, axis rotated by −angle) instead of re-reading
`Orientation3` (a ~20 ms COM read), so the recenter pan costs no extra round-trip. **There is no
discrete rotate-then-translate "integration error"** — a maintainer (and the original user) suspected
one; there isn't (§8.6).

---

## 5. The per-frame camera ops (`_flush`)

Each non-empty frame, on the worker thread:

1. `_live_view(swApp)` returns the cached `(model, view)`, re-fetching at most every `_VIEW_TTL`
   (1 s). The re-fetch **doubles as the liveness probe** (`ActiveDoc` raises if the app/doc closed →
   `_run` drops the connection) and **resyncs** the tracked `Translation3`/`Scale2` against any
   external (mouse) view change. It also (re)fetches + flags the pick handles for §7.
2. **Freeze the viewport** (`EnableGraphicsUpdate = False`) for the whole frame (§8.7).
3. Apply **orbit**, then **pan**, then **zoom** — each in its own `try/except` so one failing COM
   call is logged once (`_warn_once`) and skipped without killing the others (§8.8).
4. If a pan or zoom happened, **drop the held `view` pivot** (`_orbit_pivot = None`) so it
   re-raycasts next orbit (§8.6).
5. **Resume** graphics + one `GraphicsRedraw2()` (§8.7).

Pan (`_apply_pan`): add `PAN_SIGN * delta * PAN_SCALE` to the tracked `Translation3` and SET it.
Zoom (`_apply_zoom`): `to_center` is a bare `ZoomByFactor`; `to_object` zooms then pans the
bounding-box centre back by `(Scale2_before − Scale2_after)·(col·C)`; `to_cursor` does the same but
holds the **surface point under the mouse cursor** (§7.5, captured once per gesture into
`_zoom_pivot`), falling back to `to_center` on a miss. After a bare `ZoomByFactor` we force a resync
(`_view_ts = 0`) because it changes both `Scale2` and `Translation3` itself.

---

## 6. Orbit pivots (the control scheme — `_apply_orbit`)

`set_scheme(orbit_pivot, orbit_style, zoom_mode)` is pushed from `app._apply_schemes()` (general
default merged with the per-app SolidWorks override; per-app value `"default"` inherits the general
one — see `config.effective_scheme`). **Five pivot modes**, all built on §4's rotate-then-pan:

- **`origin`** — rotate **only**, no pan: the model spins about the world origin with **zero view
  translation**. The original behaviour, and the lightest path. (Re-added as a distinct mode after it
  was conflated with `object` — see §8.6.)
- **`object`** — hold the model **bounding-box centre**. **`selection`** uses the mean of the current
  selection points reported by `ISelectionMgr.GetSelectionPoint2`, falling back to object.
- **`view`** — hold the **screen-centre point at the true surface depth** under the crosshair, found
  by a raycast (§7). Captured once and **held** through a gesture; recomputed only after the view is
  idle ≥ `view_pivot_hold_sec` (default 0.5 s, per-app `set_pivot_hold`) or when a pan/zoom moves it.
- **`cursor`** — hold the surface point under the **mouse cursor** — the same raycast as `view`,
  aimed through the cursor pixel instead of the screen centre (§7.5). Same capture-once-and-hold; a
  miss / off-view cursor falls back to the object centre for the rest of the gesture.

Orbit **style**: `free` rotates about the composed camera-space axis (`vx·col0 + vy·col1 + vz·col2`);
`turntable` yaws about `WORLD_UP` + pitches about camera-right, **roll dropped**, composed into one
rotation via quaternion. Either way it's **one `RotateAboutAxis` per frame** (plus, for a non-origin
pivot, one recenter pan). Switching the pivot **drops any held pivot immediately** so it takes effect
at once (§8.6).

---

## 7. The `view` pivot screen-centre raycast (`_view_pivot`, `_raycast_depth`, `_get_pick_handles`)

**Why:** the camera "target" / screen-centre point sits on the optical axis at an **arbitrary depth**.
Pinning the pivot there (e.g. at the object-centre depth) makes the model **swing** whenever that
depth ≠ the surface you're actually looking at. So `view` casts a ray straight down the screen-centre
optical axis and pins the pivot at the **true surface depth** under the crosshair — exactly what
SolidWorks' own middle-drag orbit does.

**Mechanism** (`IModelDocExtension.SelectByRay` + `ISelectionMgr.GetSelectionPoint2`):

1. Build the ray from the view (§4): origin = a screen-centre optical-axis point pushed back toward
   the viewer along **+col2** by `_RAY_PUSH × bbox_diagonal` (starts outside the model); direction =
   **−col2** (into the screen).
2. Cast with an **expanding aperture** (cylinder radius) `_RAY_APERTURE_FRACS × bbox_diagonal` =
   `0.5%, 1.5%, 4.5%, 13.5%` — start tiny (the surface the axis actually pierces) and grow ×3 to catch
   thin/edge features when the exact centre is in a gap. **The first (smallest) radius that hits
   wins** = most accurate.
3. Among the entities hit at that radius, take the one **nearest the viewer** = largest `col2·hit`
   (a fat aperture selects several faces).
4. **Validate** each hit lies inside the bbox expanded by `_RAY_BBOX_MARGIN` (10 %) of its diagonal —
   guards against a bogus/sentinel point.
5. **No valid hit at any radius → fall back to the object-centre depth** (the old behaviour).
6. Only the **depth** (`col2·hit`) is used; `_view_pivot` keeps the in-plane position at the screen
   centre. The pivot is then **held for the whole gesture** (§6), so the raycast runs **once per
   gesture (~16 ms warm)**, never per frame.

**It mutates the selection set**, so `_raycast_depth` **saves** the user's selection
(`GetSelectedObject6`), **clears** before each cast (to isolate the hit), reads the point(s), then
**restores** (`ent.Select(True)` per saved entity). All of this is heavily guarded — any failure
returns `None` and `view` simply keeps the object-centre depth. The pick handles
(`model.Extension`, `model.SelectionManager`) are fetched + method-flagged in `_live_view` and
cached. See §8.9 for the brutal `SelectByRay` marshaling gotchas — **this is where almost all the
debugging time on this feature went.**

The whole feature can be disabled with the module constant `VIEW_PIVOT_RAYCAST = False` (then `view`
reverts to screen-centre-at-object-depth), mirroring the `FORCE_REDRAW` escape hatch.

---

## 7.5 The `cursor` pivot — orbit/zoom about the point under the mouse (`_cursor_pivot`, `_cursor_screen_ab`, `_cursor_client_point`)

`cursor` / `to_cursor` reuse the §7 raycast, only aimed through the **mouse cursor pixel** instead of
the screen centre. The whole trick is turning the OS cursor into the in-plane `(a, b)` offsets the
raycast already speaks. The mapping was **verified live in the GUI**, screenshot-confirmed against a
box's corners (driving the OS cursor to each corner's true on-screen position and confirming
`_cursor_pivot` returns that corner to **< 0.1 mm** — §10).

> **Lesson — a self-consistent test can still be wrong.** The first cut "verified to < 0.1 mm" by
> `SetCursorPos(Transform(corner))` then inverting `Transform` at that same pixel. That round-trips
> **by construction** — it proves the algebra inverts, not that `Transform`'s pixels are where the
> cursor actually is. A real hover landed the pivot up-and-left of the cursor. The honest test drives
> the cursor to the corner's **true screen position** and lets the driver map it back independently;
> the two facts below came out of that + a screenshot.

**Half A — the live cursor, on demand (not `IMouse`).** `IModelView.GetMouse` **exists** (a property
returning an `IMouse` dispatch — COM-introspected live), and `IMouse` is a connection-point event
source. But sinking its move notification from this **out-of-process** driver would mean makepy'ing
the whole SolidWorks typelib (SW dispatches carry **no typeinfo**) **and** a cross-process COM
callback marshaled into SolidWorks' UI thread for **every** mouse move (~100+ Hz). We need the cursor
**once per gesture**, so we read it **on demand** with Win32 `GetCursorPos` (the daemon is a Windows
process) — the same call the AutoCAD/Fusion cursor pivots use, and the pixel is always **fresh** (no
cache to stale). `_cursor_client_point` maps it with `ScreenToClient(GetViewHWnd)` and gates on the
result falling inside the client rect while the SW frame is foreground. **NOT** a `WindowFromPoint ==
GetViewHWnd` identity check — SolidWorks composites the 3D view with an inner render child, so
`WindowFromPoint` returns a window that is *neither* `GetViewHWnd` nor a resolvable descendant of it
(verified live); the client-rect test sidesteps that child, and rejects the surrounding
toolbars/panels and other apps.

**Half B — cursor pixel → model ray, via `IModelView.Transform`.** `Transform` is the model →
**pixel** transform. Its column-convention 3×3 rows are the camera **right** / camera **up** axes, so
a point on the cursor ray satisfies `row0·P = (px_x − t0)/s` and `row1·P = (px_y − t1)/s`.
`_cursor_screen_ab` inverts that: it confirms `row0`/`row1` align with the camera `col0`/`col1`
(rejecting a changed transform model rather than producing a wrong pivot), resolves each **sign** per
capture (SW's pixel-y runs the opposite direction to camera-up, so no baked-in flip constant), and
returns `(a, b)`. Then `_cursor_pivot` runs the **exact §7 raycast** from `(a, b)`.

The two facts about `Transform`'s pixel space that a naive reading gets wrong — both **screenshot-
verified** at 125 % display scaling:

- **`Transform`'s pixels are CLIENT-relative (to `GetViewHWnd`'s client top-left), not desktop.**
  There is a ~toolbar-height **y** offset (and an **x** offset when the window isn't full-width), so
  we map the cursor with `ScreenToClient(GetViewHWnd)` first; the inversion is then a bare
  `(client_px − t)/s`. (Placing the cursor at `Transform(peak)` interpreted as desktop landed it on
  the *Extruded Boss/Base* ribbon button directly above the peak — the giveaway.)
- **`Transform`'s pixels are PHYSICAL (device) pixels, while `GetCursorPos` returns pixels in the
  calling thread's DPI space.** At 125 % a DPI-unaware reader sees logical px and the pivot lands
  up-and-left of the cursor (the exact user-reported symptom). Fix: `_make_thread_dpi_aware()` sets
  the **worker thread** (only) to per-monitor-v2 DPI awareness at `_run` start
  (`SetThreadDpiAwarenessContext`, thread-local so the Tk UI is untouched), so
  `GetCursorPos`/`ScreenToClient`/`GetClientRect` all return physical px matching `Transform`.

Because both the transform and the client rect are re-read each capture, moving/resizing/maximizing
the SW window needs no extra bookkeeping — the mapping is **resize-resistant** by construction.

`to_cursor` **zoom** uses the same `_cursor_pivot` into a separate per-gesture slot (`_zoom_pivot`,
reset on orbit/pan), then rides the existing "hold a point fixed while zooming" recenter that
`to_object` already implemented (§5). A miss falls back to `to_center`.

The remaining un-verified item is purely the **feel** while a human orbits (the geometry is proven
exact); the row-alignment guard is a defensive check for a transform convention we haven't seen
SolidWorks break; and hovering the FeatureManager tree (which overlays the client's left edge) maps
to the model geometry behind it — benign, bbox-validated.

---

## 8. GOTCHAS & SOLVED PROBLEMS (read this)

Each is *symptom → cause → fix*. Most cost real debugging or live probing.

### 8.1 pywin32 late-bound dispatch mis-resolves SolidWorks members → `_FlagAsMethod`
- **Symptom:** calling a documented SW method raises `AttributeError`/"Member not found"/"server threw
  an exception", or returns a property where you expected a call.
- **Cause:** `GetActiveObject` returns a **late-bound (dynamic)** dispatch with no real type info, so
  pywin32 guesses property-vs-method wrong for many SW members.
- **Fix:** force a `DISPATCH_METHOD` invoke with `obj._FlagAsMethod("Name")` before calling. Methods
  that need this in our code: `GetMathUtility`, `CreateVector`, `GetDocumentCount`, `GetPartBox`,
  `GetBox`, `SelectByRay`, `GetSelectionPoint2`, `GetSelectedObjectCount2`, `GetSelectedObject6`,
  `Select`, and (in test/probe scripts) `ShowNamedView2`/`ViewZoomtofit2`.
- **Inverse trap:** some members resolve as **properties** and must be read **without parens** —
  `model.GetType` and `model.GetTitle` (calling `model.GetTitle()` raises "str object is not
  callable"). There is no rule; determine each by trying it live.

### 8.2 `GetActiveObject` can grab an empty SolidWorks (connected, but `ActiveDoc` is always None)
- **Symptom:** status says connected, but nothing ever moves; `ActiveDoc` is `None` even with a part
  open.
- **Cause:** `GetActiveObject` returns the **first** instance in the ROT, which may be a stray empty
  SW process.
- **Fix:** `_find_running_sw` enumerates the ROT and picks the instance with the highest
  `GetDocumentCount()`, falling back to `GetActiveObject`.

### 8.3 `TranslateBy` type-mismatches → SET `Translation3` instead
- **Symptom:** `view.TranslateBy(vector)` raises a COM type-mismatch under dynamic dispatch.
- **Cause:** the `IMathVector` argument doesn't marshal through `TranslateBy`'s signature.
- **Fix:** pan by **reading `Translation3`, adding the delta, and assigning `Translation3 =
  new_vector`**. The setter accepts the `IMathVector` we build via `CreateVector` (which itself needs
  the array as a `VT_ARRAY|VT_R8` VARIANT — a plain tuple trips a type error; see `_mkvec`).

### 8.4 `Orientation3`'s camera axes are the COLUMNS, not the rows
- **Symptom:** orbit tumbled the wrong way / about wrong axes.
- **Cause:** the 3×3 is row-major, and it's tempting to read the **rows** as the camera basis. They're
  the **columns** (`col_k = (ad[k], ad[k+3], ad[k+6])`): `col0`=right, `col1`=up, `col2`=forward (out
  of screen).
- **How it was settled:** **observe the live viewport**, not self-referential matrix algebra. An
  earlier "rows" proof was bogus — transforming `[1,0,0]` by a row-major matrix trivially returns
  row 0 and proves nothing. The decisive test: rotating about `col2` produces an in-screen-plane
  **roll**; rotating about a row **tumbles**. Determine such conventions by what you SEE.

### 8.5 `RotateAboutAxis` ignores its point argument (pivots about the ORIGIN)
- **Symptom:** passing the desired pivot as the point arg did nothing; the model always rotated about
  the world origin.
- **Cause:** verified live — SW's `RotateAboutAxis(angle, px,py,pz, ax,ay,az)` uses only the **axis**;
  `(px,py,pz)` is ignored, and `Translation3`/`Scale2` are untouched.
- **Fix:** to orbit about any pivot P, **rotate then pan** by `ΔT = Scale2·((col_before−col_after)·P)`
  (§4). The `origin` pivot is then just "skip the pan".

### 8.6 The "view/object orbit are identical and both wrong / jitter / drift" saga
This is the messiest history; the current design is the resolution of several rounds of feedback.
- **Perceived drift is NOT a math error.** The rotate-then-pan recenter is exact to ~1e-16 (verified).
  The user suspected a discrete rotate/translate integration error — **there isn't one.**
- **Real cause of perceived drift:** a **stale** `view` pivot. After a pan, the screen-centre point
  changes, but a held pivot wasn't updated → the next orbit swung about the old point. **Fix:** drop
  the held pivot on **any pan/zoom** (in `_flush`), and recapture after idle ≥ `view_pivot_hold_sec`.
- **"Orbit gets stuck on `view` when I switch to `object`":** `set_scheme` didn't release the held
  pivot. **Fix:** `set_scheme` sets `_orbit_pivot = None`.
- **Capture the pivot ONCE per gesture and HOLD it.** Re-computing the screen-centre pivot every frame
  chases a moving target (it crawls) and triples the COM round-trips. Capturing once (like native
  middle-drag sets the rotation centre on mouse-down) is both correct and fast.
- **`origin` vs `object` are different modes.** The *original* good-feeling orbit was about the
  **world origin** (pure rotation, no pan), not the object centre. They were briefly conflated; they
  are now separate modes (`origin` = pure rotation, `object` = hold the centroid). Don't merge them.

### 8.7 Jitter + a wasted repaint: freeze the viewport, then redraw once
- **Symptom:** every orbit frame the model visibly snapped to a wrong (origin-rotated) position and
  settled — a two-step flicker — and the frame rate was ~half what it should be.
- **Cause:** the frame is **two** ops (rotate, then recenter pan), and **`RotateAboutAxis` triggers
  its own intermediate repaint** showing the origin-rotated, pivot-wrong state; then `GraphicsRedraw2`
  showed the corrected one. Two repaints per frame = flicker + wasted work.
- **Fix:** wrap the whole frame in `EnableGraphicsUpdate = False … True` and do **one**
  `GraphicsRedraw2` at the end → a single repaint, no flicker. Measured **~133 → ~76 ms/frame (≈2×)**
  on a heavy part, live.
- **Subtlety:** re-enabling alone does **not** reliably repaint an automation-driven idle view, so
  **keep the explicit `GraphicsRedraw2` after re-enabling.** `_set_graphics_update` returns whether it
  toggled, so an unsupported build silently falls back to per-op repaints.

### 8.8 Per-op guards: one failing COM call must not blank the viewport
- A single failing op (orbit/pan/zoom/redraw) is caught, logged **once** via `_warn_once`, and
  skipped — the other ops and the redraw still run. **Only `ActiveDoc`/`ActiveView` access raising
  causes a disconnect** (that genuinely means the app/doc closed). A regression once let one failing
  op skip the rest and dead-screen the viewport; don't reintroduce that.

### 8.9 `SelectByRay` is the worst offender — it silently selects NOTHING unless you type its args exactly
This is the big one for the `view`-pivot raycast (§7). All found by **live probing** (you cannot
introspect: `GetTypeInfo()` raises `'Invalid index'` on these dispatches, the same makepy-hostility
that blocks everything else).
- **`Tol` MUST be a `VT_I4` integer VARIANT.** Passed as a Python/float double, `SelectByRay` returns
  `False` and **selects nothing, with no error** — it looks completely dead. This single fact cost ~5
  probe rounds. **Fix:** wrap **every** argument in an explicitly-typed VARIANT (`_variant`):
  coords/direction/radius = `VT_R8`, `Tol`/`SelectOption`/`Mark` = `VT_I4`, `Append` = `VT_BOOL`.
- **11 args, all required:** `SelectByRay(x,y,z, vx,vy,vz, radius, Tol, SelectOption, Append, Mark)`.
  The 10-arg form errors `Parameter not optional`.
- **The selection count is `GetSelectedObjectCount2(-1)`.** `GetSelectionCount` is **not a resolvable
  name** on the `ISelectionMgr` dispatch (`_FlagAsMethod` → "Unknown name").
- **Clear the selection BEFORE each cast.** `Append=False` does **not** reliably clear under VARIANT
  marshaling, and a fat radius selects **multiple** faces — so `GetSelectionPoint2(1,-1)` could return
  someone else's entity. We `ClearSelection2(True)` first to isolate our hit, then take the
  **nearest-the-viewer** hit (largest `col2·pt`).
- **Save/restore the user's selection.** `SelectByRay` clobbers it. We snapshot via
  `GetSelectedObject6(i,-1)`, then restore with `ent.Select(True)` per entity. **`Select4`/`Select2`
  type-mismatch** under dynamic dispatch; plain **`Select(bool)` works**. Selection **marks** are not
  preserved (acceptable for a navigation gesture).
- **`RayIntersections` is a dead end** via late dispatch: it resolves, but its result-retrieval
  method `GetRayIntersectionsPoints` does **not** — so you can't read the hits. `SelectByRay` +
  `GetSelectionPoint2` is the only working ray path.
- **Hit-point semantics:** at a small radius the hit is essentially on the optical axis; at a large
  radius `GetSelectionPoint2` can return a point past the bbox — that's why §7 validates against the
  bbox+margin and prefers the smallest hitting radius.

### 8.10 Bounding box: part vs assembly, and one Extension method that doesn't resolve
- Parts use `IPartDoc.GetPartBox(True)` (tight geometry box); assemblies use `IAssemblyDoc.GetBox(0)`
  (chosen by `model.GetType`: 1=part, 2=assembly, 3=drawing). Both need `_FlagAsMethod`.
- **`IModelDocExtension.GetBox` is NOT reachable** via late dispatch — don't try to "simplify" the
  two-API split into it. Drawings/empty docs return `None`, and `object`/`view` pivots fall back
  gracefully (origin-like / object-depth).

### 8.11 Property reads dominate cost — cache handles, track state, predict analytically
- Measured per-call over out-of-process COM: `ActiveDoc`/`ActiveView` ~17 ms, `Translation3` read
  ~19 ms, `Orientation3` ~20 ms, `CreateVector`/set `Translation3` ~13 ms, `RotateAboutAxis` ~6 ms,
  **`GraphicsRedraw2` only ~5 ms.** The **redraw is not the bottleneck — property reads are.**
- So: cache `model`/`view` (revalidate every `_VIEW_TTL` = 1 s, which doubles as the liveness probe);
  **track** `Translation3`/`Scale2` across our own writes instead of re-reading; predict the
  post-rotation columns analytically (Rodrigues) instead of a second `Orientation3` read. This is what
  makes a pivot-holding orbit (which must pan every frame) as smooth as the old origin-only orbit
  (~13 Hz on a heavy part).
- Selection-path costs (the once-per-gesture raycast, warm): ClearSelection2 ~1.6 ms, SelectByRay
  ~6.2 ms, GetSelectedObjectCount2 ~3.7 ms, GetSelectionPoint2 ~3.3 ms → **~16 ms total, once per
  gesture.** Negligible; don't pre-optimize it.

### 8.12 The `view`-pivot aperture is scaled by the bbox diagonal, not the viewport extent
- The "natural" scale for the ray aperture is the on-screen view half-height, but the **viewport's
  pixel extent isn't cheaply available over COM**. We use the **bounding-box diagonal** as the length
  scale for the aperture sweep, the origin pushback, and the validation margin. It's robust (always
  available) but means the aperture doesn't shrink when you zoom into a tiny feature — fine in
  practice (a small radius still hits the face right there). Retune via `_RAY_APERTURE_FRACS` /
  `_RAY_PUSH` / `_RAY_BBOX_MARGIN` if needed.

### 8.13 The `cursor` pivot mapping — `IModelView.Transform` is CLIENT-relative PHYSICAL px, `IMouse` was a trap
- `cursor` / `to_cursor` are **implemented** (§7.5). What could only be settled by observing:
  - The obvious route — sink `IModelView.GetMouse` → `IMouse`'s move event — is a **trap** for an
    out-of-process driver: SW dispatches have **no typeinfo** (makepy the whole typelib) and it fires
    a cross-process COM callback ~100+ Hz. `GetCursorPos` on demand at gesture start is right.
  - The cursor-pixel → model-ray map inverts `IModelView.Transform`. Its pixel space had **two**
    traps, both of which produced the user-reported "pivot up-and-left of the cursor" and both only
    caught by a **live screenshot** (the first-cut "verified < 0.1 mm" was **tautological** — it
    inverted `Transform` at the very pixel it had just `SetCursorPos`'d to, so it round-tripped by
    construction and proved nothing about on-screen placement):
    - **`Transform` is CLIENT-relative** (to `GetViewHWnd`'s client top-left), not desktop — map the
      cursor through `ScreenToClient(GetViewHWnd)` first. (`Transform(peak)` as desktop landed on the
      ribbon button *above* the peak.)
    - **`Transform` is PHYSICAL px; `GetCursorPos` follows the calling thread's DPI awareness** — at
      125 % a DPI-unaware reader is off by the scale. Fix: make the **worker thread** per-monitor DPI
      aware (`_make_thread_dpi_aware`, thread-local — the Tk UI thread stays as-is).
  - The over-the-view **gate is the client rect, not `WindowFromPoint` identity** — SW's inner render
    child makes `WindowFromPoint` return a window that isn't `GetViewHWnd` or a resolvable descendant.
  - **Lesson**: a self-consistent round-trip is not a verification. Drive the cursor to the feature's
    *true* screen position and let the code map back independently; confirm with eyes/screenshot.
- `selection` uses the mean of the current selected-entity points (daemon 0.1.58). The app-root
  `selection_overrides_pivot` toggle makes this replace the designated orbit/to-cursor pivot.

### 8.14 No add-in, by design
- SolidWorks add-ins need **admin COM registration**; we deliberately avoid that. Everything is the
  in-process COM driver. There is nothing to install, copy, or auto-update for SolidWorks
  (`integrations.setup_solidworks` only verifies + enables). The trade-off is the out-of-process COM
  ceiling (§8.11) — an in-process add-in would be faster but needs admin (3Dconnexion's own SW
  SpaceMouse support is an add-in for exactly this reason).

---

## 9. Tuning knobs (top of `solidworks_driver.py`)

Defaults were refined on-hardware but signs/magnitudes may want per-feel tweaks; **flip a sign if a
channel goes the wrong way.**

| Constant | Value | Meaning |
|---|---|---|
| `ORBIT_SIGN` | `(-1,-1,1)` | per-channel orbit direction (pitch about right / yaw about up / roll about forward) |
| `WORLD_UP` | `(0,1,0)` | turntable azimuth axis — SolidWorks is **Y-up** (verified) |
| `PAN_SIGN` / `PAN_SCALE` | `(1,-1)` / `0.2` | pan direction / magnitude (screen meters; do NOT divide by Scale2) |
| `ZOOM_SIGN` / `ZOOM_SCALE` | `1.0` / `0.5` | zoom direction / per-frame aggressiveness |
| `FORCE_REDRAW` | `True` | one `GraphicsRedraw2` per frame; `False` lifts the rate if SW already repaints (§8.7) |
| `VIEW_PIVOT_RAYCAST` | `True` | `view` pivot uses the surface raycast (§7); `False` = screen-centre at object depth |
| `_RAY_APERTURE_FRACS` | `(.005,.015,.045,.135)` | aperture sweep (× bbox diagonal), smallest-first (§7, §8.12) |
| `_RAY_PUSH` | `4.0` | ray origin pushback (× bbox diagonal) — starts outside the model |
| `_RAY_BBOX_MARGIN` | `0.10` | accept a hit only within bbox + N× diagonal |
| `_CURSOR_XF_ALIGN_TOL` | `0.05` | `cursor` pivot: how far `Transform`'s in-plane rows may drift from the camera axes before the mapping is distrusted (§7.5) |
| `DEFAULT_PIVOT_HOLD` | `0.5` | `view`/`cursor` pivot re-capture idle threshold (per-app `view_pivot_hold_sec`) |
| `DEFAULT_FLUSH_HZ` | `30` | flush/refresh rate (per-app `rate_hz`; `0` ⇒ global `bridge.rate_hz`) |
| `_VIEW_TTL` | `1.0` | view-handle revalidation period (also the liveness probe) |
| `_OBJ_CACHE_TTL` | `0.5` | bbox cache lifetime |
| `_RETRY_PERIOD` | `2.0` | seconds between attach attempts while SW isn't running |

Per-app sensitivity / invert / scheme and the viewport rate come from config (Per-App Bindings → the
`solidworks` app), same as the other apps.

---

## 10. Diagnostics & how to test live (this is very testable — there IS a SolidWorks on the dev box)

**Do not assume "can't test without SolidWorks."** SolidWorks 2025 is installed on the dev machine
and is usually running with a part open. The driver can be exercised end-to-end:

- **Attach + drive the real code path from a throwaway script:**
  ```python
  import pythoncom
  from trackball_daemon.solidworks_driver import SolidWorksDriver
  pythoncom.CoInitialize()
  drv = SolidWorksDriver(); drv._attach()
  drv.set_scheme("view", "free", "to_center"); drv.set_pivot_hold(0.5)
  drv._flush((0.05, 0.0, 0.0, 0.0, 0.0, 0.0))   # one orbit frame
  print(drv._orbit_pivot)                         # the captured pivot
  ```
  `drv._swApp.ActiveDoc` / `.ActiveView` give you the live model/view to read `Orientation3`,
  `Translation3`, `Scale2`, the bbox, etc.
- **Verify a fact by OBSERVING the viewport, not by self-referential math** (the lesson of §8.4): use
  computer-use `request_access(["SOLIDWORKS 2025"])` + `screenshot`, or compute the screen projection
  `Scale2*(col·P)[xy]+Translation3` of a point before/after an op and check it's pinned.
- **Probe a new/uncertain COM call before relying on it:** write a tiny script that flags the method,
  calls it with candidate argument **types** (this is how the `Tol`-must-be-`VT_I4` fact in §8.9 was
  found), and prints success/failure. Throwaway probe scripts belong in the scratchpad, not the repo.
- **A healthy attach logs:** `solidworks: attached to running instance (vNN.N.N)`. Op failures log
  `solidworks: <orbit|pan|zoom|redraw> step failed and is being skipped (<repr>)` **once** each (to
  `%APPDATA%\TrackballDaemon\daemon.log`). "connected but nothing moves" with no such line usually
  means §8.2 (empty instance / no `ActiveDoc`) or that SolidWorks isn't the focused app.
- **Restore the view after testing:** scripts here leave SolidWorks at `*Isometric` + zoom-to-fit
  (`ShowNamedView2("*Isometric",7)`, `ViewZoomtofit2()`), and `_raycast_depth` restores the user's
  selection on its own.

The unit tests (`tests/test_solidworks_driver.py`) mock the COM boundary
(`FakeApp/FakeModel/FakeView/FakeSelMgr/FakeExtension/FakeEntity`) so **no SolidWorks is required** to
run them — they cover orbit/pan/zoom, all five pivots, the raycast (surface depth, miss-fallback,
out-of-bbox rejection, aperture expansion, nearest-of-multiple, the integer-`Tol` regression,
selection save/restore), the **`cursor` pivot** (Transform inversion, per-capture sign resolution,
row-alignment rejection, hold + object-centre fallback, `to_cursor` zoom hold), the freeze,
attach/drop, and the worker loop. What they **cannot** cover is the real COM behaviour — that's what
§10's live testing is for. The `cursor` mapping specifically **was** verified live in the GUI by
driving the OS cursor to each box corner's **true on-screen position** (`Transform(corner) +
client-origin`, screenshot-confirmed) and confirming the driver's own `_cursor_pivot` — which maps
back independently via `ScreenToClient` + the `Transform` inverse — returned each corner to
**< 0.1 mm** (throwaway scratchpad probes; not in the repo).

---

## 11. Status & known limitations (at handoff)

- **Working & live-verified:** attach/connection status; orbit with all five pivots; the `view`
  surface-depth raycast (pivot at true surface depth, held-pivot screen drift ~0); the **`cursor`
  pivot** (OS cursor → `Transform` inverse → raycast, verified to < 0.1 mm against known corners,
  §7.5/§10) and **`to_cursor` zoom**; pan; zoom (`to_center`/`to_object`); orbit styles
  (`free`/`turntable`); the viewport freeze (~2× smoother); exact pivot hold (~1e-16).
- **Needs a feel/sign pass on hardware** if anything feels off: `*_SIGN` / `*_SCALE` magnitudes and
  the turntable `WORLD_UP` axis. The `cursor` pivot's **geometry is exact**; only its feel during a
  live human orbit is un-exercised.
- **Limitations:** `selection` == `object` (no per-entity selection pivot over COM; §8.13); the
  raycast aperture is bbox-scaled, not viewport-scaled (§8.12); drawings have no box (pivots degrade
  gracefully). The out-of-process COM rate is below an in-process add-in's, by design (§8.14).

---

## 12. File / wiring map

- **`solidworks_driver.py`** — the whole driver: worker loop (`_run`/`_flush`), attach
  (`_attach`/`_find_running_sw`), the verified camera ops (`_apply_orbit`/`_apply_pan`/`_apply_zoom`),
  the `view`-pivot raycast (`_view_pivot`/`_raycast_depth`/`_get_pick_handles`/`_save`/`_restore`),
  the `cursor`-pivot mapping (`_cursor_pivot`/`_cursor_screen_ab`/`_cursor_client_point`, reusing
  `_raycast_depth`), caching (`_live_view`/`_object_box`), and the math helpers (`_rodrigues`,
  quaternion, `_variant`).
- **`app.py`** — constructs `self.sw_driver = SolidWorksDriver(self._on_sw_connection_changed, ...)`;
  routes `solidworks` frames in `_nav_sink`; pushes rate (`_apply_rates`) and scheme + pivot-hold
  (`_apply_schemes` → `set_scheme` + `set_pivot_hold`); merges status in
  `_on_sw_connection_changed`/`_refresh_connected_apps`; `start()`/`stop()` lifecycle. SolidWorks is
  matched as the foreground app by the **process name `sldworks`** (`_APP_PROC_HINTS`).
- **`config.py`** — the `solidworks` app uses the shared `_app()` shape: `rate_hz` (0 ⇒ global),
  `view_pivot_hold_sec` (0.5), and `bindings.scheme` (per-app override, `"default"` inherits general).
  The general default scheme is `pivot=view, style=free, zoom=to_center`.
- **`integrations.py`** — `detect_solidworks` (globs `SLDWORKS.exe`) and `setup_solidworks` (verify SW
  + pywin32, mark enabled; **no add-in to copy**). SolidWorks is **not** in the add-in copy/update set.
- **`ui.py`** — the "Orbit pivot" dropdowns (labels map to the stored values
  `view / cursor / object / origin / selection`; the under-mouse pivot shows as "cursor (under
  mouse)") and the "View-pivot hold (s)" entry. SolidWorks honours all five pivots (`origin` and
  `selection`-vs-`object` are SolidWorks-distinct).
- **`winfocus.py`** — `foreground_process_name` (used to route to SolidWorks when `sldworks` is
  frontmost).
- **`requirements.txt`** — `pywin32` (Windows). Missing ⇒ the driver disables itself and logs once.
