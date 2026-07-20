# SpaceMouse / navlib **desktop** bridge — Milestone 0 spike report

> **Archived completed feasibility spike.** The desktop socket hypothesis was rejected; a clean-room
> `TDxNavLib.dll` remains an unapproved deferred proposal. Current disposition is in
> [`../../TODO.md`](../../TODO.md).

Status: **SPIKE COMPLETE — desktop socket hypothesis rejected.** This documents the reverse-engineering of
how native desktop CAD apps consume 3Dconnexion SpaceMouse input, the go/no-go per the Milestone 0
plan, and the one viable driver-free path. **No bridge code has been written** — per the task, the
build is gated behind this spike's decision. Companion: [`docs/apps/onshape.md`](../../docs/apps/onshape.md)
(the *web* navlib bridge, which this was supposed to generalize).

> TL;DR: The task's leading hypothesis — "desktop navlib apps connect to a loopback WebSocket on
> :8181 that we can occupy, like the web NL-Proxy" — is **FALSIFIED**. Desktop navlib is an
> **in-process C-ABI DLL** (`TDxNavLib.dll`), not a socket. With no 3Dconnexion driver installed,
> a navlib app **opens no endpoint of any kind** and silently no-ops its 3D-mouse feature. The only
> driver-free desktop path is to **ship our own clean-room `TDxNavLib.dll`** (the navlib *client*
> runtime), analogous to how the Onshape bridge is a clean-room reimplementation of the *server*.
> That path is viable but is a **native DLL** (needs a C toolchain, not currently installed) — a
> material scope change from "OnshapeBridge minus the TLS quirks." **Go/no-go is the user's call.**

---

## 1. Spike method (what was actually checked)

All on the dev box (Windows 11), with **no 3Dconnexion software present** (verified, §2). Static
analysis was decisive, so the heavier live-sniff step was deferred to post-decision (§7).

1. Confirmed the machine is clean: no `3DxService`/`3DxWinCore`/`3DxNLServer` process, no 3Dconnexion
   service, no `C:\Program Files\3Dconnexion`, no Uninstall entry, no `HKLM\SOFTWARE\3Dconnexion`,
   no `navlib.dll`/`TDxWare.dll`/`TDxNavLib.dll` anywhere. Port **8181 is free** (nothing listening).
2. Picked two local navlib **client** apps as probe targets, both installed: **FreeCAD 1.1**
   (`C:\Program Files\FreeCAD 1.1`) and **KiCad 9.0** (`C:\Program Files\KiCad`). Both are open-source
   so their navlib integration is readable.
3. Inspected what each app actually **ships and references** for navlib — binary string/symbol scan
   of `FreeCADGui.dll` and KiCad's `_pcbnew.dll` (+ siblings) — looking for the transport: a socket
   endpoint, a pipe name, a `ws://`/`8181`/`127.51.68.x`, or a DLL import. Cross-checked against the
   public 3Dconnexion SDK model.

---

## 2. The machine is clean (constraint satisfied, baseline established)

No 3Dconnexion anything: no process, no service, no install dir, no registry key, no SDK DLL, and
**port 8181 unbound**. So every observation below is the **driver-free** state the user mandates, and
any future bridge starts from a genuinely empty field (the Onshape bridge can bind 8181 freely; a
desktop bridge would not be fighting a real 3DxNLServer).

---

## 3. THE FINDING — desktop navlib is an in-process C-ABI DLL, not a socket

Both apps show the **identical** architecture, and it is **not** the web model.

### 3.1 The app compiles in the navlib **C++ accessor wrapper**…
`FreeCADGui.dll` and KiCad's `_pcbnew.dll` (also `_eeschema.dll`, `_gerbview.dll`, `_pl_editor.dll`,
`_cvpcb.dll`) contain the full 3Dconnexion navlib **C++ class set**, statically linked:
`TDx::SpaceMouse::Navigation3D::CNavigation3D`, `CNavlibImpl`, `CNavlibInterface`, and the accessor
interfaces `IAccessors`, `IHit`, `IModel`, `IView`, `IPivot`, `ISpace3D`, `IState`, `IEvents`,
`INavlib`. These are the SDK's **source-provided** wrapper (3Dconnexion ships it as source, you
compile it into your app). KiCad's PDB paths even name the per-app accessor impls:
`kicad/3d-viewer/3d_navlib/nl_3d_viewer_plugin_impl.cpp`, `kicad/pcbnew/navlib/nl_pcbnew_plugin_impl.cpp`.

