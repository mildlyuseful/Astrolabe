# AutoCAD 3D navigation — maintainer's guide

AutoCAD navigation is implemented by a compiled .NET plugin loaded into `acad.exe`. The daemon-side
COM component only stages the DLL, scopes `TRUSTEDPATHS`, and requests `NETLOAD`; it never moves the
camera. Shared focus, mapping, target isolation, and lifecycle contracts live in
[`../architecture.md`](../architecture.md). User-facing setup belongs in
[`README.md`](../../README.md).

Primary code:

- [`plugin_src/autocad/TrackballNavAcad`](../../plugin_src/autocad/TrackballNavAcad) — in-process
  GraphicsSystem transport and camera math.
- [`trackball_daemon/autocad_driver.py`](../../trackball_daemon/autocad_driver.py) — daemon-side
  staging/trust/NETLOAD loader.
- [`trackball_daemon/plugins/autocad/version.json`](../../trackball_daemon/plugins/autocad/version.json)
  — bundled artifact manifest.
- [`plugin_src/autocad/NavMathTests`](../../plugin_src/autocad/NavMathTests) and
  [`tests/test_autocad_loader.py`](../../tests/test_autocad_loader.py) — camera and loader coverage.

Read current versions from `Plugin.PluginVersion`, the project file, and the bundled manifest. Do not
copy release numbers into this guide.

> **Warning: the NETLOAD plugin is the sole AutoCAD camera transport.** Do not restore the retired
> daemon-side COM navigation path as a fallback. The transports raced during plugin startup and could
> apply stale camera state after ownership changed. The retired implementation and historical ActiveX
> evidence live under
> [`archive/autocad_com_transport/`](../../archive/autocad_com_transport/).

## Architecture

```text
MotionSample + immutable RuntimeSnapshot
    → target-tagged NavigationEnvelope
    → NavigationRouter active-target/revision gate
    → NavBroker AutoCAD channel
    → newline JSON over 127.0.0.1 TCP
    → plugin socket thread (parse and accumulate only)
    → WinForms timer on AutoCAD's UI thread
    → GraphicsSystem camera update and gesture-end commit
```

The plugin rereads `%APPDATA%\TrackballDaemon\bridge.json` on every reconnect attempt. It accepts a
numeric `port` in `1..65535`; missing, malformed, or invalid discovery data falls back to `47900`.
The broker host is fixed to `127.0.0.1`.

That path is deliberately the *previous* configuration root. The daemon moved to
`%APPDATA%\Mildly Useful\Astrolabe`, and every add-on this project ships as source now probes both,
but this one is a compiled DLL whose provenance manifest pins the shipped binary — re-pointing it
means rebuilding it. So the daemon keeps publishing `bridge.json` to the old root whenever a staged
plugin or an earlier build's directory is present, and the plugin's own
`%APPDATA%\TrackballDaemon\acad_plugin.log` stays there too. `TODO.md` carries the rebuild.

The socket thread never touches AutoCAD APIs. It parses target-isolated broker frames and accumulates
motion. A WinForms timer created on AutoCAD's UI thread drains that state, applies the resolved
profile, and owns the complete GraphicsSystem gesture lifecycle.

`NavigationRouter` is the only daemon delivery boundary. `app_registry.py` selects AutoCAD only for
the normalized exact executable identity `acad.exe`; AutoCAD LT and similarly named processes do not
inherit its runtime context.

AutoCAD space selection uses both `TILEMODE` and `CVPORT`. On a layout, `CVPORT=2` identifies Model
space inside the active floating viewport and remains eligible for the full GraphicsSystem path;
other layout values identify Paper space. `TILEMODE=0` alone does not imply Paper space. Actual Paper
space pauses model-camera navigation; genuine GraphicsSystem loss uses the `SetCurrentView` fallback.

`CVPORT=2` identifies the active floating slot, not a durable viewport owner. The current viewport
object ID owns the gesture and distinguishes layout `Viewport` entities when the user changes the
active floating viewport. This object-ID rule is layout-only: on the Model tab,
`Editor.CurrentViewportObjectId` may be null while the numbered GraphicsSystem/VPORT path is valid,
so Model-tab navigation must remain keyed by `CVPORT`.

## Setup, update, and reload

Settings → **3D Apps → AutoCAD → Set up**:

