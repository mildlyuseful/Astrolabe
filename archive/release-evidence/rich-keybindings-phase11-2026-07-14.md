# Rich-keybindings Phase 11 release evidence — 2026-07-14

> **Archived branch-level evidence.** This records the unsigned local daemon `0.1.73` artifact and
> rich-keybindings branch closure on 2026-07-14. It is not evidence for a current or production
> release. Current gates are in [`../../docs/release_verification.md`](../../docs/release_verification.md)
> and unresolved qualification is in [`../../TODO.md`](../../TODO.md).

Phase 11 closed the rich-keybindings feature branch at branch/merge quality; it did not publish the
first production release. Final five-way hardware, clean-account installation, signing, reputation,
and production-host qualification remained explicit release work after merge.

## Recorded evidence

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
  Onshape control activation after their setup/enable gates. Phase 11 distinguished reused evidence
  from new packaged-build coverage.
- Final production five-way hardware was unavailable. Its physical matrix was
  `BLOCKED_FOR_RELEASE`, while development verification continued against automated protocol vectors
  and the XIAO bench.
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
  release impact: both remain Windows-environment qualification items and cannot be advertised as
  live-passed until exercised against the eventual release artifact.
- Packaged live host evidence is Blender, Fusion 360, SOLIDWORKS, and Onshape. SketchUp, Unreal,
  Unity, Godot, FreeCAD, AutoCAD, and Rhino were not part of the available Phase 11 live environment;
  their automated integration/camera/transport suites passed, but production host/version matrices
  remained open in `TODO.md`.
