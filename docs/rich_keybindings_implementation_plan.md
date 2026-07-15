# Rich keybindings, input profiles, and layered settings implementation plan

Status: implementation complete (Phases 0–11 complete; production release deferred)

Target branch: `rich-keybindings`

Overall complexity: **5/5**

Overall feasibility: **4/5**

This document supersedes the earlier conversational plan for rich keybindings. It incorporates
dependent control states, keyboard and BLE-device buttons, system keybinding presets, sparse
Global-to-app inheritance, a declarative macro DSL, and a deliberately minimal text UI/HUD.

The work should be implemented as checkpointed phases on `rich-keybindings`. Refactor-only commits
must preserve current behavior before new behavior is enabled. Each completed delivery gate is a
merge candidate; merging, rebasing, or opening a PR happens only on explicit user instruction so
the implementation can remain reviewable without forcing one long-lived integration strategy.

A realistic estimate for one experienced engineer is **10–16 engineer-weeks**, including the live
Windows, firmware, hardware, packaged-build, and five-host verification required by this plan.
Export/import and a visual priority editor are post-MVP unless explicitly pulled into scope.

## 0. Execution protocol and durable handoff ledger

This section is the authority for continuing work after a context compaction or a new session. Do
not infer progress from conversation memory alone.

### 0.1 Start and stop commands

- `START PHASE N` authorizes only Phase N and its listed incidental debt fixes.
- `CONTINUE PHASE N` resumes an already-started phase from the ledger's **Next exact action**.
- `STOP AFTER CHECKPOINT` finishes the smallest currently safe substep, runs its stated focused
  verification, updates this ledger, and waits.
- Completing a phase does **not** authorize starting the next phase. After recording the checkpoint,
  stop and wait for the next `START PHASE N` command.
- The only valid first implementation command is **`START PHASE 0`**.

### 0.2 Mandatory session bootstrap

At the beginning of every implementation or continuation session:

1. Read this entire document, especially this ledger and the active phase's start/stop gate.
2. Read `HANDOFF.md`, `TODO.md`, and any files named in **Files in scope** for the active phase.
3. Run `git status --short --branch`, `git log -5 --oneline --decorate`, and inspect existing diffs.
4. Preserve unrelated user changes. Never assume an uncommitted file belongs to this feature.
5. Confirm that the prior phase's required checkpoint exists before editing a later phase.
6. Update the ledger to `IN_PROGRESS` before the first code edit.

### 0.3 Mandatory checkpoint record

Before ending a phase, pausing for compaction, or yielding because of a blocker, update the active
ledger row with:

- Status: `NOT_STARTED`, `IN_PROGRESS`, `COMPLETE`, or `BLOCKED`.
- Starting commit and latest checkpoint commit, if any.
- Completed numbered work items and the first incomplete item.
- Files changed and any user-owned files deliberately left untouched.
- Exact tests run, their results, and tests still required.
- Decisions made, rejected alternatives, migration/protocol versions, and unresolved risks.
- One **Next exact action** that can be followed without reconstructing prior reasoning.

When a phase is complete, update the relevant repository docs/TODO ledgers in the same checkpoint,
commit only the intended files, record the commit in this table, and wait.

### 0.4 Execution ledger

| Phase | Status | Checkpoint | Next exact action |
|---|---|---|---|
| 0 — Baseline contracts and Windows-input spike | COMPLETE | Start `d25944d`; checkpoint `cf9d116`; items 0.1–0.5 complete | Wait for explicit `START PHASE 1` or independently authorized `START PHASE 4`. |
| 1 — App and setting registries | COMPLETE | Start `30b1598`; registries `2183f45`; consumer cutover `0c23104`; items 1.1–1.4 complete | Wait for explicit `START PHASE 2`. |
| 2 — Sparse System→Global→app config | COMPLETE | Start `de0f42f`; fixtures `67f6710`; System defaults `1af1368`; resolver `da8e219`; store/migration `ecbeb3b`; consumer cutover `e70ea68`; items 2.1–2.5 complete | Wait for explicit `START PHASE 3`. |
| 3 — Runtime state and dependency closure | COMPLETE | Start `91e5a09`; frozen ownership `a2310e8`; serialized store `4c94301`; dependency engine `a58c652`; consumer cutover `a39e81e`; items 3.1–3.5 complete | Wait for explicit `START PHASE 4`. |
| 4 — Target-isolated navigation transport | COMPLETE | Start `43832a7`; protocol `abe757e`; implementation `23bc53e`; items 4.1–4.4 complete | Wait for explicit `START PHASE 5`. |
| 5 — Input providers and Windows keyboard | COMPLETE | Start `2aa1c9b`; provider foundation `19c9ad1`; Raw Input/foreground `e16e340`; acceptance gate `d99a0de`; boundary reconciliation `0b99a6f`; hidden-release fail-safe `ce07604`; items 5.1–5.6 complete | Wait for explicit `START PHASE 6`. |
| 6 — BLE five-way protocol and adapter | COMPLETE | Start `af373fa`; frozen baseline `07b9450`; host protocol/adapters `b7aec6d`; firmware/docs `26146d1`; hardware contract `dd9bb48`; completion `d569d31`; items 6.1–6.6 complete | Wait for explicit `START PHASE 7`. Final five-way pins and physical qualification remain Phase 11/release work. |
| 7 — Binding compiler, DSL, and system profiles | COMPLETE | Start `7a0ae3d`; start ledger `6d1b7a4`; profiles/overrides `1283cb4`; compiler/runtime `86fd22c`; items 7.1–7.5 complete | Wait for explicit `START PHASE 8`. |
| 8 — Motion/output integration | COMPLETE | Start `86d67e8`; start ledger `17cc139`; automated integration `5c3bcae`; completion `545a347`; items 8.1–8.6 and live gate complete | Wait for explicit `START PHASE 9`. |
| 9 — Barebones settings UX | COMPLETE | Start `9a5b143`; generated UI `bf3d155`; acceptance refinements `89365c0`; items 9.1–9.5 complete | Wait for explicit `START PHASE 10`. |
| 10 — Text HUD | COMPLETE | Start `714fc72`; implementation `2718601`; binding fix `adbf59e`; items 10.1–10.4 and live gate complete | Wait for explicit `START PHASE 11`. |
| 11 — Full verification and release docs | COMPLETE | Start `97ccd54`; automated/package checkpoint `3683901`; completion `6992d8b`; items 11.1–11.7 complete at branch/merge scope | Branch is ready for the user's push/merge. Production release gates remain in `TODO.md`; do not publish from this checkpoint. |

The dependencies above are stricter than numeric order where necessary. Phase 4 is an existing bug
fix and may be implemented or merged earlier, but its code must still honor the Phase 0 contracts.

### 0.5 Review dispositions that future sessions must preserve

These decisions incorporate the useful parts of `rich_keybindings_plan_revisions.md` without
treating that review as an authority:

- Accept identity-owned hold tokens, deterministic most-recent fallback, profile-keyed binding
  overrides, bounded v8 link inference, BLE serial-number arithmetic, lazy keyboard registration,
  foreground-monitor HUD placement, latest-state-wins UI coalescing, and same-phase TODO/version
  cleanup.
- Correct the review's physical-state recommendation: `GetKeyState` is message-queue state and is
  not suitable for reconciliation. Raw Input is the preferred event backend, with
  `GetAsyncKeyState` used only for lifecycle reconciliation; Phase 0 may select a low-level hook only
  with recorded evidence and the required watchdog.
- Keep `astrolabe_5way` as the installed product default because this daemon primarily exists for
  that hardware. Offer `keyboard_only` explicitly and show missing-device capability state; do not
  silently choose or switch profiles based on transient BLE detection.
- Treat delivery gates as optional merge candidates, not mandatory integration events. Git history
  operations remain user-directed.
- Keep export/import and a visual priority editor post-MVP; retain non-running DSL validation because
  it materially supports safe open-source contribution.

### 0.6 Phase 0 checkpoint

- **Status:** `COMPLETE` from starting commit `d25944d`; substantive checkpoint commit `cf9d116`.
- **Completed work:** 0.1 untouched-code baseline; 0.2 missing behavior-contract tests; 0.3 stable
  glossary; 0.4 Raw Input versus low-level-hook diagnostic; 0.5 durable backend decision/evidence.
- **Files changed:** this plan, `docs/rich_keybindings_phase0_baseline.md`,
  `docs/spikes/windows_keyboard_input.md`, `tests/test_rich_keybindings_baseline.py`, and
  `tools/windows_input_spike.py`. No production daemon, firmware, add-on, default-data, or config
  behavior changed.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Hardware-contract checkpoint:** commit `dd9bb48` records active-low/internal-pull-up inputs,
  firmware-owned debounce, non-enforced mechanical exclusivity, the jumper development gate, and
  the deferred production-hardware release gate.
- **Verification:** untouched baseline `python -m pytest -q` = 402 passed; focused contract suite =
  74 passed; final full suite = 410 passed; `compileall`, diagnostic `py_compile`, AutoCAD NavMath
  `ALL PASS`, automated F24 Raw Input/hook spike, and `git diff --check` all passed.
- **Decision:** Raw Input with `RIDEV_INPUTSINK`, one daemon-owned message-only window, lazy
  registration, enqueue-only callback, and fail-safe lifecycle release is the Phase 5 primary.
  `GetAsyncKeyState` is reconciliation-only outside callbacks. A low-level hook requires a recorded
  physical Raw Input failure plus watchdog/reinstall behavior.
- **Unresolved/manual evidence:** physical left/right modifiers, AltGr/layouts, host pass-through,
  lock/sleep/resume, RDP, and elevated/unreadable foreground behavior remain explicit Phase 5 live
  acceptance. Synthetic F24 was not treated as proof of those cases.
- **Next exact action:** stop. On `START PHASE 1`, run the full bootstrap and Phase 1 start gate. If
  the user instead chooses the independent broker fix, require `START PHASE 4` and run its gate.

### 0.7 Phase 1 checkpoint

- **Status:** `COMPLETE` from starting commit `30b1598`; registry checkpoint `2183f45`; complete
  consumer-cutover checkpoint `0c23104`.
- **Completed work:** 1.1 immutable `AppSpec` registry; 1.2 exhaustive `SettingSpec` and allowlisted
  `CommandSpec` registries; 1.3 registry-owned process/transport routing plus explicit Onshape
  disconnected, connected-background, and connected-foreground resolution; 1.4 identity,
  capability, default-value, path-coverage, command-security, and immutability tests.
- **Canonical ownership decisions:** `app_registry.APP_SPECS` is the sole code-owned app identity and
  order table. `integrations.AppDef` retains setup/detection/install behavior but references the
  exact immutable `AppSpec`; `config.HOST_PROFILE_APP_KEYS` derives from `APP_IDS`. App capability
  profiles and option restrictions live under `AppSpec`, while `settings_schema.py` owns stable
  setting/command IDs, types, validators, scope, System-default source metadata, capability
  predicates, UI metadata, and allowed operations. The temporary `binding_schema.py` facade was
  deleted after all production, test, documentation, and UI-demo consumers migrated.
- **Coverage/classification:** all 85 leaf paths in the resolved v8 app-profile superset are either
  registered user settings, capability-inactive for the specific app, or one of ten explicitly
  internal runtime parameters. `enabled`, `installed`, and `addin_version` remain operational.
  Every current Global-page path is registered except the three proven-unused `general.buttons`
  leaves, which are explicitly classified as deprecated for the tested Phase 2 v9 removal.
  Device name/address remain persistent UI settings but are not keybindable. The non-setting
  command allowlist contains state/input/navigation actions only and exposes no setup, install,
  certificate, network, shell, Python, startup, or device-identity action.
- **Behavior preserved:** executable matching remains case-insensitive substring matching of the
  same process hints; all desktop transports retain their prior route. A connected Onshape bridge
  does not select Onshape unless a registered browser process is foreground; the bridge's own
  viewport-focus signal remains the fine gate. No config version, config shape, user value,
  navigation math, UI behavior, host add-on, firmware, or daemon version changed.
- **Files changed:** new `trackball_daemon/app_registry.py` and `settings_schema.py`; registry
  consumers in `app.py`, `config.py`, `integrations.py`, `output.py`, and `ui.py`; deletion of
  `binding_schema.py`; focused registry/routing/default/parity tests; `HANDOFF.md`, feature/app
  maintainer docs, and the non-runtime Fable v2 metadata references.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Verification:** initial untouched Phase 1 focus suite = 66 passed; final focused registry and
  consumer suite = 106 passed; final `python -m pytest -q` = 424 passed. The complete
  `python -m compileall -q trackball_daemon tests`, AutoCAD NavMath `ALL PASS`, and
  `git diff --check` checks also passed.
- **Deferred boundary:** `SystemDefaultSource` records the current v8 packaged/Python source paths;
  Phase 2 must repoint them to validated `system_defaults.json` while preserving the stable IDs.
  Legacy `cube`/`cursor`, materialized profiles, inheritance sentinels, and deprecated buttons are
  deliberately unchanged until the v8→v9 migration. No live-host verification was required because
  routing semantics were preserved and covered at the packet/focus boundary.
- **Next exact action:** stop. On `START PHASE 2`, rerun the complete bootstrap, confirm this
  checkpoint, mark Phase 2 `IN_PROGRESS`, and freeze v8 fixtures for every inheritance sentinel,
  equal/different app value, malformed file, operational field, and legacy name before changing
  config storage.

### 0.8 Phase 2 checkpoint

- **Status:** `COMPLETE` from starting commit `de0f42f`; frozen-input checkpoint `67f6710`;
  System-default checkpoint `1af1368`; pure-resolver checkpoint `da8e219`; transactional v9
  store/migration checkpoint `ecbeb3b`; production consumer-cutover checkpoint `e70ea68`.
- **Completed work:** 2.1 complete validated and packaged `system_defaults.json`; 2.2 one pure sparse
  System→Global→app resolver; 2.3 typed locked transactions, atomic persistence, immutable
  copy-on-publish snapshots, structured events, logged subscriber failures, concurrency and
  reentrancy behavior; 2.4 source-aware v1–v8→v9 migration with canonical aliases and recovery;
  2.5 exhaustive resolver, empty-map, link/pin, reset, failure, and consumer-cutover tests.
- **v9 persisted shape:** `global_overrides`, complete sparse `app_overrides`, device overrides,
  isolated internal-runtime overrides, per-app operational records, bridge/Onshape infrastructure,
  `ui_state.selected_app`, selected input-profile ID, and empty override maps for the developer
  `astrolabe_5way` and `keyboard_only` binding profiles. Fresh installs persist empty user setting
  overrides; absence is the only inheritance representation.
