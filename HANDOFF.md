# Astrolabe maintainer handoff

This is the cross-component maintainer guide for the firmware, daemon, and 3D-host integrations.
Use it for current architecture, ownership boundaries, data flow, and rules that span applications.
Host-specific API details belong in [`docs/apps/`](docs/apps/), user instructions in
[`README.md`](README.md), and unresolved work in [`TODO.md`](TODO.md).

Do not copy version numbers or test counts into this file. They become stale quickly. The daemon
version is `trackball_daemon.__version__`; each bundled add-on has the manifest described under
Versioning below.

## 1. System overview

Astrolabe has two cooperating halves:

- [`firmware/XIAO3389/XIAO3389.ino`](firmware/XIAO3389/XIAO3389.ino) runs on a Seeed XIAO nRF52840,
  reads two PMW3389 sensors, solves their four surface-motion components into true three-axis ball
  rotation, and exposes both BLE HID mouse input and a custom rotation GATT stream.
- `trackball_daemon/` is a Windows system-tray application. It consumes the rotation stream and
  either injects pointer/wheel input or sends orbit/pan/zoom motion to the supported 3D application
  with focus.

Supported hosts are Fusion 360, SolidWorks, AutoCAD, Onshape, Blender, FreeCAD, SketchUp Desktop,
Unreal Engine, Unity, Godot, and Rhino.

The standalone [`cube_test.py`](cube_test.py) is the original math reference. The daemon retains a
debug cube and golden tests for its default pointer and quaternion behavior, but the production path
adds physical-axis mapping, app routing, and host/user alignment at explicit boundaries.

## 2. Repository map and sources of truth

```text
firmware/
  XIAO3389/XIAO3389.ino      active dual-PMW3389 test-bench firmware
  Astrolabe/Astrolabe.ino    production-hardware placeholder

trackball_daemon/
  app.py                     lifecycle, focus routing, service gates, scheme/rate delivery
  ble.py                     scan/connect/subscribe/reconnect loop
  output.py                  pointer/cube math and global/app mapping boundary
  windows_pointer.py         bounded SendInput motion and pointer-button sink
  instance_lock.py           process-lifetime single-controller ownership
  commands.py                typed live-state commands and serialized dispatch queue
  runtime_state.py           dependency-resolved live authority and immutable snapshots
  input/model.py             immutable normalized controls, events, health, and transitions
  input/aggregator.py        provider registry and atomic cross-provider pressed set
  input/bindings.py          system-profile composition, chord compiler, activation ownership
  input/macros.py            allowlisted declarative action parsing and setting validation
  input/windows_raw_input.py lazy pass-through Windows keyboard receiver and reconciliation
  config_store.py            transactional v9 loading, migration, snapshots, and events
  config_resolver.py         pure System -> Global -> app setting resolution
  config.py                  host/default helpers and historical migration machinery
  app_registry.py            immutable app identity, focus, transport, modes, capabilities
  settings_schema.py         stable setting/command IDs, validation, scope, UI metadata
  system_defaults.json       concrete developer-owned setting defaults
  system_keybinding_profiles.json developer-owned hardware and keyboard binding bases
  default_profiles.json      frozen v8 migration compatibility data
  host_profiles.json         immutable developer-owned host alignment
  navbroker.py               loopback JSON transport for socket add-ons
  solidworks_driver.py       direct out-of-process SolidWorks COM transport
  onshape_bridge.py          Onshape TLS/WAMP NL-Proxy-compatible bridge
  autocad_driver.py          AutoCAD discovery, staging, trust, and NETLOAD delivery only
  integrations.py            host detection, setup metadata, copy/install/update operations
  tray.py / ui.py            tray lifecycle and settings interface
  winfocus.py                foreground process query and motion-independent monitor
  plugins/                   bundled host add-ons and manifests

plugin_src/autocad/          C# source and console math tests for the bundled AutoCAD plugin
tests/                       daemon, config, driver, integration, and pure camera-math tests
.github/workflows/ci.yml     Windows PR/main checks for Python and AutoCAD NavMath
tools/                       host probes and diagnostic/self-test scripts
docs/apps/                   per-host maintainer guides
docs/spikes/                 durable investigation reports
archive/                     retired implementations kept only for earned technical history
ui_demo/                     design prototypes; not part of the daemon runtime
```

