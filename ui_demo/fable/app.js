/* ============================================================================
   Astrolabe control daemon — UI demo (front-end only, drives nothing)
   Every option/field/dropdown mirrors the real daemon's Tkinter settings
   window (trackball_daemon/ui.py) and config schema (config.py), reorganized
   into a compact fixed-size window: pages + sub-tabs instead of scrolling.
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

const RATE_PRESETS = ['Default', '15', '30', '45', '60', '90', '120'];

const ROUTES_BY_MODE = {
  orbit:  [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Twist', 'twist'],
           ['Pan X', 'pan_x'], ['Pan Y', 'pan_y'], ['Zoom', 'zoom']],
  camera: [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Roll', 'roll']],
  fly:    [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Bank', 'bank'],
           ['Fwd', 'forward'], ['Strafe', 'strafe'], ['Up/Dn', 'vertical']],
  walk:   [['Pitch', 'pitch'], ['Yaw', 'yaw'],
           ['Fwd', 'forward'], ['Strafe', 'strafe'], ['Up/Dn', 'vertical']],
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

function defBindings() {
  return {
    orbit: { axis_source: [0, 1, 2], sensitivity: 1.0 },
    pan:   { x_src: 1, y_src: 0, gain: 1.0 },
    zoom:  { src: 2, gain: 1.0, dominance: 1.7 },
    toggle: 'shift',
    invert: { orbit: [false, false, false], pan: [false, false], zoom: false },
    scheme: { orbit_pivot: 'default', orbit_style: 'default', zoom_mode: 'default' },
  };
}

function defAdvanced(extra = {}) {
  return Object.assign({
    nav_mode: 'orbit',
    lock_horizon: false,
    twist_action: 'roll',
    pan_scales_with_distance: true,
    fly_speed: 1.0,
    walk_speed: 1.0,
    invert: defaultModeInvert(),
    axis_source: JSON.parse(JSON.stringify(DEFAULT_ACTION_AXIS_SOURCE)),
  }, extra);
}

function defApp(kind) {
  const a = {
    rate_hz: 0,
    screen_center_pivot_hold_sec: 0.5,
    selection_overrides_pivot: true,
    level_horizon_on_entry: null,        // null = follow the General default
    bindings: defBindings(),
  };
  if (kind === 'blender') {
    a.bindings.scheme.orbit_pivot = 'camera';
    a.advanced = defAdvanced({
      zoom_style: 'zoom', zoom_to_mouse: false, lock_camera_to_view: false,
    });
  } else if (kind === 'sketchup') {
    a.advanced = defAdvanced();
    delete a.advanced.twist_action;
    delete a.advanced.pan_scales_with_distance;
  } else if (kind === 'unreal') {
    a.advanced = defAdvanced();
  } else if (kind === 'unity') {
    a.advanced = defAdvanced({ override_dynamic_clip: true, pivot_extent_mult: 8.0 });
  } else if (kind === 'godot') {
    a.advanced = defAdvanced({ twist_action: 'zoom', lock_horizon: true });
    a.bindings.scheme.orbit_style = 'turntable';
  }
  return a;
}

function buildDefaultConfig() {
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
    },
    apps: {
      blender: defApp('blender'), freecad: defApp(), sketchup: defApp('sketchup'),
      unreal: defApp('unreal'), unity: defApp('unity'), godot: defApp('godot'),
      rhino: defApp(), fusion360: defApp(), solidworks: defApp(),
      onshape: defApp(), autocad: defApp(),
    },
    bridge: { port: 47900, rate_hz: 30 },
    onshape: { cursor_userscript_warn_dismissed: false },
  };
}

/* ------------------------------------------------- 3D-app registry (mock) */
const CERTUTIL = 'certutil -user -addstore Root "%APPDATA%\\TrackballDaemon\\onshape_cert.pem"';