### 3.2 …but reaches the device through the low-level navlib **C ABI** in a separate DLL
The wrapper bottoms out at the navlib **C API** — the symbols `NlCreate` and `NlClose` appear in both
binaries, and the lone string **`TDxNavLib`** (no `.dll`, **not** in the import table) is the name fed
to a runtime loader. KiCad's build artifact is the smoking gun: it compiles
**`thirdparty/3dxware_sdk/.../src/navlib_stub.c`** — the SDK's *dynamic-loading stub*, which at runtime
does `LoadLibrary("TDxNavLib")` + `GetProcAddress` for `NlCreate`/`NlClose`/`NlReadValue`/
`NlWriteValue`/`NlGetType`/… So the **transport** (how navlib reaches a device or service) lives
**entirely inside `TDxNavLib.dll`** — the 3Dconnexion-provided **client runtime**, installed by the
driver. The app neither embeds it nor ships it.

### 3.3 The app opens **nothing** itself
Neither binary contains a socket endpoint, a named-pipe path, a `ws://`/`wss://`, `8181`, `nlproxy`,
or `127.51.68.x`. Whatever IPC navlib uses to talk to the 3DxNLServer is **inside `TDxNavLib.dll`**,
which is absent. **Therefore, driver-free, the app:** calls into the stub → `LoadLibrary("TDxNavLib")`
fails (or `NlCreate` returns "device not attached") → the 3D-mouse feature silently disables → **no
socket, no pipe, nothing for us to occupy.** (FreeCAD even carries the user-facing strings
"3Dconnexion device not attached." / "3Dconnexion device detached.")

### 3.4 Why the web bridge worked and this doesn't transfer
Onshape (web) ships the 3Dconnexion **client** itself — a JavaScript library *in the page* — so the
only missing piece was the **server**, and we impersonate that on a WebSocket. A desktop app ships the
**C++ wrapper** but **not** the client runtime DLL; that DLL *is* the missing piece, and it's a C ABI,
not a socket. The web NL-Proxy and the desktop NLServer share the *accessor model* (and reportedly the
:8181 port) but the **client→server transport differs**, and more importantly the client lives in a
**different place** (page vs driver DLL). Impersonating the desktop **server** alone is useless without
also providing the **client** DLL — which is exactly the part the app lacks.

---

## 4. Go / No-Go matrix (Milestone 0 decision gate)

| Probe target | Driver-free behavior | Socket-impersonation path (the hypothesis) | Clean-room-DLL path |
|---|---|---|---|
| **FreeCAD 1.1** | navlib stub `LoadLibrary("TDxNavLib")` → fails → 3D-mouse off; **opens no endpoint** | **RED** — nothing connects to :8181 or any port/pipe | **AMBER→GREEN** — app will `LoadLibrary` our `TDxNavLib.dll` and call its `Nl*` exports |
| **KiCad 9.0** | same (per-view navlib plugin, same stub) | **RED** | **AMBER→GREEN** — same load hook |
| **Any navlib app** (Solid Edge, NX, Rhino, Inventor, …) | same SDK, same stub | **RED** | **AMBER→GREEN** — universal, *if* we provide the DLL |

