# Astrolabe architecture

This document records the current cross-component contract shared by the firmware, desktop daemon,
and host integrations. It intentionally excludes user instructions, open work, release snapshots,
and host-specific API detail.

See [`../README.md`](../README.md) for user operation, [`../TODO.md`](../TODO.md) for unresolved work,
and [`apps/`](apps/) for host-specific implementation knowledge.

## System boundary

Astrolabe has three responsibility layers:

- The firmware owns sensor acquisition, geometric fusion, device-local controls, standalone HID
  behavior, and the custom BLE motion/input stream.
- The daemon owns foreground context, binding semantics, persistent settings, live control state,
  pointer output, physical and per-app mapping, navigation target selection, and transport routing.
- Host integrations translate the shared navigation contract into a host camera/view API. They do
  not own device bindings, foreground policy, user configuration, or global navigation state.

The active firmware fuses two optical sensors into three-axis ball rotation. The custom motion value
contains three little-endian `float32` rotation deltas in radians. Input-capable devices add sequenced
full-state control snapshots; exact UUIDs, packet layouts, descriptor rules, and sequence handling
are defined in [`ble_device_adapters.md`](ble_device_adapters.md).

Subscribing to the rotation stream transfers pointer ownership to the daemon: firmware suppresses
its HID pointer/button output so the same physical action is not delivered twice. Unsubscribe or
disconnect returns ownership to the standalone HID path.

The debug cube is a host-neutral math reference and diagnostic consumer. It is not a place for
host-specific signs, scales, pivots, or camera conventions.

## Sources of truth

| Concern | Authority |
|---|---|
| Supported app IDs, order, display identity, process selectors, transports, modes, and capabilities | [`../trackball_daemon/app_registry.py`](../trackball_daemon/app_registry.py) |
| Stable setting and command IDs, validation, scope, capability predicates, operations, and UI metadata | [`../trackball_daemon/settings_schema.py`](../trackball_daemon/settings_schema.py) |
| Setup, detection, installation, update, consent text, and health checks | [`../trackball_daemon/integrations.py`](../trackball_daemon/integrations.py), referencing canonical `AppSpec` records |
| Concrete packaged System settings | [`../trackball_daemon/system_defaults.json`](../trackball_daemon/system_defaults.json) |
| Developer-owned host alignment | [`../trackball_daemon/host_profiles.json`](../trackball_daemon/host_profiles.json) |
| Immutable System binding bases | [`../trackball_daemon/system_keybinding_profiles.json`](../trackball_daemon/system_keybinding_profiles.json) |
| Persistent user state and transactional mutation | [`../trackball_daemon/config_store.py`](../trackball_daemon/config_store.py) |
| Pure setting inheritance | [`../trackball_daemon/config_resolver.py`](../trackball_daemon/config_resolver.py) |
| Legacy migration compatibility | `config.py` and frozen `default_profiles.json`; new defaults do not belong there |
| Device control namespaces and BLE snapshot mappings | `trackball_daemon/devices/descriptor_data/` |
| Portable contributor data shapes | `trackball_daemon/schemas/`; daemon parsers remain authoritative |
| Open defects, deferred work, and unclosed verification | [`../TODO.md`](../TODO.md) |

`app_registry.py` is the only code-owned supported-app identity and ordering table. Setup definitions,
profile data, settings, routing, and UI projections validate against it rather than repeating app IDs
or capabilities.

A setting is exposed for an app only when its capability predicate matches and every advertised value
has a distinct runtime consumer. Stored shared-shape data does not by itself make a feature
implemented.

## End-to-end data flow

Motion follows this path:

```text
sensor deltas
  -> firmware fusion and integrated rotation
  -> BleTransport and selected device adapter
  -> MotionSample
  -> App captures foreground target and one RuntimeSnapshot revision
  -> OutputEngine applies one immutable mapping for the complete sample
       -> pointer mode: bounded OS pointer/wheel output
       -> 3D mode: NavigationEnvelope(target, deltas, state revision)
  -> NavigationRouter
  -> target transport
  -> host-owned UI/camera thread
  -> camera or view
```

Control input follows a separate normalized path:

