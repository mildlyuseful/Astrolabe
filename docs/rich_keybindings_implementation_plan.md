# Rich keybindings, input profiles, and layered settings implementation plan

Status: implementation design only

Target branch: `rich-keybindings`

Overall complexity: **5/5**

Overall feasibility: **4/5**

This document supersedes the earlier conversational plan for rich keybindings. It incorporates
dependent control states, keyboard and BLE-device buttons, system keybinding presets, sparse
Global-to-app inheritance, a declarative macro DSL, and a deliberately minimal text UI/HUD.

The work should be implemented incrementally on one branch. Refactor-only commits should preserve
current behavior before new behavior is enabled. A realistic estimate for one experienced engineer
is 7–11 engineer-weeks plus live host and hardware verification.

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
providers plus an input-state aggregator.

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

**Incidental fixes:** migrate `cube` to `3d`, retain backward compatibility at config load, and
rename `active_app` to an explicit UI/fallback field. Runtime foreground app must never come from
that value.

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

Provider disconnect, daemon shutdown, profile reload, session lock, and hook failure synthesize
releases for every control owned by that provider.

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
  "keybinding_overrides": {},
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
- `toggle`
- `cycle`
- numeric `add`
- numeric `multiply`
- `restore_previous`
- atomic action lists
- app/executable/input-profile conditions

Every target is a stable `SettingSpec` or `CommandSpec` identifier. JSON pointers into raw config
and Python function names are forbidden.

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
- The installer/first-run selection chooses `astrolabe_5way` when the compatible control service is
  detected, otherwise `keyboard_only`. The user can always switch manually.

### 5.4 Global page behavior

- Global must be generated from the setting registry and exhaustively include every user-tunable
  per-app setting, grouped by category. Capability-specific settings say where they apply.
- A Global control inheriting System default displays the resolved value and a `System default`
  marker.
- Editing creates a Global override.
- A circle-arrow reset appears when a Global override exists. Tooltip: **Reset global to System
  default**.
- The bottom button reads **Reset all Global settings to System defaults** and clears the complete
  Global override map after confirmation.

### 5.5 Per-app setting behavior

- Linked settings are visually distinct using plain controls: prefix `🔗`, disable the editor, and
  show the effective Global value in the disabled control. No custom artwork is required.
- Clicking the link marker while linked unlinks and copies the current effective Global value.
- Clicking it while unlinked relinks and deletes the local override.
- Attempting to edit a linked control unlinks first and applies the chosen value.
- The per-setting circle-arrow reset appears when the setting is linked and/or its effective local
  value differs from the app’s System default.
- Clicking that reset produces an explicit, unlinked override equal to the app’s System default.
  It therefore stops following Global, as requested.

Per-app header controls:

- **Link all** indicator/button in the top-right:
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
- An Advanced button that opens the declarative JSON source or its folder.
- Link/reset text or Unicode markers. Do not create image assets.

The UI must call typed commands/transactions rather than editing raw dictionaries. This is the
foundation a later top-layer redesign will consume.

### 8.2 Persistent text HUD

Implement a separate non-activating Tk `Toplevel` anchored to the current monitor’s work area. Use
plain labels only, for example:

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
- UI updates through a thread-safe event queue drained on the Tk thread.

Store semantic text descriptors such as `primary_help` and `secondary_help` in the control-state
metadata. A future visual UI can replace the text renderer without changing the state engine.

## 9. Phased implementation process

Complexity uses 1 = small and 5 = very difficult. Feasibility uses 1 = doubtful and 5 = high
confidence for this project.

### Phase 0 — Baseline and behavior contracts

**Complexity 2/5 · Feasibility 5/5**

1. Create the feature branch and record current automated results.
2. Add architecture-decision tests around current Shift behavior, mode changes, app profile
   inheritance, config preservation, and broker routing before refactoring.
3. Add explicit tests proving `general.buttons` currently has no runtime consumer, then remove that
   obsolete UI/config contract only when the new input profile migration lands.
