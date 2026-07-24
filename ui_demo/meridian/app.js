/* ============================================================================
   Astrolabe — Meridian UI demo (front-end only, drives nothing)
   Three surfaces: Hosts · Bindings · System
   ========================================================================== */
'use strict';

const $  = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = v => (typeof v === 'number' ? String(+(+v).toPrecision(6)) : String(v));

const AXIS = ['X', 'Y', 'Z'];
const GLOBAL = '__global__';

const PIVOT_LABELS = {
  camera: 'Camera', screen_center: 'Screen Center', cursor: 'Under Cursor',
  selection: 'Selection', cursor_3d: '3D Cursor', object: 'Model Center', origin: 'World Origin',
};
const ORBIT_PIVOT_METHODS = ['camera', 'screen_center', 'cursor', 'selection',
  'cursor_3d', 'object', 'origin'];
const OPTION_LABELS = {
  free: 'Free', turntable: 'Turntable', roll: 'Roll', zoom: 'Zoom', dolly: 'Dolly', none: 'None',
  to_center: 'To Center', to_object: 'To Object', to_cursor: 'To Cursor',
  '3d': '3D navigation', pointer: 'Pointer',
};
const optLabel = v => OPTION_LABELS[v] || PIVOT_LABELS[v] || String(v);

const ROUTES_BY_MODE = {
  orbit:  [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Twist', 'twist'],
           ['Pan X', 'pan_x'], ['Pan Y', 'pan_y'], ['Zoom', 'zoom']],
  camera: [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Roll', 'roll']],
  fly:    [['Pitch', 'pitch'], ['Yaw', 'yaw'], ['Bank', 'bank'],
           ['Fwd', 'forward'], ['Strafe', 'strafe'], ['Up/Dn', 'vertical']],
  walk:   [['Pitch', 'pitch'], ['Yaw', 'yaw'],
           ['Fwd', 'forward'], ['Strafe', 'strafe'], ['Up/Dn', 'vertical']],
};
const LEAN_ACTIONS = [
  ['Orbit X', 'orbit.0'], ['Orbit Y', 'orbit.1'], ['Orbit Z', 'orbit.2'],
  ['Pan X', 'pan.x'], ['Pan Y', 'pan.y'], ['Zoom', 'zoom'],
];

const PIVOTS_DEFAULT = ['screen_center', 'cursor', 'selection', 'object', 'origin'];
const PIVOTS_CAMERA = ['camera', ...PIVOTS_DEFAULT];

const PROFILES = {
  blender:    { rich: true, pivots: ['camera', 'screen_center', 'cursor', 'selection',
                                     'cursor_3d', 'object', 'origin'],
                styles: ['free', 'turntable'], twist: ['roll', 'zoom', 'dolly', 'none'],
                zb: ['zoom', 'dolly'], cameraLock: true },
  sketchup:   { rich: true, pivots: PIVOTS_CAMERA, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'] },
  unreal:     { rich: true, pivots: PIVOTS_CAMERA, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'] },
  unity:      { rich: true, pivots: PIVOTS_CAMERA, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'],
                dynamicClip: true, pivotExtent: true },
  godot:      { rich: true, noRoll: true, noHorizon: true, pivots: PIVOTS_CAMERA,
                styles: ['turntable'], twist: ['zoom', 'dolly', 'none'], zb: ['zoom', 'dolly'] },
  freecad:    { pivots: PIVOTS_DEFAULT, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: [] },
  fusion360:  { pivots: PIVOTS_DEFAULT, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: ['zoom', 'dolly'] },
  solidworks: { pivots: PIVOTS_DEFAULT, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: [] },
  onshape:    { pivots: PIVOTS_DEFAULT, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: [], userscript: true },
  autocad:    { pivots: PIVOTS_CAMERA, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: ['zoom', 'dolly'] },
  rhino:      { pivots: PIVOTS_CAMERA, styles: ['free', 'turntable'],
                twist: ['roll', 'zoom', 'none'], zb: ['zoom', 'dolly'] },
};
const APP_ORDER = ['blender', 'freecad', 'sketchup', 'unreal', 'unity', 'godot',
  'rhino', 'fusion360', 'solidworks', 'onshape', 'autocad'];

const TIP_RATE = 'Per app; Global is the bridge rate. Higher is smoother; lower if the host lags.';
const TIP_HOLDS = 'Pivot hold ends an orbit after idle; pan clears orbit pivot. Zoom hold is for To Cursor only.';
const TIP_LEVEL = 'On: entering a level mode removes existing roll once. Off: keep current tilt.';
const TIP_CLIP = 'Forces Unity Scene View Dynamic Clipping off so near/far stay fixed while looking around.';

const SETTINGS = {
  'rate':          { label: 'Refresh (Hz)', kind: 'rate', section: 'feel', hint: 'Viewport frame rate.', tip: TIP_RATE },
  'orbit.sens':    { label: 'Orbit sensitivity', kind: 'num', section: 'feel', hint: 'Rotation per ball turn.' },
  'pan.gain':      { label: 'Pan gain', kind: 'num', section: 'feel', hint: 'Pan strength while held.' },
  'zoom.gain':     { label: 'Zoom gain', kind: 'num', section: 'feel', hint: 'Held-twist zoom strength.' },
  'zoom.dom':      { label: 'Zoom dominance', kind: 'num', section: 'feel', hint: 'How firmly twist reads as zoom.' },
  'fly.speed':     { label: 'Fly speed', kind: 'num', section: 'feel', rich: true, hint: 'Fly movement speed.' },
  'walk.speed':    { label: 'Walk speed', kind: 'num', section: 'feel', rich: true, hint: 'Walk movement speed.' },
  'pivot.hold':    { label: 'Pivot hold (s)', kind: 'num', section: 'feel', hint: 'Idle gap ending orbit.', tip: TIP_HOLDS },
  'zoom.hold':     { label: 'Zoom hold (s)', kind: 'num', section: 'feel', hint: 'To-Cursor zoom hold.', tip: TIP_HOLDS },
  'orbit.style':   { label: 'Orbit style', kind: 'enum', section: 'behavior', hint: 'How the view rotates.',
                     tip: 'Free permits roll; Turntable keeps the horizon fixed.',
                     choices: o => PROFILES[o.key === GLOBAL ? 'blender' : o.key].styles },
  'orbit.pivot':   { label: 'Orbit pivot', kind: 'enum', section: 'behavior', hint: 'What the view orbits.',
                     tip: 'Failed pivots fall through the System fallback chain.',
                     labeler: PIVOT_LABELS, choices: o => o.key === GLOBAL ? ORBIT_PIVOT_METHODS : PROFILES[o.key].pivots },
  'twist':         { label: 'Twist action', kind: 'enum', section: 'behavior', hint: 'Unshifted twist in Orbit.',
                     choices: o => PROFILES[o.key === GLOBAL ? 'blender' : o.key].twist },
  'lock.horizon':  { label: 'Lock horizon', kind: 'bool', section: 'behavior', rich: true, hint: 'Stay level in Free orbit.' },
  'level.entry':   { label: 'Level on entry', kind: 'bool', section: 'behavior', hint: 'Remove roll entering level mode.', tip: TIP_LEVEL },
  'sel.override':  { label: 'Selection override', kind: 'bool', section: 'behavior', hint: 'Selection beats pivot.' },
  'zoom.target':   { label: 'Zoom mode', kind: 'enum', section: 'behavior',
                     choices: () => ['to_center', 'to_object', 'to_cursor'], hint: 'What zoom moves toward.' },
  'zoom.behavior': { label: 'Pan-mode zoom', kind: 'enum', section: 'behavior',
                     choices: o => PROFILES[o.key === GLOBAL ? 'blender' : o.key].zb, hint: 'Zoom vs Dolly.' },
  'pan.scales':    { label: 'Pan × distance', kind: 'bool', section: 'behavior', rich: true, hint: 'Pan farther when zoomed out.' },
  'dyn.clip':      { label: 'Override dynamic clip', kind: 'bool', section: 'behavior', hint: 'Hold near/far planes.', tip: TIP_CLIP },
  'pivot.ext':     { label: 'Pivot extent ×', kind: 'num', section: 'behavior', hint: 'Cap pivots at scene bounds ×.' },
  'cam.lock':      { label: 'Lock camera to view', kind: 'bool', section: 'behavior', hint: 'Drive the active camera.' },
};

function appliesToApp(id, key) {
  const p = PROFILES[key], m = SETTINGS[id];
  if (m.rich && !p.rich) return false;
  if ((id === 'lock.horizon' || id === 'level.entry') && p.noHorizon) return false;
  if (id === 'zoom.behavior' && !p.zb.length) return false;
  if (id === 'dyn.clip' && !p.dynamicClip) return false;
  if (id === 'pivot.ext' && !p.pivotExtent) return false;
  if (id === 'cam.lock' && !p.cameraLock) return false;
  return true;
}

const SYS = {
  'rate': 30, 'orbit.sens': 1.0, 'pan.gain': 1.0, 'zoom.gain': 1.0, 'zoom.dom': 1.7,
  'fly.speed': 1.0, 'walk.speed': 1.0, 'pivot.hold': 0.5, 'zoom.hold': 0.5,
  'orbit.style': 'free', 'orbit.pivot': 'screen_center', 'twist': 'roll',
  'lock.horizon': false, 'level.entry': true, 'sel.override': true,
  'zoom.target': 'to_center', 'zoom.behavior': 'zoom', 'pan.scales': true,
  'dyn.clip': true, 'pivot.ext': 8.0, 'cam.lock': false,
  'pointer.gain': 216.0, 'scroll.gain': 29.0, 'scroll.deadzone': 0.004, 'scroll.dominance': 1.7,
  'mode.default': '3d',
  'hud.visible': true, 'hud.top': true, 'hud.through': true,
  'hud.opacity': 0.9, 'hud.margin': 16, 'hud.timeout': 3.0,
};
const APP_SYS = {
  blender: { 'orbit.pivot': 'camera' },
  sketchup: { 'zoom.behavior': 'dolly' }, unreal: { 'zoom.behavior': 'dolly' },
  unity: { 'zoom.behavior': 'dolly' }, rhino: { 'zoom.behavior': 'dolly' },
  godot: { 'orbit.style': 'turntable', 'twist': 'zoom', 'zoom.behavior': 'dolly' },
};

const DEFAULT_ACTION_AXIS_SOURCE = {
  orbit:  { pitch: 0, yaw: 1, twist: 2, pan_x: 0, pan_y: 1, zoom: 2 },
  camera: { pitch: 0, yaw: 1, roll: 2 },
  fly:    { pitch: 0, yaw: 1, bank: 2, forward: 1, strafe: 0, vertical: 2 },
  walk:   { pitch: 0, yaw: 1, forward: 1, strafe: 0, vertical: 2 },
};

function freshRich() {
  const src = {}, inv = {};
  for (const [mode, acts] of Object.entries(DEFAULT_ACTION_AXIS_SOURCE)) {
    for (const [a, s] of Object.entries(acts)) {
      src[`${mode}.${a}`] = s; inv[`${mode}.${a}`] = false;
    }
  }
  return { src, inv };
}
function freshLean() {
  return {
    src: { 'orbit.0': 0, 'orbit.1': 1, 'orbit.2': 2, 'pan.x': 1, 'pan.y': 0, 'zoom': 2 },
    inv: { 'orbit.0': false, 'orbit.1': false, 'orbit.2': false, 'pan.x': false, 'pan.y': false, 'zoom': false },
  };
}
function freshRoute(key) { return (key === GLOBAL || PROFILES[key].rich) ? freshRich() : freshLean(); }

const APPS = [
  { key: 'blender', name: 'Blender',
    status: { chip: 'good', text: 'connected · v0.1.12', short: 'ok' },
    detected: 'v5.1.1', versions: '4.2–5.1',
    installModel: 'User add-on + optional startup shim.',
    security: 'Unsigned Python in current-user folders only.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Copies Trackball Nav into each detected Blender scripts/addons folder.',
      manual: 'Copy plugins/blender/trackball_nav into the user addons folder and enable it.',
      health: 'Focus Blender; row should show connected. See blender_addin.log.' } },
  { key: 'freecad', name: 'FreeCAD',
    status: { chip: 'idle', text: 'installed · v0.1.6', short: 'inst' },
    detected: 'v1.1.1', versions: '1.0–1.1',
    installModel: 'User Mod add-on.', security: 'Current-user Mod folder only.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Copies TrackballNav into FreeCAD’s user Mod folder.',
      manual: 'Copy plugins/freecad/TrackballNav into %APPDATA%\\FreeCAD\\…\\Mod.',
      health: 'Focus a 3D view; check freecad_addin.log.' } },
  { key: 'sketchup', name: 'SketchUp',
    status: { chip: 'idle', text: 'installed · v0.2.3', short: 'inst' },
    detected: 'v2026.2', versions: '2025–2026',
    installModel: 'Ruby extension in user Plugins.', security: 'Current-user Plugins only.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Copies loader + extension into each annual release.',
      manual: 'Copy into SketchUp Plugins; Web not supported.',
      health: 'Extension Manager should list Trackball Nav.' } },
  { key: 'unreal', name: 'Unreal Engine',
    status: { chip: 'warn', text: 'update available', short: 'upd' },
    detected: 'v5.9.0', versions: '5.8 tested',
    alert: { level: 'warn', text: 'Detected 5.9.0 is unverified.' },
    installModel: 'Editor plugin per engine or project.',
    security: 'Engine-wide may need UAC; per-project avoids elevation.',
    setupRequired: true, action: 'Update',
    instructions: {
      auto: 'Copies TrackballNav into Engine/Plugins (may elevate).',
      manual: 'Copy into <Project>/Plugins and enable Python Editor Script Plugin.',
      health: 'Focus a perspective viewport; see unreal_addin.log.' } },
  { key: 'unity', name: 'Unity',
    status: { chip: 'idle', text: 'not set up', short: '—' },
    detected: '6000.5.3f1', versions: 'Unity 6',
    installModel: 'UPM package per project.', security: 'Unsigned C# into Packages.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Copies package into recent/running projects.',
      manual: 'Copy com.astrolabe.trackball-nav into Packages.',
      health: 'Focus a Scene view; see unity_addin.log.' } },
  { key: 'godot', name: 'Godot',
    status: { chip: 'off', text: 'not detected', short: '—' },
    detected: '—', versions: '4.4–4.7',
    installModel: 'EditorPlugin per project.', security: 'Edits project.godot only.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Copies addons/trackball_nav and enables the plugin.',
      manual: 'Copy into addons and enable under Project Settings → Plugins.',
      health: 'Focus a 3D editor viewport.' } },
  { key: 'rhino', name: 'Rhino',
    status: { chip: 'idle', text: 'installed · v0.1.2', short: 'inst' },
    detected: 'v8.19', versions: 'Rhino 8',
    installModel: 'User Python + startup command.', security: 'Current-user scripts.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Copies TrackballNav and registers startup.',
      manual: 'Copy into Rhino 8 scripts; add RunPythonScript startup.',
      health: 'Focus a viewport; see rhino_addin.log.' } },
  { key: 'fusion360', name: 'Fusion 360',
    status: { chip: 'idle', text: 'installed · v0.1.16', short: 'inst' },
    detected: 'production', versions: 'rolling',
    installModel: 'User AddIns folder.', security: 'Run / Run on Startup required.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Copies TrackballNav; enable Run on Startup in Add-Ins.',
      manual: 'Copy into Autodesk Fusion 360 API AddIns.',
      health: 'Focus an open design.' } },
  { key: 'solidworks', name: 'SolidWorks',
    status: { chip: 'idle', text: 'ready · COM', short: 'ready' },
    detected: 'v2025', versions: '2025',
    installModel: 'Direct COM — no host files.', security: 'Per-user COM only.',
    setupRequired: false, action: null,
    instructions: {
      auto: 'Enabling runs a one-time pywin32 prerequisite check.',
      manual: 'Nothing to copy. pip install pywin32 if the check fails.',
      health: 'Focus a part/assembly; see daemon.log.' } },
  { key: 'onshape', name: 'Onshape',
    status: { chip: 'idle', text: 'not set up', short: '—' },
    detected: 'Chrome bridge', versions: 'rolling web',
    installModel: 'Browser bridge + optional userscript.',
    security: 'TLS on 127.51.68.120 only; trust-store never automatic.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Creates local TLS cert; enable SpaceMouse in Onshape prefs.',
      manual: 'Trust cert; optional userscript for Under Cursor orbit.',
      health: 'Focus an Onshape document after handshake.' } },
  { key: 'autocad', name: 'AutoCAD',
    status: { chip: 'bad', text: 'unsupported host', short: 'bad' },
    detected: 'v2024', versions: '2025–2027',
    alert: { level: 'bad', text: 'Detected 2024 is unsupported.' },
    installModel: 'Per-user .NET plugin + NETLOAD.',
    security: 'APPDATA only; TRUSTEDPATHS for that folder.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Stages DLL under APPDATA and NETLOADs on attach.',
      manual: 'Copy DLL to %APPDATA%\\TrackballDaemon\\acad_plugin and NETLOAD.',
      health: 'Type TBNAV or look for connected.' } },
];
const APPS_BY_KEY = Object.fromEntries(APPS.map(a => [a.key, a]));

