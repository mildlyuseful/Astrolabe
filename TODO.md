# Open work

This is the sole ledger for unresolved engineering, verification, parity, and release work.
Current cross-component contracts live in [`docs/architecture.md`](docs/architecture.md); completed
plans, dated evidence, and implementation history belong under [`archive/`](archive/).

## P0 — Current correctness and state-safety defects

- Make AutoCAD paper-space/GraphicsSystem fallback capability loss explicit. Preserve cursor-pivot and
  Dolly semantics, dynamically suppress unavailable choices, or surface a clear degraded-state
  diagnostic instead of silently substituting view-center orbit or a no-op.

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

### Build and artifact integrity

- Compile `firmware/PMW3610/PMW3610.ino` and `firmware/XIAO3389/XIAO3389.ino` in CI with pinned
  board-core and library versions; report artifact size. Add a pinned `west` build when the ZMK
  module begins.
- Build the sdist and wheel in CI, install the wheel outside the checkout, and run binding validation
  and release smoke against the installed package.
- Produce a deterministic archive of the complete Nuitka onedir output and publish its checksum. The
  executable hash alone does not authenticate bundled DLLs, schemas, defaults, or host add-ins.
- Make host add-in install/update transactional: stage and validate payloads, swap with a recoverable
  backup, remove obsolete files, report per-destination results, and test interrupted updates.
- Add a whole-application startup/shutdown smoke with deterministic transport, UI, tray, filesystem,
  and host-driver fakes. Verify cleanup after every startup-stage failure and while controls are held.
- Surface broker, discovery-file, BLE, Raw Input, SolidWorks, AutoCAD, Onshape, and add-on protocol
  health instead of allowing silent startup or connection failures.

### Release qualification

- Exercise the exact release wheel and onedir GUI on a clean Windows account without Python or a
  source checkout.
- Establish a dependency lock and generate a CycloneDX or SPDX SBOM from the actual release
  environment. Reconcile `requirements.txt` with package extras.
- Add the project license, bundled-component notices, complete package metadata, vulnerability
  reporting instructions, and release notes before publishing an alpha.
- Authenticode-sign the daemon, eventual installer/updater, and bundled AutoCAD DLL with one
  timestamped publisher identity. Publish the source revision and whole-artifact checksum.
- Verify SmartScreen, Defender and third-party antivirus, Windows Firewall, UAC, host trust prompts,
  and Onshape certificate behavior against the exact signed artifact.
- Test every uninstall/reversal path listed in `docs/security.md`.
- Define the first-alpha support classification and maintenance policy: supported, experimental, or
  unsupported host/version combinations.
- Qualify the packaged Raw Input path with a non-US AltGr layout and across a Remote Desktop
  connect/disconnect boundary.

## P2 — Live host qualification

Automated math, migration, routing, metadata, and installer tests do not establish real viewport
behavior. Record the host version, projection mode, exact artifact, result, and any baseline change in
a dated file under `archive/release-evidence/`; keep only distilled invariants and warnings in the
matching active app guide.

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

### Host-specific deltas

- **AutoCAD:** strict Screen Center/Under Cursor misses, stationary-cursor reprojection, independent
  holds, projection behavior, paper-space/GraphicsSystem fallback diagnostics, and gesture-commit
  timing when only the shorter-lived channel was active.
- **Onshape:** strict fabricated-hit rejection near model extents, +Z Top-plane horizon leveling,
  stationary userscript samples, independent holds, orthographic/perspective behavior, and Chromium
  plus Firefox certificate UX.
- **Blender:** passive modal mouse tracking, tracker restart after file load, camera-view handling, and
  Orbit/Fly/Walk sign and feel.
- **SketchUp:** live Win32 cursor-to-viewport mapping across display scaling and annual host versions.
- **Unreal:** focused-viewport cursor ray, live signs/scales, Play-In-Editor no-op behavior, and
  project-local versus engine-wide installation.
- **Unity:** Dynamic Clipping restoration, pivot-extent cap, domain reload, and project detection.
- **Godot:** parser/compile smoke in an installed editor and project enable/reload.
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
