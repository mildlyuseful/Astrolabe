# Unity navigation — maintainer's guide

Socket add-on for the **Unity Editor Scene view** (not Play / Game view). Same NavBroker
protocol as Unreal/Blender.

## Layout

```
trackball_daemon/plugins/unity/com.astrolabe.trackball-nav/
  package.json              UPM package descriptor
  version.json              daemon auto_update manifest
  Editor/
    Astrolabe.TrackballNav.Editor.asmdef
    TrackballNav.cs         socket reader + EditorApplication.update pump + pivots
    TrackballNavCamera.cs   free-fly camera math (Y-up, left-handed)
```

## Install

`integrations.install_unity` copies the package into each detected project's
`Packages/com.astrolabe.trackball-nav/`. Project paths come from:

1. Running `Unity.exe` command lines with `-projectpath` (PowerShell CIM; WMIC fallback).
2. Unity Hub `%APPDATA%\UnityHub\projects-v1.json` — Hub v1 wraps entries under
   `{"schema_version":"v1","data":{ "<path>": {"path":"...", ...}, ...}}`.

UPM loads it automatically; `[InitializeOnLoad]` starts the broker client after domain reload.

If no project is found, Set up stages under `%APPDATA%\Mildly Useful\Astrolabe\unity\` and asks
you to open a project and Set up again. Manual: copy the staged folder into
`<YourProject>\Packages\com.astrolabe.trackball-nav\`.

## Controls (Unreal/Blender parity)

Orbit / fly / walk / object, pivots (`camera`, `screen_center`, `cursor`, `selection`, `object`, `origin`),
free/turntable, `twist_action`, `selection_overrides_pivot`, and To Cursor zoom. Model Center uses
aggregate scene bounds and is distinct from the current selection. Under-cursor stores a world ray on
mouse move via `HandleUtility.GUIPointToWorldRay`, then hits with Physics / own mesh triangle
tests (AABB fallback). Re-cast from the update pump. Never uses `PlaceObject` or
`IntersectRayMesh` (missing on some Editor builds).

Orbit ray misses continue the configured global chain. Empty-space To Cursor zoom synthesizes a
point on the cursor ray at the tracked focus depth. Orbit and cursor-zoom targets have independent
hold settings: pan invalidates the orbit pivot but preserves the zoom target, and view rotation
invalidates the zoom target.

Per-mode `advanced.axis_source` and `advanced.invert` route every Orbit/Camera/Fly/Walk/Object action from
X/Y/Z independently. Rotation actions use `o`; shifted movement actions use `(p.x,p.y,z)`. The
identity/default map is behavior-neutral, while mappings such as Walk Forward ← Z make twist drive
forward.

In **Object mode**, primary motion rotates selected root transforms as one group around their shared
position center in Scene-view axes. Object Pitch/Yaw/Roll and Translate X/Y/Z route independently.
The secondary layer's **View** frame translates in Scene-view right/up with twist for depth; its
**Ground** frame matches Walk movement: planar sideways/forward on horizontal Scene-view axes, with
twist along world Y. The per-app Object movement sensitivity multiplies translation only. Empty
selection does nothing, selected descendants of another selected transform are filtered out, and
`Undo.RecordObjects` plus a collapsed undo group makes a continuous gesture one Unity undo action.
Scene-view camera and Dynamic Clipping state are not changed.

**Pan-mode zoom:** Zoom changes `SceneView.CameraSettings.fieldOfView` in perspective or
`SceneView.size` in orthographic mode. Dolly changes the eye-to-pivot distance. An object/cursor
target stays at the same screen position in either path.

**Distance math:** `SceneView.size` is a fit-sphere radius, not eye→pivot distance. Navigation
uses `cameraDistance` (`size / sin(fov/2)` in perspective) and writes size back by scaling
`size * (newDist / oldDist)`.

**Dynamic Clipping:** Scene View Camera overlay option that sets near/far from `size`
(`far ≈ 2000 * size`). Can feel like auto zoom-to-fit while looking around. Daemon toggle
`advanced.override_dynamic_clip` (default on) forces it off and installs fixed near/far while
navigating; turning the toggle off restores Dynamic Clipping.

**Pivot extent:** `advanced.pivot_extent_mult` (default `8`) caps under-cursor / screen-center
pivots at `scene_AABB_radius × mult` from the camera. Hits beyond that (e.g. near the horizon)
are rejected so the view does not rocket away.

**Fixed-horizon entry:** moving from a rolled Free/Orbit/Fly state into Turntable, Lock Horizon, or
Walk optionally removes roll once through `TrackballNavCamera.LevelHorizon`. Location, forward
direction, focus distance, and pivot remain stable; the first frame only establishes state.

Intrinsic signs/scales arrive in `adv.host_baseline` and are applied after mode-specific routing.
Keep `TrackballNavCamera.cs` neutral and tune suite alignment in `host_profiles.json`.

## Logs

`%APPDATA%\Mildly Useful\Astrolabe\unity_addin.log`

## Reload

Edit scripts → Unity recompiles (domain reload). Keep the code/package version marker and
`version.json` synchronized. Live checks are tracked in [`TODO.md`](../../TODO.md).