const TOKEN_LABELS = {
  'keyboard:ctrl': 'Ctrl', 'keyboard:shift': 'Shift', 'keyboard:alt': 'Alt', 'keyboard:meta': 'Win',
  'keyboard:f12': 'F12',
  'ble.astrolabe:fiveway.up': '5↑', 'ble.astrolabe:fiveway.down': '5↓',
  'ble.astrolabe:fiveway.left': '5←', 'ble.astrolabe:fiveway.right': '5→',
  'ble.astrolabe:fiveway.center': '5●',
  'ble.xiao3389:button.left': 'XIAO L', 'ble.xiao3389:button.right': 'XIAO R',
  'ble.xiao3389:button.middle': 'XIAO M',
};
const tokenLabel = t => TOKEN_LABELS[t] || t;
const tokenSource = t => t.split(':')[0];
const TOKEN_GROUPS = [
  ['Keyboard', ['keyboard:ctrl', 'keyboard:shift', 'keyboard:alt', 'keyboard:meta', 'keyboard:f12']],
  ['Astrolabe', ['ble.astrolabe:fiveway.up', 'ble.astrolabe:fiveway.down',
    'ble.astrolabe:fiveway.left', 'ble.astrolabe:fiveway.right', 'ble.astrolabe:fiveway.center']],
  ['XIAO', ['ble.xiao3389:button.left', 'ble.xiao3389:button.right', 'ble.xiao3389:button.middle']],
];

