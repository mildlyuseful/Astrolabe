# P1 release foundation implementation plan

This is the execution handoff for the hardware-independent portion of P1. It records the product
and release decisions that determine implementation order; [`../TODO.md`](../TODO.md) remains the
sole ledger for unresolved work, and [`release_verification.md`](release_verification.md) remains
the durable release gate.

When this initiative is complete, move this plan under `archive/initiatives/` rather than leaving a
finished phase plan among current documentation.

## Before starting

- Read [`../AGENTS.md`](../AGENTS.md), [`architecture.md`](architecture.md),
  [`security.md`](security.md), [`release_verification.md`](release_verification.md), and
  [`../TODO.md`](../TODO.md).
- Inspect the working tree and preserve unrelated user changes. At the time this handoff was
  created, pre-existing changes were present in `TODO.md` and `ui_demo/`, with untracked capture CSV
  files. They are not part of this initiative unless the user explicitly brings them into scope.
- Keep release-tier, identity, and capability data in `trackball_daemon/app_registry.py`; do not add
  parallel application tables.
- Keep test evidence and artifact-specific hashes out of evergreen docs. Put executed evidence in a
  dated file under `archive/release-evidence/`.
- Do not claim a legal entity, signature, clean-account pass, live-host pass, or hardware pass before
  that exact boundary has been exercised.

## Fixed decisions

| Concern | Decision |
|---|---|
| Software license | Apache License 2.0 |
| Hardware-design license | CERN Open Hardware Licence Version 2 - Weakly Reciprocal |
| Product | Astrolabe |
| Publisher and future company identity | Mildly Useful |
| First alpha | Private daily-driver build for the project owner |
| Public launch | V1 with a signed installer and polished onboarding |
| Supported integration tier | FreeCAD, Onshape, Fusion 360, Blender, and SolidWorks |
| Experimental integration tier | SketchUp, Unreal, Unity, Rhino, and AutoCAD |
| AutoCAD | Experimental because the host has not been stable enough for a support commitment |
| Product strategy | Build audience goodwill and a catalog of interesting products; do not optimize Astrolabe as the sole permanent flagship |

The private alpha does not require an installer, a public support promise, SmartScreen reputation,
or final product hardware. Public V1 requires those applicable boundaries to be closed.

## Target milestones

### Milestone A - licensed internal alpha

Produce a private onedir build from an exact revision with complete first-party licensing,
third-party notices, package metadata, an SBOM, a release manifest, and checksums. Daily-drive the
packaged build rather than a source or `pythonw` invocation.

### Milestone B - V1 software release candidate

Produce a signed, per-user installer with a complete first-run experience, supported/experimental
integration presentation, clean-account qualification, reversal coverage, and exact-artifact
security evidence. This milestone can be completed without production hardware except for the final
device-specific onboarding and acceptance rows.

### Milestone C - public V1 product

Combine the V1 software candidate with the final SuperMini nRF52840 (nice!nano v2-compatible)
hardware, production
firmware, physical input/BLE qualification, supported-host live evidence, product marking, and the
revision-specific hardware source release.

## Work batch 1 - licensing, ownership, and contribution policy

### License structure

Use a mixed-license repository with a short scope document instead of implying that one root license
governs every future artifact:

- Add the unmodified Apache-2.0 text as the root software license.
- Add the unmodified CERN-OHL-W-2.0 text under `LICENSES/`.
- Add a concise license-scope file that maps paths and artifact types to their license.
- Add `NOTICE`, `THIRD_PARTY_NOTICES.md`, `TRADEMARKS.md`, `CONTRIBUTING.md`, and `SECURITY.md`.
- Use SPDX identifiers in commentable first-party files and an explicit machine-readable mapping for
  binary, generated, or non-commentable files.

Apache-2.0 covers:

- the daemon and Python package;
- firmware, including future production firmware;
- host integrations and the AutoCAD source/DLL;
- tools, tests, schemas, examples, CI, documentation, and first-party UI assets.

CERN-OHL-W-2.0 covers future preferred-form hardware design source:

- schematics and PCB layouts;
- editable enclosure and mechanical CAD;
- manufacturing and assembly drawings;
- BOM and configuration information;
- fixtures and source needed to make or test the product.

Use exact CERN-OHL-W version 2.0, not an automatic "or later" grant. Firmware remains software and is
Apache-2.0.

### Packaged notice behavior

- Include the Apache license, project notice, and applicable third-party notices in the wheel,
  source distribution, onedir tree, ZIP, and installer.
- Add an adjacent Apache license/notice to host payloads copied outside the onedir tree. This is
  especially important for the compiled AutoCAD DLL.
- Keep third-party license texts and attributions separate from the Apache `NOTICE` unless the
  upstream component specifically requires a NOTICE entry.
