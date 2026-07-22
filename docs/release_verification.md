# Release verification

This is the durable release gate for the first Astrolabe alpha. Automated, mocked, source-review,
test-bench, and live results remain distinct. A skipped check must include its reason and release
impact; it is never converted into a pass by proximity to another test.

The shared lifecycle and ownership rules are in [`architecture.md`](architecture.md). Keep this file
command- and gate-oriented. Store completed run results under [`../archive/release-evidence/`](../archive/release-evidence/)
rather than copying test counts, artifact hashes, or dated environment claims into this checklist.

## Build and automated checks

Install the development and release extras, then run:

```powershell
python -m pip install -e ".[dev,release,onshape]"
python -m pytest -q
python -m compileall -q trackball_daemon tests tools
python -m trackball_daemon.validate_bindings
python tools/verify_autocad_artifact.py
python -m trackball_daemon --release-smoke
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --configuration Release
& ".\tools\build_release.ps1"
git diff --check
```

`build_release.ps1` refuses output paths outside the repository, verifies the bundled AutoCAD DLL
against its source-tree and hash manifest, builds the sdist and wheel in an isolated PEP 517
environment, keeps Nuitka downloads/cache under `build/release`, explicitly includes the AutoCAD DLL,
creates a Windows standalone onedir executable, and runs its side-effect-free packaged-resource smoke.
The smoke validates the AutoCAD DLL and loads defaults, profiles, descriptors, schemas, and examples,
then compiles both System profiles without acquiring the controller mutex, opening BLE or Raw Input,
starting the tray, loading host integrations, or writing user config. The script prints SHA-256 values
for both the executable and packaged AutoCAD DLL.

Before release, also install the wheel into a clean Windows account without Python or a source
checkout, exercise the onedir GUI there, uninstall/reverse every path in `security.md`, and retain
the exact artifact checksum, source revision, dependency lock/SBOM, signature/timestamp, supported
host matrix, and Defender/third-party antivirus results. A local unsigned development build cannot
close SmartScreen reputation or publisher-identity gates.

## Keyboard and lifecycle matrix

Use the packaged onedir build and record Windows version, keyboard layout, test application,
expected state, actual state, and daemon log result for each row:

- left/right modifiers and at least one non-US layout; AltGr must not strand Ctrl/Alt;
- Windows Sticky Keys enabled and disabled;
- held key repeat; one physical press must create one activation edge;
- Ctrl then Shift, Shift then Ctrl, and rapid release/repress for direct and cascading Pan chords;
- profile switch and binding save while a control is held;
- session lock/unlock, sleep/resume, and daemon quit while held;
- Remote Desktop connect/disconnect while held;
- transition into and out of an elevated foreground application.

Expected fail-safe behavior is release-all when the input desktop or integrity boundary cannot be
observed safely. The provider may synthesize a release; it must not synthesize a press, retain a
stale hold, steal focus, suppress ordinary key delivery, or log raw keyboard packets. Name the
backend Windows Raw Input, not a keyboard hook.

## Firmware and BLE matrix

Record firmware revision, descriptor ID, physical control, debounce value, and packet observation:

- initial snapshot, quick press/release, long hold, reconnect, and disconnect while held;
- one missed intermediate sequence repaired by the next full snapshot;
- duplicate, stale/out-of-order, malformed length/version/kind, and undeclared-bit rejection;
- unsigned sequence wrap from `65535` to `0`;
- every declared simultaneous-control combination or a justified pairwise contingency sample;
- legacy rotation-only operation with keyboard bindings;
- daemon-absent standard HID pointer and button operation.

The XIAO three-button/jumper bench proves the host protocol boundary, and the completed SuperMini
PMW3610 loop proves the current five-way prototype behavior. Neither is the product assembly. The
final Seeed Studio XIAO nRF52840 build's sensor path, Up/Down/Left/Right/Center pin map, debounce, and
physical five-way matrix remain release blockers until exercised on that hardware.

## Host control matrix

For each available host, record host version, integration version/transport, projection mode, input
profile, and results for:

- Pointer/3D toggle, Ctrl hold 3D, Shift cascading Pan, and explicit Ctrl+Shift in both orders;
- Orbit/Fly/Walk where advertised, horizon entry, secondary controls, and sensitivity holds;
- foreground switch while every hold is active and unknown-foreground global bindings;
- two simultaneously connected hosts with no background motion;
- setup/update/reload and current-versus-stale connected add-on reporting;
- host-specific camera/pivot checks listed in `TODO.md` and the relevant `docs/apps` guide.

Cover Blender, SketchUp, Unreal, Unity, Godot, and Onshape when available, plus every other supported
installed host. Onshape additionally needs connected-but-unfocused and focused-browser behavior.
Unavailable commercial hosts are recorded as unavailable with release impact, not passed.

## HUD and settings matrix

Using the packaged Tk runtime, verify:

- primary and secondary monitors, mixed DPI, taskbar/work-area changes, and foreground monitor moves;
- no focus/activation steal, click-through, topmost, opacity/margin, show/hide, and quit behavior;
- held binding replaces last-used text, release restores timeout behavior, and stationary keyboard/BLE
  changes update without ball motion;
- every Global category is generated, displays resolved System values, and supports per-setting and
  reset-all behavior;
- linked app rows visibly follow Global changes; edit unlinks; link icon relinks/unlinks; per-setting
  reset pins System; page link-all/unlink-all/reset-all follow their documented semantics;
- clicking blank space commits an editor and a live refresh preserves the selected category.
