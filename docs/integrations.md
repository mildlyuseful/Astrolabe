<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Host integrations

An integration moves one app's camera from Astrolabe's navigation frames. It owns no bindings,
settings, or global state: the daemon decides which app has focus, maps the motion, and delivers it;
the integration applies it on the app's own thread. Host-specific facts — threading rules, camera
APIs, verified versions, gotchas — live in the per-app guides under [`apps/`](apps/).

## Adding an app

1. **Identity and capabilities.** Add an `AppSpec` to `APP_SPECS` in
   [`app_registry.py`](../trackball_daemon/app_registry.py) via `_spec(...)`: process names, a
   `SupportTier` (a release promise, deliberately required), pivots, twist actions, zoom behaviours,
   and whether it is mode-aware (`rich=True`: Orbit/Fly/Walk, optionally Object).
2. **Setup.** Add an `AppDef` to `APPS` in [`integrations.py`](../trackball_daemon/integrations.py):
   a detector, an installer that stages and verifies its payload, the copy shown in the UI (install
   model, verified versions, instructions, security notes), and a health check.
3. **The add-on.** Put it under `trackball_daemon/plugins/<app>/` with a `version.json`, `LICENSE`,
   and `NOTICE`. A socket add-on connects to `127.0.0.1:<bridge.json port>`, sends
   `{"type": "hello", "app": "<id>", "version": "...", "pid": ...}`, then reads newline-delimited
   frames: `o` (orbit x/y/z), `p` (pan x/y), `z` (zoom), `op`/`os`/`zm` (pivot, style, zoom target),
   and `adv` (everything else, including the active mode for rich hosts). Ignore keys you do not
   know; use safe defaults for missing ones.
4. **Alignment.** Add the app to [`host_profiles.json`](../trackball_daemon/host_profiles.json) and
   tune it as below.
5. **Settings.** Nothing to do unless the app offers something new: then add a `SettingSpec` with a
   capability predicate, its value in `system_defaults.json`, and the behaviour in the add-on. The
   settings UI picks it up from the schema.
6. **Tests and docs.** A guide in `apps/`, a row in the README's tier table, and tests for the pure
   math (see `tests/test_*_nav_math.py`).

A setting is shown for an app only when every value it offers does something distinct there.

## How a frame becomes a camera move

1. Global orientation maps sensor axes to logical X/Y/Z.
2. Per-app routing, inversion, and gain map logical motion to actions.
3. The host baseline aligns actions with the app's camera conventions — once.
4. The add-on moves the camera.

Lean integrations receive frames with the baseline already applied. Mode-aware ones (Blender,
SketchUp, Unreal, Unity) receive it as `adv.host_baseline` and apply it after choosing the active
mode's actions, because which axis means what depends on the mode. Their own camera code stays
neutral, so the correction has exactly one owner. Object mode's rotation baseline is the inverse of
the camera's: turning an object under a fixed camera reads the opposite way to orbiting around it.

## Tuning a host

1. Stop the daemon and edit `host_profiles.json` in the source tree — never a user config or an
   installed add-on.
2. Flip a direction with `orbit_sign`, `pan_sign`, or `zoom_sign`; set magnitude with
   `orbit_scale`, `pan_scale`, `zoom_scale`, or `move_scale`. For a mode-specific correction in a
   rich host, list `mode.action` (for example `camera.roll`) in `advanced_invert`.
3. Leave `apply_in_daemon` alone; it is `false` exactly for the mode-aware hosts above.
4. Restart and judge with neutral user settings: if a user inversion helped you find the
   correction, move it into the profile and **Reset app** before judging again. No add-on version
   bump is needed; the daemon sends the factors at runtime.
5. Run `pytest -q tests/test_default_profiles.py tests/test_output_bitexact.py`, then the suite.

Effective factors (`sign × scale`) as shipped — `host_profiles.json` is the authority:

| App | Orbit XYZ | Pan XY | Zoom | Move | Applied by |
|---|---:|---:|---:|---:|---|
| Blender | `(0.5, -0.5, 0.5)` | `(-0.4, -0.4)` | `0.4` | `0.5` | add-on |
| FreeCAD | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.5` | `1` | daemon |
| SketchUp | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.25` | `0.5` | add-on |
| Unreal | `(2, 2, -2)` | `(-0.3, -0.3)` | `0.25` | `0.5` | add-on |
| Unity | `(1, 1, -1)` | `(-0.14, -0.14)` | `0.25` | `0.25` | add-on |
| Rhino | `(-1, -1, 1)` | `(-0.14, -0.14)` | `0.25` | `1` | daemon |
| Fusion 360 | `(-1, -1, 1)` | `(-0.14, -0.14)` | `0.25` | `1` | daemon |
| SolidWorks | `(1, 1, 1)` | `(0.2, 0.2)` | `0.5` | `1` | daemon |
| Onshape | `(-1, -1, 1)` | `(-0.3, -0.3)` | `0.25` | `1` | daemon |
| AutoCAD | `(-1, -1, 1)` | `(-0.5, -0.5)` | `0.5` | `1` | daemon |

## Factory values

`system_defaults.json` holds the installed value of every setting: `global` covers every non-device
setting, and `apps` lists only deliberate per-host differences. Never write `0`, `"default"`, or
`null` to mean "inherit" — absence is inheritance. Change `host_profiles.json` for how an app's
camera works and `system_defaults.json` for how it should feel out of the box; run
`pytest -q tests/test_system_defaults.py` after either.
