<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Architecture

Astrolabe has four parts with one-way dependencies:

```text
controller firmware  --(protocol.md: rotation + input snapshots, BLE or USB)-->  daemon
daemon               --(loopback: broker frames, COM, or the Onshape bridge)-->  host integrations
daemon               --(loopback HTTP: JSON API + events)-->                      settings UI
```

- **Firmware** measures rotation, owns the route (mouse vs. daemon), and falls back to being a mouse
  on its own. It knows nothing about apps.
- **The daemon** owns everything stateful: foreground app, bindings, settings, live mode, mapping,
  and delivery to hosts.
- **Host integrations** turn navigation frames into camera moves inside one app. They own no
  bindings, settings, or global state.
- **The settings UI** is a client of the daemon's API. It owns nothing but what is on screen.

## Repository map

| Path | What it is |
|---|---|
| `firmware/` | ZMK module and config: motion sources, route, transports ([README](../firmware/README.md)) |
| `trackball_daemon/app.py` | Wires everything; owns process lifecycle |
| `trackball_daemon/devices/` | Descriptors, protocol decoding, BLE and USB transports |
| `trackball_daemon/input/` | Input providers, aggregation, binding compiler and controller |
| `trackball_daemon/runtime_state.py`, `commands.py` | Live state and the only path that changes it |
| `trackball_daemon/config_store.py`, `config_resolver.py` | Persistent settings and inheritance |
| `trackball_daemon/output.py` | Motion mapping, pointer output |
| `trackball_daemon/navigation_router.py`, `navbroker.py` | Delivery to host integrations |
| `trackball_daemon/app_registry.py`, `integrations.py` | Which apps exist, what they can do, how to set them up |
| `trackball_daemon/plugins/` | Host add-ons copied into apps by setup |
| `trackball_daemon/webui/` | Settings UI: server, API, static front-end |
| `plugin_src/autocad/` | Source of the bundled AutoCAD plugin DLL |

## Sources of truth

| Concern | Authority |
|---|---|
| Wire protocol | [`protocol.md`](protocol.md) and `firmware/module/include/astrolabe/protocol.h` |
| Reference hardware | [`hardware.md`](hardware.md); the shield overlay holds the values |
| Supported apps, their capabilities and transports | `app_registry.py` (`APP_SPECS`) |
| Setting IDs, validation, capability predicates | `settings_schema.py` (`SETTING_SPECS`) |
| Where a setting is shown | `settings_ui_model.py` (`CATEGORIES`) |
| Factory setting values | `system_defaults.json` |
| Host camera alignment | `host_profiles.json` |
| Built-in bindings | `system_keybinding_profiles.json` |
| Setup, detection, consent text | `integrations.py` |
| Product names, paths, registry identifiers | `product.py` |
| Per-path licensing, bundled notices | `LICENSING.md` / `licensing.json`, `THIRD_PARTY_NOTICES.md` / `third_party.json` |

No table is restated elsewhere. Code and packaged data are authoritative; documents explain why.

## Motion and control paths

```text
sensor deltas -> firmware fusion (radians) -> BLE / USB transport -> MotionSample
  -> App: capture foreground app + one RuntimeSnapshot revision
  -> OutputEngine: one immutable mapping for the whole sample
       pointer mode -> optional acceleration -> OS cursor / wheel
       3D mode      -> NavigationEnvelope(target app, deltas, state revision)
  -> NavigationRouter -> broker / COM / Onshape bridge -> host camera

device snapshots or Windows keyboard -> input provider -> InputAggregator (one pressed set)
  -> BindingController -> SerializedCommandQueue -> RuntimeStore -> RuntimeSnapshot
  -> OutputEngine, NavigationRouter, HUD, settings UI
```

A sample is mapped and delivered for the same app under the same state revision. Transport threads
only parse and publish; host APIs are called on the thread each host requires. Each device session
holds an identity lease, so a callback from a replaced session is a no-op.

## Settings

`ConfigStore` is the only writer of `%APPDATA%\Mildly Useful\Astrolabe\config.json`. A transaction
clones state, applies its operations, validates the whole candidate, replaces the file atomically,
and publishes one immutable snapshot and change event.

Resolution is **System → Global → app**, and absence means inheritance:

- Global: the user's Global override, else the packaged System value.
- App: the app override, else a Global override the app can represent, else the app's own System
  value, else the global System value.
- Linking an app setting deletes its override; unlinking pins the current effective value; resetting
  pins the System value. Enabled/installed state and add-on versions are outside this hierarchy.
- Physical orientation sources are a permutation: changing one swaps it with another.

A setting is exposed for an app only when its capability predicate matches and every value it
offers has a distinct runtime behaviour in that host. Stored data does not make a feature real.
Earlier v8 configuration files are migrated once at load (`config.py`, `default_profiles.json`).

## Live state

