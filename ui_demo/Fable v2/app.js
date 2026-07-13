/* ============================================================================
   Astrolabe control daemon — UI demo, Fable v2 (front-end only, drives nothing)
   Mirrors the real daemon's settings contract (trackball_daemon/ui.py,
   config.py, binding_schema.py) reorganized into three pages:
     Overview · 3D Apps (setup + bindings merged, master–detail) · General

   Rendering model: full page renders happen ONLY on rail navigation. Every
   smaller interaction is a partial update — sub-tab clicks swap just their
   pane, segmented controls slide a thumb in place, and value edits touch the
   DOM they own. That is what keeps navigation flash-free.
   ========================================================================== */
'use strict';

/* ------------------------------------------------------------ tiny utils */
const $  = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = v => (typeof v === 'number' ? String(+(+v).toPrecision(6)) : String(v));

/* ------------------------------------------------------------- constants */
const AXIS = ['X', 'Y', 'Z'];

const PIVOT_LABELS = {
  camera: 'Camera',
  screen_center: 'Screen Center',
  cursor: 'Under Cursor (mouse)',
  selection: 'Selection',
  cursor_3d: '3D Cursor',
  object: 'Model Center',
  origin: 'World Origin',
};
const ORBIT_PIVOT_METHODS = ['camera', 'screen_center', 'cursor', 'selection',
  'cursor_3d', 'object', 'origin'];

const OPTION_LABELS = {
  default: 'Default (General)', free: 'Free', turntable: 'Turntable',
  roll: 'Roll', zoom: 'Zoom', dolly: 'Dolly', none: 'None',
  shift: 'Shift', to_center: 'To Center', to_object: 'To Object',
  to_cursor: 'To Cursor (mouse)',
};
const optLabel = v => OPTION_LABELS[v] || String(v);

