/* ============================================================================
   Astrolabe control daemon — UI demo, Fable v2 (front-end only, drives nothing)
   Reorganizes the daemon's expanded settings surface (app_registry.py,
   settings_schema.py, settings_ui_model.py, binding_ui_model.py,
   system_keybinding_profiles.json) into four pages:
     Overview · 3D Apps · Keybindings · General

   Two structural ideas carry the new complexity:
   1. The daemon's separate Global + Per-App layers collapse into ONE
      master-detail: a pinned "Global defaults" row sits atop the app list,
      and every app inherits from it. A per-control LINK CHIP (linked ↔
      override) replaces the old "Default (General)" dropdown sentinels.
   2. Rendering stays flash-free: full renders happen only on rail nav; every
      smaller change swaps just the pane it owns or mutates the DOM in place.
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
const GLOBAL = '__global__';

const PIVOT_LABELS = {
  camera: 'Camera', screen_center: 'Screen Center', cursor: 'Under Cursor (mouse)',
  selection: 'Selection', cursor_3d: '3D Cursor', object: 'Model Center', origin: 'World Origin',
};
const ORBIT_PIVOT_METHODS = ['camera', 'screen_center', 'cursor', 'selection',
  'cursor_3d', 'object', 'origin'];

const OPTION_LABELS = {
  free: 'Free', turntable: 'Turntable', roll: 'Roll', zoom: 'Zoom', dolly: 'Dolly', none: 'None',
  to_center: 'To Center', to_object: 'To Object', to_cursor: 'To Cursor (mouse)',
  '3d': '3D navigation', pointer: 'Pointer (cursor)',
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

/* -------------------------------------------- app capability profiles (schema)
   Mirrors app_registry.py AppBindingProfile: pivots, styles, twist, zoom
   behaviors, and which optional fields each host implements. */
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
const APP_ORDER = ['blender', 'freecad', 'sketchup', 'unreal', 'unity',
  'rhino', 'fusion360', 'solidworks', 'onshape', 'autocad'];

/* ---------------------------------------------------------- shared tip text */
const TIP_RATE = 'Per app; the Global default is the bridge rate. Higher is smoother (try 60); lower it if the app lags. Applies live and follows whichever app is focused.';
const TIP_HOLDS = 'Pivot hold ends an orbit gesture after this idle gap; pan clears the orbit pivot immediately. Zoom hold applies only to To Cursor zoom — pan preserves that target, orbiting invalidates it.';
const TIP_LEVEL = 'On: entering a fixed-horizon mode (Turntable / Lock horizon / Walk) removes any existing roll once. Off: the current tilt is locked. A linked app follows the Global value.';
const TIP_CLIP = 'Scene View Camera → Dynamic Clipping auto-fits near/far planes from the view size, which can feel like zoom-to-fit while you look around. On = Trackball Nav forces it off and uses fixed planes.';

/* -------------------------------------------------- setting registry (schema)
   Short ids drive both the Global layer and per-app overrides. Kept minimal:
   label + kind + hint(+tip) + optional per-app choices. */
const SETTINGS = {
  'rate':          { label: 'Viewport refresh (Hz)', kind: 'rate', hint: 'Per-app viewport frame rate.', tip: TIP_RATE },
  'orbit.sens':    { label: 'Orbit sensitivity', kind: 'num', hint: 'Rotation per ball turn; 1.0 = 1:1.' },
  'pan.gain':      { label: 'Pan gain', kind: 'num', hint: 'Pan strength while held.' },
  'zoom.gain':     { label: 'Zoom gain', kind: 'num', hint: 'Held-twist zoom / dolly strength.' },
  'zoom.dom':      { label: 'Zoom dominance', kind: 'num', hint: 'How firmly twist reads as zoom.' },
  'fly.speed':     { label: 'Fly speed', kind: 'num', rich: true, hint: 'Movement speed in Fly mode.' },
  'walk.speed':    { label: 'Walk speed', kind: 'num', rich: true, hint: 'Movement speed in Walk mode.' },
  'pivot.hold':    { label: 'Pivot hold (s)', kind: 'num', hint: 'Idle gap ending an orbit gesture.', tip: TIP_HOLDS },
  'zoom.hold':     { label: 'Zoom hold (s)', kind: 'num', hint: 'Applies to To-Cursor zoom only.', tip: TIP_HOLDS },
  'orbit.style':   { label: 'Orbit style', kind: 'enum', hint: 'How the view rotates.',
                     tip: 'Free permits full roll; Turntable keeps the horizon fixed.',
                     choices: o => PROFILES[o.key === GLOBAL ? 'blender' : o.key].styles },
  'orbit.pivot':   { label: 'Orbit pivot', kind: 'enum', hint: 'What the view rotates around.',
                     tip: 'Pivots a host can’t do are hidden. A pivot that fails at runtime falls back through the chain in General → 3D Defaults.',
                     labeler: PIVOT_LABELS, choices: o => o.key === GLOBAL ? ORBIT_PIVOT_METHODS : PROFILES[o.key].pivots },
  'twist':         { label: 'Twist action', kind: 'enum', hint: 'What unshifted twist does in Orbit.',
                     tip: 'Which actions are offered depends on the host: Roll, Zoom, Dolly, or None.',
                     choices: o => PROFILES[o.key === GLOBAL ? 'blender' : o.key].twist },
  'lock.horizon':  { label: 'Lock horizon', kind: 'bool', rich: true, hint: 'Stay level even in Free orbit.' },
  'level.entry':   { label: 'Level on entry', kind: 'bool', hint: 'Remove roll entering a level mode.', tip: TIP_LEVEL },
  'sel.override':  { label: 'Selection override', kind: 'bool', hint: 'Selection centre beats the pivot.',
                     tip: 'The Camera pivot still turns in place, ignoring any selection.' },
  'zoom.target':   { label: 'Zoom mode', kind: 'enum', choices: () => ['to_center', 'to_object', 'to_cursor'],
                     hint: 'What zoom moves toward.',
                     tip: 'To Center / To Object / To Cursor. To Cursor follows the surface under the mouse and needs a hit test.' },
  'zoom.behavior': { label: 'Pan-mode zoom', kind: 'enum', choices: o => PROFILES[o.key === GLOBAL ? 'blender' : o.key].zb,
                     hint: 'What held-twist zoom does.',
                     tip: 'Zoom changes the lens (FOV); Dolly moves the camera forward.' },
  'pan.scales':    { label: 'Pan scales with distance', kind: 'bool', rich: true, hint: 'Pan farther when zoomed out.' },
  'dyn.clip':      { label: 'Override dynamic clip', kind: 'bool', hint: 'Hold the near/far clip planes.', tip: TIP_CLIP },
  'pivot.ext':     { label: 'Pivot extent limit ×', kind: 'num', hint: 'Cap pivots at scene bounds × this.' },
  'cam.lock':      { label: 'Lock camera to view', kind: 'bool', hint: 'In camera view, drive that camera.' },
};

/* which per-app fields apply, by profile capability */
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

/* -------------------------------------------------- System defaults (layers)
   SYS = shipped global default; APP_SYS = per-host default that applies while
   an app is linked (e.g. Blender's camera pivot). */
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
};

const DEFAULT_ACTION_AXIS_SOURCE = {
  orbit:  { pitch: 0, yaw: 1, twist: 2, pan_x: 0, pan_y: 1, zoom: 2 },
  camera: { pitch: 0, yaw: 1, roll: 2 },
  fly:    { pitch: 0, yaw: 1, bank: 2, forward: 1, strafe: 0, vertical: 2 },
  walk:   { pitch: 0, yaw: 1, forward: 1, strafe: 0, vertical: 2 },
};
const LEAN_DEFAULT = { 'orbit.0': 0, 'orbit.1': 1, 'orbit.2': 2, 'pan.x': 1, 'pan.y': 0, 'zoom': 2 };

function freshRich() {
  // routing stores are flat maps keyed by `${mode}.${action}`
  const src = {}, inv = {};
  for (const [mode, acts] of Object.entries(DEFAULT_ACTION_AXIS_SOURCE)) {
    for (const [a, s] of Object.entries(acts)) { src[`${mode}.${a}`] = s; inv[`${mode}.${a}`] = false; }
  }
  return { src, inv };
}
function freshLean() {
  return { src: { 'orbit.0': 0, 'orbit.1': 1, 'orbit.2': 2, 'pan.x': 1, 'pan.y': 0, 'zoom': 2 },
           inv: { 'orbit.0': false, 'orbit.1': false, 'orbit.2': false, 'pan.x': false, 'pan.y': false, 'zoom': false } };
}
function freshRoute(key) { return (key === GLOBAL || PROFILES[key].rich) ? freshRich() : freshLean(); }

/* ------------------------------------------------- 3D-app registry (mock) */
const CERTUTIL = 'certutil -user -addstore Root "%APPDATA%\\TrackballDaemon\\onshape_cert.pem"';