- **System/default ownership:** stable setting IDs in `system_defaults.json` are the authoritative
  developer-owned System layer. The file exhaustively covers every registry setting and declares
  only concrete app-specific differences. `default_profiles.json` is now frozen v8 compatibility
  data used to reconstruct exact old inheritance; its `0`, `"default"`, null/missing, and
  `general.buttons` conventions must not be reused by v9 code.
- **Resolution decision:** an app override wins over a compatible Global override, which wins over
  the app-specific System value and then the global System value. If a linked Global enum value is
  unsupported by a host, that host uses its concrete app System value while remaining linked; a
  later compatible Global value therefore applies without user repair.
- **Migration decision:** historical v1–v8 upgrades run only in the store's isolated legacy
  boundary and do not publish mutable state. Migration compares each materialized value against
  the exact old inherited value: equality omits the v9 override, while difference pins it even if
  a current/future System value happens to match. Operational state, device/bridge/Onshape values,
  hidden runtime parameters, and selected app are preserved. `cube`/`cursor` become `3d`/`pointer`;
  `active_app` becomes `ui_state.selected_app`; obsolete buttons are dropped. Candidate v9 state
  is fully validated before atomic replacement; malformed/invalid source files remain untouched.
- **Transactions and reset semantics:** callbacks receive a structured event after the immutable
  snapshot is published and after the store lock is released. Listener exceptions are logged and
  do not block later listeners. Global reset removes an override. App link removes an override;
  unlink-all pins current effective values; app reset-to-System pins concrete System values and
  breaks links. Physical-axis source changes must commit as a complete permutation.
- **Consumer boundary:** `app.py`, `output.py`, `integrations.py`, and `ui.py` no longer read or
  mutate raw config dictionaries. Integration setup publishes detached operational records through
  typed transactions. Output uses one immutable snapshot per mapping build. Until Phase 9 generates
  settings controls from the registry, the old tuple-path UI adapter exists only inside
  `ConfigStore`; obsolete mouse-button controls were removed. The legacy materialized class exists
  only for historical migration and its direct regression tests.
- **Files changed:** new `config_resolver.py`, `config_store.py`, `system_defaults.py`, and
  `system_defaults.json`; packaged-data manifest and default-profile documentation; config,
  settings-schema, daemon/output/integration/UI consumers; frozen v8 fixtures; and focused plus
  existing migration, output, routing, installer, and default tests.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Verification:** frozen v8/config checkpoint = 44 passed; System-default checkpoint = 26 passed;
  resolver checkpoint = 24 passed; store/config boundary = 61 passed; final
  `python -m pytest -q` = 456 passed. `python -m compileall -q trackball_daemon tests`, AutoCAD
  NavMath `ALL PASS`, `git diff --check`, package-data assertions, and a production raw-config
  consumer scan all passed.
- **Manual verification:** none required for this storage-only phase. Tests use isolated APPDATA
  paths and no real user config was migrated. Packaged-runtime and live-host UI acceptance remains
  in the existing Phase 9/11 gates rather than risking a user's current config at this checkpoint.
- **Next exact action:** stop. On `START PHASE 3`, rerun the mandatory bootstrap, confirm this
  checkpoint and Phase 3's start gate, mark Phase 3 `IN_PROGRESS`, then freeze current tray mode
  changes and OutputEngine mode ownership before adding the runtime command/state store.

### 0.9 Phase 3 checkpoint

- **Status:** `COMPLETE` from starting commit `91e5a09`; frozen-ownership checkpoint `a2310e8`;
  serialized-store checkpoint `4c94301`; dependency/token checkpoint `a58c652`; command-consumer
  cutover checkpoint `a39e81e`.
- **Completed work:** 3.1 one serialized typed-command queue, atomic command batches, immutable
  `RuntimeSnapshot`, structured publication events, and monotonic revisions; 3.2 validated
  transitive dependency graph plus cycle/conflict rejection and identity-owned request tokens; 3.3
  exact priority→context→match-policy→chord-size→activation-serial precedence with
  reveal-older release behavior; 3.4 shared tray/settings/config/output/future-provider command
  authority; 3.5 focused-context-aware config base resolution.
- **Runtime ownership:** `RuntimeStore` is the sole authority for live input mode, navigation mode
  and layer, held requests, runtime setting overrides, and last binding event. Persistent
  `ConfigStore` values are only the context-resolved base. `SerializedCommandQueue` publishes one
  coherent snapshot after a command or batch; listeners cannot observe partial dependency closure.
  `OutputEngine.mode` is derived from the runtime snapshot, and its legacy setter/toggle methods are
  compatibility adapters that dispatch typed commands rather than owning a second mutable mode.
- **Dependency decision:** leaf requests expand to assignments whose prerequisites inherit the
  leaf token's complete precedence and activation identity. A leaf whose prerequisite loses a
  conflict is suppressed as a whole, preventing impossible combinations such as Pointer plus an
  orphaned Pan layer. App-specific tokens remain held but become inactive outside their matching
  context, then resolve again if that context returns; provider-scoped release-all cannot remove
  another provider's tokens.
- **Files changed:** new `trackball_daemon/runtime_state.py`, `trackball_daemon/commands.py`, and
  `tests/test_runtime_state.py`; runtime consumer changes in `app.py`, `output.py`, `tray.py`, and
  the temporary settings callback; focused routing/output/tray contract tests; this plan and
  `HANDOFF.md`.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Verification:** initial focused ownership baseline = 43 passed; frozen ownership checkpoint =
  21 passed; serialized-store focus = 15 passed; dependency/registry focus = 36 passed; final
  `python -m pytest -q` = 476 passed. `python -m compileall -q trackball_daemon tests`, AutoCAD
  NavMath `ALL PASS`, and `git diff --check` also passed.
- **Manual verification:** no hardware, keyboard provider, or camera test is required in this pure
  state phase. An optional source-runtime smoke remains: launch the daemon, click the tray Mode item,
  and confirm its text immediately alternates between Pointer and 3D navigation.
- **Deliberate deferrals:** real keyboard/BLE providers and stationary-input delivery begin in
  Phases 5–7. Packet-time legacy Shift sampling remains until Phase 8 replaces it with runtime layer
  consumption. Foreground context is currently refreshed at the existing packet boundary; the
  independent foreground service required for stationary keybinds lands in Phase 5.
- **Next exact action:** stop. On `START PHASE 4`, rerun the mandatory bootstrap, freeze broker
  handshake/wire versions and foreground routing, mark Phase 4 `IN_PROGRESS`, and implement target-
  isolated navigation transport without changing runtime command semantics.

### 0.10 Phase 4 checkpoint

- **Status:** `COMPLETE` from starting commit `43832a7`; frozen-protocol checkpoint `abe757e`;
  target-isolation implementation checkpoint `23bc53e`.
- **Completed work:** 4.1 immutable target/revision navigation envelope, one shared router boundary,
  and per-target broker accumulator, refresh period, scheme/profile revision, state revision, and
  delivery diagnostics; 4.2 hello-app-matched delivery with deterministic focus-switch discard and
  no delta relabeling; 4.3 SolidWorks and Onshape adapters with unchanged camera math and explicit
  connected-background Onshape no-target behavior; 4.4 multiple/simultaneous client, rapid focus,
  reconnect, stale/new revision, profile change, dead client, and no-target regressions plus resolved
  TODO cleanup.
- **Routing decision:** the daemon discards pending old-target deltas atomically on every target
  change and starts the new target clean. It does not finish the old accumulator because delivery
  after foreground ownership changed can move a background viewport. Samples captured for an
  inactive target and stale state revisions are rejected. A newer runtime or profile revision
  discards deltas interpreted under the prior state before accepting new motion.
- **Protocol/version decision:** target ownership is entirely server-side and uses the existing
  hello `app`. Socket hello and frame shapes, the Onshape WAMP/NL-Proxy contract, SolidWorks COM
  behavior, all camera calculations, and all bundled add-on sources are unchanged. Therefore no
  consumer or add-on version marker changed. The frozen inventory and resulting contract are in
  `docs/rich_keybindings_phase4_protocol.md`.
- **Files changed:** new `navigation_router.py`; target-aware `navbroker.py`; daemon packet-boundary
  routing in `app.py`; discard adapters in `solidworks_driver.py` and `onshape_bridge.py`; transport,
  routing, direct-driver, Blender wiring, and frozen-baseline tests; `TODO.md`, `HANDOFF.md`, the
  protocol record, and this ledger. `output.py` and plugin handshake sources were inspected and did
  not require changes.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Verification:** frozen protocol/routing baseline = 61 passed; broker/router focused checkpoint =
  48 passed; transport/routing/direct camera focused suite = 166 passed; final
  `python -m pytest -q` = 502 passed. `python -m compileall -q trackball_daemon tests`, AutoCAD
  NavMath `ALL PASS`, and `git diff --check` also passed.
- **Manual verification:** no live host action is required at this internal transport gate. A
  two-host foreground/background smoke is useful when convenient but remains part of Phase 11's
  recorded live-host matrix; it must not be represented as completed here.
- **Next exact action:** stop. On `START PHASE 5`, rerun the mandatory bootstrap, verify Phases 0 and
  3 are complete, reread the Phase 0 Windows-input decision, mark Phase 5 `IN_PROGRESS`, and begin
  the provider/aggregator contract without starting Phase 6.

### 0.11 Phase 5 complete checkpoint

- **Status:** `COMPLETE` from starting commit `2aa1c9b`; normalized provider foundation
  `19c9ad1`; Raw Input/foreground lifecycle `e16e340`; physical acceptance checkpoint `d99a0de`;
  foreground reconciliation `0b99a6f`; hidden-release fail-safe `ce07604`.
- **Completed work:** 5.1 immutable `InputEvent`, `InputControlDescriptor`, provider health/status,
  provider lifecycle interface, and atomic cross-provider pressed-set aggregation; 5.2 lazy
  message-only Raw Input receiver using `RIDEV_INPUTSINK` without `RIDEV_NOLEGACY`, a bounded
  enqueue-only native callback path, and daemon startup/shutdown ownership; 5.3 generic and
  left/right modifier descriptors, configured-control filtering, repeat-edge rejection, known
  internal injection-marker filtering, and pass-through registration; 5.4 atomic release on profile
  reload/disable, failure, lock, suspend, unregister, and shutdown plus input-desktop-gated
  `GetAsyncKeyState` reconciliation on resume/restart and release-only polling while a configured
  control is believed held; 5.5 an independent foreground monitor routed through the same
  runtime/app context boundary as BLE packets; 5.6 security/runtime inventory and a
  production-provider acceptance tool.
- **Backend decision preserved:** Raw Input remains primary. No physical failure has been observed,
  so the documented low-level-hook fallback is not authorized. Raw Input has no universal injected
  provenance flag; the initial command surface does not inject keyboard input, while explicit known
  `ExtraInformation` markers can be filtered without claiming arbitrary injection detection.
- **Files changed:** new `trackball_daemon/input/` model, aggregator, and Windows provider;
  `app.py` lifecycle wiring; `winfocus.py` monitor; focused provider/foreground/security tests;
  `tools/windows_input_acceptance.py`; `docs/security.md`; `HANDOFF.md`; and this ledger.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Verification:** untouched Phase 5 baseline `python -m pytest -q` = 502 passed; final
  `python -m pytest -q` = 532 passed; `python -m compileall -q trackball_daemon tests
  tools/windows_input_acceptance.py` passed; AutoCAD NavMath Release runner = `ALL PASS`; native
  message-window registration/unregistration passed; and `git diff --check` passed. Final approved
  automated F24 acceptance produced one background `input_sink` press, repeat suppression, one
  `input_sink` release, unchanged foreground, and no held shutdown state. Sandboxed SendInput
  attempts returned WinError 5 and are not counted as backend evidence.
- **Manual evidence recorded:** with ordinary Notepad foreground, the production provider observed
  left Ctrl, left/right Shift, left/right Alt, `Shift+A`, and F12 press/release edges through
  `input_sink`; `A` still reached Notepad, foreground was unchanged, and shutdown left no pressed
  controls. Right Ctrl could not be exercised because the test keyboard has no right Ctrl key; its
  E0 normalization remains covered by the pure provider test. With Administrator/elevated Notepad
  foreground, the non-elevated receiver observed no transitions while `A` still passed through;
  this confirms the documented integrity limitation rather than a reason to replace Raw Input.
- **First boundary result:** the ordinary Ctrl press arrived through `input_sink`, but switching
  between ordinary and elevated Notepad did not publish a foreground change because both processes
  have the same executable name. The hidden release was therefore synthesized only by
  `acceptance_complete`; this does not satisfy the live-session fail-safe gate. The provider now
  performs a low-rate `GetAsyncKeyState` reconciliation only while configured controls are believed
  held, allowing a hidden release to close without relying on executable-name changes. The rerun
  acceptance criterion was therefore `held_state_poll` or `foreground_change`—never
  `acceptance_complete`—as the release reason.
- **Final boundary result:** the rerun produced a physical `ctrl.left` `input_sink` press in ordinary
  Notepad followed by a synthetic `held_state_poll` release while elevated Notepad remained
  foreground. `final_pressed` was empty and the provider stopped cleanly. This closes the live
  access-boundary gate without installing a low-level hook.
- **Stop gate:** satisfied. Background pass-through, documented elevation behavior, modifier-side
  normalization, repeat rejection, lazy lifecycle, loss reconciliation, and fail-safe release are
  covered. No raw-key logging was introduced.
- **Next exact action:** stop. On explicit `START PHASE 6`, rerun the mandatory bootstrap, confirm
  Phase 5 is `COMPLETE`, and begin the BLE five-way protocol and adapter work without starting
  Phase 7.

### 0.12 Phase 6 completion checkpoint

- **Status:** `COMPLETE` from starting commit `af373fa`; protocol and hardware baseline frozen in
  `docs/rich_keybindings_phase6_protocol.md`.
- **Completed work:** mandatory bootstrap; Phase 5 dependency confirmation; 532-test untouched
  baseline; inventory of GATT UUIDs, byte-exact rotation packets, BLE transport ownership,
  controller/HID transitions, three-button test-bench pins and debounce, daemon-absent fallback,
  production placeholder state, and package-discovery debt; 6.1 generic multi-characteristic
  transport plus data-descriptor adapter selection; 6.2 protocol codec and sequence gate; host side
  of 6.4 legacy/five-way adapters, normalized motion, snapshot diffing, and disconnect release.
