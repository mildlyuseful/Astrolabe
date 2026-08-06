# Release verification

This is the durable release gate for the first Astrolabe alpha. Automated, mocked, source-review,
test-bench, and live results remain distinct. A skipped check must include its reason and release
impact; it is never converted into a pass by proximity to another test.

The shared lifecycle and ownership rules are in [`architecture.md`](architecture.md). Keep this file
command- and gate-oriented. Store completed run results under [`../archive/release-evidence/`](../archive/release-evidence/)
rather than copying test counts, artifact hashes, or dated environment claims into this checklist.

## Build and automated checks

Install the pinned `uv` version required by `pyproject.toml`, synchronize the checked-in lock, then
run:

```powershell
uv sync --locked --all-extras
uv run --locked --all-extras python -m pytest -q
uv run --locked --all-extras python -m compileall -q trackball_daemon tests tools
uv run --locked --all-extras python -m trackball_daemon.validate_bindings
uv run --locked --all-extras python tools/verify_autocad_artifact.py
uv run --locked --all-extras python tools/apply_license_headers.py --check
uv run --locked --all-extras python -m trackball_daemon --release-smoke
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --configuration Release
& ".\tools\build_release.ps1"
git diff --check
```

The release interpreter is pinned to Python 3.13.14. A different interpreter fails before
dependency synchronization; pass `-BuildPython <exact path>` when it is not the first `python` on
PATH. For installer qualification, install the pinned Inno Setup compiler and run:

```powershell
& ".\tools\build_release.ps1" -BuildInstaller -InnoCompilerPath <path-to-ISCC.exe>
```

Public-channel builds additionally require `-Sign`, a certificate thumbprint, and `signtool.exe`.
The builder signs and verifies staged binaries before archiving, signs and verifies the installer,
and passes the resulting signature report into the final manifest.

`build_release.ps1` refuses output paths outside the repository, refuses an interpreter other than
the pinned patch version, and synchronizes an exact,
non-editable build environment from `uv.lock`; the PEP 517 backend is constrained separately because
build dependencies are outside the application lock. It verifies the bundled AutoCAD DLL against
its source-tree and hash manifest, builds the sdist and wheel in an isolated PEP 517 environment,
keeps dependency and Nuitka caches under `build/release`, explicitly includes the AutoCAD DLL,
creates a Windows standalone onedir executable, normalizes its root to `Astrolabe`, and runs its
side-effect-free packaged-resource smoke. The Windows build explicitly includes PyWinRT's projection
package because collection
projections used by BLE advertisement callbacks are imported dynamically and are invisible to static
freezer analysis. It discovers the pinned CPython build's required dynamic runtime families from the
actual interpreter layout because official and standalone distributions use different valid OpenSSL
DLL basenames. That preflight runs before dependency synchronization and compilation; the same
resolved families are staged after Nuitka because not every valid interpreter distribution is
recognized by its dependency scanner. Packaged
smoke waits for the GUI-subsystem process and checks its actual exit code; it is not an asynchronous
launch. The smoke imports that runtime projection, validates the AutoCAD DLL, loads
defaults, profiles, descriptors, schemas, and examples, then compiles both System profiles without
acquiring the controller mutex, opening BLE or Raw Input, starting the tray, loading host
integrations, or writing user config. The script creates a deterministic ZIP of the complete onedir
tree with a checksum file. It then synchronizes a separate production-runtime environment from the
same lock and uses the pinned CycloneDX tool to produce a reproducible, validated SBOM of the
packages actually selected for that environment; the release step then stamps the root application
with the version resolved from the package's authoritative dynamic version source. The script
prints SHA-256 values for the executable, packaged DLL, archive, and SBOM.

Finally it writes the machine-readable release manifest beside the archive — last, because the
manifest records the SBOM's hash and the finalization step above rewrites that file in place. What the
manifest records, and the channel and versioning rules behind it, are described in
[`release.md`](release.md). Pass `-RequireCleanRevision` to refuse a build from a working tree with
uncommitted changes; without it the build proceeds and the manifest records the tree as dirty and its
revision as not describing the artifact.

`build_release.ps1` also audits bundled-component notices against the release runtime environment it
just synchronized. That gate is `tools/audit_notices.py`, which compares the distributions actually
installed for the release against `third_party.json` and `THIRD_PARTY_NOTICES.md` and fails when a
component starts or stops shipping without its attribution being updated. It can be run directly
against any release runtime environment:

```powershell
uv run --locked --all-extras python tools/audit_notices.py --environment <runtime-venv>\Scripts\python.exe
```

The audit reads installed distribution metadata, so it establishes that every *Python* component in
the release runtime has a resolved disposition. After Nuitka and runtime staging,
`tools/audit_native_binaries.py` separately walks every DLL, PYD, and EXE in the completed onedir
tree. Each binary must match exactly one first- or third-party component rule in `third_party.json`,
every third-party owner must have a notice, and stale rules that match nothing fail the build unless
the rule explicitly represents a dynamic library used by only one supported interpreter layout. Run
it directly against an existing artifact with:

