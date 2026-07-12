# Godot navigation — maintainer's guide

Socket **EditorPlugin** for Godot 4's editor 3D viewport. Same NavBroker protocol as
Unreal/Unity.

## Editor camera limits

Godot's 3D editor viewport stores only **yaw/pitch** (no roll). Free trackball orbit and
twist→roll are disabled: the plugin always uses **turntable**, maps twist to zoom/dolly/none
(never roll), and horizon-locks fly/walk look. Daemon UI matches (no free orbit / roll / bank).
This is a current host limitation, not an unimplemented UI option. Pan-mode **Zoom** changes the
editor Camera3D FOV/orthographic size; **Dolly** translates the camera.

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

### How projects are found

There is **no fixed projects directory**. Candidates come from:

1. Running Godot processes — `wmic` command lines with `--path "…"` or a bare
   `project.godot` argument.
2. Recent-project files under `%APPDATA%\Godot\` — especially Godot 4
   `projects.cfg`, where each project is an INI **section name** that is the
   absolute path (e.g. `[C:/Users/…/MyGame]`), plus path heuristics in
   `editor_settings-4.tres` / `editor_settings-3.tres`.

Open the project in the editor, then click **Set up** again so detection can see it.

### Manual install (when detection finds nothing)

Set up still stages a copy at
`%APPDATA%\TrackballDaemon\godot\trackball_nav\` and shows **Copy** buttons.

1. Copy that `trackball_nav` folder to `<YourProject>\addons\trackball_nav\`.
2. Enable the plugin: **Project → Project Settings → Plugins → Trackball Nav**, or add
   under `[editor_plugins]` in `project.godot`:

   ```
   enabled=PackedStringArray("res://addons/trackball_nav/plugin.cfg")
   ```

3. Reload the project or restart Godot.

## Controls (Unreal/Blender parity)

Every visible Orbit/Camera/Fly/Walk action has an independent X/Y/Z source and invert under
`advanced.axis_source` / `advanced.invert`. Godot still drops unsupported roll/bank output; source
routing does not bypass the editor camera's yaw/pitch limitation.

Orbit / fly / walk, under-cursor via `EditorInterface.get_editor_viewport_3d(0)` mouse
ray, selection AABB override, same `adv` block as Unreal/Unity.

## Logs

`%APPDATA%\TrackballDaemon\godot_addin.log`

## Reload

Disable/enable the plugin or restart Godot. Bump `plugin.cfg` version + `version.json` +
`ADDIN_VERSION` in `trackball_nav.gd`.
