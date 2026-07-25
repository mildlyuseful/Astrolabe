# Rhino navigation — maintainer's guide

Socket Python add-on for **Rhino 8** (lean suite: orbit / pan / zoom). It drives an eye+target camera
through `SetCameraLocations`. Camera math is neutral; intrinsic signs/scales live in
`host_profiles.json` and are applied by the daemon.

## Layout

```
trackball_daemon/plugins/rhino/TrackballNav/
  version.json
  start.py            entry (sys.path + tbnav_rhino.start)
  tbnav_rhino.py      socket reader + RhinoApp.Idle pump + pivots
  tbnav_camera.py     pure eye+target math (no Rhino import; pytest)
```

## Install

`integrations.install_rhino` copies to
`%APPDATA%\McNeel\Rhinoceros\8.0\scripts\TrackballNav\` and best-effort appends a
`_-RunPythonScript "…\start.py"` line to Rhino's StartupCommands in
`settings-Scheme__Default.xml`. If that edit fails, Set up shows a **Copy startup command**
button and tells you to type **Options** in Rhino's command line, then General → startup
commands (there is no top-level Options menu).

Restart Rhino once after Set up (or after an add-on version bump) so Python reloads the module.

## Controls (default / lean)

Lean scheme only (no fly/walk action tree). Pivots: `screen_center`, `cursor`, `selection`, `object`,
`origin`, plus `camera` (turn in place). Model Center uses document bounds and is distinct from
Selection. `selection_overrides_pivot` is applied.
Pan-mode **Zoom / Dolly** calls RhinoViewport `Magnify` in lens-zoom or camera-dolly mode; when a
zoom target is available its client point is supplied as the fixed screen coordinate.

Orbit ray misses continue the configured global chain. Empty-space To Cursor zoom synthesizes a
point on the mouse ray at the current target/document depth. Orbit and cursor-zoom targets have
independent holds: pan invalidates only the orbit pivot and orbit invalidates the zoom target.

Entering Turntable from Free optionally levels the horizon once via `tbnav_camera.level_horizon`,
without moving eye/target or changing distance/pivot. The first frame establishes state and a
world-up singularity is skipped.

### Under-cursor pivot

1. `view.ScreenToClient(mouse)` → `GetFrustumLine` (same client space McNeel samples use).
2. Front face: display meshes (`GetMeshes`) or `Mesh.CreateFromBrep` + camera-eye `MeshRay`
   through the farther frustum endpoint; keep the hit closest to the camera.
3. Fallback: raw frustum `RayShoot(Ray3d(line.From, line.Direction))` — this is the path that
   reliably finds Breps. That ray often runs far→near, so a lone hit can be the back face;
   eye `MeshRay` candidates win when present (closer = front).

**Pitfall:** do not use bare `vector.IsTiny` in Rhino Python — the name resolves to a method
object (always truthy) and will abort the raycast before any shoot. Use `vector.Length > …`
instead. Do not re-orient the `RayShoot` ray to near→far; that misses geometry entirely.

Misses and per-hit debug lines go to `rhino_addin.log`.

## Logs

`%APPDATA%\Mildly Useful\Astrolabe\rhino_addin.log`

## Tests

```
python -m pytest tests/test_rhino_nav_math.py tests/test_integrations_rhino.py -q
```

Startup registration, live pivot/zoom behavior, horizon entry, and sign/feel checks are tracked in
[`TODO.md`](../../TODO.md).