```text
BLE full-state snapshots or Windows keyboard events
  -> input provider
  -> InputAggregator atomic pressed-set transition
  -> BindingController
  -> SerializedCommandQueue
  -> RuntimeStore
  -> immutable RuntimeSnapshot
  -> OutputEngine, NavigationRouter profile delivery, and passive HUD consumers
```

For each motion sample, the selected foreground target, runtime revision, and mapping must agree.
Focus is captured before transformation; a sample may not be mapped for one app and delivered to
another. Unknown or disabled foreground context produces no 3D navigation target. The app selected
in Settings is an editing choice, not a runtime focus fallback.

BLE callbacks and transport-reader threads do not perform slow host API work. They parse, normalize,
accumulate, or publish immutable values; host interaction occurs on the thread required by the
destination API.

## Persistent configuration and live state

### Persistent configuration

`ConfigStore` owns user persistence. A typed transaction clones the current state, applies all
operations, validates the complete candidate, atomically replaces persistent state, and publishes one
deeply immutable snapshot plus a structured change event.

Absence from an override map means inheritance; sentinel values must not be reintroduced.

Resolution is:

- Global setting: explicit Global override, otherwise packaged global System value.
- App setting: explicit app override, otherwise a compatible Global override, otherwise a packaged
  app-specific System value, otherwise the packaged global System value.
- Device identity uses its separate System/user path.
- Operational app state such as Enabled, setup completion, and observed add-on information remains
  outside the setting hierarchy.

Legacy dictionaries and migrations are isolated at the persistence boundary. New feature consumers
use stable IDs, typed transactions, snapshots, and domain accessors. Invalid or corrupt persisted data
must fall back safely without overwriting the source merely because loading failed.

The settings and keybinding interfaces consume `SettingsUIModel` and `BindingUIModel` projections,
not mutable config maps. Packaged System defaults and binding bases remain developer-owned; user
edits are sparse overrides.

### Live state

Persistent configuration is not live control state.

`RuntimeStore` is the authority for the focused context, input mode, navigation mode/layer, held
bindings, runtime setting overrides, help text, and last binding event. It resolves a base from one
immutable config snapshot and layers runtime latches and identity-owned requests above it.

`SerializedCommandQueue` is the only mutation path into `RuntimeStore`. Each command or batch
publishes one coherent immutable `RuntimeSnapshot` with a monotonic revision.

State dependencies and precedence are resolved in the runtime layer. A dependent request carries its
prerequisites; if a prerequisite loses a conflict, the dependent leaf is suppressed rather than
creating an unreachable mixed state. A held navigation mode unsupported by the focused app is inert
there and may resume when focus returns to a compatible app.

`OutputEngine` derives its mode from `RuntimeSnapshot`; it does not own an independent mutable mode.
The HUD is a passive snapshot consumer and never becomes a state authority.

## Input and binding contract

All providers expose immutable normalized controls and events identified by stable
`source_id:control.id` tokens. GATT UUIDs, bit positions, virtual-key details, and native callback
structures stop at their provider boundaries.

`InputAggregator` owns the complete pressed set across providers and publishes one final pressed set
per transaction. Provider disconnect, unhealthy state, profile change, receiver restart, session
boundary, and shutdown release provider-owned controls atomically. A release-all operation may
synthesize releases; reconciliation must never invent a press or activation edge.

The keyboard provider is lazy and starts only when the compiled profile references keyboard
controls. Keyboard input remains pass-through and does not steal focus or suppress ordinary
application handling. `GetAsyncKeyState` is restricted to lifecycle reconciliation and release-only
fail-safe checks for controls already believed held; packet processing never polls Shift or another
modifier to choose an action.

Binding profiles are declarative data. The action surface is allowlisted and contains no evaluation,
shell execution, imports, callbacks, raw config pointers, arbitrary virtual-key codes, or scancodes.
Profile data is validated completely before replacing the active compiled profile.

The binding compiler expands selectors and evaluates the complete pressed set. `RuntimeStore` remains
the authority for dependencies and precedence. Pointer buttons are paired momentary actions with
identity-owned release behavior; the OS pointer-button sink is their only delivery boundary.

