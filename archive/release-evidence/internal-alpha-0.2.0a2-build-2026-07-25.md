<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Internal alpha 0.2.0a2 — build provenance, 2026-07-25

Clean private-alpha onedir and per-user installer build. This records build and packaged-resource
smoke only. It is not clean-account, daily-driver, host-application, signing, antivirus, or hardware
qualification.

## Identity

| Field | Value |
|---|---|
| Version | `0.2.0a2` |
| Channel | `internal-alpha` (derived from the version) |
| Target | `windows-x64` |
| Source revision | `2e9879d9d96d43696e24d99077e545bc7ec55343` |
| Working tree | clean; `revision_describes_artifact: true` |
| Embedded Python | `3.13.14` |
| `uv.lock` SHA-256 | `3eda44ef45806cc4a3969b1684a462d411c6cbedabcc43adabe50e0c581b92ce` |
| Installer compiler | Inno Setup `6.7.3`, downloaded from its pinned upstream URL after SHA-256 and Authenticode verification |
| Signed | no — the internal-alpha channel is deliberately unsigned |

Build command:

```powershell
.\tools\build_release.ps1 `
  -RequireCleanRevision `
  -BuildInstaller `
  -BuildPython C:\Users\dylan\AppData\Roaming\uv\python\cpython-3.13.14-windows-x86_64-none\python.exe `
  -InnoCompilerPath C:\tmp\astrolabe-inno-6.7.3\compiler\ISCC.exe
```

## Artifact hashes

| Artifact | Size | SHA-256 |
|---|---:|---|
| `Astrolabe.exe` | 33,320,448 | `f172a48ec59eec987126dbdcf22c3ade396be5f3f7525c197b9cca25416a5375` |
| `TrackballNavAcad.dll` | 45,568 | `accaa944b6f859f7fe35a07ef8ca10c04bb5356bf8743d3184679f75c7e791c4` |
| `Astrolabe-0.2.0a2-windows-x64.zip` | 32,338,168 | `a417addb93a45a4c0301761b196ffd1da04498686d61182e4000b87360990177` |
| `AstrolabeSetup-0.2.0a2-windows-x64.exe` | 22,012,267 | `77cadbc91b28415476be22abae2cf3e0a6e094ff589e24b1c2651f1b31682d79` |
| `Astrolabe-0.2.0a2-windows-x64.cdx.json` | 28,541 | `477fb92a4f0648dcd3fe0353ee920c2e9de323f2c2e899519c55f7b524f2f8f8` |

`verify_release_manifest.py` independently rechecked all five artifact hashes. The installer reports
file version `0.2.0.2`, product `Astrolabe`, company `Mildly Useful`, and Authenticode state
`NotSigned`, consistent with the channel.

## Executed checks

- Python suite: `1039 passed, 1 skipped`.
- AutoCAD NavMath console suite: all checks passed.
- Binding validation, bundled AutoCAD provenance verification, source release smoke, SPDX audit,
  bundled-component notice audit, and manifest verification passed.
- The final staged `Astrolabe.exe --release-smoke` completed with exit code 0 while the builder
  waited for the GUI-subsystem process.
- The ZIP has exactly one root, `Astrolabe/`.
- The ZIP contains no `ui_demo` entry. Vernier remains tracked source but is non-shipping.
- The ZIP contains the seven explicitly staged pinned-CPython runtime DLLs:
  `python3.dll`, `vcruntime140_1.dll`, `tcl86t.dll`, `tk86t.dll`, `libcrypto-3-x64.dll`,
  `libssl-3-x64.dll`, and `libffi-8.dll`.
- The support-tier snapshot is five supported integrations and six experimental integrations.

## Build defect found and closed

The first build attempt exposed two coupled release-gate defects: Nuitka did not discover seven
native runtime DLLs from the uv-managed CPython distribution, and direct PowerShell invocation of
the GUI-subsystem executable did not wait for its failing process. That attempt produced a broken
intermediate ZIP before the asynchronous failure surfaced.

Commit `2e9879d9d96d43696e24d99077e545bc7ec55343` explicitly stages those runtime files and makes
packaged smoke wait for and inspect the real process exit code. A repaired staging tree passed, the
full suite passed again, and the clean build overwrote the invalid intermediate artifacts. Only the
final hashes above qualify as the `0.2.0a2` build.

## Not established here

- Installation, upgrade, launch, and uninstall behavior in a clean standard Windows account.
- Daily-driver behavior or any supported/experimental host-application matrix.
- Authenticode, timestamp, SmartScreen, Defender, third-party antivirus, or trust-prompt behavior.
- Non-US AltGr, Remote Desktop transitions, or reversal-path qualification.
- Final XIAO production hardware, BLE transport, power, recovery, or firmware behavior.