4. Define stable vocabulary: System default, Global, linked, app override, host baseline, input
   mode, navigation mode, action layer, input profile, and binding context.

Exit: current behavior is captured and the refactor can be reviewed as behavior-preserving.

### Phase 1 — App and setting registries

**Complexity 4/5 · Feasibility 5/5**

1. Introduce `AppSpec`, consolidating app key/name, process selectors, supported modes, setting
   capabilities, and transport type.
2. Keep installer functions in `integrations.py`, but have setup metadata reference the registry
   rather than duplicating identity.
3. Introduce exhaustive `SettingSpec` and `CommandSpec` registries.
4. Generate both Global and per-app applicability from these registries.
5. Move process hints out of `App` and expose an accurate Onshape focus state.

Debt paid: duplicated app identity/capability sources and hand-coded field applicability.

Exit: every user-tunable setting has one stable ID, validator, scope, capability rule, System
default source, and permitted keybinding operations.

### Phase 2 — Sparse System→Global→app configuration

**Complexity 5/5 · Feasibility 4/5**

1. Add `system_defaults.json` and validate full setting coverage.
2. Add sparse `global_overrides` and `app_overrides` storage.
3. Replace `"default"`, absent-special-case, and numeric-sentinel inheritance with one resolver.
4. Rename the UI/config concept General→Global and legacy `cube`→`3d`.
5. Rename ambiguous `active_app` to `ui_state.selected_app` or an equally explicit field.
6. Add typed, locked config transactions and immutable published snapshots.
7. Replace silently swallowed listener errors with logged subscriber failures and observable config
   validation errors.
8. Implement v8→v9 migration that preserves effective existing behavior:
   - Convert values equal to their old inherited value into links.
   - Materialize only actual differences as sparse overrides.
   - Preserve installation/enable/version state.
   - Preserve malformed source files under the established recovery policy.

Debt paid: inconsistent inheritance, unsafe mutable config writes, ambiguous names, and broad
silent notifications.

Exit: fresh installs are System→Global→app linked; migrations are behavior-preserving and fully
tested.

### Phase 3 — Runtime state, commands, and dependency closure

**Complexity 5/5 · Feasibility 4/5**

1. Implement the serialized control runtime and immutable `RuntimeSnapshot`.
2. Implement requested-state tokens, dependency closure, priorities, and release/fallback.
3. Add a single command path used by tray, settings, keybindings, and later BLE input.
4. Publish focused app, effective input/nav/layer state, active bindings, and last action through a
   typed event bus.
5. Add cycle detection and deterministic conflict rules to the state graph.

Debt paid: scattered runtime authority and direct tray/engine mutation.

Exit: unit tests demonstrate Shift-alone cascading Pan, explicit Ctrl+Shift Pan, atomic transition
back to Ctrl-held Orbit, overlapping holds, and no stuck dependencies.

### Phase 4 — Target-isolated navigation transport

**Complexity 4/5 · Feasibility 5/5**

1. Introduce a target-aware navigation envelope.
2. Maintain per-app broker accumulator, scheme/profile revision, and rate.
3. Deliver only to matching client handshakes.
4. Adapt SolidWorks and Onshape behind the same router interface without rewriting their camera
   math.
5. Drop or finish old-target accumulation deterministically on focus changes.

Debt paid: the existing multi-client background-viewport bug and one-global-scheme leakage.

Exit: multi-client tests prove foreground isolation and no profile/delta mixing.

### Phase 5 — Input provider framework and Windows keyboard backend

**Complexity 4/5 · Feasibility 5/5**

1. Implement `InputProvider`, `InputEvent`, `InputControlDescriptor`, and the central pressed-set
   aggregator.
2. Add a Windows low-level keyboard hook on a dedicated message-loop thread.
3. Filter injected events, normalize modifiers, ignore repeats, and remain pass-through.
4. Add foreground context events independent of BLE packets.
5. Synthesize releases on shutdown, session lock, hook restart, config/profile swap, and provider
   disconnect.