const APPS = [
  { key: 'blender', name: 'Blender',
    status: { chip: 'good', text: 'active • connected (v0.1.12)', short: 'connected' },
    detected: 'v5.1.1 — C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe',
    versions: 'Blender 4.2 through 5.1 (tested on 5.1.1)',
    installModel: 'User add-on copied into each detected Blender version; optional startup shim.',
    security: 'Copies unsigned Python only into current-user folders. The optional startup shim runs it at Blender launch (asked separately). No elevation or external listener.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies Trackball Nav to every detected user scripts/addons folder and asks whether to add the auto-enable startup shim.',
      manual: 'Copy trackball_daemon\\plugins\\blender\\trackball_nav to %APPDATA%\\Blender Foundation\\Blender\\<version>\\scripts\\addons\\trackball_nav, then enable Trackball Nav in Preferences > Add-ons. No administrator access is required.',
      health: 'Restart Blender. The row should show connected while Blender is focused; details are in %APPDATA%\\TrackballDaemon\\blender_addin.log.' } },
  { key: 'freecad', name: 'FreeCAD',
    status: { chip: 'idle', text: 'installed · v0.1.6', short: 'installed' },
    detected: 'v1.1.1 — C:\\Program Files\\FreeCAD 1.1\\bin\\FreeCAD.exe',
    versions: 'FreeCAD 1.0 through 1.1 (tested on 1.1.1)',
    installModel: 'User Mod add-on; files are copied only under the current Windows profile.',
    security: 'Copies unsigned Python into FreeCAD\'s current-user Mod folder, loaded at startup. No elevation, registry write, or external port.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav into FreeCAD\'s version-aware user Mod folder.',
      manual: 'Copy trackball_daemon\\plugins\\freecad\\TrackballNav to %APPDATA%\\FreeCAD\\v<major>-<minor>\\Mod\\TrackballNav (FreeCAD 1.x), then restart FreeCAD. Use %APPDATA%\\FreeCAD\\Mod for older layouts.',
      health: 'Open a 3D view and focus FreeCAD; the row should show connected. Check %APPDATA%\\TrackballDaemon\\freecad_addin.log if it does not.' } },
  { key: 'sketchup', name: 'SketchUp',
    status: { chip: 'idle', text: 'installed · v0.2.3', short: 'installed' },
    detected: 'v2026.2.243 — C:\\Program Files\\SketchUp\\SketchUp 2026\\SketchUp.exe',
    versions: 'SketchUp Desktop 2025 through 2026 (tested on 2026.2.243)',
    installModel: 'Per-version Ruby extension in SketchUp\'s user Plugins folder.',
    security: 'Copies an unsigned Ruby extension into SketchUp\'s current-user Plugins folder; SketchUp executes it at startup. No elevation or external listener.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies the loader and extension into every detected annual release.',
      manual: 'Copy trackball_daemon\\plugins\\sketchup\\trackball_nav_loader.rb and the trackball_nav folder to %APPDATA%\\SketchUp\\SketchUp <year>\\SketchUp\\Plugins, then restart SketchUp. SketchUp for Web is not supported.',
      health: 'Extension Manager should list Trackball Nav; focus a model and look for connected in this row or inspect %APPDATA%\\TrackballDaemon\\sketchup_addin.log.' } },
  { key: 'unreal', name: 'Unreal Engine',
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
      health: 'Focus a perspective level viewport; the row should show connected. Check %APPDATA%\\TrackballDaemon\\unreal_addin.log and the Output Log on failure.' } },
  { key: 'unity', name: 'Unity',
    status: { chip: 'idle', text: 'detected — not set up', short: 'not set up' },
    detected: 'v6000.5.3f1 — C:\\Program Files\\Unity\\Hub\\Editor\\6000.5.3f1\\Editor\\Unity.exe',
    versions: 'Unity 6 / 6000.x (implemented against 6000.5.3f1)',
    installModel: 'UPM Editor package copied into each detected Unity project.',
    security: 'Copies unsigned C# editor source into each detected project\'s Packages folder; Unity compiles and runs it in the Editor. No elevation or machine-wide change.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up finds running/recent projects and copies the package into each project\'s Packages folder; Unity recompiles it automatically.',
      manual: 'Copy trackball_daemon\\plugins\\unity\\com.astrolabe.trackball-nav to <YourProject>\\Packages\\com.astrolabe.trackball-nav. If Set up found no project, the same package is staged under %APPDATA%\\TrackballDaemon\\unity.',
      health: 'Open and focus a Scene view; the row should show connected. Check the Unity Console and %APPDATA%\\TrackballDaemon\\unity_addin.log.' } },
  { key: 'rhino', name: 'Rhino',
    status: { chip: 'idle', text: 'installed · v0.1.2', short: 'installed' },
    detected: 'v8.19 — C:\\Program Files\\Rhino 8\\System\\Rhino.exe', versions: 'Rhino 8',
    installModel: 'Rhino 8 user Python scripts plus a per-user startup command.',
    security: 'Copies unsigned Python into Rhino\'s current-user scripts folder and may edit the user startup-command XML so it runs at launch. No elevation.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav into Rhino\'s user scripts folder and best-effort registers its startup command.',
      manual: 'Copy trackball_daemon\\plugins\\rhino\\TrackballNav to %APPDATA%\\McNeel\\Rhinoceros\\8.0\\scripts\\TrackballNav. In Rhino Options > General, add _-RunPythonScript "<path>\\start.py" to startup commands, then restart Rhino.',
      health: 'Focus a Rhino viewport and look for connected. Check %APPDATA%\\TrackballDaemon\\rhino_addin.log if startup failed.' } },
  { key: 'fusion360', name: 'Fusion 360',
    status: { chip: 'idle', text: 'installed · v0.1.16', short: 'installed' },
    detected: '%LOCALAPPDATA%\\Autodesk\\webdeploy\\production\\Fusion360.exe (rolling release)',
    versions: 'Current Fusion production release (rolling Autodesk release)',
    installModel: 'Fusion user add-in copied to Autodesk\'s per-user AddIns folder.',
    security: 'Copies unsigned Python into Fusion\'s current-user AddIns folder. Fusion runs it only after you click Run / Run on Startup. No elevation.',
    setupRequired: true, action: 'Reinstall',
    instructions: {
      auto: 'Set up copies TrackballNav. In Fusion, open Utilities > Add-Ins, run TrackballNav once, and enable Run on Startup.',
      manual: 'Copy trackball_daemon\\plugins\\fusion360\\TrackballNav to %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\TrackballNav, then run it from Utilities > Add-Ins. No administrator access is required.',
      health: 'Focus an open design and look for connected. Check %APPDATA%\\TrackballDaemon\\fusion_addin.log if the add-in does not handshake.' } },
  { key: 'solidworks', name: 'SolidWorks',
    status: { chip: 'idle', text: 'ready · direct COM', short: 'ready' },
    detected: 'v2025 — C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe',
    versions: 'SOLIDWORKS 2025 (tested on 2025)',
    installModel: 'Direct COM automation; no SolidWorks add-in or host files are installed.',
    security: 'Per-user COM automation against an already-running instance. No COM server registration, DLL install, elevation, or external interface.',
    setupRequired: false, action: null,
    instructions: {
      auto: 'Switching the mode slider off Off performs a one-time prerequisite check for SOLIDWORKS and pywin32. Nothing else is needed.',
      manual: 'There is nothing to copy. If the prerequisite check fails, install pywin32 into the daemon\'s Python environment with: pip install pywin32.',
      health: 'Open a part or assembly and focus SOLIDWORKS; the row should show connected. Driver messages are recorded in %APPDATA%\\TrackballDaemon\\daemon.log.' } },
  { key: 'onshape', name: 'Onshape',
    status: { chip: 'idle', text: 'not set up', short: 'not set up' },
    detected: 'Chrome 138 — bridge endpoint 127.51.68.120:8181 reserved',
    versions: 'Current Onshape web release (rolling release)',
    installModel: 'Browser bridge; no Onshape add-in is installed.',
    security: 'Creates a per-user self-signed certificate; TLS binds only to 127.51.68.120. Trust-store install is never automatic. The optional userscript reports canvas pointer coordinates only.',
    setupRequired: true, action: 'Set up',
    instructions: {
      auto: 'Set up creates the bridge\'s per-user local TLS certificate. Trust it once and enable SpaceMouse/3Dconnexion in Onshape preferences.',
      manual: 'No application files need copying. Generate/trust the certificate using the Set up dialog or certutil -user, then install the supplied userscript only if Under Cursor orbit is wanted. Administrator access is not required.',
      health: 'Open and focus an Onshape document; the row should show connected after the browser handshake. Check %APPDATA%\\TrackballDaemon\\daemon.log.' } },
  { key: 'autocad', name: 'AutoCAD',
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
      health: 'Type TBNAV in AutoCAD or look for connected in this row. Plugin details are in %APPDATA%\\TrackballDaemon\\acad_plugin.log.' } },
];
const APPS_BY_KEY = Object.fromEntries(APPS.map(a => [a.key, a]));

/* ------------------------------------------------------ keybindings (mock)
   Ported from system_keybinding_profiles.json + descriptor_data. Actions map
   the JSON press/release pairs onto the simple editor's target vocabulary. */
const TOKEN_LABELS = {
  'keyboard:ctrl': 'Ctrl', 'keyboard:shift': 'Shift', 'keyboard:alt': 'Alt', 'keyboard:meta': 'Win',
  'keyboard:f12': 'F12',
  'ble.astrolabe:fiveway.up': '5-way ↑', 'ble.astrolabe:fiveway.down': '5-way ↓',
  'ble.astrolabe:fiveway.left': '5-way ←', 'ble.astrolabe:fiveway.right': '5-way →',
  'ble.astrolabe:fiveway.center': '5-way ●',
  'ble.xiao3389:button.left': 'XIAO L', 'ble.xiao3389:button.right': 'XIAO R',
  'ble.xiao3389:button.middle': 'XIAO M',
};
const tokenLabel = t => TOKEN_LABELS[t] || t;
const tokenSource = t => t.split(':')[0];

const TOKEN_GROUPS = [
  ['Keyboard', ['keyboard:ctrl', 'keyboard:shift', 'keyboard:alt', 'keyboard:meta', 'keyboard:f12']],
  ['Astrolabe five-way', ['ble.astrolabe:fiveway.up', 'ble.astrolabe:fiveway.down',
    'ble.astrolabe:fiveway.left', 'ble.astrolabe:fiveway.right', 'ble.astrolabe:fiveway.center']],
  ['XIAO test-bench', ['ble.xiao3389:button.left', 'ble.xiao3389:button.right', 'ble.xiao3389:button.middle']],
];

const COMMON_ACTIONS = [
  ['input.toggle', 'Toggle Pointer / 3D'], ['input.hold_3d', 'Hold 3D mode'],
  ['input.hold_pointer', 'Hold Pointer mode'], ['navigation.hold_pan', 'Hold Pan / Zoom controls'],
  ['navigation.hold_secondary', 'Hold secondary Orbit controls'],
  ['navigation.set_orbit', 'Switch navigation to Orbit'], ['navigation.set_fly', 'Switch navigation to Fly'],
  ['navigation.set_walk', 'Switch navigation to Walk'], ['navigation.cycle', 'Cycle Orbit / Fly / Walk'],
  ['pointer.left', 'Hold Left click'], ['pointer.right', 'Hold Right click'], ['pointer.middle', 'Hold Middle click'],
];
const COMMON_LABELS = Object.fromEntries(COMMON_ACTIONS);

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
        { id: 'keyboard.ctrl_shift.pan', label: 'Ctrl + Shift: Pan / Zoom', enabled: true, chord: ['keyboard:ctrl', 'keyboard:shift'], match: 'exact', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'astrolabe.center.toggle_mode', label: 'Center: Toggle Pointer / 3D', enabled: true, chord: ['ble.astrolabe:fiveway.center'], match: 'exact', apps: [], action: co('input.toggle'), system: true },
        { id: 'astrolabe.up.orbit', label: 'Up: Orbit', enabled: true, chord: ['ble.astrolabe:fiveway.up'], match: 'exact', apps: [], action: co('navigation.set_orbit'), system: true },
        { id: 'astrolabe.right.fly', label: 'Right: Fly', enabled: true, chord: ['ble.astrolabe:fiveway.right'], match: 'exact', apps: [], action: co('navigation.set_fly'), system: true },
        { id: 'astrolabe.down.walk', label: 'Down: Walk', enabled: true, chord: ['ble.astrolabe:fiveway.down'], match: 'exact', apps: [], action: co('navigation.set_walk'), system: true },
        { id: 'astrolabe.left.secondary', label: 'Left: Secondary controls', enabled: true, chord: ['ble.astrolabe:fiveway.left'], match: 'exact', apps: [], action: co('navigation.hold_secondary'), system: true },
        { id: 'xiao.left.pointer_button', label: 'XIAO Left click', enabled: true, chord: ['ble.xiao3389:button.left'], match: 'exact', apps: [], action: co('pointer.left'), system: true },
        { id: 'xiao.right.pointer_button', label: 'XIAO Right click', enabled: true, chord: ['ble.xiao3389:button.right'], match: 'exact', apps: [], action: co('pointer.right'), system: true },
        { id: 'xiao.middle.pointer_button', label: 'XIAO Middle click', enabled: true, chord: ['ble.xiao3389:button.middle'], match: 'exact', apps: [], action: co('pointer.middle'), system: true },
        { id: 'user.binding.1', label: 'Alt: Turntable (Blender)', enabled: false, chord: ['keyboard:alt'], match: 'allow', apps: ['blender'], action: { kind: 'setting', target: 'orbit.style', op: 'hold', value: 'turntable' }, system: false },
      ],
    },
    keyboard_only: {
      label: 'Keyboard only',
      bindings: [
        { id: 'keyboard.ctrl.3d', label: 'Ctrl: 3D', enabled: true, chord: ['keyboard:ctrl'], match: 'allow', apps: [], action: co('input.hold_3d'), system: true },
        { id: 'keyboard.shift.pan', label: 'Shift: Pan / Zoom', enabled: true, chord: ['keyboard:shift'], match: 'allow', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'keyboard.ctrl_shift.pan', label: 'Ctrl + Shift: Pan / Zoom', enabled: true, chord: ['keyboard:ctrl', 'keyboard:shift'], match: 'exact', apps: [], action: co('navigation.hold_pan'), system: true },
        { id: 'keyboard.f12.toggle_mode', label: 'F12: Toggle Pointer / 3D', enabled: true, chord: ['keyboard:f12'], match: 'exact', apps: [], action: co('input.toggle'), system: true },
      ],
    },
  };
}