- **Firmware and extensibility work:** 6.3 preserves the fixed rotation characteristic and adds the
  separate v1 input-state characteristic to the working XIAO3389 firmware; 6.5 leaves controller
  ownership tied to rotation subscription, retains HID suppression/release and daemon-absent HID,
  and publishes debounced three-button snapshots; 6.6 documents the validated data-only descriptor
  registration boundary and rejects executable descriptor fields. The production Astrolabe sketch
  remains an honest placeholder because final five-way pins and debounce timing do not yet exist.
  The confirmed active-low/internal-pull-up electrical contract is captured without inventing the
  unfinished mapping.
- **Compatibility decisions:** keep the service and 12-byte rotation characteristic unchanged;
  reserve additive input characteristic `2cad0003-6e64-0146-b139-9cf2a4cd57fc`; use protocol v1,
  snapshot kind 1, little-endian unsigned 16-bit serial arithmetic, and descriptor-owned bit
  meanings. Do not invent production five-way pins absent from repository hardware documentation.
- **User-owned files left untouched:** `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and were not staged.
- **Files changed after the baseline:** new `trackball_daemon/devices/` models, descriptor loader,
  packet protocol, snapshot provider, adapters, registry, transport, and built-in descriptor data;
  `ble.py` compatibility facade; App wiring; subpackage/data packaging; XIAO3389 additive firmware
  publisher and production-placeholder marker; BLE adapter/security/user docs; acceptance tool;
  `TODO.md`; and focused tests.
- **Verification:** untouched `python -m pytest -q` = 532 passed; descriptor/provider/transport
  focus = 63 passed; firmware/protocol focus = 24 passed; hardware-contract focus = 30 passed;
  current full suite = 562 passed;
  compileall and `git diff --check` passed.
  Setuptools discovery now includes `trackball_daemon`, `trackball_daemon.input`, and
  `trackball_daemon.devices`. The sketch compiled with the installed Arduino CLI, Seeed non-mbed
  nRF52 1.1.12 core, and `Seeeduino:nrf52:xiaonRF52840` target: 137384 bytes program storage and
  16160 bytes global data.
- **Live gate:** passed on the XIAO3389 test bench. The acceptance run received 4635 motion
  notifications; repeatedly decoded D0 Left and D1 Right press/release snapshots; accepted a reset
  sequence baseline after each reconnect; emitted a synthetic D0 release when disconnected while
  held; re-established input after two reconnects; and ended with no pressed controls. With all
  daemon/test clients stopped, normal Windows Bluetooth HID motion and D0 left click both worked.
- **Expected pre-integration behavior:** when the daemon subscribes, firmware controller mode
  deliberately suppresses every HID report. Phase 6 normalizes the button but does not yet map it
  back to an OS click, so D0 does not click in daemon Pointer mode at this checkpoint. Phase 7 must
  compile a bounded momentary pointer-button action and Phase 8 must inject/release it, including
  disconnect/reload/shutdown fail-safes. This is tracked work, not a firmware fallback failure.
- **Adjacent lifecycle debt observed live:** two daemon instances (installed and source-tree)
  could subscribe concurrently. Phase 8 must enforce single-daemon ownership before output
  integration so competing clients cannot leave firmware controller ownership ambiguous.
- **Deferred production gate:** the final switch is active-low with internal pull-ups and firmware
  debounce. Its mechanism ordinarily allows one direction, but protocol/host code deliberately
  accepts simultaneous declared bits as a contingency. Final pins, debounce tuning, and the real
  Up/Down/Left/Right/Center physical matrix remain an explicit Phase 11/release-readiness blocker;
  the jumper result must not be represented as final-hardware qualification.
- **Stop gate:** satisfied for the agreed test-bench development boundary. Automated protocol and
  provider vectors, live motion/input/reconnect/held-disconnect behavior, compile/flash, and
  daemon-absent HID fallback all passed. Production switch qualification remains explicitly
  deferred and cannot be described as passed.
- **Next exact action:** stop. On explicit `START PHASE 7`, rerun the mandatory bootstrap, confirm
  Phase 6 is `COMPLETE`, and implement the binding compiler/DSL/profile work including the bounded
  momentary pointer-button action. Do not start Phase 8.

### 0.13 Phase 7 in-progress checkpoint

- **Status:** `IN_PROGRESS` from starting commit `7a0ae3d`.
- **Bootstrap:** complete plan/HANDOFF/TODO reread; Phase 2/3/5/6 dependency confirmation; clean
  tracked worktree; user-owned `.claude/` and `docs/rich_keybindings_plan_revisions.md` remain
  untracked and untouched.
- **Untouched baseline:** `python -m pytest -q` = 562 passed.
- **Boundary decision:** reuse Phase 3's runtime request/dependency/precedence authority and Phase
  5/6's normalized provider aggregation. Phase 7 owns declarative profile composition, validation,
  chord/context matching, activation identity, macro compilation, diagnostics, and provider
  configuration; it does not add Phase 8 motion or SendInput delivery.
- **Next exact action:** implement and validate 7.1 `system_keybinding_profiles.json`, immutable
  profile models/loading, package-data coverage, and profile-keyed sparse overrides. Then checkpoint
  before the chord state machine.

### 0.14 Phase 7 completion checkpoint

- **Status:** `COMPLETE` from starting commit `7a0ae3d`; start ledger `6d1b7a4`, packaged profiles
  and profile-scoped overrides `1283cb4`, compiler/runtime/validator `86fd22c`.
- **Completed:** 7.1–7.5. The daemon now composes immutable `astrolabe_5way` and `keyboard_only`
  profiles with profile-keyed sparse overrides; compiles indexed normalized chords and app/process
  contexts; supports exact/permissive modifier matching, generic physical modifiers, cross-provider
  chords, hold/toggle edges, dependency-owned state requests, explicit press/release lists, the
  allowlisted setting DSL, bounded identity-owned pointer-button intents, diagnostics, lazy provider
  configuration, and release-before-reload/shutdown behavior.
- **Safety decisions:** Phase 3 remains the only dependency/precedence authority. Exact hardware
  bindings reject unexpected simultaneous controls from the same device source. Pointer actions
  terminate at an inert ownership sink in Phase 7; Phase 8 must supply bounded SendInput delivery.
  Persistent setting lists use one ConfigStore transaction and cannot mix with runtime mutations in
  the same atomic list. Malformed editor/import rows are disabled individually with diagnostics;
  persisted transactions and developer profiles remain strict.
- **Verification:** untouched baseline was 562 tests. Final `python -m pytest -q` passed 588 tests;
  `python -m trackball_daemon.validate_bindings` reported both packaged profiles with zero
  diagnostics; `python -m compileall -q trackball_daemon tests` and `git diff --check` passed.
- **Files:** added/changed `system_keybinding_profiles.json`, `input/bindings.py`,
  `input/macros.py`, `validate_bindings.py`, config/settings/command/app integration, package data,
  and focused tests. User-owned `.claude/` and `docs/rich_keybindings_plan_revisions.md` remain
  untracked and untouched.
- **Deferred exactly as planned:** OS pointer-button delivery, motion/output integration, and
  single-daemon enforcement remain Phase 8; settings UX remains Phase 9; HUD remains Phase 10.
- **Next exact action:** stop. On explicit `START PHASE 8`, rerun the mandatory bootstrap and
  implement Phase 8 only. Do not infer Phase 8 authorization from Phase 7 completion.

### 0.15 Phase 8 in-progress checkpoint

- **Status:** `COMPLETE` from starting commit `86d67e8`; start-ledger commit `17cc139`;
  automated integration checkpoint `5c3bcae`; pre-acceptance ledger `c4a5987`; completion
  checkpoint `545a347`. Work items 8.1–8.6 and the live Windows/XIAO stop gate are complete.
- **Bootstrap:** complete plan/HANDOFF/TODO and Phase 8 scope reread; Phase 4/7 dependency
  confirmation; clean tracked worktree; user-owned `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and untouched.
- **Untouched baseline:** bit-exact output plus frozen legacy Shift contracts = 21 passed; full
  `python -m pytest -q` = 588 passed.
- **Boundary decision:** retain all quaternion, cursor, host-baseline, and navigation transport math.
  Packet-time `shift_held()` is gone; each sample consumes one immutable runtime snapshot for
  Pointer/3D, primary/secondary, focused mapping, settings, and target/revision routing. Windows
  injection is isolated in `windows_pointer.py`; pointer buttons remain bounded
  Left/Right/Middle/X1/X2, identity-owned, and fail visibly if `SendInput` rejects an edge.
- **Implemented:** stationary runtime settings and mode profiles publish to only the focused target;
  supported navigation modes are part of the runtime base so a held Fly/Walk request becomes inert
  in an Orbit-only app and safely resumes in a compatible app. Existing profile data supplies Shift
  Pan/Zoom and XIAO3389 Left/Right/Middle. A named process-lifetime mutex is acquired before `App`
  construction, so a second installed/source process cannot start BLE or output. Blender add-on
  0.1.23 consumes only daemon-authoritative `adv.nav_mode`; its former Alt+backtick operator, keymap, menu,
  and local override are removed. Stale Blender probes were updated to the target-isolated broker
  API and canonical pivot vocabulary while their contract was already in scope.
- **Files:** changed `output.py`, `app.py`, `runtime_state.py`, `input/bindings.py`, `__main__.py`,
  Blender add-on/version/docs/probes, and focused tests; added `windows_pointer.py`,
  `instance_lock.py`, and their tests. User-owned `.claude/` and
  `docs/rich_keybindings_plan_revisions.md` remain untracked and untouched.
- **Automated verification:** `python -m pytest -q` = 603 passed; both system binding profiles
  validate with zero diagnostics; `compileall` and `git diff --check` pass. Blender 5.1.1 headless
  math probe = 30/30, integration probe = 23/23, and socket probe = 4/4; its hello reported add-on
  0.1.23. Tests cover stationary mode/settings changes, one-snapshot packet ownership, bit-exact
  output, unsupported-mode focus transitions, BLE disconnect/reload/shutdown release ownership,
  rejected button injection, and duplicate rejection before `App` construction.
- **Manual stop gate:** passed on Windows with the physical XIAO3389 test-bench hardware on
  2026-07-14. D0/D1/D2 produced the expected conventional Pointer-mode clicks; held-button
  disconnect/quit release, duplicate-daemon rejection without disrupting the owner, and the listed
  Ctrl/Shift dependency/order checks all worked as expected.
- **Completion cleanup:** the delivered output/singleton work is documented in `HANDOFF.md`; its two
  Phase 8 backlog entries are removed from `TODO.md`. Final production five-way switch pins and
  physical qualification remain explicitly deferred to Phase 11/release work.
- **Next exact action:** stop. Wait for explicit `START PHASE 9`; do not infer Phase 9 authorization
  from Phase 8 completion.

### 0.16 Phase 9 completion checkpoint

- **Status:** `COMPLETE` from starting commit `9a5b143`; substantive generated-UI checkpoint
  `bf3d155`; acceptance-refinement checkpoint `89365c0`. Work items 9.1–9.5 and the Phase 9 stop
  gate are complete.
- **Bootstrap and baseline:** the complete plan/HANDOFF/TODO and Phase 9 scope were reread; Phases
  2 and 7 were confirmed complete; the untouched full suite passed 603 tests. User-owned
  `.claude/` and `docs/rich_keybindings_plan_revisions.md` remain untracked and untouched.
- **Architecture:** `SettingsUIModel` projects the exhaustive registry and owns only typed
  Global/device/app transactions. Immutable config snapshots expose override-presence sets without
  exposing mutable store state. `BindingUIModel` composes, validates, and stores sparse
  profile-scoped declarative patches independently of Tk. Every shipped binding projects into the
  plain-language editor; unusual activation, action, context, release, or priority structures stay
  losslessly available through Advanced DSL.
- **Global/app UX:** General is now Global and generated by category. System-default markers,
  individual/global reset, linked effective values, edit-to-unlink, link toggles, setting reset,
  Link all/Break all, and red Reset app implement section 5 exactly. Category selection survives
  refresh, and blank-space clicks commit a pending entry. The device-wide physical permutation and
  inversion are isolated in a Physical transform category; per-action routing remains separate.
- **Binding UX:** the selected System input profile exposes capability health, normalized chord
  recording across keyboard/BLE controls, exact/extra-modifier behavior, one plain-language app
  context, common control actions, and registry-generated setting/behavior/value choices. The
  simple editor generates hold restoration or press-edge toggle behavior and hides release lists,
  executable/profile contexts, low-level activation, IDs, and priority. Advanced DSL retains the
  complete validated surface. Ordinary-key pass-through remains explicit.
- **Incidental debt:** the dead `navigation.legacy_layer_toggle` setting was removed from the
  registry/System defaults and safely stripped from existing v9 files because Phase 8 moved this
  authority into binding profile data. Existing compatibility profiles retain only their inert
  classified field for v8 materialization.
- **Verification:** final `python -m pytest -q` = 620 passed with the Windows-only Tk smoke skipped
  in the sandbox; the same Tk smoke passed against the real Windows desktop. Both packaged binding
  profiles validate with zero diagnostics; `compileall` and `git diff --check` pass. Pure tests
  cover every reset/link/bulk transition, unsupported capabilities, permutation-safe transforms,
  sparse binding edits, simple hold/toggle generation, advanced-only preservation, profile
  switching, capability status, and pass-through warnings. Tk covers generated tabs, blank-space
  commit, category preservation, linked refresh, the Physical transform category, and the absence
  of raw DSL fields from the simple editor.
- **Manual acceptance:** the user reported the original Phase 9 flow working, then identified entry
  commit/category-reset friction, unclear physical-transform placement, and an overexposed binding
  editor. Those findings were incorporated in `89365c0` rather than deferred into Phase 10.
- **Next exact action:** stop. Wait for explicit `START PHASE 10`; do not infer Phase 10
  authorization from Phase 9 completion.

### 0.17 Phase 10 completion checkpoint

- **Status:** `COMPLETE` from starting commit `714fc72`; implementation checkpoint `2718601`;
  acceptance-fix checkpoint `adbf59e`. Work items 10.1–10.4 and the Phase 10 stop gate are complete.
- **Bootstrap and baseline:** the complete plan/HANDOFF/TODO and Phase 10 scope were reread; Phase 8
  was confirmed complete; the untouched full suite passed 620 tests with the Tk smoke skipped.
  User-owned `.claude/` and `docs/rich_keybindings_plan_revisions.md` remain untracked and
  untouched.
- **Semantic boundary:** immutable runtime snapshots now carry renderer-independent primary,
  secondary, and current control help plus physical held-binding records. Binding activity covers
  holds, toggles, pointer buttons, and persistent-only macros, so the HUD does not inspect input
  providers, output packets, or binding-controller internals. Request-token holds remain a safe
  fallback for direct command callers.
