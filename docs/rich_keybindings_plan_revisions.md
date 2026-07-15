# Rich keybindings plan — review notes and proposed revisions

Status: review of `rich_keybindings_implementation_plan.md` (branch `rich-keybindings`)

This is a review of the implementation plan, not of any implementation. The plan is sound overall:
its architecture-debt inventory is accurate, the refactor-first ordering and delivery gates are the
right shape, and the sparse-override and normalized-input models fit the existing codebase. The
notes below are corrections, risks the plan understates, ambiguities that should be resolved before
the affected phase begins, and process suggestions.

## 1. Claims verified against the code

Every load-bearing claim in section 3 of the plan checks out:

- `output.py:58–63` polls `GetAsyncKeyState(0x10)` and `mapping.toggle == "shift"` is evaluated
  only inside packet handling (§3.1 is accurate).
- `general.buttons` exists in `default_profiles.json`, is validated in `config.py`, and is edited
  by `ui.py:1124`, with no runtime consumer anywhere (§3.2 is accurate).
- `CONFIG_VERSION = 8` in `config.py:273`; the plan's v8→v9 target is correct.
- `config.py:584` swallows listener exceptions with a bare `except Exception` (§3.4 is accurate).
- `App._APP_PROC_HINTS` (`app.py:230`), `integrations.py`, and `binding_schema.py` split app
  identity as described (§3.5 is accurate).
- `NavBroker` keeps one `_acc`, one `_scheme`, one rate, and broadcasts to all clients (§3.6 is
  accurate and matches the existing TODO item).
- `active_app` is used both as UI selection (`ui.py:813`) and as engine fallback
  (`output.py:199,267`), and `default_mode` stores `"cube"` (§3.7/§3.8 are accurate).

## 2. Warnings — risks the plan understates

### 2.1 The Windows low-level keyboard hook is the highest operational risk, not the BLE work

Phase 5 is rated 4/5 complexity, 5/5 feasibility. The mechanics are feasible, but the plan should
name three failure modes explicitly:

- **Silent hook removal.** Windows removes a `WH_KEYBOARD_LL` hook whose callback exceeds
  `LowLevelHooksTimeout`. In a Python process, any long GIL hold elsewhere (config save, BLE
  reconnect, Tk layout) can starve the hook thread's callback past the deadline. The callback must
  do nothing but enqueue, and Phase 5 needs a watchdog that detects removal and reinstalls the
  hook. "Hook restart" is mentioned once under release synthesis; it should be a designed
  component with a test.
- **Pressed-set reconciliation.** Because hooks can be removed or events missed mid-chord, the
  aggregator should periodically reconcile its keyboard pressed set against `GetKeyState` and
  synthesize corrections. Without this, a missed release means a permanently stuck modifier and a
  permanently forced dependent state — exactly the failure §13 says must not happen. This deserves
  a line in the architecture, not just the live-test matrix.
- **Elevated foreground windows.** A non-elevated process's LL hook does not receive input
  delivered to an elevated window. A user who presses Ctrl, focuses an elevated host, and releases
  Ctrl produces a stuck press. The plan lists "elevated hosts" only as a Phase 11 live test; the
  architecture should specify the expected behavior now — the simplest safe rule is to synthesize
  releases for all keyboard controls whenever the foreground process becomes elevated (or becomes
  unreadable), the same way provider disconnect does.

### 2.2 A global keyboard hook changes the antivirus posture of the whole project

`docs/security.md` carefully inventories every sensitive behavior because the unsigned build
already combines BLE, loopback listeners, COM, and an embedded DLL. A global keyboard hook is the
single most keylogger-shaped API on Windows and will materially worsen SmartScreen/AV
classification. Two revisions:

- Install the hook lazily: only while at least one enabled binding references a `keyboard:*`
  token. `keyboard_only` users get it; a hypothetical future device-only profile never installs a
  hook at all. This is stronger and more defensible than "documented and user-disableable."
- Phase 11 must update the runtime/installation inventory table in `docs/security.md` with a
  keyboard-hook row (what is captured, what is logged, how to disable). The plan's §13 bullet list
  covers logging policy but does not name the doc update.

### 2.3 The v8→v9 link-inference migration is a heuristic; say so and bound it