const ACTION_GROUPS = [
  ['Modes', [
    ['input.toggle', 'Toggle Pointer / 3D'],
    ['input.hold_3d', 'Hold 3D'],
    ['input.hold_pointer', 'Hold Pointer'],
    ['navigation.set_orbit', 'Set Orbit'],
    ['navigation.set_fly', 'Set Fly'],
    ['navigation.set_walk', 'Set Walk'],
    ['navigation.cycle', 'Cycle Orbit / Fly / Walk'],
  ]],
  ['Navigation holds', [
    ['navigation.hold_pan', 'Hold Pan / Zoom'],
    ['navigation.hold_secondary', 'Hold secondary Orbit'],
  ]],
  ['Pointer buttons', [
    ['pointer.left', 'Hold Left click'],
    ['pointer.right', 'Hold Right click'],
    ['pointer.middle', 'Hold Middle click'],
  ]],
];
const COMMON_LABELS = Object.fromEntries(ACTION_GROUPS.flatMap(([, acts]) => acts));

const KB_SETTINGS = [
  ['navigation.mode', 'Navigation mode', 'enum', ['orbit', 'fly', 'walk']],
  ['orbit.style', 'Orbit style', 'enum', ['free', 'turntable']],
  ['orbit.pivot', 'Orbit pivot', 'enum', ORBIT_PIVOT_METHODS],
  ['twist', 'Twist action', 'enum', ['roll', 'zoom', 'dolly', 'none']],
  ['zoom.target', 'Zoom mode', 'enum', ['to_center', 'to_object', 'to_cursor']],
  ['level.entry', 'Level on entry', 'bool', null],
  ['sel.override', 'Selection override', 'bool', null],
  ['lock.horizon', 'Lock horizon', 'bool', null],
  ['orbit.sens', 'Orbit sensitivity', 'num', null],
  ['zoom.gain', 'Zoom gain', 'num', null],
];
const KB_SETTING_META = Object.fromEntries(KB_SETTINGS.map(([id, lab, kind, ch]) => [id, { lab, kind, ch }]));

function seedProfiles() {
  const co = target => ({ kind: 'common', target });
  return {
    astrolabe_5way: {
      label: 'Astrolabe hardware',
      bindings: [
        { id: 'keyboard.ctrl.3d', label: 'Ctrl: 3D', enabled: true, chord: ['keyboard:ctrl'], match: 'allow', apps: [], action: co('input.hold_3d'), system: true },
        { id: 'keyboard.shift.pan', label: 'Shift: Pan / Zoom', enabled: true, chord: ['keyboard:shift'], match: 'allow', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'keyboard.ctrl_shift.pan', label: 'Ctrl + Shift: Pan', enabled: true, chord: ['keyboard:ctrl', 'keyboard:shift'], match: 'exact', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'astrolabe.center.toggle_mode', label: 'Center: Toggle mode', enabled: true, chord: ['ble.astrolabe:fiveway.center'], match: 'exact', apps: [], action: co('input.toggle'), system: true },
        { id: 'astrolabe.up.orbit', label: 'Up: Orbit', enabled: true, chord: ['ble.astrolabe:fiveway.up'], match: 'exact', apps: [], action: co('navigation.set_orbit'), system: true },
        { id: 'astrolabe.right.fly', label: 'Right: Fly', enabled: true, chord: ['ble.astrolabe:fiveway.right'], match: 'exact', apps: [], action: co('navigation.set_fly'), system: true },
        { id: 'astrolabe.down.walk', label: 'Down: Walk', enabled: true, chord: ['ble.astrolabe:fiveway.down'], match: 'exact', apps: [], action: co('navigation.set_walk'), system: true },
        { id: 'astrolabe.left.secondary', label: 'Left: Secondary', enabled: true, chord: ['ble.astrolabe:fiveway.left'], match: 'exact', apps: [], action: co('navigation.hold_secondary'), system: true },
        { id: 'xiao.left.pointer_button', label: 'XIAO Left', enabled: true, chord: ['ble.xiao3389:button.left'], match: 'exact', apps: [], action: co('pointer.left'), system: true },
        { id: 'xiao.right.pointer_button', label: 'XIAO Right', enabled: true, chord: ['ble.xiao3389:button.right'], match: 'exact', apps: [], action: co('pointer.right'), system: true },
        { id: 'xiao.middle.pointer_button', label: 'XIAO Middle', enabled: true, chord: ['ble.xiao3389:button.middle'], match: 'exact', apps: [], action: co('pointer.middle'), system: true },
        { id: 'user.binding.1', label: 'Alt: Turntable (Blender)', enabled: false, chord: ['keyboard:alt'], match: 'allow', apps: ['blender'], action: { kind: 'setting', target: 'orbit.style', op: 'hold', value: 'turntable' }, system: false },
      ],
    },
    keyboard_only: {
      label: 'Keyboard only',
      bindings: [
        { id: 'keyboard.ctrl.3d', label: 'Ctrl: 3D', enabled: true, chord: ['keyboard:ctrl'], match: 'allow', apps: [], action: co('input.hold_3d'), system: true },
        { id: 'keyboard.shift.pan', label: 'Shift: Pan / Zoom', enabled: true, chord: ['keyboard:shift'], match: 'allow', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'keyboard.ctrl_shift.pan', label: 'Ctrl + Shift: Pan', enabled: true, chord: ['keyboard:ctrl', 'keyboard:shift'], match: 'exact', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'keyboard.f12.toggle_mode', label: 'F12: Toggle mode', enabled: true, chord: ['keyboard:f12'], match: 'exact', apps: [], action: co('input.toggle'), system: true },
      ],
    },
  };
}

const PROVIDER_HEALTH = {
  keyboard: { label: 'Keyboard', status: 'present', chip: 'good' },
  'ble.astrolabe': { label: 'Astrolabe', status: 'present', chip: 'good' },
  'ble.xiao3389': { label: 'XIAO', status: 'missing', chip: 'warn' },
};

/* ---------------------------------------------------------------- state */
const S = {
  page: 'hosts',
  mode: 'cube',
  host: 'blender',
  section: 'feel',
  routeMode: 'orbit',
  installOpen: false,
  fbSel: -1,
  kbProfile: 'astrolabe_5way',
  kbSel: null,
  kb: seedProfiles(),
  global: {},
  globalRoute: freshRich(),
  fallbacks: ['cursor_3d', 'camera', 'object', 'origin'],
  device: { name: 'Trackball BLE', address: '', bridge: 120 },
  orientation: { source: [0, 1, 2], invert: [false, false, false] },
  startAtLogin: false,
  firstRun: true,
  apps: {},
};
const DEFAULT_ENABLED = {
  blender: true, freecad: true, sketchup: true, unreal: true, unity: false,
  godot: false, rhino: true, fusion360: true, solidworks: true, onshape: false, autocad: false,
};
for (const key of APP_ORDER) {
  S.apps[key] = { enabled: DEFAULT_ENABLED[key], mode: 'orbit', ov: {}, route: freshRoute(key) };
}

/* --------------------------------------------------------- value access */
function gval(id) { return id in S.global ? S.global[id] : SYS[id]; }
function gDiverged(id) { return id in S.global && S.global[id] !== SYS[id]; }
function appLinked(key, id) { return !(id in S.apps[key].ov); }
function appEff(key, id) {
  if (id in S.apps[key].ov) return S.apps[key].ov[id];
  const as = APP_SYS[key];
  return as && id in as ? as[id] : gval(id);
}
function appSysDefault(key, id) {
  const as = APP_SYS[key];
  return as && id in as ? as[id] : SYS[id];
}
function appDiverged(key, id) { return appEff(key, id) !== appSysDefault(key, id); }

function setVal(key, id, val) {
  if (key === GLOBAL) {
    if (val === SYS[id]) delete S.global[id];
    else S.global[id] = val;
  } else {
    S.apps[key].ov[id] = val;
  }
  if (id === 'orbit.style' && val === 'free' && key !== GLOBAL) {
    const p = PROFILES[key];
    if (p.twist.includes('roll') && appEff(key, 'twist') !== 'roll') setVal(key, 'twist', 'roll');
  }
}
function linkSetting(key, id) {
  if (key === GLOBAL) return;
  delete S.apps[key].ov[id];
}
function resetSetting(key, id) {
  if (key === GLOBAL) delete S.global[id];
  else {
    S.apps[key].ov[id] = appSysDefault(key, id);
  }
}
function linkAll(key) {
  if (key === GLOBAL) return;
  S.apps[key].ov = {};
}
function resetGlobal() { S.global = {}; S.globalRoute = freshRich(); }

function routeStore(key) { return key === GLOBAL ? S.globalRoute : S.apps[key].route; }
function isRich(key) { return key === GLOBAL || PROFILES[key].rich; }

/* -------------------------------------------------------------- icons */
const ICO = {
  link: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 9.5a3 3 0 0 0 4.2.2l1.8-1.8a3 3 0 0 0-4.2-4.2L7.5 4.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M9.5 6.5a3 3 0 0 0-4.2-.2L3.5 8.1a3 3 0 0 0 4.2 4.2l.8-.8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  unlink: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 9.5a3 3 0 0 0 3.5.9M9.5 6.5a3 3 0 0 0-3.5-.9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M3 3l10 10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M10.8 4.2l1.2-1.2a3 3 0 0 1 4.2 4.2l-1.5 1.5M5.2 11.8L4 13a3 3 0 1 1-4.2-4.2l1.5-1.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  reset: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8a4.5 4.5 0 1 0 1.2-3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M3 3.5v3.2h3.2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 1600);
}

/* --------------------------------------------- sliding segment thumbs */
function placeThumb(segEl, animate = false) {
  const thumb = $('.seg-thumb', segEl);
  const on = $('button.on', segEl);
  if (!thumb || !on) return;
  if (!animate) thumb.style.transition = 'none';
  thumb.style.width = `${on.offsetWidth}px`;
  thumb.style.transform = `translateX(${on.offsetLeft}px)`;
  if (!animate) {
    void thumb.offsetWidth;
    thumb.style.transition = '';
  }
}
function initSegThumbs(scope = document) {
  $$('.seg', scope).forEach(s => placeThumb(s, false));
}
function selectSegButton(segEl, btn, animate = true) {
  if (!segEl || !btn) return;
  $$(':scope > button', segEl).forEach(b => b.classList.toggle('on', b === btn));
  placeThumb(segEl, animate);
}