- **Projection and queueing:** `control_hud.py` owns a pure snapshot-to-four-lines projection,
  held-over-last precedence with a monotonic timeout, capacity-one latest-revision mailboxes for
  runtime and config snapshots, and pure bottom-right work-area geometry. Burst and concurrent
  publisher tests prove bounded coalescing to the newest revision.
- **Window and settings:** the plain-label Tk `Toplevel` is created and drained only on the Tk
  thread. Windows styles add tool-window/no-activate and optional click-through/topmost behavior;
  absolute Win32 placement follows the foreground-window work area and polls foreground, monitor,
  work-area, and DPI identity, with cursor then primary fallback. Global settings own visibility,
  topmost, click-through, opacity, margin, and last-binding timeout; the tray has a checked
  visibility action. HUD-only config changes do not release bindings or rebuild transports.
- **Acceptance-found defect:** a saved keybinding patch was persisted, but `App` passed its deeply
  immutable config snapshot directly to a composer that used `copy.deepcopy` on nested
  `mappingproxy` values. The subscriber exception was visible in `daemon.log` but did not roll back
  the valid save, leaving the running compiler stale. Composition now explicitly detaches frozen
  containers, and an App-level regression test proves save → immutable snapshot → live compiler →
  provider registration → stationary activation. The tray's duplicate Pointer/3D action is
  removed; mode status belongs to the HUD and mode control belongs to declarative bindings.
- **Automated verification:** the post-fix suite passes 628 tests with two Tk skips;
  one is the pre-existing settings smoke and one is the new HUD smoke. The shell Python install has
  no usable Tcl/Tk runtime, so native window behavior cannot be honestly closed from this sandbox.
  Pure projection, held/last timing, semantic help, bounded/concurrent coalescing, geometry,
  settings/default coverage, runtime/compiler regressions, `py_compile`, and `git diff --check`
  pass.
- **Manual stop gate:** the user reported the full HUD matrix passing: stationary keyboard/BLE
  updates, held/last timing, visibility/options, focus retention, work-area/monitor/DPI placement,
  and click-through all behaved as expected. That pass also exposed the independent live binding
  recompilation defect above. After the fix, a saved keyboard-profile override applied normally,
  the duplicate tray mode item was absent, and Pointer/3D activation worked in Blender and Fusion.
  Fusion's initially missing delivery resolved when it became the foreground target. SolidWorks
  and Onshape were correctly diagnosed as explicitly uninstalled/disabled rather than binding
  failures; after their 3D Apps setup/enable gates were completed, the user reported every tested
  host working.
- **Next exact action:** stop. Wait for explicit `START PHASE 11`; do not infer Phase 11
  authorization from Phase 10 completion. Do not push, merge, publish, or open a PR without a
  separate command.

### 0.18 Phase 11 in-progress checkpoint and release checklist

- **Status:** `IN_PROGRESS` from starting commit `97ccd54`. Phases 0–10 and every documented prior
  stop gate are complete. The user explicitly issued `START PHASE 11`.
- **Workspace boundary:** `.claude/` and `docs/rich_keybindings_plan_revisions.md` are untracked,
  user-owned, and must remain untouched and uncommitted. Phase 11 is limited to verification,
  release hardening/docs, schemas/examples, packaging/version metadata, and defects discovered by
  verification; it must not grow new product features.
- **Automated release checklist:** full pytest suite;
  migration/rollback/recovery fixtures; config corruption preservation; package-data and installed-
  resource checks; binding validator; Python compilation; AutoCAD NavMath; static diff/manifest and
  security-posture checks; sdist/wheel build and isolated installed smoke; Nuitka onedir build and
  packaged resource/config/validator smoke. The shell environment initially had neither `build` nor
  `nuitka` installed, so their installation/build path was resolved rather than skipped.
- **Contributor-contract checklist:** publish machine-readable JSON schemas and validated examples
  for input profiles/bindings, declarative macros, and BLE device descriptors. Keep executable
  adapter code outside the data-only descriptor contract and document version/compatibility rules.
- **Documentation/security checklist:** reconcile README, HANDOFF, TODO, security, BLE protocol,
  defaults, host, and packaging docs with actual System defaults → Global → app resolution; both
  shipped input profiles; lazy/pass-through Raw Input and no raw-key logging; elevated/input-desktop
  fail-safe; BLE trust boundary; bounded declarative DSL; third-party adapter trust; reversal,
  rollback, and compatibility behavior. Reconcile package metadata and every changed firmware or
  add-on version marker, and retain export/import plus visual binding priority as post-MVP work.
- **Manual keyboard checklist:** layouts and AltGr; Sticky Keys; repeat rejection; rapid chord order;
  sleep/resume; session lock/unlock; Remote Desktop; elevated foreground; live profile reload; and
  shutdown while held. Record unavailable environments with reason and release impact. Continue to
  call the backend Windows Raw Input, not a keyboard hook.
- **Manual firmware checklist:** reconnect; missed, duplicate, stale/out-of-order, and wrapping
  sequences; disconnect while held; debounce; simultaneous five-way contingency; legacy rotation;
  and daemon-absent HID. Automated protocol vectors and the XIAO jumper bench may support this gate,
  but the unavailable final Up/Down/Left/Right/Center hardware, production pin map, and production
  debounce tuning remain an explicit release-qualification blocker and cannot be called passed.
- **Manual host checklist:** Pointer/3D; cascading Shift Pan and explicit Ctrl+Shift in both orders;
  Orbit/Fly/Walk; sensitivity holds; foreground switches while held; unknown-app/global behavior;
  Onshape connected-but-unfocused behavior; and background target isolation. Exercise Blender,
  SketchUp, Unreal, Unity, Godot, Onshape, and other installed supported hosts; unavailable hosts
  must name the reason and release impact. Phase 10 already established current Blender, Fusion,
  SOLIDWORKS, and Onshape setup/focus behavior, but Phase 11 will record which coverage is reused
  versus newly exercised.
- **Manual HUD/UI checklist:** multiple monitors, mixed DPI, work-area/taskbar changes, no activation
  or focus steal, click-through, held/last text, and live config updates; exhaustive Global categories;
  per-setting and page-level link/unlink/reset; edit commit behavior; and subwindow preservation.
- **Distribution checklist:** exercise the exact onedir artifact; clean-user install/run/uninstall and
  reversal; asInvoker/UAC, firewall, SmartScreen, AutoCAD/SketchUp trust, Onshape certificate, and AV
  observations; signing/timestamp, SHA-256, source revision, dependency lock/SBOM, supported-host
  matrix, and scan evidence. Items requiring a production signing identity, clean account, final
  hardware, or unavailable commercial hosts are release gates, not reasons to falsify a pass.
- **Automated/package checkpoint:** the untouched baseline passed `628 passed, 2 skipped`, Python
  compilation, both System profile validations, AutoCAD NavMath `ALL PASS`, and `git diff --check`.
  The hardened tree passes `637 passed, 2 skipped`; 88 focused migration/corruption, BLE trust, Raw
  Input lifecycle, release-asset, and security tests pass. The synthetic elevated F24 probe produced
  one press and one release, no retained pressed state, and no foreground change. Its injected input
  is not physical-key acceptance.
- **Contracts and distribution:** three JSON Schema 2020-12 contracts and three examples validate
  both structurally and through authoritative runtime parsers. Clean sdist/wheel builds include
  defaults, add-ons, descriptors, schemas, and examples. A separately installed wheel passes smoke
  outside the checkout. The clean Nuitka onedir contains 1,061 files (119,202,050 bytes), passes its
  embedded-resource/profile-compile smoke, and has local unsigned executable SHA-256
  `AD71024461A1B12362A6AE5D737ADC7AA1218A425BDF5E86B27A29BFB7969BAD`.
- **Verification-found defects fixed:** the build now uses a repository-bounded Nuitka cache,
  explicitly permits required tool downloads, cleans only verified artifact directories, rejects
  one-file mode, and refuses to hash a package until its smoke passes. This caught an interrupted
  executable missing onedir DLLs. Python 3.9 compatibility is restored by postponing PEP 604 type
  annotation evaluation, with a static regression guard. Stale user-facing `General` settings paths
  were renamed to `Global`.
- **Documentation/package state:** `docs/keybindings.md` documents actual profile, matching,
  dependency, DSL, context, and trust behavior; `docs/release_verification.md` owns the exact release
  matrices and evidence. README, HANDOFF, TODO, BLE, host, CI, optional dependencies, and package
  data declarations are reconciled. No firmware or host add-on runtime changed, so their version
  markers remain intentionally unchanged; daemon `0.1.73` is a local verification build, not a new
  published version.
- **First incomplete item:** 11.3 physical keyboard/lifecycle matrix, followed by 11.4 final hardware,
  11.5 host coverage, 11.6 packaged HUD/UI, and clean-account/signing/reputation release gates.
- **Packaged manual checkpoint:** the user reported the complete first onedir batch passing: tray,
  Settings, HUD, BLE connection, Ctrl/Shift/Ctrl+Shift/F12 controls, live binding save, Global and
  per-app link/reset flows, available monitor/DPI behavior, Blender/Fusion 360/SOLIDWORKS/Onshape,
  and quit/restart while held. No stale state remained.
- **Branch/release boundary:** the user explicitly scoped this phase to merging and closing the
  feature branch without publishing a production release. Final five-way hardware, clean-account
  install/uninstall, signing/timestamp, SmartScreen/AV reputation, SBOM, and final production host
  qualification remain release work in `TODO.md`. They can be honest `BLOCKED_FOR_RELEASE` or
  deferred results while this branch completes; they must never be described as production passes.
- **Next exact action:** close the remaining physical keyboard/lifecycle matrix using available prior
  evidence and one final live batch; record unavailable layout/RDP/environment cases and their
  branch-versus-release impact. Then run the final suite, mark Phase 11 complete, commit, and stop.
  Do not push, merge, publish, or open a PR.

### 0.19 Phase 11 completion checkpoint

- **Status:** `COMPLETE` at branch/merge scope from starting commit `97ccd54`; automated/package
  checkpoint `3683901`; completion checkpoint `6992d8b`. Work items 11.1–11.7 and the stop gate are
  complete under the user's explicit decision that this branch will merge without becoming the
  first production release.
- **Files and ownership:** release schemas/examples, side-effect-free smoke, repository-bounded
  Nuitka onedir builder, CI/package metadata, Python 3.9 guard/fix, release/security tests, README,
  HANDOFF, TODO, BLE/host/keybinding/release docs, and this ledger were updated. User-owned
  `.claude/` and `docs/rich_keybindings_plan_revisions.md` remain untracked and untouched.
- **Final automated result:** `python -m pytest -q` = `637 passed, 2 skipped in 9.85s`; both skips are
  the documented Tk smokes unavailable in the shell Python runtime. `compileall`, both System profile
  validations, source `--release-smoke`, AutoCAD NavMath `ALL PASS`, and `git diff --check` pass.
  Focused migration/corruption, BLE trust, Raw Input lifecycle, release-asset, and security checks
  pass 88 tests. The elevated synthetic F24 acceptance reports one press/release, no repeat edge,
  no retained state, and unchanged foreground.
- **Artifact result:** clean isolated sdist/wheel builds pass; a force-installed wheel passes smoke
  from outside the checkout. The clean Nuitka onedir has 1,061 files (119,202,050 bytes), passes its
  embedded-resource/profile-compile smoke, and has unsigned local executable SHA-256
  `AD71024461A1B12362A6AE5D737ADC7AA1218A425BDF5E86B27A29BFB7969BAD`.
- **Manual branch result:** the packaged onedir passes tray/Settings/HUD/BLE startup; Ctrl, Shift
  cascade, both Ctrl+Shift orders, and F12; live binding save; Global/per-app commit/link/reset;
  available monitor/DPI behavior; Blender, Fusion 360, SOLIDWORKS, and Onshape navigation;
  quit/restart while held; Sticky Keys; session lock/unlock while held; and sleep/resume while held.
  Each lifecycle boundary released cleanly and accepted later input.
- **Unavailable/deferred with impact:** AltGr/non-US-layout and Remote Desktop were unavailable;
  remaining supported hosts were unavailable for packaged live coverage. Automated paths pass, so
  these do not block feature-branch merge, but they remain production qualification. Final five-way
  pins/debounce/physical matrix await hardware. Clean-account install/uninstall, signing/timestamp,
  SBOM/lock, checksums for the eventual artifact, SmartScreen/AV/firewall/UAC/certificate prompts,
  and production host-version policy remain release work. None is represented as passed.
- **Compatibility and versions:** legacy rotation-only firmware, XIAO test-bench input, v8→v9
  migration/rollback, and existing add-on transports remain inside their documented compatibility
  envelopes. No firmware or host add-on runtime changed, so their markers remain unchanged. Daemon
  `0.1.73` identifies the local verification artifact; no production version was cut.
- **Residual product work:** profile export/import, visual priority editing, later visual UI polish,
  host parity gaps, production hardware, and release/distribution work remain only in `TODO.md` and
  the release matrix; no stale TODO claims this completed implementation is still missing.
- **Next exact action:** stop. The user may push/merge/close the branch when ready. Do not push,
  merge, publish, open a PR, sign, or distribute artifacts without a separate user command.

## 1. Product goals

The finished control system should support:

- Toggle and hold bindings, including distinct press and release values.
- Multi-input chords such as `Ctrl+Shift`, including modifier-only chords.
- Global switching between Pointer and 3D input.
- Switching the focused app among Orbit, Fly, and Walk where supported.
- Dependent states that automatically force their prerequisites. Asking for Pan must make Pan
  reachable by also activating 3D and the appropriate navigation mode.
- Keyboard and device buttons represented identically to the binding engine and settings UX.
- The Astrolabe five-direction switch as five named bindable controls: Up, Down, Left, Right, and
  Center.
- A versioned path for other BLE devices to expose buttons without modifying the keybinding engine.
- A safe declarative macro format able to modify any explicitly keybindable setting, including
  sensitivity and gains.
- Global and executable/app-specific binding contexts.
- Developer-owned system keybinding profiles, initially `astrolabe_5way` and `keyboard_only`.
- Developer-owned System defaults, user-owned Global settings, and per-app overrides linked to
  Global by default.
- A persistent bottom-right, text-only control panel showing current state and the current-held or
  most recently used binding.

## 2. Explicit non-goals for this branch

- Do not build the final visual design. Use plain Tk labels, standard controls, Unicode/text state,
  and simple spacing. Do not add SVG rendering, diagrams, animation, theming, or a web runtime.
