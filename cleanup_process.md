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

## [x] Step 3 — Global axis orientation and per-action axis routing

Goal: make physical trackball orientation global and host-independent.

Completed:

1. Config v5 adds `general.axis_orientation = {source:[0,1,2], invert:[false,false,false]}`.
   `OutputEngine` applies this physical-to-logical transform once, immediately after decoding a BLE
   packet and before both pointer and 3D routing.
2. The General UI has a permutation-safe **Physical trackball orientation** editor. Choosing an
   already-used source swaps it with the other logical axis, so a live edit never duplicates or
   drops a physical axis. Independent X/Y/Z inversions and Reset Orientation are included. A
   horizontal 90° base rotation is representable as X←Y, Y←X + invert one of those axes.
3. Every ordinary app's Orbit X/Y/Z, Pan X/Y, and Zoom controls now show a Source X/Y/Z selector
   beside Invert; the pre-existing `bindings.orbit.axis_source`, `pan.*_src`, and `zoom.src` fields
   are no longer hidden in JSON.
4. Blender, SketchUp, Unreal, Unity, and Godot now receive `advanced.axis_source` alongside
   `advanced.invert`. Every visible Orbit/Camera/Fly/Walk action selects X/Y/Z independently in the
   host add-on, where the actual navigation mode is known (including Blender's local mode override).
   Rotation actions select from `o`; shifted movement actions select from `(p.x,p.y,z)`. For example,
   Walk Forward←Z makes twist drive forward without changing Orbit or Fly.
5. Malformed global permutations reset to identity; malformed ordinary/action sources reset only
   the affected field. Action-source duplication is intentionally allowed. All defaults reproduce
   the pre-v5 fixed wiring and the original cube/cursor output bit-for-bit.
6. Updated the README, maintainer docs, migration tests, cross-app composition tests, source-routing
   tests, bundled add-in versions, and version-manifest assertions.

Acceptance/verification:

- `pytest -q` — 316 passed.
- Default cube orbit/pan/zoom and pointer/scroll golden-value tests remain exact.
- Tests cover global swap+invert before both pointer/3D paths, distinct per-app routes composed after
  the same global orientation, v4→v5 migration/validation, and twist/Z→Walk Forward with inversion.
- `dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --no-restore` — ALL PASS.
- `python -m compileall -q trackball_daemon` and `git diff --check` — passed.
- Ruby, Godot, and Unity command-line compilers are not installed. Their routing implementations are
  source-covered but need normal host-GUI live verification after the bundled updates are installed.

Versions: daemon 0.1.63; Blender 0.1.15; SketchUp 0.2.6; Unreal 0.2.7; Unity 0.1.9;
Godot 0.1.7. Other integration versions are unchanged.

Commit: `feat: add global and per-action axis routing`.

## [x] Step 4 — Baked software alignment and resettable default profiles

Goal: separate developer-owned host corrections from user preferences.

- Define immutable per-software baseline axis/sign/scale profiles.
- Compose settings in this order: physical global mapping → host baseline profile → user per-app
  mapping.
- Keep the normal per-app UI user-only and add reset controls that restore every relevant field
  atomically; developer host profiles must live outside the normal UI.
- Test profile composition, reset completeness, and old-config migration.

Completed:

1. Added developer-owned `trackball_daemon/host_profiles.json` for all 11 supported apps. It is
   strictly validated at startup and loaded into frozen `HostBaseline` values behind the immutable
   `HOST_BASELINE_PROFILES` mapping. Host factors no longer live in user config or duplicated camera
   constants.
2. Lightweight integrations receive their host alignment at the daemon's nav-output boundary.
   Blender, SketchUp, Unreal, Unity, and Godot receive `adv.host_baseline` and apply it after their
   mode-specific action routing, where Orbit/Fly/Walk meaning is known. Their local math constants
   are neutral, preventing double application. The local debug cube remains host-neutral.
3. Blender and SketchUp's inside-out `camera.roll` / `fly.bank` corrections moved into the immutable
   baseline. Saved user defaults are neutral; wire direction is baseline XOR user preference.
   Config v7 resets v6 per-app navigation values once so calibration changes no longer appear as
   user overrides. It preserves global orientation plus enable/install/startup/version state.
4. Added detached shipped user profiles for the full suite and per-app **Reset user overrides**.
   Reset covers rate, pivot hold/override, all bindings, and rich advanced settings in one
   save/notification while preserving operational state. The redundant **Shipped profiles** menu was
   removed: it only duplicated the adjacent **Editing app** selector and had no profile operation.
5. Added `docs/default_profiles.md`, updated the README and affected maintainer docs, synchronized
   every bundled add-in contract version, and rebuilt the bundled AutoCAD Release DLL/manifest.

Acceptance/verification:

- `pytest -q` — 332 passed.
- Tests cover packaged raw-profile loading/validation, registry immutability/full-suite completeness,
  exact payloads, a fully unchecked shipped user layer, v6→v7 cleanup, detached defaults, complete
  atomic reset + one notification, preservation of operational/global state, lean baseline + user
  inversion composition, rich baseline deferral, removal of the duplicate UI, and version markers.
- `python -m compileall -q trackball_daemon` and `git diff --check` — passed.
- `dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --no-restore` — ALL PASS.
- AutoCAD 2026 Release build succeeded and copied bundled plugin v0.3.10 + regenerated version.json.
  It retains the existing non-fatal .NET/AutoCAD `WindowsBase` version-conflict warning (0 errors).
- Ruby, Godot, and Unity command-line compilers are not installed. Their baseline consumers are
  source-covered but need normal host-GUI live verification, as do direction/feel checks in each app.

Versions: daemon 0.1.65; Blender 0.1.16; FreeCAD 0.1.9; Fusion 0.1.19; SketchUp 0.2.7;
Unreal 0.2.8; Unity 0.1.10; Godot 0.1.8; Rhino 0.1.13; AutoCAD 0.3.10.

Commits:

- `feat: add immutable host default profiles`
- `fix: separate host profiles from user overrides`

## [x] Step 5 — Level horizon on fixed-horizon mode entry (issue #2)

Goal: when switching into Turntable or Walk, remove existing roll immediately rather than locking the
current tilted horizon.

Implemented with a default-on General checkbox and an optional per-app override. Resetting an app's
user overrides removes its explicit value so it follows General again. With the option enabled, a
real free-to-fixed transition removes roll once; with it disabled, the existing tilt is preserved.
The first received frame establishes state and does not create a false transition.

- Detect transitions, not ordinary frames.
- Implement host-correct world-up leveling for every app with the mode.
- Preserve eye/target distance and the active orbit point while removing roll.
- Add transition and idempotence tests; live-verify the hosts available on the dev machine.

Completed:

- Added transition-only leveling to Blender, Fusion 360, FreeCAD, SketchUp, Unreal, Unity, Rhino,
  AutoCAD, SolidWorks, and Onshape. Turntable, explicit Lock horizon, and Walk count as fixed-horizon
  states where the host supports them. Godot is unchanged because its editor camera cannot roll.
- Leveling preserves the view direction, eye/target positions, distance, and orbit pivot. A
  world-up singularity is skipped instead of choosing an arbitrary horizon.
- Added General and per-app UI/config plumbing, inheritance/reset tests, broker routing coverage,
  host-neutral math tests, transition/idempotence tests, and AutoCAD's native math cases.
- Removed the temporary Step 4 calibration-value preservation tests now that host-profile transfer
  is complete. Tests derive any required host factors from `host_profiles.json`, so developer tuning
  no longer invalidates unrelated behavior tests. The developer's Blender baseline edit is kept.
- `pytest -q` — 341 passed.
- `python -m compileall -q trackball_daemon` and `git diff --check` — passed.
- `dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --no-restore` — ALL PASS.
- AutoCAD 2026 Release build succeeded and copied bundled plugin v0.3.11 + regenerated
  `version.json`; it retains the existing non-fatal `WindowsBase` version-conflict warning.
- Host-GUI behavior still needs live verification in each integration. Ruby, Godot, and Unity
  command-line compilers are not installed, so their source changes were not compiled independently.

Versions: daemon 0.1.66; Blender 0.1.17; FreeCAD 0.1.10; Fusion 0.1.20; SketchUp 0.2.8;
Unreal 0.2.9; Unity 0.1.11; Godot 0.1.8 (unchanged); Rhino 0.1.14; AutoCAD 0.3.11.

Commit:

- `feat: level horizon on fixed-mode entry`

## [x] Step 6 — De-generalize the 3D Apps panel

Goal: give each integration honest setup and compatibility UX.

- Add per-app metadata: supported versions, install model, setup requirement, instructions, manual
  install steps, and relevant health checks.
- Hide Re-check where it has no effect.
- Warn when detected versions are unsupported or unverified.
- Add an expandable Instructions section for every app, including “no setup required” cases.
- Include manual-install paths for restricted-permission environments.
- Test metadata completeness and state-specific actions/copy.

Completed:

- Extended every `AppDef` with its install model, setup requirement, first-run action, conservative
  supported-version list, automatic setup summary, manual/restricted-permissions path, and health
  check. The UI renders these fields instead of implying that every host installs the same way.
- Added normalized host-version detection for standard install paths plus cached Windows executable
  ProductVersion reads for Fusion and SOLIDWORKS. Compatibility is classified as supported,
  unverified, or known unsupported. The row shows amber caution for unverified versions and red
  warning for unsupported versions while still showing the detected executable.
- Added a downward-expandable Instructions section and Copy instructions action to every card.
  Project-local alternatives are documented for Unreal, Unity, and Godot; all other file-copy hosts
  show their exact per-user destinations. SolidWorks and Onshape explicitly say that no host add-in
  is installed.
- Removed the unused per-app Start automatically checkbox and default config field; neither had a
  runtime consumer. Retained the useful Enabled toggle. A future run-at-login option belongs at the
  daemon level, not once per host application.
- Removed placebo Re-check actions. SolidWorks exposes one initial prerequisite check and Onshape
  one initial certificate setup; after success their setup buttons disappear. Bundled add-in hosts
  retain meaningful Set up, Reinstall, and version-gated Update actions.
- Added metadata completeness, version-policy, warning-state, setup-action, instruction-copy, and
  no-op-control regression tests. Updated README and Onshape maintainer copy.
- `pytest -q` — 349 passed.
- `python -m compileall -q trackball_daemon` and `git diff --check` — passed.
- The current machine's detected host versions all classify as supported. Tk visual smoke testing
  could not run because this Python installation cannot locate `init.tcl`; verify the expanded-card
  layout in the normal packaged app before release.

Versions: daemon 0.1.67. Host add-ins unchanged (this step changes daemon metadata/UI only).

Commit:

- `feat: de-generalize 3d app setup panel`

### Step 6 follow-up — Declarative per-app binding profiles

- Replaced the separate app-specific settings renderers with one ordered binding schema. Each app
  now declares only its capabilities and option sets, so adding a shared setting does not require
  copying it into every app renderer.
- Unified the setting order and dropdown presentation. Orbit style is **Default / Free /
  Turntable**; Zoom mode is **Default / To Center / To Object / To Cursor** where supported; and
  the first word of every dropdown option is capitalized without changing its stored value.
- Added Twist action to every app profile. Ordinary integrations route it in the daemon; rich
  integrations apply it after their mode-specific routing. Turntable twist can therefore roll,
  zoom, dolly, or do nothing according to host capability.
- Added the shared Zoom mode to every rich profile and a separate Pan-mode zoom **Zoom / Dolly**
  control for Blender, Fusion, and SketchUp, where the host supports both behaviors.
- Made General settings scrollable and replaced persistent explanatory copy with hover tooltips.
  Removed host-alignment and other developer-facing language from the normal settings UI.
- Bumped daemon to 0.1.68, Blender to 0.1.18, Fusion to 0.1.21, and SketchUp to 0.2.9.
- Verification: `pytest -q` — 361 passed; `python -m compileall -q trackball_daemon tests` — passed;
  Blender 5.1 headless navigation math — 30 checks passed. Ruby is unavailable on this machine, so
  SketchUp behavior still needs its normal in-host smoke test.

Commit:

- `refactor: unify per-app binding profiles`

### Step 6 parity audit — Settings versus implemented host behavior

- Audited every field and option in `APP_BINDING_PROFILES` against its daemon, driver, or add-on
  consumer. Added `tests/test_feature_parity.py` so Model Center, To Object, zoom behavior, and
  capability-only controls cannot silently drift again.
- Corrected **Model Center / To Object** in Blender, Unreal, Unity, Godot, and Rhino: these now use
  aggregate project/model bounds instead of aliasing the current selection. SketchUp's selection
  override now affects To Cursor without replacing To Object, and its existing Pan scales with view
  distance control now switches between distance-scaled and fixed pan math.
- Completed advertised zoom-target behavior in Onshape and AutoCAD. To Object uses model/drawing
  bounds; To Cursor holds a real cursor/selection target; misses fall back to To Center. AutoCAD's
  parallel and perspective math now preserve the requested target's screen coordinate.
- Removed false or duplicate controls: configurable Screen Center hold is shown only for
  SolidWorks; Godot has no Pan-mode Zoom/Dolly selector because only dolly exists; Godot and Fusion
  no longer show duplicate Twist-action choices that enter the same runtime path.
- Added `docs/feature_parity.md` with the intentionally deferred capabilities. In particular,
  Camera orbit remains unexposed for Fusion, FreeCAD, and SolidWorks even though their APIs could
  support future implementations; manual Zoom/Dolly selection remains hidden in hosts with only
  one implemented or projection-selected path.
- Versions: daemon 0.1.69; Blender 0.1.19; SketchUp 0.2.10; Unreal 0.2.10; Unity 0.1.12;
  Godot 0.1.9; Rhino 0.1.15; AutoCAD 0.3.12. Fusion remains 0.1.21.
- Verification: `pytest -q` — 369 passed; Blender 5.1 headless navigation math — 30 passed;
  AutoCAD NavMath console suite — 56 passed; AutoCAD 2026 Release build — succeeded with the same
  pre-existing WindowsBase warning and no errors; Python compileall and `git diff --check` — passed.
  Godot, Unity, Unreal, Rhino, SketchUp, and Onshape changes still need their normal in-host smoke
  tests; no Godot executable or Ruby runtime is installed locally.

Commit:

- `fix: align app settings with runtime capabilities`

### Step 6 parity follow-up — Zoom/Dolly and universal Pivot hold

- Added native, distinct **Zoom / Dolly** selection for Unreal, Unity, Godot, Rhino, and AutoCAD.
  Zoom changes each host's viewport FOV, orthographic size, or field dimensions; Dolly moves the
  camera. Fixed zoom targets remain fixed on screen where the host API exposes that operation.
- Kept Godot **Turntable-only**. Its editor viewport cannot persist a rolled/free camera basis, so
  Free orbit remains intentionally unavailable even though Zoom/Dolly is now selectable.
- Added configurable **Pivot hold** to every app profile and transported the per-app value through
  every socket add-on, the Onshape bridge, SolidWorks driver, and AutoCAD gesture lifecycle.
- Added schema/runtime parity tests, native camera-math tests, and updated feature documentation.
- Versions: daemon 0.1.70; Blender 0.1.20; FreeCAD 0.1.11; Fusion 0.1.22;
  SketchUp 0.2.11; Unreal 0.2.11; Unity 0.1.13; Godot 0.1.10; Rhino 0.1.16;
  AutoCAD 0.3.13.
- Verification: `pytest -q` — 372 passed; Python compileall and `git diff --check` passed;
  Blender 5.1 headless navigation math — 30 passed; AutoCAD NavMath console suite passed and
  AutoCAD 2026 Release build succeeded with its pre-existing WindowsBase warning and no errors.
  Unreal, Unity, Godot, Rhino, SketchUp, FreeCAD, Fusion, Onshape, and SolidWorks changes still need
  their normal in-host smoke tests.

## [x] Step 7 — Privilege, antivirus, and scary-warning audit

Goal: make installation/runtime security prompts predictable for companion-product users.

- Inventory every filesystem, registry, certificate, COM, localhost server, browser userscript,
  DLL/add-in load, and elevation-sensitive action.
- Identify behaviors likely to trigger SmartScreen, Defender/third-party AV, firewall, UAC, browser,
  or CAD trust prompts.
- Minimize or remove unnecessary elevation and broad trust changes.
- Add preflight explanations and recovery/manual paths before sensitive operations.
- Document signing/installer recommendations and verify packaged-build behavior.

Completed:

- Added an app-by-app security and permission summary directly to every 3D Apps card and its
  copyable Instructions. Unreal, Godot, Rhino, Onshape, and AutoCAD show a concrete confirmation
  before setup changes files, project configuration, certificate state, or host trust behavior.
- Fixed a real consent boundary: SOLIDWORKS COM attachment, the Onshape TLS listener/certificate,
  and AutoCAD COM/TRUSTEDPATHS/NETLOAD are now gated by both successful setup and **Enabled**. A
  clean install performs none of those actions. The gates update live when settings change.
- Hardened Onshape's trusted loopback service: only HTTPS Onshape origins (plus its own local status
  page) may use HTTP/WebSocket endpoints, wildcard reflected CORS was removed, WebSocket keys and
  request sizes are validated, and disabling Onshape closes its listener/client.