Foreground context comes from the independent foreground monitor, not from the Settings selection or
the most recent motion packet. Context changes recompute bindings even while the ball is stationary.

See [`keybindings.md`](keybindings.md) for matching, actions, dependencies, schemas, and contributor
rules.

## Motion mapping and output ownership

Mapping order is a contract:

1. Firmware emits fused physical rotation in radians.
2. Global physical orientation maps raw sensor axes into one logical body frame exactly once.
3. The active app's user source, inversion, gain, and action settings map logical motion into
   orbit/pan/zoom or richer navigation actions.
4. The immutable host baseline aligns those actions with the host's camera conventions exactly once.
5. The host integration applies the resulting motion to its camera or view.

Lean integrations receive host-aligned deltas from the daemon. Rich, mode-aware integrations receive
the host baseline in the additive transport profile and apply it after choosing the active
Orbit/Fly/Walk action. `AppSpec` capability data and `host_profiles.json:apply_in_daemon` must agree so
the baseline has exactly one owner.

Do not move host-specific signs or scales into firmware fusion, global physical orientation, pointer
math, or the debug cube. Do not compensate in both daemon and add-on code.

Each packet uses one immutable output mapping. Config refresh and focus changes must not expose a
partially rebuilt mapping. Pointer output carries fractional cursor and wheel remainders rather than
discarding sub-unit motion.

See [`default_profiles.md`](default_profiles.md) for default ownership and host-alignment tuning.

## Focus, routing, and transport isolation

Foreground app identity is resolved through `app_registry.py`. Integrations may add a downstream
viewport-focus gate, but transport connection alone must not broadcast motion or make another app a
delivery target.

`NavigationEnvelope` is immutable and carries one registered target app, finite orbit/pan/zoom
deltas, and the runtime-state revision under which those deltas were produced.

`NavigationRouter` is the sole daemon-side delivery boundary for broker and direct transports. It
serializes target changes, profile changes, state revisions, and submissions.

Routing invariants:

- A sample is accepted only for the active target.
- A stale state revision is rejected.
- A target change discards pending motion for both the old and new targets.
- A profile change or newer state revision discards deltas accumulated under the older
  interpretation.
- Pending motion is never relabeled, flushed into, or broadcast to another target.
- Each target owns its rate, accumulator, profile revision, and delivery state.
- Floating-point deltas are coalesced and flushed at the configured rate; producers do not block on
  host delivery.

Socket integrations use loopback newline-delimited JSON. A client hello identifies its app, loaded
code version, host information where available, and process. A daemon frame contains `o`, `p`, `z`,
`op`, `os`, and `zm`, with an optional additive `adv` object. Clients ignore unknown additive keys and
use safe defaults when keys are absent. There is no separate broker protocol-version field; target
isolation uses the existing hello app identity.

`adv` is the complete additive profile for the frame target. The daemon runtime is authoritative for
the delivered navigation mode; host-local mode state must not compete with it.

Transport, setup, reload, and host-thread details belong in the matching [`apps/`](apps/) guide.

### Direct transports

- SolidWorks uses a direct out-of-process COM driver attached to an existing application instance.
- Onshape uses the fixed loopback TLS/WAMP endpoint `127.51.68.120:8181`; configuration cannot widen
  that listener. Browser foreground plus bridge connection selects the coarse context, and the
  bridge's own focus signal is the final camera-delivery gate.
- AutoCAD navigation uses only the in-process GraphicsSystem plugin. Daemon-side AutoCAD COM code
  stages, trusts, and `NETLOAD`s the plugin; it is not a concurrent camera transport. The retired COM
  navigation implementation remains under `archive/autocad_com_transport/` as historical evidence.

## Shared navigation semantics

Canonical pivot IDs are `camera`, `screen_center`, `cursor`, `selection`, `cursor_3d`, `object`, and
`origin`. Capability data controls which IDs an app exposes.

Shared meanings:

- `camera` is turn-in-place, not viewport center.
- `selection` is distinct from aggregate model or project center.
- `cursor_3d` is distinct from the screen pointer.
- Ray-derived orbit pivots require a real supported hit.