- Audit the exact locked runtime selected for the release, including the embedded Python runtime,
  Tcl/Tk, Nuitka runtime pieces, Bleak/WinRT, pywin32, Pillow, pystray, cryptography, native
  libraries, and first-party or third-party visual assets.
- Make CI fail when a selected bundled component lacks a resolved license/notice disposition.

### Ownership and contributions

- Do not identify a future company as the copyright owner until the legal entity or registered
  business identity exists and the existing IP is assigned to it in writing.
- Use the exact validated legal or registered name for code signing. The intended displayed identity
  is Mildly Useful.
- Reserve Mildly Useful, Astrolabe, and their logos through a trademark policy; open-source licensing
  does not grant product-brand use.
- Use a Developer Certificate of Origin and `Signed-off-by` contributions. Do not introduce a CLA
  while the project intends to keep the fixed open licenses.

### Acceptance

- Every first-party path has one unambiguous license disposition.
- Every released or externally copied payload carries its required notices.
- Wheel, sdist, onedir, and release-archive tests verify the license files.
- The dependency notice audit agrees with the locked runtime and SBOM.

## Work batch 2 - package and Windows product identity

### Python metadata

Complete `pyproject.toml` with:

- Apache-2.0 license expression and license-file declarations;
- README, author/maintainer, project, source, issue, documentation, and security metadata;
- stable supported-Python and Windows policy;
- classifiers, keywords, and complete description.

Recommended public naming:

| Surface | Name |
|---|---|
| Product | Astrolabe |
| Publisher | Mildly Useful |
| Executable | `Astrolabe.exe` |
| Installer | `AstrolabeSetup-<version>-windows-x64.exe` |
| Python distribution | `astrolabe-daemon`, after availability is verified |
| Python import package | Keep `trackball_daemon` |

Do not rename the Python import namespace merely for branding.

### Stable Windows identity

Freeze these before public V1:

- installer application/upgrade identifier;
- AppUserModelID;
- publisher and display name;
- install path, configuration path, Start Menu folder, uninstall identity, startup value, and
  single-instance identity.

Use a per-user install rooted under:

```text
%LOCALAPPDATA%\Programs\Mildly Useful\Astrolabe
```

Prefer a future canonical configuration root of:

```text
%APPDATA%\Mildly Useful\Astrolabe
```

If the existing `%APPDATA%\TrackballDaemon` path is migrated, do it at the path authority:

1. Prefer an already-valid new location.
2. When only the legacy location exists, copy it through an atomic staged migration.
3. Validate the complete migrated state before selecting it.
4. Preserve the legacy directory for rollback.
5. Never overwrite either location merely because loading failed.

All host integrations must consume the shared path authority rather than embedding a new or legacy
path independently.

### Security reporting

Add a root security policy that uses GitHub private vulnerability reporting initially. State that
only the latest public release receives security support, avoid an unsupported response-time SLA,
and instruct reporters not to post certificates, private paths, BLE addresses, or sensitive logs
publicly. Add a Mildly Useful security mailbox after the domain and mailbox exist.

### Acceptance

- Built metadata is correct when inspected from the installed wheel, not only from `pyproject.toml`.
- Distribution and import names remain intentionally distinct.
- Product paths and identifiers have one owner and migration coverage.
- Corrupt or partial legacy state cannot destroy user configuration.

## Work batch 3 - supported and experimental integration policy

Add a `SupportTier` enum to the canonical `AppSpec` model. Use:

- `SUPPORTED`
- `EXPERIMENTAL`

Do not use the release tier as host-version compatibility. The UI must be able to state, for example,
"AutoCAD: Experimental integration; AutoCAD 2026: verified host version."

### Tier assignments

Supported:

- FreeCAD
- Onshape
- Fusion 360
- Blender
- SolidWorks

Experimental:

- SketchUp Desktop
- Unreal Engine
- Unity
- Rhino
- AutoCAD

Known-incompatible versions and variants remain `unsupported` in the existing compatibility model.
An `unverified` host version may work but has not earned a compatibility claim.

### Maintenance contract

A supported integration:

- participates in the public V1 release gate;
- is advertised only for its verified host-version range;
- treats ordinary-navigation regressions as release-blocking;
- receives active compatibility maintenance and normal product triage;
- includes setup, update, reversal, and runtime-health behavior in its support claim.

An experimental integration:

- is opt-in and clearly labeled;
- may ship with documented host limitations;
- has no promise for every host update;
- does not block release for an isolated functional regression;
- still blocks release for a shared security, data-loss, configuration-corruption, or lifecycle
  defect.

For a known-unsupported host version, block automatic setup or require an explicit advanced
override with an exact warning.

### Projection

- Derive UI grouping, onboarding grouping, release-manifest data, and any generated support matrix
  from `AppSpec`.