function installNeedsAttention(key) {
  if (key === GLOBAL) return false;
  const a = APPS_BY_KEY[key];
  if (!a) return false;
  if (a.status.chip === 'warn' || a.status.chip === 'bad' || a.status.chip === 'off') return true;
  if (/not set up|update/i.test(a.status.text || '')) return true;
  if (a.action && /set up|update/i.test(a.action)) return true;
  return false;
}

function updateRowLinkState(row, key, id) {
  if (!row || key === GLOBAL) return;
  const linked = appLinked(key, id);
  row.classList.toggle('is-linked', linked);
  row.classList.toggle('is-override', !linked);
  const linkBtn = row.querySelector('[data-link]');
  if (linkBtn) {
    linkBtn.classList.toggle('is-broken', !linked);
    linkBtn.title = linked ? 'Linked to Global — click to override' : 'Override — click to relink';
    linkBtn.innerHTML = linked ? ICO.link : ICO.unlink;
  }
  const resetBtn = row.querySelector('[data-reset]');
  if (resetBtn) resetBtn.disabled = !appDiverged(key, id);
}

/* -------------------------------------------------------------- controls */
function ctrlHtml(key, id, val) {
  const m = SETTINGS[id];
  if (m.kind === 'bool') {
    return `<label class="check"><input type="checkbox" data-set="${esc(id)}" ${val ? 'checked' : ''}> ${val ? 'On' : 'Off'}</label>`;
  }
  if (m.kind === 'enum') {
    const choices = m.choices({ key });
    const lab = m.labeler || OPTION_LABELS;
    return `<select data-set="${esc(id)}">${choices.map(c =>
      `<option value="${esc(c)}" ${c === val ? 'selected' : ''}>${esc(lab[c] || c)}</option>`).join('')}</select>`;
  }
  if (m.kind === 'rate') {
    const opts = [15, 30, 60, 120];
    const custom = !opts.includes(val);
    return `<select data-set="${esc(id)}">${opts.map(n =>
      `<option value="${n}" ${n === val ? 'selected' : ''}>${n}</option>`).join('')}
      <option value="${esc(val)}" ${custom ? 'selected' : ''} ${custom ? '' : 'hidden'}>${esc(val)}</option></select>`;
  }
  return `<input type="number" step="any" data-set="${esc(id)}" value="${esc(fmt(val))}">`;
}

function rowHtml(key, id) {
  const m = SETTINGS[id];
  const isG = key === GLOBAL;
  const linked = isG ? true : appLinked(key, id);
  const val = isG ? gval(id) : appEff(key, id);
  const showReset = isG ? gDiverged(id) : appDiverged(key, id);
  const tip = m.tip ? `<span class="tip" title="${esc(m.tip)}">i</span>` : '';
  const linkBtn = isG ? '' : `<button type="button" class="op ${linked ? '' : 'is-broken'}" data-link="${esc(id)}" title="${linked ? 'Linked to Global — click to override' : 'Override — click to relink'}">${linked ? ICO.link : ICO.unlink}</button>`;
  const resetBtn = `<button type="button" class="op" data-reset="${esc(id)}" title="Reset to System" ${showReset ? '' : 'disabled'}>${ICO.reset}</button>`;
  return `<div class="row ${linked && !isG ? 'is-linked' : ''} ${!linked ? 'is-override' : ''}" data-row="${esc(id)}">
    <div class="row-lab">${esc(m.label)}${tip}</div>
    <div class="row-ctrl">${ctrlHtml(key, id, val)}</div>
    <div class="row-ops">${linkBtn}${resetBtn}</div>
    <div class="row-hint">${esc(m.hint)}</div>
  </div>`;
}

function settingsFor(key, section) {
  return Object.keys(SETTINGS).filter(id => {
    if (SETTINGS[id].section !== section) return false;
    if (key === GLOBAL) {
      if (SETTINGS[id].rich) return true;
      if (id === 'dyn.clip' || id === 'pivot.ext' || id === 'cam.lock') return false;
      return true;
    }
    return appliesToApp(id, key);
  });
}

/* -------------------------------------------------------------- host list */
function hostListHtml() {
  const gActive = S.host === GLOBAL ? 'is-active' : '';
  let html = `<button type="button" class="host-row is-global ${gActive}" data-host="${GLOBAL}">
    <span class="host-dot good" title="Baseline"></span>
    <span class="host-name">Global defaults</span>
    <span class="host-meta">base</span>
  </button>`;
  for (const a of APPS) {
    const st = S.apps[a.key];
    const active = S.host === a.key ? 'is-active' : '';
    const letter = { orbit: 'O', fly: 'F', walk: 'W' }[st.mode] || 'O';
    const chip = a.status.chip;
    const badge = st.enabled
      ? `<span class="host-dot mode ${esc(chip)}" title="${esc(st.mode)} · ${esc(a.status.text)}">${letter}</span>`
      : `<span class="host-dot ${esc(chip)}" title="${esc(a.status.text)}"></span>`;
    html += `<button type="button" class="host-row ${active}" data-host="${esc(a.key)}">
      ${badge}
      <span class="host-name">${esc(a.name)}</span>
      <span class="host-meta">${esc(a.status.short)}</span>
    </button>`;
  }
  html += `<div class="host-legend" aria-label="Status key">
    <div class="leg-row"><span class="host-dot good"></span> Connected</div>
    <div class="leg-row"><span class="host-dot warn"></span> Update / caution</div>
    <div class="leg-row"><span class="host-dot bad"></span> Unsupported / error</div>
    <div class="leg-row"><span class="host-dot idle"></span> Installed · idle</div>
    <div class="leg-row"><span class="host-dot off"></span> Not set up / off</div>
    <div class="leg-row"><span class="host-dot mode good">O</span> Letter = enabled (O/F/W)</div>
  </div>`;
  return html;
}

function modeSliderHtml(key) {
  if (key === GLOBAL) return '';
  const rich = PROFILES[key].rich;
  const modes = rich ? ['off', 'orbit', 'fly', 'walk'] : ['off', 'orbit'];
  const cur = S.apps[key].enabled ? S.apps[key].mode : 'off';
  const n = modes.length;
  const labels = { off: 'Off', orbit: 'Orbit', fly: 'Fly', walk: 'Walk' };
  return `<div class="seg mode-slider" data-n="${n}" data-mode="${esc(cur)}" data-host-mode="${esc(key)}">
    <span class="seg-thumb"></span>
    ${modes.map(m => `<button type="button" data-mmode="${m}" class="${m === cur ? 'on' : ''}">${labels[m]}</button>`).join('')}
  </div>`;
}

function installHtml(key) {
  if (key === GLOBAL) {
    return `<div class="section" id="sec-install">
      <div class="section-h"><h3>Baseline</h3><span class="hint">Inherited by every linked app</span></div>
      <p style="font-size:11px;color:var(--ink-2);margin-bottom:6px">Feel, Behavior, and Axes are the defaults every host inherits until a control is unlinked. Reset restores System (device identity unchanged).</p>
      <button type="button" class="btn" id="btn-reset-global">Reset Global → System</button>
    </div>`;
  }
  const a = APPS_BY_KEY[key];
  const alert = a.alert ? `<div class="alert ${a.alert.level}">${esc(a.alert.text)}</div>` : '';
  const cta = a.action ? `<button type="button" class="btn primary" data-setup="${esc(key)}">${esc(a.action)}</button>` : '';
  const us = PROFILES[key].userscript
    ? `<button type="button" class="btn tiny" data-userscript="${esc(key)}">Copy userscript…</button>` : '';
  return `<div class="section" id="sec-install">
    <div class="section-h"><h3>Install</h3><span class="hint">${esc(a.versions)}</span></div>
    <div class="install-bar">
      <div class="install-meta">
        <div><span class="mono">${esc(a.detected)}</span> · ${esc(a.status.text)}</div>
        <div style="margin-top:3px">${esc(a.installModel)}</div>
        <div style="margin-top:3px;font-size:10.5px;color:var(--ink-3)">${esc(a.security)}</div>
        ${alert}
      </div>
      <div class="install-cta">${cta}${us}</div>
    </div>
    <button type="button" class="inst-toggle" id="inst-toggle">${S.installOpen ? 'Hide instructions ▴' : 'Instructions ▾'}</button>
    ${S.installOpen ? `<div class="inst-body">
      <p><b>Auto</b> — ${esc(a.instructions.auto)}</p>
      <p><b>Manual</b> — ${esc(a.instructions.manual)}</p>
      <p><b>Health</b> — ${esc(a.instructions.health)}</p>
    </div>` : ''}
  </div>`;
}