- **Socket impersonation (the task's §1 leading hypothesis): RED.** Definitively falsified for native
  desktop apps. The Onshape "stand up a server they connect to" trick has nothing to connect to it.
- **Clean-room navlib client DLL: AMBER, pending decision.** Technically the right desktop analogue and
  the *universal* path, but it's a **native C-ABI `TDxNavLib.dll`**, not a Python socket server.

---

## 5. The one viable driver-free path — ship our own `TDxNavLib.dll`

Because the app resolves the navlib C ABI via **`LoadLibrary("TDxNavLib")`** (a runtime dynamic load,
not a static import), **a DLL of that name on the app's DLL search path is loaded and called.** So we
provide our **own** clean-room `TDxNavLib.dll` that exports the public navlib C ABI (`NlCreate`,
`NlClose`, `NlReadValue`, `NlWriteValue`, `NlGetType`, …). This is a clean-room reimplementation of the
**client runtime** — the exact mirror of the Onshape bridge reimplementing the **server**. We ship our
own binary; **no 3Dconnexion software, no driver.** Constraint satisfied (same precedent as Onshape).

**How navigation flows (high reuse of the existing nav model):**
- On `NlCreate`, the app hands us its **accessor table** — getters/setters for `view.affine` (camera
  matrix), `view.extents`/frustum, `model.extents`, `pivot.*`, `hit.*`, `motion`, `transaction`, etc.
  These are the **same properties** the web bridge reads/writes over WAMP (the web NL-Proxy is just a
  *remoting* of this very C ABI). Confirmed accessor names from the public SDK: `GetCameraMatrix`/
  `SetCameraMatrix`, `GetViewExtents`, `GetViewFrustum`, `SetMotionFlag`, `SetTransaction`, hit-test.
- The trackball daemon runs the **same `_navigate` orbit/pan/zoom-about-a-pivot** it already runs for
  Onshape (decode affine → apply → encode), but the read/write go through the app's **C function
  pointers** instead of WAMP frames. The cleanest split keeps the DLL **thin** (a C shim that proxies
  accessor calls to/from the Python daemon over the existing `127.0.0.1` broker socket) and leaves the
  camera math in Python — i.e. the desktop transport is the *inverse* of the WAMP transport, same model.

**Cost / prerequisites (why this is a real decision, not a free reuse):**
- **Native build toolchain required.** No compiler is installed (no MSVC/MinGW/clang; only Windows
  Kits headers). Lightest fix: portable **MinGW-w64** (unzip, no admin) or MSVC Build Tools.
- **Per-app DLL placement.** Our `TDxNavLib.dll` must sit where each app's `LoadLibrary("TDxNavLib")`
  finds it (the app's `bin\`, or a dir ahead of it on the search path). That's a **per-app file drop**,
  like installing the Fusion/Blender add-on — *not* the zero-install story the web bridge has.
- **Exact ABI match.** The C exports + the `accessor_t`/`nlCreateOptions_t` structs must match the
  public `navlib.h` precisely. The API is public and stable, but it's C/C++, not Python.

**Where its value actually is.** All five existing integrations (Fusion socket add-on, SolidWorks COM,
Onshape web bridge, Blender + FreeCAD socket add-ons) **already work driver-free**. So this DLL is
**redundant for them** (FreeCAD included — it already has a socket add-on). Its unique payoff is
**closed-source native apps that *only* support SpaceMouse via navlib and have no scripting/add-on
hook** (Solid Edge, NX, Inventor, Rhino-without-Python, etc.) — apps we *cannot* reach any other way.

---

## 6. Paths ruled out (don't revisit without new evidence)

- **Occupy :8181 / impersonate the desktop NLServer alone** — useless: the app can't reach the server
  without `TDxNavLib.dll`, which is absent. You'd have to provide the client DLL *anyway* (§5), after
  which the DLL can talk to the daemon directly — no server impersonation needed.
- **Legacy SiApp / window-message API** ("Enable support of legacy SpaceMouse devices" string is in
  FreeCAD) — the old 3DxWare-9 path relies on the **driver's** hidden "SpaceWare Driver" window +
  `RegisterWindowMessage` broadcasts. Also **driver-dependent**, so not a driver-free escape hatch.
- **USB-HID SpaceMouse emulation** (AndunHH-style) — explicitly out of scope: a fake HID device still
  needs the 3Dconnexion **driver** to translate HID→navlib. We must impersonate the *runtime*, not the
  device.
- **spacenav magellan/X11 protocol** — the *nix analogue; not relevant on Windows.

---

## 7. Remaining empirical confirmation (post-decision, if we build the DLL)

Static analysis is decisive, but the definitive live proof of the §5 path — and the natural first
build step — is the **stub-DLL test**: build a minimal `TDxNavLib.dll` that exports the navlib C ABI
and **logs every call**, drop it in FreeCAD's `bin\`, launch FreeCAD with a 3D doc open, and confirm
FreeCAD calls `NlCreate` and registers its accessors against our DLL. Success = GREEN-confirmed and we
have the real implementation's skeleton. (A pure observation pass — launch FreeCAD, watch for any
loopback/pipe activity, read its report log for "device not attached" — would only re-confirm RED-for-
socket, which §3 already establishes; it needs a compiler-free stub or GUI driving, so it's lower
value than the stub-DLL test.) This is "deep work" and is gated behind the user's go/no-go.

---

## 8. Gotchas captured during the spike

- **The web hypothesis does not transfer** (§3.4). Desktop = compiled-in C++ wrapper + `LoadLibrary`
  of a driver-provided C-ABI DLL; web = page-shipped JS client + a server we impersonate. Different
  *client location*, different *transport*. Same accessor model underneath.
- **Reading binaries beats guessing the transport.** The string scan (`navlib_stub.c`, lone
  `TDxNavLib`, `NlCreate`/`NlClose`, **no** socket strings) settled the architecture without launching
  anything. Re-run it on any new target app before assuming it's reachable.
- **"navlib-enabled app" ≠ "reachable driver-free."** An app can fully support SpaceMouse yet open
  nothing without the driver, because the client runtime is the missing DLL — the central Milestone-0
  risk, now confirmed real.
- **The accessor names line up with the web bridge** (`view.affine`/`view.extents`/`motion`/
  `transaction`/`pivot.*`/`hit.*`), so the **nav model is reusable** whichever transport wins — only
  layers [1]/[2] (transport/session) change between web-WAMP and desktop-C-ABI.