const ROUTES_BY_MODE = {
  orbit:  [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Twist', 'twist'],
           ['Pan X', 'pan_x'], ['Pan Y', 'pan_y'], ['Zoom', 'zoom']],
  camera: [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Roll', 'roll']],
  fly:    [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Bank', 'bank'],
           ['Fwd', 'forward'], ['Strafe', 'strafe'], ['Up/Dn', 'vertical']],
  walk:   [['Pitch', 'pitch'], ['Yaw', 'yaw'],
           ['Fwd', 'forward'], ['Strafe', 'strafe'], ['Up/Dn', 'vertical']],
};

/* --------------------------------------------- binding profiles (schema)
   Mirrors trackball_daemon/binding_schema.py APP_BINDING_PROFILES: which
   fields, pivots, styles, twist actions and zoom behaviors each host
   actually implements. */
const PIVOTS_DEFAULT = ['screen_center', 'cursor', 'selection', 'object', 'origin'];
const PIVOTS_CAMERA = ['camera', ...PIVOTS_DEFAULT];

const PROFILES = {
  blender:    { rich: true, pivots: ['camera', 'screen_center', 'cursor', 'selection',
                                     'cursor_3d', 'object', 'origin'],
                styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'],
                cameraLock: true },
  sketchup:   { rich: true, pivots: PIVOTS_CAMERA, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'] },
  unreal:     { rich: true, pivots: PIVOTS_CAMERA, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'] },
  unity:      { rich: true, pivots: PIVOTS_CAMERA, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'],
                dynamicClip: true, pivotExtent: true },
  godot:      { rich: true, noRoll: true, noHorizon: true, pivots: PIVOTS_CAMERA,
                styles: ['turntable'], twist: ['zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'] },
  freecad:    { pivots: PIVOTS_DEFAULT, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: [] },
  fusion360:  { pivots: PIVOTS_DEFAULT, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: ['zoom', 'dolly'] },
  solidworks: { pivots: PIVOTS_DEFAULT, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: [] },
  onshape:    { pivots: PIVOTS_DEFAULT, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: [], userscript: true },
  autocad:    { pivots: PIVOTS_CAMERA, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: ['zoom', 'dolly'] },
  rhino:      { pivots: PIVOTS_CAMERA, styles: ['default', 'free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: ['zoom', 'dolly'] },
};

/* ------------------------------------------------- default config (real) */
const DEFAULT_ACTION_AXIS_SOURCE = {
  orbit:  { pitch: 0, yaw: 1, twist: 2, pan_x: 0, pan_y: 1, zoom: 2 },
  camera: { pitch: 0, yaw: 1, roll: 2 },
  fly:    { pitch: 0, yaw: 1, bank: 2, forward: 1, strafe: 0, vertical: 2 },
  walk:   { pitch: 0, yaw: 1, forward: 1, strafe: 0, vertical: 2 },
};

function defaultModeInvert() {
  const out = {};
  for (const [mode, actions] of Object.entries(DEFAULT_ACTION_AXIS_SOURCE)) {
    out[mode] = {};
    for (const k of Object.keys(actions)) out[mode][k] = false;
  }
  return out;
}

const ZB_DEFAULTS = { blender: 'zoom', fusion360: 'zoom', autocad: 'zoom' };

function defApp(key) {
  const p = PROFILES[key];
  const a = {
    rate_hz: 0,
    orbit_pivot_hold_sec: 0.5,
    zoom_cursor_hold_sec: 0.5,
    selection_overrides_pivot: true,
    level_horizon_on_entry: null,        // null = follow the General default
    bindings: {
      orbit: { axis_source: [0, 1, 2], sensitivity: 1.0 },
      pan:   { x_src: 1, y_src: 0, gain: 1.0 },
      zoom:  { src: 2, gain: 1.0, dominance: 1.7 },
      toggle: 'shift',
      invert: { orbit: [false, false, false], pan: [false, false], zoom: false },
      scheme: { orbit_pivot: 'default', orbit_style: 'default', zoom_mode: 'default' },
    },
    advanced: { twist_action: p.noRoll ? 'zoom' : 'roll' },
  };
  if (p.zb.length) a.advanced.zoom_style = ZB_DEFAULTS[key] || 'dolly';
  if (p.rich) {
    Object.assign(a.advanced, {
      nav_mode: 'orbit',
      lock_horizon: key === 'godot',
      pan_scales_with_distance: true,
      fly_speed: 1.0,
      walk_speed: 1.0,
      invert: defaultModeInvert(),
      axis_source: JSON.parse(JSON.stringify(DEFAULT_ACTION_AXIS_SOURCE)),
    });
  }
  if (key === 'blender') {
    a.bindings.scheme.orbit_pivot = 'camera';
    a.advanced.lock_camera_to_view = false;
  }
  if (key === 'unity') {
    a.advanced.override_dynamic_clip = true;
    a.advanced.pivot_extent_mult = 8.0;
  }
  if (key === 'godot') a.bindings.scheme.orbit_style = 'turntable';
  return a;
}

function buildDefaultConfig() {
  const apps = {};
  for (const key of Object.keys(PROFILES)) apps[key] = defApp(key);
  return {
    device: { name: 'Trackball BLE', address: '' },
    general: {
      default_mode: 'cube',
      axis_orientation: { source: [0, 1, 2], invert: [false, false, false] },
      cursor: { gain: 216.0 },
      scroll: { gain: 29.0, deadzone: 0.004, dominance: 1.7 },
      buttons: { left: 'left', right: 'right', middle: 'middle' },
      scheme: { orbit_pivot: 'screen_center', orbit_style: 'free', zoom_mode: 'to_center' },
      orbit_pivot_fallbacks: ['cursor_3d', 'camera', 'object', 'origin'],
      level_horizon_on_entry: true,
      start_at_login: false,
    },
    apps,
    bridge: { port: 47900, rate_hz: 30 },
    onshape: { cursor_userscript_warn_dismissed: false },
  };
}

/* ------------------------------------------------- 3D-app registry (mock) */
const CERTUTIL = 'certutil -user -addstore Root "%APPDATA%\\TrackballDaemon\\onshape_cert.pem"';

const APPS = [
  {
    key: 'blender', name: 'Blender',
    status: { chip: 'good', text: 'active • connected (v0.1.12)', short: 'connected' },
    detected: 'v5.1.1 — C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe',
    versions: 'Blender 4.2 through 5.1 (tested on 5.1.1)',
    installModel: 'User add-on copied into each detected Blender version; optional startup shim.',
    security: 'Copies unsigned Python only into current-user folders. The optional startup shim runs it at Blender launch (asked separately). No elevation or external listener.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies Trackball Nav to every detected user scripts/addons folder and asks whether to add the auto-enable startup shim.',
      manual: 'Copy trackball_daemon\\plugins\\blender\\trackball_nav to %APPDATA%\\Blender Foundation\\Blender\\<version>\\scripts\\addons\\trackball_nav, then enable Trackball Nav in Preferences > Add-ons. No administrator access is required.',
      health: 'Restart Blender. The row should show connected while Blender is focused; details are in %APPDATA%\\TrackballDaemon\\blender_addin.log.',
    },
  },
  {
    key: 'freecad', name: 'FreeCAD',
    status: { chip: 'idle', text: 'installed · v0.1.6', short: 'installed' },
    detected: 'v1.1.1 — C:\\Program Files\\FreeCAD 1.1\\bin\\FreeCAD.exe',
    versions: 'FreeCAD 1.0 through 1.1 (tested on 1.1.1)',
    installModel: 'User Mod add-on; files are copied only under the current Windows profile.',
    security: 'Copies unsigned Python into FreeCAD\'s current-user Mod folder, loaded at startup. No elevation, registry write, or external port.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav into FreeCAD\'s version-aware user Mod folder.',
      manual: 'Copy trackball_daemon\\plugins\\freecad\\TrackballNav to %APPDATA%\\FreeCAD\\v<major>-<minor>\\Mod\\TrackballNav (FreeCAD 1.x), then restart FreeCAD. Use %APPDATA%\\FreeCAD\\Mod for older layouts.',
      health: 'Open a 3D view and focus FreeCAD; the row should show connected. Check %APPDATA%\\TrackballDaemon\\freecad_addin.log if it does not.',
    },
  },
  {
    key: 'sketchup', name: 'SketchUp',
    status: { chip: 'idle', text: 'installed · v0.2.3', short: 'installed' },
    detected: 'v2026.2.243 — C:\\Program Files\\SketchUp\\SketchUp 2026\\SketchUp.exe',
    versions: 'SketchUp Desktop 2025 through 2026 (tested on 2026.2.243)',
    installModel: 'Per-version Ruby extension in SketchUp\'s user Plugins folder.',
    security: 'Copies an unsigned Ruby extension into SketchUp\'s current-user Plugins folder; SketchUp executes it at startup. No elevation or external listener.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies the loader and extension into every detected annual release.',
      manual: 'Copy trackball_daemon\\plugins\\sketchup\\trackball_nav_loader.rb and the trackball_nav folder to %APPDATA%\\SketchUp\\SketchUp <year>\\SketchUp\\Plugins, then restart SketchUp. SketchUp for Web is not supported.',
      health: 'Extension Manager should list Trackball Nav; focus a model and look for connected in this row or inspect %APPDATA%\\TrackballDaemon\\sketchup_addin.log.',
    },
  },
  {
    key: 'unreal', name: 'Unreal Engine',
    status: { chip: 'warn', text: 'installed · v0.2.3 — update available', short: 'update' },
    detected: 'v5.9.0 — C:\\Program Files\\Epic Games\\UE_5.9\\...\\UnrealEditor.exe',
    versions: 'Unreal Engine 5.8 (tested on 5.8.0)',
    alert: { level: 'warn', text: 'CAUTION: detected host version 5.9.0 has not been verified.' },
    installModel: 'Unreal Editor plugin, installed per engine or per project.',
    security: 'Engine-wide setup writes an unsigned Python editor plugin under Program Files and may need UAC; per-project install avoids elevation. Loopback-only connection.',
    setupRequired: true, action: 'Update → v0.2.4',
    instructions: {
      auto: 'Set up copies TrackballNav to each detected Engine/Plugins folder. Engine-level writes may require administrator permission.',
      manual: 'Without administrator access, copy trackball_daemon\\plugins\\unreal\\TrackballNav to <YourProject>\\Plugins\\TrackballNav. Enable Trackball Nav and Python Editor Script Plugin in Edit > Plugins, then restart the editor.',
      health: 'Focus a perspective level viewport; the row should show connected. Check %APPDATA%\\TrackballDaemon\\unreal_addin.log and the Output Log on failure.',
    },
  },
  {
    key: 'unity', name: 'Unity',
    status: { chip: 'idle', text: 'detected — not set up', short: 'not set up' },
    detected: 'v6000.5.3f1 — C:\\Program Files\\Unity\\Hub\\Editor\\6000.5.3f1\\Editor\\Unity.exe',
    versions: 'Unity 6 / 6000.x (implemented against 6000.5.3f1)',
    installModel: 'UPM Editor package copied into each detected Unity project.',
    security: 'Copies unsigned C# editor source into each detected project\'s Packages folder; Unity compiles and runs it in the Editor. No elevation or machine-wide change.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up finds running/recent projects and copies the package into each project\'s Packages folder; Unity recompiles it automatically.',
      manual: 'Copy trackball_daemon\\plugins\\unity\\com.astrolabe.trackball-nav to <YourProject>\\Packages\\com.astrolabe.trackball-nav. If Set up found no project, the same package is staged under %APPDATA%\\TrackballDaemon\\unity.',
      health: 'Open and focus a Scene view; the row should show connected. Check the Unity Console and %APPDATA%\\TrackballDaemon\\unity_addin.log.',
    },
  },
  {
    key: 'godot', name: 'Godot',
    status: { chip: 'off', text: 'not detected', short: '—' },
    detected: 'not detected',
    versions: 'Godot 4.4 through 4.7',
    installModel: 'Godot EditorPlugin copied and enabled per project.',
    security: 'Copies unsigned GDScript into each detected project and edits that project\'s project.godot to enable the plugin. No elevation or machine-wide change.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up finds running/recent projects, copies addons/trackball_nav, and enables res://addons/trackball_nav/plugin.cfg.',
      manual: 'Copy trackball_daemon\\plugins\\godot\\trackball_nav to <YourProject>\\addons\\trackball_nav, then enable Trackball Nav under Project > Project Settings > Plugins. A staged copy is also placed under %APPDATA%\\TrackballDaemon\\godot when no project is found.',
      health: 'Reload the project, focus a 3D editor viewport, and look for connected. Check %APPDATA%\\TrackballDaemon\\godot_addin.log on failure.',
    },
  },
  {
    key: 'rhino', name: 'Rhino',
    status: { chip: 'idle', text: 'installed · v0.1.2', short: 'installed' },
    detected: 'v8.19 — C:\\Program Files\\Rhino 8\\System\\Rhino.exe',
    versions: 'Rhino 8',
    installModel: 'Rhino 8 user Python scripts plus a per-user startup command.',
    security: 'Copies unsigned Python into Rhino\'s current-user scripts folder and may edit the user startup-command XML so it runs at launch. No elevation.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav into Rhino\'s user scripts folder and best-effort registers its startup command.',
      manual: 'Copy trackball_daemon\\plugins\\rhino\\TrackballNav to %APPDATA%\\McNeel\\Rhinoceros\\8.0\\scripts\\TrackballNav. In Rhino Options > General, add _-RunPythonScript "<path>\\start.py" to startup commands, then restart Rhino.',
      health: 'Focus a Rhino viewport and look for connected. Check %APPDATA%\\TrackballDaemon\\rhino_addin.log if startup failed.',
    },
  },
  {
    key: 'fusion360', name: 'Fusion 360',
    status: { chip: 'idle', text: 'installed · v0.1.16', short: 'installed' },
    detected: '%LOCALAPPDATA%\\Autodesk\\webdeploy\\production\\Fusion360.exe (rolling release)',
    versions: 'Current Fusion production release (rolling Autodesk release)',
    installModel: 'Fusion user add-in copied to Autodesk\'s per-user AddIns folder.',
    security: 'Copies unsigned Python into Fusion\'s current-user AddIns folder. Fusion runs it only after you click Run / Run on Startup. No elevation.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav. In Fusion, open Utilities > Add-Ins, run TrackballNav once, and enable Run on Startup.',
      manual: 'Copy trackball_daemon\\plugins\\fusion360\\TrackballNav to %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\TrackballNav, then run it from Utilities > Add-Ins. No administrator access is required.',
      health: 'Focus an open design and look for connected. Check %APPDATA%\\TrackballDaemon\\fusion_addin.log if the add-in does not handshake.',
    },
  },
  {
    key: 'solidworks', name: 'SolidWorks',
    status: { chip: 'idle', text: 'ready · direct COM', short: 'ready' },
    detected: 'v2025 — C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe',
    versions: 'SOLIDWORKS 2025 (tested on 2025)',
    installModel: 'Direct COM automation; no SolidWorks add-in or host files are installed.',
    security: 'Per-user COM automation against an already-running instance. No COM server registration, DLL install, elevation, or external interface.',
    setupRequired: false, action: null,
    instructions: {
      auto: 'Switching the mode slider off Off performs a one-time prerequisite check for SOLIDWORKS and pywin32. Nothing else is needed.',
      manual: 'There is nothing to copy. If the prerequisite check fails, install pywin32 into the daemon\'s Python environment with: pip install pywin32.',
      health: 'Open a part or assembly and focus SOLIDWORKS; the row should show connected. Driver messages are recorded in %APPDATA%\\TrackballDaemon\\daemon.log.',
    },
  },
  {
    key: 'onshape', name: 'Onshape',
    status: { chip: 'idle', text: 'not set up', short: 'not set up' },
    detected: 'Chrome 138 — bridge endpoint 127.51.68.120:8181 reserved',
    versions: 'Current Onshape web release (rolling release)',
    installModel: 'Browser bridge; no Onshape add-in is installed.',
    security: 'Creates a per-user self-signed certificate; TLS binds only to 127.51.68.120. Trust-store install is never automatic. The optional userscript reports canvas pointer coordinates only.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up creates the bridge\'s per-user local TLS certificate. Trust it once and enable SpaceMouse/3Dconnexion in Onshape preferences.',
      manual: 'No application files need copying. Generate/trust the certificate using the Set up dialog or certutil -user, then install the supplied userscript only if Under Cursor orbit is wanted. Administrator access is not required.',
      health: 'Open and focus an Onshape document; the row should show connected after the browser handshake. Check %APPDATA%\\TrackballDaemon\\daemon.log.',
    },
  },
  {
    key: 'autocad', name: 'AutoCAD',
    status: { chip: 'bad', text: 'unsupported host', short: 'unsupported' },
    detected: 'v2024 — C:\\Program Files\\Autodesk\\AutoCAD 2024\\acad.exe',
    versions: 'AutoCAD 2025 through 2027 (.NET 8 family; tested on 2026)',
    alert: { level: 'bad', text: 'WARNING: detected version 2024 is unsupported.' },
    installModel: 'Per-user .NET plugin staged by the daemon and NETLOADed automatically.',
    security: 'Stages an unsigned .NET DLL under the current user\'s APPDATA, adds only that folder to TRUSTEDPATHS, and NETLOADs it via COM. Never launches AutoCAD or writes Program Files.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up stages TrackballNavAcad.dll under the daemon\'s APPDATA folder. The daemon adds that folder to TRUSTEDPATHS and NETLOADs it on attach.',
      manual: 'Copy trackball_daemon\\plugins\\autocad\\TrackballNavAcad.dll and version.json to %APPDATA%\\TrackballDaemon\\acad_plugin. Add that folder to TRUSTEDPATHS and run NETLOAD on the DLL. No Program Files write is needed.',
      health: 'Type TBNAV in AutoCAD or look for connected in this row. Plugin details are in %APPDATA%\\TrackballDaemon\\acad_plugin.log.',
    },
  },
];
const APPS_BY_KEY = Object.fromEntries(APPS.map(a => [a.key, a]));