Debt paid: packet-time Shift polling and BLE-packet-bound focus state.

Exit: a fake provider and the real Windows backend pass chord/hold/release tests without moving any
host camera.

### Phase 6 — BLE input protocol and Astrolabe five-way adapter

**Complexity 5/5 · Feasibility 4/5**

1. Split BLE transport from Astrolabe protocol decoding.
2. Support multiple characteristic subscriptions per adapter.
3. Add the versioned input-state characteristic to firmware while preserving rotation v1.
4. Add legacy and five-way Astrolabe adapters.
5. Map bit states into the same normalized input events as the keyboard.
6. Send initial snapshots, sequences, and disconnect releases.
7. Document the adapter/descriptor contract for other BLE hardware.

Debt paid: single hardcoded characteristic/callback, lost controller buttons, and unused
`general.buttons` configuration.

Exit: hardware Up/Down/Left/Right/Center appears in chord capture and behaves identically to a
keyboard control. Legacy rotation-only firmware remains functional.

### Phase 7 — Binding compiler, DSL, and system input profiles

**Complexity 5/5 · Feasibility 4/5**

1. Add and validate `system_keybinding_profiles.json`.
2. Implement `astrolabe_5way` and `keyboard_only` bases plus per-profile sparse user overrides.
3. Implement chord matching, exact/permissive policies, context precedence, repeats, priorities,
   hold/toggle behavior, and press/release actions.
4. Implement the declarative macro compiler and validation diagnostics.
5. Recompile atomically on profile/config change after releasing the prior active set.
6. Add export/import and a non-running validation command.

Exit: keyboard and five-way profiles can be switched without losing customizations or leaving held
state behind.

### Phase 8 — Motion/output integration and requested feature commands

**Complexity 4/5 · Feasibility 5/5**

1. Remove `shift_held()` from `OutputEngine`.
2. Split pure motion transformation from pointer injection and navigation routing where practical.
3. Consume one immutable effective state/profile snapshot per BLE rotation packet.
4. Recreate current Shift Pan/Zoom behavior as a system keybinding profile entry.
5. Add Pointer/3D toggle/hold commands.
6. Add focused-app Orbit/Fly/Walk set/cycle/hold commands with capability validation.
7. Add generic setting toggle/hold actions for eligible booleans, enums, and numbers.
8. Remove Blender’s independent local mode override or route it back through the daemon protocol.

Debt paid: keyboard knowledge in output math, legacy cube terminology, and competing navigation
mode authorities.

Exit: existing bit-exact motion tests remain green, and all requested state changes work while the
ball is stationary.

### Phase 9 — Barebones settings UX

**Complexity 4/5 · Feasibility 4/5**

1. Rename General to Global and render the complete registry.
2. Implement per-setting and page-level Global reset behavior.
3. Implement linked per-app controls, link/unlink, setting reset, Link all/Break all, and Reset app
   to System defaults exactly as specified in section 5.
4. Add system input-profile selection and the minimal binding editor.
5. Route all edits through commands and transactions.
6. Keep status text explicit; do not spend time on final visual polish.

Exit: every link/reset transition has a UI-independent test plus a Tk smoke pass.

### Phase 10 — Text HUD

**Complexity 3/5 · Feasibility 5/5**

1. Add the text-only persistent panel and tray visibility toggle.
2. Subscribe it to runtime snapshots rather than polling engine/config internals.
3. Show mode, focused app, nav mode, layer, semantic movement help, and held/last binding.
4. Add no-activate, work-area anchoring, DPI, monitor, and click-through handling.

Exit: the panel updates on keyboard/BLE events without BLE motion and never steals host focus.

### Phase 11 — Verification, documentation, and release hardening

**Complexity 4/5 · Feasibility 4/5**

1. Update README, HANDOFF, security docs, default/profile docs, protocol docs, and host guides.
2. Add JSON schemas and sample macros for contributors.
3. Add package-data and Nuitka checks for the new System-default/profile files.
4. Live-test rich navigation transitions in Blender, SketchUp, Unreal, Unity, and Godot.
5. Live-test keyboard hooks with layouts/AltGr, Sticky Keys, sleep/resume, session lock, Remote
   Desktop, elevated hosts, and key repeat.