v8 materializes every value into every app profile, so "equal to the old inherited value" cannot
distinguish "user never touched it" from "user deliberately set it equal." Converting equals into
links preserves *current* effective values but changes *future* behavior: those settings now track
Global edits. Conversely, any app value that differs only because a shipped default changed in some
historical version becomes a pinned override the user never chose. Both outcomes are unavoidable
given what v8 stores. The plan should state this as an accepted tradeoff, and the migration tests
should assert both directions explicitly so the behavior is documented rather than discovered.

### 2.4 One long-lived branch conflicts with the plan's own gate structure

Seven to eleven weeks (realistically more — see §5.1) on one branch, across firmware, daemon, and
UI, guarantees painful drift against `main` and makes the "refactor-only commits preserve current
behavior" property unreviewable by the end. The delivery gates already define safe merge points.
Recommend: merge to `main` at each gate that leaves behavior unchanged or strictly fixed —
Phases 0–2 (registries + config layers, behavior-preserving by construction), Phase 4 (broker
isolation, which is an existing `TODO.md` normal-priority bug fix in its own right and has no
dependency on Phases 3 or 5), and Phase 5's provider framework behind a disabled flag. Keep only
the genuinely coupled tail (6–10) on the feature branch if desired.

## 3. Design ambiguities to resolve before the affected phase

### 3.1 Conflicting dependency closures need a stated default rule (before Phase 3)

The plan defines closure for one leaf but never says who wins when two concurrently held leaves
force contradictory prerequisites — e.g. a held `orbit.secondary` (forces `navigation.mode =
orbit`) while the user presses a hold bound to `navigation.mode = fly`. §9 Phase 3 says
"deterministic conflict rules" exist but not what they are, and the risk table flags exactly this.
Pick and write down the default now; the natural choice consistent with §4.4's precedence list is
*most recent request wins while held, releasing it re-reveals the older request*. Whatever the
choice, the Phase 3 exit tests should include the contradictory-holds case, which is currently
missing from the test matrix.

### 3.2 `restore_previous` must be identity-based, not value-based (before Phase 7)

§4.6 names a `restore_previous` operation, which reads as "capture value on press, write it back
on release." That breaks under interleaved holds: hold A sets sensitivity 0.25, hold B sets 0.5,
release A restores B's captured "previous" out from under it. §4.4's layered precedence already
implies the right model — each hold contributes an override token and release removes *that
token*, recomputing the effective value from whatever remains. The plan should say explicitly that
`restore_previous` is sugar for "remove this hold's override," never a captured-value write, and
the overlapping-holds unit test should cover two holds on the same setting.

### 3.3 `keybinding_overrides` must be keyed by base profile (before Phase 2 freezes the shape)

§5.2 requires that switching system profiles "preserves overrides belonging to the other profile
for a later switch back," but the illustrative v9 config in §4.5 shows a flat
`"keybinding_overrides": {}`. Those are incompatible; the shape needs to be
`"keybinding_overrides": {"astrolabe_5way": {...}, "keyboard_only": {...}}` (or equivalent). Fix
the example so Phase 2 doesn't ship the flat shape and force a v10.

### 3.4 First-run profile selection can't rely on the device being present

§5.3 chooses `astrolabe_5way` "when the compatible control service is detected," but on first run
the trackball is typically not yet connected — device configuration is literally step 1 of
first-time setup. Specify the fallback timing: start as `keyboard_only`, then on the first
connection that advertises the input-state characteristic, either auto-switch (if the user never
picked manually) or prompt once. Otherwise most real users permanently land on `keyboard_only`.

### 3.5 Rename the other half of the mode pair too

§3.8 migrates `cube` → `3d` but the sibling value is `cursor` (`default_mode: "cube" | "cursor"`),
while the product language in §1 is Pointer/3D. If the migration is touching this enum anyway,
alias `cursor` → `pointer` in the same pass; renaming one leg and keeping the other legacy name
just relocates the §3.8 complaint.

### 3.6 Global-page exhaustiveness vs. the compact-window requirement (before Phase 9)

§5.4 requires Global to "exhaustively include every user-tunable per-app setting." The standing UI
constraint for this project is a quarter-screen window with no scrolling, preferring sub-tabs over
stacked content. An exhaustive flat page cannot satisfy that. Phase 9 should specify that the
generated Global page renders category groups as sub-tabs (the registry's category metadata makes
this nearly free), not as one long column — even in the barebones Tk version, since the layout
skeleton is what the later visual pass inherits.

### 3.7 Minor protocol/HUD decisions worth writing down

- The 2-byte input-state sequence needs a defined wraparound comparison (serial-number
  arithmetic), and the validation list in §13 should include "sequence regression without
  wraparound" as a discard case.