/* enabled state (operational, outside the reset boundary — like the daemon) */
const APP_ENABLED = {
  blender: true, freecad: true, sketchup: true, unreal: true, unity: false,
  godot: false, rhino: true, fusion360: true, solidworks: true,
  onshape: false, autocad: false,
};

/* ---------------------------------------------------------------- state */
const S = {
  page: 'overview',
  mode: 'cube',                 // tray-level toggle: 'cursor' | 'cube'
  app: 'blender',               // 3D Apps master-detail selection (= editing app)
  appTab: {},                   // appKey -> setup | tuning | nav | routing
  genTab: 'device',
  routeTab: {},                 // appKey -> routing mode tab
  fbSel: -1,
  fbFlash: -1,
  cfg: buildDefaultConfig(),
};

/* --------------------------------------------------------- config access */
function getPath(path) {
  return path.split('.').reduce((n, k) => n[k], S.cfg);
}
function setPath(path, value) {
  const ks = path.split('.');
  let node = S.cfg;
  for (const k of ks.slice(0, -1)) node = node[k];
  node[ks[ks.length - 1]] = value;
}

/* pristine defaults, used by the per-control divergence resets */
const DEFCFG = buildDefaultConfig();
const getDefault = path => path.split('.').reduce((n, k) => n[k], DEFCFG);
const divergedAny = paths => paths.some(p => getPath(p) !== getDefault(p));

/* ------------------------------------------------------------ components */
const tipIcon = t => t ? `<span class="tip" title="${esc(t)}">&#9432;</span>` : '';

function miniReset(paths) {
  if (!paths || !paths.length) return '<span class="rs"></span>';
  return `<span class="rs"><button class="mini-reset${divergedAny(paths) ? ' show' : ''}"
    data-reset-paths="${paths.join('|')}"
    title="Changed from the default — click to reset">&#8634;</button></span>`;
}

/* the v2 row grammar: label | control | divergence reset | descriptor */
function row(label, ctrl, hint = '', tip = '', resetPaths = null) {
  return `<div class="row">
    <span class="rl">${esc(label)}${tipIcon(tip)}</span>
    <span class="rc">${ctrl}</span>
    ${miniReset(resetPaths)}
    <span class="hint">${esc(hint)}</span>
  </div>`;
}

function ctrlText(path, { cast = 'num', wide = false } = {}) {
  const v = getPath(path);
  return `<input class="text${wide ? ' wide' : ''}" data-path="${path}" data-cast="${cast}"
          value="${esc(cast === 'num' || cast === 'int' ? fmt(v) : v)}" spellcheck="false">`;
}

function ctrlSelect(path, options, { warn = '' } = {}) {
  const v = String(getPath(path));
  const opts = options.map(o => {
    const [val, lab] = Array.isArray(o) ? o : [o, optLabel(o)];
    return `<option value="${esc(val)}"${String(val) === v ? ' selected' : ''}>${esc(lab)}</option>`;
  }).join('');
  return `<select class="sel" data-path="${path}" data-cast="str"${warn ? ` data-warn="${warn}"` : ''}>${opts}</select>`;
}

function ctrlRate(path) {
  const v = getPath(path);
  return `<input class="text" list="rate-presets" data-path="${path}" data-cast="rate"
          value="${esc((!v || +v === 0) ? 'Default' : String(v))}" spellcheck="false">`;
}

function ctrlSwitch(path, { checked = null } = {}) {
  const on = checked === null ? !!getPath(path) : checked;
  return `<label class="switch">
    <input type="checkbox" data-path="${path}" data-cast="bool" ${on ? 'checked' : ''}>
    <span class="track"></span>
  </label>`;
}

function axisSeg(path) {
  const cur = +getPath(path);
  return `<span class="axis-seg">` + AXIS.map((n, i) =>
    `<button data-axpick="${path}" data-ax="${i}" data-ax-hl="${i}"
             class="${i === cur ? 'on' : ''}" aria-label="source ${n}">${n}</button>`
  ).join('') + `</span>`;
}

function invPill(path) {
  const on = !!getPath(path);
  return `<button class="inv-pill ${on ? 'on' : ''}" data-invpick="${path}">Invert</button>`;
}

function routeRow(name, axPath, invPath) {
  return `<div class="route-row">
    <span class="route-name">${esc(name)}</span>
    ${axisSeg(axPath)}
    ${invPill(invPath)}
    ${miniReset([axPath, invPath])}
  </div>`;
}

/* segmented control with a sliding thumb (same feel as the rail toggle) */
function seg(options, current, dataAttr, cls = '') {
  return `<span class="seg ${cls}">
    <span class="seg-thumb"></span>` + options.map(([val, lab]) =>
    `<button data-${dataAttr}="${esc(val)}" class="${val === current ? 'on' : ''}">${esc(lab)}</button>`
  ).join('') + `</span>`;
}

function placeThumb(segEl, animate = false) {
  const thumb = $('.seg-thumb', segEl);
  const on = $('button.on', segEl);
  if (!thumb || !on) return;
  if (!animate) thumb.style.transition = 'none';
  thumb.style.width = on.offsetWidth + 'px';
  thumb.style.transform = `translateX(${on.offsetLeft}px)`;
  if (!animate) { void thumb.offsetWidth; thumb.style.transition = ''; }
}

function initSegThumbs(scope = document) {
  $$('.seg', scope).forEach(s => placeThumb(s));
}

function selectSegButton(segEl, btn, animate = true) {
  $$('button', segEl).forEach(b => b.classList.toggle('on', b === btn));
  placeThumb(segEl, animate);
}

function sect(title, note = '') {
  return `<h3 class="sect">${esc(title)}${note ? ` <span class="sect-note">— ${esc(note)}</span>` : ''}</h3>`;
}

/* ---------------------------------------------------------- shared texts */
const TIPS = {
  rate: 'Per app; Default = the global bridge rate (General → Device). Higher is smoother (try 60); lower it if the app lags. Applies live and follows whichever app is focused.',
  twist: 'Action driven by unshifted twist while Orbit is the active mode, including Turntable.',
  levelHorizon: 'On: switching into a fixed-horizon mode (Turntable / Lock horizon / Walk) removes any existing roll once. Off: the current tilt is locked as-is. Until toggled here, follows the General default; the reset arrow makes it follow General again.',
  levelHorizonGeneral: 'On: switching into a fixed-horizon mode (Turntable / Lock horizon / Walk) removes any existing roll instead of locking the tilted horizon. Per-app switches override this default.',
  selOverride: 'When on and something is selected, orbit (and supported To Cursor zoom) uses the selection centre instead of the designated pivot. Camera remains turn-in-place.',
  fallback: 'The selected pivot is tried first. If it is unavailable, resolution restarts at item 1 of this chain; unsupported methods are skipped by that integration. An empty list means no fallback. Selection override still wins when enabled, except Camera always turns in place.',
  sources: 'Sources are logical axes after the global physical orientation (General → Device). Edits apply live.',
  holds: 'Pivot hold ends an orbit gesture after this idle gap; pan clears the orbit pivot immediately. Zoom hold applies only to To Cursor zoom — pan preserves that target, orbiting invalidates it.',
  userscript: 'The Under Cursor orbit pivot in Onshape needs a small userscript that POSTs the exact #canvas pointer position to the local bridge — DOM only, loopback only, no screen capture.',
  dynamicClip: 'Scene View Camera → Dynamic Clipping auto-fits near/far planes from the view size, which can feel like zoom-to-fit while you look around. On = Trackball Nav forces it off and uses fixed planes.',
};

const PIVOT_TIPS = {
  blender: 'Screen Center raycasts the surface under the viewport centre; Under Cursor under the mouse (both held per gesture); 3D Cursor is Blender\'s 3D cursor; Model Center uses the scene bounds.',
  sketchup: 'No 3D-cursor target in SketchUp. Screen Center raycasts under the viewport centre; Under Cursor under the mouse (both held per gesture); Model Center uses model.bounds.',
  unreal: 'No 3D cursor in Unreal. Screen Center raycasts under the viewport center; Under Cursor under the mouse — the level viewport must be focused.',
  unity: 'Screen Center raycasts under the viewport center; Under Cursor under the mouse.',
  godot: 'Screen Center raycasts under the viewport center; Under Cursor under the mouse.',
  default: 'Camera turns in place; Screen Center raycasts the surface under the viewport centre; Under Cursor raycasts under the mouse (held per gesture); a miss continues through the General fallback chain.',
};

const MODE_TITLES = {
  off:   'Off — ball motion is not routed to this app',
  orbit: 'Orbit — rotate around the configured pivot; hold the toggle for pan / zoom',
  fly:   'Fly — free 6DOF flight: banks on twist, forward follows pitch; Shift strafes and rises',
  walk:  'Walk — horizon-locked look, movement stays on the ground plane; Shift moves, twist rises/falls',
};
const MODE_TITLES_GODOT = Object.assign({}, MODE_TITLES, {
  fly: 'Fly — horizon-locked flight (no roll); movement follows the view forward',
});

/* ================================================================= views */
const PAGES = { overview: 1, apps: 1, general: 1 };

