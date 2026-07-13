# Astrolabe daemon — UI/UX demo (Fable v2)

A front-end-only mock of a possible final design for the Astrolabe control daemon.
**It drives nothing** — every control is interactive but edits only in-memory demo state.

Open `index.html` directly in a browser, or serve the folder with any static server.
Pages deep-link via hash: `#overview`, `#apps:unreal`, `#apps:blender.routing`, `#general:pointer`, …

## Design style: **Precision Instrument Minimalism** (dark)

Carried over from the first Fable demo: graphite surfaces, 1 px hairlines, one restrained
accent, CAD-standard X/Y/Z = red/green/blue, monospaced numerals, and the product's 20°
base-cut angle only in quiet places (logo, heading ticks, the rail's sliding nav tick).
No frameworks, no webfonts, no images (all SVG inline); every animation is a short
transform/opacity transition, the only continuous one is the 3 s connection pulse, and
`prefers-reduced-motion` disables everything. The window is a fixed 880 × 560; nothing
scrolls except the setup-instructions box inside 3D Apps.

## Information architecture

Three pages behind a persistent left rail. The daemon's real config keys all have exactly
one home; the map below is the contract the eventual UI should follow.

| Region | Contents (real config keys) |
|---|---|
| **Rail** (persistent) | Interactive ball diagram + mode-aware axis legend · global Cursor ↔ 3D Nav toggle (`general.default_mode` live value) · page nav · live add-on handshakes · daemon version / Quit. |
| **Overview** | Link facts (device, status, mode), runtime endpoints (`bridge`, `onshape`), config/log paths, Start at login, Recenter 3D view, clickable per-app status tiles, first-run steps. |
| **3D Apps → Setup** | Per app: detection, supported versions, install model, security notes, Set up / Update action, auto/manual/health instructions (`integrations.py` data). |
| **3D Apps → Tuning** | `apps.<k>.rate_hz`, `bindings.orbit.sensitivity`, `bindings.pan.gain`, `bindings.zoom.gain/.dominance`, `bindings.toggle`, `advanced.fly_speed/.walk_speed` (rich), `orbit_pivot_hold_sec`, `zoom_cursor_hold_sec`. |
| **3D Apps → Navigation** | Orbit behavior: `bindings.scheme.orbit_style/.orbit_pivot`, `advanced.twist_action/.lock_horizon`, `level_horizon_on_entry`, `selection_overrides_pivot`, Onshape userscript. Pan/Zoom behavior: `bindings.scheme.zoom_mode`, `advanced.zoom_style/.pan_scales_with_distance/.override_dynamic_clip/.pivot_extent_mult/.lock_camera_to_view`. |
| **3D Apps → Routing** | Per-action X/Y/Z sources + inversion (`advanced.axis_source/.invert` per mode on rich hosts; `bindings.orbit/pan/zoom` sources on standard hosts). |
| **General → Device & Orientation** | `device.name/.address`, `bridge.rate_hz`, permutation-safe `general.axis_orientation`. |
| **General → 3D Defaults** | `general.scheme.*`, `general.level_horizon_on_entry`, `general.orbit_pivot_fallbacks` chain editor. |
| **General → Pointer & Buttons** | `general.cursor.gain`, `general.scroll.*`, `general.default_mode`, reserved `general.buttons.*`. |

### Why it is organized this way

- **Setup and bindings live together.** v1 (like the current Tkinter window) split an
  app's integration from its navigation settings into different pages; here selecting a
  host in the master list gives everything about it under four sub-tabs. One place to
  look, fewer clicks.
- **The header mode slider *is* the enable switch.** Each app's active control mode is a
  color-coded Off · Orbit · Fly · Walk slider (standard hosts: Off · Orbit) in the detail
  header. "Enabled" wasn't really a separate concept — an app is either off or being
  driven in some mode — so merging them removes a control and makes mode a first-class,
  always-visible fact. The app list mirrors it: an enabled app's status dot expands into
  a circled mode letter (O/F/W) whose ring keeps the integration-status color; a disabled
  app shows a dimmed plain dot.
- **Tuning = numbers, Navigation = behavior.** Every scalar the user nudges while dialing
  in feel (rates, gains, dominance, hold timers, fly/walk speeds) sits in Tuning; every
  discrete behavioral choice (pivots, styles, twist action, horizon rules, zoom targets)
  sits in Navigation. Fly/walk speeds are gains, so they belong in Tuning — that also
  turns Navigation into a single stable panel instead of v1's per-mode sub-panels.
- **Routing reads column-major.** Rotation actions (Pitch/Yaw/Twist or Orbit X/Y/Z) fill
  the left column, translation actions (Pan/Zoom, Fwd/Strafe/Up-Dn) the right, instead of
  zig-zagging. On rich hosts the routing-mode tabs default to the app's active mode.
- **One trackball, everywhere.** The rail diagram is the only axis reference in the app;
  hovering any X/Y/Z control (routing pickers, orientation editor, the legend itself)
  highlights that axis, and the legend swaps meaning with the global mode toggle
  (`X pitch · Y yaw · Z twist` vs `X cursor ↕ · Y cursor ↔ · Z scroll`).
- **Every setting explains itself.** Rows are `label · control · reset · descriptor`; the
  right-hand space carries a one-line explanation, ⓘ tooltips only for genuinely long
  text.
- **Divergence is visible and reversible per control.** Any control that differs from its
  shipped default grows a small ↺ next to it; clicking resets just that setting (per-app
  scheme fields go back to "Default (General)", the per-app level-horizon override is
  removed so the app follows General again). The header ↺ resets the whole app profile;
  operational state (mode/enable, install, add-in version) is outside that boundary,
  exactly like `APP_PROFILE_FIELDS` in the daemon.

### Interaction & animation rules

- Segmented controls never repaint on click — they slide a thumb, exactly like the rail's
  Cursor/3D toggle (equal-width buttons make the thumb a pure translate).
- A full page render happens only on rail navigation. Sub-tab clicks swap just their pane;
  routing-mode tabs swap just the route rows; the fallback editor re-renders only itself;
  value edits, axis picks, orientation swaps, and the mode slider mutate the DOM in place.
  Nothing outside the thing you clicked ever flashes.
- The pane that does swap enters with the same short fade/6 px rise used everywhere.

## Parity notes

Field visibility follows `trackball_daemon/binding_schema.py`: per-app Pivot hold and Zoom
hold timers, Twist action on every app, Pan-mode zoom (Zoom / Dolly) only where the host
implements both paths, SketchUp as a full rich app, Godot turntable-only with no
roll/horizon controls, Fusion's Roll/Zoom/None twist set, and each integration's security
notes from `integrations.py`. Free-orbit selection nudges twist to Roll once, mirroring
`ui.py`.

Orientation reference (unchanged): top view, front of the device toward you — X right
(pitch), Y up (yaw), Z toward you (twist).
