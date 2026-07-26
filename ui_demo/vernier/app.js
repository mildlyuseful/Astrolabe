/* SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Astrolabe — "Vernier" UI study.
 *
 * All state lives in this page. Resolution order, link/unlink, and reset-to-System behaviour are
 * ported from trackball_daemon/config_resolver.py and settings_ui_model.py so the demo cannot
 * imply an inheritance model the daemon does not have:
 *
 *   app override → Global override (only if the host can represent it) → app System → Global System
 *
 *   link      remove the app override; the app follows Global again
 *   unlink    write an override pinned at whatever value the app resolves to right now
 *   reset ↺   pin the app's concrete System value (an override, so it stops following Global)
 *   reset ↺   on Global: drop the override so the System default resolves again
 */

/* ------------------------------------------------------------------- state */

const state = {
  panel: "state",
  sub: { state: 0, device: 0, global: 0, perapp: 0, apps: 0, keys: 0 },
  theme: "light",

  /* user layers, sparse, exactly like config.json v9 */
  deviceOverrides: { "device.name": "Astrolabe" },
  globalOverrides: {
    "pointer.cursor.gain": 240.0,
    "navigation.refresh_rate": 60,
    "navigation.orbit.twist_action": "dolly",
    "hud.opacity": 0.82,
  },
  appOverrides: Object.fromEntries(APPS.map((a) => [a.id, {}])),

  selectedApp: "blender",
  profile: "astrolabe_5way",
  bindingOverrides: { astrolabe_5way: {}, keyboard_only: {} },
  selectedBinding: "keyboard.ctrl.3d",
  capturing: false,

  /* runtime, standing in for RuntimeStore */
  inputMode: "3d",
  navMode: "orbit",
  layer: "primary",
  foreground: "blender",

  openCards: new Set(),
  advancedBinding: false,
  startAtLogin: RUNTIME.startAtLogin,
  menu: null,          // null | "tray" | "app" | "profile"
};
state.appOverrides.blender = {
  "navigation.orbit.sensitivity": 1.25,
  "navigation.orbit.pivot": "cursor",
};

/* ------------------------------------------------------------- resolution */

function systemGlobal(id) { return SYSTEM_DEFAULTS.global[id]; }
function systemApp(appId, id) {
  const overrides = SYSTEM_DEFAULTS.apps[appId] || {};
  return id in overrides ? overrides[id] : SYSTEM_DEFAULTS.global[id];
}
function systemDevice(id) { return SYSTEM_DEFAULTS.device[id]; }

function globalView(spec) {
  if (spec.scope === "device") {
    const overridden = spec.id in state.deviceOverrides;
    return {
      spec, overridden,
      value: overridden ? state.deviceOverrides[spec.id] : systemDevice(spec.id),
      system: systemDevice(spec.id),
    };
  }
  const overridden = spec.id in state.globalOverrides;
  return {
    spec, overridden,
    value: overridden ? state.globalOverrides[spec.id] : systemGlobal(spec.id),
    system: systemGlobal(spec.id),
  };
}

function appliesTo(spec, app) {
  return spec.scope === "global_and_app" && spec.cap.every((c) => app.caps.has(c));
}

function choicesForApp(spec, app) {
  switch (spec.id) {
    case "navigation.mode": return app.modes.slice();
    case "navigation.orbit.style": return withDefault(spec, app.orbitStyles);
    case "navigation.orbit.pivot": return withDefault(spec, app.pivots);
    case "navigation.orbit.twist_action": return app.twistActions.slice();
    case "navigation.zoom.target": return withDefault(spec, app.zoomTargets);
    case "navigation.zoom.behavior": return app.zoomBehaviors.slice();
    default: return spec.choices ? spec.choices.slice() : [];
  }
}
function withDefault(spec, values) {
  const list = values.slice();
  if (spec.choices && spec.choices.includes("default") && !list.includes("default")) list.unshift("default");
  return list;
}
/* the per-app editor never offers the "default" sentinel: link/unlink replaces it */
function editableChoices(spec, app) {
  return choicesForApp(spec, app).filter((v) => v !== "default");
}
function validForApp(spec, app, value) {
  const choices = choicesForApp(spec, app);
  return !choices.length || choices.includes(value);
}

function appView(appId, spec) {
  const app = APPS_BY_ID[appId];
  const overrides = state.appOverrides[appId];
  const choices = editableChoices(spec, app);
  const globalRaw = state.globalOverrides[spec.id];
  const globalCompatible = !choices.length || choices.includes(globalValue(spec.id));
  let value, layer;
  if (spec.id in overrides) { value = overrides[spec.id]; layer = "app"; }
  else if (spec.id in state.globalOverrides && validForApp(spec, app, globalRaw)) { value = globalRaw; layer = "global"; }
  else if (spec.id in (SYSTEM_DEFAULTS.apps[appId] || {})) { value = systemApp(appId, spec.id); layer = "system_app"; }
  else { value = systemGlobal(spec.id); layer = "system"; }
  return {
    spec, appId, value, layer, choices,
    system: systemApp(appId, spec.id),
    linked: !(spec.id in overrides),
    globalCompatible,
  };
}
function globalValue(id) {
  return id in state.globalOverrides ? state.globalOverrides[id] : systemGlobal(id);
}
function appValue(appId, id) { return appView(appId, SETTINGS_BY_ID[id]).value; }

function appSettings(appId) {
  const app = APPS_BY_ID[appId];
  return SETTINGS.filter((spec) => appliesTo(spec, app));
}
function allAppLinked(appId) { return Object.keys(state.appOverrides[appId]).length === 0; }

/* ---------------------------------------------------------------- mutations */

function setGlobal(id, value) {
  const spec = SETTINGS_BY_ID[id];
  if (spec.scope === "device") { state.deviceOverrides[id] = value; return; }
  if (isAxisSource(id)) return setAxisSource(id, value);
  state.globalOverrides[id] = value;
}
function resetGlobal(id) {
  const spec = SETTINGS_BY_ID[id];
  if (spec.scope === "device") { delete state.deviceOverrides[id]; return; }
  if (isAxisSource(id)) return resetAxisSource(id);
  delete state.globalOverrides[id];
}
function resetAllGlobals() { state.globalOverrides = {}; }

function setApp(appId, id, value) { state.appOverrides[appId][id] = value; }
function toggleAppLink(appId, id) {
  if (id in state.appOverrides[appId]) delete state.appOverrides[appId][id];
  else state.appOverrides[appId][id] = appValue(appId, id);
}
function resetAppSetting(appId, id) { state.appOverrides[appId][id] = systemApp(appId, id); }
function toggleAllAppLinks(appId) {
  if (allAppLinked(appId)) {
    const pinned = {};
    appSettings(appId).forEach((spec) => { pinned[spec.id] = appValue(appId, spec.id); });
    state.appOverrides[appId] = pinned;
  } else {
    state.appOverrides[appId] = {};
  }
}
function resetApp(appId) {
  const pinned = {};
  appSettings(appId).forEach((spec) => { pinned[spec.id] = systemApp(appId, spec.id); });
  state.appOverrides[appId] = pinned;
}

/* the physical transform stays a permutation: changing one source swaps, never duplicates */
const AXES = ["x", "y", "z"];
const axisId = (axis) => `input.axis_orientation.${axis}.source`;
function isAxisSource(id) { return id.startsWith("input.axis_orientation.") && id.endsWith(".source"); }
function axisSources() {
  return Object.fromEntries(AXES.map((axis) => [axis, globalValue(axisId(axis))]));
}
function setAxisSource(id, value) {
  const axis = id.split(".")[2];
  const current = axisSources();
  const other = AXES.find((name) => current[name] === value);
  state.globalOverrides[id] = value;
  if (other && other !== axis) state.globalOverrides[axisId(other)] = current[axis];
}
function resetAxisSource(id) {
  const axis = id.split(".")[2];
  const current = axisSources();
  const systemValue = systemGlobal(id);
  if (current[axis] === systemValue) { delete state.globalOverrides[id]; return; }
  const other = AXES.find((name) => current[name] === systemValue && name !== axis);
  delete state.globalOverrides[id];
  if (other) {
    const desired = current[axis];
    if (desired === systemGlobal(axisId(other))) delete state.globalOverrides[axisId(other)];
    else state.globalOverrides[axisId(other)] = desired;
  }
}
function orientation() {
  const sources = axisSources();
  return AXES.map((axis) => [sources[axis], !!globalValue(`input.axis_orientation.${axis}.invert`)]);
}

/* ---------------------------------------------------------------- formatting */

const esc = (value) => String(value)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function fmt(spec, value) {
  if (value === null || value === undefined) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  if (Array.isArray(value)) return value.join(", ");
  if (spec && spec.kind === "number" && Number.isInteger(value)) return value.toFixed(1);
  return String(value);
}
function optionLabel(spec, value) {
  if (spec && isAxisSource(spec.id)) return AXIS_LABELS[value];
  if (spec && spec.cat === "routing") return ROUTE_AXIS_LABELS[value];
  if (value === null) return "Inherit";
  return OPTION_LABELS[value] || String(value)[0].toUpperCase() + String(value).slice(1);
}
function parseValue(spec, raw) {
  const text = String(raw).trim();
  if (spec.nullable && text === "None") return null;
  if (spec.kind === "boolean") {
    if (!["True", "False"].includes(text)) throw new Error("expected True or False");
    return text === "True";
  }
  if (spec.kind === "integer") {
    if (!/^-?\d+$/.test(text)) throw new Error(`${spec.label} expects a whole number`);
    return rangeCheck(spec, parseInt(text, 10));
  }
  if (spec.kind === "number") {
    const value = Number(text);
    if (!Number.isFinite(value)) throw new Error(`${spec.label} expects a number`);
    return rangeCheck(spec, value);
  }
  if (spec.kind === "string_list") return text.split(",").map((s) => s.trim()).filter(Boolean);
  return text;
}
function rangeCheck(spec, value) {
  if (spec.min !== null && value < spec.min) throw new Error(`${spec.label} minimum is ${spec.min}`);
  if (spec.max !== null && value > spec.max) throw new Error(`${spec.label} maximum is ${spec.max}`);
  return value;
}

const icon = (id, size = 14, cls = "") =>
  `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 16 16" aria-hidden="true"><use href="#${id}"/></svg>`;

/* free orbit needs twist=roll for a real third axis (ui.py::_free_orbit_needs_roll_warning) */
function freeOrbitNeedsRoll(appStyle, generalStyle, twistAction, supportsRoll) {
  const effective = appStyle === "default" ? generalStyle : appStyle;
  return !!(supportsRoll && effective === "free" && twistAction !== "roll");
}

/* ------------------------------------------------------------------- panels */

const PANELS = [
  { id: "state", label: "State", icon: "i-state", title: "Live state",
    blurb: "What the daemon is doing right now — mode, focus, transport, and health.",
    subs: ["Runtime", "Health", "About"] },
  { id: "device", label: "Device", icon: "i-device", title: "Device",
    blurb: "BLE identity and the firmware contracts this build recognises.",
    subs: ["Identity", "Hardware"] },
  { id: "global", label: "Global", icon: "i-global", title: "Global",
    blurb: "Applies everywhere unless an app holds its own override.",
    subs: ["Input", "Orientation", "Orbit", "Pan / Zoom", "Rates", "Routing", "Panel"] },
  { id: "perapp", label: "Per-App", icon: "i-perapp", title: "Per-App",
    blurb: "Linked rows follow Global and show its value.",
    subs: [] },
  { id: "apps", label: "3D Apps", icon: "i-apps", title: "3D app integrations",
    blurb: "Every integration starts disabled. Release tier and host version are separate claims.",
    subs: ["Supported", "Experimental"] },
  { id: "keys", label: "Keys", icon: "i-keys", title: "Keybindings",
    blurb: "Profile-scoped declarative bindings. Edits are sparse patches over the System profile.",
    subs: ["Bindings", "Input sources"] },
];
const PANELS_BY_ID = Object.fromEntries(PANELS.map((p) => [p.id, p]));

