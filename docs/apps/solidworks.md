# SolidWorks 3D navigation — maintainer's guide

This guide owns SolidWorks-specific COM setup, camera behavior, threading, and compatibility warnings.
Shared focus, state, mapping, routing, and lifecycle contracts live in
[`../architecture.md`](../architecture.md). User-facing setup belongs in
[`README.md`](../../README.md), and unresolved qualification belongs in [`TODO.md`](../../TODO.md).

Primary code: [`trackball_daemon/solidworks_driver.py`](../../trackball_daemon/solidworks_driver.py).
Focused coverage: [`tests/test_solidworks_driver.py`](../../tests/test_solidworks_driver.py) and
[`tests/test_app_routing.py`](../../tests/test_app_routing.py).

## Architecture

SolidWorks add-ins require COM registration, so Astrolabe deliberately uses a daemon-side external COM
driver instead of an in-host add-in. The driver attaches to an already-running SolidWorks instance and
moves the active view through native COM camera methods. There is no plugin to install, and the socket
broker is not involved.

```text
MotionSample + immutable RuntimeSnapshot
    → target-tagged NavigationEnvelope
    → NavigationRouter active-target/revision gate
    → SolidWorksDriver.submit() accumulation
    → COM worker thread
    → active IModelView camera update
```

`submit()` only accumulates six motion channels under a lock. The worker thread calls
`pythoncom.CoInitialize()`, owns every COM object, swaps accumulated motion at the configured rate, and
coalesces slow frames rather than dropping them. COM must never be called from the producer or UI
thread.

The driver attaches only; it never launches SolidWorks. `GetActiveObject("SldWorks.Application")` can
return an empty instance, so `_find_running_sw` enumerates the Running Object Table and prefers the
SolidWorks instance with the most open documents. Active document/view revalidation also serves as the
liveness probe. Missing `pywin32` makes the driver a logged no-op.

## Camera contract

A model-space point `P` projects through the active view as:

```text
screen_xy = Scale2 * (camera_column · P)[x,y] + Translation3
```

The durable COM facts are:

- `Orientation3.ArrayData` is row-major storage, but its **columns** are the camera axes in model space:
  right, up, and forward-out-of-screen.
- `Translation3` is the screen-plane pan offset. Pan by reading, adding, and assigning a new vector;
  late-bound `TranslateBy` does not marshal reliably.
- `Scale2` is the model-to-screen scale. `ZoomByFactor(f)` changes both scale and translation.
- `RotateAboutAxis` uses the axis but ignores its point argument, so it always rotates about the model
  origin.
- `EnableGraphicsUpdate = False/True` brackets a multi-operation frame; one explicit
  `GraphicsRedraw2()` follows re-enable.
- Part and assembly bounds come from different APIs: `GetPartBox(True)` and `GetBox(0)`.

Because `RotateAboutAxis` ignores its point, an arbitrary pivot `P` is held by rotating and then
correcting `Translation3`:

```text
ΔTranslation3 = Scale2 * ((columns_before - columns_after) · P)  # x,y
```

The driver predicts the post-rotation columns with Rodrigues math instead of rereading the orientation.
Do not duplicate SolidWorks alignment in this math: developer-owned signs and gains live in
`trackball_daemon/host_profiles.json`, and the driver receives already-composed values.

## Control model

Supported orbit pivots are:

- `origin` — pure rotation about world origin;
- `object` — aggregate model bounding-box center;
- `selection` — mean of usable selected-entity points;
- `screen_center` — surface under the viewport center; and
- `cursor` — surface under the live mouse cursor.

Unavailable candidates continue through the configured global pivot chain. `screen_center` and
`cursor` use `IModelDocExtension.SelectByRay` plus `ISelectionMgr.GetSelectionPoint2`, validate hits
against model bounds, and hold the resolved pivot for the gesture. The raycast saves, clears, and
restores the user's selection.

Free orbit composes motion about the current camera axes. Turntable orbit uses SolidWorks' Y-up world
axis for yaw, camera-right for pitch, and drops roll. A real Free → Turntable transition can queue one
horizon-leveling pass; startup initialization must not create a false transition.

Zoom supports To Center, To Object, and To Cursor. Orbit and To-Cursor zoom have independent hold
state. Pan or zoom invalidates the orbit pivot; pan preserves the zoom target; orbit invalidates the
zoom target. A cursor miss can synthesize a point on the cursor ray at the current view depth rather
than silently becoming To Center.

## Load-bearing COM warnings

### Flag late-bound methods explicitly

SolidWorks dispatches often expose no usable type information, so pywin32 can misclassify methods as
properties. Call `_FlagAsMethod("Name")` before methods such as `GetMathUtility`, `CreateVector`,
`GetDocumentCount`, `GetPartBox`, `GetBox`, `SelectByRay`, selection-manager accessors, and entity
`Select`. Conversely, members such as `GetType` and `GetTitle` can resolve as properties and must be
read without parentheses.

### Build COM arguments with exact VARIANT types

`CreateVector` needs a `VT_ARRAY | VT_R8` value. `SelectByRay` needs all eleven arguments and must
receive coordinates/direction/radius as `VT_R8`, tolerance/options/mark as `VT_I4`, and append as
`VT_BOOL`. A float tolerance can fail by selecting nothing without raising.

