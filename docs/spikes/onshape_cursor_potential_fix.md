# Onshape cursor mapping — Firefox chrome / content-rect fix

Status: **APPLIED in daemon 0.1.54** (see `trackball_daemon/onshape_bridge.py` +
`docs/apps/onshape.md` §8.14). This note keeps the diagnosis that earlier attempts hit.

## The sizing bug

`_cursor_client_fraction()` must report the cursor as a fraction of the browser **web-content**
area (the page), not the top-level window client.

1. **Firefox:** `WindowFromPoint` / `MozillaWindowClass` `GetClientRect` **includes** browser chrome
   (tabs + address bar + bookmarks, ~45 px @ 125% DPI with bookmarks visible on the test machine).
   That inflates `win_h` for `_effective_canvas_inset` (horizontal scale error that tracks the left
   feature-tree width) and shifts `fy` downward (consistent vertical pivot offset).
2. **Chrome:** multiple `Chrome_RenderWidgetHostHWND` children exist; the **first** match can be a
   tiny UI widget. Use the **largest** by client area (area > 50k px²).

## Fix (current code)

`_browser_content_rect(root)`:

- Tier 1: largest `Chrome_RenderWidgetHostHWND` under the root.
- Tier 2: Firefox — top-level client minus `_firefox_content_top` (BitBlt top band; first row that
  is Onshape near-black across the **left half**; cached per hwnd/size).
- Tier 3: raw client rect.

Combined with aspect-derived `canvas_auto_left` and a calibrated Top toolbar inset (~0.05–0.09 of
content height). Offline: `tests/test_onshape_cursor_pivot.py`. Live hit-under-cursor still flagged
needs-GUI-verify.
