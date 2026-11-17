# Astrolabe

**Make anything a 3D controller.**

Astrolabe is an open stack for 3D input. Firmware turns something you can turn — a ball, a knob, an
IMU — into a stream of rotation; a Windows daemon routes that rotation into whichever 3D app has
focus. The same device is an ordinary mouse the moment the daemon isn't there.

The reference controller is a trackball you can build: a SuperMini nRF52840, two PMW3610 optical
sensors watching one 52 mm ball (fused into true three-axis rotation, so twisting the ball is an
axis too), and a five-way switch. It drives ten apps: Blender, FreeCAD, Fusion 360, SolidWorks,
Onshape, SketchUp, Unreal Engine, Unity, Rhino, and AutoCAD.

```text
 ball / sensors                       Windows                                  apps
┌───────────────┐  BLE or USB   ┌─────────────────────┐  loopback      ┌───────────────────┐
│ firmware:     │ ────────────▶ │ daemon: focus,      │ ─────────────▶ │ add-on / COM /    │
│ fusion, route │  rotation +   │ bindings, mapping,  │  nav frames    │ browser bridge    │
│ mouse fallback│  buttons      │ settings UI, tray   │                │ moves the camera  │
└───────────────┘               └─────────────────────┘                └───────────────────┘
```

> **Status: internal alpha.** It runs daily on the reference hardware, but nothing is signed or
> released yet. [`TODO.md`](TODO.md) lists what stands between here and V1.

## Get started

**The daemon** (Windows 10/11, Python 3.13+):

```powershell
python -m pip install .
pythonw -m trackball_daemon
```

It lives in the tray. On first run it opens the setup guide; after that, **Open Settings** in the
tray. Nothing is installed into any app until you press **Set up** for it, and every change is
listed first.

**The firmware** (with the [Zephyr SDK 0.16](https://github.com/zephyrproject-rtos/sdk-ng/releases/tag/v0.16.8)
installed):

```powershell
python firmware/build.py --flash
```

No buttons needed: it reboots running firmware into its bootloader, reads which flash layout the
board needs, builds, and flashes. See [`firmware/README.md`](firmware/README.md).

## Using it

- **Pointer mode** — the ball is a mouse: tilt moves the cursor, twist scrolls.
- **3D mode** — the ball moves the camera of the app in focus: tilt orbits, twist rolls (or zooms),
  and holding **Shift** switches to pan and zoom. Mode-aware apps add Fly, Walk, and Object modes.
- On the reference trackball, **Center** toggles Pointer/3D and **Up/Right/Down** pick Orbit/Fly/Walk.
  Everything is rebindable under **Controls**, including keyboard chords.

The settings window has four pages: **Live** (a ball that turns exactly as yours does, plus what the
daemon is doing), **Apps** (turn apps on, set them up, and tune how each one feels — every app follows
**All apps** unless you give it its own value), **Controls**, and **Device** (orientation, cursor
feel, control panel). Settings live in `%APPDATA%\Mildly Useful\Astrolabe\config.json`; the log is
`daemon.log` beside it.

## Supported apps

Two separate facts are stated about each app. Its **release tier** is what this project promises
about the integration. Its **verified host versions** say which versions of the app it has been
tested against. They are independent — an experimental integration can run a verified version — so
they are never merged into one badge. Setup refuses a host version known not to work unless you
override the warning naming it.

### Supported integrations

Supported integrations gate the V1 release: an ordinary-navigation regression is release-blocking,
they get active compatibility maintenance, and their setup, update, and reversal are part of the
promise.

| App | How it connects | Guide |
|---|---|---|
| Blender | Python add-on; optionally auto-enabled at startup | [Blender](docs/apps/blender.md) |
| FreeCAD | Python `Mod` add-on | [FreeCAD](docs/apps/freecad.md) |
| Fusion 360 | Python add-in; enable **Run on Startup** once | [Fusion 360](docs/apps/fusion360.md) |
| SolidWorks | COM control of the running app; nothing installed | [SolidWorks](docs/apps/solidworks.md) |
| Onshape | Loopback TLS bridge plus a pointer userscript | [Onshape](docs/apps/onshape.md) |

### Experimental integrations

Experimental integrations are opt-in, may carry documented limitations, and are not promised to
survive every host update. A functional regression in one of them does not block a release; a
security, data-loss, configuration-corruption, or lifecycle defect still does.

| App | How it connects | Guide |
|---|---|---|
| SketchUp Desktop | Ruby extension in each detected version | [SketchUp](docs/apps/sketchup.md) |
| Unreal Engine | Editor Python plugin, per engine or per project | [Unreal Engine](docs/apps/unreal.md) |
| Unity | Editor package copied into a project | [Unity](docs/apps/unity.md) |
| Rhino 8 | Python scripts plus a startup command | [Rhino](docs/apps/rhino.md) |
| AutoCAD | .NET plugin, staged per user and `NETLOAD`ed | [AutoCAD](docs/apps/autocad.md) |

## Build your own controller

Anything that speaks the [device protocol](docs/protocol.md) is an Astrolabe controller: twelve bytes
of rotation in radians, a six-byte button snapshot, and a keepalive so the device can tell a live
daemon from a stale one. On ZMK it is two calls from your sensor driver —

```c
astrolabe_report_motion(rx, ry, rz);   // radians: +X right, +Y away, +Z up
astrolabe_register_source(wake);       // optional
```

— and the module brings the standalone mouse, both transports, route ownership, and the gestures
and status LED. Add a data-only descriptor naming your device's buttons and the daemon, bindings, and
settings UI pick it up. [`firmware/README.md`](firmware/README.md) walks through it.

Adding support for another app is described in [`docs/integrations.md`](docs/integrations.md).

## Troubleshooting

- **Not connecting over Bluetooth:** it can stay paired to Windows as a mouse — that is fine. If the
  daemon never found it, set its address under **Device**; Windows stops advertising a device it has
  paired, so discovery only works before the first pairing. `tools/ble_connect_probe.py` tells
  discovery, connection, and GATT problems apart.
- **Nothing happens in an app:** check **Live** — the app should be in focus, its integration on,
  and the mode 3D. **Apps → Setup** shows the integration's runtime health.
- **Choppy view:** lower that app's **Viewport refresh rate** under **Feel**.
- **After updating:** restart or reload the app so it loads the new add-on.

## Documentation

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | How the parts fit and what each one owns |
| [`docs/protocol.md`](docs/protocol.md) | The device protocol |
| [`docs/hardware.md`](docs/hardware.md) | The reference trackball: pins, geometry, gestures, LED |
| [`docs/integrations.md`](docs/integrations.md) | How app integrations work, adding and tuning one |
| [`docs/keybindings.md`](docs/keybindings.md) | Binding profiles, chords, and the action language |
| [`docs/security.md`](docs/security.md) | Listeners, permissions, and how to reverse every change |
| [`docs/releasing.md`](docs/releasing.md) | Checks, builds, and release gates |
| [`docs/apps/`](docs/apps/) | Per-app maintainer guides |

Contributors: start with [`CONTRIBUTING.md`](CONTRIBUTING.md). Report vulnerabilities privately per
[`SECURITY.md`](SECURITY.md).

## License

Astrolabe's software — daemon, integrations, firmware, tools, tests, and docs — is licensed under the
[Apache License 2.0](LICENSE). Hardware design source, once published, will be under
[CERN-OHL-W-2.0](LICENSES/CERN-OHL-W-v2.txt). [`LICENSING.md`](LICENSING.md) maps every path to its
license, and bundled components are attributed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
The name and logo are covered separately by [`TRADEMARKS.md`](TRADEMARKS.md).