/* ------------------------------------------------------------- overview */
function viewOverview() {
  const tiles = APPS.map(a => `
    <button class="tile" data-goto-app="${a.key}" title="${esc(a.status.text)} — open in 3D Apps">
      <span class="dot ${a.status.chip}"></span><span class="tile-nm">${esc(a.name)}</span>
      <span class="tile-st">${esc(a.status.short)}</span>
    </button>`).join('');

  return `
  <div class="ov">
    <div>
      ${sect('Link')}
      <dl>
        <div class="fact-row"><dt>Device</dt>
          <dd>Trackball BLE <span class="sub mono">notify 7 ms · 2 links · found by name scan</span></dd></div>
        <div class="fact-row"><dt>Status</dt>
          <dd style="color:var(--good)">Connected
            <span class="sub">rotation stream subscribed — onboard HID mouse suppressed</span></dd></div>
        <div class="fact-row"><dt>Mode</dt>
          <dd id="fact-mode">${S.mode === 'cube' ? '3D navigation' : 'Cursor'}
            <span class="sub">switch with the rail toggle or from the tray</span></dd></div>
      </dl>
      ${sect('Runtime')}
      <dl>
        <div class="fact-row"><dt>Daemon</dt>
          <dd class="mono">v0.1.59 <span class="sub">tray-resident; closing this window only hides it</span></dd></div>
        <div class="fact-row"><dt>Nav broker</dt>
          <dd class="mono">127.0.0.1:47900 <span class="sub">loopback socket the host add-ons connect to</span></dd></div>
        <div class="fact-row"><dt>Onshape bridge</dt>
          <dd class="mono">127.51.68.120:8181 <span class="sub">local TLS NL-proxy for Onshape's browser client</span></dd></div>
        <div class="fact-row"><dt>Config &amp; log</dt>
          <dd class="mono">%APPDATA%\\TrackballDaemon\\ <span class="sub">config.json · daemon.log · per-host add-in logs</span></dd></div>
        <div class="fact-row"><dt>Start at login</dt>
          <dd>${ctrlSwitch('general.start_at_login')}
            <span class="sub">adds the daemon to the current user's Run key — no elevation</span></dd></div>
        <div class="fact-row"><dt>Recenter</dt>
          <dd><button class="btn btn-sm" id="btn-recenter" style="margin:1px 0">Recenter 3D view</button>
            <span class="sub">re-frames the focused viewport — same as tray → Recenter</span></dd></div>
      </dl>
    </div>
    <div>
      ${sect('Integrations', 'click one to set up or tune')}
      <div class="tiles">${tiles}</div>
      ${sect('First run')}
      <ol class="steps">
        <li>Pick the host above, check <b>Setup</b>, and run <b>Set&nbsp;up</b>.</li>
        <li>Restart or reload the host, then focus its 3D viewport.</li>
        <li>Flip the rail toggle to <b>3D&nbsp;Nav</b> and move the ball — hold <b>Shift</b> to pan&nbsp;/&nbsp;zoom.</li>
        <li>Tune feel per app under <b>Tuning</b> and <b>Navigation</b>.</li>
      </ol>
    </div>
  </div>`;
}

/* --------------------------------------------------------------- 3D apps */
const APP_TABS = [['setup', 'Setup'], ['tuning', 'Tuning'], ['nav', 'Navigation'], ['routing', 'Routing']];
const MODE_LETTER = { orbit: 'O', fly: 'F', walk: 'W' };

function appMode(key) {
  if (!APP_ENABLED[key]) return 'off';
  return PROFILES[key].rich ? getPath(`apps.${key}.advanced.nav_mode`) : 'orbit';
}

function appItemInner(a) {
  const mode = appMode(a.key);
  const state = mode === 'off'
    ? `<span class="dot ${a.status.chip} dim"></span>`
    : `<span class="app-badge st-${a.status.chip}"
             title="${esc(a.status.text)} — mode: ${mode}">${MODE_LETTER[mode]}</span>`;
  return `${state}${esc(a.name)}`;
}

function appListHTML() {
  return APPS.map(a => `
    <button class="app-item ${a.key === S.app ? 'on' : ''}" data-appsel="${a.key}">
      ${appItemInner(a)}
    </button>`).join('');
}

/* per-app active-control slider: Off · Orbit (· Fly · Walk on rich hosts) */
function modeSlider(key) {
  const p = PROFILES[key];
  const cur = appMode(key);
  const opts = p.rich ? ['off', 'orbit', 'fly', 'walk'] : ['off', 'orbit'];
  const titles = p.noRoll ? MODE_TITLES_GODOT : MODE_TITLES;
  const extra = key === 'blender' ? ' In Blender, Alt+` cycles modes too.' : '';
  return `<span class="seg mode-slider ms-${cur}" data-mslider="${key}"
    title="Active control mode while ${esc(APPS_BY_KEY[key].name)} has focus in 3D mode.${esc(extra)}">
    <span class="seg-thumb"></span>
    ${opts.map(o => `<button data-mslide="${o}" class="${o === cur ? 'on' : ''}"
      title="${esc(titles[o])}">${o[0].toUpperCase()}${o.slice(1)}</button>`).join('')}
  </span>`;
}

function appDetailHTML(key) {
  const a = APPS_BY_KEY[key];
  const tab = S.appTab[key] || 'setup';
  const chipClass = { good: 'chip-good', idle: 'chip-idle', warn: 'chip-warn',
                      bad: 'chip-bad', off: 'chip-off' }[a.status.chip];
  return `
  <div class="detail-head">
    <span class="app-name">${esc(a.name)}</span>
    <span class="chip ${chipClass}">${esc(a.status.text)}</span>
    <span class="spacer"></span>
    ${modeSlider(key)}
    <button class="btn-icon" data-reset-app="${key}"
            title="Reset user overrides — every ${esc(a.name)} navigation setting back to shipped defaults. Mode and install state are preserved.">&#8634;</button>
  </div>
  <div class="subtabs">${seg(APP_TABS, tab, 'subtab')}</div>
  <div id="app-pane-host">${appPaneHTML(key, tab, false)}</div>`;
}

function appPaneHTML(key, tab, anim) {
  const inner = { setup: paneSetup, tuning: paneTuning, nav: paneNav, routing: paneRouting }[tab](key);
  return `<div class="${anim ? 'pane ' : ''}pane-fill" data-pane="${key}-${tab}">${inner}</div>`;
}

function viewApps() {
  return `
  <div class="apps">
    <div class="app-list">${appListHTML()}</div>
    <div class="detail" id="app-detail">${appDetailHTML(S.app)}</div>
  </div>`;
}

function paneSetup(key) {
  const a = APPS_BY_KEY[key];
  const actionBtn = a.action
    ? `<button class="btn ${/Set up|Update/.test(a.action) ? 'btn-primary' : ''}"
               data-setup="${a.key}">${esc(a.action)}</button>` : '';
  return `
  <div class="pane-setup">
    <dl class="app-meta">
      <dt>Detected</dt><dd class="mono">${esc(a.detected)}</dd>
      <dt>Supported</dt><dd>${esc(a.versions)}</dd>
      <dt>Install model</dt><dd>${esc(a.installModel)}</dd>
      <dt>Security</dt><dd class="sec">${esc(a.security)}</dd>
      <dt>Setup</dt><dd>${a.setupRequired ? 'one-time setup required' : 'no host setup required'}</dd>
    </dl>
    ${a.alert ? `<p class="app-alert ${a.alert.level}">${esc(a.alert.text)}</p>` : ''}
    <div class="app-actions">
      <span class="spacer"></span>
      <button class="btn btn-sm btn-ghost" data-copy-instructions="${a.key}">Copy instructions</button>
      ${actionBtn}
    </div>
    <div class="instructions-box">
      <h4>Automatic setup</h4>${esc(a.instructions.auto)}
      <h4>Manual setup / restricted permissions</h4>${esc(a.instructions.manual)}
      <h4>Health check</h4>${esc(a.instructions.health)}
    </div>
  </div>`;
}

function paneTuning(key) {
  const p = PROFILES[key];
  const b = `apps.${key}.bindings`;
  let out = `
  ${sect('Sensitivity & rate', `how ball motion drives ${APPS_BY_KEY[key].name}`)}
  ${row('Viewport refresh (Hz)', ctrlRate(`apps.${key}.rate_hz`),
        'Default = the global bridge rate; lower it if the host lags.', TIPS.rate,
        [`apps.${key}.rate_hz`])}
  ${row('Orbit sensitivity', ctrlText(`${b}.orbit.sensitivity`),
        '1.0 = this app’s aligned baseline (a true 1:1 follow).', '',
        [`${b}.orbit.sensitivity`])}
  ${row('Pan gain', ctrlText(`${b}.pan.gain`),
        'Held-pan multiplier — radians → world units.', '', [`${b}.pan.gain`])}
  ${row('Zoom gain', ctrlText(`${b}.zoom.gain`),
        'Held-twist zoom / dolly multiplier.', '', [`${b}.zoom.gain`])}
  ${row('Zoom dominance', ctrlText(`${b}.zoom.dominance`),
        'How strongly twist must beat the pan plane to count as zoom.', '',
        [`${b}.zoom.dominance`])}
  ${row('Orbit ↔ pan/zoom', ctrlSelect(`${b}.toggle`, [['shift', 'Shift'], ['none', 'None']]),
        'Shift routes held motion to pan / zoom; None always orbits.', '', [`${b}.toggle`])}`;
  if (p.rich) {
    const adv = `apps.${key}.advanced`;
    out += `
  ${row('Fly speed', ctrlText(`${adv}.fly_speed`),
        'Movement multiplier while Fly is the active mode.', '', [`${adv}.fly_speed`])}
  ${row('Walk speed', ctrlText(`${adv}.walk_speed`),
        'Movement multiplier while Walk is the active mode.', '', [`${adv}.walk_speed`])}`;
  }
  out += `
  ${sect('Gesture timing', 'independent hold timers')}
  ${row('Pivot hold (s)', ctrlText(`apps.${key}.orbit_pivot_hold_sec`),
        'Idle gap that ends an orbit gesture; pan clears it at once.', TIPS.holds,
        [`apps.${key}.orbit_pivot_hold_sec`])}
  ${row('Zoom hold (s)', ctrlText(`apps.${key}.zoom_cursor_hold_sec`),
        'To Cursor zoom only — pan keeps the target.', TIPS.holds,
        [`apps.${key}.zoom_cursor_hold_sec`])}`;
  return out;
}