Configuration and registry ownership is deliberately split:

- `system_defaults.json` exhaustively defines the concrete base-installed System setting layer and
  its sparse host-specific differences.
- `default_profiles.json` is frozen compatibility data for reconstructing v8 inheritance during
  migration; new defaults do not belong there.
- `host_profiles.json` defines software-convention corrections that users must not need to discover
  or reapply.
- `system_keybinding_profiles.json` defines immutable `astrolabe_5way` and `keyboard_only` bases.
  User changes are sparse, stored under the base-profile ID, and never mutate this packaged file.
- `%APPDATA%\TrackballDaemon\config.json` stores device and user choices.
- `app_registry.py` is the only code-owned app identity/order table. Its immutable `AppSpec`
  records own display names, process selectors, navigation transports/modes, and capability flags.
  Packaged profile files are validated against that suite.
- `settings_schema.py` owns stable setting and non-setting command IDs, v8 path mappings, types,
  validation, scope, capability predicates, concrete System-default source metadata, allowed
  operations, and the current presentation order. A setting is exposed only when the app satisfies its
  predicate and every advertised option has a distinct runtime consumer.
- `integrations.py` retains setup/detection/install metadata and functions, but every `AppDef`
  references the canonical `AppSpec`; it does not repeat app IDs or display names.

`ConfigStore` persists sparse v9 user overrides, validates the complete candidate before atomic
replacement, and publishes deeply immutable snapshots. Feature consumers use typed transactions
and snapshot/domain accessors; only the store's isolated legacy migration boundary materializes v8
dictionaries. The test suite also enforces the ownership contract
`rich_actions == not apply_in_daemon`, so each host baseline is applied exactly once.

Persistent configuration is not live control state. `RuntimeStore` resolves a context-aware base
from one immutable config snapshot, then layers runtime latches and identity-owned hold requests
above it. `SerializedCommandQueue` is the only mutation path for tray, settings refresh, output
compatibility adapters, and future keyboard/BLE providers. Each command or batch publishes one
coherent immutable `RuntimeSnapshot` with input/navigation state, focused context, active binding
identities, setting overrides, last binding event, and a monotonic revision. Dependency leaves such
as Pan carry their prerequisites and precedence as one request; if a prerequisite loses a conflict,
the dependent leaf is suppressed rather than leaving an unreachable mixed state. `OutputEngine`
derives mode from this snapshot and does not own a second mutable mode value. Supported navigation
modes are also part of the resolved base: a held Fly/Walk request is inert while an Orbit-only app
has focus and resumes safely if focus returns to a compatible app.

See [`docs/default_profiles.md`](docs/default_profiles.md) for the composition and tuning workflow.

## 3. Firmware and BLE protocol

The current test-bench wiring is documented in the firmware header: shared SPI uses `D8/D9/D10`,
sensor chip selects use `D7/D6`, and the left/right/middle buttons use `D0/D1/D2` to ground.

`buildSolver()` creates a least-squares pseudo-inverse from sensor position/mounting geometry. Four
sensor deltas become `(wx, wy, wz)`, which are divided by the ball radius in sensor counts to produce
radians and accumulated between notifications.

Two BLE paths remain available:

- Standard HID mouse output works without the daemon.
- The custom service sends integrated rotation. When its CCCD enables notifications, firmware enters
  controller mode and stops also producing HID pointer motion. Unsubscribe/disconnect returns to HID
  mode. This prevents double input while retaining a standalone mouse fallback.

Protocol contract:

- Service UUID: `2cad0001-6e64-0146-b139-9cf2a4cd57fc`.
- Rotation characteristic: `2cad0002-6e64-0146-b139-9cf2a4cd57fc`.
- Packet: 12 bytes, three little-endian `float32` values `(rx, ry, rz)` in radians since the prior
  notification.
