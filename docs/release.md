<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Releases and the internal alpha

Astrolabe has not been publicly released. What exists is a private onedir ZIP built for daily driving,
and the point of this document is to keep that artifact from being mistaken for a product — by anyone,
including its author six months from now.

## Channel identity

The channel is derived from the version, never declared beside it. `trackball_daemon/product.py`
resolves it, and a version and a channel therefore cannot contradict each other.

| Version shape | Channel | Meaning |
|---|---|---|
| `0.2.0a1`, `0.2.0a2`, … | `internal-alpha` | Private artifact. Unsigned, no support claim, not for distribution. |
| `1.0.0` and later finals | `public` | A released product. |

Two shapes deliberately refuse to resolve instead of guessing:

- a **final version below `1.0.0`**, because `1.0.0` is reserved for public V1 and a `0.x` final would
  otherwise be publishable as one;
- a **beta or release candidate**, because no channel has been defined for one. Recording it as
  `internal-alpha` would understate it and `public` would overstate it, so the channel has to be
  decided before such a version can be built.

Increment the alpha serial per artifact: `0.2.0a1`, `0.2.0a2`. The version lives in
`trackball_daemon/__init__.py` and everything else — archive name, SBOM root, manifest, tray display —
reads it from there.

## What a build produces

`tools/build_release.ps1` writes these into `build/release/`:

| Artifact | Notes |
|---|---|
| `nuitka/release_entry.dist/` | The complete onedir tree, including licences, notices, and every host add-on payload. |
| `Astrolabe-<version>-windows-x64.zip` | Deterministic archive of that tree, plus a `.sha256` beside it. |
| `Astrolabe-<version>-windows-x64.cdx.json` | CycloneDX SBOM generated from the locked runtime environment, not from the lock file. |
| `Astrolabe-<version>-windows-x64.manifest.json` | The release record described below. |
| `python/` | Wheel and sdist. |

The manifest sits *beside* the archive rather than inside it, because it records the archive's own
hash.

Pass `-RequireCleanRevision` to refuse a build whose working tree has uncommitted changes. Local
builds omit it and the manifest records the tree as dirty instead.

## The release manifest

`tools/build_release_manifest.py` records what the artifact is. Every field is derived from something
already authoritative, so there is no second place for any of it to drift:

- **product identity, target** — `product.py`;
- **version and channel** — the package version, and the channel derived from it;
- **source** — the Git revision, whether the tree was dirty, and explicitly whether that revision
  describes the artifact. A dirty build records `revision_describes_artifact: false`, because a
  revision cannot describe files that are in no commit;
- **build** — the embedded Python version, taken from the interpreter that ran the build, and the
  SHA-256 of `uv.lock`;
- **components** — every host add-on version from the integration registry, plus the AutoCAD plugin's
  version, source revision, and DLL hash from its provenance manifest;
- **support tiers** — the snapshot from `app_registry.py`, so the artifact carries its own support
  claim rather than relying on a release note;
- **licences** — the project expression, the shipped licence texts, and the bundled-component counts;
- **artifacts** — name, size, and SHA-256 for the executable, AutoCAD DLL, archive, and SBOM. A
  declared artifact that does not exist is an error, never a silently omitted entry;
- **signatures** — `signed: false` with a reason. Stated rather than omitted: an absent key would read
  as "not recorded yet" instead of "not signed".

Signing is not part of this channel. When it arrives it happens in a release staging tree, and a
signed hash is recorded separately from the deterministic development hash — signing changes the
bytes, and the checked-in AutoCAD DLL must keep matching its source provenance.

## Daily-driver process

The alpha exists to be used, and observations only count if they came from the artifact.

**Run the packaged executable.** Not `python -m trackball_daemon`, not the checkout. A source run has
different import paths, a different interpreter, and no packaged resources, so it cannot confirm or
refute anything about the build.

Record each observation with:

- the exact build revision and manifest version;
- host application and version;
- reproduction steps;
- expected and actual behaviour;
- the relevant log excerpt;
- severity;
- **support-tier impact** — whether it touches a supported or experimental integration, since that is
  what decides whether it blocks a release.

