# Security, permissions, and first-run warnings

This project controls local desktop applications and therefore does several things that security
products reasonably inspect: Bluetooth input, loopback listeners, COM automation, host-loaded
scripts, one compiled AutoCAD add-in, optional global Raw Input keyboard observation, and an
optional self-signed loopback certificate. None of the runtime paths require unrestricted inbound
network access, silently install a certificate, suppress keyboard input, disable antivirus, or
launch a supported 3D application. Shared ownership and lifecycle rules are defined in
[`architecture.md`](architecture.md).

## Expected Windows and antivirus UX

- A downloaded, unsigned daemon executable can trigger Microsoft Defender SmartScreen's
  **Windows protected your PC** reputation warning. This is about publisher/reputation, not a
  specific behavior detected in the source. Third-party antivirus may additionally inspect or
  quarantine an unsigned build because it combines BLE access, localhost sockets, COM, embedded
  scripts, and an embedded DLL.
- The daemon itself uses an `asInvoker`/per-user model and does not self-elevate. A UAC
  **Allow this app to make changes** prompt is expected only if the user deliberately runs the
  program elevated or chooses Unreal's engine-wide `Program Files` installation. Unreal's
  project-local plugin path avoids elevation.
- Windows Firewall should not need an inbound exception: the JSON broker binds only to
  `127.0.0.1`, and the Onshape TLS service binds only to `127.51.68.120`, also within loopback.
  Some endpoint-security products still notify whenever a process listens, even on loopback. Do not
  add a public/private-network firewall rule; the software does not need one.
- AutoCAD may object to an unsigned managed DLL. After explicit setup, the loader adds only
  `%APPDATA%\Mildly Useful\Astrolabe\acad_plugin` to AutoCAD `TRUSTEDPATHS`, then issues `NETLOAD`. It does
  not lower `SECURELOAD`, trust a parent directory, write `Program Files`, or launch AutoCAD.
- SketchUp installations with a restrictive extension-loading policy may warn about or block the
  unsigned Ruby extension. Prefer signing/distributing through SketchUp's supported extension
  mechanism before release; do not instruct customers to disable antivirus.
- Onshape requires a normal browser/certificate warning because its built-in 3Dconnexion client
  insists on TLS to a fixed loopback IP. Setup creates a non-CA leaf certificate in the current
  user's APPDATA but never trusts it automatically. The user can explicitly trust it, accept it for
  a browser session, or leave Onshape disabled.
- The first-run setup guide is observational by default. Its only direct choices are selecting the
  existing keyboard-only input profile and, on Finish, applying the existing current-user
  **Start at login** toggle. Host setup remains in the 3D Apps panel with the same consent and
  reversal text described below.

## Runtime and installation inventory

