# Host baselines and System defaults

This guide owns packaged setting defaults, immutable host alignment, sparse user overrides, and the
current tuning workflow. Shared state and mapping ownership is defined in
[`architecture.md`](architecture.md).

Avoid the unqualified word “default” when **System value**, **Global value**, **app override**,
**input profile**, or **host baseline** is meant.

## Ownership

- `trackball_daemon/host_profiles.json` is packaged developer data. `config.py` validates it at
  startup and exposes the runtime-immutable `HOST_BASELINE_PROFILES` mapping.
- `trackball_daemon/system_defaults.json` is the authoritative base-installed setting layer. `global`
  exhaustively covers every non-device `SettingSpec`, `device` covers non-keybindable device identity,
  and `apps` contains only concrete host-specific differences. Inheritance sentinels are forbidden.
- `trackball_daemon/default_profiles.json` is frozen compatibility data for exact v8-to-v9 migration.
  Its `0`, `"default"`, null/missing, and `general.buttons` conventions are historical input to the
  migration boundary. Do not use it for new defaults.
- `%APPDATA%/Mildly Useful/Astrolabe/config.json` stores sparse per-user state. The settings UI edits this
  layer through typed transactions; it does not mutate packaged data.
- `SYSTEM_DEFAULTS` is the validated immutable in-memory view. Accessors return detached values so a
  config transaction cannot mutate the developer-owned layer.
- Operational fields such as `enabled`, `installed`, and `addin_version` remain outside the setting
  hierarchy and survive setting resets or relinking.

## System → Global → app resolution

Missing overrides mean inheritance:

- A missing Global override uses the packaged global System value.
- A missing app override links that setting to Global.
- If a linked Global enum value is unsupported by the app, the app uses its concrete app System value
  while remaining linked.
- Unlinking an app setting materializes its current effective value.
- Relinking deletes the app override.
- Orbit-pivot fallback order uses this same link model: Global supplies the default chain and an app
  override may reorder, add, remove, or deliberately clear its own chain.
- Resetting one app setting pins its concrete app System value and breaks that setting's Global link.
- **Reset app** pins every applicable concrete app System value and breaks the app's Global links.
- **Link all to Global** deletes the app's setting overrides.
- **Break all links** materializes the app's current effective values.

These operations do not change integration enablement, setup completion, or observed add-on version.

## Mapping contract

The order is:

1. Global physical orientation maps raw sensor XYZ into body-relative logical XYZ once.
2. The active app's user source, inversion, gain, and action routing express user intent.
3. The immutable host baseline aligns those actions with the host's camera conventions exactly once.
4. A rich add-on applies the baseline after selecting Orbit/Fly/Walk meaning; a lean integration
   receives daemon-aligned deltas.

Host sign/scale and user inversion/gain compose multiplicatively (baseline direction XOR user
inversion). Global and per-action source routing remain user/device settings rather than host-profile
calibration data.

`navigation.object.translation_sensitivity` is a linked Global/app user multiplier with a System
value of `1.0`. Only Object-capable hosts expose it, and their add-ons apply it to secondary-layer
selected-object translation after routing; Object rotation is unaffected.

Orbit-pivot and cursor-zoom gesture lifetimes are independent. `orbit_pivot_hold_sec` controls
ray-derived orbit pivots; `zoom_cursor_hold_sec` controls only the target used by **To Cursor** zoom.
Pan or zoom invalidates the orbit pivot. Pan preserves the cursor-zoom target; orbit invalidates it.
A surface miss remains a miss for orbit and advances the configured fallback chain. For **To
Cursor** zoom, integrations synthesize a point on the cursor ray at a sensible target/model depth so
empty space does not silently become **To Center**.

Blender, SketchUp, Unreal, Unity, and Godot need the active Orbit/Fly/Walk action before host
alignment. The daemon therefore sends `adv.host_baseline` and those add-ons apply it immediately after
per-action routing. Their local camera constants stay neutral to prevent double application.

## Shipped baselines

`host_profiles.json` is authoritative. This table is a readable snapshot of the effective factors
(`sign * scale` per axis), not a second configuration source. Update it from the JSON whenever
calibration changes.

