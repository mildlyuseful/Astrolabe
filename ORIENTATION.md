# ORIENTATION — repo reorganized 2026-07-04 (TEMPORARY)

**For in-progress sessions whose context predates the reorganization.** Everything below
happened in five commits on 2026-07-04. Delete this file once all active sessions are caught up.

## 1. This is a git repo on GitHub now

- Remote: **https://github.com/mildlyuseful/Astrolabe** (private until open-sourced), branch `main`.
- Commit identity is set repo-locally (`mildlyuseful` + GitHub noreply email) — do NOT commit with a
  personal email; the repo goes public eventually.
- `.gitignore` covers `__pycache__/`, `.pytest_cache/`, `build/`, `*.egg-info/`,
  `plugin_src/**/bin|obj`, and `.claude/settings.local.json`. Don't commit caches or build output;
  the bundled `trackball_daemon/plugins/autocad/TrackballNavAcad.dll` IS tracked (shipping artifact).
- History: `255414d` snapshot → `c93b6b4` root tidy → `aa915ab` docs restructure → `ae63a8c` code
  consistency → `fcfe6d1` naming unification.
- The local folder is still `Downloads\XIAO3389` but the user may rename it to `Astrolabe` at any
  time — don't hardcode the old absolute path in anything durable.

## 2. Files that moved or were renamed

| Old | New |
|---|---|
| `XIAO3389.ino` (root) | `firmware/XIAO3389/XIAO3389.ino` (+ new `firmware/Astrolabe/Astrolabe.ino` placeholder for the future production firmware) |
| `sw_diag.py` (root) | `tools/sw_diag.py` |
| `README_daemon.md` | `README.md` (now the whole-project front door, slimmed to user-facing) |
| `docs/autocad_driver_notes.md` | `docs/apps/autocad.md` |
| `docs/blender_handoff.md` | `docs/apps/blender.md` |
| `docs/blender_nav_notes.md` | `docs/apps/blender_design.md` |
| `docs/freecad_driver_notes.md` | `docs/apps/freecad.md` |
| `docs/onshape_bridge_notes.md` | `docs/apps/onshape.md` |
| `docs/sketchup_driver_notes.md` | `docs/apps/sketchup.md` |
| `docs/solidworks_driver_notes.md` | `docs/apps/solidworks.md` |
| `docs/unreal_driver_notes.md` | `docs/apps/unreal.md` |
| `docs/spacemouse_bridge_notes.md` | `docs/spikes/spacemouse_desktop_navlib.md` |
| *(did not exist)* | `docs/apps/fusion360.md` — NEW; Fusion's gotchas now live in-repo |
| `tests/test_fusion_pointer_pivot.py` | `tests/test_fusion_cursor_pivot.py` |
| `tests/test_freecad_pointer_pivot.py` | `tests/test_freecad_cursor_pivot.py` |
| `tests/test_acad_navmath_pointer.py` | `tests/test_autocad_navmath_cursor.py` |
| `tools/freecad_pointer_probe.py` | `tools/freecad_cursor_probe.py` |

Rule going forward: per-app maintainer docs are `docs/apps/<app-key>.md`, named by the daemon's
app key (`fusion360`, `solidworks`, …). Investigation reports go in `docs/spikes/`.

## 3. The control-scheme values were RENAMED (daemon 0.1.44, CONFIG_VERSION 3)

Stored values now match the UI labels. If your context says `pointer`/`to_pointer`, it's stale:

| Pre-rename value | Current value | Meaning |
|---|---|---|
| `pointer` | **`cursor`** | orbit about the surface under the mouse cursor |
| `to_pointer` | **`to_cursor`** | zoom about it |
| `cursor` | **`selection`** | selection/bbox-fallback pivot |
| `cursor` (Blender only) | **`cursor_3d`** | Blender's 3D cursor |
| `to_cursor` (legacy to_center alias) | *retired* | migrates to `to_center` |

- Saved configs migrate automatically (`config.py::_migrate`, v2→v3; tests in
  `tests/test_config_migration.py`). Broker frames (`op`/`zm`) carry the new values.
- All six add-ons were bumped so auto-update ships the rename: **Fusion 0.1.14, Blender 0.1.10,
  FreeCAD 0.1.4, SketchUp 0.2.1, Unreal 0.2.1, AutoCAD plugin 0.3.4** (DLL rebuilt + rebundled).
  Daemon `__version__` is **0.1.44**. If you bump any of these, bump FROM these numbers.
- Internal renames ride along: Fusion/FreeCAD `_pointer_*` helpers are now `_cursor_*`,
  log labels are `cursor map:` / `cursor-pivot:`, FreeCAD's constants are `CURSOR_Y_FLIP` /
  `CURSOR_HOOK_CHECK_SEC`. Unreal's fake "3D Cursor (→ Selection)" dropdown option was removed.
- NOT renamed: 3Dconnexion navlib's own `pointer` property (Onshape docs) — external API name —
  and the AutoCAD commands `TBNAVPTR`/`TBNAVPTRTEST`.

## 4. Doc/content changes you might trip over

- `HANDOFF.md` §14 was compressed to actually-remaining work (the AutoCAD v0.1.28→0.3.0 saga lives
  in `docs/apps/autocad.md` §8.14–8.18). §15 lists the new doc paths.
- Repo docs no longer cite Claude memories; everything is in-repo.
- Personal absolute paths (`C:\Users\dylan\…`) were scrubbed from tracked files — don't reintroduce
  them; use `<repo>`-style placeholders in docs.
- `pyproject` version is dynamic from `trackball_daemon.__version__` (don't edit a version there).
- `integrations.py` / `app.py` / `__init__.py` docstrings were refreshed — trust them again.

Verification state at `fcfe6d1`: 202 pytest green (incl. the dotnet NavMath tests), all three
Blender headless probes ALL PASS, 0 broken doc links.