6. Live-test firmware reconnect, missed notifications, debounce, simultaneous five-way behavior,
   and daemon-absent HID fallback.
7. Live-test HUD placement at multiple DPI settings and monitors.

Exit: automated checks, hardware smoke, host smoke, migrations, packaged build, and rollback are
documented honestly.

## 10. Test matrix

### Pure unit tests

- Chord canonicalization, modifier sides, exact/permissive matching, and modifier-only chords.
- Atomic active-set recomputation for every key order.
- Toggle edge behavior and OS-repeat rejection.
- Hold press/release, explicit release value, restore previous, overlapping holds, and priorities.
- Dependency closure, conflict resolution, cycle rejection, and fallback after release.
- Cross-provider chords and provider disconnect release.
- Setting capability and keybindability validation.
- Sparse System→Global→app resolution and link/reset operations.
- System profile plus per-profile user override composition.
- DSL validation, unknown commands, invalid values, and partial-file failure isolation.
- BLE packet decoding, sequences, snapshot diffs, malformed lengths, and reconnect state.
- Broker target isolation and state-revision integrity.

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
    windows_keyboard.py        global Windows backend
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
- Preserve effective settings rather than literal legacy sentinels.
- Convert identical app values into Global links where doing so preserves behavior.
- Preserve user-edited differences as explicit app overrides.
- Remove `general.buttons` only after translating any meaningful legacy mappings or documenting
  that the field never had a controller-mode consumer.
- Accept `cube` as an input alias for `3d` during migration and macro validation for at least one
  release cycle; write only `3d`.
- Legacy firmware remains rotation-only, uses keyboard bindings, and does not expose phantom
  buttons.
- New daemon with old add-ons keeps the existing wire frame fields. Broker target isolation is
  server-side. Any add-on protocol extension remains additive and versioned.
- Input-profile switching and config reload synthesize releases before swapping compiled state.

## 13. Security and open-source considerations

- Global keyboard capture must be documented and user-disableable.
- Do not log raw keystroke streams. Log only configured binding activations and validation errors.
- Pass through keys by default; suppression, if ever added, requires explicit per-binding consent.
- Filter injected keyboard events so future output commands cannot recursively trigger bindings.
- The macro DSL is an allowlisted command language, not `eval`, Python import, or shell execution.
- Sensitive setup commands are not registered as keybindable commands.
- Community device descriptors are data. Code adapters, if later supported, are explicitly trusted
  plug-ins and should be isolated from the core macro path.
- BLE input packets are untrusted: validate version, kind, lengths, control count, and sequence
  before updating pressed state.
- A provider failure releases its held controls and cannot leave a permanent forced state.

## 14. Delivery gates

Do not proceed to the next product-facing layer until the preceding gate is met:

1. **Configuration gate:** sparse inheritance and migration are stable before keybindings persist.
2. **State gate:** dependency closure and release behavior are deterministic before global hooks.
3. **Routing gate:** target isolation is complete before app-dependent binding rollout.
4. **Input gate:** keyboard provider lifecycle is safe before BLE buttons join it.
5. **Hardware gate:** versioned snapshots and disconnect release pass before the five-way profile is
   a default option.
6. **UX gate:** commands and registries are complete before the Global/per-app editor is rebuilt.
7. **Release gate:** both profiles, migrations, multi-host isolation, and live rich-mode transitions
   pass before the feature is enabled by default.

## 15. Final feasibility assessment

| Area | Complexity | Feasibility | Main risk |
|---|---:|---:|---|
| Dependency-aware runtime state | 5/5 | 4/5 | Overlapping hold/conflict semantics |
| Sparse System/Global/app settings | 5/5 | 4/5 | Behavior-preserving v8 migration |
| Keyboard chords | 4/5 | 5/5 | Windows lifecycle and layout edge cases |
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
