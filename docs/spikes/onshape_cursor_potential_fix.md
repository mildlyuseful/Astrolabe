# Onshape cursor mapping — status (daemon 0.1.57)

**Live-verified.** Under-cursor orbit works via a page userscript; residual error is small / mostly
imperceptible.

## Why this approach

Previous approaches (Win32 content-rect + BitBlt / screen-DC canvas measure / `view.extents`
auto-left) could not recover an exact canvas size without screen capture, and `view.extents`
aspect does **not** match `#canvas` (~0.98 vs ~1.72 live). Screen capture was ruled out.

The only exact source is DOM: `document.getElementById("canvas").getBoundingClientRect()`.

## Current approach

A Violentmonkey/Tampermonkey userscript on `cad.onshape.com` posts canvas NDC to
`https://127.51.68.120:8181/trackball/pointer`.

- Source: `trackball_daemon/onshape_bridge.py` (`_POINTER_USERSCRIPT` / `pointer_userscript_source()`),
  also served at `/trackball/pointer.js`.
- Install from the daemon UI: Enable/Re-check dialog, Per-App Bindings → **Copy userscript…**, or
  the warning when selecting Orbit pivot = cursor (optional “Do not show again”).
- Steps: install Tampermonkey/Violentmonkey → new script → paste → save → reload Onshape.

## Simpler install alternatives (future)

See `docs/apps/onshape.md` §8.14 table: Greasy Fork one-click, bookmarklet, browser extension,
missing-sample warning. Not shipped yet — copy-from-daemon is the supported path.
