# Open work

The only ledger of unresolved work. Contracts live in [`docs/architecture.md`](docs/architecture.md);
finished work lives in git history.

V1 is feature-frozen: until the P1 blockers and the P2 host claims are closed, later work enters V1
only to fix a blocker or a regression in something already supported.

## P0 — Re-verify on the trackball after the November restructure

The firmware module was re-cut (motion-source API, core-owned delivery pacing, registered
transports, one `&astro` behavior) and the Tk settings window was replaced by the web UI. The
firmware builds, flashes, and passes the USB protocol checks on a bare SuperMini; nothing below has
been run with a ball attached.

- Ball motion and signs on the **Live** page in every axis; standalone cursor and scroll feel.
- The press-jitter check in [`docs/hardware.md`](docs/hardware.md): clicking each direction with the
  ball still must not move the cursor, standalone or in 3D.
- Standalone clicks: quick taps register, holds release on time, a click held while the daemon
  attaches is released.
- The four gestures and their LED patterns, and forced-standalone boot (hold Center).
- BLE: claim by keepalive, expiry when the daemon is killed, re-pair, reconnect while held.
- USB: attach, keepalive, cable pull, BLE↔USB handover while held; `build.py --flash` from running
  firmware.
- Daily-drive the web UI: every page, theme switching, a host setup end to end, and the window
  reopening from the tray after the daemon restarts.

## P1 — Production and release blockers

### Hardware

The controller, sensors, ball, and switch are fixed in [`docs/hardware.md`](docs/hardware.md); what
remains is checking them against a production assembly.

- Confirm the pin map against the final schematic and wiring.
- Calibrate sensor pose on the fixture with `tools/calibrate_sensor_mounts.py` (build with
  `--logging`): the shield's 140°/220° azimuth, 120° polar, and −25° tilt have not been isolated per
  axis. Pose error shows as cross-axis bleed under a single-axis roll, not as a wrong direction.
  Check both sensor identities and bus integrity at full speed in the same pass.
- Characterize the five-way switch: 8 ms debounce against its real bounce, no false center on
  diagonals.
- Close battery and power: cell, charge path, the discharge curve behind the VDDH estimate, what the
  status LED costs, and Battery Level after a clean Windows re-pair.
- Characterize sensor Run/Rest plus ZMK deep sleep: current draw, wake on MOTION and on a press, the
  first delta after wake, surface loss. Add no validity filter without this evidence.
- Measure flash/RAM, connection interval, throughput, latency, and battery life. Decide on ZMK Studio
  only after repeating those with it enabled.

### Release qualification

- Run the device, keyboard, host, and UI matrices in [`docs/releasing.md`](docs/releasing.md) against
  the packaged artifact, including a clean Windows account without Python.
- Finish Onshape's browser-permission matrix: denial, revocation, and pointer recovery after a
  daemon restart in Firefox and Chromium.
- Decide whether the release executable can be made reproducible: two builds of the same clean tree
  gave different `Astrolabe.exe` bytes while the SBOM matched, so the variation is in Nuitka's output.
- Verify the one-time move off `%APPDATA%\TrackballDaemon` on a machine that really ran an early
  build, then retire it: the `bridge.json` mirror, the add-on fallback probes, `config.py`'s v8
  migration, and `default_profiles.json` all go together — after the AutoCAD DLL is rebuilt to read
  the current root.
- Obtain the Authenticode certificate, configure required reviewers on the `release` environment,
  and verify SmartScreen, Defender, firewall, UAC, and host trust prompts against the signed build.
- Replace the placeholder contacts in `SECURITY.md` and `TRADEMARKS.md` with real addresses.

### Public-release gate

In order; none can start while the repository is private.

- Publish the hardware source bundle CERN-OHL-W-2.0 requires (schematic, layout, mechanical CAD,
  BOM, assembly drawings) with the source-location notice and marking.
- Request a pid.codes PID under VID `0x1209`, then change the firmware and
  `devices/descriptor_data/astrolabe_5way.json` together. Until then builds enumerate as ZMK's
  shared `0x1D50:0x615E`.
- Enable private vulnerability reporting when the repository goes public.

## P2 — Live host qualification

Record host version, projection, artifact, and result for each. If a supported host cannot finish
before the release candidate, demote it in `app_registry.py` and the README instead of waiving it.

For every host: horizon levelling from a rolled view, selection override with each pivot (Camera
stays turn-in-place), orientation + baseline + inversion + routing composing without a double sign,
Under Cursor orbit and To Cursor zoom on hits and misses with both holds, setup/update/reload, and
in Blender, Unity, and Unreal, Object mode (one, many, parent + child, empty selection, one Undo).

- **AutoCAD:** strict misses, reprojection with a still cursor, paper space, gesture-commit timing.
- **Onshape:** fabricated-hit rejection near extents, +Z horizon, orthographic and perspective.
- **Blender:** modal mouse tracking, tracker restart after file load, camera view, all modes.
- **SketchUp:** cursor-to-viewport mapping across display scaling and yearly versions.
- **Unreal:** cursor ray in the focused viewport, Object transactions, PIE no-op, both install paths.
- **Unity:** dynamic clipping restore, pivot-extent cap, Object undo, domain reload.
- **Fusion 360:** `findBRepUsingRay` on occurrence-only geometry.
- **SolidWorks:** COM throughput on large assemblies, multi-monitor DPI.
- **FreeCAD:** perspective camera, nested placements.
- **Rhino:** startup registration fallback, Under Cursor and Object Center.

## P3 — Feature parity

Not advertised until implemented and verified live:

- Camera (turn-in-place) orbit for FreeCAD, Fusion 360, and SolidWorks.
- A distinct Zoom vs. Dolly for FreeCAD, Onshape, and SolidWorks; define orthographic dolly first.

## P4 — Firmware depth (deferred past V1)

- Let behaviors send derived gestures to the daemon on bits 5–7 (decide tap vs. hold semantics).
- Let the daemon set a keymap layer on focus change (a `COMMAND_SET_LAYER` on the existing ACKed
  command path), and give it a contiguous owned layer range torn down on release.
- Report the active layer in the snapshot (a protocol version bump).

## P5 — Product backlog

- Automatic Pointer/3D switching from the focused app, with manual override and hold semantics.
- A UI firmware updater using the bootloader command `build.py` already uses.
- Keyboard and screen-reader pass over the web UI; profile export/import.
- Easier Onshape userscript installs and updates, without silent installs.
- Viewport-under-cursor selection for multi-viewport Blender layouts.
- Inline SPDX headers in `plugin_src/autocad/TrackballNavAcad/` at the next real DLL rebuild (until
  then `licensing.json` maps them).
- Enforce DCO sign-off in CI from a start-point commit.

## Accepted limitations

- SketchUp for Web has no local Ruby hook and is unsupported.
- Onshape's Camera pivot is not useful in its orthographic view.
- AutoCAD 2024 and older need a separate .NET Framework plugin build.
- A desktop 3Dconnexion NL-Proxy bridge is not viable: desktop hosts load `TDxNavLib.dll` directly.
