# Security, permissions, and first-run warnings

This project controls local desktop applications and therefore does several things that security
products reasonably inspect: Bluetooth input, loopback listeners, COM automation, host-loaded
scripts, one compiled AutoCAD add-in, optional global Raw Input keyboard observation, and an
optional self-signed loopback certificate. None of the runtime paths require unrestricted inbound
network access, silently install a certificate, suppress keyboard input, disable antivirus, or
launch a supported 3D application.

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
  `%APPDATA%\TrackballDaemon\acad_plugin` to AutoCAD `TRUSTEDPATHS`, then issues `NETLOAD`. It does
  not lower `SECURELOAD`, trust a parent directory, write `Program Files`, or launch AutoCAD.
- SketchUp installations with a restrictive extension-loading policy may warn about or block the
  unsigned Ruby extension. Prefer signing/distributing through SketchUp's supported extension
  mechanism before release; do not instruct customers to disable antivirus.
- Onshape requires a normal browser/certificate warning because its built-in 3Dconnexion client
  insists on TLS to a fixed loopback IP. Setup creates a non-CA leaf certificate in the current
  user's APPDATA but never trusts it automatically. The user can explicitly trust it, accept it for
  a browser session, or leave Onshape disabled.

## Runtime and installation inventory

| Component | Action | Scope and reversal |
|---|---|---|
| BLE input | Connects to the configured Trackball BLE device. | No driver or service installed. Remove/forget the device normally. |
| Keyboard bindings | Lazily registers the standard Windows keyboard device class through Raw Input only while an enabled compiled binding references keyboard controls. | No driver, hook, service, key suppression, or startup registration. Disable all keyboard bindings or stop the daemon to unregister it and synthesize releases. |
| Start at login | When explicitly toggled in the tray, writes `TrackballDaemon` under the current user's Windows `Run` key. | No service or scheduled task. Toggle it off or delete the HKCU value. |
| Navigation broker | TCP JSON listener on `127.0.0.1:<configured port>`. | Loopback only; stops with the daemon. No firewall rule created. |
| Blender | Copies Python add-on and, only after a separate confirmation, an auto-enable startup shim. | Current-user Blender scripts. Delete `trackball_nav` and `trackball_nav_startup.py`. |
| FreeCAD | Copies a Python Mod add-on. | Current-user FreeCAD Mod folder; delete `TrackballNav`. |
| SketchUp | Copies a Ruby extension. | Current-user Plugins folder; delete loader and `trackball_nav`. |
| Fusion 360 | Copies a Python add-in; Fusion requires the user to Run/enable startup. | Current-user Autodesk AddIns folder; remove it in Fusion or delete `TrackballNav`. |
| Unity | Copies C# editor source into detected projects. | Project `Packages` only; delete `com.astrolabe.trackball-nav`. |
| Godot | Copies GDScript and adds the plugin path to `project.godot`. | Project only; disable the plugin and remove `addons/trackball_nav`. |
| Unreal | Copies a Python editor plugin per engine or per project. | Engine path may require elevation; project path does not. Disable/delete `TrackballNav`. |
| Rhino | Copies Python and may append a current-user startup command. | Remove the script folder and the TrackballNav startup command in Rhino Options. |
| SOLIDWORKS | Attaches to an already-running instance through COM automation. | No host files, registration, registry writes, listener, or launch. Disable the integration. |
| AutoCAD | Attaches to an already-running instance through COM, scopes `TRUSTEDPATHS`, and NETLOADs a per-user DLL. | Remove the exact trusted path, delete the APPDATA plugin after closing AutoCAD, and disable the integration. |
| Onshape | Creates a per-user TLS key/certificate, listens on fixed loopback TLS, and optionally supplies an Onshape-only pointer userscript. | Delete the PEM files; remove the `127.51.68.120` certificate from the current-user trust store; remove the userscript. |
| Host detection | Reads common install folders, process command lines, recent-project files, and Rhino's install registry key. | Detection is read-only. Hidden PowerShell/WMIC calls do not modify the system. |

Sensitive in-process services are gated by both successful setup and the per-app **Enabled**
checkbox. When Onshape, AutoCAD, or SOLIDWORKS has not been set up, the daemon does not bind the
Onshape port, generate its certificate, enumerate COM applications, alter AutoCAD trust, or load a
DLL. Disabling one of these integrations stops future attachment/listening; a DLL already loaded in
AutoCAD remains loaded until AutoCAD exits.

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

The receiver never owns foreground focus. Foreground app identity is published by a separate
read-only monitor, so stationary keyboard controls can resolve app context without trackball motion
and without using the app selected in Settings as a runtime fallback.

## Release hardening before alpha distribution

1. Build a per-user **Nuitka onedir** package. Avoid self-extracting one-file packers, which are more
   likely to resemble malware droppers and repeatedly unpack executable content into temporary
   directories.
2. Authenticode-sign the daemon executable, installer, updater (if added), and AutoCAD DLL with one
   consistent publisher certificate; timestamp every signature. Signing identifies the publisher
   but does not instantly create SmartScreen reputation, so keep filenames, publisher identity, and
   download origin stable across releases.
3. Prefer a per-user installer with an `asInvoker` manifest. Offer Unreal engine-wide installation
   as a clearly labeled optional elevated action; make project-local installation the default.
4. Publish SHA-256 checksums, the source revision, dependency lock/SBOM, supported host versions,
   and VirusTotal/Defender results for the exact release artifacts. Submit false positives to the
   affected vendor rather than recommending exclusions.
5. Keep all listeners loopback-only and add automated release checks for bind addresses, origin
   filtering, absence of automatic certificate trust, and absence of `SECURELOAD=0` or antivirus
   exclusions.
6. Do not auto-install the Onshape certificate. If a future signed installer offers certificate
   setup, keep it an explicit, reversible checkbox and show the certificate fingerprint first.
7. Verify Raw Input lazy registration, pass-through/no-focus behavior, lock/suspend releases, and
   the elevated-app access boundary against the exact packaged build. Do not add key suppression or
   raw-keystroke logging as a workaround for an access limitation.

Packaged GUI behavior, SmartScreen reputation, AutoCAD/SketchUp trust prompts, and third-party AV
classification require testing against the exact signed release artifact; source inspection cannot
prove those external outcomes.
