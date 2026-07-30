# Astrolabe

Astrolabe is a DIY dual-sensor optical trackball that works as both a normal Bluetooth mouse and a
three-axis controller for 3D applications.

- The **firmware** fuses two PMW3389 sensors into true X/Y/Z ball rotation and exposes both BLE HID
  mouse input and a custom rotation stream.
- The Windows **Trackball Daemon** consumes that stream, runs from the system tray, and switches
  between pointer input and focus-routed 3D navigation.
- Eleven integrations are included: Fusion 360, SolidWorks, AutoCAD, Onshape, Blender, FreeCAD,
  SketchUp Desktop, Unreal Engine, Unity, Godot, and Rhino.

The daemon is currently internal-alpha software. It is usable from source, and the repository can
build private onedir and per-user installer artifacts, but signed end-user builds have not been
released yet.

## Capabilities

- Cursor movement and wheel scrolling without a vendor driver.
- Orbit, pan, zoom, and dolly controls routed to the supported app that has focus.
- Free-orbit, turntable, fly, and walk navigation where the host camera supports them.
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
- The Astrolabe firmware on a compatible BLE trackball. The product controller target is the Seeed
  Studio XIAO nRF52840; the repository also retains XIAO/PMW3389 and SuperMini/PMW3610 validation
  sketches while the final hardware contract is completed.
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
guide, show/hide the control panel, inspect connection status, enable **Start at login**, or quit.
Input and navigation modes are changed by the declarative bindings shown under **Keybindings**.
One binding may target any selected set of registered apps, and setting cycles accept a
comma-separated sequence of two or more enum or numeric values.
Closing Settings or the guide hides/closes that window; it does not stop the daemon.

Do not pair the trackball as a Windows Bluetooth mouse while the daemon is consuming its BLE
rotation service. With the daemon closed, the firmware's ordinary HID mouse path works normally.

## First-time setup

The first-run guide walks through this sequence and can be reopened from the tray. The detailed
manual path remains:

1. Open tray → **Settings** and configure the device name or BLE address if discovery does not find
   the trackball.
2. Open **3D Apps**, expand an application's **Instructions**, and choose **Set up** or **Enable**.
   The panel shows detected versions, supported/unverified status, every file or trust change, a
   manual path, and a health check before it acts.
3. Complete the host-side step shown by the panel, if any, then restart or reload that host.
4. Select the hardware or keyboard-only input profile under **Keybindings**. Switch to **3D mode**,
   focus the host's 3D viewport, and move the ball. Hold **Shift** for pan/zoom with the shipped
   bindings; the keyboard-only profile uses **F12** to toggle Pointer/3D.
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
| Godot | EditorPlugin copied into a selected project and enabled in `project.godot`. | [Godot](docs/apps/godot.md) |
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
pressed-control, focus, and gesture state lives only in the daemon's `RuntimeStore`. Settings changes
apply live except for BLE device identity, which applies on reconnect, and host add-on code, which
applies when that host reloads it.

If an earlier build left a `%APPDATA%\TrackballDaemon` directory, its contents are copied to the
current location once, verified, and then left where they are: the old directory is never modified,
so reverting to an earlier build only means deleting the new one. Old log files stay behind rather
than being carried across.

### Pointer and 3D modes

- **Pointer mode** converts ball rotation into cursor movement or wheel input.
- **3D mode** sends orbit input by default. With the shipped binding, holding **Shift** switches the
  same motion to pan and zoom.
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
daemon restarts at the beginning of **Global → Orbit → Orbit pivot fallback order**. Unsupported
methods are skipped, duplicates are removed, and an empty list means no orbit is performed after the
primary target fails. **Selection overrides orbit center** has higher priority when enabled, except
that a Camera primary remains a true turn-in-place operation.

### Horizon entry and gesture holds

**Level horizon when entering Turntable/Walk** removes existing camera roll once when entering a
fixed-horizon mode. Each applicable app can inherit or override the Global value. Godot does not
show the setting because its editor camera cannot retain roll.

**Pivot hold** controls when a ray-derived orbit target is recaptured. **Zoom hold** is independent
and applies only to To Cursor zoom. Panning or zooming invalidates the orbit target; panning
preserves the held cursor-zoom target, while rotating the view invalidates that zoom target.

Free orbit needs **Twist action: Roll** for full three-axis rotation. Choosing Free no longer changes
Twist automatically; if another action is selected, a red warning icon beside Twist explains how to
restore three-axis orbit without overwriting the user's choice.

## Troubleshooting

- Confirm the tray says the BLE device is connected and the control panel reports **3D** mode.
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

The working five-way validation firmware is
[`firmware/PMW3610/PMW3610.ino`](firmware/PMW3610/PMW3610.ino) (SuperMini nRF52840, dual PMW3610,
advertised name `Astrolabe`). It fuses the two sensors, exposes BLE HID, publishes the custom
rotation characteristic, and publishes the five-way input-state snapshot used by the daemon. Its
validation loop is complete, but its SuperMini controller and board are not the product hardware. The
older
[`firmware/XIAO3389/XIAO3389.ino`](firmware/XIAO3389/XIAO3389.ino) dual-PMW3389 three-button bench
remains supported under advertised name `Trackball BLE`. Legacy rotation-only firmware also remains
supported. See [`docs/ble_device_adapters.md`](docs/ble_device_adapters.md) for the packet and
descriptor contract.
[`firmware/Astrolabe/Astrolabe.ino`](firmware/Astrolabe/Astrolabe.ino) is still the production-
hardware placeholder for the Seeed Studio XIAO nRF52840 target pending the remaining sensor, switch,
power, and recovery-control freeze. Prototype-only SuperMini pins and geometry remain in the PMW3610
sketch rather than being copied into that placeholder.

## Development and project documentation

- [`AGENTS.md`](AGENTS.md) — task-first contributor routing and documentation ownership.
- [`docs/architecture.md`](docs/architecture.md) — current shared firmware, daemon, state, mapping,
  routing, lifecycle, and integration contracts.
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

Astrolabe is open source. Everything in this repository today — the daemon, its host integrations,
firmware, tooling, tests, and documentation — is licensed under the
[Apache License 2.0](LICENSE). Hardware design source, once it exists, will be licensed under the
[CERN Open Hardware Licence Version 2 - Weakly Reciprocal](LICENSES/CERN-OHL-W-v2.txt).
[`LICENSING.md`](LICENSING.md) maps each path to its license.

Those licenses cover code and design source, not the project's name or logos. See
[`TRADEMARKS.md`](TRADEMARKS.md) before naming a modified build Astrolabe.