/* Navigation: one panel — Orbit behavior + Pan/Zoom behavior. The ACTIVE
   mode (Off/Orbit/Fly/Walk) is the header slider; fly/walk speeds are gains
   and live under Tuning. */
function paneNav(key) {
  return `
  ${sect('Orbit', 'Default = follow the General scheme')}
  ${orbitRows(key)}
  ${sect('Pan / Zoom', 'while the toggle is held')}
  ${panZoomRows(key)}`;
}

function orbitRows(key) {
  const p = PROFILES[key];
  const b = `apps.${key}.bindings`;
  const adv = `apps.${key}.advanced`;
  const pivotOpts = [['default', 'Default (General)'],
    ...p.pivots.map(m => [m, PIVOT_LABELS[m]])];
  const styleHint = key === 'godot'
    ? 'Turntable only — Godot’s editor camera cannot hold a rolled basis.'
    : 'Free permits roll; Turntable keeps the horizon fixed.';
  let html = `
  ${row('Orbit style', ctrlSelect(`${b}.scheme.orbit_style`,
        p.styles.map(v => [v, optLabel(v)])), styleHint, '',
        [`${b}.scheme.orbit_style`])}
  ${row('Orbit pivot', ctrlSelect(`${b}.scheme.orbit_pivot`, pivotOpts,
        { warn: key === 'onshape' ? 'onshape-cursor' : '' }),
        'What the view rotates around; unsupported pivots are hidden.',
        PIVOT_TIPS[key] || PIVOT_TIPS.default, [`${b}.scheme.orbit_pivot`])}
  ${row('Twist action', ctrlSelect(`${adv}.twist_action`, p.twist.map(v => [v, optLabel(v)])),
        'What unshifted twist does in Orbit mode.', TIPS.twist, [`${adv}.twist_action`])}`;
  if (p.rich && !p.noHorizon) {
    html += row('Lock horizon', ctrlSwitch(`${adv}.lock_horizon`),
      'Stay level even while orbit style is Free.', '', [`${adv}.lock_horizon`]);
  }
  if (!p.noHorizon) {
    const v = S.cfg.apps[key].level_horizon_on_entry;
    const effective = typeof v === 'boolean' ? v : S.cfg.general.level_horizon_on_entry;
    html += row('Level on entry', ctrlSwitch(`apps.${key}.level_horizon_on_entry`, { checked: effective }),
      'Removes roll once when entering Turntable / Walk.', TIPS.levelHorizon,
      [`apps.${key}.level_horizon_on_entry`]);
  }
  html += row('Selection override', ctrlSwitch(`apps.${key}.selection_overrides_pivot`),
    'A selection’s centre beats the pivot.', TIPS.selOverride,
    [`apps.${key}.selection_overrides_pivot`]);
  if (p.userscript) {
    html += `<div class="row"><span class="rl">Under-cursor orbit${tipIcon(TIPS.userscript)}</span>
      <span class="rc"><button class="btn btn-sm" data-onshape-userscript>Copy userscript…</button></span>
      <span class="rs"></span>
      <span class="hint">Needed only for the Under Cursor pivot.</span></div>`;
  }
  return html;
}

function panZoomRows(key) {
  const p = PROFILES[key];
  const b = `apps.${key}.bindings`;
  const adv = `apps.${key}.advanced`;
  let html = row('Zoom mode', ctrlSelect(`${b}.scheme.zoom_mode`,
    [['default', 'Default (General)'], ['to_center', 'To Center'],
     ['to_object', 'To Object'], ['to_cursor', 'To Cursor (mouse)']]),
    'To Cursor follows the surface under the mouse.',
    'On an empty-space miss, To Cursor keeps the cursor position stable by synthesizing a point at a sensible scene depth.',
    [`${b}.scheme.zoom_mode`]);
  if (p.zb.length) {
    html += row('Pan-mode zoom', ctrlSelect(`${adv}.zoom_style`, p.zb.map(v => [v, optLabel(v)])),
      'Zoom changes the lens; Dolly moves the camera.', '', [`${adv}.zoom_style`]);
  }
  if (p.rich) {
    html += row('Pan scales with distance', ctrlSwitch(`${adv}.pan_scales_with_distance`),
      'Pan farther when zoomed out — constant on-screen travel.', '',
      [`${adv}.pan_scales_with_distance`]);
  }
  if (p.dynamicClip) {
    html += row('Override dynamic clip', ctrlSwitch(`${adv}.override_dynamic_clip`),
      'Fixes Unity’s auto near/far planes while navigating.', TIPS.dynamicClip,
      [`${adv}.override_dynamic_clip`]);
  }
  if (p.pivotExtent) {
    html += row('Pivot extent limit ×', ctrlText(`${adv}.pivot_extent_mult`),
      'Caps raycast pivots at scene bounds × this factor.',
      'Stops near-horizon hits from flinging the view. Typical 4–16; default 8.',
      [`${adv}.pivot_extent_mult`]);
  }
  if (p.cameraLock) {
    html += row('Lock camera to view', ctrlSwitch(`${adv}.lock_camera_to_view`),
      'In camera view, drive the scene camera itself.', '', [`${adv}.lock_camera_to_view`]);
  }
  return html;
}

function routesHTML(key) {
  const p = PROFILES[key];
  let rows;
  if (p.rich) {
    const adv = `apps.${key}.advanced`;
    const routeTab = S.routeTab[key] || (APP_ENABLED[key]
      ? getPath(`${adv}.nav_mode`) : 'orbit');
    let routes = ROUTES_BY_MODE[routeTab];
    if (p.noRoll && routeTab === 'camera') routes = routes.filter(([, k]) => k !== 'roll');
    if (p.noRoll && routeTab === 'fly') routes = routes.filter(([, k]) => k !== 'bank');
    rows = routes.map(([label, k]) =>
      routeRow(label, `${adv}.axis_source.${routeTab}.${k}`, `${adv}.invert.${routeTab}.${k}`)
    ).join('');
  } else {
    const b = `apps.${key}.bindings`;
    rows = [
      routeRow('Orbit X', `${b}.orbit.axis_source.0`, `${b}.invert.orbit.0`),
      routeRow('Orbit Y', `${b}.orbit.axis_source.1`, `${b}.invert.orbit.1`),
      routeRow('Orbit Z', `${b}.orbit.axis_source.2`, `${b}.invert.orbit.2`),
      routeRow('Pan X',   `${b}.pan.x_src`,           `${b}.invert.pan.0`),
      routeRow('Pan Y',   `${b}.pan.y_src`,           `${b}.invert.pan.1`),
      routeRow('Zoom',    `${b}.zoom.src`,            `${b}.invert.zoom`),
    ].join('');
  }
  return `<div class="routes-2col pane">${rows}</div>`;
}

function paneRouting(key) {
  const p = PROFILES[key];
  let tabs = '';
  if (p.rich) {
    const routeTab = S.routeTab[key] || (APP_ENABLED[key]
      ? getPath(`apps.${key}.advanced.nav_mode`) : 'orbit');
    tabs = `<div class="subtabs" style="margin:0 0 8px">${seg(
      [['orbit', 'Orbit'], ['camera', 'Camera'], ['fly', 'Fly'], ['walk', 'Walk']],
      routeTab, 'routetab')}</div>`;
  }
  return `
  ${sect('Action axes & directions', p.rich ? 'independent per mode' : 'per this app')}
  ${tabs}
  <div id="routes-host">${routesHTML(key)}</div>
  <p class="note" style="margin-top:9px">${esc(TIPS.sources)}
  ${p.rich ? ' Camera shares Orbit’s pan/zoom mappings.' : ''}
  Rotation actions fill the left column, translation the right. Hover a source to highlight
  its axis on the trackball.</p>`;
}

/* -------------------------------------------------------------- general */
const GENERAL_TABS = [
  ['device', 'Device & Orientation'], ['scheme', '3D Defaults'], ['pointer', 'Pointer & Buttons'],
];

function genPaneHTML(anim) {
  const inner = { device: paneGenDevice, scheme: paneGenScheme, pointer: paneGenPointer }[S.genTab]();
  return `<div class="${anim ? 'pane ' : ''}" data-pane="general-${S.genTab}">${inner}</div>`;
}

function viewGeneral() {
  return `
  <div class="subtabs">${seg(GENERAL_TABS, S.genTab, 'subtab')}</div>
  <div id="gen-pane-host">${genPaneHTML(false)}</div>`;
}

