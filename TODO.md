# Open work

This is the sole ledger for unresolved engineering, verification, parity, and release work.
Current cross-component contracts live in [`docs/architecture.md`](docs/architecture.md); completed
plans, dated evidence, and implementation history belong under [`archive/`](archive/).

V1 is feature-frozen. Until the P1 production/release blockers and the V1 host claims selected from
P2 are closed, P3-P5 work enters V1 only when it fixes a release blocker or a regression in an
already-supported contract.

## P1 — Production and release blockers

### Production hardware

- Complete the production hardware contract around the fixed SuperMini nRF52840 controller (a
  nice!nano v2-compatible board, ZMK's `nice_nano_v2` target) before promoting the ZMK candidate to
  production firmware. This retires the earlier Seeed Studio XIAO nRF52840 target and the controller
  port it required: `firmware/PMW3610` already validates the production controller's electronics,
  but it remains a validation prototype, not a finished production claim. Freeze or deliberately
  change its remaining candidate choices against the final assembly:
  - confirm sensor model/count, mounting geometry, buses, chip selects, interrupt/power pins, and
    two-versus-three-sensor fusion expectations against the final assembly;
  - confirm Up/Down/Left/Right/Center pins, polarity, debounce timing, and simultaneous-input behavior;
  - battery/power design, USB/BLE expectations, host-profile behavior, and an always-reachable
    mode/recovery control.
- Repeat the physical switch, sensor, reconnect, held-input, sleep/wake, and mode-transition matrix
  on the final production assembly under the ZMK production firmware. The XIAO three-button bench
  validates the protocol boundary and the completed SuperMini PMW3610 loop validates the prototype
  electronics on the production controller; neither qualifies the final assembly or the new
  firmware stack.
- The pinned ZMK candidate now builds alongside CI's `firmware/XIAO3389` protocol-bench compile.
  Replace the bench as the release firmware gate, and delete the superseded `firmware/Astrolabe`
  Arduino placeholder, only after the ZMK candidate passes its live replacement matrix
  ([`docs/zmk_migration_plan.md`](docs/zmk_migration_plan.md)). Keep the PMW3610 sketch locally
  compilable for diagnosis, but do not restore it as a required release job.
- Verify the SuperMini prototype's BLE battery estimate against a multimeter across a representative
  discharge, confirm that USB insertion preserves the last battery-only value and USB removal
  refreshes it, and confirm Battery Level appears after a clean Windows re-pair and agrees across the
  daemon's tray tooltip, tray menu, and Settings footer. The standard service, daemon transport, and
  voltage mapping are covered automatically; this does not establish calibration for the installed
  cell, the board's ADC tolerance, or live Windows GATT behavior.

### Release qualification

- Exercise the exact release wheel, onedir GUI, and per-user installer on a clean Windows account
  without Python or a source checkout.
- Daily-drive the packaged `0.2.0a2` artifact and record what it finds. The process, the per
  observation fields, and what does not count as evidence are in
  [`docs/release.md`](docs/release.md); executed-artifact evidence belongs under
  `archive/release-evidence/`. Packaged-resource smoke is not daily-driver or host evidence.
- Establish whether the release executable can be made reproducible, or state that it cannot. Two
  builds of the identical clean tree produced different `Astrolabe.exe` bytes and therefore different
  archive hashes, while the SBOM stayed byte-identical — so the non-determinism is in Nuitka's output,
  not the dependency set. The deterministic-ZIP guarantee covers archive layout only, which means a
  recipient cannot currently rebuild and compare. Evidence:
  `archive/release-evidence/internal-alpha-0.2.0a1-build-2026-07-25.md`.
- Verify the on-disk identity migration on a machine that really ran an earlier build. Automated
  tests cover the copy, the verification, the rollback guarantees, the Run-value carry-over, and the
  add-on probe order, but not a real upgrade: confirm that settings, profiles, and an
  already-trusted Onshape certificate survive, that `%APPDATA%\TrackballDaemon` is left untouched,
  that Start at login still fires exactly once, and that each host add-on installed by the earlier
  build still connects.
- Retire the legacy configuration root once no supported upgrade path starts from it. Until then
  `paths.bridge_publication_paths` mirrors `bridge.json` there, the shipped add-on payloads probe
  it, and the preserved directory is the documented rollback copy. Removing it means dropping the
  mirror, the payload fallbacks, and the migration itself — and it cannot happen before the bundled
  AutoCAD DLL is rebuilt, since that plugin can read nowhere else.
- Move to bleak 3.x. The dependency is deliberately capped at `<2` because bleak 2.0 changed GATT
  error types and 3.0 changed the scanner/client keyword surface. The transport's usage is narrow
  (`find_device_by_filter`, `BleakClient`, `start_notify`/`stop_notify`) and no documented breaking
  change appears to touch the WinRT path, but scan, connect, notify, reconnect, and
  disconnect-while-held can only be qualified against real trackball hardware. Lift the cap in one
  delivery with that hardware evidence.
- Enable GitHub private vulnerability reporting at the moment the repository becomes public.
  `SECURITY.md` advertises `security/advisories/new` as the only reporting channel, and that feature
  cannot be enabled while the repository is private, so the advertised link does not resolve until
  it is turned on.
- Replace the placeholder contact promises once the Mildly Useful domain exists. `SECURITY.md` says
  a security mailbox will be added and `TRADEMARKS.md` says a permission contact will be listed;
  both must become real addresses or stop promising one.
- Obtain the persistent Mildly Useful Authenticode certificate used for the daemon, per-user
  installer, and staged AutoCAD DLL. The release builder and protected workflow now implement and
  verify the required order — compile, stage, sign binaries, archive, build/sign installer, write
  the manifest last — and fail closed for a public version when the certificate or any verified
  signature is absent. What remains is the real publisher identity and credential, not another
  unsigned substitute.
- Configure required reviewers on the `release` GitHub environment before any signed release. A
  GitHub environment with no reviewers grants no approval — the job just proceeds — so the workflow
  currently relies on only ever creating a draft, and publishing being a manual action, for that gate.
- Verify SmartScreen, Defender and third-party antivirus, Windows Firewall, UAC, host trust prompts,
  and Onshape certificate behavior against the exact signed artifact.
- Test every uninstall/reversal path listed in `docs/security.md`.
- Qualify the packaged Raw Input path with a non-US AltGr layout and across a Remote Desktop
  connect/disconnect boundary.

## P2 — Live host qualification

Automated math, migration, routing, metadata, and installer tests do not establish real viewport
behavior. Record the host version, projection mode, exact artifact, result, and any baseline change in
a dated file under `archive/release-evidence/`; keep only distilled invariants and warnings in the
matching active app guide.

The current five supported hosts remain the V1 qualification set. If any one cannot complete its
live matrix before the release candidate, demote it in the central app registry and user
documentation instead of weakening or silently waiving its gate.

### Shared matrix

For every applicable host:

- Enter Turntable, Lock Horizon, or Walk from a visibly rolled free view. With leveling enabled, remove
  roll once without moving the eye, target, distance, or active pivot; with it disabled, preserve tilt.
- Test Selection override on and off with a real selection and every useful primary pivot. Camera must
  remain turn-in-place.
- Confirm physical orientation, host baseline, user inversion, gain, and per-action source routing
  compose without a double sign or scale.
- Test real-hit and empty-space Under Cursor orbit, To Cursor zoom, Pivot hold, Zoom hold, pan
  invalidation, view-rotation invalidation, and independent hold durations.
- Test setup, reinstall/update, restart/reload, connection status, and a supported-versus-experimental
  host version.
- In Blender, Unity, and Unreal, test Object mode with one object, unrelated multi-selection, and a
  selected parent plus selected child. Verify independently routed three-axis rotation, both
  View right/up/depth and Ground horizontal-right/horizontal-forward/world-up translation,
  at least two Object movement sensitivity values, empty-selection no-op, an unchanged camera, and
  one Undo restoring the whole gesture.

### Host-specific deltas

- **AutoCAD:** strict Screen Center/Under Cursor misses, stationary-cursor reprojection, independent
  holds, projection behavior, paper-space/GraphicsSystem fallback diagnostics, and gesture-commit
  timing when only the shorter-lived channel was active.
- **Onshape:** strict fabricated-hit rejection near model extents, +Z Top-plane horizon leveling,
  stationary userscript samples, independent holds, orthographic/perspective behavior, and Chromium
  plus Firefox certificate UX.
- **Blender:** passive modal mouse tracking, tracker restart after file load, camera-view handling,
  and Orbit/Fly/Walk/Object sign and feel.
- **SketchUp:** live Win32 cursor-to-viewport mapping across display scaling and annual host versions.
- **Unreal:** focused-viewport cursor ray, live signs/scales, Object transaction lifetime,
  Play-In-Editor no-op behavior, and project-local versus engine-wide installation.
- **Unity:** Dynamic Clipping restoration, pivot-extent cap, Object undo grouping, domain reload,
  and project detection.
- **Fusion 360:** occurrence/assembly bodies with `findBRepUsingRay`; confirm whether root-component
  queries miss occurrence-only geometry.
- **SolidWorks:** COM throughput on representative large assemblies and multi-monitor DPI behavior.
- **FreeCAD:** perspective-camera behavior and nested/placed world-space bounds.
- **Rhino:** startup-registration fallback and live Under Cursor/Object Center behavior.

## P3 — Advertised host feature parity

Do not advertise these controls until host-specific behavior exists and passes live verification:

- Implement **Camera / turn-in-place orbit** for FreeCAD, Fusion 360, and SolidWorks, in that readiness
  order. Treat SolidWorks as a focused COM/API spike before committing to the feature.
- Implement a distinct user-selectable **Zoom versus Dolly** path for FreeCAD, Onshape, and
  SolidWorks, in that readiness order. Define orthographic Dolly semantics before exposing it.

Each parity change must include implementation, capability-registry exposure, focused tests, host
documentation, and live viewport evidence in one delivery.

## P4 — ZMK production-firmware epic

The pinned, out-of-tree implementation and its critical design corrections live in
[`docs/zmk_migration_plan.md`](docs/zmk_migration_plan.md). Automated builds and daemon tests do not
close these remaining gates:

- Flash the candidate on the final electrical assembly. Verify both PMW3610 identities, shared-bus
  signal integrity, calibrated axes/signs, full-speed motion, standalone cursor/scroll feel, all five
  switch positions, recovery gestures, forced-standalone boot, and ordinary BLE/USB HID output.
- Give BLE profile and output state some indication. The device has no display or LED binding, so
  three of the four standalone gestures — profile switch, output toggle, bond clear — are
  unobservable, and "nothing happened" is indistinguishable from "it worked". Only the bootloader
  gesture confirms itself, by mounting. This is also what makes a wrong profile hard to diagnose:
  ZMK accepts a pairing only onto an open slot, so a taken slot rejects the host with nothing but
  a generic connect failure at the other end. Decide between an LED, a HID feature report the
  daemon can read, or accepting it and documenting the recovery sequence.
- Verify the standalone/daemon layer split on hardware. Confirm the recovery and radio gestures are
  reachable only in standalone, that daemon control bits report with no arbitration in front of
  them, and that the layer follows the route with no manual step across cable pull, daemon
  termination, and keepalive timeout — including a route change that happens mid-gesture.
- Characterize automatic PMW3610 Run/Rest plus ZMK deep sleep on hardware: current draw, reliable
  MOTION/button wake, first-delta direction and magnitude, no reconnect dump, and surface-loss or
  illumination cases. SQUAL and shutter are diagnostics only; add no validity filter without this
  evidence.
- Run the frozen motion/input, hold/toggle, dependency, foreground, reconnect, and HUD matrices over
  the ZMK BLE service without changing stored binding/profile semantics. Include persisted CCC,
  re-pair, disconnect while held, malformed/stale packets, and owner replacement.
- Run the same matrix over wired vendor HID on Windows. Include enumeration identity, attach/ACK,
  lost attach ACK recovery, keepalive loss, daemon termination, suspend/resume, cable pull, stale
  queued reports, rapid BLE-to-USB preference and USB-to-BLE fallback, and held inputs across every
  transition.
- Confirm charging and USB-powered battery presentation on the physical power path. BLE may publish a
  measured percentage; an active USB daemon session must remain explicitly externally powered and
  must not fabricate a fresh percentage.
- Obtain a production USB VID/PID allocation and update firmware plus descriptor together; the
  upstream ZMK IDs are development-only.
- Measure flash/RAM, connection interval, throughput, motion latency, and battery behavior. Decide
  whether to enable ZMK Studio only after repeating lifecycle and resource measurements with Studio
  and the custom service together.
- Preserve the Arduino placeholder, PMW diagnostic sketch, and XIAO protocol-bench CI gate until the
  ZMK candidate passes every applicable live replacement gate above.

### Route/keymap integration depth

The route now drives a daemon keymap layer, one way: a route change may move the layer, a layer
change never moves the route. That boundary is deliberately the whole of it for now. These extend
it and are only worth building once the split above is confirmed on hardware:

- Allow ZMK behaviors to express derived gestures to the daemon. `state.controls` is a byte with
  five bits used, and `astrolabe_route_control` caps `bit_index` at 5, so a tap-dance today can
  only reach the daemon by masquerading as a physical direction. Raising the cap and adding
  descriptor entries for bits 5-7 gives eight sources with no wire change. Settle the semantics
  first: control bits are levels and the daemon's chords use `"activation": "hold"`, so an
  instantaneous gesture has no dwell to match against — either the daemon grows a tap activation
  or the firmware holds synthetic bits for a defined duration.
- Let the daemon push a layer. `queue_command` → `command_handler` is already an ACKed RPC over the
  HID OUT endpoint, so `COMMAND_SET_LAYER` is the same dispatch path; `struct command_report`
  carries only `{opcode, request_id}` and would need a payload byte. This is the direction with
  real upside — the daemon knows which application has focus and can select a CAD layer on switch.
- Report the active layer to the daemon. `zmk_keymap_highest_layer_active()` plus the existing
  `zmk_layer_state_changed` event covers the firmware side; the snapshot is length-prefixed at
  `output[4]`, so appending a layer byte is compatible for parsers that honor it. Costs a protocol
  version bump and a descriptor update.
- Give the daemon a contiguous owned layer range and tear the whole range down on release, so a
  user-defined CAD sub-layer cannot be stranded over the standalone base when the daemon drops.
  Required before user-defined layers within daemon mode are safe to encourage.

## P5 — Product, integration, and UI backlog

- Polish the existing app cards, generated settings pages, and keybinding editor for compact packaged
  Tk use, keyboard accessibility, focus order, error presentation, DPI scaling, and contrast.
- Add automatic Pointer/3D switching from foreground context with explicit automatic, persistent
  manual override, temporary hold, and return-to-automatic semantics.
- Improve Onshape userscript installation and updates without silently installing browser code or
  certificate trust.
- Consider viewport-under-cursor selection for Blender multi-viewport layouts; current behavior targets
  the active/largest `VIEW_3D`.
- Add profile export/import and visual priority editing after the editor/schema stabilizes; Advanced
  DSL remains the lossless path for unusual context/action structures.
- Add walk gravity/teleport only when a reliable host-side physics step exists.
- Replace SolidWorks out-of-process COM only if measured large-assembly throughput demonstrates a
  practical user-facing limit.
- Add inline SPDX headers to `plugin_src/autocad/TrackballNavAcad/` the next time the bundled DLL is
  rebuilt for a real change. Editing those sources marks the shipped DLL stale against its
  provenance manifest, so `licensing.json` carries their Apache-2.0 disposition instead; remove that
  exemption once the headers land.
- Re-point the bundled AutoCAD plugin at the current configuration root in that same rebuild.
  `BrokerConfig.cs` and `Plugin.cs` compile `%APPDATA%\TrackballDaemon` in for `bridge.json` and
  `acad_plugin.log`, so it is the only add-on that cannot probe both roots. Give it the same
  current-then-previous probe the source payloads use; that is what allows the mirror in
  `paths.bridge_publication_paths` to be dropped.
- Stop the Tk tests from skipping themselves on a Tk-capable machine. `tests/test_settings_ui_tk.py`
  and `tests/test_support_tiers.py` skip when `tk.Tk()` raises, which is right for headless CI but
  also swallows a real failure: creating and destroying several Tk roots in one pytest session
  intermittently fails with "Can't find a usable tk.tcl". Sharing one module-scoped window made it
  stop reproducing locally, but the cause was never isolated, so a Windows CI run can still turn
  UI coverage off without failing. Distinguish "no display" from "Tk broke" and let the second fail.
- Enforce Developer Certificate of Origin sign-off in CI. `CONTRIBUTING.md` requires a
  `Signed-off-by` trailer on new commits, nothing checks it, and the history that predates the
  policy carries no trailers, so the gate needs a start-point rule.

## Accepted limitations and non-goals

These are deliberate scope boundaries, not active parity tasks:

- SketchUp for Web has no local Ruby hook and is unsupported.
- Onshape Camera pivot is not useful in its supported orthographic view.
- AutoCAD 2024 and older require a separate .NET Framework plugin build; the bundled .NET 8 plugin
  targets the current binary family.
- A desktop 3Dconnexion NL-Proxy socket bridge is not viable. Native desktop hosts load the
  driver-provided `TDxNavLib.dll` C ABI rather than connecting to the browser WebSocket protocol. A
  clean-room DLL is a separate deferred product proposal, not an approved implementation path.
