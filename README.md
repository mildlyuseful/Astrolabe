# Astrolabe

Astrolabe is a DIY dual-sensor optical trackball that works as both a normal Bluetooth mouse and a
three-axis controller for 3D applications.

- The **firmware** fuses two optical sensors into true X/Y/Z ball rotation and exposes an ordinary
  HID mouse plus a custom daemon stream over BLE or wired USB.
- The Windows **Trackball Daemon** consumes that stream, runs from the system tray, and switches
  between pointer input and focus-routed 3D navigation.
- Ten integrations are included: Fusion 360, SolidWorks, AutoCAD, Onshape, Blender, FreeCAD,
  SketchUp Desktop, Unreal Engine, Unity, and Rhino.

The daemon is currently internal-alpha software. It is usable from source, and the repository can
build private onedir and per-user installer artifacts, but signed end-user builds have not been
released yet.

## Capabilities

- Cursor movement and wheel scrolling without a vendor driver.
- Orbit, pan, zoom, and dolly controls routed to the supported app that has focus.
- Free-orbit, turntable, fly, and walk navigation where the host camera supports them.
- Selected-object rotation and View/Ground translation in Blender, Unity, and Unreal, with
  independent object-axis routing, multi-selection grouping, and host undo.
- Screen-center, under-cursor, selection, model-center, world-origin, camera, and Blender 3D-cursor
  orbit targets, exposed only where each host has a real implementation.
- A configurable fallback chain when a requested orbit target is unavailable.
- Surface-aware **To Cursor** zoom. Empty-space misses keep the cursor position stable by using a
  sensible scene-depth point; orbit misses remain strict and follow the fallback chain.
- Global physical-axis remapping plus per-app and per-action source, inversion, and gain controls.
- Separate orbit-pivot and cursor-zoom hold times.
- Optional one-shot horizon leveling when entering Turntable, Lock Horizon, or Walk.
- Per-app refresh rates with explicit System defaults, Global values, and linked app overrides.
- Local-only integration traffic and explicit consent before certificate, trust-path, startup, or
  project-file changes.

## Requirements

- Windows 10 or 11.
- Python 3.13 or newer for a source install. Packaged builds embed their own runtime and do not
  need Python installed.
- The Astrolabe firmware on a compatible BLE/USB trackball. The hardware is frozen: a SuperMini
  nRF52840 controller, two PMW3610 sensors on a 52 mm ball, and an ALPS SKRHADE010 five-way switch.
  See [`docs/hardware.md`](docs/hardware.md) for the full contract. Legacy rotation-only and
  three-button bench firmware also remain supported by the daemon.
- A supported 3D application only if you want its navigation integration.

## Install and run

From a PowerShell prompt in the repository:

```powershell
python -m pip install ".[onshape]"
python -m trackball_daemon
```

The installed distribution is named `astrolabe-daemon` and provides an `astrolabe` command. Its
import package stays `trackball_daemon`, so `python -m trackball_daemon` keeps working.

For normal console-free use:

```powershell
pythonw -m trackball_daemon
```

For the optional cube verification window:

```powershell
python -m trackball_daemon --debug
```

On first run the daemon opens a setup guide for device connection, input-state inspection,
supported-versus-experimental application review, existing integration consent/instructions,
navigation health, and the optional **Start at login** choice. It performs no host, certificate,
startup, or project changes without the corresponding explicit action. The guide remains available
from the tray.

Later starts show only the passive control panel. Use the tray icon to open Settings or the setup
guide, show/hide the control panel, inspect connection and battery status, enable **Start at login**,
or quit. Battery Level appears in the tray tooltip, the tray menu, and beside Connection at the
bottom of Settings when the connected firmware publishes the standard BLE Battery Service.
Input and navigation modes are changed by the declarative bindings shown under **Keybindings**.
One binding may target any selected set of registered apps, all non-integrated applications, or a
combination of both. Setting cycles accept a comma-separated sequence of two or more enum or numeric
values.
Closing Settings or the guide hides/closes that window; it does not stop the daemon.

The trackball may stay paired as an ordinary Windows Bluetooth mouse while the daemon runs. The
daemon takes the rotation stream from a device Windows is already holding, and the firmware hands
the route back on its own if the daemon exits, crashes, or the device is powered off — so the
device never ends up connected with a dead cursor. The firmware automatically prefers its exact
vendor USB interface when wired and falls back to BLE after USB loss. With the daemon closed, the
firmware's ordinary HID mouse path works normally on either ZMK output.

## First-time setup

The first-run guide walks through this sequence and can be reopened from the tray. The detailed
manual path remains:

1. Open tray → **Settings** and configure the device name or BLE address if discovery does not find
   the trackball. The daemon remembers the address once it has connected, which is what lets it
   reach a device that Windows has paired and is therefore no longer advertising.
