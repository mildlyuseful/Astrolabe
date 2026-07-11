# Host baselines and shipped default profiles

Step 4 separates software alignment from user preference. The source of truth is
`trackball_daemon/config.py`:

- `HOST_BASELINE_PROFILES` is an immutable mapping of frozen `HostBaseline` values. It contains
  developer-owned axis, sign, and scale corrections for every supported app.
- `_DEFAULT_APP_PROFILES` is the shipped user layer. `default_app_profile()` always returns a deep
  copy, so reset/edit code cannot mutate the defaults.
- `APP_PROFILE_FIELDS` defines the complete atomic reset boundary. Operational state (`enabled`,
  `installed`, `start_automatically`, and `addin_version`) is intentionally outside it.

## Composition contract

The conceptual order is:

1. Global physical mapping turns raw sensor XYZ into body-relative logical XYZ.
2. The host baseline aligns the selected application's native camera conventions with the suite.
3. Saved per-app source, invert, and gain settings express the user's preference.

Host sign/scale and user invert/gain are composed multiplicatively (baseline direction XOR user
invert). The currently shipped host source maps are identity; their explicit fields reserve a safe
place for future unconventional host axis maps.

Lightweight integrations receive aligned deltas at the daemon output boundary. Blender, SketchUp,
Unreal, Unity, and Godot need to know the active Orbit/Fly/Walk action first, so the daemon sends
`adv.host_baseline` and those add-ons apply it immediately after per-action routing. Their local
camera constants are neutral to prevent double application.

## Shipped baselines

| App | Orbit factors | Pan factors | Zoom | Move | Applied by |
|---|---:|---:|---:|---:|---|
| Blender | `(0.5, 0.5, 0.5)` | `(0.5, -0.5)` | `0.5` | `0.5` | add-on |
| FreeCAD | `(1, 1, 1)` | `(-0.14, 0.14)` | `0.25` | `1` | daemon |
| SketchUp | `(-1, -1, 1)` | `(-0.14, -0.14)` | `0.25` | `0.5` | add-on |
| Unreal | `(2, 2, 2)` | `(0.14, -0.14)` | `0.25` | `0.5` | add-on |
| Unity | `(2, 2, 2)` | `(0.14, -0.14)` | `0.25` | `0.5` | add-on |
| Godot | `(2, 2, 2)` | `(0.14, -0.14)` | `0.25` | `0.5` | add-on |
| Rhino | `(1, 1, 1)` | `(-0.14, 0.14)` | `0.25` | `1` | daemon |
| Fusion 360 | `(-1, -1, 1)` | `(-0.14, -0.14)` | `0.25` | `1` | daemon |
| SolidWorks | `(-1, -1, 1)` | `(0.2, -0.2)` | `0.5` | `1` | daemon |
| Onshape | `(-1, -1, 1)` | `(0.14, -0.14)` | `0.25` | `1` | daemon |
| AutoCAD | `(1, 1, 1)` | `(-0.5, 0.5)` | `0.5` | `1` | daemon |

Blender and SketchUp also have immutable `camera.roll` and `fly.bank` direction corrections. The
wire value is baseline XOR saved user preference. Config v6 migrates explicitly saved v5 values so
their effective directions do not change; absent old keys use the new neutral saved default.

## Changing a shipped default

Change the immutable baseline only for a software-convention or suite-alignment correction. Change
the default app profile for an intuitive user-facing default. Then bump the daemon and every add-in
whose wire/math contract changed, add a migration if existing effective behavior would change, and
update the baseline/reset/composition tests. Do not reintroduce non-neutral sign/scale constants in
integration camera math.
