# Cursor routing live evidence — 2026-07-21

This record captures only the live results that changed the current baseline. Unresolved follow-up
remains in [`../../TODO.md`](../../TODO.md).

## FreeCAD — cursor leave passed

- Host: FreeCAD 1.1.1, PySide6, orthographic view.
- Artifact: TrackballNav v0.1.14.
- Exercise: Under Cursor orbit over geometry, leave the 3D viewport, re-enter at a different point,
  and repeat with both hits and empty cached state.
- Result: pass. `cursor: invalidated (viewport_leave) -> fallback until re-entry` appeared on every
  observed leave. Motion continued through the configured fallback while outside; re-entry produced
  a fresh `cursor-pivot: surface hit`, and a gesture without a fresh pixel reported
  `no cursor pixel cached yet -> fallback`.
- Baseline change: cursor-leave invalidation is no longer an open FreeCAD qualification item.

## AutoCAD — moved cursor reused stale ray

- Host: AutoCAD 2026.
- Artifact: bundled TrackballNav v0.3.16.
- Exercise: move the physical cursor between separate Under Cursor orbit gestures.
- Result: fail. Separate gestures repeatedly reported identical expanded hits, including
  `(11.807,4.391,12.085)`, despite cursor movement. The cached PointMonitor world sample was not tied
  to its physical screen pixel, so the old ray remained eligible for expansion.
- Baseline change: stale PointMonitor reuse is P0 until the physical-pixel ownership fix in current
  plugin source is published as an attributed DLL and passes the same live matrix.

## Onshape — connection/control failed, then transport root cause isolated

- Bridge report: NL-Proxy compatibility version 1.4.8.21486.
- Initial result: fail. Status alternated between connected and disconnected roughly once per second
  and no camera control was delivered.
- With the Onshape tab selected, Firefox created a new subscription about once per second. Switching
  to another Firefox tab stopped the Onshape context and left `firefox.exe` selected.
- The first coarse logs showed each subscription as `focus=False awaiting update`, which led to a
  provisional assumption that subscription itself was this client's available focus signal. A
  compatibility-focus inference and retry grace were tried.
- The grace made context appear stable, but pan had substantially increased latency, orbit did not
  move the camera, and subscriptions were still replaced. Attempts to carry gestures and accessor
  knowledge across closes, batch reads, and skip acknowledgements reduced symptoms but added
  lifecycle and ordering debt without explaining the closes; they were rejected and removed.

### Root-cause capture

- The current client did send explicit `focus=true` immediately after subscribing; the earlier log
  simply omitted that debug-level transition.
- Each focused connection then announced a valid unfragmented WebSocket message between 378,873 and
  380,204 bytes. A transport-hardening change had introduced a 64 KiB frame ceiling underneath a
  separate 256 KiB aggregate-message ceiling. The reader closed the connection with code 1009 before
  reading the payload, and Firefox's client reconnected after roughly 500 ms.
- The foundational correction is one bounded 1 MiB budget for a complete message and therefore for
  any individual frame, with the fragment count and aggregate size still checked before unbounded
  buffering. Explicit focus, acknowledged RPC ordering, and immediate physical-disconnect cleanup are
  retained; reconnect grace, logical session carry-over, batching, and unacknowledged writes are not.
- Transport tests now include an unfragmented 380,204-byte client message and true oversized frame and
  fragmented-message rejection. Full orbit/pan remains P0 until exercised with the source daemon.