2. Open **3D Apps**, expand an application's **Instructions**, and choose **Set up** or **Enable**.
   The panel shows detected versions, supported/unverified status, every file or trust change, a
   manual path, and a health check before it acts.
3. Complete the host-side step shown by the panel, if any, then restart or reload that host.
4. Select the hardware or keyboard-only input profile under **Keybindings**. Switch to **3D mode**,
   focus the host's 3D viewport, and move the ball. Hold **Shift** for the active mode's secondary
   controls (pan/zoom in Orbit); the keyboard-only profile uses **F12** to toggle Pointer/3D.
5. Tune the app under **Per-App**. **Reset app** pins that app's concrete System values and breaks
   its Global links; **Link all to Global** removes app overrides. Neither action disables the
   integration or forgets its installed add-on version.

The status row and tray list the versions of add-ons that are actually connected. The tray also
summarizes runtime health, while **3D Apps** shows the navigation broker and gated SolidWorks,
AutoCAD, and Onshape owners as disabled, waiting, healthy, degraded, or failed with the current
actionable detail. Bundled add-ons are versioned and copied again when a newer daemon bundle is
available; host restart/reload is usually required before new code is active.

Add-in setup stages and verifies complete payloads before replacing an installed copy. Independent
host versions or projects are updated separately, and the result identifies any destination that
could not be changed while leaving its previous copy intact.

The 3D Apps panel uses the version reported by the currently or most recently connected host copy
when deciding whether to show **Update**. This exposes a stale project/document-local copy even when
another install destination is current; the update action still refreshes all destinations detected
for that integration.

## Integration summary

Every integration starts disabled and is enabled explicitly. Detailed and current setup instructions
are built into the **3D Apps** panel; the linked documents are maintainer guides for the corresponding
driver or add-on.

Two independent things are said about each integration, and they answer different questions.

**Release support** is what this project promises about the integration as a whole. **Host-version
compatibility** is whether the particular copy on your machine has been verified. Every combination
occurs — an experimental integration commonly runs a verified host version, and a supported one can
meet a host version known not to work — so they are never merged into one badge.

### Supported integrations

A supported integration gates the public V1 release, is advertised only for its verified host-version
range, treats an ordinary-navigation regression as release-blocking, receives active compatibility
maintenance, and includes its setup, update, reversal, and runtime-health behaviour in that claim.

| Application | Integration and one special setup step | Maintainer guide |
|---|---|---|
| Blender | Per-user Python add-on; setup can optionally install an auto-enable startup shim. | [Blender](docs/apps/blender.md) |
| FreeCAD | Per-user `Mod/TrackballNav` Python add-on; restart FreeCAD. | [FreeCAD](docs/apps/freecad.md) |
| Fusion 360 | Per-user Python add-in; run it once and enable **Run on Startup** in Fusion. | [Fusion 360](docs/apps/fusion360.md) |
| SolidWorks | Direct COM control of an already-running instance; no host add-in is installed. | [SolidWorks](docs/apps/solidworks.md) |
| Onshape | Loopback TLS bridge; explicitly trust/accept its certificate and install the supplied pointer userscript for accurate under-cursor targeting. | [Onshape](docs/apps/onshape.md) |

### Experimental integrations

An experimental integration is opt-in and clearly labelled, may ship with documented host
limitations, and carries no promise for every host update. An isolated functional regression in one
of these does not block a release. A shared security, data-loss, configuration-corruption, or
lifecycle defect still does.

| Application | Integration and one special setup step | Maintainer guide |
|---|---|---|
| SketchUp Desktop | Ruby extension copied into detected annual Plugins folders; restart SketchUp. | [SketchUp](docs/apps/sketchup.md) |
| Unreal Engine | Editor Python plugin; enable it once. A project-local install avoids administrator rights. | [Unreal Engine](docs/apps/unreal.md) |
| Unity | Editor package copied into a selected project's `Packages` folder. | [Unity](docs/apps/unity.md) |
| Rhino 8 | Per-user Python scripts plus a startup command; restart Rhino. | [Rhino](docs/apps/rhino.md) |
| AutoCAD | Bundled .NET plugin, staged per user and automatically `NETLOAD`ed after explicit setup. | [AutoCAD](docs/apps/autocad.md) |

### Host versions

The **3D Apps** panel states each integration's verified host versions beside its release tier, and
flags the detected copy separately. An *unverified* version may work but has not earned a claim; a
*known-unsupported* version is refused by setup unless you explicitly override the warning naming it.
Tiers live in `trackball_daemon/app_registry.py` and version ranges in
`trackball_daemon/integrations.py`; tests fail if these tables and the registry disagree.

## Controls and configuration

