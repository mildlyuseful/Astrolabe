# Unity navigation — maintainer's guide

Socket add-on for the **Unity Editor Scene view** (not Play / Game view). Same NavBroker
protocol as Unreal/Blender. Add-on `0.1.6`.

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

If no project is found, Set up stages under `%APPDATA%\TrackballDaemon\unity\` and asks
you to open a project and Set up again. Manual: copy the staged folder into
`<YourProject>\Packages\com.astrolabe.trackball-nav\`.

## Controls (Unreal/Blender parity)

Orbit / fly / walk, pivots (`viewpoint`, `view`, `cursor`, `object`, `origin`), free/turntable,
`twist_action`, `selection_overrides_pivot`, `to_cursor` zoom. Under-cursor stores a world ray on
mouse move via `HandleUtility.GUIPointToWorldRay`, then hits with Physics / own mesh triangle
tests (AABB fallback). Re-cast from the update pump. Never uses `PlaceObject` or
`IntersectRayMesh` (missing on some Editor builds).

**Distance math:** `SceneView.size` is a fit-sphere radius, not eye→pivot distance. Navigation
uses `cameraDistance` (`size / sin(fov/2)` in perspective) and writes size back by scaling
`size * (newDist / oldDist)`.

**Dynamic Clipping:** Scene View Camera overlay option that sets near/far from `size`
(`far ≈ 2000 * size`). Can feel like auto zoom-to-fit while looking around. Daemon toggle
`advanced.override_dynamic_clip` (default on) forces it off and installs fixed near/far while
navigating; turning the toggle off restores Dynamic Clipping.

**Pivot extent:** `advanced.pivot_extent_mult` (default `8`) caps under-cursor / auto-depth
pivots at `scene_AABB_radius × mult` from the camera. Hits beyond that (e.g. near the horizon)
are rejected so the view does not rocket away.

## Logs

`%APPDATA%\TrackballDaemon\unity_addin.log`

## Reload

Edit scripts → Unity recompiles (domain reload). Version bump: `package.json` + `version.json`
(+ comment in `TrackballNav.cs` AddinVersion).
