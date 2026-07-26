<!--
SPDX-FileCopyrightText: 2026 Dylan Lee
SPDX-License-Identifier: Apache-2.0
-->

# Vernier — a settings-window UI study

Open `index.html` in a browser. Nothing is wired to a daemon: every value is page state, and every
action mutates that state only.

## The idea

A vernier scale is the part of an instrument that lets you read a value more precisely than the main
scale allows, and it does that by adding one quiet extra row of marks — not a second dial. That is
the whole design brief here: the settings window should feel like reading an instrument, and extra
precision should cost extra *marks*, never extra *surface*.

So: paper ground, hairline rules, ink type, one instrument accent (deep teal), tabular figures, and
a strict 1010 × 632 frame — a quarter of a 1080p desktop. Nothing in the window may scroll at rest;
depth is bought with sub-tabs, and detail with disclosures the user opens deliberately. Motion is
spatial only — indicators slide the distance they actually travelled, disclosures grow from zero
height, rows arrive with a short stagger. Nothing bounces, nothing pulses for decoration, and
`prefers-reduced-motion` removes all of it.

Two rules follow from that and are worth stating, because they shape every panel:

- **Related settings are boxed, roughly in threes.** A faint outline and a small title turn a
  category into two or three answerable questions ("where does orbit turn?", "how fast?") instead
  of a ten-row list. Axis routing boxes by *triad* — pitch/yaw/twist, forward/strafe/vertical —
  because a gesture's three channels are read together or not at all.
- **A control's form should match the quantity.** An axis source is a position on a three-stop
  scale, so it is a slider with X/Y/Z stops that you drag, click, or arrow between — not a
  dropdown. An ordered fallback chain is a vertical stack, because the question is "what is tried,
  in what order", which reads down a column and not across a line.

Product allusions stay quiet: the mark is a graticule, the balls are graticules, and the accent is
the colour of an instrument's engraved fill.

## Panels

| Panel | Sub-tabs | Contents |
|---|---|---|
| **State** | Runtime · Health · About | Ball motion view, input/navigation/layer state, control help, transport, the resolved path a packet takes into the focused host, service and integration health, build and path identity |
| **Device** | Identity · Hardware | BLE name/address (own reset layer, applies on reconnect), the three firmware adapters with their advertised names, UUIDs, and controls |
| **Global** | Input · Orientation · Orbit · Pan / Zoom · Rates · Routing · Panel | Every Global-scope setting, grouped by the daemon's own categories, plus the physical transform and the control-panel (HUD) settings |
| **Per-App** | Rates · Orbit · Pan / Zoom · Motion · Routing · Host setup | One app's overrides, with link/unlink and reset per row and for the whole app. The app switcher *is* the panel title — every value below means something different per app, and "per-app" is context the rail already gave you |
| **3D Apps** | Supported · Experimental | Integration cards: detection, verified host versions, runtime health, enable, set up/update, and the full instruction set behind a disclosure |
| **Keys** | Bindings · Input sources | Profile-scoped declarative bindings with chord recording, and required-control health |

The tray menu (titlebar ☰) and the passive control panel (bottom-right of the page, outside the
settings frame, because that is where it actually floats) are both live against the same state.

## Behaviour ported from the daemon

Resolution follows `config_resolver.py` exactly:

```
app override  →  Global override (only if the host can represent it)  →  app System  →  Global System
```

- **Link / unlink** (the chain and broken-chain icon on each app row, *Link all to Global* /
  *Break all links* in the header) match `settings_ui_model.py`: linking removes the app override;
  unlinking writes an override pinned at whatever the app resolves to right now; *Break all links*
  pins every applicable setting.
- **Reset ↺** matches the two different meanings the daemon gives it. On a Global row it drops the
  override so the System default resolves again. On an app row it pins the app's concrete System
  value — an override, so the row stops following Global. It is offered exactly when the daemon
  offers it (`linked or differs_from_system` for apps, `overridden` for Global).
- A linked row whose Global value the host cannot represent says so and shows the app's System value
  resolving underneath — the app stays linked.
- **Physical transform** sources swap rather than duplicate, and resetting one axis restores the
  permutation, including the partner axis.
- Capability gating is real: Godot has no roll, no Free orbit style, and no horizon settings; lean
  hosts route six generic channels while rich hosts route named mode actions; Unity alone shows the
  dynamic-clipping and pivot-extent controls.
- The red `!` beside **Twist action** appears under the same condition as `ui.py`: effective orbit
  style is Free, twist is not Roll, and the host supports roll.
- Release tier and host-version compatibility are stated as two separate facts, never merged.

## Deliberate departures

Two places where this study does not copy the current window:

1. **Axis routing is one row per action**, boxed by triad. The daemon has separate settings for an
   action's source and its inversion, and the current window renders two rows. Here they are a
   single row — a three-stop slider and an invert switch — and the row's link and reset act on both
   setting IDs. A source and its sign are one decision, and 52 rows do not fit in a quarter-screen
   window.
2. **Nullable booleans get a real tri-state.** `navigation.level_horizon_on_entry` is
   `On / Off / Inherit` here. The current generated window renders nullable booleans through the
   choice branch, whose value list for a boolean is empty, so the only selectable option is
   `None` — the setting cannot be turned on or off from that row.

## Files

- `index.html` — window shell and the SVG icon sprite (37 icons, 16-px grid, `currentColor`)
- `styles.css` — tokens for light and dark, components, motion
- `data.js` — setting registry, app registry, system defaults, device descriptors, binding profiles,
  and a frozen runtime snapshot, transcribed from the daemon
- `ball.js` — quaternion integration and the two sphere views
- `app.js` — state, resolution, mutations, rendering

## The ball views

`cube_test.py` integrates the firmware's `(rx, ry, rz)` radian deltas as an axis-angle increment
into a running quaternion so a cube tracks the ball 1:1. Both views here do the same to a sphere:

- **State → Runtime** shows the large graticule sphere with a pole fiducial, a twist ring, and the
  cue for the current mode — an arrow for 3D, or a synthetic cursor with a trail and wheel notches
  for Pointer, gated by the live scroll deadzone and dominance values.
- **Global → Orientation** shows a small sphere whose three needles are the logical X/Y/Z axes, so
  changing a source or an inversion moves the needle it names.

With no device attached, motion comes from a gentle synthetic stream (pausable) plus direct input:
drag inside the limb to rotate, drag the outer ring to twist.