function paneGenDevice() {
  const src = S.cfg.general.axis_orientation.source;
  const orientRows = [0, 1, 2].map(t => `
    <div class="orient-row">
      <span class="route-name">Logical ${AXIS[t]}</span>
      <span class="route-mid">uses physical</span>
      <span class="axis-seg">${AXIS.map((n, i) =>
        `<button data-orient="${t}" data-ax="${i}" data-ax-hl="${i}"
                 class="${i === src[t] ? 'on' : ''}">${n}</button>`).join('')}</span>
      ${invPill(`general.axis_orientation.invert.${t}`)}
    </div>`).join('');

  return `
  ${sect('Device')}
  ${row('Name', ctrlText('device.name', { cast: 'str', wide: true }),
        'Used to find the trackball when no address is set. Applies on reconnect.', '',
        ['device.name'])}
  ${row('Address (optional)', ctrlText('device.address', { cast: 'str', wide: true }),
        'AA:BB:… pins one device; blank scans by name. Applies on reconnect.', '',
        ['device.address'])}
  ${row('Bridge rate (Hz)', ctrlText('bridge.rate_hz', { cast: 'int' }),
        'Global default viewport rate for every 3D app; override per app under 3D Apps → Tuning.', '',
        ['bridge.rate_hz'])}
  ${sect('Physical orientation', 'applied once, before pointer mode and every app')}
  ${orientRows}
  <div style="padding:7px 0 0; display:flex; align-items:center; gap:12px">
    <button class="btn btn-sm" id="btn-reset-orient">Reset orientation</button>
    <span class="note" style="margin:0">Picking a used source swaps the two axes, so the mapping
    always stays a valid permutation — hover a pick to preview it on the trackball.</span>
  </div>`;
}

function fbEditorHTML() {
  const chain = S.cfg.general.orbit_pivot_fallbacks;
  const fbItems = chain.length ? chain.map((m, i) => `
    <div class="fb-item ${i === S.fbSel ? 'sel' : ''} ${i === S.fbFlash ? 'just-moved' : ''}"
         data-fbsel="${i}">
      <span class="fb-n">${i + 1}</span>${esc(PIVOT_LABELS[m])}
    </div>`).join('')
    : '<div class="fb-empty">empty — a failed primary pivot produces no orbit</div>';
  const addable = ORBIT_PIVOT_METHODS.filter(m => !chain.includes(m));
  return `
  <div class="fb-editor">
    <div>
      <div class="fb-list">${fbItems}</div>
      <div class="fb-add">
        <select class="sel" id="fb-add-sel" ${addable.length ? '' : 'disabled'}>
          ${addable.map(m => `<option value="${m}">${esc(PIVOT_LABELS[m])}</option>`).join('')}
        </select>
        <button class="btn btn-sm" data-fbadd ${addable.length ? '' : 'disabled'}>Add</button>
      </div>
    </div>
    <div class="fb-controls">
      <button class="btn btn-sm" data-fbmove="-1">Up</button>
      <button class="btn btn-sm" data-fbmove="1">Down</button>
      <button class="btn btn-sm" data-fbremove>Remove</button>
    </div>
    <p class="note" style="flex:1; margin:0">${esc(TIPS.fallback)}</p>
  </div>`;
}

function paneGenScheme() {
  const g = 'general';
  return `
  ${sect('3D control scheme', 'defaults — every app can override per field')}
  ${row('Orbit pivot', ctrlSelect(`${g}.scheme.orbit_pivot`,
        ORBIT_PIVOT_METHODS.map(m => [m, PIVOT_LABELS[m]])),
        'What the view rotates around when an app is set to Default.', PIVOT_TIPS.default,
        [`${g}.scheme.orbit_pivot`])}
  ${row('Orbit style', ctrlSelect(`${g}.scheme.orbit_style`,
        [['free', 'Free'], ['turntable', 'Turntable']]),
        'Free permits roll; Turntable keeps the horizon fixed.', '',
        [`${g}.scheme.orbit_style`])}
  ${row('Zoom mode', ctrlSelect(`${g}.scheme.zoom_mode`,
        [['to_center', 'To Center'], ['to_object', 'To Object'], ['to_cursor', 'To Cursor (mouse)']]),
        'Default zoom target for apps whose Zoom mode follows General.', '',
        [`${g}.scheme.zoom_mode`])}
  ${row('Level on entry', ctrlSwitch(`${g}.level_horizon_on_entry`),
        'Entering Turntable / Walk removes existing roll once; per-app switches override.',
        TIPS.levelHorizonGeneral, [`${g}.level_horizon_on_entry`])}
  ${sect('Failure fallback order')}
  <div id="fb-host">${fbEditorHTML()}</div>`;
}

function paneGenPointer() {
  const g = 'general';
  return `
  ${sect('Pointer / scroll', 'cursor mode')}
  ${row('Cursor sensitivity', ctrlText(`${g}.cursor.gain`),
        'Ball rotation → pixels of cursor travel.', '', [`${g}.cursor.gain`])}
  ${row('Scroll gain', ctrlText(`${g}.scroll.gain`), 'Twist → wheel ticks.', '',
        [`${g}.scroll.gain`])}
  ${row('Scroll deadzone', ctrlText(`${g}.scroll.deadzone`),
        'Twist below this is ignored, so cursor moves don’t scroll.', '',
        [`${g}.scroll.deadzone`])}
  ${row('Scroll dominance', ctrlText(`${g}.scroll.dominance`),
        'How strongly twist must beat ball-plane motion to count as scroll.', '',
        [`${g}.scroll.dominance`])}
  ${sect('Mode & buttons')}
  ${row('Default mode', ctrlSelect(`${g}.default_mode`,
        [['cube', '3D navigation'], ['cursor', 'Cursor (pointer)']]),
        'The mode the daemon starts in; the rail toggle switches it live.', '',
        [`${g}.default_mode`])}
  ${row('Left button', ctrlSelect(`${g}.buttons.left`, [['left', 'Left'], ['right', 'Right'], ['middle', 'Middle'], ['none', 'None']]),
        'Reserved — the device handles buttons in HID mode.', '', [`${g}.buttons.left`])}
  ${row('Right button', ctrlSelect(`${g}.buttons.right`, [['left', 'Left'], ['right', 'Right'], ['middle', 'Middle'], ['none', 'None']]),
        '', '', [`${g}.buttons.right`])}
  ${row('Middle button', ctrlSelect(`${g}.buttons.middle`, [['left', 'Left'], ['right', 'Right'], ['middle', 'Middle'], ['none', 'None']]),
        '', '', [`${g}.buttons.middle`])}`;
}

/* ====================================================== render machinery */
const VIEWS = { overview: viewOverview, apps: viewApps, general: viewGeneral };

/* full page render — rail navigation only */
function render() {
  $('#view').innerHTML = VIEWS[S.page]();
  $$('#rnav button').forEach(b => b.classList.toggle('active', b.dataset.page === S.page));
  positionRailTick();
  initSegThumbs($('#view'));
  S.fbFlash = -1;
}

/* partial renders — everything below a persistent seg/tab swaps in place */
function renderAppPane(anim = true) {
  const host = $('#app-pane-host');
  if (!host) return;
  host.innerHTML = appPaneHTML(S.app, S.appTab[S.app] || 'setup', anim);
  initSegThumbs(host);
}

function renderAppDetail() {
  const el = $('#app-detail');
  if (!el) return;
  el.innerHTML = appDetailHTML(S.app);
  initSegThumbs(el);
}

function refreshAppList() {
  const list = $('.app-list');
  if (list) list.innerHTML = appListHTML();
}

function refreshAppListItem(key) {
  const btn = $(`.app-item[data-appsel="${key}"]`);
  if (btn) btn.innerHTML = appItemInner(APPS_BY_KEY[key]);
}

function refreshAppsPage() {
  if (S.page !== 'apps') return;
  refreshAppList();
  renderAppDetail();
}

function renderGenPane(anim = true) {
  const host = $('#gen-pane-host');
  if (!host) return;
  host.innerHTML = genPaneHTML(anim);
  initSegThumbs(host);
  S.fbFlash = -1;
}

function renderFbEditor() {
  const host = $('#fb-host');
  if (!host) return;
  host.innerHTML = fbEditorHTML();
  S.fbFlash = -1;
}

function refreshCurrentPane() {
  if (S.page === 'apps') renderAppPane(false);
  else if (S.page === 'general') renderGenPane(false);
}

function positionRailTick() {
  const active = $('#rnav button.active');
  const tick = $('#rnav-tick');
  if (!active || !tick) return;
  tick.style.transform =
    `translateY(${active.offsetTop + active.offsetHeight / 2 - 1}px) rotate(var(--tilt))`;
}

function setMode(mode) {
  S.mode = mode;
  document.body.dataset.mode = mode;
  $$('#mode-toggle button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('#mode-thumb').style.transform = mode === 'cube' ? 'translateX(76px)' : 'translateX(0)';
  const fact = $('#fact-mode');
  if (fact) fact.firstChild.textContent = (mode === 'cube' ? '3D navigation' : 'Cursor');
}

/* update one row's divergence-reset arrow after its control changed */
function refreshRowReset(el) {
  const rowEl = el.closest('.row, .route-row');
  const btn = rowEl && rowEl.querySelector('.mini-reset');
  if (btn) btn.classList.toggle('show', divergedAny(btn.dataset.resetPaths.split('|')));
}

/* ------------------------------------------------------------ clipboard */
function copyText(text, okMsg = 'Copied to clipboard') {
  const done = ok => toast(ok ? okMsg : 'Copy failed — select and copy manually');
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done(true), () => fallback());
  } else fallback();
  function fallback() {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { /* no-op */ }
    ta.remove();
    done(ok);
  }
}

/* ---------------------------------------------------------------- toast */
function toast(msg, ms = 2400) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  $('#toast-root').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, ms);
}

