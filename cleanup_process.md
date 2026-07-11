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
  SketchUp, Unreal, Unity, Godot, and Rhino. `camera` intentionally remains turn-in-place.
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
   `[cursor_3d, camera, object, origin]` chain, normalization/deep migration, and resolver-order
   tests. Explicit empty lists are preserved; unknowns and duplicates are removed.
2. Added the General-settings ordered-list editor with Add, Remove, Up, and Down controls.
3. The daemon expands `primary + chain-from-item-1` once and delivers it to every socket add-on;
   SolidWorks and Onshape receive the global chain directly.
4. Refactored Blender, FreeCAD, Fusion, SketchUp, Unreal, Unity, Godot, Rhino, AutoCAD, SolidWorks,
   and Onshape so unsupported/unavailable methods are skipped without host-specific hidden fallbacks.
   Exhausting the configured candidates produces no orbit frame.
5. Selection Override remains higher priority except when the selected primary is Camera, which
   remains turn-in-place. Resolved pivots are held across raycast gestures.
6. Documented the chain in the README, bumped daemon/add-in versions, and rebuilt the bundled
   AutoCAD DLL.

Acceptance cases:

- Primary `cursor` hit: use it; do not consult fallbacks.
- Primary `cursor` miss with chain `[cursor_3d, camera, object, origin]`: try exactly that order.
- Primary `screen_center` miss uses the chain from its first entry, not from the entry after `screen_center`.
- Unsupported `cursor_3d` outside Blender is skipped without becoming an implicit fallback.
- Duplicate/unknown config values are removed safely; `origin` remains the guaranteed terminal
  default unless the user explicitly removes it.
- Existing configs gain the default chain automatically without losing settings.

Verification:

- `pytest -q` — 303 passed after the terminology follow-up.
- `dotnet test plugin_src/autocad/NavMathTests/NavMathTests.csproj --no-restore` — passed.
- AutoCAD 2026 Release build succeeded and copied bundled plugin v0.3.7. The existing SDK
  `WindowsBase` version-conflict warning remains; there were zero build errors.
- `python -m compileall -q trackball_daemon` — passed.
- Ruby, Godot, and Unity command-line compilers were not installed; those integrations have source
  coverage and still need normal host-GUI live verification after installation/update.

Versions:

- Daemon 0.1.61; Blender 0.1.14; FreeCAD 0.1.8; Fusion 0.1.18; SketchUp 0.2.5;
  Unreal 0.2.6; Unity 0.1.8; Godot 0.1.5; Rhino 0.1.12; AutoCAD 0.3.7.

Commit: `feat: add configurable orbit pivot fallback chain` (this Step 2 checkpoint).

Terminology follow-up:

- Config v4 and every runtime now use `camera` for turn-in-place and `screen_center` exclusively
  for the first surface hit under the viewport center. Legacy `viewpoint`/`view` configs, fallback
  lists, per-mode invert groups, and `view_pivot_hold_sec` migrate automatically.
- Pivot labels are case-consistent across the UI: Default (General), Camera, Screen Center, Under
  Cursor (mouse), Selection, 3D Cursor (where supported), Model Center, and World Origin.
- Rich-panel `Selection` values now store `selection`, not `object`; Model Center remains separately
  available instead of silently losing that feature.
- Onshape Camera is explicitly tested to return the eye without invoking its Screen Center hit-test.
- AutoCAD does not expose a first-surface viewport-center pick, so Screen Center is honestly
  unsupported there and proceeds through the configured chain instead of masquerading as its view
  target.

Terminology follow-up commit: the intended `fix: disambiguate camera and screen-center pivots`
was never created — the original agent hit a token rate limit before committing. The terminology
follow-up and the pivot-capability follow-up below were therefore committed TOGETHER as
`fix: disambiguate pivots and make ray-pivot misses honest` (2026-07-11).

Pivot-capability follow-up (2026-07-11, by a second agent; same commit as above):

1. **Onshape: `camera` pivot removed.** It had been wrongly treated as supported; Onshape's view
   is orthographic, where turn-in-place (rotating about the eye) degenerates to sliding the image
   around, so the bridge now skips `camera` in any chain exactly like `cursor_3d`
   (`onshape_bridge._pivot`). A camera primary keeps its selection-override exemption (never
   hijacked by selection), matching the Fusion/SolidWorks/FreeCAD convention, whose resolvers also
   skip camera. The UI no longer offers Camera for Onshape. No config migration: a config that
   already stored onshape `orbit_pivot="camera"` keeps the raw value (combo shows it verbatim) and
   the runtime resolves through the chain — acceptable for a value that only ever shipped on this
   branch; migrate it if that assumption changes.