All persistent user configuration lives in `%APPDATA%\Mildly Useful\Astrolabe\config.json`; transient mode,
pressed-control, focus, and gesture state lives only in the daemon's `RuntimeStore`. Numeric settings
apply after a short typing pause (or immediately on Enter/focus change); other Settings changes apply
live except for BLE device identity, which applies on reconnect, and host add-on code, which applies
when that host reloads it.

If an earlier build left a `%APPDATA%\TrackballDaemon` directory, its contents are copied to the
current location once, verified, and then left where they are: the old directory is never modified,
so reverting to an earlier build only means deleting the new one. Old log files stay behind rather
than being carried across.

### Pointer and 3D modes

- **Pointer mode** converts ball rotation into cursor movement or wheel input.
- Pointer sensitivity is constant by default. Under **Global → Pointer**, an optional Linear or
  Smooth acceleration curve can increase cursor speed from a configurable ball-speed onset to a
  bounded maximum multiplier. Acceleration affects cursor movement only, never wheel or 3D motion.
- **3D mode** sends primary input for the active navigation mode. With the shipped binding, holding
  **Shift** selects that mode's secondary controls without changing the mode itself (pan/zoom in
  Orbit, movement in Fly/Walk/Object).
- Blender, Unity, and Unreal also offer **Object mode**. Primary motion rotates selected root objects
  as a group around their shared center. The secondary layer translates the group: **View** moves
  right/up in the viewport plane with twist for depth, while **Ground** mirrors Walk movement:
  planar motion moves sideways/forward on the ground and twist moves up/down. Object Pitch/Yaw/Roll and
  Translate X/Y/Z each have an independent source axis and invert switch, and **Movement sensitivity**
  scales only the secondary translation layer. An empty selection is a no-op. Choose Object under the
  app's Mode setting, or assign **Switch navigation to Object** / **Hold Object mode** in Keybindings.
- The mode switch is global and manual. Navigation is otherwise selected by the foreground app.

### Physical orientation and action routing

**Global → Physical transform** maps physical sensor X/Y/Z to logical axes once, before both pointer
and 3D routing. Source changes remain a permutation, so an axis cannot be accidentally duplicated or
lost.

Each app's **Per-App → Axis routing** category then chooses the source, inversion, and gain for
Orbit, Pan, Zoom, and the mode-specific actions offered by richer integrations. This is the user
layer; software-convention corrections are supplied by immutable host baselines.

### Orbit targets and fallback order

The app's selected **Orbit pivot** is tried first. If it is unsupported or a raycast misses, the
daemon restarts at the beginning of the app's effective **Orbit pivot fallback order**. Edit the
default under **Global → Orbit**, or use the link control beside the same setting under **Per-App**
to give one host its own order. Unsupported methods are skipped, duplicates are removed, and an
empty list means no orbit is performed after the primary target fails. **Selection overrides orbit
center** has higher priority when enabled, except that a Camera primary remains a true turn-in-place
operation.

### Horizon entry and gesture holds

**Level horizon when entering Turntable/Walk** removes existing camera roll once when entering a
fixed-horizon mode. Each applicable app can inherit or override the Global value.

**Pivot hold** controls when a ray-derived orbit target is recaptured. **Zoom hold** is independent
and applies only to To Cursor zoom. Panning or zooming invalidates the orbit target; panning
preserves the held cursor-zoom target, while rotating the view invalidates that zoom target.

Free orbit needs **Twist action: Roll** for full three-axis rotation. Choosing Free no longer changes
Twist automatically; if another action is selected, a red warning icon beside Twist explains how to
restore three-axis orbit without overwriting the user's choice.

## Troubleshooting

- Confirm the tray says the device is connected over BLE or USB and the control panel reports
  **3D** mode.
- Confirm the intended host is foreground, its integration is enabled, and the 3D viewport has
  focus where the host requires it.
- Open **3D Apps** and inspect the runtime-health detail before running the listed host health
  check. A connected row shows the loaded integration version; degraded and failed rows retain the
  transport or protocol error that needs attention.
- Check `%APPDATA%\Mildly Useful\Astrolabe\daemon.log`; host-specific log paths are listed in each app's
  Instructions panel and maintainer guide.
- Lower the app's **Viewport refresh rate** if navigation queues or stutters.
- Restart the host after an add-on update. AutoCAD may require closing the host before a locked DLL
  can be replaced.
- Review [security and permissions](docs/security.md) before treating an unsigned-build, trust, or
  certificate warning as unexpected.

## Firmware

The production firmware is the pinned ZMK workspace under [`firmware/zmk/`](firmware/zmk/). Its
out-of-tree Astrolabe module targets `nice_nano_v2`, reads both PMW3610 sensors over their shared
three-wire bus, fuses them into three-axis ball rotation, and retains ZMK's ordinary BLE/USB HID
mouse behavior. When the daemon claims ownership it instead emits the frozen rotation and five-way
snapshots over the custom BLE service or a second vendor USB HID interface. USB preference,
acknowledged attach, keepalive, exact session leases, and BLE fallback are implemented in the daemon.