1. verifies a supported AutoCAD installation and `pywin32`;
2. copies or stages the bundled DLL and manifest in the per-user runtime plugin directory;
3. adds only that directory to `TRUSTEDPATHS`; and
4. asks the already-running AutoCAD instance to `NETLOAD` the DLL once per session.

A locked installed DLL is staged and applied on a later AutoCAD start. Plugin source changes require a
new build and an AutoCAD restart; daemon loader changes require a daemon restart. Keep the project
version, `Plugin.PluginVersion`, bundled DLL, and manifest synchronized.

Build a shippable DLL with `tools/build_autocad_plugin.ps1`. A bare Release build compiles only to the
project output and cannot modify the bundled DLL or manifest. The controlled build refuses modified
or untracked plugin sources, injects an existing full source revision into the assembly informational
version, and records the plugin source-tree ID, DLL hash, target framework, .NET SDK, and exact
AutoCAD managed-reference versions and hashes in `version.json`. The artifact commit can follow the
recorded source revision; requiring a binary to name the commit that contains that same binary would
be circular. `tools/verify_autocad_artifact.py` and the source-checkout Python suite fail when current
plugin source no longer matches the manifest. Packaged release smoke has no Git checkout to compare;
it validates the DLL bytes and the manifest's intrinsic provenance instead.

The production build targets the managed API references for the supported AutoCAD generation. A
successful compile alone does not establish runtime compatibility with another binary era. The known
`WindowsBase` 4.0/8.0 resolution warning remains a release-integrity warning even when the build has
zero errors.

## Camera contract

The primary path obtains the live AcGs view, seeds a shadow `CamState`, calls
`BeginInteractivity`, applies each frame to the shadow, and drives the kernel view directly. Gesture
end commits the final camera to AutoCAD's persistent view state.

Supported behavior:

- **Orbit:** Free and Turntable styles, including Roll and one-shot horizon leveling on a real
  free-to-turntable transition.
- **Pivots:** Camera, Origin, Model Center, Selection, Screen Center, and Under Cursor.
- **Selection override:** a non-empty selection may replace external pivots; Camera remains a true
  eye pivot.
- **Zoom targets:** To Center, To Object, and To Cursor.
- **Pan-mode behavior:** Zoom changes projection field size; Dolly moves the camera. Dolly does not
  change magnification in a parallel projection.
- **Gesture state:** Orbit-pivot and To-Cursor zoom holds are independent. Pan/zoom invalidate orbit
  state as defined by the shared architecture; scheme changes clear both.
- **Pointer freshness:** each PointMonitor world sample is owned by the simultaneous Win32 screen
  pixel. Gesture capture rejects it if the physical cursor has since moved, preventing an old ray
  from producing a plausible stale hit. When the cursor is stationary, the plugin reprojects the
  cached cursor-plane sample after camera motion so a pan followed by orbit can recast without a
  mouse jog.

Screen Center and Under Cursor accept a real entity/AABB/curve hit only. Empty space makes the pivot
candidate unavailable and continues the configured fallback chain. To Cursor zoom may synthesize a
point on the cursor ray at the current target depth when no surface is hit.

Intrinsic signs and scales live in `trackball_daemon/host_profiles.json`. Plugin-local camera
multipliers are neutral. Do not compensate in both the host profile and `NavMath.cs`.

## Load-bearing warnings

### 2D Wireframe commit order is crash-sensitive

The 3D visual-style path can commit without a per-frame regeneration. AutoCAD's 2D Wireframe
presentation uses a separate view-dependent cache, so gesture end must perform this sequence:

1. identify the exact current viewport object captured with the gesture;
2. for a Model-tab viewport, write the shadow camera into its existing VPORT record and immediately
   call `UpdateTiledViewportsFromDatabase()`;
3. for a floating layout viewport, write the shadow camera into its `Viewport` entity and update that
   entity's display without calling either tiled-viewport update method;
4. queue one `_.REGEN` through the plugin's quiescent-state interlock; and
5. block new GraphicsSystem driving while that regeneration is in flight.

Do not reorder or split a tiled record write and database reapply, and never use a tiled-viewport
update method for a floating layout viewport. Earlier experiments that recreated the VPORT record,
left a write unapplied, or overlapped REGEN with an active GS view produced native access violations
that managed exception handling cannot catch.

### Keep one camera writer