- Do not run arbitrary Python inside the daemon. “Scripting” means a versioned declarative DSL.
- Do not make every configuration field keybindable. Install, trust, certificate, network endpoint,
  startup, device identity, and add-on lifecycle fields remain outside the command surface.
- Do not consume/suppress foreground-app keystrokes by default. Global input remains pass-through.
- Do not redesign host camera math unless a state transition exposes a real host-specific defect.
- Do not require all BLE devices to use the Astrolabe packet format. Put protocol-specific work
  behind an adapter contract.

## 3. Relevant architecture and debt that must be handled first

### 3.1 Keyboard state is currently sampled, not observed

`output.py` calls `GetAsyncKeyState(VK_SHIFT)` only while a BLE rotation packet is processed. That
cannot represent press/release transitions, chords, current-held HUD state, or a binding that
changes settings while the ball is stationary.

**Required fix:** remove keyboard inspection from motion mapping and introduce event-producing input
providers plus an input-state aggregator. On Windows, prefer Raw Input on a dedicated message-only
window: it produces transitions while the daemon is unfocused without suppressing ordinary app
input. Phase 0 must validate that choice before it becomes a permanent dependency.

### 3.2 Controller-mode buttons disappear

The active firmware has one fixed 12-byte rotation characteristic. In HID mode it sends three
mouse buttons. When the daemon subscribes, controller mode suppresses HID reports and releases an
existing HID button hold, but it does not send button state to the daemon. `general.buttons` is
therefore a reserved UI/config block without a runtime consumer. This is also called out in
`TODO.md` as missing controller-mode button events.

**Required fix:** replace the unused logical mouse-button configuration with the generic binding
input model, and extend the custom BLE service without breaking the existing rotation
characteristic.

### 3.3 “Default” and inheritance currently mean several different things

Current configuration uses all of the following:

- Fully materialized common values in each app profile.
- The string `"default"` for some per-app scheme inheritance.
- An absent key for `level_horizon_on_entry` inheritance.
- `0` for refresh-rate inheritance.
- A reset operation that copies developer defaults into the app profile.

This cannot cleanly support link/unlink UX or exhaustive Global settings.

**Required fix:** use sparse override maps. Absence has one meaning: inherit the next layer. Do not
store sentinel values to express links.

### 3.4 Configuration mutation is not a safe control API

The Tk UI mutates `config.data` directly, then saves the whole dictionary. Writes happen outside
the config lock; listeners are broad invalidations; listener exceptions are silently swallowed.
A global keyboard hook, BLE button events, a HUD, and app-focus changes would multiply the race and
diagnostic problems.

**Required fix:** add typed transactions, validation, immutable snapshots, and a logged event bus.
UI, tray, keybindings, and macros must use the same command API.

### 3.5 App identity and capabilities have multiple owners

Setup metadata lives in `integrations.py`, control capabilities in `binding_schema.py`, and process
matching in `App._APP_PROC_HINTS`. Onshape has additional browser/focus behavior. This duplication
will make app-dependent bindings drift.

**Required fix:** create one app registry for identity, process selectors, supported settings and
modes, transport kind, and special focus resolution. Installer implementations can remain in
`integrations.py`.

### 3.6 The broker is not target-isolated

`NavBroker` owns one accumulator, rate, and scheme and broadcasts frames to every socket client.
The existing backlog already identifies this. Per-app keybound settings cannot safely build on it.

**Required fix:** make app identity part of submit, accumulation, scheme, rate, and delivery.

### 3.7 Runtime control state has no single authority

Pointer/3D lives on `OutputEngine`; per-app navigation mode lives in config; the tray changes mode
directly; Blender has a host-local mode override; focus updates happen primarily at BLE packet
boundaries. A HUD cannot accurately summarize this.

**Required fix:** one runtime state store must own effective input mode, navigation mode, action
layer, held bindings, latched overrides, focused context, and last binding event. Remove or route
Blender’s local override through the daemon.

### 3.8 Legacy naming obscures the product model

The daemon calls 3D mode `cube` because of the old debug window. `active_app` also means the app
selected in settings/fallback profile in some places, not necessarily the foreground app.

**Incidental fixes:** migrate `cube` to `3d` and `cursor` to `pointer`, retain backward compatibility
at config load, and rename `active_app` to an explicit UI/fallback field. Runtime foreground app
must never come from that value.

## 4. Architectural decisions

### 4.1 Resolve dependent states through a state graph

Adopt the preferred cascading-dependency behavior.

Bindings request a leaf state. The runtime resolver computes and applies the transitive dependency
closure; it does not simulate or fire other keybindings.

Initial state graph:

```text
input.mode = pointer | 3d

navigation.mode = orbit | fly | walk
  requires input.mode = 3d

navigation.layer = primary | secondary
  requires input.mode = 3d

orbit.secondary (pan/zoom)
  requires input.mode = 3d
  requires navigation.mode = orbit
  requires navigation.layer = secondary

fly.secondary / walk.secondary (move)
  require input.mode = 3d
  require the matching navigation.mode
  require navigation.layer = secondary
```

The UI can call the Orbit secondary layer “Pan / Zoom” and the Fly/Walk secondary layer “Move,” but
the underlying state should remain mode-neutral.

Example behavior:

- Holding `Ctrl` requests 3D. With no stronger request, 3D uses Orbit and the primary layer.
- Holding `Shift` when bound to Pan/Zoom requests the Orbit secondary leaf. The resolver also forces
  3D and Orbit, so Shift alone is useful.
- A user who wants physical dependency representation can instead bind Pan/Zoom to `Ctrl+Shift`.
- With exact matching, `Ctrl+Shift` chooses that specific binding. Releasing Shift while Ctrl
  remains held atomically removes Pan/Zoom and activates the exact Ctrl binding, leaving 3D Orbit.

There is no key-order requirement and no “host chord must be pressed first” race. After every input
event, the engine computes the complete desired active-binding set and commits one state
transaction.

Dependencies are held overrides, not destructive writes. Releasing a leaf removes its derived
dependency token and reveals any remaining request or base state.

Contradictory requests use one deterministic precedence tuple:

1. Explicit binding priority.
2. Context specificity: executable/app/profile-specific conditions outrank broader conditions.
3. Chord specificity: exact matches outrank permissive matches, then more-specific chords outrank
   less-specific chords.
4. Activation serial: the most recently activated request wins when the first three fields tie.

A derived dependency inherits its leaf request's entire precedence tuple and activation identity.
For example, if an older `orbit.secondary` hold and a newer `fly.primary` hold are both active, the
newer contradictory Fly request wins; releasing it reveals the still-held Orbit secondary request.
This is stack-like fallback, not destruction of the older request. The compiler still rejects
duplicate bindings with the same chord, context, and priority, because those would activate in the
same event and have no meaningful recency distinction.

### 4.2 Keep exact matching but make it a binding policy

The default policy should remain exact modifier matching because it is predictable. Each binding
may select:

- `exact`: no unlisted modifiers may be down.
- `allow_extra_modifiers`: additional modifier keys do not prevent activation.

Do not use one global switch that changes every binding’s semantics. Per-binding policy avoids the
physical race and surprise activation problems of broadly making Ctrl imply every Ctrl-prefixed
binding. The UI may expose a Global default for newly created bindings, but each saved binding
stores its resolved policy.

Specific exact chords outrank permissive matches. Equal-specificity conflicts are rejected unless
the user assigns an explicit priority.

Dependency closure does not secretly weaken exact matching. If separate exact `Ctrl` and exact
`Shift` bindings exist with no `Ctrl+Shift` binding, the combined chord is intentionally uncovered.
The editor/compiler should warn about that reachable gap. A shipped profile must avoid a surprising
no-op by either defining explicit `Ctrl+Shift` Pan or marking the Shift Pan binding
`allow_extra_modifiers`; the choice remains visible and user-editable.

### 4.3 Use one normalized input model for keyboard and device controls

All providers emit the same event type:

```text
InputEvent
  source_id       keyboard, ble:<device-instance>, future provider
  control_id      stable provider control identifier
  phase           pressed | released | disconnected
  timestamp
  sequence        optional provider sequence
  metadata        scan code, device model, etc.
```

Bindings store namespaced tokens, for example:

```text
keyboard:ctrl
keyboard:shift
ble.astrolabe:fiveway.up
ble.astrolabe:fiveway.down
ble.astrolabe:fiveway.left
ble.astrolabe:fiveway.right
ble.astrolabe:fiveway.center
```

The aggregator owns the complete pressed set across providers. Cross-provider chords are allowed,
although device capability metadata may warn when a physical switch cannot produce certain
simultaneous directions.

The Windows keyboard provider should use Raw Input (`RIDEV_INPUTSINK`) on a dedicated message-only
window, without `RIDEV_NOLEGACY`, so configured keys remain pass-through to the active app. Register
it lazily only while the compiled profile contains enabled keyboard bindings. Its window procedure
must enqueue compact events and return quickly; normalization, matching, and command execution run
off the message callback. Reconcile configured controls outside that callback with
`GetAsyncKeyState` after lifecycle discontinuities. If physical state cannot be read reliably—for
example across a locked/inactive desktop or an access boundary—release all keyboard-owned controls
rather than risk a stuck hold.

A low-level keyboard hook is a fallback only if the Phase 0 spike proves Raw Input cannot satisfy an
acceptance case. That fallback requires a dedicated hook thread, a heartbeat/watchdog that detects
silent hook removal, reinstallation followed by state reconciliation, and the same fail-safe release
behavior. `GetKeyState` is not the reconciliation API: it reports message-queue state rather than
the immediate physical state needed here.

Provider disconnect, daemon shutdown, profile reload, session lock, receiver failure/restart, and
device removal synthesize releases for every control owned by that provider.

### 4.4 Separate persisted base settings from runtime overrides

Effective value precedence:

1. Highest-priority active hold request.
2. Runtime latched toggle/macro override.
3. Per-app explicit override, if present.
4. User Global override, if present and supported by the app.
5. Developer System default for that app/setting.
6. Immutable host baseline correction at the existing host-alignment boundary.

Host baselines are not user defaults and remain separately immutable.

Hold actions never write the config file. Runtime toggles also default to non-persistent. A macro
may explicitly request a persistent setting change, but the UI should label that action clearly.
Each hold owns an override token keyed by binding ID, activation instance, target, and source. Two
holds affecting the same setting therefore remain independently removable. The base resolver takes
the current app/context as input instead of treating Pointer/3D or any other base setting as a
process-wide scalar; that keeps the state interface open to future per-app automatic Pointer/3D
selection without another ownership refactor.

### 4.5 Use sparse overrides to represent links

Proposed user config shape:

```json
{
  "version": 9,
  "global_overrides": {},
  "app_overrides": {
    "blender": {},
    "fusion360": {}
  },
  "input_profile": "astrolabe_5way",
  "keybinding_overrides": {
    "astrolabe_5way": {},
    "keyboard_only": {}
  },
  "ui_state": {
    "selected_app": "blender"
  }
}
```

- Missing Global override means “System default.”
- Missing per-app override means “linked to Global.”
- Relinking deletes the per-app override.
- Unlinking materializes the current effective Global value into a per-app override.
- Editing a linked control first unlinks it, then applies the edit.
- Linked controls display the current effective Global value. Do not preserve a hidden stale local
  value; that commonly surprises users and makes link state hard to reason about.

If a Global value is unsupported by one app, the resolver uses that app’s compatible System
default and the UI shows `Linked · unsupported globally; using System default`. Unsupported fields
remain hidden from that app’s page. Global exposes the full setting superset and labels settings
that apply only to capable apps.

### 4.6 Keep the macro language declarative

Macros and UI-created bindings compile into the same command model. Initial operations:

- `set`
- boolean or explicit two-value `toggle`
- `cycle`
- numeric `add`
- numeric `multiply`
- `restore_previous`
- atomic action lists
- app/executable/input-profile conditions

Every target is a stable `SettingSpec` or `CommandSpec` identifier. JSON pointers into raw config
and Python function names are forbidden.

`restore_previous` is declarative sugar for removing the current binding activation's own runtime
override token. It must never write a value captured at press time: another hold, app-focus change,
Global edit, or profile change may have altered the correct underlying value while the key was held.

## 5. System defaults, Global settings, and per-app UX

### 5.1 Terminology

- Rename **General** to **Global** throughout UI and documentation.
- Call developer-owned installed values **System defaults**.
- Reserve **host baseline** for immutable application coordinate/sign/scale correction.
- Avoid unqualified “default” in new code, schema keys, tooltips, and documentation.

### 5.2 Developer-owned data

Replace or evolve `default_profiles.json` into a clearly named `system_defaults.json` containing:

- A complete System-default value for every user-tunable setting.
- App-specific System-default overrides where capability or host UX requires them.
- No user data and no runtime latch state.

Add `system_keybinding_profiles.json` with at least:

- `astrolabe_5way`: bindings that may reference the five switch controls plus any desired keyboard
  fallbacks.
- `keyboard_only`: no device-specific controls.

The precise shipped actions for the five switch directions should be chosen and live-tested during
implementation; the schema and tests must not hardcode assumptions outside the profile data.

The selected system keybinding profile is a base, not a destructive template. User changes are
sparse overrides associated with that base profile. Switching profiles releases all active
bindings, compiles the new base plus its overrides, and preserves overrides belonging to the other
profile for a later switch back.

### 5.3 Fresh-install behavior

- `global_overrides` starts empty: every Global setting displays its System-default value.
- Every app override map starts empty: all applicable app settings are linked to Global.
- The installed product default is `astrolabe_5way`, even when the device is not currently present;
  first-run setup also offers an explicit `keyboard_only` choice. The chosen profile persists and
  must not auto-switch behind the user as BLE availability changes. The Astrolabe profile may ship
  useful keyboard fallbacks, and the UI reports unavailable device controls as capability status.

### 5.4 Global page behavior

- Global must be generated from the setting registry and exhaustively include every user-tunable
  per-app setting. Render registry categories as ordinary Tk sub-tabs so one exhaustive page does
  not become an unbounded form; a category may scroll when necessary. Capability-specific settings
  say where they apply.
- A Global control inheriting System default displays the resolved value and a `System default`
  marker.
- Editing creates a Global override.
- A circle-arrow reset appears when a Global override exists. Tooltip: **Reset global to System
  default**.
- The bottom button reads **Reset all Global settings to System defaults** and clears the complete
  Global override map after confirmation.

### 5.5 Per-app setting behavior

- Linked settings are visually distinct using plain controls: prefix `🔗`, use a subdued linked
  style, and show the effective Global value. Do not make the editor truly non-interactive: its
  first edit must be able to atomically unlink and apply the new value. No custom artwork is
  required.