- Input-state characteristic: `2cad0003-6e64-0146-b139-9cf2a4cd57fc`. Protocol v1 kind 1 is a
  full-state snapshot: version, kind, little-endian `uint16` sequence, payload byte count, then the
  descriptor-mapped pressed bitset. The rotation characteristic remains unchanged.
- Bluefruit stores the 128-bit UUID byte arrays in reverse order.
- Firmware accumulates float deltas and clears them only after notification, so polling/notify
  cadence does not quantize away motion.

The raw-sensor IPS report is measured before `IPS_CAP`; the cap uses actual elapsed poll time. It is
currently a test knob for emulating a lower-performance sensor, not the reported hardware limit.
Set it high to disable the emulation during sensor characterization.

The daemon's generic BLE transport scans by configured name or connects by address, inventories
GATT, selects a data-descriptor-backed adapter, and subscribes to every characteristic that adapter
requests. Legacy firmware subscribes only to rotation. Input-capable firmware keeps the same motion
path and additionally diffs accepted full-state snapshots into normalized `InputEvent` batches.
Duplicate/stale/malformed snapshots cannot change pressed state; reconnect resets the sequence
baseline, and disconnect releases every control owned by that provider. Device identity changes
apply on the next reconnect.

## 4. Daemon threads and data flow

Daemon-side ownership:

- Main thread: hidden Tk root and all GUI work.
- Tray thread: `pystray`; callbacks marshal to Tk.
- BLE thread: asyncio/bleak subscription and packet delivery.
- Raw Input window thread: message-only global keyboard receipt while keyboard bindings require it.
- Input worker: normalization, reconciliation, and pressed-set publication outside native callbacks.
- Foreground monitor: publishes process/app context independently of BLE motion.
- Broker accept/sender threads: socket add-on connections and coalesced flushes.
- SolidWorks worker: COM-initialized driver thread.
- AutoCAD worker: COM-initialized loader/delivery thread; not the navigation transport.
- Onshape server, reader, and navigation workers: loopback TLS/WAMP bridge.
- Optional debug thread: pygame/OpenGL cube.

The data path is:

```text
BLE float packet
  -> App._handle_ble_packet
       -> capture one immutable runtime snapshot, focused host, and state revision
  -> OutputEngine.handle_packet
       -> global physical orientation
       -> pointer mode: windows_pointer SendInput cursor/wheel
       -> 3D mode: app user mapping and host-alignment boundary
  -> App._nav_sink (enabled gate; routes with the same captured host key)
       -> SolidWorksDriver for SolidWorks
       -> OnshapeBridge for Onshape
       -> NavBroker for all socket/plugin hosts, including AutoCAD
  -> driver/add-on accumulates and flushes on its UI-safe thread
  -> host camera/view
```

Every transport accumulates floating-point motion and flushes at its configured rate instead of
blocking the BLE thread. Preserve that carry: immediate sends flood slow hosts, while dropping
unflushed deltas loses physical rotation.

The process remains alive on Tk's mainloop. Closing Settings hides it; tray → **Quit** performs the
only normal shutdown.

`InputAggregator` owns the complete pressed set across providers and publishes atomic batches, so
disconnect or release-all cannot expose a half-released chord. The binding compiler expands generic
selectors to physical controls, indexes candidates by source token, and recomputes the complete
hold/toggle set after every batch or foreground-context change. Phase 3 resolves dependent states,
priority, context specificity, exactness, chord size, and activation recency; the compiler does not
duplicate that authority. Exact keyboard matching rejects unlisted modifiers, while exact device
bindings also reject unexpected simultaneous controls from the same source. `WindowsRawInputProvider`
starts only when the active compiled profile requests keyboard controls. Repeats preserve pressed
state without emitting another activation edge. Profile swap, receiver failure/restart,
lock/suspend, and shutdown release safely; resume/restart reconcile configured controls with
`GetAsyncKeyState` only when the input desktop is accessible. `ForegroundMonitor` drives the same
runtime context path as BLE packet routing, allowing stationary inputs to resolve the actual
foreground app. Windows can hide Raw Input releases while a higher-integrity window is foreground,
so the provider also polls only controls it already believes held and can synthesize releases only;
it does not scan other configured keys or create activation edges from that fail-safe.