Use `GetSelectedObjectCount2(-1)`, not an assumed selection-count alias. Clear before every cast even
when `Append=False`, prefer the smallest aperture that hits, choose the nearest valid hit, and restore
the prior selection with `ent.Select(True)`. Selection marks are not preserved.

### Keep part and assembly bounds separate

Parts use `IPartDoc.GetPartBox(True)`; assemblies use `IAssemblyDoc.GetBox(0)`. Both need method
flagging. `IModelDocExtension.GetBox` is not reachable through the late-bound path, so do not collapse
the two APIs into it. Drawings and empty documents may have no usable bounds; pivot resolution must
fall through safely.

### Capture pivots once and invalidate them deliberately

Recasting every frame chases the changing camera and adds COM round-trips. Capture once per gesture,
then invalidate on the matching hold timeout, scheme change, or operation that moves the pivot.
`origin` and `object` are distinct: the former skips recentering, while the latter holds geometry.

### Freeze the complete frame, then redraw once

Orbit about an arbitrary pivot is a rotate followed by a recenter pan. Without graphics suspension,
SolidWorks can repaint between those operations and show a transient origin-rotated view. Disable
graphics updates for the whole frame, re-enable them in a guarded cleanup path, and call one explicit
`GraphicsRedraw2()` afterward. Re-enable alone does not reliably repaint an idle automation-driven
view.

### Guard operations independently

Orbit, pan, zoom, graphics re-enable, and redraw failures are logged once and skipped independently.
One unsupported operation must not prevent the remaining operations or strand graphics updates.
Disconnect only when active document/view access establishes that the host or document is gone.

### Cache handles and tracked camera state

Out-of-process property reads dominate the path. Cache model/view and pick handles, revalidate them on
a bounded TTL, track `Translation3` and `Scale2` across local writes, and predict post-rotation axes
analytically. Do not remove the periodic liveness/resynchronization read: users can also move the view
through SolidWorks.

### Cursor coordinates are client-relative physical pixels

`IModelView.Transform` maps model points into physical pixels relative to `GetViewHWnd`'s client area,
not desktop coordinates. Map the OS cursor through `ScreenToClient(GetViewHWnd)` and make only the COM
worker thread per-monitor-DPI-aware so Win32 pixels match `Transform` without changing Tk's DPI
context.

Gate on the view client rectangle, not `WindowFromPoint == GetViewHWnd`; SolidWorks composites the
viewport with an inner render child. Re-read the transform and client rectangle for every capture so
window moves and resizes require no separate bookkeeping. Reject transform rows that no longer align
with the camera basis rather than producing a wrong pivot.

Do not replace the on-demand Win32 cursor read with an out-of-process `IMouse` event sink. That path
requires unavailable type information and would marshal high-rate callbacks through SolidWorks' UI
thread even though navigation needs one cursor sample per gesture.

### Preserve one direct transport

SolidWorks has no Astrolabe add-in by design. `integrations.setup_solidworks` only verifies the host and
`pywin32`, then enables the integration. Do not introduce broker or plugin ownership unless the
architecture is intentionally redesigned.

## Setup, diagnostics, and verification

Settings → **3D Apps → SolidWorks → Set up** verifies a detected SolidWorks installation and
`pywin32`; there is nothing to copy, register, or update. Restart the daemon after driver changes.

Useful diagnostics:

- a healthy attach logs the running SolidWorks revision;
- a connected state with no motion usually means the wrong ROT instance, no active document, or failed
  foreground routing;
- operation failures are logged once per orbit/pan/zoom/redraw category; and
- `%APPDATA%\TrackballDaemon\daemon.log` is the daemon-side evidence source.

Automated tests mock the COM boundary and cover camera operations, pivots, raycast typing and
selection restoration, cursor-transform inversion, hold lifetimes, horizon leveling, attach/drop, and
the worker loop. They do not establish real COM behavior or visible viewport motion.

A current live claim requires an installed SolidWorks instance with a disposable unsaved part or
assembly. Exercise the production driver, observe the viewport, inspect camera state independently,
and record host version, projection mode, source revision/artifact, and result in a dated file under
`archive/release-evidence/`. Do not infer a live pass from self-consistent matrix round-trips or mocked
tests.

## Current limitations

External COM has a lower throughput ceiling than an in-host add-in. Selection uses the mean of exposed
selection points rather than selected-geometry bounds. The ray aperture scales from model bounds rather
than viewport size, and drawings without usable bounds continue through the fallback chain. Camera
pivot and 3D Cursor capabilities remain unsupported. Exact executable identity and current live-host
qualification work are tracked only in [`TODO.md`](../../TODO.md).

## File and ownership map

- `trackball_daemon/solidworks_driver.py` — COM worker, attach/liveness, camera operations, pivots,
  cursor mapping, raycast, caching, and math helpers.
- `trackball_daemon/navigation_router.py` — active-target and state-revision delivery boundary.
- `trackball_daemon/app_registry.py` — canonical identity, selector, transport, modes, and capabilities.
- `trackball_daemon/integrations.py` — detection and setup verification; SolidWorks is not an add-on
  copy/update target.
- `trackball_daemon/config_store.py`, `settings_schema.py`, and `system_defaults.json` — sparse typed
  configuration and resolved profile values.
- `trackball_daemon/host_profiles.json` — immutable SolidWorks alignment applied before driver math.
