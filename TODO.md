# Open work

This is the sole ledger for unresolved engineering, verification, parity, and release work.
Current cross-component contracts live in [`docs/architecture.md`](docs/architecture.md); completed
plans, dated evidence, and implementation history belong under [`archive/`](archive/).

V1 is feature-frozen. Until the P1 production/release blockers and the V1 host claims selected from
P2 are closed, P3-P5 work enters V1 only when it fixes a release blocker or a regression in an
already-supported contract.

## P1 — Production and release blockers

### Production hardware

- Complete the production hardware contract around the fixed Seeed Studio XIAO nRF52840 controller
  before replacing the honest `firmware/Astrolabe` placeholder. `firmware/PMW3610` is the completed
  SuperMini dual-PMW3610 + five-way validation prototype, not the product controller or a finished
  production claim. Freeze or deliberately change its remaining candidate choices against the final
  XIAO assembly:
  - confirm sensor model/count, mounting geometry, buses, chip selects, interrupt/power pins, and
    two-versus-three-sensor fusion expectations against the final assembly;
  - confirm Up/Down/Left/Right/Center pins, polarity, debounce timing, and simultaneous-input behavior;
  - battery/power design, USB/BLE expectations, host-profile behavior, and an always-reachable
    mode/recovery control.
- Port the frozen contract to the final XIAO nRF52840 assembly and repeat the physical switch, sensor,
  reconnect, held-input, sleep/wake, and mode-transition matrix there. The XIAO three-button bench
  validates the protocol boundary and the completed SuperMini PMW3610 loop validates the five-way
  prototype; neither qualifies the final product hardware.
- Replace CI's `firmware/XIAO3389` protocol-bench compile with the official
  `firmware/Astrolabe` XIAO nRF52840 target when that placeholder becomes the frozen production
  firmware. The obsolete SuperMini compile gate was removed because a prototype controller is not a
  public-V1 build target. Keep the PMW3610 sketch locally compilable for diagnosis, but do not
  restore it as a required release job.
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
- Enumerate the native libraries inside a built onedir tree and give each one an attribution.
  `tools/audit_notices.py` resolves every Python distribution in the release runtime, and
  `THIRD_PARTY_NOTICES.md` records CPython, Tcl/Tk, and the Nuitka runtime from the build
  configuration. The builder now explicitly stages the seven dynamic files the pinned CPython
  distribution needs even when Nuitka does not discover them, but nothing yet walks the completed
  tree to confirm every native library it actually carries and reconcile that inventory to notices.
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
  View right/up/depth and Ground horizontal-right/world-up/horizontal-depth translation,
  empty-selection no-op, an
  unchanged camera, and one Undo restoring the whole gesture.

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
- **Godot:** project enable/reload in an installed editor. The parse smoke is now a command --
  `python tools/godot_parse_check.py --godot <editor exe>` -- because the add-on shipped for several
  versions with a GDScript type-inference error that made the whole script fail to load, and nothing
  in this repository could see it. Run it whenever the payload changes; a Godot release can turn a
  previously inferable expression into a parse error.
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

Keep detailed design decisions in a dedicated current design or decision record; this section owns the
open epic and acceptance gates only.

### Feasibility and protocol decisions

- Use pinned upstream ZMK/Zephyr plus a separately maintained, out-of-tree Astrolabe module. Carry an
  isolated upstream patch only when module APIs cannot provide a required hook, and record an exit
  condition for every patch.
- Decide explicit, mutually exclusive Astrolabe and Standalone output-route state, physical mode and
  recovery controls, reboot persistence, BLE host profiles, USB/BLE behavior, and ZMK Studio scope.
- Keep the frozen v1 motion/input wire contract where possible. The current protocol has version/kind,
  complete pressed-state snapshots, and wrap-aware sequence handling, but no capability negotiation,
  transport epoch, or battery message. Specify backward compatibility before adding those features;
  add daemon adapter code only when a protocol extension demonstrably requires it.
- Preserve ownership: firmware handles device-local acquisition, normalization, power, bonding,
  standalone HID, and keymaps; the daemon remains authoritative for app context, bindings,
  dependencies, navigation state, settings, integrations, and HUD behavior.

### Vertical slice

- First deliver a reproducible module build with PMW motion, ordinary standalone HID pointer behavior,
  stock five-way mappings, an always-reachable physical mode switch, existing rotation/snapshot
  compatibility, and synthetic releases on mode change, disconnect, and transport loss.
- Reuse the existing descriptor/provider boundary and stable `source_id:control.id` tokens. Do not add
  a ZMK-specific binding engine or move application-aware rules into firmware.
- Keep ZMK Studio limited to device-local standalone mappings and firmware behaviors. Astrolabe
  Settings continues to own daemon bindings and app-dependent behavior.

### Acceptance gates

- Without the daemon, pointer motion, buttons, reboot persistence, host switching, sleep/wake, and
  USB/BLE output work as a normal trackball.
- With the daemon, the current motion, five-way, hold/toggle, dependency, foreground, reconnect, and
  HUD matrices pass without changing binding/profile semantics.
- Repeated mode changes during held inputs, daemon termination, BLE loss, sleep/resume, reboot, and
  Studio connect/disconnect produce no stuck state, duplicate click, or unintended fallback action.
- Measure flash/RAM, connection interval, throughput, motion latency, and battery behavior with Studio
  and the custom data service enabled together before replacing the current firmware.

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
- Establish why `push`-event workflow runs produced zero jobs between the P0 merge and 2026-07-25, or
  confirm it is fixed. Every push run in that window failed in 0s having created no job, including two
  pushes to `main`, so the gates genuinely stopped running there. `pull_request` runs on the *identical*
  workflow file executed all three jobs normally, which is why the cause is not established: moving the
  firmware job's `${{ runner.temp }}` out of a job-level `env` correlates with push runs working again,
  but that construct demonstrably resolved fine in a pre-fix pull_request run, so it may not have been
  the cause. The job-level restriction is real in GitHub's documented context availability, so the
  change stands either way — but the mechanism is unproven and the first post-fix push to `main` is the
  only evidence. Watch the next few pushes to `main` before treating this as closed.
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

- Godot remains Turntable-only unless the editor begins preserving a rolled camera basis.
- SketchUp for Web has no local Ruby hook and is unsupported.
- Onshape Camera pivot is not useful in its supported orthographic view.
- AutoCAD 2024 and older require a separate .NET Framework plugin build; the bundled .NET 8 plugin
  targets the current binary family.
- A desktop 3Dconnexion NL-Proxy socket bridge is not viable. Native desktop hosts load the
  driver-provided `TDxNavLib.dll` C ABI rather than connecting to the browser WebSocket protocol. A
  clean-room DLL is a separate deferred product proposal, not an approved implementation path.