The binding DSL is data only: stable command/setting IDs support set, explicit two-value toggle,
cycle, numeric add/multiply, identity-based restore, and explicit persistent setting transactions.
There is no eval, shell, raw JSON-pointer, arbitrary virtual-key, or scancode surface. Pointer
buttons are restricted to paired momentary Left/Right/Middle/X1/X2 actions and are identity-owned;
`SendInputPointerButtonSink` is the only OS delivery boundary. It retains
release-on-disconnect/reload/shutdown/owner-replacement behavior and reports a rejected button edge
instead of silently retaining ambiguous ownership. Motion injection preserves the historical
non-throwing behavior so a transient desktop boundary cannot tear down the BLE stream.

`SingleInstanceGuard` acquires `Local\TrackballDaemon.Controller.v1` before `App` is constructed.
Therefore a second installed or source-tree instance cannot open BLE or output transports; it exits
with an actionable diagnostic. Keep this acquisition ahead of every future transport startup.

## 5. Pointer, 3D, and mapping semantics

`OutputEngine` has pointer and 3D modes. In pointer mode, yaw-dominant motion becomes wheel input;
otherwise planar motion becomes cursor movement. Fractional pixel/notch remainders are carried.

In 3D mode, the shipped profile uses unmodified motion for orbit and Shift for mutually exclusive
pan/zoom. Shift is an ordinary declarative hold binding whose `pan` dependency cascades through
Orbit-secondary and 3D; `OutputEngine` never polls keyboard state. The app's binding profile can
change the active mode/layer, action sources, inversions, and gains.
`OutputEngine` publishes a complete immutable mapping snapshot and each packet retains one snapshot
through transformation and emission. Config reloads and focus changes are serialized so fields from
two app profiles cannot be mixed.

Mapping order is a contract:

1. Global physical orientation maps raw sensor XYZ to logical body XYZ once.
2. The active app's user source/invert/gain settings choose the action channels.
3. The immutable host baseline aligns that host's camera conventions with the suite.
4. Mode-aware rich add-ons apply their host baseline after selecting Orbit/Fly/Walk meaning; lean
   integrations receive host-aligned deltas at the daemon boundary.

The debug cube remains host-neutral. Never move a host-specific sign/scale into the cube or BLE
math, and never apply a baseline in both daemon and add-on. The `rich_actions` capability and
`host_profiles.json:apply_in_daemon` must continue to describe the same ownership decision.

## 6. Integration transports

| Host | Runtime transport | Setup/install model | Code version source |
|---|---|---|---|
| Fusion 360 | Broker → Python add-in | Per-user add-in; user enables Run on Startup | Python constant + `.manifest` |
| SolidWorks | Direct COM driver | No host add-in; explicit prerequisite enable | Daemon version |
| AutoCAD | Broker → in-process .NET plugin | Per-user staging/trust + automatic NETLOAD | C# constant + csproj-generated `version.json` |
| Onshape | Direct loopback TLS/WAMP bridge | Explicit cert setup and optional pointer userscript | Daemon version / web client hello |
| Blender | Broker → Python add-on | Per-user add-on; optional startup shim | `bl_info`, Python constant, `version.json` |
| FreeCAD | Broker → Python Mod add-on | Version-aware user `Mod` folder | Python constant + `version.json` |
| SketchUp Desktop | Broker → Ruby extension | Each detected annual user Plugins folder | Loader/main constants + `version.json` |
| Unreal Engine | Broker → Editor Python plugin | Engine-wide or project-local plugin | Python constant + `.uplugin` + `version.json` |
| Unity | Broker → Editor C# package | Project `Packages` folder | package metadata + `version.json` |
| Godot | Broker → EditorPlugin | Project `addons` plus enable entry | GDScript constant + `plugin.cfg` + `version.json` |
| Rhino 8 | Broker → Python scripts | Per-user scripts plus startup command | Python constant + `version.json` |