| App | Effective orbit XYZ | Effective pan XY | Zoom | Move | Applied by |
|---|---:|---:|---:|---:|---|
| Blender | `(0.5, -0.5, 0.5)` | `(-0.4, -0.4)` | `0.4` | `0.5` | add-on |
| FreeCAD | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.5` | `1` | daemon |
| SketchUp | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.25` | `0.5` | add-on |
| Unreal | `(2, 2, -2)` | `(-0.3, -0.3)` | `0.25` | `0.5` | add-on |
| Unity | `(1, 1, -1)` | `(-0.14, -0.14)` | `0.25` | `0.25` | add-on |
| Godot | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.25` | `0.5` | add-on |
| Rhino | `(-1, -1, 1)` | `(-0.14, -0.14)` | `0.25` | `1` | daemon |
| Fusion 360 | `(-1, -1, 1)` | `(-0.14, -0.14)` | `0.25` | `1` | daemon |
| SolidWorks | `(1, 1, 1)` | `(0.2, 0.2)` | `0.5` | `1` | daemon |
| Onshape | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.25` | `1` | daemon |
| AutoCAD | `(-1, -1, 1)` | `(-0.5, -0.5)` | `0.5` | `1` | daemon |

Blender and SketchUp also have immutable `camera.roll` and `fly.bank` direction corrections. The
wire value is baseline XOR saved user preference.

Rich Object integrations derive `host_baseline.object_rotation` as the negative of the host's
camera/orbit rotation factors. Turning an object under a fixed camera has the opposite perceived
direction from turning the camera around the object; user Object inversion settings remain neutral
and compose after this correction.

## v8 migration compatibility

`default_profiles.json` exists only to reconstruct the exact value visible under v8 inheritance:

- If the reconstructed value equals the corresponding v9 inherited value, migration stores no
  override and preserves linking.
- If it differs, migration pins the value as a sparse user override.
- `cube` and `cursor` are accepted as migration aliases for `3d` and `pointer`; only canonical values
  are written.
- Obsolete `general.buttons` data is discarded.
- The complete candidate v9 state is validated before atomic replacement.
- A malformed or structurally invalid source file falls back safely without being overwritten merely
  because load failed.

Detailed version-by-version execution history belongs under `archive/`, not in this current contract.

## Pointer acceleration

Pointer acceleration is a Global-only v9 setting family in `system_defaults.json`, not a legacy
materialized profile field. `off` preserves constant cursor sensitivity. `linear` and `smooth` map
planar angular speed from the configured onset across the configured ramp to a bounded maximum
multiplier. The curve multiplier and effective Pointer sensitivity compose multiplicatively. The
curve is evaluated from physical motion before sensitivity, using motion-sample timestamps, so its
thresholds remain in radians per second and do not change with sensitivity or BLE notification
cadence. Scroll and 3D navigation bypass the curve.

## Developer tuning workflow

### Host alignment

1. Stop the daemon and edit `trackball_daemon/host_profiles.json` in the source tree. Do not calibrate
   by changing `%APPDATA%/Mildly Useful/Astrolabe/config.json` or an installed host copy.
2. Flip an intrinsic direction with `orbit_sign`, `pan_sign`, or `zoom_sign`. Tune magnitude with the
   positive `orbit_scale`, `pan_scale`, `zoom_scale`, or `move_scale` value.
3. For a mode-specific inside/out correction in a rich integration, add or remove a `mode.action`
   string in `advanced_invert`, such as `camera.roll` or `fly.bank`.
4. Do not change `apply_in_daemon` while tuning feel. It is `false` only for Blender, SketchUp,
   Unreal, Unity, and Godot because those integrations apply the baseline after resolving their
   active action. All other integrations use `true`.
5. Restart the daemon. A source/editable install reads the JSON at startup; a packaged release must be
   rebuilt. Add-ins do not need a version bump for a data-only factor change because the daemon
   supplies the factors at runtime.
6. Keep user settings neutral while validating. If a user inversion or gain helped discover a
   correction, transfer it into `host_profiles.json`, restart, then use **Reset app** before judging
   the result.
7. Run `pytest -q tests/test_default_profiles.py tests/test_output_bitexact.py`, then the full suite.

### System defaults

1. Stop the daemon and edit `trackball_daemon/system_defaults.json`.
2. Change `global` for the installed base value. Every registered non-device setting remains present,
   including settings exposed only by some apps.
3. Add a stable setting ID beneath an app only when that host intentionally ships with a different
   concrete value. Omission inherits the System global value; never write `0`, `"default"`, or null
   to mean inheritance.
4. Restart the daemon. Existing Global and app overrides remain user-owned; reset operations resolve
   against the revised System layer.
5. Run `pytest -q tests/test_system_defaults.py tests/test_config_migration.py`, then the full suite.

Change `host_profiles.json` only for software-convention/suite-alignment corrections. Change
`system_defaults.json` for user-facing installed values. Keep `default_profiles.json` unchanged
unless the v8 migration contract itself is deliberately versioned. Do not reintroduce non-neutral
sign/scale constants in integration camera math.
