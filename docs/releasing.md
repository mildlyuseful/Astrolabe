<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Building and releasing

Astrolabe has not been publicly released. Builds are internal alphas: unsigned, unsupported, not for
distribution. This page is how an artifact is built, what it records about itself, and what has to
be true before one ships. Open release blockers live only in [`TODO.md`](../TODO.md).

## Versions and channels

The channel is derived from the version in `trackball_daemon/__init__.py`, never declared beside it
(`product.py` resolves it):

| Version | Channel | Meaning |
|---|---|---|
| `0.2.0a1`, `0.2.0a2`, … | `internal-alpha` | Private artifact. Unsigned, no support claim. |
| `1.0.0` and later finals | `public` | A released product. |

A final below `1.0.0`, or any beta or release candidate, refuses to resolve: `1.0.0` is reserved for
public V1, and no channel exists yet for the others. Bump the alpha serial per artifact.

## Checks

Install the `uv` version pinned in `pyproject.toml` (or run it as `uvx --from uv==<version> uv`),
plus Node.js 22+ for the Onshape userscript tests and the .NET 8 SDK for the AutoCAD math tests:

```powershell
uv sync --locked --all-extras
uv run --locked --all-extras python -m pytest -q
uv run --locked --all-extras python -m compileall -q trackball_daemon tests tools
uv run --locked --all-extras python -m trackball_daemon.validate_bindings
uv run --locked --all-extras python -m trackball_daemon --release-smoke
uv run --locked --all-extras python tools/verify_autocad_artifact.py
uv run --locked --all-extras python tools/apply_license_headers.py --check
dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --configuration Release
python firmware/build.py
```

## Building the artifact

```powershell
.\tools\build_release.ps1                      # unsigned onedir, ZIP, SBOM, manifest
.\tools\build_release.ps1 -BuildInstaller -InnoCompilerPath <ISCC.exe>
.\tools\build_release.ps1 -RequireCleanRevision # refuse uncommitted changes
```

The builder requires the pinned interpreter (Python 3.13.14; pass `-BuildPython` if it is not first
on `PATH`), syncs an exact environment from `uv.lock`, verifies the AutoCAD DLL against its source
provenance, compiles a Nuitka onedir, runs the packaged smoke (resources, defaults, profiles,
descriptors, the settings UI files, and the AutoCAD DLL — without starting the daemon), and audits
every bundled Python distribution and every native binary against `third_party.json`. It writes to
`build/release/`:

| Artifact | |
|---|---|
| `nuitka/Astrolabe/` | The onedir tree, with licences, notices, and every host add-on payload |
| `Astrolabe-<version>-windows-x64.zip` (+ `.sha256`) | Deterministic archive of that tree |
| `…cdx.json` | CycloneDX SBOM of the runtime actually installed |
| `…manifest.json` | What the artifact is, below |
| `python/` | Wheel and sdist |

The manifest records product identity, version and channel, the source revision and whether the tree
was dirty (a dirty build says its revision does not describe it), the embedded Python and `uv.lock`
hash, every add-on version, the support-tier snapshot from `app_registry.py`, licences, the hash of
every artifact, and `signed: false` with a reason. `tools/verify_release_manifest.py` re-checks a
manifest against the files.

## CI

| Workflow | Trigger | Produces | Secrets |
|---|---|---|---|
| `ci.yml` · checks | every pull request, manual | tests, wheel smoke, notice audit | none |
| `ci.yml` · onedir | PRs that change a packaging input, manual | unsigned onedir, SBOM, manifest | none |
| `ci.yml` · zmk_firmware | every pull request, manual | UF2, ELF, config, SPDX, provenance | none |
| `release.yml` | a `v*` tag, or a manual run on an exact revision | the same, plus a **draft** release | `release` environment only |

The firmware job builds from the pinned ZMK/Zephyr commits in an immutable container and reads the
application address back out of the UF2, so a dropped flash-layout override fails the build instead
of bricking a board. `release.yml` runs every gate before building, and never publishes: a person
reads the draft and its manifest and presses Publish.

## Signing

Nothing has been signed; the certificate does not exist yet. The builder fails closed — a public
build requires `-Sign`, an installer, and a thumbprint — and signs the *staged* AutoCAD DLL, never
the checked-in one, recording the signed hash as its own fact beside the development hash.

## Before a release

Automated, mocked, and live results are different claims; a skipped check records why and what it
blocks. Using the packaged build, not a source checkout:

- **Install**: clean Windows account without Python; run, uninstall, and reverse every path in
  [`security.md`](security.md); record checksums, SBOM, and antivirus/SmartScreen results.
- **Keyboard**: left/right modifiers and a non-US layout (AltGr must not strand Ctrl/Alt), Sticky
  Keys, key repeat, both Ctrl/Shift orders, profile switch and save while held, lock/unlock,
  sleep/resume, Remote Desktop, and an elevated foreground app. Failure must release, never press.
- **Device**: snapshots (quick press, long hold, reconnect, disconnect while held, sequence wrap,
  malformed and stale packets), standalone mouse with no daemon, and over USB: attach, keepalive,
  cable pull, suspend, daemon exit, BLE↔USB handover — all while controls are held.
- **Firmware**: ball motion and fusion signs on the Live page, standalone clicks and scroll, the
  four gestures and their LED patterns, sleep and wake, and the press-jitter check from
  [`hardware.md`](hardware.md).
- **Hosts**: per supported host, Pointer/3D, holds, Orbit/Fly/Walk where offered, focus switches
  while holding, two hosts open at once, and setup/update/reload. Unavailable hosts are recorded as
  unavailable, not passed.
- **UI and HUD**: every page in light and dark, link/unlink/reset, binding edits, multiple monitors
  and DPI for the HUD, no focus stealing, and quit while windows are open.