Standing alone, the device is an ordinary mouse: Down, Right, and Center are left, right, and middle
click, and yaw-dominant ball rotation becomes scroll. Up and Left carry no button, so they host the
recovery and radio gestures — double-tap to toggle USB/BLE output or step BLE profile, hold three
seconds for bootloader or bond clear. Those gestures exist only in standalone; with a daemon
attached the same positions are plain control bits with nothing arbitrating in front of them.

Because three of those gestures change radio or endpoint state with no host to report it, the
controller's red LED blinks a short pattern naming the state the device ended up in — which output
is active, which BLE profile, and whether the thing you selected could actually be applied. It is
dark at rest. The vocabulary is in [`docs/hardware.md`](docs/hardware.md).

The firmware builds from exact ZMK and Zephyr commits in CI and retains its UF2, ELF, effective
configuration, DTS, frozen manifest, hashes, and license provenance. It runs on the current
fixture, where HID output, the gestures, the indicator, route handover, and the daemon's keepalive
fail-safe are confirmed. It is still not qualified for distribution: the development USB VID/PID is
not product identity, and sensor pose calibration, switch bounce margin, sleep/wake, battery, and
latency remain release blockers in [`TODO.md`](TODO.md). Hardware is frozen in
[`docs/hardware.md`](docs/hardware.md); build and design details are in
[`docs/zmk_migration_plan.md`](docs/zmk_migration_plan.md).

Three Arduino sketches are retained as diagnostics and compatibility references, not as products:
[`firmware/PMW3610/`](firmware/PMW3610/PMW3610.ino) is the five-way validation prototype that proved
the sensor loop and the battery service on this controller,
[`firmware/XIAO3389/`](firmware/XIAO3389/XIAO3389.ino) is the dual-PMW3389 three-button protocol
bench that still guards the host packet boundary in CI, and
[`firmware/Astrolabe/`](firmware/Astrolabe/Astrolabe.ino) is a superseded placeholder. All three are
retired once the ZMK firmware passes its live replacement gates. Legacy rotation-only firmware
remains supported by the daemon. See [`docs/ble_device_adapters.md`](docs/ble_device_adapters.md)
for the packet and descriptor contract.

## Development and project documentation

- [`AGENTS.md`](AGENTS.md) — task-first contributor routing and documentation ownership.
- [`docs/architecture.md`](docs/architecture.md) — current shared firmware, daemon, state, mapping,
  routing, lifecycle, and integration contracts.
- [`docs/hardware.md`](docs/hardware.md) — the frozen controller, sensor, ball, and switch contract,
  with the pin map and geometry the firmware implements.
- [`TODO.md`](TODO.md) — open verification, parity gaps, risks, and product work.
- [`docs/apps/`](docs/apps/) — host-specific implementation and maintenance guides.
- [`docs/default_profiles.md`](docs/default_profiles.md) — host alignment and shipped-default data.
- [`docs/keybindings.md`](docs/keybindings.md) — profiles, chords, dependency cascading, declarative
  actions, schemas, and contributor examples.
- [`docs/feature_parity.md`](docs/feature_parity.md) — the currently enforced capability contract.
- [`docs/security.md`](docs/security.md) — local listeners, permissions, reversal steps, and release
  hardening.
- [`docs/release.md`](docs/release.md) — release channel and versioning rules, what the release
  manifest records, and the daily-driver observation contract for the internal alpha.
- [`docs/release_verification.md`](docs/release_verification.md) — automated and manual release gates
  with explicit skip/release-impact rules.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — inbound licensing, Developer Certificate of Origin sign-off,
  and the checks to run before opening a pull request.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability privately.

Run the automated checks with:

```powershell
python -m pytest -q
python -m compileall -q trackball_daemon tests
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj
```

Host camera behavior still requires smoke testing inside the corresponding GUI. See `TODO.md` for
the current verification matrix rather than relying on an old test count or version snapshot.

## License

Astrolabe is open source. Astrolabe-authored daemon, integration, firmware, tooling, test, and
documentation source is licensed under the
[Apache License 2.0](LICENSE). Hardware design source, once it exists, will be licensed under the
[CERN Open Hardware Licence Version 2 - Weakly Reciprocal](LICENSES/CERN-OHL-W-v2.txt).
[`LICENSING.md`](LICENSING.md) maps each path to its license; bundled daemon and firmware components
are attributed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Those licenses cover code and design source, not the project's name or logos. See
[`TRADEMARKS.md`](TRADEMARKS.md) before naming a modified build Astrolabe.