Socket add-ons send one hello line containing app key, loaded code version, host version, and PID.
Rich add-ons consume the daemon runtime's `adv.nav_mode`; host-local mode overrides must not compete
with that authority. Blender's former Alt+backtick operator was removed for this reason.
Broker frames contain:

```json
{
  "o": [0, 0, 0],
  "p": [0, 0],
  "z": 0,
  "op": "screen_center",
  "os": "free",
  "zm": "to_center",
  "adv": {}
}
```

`adv` is the frame target's complete additive contract, not a Blender-only extension. It can
include mode-specific settings, action routing, host baseline, selection override, candidate list,
independent hold times, zoom style, and horizon-entry behavior. Add-ons must ignore unknown keys and
use safe defaults for missing ones.

`NavigationRouter` is the single daemon-side delivery boundary for broker clients, SolidWorks COM,
and the Onshape bridge. Every immutable sample envelope carries its target app and runtime-state
revision. The broker owns independent accumulators, rates, schemes/profile revisions, and delivery
state per target, and sends a frame only to clients whose existing hello `app` matches that target.
Focus changes atomically discard pending old-target motion; they never relabel or flush it into the
new target. A disabled/unknown foreground app and connected-but-background Onshape select no target.
Stale runtime revisions are rejected, while a newer revision discards motion accumulated under the
prior state before accepting new deltas.

This isolation is entirely server-side. The newline JSON hello/frame shapes and all bundled add-on
version markers remain unchanged; `docs/rich_keybindings_phase4_protocol.md` records the frozen
inventory and Phase 4 decision.

Each socket add-on's hello reports the version of the copy actually loaded by that host document.
The daemon remembers the most recently observed version per app and uses it for Setup/Update status,
so a stale active project copy is not hidden by a current primary install path. The existing setup
actions still copy updates to every destination that host's installer detects.

## 7. Navigation behavior contract

Canonical pivot IDs are `camera`, `screen_center`, `cursor`, `selection`, `cursor_3d`, `object`, and
`origin`:

- Camera is turn-in-place, not a synonym for viewport center.
- Screen Center and Under Cursor require real surface hits for orbit.
- Selection is distinct from Model Center.
- Blender's 3D Cursor is distinct from the mouse cursor.

The selected primary is tried first. On failure, resolution restarts at item one of the global
fallback chain. Unsupported entries are skipped, duplicates/unknown values are removed, and an empty
chain produces no orbit after the primary fails. There are no hidden per-host model-center fallbacks.

Selection override is implemented across all eleven hosts. When enabled, a non-empty selection wins
over external orbit pivots and To Cursor zoom where supported. A Camera primary is exempt and remains
turn-in-place. This is the functional resolution of issue #3.

Ray-derived orbit pivots are held across a gesture. `orbit_pivot_hold_sec` controls recapture after
idle; pan and zoom invalidate the orbit pivot. `zoom_cursor_hold_sec` is separate and applies only to
To Cursor zoom; pan preserves that target, while rotating the view invalidates it. A To Cursor
surface miss synthesizes a point on the cursor ray at a host-appropriate target/model depth so the
cursor stays fixed. Orbit ray misses remain strict and continue the configured chain.

When entering Turntable, Lock Horizon, or Walk, `level_horizon_on_entry` optionally removes roll
once while preserving view direction, eye/target distance, and active pivot. The first received frame
establishes state and does not create a false transition. Godot omits the setting because its editor
camera cannot retain roll. This is the functional resolution of issue #2.

See [`docs/feature_parity.md`](docs/feature_parity.md) for the enforced current capability contract
and `TODO.md` for intentionally deferred parity.

## 8. Configuration and migrations

The current config schema is version 9. Its setting hierarchy is sparse System→Global→app:
missing overrides inherit, and persisted v9 state never uses `0`, `"default"`, missing-field
exceptions, or legacy mode names as inheritance sentinels. Important milestones:

- v2 neutralized old per-app scaling that moved into host integrations.
- v3 renamed pointer/cursor scheme values without losing Blender's separate 3D Cursor meaning.
- v4 separated Camera from Screen Center and renamed the old view-pivot hold.
- v5 added global physical orientation and per-action axis sources.
- v6/v7 transferred developer host alignment out of saved user preferences and established atomic
  user-profile reset semantics.
- v8 separated orbit-pivot and cursor-zoom holds.
- v9 introduced stable-ID System/Global/app layers, typed transactions, immutable snapshots,
  structured observable change events, `cube`/`cursor` to `3d`/`pointer` aliases, selected-app UI
  state, and removal of obsolete `general.buttons`.

Old values must continue to land on their current meaning. Migration tests are durable user-data
tests, not checkpoint tests. Corrupt or structurally invalid configuration must fall back safely
without overwriting the bad source file unless a deliberate recovery policy is introduced.

Global reset removes the user override so System is visible again. Linking an app setting removes
its app override; unlink-all pins the current effective values. Resetting an app setting to System
pins the concrete System value and therefore breaks its Global link. All these operations preserve
operational state such as enabled/installed/add-on version.

The deprecated per-app `start_automatically` field had no consumer and is removed on load. The real
daemon-level **Start at login** toggle lives in `tray.py` and writes only the current user's Windows
Run key.

## 9. Setup, security, and lifecycle

`integrations.AppDef` is the source for every 3D Apps card: install model, supported versions,
automatic and manual setup, security notes, confirmation text, and health check. Keep metadata
honest; no-file integrations must not present fake reinstall/recheck actions.

Sensitive services are gated by both completed setup and Enabled state:

- SolidWorks COM attachment does not start on a clean install.
- AutoCAD enumeration/TRUSTEDPATHS/NETLOAD does not start on a clean install.
- Onshape certificate generation/listener does not start on a clean install.

All network listeners are loopback-only. The Onshape bridge accepts only allowed HTTPS Onshape
origins plus its own status page. Setup never automatically trusts the generated certificate or
disables AutoCAD `SECURELOAD`.

See [`docs/security.md`](docs/security.md) for the complete action/reversal inventory and release
hardening requirements.

Reload rules are easy to confuse:

- Daemon/config/driver changes require a daemon restart.
- Socket add-on changes require that host to reload or restart its embedded interpreter.
- Version-gated auto-update only recopies when the bundled manifest version is newer. A runtime
  code change therefore requires every version marker for that host to move together.
- SolidWorks and Onshape have no bundled host code; their runtime changes ship with the daemon.
- AutoCAD source changes require rebuilding the bundled DLL as well as bumping its synchronized
  version metadata.

## 10. Cross-cutting gotchas

### BLE and Windows HID coexistence

On Windows, pairing the device as HID can stop the advertisement path bleak needs. The supported
daemon workflow lets the daemon own the BLE connection and injects pointer input with `SendInput`;
native HID remains the daemon-closed fallback.

### Host focus and process identity

Desktop hosts route by foreground process name through `app_registry.resolve_foreground_context`.
Onshape cannot route by title because the browser tab title is the document name. The resolver
models its bridge as disconnected, connected-background, or connected-foreground; only foreground
browser + connected bridge selects Onshape, then Onshape's own focus signal remains the fine gate.
Keep routing tests at packet boundaries: discovering focus only after a packet has already been
transformed applies the previous app's mapping to that packet.

### COM attachment

Enumerate the Running Object Table instead of trusting `GetActiveObject`. SolidWorks can return an
empty/older registered instance; AutoCAD has returned “Operation unavailable” despite a live app.
Choose the suitable live instance, attach only, and never launch a host as a side effect.

### Host APIs and threads

Embedded host APIs are generally main-thread-only. Reader threads may parse and accumulate, but
Fusion CustomEvents, Blender timers, FreeCAD QTimer/Coin, SketchUp `UI.start_timer`, Unreal editor
callbacks, Unity `EditorApplication.update`, Godot editor callbacks, Rhino UI dispatch, and AutoCAD
WinForms/UI work must own the actual camera change.

### Surface-pick APIs are not interchangeable