- Rename UI text that currently calls the complete list "Supported 3D apps."
- Keep supported integrations prominent and collapse experimental integrations by default.
- Keep every integration disabled until explicitly enabled.
- Retain AutoCAD in source. Include its DLL in a public build only when the exact artifact passes
  integrity/signing checks and there is no known destructive viewport defect.
- Add tests that fail when user-facing support information and the registry diverge.

## Work batch 4 - internal-alpha artifact and release provenance

The private alpha remains an onedir ZIP, not an installed public product. Build it from a clean
revision and include:

- complete onedir tree;
- deterministic ZIP and checksum;
- CycloneDX SBOM;
- licenses and notices;
- source revision;
- machine-readable release manifest;
- explicit internal-alpha channel identity.

Use a PEP 440 prerelease sequence such as `0.2.0a1`, `0.2.0a2`, and so on, with `1.0.0` reserved for
public V1.

### Daily-driver process

- Run the packaged onedir executable, not the source checkout.
- Record each observation with exact build revision, host/version, reproduction, expected/actual
  behavior, logs, severity, and support-tier impact.
- Keep the working journal in issues or another private operational surface.
- Promote only durable unresolved work to `TODO.md`.
- Store executed artifact evidence under `archive/release-evidence/`.
- Do not treat YouTube videos, development narration, or source-run behavior as release evidence.

## Work batch 5 - protected release pipeline

### Ordinary CI

PR and branch CI should:

- run the complete automated suite and compilation checks;
- build wheel, sdist, and unsigned onedir artifacts;
- validate packaged resources, licenses, notices, and SBOM;
- validate installer source after it exists;
- never receive signing credentials.

### Release workflow

Use a protected tag/manual workflow with an approval environment:

1. Check out an exact clean revision.
2. Verify tag, package, integration, and installer versions agree.
3. Run all automated release gates.
4. Build and populate a release staging tree.
5. Insert licenses, notices, release notes, and manifest inputs.
6. Sign and timestamp the staged AutoCAD DLL.
7. Record its signed hash separately from its deterministic development/source-build hash.
8. Update release-only AutoCAD provenance and rerun the packaged smoke.
9. Sign and timestamp `Astrolabe.exe`.
10. Verify all expected signatures.
11. Create the ZIP from the signed staged tree.
12. Build the installer from that same tree.
13. Sign and timestamp the installer.
14. Generate final hashes, SBOM, support-tier snapshot, and release manifest.
15. Create a draft GitHub release.
16. Require human approval before publishing.

Do not sign the checked-in development AutoCAD DLL. Signing changes its bytes and must occur in a
release staging tree without weakening the existing source/artifact provenance checks.

### Release manifest

Record:

- product and component versions;
- Git revision and release channel;
- Windows target and embedded Python version;
- dependency-lock hash;
- licenses;
- support-tier snapshot;
- ZIP, installer, executable, AutoCAD DLL, and SBOM hashes;
- signature subject, certificate identity, and timestamp verification.

Reject the release if an artifact is modified after signing or after its final hash is recorded.

## Work batch 6 - V1 installer

Use Inno Setup for the first public installer. Configure a non-elevated, current-user installation.
Defer MSIX because its identity/virtualization model adds risk around shared AppData state, external
host payloads, and integrations loaded into other desktop applications.

The installer owns:

- the signed onedir application tree;
- Start Menu shortcut;
- uninstall registration;
- upgrade of an existing installation;
- optional post-install launch;
- removal of installed application files.

The installer does not:

- install or enable CAD add-ons;
- change AutoCAD trusted paths;
- trust an Onshape certificate;
- add a firewall rule;
- enable start-at-login without explicit in-app action;
- launch or modify a host application;
- require administrator rights.

Those actions stay behind the existing explicit, reversible in-app integration setup boundaries.

### V1 update policy

V1 supports manual upgrade by running a newer signed installer. Do not add an automatic updater in
this batch. A later updater needs its own signed manifest, HTTPS and hash verification, channel
policy, atomic replacement, rollback, and downgrade rules.

### Installer acceptance

Exercise:

- fresh standard-user install;
- upgrade over an older build;
- daemon-running shutdown/upgrade/uninstall;
- cancelled and interrupted install;
- failed upgrade rollback;
- configuration preservation;
- explicit optional user-data removal;
- uninstall then reinstall;
- no unexpected UAC, firewall, certificate, startup, or host changes.

## Work batch 7 - V1 first-run onboarding

Build onboarding by projecting existing authorities rather than creating a second setup system:

1. **Welcome:** Mildly Useful/Astrolabe identity, open-source links, no-telemetry statement, and a
   concise explanation of BLE, optional Raw Input, loopback services, and host integration.
2. **Device:** discover the trackball, show firmware/protocol/descriptor state, and allow a
   keyboard-only skip.
3. **Input test:** visualize X/Y/Z and five-way state, detect missing/stuck controls, and offer
   physical-orientation calibration.
