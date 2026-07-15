# Release verification

This is the durable release gate for the first Astrolabe alpha. Automated, mocked, source-review,
test-bench, and live results remain distinct. A skipped check must include its reason and release
impact; it is never converted into a pass by proximity to another test.

Phase 11 closes the rich-keybindings feature branch at branch/merge quality; it does not publish the
first production release. Final five-way hardware, clean-account installation, signing, reputation,
and production-host qualification remain explicit release work after merge. They do not block the
feature branch when the implemented boundary has automated coverage, available live tests pass, and
the deferral plus release impact is recorded here and in `TODO.md`.

## Build and automated checks

Install the development and release extras, then run:

```powershell
python -m pip install -e ".[dev,release]"
python -m pytest -q
python -m compileall -q trackball_daemon tests tools
python -m trackball_daemon.validate_bindings
python -m trackball_daemon --release-smoke
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --configuration Release --no-restore
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_release.ps1
git diff --check
```

`build_release.ps1` refuses output paths outside the repository, builds the sdist and wheel in an
isolated PEP 517 environment, keeps Nuitka downloads/cache under `build/release`, creates a Windows
standalone onedir executable, runs its side-effect-free packaged-resource smoke, and prints the
executable's SHA-256. The smoke loads defaults, profiles, descriptors, schemas, and examples and
compiles both System profiles without acquiring the controller mutex, opening BLE or Raw Input,
starting the tray, loading host integrations, or writing user config.

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

The XIAO three-button/jumper bench proves the host protocol boundary but not production hardware.
The final Up/Down/Left/Right/Center pin map, production debounce, and physical five-way matrix remain
a release blocker until tested on the final active-low/internal-pull-up switch.

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

## Current Phase 11 evidence

- Starting revision: `97ccd54` on `rich-keybindings`; user-owned `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` were left untouched.
- Untouched source baseline: `628 passed, 2 skipped`; the two skips are Tk window smokes unavailable
  in the shell Python's Tcl/Tk runtime. Python compilation, both System profile validations,
  AutoCAD NavMath (`ALL PASS`), and `git diff --check` passed.
- Contributor-contract checks validate all schemas and examples with JSON Schema Draft 2020-12 and
  again through the authoritative daemon profile, action, and descriptor parsers.
- The sdist and wheel for daemon `0.1.73` built successfully in an isolated environment and contain
  defaults, host add-ons, descriptors, schemas, and examples.
- The first Nuitka attempt correctly stopped before standalone output because non-interactive mode
  would not download Dependency Walker. An interrupted long compile then left an executable without
  its required onedir DLLs; its `_tkinter.pyd` smoke failure proved that a file-presence check alone
  was insufficient. The final clean current-tree build contains 1,061 files (119,202,050 bytes),
  passed the packaged-resource smoke, and produced executable SHA-256
  `AD71024461A1B12362A6AE5D737ADC7AA1218A425BDF5E86B27A29BFB7969BAD`. The wheel was force-installed
  into a separate environment and its smoke passed from `%TEMP%`, outside the source checkout. This
  is an unsigned local verification artifact, not a published release. The builder cleans artifact
  directories, preserves only its repository-local tool cache, and always runs the smoke before
  hashing.
- Final hardened source verification passes `637 passed, 2 skipped`; compilation, both System
  profile validations, and AutoCAD NavMath also pass. The focused migration/corruption, BLE trust,
  Raw Input lifecycle, release-asset, and security group passes 88 tests. The elevated synthetic F24
  Raw Input probe reports one press and one release, no repeat activation, no retained pressed state,
  and unchanged foreground; it is not a substitute for physical-key acceptance.
- The metadata audit found and fixed Python 3.9 import incompatibility in PEP 604 annotations by
  postponing annotation evaluation. A static guard now keeps the declared Python floor and CI lane
  aligned.
- Prior Phase 10 live evidence covers HUD/focus behavior and Blender, Fusion 360, SOLIDWORKS, and
  Onshape control activation after their setup/enable gates. Phase 11 must still distinguish reused
  evidence from new packaged-build coverage.
- Final production five-way hardware is unavailable. Its physical matrix is `BLOCKED_FOR_RELEASE`,
  while development verification continues against automated protocol vectors and the XIAO bench.
- Packaged-build manual batch passed: tray, Settings, HUD, BLE connection, Ctrl 3D, cascading Shift
  Pan, Ctrl+Shift in both orders, keyboard-only F12 toggle, immediate binding recompilation, Global
  blank-space commit/category preservation, per-app link/reset flows, available monitor/DPI HUD
  behavior, Blender/Fusion 360/SOLIDWORKS/Onshape navigation, and quit/restart while a key was held.
  No stale held state remained. This is branch-level live evidence for the unsigned local onedir,
  not clean-account, signed-artifact, or final-hardware qualification.
- Packaged lifecycle batch passed with Sticky Keys enabled/disabled, session lock/unlock while held,
  and sleep/resume while held. Each boundary released the control, retained no stale HUD/runtime
  state, and accepted new bindings afterward. Earlier physical/elevated acceptance covers ordinary
  pass-through, integrity-boundary synthetic release, rapid chord order, profile reload, repeat, and
  daemon shutdown.
- AltGr/non-US-layout and Remote Desktop acceptance were unavailable in this environment. Branch
  impact: none; the normalized/right-Alt and release-all paths retain automated coverage. Production
  release impact: both remain named Windows-environment qualification items and cannot be advertised
  as live-passed until exercised against the eventual release artifact.
- Packaged live host evidence is Blender, Fusion 360, SOLIDWORKS, and Onshape. SketchUp, Unreal,
  Unity, Godot, FreeCAD, AutoCAD, and Rhino were not part of the available Phase 11 live environment;
  their automated integration/camera/transport suites pass, but their production host/version
  matrices remain open in `TODO.md`.
