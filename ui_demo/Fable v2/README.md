# Astrolabe daemon — UI/UX demo (Fable v2)

A front-end-only mock of a possible final design for the Astrolabe control daemon.
**It drives nothing** — every control is interactive but edits only in-memory demo state.

Open `index.html` directly in a browser, or serve the folder with any static server.
Pages deep-link via hash: `#overview`, `#apps:global.nav`, `#apps:unreal.routing`, `#keys`, `#general:panel`, …

## Design style: **Precision Instrument Minimalism** (dark)

Carried over from the first Fable demo: graphite surfaces, 1 px hairlines, one restrained
accent, CAD-standard X/Y/Z = red/green/blue, monospaced numerals, and the product's 20°
base-cut angle only in quiet places (logo, heading ticks, the rail's sliding nav tick).
No frameworks, no webfonts, no images (all SVG inline); every animation is a short
transform/opacity transition, the only continuous one is the 3 s connection pulse, and
`prefers-reduced-motion` disables everything. The window is a fixed 880 × 560; the only
scroll regions are the setup-instructions box and the keybinding list/editor.

## What the daemon added, and how this demo absorbs it

The `rich-keybindings` work reshaped the daemon's real settings surface into four Tkinter
tabs — **3D Apps**, **Global**, **Per-App**, **Keybindings** — plus a passive text **control
panel** and a three-layer **System → Global → per-app** settings model
(`app_registry.py`, `settings_schema.py`, `settings_ui_model.py`, `binding_ui_model.py`,
`system_keybinding_profiles.json`). Two moves keep that compact:

- **Global + Per-App collapse into one master-detail.** The daemon gives every navigation
  setting a Global default that each app either *links* to or *overrides*. Instead of two
  parallel tabs, this demo pins a **“Global defaults”** row at the top of the 3D Apps list;
  the eleven apps sit below it and visibly inherit from it. Selecting Global edits the
  baseline; selecting an app edits its overrides against that baseline.
- **Two per-control buttons replace the “Default (General)” sentinels.** In v1 every
  per-app dropdown carried a fake “Default (General)” option to express inheritance. Here
  each per-app row carries two small buttons:
  - a **link button** — a **chain icon** when the control is *linked* (subdued, showing the
    Global value) and a **broken-chain icon** when it is *unlinked* (live, app-specific).
    Clicking it links ↔ unlinks; relinking discards the app’s own value.
  - a **reset button** (↺) that resets the control to its shipped **System** default. It
    appears whenever the current value differs from that System default — even while the row
    is still *linked* (i.e. the Global default itself has moved off System) — and clicking it
    **always breaks the link**, because holding the System value means no longer following
    Global. The settings column is sized so both buttons sit at full size.

  The Global-defaults row has no link (it is the baseline), so it shows only the reset
  button, which resets that Global control to System.

## Information architecture

Four pages behind a persistent left rail. Every real config key has exactly one home; this
map is the contract the eventual UI should follow.

| Region | Contents (config keys / source) |
|---|---|
| **Rail** (persistent) | Interactive ball diagram + mode-aware axis legend · live Pointer ↔ 3D toggle (`input.mode.default`, normally driven by a keybinding) · 4-item page nav · add-on handshake · daemon version / Quit. |
| **Overview** | Link facts, active input profile, runtime endpoints (`bridge`, `onshape`), control-panel state, Start at login, Recenter, clickable per-app status tiles, first-run steps. |
| **3D Apps → Global defaults** | The Global layer: **Tuning · Navigation · Routing** for the baseline every app inherits (`system_defaults.json` `global`). |
| **3D Apps → \<app\> → Setup** | Detection, supported versions, install model, security notes, Set up / Update, honest auto/manual/health instructions (`integrations.py`). |
| **3D Apps → \<app\> → Tuning** | `navigation.refresh_rate`, `orbit.sensitivity`, `pan.gain`, `zoom.gain/.dominance`, `fly.speed/.walk_speed` (rich), `orbit.pivot_hold_seconds`, `zoom.cursor_hold_seconds` — each with a link + reset button. |
| **3D Apps → \<app\> → Navigation** | Orbit: `orbit.style/.pivot`, `twist_action`, `lock_horizon`, `level_horizon_on_entry`, `selection_override`, Onshape userscript. Pan/Zoom: `zoom.target`, `zoom.behavior`, `pan.scales_with_distance`, Unity `override_dynamic_clip` / `pivot_extent_multiplier`, Blender `lock_camera_to_view`. |
| **3D Apps → \<app\> → Routing** | Per-action X/Y/Z source + inversion, per mode on rich hosts (`navigation.routing.*`). |
| **Keybindings** | Input-profile selector (`astrolabe_5way`, `keyboard_only`) · device/capability health strip · binding list · editor: chord (Record…), extra-modifier match, active app, action (built-in command **or** a setting hold/toggle/cycle/add/multiply), value, Advanced DSL, delete / restore-System. |
| **General → Device & Orientation** | `device.name/.address`, `bridge.rate_hz`, permutation-safe `input.axis_orientation`. |
| **General → 3D Defaults** | The two device-wide scheme controls with no per-app form: startup `input.mode.default` and the `orbit.pivot_fallbacks` chain editor. |
| **General → Pointer** | `pointer.cursor.gain`, `pointer.scroll.*`. Notes that physical buttons are mapped under Keybindings, not a reserved-button table. |
| **General → Control Panel** | The passive HUD: `hud.visible/.always_on_top/.click_through/.opacity/.margin/.last_binding_timeout`. |

### Why it is organized this way

- **Setup, tuning, and bindings live together.** Selecting a host gives everything about it
  under four sub-tabs — no jumping between a “Global” tab and a “Per-App” tab to compare a
  value against its default; the Global row is right there in the same list.
- **The header mode slider is the enable switch.** Each app's active control mode is a
  colour-coded **Off · Orbit · Fly · Walk** slider (standard hosts: Off · Orbit). Off is
  “disabled,” so one control expresses both facts. The app list mirrors it: an enabled app's
  status dot becomes a circled mode letter (O/F/W) keeping the integration-status colour; a
  disabled app is a dimmed dot. (The Global row has no slider — it is a baseline, not a
  running integration.)
- **Tuning = numbers, Navigation = behaviour.** Scalars you nudge for feel (rates, gains,
  dominance, hold timers, fly/walk speeds) sit in Tuning; discrete behavioural choices
  (pivots, styles, twist, horizon rules, zoom targets) sit in Navigation, one stable panel.
- **Keybindings is its own page.** Chords, input profiles, and the built-in/setting action
  vocabulary are a distinct concern from how navigation *feels*, and the binding editor is a
  full master-detail in its own right. Device presence shows as a health strip so a missing
  provider (here the XIAO test-bench) is visible at a glance.
- **The control panel gets a General sub-tab.** It is a handful of global HUD settings with
  no per-app or per-app-capable analog, so it sits with the other device-wide settings.
- **Every setting explains itself.** Rows are `label · control · link · reset · descriptor`;
  each descriptor is a single line, with any option- or mode-specific detail (what a given
  dropdown value does) moved into the ⓘ tooltip rather than the line itself.
- **Divergence is visible and reversible.** Any control whose value differs from its shipped
  System default grows a small ↺ that resets just that control to System — on the Global row
  and on each app alike. On an app the link button additionally links/unlinks it from the
  Global default. The header ↺ relinks a whole app; “Reset Global” resets the whole baseline.
  Operational state (mode/enable, install, add-in version) is outside those boundaries,
  matching `APP_PROFILE_FIELDS` / the daemon's reset scope.

### Interaction & animation rules

- Segmented controls (sub-tabs, mode slider, match/profile toggles) never repaint on click —
  they slide a thumb, exactly like the rail's Pointer/3D toggle.
- A full page render happens only on rail navigation. Sub-tab clicks swap just their pane;
  routing-mode tabs swap just the route rows; the fallback and keybinding editors re-render
  only themselves; link / reset buttons, value edits, axis picks and the mode slider mutate the DOM in
  place. Nothing outside the thing you clicked ever flashes.

## Parity notes

Field visibility follows `app_registry.py` / `settings_schema.py`: per-app Pivot hold and
Zoom hold timers, Twist action on every app, Pan-mode zoom (Zoom / Dolly) only where the host
implements both paths, and SketchUp as a full rich app. Capability-specific controls include
Fusion's Roll/Zoom/None twist set, Unity's dynamic-clip and pivot-extent, Blender's camera-lock,
and each integration's security notes from
`integrations.py`. Keybinding chords, actions, match modes, and the two System profiles are
ported from `system_keybinding_profiles.json` and `devices/descriptor_data`. Free-orbit
selection still nudges twist to Roll once, mirroring `ui.py`.

Orientation reference (unchanged): top view, front of the device toward you — X right
(pitch), Y up (yaw), Z toward you (twist).