Each host has an earned maintainer note: Fusion uses `ObjectCollection`; SolidWorks `SelectByRay`
needs exact COM argument types and selection preservation; Onshape fabricates no-hit depth points;
Blender raycasts are depsgraph-first; FreeCAD requires `pivy.coin`; SketchUp changed logical pixel
behavior; AutoCAD uses entity/extent ray approximations. Read the app guide before “simplifying” a
resolver.

### AutoCAD's active transport

The bundled in-process GraphicsSystem plugin is the sole navigation transport. `autocad_driver.py`
only stages, trusts, and NETLOADs it. The retired COM camera transport is in
`archive/autocad_com_transport/`; its desynchronized `ActiveViewport`, destructive `.Center`, and
per-frame regeneration history is useful research but not current runtime architecture.

### Errors and caches

One host API failure should be logged and skip only that operation; it must not blank a viewport or
disconnect unrelated services. View/document/cursor/pivot caches need explicit invalidation on host
switch, document switch, pan/orbit/zoom semantics, and scheme changes. Keep independent orbit and
cursor-zoom lifecycle state independent.

## 11. Build and verification

Source run:

```powershell
python -m pip install -r requirements.txt
python -m trackball_daemon
python -m trackball_daemon --debug
```

Core automated checks:

```powershell
python -m pytest -q
python -m compileall -q trackball_daemon tests
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --no-restore
git diff --check
```

The AutoCAD project is a console test runner; `dotnet test` is not the meaningful invocation.
Blender and host-specific probes live in `tools/`. Run the focused pure tests first, then the full
suite, then a GUI smoke pass in every changed host. Do not describe source review, mocked APIs, or a
headless math probe as live viewport verification.

Firmware requires the Seeed non-mbed nRF52 board package, Adafruit Bluefruit/TinyUSB dependencies,
and Serial at 115200 for IPS/connection diagnostics.

The intended release shape is a per-user Nuitka onedir build or the installed GUI script. No signed
alpha artifact has been cut; release validation remains in `TODO.md`.

## 12. Versioning and change discipline

For any daemon change:

1. Update behavior, tests, user docs, and the relevant maintainer guide together.
2. Bump `trackball_daemon.__version__` only when shipping a new daemon build.
3. If a bundled host add-on changed, bump every marker named in the transport table and verify the
   installed/update path. Rebuild the AutoCAD DLL for C# changes.
4. Run automated checks and record live verification honestly.
5. Put unresolved concerns in `TODO.md`, not a temporary process ledger.

Do not edit a user's local config or installed host copy as the source of a shipped fix. Make the
change in the repository source/default data, then exercise the normal setup/update path.

## 13. Documentation map

- [`README.md`](README.md): user capabilities, installation, operation, and troubleshooting.
- [`TODO.md`](TODO.md): the only active backlog and live-verification ledger.
- [`docs/default_profiles.md`](docs/default_profiles.md): host/default ownership and tuning.
- [`docs/feature_parity.md`](docs/feature_parity.md): current capability contract.
- [`docs/security.md`](docs/security.md): permissions, warnings, reversal, and release hardening.
- [`docs/apps/autocad.md`](docs/apps/autocad.md)
- [`docs/apps/blender.md`](docs/apps/blender.md)
- [`docs/apps/freecad.md`](docs/apps/freecad.md)
- [`docs/apps/fusion360.md`](docs/apps/fusion360.md)
- [`docs/apps/godot.md`](docs/apps/godot.md)
- [`docs/apps/onshape.md`](docs/apps/onshape.md)
- [`docs/apps/rhino.md`](docs/apps/rhino.md)
- [`docs/apps/sketchup.md`](docs/apps/sketchup.md)
- [`docs/apps/solidworks.md`](docs/apps/solidworks.md)
- [`docs/apps/unity.md`](docs/apps/unity.md)
- [`docs/apps/unreal.md`](docs/apps/unreal.md)

When a fact cannot be inferred safely from code—host API convention, coordinate-space trap,
threading requirement, live result, or rejected approach—record it in the relevant app guide. If it
spans components, summarize the contract here as well.