function matrixHtml(key) {
  const rich = isRich(key);
  const store = routeStore(key);
  if (!rich) {
    return `<table class="matrix"><thead><tr><th>Action</th><th>Source</th><th>Inv</th><th></th></tr></thead><tbody>
      ${LEAN_ACTIONS.map(([lab, act]) => {
        const src = store.src[act] ?? 0;
        const inv = !!store.inv[act];
        return `<tr>
          <td>${esc(lab)}</td>
          <td><select data-rsrc="${esc(act)}">${AXIS.map((ax, i) =>
            `<option value="${i}" ${i === src ? 'selected' : ''}>${ax}</option>`).join('')}</select></td>
          <td><button type="button" class="inv ${inv ? 'on' : ''}" data-rinv="${esc(act)}" title="Invert">−</button></td>
          <td><span class="ax ${AXIS[src].toLowerCase()}">${AXIS[src]}</span></td>
        </tr>`;
      }).join('')}
    </tbody></table>`;
  }
  const modes = Object.keys(ROUTES_BY_MODE);
  const mode = modes.includes(S.routeMode) ? S.routeMode : 'orbit';
  S.routeMode = mode;
  const rows = ROUTES_BY_MODE[mode];
  return `<div class="route-modes">${modes.map(m =>
    `<button type="button" data-rmode="${m}" class="${m === mode ? 'is-active' : ''}">${m}</button>`).join('')}</div>
    <table class="matrix"><thead><tr><th>Action</th><th>Source</th><th>Inv</th><th></th></tr></thead><tbody>
      ${rows.map(([lab, act]) => {
        const k = `${mode}.${act}`;
        const src = store.src[k] ?? 0;
        const inv = !!store.inv[k];
        return `<tr>
          <td>${esc(lab)}</td>
          <td><select data-rsrc="${esc(k)}">${AXIS.map((ax, i) =>
            `<option value="${i}" ${i === src ? 'selected' : ''}>${ax}</option>`).join('')}</select></td>
          <td><button type="button" class="inv ${inv ? 'on' : ''}" data-rinv="${esc(k)}" title="Invert">−</button></td>
          <td><span class="ax ${AXIS[src].toLowerCase()}">${AXIS[src]}</span></td>
        </tr>`;
      }).join('')}
    </tbody></table>`;
}

function sectionBodyHtml(key) {
  const feelRows = settingsFor(key, 'feel').map(id => rowHtml(key, id)).join('');
  const behRows = settingsFor(key, 'behavior').map(id => rowHtml(key, id)).join('');
  const sec = S.section;
  if (sec === 'install') return installHtml(key);
  if (sec === 'feel') {
    return `<div class="section" id="sec-feel">
      <div class="section-h"><h3>Feel</h3><span class="hint">Rates, gains, holds</span></div>
      <div class="rows">${feelRows || '<p class="kb-empty">No feel settings for this host.</p>'}</div>
    </div>`;
  }
  if (sec === 'behavior') {
    return `<div class="section" id="sec-behavior">
      <div class="section-h"><h3>Behavior</h3><span class="hint">Orbit, zoom, host extras</span></div>
      <div class="rows">${behRows || '<p class="kb-empty">No behavior settings for this host.</p>'}</div>
    </div>`;
  }
  return `<div class="section" id="sec-axes">
    <div class="section-h"><h3>Axes</h3><span class="hint">Source × invert matrix</span></div>
    <div id="route-matrix">${matrixHtml(key)}</div>
  </div>`;
}

function detailHtml() {
  const key = S.host;
  const name = key === GLOBAL ? 'Global defaults' : APPS_BY_KEY[key].name;
  const sub = key === GLOBAL
    ? 'Baseline every app inherits'
    : (S.apps[key].enabled ? `${S.apps[key].mode} · overrides Global when unlinked` : 'Disabled');
  const chips = ['install', 'feel', 'behavior', 'axes'];
  const chipLabels = { install: 'Install', feel: 'Feel', behavior: 'Behavior', axes: 'Axes' };
  if (key === GLOBAL) chipLabels.install = 'About';
  const attn = installNeedsAttention(key);
  const attnCls = APPS_BY_KEY[key]?.status?.chip === 'bad' ? 'bad' : '';

  return `
    <div class="detail-head">
      <div class="detail-title">${esc(name)}<span class="sub" id="detail-sub">${esc(sub)}</span></div>
      <div class="detail-actions">
        ${modeSliderHtml(key)}
        ${key !== GLOBAL ? `<button type="button" class="icon-btn" id="btn-link-all" title="Relink all to Global">${ICO.link}</button>` : ''}
      </div>
    </div>
    <div class="seg sec-chips" id="sec-chips">
      <span class="seg-thumb"></span>
      ${chips.map(c => {
        const mark = (c === 'install' && attn)
          ? `<span class="chip-attn ${attnCls}" title="Setup action needed"></span>` : '';
        return `<button type="button" data-sec="${c}" class="${S.section === c ? 'on' : ''}">${chipLabels[c]}${mark}</button>`;
      }).join('')}
    </div>
    <div class="detail-scroll" id="detail-scroll">
      <div class="pane-fade" id="section-pane">${sectionBodyHtml(key)}</div>
    </div>`;
}

function renderHosts() {
  return `<div class="page" data-page="hosts">
    <div class="page-head">
      <div class="page-title">Hosts</div>
      <div class="page-sub">Global baseline + per-app install / feel / behavior / axes</div>
    </div>
    <div class="split">
      <div class="host-list" id="host-list">${hostListHtml()}</div>
      <div class="detail" id="host-detail">${detailHtml()}</div>
    </div>
  </div>`;
}

/* ------------------------------------------------------------ bindings */
function actionSelectHtml(b) {
  const cur = b.action.kind === 'common'
    ? `common:${b.action.target}`
    : `setting:${b.action.target}`;
  let opts = '';
  for (const [gname, acts] of ACTION_GROUPS) {
    opts += `<optgroup label="${esc(gname)}">${acts.map(([id, lab]) =>
      `<option value="common:${esc(id)}" ${cur === `common:${id}` ? 'selected' : ''}>${esc(lab)}</option>`).join('')}</optgroup>`;
  }
  opts += `<optgroup label="Settings">${KB_SETTINGS.map(([id, lab]) =>
    `<option value="setting:${esc(id)}" ${cur === `setting:${id}` ? 'selected' : ''}>${esc(lab)}</option>`).join('')}</optgroup>`;
  return `<select class="action-groups" data-kb="action">${opts}</select>`;
}

