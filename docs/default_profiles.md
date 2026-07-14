# Host baselines and System Defaults

Software alignment and shipped settings are separate packaged developer files. Neither is
edited by the normal settings UI:

- `trackball_daemon/host_profiles.json` is packaged developer data. `config.py` validates it at
  startup and exposes it as the runtime-immutable `HOST_BASELINE_PROFILES` mapping.
- `trackball_daemon/system_defaults.json` is the authoritative base-installed profile. `global`
  exhaustively covers every non-device `SettingSpec`, `device` covers the non-keybindable device
  identity settings, and `apps` contains only concrete host-specific overrides. It is validated
  against both canonical registries at startup; inheritance sentinels are forbidden.
- `trackball_daemon/default_profiles.json` is frozen compatibility data for exact v8-to-v9
  migration. It retains v8's `0`, `"default"`, null/missing, and `general.buttons` conventions so
  migration can distinguish linked values from pinned user values. Do not use it for new defaults.
- `%APPDATA%/TrackballDaemon/config.json` is per-user state. The normal UI edits only this layer;
  user gains start at their defaults and all user inversion checkboxes start unchecked.
- `SYSTEM_DEFAULTS` is the validated, immutable in-memory view. Accessors return detached values,
  so config transactions cannot mutate the developer-owned layer.
- Operational fields (`enabled`, `installed`, and `addin_version`) remain outside the setting
  hierarchy and are preserved independently.

The removed **Shipped profiles** menu had no separate profile operation: it selected the same app as
the adjacent **Editing app** dropdown. It was redundant and has been removed. The remaining
**Reset to defaults** button resets the app selected by **Editing app**.

## Composition contract

The conceptual order is:

1. Global physical mapping turns raw sensor XYZ into body-relative logical XYZ.
2. The host baseline aligns the selected application's native camera conventions with the suite.
3. Saved per-app source, invert, and gain settings express the user's preference.

Host sign/scale and user invert/gain are composed multiplicatively (baseline direction XOR user
invert). Global and per-action source routing remain user/device settings rather than host-profile
calibration data.

Orbit-pivot and cursor-zoom gesture lifetimes are deliberately separate in config v8:
`orbit_pivot_hold_sec` controls ray-derived orbit pivots, while `zoom_cursor_hold_sec` controls only
the target used by **To Cursor** zoom. Pan or zoom invalidates the orbit pivot; pan preserves the
cursor-zoom target, while orbit invalidates it. A surface miss remains a miss for orbit and advances
the configured fallback chain. For **To Cursor** zoom, however, integrations synthesize a point on the
cursor ray at a sensible target/model depth so empty space does not silently become **To Center**.

Lightweight integrations receive aligned deltas at the daemon output boundary. Blender, SketchUp,
Unreal, Unity, and Godot need to know the active Orbit/Fly/Walk action first, so the daemon sends
`adv.host_baseline` and those add-ons apply it immediately after per-action routing. Their local
camera constants are neutral to prevent double application.

## Shipped baselines

`host_profiles.json` is authoritative. The table below is a readable snapshot of the current
effective factors (`sign * scale` for each axis), not a second configuration source. Update it from
the JSON whenever calibration changes.

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
wire value is baseline XOR saved user preference. Config v7 performs a one-time reset of v6 per-app
navigation fields because v6 could still contain values used during developer calibration. It
preserves global physical orientation plus app enable/install/version state. Older configs
that skip directly to v7 retain their established user preferences after historical migrations.

Config v8 introduces the independent `zoom_cursor_hold_sec` field and retires the temporary
`screen_center_pivot_hold_sec` name. Migration preserves that old value as
`orbit_pivot_hold_sec`; Zoom hold comes from the shipped default because the old field controlled
only the orbit pivot. The still older `view_pivot_hold_sec` name was already migrated to
`orbit_pivot_hold_sec` in config v4.

## Developer tuning workflow

### Host alignment

1. Stop the daemon. Open `trackball_daemon/host_profiles.json` in the source tree. Do not edit the
   user's `%APPDATA%/TrackballDaemon/config.json` for host calibration.
2. Find the software key. Flip an intrinsic direction by changing the corresponding
   `orbit_sign`, `pan_sign`, or `zoom_sign` entry between `1` and `-1`. Tune magnitude with the
   positive `orbit_scale`, `pan_scale`, `zoom_scale`, or `move_scale` value.
3. For a mode-specific inside/out correction in a rich integration, add/remove a `mode.action`
   string in `advanced_invert` (for example `camera.roll` or `fly.bank`).
4. Do not change `apply_in_daemon` while tuning feel. It is `false` only for Blender, SketchUp,
   Unreal, Unity, and Godot because those integrations must apply the baseline after resolving their
   active Orbit/Fly/Walk action. All other integrations use `true`.
5. Restart the daemon. A source/editable install reads the edited JSON immediately at startup; a
   packaged release must be rebuilt so the revised JSON is included. Add-ins do not need a version
   bump for a data-only factor change because the daemon supplies the factors at runtime.
6. Keep the normal UI neutral while validating. If you temporarily use a user checkbox or gain to
   discover a correction, transfer that correction into `host_profiles.json`, restart, then click
   **Reset to defaults** for that app before judging the result.
7. Run `pytest -q tests/test_default_profiles.py tests/test_output_bitexact.py`, then the full
   `pytest -q`. Invalid signs, non-positive scales, malformed action paths, or missing app profiles
   fail fast at daemon import/startup.

### System Defaults

1. Stop the daemon and open `trackball_daemon/system_defaults.json`.
2. Change `global` for the installed base value. Every registered non-device setting must remain
   present, including settings that only some applications expose.
3. Add a stable setting ID beneath one app only when that host intentionally ships with a different
   concrete value. Omission inherits the System global value; never write `0`, `"default"`, or null
   to mean inheritance.
4. Restart the daemon. Existing Global and app overrides remain user-owned; resets resolve against
   the revised System layer.
5. Run `pytest -q tests/test_system_defaults.py tests/test_config_migration.py`, then the full suite.

Change `host_profiles.json` only for software-convention/suite-alignment corrections. Change
`system_defaults.json` for intuitive user-facing defaults. Keep `default_profiles.json` unchanged
unless the v8 migration contract itself is deliberately versioned. Do not reintroduce non-neutral
sign/scale constants in integration camera math.