- Clicking the link marker while linked unlinks and copies the current effective Global value.
- Clicking it while unlinked relinks and deletes the local override.
- Attempting to edit a linked control unlinks first and applies the chosen value.
- The per-setting circle-arrow reset appears when the setting is linked and/or its effective local
  value differs from the app’s System default.
- Clicking that reset produces an explicit, unlinked override equal to the app’s System default.
  It therefore stops following Global, as requested.

Per-app header controls:

- **Link all** indicator/button in the top-right, grouped with the reset as compact red actions:
  - Unbroken when all applicable settings follow Global; clicking it breaks all links by
    materializing current effective Global values.
  - Broken when any setting is local; clicking it deletes every app override and restores all
    Global links.
- **Reset app to System defaults** in red: materializes every applicable app System default and
  breaks every Global link after confirmation.

Operational fields such as Enabled, Installed, and add-on version are outside this hierarchy.

## 6. BLE control protocol and device extensibility

### 6.1 Preserve the rotation characteristic

Keep the current 12-byte `float32 x 3` rotation characteristic unchanged so old firmware and old
daemon versions can coexist.

Add a separate input-state notify characteristic under the same custom service. Do not append
buttons to the rotation packet: doing so would break fixed-length consumers and couples high-rate
motion cadence to low-rate discrete input.

### 6.2 Prefer state snapshots over edge-only notifications

The input characteristic should send a versioned full state snapshot whenever button state
changes and once immediately after subscription:

```text
byte 0      protocol version
byte 1      message kind: input-state snapshot
bytes 2–3   monotonically increasing sequence
byte 4      payload byte count N
bytes 5..   N-byte pressed-control bitset
```

Benefits:

- A missed notification is repaired by the next snapshot.
- The daemon can diff snapshots into press/release events.
- Disconnect can release every set bit deterministically.
- The format scales beyond five controls without redefining event records.

The initial Astrolabe device descriptor maps five bits to Up, Down, Left, Right, and Center.
Legacy three-button test-bench hardware can receive its own descriptor without changing the
binding engine.

Treat the sequence as an unsigned 16-bit serial number. Given `delta = (incoming - last) & 0xffff`,
`delta == 0` is a duplicate, `1..0x7fff` is newer (including wraparound), and `0x8000..0xffff` is
stale/out of order and must be discarded. A new subscription/reconnect session resets the baseline
and accepts its first well-formed snapshot unconditionally. The protocol must not reuse a prior
session's baseline to judge a restarted device.

### 6.3 Add a device adapter boundary

Refactor `ble.py` from one `(name, address, char_uuid, callback)` subscription into:

```text
BleTransport
  scan/connect/reconnect
  discover and subscribe to requested characteristics

DeviceProtocolAdapter
  matches advertised service/device metadata
  declares controls and capabilities
  decodes motion notifications
  decodes input-state notifications
  emits normalized MotionSample and InputEvent objects
```

Ship adapters for:

- Legacy Astrolabe rotation-only firmware.
- Astrolabe rotation plus five-way control protocol.

Document an adapter registration API and JSON device-control descriptor format for community
hardware. Code-bearing third-party adapters can remain a later, explicitly trusted extension; the
binding engine itself must depend only on normalized events.

### 6.4 Firmware behavior

When the daemon owns controller mode:

- Continue suppressing HID motion to prevent double input.
- Release any outstanding HID button state on transition, as today.
- Publish five-way state through the custom input characteristic.
- Send the current snapshot on subscribe and after reconnection.
- Increment the sequence on every state change.

When the daemon is absent, ordinary HID behavior remains available. The firmware and daemon should
have packet-vector tests/documentation, while final debounce and simultaneous-direction behavior
requires hardware verification.

## 7. Declarative binding schema

Illustrative binding:

```json
{
  "id": "orbit-pan-hold",
  "enabled": true,
  "when": {
    "apps": ["blender", "fusion360"],
    "input_profiles": ["keyboard_only", "astrolabe_5way"]
  },
  "chord": ["keyboard:ctrl", "keyboard:shift"],
  "match": "exact",
  "activation": "hold",
  "priority": 0,
  "press": [
    {"command": "state.request", "target": "orbit.secondary"}
  ],
  "release": [
    {"command": "state.release", "target": "orbit.secondary"}
  ]
}
```

Illustrative sensitivity macro:

```json
{
  "id": "precision-orbit",
  "when": {"apps": ["fusion360"]},
  "chord": ["ble.astrolabe:fiveway.center"],
  "match": "exact",
  "activation": "hold",
  "press": [
    {"command": "setting.set_runtime", "target": "orbit.sensitivity", "value": 0.25}
  ],
  "release": [
    {"command": "setting.restore_previous", "target": "orbit.sensitivity"}
  ]
}
```

Compiler behavior:

- Validate command/setting IDs, values, app capabilities, and dependency cycles.
- Canonicalize generic versus left/right modifiers.
- Reject duplicate exact chords in the same context unless priority resolves them.
- Ignore OS key-repeat for activation transitions.
- Compile indexes by source token and context; do not scan all bindings on every event.
- On each event, compute the entire next active-binding set before running one transaction.
- Invalid entries are disabled individually with actionable diagnostics; they do not invalidate the
  core config or other bindings.

## 8. Minimal UI and text control panel

### 8.1 Settings UI

Keep Tk and standard widgets. Add only the minimum framework required:

- Global page generated from `SettingSpec` metadata.
- Per-app page generated from the same metadata plus app capability predicates.
- Keybindings page with profile selector, binding list, record-chord action, activation type,
  context, values/actions, match policy, enable/delete, and validation text.
- Show a concise pass-through warning when a keyboard chord contains non-modifier keys: activating
  the binding does not prevent the foreground app from receiving those keys.
- An Advanced button that opens the declarative JSON source or its folder.
- Link/reset text or Unicode markers. Do not create image assets.

The UI must call typed commands/transactions rather than editing raw dictionaries. This is the
foundation a later top-layer redesign will consume.

### 8.2 Persistent text HUD

Implement a separate non-activating Tk `Toplevel` anchored to the work area of the monitor containing
the foreground window. Fall back to the cursor monitor and then the primary monitor only when there
is no valid foreground window. Use plain labels only, for example:

```text
Astrolabe · 3D
Blender · Orbit · Pan / Zoom
Ball: planar = pan · twist = zoom
Held: Ctrl + Shift
```

When no binding is held, the last-used line remains for a configurable short timeout. Held input
always takes precedence.

Required Windows behavior:

- Tool-window and no-activate styles.
- Optional always-on-top and click-through settings.
- Primary/current-monitor anchor, margin, opacity, show/hide, and last-binding timeout.
- Reposition on work-area, display, foreground-window, and DPI changes.
- UI updates through a bounded, coalescing queue drained on the Tk thread. Runtime snapshots use
  latest-state-wins semantics so a burst cannot create a stale HUD backlog; discrete diagnostic
  messages, if any, use a separately bounded path.

Store semantic text descriptors such as `primary_help` and `secondary_help` in the control-state
metadata. A future visual UI can replace the text renderer without changing the state engine.

## 9. Phased implementation process

Complexity uses 1 = small and 5 = very difficult. Feasibility uses 1 = doubtful and 5 = high
confidence for this project.

Every phase below is a bounded authorization unit. **Files in scope** names the expected ownership
area, not permission to rewrite an entire file unnecessarily. If implementation discovers a change
outside that area, record it as a follow-up unless it is required to meet the phase's stop gate.
Safe internal checkpoints are valid places to honor `STOP AFTER CHECKPOINT` or survive compaction.
At a stop gate, commit the phase's intended changes locally, update the execution ledger, report the
exact verification, and wait; do not push, merge, open a PR, or start another phase without explicit
instruction.

### Phase 0 — Baseline and behavior contracts

**Complexity 2/5 · Feasibility 5/5**

**Start gate**

- Required command: `START PHASE 0`.
- Confirm branch `rich-keybindings`, perform the section 0.2 bootstrap, and set ledger Phase 0 to
  `IN_PROGRESS` with the current commit as its starting point.
- No later phase may be started in the same authorization.
- Files in scope: existing tests, `docs/`, `HANDOFF.md`, `TODO.md`, and a disposable diagnostic under
  `tools/` if the Windows-input spike needs one. Production behavior is not changed in this phase.

**Work**

0.1 Run and record the complete existing automated suite plus packaged/static checks that already
exist. Classify any pre-existing failure; do not silently normalize it into the feature baseline.

0.2 Add behavior-contract tests for current Shift sampling, Pointer/3D changes, app setting
inheritance, corruption-preserving config recovery, host-baseline ownership, focus identity, and
broker routing. Do **not** add a misleading test that merely proves `general.buttons` has no
consumer; record that verified debt and test its removal in the v9 migration instead.

0.3 Write the stable glossary used by later APIs: System default, Global, linked, app override,
host baseline, input mode, navigation mode, action layer, input profile, binding context, requested
state, effective state, and foreground app.

0.4 Run a small Windows input spike comparing Raw Input on a dedicated message-only window with the
low-level keyboard-hook fallback. Acceptance cases:

- Background press/release and modifier-only chords while another ordinary app has focus.
- Left/right modifier identity, repeats, AltGr/layout behavior, and pass-through behavior.
- No focus steal and no use of key suppression.
- Registration/unregistration and synthetic release on profile disable, session lock, and shutdown.
- A documented elevated-app/access-boundary result; inability to observe safely must release state.
- Callback work limited to decoding/enqueueing, with observable provider heartbeat/lifecycle.

Prefer Raw Input with `RIDEV_INPUTSINK` and no `RIDEV_NOLEGACY`. Choose the low-level hook only when
the spike records a concrete failed acceptance case that Raw Input cannot meet. If the hook wins,
the architecture decision must require a heartbeat/watchdog, automatic reinstall, and
`GetAsyncKeyState` reconciliation; it must not use `GetKeyState` as physical-state truth.

0.5 Record the input-backend decision and its evidence in `docs/` so Phase 5 can implement it without
reopening the design. Update the ledger with the selected backend and any manual tests that could
not be automated.

**Safe internal checkpoints:** after 0.1 baseline capture; after 0.3 contract tests/glossary; after
0.4 spike evidence.

**Stop gate**

- The original full suite and the new contract tests pass, or every pre-existing exception is
  explicitly recorded.
- The Raw Input versus hook decision, lifecycle contract, and fallback conditions are durable docs.
- No production control behavior changed.
- Commit only baseline tests/docs/tools, mark Phase 0 `COMPLETE`, set the next action to wait for
  either `START PHASE 1` or the independently authorized `START PHASE 4`, then stop.

### Phase 1 — App and setting registries

**Complexity 4/5 · Feasibility 5/5**

**Start gate**

- Required command: `START PHASE 1`; Phase 0 must be `COMPLETE`.
- Files in scope: `trackball_daemon/app.py`, `binding_schema.py`, `integrations.py`, `winfocus.py`,
  expected new `app_registry.py`, `settings_schema.py`, and their focused tests.
- Capture the existing app IDs, process hints, setting paths, capability predicates, setup metadata,
  and Onshape browser-focus special case before moving ownership.

**Work**

1.1 Introduce immutable `AppSpec` records that own stable app ID/display name, process selectors,
supported modes, setting capabilities, transport kind, and special focus resolution. Installer
functions stay in `integrations.py` but reference registry identity.

1.2 Introduce exhaustive `SettingSpec` records with stable ID, type, validator, category, scope,
capability predicate, System-default source, UI metadata, and allowed runtime/persistent operations.
Introduce `CommandSpec` for non-setting actions and exclude sensitive lifecycle/setup operations.

1.3 Move `App._APP_PROC_HINTS` and other duplicated metadata behind the registry. Model Onshape as
connected-versus-foreground states explicitly rather than treating a connected browser as active.

1.4 Add registry integrity tests: unique stable IDs, full default coverage, valid capabilities,
known process selectors, no duplicate owners, and every current user-tunable per-app field either
registered or deliberately classified as operational/non-user-tunable.

**Safe internal checkpoints:** AppSpec and identity tests; SettingSpec/CommandSpec coverage;
consumer migration with old compatibility shims removed.

**Stop gate**

- Existing integration metadata/routing tests and new registry integrity tests pass.
- Every user-tunable setting has one registry owner; app identity and process selection no longer
  have competing tables.
- Update relevant documentation, commit the registry boundary, mark Phase 1 `COMPLETE`, set next
  action to `START PHASE 2`, and stop.

### Phase 2 — Sparse System→Global→app configuration

**Complexity 5/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 2`; Phase 1 must be `COMPLETE`.
- Files in scope: `trackball_daemon/config.py`, `default_profiles.json`, expected
  `config_store.py`/`system_defaults.json`, config/default/profile tests, package manifests, and
  terminology docs. UI rendering itself remains Phase 9.
- Freeze v8 fixtures covering every inheritance sentinel, equal/different app values, malformed
  files, operational fields, and legacy `cube`/`cursor` names before writing migration code.

**Work**

2.1 Add validated `system_defaults.json` with complete registry coverage, including app-specific
System defaults where needed. Add package-data assertions before consumers switch to it.

2.2 Implement one pure resolver for System→Global→app precedence. Missing override means inherit;
remove `"default"`, missing-field exceptions, and numeric sentinels from the new model.

2.3 Add typed, locked transactions, copy-on-publish immutable snapshots, structured change events,
and logged subscriber failures. Convert callers away from direct `config.data` mutation in bounded
consumer groups; compatibility reads may exist only inside the store until all consumers migrate.

2.4 Implement v8→v9 as a one-time, source-aware migration:

- Reconstruct the v8 inherited value using the exact shipped v8 defaults and legacy rules.
- If a materialized app value equals that inherited value, omit it in v9 so it follows Global.
- If it differs, pin it as an explicit app override, even if a future System default happens to
  match. This is the bounded heuristic: migration preserves current behavior and cannot infer old
  user intent beyond equality to the old inherited value.
- Preserve installation/enabled/version and other operational state outside the setting hierarchy.
- Translate `cube`→`3d`, `cursor`→`pointer`, and `active_app`→`ui_state.selected_app`; accept legacy
  names on read for one compatibility cycle but write only canonical names.
- Remove obsolete `general.buttons` data only under an explicit v9 migration rule and test that v9
  never writes it.
- Write v9 only after full validation; retain the original file under the established recovery
  policy if migration fails.

2.5 Add transaction concurrency/reentrancy tests, listener-failure observability tests, exhaustive
resolver tables, fresh-install empty maps, and both migration branches: equal becomes linked,
different remains pinned.

**Safe internal checkpoints:** System-default schema; pure resolver; transaction store; migration
fixtures and compatibility aliases; final consumer cutover.

**Stop gate**

- Fresh installs resolve every Global value from System defaults and every app value through
  Global, with empty override maps.
- v8 fixtures preserve effective values, both link/pin heuristic branches pass, malformed input is
  preserved, and v9 output contains no old sentinels/buttons or legacy names.
- No remaining feature consumer mutates raw config dictionaries; subscriber errors are observable.
- Commit the complete schema/migration boundary, mark Phase 2 `COMPLETE`, set next action to
  `START PHASE 3`, and stop.

### Phase 3 — Runtime state, commands, and dependency closure

**Complexity 5/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 3`; Phase 2 must be `COMPLETE`.
- Files in scope: expected `runtime_state.py`, `commands.py`, `tray.py`, relevant state/config/app
  consumers, and focused runtime tests. Real keyboard/BLE providers remain out of scope; use fakes.
