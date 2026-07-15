# Open work

This file is the single list of unresolved engineering, verification, parity, and release work.
Completed implementation history belongs in `HANDOFF.md` or the relevant `docs/apps/<app>.md`, not
here.

## Highest priority

- Before replacing the honest `firmware/Astrolabe` placeholder, supply the final
  Up/Down/Left/Right/Center pins and debounce timing. Phase 11/release qualification must repeat the
  physical matrix on the production five-way switch, including the simultaneous-input
  contingency; the passed XIAO jumper gate validates the software boundary but does not substitute
  for final-hardware qualification.

## ZMK firmware foundation

- Replace the final-hardware firmware with a build based on pinned upstream ZMK and a separately
  maintained, out-of-tree Astrolabe ZMK module. Do not begin with a permanent ZMK fork: keep the
  board/shield definition, PMW sensor drivers, custom service, and Astrolabe behavior in the module
  so regular upstream ZMK/Zephyr updates remain possible. Carry a minimal, isolated upstream patch
  only if a required hook cannot be implemented through the supported module APIs, and document an
  exit condition for every such patch.
- Expose two explicit, mutually exclusive device modes through a ZMK behavior that can be assigned
  to an always-reachable physical control:
  - **Astrolabe mode:** publish motion, atomic five-way pressed-state snapshots, sequence/epoch,
    protocol version, and capability/battery information through a versioned custom GATT service
    consumed by the daemon. Controls owned by this mode must not simultaneously emit HID reports.
  - **Standalone trackball mode:** emit an ordinary HID pointer and HID buttons without requiring
    the daemon. Define the five-way switch as physical ZMK key positions and make its standalone
    assignments editable through ZMK Studio where current behavior metadata permits it. Predeclare
    the supported mouse-button/key behaviors and a bounded number of editable layers in devicetree;
    verify that Studio can assign the required mouse-button parameters before promising this UX. If
    it cannot, retain safe stock mappings plus advanced keymap/devicetree configuration rather than
    forking Studio solely to fill that gap.
- Treat the user-facing mode as more than an ordinary keymap layer even if a layer behavior is used
  to select it: it changes the output route and therefore needs explicit firmware state. A mode
  transition must release all active HID and custom-service controls, advance the transport epoch,
  publish a complete snapshot, and leave an always-available recovery binding. Specify whether the
  selected mode persists across reboot; a disconnect or daemon crash must never leave a held button
  or produce both a daemon action and an HID action.
- Preserve the existing daemon architecture and ownership boundary:
  - Firmware owns GPIO scanning/debounce, PMW sensor acquisition, safe device-local normalization,
    BLE bonding/host profiles, battery and power behavior, standalone HID output, and the
    device-local ZMK keymap.
  - The daemon remains the authority for Windows keyboard chords, foreground-app context,
    application capabilities, dependency closure, pointer/Orbit/Walk/Fly/Pan state, runtime setting
    holds/toggles, global/per-app settings, host integrations, and the HUD.
  - Implement the ZMK endpoint as another device transport/descriptor adapter that emits the
    existing normalized input-provider events and stable `source_id:control.id` tokens. Do not add a
    parallel ZMK-specific binding engine or move application-aware rules into firmware.
- Keep ZMK Studio and Astrolabe Settings deliberately separate. Studio edits only device-local
  standalone mappings, layers, and firmware behaviors; Astrolabe Settings edits daemon bindings and
  app-dependent behavior. Do not extend or fork the Studio RPC protocol for the first
  implementation. The Astrolabe data plane should remain its own small, bonded custom GATT service
  so its protocol can evolve independently and so the existing daemon BLE adapter is changed rather
  than replaced.
- Reuse the current BLE snapshot guarantees in the ZMK transport: protocol/capability negotiation,
  unsigned sequence handling, complete pressed-state snapshots, duplicate/out-of-order rejection,
  reconnect reconciliation, and synthetic release on loss. Keep high-rate motion separate from
  low-rate configuration and button state so ZMK Studio traffic cannot delay navigation input.
- Pin and record the upstream ZMK, Zephyr, module, and toolchain revisions; produce reproducible
  firmware artifacts in CI; and test module builds independently of the daemon. Publish the module
  and stock keymap with the product so ZMK adoption provides a real repair/customization path rather
  than serving only as an internal implementation detail.
- Acceptance gates before replacing the existing firmware:
  - With no daemon installed, pointer motion, remappable buttons, reboot persistence, BLE host
    switching, sleep/wake, and USB/BLE output work as a normal trackball.
  - With the daemon connected, the current motion, five-way, hold/toggle, dependency, foreground,
    reconnect, and HUD matrices pass without changing binding/profile semantics.
  - Repeated mode changes during held inputs, daemon termination, BLE loss, sleep/resume, firmware
    reboot, and Studio connect/disconnect produce no stuck state, duplicate click, or unintended
    fallback action.
  - Measure XIAO nRF52840 flash/RAM, connection interval, notification throughput, motion latency,
    and battery behavior with ZMK Studio and the custom GATT service enabled together before making
    ZMK the production firmware foundation.

## Host feature parity

These controls are intentionally not advertised until the host-specific behavior exists and has
been verified:

- Implement **Camera / turn-in-place orbit** for Fusion 360, FreeCAD, and SolidWorks. Their
  perspective camera APIs can support it, but their current pivot resolvers skip Camera.
- Implement a distinct user-selectable **Zoom versus Dolly** path for FreeCAD, Onshape, and
  SolidWorks.