| Component | Action | Scope and reversal |
|---|---|---|
| BLE input | Connects to the configured Trackball BLE device, and records the address it connected to in per-user configuration so it can still reach a device that Windows has paired and that has therefore stopped advertising. It never overwrites an address set by hand. | No driver or service installed. Clear the address in Settings, or remove/forget the device normally. |
| USB vendor-HID input | Opens the device's second HID interface when VID, PID, usage page, usage, and product string all match, then sends an acknowledged attach to take ownership. | No driver or service installed. Read/write is confined to that one matched interface; unplugging it or stopping the daemon releases ownership. |
| Keyboard bindings | Lazily registers the standard Windows keyboard device class through Raw Input only while an enabled compiled binding references keyboard controls. | No driver, hook, service, key suppression, or startup registration. Disable all keyboard bindings or stop the daemon to unregister it and synthesize releases. |
| Start at login | When explicitly toggled in the tray, writes `Astrolabe` under the current user's Windows `Run` key. An earlier build's `TrackballDaemon` value is carried onto that name at startup and then removed, so only one registration ever fires at login. | No service or scheduled task. Toggle it off or delete the HKCU value. |
| Configuration directory | Per-user state under `%APPDATA%\Mildly Useful\Astrolabe`. An earlier build's `%APPDATA%\TrackballDaemon` is copied there once, verified, and then left in place unmodified as the rollback copy. | Delete either directory. Deleting the new one reverts to the preserved copy. |
| Navigation broker | TCP JSON listener on `127.0.0.1:<configured port>`. | Loopback only; stops with the daemon. No firewall rule created. |
| Blender | Copies Python add-on and, only after a separate confirmation, an auto-enable startup shim. | Current-user Blender scripts. Delete `trackball_nav` and `trackball_nav_startup.py`. |
| FreeCAD | Copies a Python Mod add-on. | Current-user FreeCAD Mod folder; delete `TrackballNav`. |
| SketchUp | Copies a Ruby extension. | Current-user Plugins folder; delete loader and `trackball_nav`. |
| Fusion 360 | Copies a Python add-in; Fusion requires the user to Run/enable startup. | Current-user Autodesk AddIns folder; remove it in Fusion or delete `TrackballNav`. |
| Unity | Copies C# editor source into detected projects. | Project `Packages` only; delete `com.astrolabe.trackball-nav`. |
| Unreal | Copies a Python editor plugin per engine or per project. | Engine path may require elevation; project path does not. Disable/delete `TrackballNav`. |
| Rhino | Copies Python and may append a current-user startup command. | Remove the script folder and the TrackballNav startup command in Rhino Options. |
| SOLIDWORKS | Attaches to an already-running instance through COM automation. | No host files, registration, registry writes, listener, or launch. Disable the integration. |
| AutoCAD | Uses COM only to attach, scope `TRUSTEDPATHS`, and NETLOAD a per-user DLL; the host-loaded plugin is the sole camera transport. | Remove the exact trusted path, delete the APPDATA `acad_plugin` folder after closing AutoCAD, and disable the integration. |
| Onshape | Creates a per-user TLS key/certificate, listens on fixed loopback TLS, and optionally supplies an Onshape-only pointer userscript. | Delete the PEM files; remove the `127.51.68.120` certificate from the current-user trust store; remove the userscript. |
| Host detection | Reads common install folders, process command lines, recent-project files, and Rhino's install registry key. | Detection is read-only. Hidden PowerShell/WMIC calls do not modify the system. |

Every copied payload carries a verbatim `LICENSE` and `NOTICE`, so an add-on sitting in a host's
folders states what it is and under what terms without reference to this project. They are part of
the payload, so they are staged, validated, and removed with it; deleting the payload directory
listed above still reverses the installation completely.

Copied host payloads are staged and byte-validated next to their destination before replacement.
Existing copies move to fixed-name sibling backups only for the swap, grouped payloads roll back
together on failure, and the next setup run recovers an interrupted backup before retrying. A
successful exact directory replacement removes files no longer present in the bundled add-in.

Sensitive daemon-side listeners, attachment services, and loaders are gated by both successful setup
and the per-app **Enabled** checkbox. When Onshape, AutoCAD, or SOLIDWORKS has not been set up, the
daemon does not bind the Onshape port, generate its certificate, enumerate COM applications, alter
AutoCAD trust, or load a DLL. Disabling one of these integrations stops future attachment/listening;
a DLL already loaded in AutoCAD remains loaded until AutoCAD exits.

The Onshape HTTP/WebSocket service also rejects browser origins outside HTTPS `onshape.com` (plus
its own local status page), limits request bodies, and never returns permissive wildcard CORS.

## Global keyboard input

Rich keyboard bindings use one daemon-owned, message-only Raw Input window registered for keyboard
usage page `0x01`, usage `0x06`, with `RIDEV_INPUTSINK`. Registration is lazy: an empty or
device-only compiled profile does not start the receiver. Profile disable/reload, receiver failure,
session lock, suspend, shutdown, and unregister synthesize releases before state is discarded.
Resume, receiver restart, and profile changes reconcile only configured controls with the most
significant state bit from `GetAsyncKeyState`.