The retired COM transport attached faster than the plugin and accepted early-session frames. Its
deferred orbit could then commit after the broker client connected, producing a stale second writer.
A plugin load failure must remain a visible degraded state, not silently switch to different camera
math.

### Distinguish loader failure from broker failure

- No loader attach/NETLOAD log: inspect AutoCAD detection, `pywin32`, document availability,
  `TRUSTEDPATHS`, and DLL staging.
- Plugin loaded but no broker hello: inspect `bridge.json`, the loopback port, and plugin socket logs.
- Broker hello but no motion: inspect foreground routing, target/revision changes, paper space, and
  AutoCAD's command/quiescent state.
- Motion in 3D styles but not 2D Wireframe: inspect the VPORT commit and regeneration interlock before
  changing camera math.

### The fallback is deliberately explicit and limited

When GraphicsSystem is unavailable in Model space, the `SetCurrentView` fallback supports view-center
orbit, pan, and centered Zoom. It cannot preserve configured or selection pivots, To Cursor/Object
anchoring, or Dolly. The Paper-space canvas has no active model camera, so navigation pauses there
instead of calling `SetCurrentView`. On each transition, the plugin writes one message to the AutoCAD
command line and log; `TBNAV` reports the current cause and limitations. Do not silently promote the
fallback to full capability or infer Paper space from `TILEMODE` alone.

### Do not probe against production modules or user drawings

Never copy files from `archive/autocad_com_transport/` over active `trackball_daemon` modules. If a
historical COM fact must be reproduced, use an isolated checkout and import the archived code under a
non-production module name. Probe only a disposable drawing and close it without saving.

## Testing and evidence

Automated checks cover:

- broker-port discovery, malformed-data fallback, and rereading after file changes;
- source wiring between `BrokerConfig.cs` and the plugin connection path;
- pure camera math and pivot behavior in `NavMathTests`;
- loader attach, staging, `TRUSTEDPATHS` deduplication, one-NETLOAD-per-session behavior, and graceful
  degradation without `pywin32`; and
- target-isolated app routing.

Run the focused suites before the full Python suite:

```powershell
python -m pytest tests/test_autocad_loader.py tests/test_integrations_autocad.py tests/test_autocad_artifact_provenance.py tests/test_app_routing.py -q
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj -c Release
& ".\tools\build_autocad_plugin.ps1"       # only when producing a new bundled DLL
python tools/verify_autocad_artifact.py
```

A current live claim requires the built DLL loaded into a disposable AutoCAD session. Record completed
run evidence in a dated file under `archive/release-evidence/`; keep only durable API invariants and
warnings here. Unresolved qualification and parity work belongs only in [`TODO.md`](../../TODO.md).

## Diagnostics

- `TBNAV` reports plugin state and active transport.
- `TBNAVTEST` exercises a bounded production navigation gesture.
- `TBNAVPTRTEST` inspects pointer/pivot behavior.
- The daemon log records loader attachment, staging, trust-path updates, and NETLOAD failures.
- Plugin logs should identify broker connection, selected port, GS/fallback state, gesture start/end,
  and guarded 2D regeneration events.

Avoid tight COM/command probe loops. AutoCAD can reject calls while busy, and aggressive command
injection can destabilize a disposable session even when production timing is safe.

## File and ownership map

- `plugin_src/autocad/TrackballNavAcad/Plugin.cs` — broker client, UI-thread pump, GS gesture engine,
  2D commit/interlock, commands, and plugin lifecycle.
- `plugin_src/autocad/TrackballNavAcad/BrokerConfig.cs` — per-reconnect discovery-file parsing and
  protocol fallback.
- `plugin_src/autocad/TrackballNavAcad/NavMath.cs` — pure camera and pivot math.
- `trackball_daemon/autocad_driver.py` — loader only: attach, stage/copy, trust, and NETLOAD.
- `trackball_daemon/navigation_router.py` / `navbroker.py` — target/revision isolation and AutoCAD
  broker delivery.
- `trackball_daemon/app_registry.py` — canonical identity, current process selector, transport, modes,
  and capabilities.
- `trackball_daemon/integrations.py` — detection, setup/install/update metadata, and runtime plugin
  destination.
- `trackball_daemon/config_store.py` / `settings_schema.py` / `system_defaults.json` — sparse typed
  configuration, capability presentation, and concrete installed defaults.
- `trackball_daemon/host_profiles.json` — immutable host alignment composed before neutral plugin
  math.