`RuntimeStore` is the authority for focus, input mode, navigation mode and layer, held bindings,
runtime setting overrides, and help text. `SerializedCommandQueue` is its only mutation path; each
command publishes one immutable `RuntimeSnapshot` with a new revision. A navigation mode the focused
app cannot do is inert there. The secondary layer (Shift by default) requires 3D but deliberately
does not pick a mode, so it never replaces Object, Fly, or Walk with Orbit.

## Input and bindings

Providers expose controls as `source:control` tokens; GATT UUIDs and virtual-key codes stop at the
provider. `InputAggregator` owns the complete pressed set and releases a provider's controls when it
disconnects or restarts — it may synthesize a release, never a press. The keyboard provider starts
only when a binding references a key, and keys pass through to the foreground app. Binding profiles
are declarative data over an allowlisted action set; see [`keybindings.md`](keybindings.md).

## Mapping

Applied once each, in this order:

1. Firmware reports rotation in radians in the housing frame.
2. Global physical orientation maps sensor axes to logical X/Y/Z.
3. The app's routing, inversion, and gains map logical motion to actions.
4. The immutable host baseline (`host_profiles.json`) aligns actions with the host's camera.
5. The integration moves its camera, view, or selected objects.

Lean integrations receive aligned deltas. Mode-aware ones (Blender, SketchUp, Unreal, Unity) get the
baseline in their frame and apply it after choosing Orbit/Fly/Walk/Object, so it has exactly one
owner. Never compensate for a host convention in firmware, orientation, or pointer math.

## Navigation semantics

Pivots: `camera` (turn in place), `screen_center`, `cursor` (needs a real surface hit), `selection`,
`cursor_3d` (Blender's 3D cursor, not the pointer), `object` (model bounds center), `origin`. The
primary pivot is tried first; on a miss, the fallback order is tried from the top. Unsupported and
duplicate entries are skipped; an empty order means no orbit. A non-empty selection may override the
pivot and To-cursor zoom, except that `camera` stays turn-in-place.

Orbit-pivot and cursor-zoom targets have separate hold timers. Pan or zoom invalidates the orbit
pivot; pan keeps the zoom target and rotation invalidates it. A To-cursor zoom that misses picks a
point on the cursor ray at a sensible depth; an orbit miss stays a miss. Horizon levelling happens
once on entering a fixed-horizon mode.

## Delivery to hosts

`NavigationRouter` is the only delivery boundary. A sample is accepted only for the active target
and current state revision; a target change discards pending motion for both targets; nothing is
relabelled or broadcast. Each target has its own rate and accumulator.

Socket add-ons speak newline-delimited JSON to the loopback broker after a hello naming their app,
version, and process. Frames carry `o`, `p`, `z`, `op`, `os`, `zm`, and an optional additive `adv`;
clients ignore unknown keys. SolidWorks is driven by COM from the daemon; AutoCAD by an in-process
plugin that the daemon stages and `NETLOAD`s; Onshape through a TLS bridge on `127.51.68.120:8181`
that becomes the target only while its browser tab owns viewport focus. Host-specific detail lives
in [`apps/`](apps/); adding a host is described in [`integrations.md`](integrations.md).

Add-on setup stages and verifies every file before swapping any installed copy, rolls back on
failure, and keeps every app's setup action available after success.

## Settings UI

[`webui/server.py`](../trackball_daemon/webui/server.py) serves a static page on `127.0.0.1` at an
ephemeral port and opens it in an Edge or Chrome app window. The page calls three endpoints:
`/api/state` (everything configurable), `/api/events` (server-sent invalidations, ball motion, and
pressed controls), and `/api/action` (named actions). API calls need the per-run token and a `Host`
header naming the listener; see [`security.md`](security.md).

The UI is generated: [`settings.js`](../trackball_daemon/webui/static/settings.js) renders any
`SettingSpec` from its kind and choices, `CATEGORIES` decides which page shows it, and the daemon
computes cross-setting advice (`app_hints`). To add a setting, add the spec and its System value. To
add an action, add a function to [`api.py`](../trackball_daemon/webui/api.py) with `@action`. To add
a page, write a module exporting `{id, title, icon, render}` and list it in `app.js`.

## Lifecycle and failure safety

A single-instance mutex is taken before anything starts. Tk owns the main thread for the control
panel; the tray, transports, broker, host drivers, and UI server run on their own threads. Startup
failure at any stage runs the same idempotent, release-first shutdown: bindings, pointer buttons,
and provider-held controls are released before transports go away. Every listener is loopback-only;
sensitive host services (SolidWorks COM, the Onshape bridge, AutoCAD loading) run only when an
app is both set up and enabled.

Long-running services publish one health value — `disabled`, `waiting`, `healthy`, `degraded`, or
`failed` — with a current detail, shown in the UI and summarized in the tray. A failed host
operation is logged and skipped; it never stalls the device stream or other hosts.
