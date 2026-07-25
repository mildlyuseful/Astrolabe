<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Internal alpha 0.2.0a1 — build provenance, 2026-07-25

First artifact built from a clean revision with a release manifest. This records the build only.
**No behaviour of this artifact has been observed yet** — it has not been run on a host application,
and nothing here is host qualification.

## Identity

| Field | Value |
|---|---|
| Version | `0.2.0a1` |
| Channel | `internal-alpha` (derived from the version) |
| Target | `windows-x64` |
| Source revision | `b75a50c416cc30627bb8c2bb06bdfac154b6c7f6` |
| Working tree | clean; `revision_describes_artifact: true` |
| Embedded Python | 3.13.14 |
| `uv.lock` SHA-256 | `ad0dfb21f2a44586ef391ecad4dc89b9aaadbf974ce197c98fb25a8b9f9df650` |
| Signed | no — unsigned channel |

Built with `tools/build_release.ps1 -RequireCleanRevision`, which refuses a build whose recorded
revision would not describe it.

## Artifact hashes (SHA-256)

| Artifact | Size | SHA-256 |
|---|---|---|
| `Astrolabe.exe` | 33,191,936 | `6395b8ffa39cafbc8f216251268f540d9574bc73a8e5feb36f8b8f7f985d52bb` |
| `TrackballNavAcad.dll` | 45,568 | `accaa944b6f859f7fe35a07ef8ca10c04bb5356bf8743d3184679f75c7e791c4` |
| `Astrolabe-0.2.0a1-windows-x64.zip` | 32,316,166 | `798f2c190261edfb6348577eed9510d53b5f8e6a16928ef2a2804dcfbeaf0faa` |
| `Astrolabe-0.2.0a1-windows-x64.cdx.json` | 28,541 | `deefd6645fb478faaab8bcc3b465ee022004d64dd4d6d63a29b7fbe101a83dff` |

The bundled AutoCAD DLL hash matches its checked-in source provenance manifest (plugin `0.3.20`,
source revision `88c233595a30764267b6c28ca909a3c702e2c235`), as it must: it is copied, never rebuilt.

## Component versions

Host add-ons: blender `0.1.24`, freecad `0.1.15`, fusion360 `0.1.25`, godot `0.1.14`, rhino `0.1.19`,
sketchup `0.2.14`, unity `0.1.16`, unreal `0.2.14`. SolidWorks and Onshape ship no payload.

Support-tier snapshot carried by the artifact: 5 supported, 6 experimental.

## Observations from this build

- **Nuitka output is not byte-reproducible.** An immediately preceding build of the same tree produced
  a different `Astrolabe.exe` (`ce8f837e…`) and therefore a different ZIP. The SBOM hash was identical
  across both builds, so the non-determinism is in the compiled executable, not in the dependency set
  or the SBOM generator. Any future claim of a reproducible artifact has to account for this; the
  deterministic-ZIP guarantee is about archive layout, not about the executable inside it.
- The manifest correctly reported the earlier build as dirty and this one as clean, so the
  dirty-tree detection is exercised in both directions.
- Packaged `--release-smoke` on the built executable: `ok`, 18 payload notices, tier snapshot present.

## Not established here

- Any host-application behaviour, on any integration.
- Behaviour on a clean Windows account without Python or a source checkout.
- SmartScreen, Defender, or antivirus reaction to the unsigned executable.
- Anything about the installer, which does not exist.
