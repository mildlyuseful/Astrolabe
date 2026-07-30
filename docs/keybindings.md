# Keybindings and declarative actions

Astrolabe keybindings combine normalized keyboard or BLE controls with a data-only action language.
They do not execute Python, shell commands, imports, callbacks, JSON pointers, arbitrary virtual-key
codes, or scancodes. Shared input, state, and lifecycle ownership is defined in
[`architecture.md`](architecture.md).

## Profiles and ownership

`trackball_daemon/system_keybinding_profiles.json` contains the two developer-owned System profiles:

- `astrolabe_5way` includes the production five-way controls, the XIAO three-button test-bench
  controls, and keyboard fallbacks.
- `keyboard_only` contains only keyboard controls and is usable with legacy rotation-only firmware.

Fresh installs select `astrolabe_5way`; `keyboard_only` is an explicit user choice. Selecting a
profile does not copy or rewrite it. User edits are sparse patches stored under that profile ID in
`%APPDATA%\Mildly Useful\Astrolabe\config.json`. Switching profiles preserves each profile's own patches.
The daemon never changes profiles merely because BLE hardware appears or disappears.

The ordinary Keybindings editor exposes the control chord, any number of registered app contexts,
plain-language action or setting, and setting behavior/value. No selected app means the binding is
global. It generates paired release/restoration actions for holds. Advanced DSL exposes executable
and profile contexts, explicit priority, low-level activation, and custom action lists. Invalid
edits are rejected before the live compiled profile changes.

## Matching and dependency behavior

A chord is an unordered set of normalized control tokens, for example `keyboard:ctrl` plus
`keyboard:shift`, or `ble.astrolabe:fiveway.center`. Modifier aliases such as `keyboard:ctrl` match
either physical side; side-specific tokens remain available.

- `exact` rejects extra held keyboard modifiers. For a BLE binding it also rejects unexpected
  simultaneous controls from that same device source.
- `allow_extra_modifiers` permits extra keyboard modifiers but does not turn unrelated keys or BLE
  controls into part of the chord.
- OS repeat packets do not create another activation edge.
- The compiler evaluates the complete pressed set atomically, so Ctrl+Shift behaves the same in
  either press order.

State dependencies cascade as state, not as simulated keybindings. Requesting Pan also requests its
3D and secondary-control prerequisites. If a higher-precedence request makes a prerequisite
impossible, the dependent leaf is suppressed rather than producing a mixed state. A user may bind
Shift directly to Pan or use the physically descriptive Ctrl+Shift chord; both use the same
dependency closure.

When definitions overlap, runtime precedence is explicit priority, context specificity, exact
matching, larger chord, then activation recency. The binding compiler separately suppresses a
fallback when a more-specific definition matches the same logical chord. The ordinary editor leaves
non-default priority editing in Advanced DSL; visual priority editing and profile export/import are
open product work in [`../TODO.md`](../TODO.md).

## Hold, toggle, and setting actions

The simple editor always stores low-level `activation: "hold"` and expresses user-visible behavior
through actions:

- A hold uses `setting.set_runtime` on press and identity-matched `setting.restore_previous` on
  release. Multiple holds on the same setting resolve by precedence and reveal the next valid value
  when one releases.
- A toggle uses `setting.toggle_runtime` on the press edge. Boolean settings need no values;
  non-boolean settings require two distinct valid values.
- Cycle accepts two or more distinct comma-separated enum or numeric values and advances once per
  press edge. Numeric add and numeric multiply are also press-edge runtime operations.
- `setting.set_persistent` is an explicit atomic config transaction and cannot be mixed with runtime
  actions in the same phase.

Advanced DSL's `activation: "toggle"` is a lower-level binding-latch mechanism: each newly satisfied
chord edge alternates the entire binding's active state. It is different from the simple editor's
setting toggle and is intentionally hidden from ordinary editing.

Pointer buttons are limited to paired, momentary Left/Right/Middle/X1/X2 press and release actions.
Profile reload, provider disconnect, owner replacement, and shutdown release their identity-owned
holds. Keyboard controls remain pass-through; Windows Raw Input does not suppress the key's normal
application behavior. Shipped pointer buttons and new simple-editor click actions allow extra
modifiers, so holding Shift, Ctrl, Alt, or Meta produces the foreground application's normal modified
click. Advanced DSL may still select `exact` when a click must be disabled while a modifier is held.

## App context and capabilities

`when.apps` uses registered app IDs, `when.executables` uses case-insensitive executable names, and
`when.input_profiles` narrows a binding to its own System profile. Empty context means global.
Foreground identity comes from the independent Windows foreground monitor, not the app selected in
Settings and not the most recent motion packet.

Navigation actions and per-app settings are capability-checked against the foreground host. A Fly
or Walk hold can remain physically held while an Orbit-only host is focused; the unsupported action
is inert there and can resume when focus returns to a compatible host. Global settings remain
available without a recognized foreground host.

## Schemas, examples, and validation

Packaged JSON Schema 2020-12 contracts live in `trackball_daemon/schemas`:

- `binding-profile-catalog-v1.schema.json`
- `declarative-actions-v1.schema.json`
- `device-descriptor-v1.schema.json`

Matching validated examples live in `trackball_daemon/examples`. They are contributor references,
not files automatically discovered or executed at runtime. JSON Schema checks portable structure;
the daemon parser remains authoritative for registered controls/apps/settings, setting-specific
value ranges, paired hold actions, duplicate triggers, and cross-profile rules.

Validate the shipped profiles and an optional config without starting the daemon:

```powershell
python -m trackball_daemon.validate_bindings
python -m trackball_daemon.validate_bindings --config "$env:APPDATA\Mildly Useful\Astrolabe\config.json"
```

See [`ble_device_adapters.md`](ble_device_adapters.md) for the data-only BLE descriptor and snapshot
protocol. A community device using that protocol can contribute reviewed descriptor data. A device
with a different wire protocol requires reviewed adapter code; the daemon never imports adapter
modules named by descriptor JSON.