Raw Input registration is device-class-wide, so Windows delivers keyboard packets for all keys
while the receiver is enabled. The callback copies compact native fields into a bounded in-memory
queue and returns. Outside the callback, the provider immediately ignores controls not referenced
by the active compiled profile; only normalized configured-control transitions reach the pressed
set. The daemon does not log raw keyboard packets or unrelated keys. Provider health and configured
binding activations may be logged without the raw stream.

Keys remain pass-through. The daemon does not request `RIDEV_NOLEGACY`, return suppression results,
install a keyboard hook, or inject keyboard input through its command surface. Raw Input does not
provide a universal trustworthy injected-event flag. The provider can reject explicitly known
internal `ExtraInformation` markers, and keyboard injection is excluded from the initial command
surface to avoid recursion; it does not pretend that arbitrary third-party injection is physically
provenance-tagged.

`GetAsyncKeyState` can return zero when the input desktop is inactive or access is unavailable, not
only when a key is up. Reconciliation first checks access to the current input desktop. Lock,
inactive-desktop, and otherwise ambiguous access paths release every keyboard-owned control and
mark the provider suspended instead of trusting a potentially false all-up observation. The daemon
is per-user and non-elevated, so observation while a higher-integrity application is foreground is
an explicit acceptance case; inability to observe or reconcile safely degrades to release-all.
Every foreground transition also triggers reconciliation. When Windows no longer reports a held
control across an integrity boundary, the provider closes it with a synthetic release instead of
preserving stale state while the eventual physical release is hidden.
While the provider believes a configured control is held, it also rechecks only that held control
at a low rate. This is a release-loss fail-safe—not a keyboard scanner—and it neither inspects
unconfigured keys nor creates repeated activation edges.

The receiver never owns foreground focus. Foreground app identity is published by a separate
read-only monitor, so stationary keyboard controls can resolve app context without trackball motion
and without using the app selected in Settings as a runtime fallback.

Raw Input is the approved backend. Do not add a low-level keyboard hook merely as a convenience
fallback. Reopening that decision requires a recorded physical Raw Input product failure. Any hook
proposal must include an enqueue-only callback, heartbeat/watchdog, reinstall plus
`GetAsyncKeyState` reconciliation, and fail-safe release before it can be considered.

Device input-state packets are untrusted over both transports. The daemon validates protocol version,
message kind, exact length, descriptor bit range, and unsigned wrap-aware sequence ordering before
changing normalized pressed state. Invalid, duplicate, and stale packets cannot activate or release controls. A new
connection resets only the sequence baseline; disconnect releases the complete device-owned pressed
set. Community control descriptors are validated JSON data and cannot name Python modules,
callbacks, commands, config paths, or executable code. Automatic third-party adapter-code loading
is not supported.

USB attach is identity-matched, not authenticated. Any local process able to open the same HID
interface can issue the same ownership commands, and the current VID/PID are upstream ZMK
development defaults shared by other devices, so the product string carries the match. That is
acceptable for a local input peripheral whose worst case is pointer motion, and it is the reason a
production VID/PID allocation is a release gate in `TODO.md`. It is not a confidentiality boundary.

## Release security gates

Release blockers and unfinished hardening are tracked only in [`TODO.md`](../TODO.md). The evergreen
artifact, signing, listener, trust, reversibility, and packaged-behavior checks are in
[`release_verification.md`](release_verification.md).

Keep these requirements invariant across releases:

- use a per-user, `asInvoker`, Nuitka onedir package rather than a self-extracting one-file wrapper;
- sign executable artifacts with one timestamped publisher identity and publish the source revision,
  whole-artifact SHA-256, and dependency/SBOM information;
- keep every listener loopback-only and never disable host security, certificate validation,
  SmartScreen, antivirus, or AutoCAD secure loading as a workaround;
- keep certificate trust and elevated host installation explicit, narrowly scoped, and reversible;
  and
- qualify Raw Input pass-through, lifecycle release, and integrity-boundary behavior against the
  exact packaged artifact.

Source inspection cannot prove SmartScreen reputation, host trust prompts, GUI behavior, or
third-party antivirus classification. Store completed release-run evidence as dated archive material,
not as current status in this guide.