Keep the working journal in issues or another private operational surface. Promote only durable
unresolved work to [`TODO.md`](../TODO.md); a journal entry that was fixed the same day does not
belong there.

Store evidence from an executed artifact under [`archive/release-evidence/`](../archive/release-evidence/)
in a dated file, and keep only distilled invariants and warnings in the matching app guide.

**What is not release evidence:** a YouTube video, development narration, a screen recording of the
source tree running, or a behaviour observed while iterating on code. Each of these describes
something other than the artifact under test.

## Release gates

The commands a build must pass, and the skip/release-impact rules for each, live in
[`release_verification.md`](release_verification.md). This document deliberately does not restate
them; a second copy of a gate list is a list that goes stale.

## The two workflows

| Workflow / job | Trigger | Produces | Credentials |
|---|---|---|---|
| `ci.yml` · `checks`, `firmware` | every pull request and push to `main` | wheel, sdist, installed-wheel smoke, SBOM, firmware | none |
| `ci.yml` · `onedir` | push to `main`, or a manual run | **unsigned onedir**, SBOM, manifest | none |
| `release.yml` | a `v*` tag, or a manual run naming an exact revision | the same artifacts plus a **draft** GitHub release | signing secrets, scoped to the `release` environment |

`ci.yml` references no secrets and declares no environment, and tests enforce both.

The onedir build is deliberately not on pull requests. It is what a user actually runs, and its build
path — Nuitka, data-file inclusion, the packaged smoke, the SBOM, the manifest — is exercised nowhere
else, so it does need to run somewhere: on the way into `main`, before anything can be released from
it. Per proposal it is a ten-plus-minute Windows job billed at twice the Linux rate, which spends the
monthly budget faster than it finds anything. Use the `workflow_dispatch` trigger to run it against a
branch when the packaging path is what changed.

Superseded runs are cancelled, so a branch pushed several times in a row does not keep every
intermediate run alive to completion.

CI also asserts that the manifest it produced does **not** claim to be signed, because it has no
certificate to sign with.

`release.yml` runs every gate before producing a single artifact — a suite failure found after signing
has already spent the certificate on bad bytes — then builds with `-RequireCleanRevision`, re-verifies
the manifest, and leaves a **draft**.

**Publishing is always a human action.** The workflow never publishes, and that is not merely a
default: a GitHub `environment:` naming a target with no required reviewers grants no approval at all —
GitHub creates it implicitly and the job proceeds. An approval gate that depends on repository settings
being right is a protection that can silently not be there, so the last step is a person opening the
draft, reading the embedded manifest, and pressing Publish.

Configure required reviewers on the `release` environment before any signed release. Until signing
exists the job cannot use credentials anyway, so the setting is currently belt-and-braces.

## Signing

Nothing is signed yet, and the manifest says so rather than staying quiet about it. The contract a
signing step has to satisfy already exists and is tested:

- sign the **staged** AutoCAD DLL, never the checked-in one. Signing changes bytes, and the
  checked-in DLL must keep matching the source provenance manifest that `verify_autocad_artifact.py`
  enforces;
- record the signed hash as its own fact. `components.autocad_plugin.sha256` is the development hash
  and `artifacts.autocad_plugin.sha256` is what shipped; they are equal only while unsigned;
- write a signature report — subject, issuer, thumbprint, timestamp authority, and the verification
  verdict per artifact — and pass it to `build_release_manifest.py --signature-report`. A report whose
  own verdict is negative, one that omits a signable artifact, or one naming something that is not an
  Authenticode target is refused;
- expect `verify_release_manifest.py` to fail if an artifact was signed *after* its hash was recorded.
  That is intended: signing is a modification, and the signed bytes must be hashed as their own fact.

Three things block it, and `release.yml` fails closed on a final version rather than work around any
of them:

1. **No code-signing certificate.** Nothing else can proceed without one.
2. **The build has no signing stage.** `build_release.ps1` runs compile → archive → manifest, with no
   point between compiling and archiving at which signed bytes could be produced. A signed release
   needs that restructuring so the archive is built from signed binaries.
3. **No installer exists**, so the installer signing and verification steps have nothing to act on.

[`TODO.md`](../TODO.md) carries all three.