const APPS = [
  {
    key: 'blender', name: 'Blender', rich: true,
    status: { chip: 'good', text: 'active • connected (v0.1.12)' },
    detected: 'detected v5.1.1: C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe',
    versions: 'Blender 4.2 through 5.1 (tested on 5.1.1)',
    installModel: 'User add-on copied into each detected Blender version; optional startup shim.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies Trackball Nav to every detected user scripts/addons folder and asks whether to add the auto-enable startup shim.',
      manual: 'Copy trackball_daemon\\plugins\\blender\\trackball_nav to %APPDATA%\\Blender Foundation\\Blender\\<version>\\scripts\\addons\\trackball_nav, then enable Trackball Nav in Preferences > Add-ons. No administrator access is required.',
      health: 'Restart Blender. The row should show connected while Blender is focused; details are in %APPDATA%\\TrackballDaemon\\blender_addin.log.',
    },
  },
  {
    key: 'freecad', name: 'FreeCAD', rich: false,
    status: { chip: 'idle', text: 'installed · v0.1.6' },
    detected: 'detected v1.1.1: C:\\Program Files\\FreeCAD 1.1\\bin\\FreeCAD.exe',
    versions: 'FreeCAD 1.0 through 1.1 (tested on 1.1.1)',
    installModel: 'User Mod add-on; files are copied only under the current Windows profile.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav into FreeCAD\'s version-aware user Mod folder.',
      manual: 'Copy trackball_daemon\\plugins\\freecad\\TrackballNav to %APPDATA%\\FreeCAD\\v<major>-<minor>\\Mod\\TrackballNav (FreeCAD 1.x), then restart FreeCAD. Use %APPDATA%\\FreeCAD\\Mod for older layouts.',
      health: 'Open a 3D view and focus FreeCAD; the row should show connected. Check %APPDATA%\\TrackballDaemon\\freecad_addin.log if it does not.',
    },
  },
  {
    key: 'sketchup', name: 'SketchUp', rich: true,
    status: { chip: 'idle', text: 'installed · v0.2.3' },
    detected: 'detected v2026.2.243: C:\\Program Files\\SketchUp\\SketchUp 2026\\SketchUp.exe',
    versions: 'SketchUp Desktop 2025 through 2026 (tested on 2026.2.243)',
    installModel: 'Per-version Ruby extension in SketchUp\'s user Plugins folder.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies the loader and extension into every detected annual release.',
      manual: 'Copy trackball_daemon\\plugins\\sketchup\\trackball_nav_loader.rb and the trackball_nav folder to %APPDATA%\\SketchUp\\SketchUp <year>\\SketchUp\\Plugins, then restart SketchUp. SketchUp for Web is not supported.',
      health: 'Extension Manager should list Trackball Nav; focus a model and look for connected in this row or inspect %APPDATA%\\TrackballDaemon\\sketchup_addin.log.',
    },
  },
  {
    key: 'unreal', name: 'Unreal Engine', rich: true,
    status: { chip: 'warn', text: 'installed · v0.2.3 — update available' },
    detected: 'detected v5.9.0: C:\\Program Files\\Epic Games\\UE_5.9\\Engine\\Binaries\\Win64\\UnrealEditor.exe',
    versions: 'Unreal Engine 5.8 (tested on 5.8.0)',
    alert: { level: 'warn', text: 'CAUTION: detected host version 5.9.0 has not been verified.' },
    installModel: 'Unreal Editor plugin, installed per engine or per project.',
    setupRequired: true, action: 'Update → v0.2.4',
    instructions: {
      auto: 'Set up copies TrackballNav to each detected Engine/Plugins folder. Engine-level writes may require administrator permission.',
      manual: 'Without administrator access, copy trackball_daemon\\plugins\\unreal\\TrackballNav to <YourProject>\\Plugins\\TrackballNav. Enable Trackball Nav and Python Editor Script Plugin in Edit > Plugins, then restart the editor.',
      health: 'Focus a perspective level viewport; the row should show connected. Check %APPDATA%\\TrackballDaemon\\unreal_addin.log and the Output Log on failure.',
    },
  },
  {
    key: 'unity', name: 'Unity', rich: true,
    status: { chip: 'idle', text: 'detected — not set up' },
    detected: 'detected v6000.5.3f1: C:\\Program Files\\Unity\\Hub\\Editor\\6000.5.3f1\\Editor\\Unity.exe',
    versions: 'Unity 6 / 6000.x (implemented against 6000.5.3f1)',
    installModel: 'UPM Editor package copied into each detected Unity project.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up finds running/recent projects and copies the package into each project\'s Packages folder; Unity recompiles it automatically.',
      manual: 'Copy trackball_daemon\\plugins\\unity\\com.astrolabe.trackball-nav to <YourProject>\\Packages\\com.astrolabe.trackball-nav. If Set up found no project, the same package is staged under %APPDATA%\\TrackballDaemon\\unity.',
      health: 'Open and focus a Scene view; the row should show connected. Check the Unity Console and %APPDATA%\\TrackballDaemon\\unity_addin.log.',
    },
  },
  {
    key: 'godot', name: 'Godot', rich: true,
    status: { chip: 'off', text: 'not detected' },
    detected: 'not detected',
    versions: 'Godot 4.4 through 4.7',
    installModel: 'Godot EditorPlugin copied and enabled per project.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up finds running/recent projects, copies addons/trackball_nav, and enables res://addons/trackball_nav/plugin.cfg.',
      manual: 'Copy trackball_daemon\\plugins\\godot\\trackball_nav to <YourProject>\\addons\\trackball_nav, then enable Trackball Nav under Project > Project Settings > Plugins. A staged copy is also placed under %APPDATA%\\TrackballDaemon\\godot when no project is found.',
      health: 'Reload the project, focus a 3D editor viewport, and look for connected. Check %APPDATA%\\TrackballDaemon\\godot_addin.log on failure.',
    },
  },
  {
    key: 'rhino', name: 'Rhino', rich: false,
    status: { chip: 'idle', text: 'installed · v0.1.2' },
    detected: 'detected v8.19: C:\\Program Files\\Rhino 8\\System\\Rhino.exe',
    versions: 'Rhino 8',
    installModel: 'Rhino 8 user Python scripts plus a per-user startup command.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav into Rhino\'s user scripts folder and best-effort registers its startup command.',
      manual: 'Copy trackball_daemon\\plugins\\rhino\\TrackballNav to %APPDATA%\\McNeel\\Rhinoceros\\8.0\\scripts\\TrackballNav. In Rhino Options > General, add _-RunPythonScript "<path>\\start.py" to startup commands, then restart Rhino.',
      health: 'Focus a Rhino viewport and look for connected. Check %APPDATA%\\TrackballDaemon\\rhino_addin.log if startup failed.',
    },
  },
  {
    key: 'fusion360', name: 'Fusion 360', rich: false,
    status: { chip: 'idle', text: 'installed · v0.1.16' },
    detected: 'detected: %LOCALAPPDATA%\\Autodesk\\webdeploy\\production\\Fusion360.exe (rolling release)',
    versions: 'Current Fusion production release (rolling Autodesk release)',
    installModel: 'Fusion user add-in copied to Autodesk\'s per-user AddIns folder.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav. In Fusion, open Utilities > Add-Ins, run TrackballNav once, and enable Run on Startup.',
      manual: 'Copy trackball_daemon\\plugins\\fusion360\\TrackballNav to %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\TrackballNav, then run it from Utilities > Add-Ins. No administrator access is required.',
      health: 'Focus an open design and look for connected. Check %APPDATA%\\TrackballDaemon\\fusion_addin.log if the add-in does not handshake.',
    },
  },
  {
    key: 'solidworks', name: 'SolidWorks', rich: false,
    status: { chip: 'idle', text: 'ready · direct COM' },
    detected: 'detected v2025: C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe',
    versions: 'SOLIDWORKS 2025 (tested on 2025)',
    installModel: 'Direct COM automation; no SolidWorks add-in or host files are installed.',
    setupRequired: false, action: null,
    instructions: {
      auto: 'Enable performs a one-time prerequisite check for SOLIDWORKS and pywin32. After that, the Enabled checkbox is the only control needed.',
      manual: 'There is nothing to copy. If the prerequisite check fails, install pywin32 into the daemon\'s Python environment with: pip install pywin32.',
      health: 'Open a part or assembly and focus SOLIDWORKS; the row should show connected. Driver messages are recorded in %APPDATA%\\TrackballDaemon\\daemon.log.',
    },
  },
  {
    key: 'onshape', name: 'Onshape', rich: false,
    status: { chip: 'idle', text: 'not set up' },
    detected: 'Chrome 138 detected — bridge endpoint 127.51.68.120:8181 reserved',
    versions: 'Current Onshape web release (rolling release)',
    installModel: 'Browser bridge; no Onshape add-in is installed.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up creates the bridge\'s per-user local TLS certificate. Trust it once and enable SpaceMouse/3Dconnexion in Onshape preferences.',
      manual: 'No application files need copying. Generate/trust the certificate using the Set up dialog or certutil -user, then install the supplied userscript only if Under Cursor orbit is wanted. Administrator access is not required.',
      health: 'Open and focus an Onshape document; the row should show connected after the browser handshake. Check %APPDATA%\\TrackballDaemon\\daemon.log.',
    },
  },
  {
    key: 'autocad', name: 'AutoCAD', rich: false,
    status: { chip: 'bad', text: 'unsupported host' },
    detected: 'detected v2024: C:\\Program Files\\Autodesk\\AutoCAD 2024\\acad.exe',
    versions: 'AutoCAD 2025 through 2027 (.NET 8 family; tested on 2026)',
    alert: { level: 'bad', text: 'WARNING: detected version 2024 is unsupported.' },
    installModel: 'Per-user .NET plugin staged by the daemon and NETLOADed automatically.',
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
  route: 'overview',
  mode: 'cube',                 // tray-level toggle: 'cursor' | 'cube'
  editingApp: 'blender',        // Bindings page
  selApp: 'blender',            // 3D Apps page (master-detail selection)
  sub: { bindings: 'tuning', general: 'device' },
  fbSel: -1,
  fbFlash: -1,
  navTab: {},                   // appKey -> selected nav-mode panel
  routeTab: {},                 // appKey -> selected routing tab
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

/* ------------------------------------------------------------ components */
const tipIcon = t => t ? `<span class="tip" title="${esc(t)}">&#9432;</span>` : '';

function field(label, ctrl, tip = '') {
  return `<div class="f"><span class="f-l">${esc(label)}${tipIcon(tip)}</span><span>${ctrl}</span></div>`;
}

function ctrlText(path, { cast = 'num', wide = false } = {}) {
  const v = getPath(path);
  return `<input class="text${wide ? ' wide' : ''}" data-path="${path}" data-cast="${cast}"
          value="${esc(cast === 'num' || cast === 'int' ? fmt(v) : v)}" spellcheck="false">`;
}

function ctrlSelect(path, options, { warn = '' } = {}) {
  const v = String(getPath(path));
  const opts = options.map(o => {
    const [val, lab] = Array.isArray(o) ? o : [o, o];
    return `<option value="${esc(val)}"${String(val) === v ? ' selected' : ''}>${esc(lab)}</option>`;
  }).join('');
  return `<select class="sel" data-path="${path}" data-cast="str"${warn ? ` data-warn="${warn}"` : ''}>${opts}</select>`;
}

function ctrlRate(path) {
  const v = getPath(path);
  return `<input class="text" list="rate-presets" data-path="${path}" data-cast="rate"
          value="${esc((!v || +v === 0) ? 'Default' : String(v))}" spellcheck="false">`;
}

function swRow(label, path, { tip = '', checked = null } = {}) {
  const on = checked === null ? !!getPath(path) : checked;
  return `<div class="sw-row">
    <label class="switch">
      <input type="checkbox" data-path="${path}" data-cast="bool" ${on ? 'checked' : ''}>
      <span class="track"></span>
      <span class="sw-label">${esc(label)}</span>
    </label>${tipIcon(tip)}
  </div>`;
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
  </div>`;
}

function seg(options, current, dataAttr) {
  return `<span class="seg">` + options.map(([val, lab]) =>
    `<button data-${dataAttr}="${esc(val)}" class="${val === current ? 'on' : ''}">${esc(lab)}</button>`
  ).join('') + `</span>`;
}

function sect(title, note = '') {
  return `<h3 class="sect">${esc(title)}${note ? ` <span class="sect-note">— ${esc(note)}</span>` : ''}</h3>`;
}

/* -------------------------------------------------------------- diagram */
let uidCounter = 0;

function ballDiagram(width, labels = { x: 'PITCH', y: 'YAW', z: 'TWIST' }) {
  const uid = 'd' + (uidCounter++);
  const mk = ax => `
    <marker id="${uid}-m${ax}" viewBox="0 0 10 10" refX="7.5" refY="5"
            markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" class="mh-${ax}"/>
    </marker>`;
  return `
  <svg class="ball-diagram" width="${width}" viewBox="0 0 280 292" role="img"
       aria-label="Trackball rotation axes, top view">
    <defs>
      ${mk('x')}${mk('y')}${mk('z')}
      <radialGradient id="${uid}-g" cx="0.38" cy="0.32" r="0.85">
        <stop offset="0%" stop-color="rgba(255,255,255,0.09)"/>
        <stop offset="60%" stop-color="rgba(255,255,255,0.02)"/>
        <stop offset="100%" stop-color="rgba(0,0,0,0.12)"/>
      </radialGradient>
      <style>
        .mh-x{fill:var(--ax-x)} .mh-y{fill:var(--ax-y)} .mh-z{fill:var(--ax-z)}
      </style>
    </defs>

    <!-- housing rim (top view) -->
    <circle cx="140" cy="128" r="104" fill="none" stroke="var(--line2)"/>
    <!-- front marker -->
    <line x1="140" y1="232" x2="140" y2="243" stroke="var(--text3)" stroke-width="1.5"/>
    <text x="140" y="262" text-anchor="middle" class="ax-sub"
          style="letter-spacing:.14em">FRONT · TOWARD YOU</text>

    <!-- ball -->
    <circle cx="140" cy="128" r="72" fill="#11161d" stroke="var(--line3)"/>
    <circle cx="140" cy="128" r="72" fill="url(#${uid}-g)"/>

    <!-- X axis: pitch -->
    <g class="ax ax-x">
      <line class="ax-line" x1="70" y1="128" x2="140" y2="128"
            stroke-dasharray="3 4" opacity="0.35"/>
      <line class="ax-line" x1="140" y1="128" x2="222" y2="128" marker-end="url(#${uid}-mx)"/>
      <path class="ax-arc" d="M 196 152 A 7.5 24 0 0 0 196 104" marker-end="url(#${uid}-mx)"/>
      <text class="ax-label" x="234" y="132">X</text>
      <text class="ax-sub" x="234" y="146">${esc(labels.x)}</text>
    </g>

    <!-- Y axis: yaw -->
    <g class="ax ax-y">
      <line class="ax-line" x1="140" y1="128" x2="140" y2="198"
            stroke-dasharray="3 4" opacity="0.35"/>
      <line class="ax-line" x1="140" y1="128" x2="140" y2="46" marker-end="url(#${uid}-my)"/>
      <path class="ax-arc" d="M 116 78 A 24 7.5 0 0 1 164 78" marker-end="url(#${uid}-my)"/>
      <text class="ax-label" x="140" y="32" text-anchor="middle">Y</text>
      <text class="ax-sub" x="172" y="31">${esc(labels.y)}</text>
    </g>

    <!-- Z axis: out of the page (twist), arc centered on the ball center -->
    <g class="ax ax-z">
      <circle class="ax-ring" cx="140" cy="128" r="7"/>
      <circle class="ax-dot" cx="140" cy="128" r="2"/>
      <path class="ax-arc" d="M 159.9 104.3 A 31 31 0 1 0 159.9 151.8"
            marker-end="url(#${uid}-mz)"/>
      <text class="ax-label" x="140" y="186" text-anchor="middle">Z</text>
      <text class="ax-sub" x="140" y="199" text-anchor="middle">${esc(labels.z)}</text>
    </g>
  </svg>`;
}

/* ---------------------------------------------------------- pivot options */
function pivotOptionsFor(key) {
  const out = [['default', 'Default (General)']];
  if (['blender', 'sketchup', 'unreal', 'unity', 'godot', 'autocad', 'rhino'].includes(key)) {
    out.push(['camera', PIVOT_LABELS.camera]);
  }
  out.push(['screen_center', PIVOT_LABELS.screen_center],
           ['cursor', PIVOT_LABELS.cursor],
           ['selection', PIVOT_LABELS.selection]);
  if (key === 'blender') out.push(['cursor_3d', PIVOT_LABELS.cursor_3d]);
  out.push(['object', PIVOT_LABELS.object], ['origin', PIVOT_LABELS.origin]);
  return out;
}

const ZOOM_MODE_OPTIONS = [
  ['default', 'Default (General)'], ['to_center', 'to_center'],
  ['to_object', 'to_object'], ['to_cursor', 'to_cursor (under mouse)'],
];

/* shared tooltip texts (the real UI's hint/note strings) */
const TIPS = {
  rate: 'Per app; Default = the global bridge rate (General). Higher = smoother (try 60), lower it if the app lags. Applies live and follows whichever app is focused.',
  orbitSens1to1: '1.0 = true 1:1 — the on-screen object follows the full ball angle.',
  orbitSensBaseline: '1.0 = this app\'s baseline; set 0.5 for the cube\'s exact 1:1.',
  panGain: 'radians → world units.',
  zoomGain: 'radians → dolly.',
  dominance: 'How strongly twist must dominate the pan-plane before it counts as zoom.',
  toggle: 'Which modifier switches orbit → pan/zoom while held. "none" = always orbit.',
  levelHorizon: 'On: switching into a fixed-horizon mode (Turntable / Lock horizon / Walk) removes any existing roll once. Off: the current tilt is locked as-is. Until toggled here, follows the General default; Reset user overrides makes it follow General again.',
  levelHorizonGeneral: 'On: switching into a fixed-horizon mode (Turntable / Lock horizon / Walk) removes any existing roll instead of locking the tilted horizon. Per-app checkboxes override this default.',
  selOverride: 'When on and something is selected, orbit (and supported to_cursor zoom) uses the selection centre instead of the designated pivot. Camera remains turn-in-place.',
  screenHold: 'Seconds the view must be still before the Screen Center / Under Cursor raycast is recomputed. 0 = recompute at the start of every orbit; a pan/zoom always recomputes immediately.',
  fallback: 'The selected pivot is tried first. If it is unavailable, resolution restarts at item 1 of this chain; unsupported methods are skipped by that integration. An empty list means no fallback. Selection Override still wins when enabled, except Camera always turns in place.',
  sources: 'Sources refer to logical axes after the global physical orientation (General). Edits apply live.',
};

const PIVOT_TIPS = {
  blender: 'Screen Center raycasts the surface under the viewport centre; Under Cursor under the mouse (both held per gesture); 3D Cursor is Blender\'s 3D cursor; Model Center uses the scene bounds.',
  sketchup: 'No 3D-cursor target in SketchUp. Screen Center raycasts under the viewport centre; Under Cursor under the mouse (both held per gesture); Model Center uses model.bounds.',
  unreal: 'No 3D cursor in Unreal. Screen Center raycasts under the viewport center; Under Cursor under the mouse — the level viewport must be focused.',
  unity: 'Screen Center raycasts under the viewport center; Under Cursor under the mouse.',
  godot: 'Screen Center raycasts under the viewport center; Under Cursor under the mouse.',
  default: 'Camera turns in place; Screen Center raycasts the surface under the viewport centre; Under Cursor raycasts under the mouse (held per gesture); a miss continues through the General fallback chain.',
};

function levelHorizonSw(key) {
  const v = S.cfg.apps[key].level_horizon_on_entry;
  const effective = typeof v === 'boolean' ? v : S.cfg.general.level_horizon_on_entry;
  return swRow('Level horizon when entering Turntable/Walk',
    `apps.${key}.level_horizon_on_entry`, { tip: TIPS.levelHorizon, checked: effective });
}

/* ================================================================= views */
const PAGES = { overview: 1, apps: 1, bindings: 1, general: 1 };

/* ------------------------------------------------------------- overview */
function viewOverview() {
  return `
  <div class="overview">
    <div class="diagram-cell">${ballDiagram(238)}</div>
    <dl>
      <div class="fact-row"><dt>Device</dt>
        <dd>Trackball BLE <span class="sub mono">notify 7 ms · 2 links</span></dd></div>
      <div class="fact-row"><dt>Link</dt>
        <dd style="color:var(--good)">Connected <span class="sub">rotation stream subscribed — onboard HID mouse suppressed</span></dd></div>
      <div class="fact-row"><dt>Mode</dt>
        <dd id="fact-mode">${S.mode === 'cube' ? '3D navigation ' : 'Cursor '}<span class="sub">toggle above, or from the tray</span></dd></div>
      <div class="fact-row"><dt>Add-on handshakes</dt>
        <dd class="mono">blender v0.1.12</dd></div>
      <div class="fact-row"><dt>Active profile</dt>
        <dd class="mono">${esc(S.editingApp)} <span class="sub">set in Bindings</span></dd></div>
      <div class="fact-row"><dt>Daemon</dt>
        <dd class="mono">v0.1.59</dd></div>
      <div class="fact-row"><dt>Config / log</dt>
        <dd class="mono">%APPDATA%\\TrackballDaemon\\ <span class="sub">config.json · daemon.log</span></dd></div>
      <div class="fact-row"><dt>Nav broker</dt>
        <dd class="mono">127.0.0.1:47900 <span class="sub">socket add-ons</span></dd></div>
      <div class="fact-row"><dt>Onshape bridge</dt>
        <dd class="mono">127.51.68.120:8181 <span class="sub">NL-Proxy, TLS</span></dd></div>
    </dl>
    <p class="note overview-note">Top view, front of the device toward you. In the default
    alignment the ball's axes match the on-screen object: X pitches, Y yaws, Z twists.
    Remap or invert under <b>General → Device &amp; Orientation</b>. Closing this window
    hides it to the tray; the daemon exits only via tray → Quit.</p>
  </div>`;
}

/* -------------------------------------------------------------- 3D apps */
function viewApps() {
  const list = APPS.map(a => `
    <button class="app-item ${a.key === S.selApp ? 'on' : ''}" data-appsel="${a.key}">
      <span class="app-dot ${a.status.chip}"></span>${esc(a.name)}
    </button>`).join('');

  const a = APPS_BY_KEY[S.selApp];
  const chipClass = { good: 'chip-good', idle: 'chip-idle', warn: 'chip-warn',
                      bad: 'chip-bad', off: 'chip-off' }[a.status.chip];
  const actionBtn = a.action
    ? `<button class="btn ${/Set up|Update/.test(a.action) ? 'btn-primary' : ''}"
               data-setup="${a.key}">${esc(a.action)}</button>` : '';

  return `
  <div class="apps">
    <div class="app-list">${list}</div>
    <div class="app-detail pane" data-pane="${a.key}">
      <div class="app-detail-head">
        <span class="app-name">${esc(a.name)}</span>
        <span class="chip ${chipClass}">${esc(a.status.text)}</span>
      </div>
      <dl class="app-meta">
        <dt>Detected</dt><dd class="mono">${esc(a.detected)}</dd>
        <dt>Supported versions</dt><dd>${esc(a.versions)}</dd>
        <dt>Install model</dt><dd>${esc(a.installModel)}</dd>
        <dt>Setup requirement</dt>
        <dd>${a.setupRequired ? 'one-time setup required' : 'no host setup required'}</dd>
      </dl>
      ${a.alert ? `<p class="app-alert ${a.alert.level}">${esc(a.alert.text)}</p>` : ''}
      <div class="app-actions">
        <label class="switch">
          <input type="checkbox" data-enable="${a.key}" ${APP_ENABLED[a.key] ? 'checked' : ''}>
          <span class="track"></span><span class="sw-label">Enabled</span>
        </label>
        ${tipIcon('Navigation is routed to this app only while it is enabled, focused, and the daemon is in 3D mode.')}
        <span class="spacer"></span>
        <button class="btn btn-sm btn-ghost" data-copy-instructions="${a.key}">Copy instructions</button>
        ${actionBtn}
      </div>
      <div class="instructions-box">
        <h4>Automatic setup</h4>${esc(a.instructions.auto)}
        <h4>Manual setup / restricted permissions</h4>${esc(a.instructions.manual)}
        <h4>Health check</h4>${esc(a.instructions.health)}
      </div>
    </div>
  </div>`;
}

/* ------------------------------------------------------------- bindings */
function bindingsSubTabs(key) {
  return APPS_BY_KEY[key].rich
    ? [['tuning', 'Tuning'], ['mode', 'Mode'], ['panzoom', 'Pan / Zoom'], ['routing', 'Axes & Routing']]
    : [['tuning', 'Tuning'], ['scheme', 'Control Scheme'], ['routing', 'Axes & Routing']];
}

function viewBindings() {
  const key = S.editingApp;
  const tabs = bindingsSubTabs(key);
  if (!tabs.some(([v]) => v === S.sub.bindings)) S.sub.bindings = tabs[0][0];

  const head = `
  <div class="f" style="grid-template-columns:164px max-content max-content 1fr; margin-bottom:2px">
    <span class="f-l">Editing app${tipIcon('Also the active profile the daemon drives.')}</span>
    <select class="sel" id="editing-app" style="min-width:130px">
      ${APPS.map(a => `<option value="${a.key}" ${a.key === key ? 'selected' : ''}>${esc(a.key)}</option>`).join('')}
    </select>
    <button class="btn btn-sm" id="btn-reset-overrides" style="margin-left:14px">Reset user overrides</button>
    <span class="note" style="margin:0 0 0 12px">Host alignment is developer-owned and hidden here.</span>
  </div>
  <div class="subtabs">${seg(tabs, S.sub.bindings, 'subtab')}</div>`;

  const pane = {
    tuning: paneTuning, mode: paneMode, panzoom: panePanZoom,
    routing: paneRouting, scheme: paneScheme,
  }[S.sub.bindings](key);

  return head + `<div class="pane" data-pane="${key}-${S.sub.bindings}">${pane}</div>`;
}

function paneTuning(key) {
  const b = `apps.${key}.bindings`;
  const rich = APPS_BY_KEY[key].rich;
  const sk = key === 'sketchup';
  const sensTip = (key === 'blender' || sk || !rich) ? TIPS.orbitSens1to1 : TIPS.orbitSensBaseline;
  return `
  ${sect('Sensitivity & rate', `how trackball motion drives ${APPS_BY_KEY[key].name}`)}
  <div class="g2">
    ${field('Viewport refresh rate (Hz)', ctrlRate(`apps.${key}.rate_hz`), TIPS.rate)}
    ${field(sk ? 'Orbit ↔ pan/move toggle' : 'Orbit ↔ pan/zoom toggle',
            ctrlSelect(`${b}.toggle`, ['shift', 'none']), TIPS.toggle)}
    ${field('Orbit sensitivity', ctrlText(`${b}.orbit.sensitivity`), sensTip)}
    ${field('Zoom dominance', ctrlText(`${b}.zoom.dominance`), TIPS.dominance)}
    ${field(sk ? 'Pan / move gain' : 'Pan gain', ctrlText(`${b}.pan.gain`), TIPS.panGain)}
    ${field(sk ? 'Zoom / vertical gain' : 'Zoom gain', ctrlText(`${b}.zoom.gain`), TIPS.zoomGain)}
  </div>`;
}

function paneScheme(key) {           // standard apps only
  const b = `apps.${key}.bindings`;
  const onshapeExtra = key !== 'onshape' ? '' : `
  ${sect('Under-cursor orbit — userscript')}
  <div class="sw-row" style="gap:10px">
    <button class="btn btn-sm" data-onshape-userscript="copy">Copy userscript…</button>
    <span class="note" style="margin:0">Required only when Orbit pivot = Under Cursor (mouse);
    reports the exact #canvas pointer to the local bridge.</span>
  </div>`;
  return `
  ${sect('Control scheme', 'Default = use the General default')}
  <div class="g2">
    ${field('Orbit pivot', ctrlSelect(`${b}.scheme.orbit_pivot`, pivotOptionsFor(key),
      { warn: key === 'onshape' ? 'onshape-cursor' : '' }), PIVOT_TIPS.default)}
    ${field('Orbit style', ctrlSelect(`${b}.scheme.orbit_style`,
      [['default', 'default'], ['free', 'free'], ['turntable', 'turntable']]))}
    ${field('Zoom mode', ctrlSelect(`${b}.scheme.zoom_mode`, ZOOM_MODE_OPTIONS))}
    ${field('Screen Center hold (s)', ctrlText(`apps.${key}.screen_center_pivot_hold_sec`), TIPS.screenHold)}
  </div>
  ${levelHorizonSw(key)}
  ${swRow('Selection overrides orbit center', `apps.${key}.selection_overrides_pivot`,
    { tip: TIPS.selOverride })}
  ${onshapeExtra}`;
}

function paneMode(key) {             // rich apps only
  const b = `apps.${key}.bindings`;
  const adv = `apps.${key}.advanced`;
  const isBlender = key === 'blender';
  const isSketchup = key === 'sketchup';
  const isGodot = key === 'godot';

  const navMode = S.navTab[key] || getPath(`${adv}.nav_mode`);
  let panel = '';
  if (navMode === 'orbit') {
    const styleOpts = isGodot
      ? [['turntable', 'Turntable']]
      : [['default', 'Default (General)'], ['free', 'Trackball (free)'], ['turntable', 'Turntable']];
    const twistOpts = isGodot ? ['zoom', 'dolly', 'none'] : ['roll', 'zoom', 'dolly', 'none'];
    panel = `
    <div class="g2">
      ${field('Orbit method', ctrlSelect(`${b}.scheme.orbit_style`, styleOpts),
        isGodot ? 'Turntable only — Godot\'s editor camera is yaw/pitch and cannot roll.' : '')}
      ${field('Orbit pivot', ctrlSelect(`${b}.scheme.orbit_pivot`, pivotOptionsFor(key)), PIVOT_TIPS[key])}
      ${isSketchup ? '' : field('Twist action', ctrlSelect(`${adv}.twist_action`, twistOpts),
        'What un-shifted twist does in orbit mode.')}
    </div>
    ${isGodot ? '' : swRow(
      isBlender ? 'Lock horizon (keep level even in trackball)'
      : isSketchup ? 'Lock horizon (keep level in free orbit)'
      : 'Lock horizon (keep level even in free orbit)', `${adv}.lock_horizon`)}
    ${isGodot ? '' : levelHorizonSw(key)}
    ${swRow('Selection overrides orbit center', `apps.${key}.selection_overrides_pivot`,
      { tip: TIPS.selOverride })}`;
  } else if (navMode === 'fly') {
    panel = `
    <div class="g2">${field('Fly speed', ctrlText(`${adv}.fly_speed`))}</div>
    <p class="note">${esc(isGodot
      ? 'Fly look is horizon-locked (no roll). Movement follows the view forward.'
      : 'Fly = free 6DOF (banks on twist; forward follows pitch). Hold Shift to strafe/advance and rise/fall.')}</p>`;
  } else {
    panel = `
    <div class="g2">${field('Walk speed', ctrlText(`${adv}.walk_speed`))}</div>
    <p class="note">Walk = horizon-locked look; movement stays on the ground plane. Hold
    Shift to move, with twist for rise/fall (useful for stairs and floors).</p>`;
  }

  return `
  ${sect('Navigation mode')}
  <div class="f" style="grid-template-columns:164px max-content; margin-bottom:6px">
    <span class="f-l">Mode${isBlender ? tipIcon('In Blender, Alt+` (or View ▸ Trackball: Cycle Nav Mode) toggles orbit/fly/walk too.') : ''}</span>
    ${seg([['orbit', 'Orbit'], ['fly', 'Fly'], ['walk', 'Walk']], navMode, 'navmode')}
  </div>
  <div class="pane" data-pane="${key}-${navMode}">${panel}</div>`;
}

function panePanZoom(key) {          // rich apps only
  const b = `apps.${key}.bindings`;
  const adv = `apps.${key}.advanced`;
  const hold = field('Screen Center hold (s)',
    ctrlText(`apps.${key}.screen_center_pivot_hold_sec`), TIPS.screenHold);

  if (key === 'blender') {
    return `
    ${sect('Pan / Zoom')}
    <div class="g2">
      ${field('Zoom style', ctrlSelect(`${adv}.zoom_style`, ['zoom', 'dolly']),
        'zoom changes the view distance; dolly translates the eye.')}
      ${hold}
    </div>
    ${swRow('Zoom to mouse (screen-centre surface)', `${adv}.zoom_to_mouse`)}
    ${swRow('Pan scales with view distance', `${adv}.pan_scales_with_distance`)}
    ${sect('Camera view')}
    ${swRow('Lock camera to view (drive the scene camera)', `${adv}.lock_camera_to_view`,
      { tip: 'In camera view, the trackball drives scene.camera instead of the viewport.' })}`;
  }
  if (key === 'sketchup') {
    return `${sect('Pan / Zoom')}<div class="g2">${hold}</div>`;
  }
  const unityExtra = key !== 'unity' ? '' : `
  ${swRow('Override Unity Dynamic Clipping', `${adv}.override_dynamic_clip`,
    { tip: 'Scene View Camera → Dynamic Clipping auto-fits near/far planes from the view size (can feel like zoom-to-fit while you look around). When on, Trackball Nav forces it off and uses fixed clip planes; turning this off restores Dynamic Clipping.' })}
  <div class="g2">
    ${field('Pivot extent limit ×', ctrlText(`${adv}.pivot_extent_mult`),
      'Caps how far an Under Cursor / Screen Center pivot can be from the camera (scene AABB radius × multiplier). Stops near-horizon hits from flinging the view. Typical 4–16; default 8.')}
  </div>`;
  return `
  ${sect('Pan / Zoom')}
  <div class="g2">
    ${field('Zoom mode', ctrlSelect(`${b}.scheme.zoom_mode`, ZOOM_MODE_OPTIONS))}
    ${hold}
  </div>
  ${swRow('Pan scales with focus distance', `${adv}.pan_scales_with_distance`)}
  ${unityExtra}`;
}

function paneRouting(key) {
  const rich = APPS_BY_KEY[key].rich;
  let rows, tabs = '';
  if (rich) {
    const adv = `apps.${key}.advanced`;
    const isGodot = key === 'godot';
    const routeTab = S.routeTab[key] || 'orbit';
    let routes = ROUTES_BY_MODE[routeTab];
    if (isGodot && routeTab === 'camera') routes = routes.filter(([, k]) => k !== 'roll');
    if (isGodot && routeTab === 'fly') routes = routes.filter(([, k]) => k !== 'bank');
    rows = routes.map(([label, k]) =>
      routeRow(label, `${adv}.axis_source.${routeTab}.${k}`, `${adv}.invert.${routeTab}.${k}`)
    ).join('');
    tabs = `<div style="margin:0 0 8px">${seg(
      [['orbit', 'Orbit'], ['camera', 'Camera'], ['fly', 'Fly'], ['walk', 'Walk']],
      routeTab, 'routetab')}</div>`;
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
  return `
  ${sect('Action axes & directions', rich ? 'independent per mode' : 'per this app')}
  ${tabs}
  <div class="routing-grid">
    <div class="pane" data-pane="${key}-routes">
      ${rows}
      <p class="note">${esc(TIPS.sources)}${rich ? ' Camera shares Orbit\'s pan/zoom mappings.' : ''}</p>
    </div>
    <div class="routing-diagram">
      ${ballDiagram(176)}
      <p class="note">Hover a source to highlight its axis.</p>
    </div>
  </div>`;
}

/* -------------------------------------------------------------- general */
const GENERAL_TABS = [
  ['device', 'Device & Orientation'], ['scheme', '3D Scheme'], ['pointer', 'Pointer & Mode'],
];

function viewGeneral() {
  const pane = { device: paneGenDevice, scheme: paneGenScheme, pointer: paneGenPointer }
    [S.sub.general]();
  return `
  <div class="subtabs">${seg(GENERAL_TABS, S.sub.general, 'subtab')}</div>
  <div class="pane" data-pane="general-${S.sub.general}">${pane}</div>`;
}

function paneGenDevice() {
  const src = S.cfg.general.axis_orientation.source;
  const orientRows = [0, 1, 2].map(t => `
    <div class="route-row wide-name">
      <span class="route-name">Logical ${AXIS[t]}</span>
      <span class="route-mid">uses physical</span>
      <span class="axis-seg">${AXIS.map((n, i) =>
        `<button data-orient="${t}" data-ax="${i}" data-ax-hl="${i}"
                 class="${i === src[t] ? 'on' : ''}">${n}</button>`).join('')}</span>
      ${invPill(`general.axis_orientation.invert.${t}`)}
    </div>`).join('');

  return `
  ${sect('Device')}
  <div class="g2">
    ${field('Name', ctrlText('device.name', { cast: 'str', wide: true }), 'Applies on the next reconnect.')}
    ${field('Address (optional)', ctrlText('device.address', { cast: 'str', wide: true }),
      'AA:BB:.. — applies on reconnect; blank = scan by name.')}
    ${field('Bridge update rate (Hz)', ctrlText('bridge.rate_hz', { cast: 'int' }),
      'Global default viewport rate for every 3D app; override per app in Bindings.')}
  </div>
  ${sect('Physical trackball orientation', 'applied once, before pointer mode and every app')}
  <div class="routing-grid">
    <div>
      ${orientRows}
      <div style="padding-top:8px">
        <button class="btn btn-sm" id="btn-reset-orient">Reset orientation</button>
      </div>
      <p class="note">Changing a source swaps axes instead of duplicating one, so the mapping
      always stays a valid permutation.</p>
    </div>
    <div class="routing-diagram">
      ${ballDiagram(176)}
      <p class="note">Physical axes — top view, front toward you.</p>
    </div>
  </div>`;
}

function paneGenScheme() {
  const g = 'general';
  const chain = S.cfg.general.orbit_pivot_fallbacks;
  const fbItems = chain.length ? chain.map((m, i) => `
    <div class="fb-item ${i === S.fbSel ? 'sel' : ''} ${i === S.fbFlash ? 'just-moved' : ''}"
         data-fbsel="${i}">
      <span class="fb-n">${i + 1}</span>${esc(PIVOT_LABELS[m])}
    </div>`).join('')
    : '<div class="fb-empty">empty — a failed primary pivot produces no orbit</div>';
  const addable = ORBIT_PIVOT_METHODS.filter(m => !chain.includes(m));

  return `
  ${sect('3D control scheme', 'defaults — every app can override per field')}
  <div class="g2">
    ${field('Orbit pivot', ctrlSelect(`${g}.scheme.orbit_pivot`,
      ORBIT_PIVOT_METHODS.map(m => [m, PIVOT_LABELS[m]])), PIVOT_TIPS.default)}
    ${field('Orbit style', ctrlSelect(`${g}.scheme.orbit_style`, ['free', 'turntable']))}
    ${field('Zoom mode', ctrlSelect(`${g}.scheme.zoom_mode`,
      [['to_center', 'to_center'], ['to_object', 'to_object'],
       ['to_cursor', 'to_cursor (under mouse)']]))}
  </div>
  ${swRow('Level horizon when entering Turntable/Walk', `${g}.level_horizon_on_entry`,
    { tip: TIPS.levelHorizonGeneral })}
  ${sect('Failure fallback order')}
  <div class="fb-editor">
    <div>
      <div class="fb-list">${fbItems}</div>
      <div class="fb-add">
        <select class="sel" id="fb-add-sel" style="min-width:130px" ${addable.length ? '' : 'disabled'}>
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

function paneGenPointer() {
  const g = 'general';
  return `
  ${sect('Pointer / scroll', 'cursor mode')}
  <div class="g2">
    ${field('Cursor sensitivity', ctrlText(`${g}.cursor.gain`), 'radians → pixels.')}
    ${field('Scroll gain', ctrlText(`${g}.scroll.gain`))}
    ${field('Scroll deadzone', ctrlText(`${g}.scroll.deadzone`))}
    ${field('Scroll dominance', ctrlText(`${g}.scroll.dominance`),
      'How strongly twist must dominate ball-plane motion before it counts as scroll.')}
  </div>
  ${sect('Mode & buttons')}
  <div class="g2">
    ${field('Default mode', ctrlSelect(`${g}.default_mode`,
      [['cube', 'cube (3D nav)'], ['cursor', 'cursor (pointer)']]),
      'The mode the daemon starts in; the toggle above switches it live.')}
    ${field('Left button', ctrlSelect(`${g}.buttons.left`, ['left', 'right', 'middle', 'none']))}
    ${field('Right button', ctrlSelect(`${g}.buttons.right`, ['left', 'right', 'middle', 'none']))}
    ${field('Middle button', ctrlSelect(`${g}.buttons.middle`, ['left', 'right', 'middle', 'none']))}
  </div>
  <p class="note">Button mappings are reserved (the device handles buttons in HID mode).</p>`;
}

/* ============================================================== renderer */
const VIEWS = { overview: viewOverview, apps: viewApps, bindings: viewBindings,
                general: viewGeneral };

function render() {
  $('#view').innerHTML = VIEWS[S.route]() +
    '<datalist id="rate-presets">' +
    RATE_PRESETS.map(r => `<option value="${r}">`).join('') + '</datalist>';
  $$('.tab').forEach(b => b.classList.toggle('active', b.dataset.route === S.route));
  positionNavUnderline();
  S.fbFlash = -1;
}

function rerenderView() {            // re-render without replaying the page transition
  const view = $('#view');
  const scroll = view.scrollTop;
  view.style.animation = 'none';
  render();
  view.scrollTop = scroll;
  requestAnimationFrame(() => { view.style.animation = ''; });
}

function positionNavUnderline() {
  const active = $('.tab.active');
  const u = $('#nav-underline');
  if (!active || !u) return;
  u.style.transform =
    `translateX(${active.offsetLeft + active.offsetWidth / 2 - 8}px) rotate(var(--tilt))`;
}

function setMode(mode) {
  S.mode = mode;
  $$('#mode-toggle button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('#mode-thumb').style.transform = mode === 'cube' ? 'translateX(68px)' : 'translateX(0)';
  const fact = $('#fact-mode');
  if (fact) fact.firstChild.textContent = (mode === 'cube' ? '3D navigation ' : 'Cursor ');
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
        a.status = { chip: 'warn', text: 'installed · v0.2.4 — restart editor' };
        a.action = 'Reinstall';
        rerenderView();
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
        a.status = { chip: 'idle', text: 'installed · v0.1.6' };
        a.action = 'Reinstall';
        APP_ENABLED.unity = true;
        rerenderView();
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
        a.status = { chip: 'idle', text: 'bridge ready — awaiting browser' };
        a.action = null;
        APP_ENABLED.onshape = true;
        rerenderView();
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

  const nav = t.closest('.tab[data-route]');
  if (nav) {
    S.route = nav.dataset.route;
    history.replaceState(null, '', '#' + S.route);
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

  const sub = t.closest('[data-subtab]');
  if (sub) { S.sub[S.route] = sub.dataset.subtab; rerenderView(); return; }

  const appSel = t.closest('[data-appsel]');
  if (appSel) { S.selApp = appSel.dataset.appsel; rerenderView(); return; }

  /* axis source pick (per-app routing) */
  const ax = t.closest('[data-axpick]');
  if (ax) {
    setPath(ax.dataset.axpick, +ax.dataset.ax);
    $$('button', ax.parentElement).forEach(b => b.classList.toggle('on', b === ax));
    return;
  }

  /* global orientation pick — swap to preserve the permutation */
  const or = t.closest('[data-orient]');
  if (or) {
    const target = +or.dataset.orient, wanted = +or.dataset.ax;
    const src = S.cfg.general.axis_orientation.source;
    const other = src.indexOf(wanted);
    [src[target], src[other]] = [src[other], src[target]];
    rerenderView();
    return;
  }

  const inv = t.closest('[data-invpick]');
  if (inv) {
    const path = inv.dataset.invpick;
    const v = !getPath(path);
    setPath(path, v);
    inv.classList.toggle('on', v);
    return;
  }

  const nm = t.closest('[data-navmode]');
  if (nm) {
    S.navTab[S.editingApp] = nm.dataset.navmode;
    setPath(`apps.${S.editingApp}.advanced.nav_mode`, nm.dataset.navmode);
    rerenderView();
    return;
  }

  const rt = t.closest('[data-routetab]');
  if (rt) { S.routeTab[S.editingApp] = rt.dataset.routetab; rerenderView(); return; }

  const ci = t.closest('[data-copy-instructions]');
  if (ci) {
    const a = APPS_BY_KEY[ci.dataset.copyInstructions];
    copyText(`Install model\n${a.installModel}\n\nAutomatic setup\n${a.instructions.auto}\n\n` +
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
    rerenderView();
    toast('Orientation reset to identity');
    return;
  }

  if (t.closest('#btn-reset-overrides')) {
    const key = S.editingApp;
    const name = APPS_BY_KEY[key].name;
    openModal({
      title: 'Reset user overrides?',
      body: `Reset every ${name} user-facing navigation setting to its clean default?\n\n` +
            'Developer host alignment is stored separately and will not change.\n' +
            'Enable/install state and installed add-in version will be preserved.',
      buttons: [
        { label: 'Reset', kind: 'btn-danger', onClick: () => {
          const kind = ['blender', 'sketchup', 'unreal', 'unity', 'godot'].includes(key) ? key : '';
          S.cfg.apps[key] = defApp(kind);
          delete S.navTab[key];
          delete S.routeTab[key];
          rerenderView();
          toast(`${name} user overrides reset — now following General`);
        } },
        { label: 'Cancel', kind: 'btn-ghost' }],
    });
    return;
  }

  /* fallback-chain editor */
  const fbi = t.closest('[data-fbsel]');
  if (fbi) { S.fbSel = +fbi.dataset.fbsel; rerenderView(); return; }

  const fbm = t.closest('[data-fbmove]');
  if (fbm) {
    const d = +fbm.dataset.fbmove, chain = S.cfg.general.orbit_pivot_fallbacks, i = S.fbSel;
    if (i >= 0 && i + d >= 0 && i + d < chain.length) {
      [chain[i], chain[i + d]] = [chain[i + d], chain[i]];
      S.fbSel = i + d;
      S.fbFlash = i + d;
      rerenderView();
    }
    return;
  }

  if (t.closest('[data-fbremove]')) {
    const chain = S.cfg.general.orbit_pivot_fallbacks;
    if (S.fbSel >= 0 && S.fbSel < chain.length) {
      chain.splice(S.fbSel, 1);
      S.fbSel = Math.min(S.fbSel, chain.length - 1);
      rerenderView();
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
      rerenderView();
    }
    return;
  }
});

document.addEventListener('change', e => {
  const t = e.target;

  if (t.id === 'editing-app') {
    S.editingApp = t.value;
    history.replaceState(null, '', '#bindings:' + t.value);
    render();
    return;
  }

  const en = t.closest('[data-enable]');
  if (en) {
    APP_ENABLED[en.dataset.enable] = t.checked;
    toast(`${APPS_BY_KEY[en.dataset.enable].name} integration ${t.checked ? 'enabled' : 'disabled'}`);
    return;
  }

  const path = t.dataset.path;
  if (!path) return;
  const cast = t.dataset.cast;

  if (cast === 'bool') {
    setPath(path, t.checked);
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
    if (t.dataset.warn === 'onshape-cursor' && t.value === 'cursor' &&
        !S.cfg.onshape.cursor_userscript_warn_dismissed) {
      onshapeUserscriptDialog({ warn: true });
    }
  }
});

function fmtRate(v) { return (!v || +v === 0) ? 'Default' : String(v); }

/* axis-picker hover → highlight that axis on every visible ball diagram */
document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-ax-hl]');
  if (!el) return;
  const n = el.dataset.axHl;
  $$('svg.ball-diagram').forEach(svg => {
    svg.classList.remove('hl-0', 'hl-1', 'hl-2');
    svg.classList.add('hl-' + n);
  });
});
document.addEventListener('mouseout', e => {
  if (!e.target.closest('[data-ax-hl]')) return;
  $$('svg.ball-diagram').forEach(svg => svg.classList.remove('hl-0', 'hl-1', 'hl-2'));
});

/* ------------------------------------------------------------------ boot */
(function initFromHash() {
  // deep-linkable: "#apps", "#general", "#bindings", "#bindings:unreal", …
  const [route, arg] = location.hash.replace(/^#/, '').split(':');
  if (PAGES[route]) S.route = route;
  if (route === 'bindings' && APPS_BY_KEY[arg]) S.editingApp = arg;
  if (route === 'apps' && APPS_BY_KEY[arg]) S.selApp = arg;
})();
setMode(S.cfg.general.default_mode === 'cube' ? 'cube' : 'cursor');
render();
window.addEventListener('resize', positionNavUnderline);