- "Anchored to the current monitor" is ambiguous: foreground window's monitor or cursor's monitor?
  Recommend the foreground window's monitor, since the HUD describes what will happen to that app;
  it also matches the listed reposition-on-foreground-change trigger.
- HUD updates should be coalesced (latest-state-wins) in the queue drained on the Tk thread. A
  chord entry produces several events in a few milliseconds; rendering each intermediate state is
  wasted Tk work and visible flicker.

## 4. Product-behavior notes

### 4.1 Pass-through means every keyboard binding is also delivered to the host — surface it

Keeping global input pass-through is the right default, but its consequence should appear in the
binding editor UX, not just §2/§13: a user who binds bare `F` to a macro will also type `f` into
the focused app; a user who binds `Ctrl` as a 3D hold still primes the host's Ctrl-shortcuts. The
shipped Shift behavior already has this property so it isn't new, but the editor invites much
broader key choices. A one-line validation hint ("this key still reaches the focused app") for
non-modifier chords is cheap in Phase 9 and preempts the most predictable user bug report.

### 4.2 Keep the door open for per-app automatic Pointer/3D switching

`TODO.md` carries "automatic pointer/3D mode switching based on the foreground app" as backlog.
The Phase 3 runtime store is the natural home for that later feature; it just needs the base (non-
held) layer of `input.mode` to be resolvable per focused context rather than hardwired global.
Nothing needs to be built now, but the state-store interface shouldn't assume the base input mode
is a single global scalar, or the backlog item will require reopening Phase 3.

### 4.3 Cross-references to keep the ledgers honest

- Phase 4 resolves the existing `TODO.md` broker-isolation item; Phase 6 resolves the
  "controller-mode button-event message" backlog item. Both should be removed from `TODO.md` in
  the phase that lands them, per the repo's change discipline, and §3.2/§3.6 of the plan could
  cite them so reviewers see the plan supersedes the ledger entries.
- Phase 8's removal of Blender's local mode override and Phase 6's firmware/adapter work both
  touch bundled add-on/firmware code: HANDOFF §9's version-marker discipline applies (every marker
  for that host moves together, hosts must reload before live verification). Worth a reminder line
  in those phases, since it is the most commonly forgotten step in this repo.

## 5. Process and estimate

### 5.1 The estimate is optimistic

7–11 engineer-weeks covers the coding honestly but the plan itself demands live verification
across both keybinding profiles, five-way hardware (press/hold/disconnect/reconnect), keyboard
lifecycle edge cases (AltGr, Sticky Keys, RDP, lock, sleep), HUD DPI/monitor behavior, and rich
transitions in at least five hosts. On this project's record, live host verification routinely
surfaces baseline/threading work. 10–16 weeks is a more defensible envelope; alternatively, trim
scope by deferring export/import (Phase 7.6) and the per-binding priority UI to post-branch work —
neither gates the product goals in §1.

### 5.2 Phase 0's "test proving no runtime consumer" is the wrong instrument

You can't durably prove a negative with a unit test — a future consumer wouldn't fail it. A better
Phase 0 artifact: a short note in the migration section (already half-written in §12) plus a test
that the v9 migration *drops* `general.buttons` without behavior change. The interesting
assertion belongs to the migration, not the status quo.

### 5.3 Suggested plan-text edits, in one list

1. §4.5: key `keybinding_overrides` by profile name (see 3.3).
2. §4.6: define `restore_previous` as removal of this hold's override token (see 3.2).
3. §4.1/§9 Phase 3: state the contradictory-holds rule and add it to the exit tests (see 3.1).
4. §5.3: specify deferred/first-connection profile selection (see 3.4).
5. §3.8/§12: alias `cursor` → `pointer` alongside `cube` → `3d` (see 3.5).
6. §5.4/§9 Phase 9: render Global categories as sub-tabs to honor the compact-window constraint
   (see 3.6).
7. §9 Phase 5: add hook watchdog + `GetKeyState` reconciliation + elevated-foreground release
   synthesis as designed components (see 2.1).
8. §13/§9 Phase 11: lazy hook installation and a `docs/security.md` inventory row (see 2.2).
9. §12: state the link-inference tradeoff explicitly and test both directions (see 2.3).
10. §Intro/§14: name the gate boundaries where the branch merges back to `main` (see 2.4).
11. §6.2/§13: sequence wraparound comparison and discard rule (see 3.7).
12. §10: add unit tests for contradictory holds and for two overlapping holds on one setting.
