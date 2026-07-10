# Unity navigation — maintainer's guide

Socket add-on for the **Unity Editor Scene view** (not Play / Game view). Same NavBroker
protocol as Unreal/Blender. Add-on `0.1.0`.

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
`Packages/com.astrolabe.trackball-nav/`. Project paths come from running `Unity.exe`
`-projectpath` and Unity Hub recent-projects JSON. UPM loads it automatically;
`[InitializeOnLoad]` starts the broker client after domain reload.

If no project is found, Set up stages under `%APPDATA%\TrackballDaemon\unity\` and asks
you to open a project and Set up again.

## Controls (Unreal/Blender parity)

Orbit / fly / walk, pivots (`viewpoint`, `view`, `cursor`, `object`, `origin`), free/turntable,
`twist_action`, `selection_overrides_pivot`, `to_cursor` zoom. Under-cursor uses
`SceneView.duringSceneGui` mouse + `Camera.ScreenPointToRay` + Physics / PickGameObject.

## Logs

`%APPDATA%\TrackballDaemon\unity_addin.log`

## Reload

Edit scripts → Unity recompiles (domain reload). Version bump: `package.json` + `version.json`
(+ comment in `TrackballNav.cs` AddinVersion).