2. **Onshape: fabricated no-hit points no longer become pivots.** Onshape never signals a raycast
   miss — it fabricates `hit.lookat` as a point on the pick ray at roughly the scene's distance
   from the camera (user-observed: "a point below the cursor the same distance from the camera as
   the objects"), which made `screen_center`/`cursor` orbit about empty air instead of falling
   back. Two new guards in `_hit_ray`/`_valid_hit` (shared by both ray pivots): the hit must be
   essentially inside `model.extents` (margin cut from 10% of the diagonal to 0.1%), and it must
   survive a confirmation re-cast of the same ray with the origin slid `_CONFIRM_BACKOFF` (4 ×
   view half-extent) further back, agreeing within `_CONFIRM_TOL` (real surfaces are
   ray-origin-invariant; at-depth fabrications track the origin). A rejected hit makes the method
   unavailable and the configured fallback chain continues. Offline-tested only — needs a live
   pass; a fabrication computed from Onshape's OWN camera (rather than our ray origin) would be
   caught only by the bbox guard. See docs/apps/onshape.md §6/§8.6.
3. **AutoCAD: `screen_center` is now really implemented** (plugin 0.3.8, was "honestly
   unsupported"). `CaptureScreenCenterPivot` aims the cursor pivot's expanding model-space ray
   (`ExpandRayDepth`) through the view centre (GS shadow target) in a new STRICT mode:
   `EntityRayDepth(strict:true)` accepts only a real (radius-thickened) ray/AABB or curve
   intersection and drops the radius-0 bbox-centre depth synthesis that the cursor pivot keeps for
   its under-the-mouse salvage. A miss returns null and the chain continues — same contract as the
   other hosts. Depth is AABB-near-face approximate (all AutoCAD offers without firing real
   selection). The UI now offers Screen Center for AutoCAD. Needs a live feel pass (labeled in
   docs/apps/autocad.md §8.6/§10).
4. **AutoCAD: `cursor` pivot is strict too** (plugin 0.3.9). The v0.3.2–0.3.3 salvage behaviors
   (reproject a plane-only or OOB sample to view depth — "still under the cursor" but a fabricated
   pivot in empty space) were removed from `CapturePointerPivot`: a plane-only sample now runs the
   strict expanding ray (which still recovers 2D-Wireframe mid-face/near-edge via real ray/AABB
   hits), and nothing under the cursor is a MISS that continues the configured chain, aligning
   AutoCAD with every other host's ray pivots. An OOB entity sample also returns null now.
   `to_cursor` zoom misses degrade to the plain dolly as before. Needs a live re-check (the old
   behavior was live-verified WITH the salvage; labeled in docs/apps/autocad.md §8.17/§10).
5. **Godot add-on cleanup** (0.1.6): removed the dead `_walk_meshes` no-op and the selection-bbox
   gating threaded through `_screen_center_pivot`/`_cursor_pivot`/`_trace_ray`/`_mesh_aabb_ray`
   (it was null in every reachable path except the camera-primary corner, where it wrongly
   restricted scene raycasts to the selection bbox). `_selection_center` now returns just the
   centre; `_in_bbox`/`BBOX_MARGIN`/`_obj_bbox` deleted. Behavior change only in that corner.
6. **Camera turn-in-place capability notes** added to docs/apps/fusion360.md §4,
   docs/apps/solidworks.md §11 (limitations), and docs/apps/freecad.md §11: all three hosts skip
   `camera` today but are perspective-capable, so a real turn-in-place is implementable — labeled
   "candidate for future development" with the per-host mechanism sketched.

Verification (2026-07-11):

- `python -m pytest -q` — 307 passed (4 new Onshape guard/camera tests).
- `plugin_src/autocad/NavMathTests` console runner — ALL PASS. (Note: it is a plain console exe;
  `dotnet test` on it is a silent no-op — run the built exe or `dotnet run`, exit 0 = pass.)
- AutoCAD 2026 Release build succeeded, copied bundled plugin v0.3.9 + regenerated version.json.
  Same pre-existing WindowsBase MSB3277 warning, zero errors.
- Godot has no local compiler installed — 0.1.6 is source-reviewed only, needs the normal
  host-GUI live verification.
- Live verification still needed (labeled in the docs): Onshape miss-falls-through + real hits
  still land; AutoCAD Screen Center feel + strict cursor re-check.

Versions: daemon 0.1.62; AutoCAD plugin 0.3.9; Godot 0.1.6. (No other add-in versions changed.)

Cross-app pivot alignment check (cursory, code-only, 2026-07-11) — findings and resolutions:

- **Camera-pivot support is split by host tier:** Blender/SketchUp/Unreal/Unity/Godot/Rhino/
  AutoCAD implement `camera` turn-in-place; Fusion, SolidWorks, and FreeCAD skip it as unsupported
  (their resolvers' `else: # camera / cursor_3d unsupported` branch), and now Onshape too. Since
  the default global chain is `[cursor_3d, camera, object, origin]`, its first two entries are
  dead in Fusion/SolidWorks/FreeCAD/Onshape — effective default chain there is `[object, origin]`.
  Fusion, SolidWorks, and FreeCAD are perspective-capable, so camera turn-in-place is
  implementable there (capability gap, unlike Onshape where it is geometrically pointless).
  → RESOLVED as documentation (follow-up item 6): future-development notes in each host's doc.
- **AutoCAD's `cursor` pivot synthesized a depth on a true miss** (construction-plane / view-depth
  salvage — orbiting empty air at view depth instead of continuing the chain), the exact behavior
  removed from Onshape's ray pivots. → RESOLVED in plugin 0.3.9 (follow-up item 4): strict now.
- **Godot code smells** (dead `_walk_meshes`, selection-bbox gating of scene raycasts in the
  camera-primary corner). → RESOLVED in add-on 0.1.6 (follow-up item 5).
- Naming is otherwise consistent: canonical ids (`camera`/`screen_center`/`cursor`/`selection`/
  `cursor_3d`/`object`/`origin`) and the sel-override camera exemption are uniform across all
  eleven integrations; ray pivots hold per gesture everywhere.

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
