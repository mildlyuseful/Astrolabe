# Astrolabe — Meridian UI demo

A front-end-only mock of a compact settings surface for the Astrolabe control daemon.
**It drives nothing** — controls are interactive but edit only in-memory demo state.

## How to host

From this folder:

```bash
# Python
python -m http.server 8765

# or Node
npx --yes serve -p 8765
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

You can also open `index.html` directly in a browser (file://). Hash deep-links work either way.

| Hash | View |
|---|---|
| `#hosts` | Hosts (last selection) |
| `#hosts:global.feel` | Global defaults → Feel |
| `#hosts:unreal.axes` | Unreal → Axes matrix |
| `#bindings` | Keybindings |
| `#bindings:keyboard_only` | Keyboard-only profile |
| `#system` | Device, orientation, HUD, session |

## Design methodology

### Problem

The live Tkinter UI exposes **four top-level tabs** (3D Apps, Global, Per-App, Keybindings) plus nested category notebooks. Earlier prototypes collapsed Global + Per-App into one master-detail, but still used **four rail pages** and **four per-app sub-tabs**. That is too much chrome for a utility that should stay open beside CAD all day.

### Solution: three surfaces

| Surface | Owns |
|---|---|
| **Hosts** | Pinned Global defaults + 11 apps. Detail header = Off·Orbit·Fly·Walk (enable + mode). Section chips swap one pane at a time: **Install · Feel · Behavior · Axes** (no nested notebook stack). |
| **Bindings** | Input profiles, provider health, binding list, chord editor, **grouped** actions (Modes / Navigation holds / Pointer / Settings), Advanced DSL. |
| **System** | Device, permutation-safe orientation + ball, pointer gains, startup mode, ordered pivot fallbacks, HUD, Start at login, Recenter. |

Overview is **not a page**. BLE status, Pointer/3D mode, handshake, and Quit live in chrome; first-run is a dismissible strip on System; app health is list badges.

### UX optimizations (framework can catch up)

1. **Section chips instead of nested notebooks** — Install / Feel / Behavior / Axes swap a single pane (not four stacked scrolls). Full height goes to the active concern.
2. **Routing as a source×invert matrix** — lean 6-channel or rich mode groups, not dozens of labeled rows.
3. **Axis orientation as a permutation swap** — changing one source swaps with the other axis; invert stays per physical channel.
4. **Pivot fallbacks as ordered chips** — Up/Down/Add/Remove instead of a CSV string.
5. **Grouped keybinding actions** — mirrors daemon common actions + setting ops without a flat mega-list.
6. **Link / reset grammar** — chain / broken-chain + ↺ to System; Global has reset only.

### Visual language: Meridian

- Light drafting desk: cool ink-slate desktop, warm paper window, single **copper** accent
- Faint blueprint grid on the desktop (CSS only)
- CAD RGB for X/Y/Z
- Type: `Bahnschrift` / Segoe UI Variable + Cascadia Mono — **no webfont downloads**
- Fixed **780 × 500** utility frame
- Host section chips swap panes (Install / Feel / Behavior / Axes) so the active concern gets the full detail height
- Motion: short transform/opacity on rail tick, mode thumbs, segment thumbs; one 3 s BLE pulse; `prefers-reduced-motion` disables all

### Lightness rules

- No frameworks, no images except a tiny SVG logo
- Full page render only on rail navigation; host list / detail / binding editor refresh in place
- Idle cost is essentially zero (one CSS pulse)

### Parity notes

Field visibility follows the spirit of `app_registry.py` / `settings_schema.py` (Fusion twist set, Unity clip/extent, Blender camera lock, Onshape userscript). Keybinding profiles and chords mirror `system_keybinding_profiles.json`. This is a **UX prototype**, not a live projection of `SettingsUIModel` / `BindingUIModel`.

### Prototype boundary

Meridian is the current UI design prototype. Treat daemon code, packaged data,
`docs/keybindings.md`, and `settings_schema.py` as the product contract; this demo is not a runtime
source.