const GLOBAL_GROUPS = [
  { label: "Input", cats: ["input", "pointer"] },
  { label: "Orientation", cats: ["transform"] },
  { label: "Orbit", cats: ["orbit"] },
  { label: "Pan / Zoom", cats: ["pan_zoom"] },
  { label: "Rates", cats: ["sensitivity", "navigation", "camera"] },
  { label: "Routing", cats: ["routing"] },
  { label: "Panel", cats: ["hud"] },
];
const APP_GROUPS = [
  { label: "Rates", cats: ["sensitivity"] },
  { label: "Orbit", cats: ["orbit"] },
  { label: "Pan / Zoom", cats: ["pan_zoom"] },
  { label: "Motion", cats: ["navigation", "camera"] },
  { label: "Routing", cats: ["routing"] },
];

/* Related settings, boxed in threes. The grouping is semantic, not alphabetical: each box answers
 * one question ("where does orbit turn?", "how fast?"), which is how these get read in practice. */
const SUBGROUPS = [
  { cat: "device", title: "BLE identity", ids: ["device.name", "device.address"] },

  { cat: "input", title: "Startup mode", ids: ["input.mode.default"] },
  { cat: "pointer", title: "Cursor", ids: ["pointer.cursor.gain"] },
  { cat: "pointer", title: "Scroll", ids: ["pointer.scroll.gain", "pointer.scroll.deadzone", "pointer.scroll.dominance"] },

  { cat: "transform", title: "Logical X", ids: ["input.axis_orientation.x.source", "input.axis_orientation.x.invert"] },
  { cat: "transform", title: "Logical Y", ids: ["input.axis_orientation.y.source", "input.axis_orientation.y.invert"] },
  { cat: "transform", title: "Logical Z", ids: ["input.axis_orientation.z.source", "input.axis_orientation.z.invert"] },

  { cat: "orbit", title: "Pivot", ids: ["navigation.orbit.pivot", "navigation.orbit.pivot_hold_seconds", "navigation.orbit.selection_override"] },
  { cat: "orbit", title: "Style & horizon", ids: ["navigation.orbit.style", "navigation.orbit.twist_action", "navigation.orbit.lock_horizon", "navigation.level_horizon_on_entry"] },
  { cat: "orbit", title: "Pivot fallback chain", ids: ["navigation.orbit.pivot_fallbacks"], aside: true },

  { cat: "pan_zoom", title: "Zoom", ids: ["navigation.zoom.target", "navigation.zoom.cursor_hold_seconds", "navigation.zoom.behavior"] },
  { cat: "pan_zoom", title: "Pan", ids: ["navigation.pan.scales_with_distance"] },
  { cat: "pan_zoom", title: "Unity clipping", ids: ["navigation.unity.override_dynamic_clip", "navigation.unity.pivot_extent_multiplier"] },

  { cat: "sensitivity", title: "Gains", ids: ["navigation.orbit.sensitivity", "navigation.pan.gain", "navigation.zoom.gain"] },
  { cat: "sensitivity", title: "Rate & dominance", ids: ["navigation.refresh_rate", "navigation.zoom.dominance"] },
  { cat: "navigation", title: "Modes & speeds", ids: ["navigation.mode", "navigation.fly.speed", "navigation.walk.speed"] },
  { cat: "camera", title: "Camera", ids: ["navigation.camera.lock_to_view"] },

  { cat: "hud", title: "Visibility", ids: ["hud.visible", "hud.always_on_top", "hud.click_through"] },
  { cat: "hud", title: "Appearance & timing", ids: ["hud.opacity", "hud.margin", "hud.last_binding_timeout"] },
];

/* Axis routing groups as triads, because that is what an axis set is: three channels of one gesture. */
const ROUTING_TRIADS = [
  { mode: "lean", title: "Orbit axes", keys: ["orbit.x", "orbit.y", "orbit.z"] },
  { mode: "lean", title: "Pan & zoom", keys: ["pan.x", "pan.y", "zoom"] },
  { mode: "orbit", title: "Orbit · rotate", keys: ["orbit.pitch", "orbit.yaw", "orbit.twist"] },
  { mode: "orbit", title: "Orbit · translate", keys: ["orbit.pan_x", "orbit.pan_y", "orbit.zoom"] },
  { mode: "camera", title: "Camera", keys: ["camera.pitch", "camera.yaw", "camera.roll"] },
  { mode: "fly", title: "Fly · look", keys: ["fly.pitch", "fly.yaw", "fly.bank"] },
  { mode: "fly", title: "Fly · move", keys: ["fly.forward", "fly.strafe", "fly.vertical"] },
  { mode: "walk", title: "Walk · look", keys: ["walk.pitch", "walk.yaw"] },
  { mode: "walk", title: "Walk · move", keys: ["walk.forward", "walk.strafe", "walk.vertical"] },
];

/* one mark per category, so a section is recognisable before it is read */
const CATEGORY_ICONS = {
  device: "i-device", hud: "i-eye", input: "i-state", transform: "i-swap",
  pointer: "i-pointer", orbit: "i-orbit", sensitivity: "i-gauge",
  pan_zoom: "i-recenter", navigation: "i-orbit", camera: "i-lock", routing: "i-perapp",
};
const sectionHead = (cat, label) =>
  `<div class="sectionhead">${icon(CATEGORY_ICONS[cat] || "i-info", 11)}${esc(label || CATEGORY_TITLES[cat])}</div>`;

function appGroups(appId) {
  const specs = appSettings(appId);
  const groups = APP_GROUPS
    .map((group) => ({ ...group, specs: specs.filter((s) => group.cats.includes(s.cat)) }))
    .filter((group) => group.specs.length);
  if (APPS_BY_ID[appId].caps.has("onshape_userscript")) groups.push({ label: "Host setup", cats: [], specs: [] });
  return groups;
}
function subsFor(panel) {
  if (panel === "perapp") return appGroups(state.selectedApp).map((g) => g.label);
  return PANELS_BY_ID[panel].subs;
}

/* -------------------------------------------------------------- row builders */

function rowClasses(view, isApp) {
  const classes = ["row"];
  if (!isApp) classes.push("global");
  if (isApp && view.linked) classes.push("linked");
  if (isApp ? !view.linked : view.overridden) classes.push("overridden");
  return classes.join(" ");
}

/* three-stop slider: an axis choice is a position on a scale, not an item in a list */
function axisSlider(spec, value, appId, label) {
  const letters = ["X", "Y", "Z"];
  return `<span class="axis" role="slider" tabindex="0" data-act="axis" data-id="${spec.id}"
    ${appId ? `data-app="${appId}"` : ""} style="--stop:${value}"
    aria-label="${esc(label || spec.label)}" aria-valuemin="0" aria-valuemax="2"
    aria-valuenow="${value}" aria-valuetext="${letters[value]}"
    title="${esc(spec.help)} — drag or use ← →">
    <span class="track"><i></i><i></i><i></i></span>
    <span class="knob">${letters[value]}</span>
    <span class="stops">${letters.map((letter, index) =>
      `<b class="${index === value ? "on" : ""}">${letter}</b>`).join("")}</span>
  </span>`;
}

function controlHtml(view, isApp) {
  const spec = view.spec;
  const target = isApp ? `data-app="${view.appId}"` : "";
  const base = `data-id="${spec.id}" ${target}`;
  const value = view.value;

  if (spec.control === "choice" && spec.kind === "integer" &&
      (spec.choices || []).length === 3 && spec.choices[0] === 0) {
    return axisSlider(spec, value, isApp ? view.appId : null);
  }

  if (spec.kind === "boolean" && spec.nullable) {
    const options = [[true, "On"], [false, "Off"], [null, "Inherit"]];
    return `<span class="tri">${options.map(([candidate, label]) => `
      <button ${base} data-act="set" data-raw="${candidate === null ? "None" : candidate ? "True" : "False"}"
        aria-pressed="${value === candidate}" title="${esc(spec.help)}">${label}</button>`).join("")}</span>`;
  }
  if (spec.kind === "boolean") {
    return `<input class="switch" type="checkbox" ${base} data-act="toggle" ${value ? "checked" : ""}
      title="${esc(spec.help)}" aria-label="${esc(spec.label)}">`;
  }
  if (spec.control === "ordered_list") return orderedListHtml(view);

  const choices = isApp ? view.choices : (spec.choices || []).filter((v) => !["default", "cube", "cursor_mode"].includes(v));
  if (choices.length) {
    return `<select ${base} data-act="choose" title="${esc(spec.help)}">${choices.map((candidate) => `
      <option value="${esc(candidate)}" ${candidate === value ? "selected" : ""}>${esc(optionLabel(spec, candidate))}</option>`).join("")}</select>`;
  }
  const mono = spec.kind === "string" ? " mono" : "";
  return `<input class="entry${mono}" type="text" ${base} data-act="commit"
    value="${esc(fmt(spec, value))}" title="${esc(spec.help)}" spellcheck="false">`;
}

function orderedListHtml(view) {
  const spec = view.spec;
  const order = view.value.slice();
  const rest = spec.choices.filter((v) => !order.includes(v));
  const chain = order.map((entry, index) => `
    <div class="chainitem" title="Tried at position ${index + 1}">
      <span class="ord">${index + 1}</span>
      <span>${esc(optionLabel(spec, entry))}</span>
      <button class="iconbtn" data-act="fallback-move" data-id="${entry}" data-dir="-1"
        ${index === 0 ? "disabled" : ""} title="Earlier">${icon("i-up", 12)}</button>
      <button class="iconbtn" data-act="fallback-move" data-id="${entry}" data-dir="1"
        ${index === order.length - 1 ? "disabled" : ""} title="Later">${icon("i-down", 12)}</button>
      <button class="iconbtn" data-act="fallback-drop" data-id="${entry}" title="Remove from the chain">${icon("i-x", 11)}</button>
    </div>`).join("");
  const spare = rest.map((entry) => `
    <div class="chainitem off" title="Not in the chain">
      <span class="ord">—</span>
      <span>${esc(optionLabel(spec, entry))}</span>
      <span></span><span></span>
      <button class="iconbtn" data-act="fallback-add" data-id="${entry}" title="Append to the chain">${icon("i-plus", 11)}</button>
    </div>`).join("");
  return `<div class="chain">${chain}
    ${order.length ? "" : `<div style="color:var(--faint);font-size:10.5px">empty — no orbit after the primary target fails</div>`}
    ${spare ? `<div class="chainsplit">not in the chain</div>${spare}` : ""}</div>`;
}