- Kept AutoCAD's trust change narrow: exact-entry matching prevents a parent or substring path from
  satisfying/diluting the `%APPDATA%\TrackballDaemon\acad_plugin` TRUSTEDPATHS entry. `SECURELOAD`
  is never disabled and AutoCAD is never launched.
- Added `docs/security.md`: complete filesystem/registry/COM/listener/certificate inventory,
  reversal instructions, expected SmartScreen/Defender/firewall/UAC/CAD warnings, and an alpha
  release checklist covering Nuitka onedir, Authenticode/timestamping, stable publisher identity,
  hashes/SBOM, false-positive submission, and per-user `asInvoker` installation.
- Daemon version: 0.1.71. Host add-ins unchanged; all behavior changes are in daemon-side lifecycle,
  setup UI, the AutoCAD loader, SOLIDWORKS driver, and Onshape bridge.
- Packaging audit found and fixed local `__pycache__`/`.pyc` leakage from the broad add-in data
  glob. `MANIFEST.in` and setuptools exclusions now remove interpreter caches while retaining each
  host's required add-in marker and the AutoCAD DLL. A clean 0.1.71 source archive build passed.
- Windows `Get-AuthenticodeSignature` confirms the bundled AutoCAD DLL is currently `NotSigned`;
  signing it and the future daemon/installer is therefore a real alpha-release requirement, not a
  hypothetical recommendation.
- Verification: `pytest -q` — 384 passed; Python compileall and `git diff --check` passed. Nuitka
  and wheel are not installed on this machine, so the
  exact signed GUI artifact and its SmartScreen/third-party-AV reputation remain **needs live
  verification**; source-package data and cache exclusion were verified with setuptools `sdist`.

## Handoff rules

- Work only on the first `[~]` step; do not start later steps in the same checkpoint.
- Update this file before stopping, including exact blockers, tests run, versions bumped, and commit.
- Preserve user changes and keep each completed step in its own commit.
- Host-GUI behavior that cannot be automated must be labeled “needs live verification”; never report
  source inspection as a live pass.
