# Alpha architecture cleanup process

This is the durable handoff ledger for the pre-alpha architecture branch
`codex/alpha-architecture-cleanup`. Keep it current whenever a step starts, pauses, or completes so
another agent can resume without reconstructing intent from chat history.

Status markers: `[x]` complete, `[~]` in progress, `[ ]` not started.

## [x] Step 1 — Orbit-pivot feature parity

Goal: remove non-functional pivot settings and silent placeholder behavior before building the
fallback chain.

Completed:

- `selection_overrides_pivot` works in Fusion, SolidWorks, Onshape, AutoCAD, Blender, FreeCAD,
  SketchUp, Unreal, Unity, Godot, and Rhino. `viewpoint` intentionally remains turn-in-place.
- Fusion has real origin/selection pivots. AutoCAD's primary GS path has real origin/object/selection
  pivots.
- Fusion selection reads `app.userInterface.activeSelections` and uses aggregate entity bounds with
  the documented selection point as a fallback.
- FreeCAD project bounds exclude nested local-space Part/Body features already represented by their
  transformed container Shape; nested selection bounds are transformed to world space.
- UI placeholder copy, add-in versions, bundled AutoCAD DLL, maintainer docs, and regression tests
  were updated.

Verification:

- `python -m pytest -q` — 295 passed after the Fusion/FreeCAD follow-up.
- AutoCAD Release build succeeded; `NavMathTests` passed.
- Installed FreeCAD 1.1.1 runtime probe matched the world-space center of a rotated/translated
  `App::Part` exactly.

Commits:

- `da5d7b3 feat: complete orbit pivot selection parity`
- `66bd277 fix: repair Fusion and FreeCAD pivot centers`

## [x] Step 2 — Customizable orbit-pivot fallback chain

Goal: add one global ordered fallback chain. The selected primary pivot is tried first; if it fails,
resolution restarts at the first item in the fallback chain, regardless of where the primary method
would appear in that chain. Unsupported methods are skipped by each integration. The same resolved
pivot is held for the gesture where the host requires a held raycast.

Completed:

1. Added canonical pivot method IDs, the default
   `[cursor_3d, viewpoint, object, origin]` chain, normalization/deep migration, and resolver-order
   tests. Explicit empty lists are preserved; unknowns and duplicates are removed.
2. Added the General-settings ordered-list editor with Add, Remove, Up, and Down controls.
3. The daemon expands `primary + chain-from-item-1` once and delivers it to every socket add-on;
   SolidWorks and Onshape receive the global chain directly.
4. Refactored Blender, FreeCAD, Fusion, SketchUp, Unreal, Unity, Godot, Rhino, AutoCAD, SolidWorks,
   and Onshape so unsupported/unavailable methods are skipped without host-specific hidden fallbacks.
   Exhausting the configured candidates produces no orbit frame.
5. Selection Override remains higher priority except when the selected primary is Viewpoint, which
   remains turn-in-place. Resolved pivots are held across raycast gestures.
6. Documented the chain in the README, bumped daemon/add-in versions, and rebuilt the bundled
   AutoCAD DLL.

Acceptance cases:

- Primary `cursor` hit: use it; do not consult fallbacks.
- Primary `cursor` miss with chain `[cursor_3d, viewpoint, object, origin]`: try exactly that order.
- Primary `view` miss uses the chain from its first entry, not from the entry after `view`.
- Unsupported `cursor_3d` outside Blender is skipped without becoming an implicit fallback.
- Duplicate/unknown config values are removed safely; `origin` remains the guaranteed terminal
  default unless the user explicitly removes it.
- Existing configs gain the default chain automatically without losing settings.

Verification:

- `pytest -q` — 299 passed.
- `dotnet test plugin_src/autocad/NavMathTests/NavMathTests.csproj --no-restore` — passed.
- AutoCAD 2026 Release build succeeded and copied bundled plugin v0.3.6. The existing SDK
  `WindowsBase` version-conflict warning remains; there were zero build errors.
- `python -m compileall -q trackball_daemon` — passed.
- Ruby, Godot, and Unity command-line compilers were not installed; those integrations have source
  coverage and still need normal host-GUI live verification after installation/update.

Versions:

- Daemon 0.1.60; Blender 0.1.13; FreeCAD 0.1.7; Fusion 0.1.17; SketchUp 0.2.4;
  Unreal 0.2.5; Unity 0.1.7; Godot 0.1.4; Rhino 0.1.11; AutoCAD 0.3.6.

Commit: `feat: add configurable orbit pivot fallback chain` (this Step 2 checkpoint).

## [ ] Step 3 — Global axis orientation and per-action axis routing

Goal: make physical trackball orientation global and host-independent.

- Add a global X/Y/Z permutation and per-axis inversion applied once before cursor/3D routing.
- Add a safe calibration UI that can represent a 90-degree base rotation, horizontal-axis swaps,
  and inversions.
- For every per-app action that currently has an invert checkbox, add an X/Y/Z source selector.
- Keep mode-specific semantics independent (for example, twist can drive Walk Forward without
  changing Orbit Twist).
- Add config migration, permutation validation, bit-exact default tests, and cross-app routing tests.

## [ ] Step 4 — Baked software alignment and resettable default profiles

Goal: separate developer-owned host corrections from user preferences.

- Define immutable per-software baseline axis/sign/scale profiles.
- Compose settings in this order: physical global mapping → host baseline profile → user per-app
  mapping.
- Add a Default Profile menu containing the full supported-app suite.
- Add per-app “Reset to default profile” controls that restore every relevant field atomically.
- Test profile composition, reset completeness, and old-config migration.

## [ ] Step 5 — Level horizon on fixed-horizon mode entry (issue #2)

Goal: when switching into Turntable or Walk, remove existing roll immediately rather than locking the
current tilted horizon.

- Detect transitions, not ordinary frames.
- Implement host-correct world-up leveling for every app with the mode.
- Preserve eye/target distance and the active orbit point while removing roll.
- Add transition and idempotence tests; live-verify the hosts available on the dev machine.

## [ ] Step 6 — De-generalize the 3D Apps panel

Goal: give each integration honest setup and compatibility UX.

- Add per-app metadata: supported versions, install model, setup requirement, instructions, manual
  install steps, and relevant health checks.
- Hide Re-check where it has no effect.
- Warn when detected versions are unsupported or unverified.
- Add an expandable Instructions section for every app, including “no setup required” cases.
- Include manual-install paths for restricted-permission environments.
- Test metadata completeness and state-specific actions/copy.

## [ ] Step 7 — Privilege, antivirus, and scary-warning audit

Goal: make installation/runtime security prompts predictable for companion-product users.

- Inventory every filesystem, registry, certificate, COM, localhost server, browser userscript,
  DLL/add-in load, and elevation-sensitive action.
- Identify behaviors likely to trigger SmartScreen, Defender/third-party AV, firewall, UAC, browser,
  or CAD trust prompts.
- Minimize or remove unnecessary elevation and broad trust changes.
- Add preflight explanations and recovery/manual paths before sensitive operations.
- Document signing/installer recommendations and verify packaged-build behavior.

## Handoff rules

- Work only on the first `[~]` step; do not start later steps in the same checkpoint.
- Update this file before stopping, including exact blockers, tests run, versions bumped, and commit.
- Preserve user changes and keep each completed step in its own commit.
- Host-GUI behavior that cannot be automated must be labeled “needs live verification”; never report
  source inspection as a live pass.