- Freeze current tray mode changes and OutputEngine mode ownership in tests before rerouting them.

**Work**

3.1 Implement a serialized command queue/runtime store and immutable `RuntimeSnapshot` containing
focused context, base/effective Pointer/3D, navigation mode/layer, held binding identities, latched
overrides, last binding event, and monotonically increasing revision.

3.2 Implement identity-owned request tokens and transitive dependency closure from section 4.1.
Derived prerequisites inherit the leaf token's precedence. Detect cycles at graph construction.

3.3 Apply the exact precedence tuple from section 4.1. Test contradictory holds in both activation
orders and prove release reveals the older still-held request. Test two independent holds on the
same setting so `restore_previous` removes only its own token.

3.4 Implement one typed command path for tray, settings, future bindings, and providers. Route
existing tray/state mutation through it; command transactions publish one coherent snapshot rather
than observable intermediate dependency states.

3.5 Keep base resolution context-aware even if initial Pointer/3D defaults are global. This is the
extension seam for later per-app automatic Pointer/3D selection.

**Safe internal checkpoints:** serialized store/snapshots; dependency/token engine; command consumer
cutover and event publication.

**Stop gate**

- Pure tests cover Shift-alone cascading Pan, explicit Ctrl+Shift Pan in both key orders, fallback
  to Ctrl-held Orbit, contradictory Fly/Orbit holds, same-setting overlapping holds, cycles,
  priorities, context changes, and release-all.
- Tray and fake-provider commands have one runtime authority; no camera motion is required.
- Commit the runtime/command boundary, mark Phase 3 `COMPLETE`, set next action to either
  `START PHASE 4` if not complete or `START PHASE 5`, and stop.

### Phase 4 — Target-isolated navigation transport

**Complexity 4/5 · Feasibility 5/5**

**Start gate**

- Required command: `START PHASE 4`; Phase 0 must be `COMPLETE`. This phase may run before Phases
  1–3 only as a separately reviewed transport fix.
- Files in scope: `navbroker.py`, `app.py`, `output.py`, `onshape_bridge.py`,
  `solidworks_driver.py`, affected plugin handshake code, routing tests, `TODO.md`, and protocol docs.
- Record all current client handshake/wire versions and foreground routing behavior. Any wire change
  must be additive and versioned.

**Work**

4.1 Add a target-aware navigation envelope and per-target accumulator, rate, scheme/profile
revision, and delivery state. A submitted sample is associated with one resolved target.

4.2 Deliver only to clients whose handshake matches that target. Define deterministic focus-change
behavior: finish or discard the old accumulator according to a tested rule; never relabel old deltas
as the new target.

4.3 Adapt SolidWorks and Onshape to the same router boundary without changing host camera math.
Preserve connected-but-unfocused Onshape behavior explicitly.

4.4 Add simultaneous/multiple-client, rapid-focus, reconnect, stale-revision, and no-target tests.
Remove the corresponding broker-isolation item from `TODO.md` when—and only when—the fix lands.

**Safe internal checkpoints:** envelope/handshake; per-target accumulation; host adapters and docs.

**Stop gate**

- Multi-client tests prove no background viewport receives motion and no delta/rate/scheme revision
  crosses targets; all existing camera-math tests remain green.
- Add-on protocol/version markers are updated together with any changed consumer, never in a later
  unrelated phase.
- Commit this independently reviewable fix, mark Phase 4 `COMPLETE`, set the next action according
  to the first incomplete dependency in the ledger, and stop.

### Phase 5 — Input provider framework and Windows keyboard backend

**Complexity 4/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 5`; Phases 0 and 3 must be `COMPLETE`, including the Phase 0 input
  backend decision.
- Files in scope: expected `input/model.py`, `input/aggregator.py`,
  `input/windows_raw_input.py` (or the documented fallback module), `winfocus.py`, runtime startup/
  shutdown wiring, `docs/security.md`, and provider tests.
- Re-read the Phase 0 evidence; changing the selected backend requires stopping and recording a new
  architecture decision, not silently switching during implementation.

**Work**

5.1 Implement `InputProvider`, `InputEvent`, `InputControlDescriptor`, provider health, and a central
pressed-set aggregator. Events carry source identity, stable control ID, phase, time, optional
sequence, and metadata but no raw config path.

5.2 Implement the chosen Windows backend. For Raw Input, use a dedicated message-only window,
`RIDEV_INPUTSINK`, no `RIDEV_NOLEGACY`, compact callback enqueueing, and lazy registration only when
an enabled compiled binding references keyboard controls. For a hook fallback, include the Phase 0
watchdog/reinstall contract and keep the hook callback enqueue-only.

5.3 Normalize generic and left/right modifiers, filter injected events where the backend exposes
that metadata, reject repeat activations while preserving pressed state, and keep all keys
pass-through.

5.4 Reconcile configured controls with `GetAsyncKeyState` after receiver restart, resume, profile
swap, or suspected loss. On session lock/inactive desktop, access ambiguity, receiver failure,
shutdown, or unregister, synthesize releases for all keyboard-owned controls before continuing.

5.5 Publish foreground-context changes independently of BLE motion. The runtime, not the selected
settings page, resolves the foreground app.

5.6 Update the runtime/installation inventory in `docs/security.md` and document exactly what
keyboard input is registered/observed, when the provider is active, that raw keystrokes are not
logged, the pass-through model, and the elevated-app limitation/fail-safe.

**Safe internal checkpoints:** provider/aggregator fakes; Windows receiver; reconciliation and
foreground lifecycle; security documentation.

**Stop gate**

- Fake and real providers pass press/hold/release, repeat, modifier side, lazy lifecycle,
  profile-reload, shutdown, receiver-restart, lock/resume, and fail-safe release tests without
  moving a host camera.
- Manual results cover background pass-through and the documented access-boundary case. No raw-key
  logging is present.
- Commit provider/security work, mark Phase 5 `COMPLETE`, set next action to `START PHASE 6`, and
  stop.

### Phase 6 — BLE input protocol and Astrolabe five-way adapter

**Complexity 5/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 6`; Phase 5's normalized provider contract must be `COMPLETE`.
- Files in scope: `ble.py`, expected `devices/` adapters, `firmware/Astrolabe/Astrolabe.ino`, BLE/
  provider tests, protocol docs, `TODO.md`, and firmware/version markers. Preserve unrelated
  `firmware/XIAO3389` behavior unless it is explicitly used as a test-bench adapter.
- Record current characteristic UUIDs, the 12-byte rotation vector, controller/HID transitions,
  debounce behavior, and daemon-absent behavior before firmware edits.

**Work**

6.1 Split generic scan/connect/reconnect/subscription transport from device protocol adapters. Allow
one adapter to subscribe to motion plus input-state characteristics and declare named controls.

6.2 Specify and packet-vector-test the versioned input-state snapshot, unsigned 16-bit sequence
arithmetic, initial-snapshot baseline, wraparound, duplicate/stale rejection, malformed lengths,
and reconnect baseline reset from section 6.2.

6.3 Preserve the existing rotation characteristic byte-for-byte. Add a separate input-state
characteristic to Astrolabe firmware and emit the five-direction bitset on changes plus an initial
snapshot after subscription/reconnect.

6.4 Ship legacy rotation-only and five-way adapters. Diff accepted snapshots into normalized
press/release events; disconnect always releases every control owned by that device instance.

6.5 Preserve controller-mode HID suppression and transition releases. When the daemon is absent,
ordinary HID operation remains available. While the daemon owns controller mode, normalized
buttons intentionally await Phase 7/8 host actions rather than also emitting HID and risking double
activation. Hardware-test debounce and physically possible switch combinations rather than
guessing them into schema logic.

6.6 Document a data-only device descriptor/adapter registration boundary for other BLE hardware.
Remove the lost-controller-buttons TODO only when hardware and packet-vector tests pass. Update
firmware protocol/version markers in this same phase.

**Safe internal checkpoints:** transport/adapter split; packet spec and host decoder; firmware
publisher; live five-way and legacy compatibility.

**Stop gate**

- Automated vectors cover sequence wraparound, duplicates, stale packets, missed transitions,
  reconnect, malformed input, and disconnect-held release.
- Live test-bench button events are indistinguishable from keyboard controls to the fake binding
  consumer; legacy rotation-only and daemon-absent HID paths still work. Final production
  Up/Down/Left/Right/Center qualification is a Phase 11/release gate.
- Commit host+firmware+protocol work as one compatible boundary, mark Phase 6 `COMPLETE`, set next
  action to `START PHASE 7`, and stop.

### Phase 7 — Binding compiler, DSL, and system input profiles

**Complexity 5/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 7`; Phases 2, 3, 5, and 6 must be `COMPLETE`.
- Files in scope: expected `input/bindings.py`, `input/macros.py`,
  `system_keybinding_profiles.json`, config integration, schemas/docs, and compiler/runtime tests.
- Export/import and a visual priority editor remain post-MVP; do not expand this phase without a new
  user instruction.

**Work**

7.1 Define and validate developer-owned base profiles `astrolabe_5way` and `keyboard_only`.
`astrolabe_5way` may include keyboard fallbacks plus test-bench compatibility bindings for
`ble.xiao3389:button.left/right/middle`; these preserve conventional pointer buttons without
changing the final five-way switch semantics. Store sparse user overrides under the selected
base-profile ID so switching away and back restores the correct customizations.

7.2 Compile normalized chords and indexes by source token/context. Implement exact and
allow-extra-modifier policies, generic/left-right modifiers, modifier-only and cross-provider
chords, context specificity, explicit priority, activation serial, and OS-repeat rejection.

7.3 Implement hold and toggle activation, distinct press/release action lists, atomic active-set
recomputation after every event, and release-all before profile/config recompilation. Track
externally held output resources by binding activation identity so provider disconnect, profile
reload, shutdown, and a replaced owner always synthesize the matching release.

7.4 Implement the allowlisted declarative DSL: stable command/setting IDs; `set`, boolean or
explicit two-value `toggle` (including numeric sensitivity presets), `cycle`, numeric
`add`/`multiply`, identity-based `restore_previous`, and atomic action lists; app,
executable, and input-profile conditions; capability/type/value validation; no Python/eval/shell or
raw JSON-pointer targets.

The allowlist also includes a narrow momentary `pointer_button` action for Left, Right, Middle,
X1, and X2. It is valid only as paired press/release behavior, never as arbitrary key/scancode
injection or a persistent toggle, and must participate in the release-all ownership rules.

7.5 Disable invalid entries individually with actionable diagnostics. Add a non-running validation
command suitable for contributors and packaged-build tests.

**Safe internal checkpoints:** schema/base profile loading; chord/context compiler; activation state
machine; macro operations/diagnostics; atomic reload.

**Stop gate**

- Tests cover both base profiles, profile-scoped overrides, every chord order, exact/permissive
  precedence, duplicate rejection, contradictory hold recency, toggle edges, explicit release
  values, identity-safe `restore_previous`, invalid-entry isolation, and atomic reload release.
- Switching profiles does not lose either profile's overrides or leave controls held.
- Commit compiler/DSL/profiles, mark Phase 7 `COMPLETE`, set next action according to the first
  incomplete Phase 8/9 dependency, and stop.

### Phase 8 — Motion/output integration and requested feature commands

**Complexity 4/5 · Feasibility 5/5**

**Start gate**

- Required command: `START PHASE 8`; Phases 4 and 7 must be `COMPLETE`.
- Files in scope: `output.py`, `app.py`, `navbroker.py`, `tray.py`, affected host/add-on state
  protocol, system profile data, output/routing tests, and version docs. Do not rewrite host camera
  math unless a focused regression proves the state boundary requires it.
- Record bit-exact output and current Shift Pan/Zoom behavior immediately before integration.

**Work**

8.1 Remove `shift_held()` and all packet-time keyboard inspection from `OutputEngine`. Consume one
immutable effective runtime/profile snapshot per motion sample.

8.2 Split pure motion transformation from pointer injection and navigation routing where the new
boundary is real. Preserve all bit-exact coordinate/quaternion and host-baseline behavior.

8.3 Recreate current Shift Pan/Zoom as profile data, then add Pointer/3D toggle/hold and focused-app
Orbit/Fly/Walk set/cycle/hold commands. Validate unsupported app modes rather than silently applying
them.

8.4 Enable generic setting hold/toggle actions only for registry-approved booleans, enums, and
numbers, including sensitivity/gain. Persistent macro writes remain visibly explicit.

8.5 Remove Blender's independent local mode authority or version the daemon/add-on protocol so the
daemon remains authoritative. Update Blender version markers and bundled metadata in the same
commit if its add-on changes.

8.6 Route compiled `pointer_button` actions through a dedicated SendInput press/release sink and
ship the XIAO3389 Left/Right/Middle compatibility bindings. Enforce one daemon instance/one BLE
controller owner before starting transport; a second installed or source-tree instance must exit
with an actionable diagnostic instead of competing for subscriptions.

**Safe internal checkpoints:** OutputEngine snapshot consumption; feature command integration;
host-local state authority/versioning.

**Stop gate**

- Existing bit-exact output, cursor/pointer-pivot, quaternion, and host camera tests remain green.
- All requested mode/setting transitions work while the ball is stationary and affect only the
  foreground target; focus change during a hold resolves safely.
- In daemon Pointer mode, XIAO3389 Left/Right/Middle produce conventional OS clicks without a
  duplicate firmware HID report, and every disconnect/reload/shutdown path releases a held button.
  A second daemon instance cannot acquire BLE/output ownership.
- Commit integration and any synchronized add-on version change, mark Phase 8 `COMPLETE`, set next
  action according to the first incomplete Phase 9/10 dependency, and stop.

### Phase 9 — Barebones settings UX

**Complexity 4/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 9`; Phases 2 and 7 must be `COMPLETE`.
- Files in scope: `ui.py`, `tray.py` only where it opens/settings state, UI metadata in registries,
  config/command calls, and UI-independent plus Tk smoke tests. No SVGs, diagrams, animation,
  theming, or web UI.
