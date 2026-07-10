# Godot navigation — maintainer's guide

Socket **EditorPlugin** for Godot 4's editor 3D viewport. Same NavBroker protocol as
Unreal/Unity. Add-on `0.1.0`.

## Layout

```
trackball_daemon/plugins/godot/trackball_nav/
  plugin.cfg
  version.json
  trackball_nav.gd          EditorPlugin: socket + _process pump + pivots
  trackball_nav_camera.gd   free-fly camera math (Y-up, right-handed)
```

## Install

`integrations.install_godot` copies into `<project>/addons/trackball_nav/` and enables
`res://addons/trackball_nav/plugin.cfg` under `[editor_plugins]` in `project.godot`.
Project paths come from running Godot `--path` / `project.godot` args and Godot's
`%APPDATA%\Godot\` recent-project files.

## Controls (Unreal/Blender parity)

Orbit / fly / walk, under-cursor via `EditorInterface.get_editor_viewport_3d(0)` mouse
ray, selection AABB override, same `adv` block as Unreal/Unity.

## Logs

`%APPDATA%\TrackballDaemon\godot_addin.log`

## Reload

Disable/enable the plugin or restart Godot. Bump `plugin.cfg` version + `version.json` +
`ADDIN_VERSION` in `trackball_nav.gd`.