/* ---------------------------------------------------------------- modal */
function openModal({ title, body, buttons, copyables = [], dontAgain = null }) {
  const root = $('#modal-root');
  const btns = buttons || [{ label: 'OK', kind: 'btn-primary' }];
  root.innerHTML = `
  <div class="modal-backdrop">
    <div class="modal" role="dialog" aria-label="${esc(title)}">
      <div class="modal-head">${esc(title)}</div>
      <div class="modal-body">${esc(body)}</div>
      ${copyables.length ? `<div class="modal-foot" style="padding-bottom:2px">
        ${copyables.map((c, i) =>
          `<button class="btn btn-sm" data-modal-copy="${i}">${esc(c[0])}</button>`).join('')}
        <span class="modal-copy-status" id="modal-copy-status"></span>
      </div>` : ''}
      ${dontAgain ? `<label class="switch dont-again">
        <input type="checkbox" id="modal-dont-again"><span class="track"></span>
        <span class="sw-label">Do not show again</span></label>` : ''}
      <div class="modal-foot">
        <span class="spacer"></span>
        ${btns.map((b, i) =>
          `<button class="btn ${b.kind || ''}" data-modal-btn="${i}">${esc(b.label)}</button>`).join('')}
      </div>
    </div>
  </div>`;

  const close = () => { root.innerHTML = ''; };
  $('.modal-backdrop', root).addEventListener('click', e => {
    if (e.target === e.currentTarget) finish(null);
  });
  function finish(btn) {
    if (dontAgain && $('#modal-dont-again')?.checked) setPath(dontAgain, true);
    close();
    if (btn && btn.onClick) btn.onClick();
  }
  $$('[data-modal-btn]', root).forEach(el =>
    el.addEventListener('click', () => finish(btns[+el.dataset.modalBtn])));
  $$('[data-modal-copy]', root).forEach(el =>
    el.addEventListener('click', () => {
      const [label, payload] = copyables[+el.dataset.modalCopy];
      copyText(payload, 'Copied: ' + label);
      const st = $('#modal-copy-status');
      if (st) st.textContent = 'Copied: ' + label;
    }));
}

/* --------------------------------------------------- mock setup dialogs */
const USERSCRIPT_STEPS =
  '1. Install Violentmonkey or Tampermonkey in the browser you use for Onshape.\n' +
  '2. Create a new userscript and paste the clipboard contents.\n' +
  '3. Reload the Onshape tab.\n\n' +
  'The script POSTs the exact #canvas pointer position to the local bridge at ' +
  '/trackball/pointer — DOM only, loopback only, no screen capture.';

const USERSCRIPT_PLACEHOLDER =
  '// ==UserScript==\n// Astrolabe pointer bridge (demo placeholder)\n// ==/UserScript==';

function onshapeUserscriptDialog({ warn = false } = {}) {
  openModal({
    title: warn ? 'Onshape — under-cursor orbit' : 'Onshape — copy userscript',
    body: (warn
      ? 'Orbit pivot "Under Cursor (mouse)" needs the Astrolabe userscript in your Onshape ' +
        'browser. Without a hit, the configured fallback chain is used.\n\n'
      : 'The userscript is on your clipboard. Install steps:\n\n') + USERSCRIPT_STEPS,
    copyables: [['Copy userscript', USERSCRIPT_PLACEHOLDER]],
    dontAgain: warn ? 'onshape.cursor_userscript_warn_dismissed' : null,
  });
}

function runSetup(key) {
  const dialogs = {
    blender: () => openModal({
      title: 'Blender — auto-start?',
      body: 'Install the Trackball add-on into Blender\'s add-ons folder (every detected ' +
            'version).\n\nAuto-enable it on every Blender launch? This also writes a small ' +
            'startup script (scripts/startup/trackball_nav_startup.py) — the analogue of ' +
            'Fusion\'s "Run on Startup".',
      buttons: [
        { label: 'Yes — install + auto-enable', kind: 'btn-primary', onClick: () =>
          openModal({ title: 'Integration', body: 'Blender add-on v0.1.12 installed to every detected version and the auto-enable startup shim was written.\n\nNew add-on code takes effect on Blender\'s next launch.' }) },
        { label: 'No — install only', onClick: () =>
          openModal({ title: 'Integration', body: 'Blender add-on v0.1.12 installed.\n\nEnable Trackball Nav once in Preferences → Add-ons.' }) },
        { label: 'Cancel', kind: 'btn-ghost', onClick: () => toast('Blender setup cancelled') },
      ],
    }),
    unreal: () => openModal({
      title: 'Integration',
      body: 'TrackballNav updated to v0.2.4 in C:\\Program Files\\Epic Games\\UE_5.9\\Engine\\' +
            'Plugins\\TrackballNav.\n\nNew plugin code takes effect on the editor\'s next launch.',
      copyables: [['Copy project-Plugins path', '<YourProject>\\Plugins\\TrackballNav']],
      buttons: [{ label: 'OK', kind: 'btn-primary', onClick: () => {
        const a = APPS_BY_KEY.unreal;
        a.status = { chip: 'warn', text: 'installed · v0.2.4 — restart editor', short: 'restart' };
        a.action = 'Reinstall';
        refreshAppsPage();
      } }],
    }),
    unity: () => openModal({
      title: 'Integration',
      body: 'Package copied into 1 project:\n' +
            '  D:\\Projects\\Sandbox\\Packages\\com.astrolabe.trackball-nav\n\n' +
            'Let Unity reimport (or restart the editor); the row shows connected once the ' +
            'Scene view handshakes.',
      copyables: [['Copy package path', '<YourProject>\\Packages\\com.astrolabe.trackball-nav']],
      buttons: [{ label: 'OK', kind: 'btn-primary', onClick: () => {
        const a = APPS_BY_KEY.unity;
        a.status = { chip: 'idle', text: 'installed · v0.1.6', short: 'installed' };
        a.action = 'Reinstall';
        APP_ENABLED.unity = true;
        refreshAppsPage();
      } }],
    }),
    godot: () => openModal({
      title: 'Integration',
      body: 'Godot was not found on this machine — install it first.\n\nA staged copy of the ' +
            'add-on is available under %APPDATA%\\TrackballDaemon\\godot for a manual install ' +
            'into <YourProject>\\addons\\trackball_nav.',
      copyables: [['Copy add-on path', '<YourProject>\\addons\\trackball_nav']],
    }),
    onshape: () => openModal({
      title: 'Onshape — Set up',
      body: 'Bridge certificate generated at %APPDATA%\\TrackballDaemon\\onshape_cert.pem ' +
            '(no trust store was touched).\n\n1. Trust the cert (no admin): run the certutil ' +
            'command below and click Yes.\n2. Enable SpaceMouse / 3Dconnexion in Onshape → ' +
            'Account → Preferences.\n3. Optional: install the pointer userscript if you want ' +
            'Under Cursor orbit.\n\n' + USERSCRIPT_STEPS,
      copyables: [['Copy certutil command', CERTUTIL], ['Copy userscript', USERSCRIPT_PLACEHOLDER]],
      buttons: [{ label: 'OK', kind: 'btn-primary', onClick: () => {
        const a = APPS_BY_KEY.onshape;
        a.status = { chip: 'idle', text: 'bridge ready — awaiting browser', short: 'ready' };
        a.action = null;
        APP_ENABLED.onshape = true;
        refreshAppsPage();
      } }],
    }),
    autocad: () => openModal({
      title: 'Integration',
      body: 'TrackballNavAcad.dll staged to %APPDATA%\\TrackballDaemon\\acad_plugin. The ' +
            'folder was added to AutoCAD\'s TRUSTEDPATHS; the plugin NETLOADs automatically ' +
            'when the daemon attaches to a running AutoCAD.\n\nNote: detected AutoCAD 2024 is ' +
            'outside the supported .NET 8 family (2025–2027) — the plugin may fail to load ' +
            'there. Type TBNAV in AutoCAD to check.',
    }),
    fusion360: () => openModal({
      title: 'Integration',
      body: 'TrackballNav v0.1.16 copied to %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\' +
            'AddIns\\TrackballNav.\n\nIn Fusion: Utilities → Add-Ins (Shift+S) → run ' +
            'TrackballNav and tick Run on Startup (one-time click — Fusion won\'t let an ' +
            'installer set it).',
    }),
  };
  const generic = () => openModal({
    title: 'Integration',
    body: `${APPS_BY_KEY[key].name} add-on reinstalled.\n\nNew add-on code takes effect on ` +
          'the app\'s next launch (or stop/run the add-on).',
  });
  (dialogs[key] || generic)();
}

