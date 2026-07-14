# Open work

This file is the single list of unresolved engineering, verification, parity, and release work.
Completed implementation history belongs in `HANDOFF.md` or the relevant `docs/apps/<app>.md`, not
here.

## Highest priority

- Flash and hardware-verify the Phase 6 input-state firmware on the XIAO3389 test bench: initial
  snapshot, jumper press/hold/release, rapid transitions, disconnect while held, reconnect,
  unchanged rotation, and daemon-absent HID fallback. Before replacing the honest
  `firmware/Astrolabe` placeholder, supply the final Up/Down/Left/Right/Center pins and debounce
  timing. Phase 11/release qualification must repeat the physical matrix on the production
  five-way switch, including the simultaneous-input contingency; the jumper gate validates the
  software boundary but does not substitute for final-hardware qualification.

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
- **Daemon UI:** expanded 3D-app cards, scrolling, tooltips, confirmations, and reset behavior in the
  normal packaged Tk runtime.

## Release readiness

- Produce the first per-user Nuitka onedir build and test it on a clean Windows account without a
  source checkout or Python installation.
- Authenticode-sign the daemon, installer/updater, and bundled AutoCAD DLL with one timestamped
  publisher identity. Publish SHA-256 checksums, source revision, dependency lock/SBOM, and exact
  Defender/third-party antivirus results.
- Verify SmartScreen, Windows Firewall, UAC, AutoCAD/SketchUp trust prompts, and Onshape certificate
  handling against the exact signed artifact. Source inspection cannot establish these outcomes.
- Test uninstall/reversal for every path in `docs/security.md`.
- Decide and document the supported host-version policy for the first alpha.

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

- SketchUp for Web has no local Ruby hook and is not supported.
- Onshape Camera pivot is not useful in its supported orthographic view; rotating about the eye
  degenerates into image sliding.
- AutoCAD 2024 and older require a different .NET Framework plugin build; the bundled .NET 8 plugin
  targets the current AutoCAD binary family.
- A desktop 3Dconnexion NL-Proxy socket bridge is not viable. Native desktop hosts load the
  driver-provided `TDxNavLib.dll` C ABI rather than connecting to the browser WebSocket protocol.
  The durable investigation is in `docs/spikes/spacemouse_desktop_navlib.md`.