4. **Applications:** show detected supported integrations first and experimental integrations in a
   separate collapsed section.
5. **Setup:** reuse `integrations.py` instructions, consent, health, setup, and reversal information.
6. **Navigation test:** verify mode, focus, orbit, pan, zoom, and runtime-health state.
7. **Finish:** optional Start at login, open Settings, documentation, issue reporting, and Mildly
   Useful channel links.

Constraints:

- no silent userscript installation or certificate trust;
- no silent host modification;
- no telemetry by default;
- no support claim for experimental integrations;
- keyboard accessibility, focus order, mixed-DPI behavior, and clear error recovery are V1 gates.

Most onboarding can be built with keyboard-only and simulated/device-bench states. Final production
pairing, recovery, battery, and firmware-update behavior remains hardware-gated.

## Work batch 8 - signing identity and exact-artifact qualification

### Signing prerequisite

- Establish or register the exact Mildly Useful identity that will appear to users.
- Assign the existing project IP to that identity where appropriate.
- Enroll that identity with a trusted Authenticode provider. Prefer Microsoft Artifact Signing
  (formerly Trusted Signing) if identity and regional eligibility fit.
- Keep signing credentials in a protected release environment, never PR CI or a developer script.
- Sign `Astrolabe.exe`, the staged AutoCAD DLL, the installer, and any future updater with one
  persistent, timestamped publisher identity.
- Do not purchase EV solely to avoid SmartScreen; a new signed publisher/file can still require
  reputation accumulation.

### Clean-environment split

Clarify the existing P1 clean-account wording:

- Test the wheel in a clean supported-Python environment with no source checkout.
- Test onedir and installer artifacts in a clean standard Windows account with neither Python nor a
  source checkout.

Use disposable VMs or snapshots for:

- fresh install, upgrade, and uninstall;
- primary/secondary monitors and mixed DPI;
- a physical non-US AltGr layout;
- Remote Desktop connect/disconnect;
- elevated-foreground transitions;
- Defender and at least one independent third-party antivirus;
- Windows Firewall and registry before/after comparison;
- Chromium and Firefox Onshape certificate behavior.

### Reversal

Exercise every path in [`security.md`](security.md), including start-at-login, each copied host
payload, Rhino startup registration, AutoCAD trust/DLL state, and Onshape key/certificate/trust/
userscript state. Automated targeting tests do not replace a real install/reversal run.

### Supported-host boundary

Before public V1, the live P2 matrix is release-blocking for FreeCAD, Onshape, Fusion 360, Blender,
and SolidWorks. Experimental-host gaps remain recorded but are non-blocking unless they expose a
shared security, data-loss, configuration, or lifecycle defect.

## Hardware-gated work

Do not attempt to close these with the current validation devices:

- production sensor/count/geometry and electrical contract;
- final controller pin, power, battery, recovery, USB/BLE, and switch behavior;
- production firmware port and ZMK decisions;
- final physical simultaneous-control, reconnect, sleep/wake, and mode-transition matrix;
- battery and latency measurements;
- production device onboarding and firmware update/recovery;
- hardware design source bundle, product source-location notice, and physical packaging/marking.

The completed SuperMini PMW3610 loop and XIAO protocol bench remain useful development evidence but
do not qualify the product assembly; the SuperMini controller itself is now the production target.

## Documentation ownership during implementation

- `README.md`: user operation, install, onboarding, support-tier meaning, and troubleshooting.
- `docs/architecture.md`: only cross-component identity, migration, ownership, or data-flow
  invariants.
- `docs/security.md`: installer, signing, permissions, trust, and reversal behavior.
- `docs/release_verification.md`: exact commands, matrices, and release gates.
- `docs/feature_parity.md`: behavior/capability contract, not release-history or tier duplication.
- `TODO.md`: unresolved blockers only.
- `archive/release-evidence/`: dated executed results.
- GitHub Releases, optionally mirrored under `archive/releases/`: release notes and artifacts.

Do not create a current execution ledger in README or copy release versions, test counts, artifact
hashes, or completed phase history into evergreen documentation.

## Recommended execution order

1. Licensing, ownership, notices, contribution, trademark, and vulnerability policy.
2. Package metadata, product identity, stable paths, and migration.
3. Canonical support tiers and their UI/document projections.
4. Internal-alpha release manifest, notices, and unsigned release pipeline.
5. Per-user installer and upgrade/uninstall tests.
6. First-run onboarding and accessibility.
7. Mildly Useful identity validation and protected signing workflow.
8. Clean-account, security, AltGr, RDP, reversal, and supported-host qualification.
9. Final hardware, firmware, packaging, and public V1 closure.

The first implementation session should start with batches 1 through 3. They define the legal and
product contract consumed by every later installer, release, onboarding, website, support, and
content surface.