function settingOpsHtml(b) {
  if (b.action.kind !== 'setting') return '';
  const meta = KB_SETTING_META[b.action.target];
  if (!meta) return '';
  const ops = meta.kind === 'bool'
    ? [['hold', 'Hold'], ['toggle', 'Toggle']]
    : meta.kind === 'enum'
      ? [['hold', 'Hold'], ['toggle', 'Toggle'], ['cycle', 'Cycle']]
      : [['hold', 'Hold'], ['add', 'Add'], ['multiply', 'Multiply']];
  const op = b.action.op || 'hold';
  let extra = '';
  if (op === 'hold' || op === 'toggle') {
    if (meta.kind === 'enum') {
      const vals = meta.ch || [];
      extra = `<select data-kb="value">${vals.map(v =>
        `<option value="${esc(v)}" ${b.action.value === v ? 'selected' : ''}>${esc(optLabel(v))}</option>`).join('')}</select>`;
    } else if (meta.kind === 'bool') {
      extra = `<select data-kb="value">
        <option value="true" ${b.action.value === true || b.action.value === 'true' ? 'selected' : ''}>On</option>
        <option value="false" ${b.action.value === false || b.action.value === 'false' ? 'selected' : ''}>Off</option>
      </select>`;
    } else {
      extra = `<input type="number" step="any" data-kb="value" value="${esc(fmt(b.action.value ?? 1))}">`;
    }
  } else if (op === 'add' || op === 'multiply') {
    extra = `<input type="number" step="any" data-kb="value" value="${esc(fmt(b.action.value ?? 1))}">`;
  }
  return `<select data-kb="op">${ops.map(([v, l]) =>
    `<option value="${v}" ${op === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
    ${extra ? `<div style="margin-top:4px">${extra}</div>` : ''}`;
}

function kbListHtml() {
  const bindings = S.kb[S.kbProfile].bindings;
  if (!bindings.length) return `<div class="kb-empty">No bindings.</div>`;
  return bindings.map(b => {
    const active = b.id === S.kbSel ? 'is-active' : '';
    const off = b.enabled ? '' : 'is-off';
    return `<button type="button" class="kb-item ${active} ${off}" data-kbid="${esc(b.id)}">
      <span class="lab">${esc(b.label)}</span>
      <span class="sys">${b.system ? 'sys' : 'user'}</span>
      <span class="ch">${b.chord.map(t => `<span class="chip">${esc(tokenLabel(t))}</span>`).join('')}</span>
    </button>`;
  }).join('');
}

function currentBinding() {
  return S.kb[S.kbProfile].bindings.find(b => b.id === S.kbSel) || null;
}

function kbEditorHtml() {
  const b = currentBinding();
  if (!b) return `<div class="kb-empty">Select or create a binding.</div>`;
  const pass = b.chord.some(t => t.startsWith('keyboard:') && !['keyboard:ctrl', 'keyboard:shift', 'keyboard:alt', 'keyboard:meta'].includes(t));
  return `<div class="form-grid">
    <div class="form-lab">Label</div>
    <input type="text" data-kb="label" value="${esc(b.label)}">
    <div class="form-lab">Enabled</div>
    <label class="check"><input type="checkbox" data-kb="enabled" ${b.enabled ? 'checked' : ''}> Active</label>
    <div class="form-lab">Chord</div>
    <div>
      <div class="chord-box" id="chord-box">
        ${b.chord.map(t => `<span class="chip">${esc(tokenLabel(t))}<button type="button" data-rm-token="${esc(t)}" title="Remove">×</button></span>`).join('')}
        <button type="button" class="btn tiny" id="btn-record">Record…</button>
        <select id="token-add" style="width:auto;height:22px">
          <option value="">Add…</option>
          ${TOKEN_GROUPS.map(([g, toks]) =>
            `<optgroup label="${esc(g)}">${toks.map(t =>
              `<option value="${esc(t)}">${esc(tokenLabel(t))}</option>`).join('')}</optgroup>`).join('')}
        </select>
      </div>
    </div>
    <div class="form-lab">Match</div>
    <div class="seg" id="match-seg">
      <span class="seg-thumb"></span>
      <button type="button" data-match="allow" class="${b.match === 'allow' ? 'on' : ''}">Allow extras</button>
      <button type="button" data-match="exact" class="${b.match === 'exact' ? 'on' : ''}">Exact</button>
    </div>
    <div class="form-lab">App</div>
    <select data-kb="apps">
      <option value="" ${!b.apps.length ? 'selected' : ''}>All applications</option>
      ${APPS.map(a => `<option value="${esc(a.key)}" ${b.apps[0] === a.key ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}
    </select>
    <div class="form-lab">Action</div>
    <div>${actionSelectHtml(b)}${b.action.kind === 'setting' ? `<div style="margin-top:4px">${settingOpsHtml(b)}</div>` : ''}</div>
    ${pass ? `<div class="warn-line">Non-modifier keys still reach the focused app (pass-through).</div>` : ''}
  </div>
  <div class="editor-foot">
    <button type="button" class="btn" id="btn-adv">Advanced DSL…</button>
    ${b.system
      ? `<button type="button" class="btn" id="btn-restore">Restore System</button>`
      : `<button type="button" class="btn" id="btn-del" style="color:var(--bad)">Delete</button>`}
    <button type="button" class="btn primary" id="btn-new-binding" style="margin-left:auto">New binding</button>
  </div>`;
}

function healthHtml() {
  const used = new Set();
  for (const b of S.kb[S.kbProfile].bindings) for (const t of b.chord) used.add(tokenSource(t));
  return Object.entries(PROVIDER_HEALTH).map(([id, h]) => {
    const dim = used.has(id) || id === 'keyboard' ? '' : ' style="opacity:.55"';
    return `<span${dim}><i class="d ${h.chip}"></i>${esc(h.label)} · ${esc(h.status)}</span>`;
  }).join('');
}

function renderBindings() {
  const profiles = Object.keys(S.kb);
  return `<div class="page" data-page="bindings">
    <div class="page-head">
      <div class="page-title">Bindings</div>
      <div class="page-sub">Profiles, chords, common actions &amp; setting ops</div>
    </div>
    <div class="kb-toolbar">
      <div class="seg" id="profile-seg">
        <span class="seg-thumb"></span>
        ${profiles.map(p =>
          `<button type="button" data-profile="${esc(p)}" class="${p === S.kbProfile ? 'on' : ''}">${esc(S.kb[p].label)}</button>`).join('')}
      </div>
      <div class="health">${healthHtml()}</div>
    </div>
    <div class="kb-split">
      <div class="kb-list" id="kb-list">${kbListHtml()}</div>
      <div class="kb-editor" id="kb-editor">${kbEditorHtml()}</div>
    </div>
  </div>`;
}

/* -------------------------------------------------------------- system */
function ballSvg() {
  return `<svg class="ball-mini" id="sys-ball" viewBox="0 0 200 160" aria-label="Axis orientation">
    <circle cx="100" cy="82" r="62" fill="none" stroke="rgba(26,35,48,0.18)"/>
    <circle cx="100" cy="82" r="44" fill="#fff" stroke="rgba(26,35,48,0.22)"/>
    <g class="ax ax-x" data-ax="0" style="color:var(--ax-x)">
      <line x1="52" y1="82" x2="148" y2="82" stroke="currentColor" stroke-width="1.6" marker-end="url(#mx)"/>
      <text x="156" y="86" font-size="12" font-family="var(--mono)" fill="currentColor" font-weight="700">X</text>
    </g>
    <g class="ax ax-y" data-ax="1" style="color:var(--ax-y)">
      <line x1="100" y1="128" x2="100" y2="36" stroke="currentColor" stroke-width="1.6"/>
      <text x="104" y="30" font-size="12" font-family="var(--mono)" fill="currentColor" font-weight="700">Y</text>
    </g>
    <g class="ax ax-z" data-ax="2" style="color:var(--ax-z)">
      <ellipse cx="100" cy="82" rx="28" ry="14" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <text x="132" y="70" font-size="12" font-family="var(--mono)" fill="currentColor" font-weight="700">Z</text>
    </g>
  </svg>`;
}

function fallbackHtml() {
  return `<div class="fb-list" id="fb-list">
    ${S.fallbacks.map((f, i) =>
      `<button type="button" class="fb-chip ${S.fbSel === i ? 'is-sel' : ''}" data-fb="${i}">${i + 1}. ${esc(PIVOT_LABELS[f] || f)}</button>`).join('')}
  </div>
  <div class="fb-ops">
    <button type="button" class="btn tiny" data-fb-op="up">Up</button>
    <button type="button" class="btn tiny" data-fb-op="down">Down</button>
    <button type="button" class="btn tiny" data-fb-op="add">Add</button>
    <button type="button" class="btn tiny" data-fb-op="rm">Remove</button>
  </div>`;
}

function renderSystem() {
  const o = S.orientation;
  const phys = ['pitch', 'yaw', 'twist'];
  return `<div class="page" data-page="system">
    <div class="page-head">
      <div class="page-title">System</div>
      <div class="page-sub">Device, input base, HUD, session</div>
    </div>
    <div class="sys-scroll">
      ${S.firstRun ? `<div class="first-run" id="first-run">
        <div>
          <b>First run</b>
          <ol>
            <li>Confirm device name under Device.</li>
            <li>Open Hosts → set up each CAD host you use.</li>
            <li>Pick a Bindings profile and try Ctrl for 3D.</li>
          </ol>
        </div>
        <button type="button" id="dismiss-fr" aria-label="Dismiss">×</button>
      </div>` : ''}
      <div class="sys-grid">
        <div class="sys-card">
          <h3>Device</h3>
          <div class="rows">
            <div class="row" style="grid-template-columns:72px 1fr">
              <div class="row-lab">Name</div>
              <input type="text" data-dev="name" value="${esc(S.device.name)}">
            </div>
            <div class="row" style="grid-template-columns:72px 1fr">
              <div class="row-lab">Address</div>
              <input type="text" data-dev="address" value="${esc(S.device.address)}" placeholder="auto">
            </div>
            <div class="row" style="grid-template-columns:72px 1fr">
              <div class="row-lab">Bridge Hz</div>
              <input type="number" data-dev="bridge" value="${esc(S.device.bridge)}">
            </div>
          </div>
        </div>
        <div class="sys-card">
          <h3>Pointer</h3>
          <div class="rows">
            <div class="row" style="grid-template-columns:96px 1fr">
              <div class="row-lab">Cursor gain</div>
              <input type="number" step="any" data-sys="pointer.gain" value="${esc(fmt(gval('pointer.gain')))}">
            </div>
            <div class="row" style="grid-template-columns:96px 1fr">
              <div class="row-lab">Scroll gain</div>
              <input type="number" step="any" data-sys="scroll.gain" value="${esc(fmt(gval('scroll.gain')))}">
            </div>
            <div class="row" style="grid-template-columns:96px 1fr">
              <div class="row-lab">Deadzone</div>
              <input type="number" step="any" data-sys="scroll.deadzone" value="${esc(fmt(gval('scroll.deadzone')))}">
            </div>
            <p style="font-size:10px;color:var(--ink-3);margin-top:4px">Buttons map under Bindings — no reserved table.</p>
          </div>
        </div>
        <div class="sys-card wide">
          <h3>Orientation</h3>
          <div class="orient">
            ${ballSvg()}
            <div>
              ${[0, 1, 2].map(i => `
                <div class="swap-row" data-orient="${i}">
                  <span class="ax-lab ${AXIS[i].toLowerCase()}">${AXIS[i]}</span>
                  <select data-osrc="${i}">${AXIS.map((ax, j) =>
                    `<option value="${j}" ${o.source[i] === j ? 'selected' : ''}>${ax} ← ${phys[j]}</option>`).join('')}</select>
                  <label class="check"><input type="checkbox" data-oinv="${i}" ${o.invert[i] ? 'checked' : ''}> Invert</label>
                </div>`).join('')}
              <p style="font-size:10px;color:var(--ink-3);margin-top:4px">Top view, front toward you. Sources stay a permutation.</p>
            </div>
          </div>
        </div>
        <div class="sys-card">
          <h3>3D defaults</h3>
          <div class="rows">
            <div class="row" style="grid-template-columns:96px 1fr">
              <div class="row-lab">Startup mode</div>
              <select data-sys="mode.default">
                <option value="3d" ${gval('mode.default') === '3d' ? 'selected' : ''}>3D navigation</option>
                <option value="pointer" ${gval('mode.default') === 'pointer' ? 'selected' : ''}>Pointer</option>
              </select>
            </div>
          </div>
          <p style="font-size:10.5px;color:var(--ink-2);margin:8px 0 4px">Pivot fallbacks</p>
          ${fallbackHtml()}
        </div>
        <div class="sys-card">
          <h3>Control panel</h3>
          <div class="rows">
            <label class="check"><input type="checkbox" data-sys="hud.visible" ${gval('hud.visible') ? 'checked' : ''}> Visible</label>
            <label class="check"><input type="checkbox" data-sys="hud.top" ${gval('hud.top') ? 'checked' : ''}> Always on top</label>
            <label class="check"><input type="checkbox" data-sys="hud.through" ${gval('hud.through') ? 'checked' : ''}> Click-through</label>
            <div class="row" style="grid-template-columns:72px 1fr auto;margin-top:4px;align-items:center">
              <div class="row-lab">Opacity</div>
              <input type="range" min="0.3" max="1" step="0.05" data-sys="hud.opacity" value="${esc(gval('hud.opacity'))}" aria-label="Control panel opacity">
              <span class="mono" id="op-val">${esc(fmt(gval('hud.opacity')))}</span>
            </div>
            <div class="row" style="grid-template-columns:96px 1fr;margin-top:4px">
              <div class="row-lab">Margin</div>
              <input type="number" data-sys="hud.margin" value="${esc(gval('hud.margin'))}">
            </div>
            <div class="row" style="grid-template-columns:96px 1fr">
              <div class="row-lab">Last bind (s)</div>
              <input type="number" step="any" data-sys="hud.timeout" value="${esc(fmt(gval('hud.timeout')))}">
            </div>
          </div>
        </div>
        <div class="sys-card wide">
          <h3>Session</h3>
          <div class="session-row">
            <label class="check"><input type="checkbox" id="login-check" ${S.startAtLogin ? 'checked' : ''}> Start at login</label>
            <button type="button" class="btn" id="btn-recenter">Recenter 3D view</button>
            <span class="mono" style="color:var(--ink-3);font-size:10px;margin-left:auto">127.0.0.1 · config.json</span>
          </div>
        </div>
      </div>
    </div>
  </div>`;
}

/* -------------------------------------------------------------- render */
function updateRail() {
  const buttons = $$('#rail-nav button');
  buttons.forEach(b => b.classList.toggle('is-active', b.dataset.page === S.page));
  const active = buttons.find(b => b.dataset.page === S.page);
  const tick = $('#rail-tick');
  if (active && tick) tick.style.top = `${active.offsetTop + 6}px`;
  document.body.dataset.mode = S.mode;
  const hs = $('#handshake .hs-text');
  if (hs) {
    const connected = APPS.find(a => a.status.chip === 'good');
    hs.textContent = connected ? `${connected.name} · linked` : 'No add-on';
  }
}

function render() {
  const view = $('#view');
  if (S.page === 'hosts') view.innerHTML = renderHosts();
  else if (S.page === 'bindings') view.innerHTML = renderBindings();
  else view.innerHTML = renderSystem();
  updateRail();
  syncHash();
  // Measure after layout so sliding thumbs land on the active button.
  requestAnimationFrame(() => {
    initSegThumbs(document);
    const modeSeg = $('#mode-toggle');
    if (modeSeg) {
      $$(':scope > button', modeSeg).forEach(b => b.classList.toggle('on', b.dataset.mode === S.mode));
      placeThumb(modeSeg, false);
    }
  });
}

function refreshHostList() {
  const el = $('#host-list');
  if (el) el.innerHTML = hostListHtml();
}
function refreshDetail() {
  const el = $('#host-detail');
  if (el) {
    el.innerHTML = detailHtml();
    requestAnimationFrame(() => initSegThumbs(el));
  }
}
function refreshKb() {
  const list = $('#kb-list');
  const ed = $('#kb-editor');
  if (list) list.innerHTML = kbListHtml();
  if (ed) ed.innerHTML = kbEditorHtml();
  requestAnimationFrame(() => initSegThumbs($('.page') || document));
}

function swapSection(sec, animate = true) {
  S.section = sec;
  const chips = $('#sec-chips');
  const btn = chips && $(`button[data-sec="${sec}"]`, chips);
  if (chips && btn) selectSegButton(chips, btn, animate);
  const pane = $('#section-pane');
  if (!pane) { refreshDetail(); syncHash(); return; }
  const apply = () => {
    pane.innerHTML = sectionBodyHtml(S.host);
    pane.classList.remove('is-exit');
    if (animate) {
      pane.classList.add('is-enter');
      requestAnimationFrame(() => {
        requestAnimationFrame(() => pane.classList.remove('is-enter'));
      });
    }
    syncHash();
  };
  if (!animate) { apply(); return; }
  pane.classList.add('is-exit');
  setTimeout(apply, 160);
}

/* --------------------------------------------------------------- hash */
function syncHash() {
  let h = `#${S.page}`;
  if (S.page === 'hosts') {
    const host = S.host === GLOBAL ? 'global' : S.host;
    h = `#hosts:${host}.${S.section}`;
  } else if (S.page === 'bindings') {
    h = `#bindings:${S.kbProfile}`;
  }
  if (location.hash !== h) history.replaceState(null, '', h);
}

function parseHash() {
  const raw = (location.hash || '#hosts').slice(1);
  const [page, rest] = raw.split(':');
  if (page === 'hosts') {
    S.page = 'hosts';
    if (rest) {
      const [host, sec] = rest.split('.');
      S.host = host === 'global' ? GLOBAL : (APPS_BY_KEY[host] ? host : S.host);
      if (sec && ['install', 'feel', 'behavior', 'axes'].includes(sec)) S.section = sec;
    }
  } else if (page === 'bindings') {
    S.page = 'bindings';
    if (rest && S.kb[rest]) S.kbProfile = rest;
  } else if (page === 'system') {
    S.page = 'system';
  }
}

/* -------------------------------------------------------------- modal */
function openModal(title, bodyHtml, onOk) {
  const dlg = $('#modal');
  dlg.innerHTML = `<div class="modal-body"><h2>${esc(title)}</h2>${bodyHtml}</div>
    <div class="modal-foot">
      <button type="button" class="btn ghost" data-close>Cancel</button>
      <button type="button" class="btn primary" data-ok>Save</button>
    </div>`;
  dlg.showModal();
  dlg.querySelector('[data-close]').onclick = () => dlg.close();
  dlg.querySelector('[data-ok]').onclick = () => { onOk?.(dlg); dlg.close(); };
}

/* ------------------------------------------------------------- events */
function bindShell() {
  $('#rail-nav').addEventListener('click', e => {
    const btn = e.target.closest('button[data-page]');
    if (!btn) return;
    S.page = btn.dataset.page;
    render();
  });
  $('#mode-toggle').addEventListener('click', e => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn) return;
    S.mode = btn.dataset.mode;
    document.body.dataset.mode = S.mode;
    selectSegButton($('#mode-toggle'), btn, true);
    toast(S.mode === 'cube' ? '3D navigation' : 'Pointer mode');
  });
  $('#btn-hide').onclick = () => toast('Hidden to tray — daemon keeps running');
  $('#btn-quit').onclick = () => toast('Quit would stop the daemon');
  window.addEventListener('hashchange', () => { parseHash(); render(); });
}

function onViewClick(e) {
  const t = e.target;

  const hostBtn = t.closest('[data-host]');
  if (hostBtn && t.closest('#host-list')) {
    S.host = hostBtn.dataset.host;
    S.installOpen = false;
    S.section = 'feel';
    refreshHostList();
    refreshDetail();
    syncHash();
    return;
  }

  const sec = t.closest('[data-sec]');
  if (sec) {
    if (sec.dataset.sec !== S.section) swapSection(sec.dataset.sec, true);
    return;
  }

  const mm = t.closest('[data-mmode]');
  if (mm) {
    const slider = mm.closest('[data-host-mode]');
    const key = slider.dataset.hostMode;
    const m = mm.dataset.mmode;
    if (m === 'off') S.apps[key].enabled = false;
    else { S.apps[key].enabled = true; S.apps[key].mode = m; }
    slider.dataset.mode = m;
    selectSegButton(slider, mm, true);
    refreshHostList();
    const sub = $('#detail-sub');
    if (sub) {
      sub.textContent = S.apps[key].enabled
        ? `${S.apps[key].mode} · overrides Global when unlinked`
        : 'Disabled';
    }
    return;
  }

  if (t.id === 'btn-link-all') { linkAll(S.host); refreshDetail(); toast('Relinked to Global'); return; }
  if (t.id === 'btn-reset-global') { resetGlobal(); refreshDetail(); toast('Global reset to System'); return; }
  if (t.id === 'inst-toggle') {
    S.installOpen = !S.installOpen;
    refreshDetail();
    return;
  }

  const setup = t.closest('[data-setup]');
  if (setup) { toast(`${APPS_BY_KEY[setup.dataset.setup].name}: ${setup.textContent.trim()} (demo)`); return; }
  const us = t.closest('[data-userscript]');
  if (us) { toast('Userscript copied (demo)'); return; }

  const link = t.closest('[data-link]');
  if (link) {
    const id = link.dataset.link;
    if (appLinked(S.host, id)) setVal(S.host, id, appEff(S.host, id));
    else linkSetting(S.host, id);
    refreshDetail();
    return;
  }
  const reset = t.closest('[data-reset]');
  if (reset && !reset.disabled) {
    resetSetting(S.host === GLOBAL ? GLOBAL : S.host, reset.dataset.reset);
    refreshDetail();
    return;
  }

  const rmode = t.closest('[data-rmode]');
  if (rmode) {
    S.routeMode = rmode.dataset.rmode;
    const box = $('#route-matrix');
    if (box) box.innerHTML = matrixHtml(S.host);
    return;
  }
  const rinv = t.closest('[data-rinv]');
  if (rinv) {
    const store = routeStore(S.host);
    store.inv[rinv.dataset.rinv] = !store.inv[rinv.dataset.rinv];
    const box = $('#route-matrix');
    if (box) box.innerHTML = matrixHtml(S.host);
    return;
  }

  /* bindings */
  const profile = t.closest('[data-profile]');
  if (profile) {
    S.kbProfile = profile.dataset.profile;
    S.kbSel = S.kb[S.kbProfile].bindings[0]?.id || null;
    render();
    return;
  }
  const kbi = t.closest('[data-kbid]');
  if (kbi) { S.kbSel = kbi.dataset.kbid; refreshKb(); return; }
  const match = t.closest('[data-match]');
  if (match) {
    const b = currentBinding();
    if (b) {
      b.match = match.dataset.match;
      selectSegButton(match.closest('.seg'), match, true);
    }
    return;
  }
  const rmTok = t.closest('[data-rm-token]');
  if (rmTok) {
    const b = currentBinding();
    if (b) { b.chord = b.chord.filter(x => x !== rmTok.dataset.rmToken); refreshKb(); }
    return;
  }
  if (t.id === 'btn-record') {
    const b = currentBinding();
    if (!b) return;
    openModal('Record chord', `<p style="font-size:11px;color:var(--ink-2)">Demo: press keys or click a token below.</p>
      <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px" id="rec-tokens">
        ${TOKEN_GROUPS.flatMap(([, toks]) => toks).map(tok =>
          `<button type="button" class="chip" data-rec="${esc(tok)}" style="cursor:pointer">${esc(tokenLabel(tok))}</button>`).join('')}
      </div>`, dlg => {
      const picked = [...dlg.querySelectorAll('[data-rec].is-on')].map(el => el.dataset.rec);
      if (picked.length) { b.chord = picked; refreshKb(); }
    });
    const dlg = $('#modal');
    dlg.addEventListener('click', ev => {
      const chip = ev.target.closest('[data-rec]');
      if (chip) chip.classList.toggle('is-on');
    });
    return;
  }
  if (t.id === 'btn-new-binding') {
    const arr = S.kb[S.kbProfile].bindings;
    let n = 1; while (arr.some(b => b.id === `user.binding.${n}`)) n++;
    const nb = { id: `user.binding.${n}`, label: `Custom ${n}`, enabled: true, chord: ['keyboard:alt'],
      match: 'allow', apps: [], action: { kind: 'common', target: 'input.toggle' }, system: false };
    arr.push(nb); S.kbSel = nb.id; refreshKb();
    return;
  }
  if (t.id === 'btn-del') {
    const b = currentBinding();
    if (!b || b.system) return;
    const arr = S.kb[S.kbProfile].bindings;
    arr.splice(arr.indexOf(b), 1);
    S.kbSel = arr[0]?.id || null;
    refreshKb();
    return;
  }
  if (t.id === 'btn-restore') {
    const b = currentBinding();
    const seed = seedProfiles()[S.kbProfile].bindings.find(x => x.id === b?.id);
    if (b && seed) {
      Object.assign(b, JSON.parse(JSON.stringify(seed)));
      refreshKb();
      toast('Restored System binding');
    }
    return;
  }
  if (t.id === 'btn-adv') {
    const b = currentBinding();
    if (!b) return;
    openModal('Advanced DSL', `<textarea id="adv-json">${esc(JSON.stringify(b, null, 2))}</textarea>
      <p style="font-size:10.5px;color:var(--ink-3);margin-top:6px">Full binding JSON — priority, multi-app when, activation latch, custom press/release.</p>`, dlg => {
      try {
        const parsed = JSON.parse(dlg.querySelector('#adv-json').value);
        const arr = S.kb[S.kbProfile].bindings;
        const i = arr.findIndex(x => x.id === b.id);
        if (i >= 0) { arr[i] = parsed; S.kbSel = parsed.id; refreshKb(); }
      } catch {
        toast('Invalid JSON');
      }
    });
    return;
  }

  /* system */
  if (t.id === 'dismiss-fr') { S.firstRun = false; render(); return; }
  if (t.id === 'btn-recenter') { toast('Recenter sent (demo)'); return; }

  const fb = t.closest('[data-fb]');
  if (fb) {
    S.fbSel = +fb.dataset.fb;
    $$('.fb-chip').forEach(c => c.classList.toggle('is-sel', +c.dataset.fb === S.fbSel));
    return;
  }
  const fbOp = t.closest('[data-fb-op]');
  if (fbOp) {
    const op = fbOp.dataset.fbOp;
    const a = S.fallbacks;
    if (op === 'up' && S.fbSel > 0) {
      [a[S.fbSel - 1], a[S.fbSel]] = [a[S.fbSel], a[S.fbSel - 1]];
      S.fbSel--;
    } else if (op === 'down' && S.fbSel >= 0 && S.fbSel < a.length - 1) {
      [a[S.fbSel + 1], a[S.fbSel]] = [a[S.fbSel], a[S.fbSel + 1]];
      S.fbSel++;
    } else if (op === 'rm' && S.fbSel >= 0) {
      a.splice(S.fbSel, 1);
      S.fbSel = Math.min(S.fbSel, a.length - 1);
    } else if (op === 'add') {
      const unused = ORBIT_PIVOT_METHODS.find(p => !a.includes(p));
      if (unused) { a.push(unused); S.fbSel = a.length - 1; }
    }
    render();
  }
}

function onViewChange(e) {
  const t = e.target;

  if (t.matches('[data-set]')) {
    const id = t.dataset.set;
    let val;
    if (t.type === 'checkbox') val = t.checked;
    else if (SETTINGS[id].kind === 'num' || SETTINGS[id].kind === 'rate') val = parseFloat(t.value);
    else val = t.value;
    // Editing always writes an app override — that breaks the Global link.
    setVal(S.host, id, val);
    const row = t.closest('[data-row]');
    if (row) {
      updateRowLinkState(row, S.host, id);
      if (SETTINGS[id].kind === 'bool') {
        const lab = t.closest('.check');
        if (lab) {
          const text = lab.querySelector('.check-lab') || lab;
          // Keep the On/Off label next to the checkbox.
          const nodes = [...lab.childNodes].filter(n => n.nodeType === 3);
          nodes.forEach(n => { n.textContent = ` ${val ? 'On' : 'Off'}`; });
        }
      }
    }
    return;
  }

  if (t.matches('[data-rsrc]')) {
    const store = routeStore(S.host);
    store.src[t.dataset.rsrc] = +t.value;
    const box = $('#route-matrix');
    if (box) box.innerHTML = matrixHtml(S.host);
    return;
  }

  if (t.id === 'token-add' && t.value) {
    const b = currentBinding();
    if (b && !b.chord.includes(t.value)) { b.chord.push(t.value); refreshKb(); }
    return;
  }

  if (t.matches('[data-kb]')) {
    const b = currentBinding();
    if (!b) return;
    const k = t.dataset.kb;
    if (k === 'label') b.label = t.value;
    else if (k === 'enabled') { b.enabled = t.checked; refreshKb(); }
    else if (k === 'apps') b.apps = t.value ? [t.value] : [];
    else if (k === 'action') {
      const [kind, target] = t.value.split(':');
      if (kind === 'common') b.action = { kind: 'common', target };
      else b.action = { kind: 'setting', target, op: 'hold', value: KB_SETTING_META[target]?.kind === 'bool' ? true : (KB_SETTING_META[target]?.ch?.[0] ?? 1) };
      refreshKb();
    } else if (k === 'op') { b.action.op = t.value; refreshKb(); }
    else if (k === 'value') {
      const meta = KB_SETTING_META[b.action.target];
      if (meta?.kind === 'bool') b.action.value = t.value === 'true';
      else if (meta?.kind === 'num') b.action.value = parseFloat(t.value);
      else b.action.value = t.value;
    }
    return;
  }

  if (t.matches('[data-dev]')) {
    S.device[t.dataset.dev] = t.type === 'number' ? parseFloat(t.value) : t.value;
    return;
  }
  if (t.matches('[data-sys]')) {
    const id = t.dataset.sys;
    let val = t.type === 'checkbox' ? t.checked : (t.type === 'range' || t.type === 'number' ? parseFloat(t.value) : t.value);
    if (val === SYS[id]) delete S.global[id];
    else S.global[id] = val;
    if (id === 'hud.opacity') {
      const ov = $('#op-val');
      if (ov) ov.textContent = fmt(val);
    }
    return;
  }
  if (t.id === 'login-check') { S.startAtLogin = t.checked; return; }

  if (t.matches('[data-osrc]')) {
    const i = +t.dataset.osrc;
    const next = +t.value;
    const cur = S.orientation.source[i];
    const j = S.orientation.source.indexOf(next);
    if (j >= 0) S.orientation.source[j] = cur;
    S.orientation.source[i] = next;
    render();
    return;
  }
  if (t.matches('[data-oinv]')) {
    S.orientation.invert[+t.dataset.oinv] = t.checked;
  }
}

function onViewOver(e) {
  const row = e.target.closest('[data-orient], [data-rsrc], .swap-row');
  const ball = $('#sys-ball');
  if (!ball) return;
  const axes = $$('.ax', ball);
  if (!row) { axes.forEach(a => { a.classList.remove('dim', 'lit'); }); return; }
  let idx = -1;
  if (row.dataset.orient != null) idx = +row.dataset.orient;
  else if (e.target.dataset.rsrc) {
    const store = routeStore(S.host);
    idx = store.src[e.target.dataset.rsrc] ?? -1;
  }
  axes.forEach(a => {
    const ai = +a.dataset.ax;
    a.classList.toggle('lit', ai === idx);
    a.classList.toggle('dim', idx >= 0 && ai !== idx);
  });
}

/* ---------------------------------------------------------------- boot */
function boot() {
  parseHash();
  if (!S.kbSel) S.kbSel = S.kb[S.kbProfile].bindings[0]?.id || null;
  bindShell();
  const view = $('#view');
  view.addEventListener('click', onViewClick);
  view.addEventListener('change', onViewChange);
  view.addEventListener('input', e => {
    if (e.target.matches('[data-sys="hud.opacity"], [data-set]')) onViewChange(e);
  });
  view.addEventListener('pointerover', onViewOver);
  render();
  requestAnimationFrame(updateRail);
}

boot();
