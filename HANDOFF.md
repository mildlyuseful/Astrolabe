# Trackball project — master handoff

**Read this first if you are taking over the whole project.** It describes the entire system
(firmware + daemon + every 3D-app integration), the cross-cutting **gotchas and solved problems you
cannot see by reading the code**, and the **full list of future plans / requested-but-unimplemented
options**. Per-component deep dives live in their own docs (linked in §15); this file is the map that
ties them together and covers the things that span more than one component.

> Snapshot at time of writing (versions drift — see §13): daemon `__version__` **0.1.43**, Fusion
> add-in **0.1.13**, Blender add-on **0.1.9**, FreeCAD add-on **0.1.3**, SketchUp extension
> **0.2.0**, Unreal add-on **0.2.0**,
> `pyproject` version is dynamic (single-sourced from `__version__`; packaging not yet cut). Dev machine: Windows 11, Blender 5.1.1, SolidWorks
> 2025, Fusion 360, FreeCAD 1.1.1, SketchUp 2026.2, Unreal Engine 5.8, AutoCAD 2026 installed. The firmware has no
> version field.

---

## Table of contents
1. [What the project is](#1-what-the-project-is)
2. [Repository map](#2-repository-map)
3. [Hardware & firmware](#3-hardware--firmware-xiao3389ino)
4. [The BLE rotation protocol](#4-the-ble-rotation-protocol)
5. [Daemon architecture & data flow](#5-daemon-architecture--data-flow)
6. [Modes: cursor vs 3D (cube)](#6-modes-cursor-vs-3d-cube)
7. [3D-app integration framework](#7-3d-app-integration-framework)
8. [The integrations](#8-the-integrations-fusion-solidworks-autocad-onshape-blender-freecad-sketchup-unreal)
9. [Configuration](#9-configuration)
10. [UI, tray, logs, diagnostics](#10-ui-tray-logs-diagnostics)
11. [Build / run / flash / test](#11-build--run--flash--test)
12. [Cross-cutting gotchas & solved problems](#12-cross-cutting-gotchas--solved-problems)
13. [Versioning & shipping a change](#13-versioning--shipping-a-change)
14. [Future plans & requested-but-unimplemented](#14-future-plans--requested-but-unimplemented)
15. [The other docs](#15-the-other-docs)

---

## 1. What the project is

A DIY **dual-sensor optical trackball** and the software that makes it useful. Two halves:

- **Firmware** ([`firmware/XIAO3389/XIAO3389.ino`](firmware/XIAO3389/XIAO3389.ino)) on a **Seeed XIAO nRF52840** reads **two PMW3389**
  optical sensors aimed at the ball, fuses them into **true 3-axis ball rotation**, and presents
  **two things over BLE at once**: a normal **HID mouse**, and a **custom GATT service** that streams
  the fused rotation. It can seamlessly switch between "act like a mouse" and "act like a 3-axis
  controller" with **no physical button** — the switch is driven by whether the daemon is subscribed.

- **Daemon** (`trackball_daemon/`, Python, Windows) is a **system-tray app** that consumes the
  rotation stream and either **injects mouse movement** (cursor mode) or **drives a 3D CAD app's
  camera** like a 3Dconnexion SpaceMouse (3D mode). It supports **Fusion 360, SolidWorks, AutoCAD,
  Onshape, Blender, FreeCAD, SketchUp Desktop, and Unreal Engine** today, routed to whichever app is focused.

The original proof-of-concept was a single pygame window ([`cube_test.py`](cube_test.py)) that
rotated a cube 1:1 with the ball. The daemon **preserves that math byte-for-byte** (see §12.1) and
wraps it in an app shell + settings UI + the CAD integrations.

---

## 2. Repository map

```
firmware/XIAO3389/XIAO3389.ino   Test-bench firmware: dual-PMW3389 fusion → BLE HID mouse + custom rotation GATT service
firmware/Astrolabe/Astrolabe.ino Placeholder for the production firmware (different sensors; will replace XIAO3389)
cube_test.py                 Legacy standalone pygame cube (the math's origin; kept for reference/--debug parity)
tools/sw_diag.py             Throwaway SolidWorks COM probe (diagnostics; not part of the package)
requirements.txt / pyproject.toml   Deps + packaging (Nuitka onedir / pip gui-script)
README.md             USER-facing: how to run + per-app setup steps
HANDOFF.md                   THIS FILE — whole-system maintainer handoff

trackball_daemon/            The daemon package
  __main__.py                Entry point (python -m trackball_daemon [--debug])
  __init__.py                __version__ (daemon build number; drives Nuitka/pip)
  paths.py                   Per-user config dir (%APPDATA%\TrackballDaemon)
  util.py                    File logger (safe under pythonw / no console) → daemon.log
  config.py                  THE single source of truth (JSON); defaults reproduce cube_test.py exactly
  ble.py                     BLE ingestion (scan/connect/subscribe/reconnect) — unchanged data path
  output.py                  OutputEngine: SendInput + quaternion + routing math (verbatim from cube_test)
  winfocus.py                Foreground-window process/title (route nav to the focused app)
  navbroker.py               127.0.0.1 TCP nav broker → streams orbit/pan/zoom to SOCKET add-ons
  solidworks_driver.py       In-process SolidWorks COM driver (no add-in; drives a running SW)
  onshape_bridge.py          In-process Onshape bridge (TLS-WebSocket NL-Proxy emulator for the browser)
  autocad_driver.py          AutoCAD plugin LOADER (COM NETLOAD delivery only; the old COM nav
                             transport + overlay are archived at archive/autocad_com_transport/)
  plugins/autocad/TrackballNavAcad.dll   Bundled NETLOAD plugin — the SOLE AutoCAD transport
                             (live GS kernel view, ~1.6 ms/frame, ZERO regens; a SOCKET/broker client)
  integrations.py            3D-app registry: detect / set up / install add-ons / auto-update
  tray.py                    pystray tray icon + menu (process lifecycle)
  ui.py                      Tkinter settings window (General + Per-App Bindings tabs)
  debugview.py               Optional pygame cube (--debug only)
  app.py                     Orchestrator: wires everything; focus-routes nav to the right driver
  plugins/fusion360/TrackballNav/    Bundled Fusion add-in (.py + .manifest) — a SOCKET add-on
  plugins/blender/trackball_nav/     Bundled Blender add-on (__init__.py + version.json) — a SOCKET add-on
  plugins/blender/startup/           Auto-enable shim copied into Blender's scripts/startup
  plugins/freecad/TrackballNav/      Bundled FreeCAD add-on (Init.py + InitGui.py + tbnav_freecad.py
                                     + pure-math tbnav_camera.py + version.json) — a SOCKET add-on
  plugins/sketchup/                  Bundled SketchUp Ruby extension (top-level loader +
                                     trackball_nav/{main.rb,camera.rb,version.json}) — a SOCKET add-on
  plugins/unreal/TrackballNav/       Bundled Unreal Editor plugin (TrackballNav.uplugin + version.json +
                                     Content/Python/{init_unreal.py, trackball_nav.py, pure-math
                                     tbnav_unreal_camera.py}) — a SOCKET add-on (content-only plugin)

plugin_src/autocad/          C# source of the AutoCAD NETLOAD plugin (dotnet build -c Release; needs .NET 8 SDK)
tools/blender_nav_*.py       Headless Blender test scripts (math / integration / socket probes)
tools/sketchup_nav_selftest.rb  Interactive SketchUp Ruby Console camera/raycast/socket self-test
tests/                       pytest: bit-exact output, SW driver, app routing, blender wiring, integrations
docs/apps/<app>.md           Per-app maintainer guides, named by app key (see §15)
docs/spikes/                 Investigation reports (e.g. the desktop-navlib spike)
```

The **daemon Python** and each **add-on** (including SketchUp's embedded Ruby) run in **different interpreters** and
only talk over the broker socket. SolidWorks, Onshape, and AutoCAD are driven **in-process** (COM /
TLS-WS / COM) with no add-on. That interpreter/transport split is the source of a lot of the gotchas.

---

## 3. Hardware & firmware ([`firmware/XIAO3389/XIAO3389.ino`](firmware/XIAO3389/XIAO3389.ino))

**Wiring** (in the header comment): shared SPI `SCK=D8 MISO=D9 MOSI=D10`; sensor CS `A=D7 B=D6`;
buttons L/R/M = `D0/D1/D2` to GND; on-board LED for status.

**Dual-sensor fusion (the heart of it).** Two PMW3389 sensors look at the ball at known spherical
positions (`SENSOR_A/B_PHI/THETA`) and mounting angles. Each reports 2D surface motion (`dx,dy`). The
firmware builds a **least-squares pseudo-inverse** `pinv` (`buildSolver()`) that maps the 4 sensor
deltas → the ball's 3D angular velocity `(wx,wy,wz)`. The solver output is `R_counts × radians`, so
dividing by `R_COUNTS` (ball radius in sensor counts, `≈866` at 1600 CPI / 55 mm) yields **true
radians**. This is integrated per 1 kHz poll into `gx,gy,gz`.

**Two outputs, always live:**
- **HID mouse** (`BLEHidAdafruit`) — standard Bluetooth mouse, works standalone.
- **Custom rotation GATT service** (`[GATT]` blocks) — one **notify** characteristic shipping the
  integrated `(rx,ry,rz)` radians (see §4). `notify()` is a no-op until a client subscribes, so the
  service is purely additive — the HID mouse is unchanged whether or not anything listens.

**Seamless mode toggle, no button (`[MODE]`).** When a client **enables notifications** (writes the
CCCD) on the rotation characteristic, `rotCccdCallback` flips `g_controller = true`: the firmware
**stops driving the HID mouse** and only streams rotation. Unsubscribe **or disconnect** →
`g_controller = false` → plain mouse again. `Bluefruit.begin(2,0)` allows **two simultaneous
peripheral links** (e.g. Windows HID + the daemon). LED: slow blink = disconnected, fast blink =
controller mode, solid = mouse mode.

**Cursor/scroll mapping in mouse mode** (firmware-side, when not in controller mode): horizontal ball
motion → pointer; **yaw twist** dominating (via `YAW_DEADZONE`/`YAW_DOMINANCE_RATIO`) → scroll wheel
with an 80 ms hold. This is the on-device version of what the daemon's cursor mode also does — but in
normal use the daemon is subscribed, so the firmware is in controller mode and the **daemon** does the
cursor mapping.

**IPS reporting & the cap (`[IPS]`/`[PMW3610]`).** The firmware measures **peak raw sensor speed**
(inches/sec) per sensor and prints it over Serial every 8 s (`peak ... | session max ...`). "Session
max" = the slowest tracking speed the device actually demands — used to **spec/validate sensors**.
Independently, `clampSensorIps()` caps each poll's raw motion at `IPS_CAP` **(currently 30 ips —
emulating a PMW3610's tracking ceiling; set to `9999` to disable)**. The cap is applied at the **raw
input stage, after measuring**, using the **actual elapsed poll time** (not a nominal 1 ms) — see
§12.6 for why that detail mattered. The peak readout is always **pre-cap/raw**.

**Tuning knobs at the top of the file:** `SENSOR_CPI`, `CURSOR_GAIN`, `CURSOR_SWAP_XY/INVERT_*`,
`SCROLL_*`, `YAW_*`, `BALL_DIAMETER_MM`, `ROT_SIGN_X/Y/Z` (per-axis rotation direction — the daemon
also has sign knobs), `IPS_CAP`, `BLE_SEND_MS` (notify period), `POLL_INTERVAL_US` (1 kHz).

---

## 4. The BLE rotation protocol

- **Service UUID** `2cad0001-6e64-0146-b139-9cf2a4cd57fc`, **characteristic**
  `2cad0002-6e64-0146-b139-9cf2a4cd57fc` (the daemon's `config.device.char_uuid`). Bluefruit takes
  128-bit UUIDs **little-endian** (reversed byte arrays in the firmware).
- **Packet:** 12 bytes = **3 × float32 little-endian** `(rx, ry, rz)` = integrated ball rotation in
  **radians** since the previous notify. Sent every `BLE_SEND_MS` (7 ms) while connected.
- **Why integrated deltas, float-carried:** the firmware accumulates radians in floats and **zeros
  after each notify**, so total rotation tracks the ball **regardless of packet timing/loss** — the
  same "carry the remainder" idea reused throughout the daemon (drivers coalesce motion the same way).
- Signs are applied on-device (`ROT_SIGN_*`) and again, optionally, in the daemon (per-app invert) —
  see §12.5 (don't double-invert).

The daemon side is intentionally a thin reader: [`ble.py`](trackball_daemon/ble.py) scans by name (or
connects by address), subscribes to the characteristic, and hands each 12-byte packet to
`OutputEngine.handle_packet`. Config edits to device name/address take effect on the **next
reconnect**.

---

## 5. Daemon architecture & data flow

**Threads (all daemon-side):**
- **main thread** — hidden Tk root + mainloop (owns all GUI; the window shows/hides on demand).
- **tray thread** — pystray icon loop; menu callbacks marshalled to Tk via `root.after`.
- **BLE thread** — asyncio bleak loop (the data path).
- **broker threads** — accept + sender (NavBroker).
- **SolidWorks worker** — its own `CoInitialize`d COM thread.
- **AutoCAD worker** — its own `CoInitialize`d COM thread (attaches to a running AutoCAD).
- **Onshape server/reader/worker threads** — TLS accept, per-connection WAMP reader, nav worker.
- **debug thread** — optional pygame cube (`--debug`).

The process stays alive on the Tk mainloop and exits **only** via tray → **Quit**. Closing the
settings window **hides to tray**.

**End-to-end data flow:**
```
BLE packet (rx,ry,rz radians)
  → OutputEngine.handle_packet            (output.py; VERBATIM cube_test math)
       • cursor mode → SendInput mouse move/scroll (pure ctypes)
       • 3D mode     → orbit/pan/zoom deltas → self._emit_nav(...)
  → App._nav_sink                          (only when a supported, ENABLED, FOCUSED app is up)
       routes by focused app:
         onshape     → OnshapeBridge.submit()      (in-process TLS-WS to the browser)
         solidworks  → SolidWorksDriver.submit()   (in-process COM to a running SW)
         else        → NavBroker.submit()          (socket add-ons: Fusion, Blender, FreeCAD,
                                                    SketchUp, Unreal, AutoCAD's NETLOAD plugin)
  → each driver/broker ACCUMULATES the 6-float delta and FLUSHES one coalesced frame at rate_hz
  → (broker only) ──TCP──> the focused app's add-on → its viewport camera
```

Every nav transport (the broker + the SolidWorks/Onshape in-process drivers) exposes the **same
surface** so `app.py` wires and routes them identically:
`submit(ox,oy,oz,px,py,zoom)` (BLE thread, accumulate-only, never blocks) · `set_rate(hz)` ·
`set_scheme(orbit_pivot, orbit_style, zoom_mode[, advanced])` · `start()` / `stop()` ·
`is_connected()` / `version()` · an `on_connection_changed(connected, version)` callback that feeds
the tray/UI "Apps:" status. (The AutoCAD plugin *loader* is not a transport — just `start()`/`stop()`.)

**The cardinal rule:** the **BLE/data path and the cursor/cube math are bit-exact** with the original
and must stay that way (§12.1). New integrations **add a sink**; they never alter `handle_packet` or
the BLE loop.

---

## 6. Modes: cursor vs 3D (cube)

`OutputEngine.mode` is `MODE_CURSOR` or `MODE_CUBE`, toggled from the tray (and chosen at startup by
`general.default_mode`). It is a **global** software switch (not per-app).

- **Cursor mode** — each packet's rotation becomes pointer/wheel input via `SendInput` (pure ctypes,
  no dependency). Yaw-dominant twist → scroll (with deadzone/dominance), otherwise → pointer move.
  Sub-pixel/notch remainders are carried.
- **3D (cube) mode** — each packet becomes an **orbit** increment (axis-angle → delta quaternion,
  composed in world frame) that rotates the internal view **and** is emitted to `nav_sink`. Holding
  **Shift** (system-wide, via `GetAsyncKeyState`) switches to **pan** (ball plane) / **zoom** (twist),
  mutually exclusive via the same dominance test cursor mode uses. The internal cube state still
  updates so `--debug` shows it; the same deltas drive the focused CAD app.

`toggle` per app can be `"shift"` (default) or `"none"` (always orbit). The daemon **already scales**
o/p/z by the per-app bindings (sensitivity/gain + the generic invert) before the sink — each add-on
only bakes in a **baseline sign/scale** and the **scheme**, so you must not re-scale in both places.

---

## 7. 3D-app integration framework

Two delivery mechanisms, chosen per app by what that app can accept:

| Mechanism | Apps | How |
|---|---|---|
| **Socket add-on** via `NavBroker` | Fusion 360, Blender, FreeCAD, SketchUp, Unreal Engine | The daemon streams newline-JSON frames on `127.0.0.1:47900`; a bundled add-on running **inside** the app connects and applies frames on the app's **main thread**. Fusion/Blender/FreeCAD/Unreal marshal from a reader thread; SketchUp polls the non-blocking socket directly from `UI.start_timer`. |
| **In-process driver** (no add-on) | SolidWorks (COM), Onshape (TLS-WS), AutoCAD (COM) | A driver **inside the daemon** talks directly to a running app (SolidWorks / AutoCAD COM automation) or to the browser (impersonating the 3Dconnexion NL-Proxy). |

**Broker frame:** `{"o":[ox,oy,oz], "p":[px,py], "z":zoom, "op":<orbit_pivot>, "os":<orbit_style>,
"zm":<zoom_mode>, "adv":{...}}`. `"adv"` (Blender's richer nav options) is **additive** and present
**only when non-None**, so Fusion's frames are byte-unchanged and Fusion ignores keys it doesn't read.

**Focus routing** (`app.py::_foreground_app_key`): the daemon checks the foreground process name
(`fusion`, `blender`, `sldworks`, `acad`, …). Onshape is special — its foreground process is the
**browser**,
and the tab title is the document name, not "Onshape" (§12.8) — so Onshape is selected when a browser
is foreground **AND** the bridge is connected, with Onshape's own **focus** signal as the fine gate.
Nav only flows in **3D mode** and only to an **enabled** app.

**Control scheme** (`config.effective_scheme`): `orbit_pivot` (view/**pointer**/object/origin/cursor,
plus `viewpoint` in Blender/SketchUp/Unreal), `orbit_style` (free/turntable), `zoom_mode`
(to_center/to_object/to_cursor/**to_pointer**). `pointer`/`to_pointer` (daemon 0.1.39, additive — no
CONFIG_VERSION bump) = orbit/zoom about the surface under the **MOUSE POINTER**, deliberately
DISTINCT from `cursor`/`to_cursor` (selection / Blender's 3D cursor, unchanged); the daemon wires the
value through every dropdown + broker frame once, and each app only needs a plugin-side pivot
resolver — implemented so far in **FreeCAD (add-on 0.1.3)**, **AutoCAD (NETLOAD plugin 0.3.0,
PointMonitor)**, and **Fusion (add-in 0.1.12, GetCursorPos + screenToView)**; apps without one fall
back to their view/object pivot. **UI labels ≠ stored values here (user-requested rename):** the
dropdowns SHOW the under-mouse pivot as **"cursor (under mouse)"** / "Under Cursor (mouse)" and the
legacy `cursor` value as **"selection"** (so only one cursor-named option exists), but the STORED
scheme values remain `pointer`/`to_pointer`/`cursor` — every plugin and broker frame speaks the old
names; do not rename the values. Legacy `to_cursor` (a to_center alias everywhere) is no longer
offered in the dropdowns but still parses. Set in
General (default) or per app (per-app `"default"` inherits General). `app._apply_schemes` pushes the
focused app's effective scheme to its driver; the **focused broker app's** `advanced` block (Blender's
or Unreal's) is attached to the broker scheme (§12.9).

**The shared "view-pivot raycast" idea.** For the `view` orbit pivot, camera apps cast a ray
down the **screen centre** to the **real surface depth** under the crosshair (like native middle/right-
drag orbit), validate the hit against the model bbox, **fall back** to the model centre on a miss, and
**hold the pivot for the whole gesture** (re-cast only on pan/zoom or after an idle gap). The *concept*
is identical across Fusion (`findBRepUsingRay`), SolidWorks (`SelectByRay`), Onshape (navlib
`hit.lookat`), Blender (`scene.ray_cast`), FreeCAD (`view.getObjectInfo`), and SketchUp
(`view.pickray` + `model.raytest`) — but each app's pick API has its
**own** brutal gotchas (§12.7). This is the single most-reused and most-debugged feature.

---

## 8. The integrations (Fusion, SolidWorks, AutoCAD, Onshape, Blender, FreeCAD, SketchUp, Unreal)

Each has a dedicated maintainer doc (§15) — read it before touching that integration. Summary:

- **Fusion 360** — *socket add-on* (`plugins/fusion360/TrackballNav`). Reads broker frames via a
  background socket thread, applies on Fusion's main thread via a **CustomEvent**, drives
  `app.activeViewport.camera`. **Set up** copies the add-in; **auto-update** re-copies on a version
  bump (takes effect on Fusion's next launch). Gotchas in [`docs/apps/fusion360.md`](docs/apps/fusion360.md) and the code:
  no external automation API (verify via `%APPDATA%\TrackballDaemon\fusion_addin.log`), **`adsk.core`
  has no `Point3DList`** (use `ObjectCollection` for `findBRepUsingRay` hit points), the active Design
  isn't reliably `app.activeProduct`. Add-in **0.1.13** adds the **`pointer` orbit pivot /
  `to_pointer` zoom** (shown as "cursor (under mouse)" in the UI; raycast the surface under the
  mouse cursor): the cursor is read on-demand at gesture start via ctypes `GetCursorPos` — NOT a
  mouseMove Command (modal → intrusive). Fusion's MIXED coordinate model was pinned by two live
  user passes at 125%: **`screenToView` takes LOGICAL screen px but returns PHYSICAL viewport px;
  `viewToModelSpace` consumes PHYSICAL; `vp.width/height` are LOGICAL** — so the input is ÷ the
  monitor's DPI scale (0.1.12: fixed the down-right offset) and the output bounds check is ×
  the scale (0.1.13: the logical bounds wrongly rejected the right/bottom ~20% band). §14 / the
  add-in's POINTER PIVOT block.

- **SolidWorks** — *in-process COM driver* (`solidworks_driver.py`), **no add-in** (those need admin
  registration). Attaches to a **running** SolidWorks via `GetActiveObject`, drives the view with
  native methods. The richest pivot support (origin/object/view/cursor) and the most COM gotchas.
  → [`docs/apps/solidworks.md`](docs/apps/solidworks.md).

- **AutoCAD** — a bundled **NETLOAD .NET plugin** is the **SOLE transport**
  (`plugin_src/autocad` → `plugins/autocad/TrackballNavAcad.dll`); the *plugin loader*
  (`autocad_driver.py`, COM) only delivers it into a running AutoCAD (copy to
  `%APPDATA%\TrackballDaemon\acad_plugin\` + `TRUSTEDPATHS` + LISP NETLOAD, once per session — zero
  user steps; ROT attach because `GetActiveObject` is unreliable, §12.13). The plugin is a normal
  **broker/socket client** (`app.py` routes autocad frames to the broker **unconditionally**, like
  Fusion/Blender) and drives the viewport's **live GraphicsSystem kernel view**
  (`ObtainAcGsView(vpn, {"3D Drawing"})` + `SetView`/`Update`, ~1.6 ms/frame, **ZERO regens**,
  free-roll in the up vector) with **one DB sync per gesture** — regen-free in 3D visual styles
  (`SetViewportFromView(regenRequired:false)`), via the VPORT-record write + immediate
  `UpdateTiledViewportsFromDatabase` + **one real `REGEN`** in "2D Wireframe" whose 2D pipeline
  presents from a projected cache (notes §8.16 — read the crash taxonomy before touching it;
  `GetCurrentAcGsView` is a trap, the kernel-descriptor accessor is the live one). Rebuild:
  `dotnet build -c Release plugin_src/autocad/TrackballNavAcad` (.NET 8 SDK; AutoCAD 2025–2027
  binary-compatible). Plugin **0.3.0** adds the **`pointer` orbit pivot / `to_pointer` zoom**
  (orbit/zoom about the point under the mouse via a passive `Editor.PointMonitor` cache — §14 /
  notes §8.17). The old **COM nav transport** (reassign-commit orbit + deferred-orbit overlay cube;
  regens ~50 ms per orbit commit, `Center` property destructive, no `VIEWTWIST`, no raycast — the
  full verified ActiveX view model) is **ARCHIVED at `archive/autocad_com_transport/`**: as a
  fallback it attached before the plugin finished its broker handshake, claimed the first frames of
  each session (flicker + overlay), and could land a stale deferred orbit after the plugin took
  over. Resurrect it only for a target with no in-process path.
  → [`docs/apps/autocad.md`](docs/apps/autocad.md).

- **Onshape** — *in-process TLS-WebSocket bridge* (`onshape_bridge.py`) that **impersonates the
  3Dconnexion NL-Proxy** at `127.51.68.120:8181`, speaking WAMP. Onshape's page connects, hands us its
  camera (`view.affine`), and we run the nav model. No add-in/extension; needs a one-time **cert
  trust**. → [`docs/apps/onshape.md`](docs/apps/onshape.md).

- **Blender** — *socket add-on* (`plugins/blender/trackball_nav`), the **richest** target: orbit
  (free/turntable, 5 pivots), pan/zoom/dolly/roll, **fly/walk** first-person modes, camera-view
  driving, per-mode/per-axis inverts, an in-Blender **Alt+`** mode toggle, and an "Advanced" settings
  section. → [`docs/apps/blender.md`](docs/apps/blender.md) (read first) +
  [`docs/apps/blender_design.md`](docs/apps/blender_design.md).

- **FreeCAD** — *socket add-on* (`plugins/freecad/TrackballNav`), modelled on Fusion/Blender. A
  background socket thread reads broker frames into a queue; a main-thread **PySide `QTimer`** drains
  it and drives the active 3D view's **Coin (pivy) `SoCamera`**. **Set up** copies the add-on into
  FreeCAD's *versioned* user Mod dir (`%APPDATA%\FreeCAD\v1-1\Mod` on 1.x), and it **auto-starts** on
  FreeCAD's next launch (FreeCAD runs every `Mod/*/InitGui.py` — no enable shim needed, unlike
  Blender). Camera math is pure/headless-testable (`tbnav_camera.py`). The FreeCAD-specific traps
  (InitGui's separate-globals/locals exec → thin shim + sibling module; `pivy.coin` must be imported
  before `getCameraNode`; `QTimer.singleShot` deferral gated on `FreeCAD.GuiUp`; versioned user dir)
  are in [`docs/apps/freecad.md`](docs/apps/freecad.md) (read first) and the code.
  Verified live on FreeCAD 1.1.1. Add-on **0.1.3** adds the **`pointer` orbit pivot / `to_pointer`
  zoom** (orbit/zoom about the surface under the live mouse pointer — the FIRST app with it, §14): a
  passive `SoLocation2Event` observer caches the pointer pixel (device-px bottom-left, same
  convention `getObjectInfo` takes — no y-flip, verified live) and the `view`-pivot raycast/hold
  machinery reuses it.

- **SketchUp Desktop** — *Ruby socket extension* (`plugins/sketchup`). A repeating main-thread
  `UI.start_timer(0.02, true)` owns both non-blocking TCP polling and camera application, avoiding a
  Ruby reader thread entirely. The explicit `Sketchup::Camera` eye/target/up model uses
  `Geom::Transformation.rotation` for free/turntable orbit; pan translates eye+target; perspective
  zoom dollies and parallel zoom scales `Camera#height`. `view` pivot uses `View#pickray` plus
  `Model#raytest`, validates against `model.bounds`, and holds the surface point per gesture. **Set
  up** copies the loader + `trackball_nav/` to every annual
  `%APPDATA%\SketchUp\SketchUp <year>\SketchUp\Plugins` dir; the loader registers and auto-enables it
  for the next launch. Verified live on SketchUp 2026.2.243 / Ruby 3.2.2: the initial API probe,
  broker hello (`sketchup v0.1.0`), and clean installed-loader restart passed. Extension `0.2.0`
  adds the Blender-parity **Viewpoint / Fly / Walk** camera models and independent
  Orbit/Viewpoint/Fly/Walk
  inversion groups (no 3D cursor): viewpoint and fly rotate about the eye, fly movement follows the
  camera axes, and walk horizon-locks look while projecting movement onto world XY. All 23 production
  camera assertions pass live, and `0.2.0` is installed for the next SketchUp restart; physical
  sign/feel remains a calibration TODO. See
  [`docs/apps/sketchup.md`](docs/apps/sketchup.md).

- **Unreal Engine** — *socket add-on* (`plugins/unreal/TrackballNav`), a **content-only Unreal
  plugin** modelled on FreeCAD/Blender. A background socket thread reads broker frames into a queue;
  a **Slate post-tick** callback on the editor's main thread drains it and drives the level-editor
  **perspective viewport camera** (`UnrealEditorSubsystem.get/set_level_viewport_camera_info`, with
  the deprecated `EditorLevelLibrary` as the UE4.27 fallback). The editor camera is a **free-fly
  eye+FRotator** (no view-distance/look-at), so orbit-about-a-pivot and zoom are **synthesised** and
  the location+rotator are written back each frame; camera math is pure/headless-testable
  (`tbnav_unreal_camera.py`). It carries the **full Blender-parity scheme** (add-on 0.2.0): an
  `advanced` block with **orbit / fly / walk** modes (fly banks + flies along the look; walk is
  horizon-locked + ground-plane — verified they differ), **viewpoint** pivot (turn in place),
  `twist_action`, `lock_horizon`, and **per-mode inverts** — the same additive `"adv"` object Blender
  uses (now attached per-focused-app, §12.9). Unreal has **no 3D cursor** (probed), so `cursor` orbits
  the selection; and its default orbit baseline is **doubled** (`ORBIT_SCALE=2.0`) because the device
  felt half at 1.0. **Set up** copies the plugin into each detected engine's
  `Engine/Plugins` (writing there needs **admin** → falls back to printed manual steps / a project
  `Plugins` dir), and the user **enables it once** in *Edit → Plugins → "Trackball" → restart* (the
  plugin depends on the Python Editor Script Plugin, so enabling ours enables Python too). The
  Unreal-specific traps (left-handed/Z-up/cm/degrees conventions verified live; `Rotator(roll,pitch,
  yaw)` positional order; `make_rot_from_xz` for the rotator rebuild; `HitResult.to_dict()` for trace
  hits; the project-centric/admin install) are in
  [`docs/apps/unreal.md`](docs/apps/unreal.md) (read first) and the code. **API +
  conventions + plugin auto-load verified live, headless, on Unreal Engine 5.8**; the GUI sign/scale
  feel is a live-tune TODO.

---

## 9. Configuration

- **One JSON file**: `%APPDATA%\TrackballDaemon\config.json` ([`config.py`](trackball_daemon/config.py)
  is the single source of truth). The UI and the OutputEngine both read/write it; edits **apply live**
  (device name/address on next reconnect).
- **Defaults reproduce `cube_test.py` exactly** — moving the numbers into config must not change the
  math (guarded by `tests/test_output_bitexact.py`).
- **Deep-merge on load** (`_deep_merge`): disk values overlay the defaults, so **new keys appear
  automatically** on upgrade — **adding a key needs no `CONFIG_VERSION` bump or migration**. Caveat:
  deep-merge never *removes* keys, so retired keys linger harmlessly (e.g. `invert_viewpoint_roll`).
- **Migrations** are only for **semantic** changes. The single existing one (`v1→v2`) reset 3D
  bindings when per-app correction factors moved into the add-ons (so they wouldn't double up).
- **Shape:** `device`, `general` (default_mode, cursor/scroll mapping, default `scheme`), `apps.<key>`
  (enabled/installed, `rate_hz`, `view_pivot_hold_sec`, `bindings` {orbit/pan/zoom/toggle/invert/
  scheme}, and Blender-only `advanced`), `active_app`, `bridge` {port, rate_hz}, and a top-level
  `onshape` {address, port, cert_path, key_path}.

---

## 10. UI, tray, logs, diagnostics

- **Tray** (`tray.py`): Open Settings, connection status, mode toggle, Quit. **Quit is the only exit.**
- **Settings** (`ui.py`): a **General** tab (device, default mode, cursor/scroll, default control
  scheme, the global bridge rate) and a **Per-App Bindings** tab (per app: orbit/pan/zoom gains,
  per-axis **Invert** checkboxes, viewport **refresh rate**, control-scheme combos, SolidWorks'
  view-pivot-hold, and Blender's whole **Advanced** section — merged in, scrollable). The 3D-Apps rows
  show detect/Set-up/Update/connection status.
- **Logs:** the daemon writes `%APPDATA%\TrackballDaemon\daemon.log`. The **Fusion add-in** writes
  `fusion_addin.log`; the **Blender add-on** writes `blender_addin.log`; Onshape logs into
  `daemon.log` (with `TB_ONSHAPE_DEBUG=1` for verbose). These add-on logs are the **only** way to see
  inside the add-on interpreters — lean on them.
- **Standalone harnesses:** `python -m trackball_daemon.onshape_bridge [--spin] [--force]`;
  `tools/blender_nav_*.py` (run via `blender --background --python`); `tools/sw_diag.py` for SolidWorks COM.

---

## 11. Build / run / flash / test

**Firmware:** Arduino IDE with the **Seeed nRF52 Boards (non-mbed)** package + Adafruit Bluefruit;
include `Adafruit_TinyUSB.h`. Flash to the XIAO nRF52840. Open Serial @ 115200 to see the IPS report
and connection interval.

**Daemon:**
```sh
pip install -r requirements.txt
python  -m trackball_daemon          # tray app (with console)
pythonw -m trackball_daemon          # tray app, NO console (normal use)
python  -m trackball_daemon --debug  # also opens the pygame cube verification window
```
Tkinter is stdlib. `pygame`/`PyOpenGL` are only for `--debug`. `pywin32` (SolidWorks) and
`cryptography` (Onshape cert) are Windows-only and degrade gracefully if absent.

**Tests:**
```sh
python -m pytest tests -q     # bit-exact output, SW driver (mocked COM), app routing, blender wiring, integrations
# Blender (headless — exercises the REAL add-on against a real RegionView3D, no GUI/hardware):
BL="/c/Program Files/Blender Foundation/Blender 5.1/blender.exe"
"$BL" --background --factory-startup --python tools/blender_nav_math_test.py
"$BL" --background --factory-startup --python tools/blender_nav_integration_probe.py
"$BL" --background --factory-startup --python tools/blender_nav_socket_probe.py
```
SolidWorks is **live-testable** on the dev box (attach a throwaway `SolidWorksDriver` and call
`_flush`); Fusion and Onshape need the real app + the add-on/cert (verify via their logs).

**Packaging** (planned, not yet cut — §14): Nuitka onedir (`--standalone --enable-plugin=tk-inter
--windows-console-mode=disable`) or `pip install git+…` → `trackball-daemon` gui-script. Bundled
add-ins ship as package data so "Set up" can copy them.

---

## 12. Cross-cutting gotchas & solved problems

The non-obvious, system-wide history. (Per-integration gotchas are in their own docs' §8.)

### 12.1 The bit-exact constraint
`output.py` (SendInput, quaternion, routing) is **copied verbatim** from `cube_test.py`; only the
numbers come from config now. Config **defaults are chosen to reproduce the original exactly**, and
`tests/test_output_bitexact.py` guards it. **Do not "tidy" the data path or the math.** New behavior
is added as a `nav_sink` tap and per-app bindings folded into cached signs (default-off → no-op).

### 12.2 Windows BLE coexistence — why the daemon injects the mouse
You **cannot** have Windows pair the trackball as an HID mouse **and** have `bleak` attach to it at the
same time on Windows: a paired HID peripheral stops advertising the way bleak needs, so bleak reports
"device not found" / can't attach. Early attempts to "let Windows do the mouse and the daemon do 3D"
failed for exactly this reason. **Resolution:** the daemon owns the BLE link, the **firmware's CCCD
toggle** suppresses its own HID mouse while the daemon is subscribed (§3), and the **daemon injects the
pointer via `SendInput`**. The firmware's native HID is the standalone fallback when the daemon is
closed. **Operational rule: don't also pair the trackball in Windows Bluetooth while the daemon runs.**

### 12.3 The `cannot access local variable 'device'` bug
In an early `ble.py`, the direct-connect-by-address path referenced `device` (only set on the scan
path). Fixed by using `client.address` for the status line. The current loop is clean — keep the
two paths (`address` vs `find_device_by_name`) distinct.

### 12.4 Float-carry / accumulate-then-flush everywhere
Firmware integrates radians and zeroes on notify; every driver/broker **accumulates** the 6-float
delta and **flushes one coalesced frame** at `rate_hz`, zeroing after. A slow viewport therefore
**sums** motion instead of dropping it. Don't "simplify" this to send-immediately — it both loses
motion and floods slow apps.

### 12.5 Signs live in two places — don't double-invert
Direction can be flipped on-device (`ROT_SIGN_*`), in the daemon (per-app `bindings.invert`, folded
into cached signs in `output.py`), and in each add-on (baseline `*_SIGN`/`ORBIT_SCALE`). The
convention: **the add-on/driver bakes the baseline orientation; the daemon's Invert is a user
preference on top.** For **Blender**, the generic per-app invert is **hidden in the UI and left off**
because Blender uses **per-mode** inverts in the add-on (§12.9) — wiring both would double-invert.

### 12.6 The IPS cap saga (firmware)
The original cap used a **nominal 1 ms** poll interval, so it clamped too early under real jitter. It
was reworked to use the **actual elapsed poll time** with the *same* formula as the peak readout, so
"cap at X ips" means X ips **as measured**. The cap then emulated a PMW3610's ceiling; the user asked
to **remove the caps and just report peak raw IPS** to spec the real sensor — so the **peak readout is
always pre-cap**, and `IPS_CAP` is a knob (currently 30; `9999` disables). If the device feels
limited, check `IPS_CAP`.

### 12.7 Every app's "view-pivot raycast" pick API is differently cursed
The shared concept (§7) hides per-app traps, each of which cost real debugging:
- **Fusion:** `adsk.core.Point3DList` doesn't exist in Python — pass `adsk.core.ObjectCollection`.
- **SolidWorks:** `SelectByRay`'s `Tol` must be a `VT_I4` integer VARIANT (a float silently selects
  nothing); count via `GetSelectedObjectCount2`; clear+save/restore the selection.
- **Onshape:** `hit.lookfrom/direction/aperture` are **write-only** (reading returns "unknown
  property" — expected); never read `hit.lookat` through the caching reader (a no-hit error would
  poison the cache).
- **Blender:** `scene.ray_cast` is **depsgraph-first**; build the ray with `view3d_utils`.
- **FreeCAD:** `view.getObjectInfo((cx,cy))` does the pick for you (returns a dict with world
  `x/y/z`, or **`None`** off-model) — but you must `import pivy.coin` first or *any* camera-node
  access raises `No SWIG wrapped library loaded` (§8 / freecad notes).
- **SketchUp:** `view.pickray(view.vpwidth*0.5, view.vpheight*0.5)` feeds `model.raytest`; since
  SketchUp 2025 the viewport sizes and accepted coordinates are logical-pixel **Floats**, not the
  pre-2025 physical-pixel Integers.
All six **validate against the model bbox and fall back to the model centre**, and **hold the pivot
per gesture**. When adding another app, expect a fresh pick-API gotcha and code defensively.

### 12.8 Routing Onshape by window title silently dropped every frame
The browser titles the tab with the **document** name, not "Onshape", so a title match returned `None`
and `_nav_sink` dropped everything ("connected but nothing moves"). Fixed by routing on **browser
process + bridge connected**, with Onshape's own focus signal as the precise gate.

### 12.9 The `advanced` block rides per-focused-app, and inverts must live in the add-on
`nav_mode` (orbit/fly/walk) and the rest of the richer nav options ride in the broker frame's `"adv"`.
**Blender, SketchUp, and Unreal carry an `advanced` block**, and the broker has a single `"adv"` slot, so
`app._apply_schemes` attaches the **focused broker app's** advanced (Blender's when Blender is focused,
SketchUp's or Unreal's when that app is focused; Fusion/FreeCAD have none → `None`, which they ignore). This replaced
the earlier "**always** attach Blender's adv" special-case — that was a workaround for Blender being the
only advanced app and can't coexist with a second one. `key` (the focused broker app) **persists across
a focus loss** (it's only reassigned in `_nav_sink`), so a mode change made while the settings window is
up still targets the right app, and each app's mode reaches its add-on on focus. (Caveat unchanged from
before: if two broker apps are connected at once, all connected add-ons apply the streamed frames — the
unfocused one just moves off-screen; a per-client scheme would be the real fix.) And because the **same
physical channel means different things per mode** (ball-forward is orbit pan-Y but fly/walk forward),
direction inverts are **per-mode, applied in the add-on**, not in the daemon's generic invert. The
in-Blender **Alt+`** toggle exists because the trackball **cannot drive Blender's native modal Walk/Fly**
(those read the mouse directly and ignore our view edits); Unreal has no equivalent in-editor toggle, so
its mode is set from the daemon dropdown.

### 12.10 Two interpreters → two reload rules
A change to a **socket add-on** (Fusion/Blender/FreeCAD/SketchUp) needs the **app** to reload it (Fusion:
stop/run or restart; Blender: F3 "Reload Scripts"; FreeCAD: **restart** — no reload-scripts
equivalent; SketchUp: restart, or development `load '.../main.rb'` in the Ruby Console). A change to
anything in the **daemon** (config/ui/app/broker/drivers) needs the
**daemon** restarted. A change to both needs both. This is the #1 cause of "I changed it and nothing
happened." In-process drivers (SW/Onshape) only need the daemon restarted.

### 12.11 Add-in auto-update is version-gated
`integrations.auto_update` re-copies a bundled add-on **only when its bundled version > installed**
(Fusion reads `.manifest`, Blender reads `version.json`). So shipping an add-on change **requires
bumping its version** (Fusion: `ADDIN_VERSION` + manifest; Blender: `bl_info` + `ADDIN_VERSION` +
`version.json` — three places; FreeCAD: `ADDIN_VERSION` + `version.json` — two; SketchUp: loader
`ADDIN_VERSION` + main `ADDIN_VERSION` + `version.json` — three). Forgetting the bump
means the user's app keeps the old code.

### 12.12 `ORBIT_SCALE`/`*_SCALE` are per-add-on and independent — and the orbit magnitude is camera-model-specific
Each add-on/driver bakes its OWN baseline `ORBIT_SCALE`/`PAN_SCALE`/`ZOOM_SCALE`; **none of it lives
in shared daemon code** (`output.py` emits `recv * sensitivity` with the default sensitivity `1.0` and
no other factor — the `--debug` cube rotates by that full angle, the canonical 1:1 reference). So a
scale change in one add-on **cannot** affect another. In particular the **orbit magnitude** convention
splits by camera model: the **eye+target** apps — **Fusion, SolidWorks, Onshape, FreeCAD, and
SketchUp** — use
magnitude **1.0** (rotate by the full ball angle = match the cube = true 1:1), while **Blender uses
0.5** because of its `RegionView3D`. Copying Blender's `0.5` into an eye-camera add-on makes that app
orbit at **half** speed (this exact bug shipped in FreeCAD 0.1.1 and was fixed to 1.0 in 0.1.2). When
adding/porting an add-on, take the orbit scale from **Fusion**, not Blender, unless the target is a
Blender-style view.

### 12.13 Attaching to COM apps: enumerate the ROT, don't trust `GetActiveObject`
Both COM attachers (the SolidWorks driver and the AutoCAD plugin loader) work by **enumerating the
Running Object Table**, not via
`GetActiveObject(ProgID)`. For SolidWorks it's because `GetActiveObject` grabs whichever instance
registered first (often a stray empty one — SW §8.2); for **AutoCAD** it's because `GetActiveObject(
"AutoCAD.Application")` was observed **raising `-2147221021` "Operation unavailable"** even with AutoCAD
running and a drawing open (AutoCAD notes §8.1). The shared pattern: `GetRunningObjectTable()` →
`EnumRunning()` → normalize each dispatch (SW by `GetDocumentCount`, AutoCAD by `.Application.Name ==
"AutoCAD"`) → pick the one with the most open docs → fall back to `GetActiveObject`. Attach **only,
never launch**.

### 12.14 AutoCAD's viewport is read-desynced, write-regens, and `.Center` is a trap
*(This documents the COM nav transport, ARCHIVED at `archive/autocad_com_transport/` since daemon
0.1.41 — the NETLOAD plugin is the sole AutoCAD transport now. Kept because the facts cost live
probing to earn and apply to ANY external AutoCAD automation.)*
Unlike SolidWorks' `IModelView` (whose reads are live and cheap), AutoCAD's `doc.ActiveViewport`
returns a **clone that does not reflect the displayed view** (reads stale defaults), writes to it are
inert until you **reassign** `doc.ActiveViewport = vp`, that reassign **REGENs** (~50 ms — the orbit
flicker; `REGENMODE=0` doesn't suppress it), and the `.Center` property is **destructive** off-origin
(`Center=(0,0)` ballooned VIEWSIZE 27→118). So the AutoCAD driver: **reads the view from sysvars**
(`VIEWDIR`/`VIEWSIZE`, and `VIEWCTR` to seed the pivot — *not* `TARGET`, which can sit far from the
geometry after a Zoom Extents); does **orbit** (the only op that must change Direction) via the reassign
(setting Direction+Target+**Height** so its intermediate repaint isn't a default-zoom flash) **then**
`ZoomCenter(pivot, VIEWSIZE)` to re-frame (never setting `.Center`); and does **pan + zoom** via the
regen-free `ZoomCenter`/`ZoomScaled` methods. The reassign REGENs unconditionally (verified:
`REGENMODE=0` doesn't help; no redraw-only rotation over COM), and that regen **can't be separated from
the rotation** — so orbit is either per-frame (default, continuous+flickery) or deferred
(`_ORBIT_DEFER`, flicker-free+choppy); truly smooth needs ObjectARX. All centring is tracked through one
3D pivot point so the ops compose without jumps. This is the AutoCAD analogue of SolidWorks'
"RotateAboutAxis ignores its point arg" — a view-model surprise you'd never guess from the API names,
and the source of the live-reported bugs fixed here (AutoCAD notes §8.2/§8.3/§8.9/§8.10/§8.11/§8.12).

---

## 13. Versioning & shipping a change

- **Daemon build**: `trackball_daemon/__init__.py::__version__` (shown in the tray; bump per release).
- **Fusion add-in**: `ADDIN_VERSION` in `TrackballNav.py` **and** `version` in `TrackballNav.manifest`
  (must match; drives auto-update + the handshake the tray shows).
- **Blender add-on**: `bl_info["version"]` **and** `ADDIN_VERSION` **and** `version.json` (all three).
- **FreeCAD add-on**: `ADDIN_VERSION` **and** `version.json` (both).
- **SketchUp extension**: `ADDIN_VERSION` in loader + main **and** `version.json` (all three).
- **Unreal plugin**: Python `ADDIN_VERSION`, `.uplugin` version name, and `version.json`.
- **SolidWorks/Onshape/AutoCAD**: no add-in version (driven in-process); just the daemon build.
- Add-on versions are **independent** of the daemon build (they move on their own cadence). To ship an
  add-on change: bump its version → restart the daemon (auto-update re-copies) → reload it in the app.

---

## 14. Future plans & requested-but-unimplemented

Everything that was discussed/requested but not finished, so nothing is lost in the handoff.

**Apps & hardware**
- ~~**FreeCAD integration**~~ — **DONE** (socket add-on, §8 / [`docs/apps/freecad.md`](docs/apps/freecad.md)).
  This completed the original **5-app plan** (Fusion, SolidWorks, Onshape, Blender, FreeCAD). Remaining
  FreeCAD polish: a **sign/scale calibration pass on real hardware** (the `ORBIT_SCALE`/`PAN_*`/`ZOOM_*`
  defaults in `tbnav_camera.py` are best-guesses; direction is flippable via the per-app Invert
  checkboxes), and the perspective-camera path is lightly exercised live (FreeCAD defaults to ortho).
- ~~**Unreal Engine integration**~~ — **DONE** (content-only socket add-on, §8 /
  [`docs/apps/unreal.md`](docs/apps/unreal.md)). The editor Python API, the
  left-handed/Z-up/cm/degrees camera conventions, and the **script-only-plugin auto-load** were all
  verified **live, headless** on Unreal Engine 5.8 (a pythonscript commandlet). Remaining Unreal
  polish: the **live GUI sign/scale calibration** (`ORBIT_SIGN`/`PAN_*`/`ZOOM_*` in
  `tbnav_unreal_camera.py` are best-guesses — the one thing not verifiable headless, since the
  viewport camera is meaningless without the GUI), and the **install ergonomics** (writing into an
  engine `Engine/Plugins` dir needs admin; the no-admin path is a per-project `Plugins` drop, and the
  plugin must be enabled once in *Edit → Plugins*).
- ~~**SketchUp Desktop integration**~~ — **DONE** (Ruby socket extension `0.2.0`, verified live on
  SketchUp 2026.2.243, §8 / [`docs/apps/sketchup.md`](docs/apps/sketchup.md)). Remaining polish: a
  physical trackball sign/feel pass for `ORBIT_SCALE`/`PAN_*`/`ZOOM_*`. SketchUp for Web remains
  deliberately out of scope because it has no local Ruby hook.
- ~~**AutoCAD integration**~~ — **DONE** (compiled NETLOAD plugin v0.3.0, the sole transport since
  daemon 0.1.41; the COM nav transport is archived at `archive/autocad_com_transport/`). The full
  road there — the COM-era view model, the per-frame-regen discovery, the regen-free GS kernel-view
  transport, the 2D-Wireframe snap-back/crash taxonomy, and the PointMonitor pointer pivot — is the
  solved-problem history in [`docs/apps/autocad.md`](docs/apps/autocad.md) **§8.9–§8.18** (read
  §8.16's crash taxonomy before touching the commit path). Remaining AutoCAD polish: (1) the **live
  GUI sign/scale calibration** of the plugin (`OrbitSign/Pan*/Zoom*` in Plugin.cs — best-guesses,
  pan/zoom scale especially); (2) plugin pivot modes (`origin`/`object` still orbit like `view` —
  `pointer` is implemented, v0.3.0) and `to_object` zoom are TODO; (3) the plugin needs a per-era
  rebuild when AutoCAD breaks .NET binary compat (2025–2027 are one family), and 2024-or-older
  needs a .NET Framework variant.
- **PMW3610 third sensor** — the design anticipates a PMW3610. Firmware currently **emulates** its
  tracking ceiling via `IPS_CAP` (and reports raw peak IPS to spec the real part). Real PMW3610
  support (its own driver class + possibly a 3-sensor fusion solver) is future work.
- **Generalize the 3Dconnexion NL-Proxy bridge to desktop apps** — **INVESTIGATED (Milestone-0 spike,
  2026-06-30) → not viable as a socket bridge; deferred by decision.** See
  [`docs/spikes/spacemouse_desktop_navlib.md`](docs/spikes/spacemouse_desktop_navlib.md). The leading hypothesis
  (desktop navlib apps connect to a loopback WebSocket on :8181 we could occupy, like the web NL-Proxy)
  is **falsified**: native apps (verified on FreeCAD 1.1 + KiCad 9.0, driver-free) **compile in** the
  navlib C++ accessor wrapper but reach the device through the low-level navlib **C ABI**
  (`NlCreate`/`NlReadValue`/…) in a **driver-provided `TDxNavLib.dll`** loaded via
  `LoadLibrary("TDxNavLib")`. With no driver that DLL is absent, so the app **opens no socket/pipe at
  all** — there is nothing to impersonate. The web bridge only worked because Onshape ships the
  3Dconnexion *client* in its page; desktop apps ship the wrapper but not the client runtime. The one
  driver-free desktop path is to **ship our own clean-room `TDxNavLib.dll`** (export the navlib C ABI;
  the app loads ours and we drive its camera via the registered accessors, reusing the Onshape
  `_navigate` math). That's a **native DLL** (needs a C toolchain — none installed) + a **per-app DLL
  drop**, and is **redundant for the five existing integrations** (all already driver-free, FreeCAD
  included); its only unique value is **closed-source apps with no scripting hook** (Solid Edge/NX/
  Inventor/Rhino). Deferred for that reason. *Web* navlib generalization (other browser CAD that uses
  the same WAMP NL-Proxy) is still in-scope for the existing Onshape bridge.

**Control features (need a new channel/signal)**
- **Discrete view ops** — Frame/View Selected, ortho axis snaps (Front/Top/Right), 15° orbit steps,
  recenter on cursor. These are **button/gesture** actions, but the broker streams only continuous
  o/p/z; **buttons are handled on-device in HID mode**. Wiring these needs a **new broker message type
  for button events** (and the firmware to forward button state in controller mode). The Blender add-on
  already leaves clean hooks but nothing fires them. (Catalog item "K".)
- **True cursor-position pivot / "zoom to mouse"** — **IN PROGRESS (one app at a time, best-first —
  the ranked remaining apps are at the end of this bullet).** The shared daemon wiring is DONE
  (daemon 0.1.39): new scheme values `pointer` (orbit_pivot) / `to_pointer` (zoom_mode), additive and
  distinct from `cursor`/`to_cursor` (which keep their old fallback meanings), in every ui.py
  dropdown and every broker frame — the only per-app work left is each plugin's pivot resolver.
  **FreeCAD — DONE (add-on 0.1.3, verified live):** a passive `SoLocation2Event` observer caches the
  live pointer pixel; `getObjectInfo(pixel)` raycasts it with the same bbox validation + per-gesture
  hold as `view`; `to_pointer` zoom holds the point under the pointer. Verified with synthetic
  QMouseEvents through the real Qt→Quarter→Coin pipeline + an end-to-end broker run (notes §8/§9);
  only the human hover-and-orbit feel pass remains. **AutoCAD — DONE (NETLOAD plugin 0.3.0, verified
  live in a throwaway instance):** a passive `Editor.PointMonitor` caches the cursor's WCS point
  (osnap > picked-entity depth along the view ray > UCS-plane point), extents-validated + held per
  gesture; `NavMath.Apply` grew optional orbit/zoom pivots (rigid orbit about P, parallel
  to_pointer zoom); `TBNAVPTRTEST` proved the full pipeline (P's screen offset exact, DB target
  err 0, both commit flavours) and a real cursor sweep fed the cache — human feel pass remains
  (AutoCAD notes §8.17). **Fusion — DONE (add-in 0.1.13, live-verified by the user over two
  passes; right/bottom-band fix awaiting a spot re-check):** the mouseMove-Command route was
  REJECTED as intrusive (a Fusion Command is modal — it owns clicks and any tool activation
  terminates it, so an always-on tracker fights normal modeling); instead the pointer is read
  ON-DEMAND at gesture start — ctypes `GetCursorPos` → ÷ the monitor's effective DPI scale →
  `Viewport.screenToView` (output validated against `vp.size × scale`, with a window-under-cursor
  client-rect mapping as fallback) → `viewToModelSpace` to AIM a ray (perspective: eye→point;
  ortho: parallel, pushed back) → the existing `findBRepUsingRay` pick with the same
  aperture/bbox/hold machinery as `view`; `to_pointer` zoom rides the same raycast. The two live
  passes at 125% scaling pinned **Fusion's mixed coordinate model** (fit `view = 1.25·logical_in −
  physical_origin`, exact on every logged sample): `screenToView` = LOGICAL screen px IN →
  PHYSICAL viewport px OUT; `viewToModelSpace` = PHYSICAL in; `vp.width/height` = LOGICAL. Pass 1
  (0.1.11) caught the input scale (hits down-right of the cursor → 0.1.12 divides the input);
  pass 2 (0.1.12, "works very precisely") caught the OUTPUT bounds (the logical-size range check
  wrongly rejected the right/bottom ~20% band → 0.1.13 validates against `vp.size × scale`).
  A `pointer map:` log line prints screen px → scale → view px for diagnosis. Pixel→ray→pivot
  math + hold + both DPI cases are unit-tested against a stubbed `adsk`
  (tests/test_fusion_pointer_pivot.py). Remaining apps: Onshape (cursor→canvas mapping),
  SolidWorks (IMouse), SketchUp (`GetCursorPos` via Fiddle), Blender (modal operator or Win32 —
  no on-demand mouse getter in a timer), Unreal (C++-only — skip). Blender's `cursor` stays the
  3D cursor.
- **"Viewport under the pointer" targeting** (Blender quad-view) — same live-mouse limitation; v1
  targets the active/largest VIEW_3D.

**Per-app polish (mostly hardware sign/feel passes)**
- **Sign/scale calibration on real hardware** for SolidWorks, Onshape, AutoCAD, Blender, and SketchUp — the
  `*_SIGN`/`*_SCALE`/`WORLD_UP` and Blender's per-mode invert **defaults are best-guesses**; verify and
  flip on the device. Onshape's turntable `WORLD_UP` (Y vs Z) specifically needs confirming. (AutoCAD's
  `WORLD_UP` is confirmed Z-up; its pan/zoom *scale* is the main open item — drawing units vary wildly.)
- **Onshape**: perspective path is lightly tested (Onshape defaults to **ortho**); zoom-to-object/
  cursor for ortho is approximate; Firefox needs its own cert-trust UX.
- **SolidWorks**: an **in-process add-in** would beat the out-of-process COM rate ceiling. This is
  now a **viable, proven path** — the AutoCAD plugin (§8/notes §8.14–8.15) established the pattern
  (bundle a pre-built .NET DLL + auto-load, zero user steps; the .NET 8 SDK is on the dev machine)
  and SW add-ins can register under **HKCU (no admin)**. Build it if the COM rate/latency ever
  becomes the limiting factor. `to_cursor` zoom falls back to center.
- **Blender**: **walk gravity/teleport** (no physics step — walk is horizon-locked look + horizontal
  move only); verify camera-lock feel live; consider migrating from the legacy `bl_info` add-on to the
  new **extensions** system (re-check install/auto-enable if so).
- **Fusion**: `findBRepUsingRay` on the root component may miss bodies inside **assembly occurrences**
  (degrades to object-centre); revisit if assembly orbit feels off.

**Daemon / UX**
- **Packaging** — cut the actual **Nuitka onedir** build (and/or the pip gui-script) for end users who
  have neither Python nor git. The `pyproject` version is dynamic (reads `__version__`), so a cut
  build inherits the daemon build number automatically.
- **Auto-start on login** — `apps.<key>.start_automatically` exists in config but the daemon's own
  run-at-login isn't wired.
- **Auto mode switching** — cursor↔3D is a manual global toggle; it could auto-switch to 3D when a CAD
  app is focused.
- **Custom UI look** — the settings UI is "standard Windows-10" Tkinter. A richer themed/animated UI
  was discussed as desirable; feasibility within Tkinter is limited (would likely mean a custom theme
  or a different toolkit). Intentionally deferred.

---

## 15. The other docs

- [`README.md`](README.md) — **user-facing**: how to run + click-by-click per-app setup.
- [`docs/apps/solidworks.md`](docs/apps/solidworks.md) — SolidWorks: the verified COM
  view-transform model + every pywin32/late-dispatch/`SelectByRay` gotcha. Read before touching the
  driver.
- [`docs/apps/onshape.md`](docs/apps/onshape.md) — Onshape: the reverse-engineered
  WAMP/navlib protocol, the affine convention, cert trust, and the solved-problem history.
- [`docs/apps/autocad.md`](docs/apps/autocad.md) — AutoCAD: the verified ActiveX view
  model (read from sysvars, write via the reassign commit ritual), the ROT attach, SAFEARRAY
  marshaling, the auto-level/no-roll + no-raycast limitations, and how to test live. **Read before
  touching the driver.**
- [`docs/apps/blender.md`](docs/apps/blender.md) — Blender: architecture, the full gotcha +
  solved-problem history, testing. **Read first** for Blender.
- [`docs/apps/blender_design.md`](docs/apps/blender_design.md) — Blender design rationale: the full mode
  catalog, the generic-scheme↔Blender reconciliation, and verified Blender-API facts.
- [`docs/apps/freecad.md`](docs/apps/freecad.md) — FreeCAD: the verified Coin camera
  model + the FreeCAD-specific gotchas (InitGui separate-globals/locals exec, `pivy.coin` SWIG load,
  `GuiUp`/`QTimer` deferral, versioned user Mod dir) and how to test live. **Read before touching the
  add-on.**
- [`docs/apps/sketchup.md`](docs/apps/sketchup.md) — SketchUp: the live-verified Ruby
  eye/target/up camera, single-main-thread `UI.start_timer` socket poll, `pickray`/`raytest` held
  pivot, annual Plugins install, interactive self-test, and startup/Length/logical-pixel gotchas.
  **Read before touching the extension.**
- [`docs/apps/unreal.md`](docs/apps/unreal.md) — Unreal Engine: the verified free-fly
  editor-camera model + conventions (left-handed/Z-up/cm/degrees), the Unreal-specific gotchas
  (`Rotator(roll,pitch,yaw)` positional order, `compose_rotators` misbehaving, `HitResult.to_dict()`,
  `make_rot_from_xz` rebuild), the project-centric/admin install reality + what auto-loads, and how to
  live-probe the editor Python API **headless**. **Read before touching the add-on.**
- [`docs/apps/fusion360.md`](docs/apps/fusion360.md) — Fusion 360: the CustomEvent socket add-in
  architecture, the no-external-automation reality (verify via `fusion_addin.log`), the
  `ObjectCollection`/`activeProduct` API traps, the modal-Command rejection, and the live-verified
  mixed logical/physical DPI coordinate model behind the pointer pivot. **Read before touching the
  add-in.**

> When you change something that "you couldn't have known by reading the code," add it to the relevant
> doc's gotcha/solved-problem section **and** (if it spans components) here. That discipline is why
> this project is handoff-able.