The configured primary pivot is tried first. If it is unsupported or fails, resolution restarts at
the beginning of the global fallback chain. Unsupported, unknown, and duplicate entries are skipped.
An empty usable chain means no orbit after the primary fails; integrations must not add hidden
host-local fallbacks.

When supported and enabled, a non-empty selection may override external orbit pivots and To Cursor
zoom. A `camera` primary remains turn-in-place and is exempt from that override.

Orbit and cursor-zoom gesture targets have independent lifetimes:

- The orbit hold controls recapture of ray-derived orbit pivots. Pan or zoom invalidates that pivot.
- The zoom hold applies only to To Cursor zoom. Pan preserves that target; view rotation invalidates
  it.
- A To Cursor surface miss may synthesize a point on the cursor ray at a meaningful scene depth so
  the cursor remains fixed.
- An orbit ray miss remains strict and continues the configured fallback chain.

Optional horizon leveling occurs once when entering a fixed-horizon mode. It preserves view
direction, eye/target distance, and the active pivot rather than creating a second navigation step.

Current app exposure and deliberate limitations are recorded in
[`feature_parity.md`](feature_parity.md). Open parity work belongs in [`../TODO.md`](../TODO.md).

## Threading, lifecycle, and failure safety

The process-lifetime single-instance guard is acquired before constructing `App` or starting BLE,
pointer, broker, or host transports. A second instance must not become a second controller.

The GUI owner thread owns Tk windows and rendering. Tray and worker callbacks marshal UI operations
to that thread. Host APIs are called only on their required owner or UI thread; reader and producer
threads may parse and accumulate but do not mutate host cameras directly unless the host contract
explicitly assigns that worker ownership.

Sensitive attachment, trust, certificate, and loader services require both successful setup and an
Enabled integration. All network listeners are loopback-only. Setup must not silently trust a
certificate, reduce a host's security policy, create unrestricted inbound access, or launch a
supported host as a side effect.

Shutdown and failure paths release binding-owned state, provider-held controls, and pointer-button
ownership before transports disappear. Reloading a profile or replacing an owner follows the same
release-first rule.

One host operation failure should be logged and skip that operation without blanking a view,
terminating the BLE stream, or disconnecting unrelated integrations. Document/view/pivot/cursor
caches require explicit invalidation when their owning context or navigation semantics change.
Orbit-pivot and cursor-zoom caches remain independent.

Detailed permission, listener, and reversal rules are in [`security.md`](security.md).

## Change and verification discipline

- Update behavior, focused tests, user documentation, and the owning maintainer guide together.
- Bump daemon or add-on versions only when shipping the corresponding code. Keep each add-on's source,
  bundled payload, and version markers synchronized; AutoCAD source changes require a DLL rebuild.
- Run focused pure tests first, then the full suite, then live GUI verification for every changed host.
- Do not describe source review, mocked APIs, or headless camera math as live viewport verification.
- Put unresolved concerns in `TODO.md`, not temporary phase ledgers or evergreen documentation.

The exact current release checklist and manual matrices are in
[`release_verification.md`](release_verification.md). Completed run evidence belongs under
[`../archive/`](../archive/).

## Related current documentation

- [`../AGENTS.md`](../AGENTS.md): contributor task router and documentation ownership.
- [`../README.md`](../README.md): user capabilities, setup, operation, and troubleshooting.
- [`../TODO.md`](../TODO.md): sole active backlog and unresolved verification ledger.
- [`ble_device_adapters.md`](ble_device_adapters.md): BLE motion, snapshots, descriptors, and adapters.
- [`default_profiles.md`](default_profiles.md): packaged defaults, host alignment, and tuning.
- [`keybindings.md`](keybindings.md): profiles, chords, dependencies, declarative actions, and schemas.
- [`feature_parity.md`](feature_parity.md): capability-gated host feature exposure.
- [`security.md`](security.md): permissions, listeners, consent, reversal, and hardening.
- [`release_verification.md`](release_verification.md): build and verification claim boundaries.
- [`apps/`](apps/): host transports, APIs, threading, setup, camera behavior, and earned maintainer
  knowledge.