```powershell
uv run --locked --all-extras python tools/audit_native_binaries.py --root build/release/nuitka/Astrolabe
```

Notice placement is verified in each distributed form rather than assumed from the build
configuration. The packaged smoke fails when any add-on payload directory lacks the `LICENSE` and
`NOTICE` that setup would copy into a host application, and `tools/verify_installed_metadata.py` reads
the installed distribution's own metadata to confirm the declared license files were written, not
merely declared:

```powershell
<wheel-venv>\Scripts\python.exe tools/verify_installed_metadata.py
```

Every pull request builds and installs the wheel outside the checkout before running its smoke and
binding validation, runs that notice verification against the installed wheel, and runs the bundled
component audit against a separately synchronized release runtime. When a pull request changes an
artifact input, the unsigned onedir waits for those checks and then exercises runtime discovery,
native attribution, packaged smoke, SBOM, archive, and manifest before merge. A manual CI run always
builds it. A separate job compiles the
retained XIAO protocol-bench sketch with pinned Arduino CLI and board-core inputs, reports binary
sizes, and retains the firmware artifacts. Seeed's pinned nRF52 core bundles its packaging utility
for Windows and macOS but calls `adafruit-nrfutil` from `PATH` on Linux, so that job installs and
checks the pinned PyPI release before compiling. A separate job builds the ZMK firmware from exact
ZMK/Zephyr commits in an immutable image and retains the UF2, ELF, effective configuration, DTS,
frozen west manifest, build hashes, SPDX build documents, and firmware license bundle. That job also
asserts the application address by reading it back out of the built UF2, which must be `0x00027000`
for the SuperMini's S140 7.3.0 bootloader. It reads the artifact rather than re-checking the
overlay's constant, so it catches the shield's `code_partition` override being dropped and not only
edited — a build with upstream's `0x26000` lands inside the SoftDevice, produces a board that never
enumerates, and fails nothing at build time. The PMW3610
prototype sketch remains available as source and evidence but is not a release build target. Keep
the XIAO bench gate beside the ZMK build until the live replacement matrix in
[`../TODO.md`](../TODO.md) passes. These checks establish reconstructible inputs and buildability;
they do not establish bit-identical output or qualify product hardware.

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

## Firmware, BLE, and USB matrix

Record firmware revision, descriptor ID, physical control, debounce value, and packet observation:

- initial snapshot, quick press/release, long hold, reconnect, and disconnect while held;
- one missed intermediate sequence repaired by the next full snapshot;
- duplicate, stale/out-of-order, malformed length/version/kind, and undeclared-bit rejection;
- unsigned sequence wrap from `65535` to `0`;
- every declared simultaneous-control combination or a justified pairwise contingency sample;
- legacy rotation-only operation with keyboard bindings;
- daemon-absent standard HID pointer and button operation.

Run the same payload/state cases over the ZMK vendor USB interface. Additionally record feature
capability framing, exact VID/PID/usage/product match, attach request and matching ACK, keepalive,
external-power presentation, cable pull, suspend/resume, daemon termination, BLE-to-USB preference,
USB-to-BLE fallback, and stale-callback/late-disconnect rejection. Exercise every loss and handover
while controls are held; no old lease may release or publish into its replacement session, and no
queued report from an old USB owner may appear after reattach.

The XIAO three-button/jumper bench proves the host protocol boundary, and the SuperMini PMW3610 loop
proved the five-way protocol and input behavior on the production controller. Neither is the product
assembly, and both predate the ZMK stack. The ZMK candidate now runs on the current fixture with
BLE and USB HID output, route handover, and the keepalive fail-safe confirmed
([evidence](../archive/release-evidence/zmk-hardware-freeze-live-2026-08-06.md)) — but confirming
individual behaviors is not running this matrix, and the pin map, debounce margin, sensor pose
calibration, PMW3610 Run/Rest path, and ZMK wake integration remain release blockers tracked in
[`../TODO.md`](../TODO.md).

## Host control matrix

For each available host, record host version, integration version/transport, projection mode, input
profile, and results for:

- Pointer/3D toggle, Ctrl hold 3D, Shift cascading Pan, and explicit Ctrl+Shift in both orders;
- Orbit/Fly/Walk where advertised, horizon entry, secondary controls, and sensitivity holds;
- foreground switch while every hold is active and unknown-foreground global bindings;
- two simultaneously connected hosts with no background motion;
- setup/update/reload and current-versus-stale connected add-on reporting;
- host-specific camera/pivot checks listed in `TODO.md` and the relevant `docs/apps` guide.

Cover Blender, SketchUp, Unreal, Unity, and Onshape when available, plus every other supported
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
