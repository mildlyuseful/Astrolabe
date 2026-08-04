# Open work

This is the sole ledger for unresolved engineering, verification, parity, and release work.
Current cross-component contracts live in [`docs/architecture.md`](docs/architecture.md); completed
plans, dated evidence, and implementation history belong under [`archive/`](archive/).

V1 is feature-frozen. Until the P1 production/release blockers and the V1 host claims selected from
P2 are closed, P3-P5 work enters V1 only when it fixes a release blocker or a regression in an
already-supported contract.

## P1 — Production and release blockers

### Production hardware

The controller, sensor count and model, ball, and five-way switch are frozen in
[`docs/hardware.md`](docs/hardware.md). Those are no longer open design questions; what remains is
verifying the frozen contract against a physical production assembly. A gate below that fails is a
product decision to reopen, not a value to quietly retune.

- Confirm the frozen pin map and ball geometry against the final assembly's schematic and build.
  `firmware/PMW3610` validated these electronics on the production controller, but on a prototype
  fixture with a 50.8 mm ball; the shipped ball is 52 mm and the enclosure, mechanical sensor
  mounting, and wiring harness are new. The shield now carries the final fixture's 140°/220° azimuth
  and −25° frame tilt, so what remains is confirming those measurements produce correct axes and
  signs in use — a pose error shows up as cross-axis bleed, not as an obviously wrong direction.
- Verify the five-way switch electrically and mechanically: all five positions reachable and
  distinct, 8 ms debounce adequate for the SKRHADE010's real bounce profile, no false center on
  diagonal actuation, and the forced-standalone escape (Center held through boot) reachable on the
  assembled enclosure.
- Close the battery and power design: cell selection, charge path, and the measured discharge curve
  behind the VDDH estimate. Then verify that estimate against a multimeter across a representative
  discharge, that USB insertion preserves the last battery-only value while USB removal refreshes
  it, and that Battery Level appears after a clean Windows re-pair and agrees across the daemon's
  tray tooltip, tray menu, and Settings footer. The standard service, daemon transport, and voltage
  mapping are covered automatically; none of that establishes calibration for the installed cell,
  the board's ADC tolerance, or live Windows GATT behavior.
- Run the physical switch, sensor, reconnect, held-input, sleep/wake, and mode-transition matrix on
  the final assembly under the ZMK production firmware. The XIAO three-button bench validates the
  protocol boundary and the SuperMini PMW3610 loop validated the prototype electronics; neither
  qualifies the final assembly or the ZMK stack.
- Replace CI's `firmware/XIAO3389` protocol-bench compile as the release firmware gate, and delete
  the superseded `firmware/Astrolabe` placeholder, only after the ZMK candidate passes the P4 live
  replacement matrix. Keep the PMW3610 sketch locally compilable for diagnosis; do not restore it
  as a required release job. Note that its `BALL_DIAMETER_MM` is still the prototype's 50.8, which
  is correct for the evidence it recorded and wrong for the shipped ball — treat its rotation output
  as uncalibrated against production.
- Produce the hardware design source bundle CERN-OHL-W-2.0 requires: schematics, layout, mechanical
  CAD, BOM, and assembly drawings in preferred editable form, plus the product source-location
  notice and physical marking. None of it exists in the repository yet, and the license obligation
  attaches at distribution.

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
[`docs/zmk_migration_plan.md`](docs/zmk_migration_plan.md); the hardware it targets is frozen in
[`docs/hardware.md`](docs/hardware.md). Automated builds and daemon tests do not close these
remaining gates:

- Flash the candidate on the final electrical assembly. Verify both PMW3610 identities, shared-bus
  signal integrity, calibrated axes/signs, full-speed motion, standalone cursor/scroll feel, all five
  switch positions, recovery gestures, forced-standalone boot, and ordinary BLE/USB HID output.
  Several of these already passed on the prototype fixture — standalone pointer delivery at 133 Hz
  with daemon feel parity, all four gestures, the route-driven layer split, and BLE/USB HID output
  across two hosts — so this gate is about the assembled product, not first proof the firmware
  works. Re-run it whole regardless: the ball diameter, mounting, and harness all changed.
- Verify each status-LED pattern on hardware. The indicator is implemented and the pin is confirmed
  (P0.15, the board's misleadingly named `blue_led` node); what is unverified is that each pattern
  fires when it should and is readable. Confirm: endpoint toggle shows long-then-one for USB and
  long-then-two for BLE; a profile switch shows the right count; a bond clear adds the trailing
  long; a toggle that cannot be applied (cable out, or no BLE host) shows the trailing long instead
  of nothing; and a bond clear on an already-unbonded profile shows nothing at all, which is ZMK
  raising no event rather than a fault. Also confirm the LED does not measurably shorten runtime once the
  battery discharge curve exists, and that it does not disturb either sensor — the old interference
  claim was a misdiagnosis of the wake transient, but it has never been tested with the LED
  deliberately lit.
- Finish verifying the standalone/daemon layer split. Clean transitions, gesture reachability in
  standalone, and unarbitrated daemon control bits are confirmed on the prototype. Still open: that
  the layer follows the route with no manual step across cable pull, daemon termination, and
  keepalive timeout, and that a route change landing mid-gesture leaves neither a stuck layer nor a
  stranded held control.
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
- Request a PID under the pid.codes VID `0x1209` and update the firmware descriptor plus
  `trackball_daemon/devices/descriptor_data/astrolabe_5way.json` together. Builds currently
  enumerate as `0x1D50:0x615E`, which is ZMK's own OpenMoko sub-allocation, so shipping them would
  present Astrolabe as a generic ZMK device and collide with every other ZMK board on a USB match.
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