/* --------------------------------------------------------------- events */
document.addEventListener('click', e => {
  const t = e.target;

  const nav = t.closest('#rnav button[data-page]');
  if (nav) {
    S.page = nav.dataset.page;
    history.replaceState(null, '', '#' + S.page);
    render();
    return;
  }

  const mode = t.closest('#mode-toggle button');
  if (mode) { setMode(mode.dataset.mode); return; }

  if (t.closest('#btn-hide')) {
    toast('Window hidden to tray — the daemon keeps running (demo)');
    return;
  }

  if (t.closest('#btn-quit')) {
    openModal({
      title: 'Quit Astrolabe?',
      body: 'The daemon stops and the settings window closes. The trackball falls back to ' +
            'its onboard Bluetooth HID mouse until the daemon runs again.',
      buttons: [
        { label: 'Quit', kind: 'btn-danger', onClick: () => toast('Demo — the daemon keeps running') },
        { label: 'Cancel', kind: 'btn-ghost' }],
    });
    return;
  }

  if (t.closest('#btn-recenter')) { toast('Recenter sent to the focused viewport (demo)'); return; }

  const goto = t.closest('[data-goto-app]');
  if (goto) {
    S.page = 'apps';
    S.app = goto.dataset.gotoApp;
    history.replaceState(null, '', '#apps:' + S.app);
    render();
    return;
  }

  /* per-app active-control slider (Off / Orbit / Fly / Walk) */
  const ms = t.closest('button[data-mslide]');
  if (ms) {
    const slider = ms.closest('[data-mslider]');
    const key = slider.dataset.mslider;
    const val = ms.dataset.mslide;
    const name = APPS_BY_KEY[key].name;
    if (val === 'off') {
      if (APP_ENABLED[key]) toast(`${name} disabled — motion is no longer routed to it`);
      APP_ENABLED[key] = false;
    } else {
      if (!APP_ENABLED[key]) toast(`${name} enabled — ${val} while it has focus`);
      APP_ENABLED[key] = true;
      if (PROFILES[key].rich) setPath(`apps.${key}.advanced.nav_mode`, val);
    }
    slider.className = slider.className.replace(/ms-\w+/, 'ms-' + val);
    selectSegButton(slider, ms);
    refreshAppListItem(key);
    return;
  }

  /* sub-tabs (3D Apps detail + General) — slide the thumb, swap only the pane */
  const sub = t.closest('button[data-subtab]');
  if (sub) {
    const segEl = sub.closest('.seg');
    selectSegButton(segEl, sub);
    if (S.page === 'apps') {
      S.appTab[S.app] = sub.dataset.subtab;
      history.replaceState(null, '', `#apps:${S.app}.${sub.dataset.subtab}`);
      renderAppPane(true);
    } else {
      S.genTab = sub.dataset.subtab;
      history.replaceState(null, '', '#general:' + S.genTab);
      renderGenPane(true);
    }
    return;
  }

  /* routing mode tabs — slide the thumb, swap only the route rows */
  const rt = t.closest('button[data-routetab]');
  if (rt) {
    S.routeTab[S.app] = rt.dataset.routetab;
    selectSegButton(rt.closest('.seg'), rt);
    const host = $('#routes-host');
    if (host) host.innerHTML = routesHTML(S.app);
    return;
  }

  /* app list selection — only the detail column re-renders */
  const appSel = t.closest('[data-appsel]');
  if (appSel) {
    S.app = appSel.dataset.appsel;
    history.replaceState(null, '', '#apps:' + S.app);
    $$('.app-item').forEach(b => b.classList.toggle('on', b.dataset.appsel === S.app));
    renderAppDetail();
    return;
  }

  /* per-control divergence reset */
  const mr = t.closest('.mini-reset');
  if (mr) {
    mr.dataset.resetPaths.split('|').forEach(p => setPath(p, getDefault(p)));
    refreshCurrentPane();
    return;
  }

  /* axis source pick (per-app routing) */
  const ax = t.closest('[data-axpick]');
  if (ax) {
    setPath(ax.dataset.axpick, +ax.dataset.ax);
    $$('button', ax.parentElement).forEach(b => b.classList.toggle('on', b === ax));
    refreshRowReset(ax);
    return;
  }

  /* global orientation pick — swap to preserve the permutation */
  const or = t.closest('[data-orient]');
  if (or) {
    const target = +or.dataset.orient, wanted = +or.dataset.ax;
    const src = S.cfg.general.axis_orientation.source;
    const other = src.indexOf(wanted);
    [src[target], src[other]] = [src[other], src[target]];
    $$('[data-orient]').forEach(b =>
      b.classList.toggle('on', src[+b.dataset.orient] === +b.dataset.ax));
    return;
  }

  const inv = t.closest('[data-invpick]');
  if (inv) {
    const path = inv.dataset.invpick;
    const v = !getPath(path);
    setPath(path, v);
    inv.classList.toggle('on', v);
    refreshRowReset(inv);
    return;
  }

  const ci = t.closest('[data-copy-instructions]');
  if (ci) {
    const a = APPS_BY_KEY[ci.dataset.copyInstructions];
    copyText(`Install model\n${a.installModel}\n\nSecurity\n${a.security}\n\n` +
             `Automatic setup\n${a.instructions.auto}\n\n` +
             `Manual setup / restricted permissions\n${a.instructions.manual}\n\n` +
             `Health check\n${a.instructions.health}`);
    return;
  }

  const setup = t.closest('[data-setup]');
  if (setup) { runSetup(setup.dataset.setup); return; }

  if (t.closest('[data-onshape-userscript]')) {
    copyText(USERSCRIPT_PLACEHOLDER, 'Userscript copied');
    onshapeUserscriptDialog();
    return;
  }

  if (t.closest('#btn-reset-orient')) {
    S.cfg.general.axis_orientation = { source: [0, 1, 2], invert: [false, false, false] };
    renderGenPane(false);
    toast('Orientation reset to identity');
    return;
  }

  const ra = t.closest('[data-reset-app]');
  if (ra) {
    const key = ra.dataset.resetApp;
    const name = APPS_BY_KEY[key].name;
    openModal({
      title: 'Reset user overrides?',
      body: `Reset every ${name} user-facing navigation setting to its clean default?\n\n` +
            'Developer host alignment is stored separately and will not change.\n' +
            'Enable/install state and installed add-in version will be preserved.',
      buttons: [
        { label: 'Reset', kind: 'btn-danger', onClick: () => {
          S.cfg.apps[key] = defApp(key);
          delete S.routeTab[key];
          refreshAppsPage();
          toast(`${name} user overrides reset — now following General`);
        } },
        { label: 'Cancel', kind: 'btn-ghost' }],
    });
    return;
  }

  /* fallback-chain editor — all edits stay inside #fb-host */
  const fbi = t.closest('[data-fbsel]');
  if (fbi) { S.fbSel = +fbi.dataset.fbsel; renderFbEditor(); return; }

  const fbm = t.closest('[data-fbmove]');
  if (fbm) {
    const d = +fbm.dataset.fbmove, chain = S.cfg.general.orbit_pivot_fallbacks, i = S.fbSel;
    if (i >= 0 && i + d >= 0 && i + d < chain.length) {
      [chain[i], chain[i + d]] = [chain[i + d], chain[i]];
      S.fbSel = i + d;
      S.fbFlash = i + d;
      renderFbEditor();
    }
    return;
  }

  if (t.closest('[data-fbremove]')) {
    const chain = S.cfg.general.orbit_pivot_fallbacks;
    if (S.fbSel >= 0 && S.fbSel < chain.length) {
      chain.splice(S.fbSel, 1);
      S.fbSel = Math.min(S.fbSel, chain.length - 1);
      renderFbEditor();
    }
    return;
  }

  if (t.closest('[data-fbadd]')) {
    const sel = $('#fb-add-sel');
    const chain = S.cfg.general.orbit_pivot_fallbacks;
    if (sel && sel.value && !chain.includes(sel.value)) {
      chain.push(sel.value);
      S.fbSel = chain.length - 1;
      S.fbFlash = S.fbSel;
      renderFbEditor();
    }
    return;
  }
});

document.addEventListener('change', e => {
  const t = e.target;
  const path = t.dataset.path;
  if (!path) return;
  const cast = t.dataset.cast;

  if (cast === 'bool') {
    setPath(path, t.checked);
    if (path === 'general.start_at_login') {
      toast(`Start at login ${t.checked ? 'enabled — HKCU Run key written' : 'disabled'} (demo)`);
    }
  } else if (cast === 'rate') {
    const s = t.value.trim().toLowerCase();
    let v;
    if (s === '' || s === 'default' || s === '0') v = 0;
    else {
      v = parseInt(parseFloat(s), 10);
      if (Number.isNaN(v)) { t.value = fmtRate(getPath(path)); return; }
      v = Math.max(1, Math.min(240, v));
    }
    setPath(path, v);
    t.value = fmtRate(v);
  } else if (cast === 'num' || cast === 'int') {
    const v = cast === 'int' ? parseInt(t.value, 10) : parseFloat(t.value);
    if (Number.isNaN(v)) { t.value = fmt(getPath(path)); return; }
    setPath(path, v);
    t.value = fmt(v);
  } else {
    setPath(path, t.value);
    if (path === 'general.default_mode') setMode(t.value === 'cube' ? 'cube' : 'cursor');
    /* choosing Free orbit should feel like a trackball immediately — a one-time
       convenience mirroring the real UI; Twist action stays freely editable after */
    if (path.endsWith('.scheme.orbit_style') && t.value === 'free' && S.page === 'apps') {
      const p = PROFILES[S.app];
      const twistPath = `apps.${S.app}.advanced.twist_action`;
      if (p && p.twist.includes('roll') && getPath(twistPath) !== 'roll') {
        setPath(twistPath, 'roll');
        const twistSel = $(`[data-path="${twistPath}"]`);
        if (twistSel) { twistSel.value = 'roll'; refreshRowReset(twistSel); }
        toast('Twist action set to Roll for free orbit');
      }
    }
    if (t.dataset.warn === 'onshape-cursor' && t.value === 'cursor' &&
        !S.cfg.onshape.cursor_userscript_warn_dismissed) {
      onshapeUserscriptDialog({ warn: true });
    }
  }
  refreshRowReset(t);
});

function fmtRate(v) { return (!v || +v === 0) ? 'Default' : String(v); }

/* axis-control hover → highlight that axis on the rail trackball */
const railBall = () => $('#rail-ball');
document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-ax-hl]');
  if (!el) return;
  const svg = railBall();
  svg.classList.remove('hl-0', 'hl-1', 'hl-2');
  svg.classList.add('hl-' + el.dataset.axHl);
});
document.addEventListener('mouseout', e => {
  if (!e.target.closest('[data-ax-hl]')) return;
  railBall().classList.remove('hl-0', 'hl-1', 'hl-2');
});

/* ------------------------------------------------------------------ boot */
(function initFromHash() {
  // deep-linkable: "#apps", "#general:pointer", "#apps:unreal", "#apps:unreal.routing", …
  const [page, arg] = location.hash.replace(/^#/, '').split(':');
  if (PAGES[page]) S.page = page;
  if (page === 'apps' && arg) {
    const [app, tab] = arg.split('.');
    if (APPS_BY_KEY[app]) S.app = app;
    if (['setup', 'tuning', 'nav', 'routing'].includes(tab)) S.appTab[S.app] = tab;
  }
  if (page === 'general' && ['device', 'scheme', 'pointer'].includes(arg)) S.genTab = arg;
})();
setMode(S.cfg.general.default_mode === 'cube' ? 'cube' : 'cursor');
render();
window.addEventListener('resize', () => { positionRailTick(); initSegThumbs(); });
