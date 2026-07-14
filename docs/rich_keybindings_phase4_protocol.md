# Phase 4 navigation transport baseline

This freezes the broker and direct-driver contracts at Phase 4's starting commit `43832a7` before
target isolation changes server-side ownership. It is an implementation checkpoint, not a promise
that version numbers belong in evergreen user documentation.

## Socket broker contract

- Endpoint: daemon-configured TCP port on `127.0.0.1` (normally `47900`).
- Framing: UTF-8 newline-delimited JSON in both directions.
- Client hello: one object containing `type: "hello"`, stable `app`, loaded add-on `version`, host
  version where available, and process `pid`.
- Daemon frame: `o` (three orbit floats), `p` (two pan floats), `z` (zoom float), `op` (orbit
  pivot), `os` (orbit style), and `zm` (zoom target). The additive `adv` object is omitted only when
  unset; add-ons ignore unknown additive keys.
- There is no broker protocol-version field. Phase 4 therefore keeps the hello and frame shapes
  unchanged and performs target isolation entirely from the existing hello `app` identity.

Starting bundled client inventory:

| App hello ID | Loaded-code version | Synchronized source(s) |
|---|---:|---|
| `fusion360` | `0.1.24` | `ADDIN_VERSION`, `TrackballNav.manifest` |
| `blender` | `0.1.22` | `bl_info`, `ADDIN_VERSION`, `version.json` |
| `freecad` | `0.1.13` | `ADDIN_VERSION`, `version.json` |
| `sketchup` | `0.2.13` | loader/main `ADDIN_VERSION`, `version.json` |
| `unreal` | `0.2.13` | `ADDIN_VERSION`, `.uplugin`, `version.json` |
| `unity` | `0.1.15` | `AddinVersion`, `package.json`, `version.json` |
| `godot` | `0.1.12` | `ADDIN_VERSION`, `plugin.cfg`, `version.json` |
| `rhino` | `0.1.18` | `ADDIN_VERSION`, `version.json` |
| `autocad` | `0.3.15` | `PluginVersion`, project version, bundled `version.json` |

Because no socket consumer or frame field changes in Phase 4, none of these markers should move.

## Direct-driver contracts

- SolidWorks has no socket hello. `SolidWorksDriver` attaches to an already-running COM instance,
  reports `RevisionNumber`, and accumulates six floats until its COM worker flushes them.
- Onshape has no broker hello. `OnshapeBridge` exposes the separate NL-Proxy-compatible TLS/WAMP v1
  endpoint at `127.51.68.120:8181`, reports `NLPROXY_VERSION = 1.4.8.21486`, and accepts motion only
  when the browser client is subscribed and reports focus.
- Phase 4 may add a daemon-internal envelope/router around these drivers, but must not alter either
  host protocol or camera math.

## Starting routing and focus behavior

`App._handle_ble_packet` captures one foreground context before transforming the packet. The same
captured enabled app is retained in `_packet_app_key` through `OutputEngine.handle_packet` and
`App._nav_sink`, so the packet's mapping and destination agree. An unknown/disabled foreground app
drops navigation. Onshape resolves only for a registered foreground browser while its bridge is
connected; connected-but-background remains non-targeted.

At the baseline, the known defect was downstream of that capture: `NavBroker` had one
accumulator/rate/scheme and broadcast each frame to every connected socket client. A focus switch
could also leave an old transport accumulator pending.

## Implemented Phase 4 contract

- `NavigationEnvelope` is immutable and carries one registered target, finite orbit/pan/zoom values,
  and the monotonic runtime-state revision captured for the BLE packet.
- `NavigationRouter` serializes focus changes, profile changes, revision changes, and submissions
  across socket clients, SolidWorks COM, and the Onshape bridge.
- Each broker target owns its accumulator, refresh period, scheme/profile revision, accepted and
  pending state revisions, delivery serial, and discard diagnostics.
- Only clients whose existing hello `app` equals the active envelope target receive its frame.
  Multiple clients for that same app receive the same frame; every other client receives nothing.
- The deterministic focus-change policy is **discard old pending motion**. The newly selected target
  also begins clean. Old deltas are never finished into, flushed to, or relabeled as a new target.
- A profile change or newer runtime-state revision discards pending deltas produced under the older
  interpretation. A stale revision is rejected.
- No matching client means pending socket motion is discarded. A later reconnect begins clean.
- Onshape may remain connected in the background, but daemon navigation selects no target until its
  registered browser is foreground. SolidWorks and Onshape expose the same discard boundary without
  changing their camera calculations or host protocols.

The implementation changed no hello field, daemon frame field, host consumer, or add-on source.
Accordingly, every version in the inventory above remains unchanged.