function warnIcon(view, isApp) {
  const spec = view.spec;
  if (spec.id !== "navigation.orbit.twist_action") return "";
  const app = isApp ? APPS_BY_ID[view.appId] : null;
  const supportsRoll = app ? app.twistActions.includes("roll") : true;
  const appStyle = isApp ? appValue(view.appId, "navigation.orbit.style") : "default";
  const show = freeOrbitNeedsRoll(appStyle, globalValue("navigation.orbit.style"), view.value, supportsRoll);
  return show
    ? `<span class="warnicon" title='Switch to "roll" for 3-axis orbit'>${icon("i-alert", 13)}</span>`
    : "";
}

function settingRow(view, index, isApp) {
  const spec = view.spec;
  const linkIcon = view.linked ? "i-link" : "i-unlink";
  const link = isApp ? `
    <button class="linkbtn ${view.linked ? "" : "unlinked"}" data-act="link" data-id="${spec.id}" data-app="${view.appId}"
      title="${view.linked ? "Linked to Global — click to write an app override" : "App-specific override — click to follow Global again"}">
      ${icon(linkIcon, 14)}</button>` : "";

  let source, showReset;
  if (isApp) {
    const incompatible = view.linked && !view.globalCompatible;
    source = `<span class="src ${view.linked ? "" : "strong"}">${view.linked ? "Linked to Global" : "App override"}
      ${incompatible ? `<span class="note" title="This app cannot represent the Global value, so its own System value resolves. The app is still linked.">${icon("i-info", 12)} Global value unsupported</span>` : ""}</span>`;
    showReset = view.linked || fmt(spec, view.value) !== fmt(spec, view.system);
  } else {
    let text = view.overridden ? "User override" : "System default";
    if (spec.scope === "global_and_app") text += " · per-app capable";
    else if (spec.scope === "device") text += " · device";
    source = `<span class="src ${view.overridden ? "strong" : ""}">${text}</span>`;
    showReset = view.overridden;
  }
  const reset = showReset ? `
    <button class="resetbtn" data-act="reset" data-id="${spec.id}" ${isApp ? `data-app="${view.appId}"` : ""}
      title="${isApp ? "Reset app setting to System default" : "Reset to System default"} (${esc(fmt(spec, view.system))})">
      ${icon("i-reset", 14)}</button>` : "";

  /* an ordered chain needs the full width, so its row stacks instead of columnising */
  if (spec.control === "ordered_list") {
    return `<div class="${rowClasses(view, isApp)} stack" style="--i:${index}">
      <span class="rowlabel" title="${esc(spec.help)}">${esc(spec.label)}</span>
      ${source}
      ${reset || "<span></span>"}
      ${controlHtml(view, isApp)}
    </div>`;
  }

  return `<div class="${rowClasses(view, isApp)}" style="--i:${index}">
    <span class="rowlabel" title="${esc(spec.help)}">${esc(spec.label)}${warnIcon(view, isApp)}</span>
    ${link}
    ${controlHtml(view, isApp)}
    ${source}
    ${reset}
  </div>`;
}

/* Axis routing collapses each action's source and inversion into one row: the pair is a single
 * decision, and link/reset here act on both of that action's setting IDs at once. Rows are boxed
 * by triad, so a gesture's three channels are read together. */
function shortRouteLabel(key, lean) {
  const parts = key.split(".");
  if (lean) return parts[0] === "orbit" ? parts[1].toUpperCase() : prettyRoute(key.replace(".", "_"));
  return prettyRoute(parts.slice(1).join("_"));
}
function prettyRoute(text) {
  return text.split("_").map((word) => (word.length === 1 ? word.toUpperCase()
    : word[0].toUpperCase() + word.slice(1))).join(" ");
}

function routingRows(appId) {
  const isApp = !!appId;
  const available = new Set((appId ? appSettings(appId) : SETTINGS)
    .filter((spec) => spec.cat === "routing").map((spec) => spec.id));

  const cards = ROUTING_TRIADS.map((triad) => {
    const items = triad.keys.map((key) => ({
      key,
      sourceId: `navigation.routing.${key}.source`,
      invertId: `navigation.routing.${key}.invert`,
      label: shortRouteLabel(key, triad.mode === "lean"),
    })).filter((item) => available.has(item.sourceId) && available.has(item.invertId));
    return { title: triad.title, items };
  }).filter((card) => card.items.length);

  return `<div class="groupgrid cols3">${cards.map((card, cardIndex) => `
    <section class="group" style="--i:${cardIndex}">
      <div class="grouptitle">${esc(card.title)}<span class="legend">invert</span></div>
      <div class="rows">${card.items.map((item, index) =>
        routingRow(item, index, appId, isApp)).join("")}</div>
    </section>`).join("")}</div>`;
}

function routingRow(item, index, appId, isApp) {
  const sourceSpec = SETTINGS_BY_ID[item.sourceId];
  const invertSpec = SETTINGS_BY_ID[item.invertId];
  const sourceView = isApp ? appView(appId, sourceSpec) : globalView(sourceSpec);
  const invertView = isApp ? appView(appId, invertSpec) : globalView(invertSpec);
  const linked = isApp && sourceView.linked && invertView.linked;
  const overridden = isApp ? !linked : (sourceView.overridden || invertView.overridden);
  const pair = `${item.sourceId} ${item.invertId}`;
  const status = isApp ? (linked ? "Linked to Global" : "App override") : (overridden ? "User override" : "System default");

  const link = isApp ? `
    <button class="linkbtn ${linked ? "" : "unlinked"}" data-act="link-pair" data-pair="${pair}" data-app="${appId}"
      title="${linked ? "Linked to Global — click to override this action's source and inversion" : "App override — click to follow Global again"}">
      ${icon(linked ? "i-link" : "i-unlink", 14)}</button>` : "";
  const showReset = isApp
    ? (linked || sourceView.value !== sourceView.system || invertView.value !== invertView.system)
    : overridden;
  const reset = showReset ? `
    <button class="resetbtn" data-act="reset-pair" data-pair="${pair}" ${isApp ? `data-app="${appId}"` : ""}
      title="Reset this action's source and inversion to System defaults">${icon("i-reset", 14)}</button>` : "";

  return `<div class="row rt ${isApp ? "linkable" : ""} ${isApp && linked ? "linked" : ""} ${overridden ? "overridden" : ""}" style="--i:${index}">
    <span class="rowlabel" title="${esc(sourceSpec.label.replace(/ source$/, ""))} — ${status}. ${esc(sourceSpec.help)}">${esc(item.label)}</span>
    ${link}
    ${axisSlider(sourceSpec, sourceView.value, isApp ? appId : null, sourceSpec.label)}
    <input class="switch" type="checkbox" data-act="toggle" data-id="${item.invertId}"
      ${isApp ? `data-app="${appId}"` : ""} ${invertView.value ? "checked" : ""}
      title="${esc(invertSpec.label)}" aria-label="${esc(invertSpec.label)}">
    ${reset || `<span></span>`}
  </div>`;
}

/* --------------------------------------------------------------- panel: state */

function controlHelp() {
  const twist = state.foreground ? appValue(state.foreground, "navigation.orbit.twist_action") : "roll";
  const zoom = state.foreground ? appValue(state.foreground, "navigation.zoom.behavior") : "zoom";
  if (state.inputMode === "pointer") {
    const help = "Ball: planar = pointer · twist = scroll";
    return { label: "Pointer", primary: help, secondary: help, current: help };
  }
  if (state.navMode === "fly") {
    return {
      label: state.layer === "secondary" ? "Move" : "Fly",
      primary: "Ball: planar = look · twist = bank",
      secondary: "Ball: planar = strafe / forward · twist = rise / fall",
      current: state.layer === "secondary" ? "Ball: planar = strafe / forward · twist = rise / fall" : "Ball: planar = look · twist = bank",
    };
  }
  if (state.navMode === "walk") {
    return {
      label: state.layer === "secondary" ? "Move" : "Walk",
      primary: "Ball: planar = look · twist = unused",
      secondary: "Ball: planar = strafe / forward · twist = rise / fall",
      current: state.layer === "secondary" ? "Ball: planar = strafe / forward · twist = rise / fall" : "Ball: planar = look · twist = unused",
    };
  }
  const twistLabel = { roll: "roll", zoom: "zoom", dolly: "dolly", none: "unused" }[twist] || twist;
  const primary = `Ball: planar = orbit · twist = ${twistLabel}`;
  const secondary = `Ball: planar = pan · twist = ${zoom === "dolly" ? "dolly" : "zoom"}`;
  return {
    label: state.layer === "secondary" ? "Pan / Zoom" : "Orbit",
    primary, secondary,
    current: state.layer === "secondary" ? secondary : primary,
  };
}

function ballCard() {
  const help = controlHelp();
  const secondaryActive = state.layer === "secondary";
  return `<div class="ballcard">
    <div class="ballhead">
      <span class="who">Ball motion · 1:1</span>
      <button class="iconbtn ${motion.simulate ? "on" : ""}" data-act="simulate"
        title="${motion.simulate ? "Pause the synthetic motion stream" : "Play the synthetic motion stream"}">
        ${icon(motion.simulate ? "i-pause" : "i-play", 13)}</button>
      <button class="iconbtn" data-act="recenter" title="Recenter 3D view">${icon("i-recenter", 13)}</button>
    </div>
    <canvas id="ball-main" width="208" height="208" role="img"
      aria-label="Sphere showing integrated ball rotation"></canvas>
    <div class="readout" id="ball-readout"></div>
    <div class="helpline ${secondaryActive ? "dim" : "active"}"><span class="tag">Primary</span>${esc(help.primary)}</div>
    <div class="helpline ${secondaryActive ? "active" : "dim"}"><span class="tag">Secondary</span>${esc(help.secondary)}</div>
  </div>`;
}

function triRow(label, options, act, current, disabled) {
  return `<div class="fact"><span>${esc(label)}</span><span class="val"><span class="tri">
    ${options.map(([value, text]) => `<button data-act="${act}" data-value="${value}"
      aria-pressed="${value === current}" ${disabled ? "disabled" : ""}>${esc(text)}</button>`).join("")}
  </span></span></div>`;
}