- Godot Free orbit/roll is an accounted-for host limitation, not a missing checkbox: the editor
  camera persists yaw/pitch but not a rolled basis. Keep the integration Turntable-only unless the
  host API changes.

## Live integration verification

Automated math, migration, routing, metadata, and installer tests are not a substitute for a real
viewport. Record host version, projection mode, result, and any baseline changes in the matching
app guide.

For every applicable host:

- Enter Turntable, Lock Horizon, or Walk from a visibly rolled free view. With leveling enabled,
  roll should be removed once without moving the eye, target, distance, or active pivot. With the
  setting disabled, the existing tilt should be retained. This is the live acceptance pass for
  issue #2.
- Test **Selection overrides orbit center** both on and off with an actual selection and each useful
  primary pivot. Camera must remain turn-in-place. This is the live acceptance pass for issue #3.
- Confirm physical orientation, host baseline, user inversion, gain, and per-action source routing
  compose without a double sign or scale.
- Test real-hit and empty-space Under Cursor orbit, To Cursor zoom, Pivot hold, Zoom hold, pan
  invalidation, and view-rotation invalidation.
- Test setup, reinstall/update, restart/reload, connection status, and a supported-versus-unverified
  host version.

High-risk app-specific checks:

- **AutoCAD:** strict Screen Center and Under Cursor misses, stationary-cursor reprojection after
  pan/zoom, independent holds, parallel/perspective zoom, and both 3D visual styles and 2D Wireframe.
  Also assess whether using the maximum configured hold as the GS gesture commit timeout delays the
  database sync unnecessarily when only the shorter-lived channel was active.
- **Onshape:** strict fabricated-hit rejection with real geometry near model extents, +Z Top-plane
  horizon leveling, stationary userscript samples, independent holds, orthographic and perspective
  behavior, and Chrome/Edge plus Firefox certificate UX.
- **Blender:** passive modal mouse tracking, tracker restart after file load, camera-view handling,
  and Orbit/Fly/Walk sign and feel in a live GUI.
- **SketchUp:** Win32 cursor-to-viewport mapping at non-100% display scaling and across annual host
  versions.
- **Unreal:** focused-viewport cursor ray, live GUI signs/scales, Play-In-Editor no-op behavior, and
  project-local versus engine-wide installation.
- **Unity:** Dynamic Clipping override restoration, pivot-extent cap, domain reload, and project
  detection.
- **Godot:** compile/parser smoke test in an installed Godot build and project enable/reload.
- **Fusion 360:** occurrence/assembly bodies with `findBRepUsingRay`; the root-component query may
  miss geometry represented only through occurrences.
- **SolidWorks:** COM throughput on large assemblies and cursor mapping at non-100% display scaling.
- **FreeCAD:** perspective-camera path and world-space bounds for nested/placed objects.
- **Rhino:** startup registration fallback and live under-cursor/object-center behavior.
- **Daemon UI:** finish the later visual layer for expanded 3D-app cards and the declarative
  keybinding editor, including accessibility/usability polish in the normal packaged Tk runtime.

## Release readiness

- Complete the exact manual matrix in `docs/release_verification.md`. The first sdist, wheel, and
  per-user Nuitka onedir builds pass automated resource smoke; test the onedir GUI on a clean Windows
  account without a source checkout or Python installation.
- Authenticode-sign the daemon, installer/updater, and bundled AutoCAD DLL with one timestamped
  publisher identity. Publish SHA-256 checksums, source revision, dependency lock/SBOM, and exact
  Defender/third-party antivirus results.
- Verify SmartScreen, Windows Firewall, UAC, AutoCAD/SketchUp trust prompts, and Onshape certificate
  handling against the exact signed artifact. Source inspection cannot establish these outcomes.
- Test uninstall/reversal for every path in `docs/security.md`.
- Decide and document the supported host-version policy for the first alpha.
- Qualify the final packaged Raw Input path with a non-US AltGr layout and across a Remote Desktop
  connect/disconnect boundary; neither environment was available during feature-branch closure.

## Product and integration backlog

- Improve Onshape pointer-userscript installation and updates without silently installing browser
  code or certificate trust.
- Add automatic pointer/3D mode switching based on the foreground app, while retaining a predictable
  manual override.
- Consider viewport-under-cursor selection for Blender multi-viewport layouts; the current behavior
  targets the active/largest `VIEW_3D`.
- Add walk gravity/teleport only if a reliable host-side physics step is available.
- Replace the SolidWorks out-of-process COM transport with a per-user in-process add-in only if COM
  throughput becomes a practical limit.
- Add PMW3610/third-sensor firmware support and, if required, a three-sensor fusion solver.

## Deferred by design

- Keybinding profile export/import and a visual priority editor remain post-MVP. Advanced DSL is the
  current lossless path for non-default priority and unusual context/action structures.

- SketchUp for Web has no local Ruby hook and is not supported.
- Onshape Camera pivot is not useful in its supported orthographic view; rotating about the eye
  degenerates into image sliding.
- AutoCAD 2024 and older require a different .NET Framework plugin build; the bundled .NET 8 plugin
  targets the current AutoCAD binary family.
- A desktop 3Dconnexion NL-Proxy socket bridge is not viable. Native desktop hosts load the
  driver-provided `TDxNavLib.dll` C ABI rather than connecting to the browser WebSocket protocol.
  The durable investigation is in `docs/spikes/spacemouse_desktop_navlib.md`.
