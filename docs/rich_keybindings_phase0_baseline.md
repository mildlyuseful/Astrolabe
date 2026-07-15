# Rich keybindings Phase 0 baseline and glossary

Status: Phase 0 behavior contract

Starting commit: `d25944d`

Captured: 2026-07-13 on Windows `10.0.26200`, Python `3.11.9`, and .NET SDK `8.0.422`

This document records the behavior that later rich-keybinding phases must preserve or replace
deliberately. It is not a description of the target architecture. The target and phase gates live
in [`rich_keybindings_implementation_plan.md`](rich_keybindings_implementation_plan.md); the Windows
provider decision lives in [`spikes/windows_keyboard_input.md`](spikes/windows_keyboard_input.md).

## Untouched-code baseline

The following commands ran before any production-code or test change:

| Command | Result |
|---|---|
| `python -m pytest -q` | PASS — 402 tests in 7.32 seconds |
| `python -m compileall -q trackball_daemon tests` | PASS |
| `dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --configuration Release --no-restore` | PASS — `ALL PASS` |

The CI workflow performs the same Python compilation, Python suite, and AutoCAD NavMath runner after
an editable package install. Existing package-data assertions verify that the two shipped profile
JSON files and bundled plugins are declared. There is no established packaged executable check yet;
the first Nuitka onedir build remains release-readiness work in `TODO.md`.

After adding the Phase 0 contracts and diagnostic, the final local verification was:

| Command | Result |
|---|---|
| `python -m pytest -q` | PASS — 410 tests in 5.00 seconds |
| `python -m compileall -q trackball_daemon tests` | PASS |
| `python -m py_compile tools/windows_input_spike.py` | PASS |
| `dotnet run --project plugin_src/autocad/NavMathTests/NavMathTests.csproj --configuration Release --no-restore` | PASS — `ALL PASS` |
| `python tools/windows_input_spike.py --automated` | PASS — Raw Input and hook observed F24 down/repeat/up; reconciliation and no-focus-steal checks passed |
| `git diff --check` | PASS |

## Current behavior contracts

The Phase 0 tests are intentionally split between existing earned coverage and the missing boundary
tests in `tests/test_rich_keybindings_baseline.py`.

| Contract | Current behavior and coverage |
|---|---|
| Shift behavior | Shift is sampled through `GetAsyncKeyState` only while a valid packet is handled in 3D mode. It cannot act while the ball is stationary. The new packet-boundary test covers sampling; `test_output_bitexact.py` retains exact Orbit/Pan/Zoom numbers. |
| Pointer/3D mode | `default_mode` is read only when an `OutputEngine` is constructed. Runtime toggle state survives `apply_config`; a replacement engine re-reads the startup default. |
| App inheritance | The app scheme inherits a field only when its value is the literal `"default"`. The horizon checkbox instead inherits when the app key is absent. Both sentinel styles are frozen so Phase 2 can replace them consciously. |
| Config preservation | Malformed JSON and syntactically valid but structurally invalid JSON fall back in memory without overwriting the source file. Historical migration tests remain durable user-data tests. |
| Host baseline ownership | Host alignment is immutable, covers every supported app, and has exactly one owner: daemon for lean integrations or add-on for rich integrations. A mapping snapshot captures the selected app's baseline. |
| Focus identity | An unknown foreground process produces no active app; the settings-selected `active_app` is not a runtime focus fallback. Onshape requires a browser foreground process and a connected bridge. Focus is resolved before a packet is transformed. |
| Broker routing | `App` chooses direct SolidWorks/Onshape transports or the socket broker correctly, but `NavBroker` currently sends one accumulated frame to every connected socket client. The Phase 0 test captures this known bug; Phase 4 must replace it with target-isolation assertions. |

`general.buttons` remains verified debt, not a negative unit test: it is shipped in
`default_profiles.json`, validated by `config.py`, and rendered by `ui.py`, but controller mode has no
runtime consumer for it. Phase 2 must test that the v9 migration drops it, and Phase 6 replaces the
missing behavior with normalized device controls.

## Stable vocabulary

- **System default:** developer-owned installed value. It is immutable from ordinary user settings
  and is the final user-setting fallback before any host baseline.
- **Global:** user-owned value shared by every capable app. A missing Global override means use the
  System default.
- **Linked:** a per-app setting with no app override. Its effective value follows Global.
- **App override:** an explicit user-owned value for one app. It does not follow later Global edits.
- **Host baseline:** immutable sign/scale/coordinate correction required by a host API. It is not a
  user default and is applied exactly once.
- **Input mode:** Pointer or 3D interpretation of physical trackball motion.
- **Navigation mode:** Orbit, Fly, or Walk meaning inside 3D input, subject to app capability.
- **Action layer:** the primary or secondary interpretation within a navigation mode; Orbit's
  secondary layer is presented as Pan/Zoom.
- **Input profile:** a developer-owned base keybinding set plus sparse user overrides scoped to that
  base, initially `astrolabe_5way` or `keyboard_only`.
- **Binding context:** predicates that decide where a binding is eligible, including foreground app,
  executable, and input profile.
- **Requested state:** one identity-owned hold or latch contribution before dependency and conflict
  resolution.
- **Effective state:** the single resolved value after active requests, runtime latches, app/Global/
  System layers, capabilities, and dependencies are applied.
- **Foreground app:** the enabled supported app resolved from current OS/process/browser focus. It is
  not the app currently selected in Settings.

Avoid unqualified “default” in new APIs and docs. Where legacy code still uses General, Cube,
Cursor, or `active_app`, later migrations must distinguish the old storage name from the canonical
Global, 3D, Pointer, and selected-versus-foreground concepts.