function statePanelRuntime() {
  const app = APPS_BY_ID[state.foreground];
  const help = controlHelp();
  const connected = APPS.filter((a) => a.integration.connected);
  const modes = app.modes.map((mode) => [mode, OPTION_LABELS[mode]]);
  return `<div class="statusgrid">
    ${ballCard()}
    <div class="facts">
      <div class="factgroup">
        <header>${icon("i-orbit", 12)} Runtime state <span class="spacer"></span>
          <span class="mono" style="font-size:10px;color:var(--faint)">RuntimeStore · transient</span></header>
        ${triRow("Input mode", [["pointer", "Pointer"], ["3d", "3D"]], "mode", state.inputMode)}
        ${triRow("Navigation", modes, "navmode", state.navMode, state.inputMode !== "3d")}
        ${triRow("Control layer", [["primary", "Primary"], ["secondary", help.label === "Pointer" ? "Secondary" : "Pan / Zoom"]], "layer", state.layer, state.inputMode !== "3d")}
        <div class="fact"><span>Foreground</span><span class="val">
          <select data-act="foreground" style="width:150px">
            ${APPS.map((a) => `<option value="${a.id}" ${a.id === state.foreground ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
          </select>
          <small class="mono">${esc(app.process.split(" · ")[0])}</small></span></div>
        <div class="fact"><span>Effective help</span><span class="val">${esc(help.current)}</span></div>
        <div class="fact"><span>Binding</span><span class="val">
          ${motion.dragging ? `Held: ${esc(RUNTIME.lastBinding)}` : `Last: ${esc(RUNTIME.lastBinding)}`}
          <small>retained ${fmt(SETTINGS_BY_ID["hud.last_binding_timeout"], globalValue("hud.last_binding_timeout"))} s</small></span></div>
      </div>
      <div class="factgroup">
        <header>${icon("i-device", 12)} Transport</header>
        <div class="fact"><span>BLE</span><span class="val"><span class="dot ok live"></span>${esc(RUNTIME.connection)}</span></div>
        <div class="fact"><span>Device</span><span class="val">${esc(globalView(SETTINGS_BY_ID["device.name"]).value)}
          <small>${esc(globalView(SETTINGS_BY_ID["device.address"]).value || "address: discovery by name")}</small></span></div>
        <div class="fact"><span>Add-ons</span><span class="val">${connected.length
          ? connected.map((a) => `<span class="chip">${esc(a.name)} v${esc(a.integration.connected)}</span>`).join(" ")
          : "none connected"}</span></div>
      </div>
      ${resolutionGroup(app)}
    </div>
  </div>`;
}

/* the resolved path a rotation packet actually takes into the focused host */
function resolutionGroup(app) {
  const map = orientation()
    .map(([source, invert], logical) => `${["X", "Y", "Z"][logical]}←${invert ? "−" : ""}${AXIS_LABELS[source].slice(0, 6)}`)
    .join("  ");
  const rich = app.caps.has("rich_actions");
  const routes = rich
    ? ["orbit.pitch", "orbit.yaw", "orbit.twist"].map((key) => {
        const value = appValue(app.id, `navigation.routing.${key}.source`);
        const inverted = appValue(app.id, `navigation.routing.${key}.invert`);
        return `${key.split(".")[1]}←${inverted ? "−" : ""}${ROUTE_AXIS_LABELS[value]}`;
      }).join("  ")
    : ["orbit.x", "orbit.y", "orbit.z"].map((key) => {
        const value = appValue(app.id, `navigation.routing.${key}.source`);
        const inverted = appValue(app.id, `navigation.routing.${key}.invert`);
        return `${key.split(".")[1]}←${inverted ? "−" : ""}${ROUTE_AXIS_LABELS[value]}`;
      }).join("  ");
  const pivotSpec = SETTINGS_BY_ID["navigation.orbit.pivot"];
  const pivot = appliesTo(pivotSpec, app) ? appValue(app.id, pivotSpec.id) : "screen_center";
  const chain = globalValue("navigation.orbit.pivot_fallbacks")
    .filter((value) => app.pivots.includes(value))
    .map((value) => optionLabel(pivotSpec, value)).join(" ▸ ") || "none";
  const rate = appliesTo(SETTINGS_BY_ID["navigation.refresh_rate"], app)
    ? appValue(app.id, "navigation.refresh_rate") : 30;
  return `<div class="factgroup">
    <header>${icon("i-swap", 12)} Where this motion goes <span class="spacer" style="flex:1"></span>
      <span style="font-size:10px;color:var(--faint)">resolved for ${esc(app.name)}</span></header>
    <div class="fact" title="Global → Physical transform, applied once before pointer and 3D routing">
      <span>Physical → logical</span><span class="val mono" style="font-size:10.5px">${esc(map)}</span></div>
    <div class="fact" title="Per-App → Axis routing for the current mode">
      <span>Logical → action</span><span class="val mono" style="font-size:10.5px">${esc(routes)}</span></div>
    <div class="fact" title="The app's pivot is tried first; misses restart at the Global fallback order, skipping unsupported methods">
      <span>Orbit pivot</span><span class="val">${esc(optionLabel(pivotSpec, pivot))}
        <small>then ${esc(chain)}</small></span></div>
    <div class="fact"><span>Delivery</span><span class="val">${esc(app.transport)}
      <small>${rate ? `${rate} Hz` : "unthrottled"}</small></span></div>
  </div>`;
}

function statePanelHealth() {
  return `<p class="hint">Transport health is reported separately from host presence: a connected add-on can still be
    degraded, and a healthy owner can have nothing focused.</p>
    <div class="factgroup">
      <header>${icon("i-gauge", 12)} Services</header>
      ${RUNTIME.services.map(([id, stateName, detail]) => `<div class="healthrow">
        <span class="dot ${stateName === "healthy" ? "ok" : stateName === "failed" ? "bad" : stateName === "disabled" ? "" : "warn"}"></span>
        <span class="svc">${esc(id)}</span>
        <span class="state ${stateName}">${esc(stateName)}</span>
        <span class="detail" title="${esc(detail)}">${esc(detail)}</span></div>`).join("")}
    </div>
    <div class="factgroup" style="margin-top:8px">
      <header>${icon("i-apps", 12)} Integration runtime</header>
      ${APPS.filter((a) => a.integration.enabled || a.integration.connected).map((a) => `<div class="healthrow">
        <span class="dot ${a.integration.runtime.state === "healthy" ? "ok" : a.integration.runtime.state === "failed" ? "bad" : "warn"}"></span>
        <span class="svc">${esc(a.name)}${a.integration.connected ? ` v${esc(a.integration.connected)}` : ""}</span>
        <span class="state ${a.integration.runtime.state}">${esc(a.integration.runtime.state)}</span>
        <span class="detail" title="${esc(a.integration.runtime.detail)}">${esc(a.integration.runtime.detail)}</span></div>`).join("")}
    </div>
    <div class="factgroup" style="margin-top:8px">
      <header>${icon("i-folder", 12)} Logs</header>
      <div class="fact"><span>Daemon log</span><span class="val mono">${esc(PRODUCT.logFile)}
        <button class="iconbtn" data-act="copy" data-text="${esc(PRODUCT.logFile)}" title="Copy path">${icon("i-copy", 12)}</button></span></div>
      <div class="fact"><span>Host logs</span><span class="val">listed per integration under 3D Apps → Instructions</span></div>
    </div>`;
}

function statePanelAbout() {
  const rows = [
    ["Version", `${PRODUCT.version} · ${PRODUCT.channel}`, "Pre-release channel is derived from the version, never declared."],
    ["Publisher", PRODUCT.publisher, ""],
    ["Configuration", PRODUCT.configDir, "Every persistent user setting lives in config.json here."],
    ["Legacy root", "%APPDATA%\\TrackballDaemon", "Copied across once, then left untouched as the rollback copy."],
    ["Single instance", "Local\\Astrolabe.Controller.v1", "One process may own the controller per desktop session."],
    ["Start at login", state.startAtLogin ? "Registered (HKCU\\…\\Run → Astrolabe)" : "Not registered", "Toggled from the tray menu."],
    ["Licence", "Apache-2.0 (hardware source: CERN-OHL-W-v2)", ""],
    ["Firmware", "PMW3610 dual-sensor validation build · advertised name Astrolabe", "Rotation stream plus the five-way input-state snapshot."],
  ];
  return `<p class="hint">Settings apply live, except BLE device identity (applies on reconnect) and host add-on
    code (applies when that host reloads it).</p>
    <div class="factgroup">
      <header>${icon("i-info", 12)} Build</header>
      ${rows.map(([label, value, hint]) => `<div class="fact" ${hint ? `title="${esc(hint)}"` : ""}>
        <span>${esc(label)}</span><span class="val mono" style="font-size:11px">${esc(value)}</span></div>`).join("")}
    </div>`;
}

/* -------------------------------------------------------------- panel: device */

function devicePanelIdentity() {
  const specs = SETTINGS.filter((s) => s.scope === "device");
  return `<p class="hint">Identity has its own reset layer: <em>Reset all Global settings</em> never touches it.
    Changes take effect on the next reconnect.</p>
    <div class="rows">${specs.map((spec, index) => settingRow(globalView(spec), index, false)).join("")}</div>
    <div class="factgroup" style="margin-top:10px">
      <header>${icon("i-alert", 12)} While the daemon owns the radio</header>
      <div class="fact"><span>Pairing</span><span class="val">Do not pair the trackball as a Windows Bluetooth mouse.
        With the daemon closed the firmware's own HID path works normally.</span></div>
      <div class="fact"><span>Controller mode</span><span class="val">A subscribed daemon suppresses firmware HID output,
        so movement is never doubled.</span></div>
    </div>`;
}

function devicePanelHardware() {
  return `<p class="hint tight">Adapters are selected by advertised name and characteristic set, so a legacy
    rotation-only build still connects — it simply publishes no controls.</p>
    ${DEVICE_DESCRIPTORS.map((d, index) => `<div class="card ${d.present ? "on" : ""}" style="--i:${index}">
      <div class="cardtop">
        <span class="dot ${d.present ? "ok live" : ""}"></span>
        <span class="name">${esc(d.label)}</span>
        <span class="tierbadge">${esc(d.role)}</span>
        <span class="grow"></span>
        <span class="chip">${esc(d.source)}</span>
      </div>
      <div class="cardline">advertised as <span class="mono">${esc(d.advertised)}</span></div>
      <div class="cardline">${d.controls.length
        ? d.controls.map(([id, label, bit]) => `<span class="token" title="${esc(label)} · bit ${bit}">${esc(id)}</span>`).join("")
        : `<span style="color:var(--faint)">no input-state characteristic</span>`}</div>
      <div class="cardline" style="color:var(--faint)">${esc(d.notes)}</div>
    </div>`).join("")}`;
}

/* -------------------------------------------------------------- panel: global */

/* split one category's settings into its outlined boxes; anything unclaimed keeps the category name */
function subgroupBoxes(cat, specs) {
  const remaining = new Map(specs.map((spec) => [spec.id, spec]));
  const boxes = [];
  SUBGROUPS.filter((group) => group.cat === cat).forEach((group) => {
    const members = group.ids.map((id) => remaining.get(id)).filter(Boolean);
    members.forEach((spec) => remaining.delete(spec.id));
    if (members.length) boxes.push({ title: group.title, aside: group.aside, specs: members });
  });
  if (remaining.size) boxes.push({ title: CATEGORY_TITLES[cat], specs: [...remaining.values()] });
  return boxes;
}

function boxHtml(box, boxIndex, appId) {
  return `<section class="group" style="--i:${boxIndex}">
    <div class="grouptitle">${esc(box.title)}</div>
    <div class="rows">${box.specs.map((spec, index) =>
      settingRow(appId ? appView(appId, spec) : globalView(spec), index, !!appId)).join("")}</div>
  </section>`;
}

/* A box flagged `aside` is narrow by nature (the fallback chain), so it sits in a side column
 * rather than eating a full-width band of a window that may not scroll. */
function renderBoxes(boxes, appId) {
  const main = boxes.filter((box) => !box.aside);
  const aside = boxes.filter((box) => box.aside);
  const column = (list, offset) => list.map((box, index) => boxHtml(box, index + offset, appId)).join("");
  if (!aside.length) return `<div class="groupgrid">${column(main, 0)}</div>`;
  return `<div class="splitgrid">
    <div class="groupgrid">${column(main, 0)}</div>
    <div class="groupgrid">${column(aside, main.length)}</div>
  </div>`;
}

function globalPanel() {
  const group = GLOBAL_GROUPS[state.sub.global] || GLOBAL_GROUPS[0];
  if (group.cats.includes("routing")) {
    return `<p class="hint tight">Each action picks which logical axis drives it, after the physical transform.
      Source and inversion are one decision here, so linking and resetting cover both.</p>${routingRows(null)}`;
  }
  const intro = {
    Input: "Pointer output and the mode the daemon starts in when no binding overrides it.",
    Orientation: "Device-wide base orientation. Rotating the ball's base should need only an axis swap and, at most, one inversion.",
    Orbit: "Global orbit behaviour and the fallback chain used when a pivot is unsupported or a raycast misses.",
    "Pan / Zoom": "Global pan and zoom behaviour, including the independent To Cursor hold.",
    Rates: "Sensitivity, packet rate, and the navigation modes richer hosts expose.",
    Panel: "The passive text control panel that sits above your work.",
  }[group.label];

  const body = group.cats.map((cat) => {
    const specs = SETTINGS.filter((s) => s.cat === cat && s.scope !== "device");
    if (!specs.length) return "";
    const extra = cat === "transform" ? transformExtra() : "";
    return `<section class="section">
      ${group.cats.length > 1 ? sectionHead(cat) : ""}
      ${extra}
      ${renderBoxes(subgroupBoxes(cat, specs), null)}
    </section>`;
  }).join("");

  return `${intro ? `<p class="hint tight">${esc(intro)}</p>` : ""}${body}`;
}

function transformExtra() {
  return `<div style="display:flex;gap:12px;align-items:center;margin:2px 0 6px">
    <div class="ballcard" style="padding:6px">
      <canvas id="ball-axes" width="128" height="128" role="img" aria-label="Logical axis needles on the ball"></canvas>
    </div>
    <div style="font-size:10.5px;color:var(--muted);line-height:1.5">
      <div><b style="color:var(--accent)">X</b> · <b style="color:var(--warn)">Y</b> ·
        <b style="color:var(--muted)">Z</b> needles show where each logical axis currently points.</div>
      <div style="margin-top:4px">${icon("i-swap", 12)} Changing a source swaps two axes instead of duplicating one,
        so an axis can never be lost.</div>
      <div style="margin-top:4px">Drag either ball to move it; the outer ring twists.</div>
      <button class="btn sm ghost" data-act="reset-orientation" style="margin-top:6px">
        ${icon("i-reset", 12)} Reset orientation</button>
    </div>
  </div>`;
}

/* -------------------------------------------------------------- panel: perapp */

const APP_GLYPHS = {
  blender: "BL", freecad: "FC", sketchup: "SU", unreal: "UE", unity: "UY", godot: "GD",
  rhino: "RH", fusion360: "F3", solidworks: "SW", onshape: "OS", autocad: "AC",
};

/* The app's name lives in the panel title; this strip carries only what the title cannot say. */
function appIdentity(app) {
  const count = Object.keys(state.appOverrides[app.id]).length;
  const info = app.integration;
  return `<div class="identity">
    <span class="meta">
      <span class="tierbadge ${app.tier}">${app.tier}</span>
      <span>${esc(app.transport)} transport</span>
      <span class="mono">${esc(app.process.split(" · ")[0])}</span>
      <span>${info.enabled ? "integration enabled" : "integration disabled"}</span>
      <span style="color:${count ? "var(--accent)" : "var(--faint)"}">
        ${count ? `${count} app override${count === 1 ? "" : "s"}` : "no app overrides — every row follows Global"}</span>
    </span>
  </div>`;
}

function perAppPanel() {
  const appId = state.selectedApp;
  const app = APPS_BY_ID[appId];
  const groups = appGroups(appId);
  const group = groups[Math.min(state.sub.perapp, groups.length - 1)] || groups[0];

  if (group.label === "Host setup") {
    return `${appIdentity(app)}<p class="hint tight">Onshape-only, and optional: without the userscript, Under Cursor orbit falls back to
      the configured chain.</p>
      <div class="factgroup">
        <header>${icon("i-shield", 12)} Under-cursor pointer userscript</header>
        <div class="fact"><span>What it does</span><span class="val">Reports canvas-relative pointer coordinates from
          onshape.com only. It does not capture the screen or send model data.</span></div>
        <div class="fact"><span>Needed for</span><span class="val">Accurate <b>Under Cursor</b> orbit and To Cursor zoom
          in the browser.</span></div>
        <div class="fact"><span>Install</span><span class="val">
          <button class="btn sm" data-act="copy" data-text="Astrolabe Onshape pointer userscript">${icon("i-copy", 12)} Copy userscript…</button>
          <small>paste into your userscript manager, then reload the document</small></span></div>
      </div>`;
  }
  if (group.cats.includes("routing")) {
    return `${appIdentity(app)}<p class="hint tight">${esc(app.name)} exposes ${app.caps.has("rich_actions") ? "named mode actions" : "six generic channels"}.
      Software-convention corrections come from immutable host baselines; this is the user layer.</p>${routingRows(appId)}`;
  }

  const body = group.cats.map((cat) => {
    const specs = group.specs.filter((s) => s.cat === cat);
    if (!specs.length) return "";
    return `<section class="section">
      ${group.cats.length > 1 ? sectionHead(cat) : ""}
      ${renderBoxes(subgroupBoxes(cat, specs), appId)}
    </section>`;
  }).join("");

  return appIdentity(app) + body;
}

/* ---------------------------------------------------------------- panel: apps */

function appsPanel() {
  const tier = state.sub.apps === 1 ? "experimental" : "supported";
  const list = APPS.filter((a) => a.tier === tier);
  return `<div class="cardline" style="margin:0 0 8px">
      <span class="dot ${RUNTIME.broker.state === "healthy" ? "ok live" : "warn"}"></span>
      <b style="color:var(--ink-2)">Navigation broker: ${esc(RUNTIME.broker.state)}</b>
      <span class="mono">${esc(RUNTIME.broker.detail)}</span>
    </div>
    <div class="tiergroup">
      <div class="tierhead">
        <h2>${esc(TIERS[tier].label)}s</h2>
        <p>${esc(TIERS[tier].summary)}</p>
      </div>
      ${list.map((app, index) => integrationCard(app, index)).join("")}
    </div>`;
}

function integrationCard(app, index) {
  const info = app.integration;
  const open = state.openCards.has(app.id);
  const compatClass = info.compat === "unsupported" ? "bad" : info.compat === "unverified" ? "warn" : "";
  const compatPrefix = info.compat === "unsupported" ? "WARNING — " : info.compat === "unverified" ? "CAUTION — " : "";
  const runtime = info.runtime;
  return `<div class="card ${info.enabled ? "on" : ""}" style="--i:${index}">
    <div class="cardtop">
      <span class="dot ${info.connected ? (runtime.state === "healthy" ? "ok live" : "warn") : runtime.state === "waiting" ? "warn" : ""}"></span>
      <span class="name">${esc(app.name)}</span>
      <span class="tierbadge ${app.tier}">${app.tier}</span>
      <span class="grow"></span>
      <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)">
        <input class="switch" type="checkbox" data-act="enable-app" data-app="${app.id}" ${info.enabled ? "checked" : ""}>
        Enabled</label>
      ${info.action ? `<button class="btn sm ${info.action.startsWith("Update") ? "primary" : ""}" data-act="setup" data-app="${app.id}">
        ${info.action.startsWith("Update") ? icon("i-download", 12) : icon("i-power", 12)} ${esc(info.action)}</button>` : ""}
      <button class="btn sm ghost" data-act="card" data-app="${app.id}">
        ${icon("i-chev", 12, open ? "chev open" : "chev")} Instructions</button>
    </div>
    <div class="cardline"><span class="mono" title="${esc(info.detected)}">${esc(info.detected)}</span>
      <span class="spacer" style="flex:1"></span>
      <span class="${compatClass}" style="flex:none" title="Release tier and host version are separate claims">
        ${esc(compatPrefix)}verified: ${esc(info.verified)}</span></div>
    <div class="cardline ${runtime.state === "degraded" || runtime.state === "waiting" ? "warn" : ""}">
      ${info.connected ? `connected v${esc(info.connected)} · ` : ""}runtime ${esc(runtime.state)}: ${esc(runtime.detail)}</div>
    <div class="disclosure ${open ? "open" : ""}"><div><div class="inner">
      ${[["Install model", info.installModel], ["Setup", info.setup], ["Health check", info.health]]
        .map(([title, body]) => `<div><div class="deftitle">${title}</div><div class="defbody">${esc(body)}</div></div>`).join("")}
      <div><div class="deftitle">${icon("i-shield", 11)} Security and permissions</div>
        <div class="defbody" style="color:var(--warn)">${esc(info.security)}</div></div>
      ${info.confirmation ? `<div class="notice">${icon("i-alert", 12)}<span>${esc(info.confirmation)}</span></div>` : ""}
      <div><div class="deftitle">Manual install</div><div class="defbody path">${esc(info.manual)}</div></div>
      <div><div class="deftitle">Routing</div><div class="defbody">
        ${esc(app.transport)} transport · focus by ${esc(app.focus)} · <span class="mono">${esc(app.process)}</span> ·
        modes ${app.modes.map((m) => OPTION_LABELS[m]).join(" / ")} ·
        ${info.setupRequired ? "one-time setup required" : "no host setup required"}</div></div>
      <div style="display:flex;gap:6px">
        <button class="btn sm" data-act="copy" data-text="${esc(app.name)} setup instructions">${icon("i-copy", 12)} Copy instructions</button>
        ${app.id === "onshape" ? `<button class="btn sm" data-act="copy" data-text="Onshape userscript">${icon("i-copy", 12)} Copy userscript…</button>` : ""}
      </div>
    </div></div></div>
  </div>`;
}

/* ---------------------------------------------------------------- panel: keys */

function composedBindings(profileId = state.profile) {
  const overrides = state.bindingOverrides[profileId];
  const systemList = BINDING_PROFILES[profileId].bindings;
  const rows = [];
  systemList.forEach((binding) => {
    const patch = overrides[binding.id];
    if (patch && patch.deleted) return;
    rows.push({ ...binding, ...(patch || {}), system: true, edited: !!patch });
  });
  Object.entries(overrides).forEach(([id, patch]) => {
    if (systemList.some((b) => b.id === id) || patch.deleted) return;
    rows.push({ id, system: false, edited: true, enabled: true, chord: [], match: "exact", priority: 0, apps: [], action: "input.toggle", ...patch });
  });
  return rows;
}

function keysPanel() {
  if (state.sub.keys === 1) return inputSourcesPanel();
  const bindings = composedBindings();
  const selected = bindings.find((b) => b.id === state.selectedBinding) || bindings[0];
  if (selected) state.selectedBinding = selected.id;
  return `<div class="kb">
    <div>
      <div class="kblist">
        ${bindings.map((binding) => `<button class="kbitem ${binding.enabled ? "" : "off"}"
          data-act="select-binding" data-id="${binding.id}" aria-selected="${selected && binding.id === selected.id}"
          title="${esc(binding.chord.join(" + ") || "no chord")}">
          <span class="dot ${binding.enabled ? "ok" : ""}"></span>
          <span class="lbl">${esc(binding.label || binding.id)}</span>
          ${binding.edited ? `<span class="edited" title="User patch over the System profile">${icon("i-record", 10)}</span>` : ""}
        </button>`).join("")}
      </div>
      <button class="btn sm ghost" data-act="new-binding" style="width:100%;margin-top:5px;justify-content:center">
        ${icon("i-plus", 12)} New binding</button>
    </div>
    ${selected ? bindingEditor(selected) : `<div class="kbeditor">No bindings in this profile.</div>`}
  </div>`;
}

function bindingEditor(binding) {
  const passThrough = binding.chord.some((token) => token.startsWith("keyboard:") &&
    !/^keyboard:(ctrl|shift|alt|meta)(\.(left|right))?$/.test(token));
  const deviceControls = DEVICE_DESCRIPTORS.flatMap((d) => d.controls.map(([id]) => `${d.source}:${id}`));
  return `<div class="kbeditor">
    <div class="field"><label for="kb-label">Label</label>
      <input id="kb-label" type="text" data-act="binding-field" data-field="label" value="${esc(binding.label || "")}">
      <span class="src">${binding.system ? "System binding" : "User binding"}</span></div>

    <div class="field"><label>Enabled</label>
      <input class="switch" type="checkbox" data-act="binding-field" data-field="enabled" ${binding.enabled ? "checked" : ""}>
      <span></span></div>

    <div class="field" style="align-items:start"><label>Chord</label>
      <span class="chordwrap">
        ${binding.chord.map((token) => `<span class="token">${esc(token)}
          <button data-act="chord-remove" data-token="${esc(token)}" title="Remove">${icon("i-x", 10)}</button></span>`).join("")}
        ${state.capturing ? `<span class="token" style="border-color:var(--accent);color:var(--accent)">${icon("i-record", 11)} press keys…</span>` : ""}
      </span>
      <button class="btn sm ${state.capturing ? "primary" : ""}" data-act="record">
        ${icon("i-record", 12)} ${state.capturing ? "Stop" : "Record…"}</button></div>

    <div class="field"><label>Device control</label>
      <select data-act="chord-add">
        <option value="">add a control…</option>
        ${deviceControls.map((token) => `<option value="${esc(token)}">${esc(token)}</option>`).join("")}
      </select><span></span></div>

    <div class="field"><label>Match</label>
      <select data-act="binding-field" data-field="match">
        <option value="exact" ${binding.match === "exact" ? "selected" : ""}>Exact — reject extra modifiers</option>
        <option value="allow_extra_modifiers" ${binding.match === "allow_extra_modifiers" ? "selected" : ""}>Allow extra modifiers</option>
      </select><span></span></div>

    <div class="field"><label>Action</label>
      <select data-act="binding-field" data-field="action">
        ${COMMON_ACTIONS.map(([id, label]) => `<option value="${id}" ${binding.action === id ? "selected" : ""}>${esc(label)}</option>`).join("")}
        <optgroup label="Settings">
          ${SETTINGS.filter((s) => s.scope !== "device" && s.cat !== "hud").slice(0, 14).map((s) =>
            `<option value="setting:${s.id}" ${binding.action === `setting:${s.id}` ? "selected" : ""}>Setting — ${esc(s.label)}</option>`).join("")}
        </optgroup>
      </select><span></span></div>

    ${binding.action && binding.action.startsWith("setting:") ? `
    <div class="field"><label>Behavior</label>
      <select data-act="binding-field" data-field="behavior">
        ${[["hold", "Hold a value; restore it on release"], ["toggle", "Toggle between two values"],
           ["cycle", "Cycle through available values"], ["add", "Add an amount"], ["multiply", "Multiply by a factor"]]
          .map(([id, label]) => `<option value="${id}" ${binding.behavior === id ? "selected" : ""}>${esc(label)}</option>`).join("")}
      </select><span></span></div>
    <div class="field"><label>Value</label>
      <input type="text" data-act="binding-field" data-field="value" value="${esc(binding.value || "")}"
        placeholder="e.g. turntable, or 1.5"><span></span></div>` : ""}

    <div class="field"><label>App context</label>
      <select data-act="binding-field" data-field="app">
        <option value="" ${!binding.apps.length ? "selected" : ""}>Any app — global</option>
        ${APPS.map((a) => `<option value="${a.id}" ${binding.apps[0] === a.id ? "selected" : ""}>${esc(a.name)} only</option>`).join("")}
      </select><span></span></div>

    <div class="field"><label>
      <button class="btn sm ghost" data-act="advanced" style="padding:0 4px">
        ${icon("i-chev", 11, state.advancedBinding ? "chev open" : "chev")} Advanced</button></label>
      <span class="src">priority, activation latch, multi-app context</span><span></span></div>
    <div class="disclosure ${state.advancedBinding ? "open" : ""}"><div><div class="inner">
      <div class="field"><label>Priority</label>
        <input type="text" class="mono" data-act="binding-field" data-field="priority" value="${esc(binding.priority ?? 0)}">
        <span class="src">explicit priority wins before context specificity</span></div>
      <div class="defbody" style="color:var(--muted)">Runtime precedence: explicit priority, context specificity, exact
        matching, larger chord, then activation recency. Simple edits always store <span class="mono">activation: "hold"</span>
        and express behaviour through paired press/release actions.</div>
    </div></div></div>

    ${passThrough ? `<div class="notice">${icon("i-alert", 12)}<span>Keyboard chords are observed passively: non-modifier
      keys still reach the foreground application.</span></div>` : ""}

    <div style="display:flex;gap:6px;margin-top:2px">
      <button class="btn sm primary" data-act="save-binding">${icon("i-check", 12)} Save</button>
      ${binding.system ? `<button class="btn sm ghost" data-act="restore-binding" ${binding.edited ? "" : "disabled"}
        title="Drop the user patch and restore the System binding">${icon("i-reset", 12)} Restore system</button>` : ""}
      <span class="spacer"></span>
      <button class="btn sm danger" data-act="delete-binding">${icon("i-trash", 12)} Delete</button>
    </div>
  </div>`;
}

function inputSourcesPanel() {
  const caps = INPUT_CAPABILITY[state.profile];
  return `<p class="hint tight">Sources required by the enabled bindings in
    <b>${esc(BINDING_PROFILES[state.profile].label)}</b>, checked against live provider health.</p>
    <div class="capchips">
      ${caps.map((cap) => `<span class="capchip" title="${esc(cap.detail)}">
        <span class="dot ${cap.status === "running" ? "ok" : cap.status === "not present" ? "" : "warn"}"></span>
        <span class="mono">${esc(cap.source)}</span> ${esc(cap.status)}</span>`).join("")}
    </div>
    <div class="factgroup">
      <header>${icon("i-keys", 12)} Required controls</header>
      ${caps.map((cap) => `<div class="fact"><span class="mono" style="font-size:10.5px">${esc(cap.source)}</span>
        <span class="val"><span class="mono" style="font-size:10.5px;color:var(--ink-2)">${esc(cap.controls)}</span>
          <small>${esc(cap.detail)}</small></span></div>`).join("")}
    </div>
    <div class="factgroup" style="margin-top:8px">
      <header>${icon("i-info", 12)} Profile ownership</header>
      <div class="fact"><span>Profiles</span><span class="val">Two developer-owned System profiles. Selecting one never
        copies or rewrites it; your edits are sparse patches stored per profile.</span></div>
      <div class="fact"><span>Switching</span><span class="val">Each profile keeps its own patches, and the daemon never
        changes profiles just because hardware appeared or disappeared.</span></div>
      <div class="fact"><span>Dependencies</span><span class="val">Requesting Pan also requests its 3D and
        secondary-control prerequisites; a suppressed prerequisite suppresses the dependent leaf rather than
        producing a mixed state.</span></div>
    </div>`;
}

/* ----------------------------------------------------------------- rendering */

const $ = (id) => document.getElementById(id);

function renderRail() {
  const rail = $("rail");
  const indicator = $("rail-indicator");
  rail.querySelectorAll(".railbtn").forEach((node) => node.remove());
  PANELS.forEach((panel) => {
    const button = document.createElement("button");
    button.className = "railbtn";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(panel.id === state.panel));
    button.dataset.act = "panel";
    button.dataset.panel = panel.id;
    button.title = panel.blurb;
    button.innerHTML = `${icon(panel.icon, 17)}<span>${panel.label}</span>`;
    rail.appendChild(button);
  });
  /* measured synchronously: layout is settled once the nodes are in, and an animation frame never
     arrives while the window is hidden, which would leave the indicator behind the selection */
  const active = rail.querySelector('.railbtn[aria-selected="true"]');
  if (active) {
    indicator.style.height = `${active.offsetHeight}px`;
    indicator.style.transform = `translateY(${active.offsetTop}px)`;
  }
}

function renderHead() {
  const panel = PANELS_BY_ID[state.panel];
  let actions = "";
  if (state.panel === "global") {
    actions = `<button class="btn sm ghost danger" data-act="reset-globals"
      title="Clears all Global overrides; device identity is unchanged.">${icon("i-reset", 12)} Reset all Global</button>`;
  } else if (state.panel === "perapp") {
    const linked = allAppLinked(state.selectedApp);
    actions = `
      <button class="btn sm" data-act="link-all" title="${linked
        ? "Pin every setting at its current value as an app override"
        : "Remove every app override so all rows follow Global"}">
        ${icon(linked ? "i-unlink" : "i-link", 12)} ${linked ? "Break all links" : "Link all to Global"}</button>
      <button class="btn sm ghost danger" data-act="reset-app"
        title="Pin this app's concrete System values and break its Global links. The integration stays enabled.">
        ${icon("i-reset", 12)} Reset app</button>`;
  } else if (state.panel === "keys") {
    const profile = BINDING_PROFILES[state.profile];
    actions = `<button class="appswitch" data-act="profile-menu" title="Input profile being edited">
      <span class="glyph">${profile.id === "keyboard_only" ? "KB" : "5W"}</span>
      <span class="who">${esc(profile.label)}</span>
      ${icon("i-chev", 12, state.menu === "profile" ? "chev open" : "chev")}
    </button>`;
  }
  /* On Per-App the app is the subject of the panel, so it sits in the title — but the panel keeps
     its name, and only the app itself is the control. */
  let title = `<h1>${esc(panel.title)}</h1>`;
  if (state.panel === "perapp") {
    const app = APPS_BY_ID[state.selectedApp];
    title = `<span class="titlerow">
      <span class="titleprefix">Per-App:</span>
      <button class="appswitch title" data-act="app-menu"
        title="Choose which application's overrides are shown">
        <span class="glyph">${APP_GLYPHS[app.id]}</span>
        <span class="who">${esc(app.name)}</span>
        ${icon("i-chev", 13, state.menu === "app" ? "chev open" : "chev")}
      </button>
    </span>`;
  }
  $("panelhead").innerHTML = `${title}<p>${esc(panel.blurb)}</p>
    <span class="headactions">${actions}</span>`;
}

function renderSubtabs() {
  const subs = subsFor(state.panel);
  const host = $("subtabs");
  host.querySelectorAll(".subtab").forEach((node) => node.remove());
  const index = Math.min(state.sub[state.panel] || 0, Math.max(subs.length - 1, 0));
  state.sub[state.panel] = index;
  subs.forEach((label, i) => {
    const button = document.createElement("button");
    button.className = "subtab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(i === index));
    button.dataset.act = "sub";
    button.dataset.index = String(i);
    button.textContent = label;
    host.appendChild(button);
  });
  host.style.display = subs.length ? "flex" : "none";
  const active = host.querySelector('.subtab[aria-selected="true"]');
  const indicator = $("sub-indicator");
  if (active) {
    indicator.style.width = `${active.offsetWidth - 8}px`;
    indicator.style.transform = `translateX(${active.offsetLeft + 4}px)`;
    indicator.style.opacity = "1";
  } else {
    indicator.style.opacity = "0";
  }
}

function renderContent() {
  const content = $("content");
  const keepScroll = content.scrollTop;
  let html = "";
  if (state.panel === "state") {
    html = [statePanelRuntime, statePanelHealth, statePanelAbout][state.sub.state]();
  } else if (state.panel === "device") {
    html = [devicePanelIdentity, devicePanelHardware][state.sub.device]();
  } else if (state.panel === "global") {
    html = globalPanel();
  } else if (state.panel === "perapp") {
    html = perAppPanel();
  } else if (state.panel === "apps") {
    html = appsPanel();
  } else {
    html = keysPanel();
  }
  content.innerHTML = html;
  content.scrollTop = keepScroll;
  mountBalls();
}

function renderChrome() {
  $("version-chip").textContent = `${PRODUCT.version} · ${PRODUCT.channel}`;
  $("status-pill").innerHTML = `<span class="dot ok live"></span><span>${esc(RUNTIME.connection)}</span>`;
  $("theme-btn").innerHTML = icon(state.theme === "light" ? "i-moon" : "i-sun", 15);

  const health = "healthy: navigation-broker, solidworks";
  const app = APPS_BY_ID[state.foreground];
  $("statusbar").innerHTML = `
    <span>Connection: <b>subscribed</b></span><span class="sep">·</span>
    <span>Foreground: <b>${esc(app.name)}</b> <span class="mono">${esc(app.process.split(" · ")[0])}</span></span>
    <span class="sep">·</span><span>Health: <b>${esc(health)}</b></span>
    <span class="spacer" style="flex:1"></span>
    <span class="mono">${esc(PRODUCT.configDir)}\\config.json</span>`;

  const hud = $("hud");
  const visible = globalValue("hud.visible");
  const help = controlHelp();
  const flags = [globalValue("hud.always_on_top") ? "always on top" : "normal z-order",
    globalValue("hud.click_through") ? "click through" : "clickable"].join(" · ");
  hud.className = `hud ${visible ? "" : "hidden"}`;
  hud.style.opacity = String(globalValue("hud.opacity"));
  hud.style.marginRight = `${Math.min(28, globalValue("hud.margin") / 2)}px`;
  hud.innerHTML = `<div><b>Astrolabe · ${state.inputMode === "3d" ? "3D" : "Pointer"}</b></div>
    <div>${esc(app.name)} · ${state.inputMode === "3d" ? esc(OPTION_LABELS[state.navMode]) + " · " + esc(help.label) : "Pointer"}</div>
    <div class="sub">${esc(help.current)}</div>
    <div class="sub">Last: ${esc(RUNTIME.lastBinding)}</div>
    <div class="tagline">${esc(flags)}</div>`;
}

function render() {
  renderRail();
  renderHead();
  renderSubtabs();
  renderContent();
  renderChrome();
  renderMenus();
}

/* ------------------------------------------------------------------- balls */

function ballContext() {
  return {
    mode: state.inputMode,
    deadzone: globalValue("pointer.scroll.deadzone"),
    dominance: globalValue("pointer.scroll.dominance"),
    cursorGain: globalValue("pointer.cursor.gain"),
    scrollGain: globalValue("pointer.scroll.gain"),
    orientation: orientation(),
  };
}
function mountBalls() {
  unmountBalls();
  const main = $("ball-main");
  if (main) mountBall(main, { variant: "orbit", size: 208, getContext: ballContext });
  const axes = $("ball-axes");
  if (axes) mountBall(axes, { variant: "axes", size: 128, getContext: ballContext });
}

/* live rate readout, updated outside the render cycle so typing is never interrupted */
setInterval(() => {
  const readout = $("ball-readout");
  if (!readout) return;
  const names = ["rx", "ry", "rz"];
  readout.innerHTML = motion.rate.map((value, index) =>
    `<div><span>${names[index]}</span><b>${value >= 0 ? "+" : ""}${value.toFixed(2)}</b></div>`).join("");
}, 90);

/* ------------------------------------------------------------------ actions */

function toast(text, iconId = "i-check") {
  const host = $("toasts");
  const node = document.createElement("div");
  node.className = "toast";
  node.innerHTML = `${icon(iconId, 12)}<span>${esc(text)}</span>`;
  host.appendChild(node);
  setTimeout(() => { node.classList.add("out"); setTimeout(() => node.remove(), 250); }, 2300);
}

function flashRow(node) {
  const row = node && node.closest(".row");
  if (row) { row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash"); }
}

function applySetting(node, id, appId, value) {
  const spec = SETTINGS_BY_ID[id];
  try {
    if (appId) setApp(appId, id, value); else setGlobal(id, value);
  } catch (error) {
    toast(`Invalid setting: ${error.message}`, "i-alert");
    return;
  }
  render();
  const fresh = document.querySelector(`[data-id="${id}"]${appId ? `[data-app="${appId}"]` : ""}`);
  flashRow(fresh);
  if (spec.cat === "hud") renderChrome();
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-act]");
  const insideMenu = !!event.target.closest(".popover");
  const menuButton = target && ["tray", "app-menu", "profile-menu"].includes(target.dataset.act);
  if (state.menu && !insideMenu && !menuButton) { state.menu = null; renderMenus(); }
  if (!target) return;
  const { act } = target.dataset;
  const id = target.dataset.id;
  const appId = target.dataset.app;

  switch (act) {
    case "panel":
      if (state.panel !== target.dataset.panel) { state.panel = target.dataset.panel; render(); }
      break;
    case "sub":
      state.sub[state.panel] = Number(target.dataset.index);
      renderSubtabs(); renderContent();
      break;
    case "set":
      applySetting(target, id, appId, parseValue(SETTINGS_BY_ID[id], target.dataset.raw));
      break;
    case "link":
      toggleAppLink(appId, id);
      render();
      toast(state.appOverrides[appId][id] === undefined
        ? `${SETTINGS_BY_ID[id].label} → linked to Global`
        : `${SETTINGS_BY_ID[id].label} → app override pinned at ${fmt(SETTINGS_BY_ID[id], appValue(appId, id))}`,
        state.appOverrides[appId][id] === undefined ? "i-link" : "i-unlink");
      break;
    case "link-pair": {
      const [sourceId, invertId] = target.dataset.pair.split(" ");
      const linked = !(sourceId in state.appOverrides[appId]) && !(invertId in state.appOverrides[appId]);
      if (linked) { setApp(appId, sourceId, appValue(appId, sourceId)); setApp(appId, invertId, appValue(appId, invertId)); }
      else { delete state.appOverrides[appId][sourceId]; delete state.appOverrides[appId][invertId]; }
      render();
      break;
    }
    case "reset":
      target.classList.add("spin");
      if (appId) resetAppSetting(appId, id); else resetGlobal(id);
      setTimeout(() => {
        render();
        toast(`${SETTINGS_BY_ID[id].label} → System default`, "i-reset");
      }, 180);
      break;
    case "reset-pair": {
      const [sourceId, invertId] = target.dataset.pair.split(" ");
      target.classList.add("spin");
      if (appId) { resetAppSetting(appId, sourceId); resetAppSetting(appId, invertId); }
      else { resetGlobal(sourceId); resetGlobal(invertId); }
      setTimeout(() => { render(); toast("Axis routing → System defaults", "i-reset"); }, 180);
      break;
    }
    case "reset-globals": {
      const count = Object.keys(state.globalOverrides).length;
      resetAllGlobals();
      render();
      toast(count ? `${count} Global override${count === 1 ? "" : "s"} cleared · device identity unchanged`
        : "No Global overrides to clear", "i-reset");
      break;
    }
    case "reset-orientation":
      AXES.forEach((axis) => {
        delete state.globalOverrides[axisId(axis)];
        delete state.globalOverrides[`input.axis_orientation.${axis}.invert`];
      });
      render();
      toast("Physical transform → System default permutation", "i-reset");
      break;
    case "link-all": {
      const wasLinked = allAppLinked(state.selectedApp);
      toggleAllAppLinks(state.selectedApp);
      render();
      toast(wasLinked
        ? `${APPS_BY_ID[state.selectedApp].name}: every setting pinned as an app override`
        : `${APPS_BY_ID[state.selectedApp].name}: all overrides removed — following Global`,
        wasLinked ? "i-unlink" : "i-link");
      break;
    }
    case "reset-app":
      resetApp(state.selectedApp);
      render();
      toast(`${APPS_BY_ID[state.selectedApp].name} pinned at its System values · integration untouched`, "i-reset");
      break;
    case "fallback-move": {
      const order = globalValue("navigation.orbit.pivot_fallbacks").slice();
      const from = order.indexOf(id);
      const to = from + Number(target.dataset.dir);
      if (to >= 0 && to < order.length) {
        order.splice(to, 0, order.splice(from, 1)[0]);
        setGlobal("navigation.orbit.pivot_fallbacks", order);
        render();
      }
      break;
    }
    case "fallback-drop":
      setGlobal("navigation.orbit.pivot_fallbacks",
        globalValue("navigation.orbit.pivot_fallbacks").filter((v) => v !== id));
      render();
      break;
    case "fallback-add":
      setGlobal("navigation.orbit.pivot_fallbacks",
        globalValue("navigation.orbit.pivot_fallbacks").concat([id]));
      render();
      break;
    case "mode": state.inputMode = target.dataset.value; render(); break;
    case "navmode": state.navMode = target.dataset.value; render(); break;
    case "layer": state.layer = target.dataset.value; render(); break;
    case "simulate": motion.simulate = !motion.simulate; renderContent(); break;
    case "recenter": motion.recenter(); toast("3D view recentred", "i-recenter"); break;
    case "card":
      if (state.openCards.has(appId)) state.openCards.delete(appId); else state.openCards.add(appId);
      renderContent();
      break;
    case "setup":
      toast(`${APPS_BY_ID[appId].name}: ${APPS_BY_ID[appId].integration.action} — demo only, nothing was written`, "i-info");
      break;
    case "copy":
      navigator.clipboard?.writeText(target.dataset.text || "").catch(() => {});
      toast("Copied", "i-copy");
      break;
    case "select-binding": state.selectedBinding = id; renderContent(); break;
    case "new-binding": {
      const overrides = state.bindingOverrides[state.profile];
      let index = 1;
      while (overrides[`user.binding.${index}`]) index += 1;
      const newId = `user.binding.${index}`;
      overrides[newId] = { label: "New binding", enabled: true, chord: [], match: "exact", action: "input.toggle", priority: 0, apps: [] };
      state.selectedBinding = newId;
      renderContent();
      toast(`${newId} created`, "i-plus");
      break;
    }
    case "restore-binding":
      delete state.bindingOverrides[state.profile][state.selectedBinding];
      renderContent();
      toast("System binding restored", "i-reset");
      break;
    case "delete-binding": {
      const binding = composedBindings().find((b) => b.id === state.selectedBinding);
      if (!binding) break;
      state.bindingOverrides[state.profile][binding.id] = binding.system ? { deleted: true } : undefined;
      if (!binding.system) delete state.bindingOverrides[state.profile][binding.id];
      state.selectedBinding = null;
      renderContent();
      toast(binding.system ? "System binding suppressed by a user patch" : "User binding deleted", "i-trash");
      break;
    }
    case "save-binding": renderContent(); toast("Binding validated and saved", "i-check"); break;
    case "advanced": state.advancedBinding = !state.advancedBinding; renderContent(); break;
    case "record":
      state.capturing = !state.capturing;
      renderContent();
      if (state.capturing) toast("Recording — press a chord, Esc to stop", "i-record");
      break;
    case "chord-remove":
      patchBinding({ chord: currentBinding().chord.filter((t) => t !== target.dataset.token) });
      break;
    case "tray": state.menu = state.menu === "tray" ? null : "tray"; renderMenus(); break;
    case "app-menu": state.menu = state.menu === "app" ? null : "app"; renderHead(); renderMenus(); break;
    case "profile-menu": state.menu = state.menu === "profile" ? null : "profile"; renderHead(); renderMenus(); break;
    case "pick-app":
      state.menu = null; state.selectedApp = appId; state.sub.perapp = 0; render();
      break;
    case "pick-profile":
      state.menu = null; state.profile = id; state.selectedBinding = null; render();
      break;
    case "tray-hud":
      setGlobal("hud.visible", !globalValue("hud.visible"));
      renderMenus(); renderChrome(); if (state.panel === "global") renderContent();
      break;
    case "tray-startup":
      state.startAtLogin = !state.startAtLogin;
      renderMenus();
      toast(state.startAtLogin ? "Start at login registered" : "Start at login removed", "i-power");
      break;
    case "tray-settings": state.menu = null; renderMenus(); toast("Settings is already open", "i-info"); break;
    case "tray-recenter": motion.recenter(); state.menu = null; renderMenus(); toast("3D view recentred", "i-recenter"); break;
    case "tray-quit": state.menu = null; renderMenus(); toast("Tray → Quit: the demo stays open", "i-info"); break;
    case "theme": break;
    default: break;
  }
});

document.addEventListener("change", (event) => {
  const target = event.target.closest("[data-act]");
  if (!target) return;
  const { act } = target.dataset;
  const id = target.dataset.id;
  const appId = target.dataset.app;
  if (act === "toggle") applySetting(target, id, appId, target.checked);
  else if (act === "choose") {
    const spec = SETTINGS_BY_ID[id];
    let value = target.value;
    if (spec.kind === "integer") value = parseInt(value, 10);
    applySetting(target, id, appId, value);
  } else if (act === "commit") commitEntry(target);
  else if (act === "foreground") { state.foreground = target.value; if (!APPS_BY_ID[state.foreground].modes.includes(state.navMode)) state.navMode = "orbit"; render(); }
  else if (act === "enable-app") {
    APPS_BY_ID[appId].integration.enabled = target.checked;
    if (!target.checked) APPS_BY_ID[appId].integration.runtime = { state: "disabled", detail: "integration disabled" };
    renderContent();
    toast(`${APPS_BY_ID[appId].name} ${target.checked ? "enabled" : "disabled"}`, target.checked ? "i-check" : "i-info");
  } else if (act === "chord-add") {
    if (target.value) patchBinding({ chord: currentBinding().chord.concat([target.value]) });
  } else if (act === "binding-field") {
    const field = target.dataset.field;
    const value = target.type === "checkbox" ? target.checked : target.value;
    if (field === "app") patchBinding({ apps: value ? [value] : [] });
    else if (field === "priority") patchBinding({ priority: Number(value) || 0 });
    else patchBinding({ [field]: value });
  }
});

/* Axis sliders: drag moves the knob without committing, so a mid-drag re-render cannot yank the
 * element out from under the pointer. The value lands on release, click, or arrow key. */
let axisDrag = null;

function axisStopFromPointer(node, clientX) {
  const rect = node.getBoundingClientRect();
  const travel = rect.width - 16;                       // knob width
  const ratio = (clientX - rect.left - 8) / (travel || 1);
  return Math.max(0, Math.min(2, Math.round(ratio * 2)));
}
function paintAxis(node, stop) {
  node.style.setProperty("--stop", stop);
  node.setAttribute("aria-valuenow", stop);
  node.setAttribute("aria-valuetext", ["X", "Y", "Z"][stop]);
  node.querySelector(".knob").textContent = ["X", "Y", "Z"][stop];
  node.querySelectorAll(".stops b").forEach((mark, index) => mark.classList.toggle("on", index === stop));
}
function commitAxis(node, stop) {
  const current = Number(node.dataset.value ?? node.getAttribute("aria-valuenow"));
  applySetting(node, node.dataset.id, node.dataset.app, stop);
  return current;
}

document.addEventListener("pointerdown", (event) => {
  const node = event.target.closest(".axis");
  if (!node) return;
  event.preventDefault();
  node.focus();
  axisDrag = { node, start: Number(node.getAttribute("aria-valuenow")) };
  paintAxis(node, axisStopFromPointer(node, event.clientX));
  node.setPointerCapture?.(event.pointerId);
});
document.addEventListener("pointermove", (event) => {
  if (!axisDrag) return;
  paintAxis(axisDrag.node, axisStopFromPointer(axisDrag.node, event.clientX));
});
document.addEventListener("pointerup", () => {
  if (!axisDrag) return;
  const { node, start } = axisDrag;
  axisDrag = null;
  const stop = Number(node.getAttribute("aria-valuenow"));
  if (stop !== start) commitAxis(node, stop);
});

document.addEventListener("keydown", (event) => {
  const axis = event.target.closest && event.target.closest(".axis");
  if (axis && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const current = Number(axis.getAttribute("aria-valuenow"));
    const next = event.key === "Home" ? 0 : event.key === "End" ? 2
      : Math.max(0, Math.min(2, current + (event.key === "ArrowRight" ? 1 : -1)));
    if (next !== current) commitAxis(axis, next);
    return;
  }
  if (state.capturing) {
    if (event.key === "Escape") { state.capturing = false; renderContent(); return; }
    event.preventDefault();
    const tokens = [];
    if (event.ctrlKey) tokens.push("keyboard:ctrl");
    if (event.shiftKey) tokens.push("keyboard:shift");
    if (event.altKey) tokens.push("keyboard:alt");
    if (event.metaKey) tokens.push("keyboard:meta");
    const key = event.key.toLowerCase();
    if (!["control", "shift", "alt", "meta"].includes(key)) tokens.push(`keyboard:${key}`);
    if (tokens.length) patchBinding({ chord: tokens });
    return;
  }
  const target = event.target.closest("[data-act='commit']");
  if (target && event.key === "Enter") { commitEntry(target); }
});

document.addEventListener("focusout", (event) => {
  const target = event.target.closest && event.target.closest("[data-act='commit']");
  if (target) commitEntry(target);
});

function commitEntry(node) {
  const id = node.dataset.id;
  const appId = node.dataset.app;
  const spec = SETTINGS_BY_ID[id];
  const current = appId ? appView(appId, spec).value : globalView(spec).value;
  if (node.value === fmt(spec, current)) return;
  let value;
  try { value = parseValue(spec, node.value); }
  catch (error) { node.value = fmt(spec, current); toast(`Invalid setting: ${error.message}`, "i-alert"); return; }
  applySetting(node, id, appId, value);
}

function currentBinding() {
  return composedBindings().find((b) => b.id === state.selectedBinding) || { chord: [] };
}
function patchBinding(patch) {
  const binding = currentBinding();
  if (!binding.id) return;
  const overrides = state.bindingOverrides[state.profile];
  overrides[binding.id] = { ...(overrides[binding.id] || {}), ...patch };
  renderContent();
}

/* --------------------------------------------------------------- tray menu */

function renderMenus() {
  const existing = document.querySelector(".popover");
  if (existing) existing.remove();
  $("tray-btn").classList.toggle("on", state.menu === "tray");
  if (state.menu === "app") return renderAppMenu();
  if (state.menu === "profile") return renderProfileMenu();
  if (state.menu !== "tray") return;
  const connected = APPS.filter((a) => a.integration.connected)
    .map((a) => `${a.id} v${a.integration.connected}`).join(", ") || "none";
  const node = document.createElement("div");
  node.className = "popover";
  node.innerHTML = `
    <button class="item" data-act="tray-settings">${icon("i-global", 13)} Open Settings</button>
    <div class="item static">Status: Connected</div>
    <div class="item static" title="${esc(connected)}">Apps: ${esc(connected)}</div>
    <div class="item static">Health: healthy: navigation-broker, solidworks</div>
    <hr>
    <button class="item" data-act="tray-recenter">${icon("i-recenter", 13)} Recenter 3D view</button>
    <button class="item" data-act="tray-hud">${globalValue("hud.visible") ? icon("i-check", 13) : `<span style="width:13px"></span>`}
      Show control panel</button>
    <button class="item" data-act="tray-startup">${state.startAtLogin ? icon("i-check", 13) : `<span style="width:13px"></span>`}
      Start at login</button>
    <hr>
    <button class="item" data-act="tray-quit">${icon("i-power", 13)} Quit</button>`;
  placePopover(node, $("tray-btn"));
}

/* A menu belongs under the thing that opened it, wherever that thing has moved to. */
function placePopover(node, anchor) {
  $("app").appendChild(node);
  if (!anchor) return;
  const frame = $("app").getBoundingClientRect();
  const box = anchor.getBoundingClientRect();
  const width = node.offsetWidth;
  const rightAligned = box.left - frame.left > frame.width / 2;
  const left = rightAligned ? box.right - frame.left - width : box.left - frame.left;
  node.style.right = "auto";
  node.style.left = `${Math.round(Math.max(6, Math.min(left, frame.width - width - 6)))}px`;
  node.style.top = `${Math.round(box.bottom - frame.top + 5)}px`;
  node.style.transformOrigin = rightAligned ? "top right" : "top left";
}

function renderAppMenu() {
  const node = document.createElement("div");
  node.className = "popover";
  const row = (app) => {
    const count = Object.keys(state.appOverrides[app.id]).length;
    const current = app.id === state.selectedApp;
    return `<button class="item" data-act="pick-app" data-app="${app.id}">
      ${current ? icon("i-check", 13) : `<span style="width:13px"></span>`}
      <span class="grow">${esc(app.name)}</span>
      ${app.integration.enabled ? `<span class="dot ok" title="integration enabled"></span>` : ""}
      ${count ? `<span class="count">${count}</span>` : ""}
    </button>`;
  };
  node.innerHTML = `<div class="applist">
    <div class="grouplabel">Supported</div>
    ${APPS.filter((a) => a.tier === "supported").map(row).join("")}
    <div class="grouplabel">Experimental</div>
    ${APPS.filter((a) => a.tier === "experimental").map(row).join("")}
  </div>`;
  placePopover(node, document.querySelector('[data-act="app-menu"]'));
}

function renderProfileMenu() {
  const node = document.createElement("div");
  node.className = "popover";
  node.innerHTML = Object.values(BINDING_PROFILES).map((profile) => {
    const patches = Object.keys(state.bindingOverrides[profile.id]).length;
    return `<button class="item" data-act="pick-profile" data-id="${profile.id}">
      ${profile.id === state.profile ? icon("i-check", 13) : `<span style="width:13px"></span>`}
      <span class="grow">${esc(profile.label)}</span>
      ${patches ? `<span class="count">${patches}</span>` : ""}
    </button>`;
  }).join("") + `<hr><div class="item static">System profiles are never rewritten; your edits are stored
    as sparse patches per profile.</div>`;
  placePopover(node, document.querySelector('[data-act="profile-menu"]'));
}

$("tray-btn").dataset.act = "tray";

$("theme-btn").addEventListener("click", () => {
  state.theme = state.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = state.theme;
  readPalette();
  renderChrome();
});

window.addEventListener("resize", () => { renderSubtabs(); renderRail(); });

render();