- Re-read section 5's exact reset/link semantics; they are acceptance requirements, not visual
  suggestions.

**Work**

9.1 Rename General→Global and render the exhaustive SettingSpec registry in ordinary category
sub-tabs, with optional per-category scrolling and capability labels.

9.2 Implement Global controls, `System default` markers, per-setting circle-arrow reset, and the
bottom **Reset all Global settings to System defaults** action.

9.3 Implement linked per-app display and link toggles. Editing a linked value unlinks and writes;
relink deletes the app override; the setting reset creates an unlinked app System-default override.
Implement header Link all/Break all and red Reset app exactly as section 5.5 specifies.

9.4 Add system input-profile selection, capability status for absent controls, and a minimal binding
editor for chord capture, activation, actions/values, context, match policy, enable/delete, and
validation. Explicit non-default priority remains editable through the Advanced
DSL in MVP; a visual priority editor is post-MVP. Show the pass-through warning for non-modifier
keyboard chords.

9.5 Route every edit through typed transactions/commands. Use text/Unicode markers and explicit
status; leave the renderer driven by semantic metadata so a future UI can replace it.

**Safe internal checkpoints:** generated Global categories; per-app link/reset behavior; profile and
binding editor; Tk smoke/accessibility pass.

**Stop gate**

- UI-independent tests cover every link/reset transition and header bulk action; Tk smoke confirms
  the effective values/subdued linked state update when Global changes.
- All registered settings are reachable in Global, unsupported app settings are correctly handled,
  and no UI code mutates raw config dictionaries.
- Commit the barebones UX, mark Phase 9 `COMPLETE`, set next action according to the first incomplete
  dependency (usually `START PHASE 10` after Phase 8), and stop.

### Phase 10 — Text HUD

**Complexity 3/5 · Feasibility 5/5**

**Start gate**

- Required command: `START PHASE 10`; Phase 8 must be `COMPLETE`.
- Files in scope: expected `control_hud.py`, tray visibility wiring, semantic runtime/help metadata,
  UI queue helpers, HUD tests, and packaging data. Do not add image assets.
- Freeze a small snapshot-to-text table before building the window so renderer behavior is testable
  without Tk/Windows.

**Work**

10.1 Build a plain-label, non-activating Tk `Toplevel` subscribed only to immutable runtime
snapshots. Show product/input mode, foreground app, nav mode/layer, semantic movement help, and
current-held or timed last-used binding.

10.2 Use a bounded/coalescing cross-thread queue: state snapshots are latest-state-wins, with no
unbounded stale backlog. Drain and render only on the Tk thread.

10.3 Anchor to the work area of the monitor containing the foreground window; fall back to cursor
then primary monitor only when necessary. Reposition on foreground, work-area, display, and DPI
changes.

10.4 Add tool-window/no-activate behavior, optional always-on-top/click-through, margin, opacity,
show/hide, timeout, and tray visibility control. Verify the panel never changes active host focus.

**Safe internal checkpoints:** pure text projection; coalescing subscriber; native window behavior;
multi-monitor/DPI pass.

**Stop gate**

- Pure tests prove text and held/last precedence; stress tests prove snapshot bursts coalesce to the
  latest revision.
- Live keyboard and BLE state changes update without ball motion; focus, monitor, DPI, and
  click-through checks pass without focus steal.
- Commit HUD/framework work, mark Phase 10 `COMPLETE`, set next action to `START PHASE 11` only when
  all Phases 0–9 are also complete, and stop.

### Phase 11 — Verification, documentation, and release hardening

**Complexity 4/5 · Feasibility 4/5**

**Start gate**

- Required command: `START PHASE 11`; Phases 0–10 must all be `COMPLETE` with no undocumented
  skipped stop-gate test.
- Files in scope: all automated tests, `README.md`, `HANDOFF.md`, `TODO.md`, `docs/`, schemas/samples,
  packaging manifests/config, version metadata, and only defect fixes discovered by verification.
  New product features are out of scope.
- Build a release checklist from the ledger's unresolved/manual items before changing code.

**Work**

11.1 Run the full automated suite, migration fixtures, package-data checks, packaged executable
build, rollback/recovery checks, and static/security posture tests. Add JSON schemas and contributor
examples for profiles, device descriptors, and macros.

11.2 Audit docs against actual behavior: System/Global/app semantics, both system profiles,
pass-through keyboard capture and lazy registration, no raw-key logging, elevated/access-boundary
fail-safe, BLE packet trust boundary, declarative-DSL limits, and third-party adapter trust model.

11.3 Live-test keyboard behavior across layouts/AltGr, Sticky Keys, repeat, rapid key order,
sleep/resume, session lock, Remote Desktop, elevated foreground apps, profile reload, and daemon
shutdown. Use the selected backend's name, not the generic phrase “keyboard hook.”

11.4 Live-test firmware reconnect, missed/duplicate/out-of-order/wrapped sequences, disconnect while
held, debounce, simultaneous five-way behavior, legacy rotation, and daemon-absent HID fallback.

11.5 Live-test Pointer/3D, cascading Pan, Ctrl+Shift, Orbit/Fly/Walk, sensitivity holds, app-focus
switch while held, and target isolation in Blender, SketchUp, Unreal, Unity, and Godot. Verify
Onshape and other supported drivers according to available host access; record unavailable hosts
honestly rather than marking them passed.

11.6 Live-test the HUD on multiple monitors/DPI/work-area changes and verify no activation/focus
steal. Verify the barebones UI's exhaustive Global categories and all reset/link flows.

11.7 Update `HANDOFF.md`, `TODO.md`, protocol/default-profile/host docs, package metadata, and all
changed firmware/add-on version markers. Produce a concise list of post-MVP work, including
export/import and visual priority editing.

**Safe internal checkpoints:** automated/package verification; docs/security audit; keyboard and
hardware matrix; host matrix; HUD/UI matrix and final handoff.

**Stop gate**

- Every automated check and available live test has an exact recorded result; skipped hardware or
  host checks name the reason and release impact.
- Packaged resources load, v8 rollback/recovery is proven, legacy firmware/add-ons remain within the
  documented compatibility envelope, and no stale TODO claims a completed debt item.
- Commit only release hardening/docs/defect fixes, mark Phase 11 `COMPLETE`, record the final commit
  and residual risks, then stop. Do not merge, push, publish, or open a PR without a separate command.

## 10. Test matrix

### Pure unit tests

- Chord canonicalization, modifier sides, exact/permissive matching, and modifier-only chords.
- Atomic active-set recomputation for every key order.
- Toggle edge behavior and OS-repeat rejection.
- Hold press/release, explicit release value, identity-based `restore_previous`, two holds on the same
  setting, and every priority/activation ordering.
- Dependency closure, contradictory leaf conflict resolution, cycle rejection, most-recent tie
  breaking, and reveal-older fallback after release.
- Cross-provider chords, provider registration/restart, lifecycle reconciliation, and disconnect
  release.
- Setting capability and keybindability validation.
- Sparse System→Global→app resolution and link/reset operations.
- v8 equal-to-inherited→linked and different→pinned migration branches, plus removal of obsolete
  buttons and legacy-name aliases from v9 output.
- System profile plus profile-keyed sparse user override composition.
- DSL validation, unknown commands, invalid values, and partial-file failure isolation.
- BLE packet decoding, unsigned sequence wraparound/duplicate/stale rules, snapshot diffs, malformed
  lengths, initial snapshot, and reconnect baseline reset.
- Broker target isolation and state-revision integrity.
- HUD snapshot coalescing, monotonic revision handling, and held-versus-last text projection.

### Existing regression suites to preserve

- Bit-exact pointer and quaternion output.
- Config corruption preservation and historical migrations.
- Host-baseline ownership and per-app capabilities.
- Integration setup/security gates.
- Host camera and cursor-pivot math.

### Live verification

- Both shipped keybinding profiles.
- Five-way press, hold, release, quick transitions, disconnect while held, and reconnect.
- Ctrl 3D, Shift cascading Pan, and explicit Ctrl+Shift Pan in both key orders.
- App focus switch during every held state.
- Orbit/Fly/Walk transitions and horizon-entry behavior.
- Unknown foreground executable plus global bindings.
- Onshape browser connected-but-unfocused behavior.
- Multi-host connections proving no background motion.
- HUD no-focus-steal behavior and held/last-binding accuracy.

## 11. Suggested module layout

```text
trackball_daemon/
  app_registry.py              app identity, focus selectors, capabilities
  settings_schema.py           SettingSpec and System/Global/app metadata
  commands.py                  stable typed command registry
  runtime_state.py             serialized requests, dependencies, snapshots, event bus
  config_store.py              sparse config, transactions, migrations
  input/
    model.py                   InputEvent and InputControlDescriptor
    aggregator.py              pressed set and provider lifecycle
    windows_raw_input.py       preferred lazy Windows Raw Input backend
    windows_keyboard_hook.py   optional spike-selected fallback only
    bindings.py                chord compiler and state machine
    macros.py                  declarative DSL loader/compiler
  devices/
    transport.py               generic Bleak connection/subscription loop
    astrolabe_legacy.py        rotation-only decoder
    astrolabe_fiveway.py       rotation + input-state decoder
  navigation_router.py         target-aware driver/broker dispatch
  control_hud.py               text-only Tk panel
  system_defaults.json
  system_keybinding_profiles.json
```

This layout is directional rather than a requirement to move unrelated host math. Large existing
files should be split only where the new ownership boundary is real.

## 12. Migration and compatibility strategy

- Read v8 configuration and write v9 only after a fully successful migration.
- Preserve the old source on failure; do not overwrite structurally invalid config.
- Preserve effective settings rather than literal legacy sentinels. Reconstruct the exact v8
  inherited value: equality becomes a Global link, while inequality remains an explicit app
  override. This bounded heuristic deliberately makes no stronger claim about historical user
  intent.
- Remove `general.buttons` under a tested v9 rule; it was reserved UI/config state with no
  controller-mode runtime consumer, not a binding that can be faithfully translated.
- Accept `cube`/`cursor` as input aliases for `3d`/`pointer` during migration and macro validation
  for at least one release cycle; write only canonical names.
- Default a fresh install to `astrolabe_5way`, offer `keyboard_only` explicitly, persist the user's
  choice, and never switch profiles merely because BLE presence changes.
- Legacy firmware remains rotation-only, uses keyboard bindings, and does not expose phantom
  buttons.
- New daemon with old add-ons keeps the existing wire frame fields. Broker target isolation is
  server-side. Any add-on protocol extension remains additive and versioned; firmware/add-on version
  markers change in the same commit as their protocol consumer.
- Input-profile switching and config reload synthesize releases before swapping compiled state.

## 13. Security and open-source considerations

- Global keyboard reception must be documented and user-disableable. Register it lazily only when
  at least one enabled binding in the compiled profile uses a keyboard control.
- Do not log raw keystroke streams. Log only configured binding activations and validation errors.
- Pass through keys by default and warn in the editor that non-modifier binding keys also reach the
  foreground app. Suppression, if ever added, requires explicit per-binding consent.
- Filter injected keyboard events when the selected backend exposes reliable provenance. Also keep
  keyboard injection out of the initial command surface and add command-origin recursion guards;
  Raw Input itself does not provide a universal trustworthy “injected” flag.
- The macro DSL is an allowlisted command language, not `eval`, Python import, or shell execution.
- Sensitive setup commands are not registered as keybindable commands.
- Community device descriptors are data. Code adapters, if later supported, are explicitly trusted
  plug-ins and should be isolated from the core macro path.
- BLE input packets are untrusted: validate version, kind, lengths, control count, and sequence
  before updating pressed state; discard duplicate or regressed sequences under the wrap-aware rule
  in section 6.2.
- A provider failure releases its held controls and cannot leave a permanent forced state.

Relevant Windows platform references for the Phase 0 decision are Microsoft's documentation for
[Raw Input](https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input),
[`WM_INPUT`](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-input),
[`RegisterRawInputDevices`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerrawinputdevices),
[`LowLevelKeyboardProc`](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc),
and [`GetAsyncKeyState`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate).

## 14. Delivery gates

Do not proceed to the next product-facing layer until the preceding gate is met. Each satisfied gate
is a reasonable merge candidate, but whether to merge or keep accumulating on the feature branch is
the user's decision:

1. **Configuration gate:** sparse inheritance and migration are stable before keybindings persist.
2. **State gate:** dependency closure and release behavior are deterministic before global keyboard
   reception.
3. **Routing gate:** target isolation is complete before app-dependent binding rollout.
4. **Input gate:** keyboard provider lifecycle is safe before BLE buttons join it.
5. **Hardware gate:** versioned snapshots and disconnect release pass before the five-way device
   bindings are enabled in a shipped product-default profile.
6. **UX gate:** commands and registries are complete before the Global/per-app editor is rebuilt.
7. **Release gate:** both profiles, migrations, multi-host isolation, and live rich-mode transitions
   pass before the feature is enabled by default.

## 15. Final feasibility assessment

| Area | Complexity | Feasibility | Main risk |
|---|---:|---:|---|
| Dependency-aware runtime state | 5/5 | 4/5 | Overlapping hold/conflict semantics |
| Sparse System/Global/app settings | 5/5 | 4/5 | Behavior-preserving v8 migration |
| Keyboard chords | 4/5 | 4/5 | Raw Input lifecycle, access boundaries, and layout edge cases |
| BLE five-way controls | 5/5 | 4/5 | Firmware compatibility and lost-release recovery |
| Other BLE-device foundation | 4/5 | 4/5 | Keeping adapter API small and stable |
| Declarative DSL | 4/5 | 5/5 | Stable public command/setting IDs |
| App-dependent bindings | 4/5 | 4/5 | Accurate Onshape/browser focus |
| Broker isolation | 4/5 | 5/5 | Accumulator handoff on rapid focus changes |
| Barebones linked-settings UI | 4/5 | 4/5 | Exhaustive capability-aware rendering |
| Text-only HUD | 3/5 | 5/5 | DPI/no-activate behavior |

The project is a good fit for this architecture. The existing immutable mapping snapshots,
capability schema, configuration migrations, host baselines, and pure math tests are useful
foundations. The work is large because it crosses configuration, firmware, input, runtime state,
routing, and UI, but the risks are separable if the delivery gates and refactor-first order above
are followed.
