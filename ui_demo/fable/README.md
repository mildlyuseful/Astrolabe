# Astrolabe daemon — UI/UX demo (Fable)

A front-end-only mock of a possible final design for the Astrolabe control daemon.
**It drives nothing** — every control is interactive but edits only in-memory demo state.

Open `index.html` directly in a browser, or serve the folder with any static server.
Pages deep-link via hash: `#overview`, `#apps`, `#bindings:unreal`, `#general`, …

## Design style: **Precision Instrument Minimalism** (dark)

The daemon is a background utility for people who live in CAD viewports, so the UI reads
like a well-made instrument panel rather than a consumer app:

- **A small utility window.** Everything fits a fixed ~920 × 600 window (about a quarter of
  a 1080p screen). No page ever scrolls: content is reorganized into four pages with
  sub-tabs, two-column field grids, and a master–detail app list. Long explanations live in
  hover tooltips (ⓘ) instead of paragraph noise.
- **Graphite & hairlines.** Near-black blue-tinted surfaces, 1 px hairline borders, one
  restrained accent color. No gradients-as-decoration, no shadows-as-chrome.
- **A subtle 20° signature.** The product's base-cut angle appears only in quiet places:
  the logo (circle + 20° line through its center), the tick before section headings, and
  the sliding nav underline. Nothing in the UI names or explains it.
- **CAD-native color language.** X/Y/Z always render red/green/blue — the convention every
  supported host (Blender, Fusion, SolidWorks…) already uses — in the axis pickers and in
  every ball diagram.
- **Numerals are instruments.** All values, versions, paths, and addresses are set in a
  monospaced face; prose stays in the system UI face.
- **Lightweight by construction.** No frameworks, no webfonts, no images (all SVG inline),
  ~0 idle CPU: every animation is a short transform/opacity transition; the only continuous
  animation is a 3 s connection pulse. `prefers-reduced-motion` disables everything.

## Layout

| Page | Sub-tabs / contents (parity with the real Tkinter window) |
|---|---|
| **Overview** | Link status, tray-level Cursor ↔ 3D toggle, add-on handshakes, runtime endpoints, and the interactive axis diagram — one screen. |
| **3D Apps** | Master–detail: 11-host list with status dots; detail pane shows detected version + compatibility warnings (green/amber/red), install model, Enabled, Set up / Update / Reinstall, and always-visible setup instructions with Copy. |
| **Bindings** | Per-app user overrides. Sub-tabs: **Tuning** · **Mode** (Orbit / Fly / Walk sub-panels) · **Pan / Zoom** · **Axes & Routing** (per-mode tabs + live axis diagram). Standard apps get **Tuning** · **Control Scheme** · **Axes & Routing**. |
| **General** | Sub-tabs: **Device & Orientation** (permutation-safe swap editor + diagram) · **3D Scheme** (defaults + failure fallback chain editor) · **Pointer & Mode**. |

Ball-axis diagrams appear in every menu that routes axes; hovering any X/Y/Z picker
highlights that axis of rotation on the diagram. Orientation reference: top view, front of
the device toward you — X right (pitch), Y up (yaw), Z toward you (twist).