/* device / provider health (mock) — the 5-way is present; the XIAO bench is not */
const PROVIDER_HEALTH = {
  keyboard: { status: 'present', chip: 'good' },
  'ble.astrolabe': { status: 'present', chip: 'good' },
  'ble.xiao3389': { status: 'not present', chip: 'warn' },
};

/* ---------------------------------------------------------------- state */
const S = {
  page: 'overview',
  mode: 'cube',                 // live input mode: 'cursor'(pointer) | 'cube'(3d)
  app: 'blender',               // 3D Apps selection ('__global__' or an app key)
  appTab: {}, genTab: 'device', routeTab: {},
  fbSel: -1, fbFlash: -1,
  kbProfile: 'astrolabe_5way', kbSel: null, kb: seedProfiles(),
  global: {},                   // sparse Global overrides over SYS
  globalRoute: freshRich(),
  fallbacks: ['cursor_3d', 'camera', 'object', 'origin'],
  device: { name: 'Trackball BLE', address: '' },
  orientation: { source: [0, 1, 2], invert: [false, false, false] },
  startAtLogin: false,
  apps: {},                     // key -> { enabled, mode, ov:{}, route:{} }
};
const DEFAULT_ENABLED = {
  blender: true, freecad: true, sketchup: true, unreal: true, unity: false,
  rhino: true, fusion360: true, solidworks: true, onshape: false, autocad: false,
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
/* the shipped System default that applies to this app (per-host default, else global System) */
function appSysDefault(key, id) {
  const as = APP_SYS[key];
  return as && id in as ? as[id] : SYS[id];
}
function appDivergesSys(key, id) { return String(appEff(key, id)) !== String(appSysDefault(key, id)); }

/* -------------------------------------------------------------- helpers */
const tipIcon = t => t ? `<span class="tip" title="${esc(t)}">&#9432;</span>` : '';

/* crisp inline link / broken-link glyphs (Precision Instrument Minimalism —
   SVG hairlines rather than emoji, so they inherit colour and theme cleanly) */
const ICON_LINK = `<svg class="lk" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`;
const ICON_UNLINK = `<svg class="lk" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m18.84 12.25 1.72-1.71a5.004 5.004 0 0 0-.12-7.07 5.006 5.006 0 0 0-6.95 0l-1.72 1.71"/><path d="m5.17 11.75-1.71 1.71a5.004 5.004 0 0 0 .12 7.07 5.006 5.006 0 0 0 6.95 0l1.71-1.71"/><line x1="8" y1="2" x2="8" y2="5"/><line x1="2" y1="8" x2="5" y2="8"/><line x1="16" y1="19" x2="16" y2="22"/><line x1="19" y1="16" x2="22" y2="16"/></svg>`;

/* the v2 row grammar: label | control | button A | button B | descriptor.
   App rows carry a link button + a reset button; Global rows just a reset. */
function detailRow(label, ctrl, cellA, cellB, hint, tip, rcClass = '') {
  return `<div class="row">
    <span class="rl">${esc(label)}${tipIcon(tip)}</span>
    <span class="rc ${rcClass}">${ctrl}</span>
    ${cellA}${cellB}
    <span class="hint">${esc(hint)}</span>
  </div>`;
}

/* the base 4-column row (General page, keybinding editor): single button slot */
function rowHTML(label, ctrl, chip, hint, tip, rcClass = '') {
  return `<div class="row">
    <span class="rl">${esc(label)}${tipIcon(tip)}</span>
    <span class="rc ${rcClass}">${ctrl}</span>
    ${chip}
    <span class="hint">${esc(hint)}</span>
  </div>`;
}

/* a settings row bound to the Global layer or a per-app override.
   App: [link] [reset-to-System]. Global: [reset-to-System] [—]. */
function srow(owner, id) {
  const meta = SETTINGS[id];
  const isApp = owner.key !== GLOBAL;
  const key = owner.key;
  const linked = isApp && appLinked(key, id);
  const val = isApp ? appEff(key, id) : gval(id);
  const choices = meta.choices ? meta.choices(owner) : null;
  const ctrl = settingControl({ owner, id, meta, val, choices, disabled: linked });
  const cellA = isApp ? linkBtn(key, id, linked) : miniResetGlobal(id);
  const cellB = isApp ? appResetBtn(key, id)     : '<span class="rs"></span>';
  return detailRow(meta.label, ctrl, cellA, cellB, meta.hint, meta.tip, linked ? 'linked' : '');
}

function settingControl({ owner, id, meta, val, choices, disabled }) {
  const key = owner.key;
  const data = `data-scope="${key === GLOBAL ? 'global' : 'app'}"${key === GLOBAL ? '' : ` data-app="${key}"`} data-id="${id}" data-kind="${meta.kind}"`;
  const dis = disabled ? ' disabled' : '';
  if (meta.kind === 'bool') {
    return `<label class="switch"><input type="checkbox" ${data}${dis} ${val ? 'checked' : ''}>
      <span class="track"></span></label>`;
  }
  if (meta.kind === 'enum') {
    const lab = meta.labeler ? (v => meta.labeler[v] || optLabel(v)) : optLabel;
    const opts = choices.map(c =>
      `<option value="${esc(c)}"${String(c) === String(val) ? ' selected' : ''}>${esc(lab(c))}</option>`).join('');
    const warn = id === 'orbit.pivot' && key !== GLOBAL ? ' data-warn="onshape-cursor"' : '';
    return `<select class="sel" ${data}${warn}${dis}>${opts}</select>`;
  }
  if (meta.kind === 'rate') {
    const v = (!val || +val === 0) ? 'Default' : String(val);
    return `<input class="text" list="rate-presets" ${data} value="${esc(v)}" spellcheck="false"${dis}>`;
  }
  return `<input class="text" ${data} value="${esc(fmt(val))}" spellcheck="false"${dis}>`;
}

/* link/unlink this app's setting from the Global default (always shown) */
function linkBtn(key, id, linked) {
  const title = linked
    ? 'Linked to the Global default — click to unlink and set a value just for this app.'
    : 'Unlinked — using this app’s own value. Click to relink to the Global default.';
  return `<span class="rs"><button class="link-chip ${linked ? 'linked' : 'broken'}"
    data-link="${id}" data-linkapp="${key}" title="${esc(title)}"
    aria-label="${linked ? 'Linked to Global default' : 'Unlinked from Global default'}">${linked ? ICON_LINK : ICON_UNLINK}</button></span>`;
}
/* reset this app's setting to its shipped System default (also breaks the link) */
function appResetBtn(key, id) {
  const show = appDivergesSys(key, id);
  return `<span class="rs"><button class="mini-reset${show ? ' show' : ''}" data-sysreset="${id}" data-sysapp="${key}"
    title="Differs from the shipped default — reset to it (this also unlinks from Global).">&#8634;</button></span>`;
}
function miniResetGlobal(id) {
  const show = gDiverged(id);
  return `<span class="rs"><button class="mini-reset${show ? ' show' : ''}" data-greset="${id}"
    title="Changed from the shipped System default — click to reset.">&#8634;</button></span>`;
}

function ctrlSwitchPlain(scope, id, val) {
  return `<label class="switch"><input type="checkbox" data-scope="${scope}" data-id="${id}" data-kind="bool" ${val ? 'checked' : ''}>
    <span class="track"></span></label>`;
}
function ctrlTextPlain(scope, id, val, kind, wide) {
  return `<input class="text${wide ? ' wide' : ''}" data-scope="${scope}" data-id="${id}" data-kind="${kind}" value="${esc(fmt(val))}" spellcheck="false">`;
}
function ctrlSelectPlain(scope, id, val, options) {
  const opts = options.map(([v, l]) =>
    `<option value="${esc(v)}"${String(v) === String(val) ? ' selected' : ''}>${esc(l)}</option>`).join('');
  return `<select class="sel" data-scope="${scope}" data-id="${id}" data-kind="enum">${opts}</select>`;
}

function axisSeg(store, id) {
  const cur = +store.src[id];
  return `<span class="axis-seg">` + AXIS.map((n, i) =>
    `<button data-axpick="${id}" data-ax="${i}" data-ax-hl="${i}"
       class="${i === cur ? 'on' : ''}" aria-label="source ${n}">${n}</button>`).join('') + `</span>`;
}
function invPillRoute(store, id) {
  return `<button class="inv-pill ${store.inv[id] ? 'on' : ''}" data-invroute="${id}">Invert</button>`;
}
function invPillOrient(idx) {
  return `<button class="inv-pill ${S.orientation.invert[idx] ? 'on' : ''}" data-invorient="${idx}">Invert</button>`;
}

function seg(options, current, dataAttr, cls = '') {
  return `<span class="seg ${cls}"><span class="seg-thumb"></span>` + options.map(([val, lab]) =>
    `<button data-${dataAttr}="${esc(val)}" class="${val === current ? 'on' : ''}">${esc(lab)}</button>`).join('') + `</span>`;
}
function placeThumb(segEl, animate = false) {
  const thumb = $('.seg-thumb', segEl), on = $('button.on', segEl);
  if (!thumb || !on) return;
  if (!animate) thumb.style.transition = 'none';
  thumb.style.width = on.offsetWidth + 'px';
  thumb.style.transform = `translateX(${on.offsetLeft}px)`;
  if (!animate) { void thumb.offsetWidth; thumb.style.transition = ''; }
}
function initSegThumbs(scope = document) { $$('.seg', scope).forEach(s => placeThumb(s)); }
function selectSegButton(segEl, btn, animate = true) {
  $$('button', segEl).forEach(b => b.classList.toggle('on', b === btn));
  placeThumb(segEl, animate);
}
function sect(title, note = '') {
  return `<h3 class="sect">${esc(title)}${note ? ` <span class="sect-note">— ${esc(note)}</span>` : ''}</h3>`;
}

const TIPS = {
  fallback: 'The selected pivot is tried first. If it is unavailable, resolution restarts at item 1 of this chain; unsupported methods are skipped by that integration. An empty list means no fallback. Selection override still wins when enabled, except Camera always turns in place.',
  sources: 'Sources are logical axes after the global physical orientation (General → Device). Edits apply live.',
  userscript: 'The Under Cursor orbit pivot in Onshape needs a small userscript that POSTs the exact #canvas pointer position to the local bridge — DOM only, loopback only, no screen capture.',
  passthrough: 'Keyboard chords are observed passively: non-modifier keys still reach the foreground application.',
};

/* ================================================================= views */
const PAGES = { overview: 1, apps: 1, keys: 1, general: 1 };

/* ------------------------------------------------------------- overview */
function viewOverview() {
  const tiles = APPS.map(a => `
    <button class="tile" data-goto-app="${a.key}" title="${esc(a.status.text)} — open in 3D Apps">
      <span class="dot ${a.status.chip}"></span><span class="tile-nm">${esc(a.name)}</span>
      <span class="tile-st">${esc(a.status.short)}</span>
    </button>`).join('');
  const prof = S.kb[S.kbProfile].label;
  const hud = gval('hud.visible') ? 'on' : 'off';
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
          <dd id="fact-mode">${S.mode === 'cube' ? '3D navigation' : 'Pointer'}
            <span class="sub">set by a keybinding or the rail toggle; navigation follows the focused app</span></dd></div>
        <div class="fact-row"><dt>Input profile</dt>
          <dd>${esc(prof)} <span class="sub">edit under Keybindings</span></dd></div>
      </dl>
      ${sect('Runtime')}
      <dl>
        <div class="fact-row"><dt>Daemon</dt>
          <dd class="mono">v0.1.59 <span class="sub">tray-resident; closing this window only hides it</span></dd></div>
        <div class="fact-row"><dt>Nav broker</dt>
          <dd class="mono">127.0.0.1:47900 <span class="sub">loopback socket the host add-ons connect to</span></dd></div>
        <div class="fact-row"><dt>Onshape bridge</dt>
          <dd class="mono">127.51.68.120:8181 <span class="sub">local TLS NL-proxy for Onshape's browser client</span></dd></div>
        <div class="fact-row"><dt>Control panel</dt>
          <dd>Text HUD ${esc(hud)} <span class="sub">bottom-right overlay; configure under General → Control Panel</span></dd></div>
        <div class="fact-row"><dt>Start at login</dt>
          <dd>${ctrlSwitchPlain('login', 'start', S.startAtLogin)}
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
        <li>Under <b>Keybindings</b> pick your input profile; hold the mapped control for <b>3D</b> and move the ball.</li>
        <li>Set the app's active mode from its header slider; tune feel under <b>Tuning</b> / <b>Navigation</b>.</li>
      </ol>
    </div>
  </div>`;
}

/* --------------------------------------------------------------- 3D apps */
const APP_TABS = [['setup', 'Setup'], ['tuning', 'Tuning'], ['nav', 'Navigation'], ['routing', 'Routing']];
const GLOBAL_TABS = [['tuning', 'Tuning'], ['nav', 'Navigation'], ['routing', 'Routing']];
const MODE_LETTER = { orbit: 'O', fly: 'F', walk: 'W' };

function appMode(key) {
  if (!S.apps[key].enabled) return 'off';
  return PROFILES[key].rich ? S.apps[key].mode : 'orbit';
}
function appItemInner(a) {
  const mode = appMode(a.key);
  const state = mode === 'off'
    ? `<span class="dot ${a.status.chip} dim"></span>`
    : `<span class="app-badge st-${a.status.chip}" title="${esc(a.status.text)} — mode: ${mode}">${MODE_LETTER[mode]}</span>`;
  return `${state}${esc(a.name)}`;
}
function appListHTML() {
  const gear = `<span class="gicon"><svg viewBox="0 0 16 16" width="12" height="12" fill="none"
    stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="2.4"/>
    <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4"/></svg></span>`;
  const global = `
    <button class="app-item global-row ${S.app === GLOBAL ? 'on' : ''}" data-appsel="${GLOBAL}">${gear}Global defaults</button>
    <div class="list-cap">inherited by every app</div>
    <div class="list-div"></div>`;
  const apps = APPS.map(a => `
    <button class="app-item ${a.key === S.app ? 'on' : ''}" data-appsel="${a.key}">${appItemInner(a)}</button>`).join('');
  return global + apps;
}

const MODE_TITLES = {
  off: 'Off — ball motion is not routed to this app',
  orbit: 'Orbit — rotate around the configured pivot; hold the mapped control for pan / zoom',
  fly: 'Fly — free 6DOF flight: banks on twist, forward follows pitch; secondary hold strafes and rises',
  walk: 'Walk — horizon-locked look, movement stays on the ground plane',
};
function modeSlider(key) {
  const p = PROFILES[key], cur = appMode(key);
  const opts = p.rich ? ['off', 'orbit', 'fly', 'walk'] : ['off', 'orbit'];
  const titles = MODE_TITLES;
  return `<span class="seg mode-slider ms-${cur}" data-mslider="${key}"
    title="Active control mode while ${esc(APPS_BY_KEY[key].name)} has focus in 3D mode. Off also disables the integration.">
    <span class="seg-thumb"></span>
    ${opts.map(o => `<button data-mslide="${o}" class="${o === cur ? 'on' : ''}"
      title="${esc(titles[o])}">${o[0].toUpperCase()}${o.slice(1)}</button>`).join('')}
  </span>`;
}

function appDetailHTML(key) {
  if (key === GLOBAL) return globalDetailHTML();
  const a = APPS_BY_KEY[key];
  const tab = S.appTab[key] || 'setup';
  const chipClass = { good: 'chip-good', idle: 'chip-idle', warn: 'chip-warn', bad: 'chip-bad', off: 'chip-off' }[a.status.chip];
  return `
  <div class="detail-head">
    <span class="app-name">${esc(a.name)}</span>
    <span class="chip ${chipClass}">${esc(a.status.text)}</span>
    <span class="spacer"></span>
    ${modeSlider(key)}
    <button class="btn-icon" data-reset-app="${key}"
            title="Reset overrides — relink every ${esc(a.name)} setting to the Global defaults. Mode and install state are preserved.">&#8634;</button>
  </div>
  <div class="subtabs">${seg(APP_TABS, tab, 'subtab')}</div>
  <div id="app-pane-host">${appPaneHTML(key, tab, false)}</div>`;
}
function globalDetailHTML() {
  const tab = S.appTab[GLOBAL] || 'tuning';
  return `
  <div class="detail-head">
    <span class="app-name">Global defaults</span>
    <span class="global-tag">baseline every app inherits</span>
    <span class="spacer"></span>
    <button class="btn-icon" data-reset-global title="Reset all Global settings to their shipped System defaults.">&#8634;</button>
  </div>
  <div class="subtabs">${seg(GLOBAL_TABS, tab, 'subtab')}</div>
  <div id="app-pane-host">${appPaneHTML(GLOBAL, tab, false)}</div>`;
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
    ? `<button class="btn ${/Set up|Update/.test(a.action) ? 'btn-primary' : ''}" data-setup="${a.key}">${esc(a.action)}</button>` : '';
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
  const o = { key };
  const rich = key === GLOBAL || PROFILES[key].rich;
  const name = key === GLOBAL ? 'the Global default' : APPS_BY_KEY[key].name;
  let out = `${sect('Sensitivity & rate', `how ball motion drives ${name}`)}
  ${srow(o, 'rate')}${srow(o, 'orbit.sens')}${srow(o, 'pan.gain')}${srow(o, 'zoom.gain')}${srow(o, 'zoom.dom')}`;
  if (rich) out += `${srow(o, 'fly.speed')}${srow(o, 'walk.speed')}`;
  out += `${sect('Gesture timing', 'independent hold timers')}${srow(o, 'pivot.hold')}${srow(o, 'zoom.hold')}`;
  return out;
}

function paneNav(key) {
  const o = { key };
  const isGlobal = key === GLOBAL;
  const p = isGlobal ? null : PROFILES[key];
  const has = id => isGlobal ? true : appliesToApp(id, key);
  let orbit = sect('Orbit') + srow(o, 'orbit.style') + srow(o, 'orbit.pivot') + srow(o, 'twist');
  if (has('lock.horizon')) orbit += srow(o, 'lock.horizon');
  if (has('level.entry')) orbit += srow(o, 'level.entry');
  orbit += srow(o, 'sel.override');
  if (!isGlobal && p.userscript) {
    orbit += `<div class="row"><span class="rl">Under-cursor orbit${tipIcon(TIPS.userscript)}</span>
      <span class="rc"><button class="btn btn-sm" data-onshape-userscript>Copy userscript…</button></span>
      <span class="rs"></span><span class="rs"></span><span class="hint">Only for Under Cursor orbit.</span></div>`;
  }
  let pz = sect('Pan / Zoom', 'while the toggle is held') + srow(o, 'zoom.target');
  if (has('zoom.behavior')) pz += srow(o, 'zoom.behavior');
  if (has('pan.scales')) pz += srow(o, 'pan.scales');
  if (has('dyn.clip')) pz += srow(o, 'dyn.clip');
  if (has('pivot.ext')) pz += srow(o, 'pivot.ext');
  if (has('cam.lock')) pz += srow(o, 'cam.lock');
  return orbit + pz;
}

/* routing — column-major (rotation left, translation right) */
function routeStoreFor(key) { return key === GLOBAL ? S.globalRoute : S.apps[key].route; }
function routeRowHTML(store, label, srcId, invId, def) {
  const diverged = store.src[srcId] !== def.src || store.inv[invId] !== def.inv;
  return `<div class="route-row">
    <span class="route-name">${esc(label)}</span>
    ${axisSeg(store, srcId)}
    ${invPillRoute(store, invId)}
    <span class="rs"><button class="mini-reset${diverged ? ' show' : ''}" data-rreset="${srcId}|${invId}|${def.src}|${def.inv ? 1 : 0}"
      title="Changed from the default routing — click to reset.">&#8634;</button></span>
  </div>`;
}
function routesHTML(key) {
  const store = routeStoreFor(key);
  const isRich = key === GLOBAL || PROFILES[key].rich;
  let rows;
  if (isRich) {
    const p = key === GLOBAL ? null : PROFILES[key];
    const routeTab = S.routeTab[key] || (key !== GLOBAL && S.apps[key].enabled ? appMode(key) : 'orbit');
    let routes = ROUTES_BY_MODE[routeTab];
    if (p && p.noRoll && routeTab === 'camera') routes = routes.filter(([, k]) => k !== 'roll');
    if (p && p.noRoll && routeTab === 'fly') routes = routes.filter(([, k]) => k !== 'bank');
    rows = routes.map(([label, act]) => routeRowHTML(store, label, `${routeTab}.${act}`, `${routeTab}.${act}`,
      { src: DEFAULT_ACTION_AXIS_SOURCE[routeTab][act], inv: false })).join('');
  } else {
    const L = [['Orbit X', 'orbit.0'], ['Orbit Y', 'orbit.1'], ['Orbit Z', 'orbit.2'],
              ['Pan X', 'pan.x'], ['Pan Y', 'pan.y'], ['Zoom', 'zoom']];
    rows = L.map(([label, act]) => routeRowHTML(store, label, act, act, { src: LEAN_DEFAULT[act], inv: false })).join('');
  }
  return `<div class="routes-2col pane">${rows}</div>`;
}
function paneRouting(key) {
  const isRich = key === GLOBAL || PROFILES[key].rich;
  let tabs = '';
  if (isRich) {
    const routeTab = S.routeTab[key] || (key !== GLOBAL && S.apps[key].enabled ? appMode(key) : 'orbit');
    tabs = `<div class="subtabs" style="margin:0 0 8px">${seg(
      [['orbit', 'Orbit'], ['camera', 'Camera'], ['fly', 'Fly'], ['walk', 'Walk']], routeTab, 'routetab')}</div>`;
  }
  const note = key === GLOBAL ? 'These map the Global default routing every app inherits.' : TIPS.sources;
  return `
  ${sect('Action axes & directions', isRich ? 'independent per mode' : 'per this app')}
  ${tabs}
  <div id="routes-host">${routesHTML(key)}</div>
  <p class="note" style="margin-top:9px">${esc(note)}
  Rotation actions fill the left column, translation the right. Hover a source to highlight its axis on the trackball.</p>`;
}

/* ------------------------------------------------------------ keybindings */
function providerSources(profileId) {
  const set = new Set();
  for (const b of S.kb[profileId].bindings) for (const t of b.chord) set.add(tokenSource(t));
  return [...set].sort();
}
function capStripHTML() {
  const items = providerSources(S.kbProfile).map(src => {
    const h = PROVIDER_HEALTH[src] || { status: 'unknown', chip: 'idle' };
    return `<span class="cap-item ${h.chip === 'good' ? '' : 'absent'}"><span class="dot ${h.chip}"></span>
      <span class="mono">${esc(src)}</span> · ${esc(h.status)}</span>`;
  }).join('');
  return `<div class="cap-strip">${items || '<span class="cap-item absent">No input controls required.</span>'}</div>`;
}
function kbChips(chord, editor) {
  if (!chord.length) return `<span class="chord-chip plus">${editor ? 'no controls yet' : 'no chord'}</span>`;
  return chord.map((t, i) => `${i && !editor ? '<span class="chord-chip plus">+</span>' : ''}<span class="chord-chip ${tokenSource(t).startsWith('ble') ? 'ble' : ''}">${esc(tokenLabel(t))}</span>`).join('');
}
function kbListHTML() {
  const bindings = S.kb[S.kbProfile].bindings;
  if (!bindings.length) return `<div class="kb-empty">No bindings in this profile.</div>`;
  return bindings.map(b => `
    <button class="kb-item ${b.id === S.kbSel ? 'on' : ''} ${b.enabled ? '' : 'disabled'}" data-kbsel="${esc(b.id)}">
      <span class="kb-label">${esc(b.label)}${b.enabled ? '' : '<span class="kb-off">off</span>'}</span>
      <span class="kb-chord">${kbChips(b.chord, false)}</span>
    </button>`).join('');
}
function currentBinding() { return S.kb[S.kbProfile].bindings.find(b => b.id === S.kbSel) || null; }

function kbEditorHTML() {
  const b = currentBinding();
  if (!b) return `<div class="kb-empty">Select a binding, or add a new one.</div>`;
  const appOpts = [['', 'All applications'], ...APP_ORDER.map(k => [k, APPS_BY_KEY[k].name])];
  const curApp = b.apps.length === 1 ? b.apps[0] : '';
  const targetOpts = [
    ...COMMON_ACTIONS.map(([id, lab]) => [`common:${id}`, lab]),
    ...KB_SETTINGS.map(([id, lab]) => [`setting:${id}`, `Setting — ${lab}`]),
  ];
  const curTarget = b.action.kind === 'common' ? `common:${b.action.target}` : `setting:${b.action.target}`;
  const modifiers = ['keyboard:ctrl', 'keyboard:shift', 'keyboard:alt', 'keyboard:meta'];
  const warn = b.chord.some(t => t.startsWith('keyboard:') && !modifiers.includes(t))
    ? `<p class="kb-warn">${esc(TIPS.passthrough)}</p>` : '';

  let actionRows = rowHTML('What it controls', ctrlSelectPlain('kbtarget', 'target', curTarget, targetOpts),
    '<span class="rs"></span>', 'Built-in navigation actions, or hold/toggle/cycle any keybindable setting.');
  if (b.action.kind === 'setting') {
    const meta = KB_SETTING_META[b.action.target];
    const ops = meta.kind === 'bool' ? [['toggle', 'Toggle between two values'], ['hold', 'Hold a value; restore on release']]
      : meta.kind === 'enum' ? [['cycle', 'Cycle through values'], ['hold', 'Hold a value; restore on release']]
      : [['add', 'Add an amount'], ['multiply', 'Multiply by a factor'], ['hold', 'Hold a value; restore on release']];
    actionRows += rowHTML('Behavior', ctrlSelectPlain('kbop', 'op', b.action.op, ops),
      '<span class="rs"></span>', 'How the chord changes the setting.');
    if (b.action.op === 'hold' || b.action.op === 'add' || b.action.op === 'multiply') {
      const valCtrl = (b.action.op === 'hold' && meta.ch)
        ? ctrlSelectPlain('kbval', 'value', b.action.value, meta.ch.map(c => [c, optLabel(c)]))
        : ctrlTextPlain('kbval', 'value', b.action.value ?? '', 'str');
      actionRows += rowHTML(b.action.op === 'hold' ? 'Value while held' : 'Amount', valCtrl,
        '<span class="rs"></span>', b.action.op === 'hold' ? 'Applied on press, restored on release.' : 'Applied on the press edge.');
    }
  }

  return `
  <div class="kb-editor-head">
    <span class="app-name">${esc(b.label || 'Binding')}</span>
    <span class="global-tag">${b.system ? 'System binding' : 'Custom'}</span>
    <span class="spacer"></span>
    <label class="switch" title="Enable or disable this binding without deleting it.">
      <input type="checkbox" data-kbenabled ${b.enabled ? 'checked' : ''}>
      <span class="track"></span><span class="sw-label">Enabled</span></label>
  </div>
  ${rowHTML('Label', ctrlTextPlain('kblabel', 'label', b.label, 'str'), '<span class="rs"></span>', 'Shown in the list and the control panel.')}
  <div class="row"><span class="rl">Chord</span>
    <span class="rc"><span class="chord-edit">${kbChips(b.chord, true)}</span></span>
    <span class="rs"></span>
    <span class="hint"><button class="btn btn-sm" data-kbchord>Record…</button> the controls pressed together.</span></div>
  ${warn}
  ${rowHTML('Extra modifiers', seg([['exact', 'Match exactly'], ['allow', 'Allow extra']], b.match, 'kbmatch'),
    '<span class="rs"></span>', 'Exact rejects other held modifiers; Allow tolerates them.')}
  ${rowHTML('Active app', ctrlSelectPlain('kbapp', 'app', curApp, appOpts), '<span class="rs"></span>',
    'Limit this binding to one focused host, or leave global.')}
  ${sect('Action')}
  ${actionRows}
  <div class="app-actions">
    <span class="spacer"></span>
    <button class="btn btn-sm btn-ghost" data-kbadvanced>Advanced DSL…</button>
    ${b.system ? '<button class="btn btn-sm" data-kbrestore>Restore System</button>'
               : '<button class="btn btn-sm btn-danger" data-kbdelete>Delete</button>'}
  </div>`;
}
function viewKeys() {
  const profs = Object.entries(S.kb).map(([id, p]) => [id, p.label]);
  return `
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex:none">
    <span class="rl" style="color:var(--text2)">Input profile</span>
    ${seg(profs, S.kbProfile, 'kbprofile')}
    <span class="note" style="margin:0">Two developer profiles; your edits are sparse patches per profile.</span>
  </div>
  <div id="cap-host">${capStripHTML()}</div>
  <div class="keys">
    <div class="keys-list">
      <div class="kb-scroll" id="kb-scroll">${kbListHTML()}</div>
      <button class="btn btn-sm kb-new" id="kb-new">New binding</button>
    </div>
    <div class="kb-editor" id="kb-editor">${kbEditorHTML()}</div>
  </div>`;
}

/* -------------------------------------------------------------- general */
const GENERAL_TABS = [
  ['device', 'Device & Orientation'], ['scheme', '3D Defaults'],
  ['pointer', 'Pointer'], ['panel', 'Control Panel'],
];
function genPaneHTML(anim) {
  const inner = { device: paneGenDevice, scheme: paneGenScheme, pointer: paneGenPointer, panel: paneGenPanel }[S.genTab]();
  return `<div class="${anim ? 'pane ' : ''}" data-pane="general-${S.genTab}">${inner}</div>`;
}
function viewGeneral() {
  return `
  <div class="subtabs">${seg(GENERAL_TABS, S.genTab, 'subtab')}</div>
  <div id="gen-pane-host">${genPaneHTML(false)}</div>`;
}

function paneGenDevice() {
  const src = S.orientation.source;
  const orientRows = [0, 1, 2].map(t => `
    <div class="orient-row">
      <span class="route-name">Logical ${AXIS[t]}</span>
      <span class="route-mid">uses physical</span>
      <span class="axis-seg">${AXIS.map((n, i) =>
        `<button data-orient="${t}" data-ax="${i}" data-ax-hl="${i}" class="${i === src[t] ? 'on' : ''}">${n}</button>`).join('')}</span>
      ${invPillOrient(t)}
    </div>`).join('');
  return `
  ${sect('Device')}
  ${rowHTML('Name', ctrlTextPlain('device', 'name', S.device.name, 'str', true), '<span class="rs"></span>',
    'Used to find the trackball when no address is set. Applies on reconnect.')}
  ${rowHTML('Address (optional)', ctrlTextPlain('device', 'address', S.device.address, 'str', true), '<span class="rs"></span>',
    'AA:BB:… pins one device; blank scans by name. Applies on reconnect.')}
  ${rowHTML('Bridge rate (Hz)', ctrlTextPlain('global', 'rate', gval('rate'), 'int'), miniResetGlobal('rate'),
    'Global default viewport rate for every 3D app; override per app under 3D Apps → Tuning.')}
  ${sect('Physical orientation', 'applied once, before pointer mode and every app')}
  ${orientRows}
  <div style="padding:7px 0 0; display:flex; align-items:center; gap:12px">
    <button class="btn btn-sm" id="btn-reset-orient">Reset orientation</button>
    <span class="note" style="margin:0">Picking a used source swaps the two axes, so the mapping
    always stays a valid permutation — hover a pick to preview it on the trackball.</span>
  </div>`;
}

function fbEditorHTML() {
  const chain = S.fallbacks;
  const fbItems = chain.length ? chain.map((m, i) => `
    <div class="fb-item ${i === S.fbSel ? 'sel' : ''} ${i === S.fbFlash ? 'just-moved' : ''}" data-fbsel="${i}">
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
  return `
  ${sect('3D defaults', 'the Global scheme; per-app overrides live under 3D Apps')}
  <p class="note" style="margin:0 0 8px">Orbit style, pivot, twist, zoom and every gain now live on the
  <b>Global defaults</b> row at the top of <b>3D Apps</b> — each app links to them or overrides. This tab keeps
  the two device-wide scheme controls that have no per-app form: the input mode the daemon starts in and the
  orbit-pivot fallback chain.</p>
  ${rowHTML('Startup input mode', ctrlSelectPlain('global', 'mode.default', gval('mode.default'),
    [['3d', '3D navigation'], ['pointer', 'Pointer (cursor)']]),
    miniResetGlobal('mode.default'), 'The mode the daemon starts in; a keybinding or the rail toggle switches it live.')}
  ${sect('Failure fallback order')}
  <div id="fb-host">${fbEditorHTML()}</div>`;
}

function paneGenPointer() {
  return `
  ${sect('Pointer / scroll', 'cursor mode')}
  ${rowHTML('Cursor sensitivity', ctrlTextPlain('global', 'pointer.gain', gval('pointer.gain'), 'num'),
    miniResetGlobal('pointer.gain'), 'Ball rotation → pixels of cursor travel.')}
  ${rowHTML('Scroll gain', ctrlTextPlain('global', 'scroll.gain', gval('scroll.gain'), 'num'),
    miniResetGlobal('scroll.gain'), 'Twist → wheel ticks.')}
  ${rowHTML('Scroll deadzone', ctrlTextPlain('global', 'scroll.deadzone', gval('scroll.deadzone'), 'num'),
    miniResetGlobal('scroll.deadzone'), 'Twist below this is ignored, so cursor moves don’t scroll.')}
  ${rowHTML('Scroll dominance', ctrlTextPlain('global', 'scroll.dominance', gval('scroll.dominance'), 'num'),
    miniResetGlobal('scroll.dominance'), 'How strongly twist must beat ball-plane motion to count as scroll.')}
  <p class="note">Physical buttons are handled by the device in HID mode and mapped under
  <b>Keybindings</b> (e.g. the XIAO test-bench buttons); there is no reserved-button table here.</p>`;
}

function paneGenPanel() {
  return `
  ${sect('Control panel', 'passive text HUD, bottom-right')}
  ${rowHTML('Show control panel', ctrlSwitchPlain('global', 'hud.visible', gval('hud.visible')),
    miniResetGlobal('hud.visible'), 'The always-available text readout of mode, focus and the last binding.')}
  ${rowHTML('Always on top', ctrlSwitchPlain('global', 'hud.top', gval('hud.top')),
    miniResetGlobal('hud.top'), 'Keep the panel above ordinary windows.')}
  ${rowHTML('Click through', ctrlSwitchPlain('global', 'hud.through', gval('hud.through')),
    miniResetGlobal('hud.through'), 'Let pointer input pass through the panel to what’s underneath.')}
  ${rowHTML('Opacity', ctrlTextPlain('global', 'hud.opacity', gval('hud.opacity'), 'num'),
    miniResetGlobal('hud.opacity'), '0.2 – 1.0.')}
  ${rowHTML('Screen margin (px)', ctrlTextPlain('global', 'hud.margin', gval('hud.margin'), 'int'),
    miniResetGlobal('hud.margin'), 'Distance from the monitor work-area edges.')}
  ${rowHTML('Last-binding timeout (s)', ctrlTextPlain('global', 'hud.timeout', gval('hud.timeout'), 'num'),
    miniResetGlobal('hud.timeout'), 'How long the last-used binding stays shown after release.')}`;
}

/* ====================================================== render machinery */
const VIEWS = { overview: viewOverview, apps: viewApps, keys: viewKeys, general: viewGeneral };

function render() {
  $('#view').innerHTML = VIEWS[S.page]();
  $$('#rnav button').forEach(b => b.classList.toggle('active', b.dataset.page === S.page));
  positionRailTick();
  initSegThumbs($('#view'));
  S.fbFlash = -1;
}
function renderAppPane(anim = true) {
  const host = $('#app-pane-host');
  if (!host) return;
  host.innerHTML = appPaneHTML(S.app, S.appTab[S.app] || (S.app === GLOBAL ? 'tuning' : 'setup'), anim);
  initSegThumbs(host);
}
function renderAppDetail() {
  const el = $('#app-detail');
  if (!el) return;
  el.innerHTML = appDetailHTML(S.app);
  initSegThumbs(el);
}
function refreshAppList() { const l = $('.app-list'); if (l) l.innerHTML = appListHTML(); }
function refreshAppListItem(key) { const b = $(`.app-item[data-appsel="${key}"]`); if (b) b.innerHTML = appItemInner(APPS_BY_KEY[key]); }
function refreshAppsPage() { if (S.page !== 'apps') return; refreshAppList(); renderAppDetail(); }

function renderGenPane(anim = true) {
  const host = $('#gen-pane-host');
  if (!host) return;
  host.innerHTML = genPaneHTML(anim);
  initSegThumbs(host);
  S.fbFlash = -1;
}
function renderFbEditor() { const h = $('#fb-host'); if (h) { h.innerHTML = fbEditorHTML(); S.fbFlash = -1; } }

function renderKbBody() {
  const cap = $('#cap-host'); if (cap) cap.innerHTML = capStripHTML();
  const list = $('#kb-scroll'); if (list) list.innerHTML = kbListHTML();
  renderKbEditor();
}
function renderKbEditor() { const ed = $('#kb-editor'); if (ed) { ed.innerHTML = kbEditorHTML(); initSegThumbs(ed); } }
function refreshKbListItem() { const l = $('#kb-scroll'); if (l) l.innerHTML = kbListHTML(); }

function positionRailTick() {
  const active = $('#rnav button.active'), tick = $('#rnav-tick');
  if (!active || !tick) return;
  tick.style.transform = `translateY(${active.offsetTop + active.offsetHeight / 2 - 1}px) rotate(var(--tilt))`;
}
function setMode(mode) {
  S.mode = mode;
  document.body.dataset.mode = mode;
  $$('#mode-toggle button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  $('#mode-thumb').style.transform = mode === 'cube' ? 'translateX(76px)' : 'translateX(0)';
  const fact = $('#fact-mode');
  if (fact) fact.firstChild.textContent = (mode === 'cube' ? '3D navigation' : 'Pointer');
}

/* ------------------------------------------------------------ clipboard */
function copyText(text, okMsg = 'Copied to clipboard') {
  const done = ok => toast(ok ? okMsg : 'Copy failed — select and copy manually');
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => done(true), () => fallback());
  } else fallback();
  function fallback() {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { /* no-op */ }
    ta.remove(); done(ok);
  }
}

/* ---------------------------------------------------------------- toast */
function toast(msg, ms = 2400) {
  const el = document.createElement('div');
  el.className = 'toast'; el.textContent = msg;
  $('#toast-root').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, ms);
}

/* ---------------------------------------------------------------- modal */
function openModal({ title, body, buttons, copyables = [], dontAgain = null, html = null }) {
  const root = $('#modal-root');
  const btns = buttons || [{ label: 'OK', kind: 'btn-primary' }];
  root.innerHTML = `
  <div class="modal-backdrop">
    <div class="modal" role="dialog" aria-label="${esc(title)}">
      <div class="modal-head">${esc(title)}</div>
      <div class="modal-body">${html != null ? html : esc(body)}</div>
      ${copyables.length ? `<div class="modal-foot" style="padding-bottom:2px">
        ${copyables.map((c, i) => `<button class="btn btn-sm" data-modal-copy="${i}">${esc(c[0])}</button>`).join('')}
        <span class="modal-copy-status" id="modal-copy-status"></span></div>` : ''}
      ${dontAgain ? `<label class="switch dont-again"><input type="checkbox" id="modal-dont-again">
        <span class="track"></span><span class="sw-label">Do not show again</span></label>` : ''}
      <div class="modal-foot"><span class="spacer"></span>
        ${btns.map((b, i) => `<button class="btn ${b.kind || ''}" data-modal-btn="${i}">${esc(b.label)}</button>`).join('')}
      </div>
    </div>
  </div>`;
  const close = () => { root.innerHTML = ''; };
  $('.modal-backdrop', root).addEventListener('click', e => { if (e.target === e.currentTarget) finish(null); });
  function finish(btn) {
    if (dontAgain && $('#modal-dont-again')?.checked) S.global[dontAgain] = true;
    if (btn && btn.onClick && btn.onClick() === false) return;
    close();
  }
  $$('[data-modal-btn]', root).forEach(el => el.addEventListener('click', () => finish(btns[+el.dataset.modalBtn])));
  $$('[data-modal-copy]', root).forEach(el => el.addEventListener('click', () => {
    const [label, payload] = copyables[+el.dataset.modalCopy];
    copyText(payload, 'Copied: ' + label);
    const st = $('#modal-copy-status'); if (st) st.textContent = 'Copied: ' + label;
  }));
  return { close };
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
    dontAgain: warn ? 'onshape.warn' : null,
  });
}

function runSetup(key) {
  const dialogs = {
    blender: () => openModal({
      title: 'Blender — auto-start?',
      body: 'Install the Trackball add-on into Blender\'s add-ons folder (every detected version).\n\n' +
            'Auto-enable it on every Blender launch? This also writes a small startup script ' +
            '(scripts/startup/trackball_nav_startup.py) — the analogue of Fusion\'s "Run on Startup".',
      buttons: [
        { label: 'Yes — install + auto-enable', kind: 'btn-primary', onClick: () => {
          openModal({ title: 'Integration', body: 'Blender add-on v0.1.12 installed to every detected version and the auto-enable startup shim was written.\n\nNew add-on code takes effect on Blender\'s next launch.' }); return false; } },
        { label: 'No — install only', onClick: () => {
          openModal({ title: 'Integration', body: 'Blender add-on v0.1.12 installed.\n\nEnable Trackball Nav once in Preferences → Add-ons.' }); return false; } },
        { label: 'Cancel', kind: 'btn-ghost', onClick: () => toast('Blender setup cancelled') } ] }),
    unreal: () => openModal({
      title: 'Integration',
      body: 'TrackballNav updated to v0.2.4 in C:\\Program Files\\Epic Games\\UE_5.9\\Engine\\Plugins\\TrackballNav.\n\nNew plugin code takes effect on the editor\'s next launch.',
      copyables: [['Copy project-Plugins path', '<YourProject>\\Plugins\\TrackballNav']],
      buttons: [{ label: 'OK', kind: 'btn-primary', onClick: () => {
        const a = APPS_BY_KEY.unreal;
        a.status = { chip: 'warn', text: 'installed · v0.2.4 — restart editor', short: 'restart' };
        a.action = 'Reinstall'; refreshAppsPage(); } }] }),
    unity: () => openModal({
      title: 'Integration',
      body: 'Package copied into 1 project:\n  D:\\Projects\\Sandbox\\Packages\\com.astrolabe.trackball-nav\n\nLet Unity reimport (or restart the editor); the row shows connected once the Scene view handshakes.',
      copyables: [['Copy package path', '<YourProject>\\Packages\\com.astrolabe.trackball-nav']],
      buttons: [{ label: 'OK', kind: 'btn-primary', onClick: () => {
        const a = APPS_BY_KEY.unity;
        a.status = { chip: 'idle', text: 'installed · v0.1.6', short: 'installed' };
        a.action = 'Reinstall'; S.apps.unity.enabled = true; refreshAppsPage(); } }] }),
    onshape: () => openModal({
      title: 'Onshape — Set up',
      body: 'Bridge certificate generated at %APPDATA%\\TrackballDaemon\\onshape_cert.pem (no trust store was touched).\n\n1. Trust the cert (no admin): run the certutil command below and click Yes.\n2. Enable SpaceMouse / 3Dconnexion in Onshape → Account → Preferences.\n3. Optional: install the pointer userscript if you want Under Cursor orbit.\n\n' + USERSCRIPT_STEPS,
      copyables: [['Copy certutil command', CERTUTIL], ['Copy userscript', USERSCRIPT_PLACEHOLDER]],
      buttons: [{ label: 'OK', kind: 'btn-primary', onClick: () => {
        const a = APPS_BY_KEY.onshape;
        a.status = { chip: 'idle', text: 'bridge ready — awaiting browser', short: 'ready' };
        a.action = null; S.apps.onshape.enabled = true; refreshAppsPage(); } }] }),
    autocad: () => openModal({
      title: 'Integration',
      body: 'TrackballNavAcad.dll staged to %APPDATA%\\TrackballDaemon\\acad_plugin. The folder was added to AutoCAD\'s TRUSTEDPATHS; the plugin NETLOADs automatically when the daemon attaches to a running AutoCAD.\n\nNote: detected AutoCAD 2024 is outside the supported .NET 8 family (2025–2027) — the plugin may fail to load there. Type TBNAV in AutoCAD to check.' }),
    fusion360: () => openModal({
      title: 'Integration',
      body: 'TrackballNav v0.1.16 copied to %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\TrackballNav.\n\nIn Fusion: Utilities → Add-Ins (Shift+S) → run TrackballNav and tick Run on Startup (one-time click — Fusion won\'t let an installer set it).' }),
  };
  const generic = () => openModal({
    title: 'Integration',
    body: `${APPS_BY_KEY[key].name} add-on reinstalled.\n\nNew add-on code takes effect on the app's next launch (or stop/run the add-on).` });
  (dialogs[key] || generic)();
}

/* chord recording modal */
function openChordModal() {
  const b = currentBinding();
  if (!b) return;
  const groups = TOKEN_GROUPS.map(([name, toks]) => `
    <div class="token-group"><h5>${esc(name)}</h5><div class="token-wrap">
      ${toks.map(t => `<button class="token-btn ${b.chord.includes(t) ? 'on' : ''}" data-token="${esc(t)}">${esc(tokenLabel(t))}</button>`).join('')}
    </div></div>`).join('');
  openModal({
    title: 'Record chord',
    html: `<div class="token-groups">${groups}</div>
      <p class="note" style="margin-top:8px">Pick every control pressed together. Modifier keys match either side.</p>`,
    buttons: [{ label: 'Done', kind: 'btn-primary' }],
  });
  $$('[data-token]', $('#modal-root')).forEach(btn => btn.addEventListener('click', () => {
    const t = btn.dataset.token, cur = currentBinding();
    const i = cur.chord.indexOf(t);
    if (i >= 0) cur.chord.splice(i, 1); else cur.chord.push(t);
    btn.classList.toggle('on');
    renderKbEditor(); refreshKbListItem();
  }));
}

/* advanced DSL modal (read-only JSON view for the demo) */
function openAdvancedModal() {
  const b = currentBinding();
  if (!b) return;
  const dsl = {
    id: b.id, label: b.label, enabled: b.enabled, chord: b.chord,
    match: b.match === 'allow' ? 'allow_extra_modifiers' : 'exact', activation: 'hold', priority: 0,
    when: b.apps.length ? { apps: b.apps } : undefined, action: b.action,
  };
  openModal({
    title: 'Advanced binding DSL',
    html: `<p class="note" style="margin:0 0 8px">The complete declarative binding. In the daemon this editor
      exposes priority, multi-app contexts, executables, and custom action lists; executable code and unknown
      targets are rejected. This demo shows it read-only.</p>
      <pre style="margin:0;white-space:pre;overflow:auto;font-family:var(--mono);font-size:11px;color:var(--text2)">${esc(JSON.stringify(dsl, null, 2))}</pre>`,
    buttons: [{ label: 'Close', kind: 'btn-primary' }],
  });
}

/* --------------------------------------------------------------- events */
document.addEventListener('click', e => {
  const t = e.target;

  const nav = t.closest('#rnav button[data-page]');
  if (nav) { S.page = nav.dataset.page; history.replaceState(null, '', '#' + S.page); render(); return; }

  const mode = t.closest('#mode-toggle button');
  if (mode) { setMode(mode.dataset.mode); return; }

  if (t.closest('#btn-hide')) { toast('Window hidden to tray — the daemon keeps running (demo)'); return; }

  if (t.closest('#btn-quit')) {
    openModal({ title: 'Quit Astrolabe?',
      body: 'The daemon stops and the settings window closes. The trackball falls back to its onboard Bluetooth HID mouse until the daemon runs again.',
      buttons: [{ label: 'Quit', kind: 'btn-danger', onClick: () => toast('Demo — the daemon keeps running') },
                { label: 'Cancel', kind: 'btn-ghost' }] });
    return;
  }
  if (t.closest('#btn-recenter')) { toast('Recenter sent to the focused viewport (demo)'); return; }

  const goto = t.closest('[data-goto-app]');
  if (goto) { S.page = 'apps'; S.app = goto.dataset.gotoApp; history.replaceState(null, '', '#apps:' + S.app); render(); return; }

  /* per-app active-control slider */
  const ms = t.closest('button[data-mslide]');
  if (ms) {
    const slider = ms.closest('[data-mslider]');
    const key = slider.dataset.mslider, val = ms.dataset.mslide, name = APPS_BY_KEY[key].name;
    if (val === 'off') { if (S.apps[key].enabled) toast(`${name} disabled — motion is no longer routed to it`); S.apps[key].enabled = false; }
    else { if (!S.apps[key].enabled) toast(`${name} enabled — ${val} while it has focus`); S.apps[key].enabled = true; if (PROFILES[key].rich) S.apps[key].mode = val; }
    slider.className = slider.className.replace(/ms-\w+/, 'ms-' + val);
    selectSegButton(slider, ms);
    refreshAppListItem(key);
    return;
  }

  /* sub-tabs (3D Apps detail + General) */
  const sub = t.closest('button[data-subtab]');
  if (sub) {
    selectSegButton(sub.closest('.seg'), sub);
    if (S.page === 'apps') {
      S.appTab[S.app] = sub.dataset.subtab;
      history.replaceState(null, '', `#apps:${S.app === GLOBAL ? 'global' : S.app}.${sub.dataset.subtab}`);
      renderAppPane(true);
    } else { S.genTab = sub.dataset.subtab; history.replaceState(null, '', '#general:' + S.genTab); renderGenPane(true); }
    return;
  }

  /* routing mode tabs */
  const rt = t.closest('button[data-routetab]');
  if (rt) {
    S.routeTab[S.app] = rt.dataset.routetab;
    selectSegButton(rt.closest('.seg'), rt);
    const host = $('#routes-host'); if (host) host.innerHTML = routesHTML(S.app);
    return;
  }

  /* keybinding profile switch */
  const kp = t.closest('button[data-kbprofile]');
  if (kp) {
    S.kbProfile = kp.dataset.kbprofile;
    const first = S.kb[S.kbProfile].bindings[0];
    S.kbSel = first ? first.id : null;
    selectSegButton(kp.closest('.seg'), kp);
    renderKbBody();
    return;
  }

  /* app list selection */
  const appSel = t.closest('[data-appsel]');
  if (appSel) {
    S.app = appSel.dataset.appsel;
    history.replaceState(null, '', '#apps:' + (S.app === GLOBAL ? 'global' : S.app));
    $$('.app-item').forEach(b => b.classList.toggle('on', b.dataset.appsel === S.app));
    renderAppDetail();
    return;
  }

  /* keybinding list selection */
  const kbsel = t.closest('[data-kbsel]');
  if (kbsel) {
    S.kbSel = kbsel.dataset.kbsel;
    $$('.kb-item').forEach(b => b.classList.toggle('on', b.dataset.kbsel === S.kbSel));
    renderKbEditor();
    return;
  }

  /* link button: link ↔ unlink this app's setting from the Global default */
  const link = t.closest('[data-link]');
  if (link) {
    const id = link.dataset.link, key = link.dataset.linkapp;
    if (appLinked(key, id)) S.apps[key].ov[id] = appEff(key, id);
    else delete S.apps[key].ov[id];
    renderAppPane(false);
    return;
  }

  /* per-app reset to the shipped System default (breaks the Global link) */
  const sr = t.closest('[data-sysreset]');
  if (sr) {
    const id = sr.dataset.sysreset, key = sr.dataset.sysapp;
    S.apps[key].ov[id] = appSysDefault(key, id);
    renderAppPane(false);
    return;
  }

  /* Global-layer reset to System default */
  const gr = t.closest('[data-greset]');
  if (gr) {
    delete S.global[gr.dataset.greset];
    if (S.page === 'apps') renderAppPane(false); else renderGenPane(false);
    return;
  }

  /* routing reset */
  const rr = t.closest('[data-rreset]');
  if (rr) {
    const [srcId, invId, src, inv] = rr.dataset.rreset.split('|');
    const store = routeStoreFor(S.app);
    store.src[srcId] = +src; store.inv[invId] = inv === '1';
    const host = $('#routes-host'); if (host) host.innerHTML = routesHTML(S.app);
    return;
  }

  /* axis source pick */
  const ax = t.closest('[data-axpick]');
  if (ax) {
    routeStoreFor(S.app).src[ax.dataset.axpick] = +ax.dataset.ax;
    $$('button', ax.parentElement).forEach(b => b.classList.toggle('on', b === ax));
    refreshRouteRowReset(ax);
    return;
  }
  const invr = t.closest('[data-invroute]');
  if (invr) {
    const store = routeStoreFor(S.app), id = invr.dataset.invroute;
    store.inv[id] = !store.inv[id];
    invr.classList.toggle('on', store.inv[id]);
    refreshRouteRowReset(invr);
    return;
  }

  /* global physical orientation pick — swap to preserve the permutation */
  const or = t.closest('[data-orient]');
  if (or) {
    const target = +or.dataset.orient, wanted = +or.dataset.ax, src = S.orientation.source;
    const other = src.indexOf(wanted);
    [src[target], src[other]] = [src[other], src[target]];
    $$('[data-orient]').forEach(b => b.classList.toggle('on', src[+b.dataset.orient] === +b.dataset.ax));
    return;
  }
  const invo = t.closest('[data-invorient]');
  if (invo) {
    const i = +invo.dataset.invorient;
    S.orientation.invert[i] = !S.orientation.invert[i];
    invo.classList.toggle('on', S.orientation.invert[i]);
    return;
  }

  const ci = t.closest('[data-copy-instructions]');
  if (ci) {
    const a = APPS_BY_KEY[ci.dataset.copyInstructions];
    copyText(`Install model\n${a.installModel}\n\nSecurity\n${a.security}\n\nAutomatic setup\n${a.instructions.auto}\n\n` +
      `Manual setup / restricted permissions\n${a.instructions.manual}\n\nHealth check\n${a.instructions.health}`);
    return;
  }
  const setup = t.closest('[data-setup]');
  if (setup) { runSetup(setup.dataset.setup); return; }
  if (t.closest('[data-onshape-userscript]')) { copyText(USERSCRIPT_PLACEHOLDER, 'Userscript copied'); onshapeUserscriptDialog(); return; }

  if (t.closest('#btn-reset-orient')) {
    S.orientation = { source: [0, 1, 2], invert: [false, false, false] };
    renderGenPane(false); toast('Orientation reset to identity'); return;
  }

  /* reset whole app (relink all overrides) */
  const ra = t.closest('[data-reset-app]');
  if (ra) {
    const key = ra.dataset.resetApp, name = APPS_BY_KEY[key].name;
    openModal({ title: 'Reset overrides?',
      body: `Relink every ${name} setting to the Global defaults and clear its routing overrides?\n\nMode, enable, and install state are preserved.`,
      buttons: [
        { label: 'Reset', kind: 'btn-danger', onClick: () => {
          S.apps[key].ov = {}; S.apps[key].route = freshRoute(key); delete S.routeTab[key];
          renderAppDetail(); toast(`${name} settings relinked to Global`); } },
        { label: 'Cancel', kind: 'btn-ghost' }] });
    return;
  }
  if (t.closest('[data-reset-global]')) {
    openModal({ title: 'Reset Global defaults?',
      body: 'Reset every Global setting to its shipped System default. Per-app overrides are untouched.',
      buttons: [
        { label: 'Reset', kind: 'btn-danger', onClick: () => {
          S.global = {}; S.globalRoute = freshRich(); delete S.routeTab[GLOBAL];
          renderAppDetail(); toast('Global defaults reset to System'); } },
        { label: 'Cancel', kind: 'btn-ghost' }] });
    return;
  }

  /* fallback-chain editor */
  const fbi = t.closest('[data-fbsel]');
  if (fbi) { S.fbSel = +fbi.dataset.fbsel; renderFbEditor(); return; }
  const fbm = t.closest('[data-fbmove]');
  if (fbm) {
    const d = +fbm.dataset.fbmove, chain = S.fallbacks, i = S.fbSel;
    if (i >= 0 && i + d >= 0 && i + d < chain.length) {
      [chain[i], chain[i + d]] = [chain[i + d], chain[i]];
      S.fbSel = i + d; S.fbFlash = i + d; renderFbEditor();
    }
    return;
  }
  if (t.closest('[data-fbremove]')) {
    const chain = S.fallbacks;
    if (S.fbSel >= 0 && S.fbSel < chain.length) { chain.splice(S.fbSel, 1); S.fbSel = Math.min(S.fbSel, chain.length - 1); renderFbEditor(); }
    return;
  }
  if (t.closest('[data-fbadd]')) {
    const sel = $('#fb-add-sel'), chain = S.fallbacks;
    if (sel && sel.value && !chain.includes(sel.value)) { chain.push(sel.value); S.fbSel = chain.length - 1; S.fbFlash = S.fbSel; renderFbEditor(); }
    return;
  }

  /* keybinding editor actions */
  const km = t.closest('button[data-kbmatch]');
  if (km) { const b = currentBinding(); if (b) { b.match = km.dataset.kbmatch; selectSegButton(km.closest('.seg'), km); } return; }
  if (t.closest('[data-kbchord]')) { openChordModal(); return; }
  if (t.closest('[data-kbadvanced]')) { openAdvancedModal(); return; }
  if (t.closest('#kb-new')) {
    const bindings = S.kb[S.kbProfile].bindings;
    let n = 1; while (bindings.some(b => b.id === `user.binding.${n}`)) n++;
    const nb = { id: `user.binding.${n}`, label: 'New binding', enabled: true, chord: [], match: 'exact',
      apps: [], action: { kind: 'common', target: 'input.toggle' }, system: false };
    bindings.push(nb); S.kbSel = nb.id; refreshKbListItem(); renderKbEditor();
    return;
  }
  if (t.closest('[data-kbdelete]')) {
    const b = currentBinding();
    if (b) { const arr = S.kb[S.kbProfile].bindings; arr.splice(arr.indexOf(b), 1); S.kbSel = arr.length ? arr[0].id : null; renderKbBody(); }
    return;
  }
  if (t.closest('[data-kbrestore]')) {
    const b = currentBinding();
    if (b && b.system) {
      const seed = seedProfiles()[S.kbProfile].bindings.find(x => x.id === b.id);
      if (seed) { Object.assign(b, JSON.parse(JSON.stringify(seed))); renderKbBody(); toast('System binding restored'); }
    }
    return;
  }
});

document.addEventListener('change', e => {
  const t = e.target;

  /* keybinding editor fields */
  if (t.hasAttribute('data-kbenabled')) { const b = currentBinding(); if (b) { b.enabled = t.checked; refreshKbListItem(); } return; }
  const scope = t.dataset.scope;
  if (scope === 'kblabel') { const b = currentBinding(); if (b) { b.label = t.value; refreshKbListItem(); } return; }
  if (scope === 'kbapp') { const b = currentBinding(); if (b) b.apps = t.value ? [t.value] : []; return; }
  if (scope === 'kbtarget') {
    const b = currentBinding();
    if (b) {
      const v = t.value;
      if (v.startsWith('common:')) b.action = { kind: 'common', target: v.slice(7) };
      else {
        const id = v.slice(8), meta = KB_SETTING_META[id];
        const op = meta.kind === 'bool' ? 'toggle' : meta.kind === 'enum' ? 'cycle' : 'add';
        const value = op === 'add' ? 1 : '';
        b.action = { kind: 'setting', target: id, op, value };
      }
      renderKbEditor(); refreshKbListItem();
    }
    return;
  }
  if (scope === 'kbop') {
    const b = currentBinding();
    if (b) {
      b.action.op = t.value;
      const meta = KB_SETTING_META[b.action.target];
      if (b.action.op === 'hold' && meta.ch) b.action.value = meta.ch[0];
      else if (b.action.op === 'add' || b.action.op === 'multiply') b.action.value = 1;
      else b.action.value = '';
      renderKbEditor(); refreshKbListItem();
    }
    return;
  }
  if (scope === 'kbval') { const b = currentBinding(); if (b) { b.action.value = t.value; refreshKbListItem(); } return; }

  if (!scope) return;

  if (scope === 'login') { S.startAtLogin = t.checked; toast(`Start at login ${t.checked ? 'enabled — HKCU Run key written' : 'disabled'} (demo)`); return; }
  if (scope === 'device') { S.device[t.dataset.id] = t.value; return; }

  const id = t.dataset.id, kind = t.dataset.kind;
  const parsed = parseValue(t, kind);
  if (parsed === undefined) return;

  if (scope === 'global') {
    if (parsed === SYS[id]) delete S.global[id]; else S.global[id] = parsed;
    refreshRowResetGlobal(t, id);
  } else if (scope === 'app') {
    const key = t.dataset.app;
    S.apps[key].ov[id] = parsed;
    if (id === 'orbit.style' && parsed === 'free') {
      const p = PROFILES[key];
      if (p.twist.includes('roll') && appEff(key, 'twist') !== 'roll') {
        S.apps[key].ov['twist'] = 'roll';
        renderAppPane(false); toast('Twist action set to Roll for free orbit'); return;
      }
    }
    if (t.dataset.warn === 'onshape-cursor' && parsed === 'cursor' && !S.global['onshape.warn']) {
      onshapeUserscriptDialog({ warn: true });
    }
    refreshAppRowReset(t, key, id);
  }
});

function parseValue(t, kind) {
  if (kind === 'bool') return t.checked;
  if (kind === 'str' || kind === 'enum') return t.value;
  if (kind === 'rate') {
    const s = t.value.trim().toLowerCase();
    let v;
    if (s === '' || s === 'default' || s === '0') v = 0;
    else { v = parseInt(parseFloat(s), 10); if (Number.isNaN(v)) { t.value = 'Default'; return undefined; } v = Math.max(1, Math.min(240, v)); }
    t.value = (!v ? 'Default' : String(v));
    return v;
  }
  const v = kind === 'int' ? parseInt(t.value, 10) : parseFloat(t.value);
  if (Number.isNaN(v)) { t.value = fmt(t.dataset.scope === 'global' ? gval(t.dataset.id) : SYS[t.dataset.id]); return undefined; }
  t.value = fmt(v);
  return v;
}

function refreshRowResetGlobal(el, id) {
  const rowEl = el.closest('.row');
  const btn = rowEl && rowEl.querySelector('.mini-reset[data-greset]');
  if (btn) btn.classList.toggle('show', gDiverged(id));
}
function refreshAppRowReset(el, key, id) {
  const rowEl = el.closest('.row');
  const btn = rowEl && rowEl.querySelector('.mini-reset[data-sysreset]');
  if (btn) btn.classList.toggle('show', appDivergesSys(key, id));
}
function refreshRouteRowReset(el) {
  const rowEl = el.closest('.route-row');
  const btn = rowEl && rowEl.querySelector('.mini-reset[data-rreset]');
  if (!btn) return;
  const [srcId, invId, src, inv] = btn.dataset.rreset.split('|');
  const store = routeStoreFor(S.app);
  btn.classList.toggle('show', store.src[srcId] !== +src || store.inv[invId] !== (inv === '1'));
}

/* axis-control hover → highlight that axis on the rail trackball */
const railBall = () => $('#rail-ball');
document.addEventListener('mouseover', e => {
  const el = e.target.closest('[data-ax-hl]');
  if (!el) return;
  const svg = railBall(); svg.classList.remove('hl-0', 'hl-1', 'hl-2'); svg.classList.add('hl-' + el.dataset.axHl);
});
document.addEventListener('mouseout', e => {
  if (!e.target.closest('[data-ax-hl]')) return;
  railBall().classList.remove('hl-0', 'hl-1', 'hl-2');
});

/* ------------------------------------------------------------------ boot */
(function initFromHash() {
  const [page, arg] = location.hash.replace(/^#/, '').split(':');
  if (PAGES[page]) S.page = page;
  if (page === 'apps' && arg) {
    const [app, tab] = arg.split('.');
    if (app === 'global') S.app = GLOBAL; else if (APPS_BY_KEY[app]) S.app = app;
    if (['setup', 'tuning', 'nav', 'routing'].includes(tab)) S.appTab[S.app] = tab;
  }
  if (page === 'general' && ['device', 'scheme', 'pointer', 'panel'].includes(arg)) S.genTab = arg;
})();
S.kbSel = S.kb[S.kbProfile].bindings[0].id;
setMode(gval('mode.default') === 'pointer' ? 'cursor' : 'cube');
render();
window.addEventListener('resize', () => { positionRailTick(); initSegThumbs(); });
