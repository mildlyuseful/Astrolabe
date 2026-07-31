/* SPDX-FileCopyrightText: 2026 Dylan Lee
 * SPDX-License-Identifier: Apache-2.0
 *
 * Astrolabe — "Vernier" UI study.
 * Static mirror of the daemon's user-facing surface: setting registry, app registry,
 * system defaults, device descriptors, keybinding profiles, and a frozen runtime snapshot.
 * Transcribed from trackball_daemon/{settings_schema,app_registry,system_defaults.json,
 * integrations,devices/descriptor_data,system_keybinding_profiles}.py|json. Nothing here talks
 * to a daemon; every value is local state in the page.
 */

const PRODUCT = {
  name: "Astrolabe",
  publisher: "Mildly Useful",
  version: "0.2.0a1",
  channel: "internal-alpha",
  configDir: "%APPDATA%\\Mildly Useful\\Astrolabe",
  logFile: "%APPDATA%\\Mildly Useful\\Astrolabe\\daemon.log",
};

const CATEGORY_TITLES = {
  device: "Device",
  hud: "Control panel",
  input: "Input",
  transform: "Physical transform",
  pointer: "Pointer",
  orbit: "Orbit",
  sensitivity: "Sensitivity & rate",
  pan_zoom: "Pan / Zoom",
  navigation: "Navigation",
  camera: "Camera",
  routing: "Axis routing",
};

const OPTION_LABELS = {
  default: "Default", free: "Free", turntable: "Turntable",
  orbit: "Orbit", fly: "Fly", walk: "Walk",
  roll: "Roll", zoom: "Zoom", dolly: "Dolly", none: "None",
  "3d": "3D", pointer: "Pointer", cube: "Cube", cursor_mode: "Cursor",
  to_center: "To Center", to_object: "To Object", to_cursor: "To Cursor",
  camera: "Camera", screen_center: "Screen Center", cursor: "Under Cursor (mouse)",
  selection: "Selection", cursor_3d: "3D Cursor", object: "Model Center", origin: "World Origin",
};

const AXIS_LABELS = { 0: "Axis 0 (X)", 1: "Axis 1 (Y)", 2: "Axis 2 (Z)" };
const ROUTE_AXIS_LABELS = { 0: "X", 1: "Y", 2: "Z" };

/* ------------------------------------------------------------------ settings */
/* scope: "device" | "global_only" | "global_and_app"
 * cap:   capability keys an app must expose before the setting applies to it   */

function spec(id, kind, cat, label, o = {}) {
  return {
    id, kind, cat, label,
    help: o.help || label,
    scope: o.scope || "global_and_app",
    cap: o.cap || [],
    choices: o.choices || null,
    min: o.min ?? null,
    max: o.max ?? null,
    nullable: !!o.nullable,
    control: o.control || (kind === "boolean" ? "checkbox" : kind === "enum" ? "choice" : "entry"),
  };
}

const SETTINGS = [
  spec("device.name", "string", "device", "Device name",
    { scope: "device", help: "BLE advertised name; applies on reconnect." }),
  spec("device.address", "string", "device", "Device address",
    { scope: "device", help: "Optional BLE address; applies on reconnect." }),

  spec("hud.visible", "boolean", "hud", "Show control panel",
    { scope: "global_only", help: "Show the persistent text control panel." }),
  spec("hud.always_on_top", "boolean", "hud", "Always on top",
    { scope: "global_only", help: "Keep the control panel above ordinary windows." }),
  spec("hud.click_through", "boolean", "hud", "Click through",
    { scope: "global_only", help: "Allow pointer input to pass through the control panel." }),
  spec("hud.opacity", "number", "hud", "Opacity",
    { scope: "global_only", min: 0.2, max: 1.0, help: "Control-panel opacity from 0.2 to 1.0." }),
  spec("hud.margin", "integer", "hud", "Screen margin",
    { scope: "global_only", min: 0, max: 200, help: "Distance in pixels from the monitor work-area edges." }),
  spec("hud.last_binding_timeout", "number", "hud", "Last binding timeout",
    { scope: "global_only", min: 0, max: 30, help: "Seconds to retain the last-used binding after release." }),

  spec("input.mode.default", "enum", "input", "Default input mode", {
    scope: "global_only", choices: ["3d", "pointer"],
    help: "Input mode used when no runtime binding overrides it; legacy values migrate.",
  }),
  spec("pointer.cursor.gain", "number", "pointer", "Pointer sensitivity",
    { scope: "global_only", min: 0, help: "Ball rotation to cursor pixels." }),
  spec("pointer.scroll.gain", "number", "pointer", "Scroll gain",
    { scope: "global_only", min: 0, help: "Twist to wheel notches." }),
  spec("pointer.scroll.deadzone", "number", "pointer", "Scroll deadzone",
    { scope: "global_only", min: 0, help: "Radians per packet below which the wheel stays put." }),
  spec("pointer.scroll.dominance", "number", "pointer", "Scroll dominance",
    { scope: "global_only", min: 0, help: "How far twist must exceed planar motion before it scrolls." }),

  spec("navigation.orbit.pivot_fallbacks", "string_list", "orbit", "Orbit pivot fallback order", {
    scope: "global_only", control: "ordered_list",
    choices: ["camera", "screen_center", "cursor", "selection", "cursor_3d", "object", "origin"],
    help: "Tried in order when the app's orbit pivot is unsupported or a raycast misses. Empty means no orbit after the primary target fails.",
  }),

  spec("navigation.refresh_rate", "integer", "sensitivity", "Viewport refresh rate",
    { cap: ["rate"], min: 0, max: 240, help: "Navigation packets per second sent to this host." }),
  spec("navigation.orbit.pivot_hold_seconds", "number", "orbit", "Orbit pivot hold",
    { cap: ["orbit_hold"], min: 0, help: "Seconds a ray-derived orbit target is retained before recapture." }),
  spec("navigation.zoom.cursor_hold_seconds", "number", "pan_zoom", "Zoom cursor hold",
    { cap: ["zoom_hold"], min: 0, help: "Independent hold time for the To Cursor zoom target." }),
  spec("navigation.orbit.selection_override", "boolean", "orbit", "Selection overrides orbit center",
    { cap: ["selection_override"], help: "When a selection exists, use its center before the configured pivot. Camera remains turn-in-place." }),
  spec("navigation.level_horizon_on_entry", "boolean", "orbit", "Level horizon on mode entry",
    { cap: ["level_horizon"], nullable: true, help: "Remove camera roll once when entering Turntable, Lock Horizon, or Walk." }),
  spec("navigation.orbit.sensitivity", "number", "sensitivity", "Orbit sensitivity",
    { cap: ["orbit_sensitivity"], min: 0 }),
  spec("navigation.pan.gain", "number", "sensitivity", "Pan gain", { cap: ["pan_gain"], min: 0 }),
  spec("navigation.zoom.gain", "number", "sensitivity", "Zoom gain", { cap: ["zoom_gain"], min: 0 }),
  spec("navigation.zoom.dominance", "number", "sensitivity", "Zoom dominance",
    { cap: ["zoom_dominance"], min: 0, help: "How strongly twist must dominate planar movement before it becomes zoom." }),
  spec("navigation.orbit.style", "enum", "orbit", "Orbit style", {
    cap: ["orbit_style"], choices: ["default", "free", "turntable"],
    help: "Free permits roll; Turntable keeps a fixed horizon.",
  }),
  spec("navigation.orbit.pivot", "enum", "orbit", "Orbit pivot", {
    cap: ["orbit_pivot"],
    choices: ["default", "camera", "screen_center", "cursor", "selection", "cursor_3d", "object", "origin"],
    help: "Point the camera rotates around. Unsupported pivots are absent for this app.",
  }),
  spec("navigation.orbit.twist_action", "enum", "orbit", "Twist action", {
    cap: ["twist_action"], choices: ["roll", "zoom", "dolly", "none"],
    help: "Action driven by unshifted twist in Orbit mode, including Turntable.",
  }),
  spec("navigation.zoom.target", "enum", "pan_zoom", "Zoom target", {
    cap: ["zoom_target"], choices: ["default", "to_center", "to_object", "to_cursor"],
    help: "To Cursor uses the surface under the mouse; empty-space misses keep the cursor stable.",
  }),
  spec("navigation.mode", "enum", "navigation", "Navigation mode",
    { cap: ["nav_mode"], choices: ["orbit", "fly", "walk"] }),
  spec("navigation.fly.speed", "number", "navigation", "Fly speed", { cap: ["fly_speed"], min: 0 }),
  spec("navigation.walk.speed", "number", "navigation", "Walk speed", { cap: ["walk_speed"], min: 0 }),
  spec("navigation.orbit.lock_horizon", "boolean", "orbit", "Lock horizon",
    { cap: ["lock_horizon"], help: "Keep the horizon fixed even when Orbit style is Free." }),
  spec("navigation.pan.scales_with_distance", "boolean", "pan_zoom", "Pan scales with view distance",
    { cap: ["pan_scales"] }),
  spec("navigation.zoom.behavior", "enum", "pan_zoom", "Pan-mode zoom behavior", {
    cap: ["zoom_behavior"], choices: ["zoom", "dolly"],
    help: "Zoom changes view scale or lens. Dolly moves the camera.",
  }),
  spec("navigation.camera.lock_to_view", "boolean", "camera", "Lock camera to view",
    { cap: ["camera_lock"] }),
  spec("navigation.unity.override_dynamic_clip", "boolean", "pan_zoom", "Override Unity Dynamic Clipping",
    { cap: ["dynamic_clip"] }),
  spec("navigation.unity.pivot_extent_multiplier", "number", "pan_zoom", "Pivot extent limit",
    { cap: ["pivot_extent"], min: 0 }),
];

/* physical orientation — persistent-only, one permutation across the three axes */
["x", "y", "z"].forEach((axis) => {
  SETTINGS.push(spec(`input.axis_orientation.${axis}.source`, "integer", "transform",
    `Logical ${axis.toUpperCase()} uses physical axis`, {
      scope: "global_only", choices: [0, 1, 2], control: "choice",
      help: "Changing a source swaps axes instead of duplicating one.",
    }));
  SETTINGS.push(spec(`input.axis_orientation.${axis}.invert`, "boolean", "transform",
    `Invert logical ${axis.toUpperCase()}`, { scope: "global_only" }));
});

/* lean integrations route six generic channels */
const LEAN_ROUTES = [
  ["orbit.x", "Orbit X"], ["orbit.y", "Orbit Y"], ["orbit.z", "Orbit Z"],
  ["pan.x", "Pan X"], ["pan.y", "Pan Y"], ["zoom", "Zoom"],
];
LEAN_ROUTES.forEach(([routeId, label]) => {
  SETTINGS.push(spec(`navigation.routing.${routeId}.source`, "integer", "routing", `${label} source`,
    { cap: ["action_routing", "lean_actions"], choices: [0, 1, 2], control: "choice", group: label }));
  SETTINGS.push(spec(`navigation.routing.${routeId}.invert`, "boolean", "routing", `Invert ${label}`,
    { cap: ["action_routing", "lean_actions"] }));
});

/* rich integrations route named mode actions */
const RICH_ACTIONS = {
  orbit: ["pitch", "yaw", "twist", "pan_x", "pan_y", "zoom"],
  camera: ["pitch", "yaw", "roll"],
  fly: ["pitch", "yaw", "bank", "forward", "strafe", "vertical"],
  walk: ["pitch", "yaw", "forward", "strafe", "vertical"],
};
Object.entries(RICH_ACTIONS).forEach(([mode, actions]) => {
  actions.forEach((action) => {
    const needsRoll = (mode === "camera" && action === "roll") || (mode === "fly" && action === "bank");
    const cap = ["action_routing", "rich_actions"].concat(needsRoll ? ["roll"] : []);
    const title = mode[0].toUpperCase() + mode.slice(1);
    const pretty = action.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    const label = `${title} ${pretty}`;
    SETTINGS.push(spec(`navigation.routing.${mode}.${action}.source`, "integer", "routing",
      `${label} source`, { cap, choices: [0, 1, 2], control: "choice", mode }));
    SETTINGS.push(spec(`navigation.routing.${mode}.${action}.invert`, "boolean", "routing",
      `Invert ${label}`, { cap, mode }));
  });
});

const SETTINGS_BY_ID = Object.fromEntries(SETTINGS.map((s) => [s.id, s]));

/* ------------------------------------------------------------------- registry */

const BASE_FEATURES = [
  "rate", "orbit_sensitivity", "pan_gain", "zoom_gain", "zoom_dominance",
  "orbit_style", "orbit_pivot", "twist_action", "level_horizon", "selection_override",
  "zoom_target", "orbit_hold", "zoom_hold", "action_routing",
];
const RICH_FEATURES = ["nav_mode", "fly_speed", "walk_speed", "lock_horizon", "pan_scales"];
const PIVOTS_DEFAULT = ["screen_center", "cursor", "selection", "object", "origin"];
const PIVOTS_CAMERA = ["camera"].concat(PIVOTS_DEFAULT);

function app(id, name, o) {
  const features = new Set(BASE_FEATURES);
  if (o.rich) RICH_FEATURES.forEach((f) => features.add(f));
  (o.features || []).forEach((f) => features.add(f));
  (o.exclude || []).forEach((f) => features.delete(f));
  const caps = new Set(features);
  caps.add(o.rich ? "rich_actions" : "lean_actions");
  if (!o.noRoll) caps.add("roll");
  return {
    id, name,
    tier: o.tier,
    process: o.process,
    transport: o.transport || "broker",
    focus: o.focus || "desktop process",
    modes: o.rich ? ["orbit", "fly", "walk"] : ["orbit"],
    rich: !!o.rich,
    noRoll: !!o.noRoll,
    caps,
    pivots: o.pivots || PIVOTS_DEFAULT,
    orbitStyles: o.orbitStyles || ["default", "free", "turntable"],
    twistActions: o.twistActions || (o.noRoll ? ["zoom", "dolly", "none"] : ["roll", "zoom", "none"]),
    zoomTargets: ["default", "to_center", "to_object", "to_cursor"],
    zoomBehaviors: o.zoomBehaviors || [],
    integration: o.integration,
  };
}

const APPS = [
  app("blender", "Blender", {
    tier: "supported", rich: true, process: "blender.exe",
    pivots: ["camera", "screen_center", "cursor", "selection", "cursor_3d", "object", "origin"],
    twistActions: ["roll", "zoom", "dolly", "none"], zoomBehaviors: ["zoom", "dolly"],
    features: ["zoom_behavior", "camera_lock"],
    integration: {
      detected: "detected v5.1.1: C:\\Program Files\\Blender Foundation\\Blender 5.1\\blender.exe",
      compat: "supported", verified: "Blender 4.2 through 5.1 (tested on 5.1.1)",
      installModel: "User add-on copied into each detected Blender version; optional startup shim.",
      setup: "Set up copies Trackball Nav to every detected user scripts/addons folder and asks whether to add the auto-enable startup shim.",
      manual: "Copy trackball_daemon\\plugins\\blender\\trackball_nav to %APPDATA%\\Blender Foundation\\Blender\\<version>\\scripts\\addons\\trackball_nav, then enable Trackball Nav in Preferences > Add-ons. No administrator access is required.",
      health: "Restart Blender. The row should show connected while Blender is focused; details are in %APPDATA%\\Mildly Useful\\Astrolabe\\blender_addin.log.",
      security: "Copies unsigned Python source only into Blender's current-user folders. The optional startup shim runs that source at Blender launch; the UI asks separately before installing it. No elevation or external listener.",
      setupRequired: true, action: "Reinstall", installed: true, enabled: true,
      connected: "1.4.0", runtime: { state: "healthy", detail: "add-on connected; 30 Hz stream accepted" },
    },
  }),
  app("freecad", "FreeCAD", {
    tier: "supported", process: "freecad.exe",
    integration: {
      detected: "detected v1.1.1: C:\\Program Files\\FreeCAD 1.1\\bin\\FreeCAD.exe",
      compat: "supported", verified: "FreeCAD 1.0 through 1.1 (tested on 1.1.1)",
      installModel: "User Mod add-on; files are copied only under the current Windows profile.",
      setup: "Set up copies TrackballNav into FreeCAD's version-aware user Mod folder.",
      manual: "Copy trackball_daemon\\plugins\\freecad\\TrackballNav to %APPDATA%\\FreeCAD\\v<major>-<minor>\\Mod\\TrackballNav (FreeCAD 1.x), then restart FreeCAD. Use %APPDATA%\\FreeCAD\\Mod for older layouts.",
      health: "Open a 3D view and focus FreeCAD; the row should show connected. Check %APPDATA%\\Mildly Useful\\Astrolabe\\freecad_addin.log if it does not.",
      security: "Copies unsigned Python into FreeCAD's current-user Mod folder, where FreeCAD loads it at startup. No elevation, registry write, or external port.",
      setupRequired: true, action: "Update → v1.4.0", installed: true, enabled: true,
      connected: "1.3.2", stale: true,
      runtime: { state: "degraded", detail: "add-on v1.3.2 predates the bundled v1.4.0 protocol; restart FreeCAD after updating" },
    },
  }),
  app("sketchup", "SketchUp", {
    tier: "experimental", rich: true, process: "sketchup.exe", pivots: PIVOTS_CAMERA,
    twistActions: ["roll", "zoom", "dolly", "none"], zoomBehaviors: ["zoom", "dolly"],
    features: ["zoom_behavior"],
    integration: {
      detected: "not detected", compat: "unknown",
      verified: "SketchUp Desktop 2025 through 2026 (tested on 2026.2.243)",
      installModel: "Per-version Ruby extension in SketchUp's user Plugins folder.",
      setup: "Set up copies the loader and extension into every detected annual release.",
      manual: "Copy trackball_daemon\\plugins\\sketchup\\trackball_nav_loader.rb and the trackball_nav folder to %APPDATA%\\SketchUp\\SketchUp <year>\\SketchUp\\Plugins, then restart SketchUp. SketchUp for Web is not supported.",
      health: "Extension Manager should list Trackball Nav; focus a model and look for connected in this row or inspect %APPDATA%\\Mildly Useful\\Astrolabe\\sketchup_addin.log.",
      security: "Copies an unsigned Ruby extension into SketchUp's current-user Plugins folder. SketchUp executes it at startup. No elevation or external listener.",
      setupRequired: true, action: "Set up", installed: false, enabled: false,
      runtime: { state: "disabled", detail: "integration disabled" },
    },
  }),
  app("unreal", "Unreal Engine", {
    tier: "experimental", rich: true, process: "unrealeditor.exe", pivots: PIVOTS_CAMERA,
    twistActions: ["roll", "zoom", "dolly", "none"], zoomBehaviors: ["zoom", "dolly"],
    features: ["zoom_behavior"],
    integration: {
      detected: "CAUTION — detected host 5.9.1  •  C:\\Program Files\\Epic Games\\UE_5.9\\Engine\\Binaries\\Win64\\UnrealEditor.exe",
      compat: "unverified", verified: "Unreal Engine 5.8 (tested on 5.8.0)",
      installModel: "Unreal Editor plugin, installed per engine or per project.",
      setup: "Set up copies TrackballNav to each detected Engine/Plugins folder. Engine-level writes may require administrator permission.",
      manual: "Without administrator access, copy trackball_daemon\\plugins\\unreal\\TrackballNav to <YourProject>\\Plugins\\TrackballNav. Enable Trackball Nav and Python Editor Script Plugin in Edit > Plugins, then restart the editor.",
      health: "Focus a perspective level viewport; the row should show connected. Check %APPDATA%\\Mildly Useful\\Astrolabe\\unreal_addin.log and the Output Log on failure.",
      security: "Engine-wide setup writes an unsigned Python editor plugin under Program Files and may require UAC/elevation. Per-project installation avoids elevation. The plugin connects only to the loopback nav broker.",
      confirmation: "Unreal engine-wide setup will attempt to copy an unsigned Python editor plugin into each detected Engine/Plugins folder. Windows may request administrator approval; use the documented per-project Plugins folder if you do not want an elevated install.",
      setupRequired: true, action: "Set up", installed: false, enabled: false,
      runtime: { state: "disabled", detail: "integration disabled" },
    },
  }),
  app("unity", "Unity", {
    tier: "experimental", rich: true, process: "unity.exe", pivots: PIVOTS_CAMERA,
    twistActions: ["roll", "zoom", "dolly", "none"], zoomBehaviors: ["zoom", "dolly"],
    features: ["zoom_behavior", "dynamic_clip", "pivot_extent"],
    integration: {
      detected: "detected v6000.5.3f1: C:\\Program Files\\Unity\\Hub\\Editor\\6000.5.3f1\\Editor\\Unity.exe",
      compat: "supported", verified: "Unity 6 / 6000.x (implemented against 6000.5.3f1)",
      installModel: "UPM Editor package copied into each detected Unity project.",
      setup: "Set up finds running/recent projects and copies the package into each project's Packages folder; Unity recompiles it automatically.",
      manual: "Copy trackball_daemon\\plugins\\unity\\com.astrolabe.trackball-nav to <YourProject>\\Packages\\com.astrolabe.trackball-nav. If Set up found no project, the same package is staged under %APPDATA%\\Mildly Useful\\Astrolabe\\unity.",
      health: "Open and focus a Scene view; the row should show connected. Check the Unity Console and %APPDATA%\\Mildly Useful\\Astrolabe\\unity_addin.log.",
      security: "Copies unsigned C# editor source into each detected project's Packages folder; Unity compiles and executes it in the Editor. No elevation or machine-wide setting change.",
      setupRequired: true, action: "Reinstall", installed: true, enabled: false,
      runtime: { state: "disabled", detail: "integration disabled" },
    },
  }),
  app("rhino", "Rhino", {
    tier: "experimental", process: "rhino.exe", pivots: PIVOTS_CAMERA,
    zoomBehaviors: ["zoom", "dolly"], features: ["zoom_behavior"],
    integration: {
      detected: "not detected", compat: "unknown", verified: "Rhino 8",
      installModel: "Rhino 8 user Python scripts plus a per-user startup command.",
      setup: "Set up copies TrackballNav into Rhino's user scripts folder and best-effort registers its startup command.",
      manual: "Copy trackball_daemon\\plugins\\rhino\\TrackballNav to %APPDATA%\\McNeel\\Rhinoceros\\8.0\\scripts\\TrackballNav. In Rhino Options > General, add _-RunPythonScript \"<path>\\start.py\" to startup commands, then restart Rhino.",
      health: "Focus a Rhino viewport and look for connected. Check %APPDATA%\\Mildly Useful\\Astrolabe\\rhino_addin.log if startup failed.",
      security: "Copies unsigned Python into Rhino's current-user scripts folder and may edit the current-user Rhino startup-command XML so it runs at launch. No elevation or machine-wide registry write.",
      confirmation: "Rhino setup copies Python scripts and will try to append a command to your per-user Rhino startup configuration. If you decline, use the documented manual command instead.",
      setupRequired: true, action: "Set up", installed: false, enabled: false,
      runtime: { state: "disabled", detail: "integration disabled" },
    },
  }),
  app("fusion360", "Fusion 360", {
    tier: "supported", process: "fusion360.exe", twistActions: ["roll", "zoom", "none"],
    zoomBehaviors: ["zoom", "dolly"], features: ["zoom_behavior"],
    integration: {
      detected: "detected: C:\\Users\\dylan\\AppData\\Local\\Autodesk\\webdeploy\\production\\Fusion360.exe",
      compat: "supported", verified: "Current Fusion production release (rolling Autodesk release)",
      installModel: "Fusion user add-in copied to Autodesk's per-user AddIns folder.",
      setup: "Set up copies TrackballNav. In Fusion, open Utilities > Add-Ins, run TrackballNav once, and enable Run on Startup.",
      manual: "Copy trackball_daemon\\plugins\\fusion360\\TrackballNav to %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\TrackballNav, then run it from Utilities > Add-Ins. No administrator access is required.",
      health: "Focus an open design and look for connected. Check %APPDATA%\\Mildly Useful\\Astrolabe\\fusion_addin.log if the add-in does not handshake.",
      security: "Copies unsigned Python into Fusion's current-user AddIns folder. Fusion does not execute it until you explicitly Run it and select Run on Startup. No elevation or machine-wide setting change.",
      setupRequired: true, action: "Reinstall", installed: true, enabled: true,
      runtime: { state: "waiting", detail: "add-in installed; waiting for the first handshake from Fusion" },
    },
  }),
  app("solidworks", "SolidWorks", {
    tier: "supported", process: "sldworks.exe", transport: "SolidWorks COM",
    integration: {
      detected: "detected 2025: C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe",
      compat: "supported", verified: "SOLIDWORKS 2025 (tested on 2025)",
      installModel: "Direct COM automation; no SolidWorks add-in or host files are installed.",
      setup: "Enable performs a one-time prerequisite check for SOLIDWORKS and pywin32. After that, the Enabled checkbox is the only control needed.",
      manual: "There is nothing to copy. If the prerequisite check fails, install pywin32 into the daemon's Python environment with: pip install pywin32.",
      health: "Open a part or assembly and focus SOLIDWORKS; the row should show connected. Driver messages are recorded in %APPDATA%\\Mildly Useful\\Astrolabe\\daemon.log.",
      security: "Uses per-user COM automation against an already-running SOLIDWORKS instance. It does not register a COM server, install a DLL, launch SOLIDWORKS, request elevation, or listen on an external interface.",
      setupRequired: false, action: null, installed: true, enabled: true,
      runtime: { state: "healthy", detail: "COM owner attached to one running SOLIDWORKS session" },
    },
  }),
  app("onshape", "Onshape", {
    tier: "supported", process: "chrome.exe · msedge.exe · firefox.exe · brave.exe · opera.exe · vivaldi.exe",
    transport: "Onshape bridge", focus: "browser + viewport focus",
    features: ["onshape_userscript"],
    integration: {
      detected: "loopback TLS bridge on 127.51.68.120:51680",
      compat: "supported", verified: "Current Onshape web release (rolling release)",
      installModel: "Browser bridge; no Onshape add-in is installed.",
      setup: "Set up creates the bridge's per-user local TLS certificate. Trust it once and enable SpaceMouse/3Dconnexion in Onshape preferences.",
      manual: "No application files need copying. Generate/trust the certificate using the Set up dialog or certutil -user, then install the supplied userscript only if Under Cursor orbit is wanted. Administrator access is not required.",
      health: "Open and focus an Onshape document; the row should show connected after the browser handshake. Check %APPDATA%\\Mildly Useful\\Astrolabe\\daemon.log.",
      security: "Creates a per-user self-signed leaf certificate and binds TLS only to 127.51.68.120. Trust-store installation is never automatic. The optional Onshape-only userscript reports canvas-relative pointer coordinates; it does not capture the screen or send model data.",
      confirmation: "Onshape setup creates a self-signed certificate in your APPDATA folder. Trusting it is a separate manual action that produces a normal Windows/browser security warning and can be undone. The local service accepts browser requests only from onshape.com.",
      setupRequired: true, action: null, installed: true, enabled: true,
      runtime: { state: "waiting", detail: "certificate trusted; no subscribed browser controller yet" },
    },
  }),
  app("autocad", "AutoCAD", {
    tier: "experimental", process: "acad.exe", pivots: PIVOTS_CAMERA,
    zoomBehaviors: ["zoom", "dolly"], features: ["zoom_behavior"],
    integration: {
      detected: "detected 2026: C:\\Program Files\\Autodesk\\AutoCAD 2026\\acad.exe",
      compat: "supported", verified: "AutoCAD 2025 through 2027 (.NET 8 family; tested on 2026)",
      installModel: "Per-user .NET plugin staged by the daemon and NETLOADed automatically.",
      setup: "Set up stages TrackballNavAcad.dll under the daemon's APPDATA folder. The daemon adds that folder to TRUSTEDPATHS and NETLOADs it on attach.",
      manual: "Copy trackball_daemon\\plugins\\autocad\\TrackballNavAcad.dll and version.json to %APPDATA%\\Mildly Useful\\Astrolabe\\acad_plugin. Add that folder to TRUSTEDPATHS and run NETLOAD on the DLL. No Program Files write is needed.",
      health: "Type TBNAV in AutoCAD or look for connected in this row. Plugin details are in %APPDATA%\\TrackballDaemon\\acad_plugin.log.",
      security: "Stages an unsigned .NET DLL under the current user's APPDATA, adds only that exact folder to AutoCAD TRUSTEDPATHS, and NETLOADs it through COM. It never launches AutoCAD, writes Program Files, or requires elevation.",
      confirmation: "AutoCAD setup enables later automatic loading of an unsigned .NET plugin. When the daemon attaches to a running AutoCAD it adds the single APPDATA plugin folder to TRUSTEDPATHS and issues NETLOAD. Cancel if you prefer the documented manual trust/load steps.",
      setupRequired: true, action: "Set up", installed: false, enabled: false,
      runtime: { state: "disabled", detail: "integration disabled" },
    },
  }),
];

const APPS_BY_ID = Object.fromEntries(APPS.map((a) => [a.id, a]));

const TIERS = {
  supported: {
    label: "Supported integration",
    summary: "Gates the release, is advertised only for its verified host versions, and treats an ordinary-navigation regression as release-blocking.",
  },
  experimental: {
    label: "Experimental integration",
    summary: "Opt-in, may ship with documented host limitations, and carries no promise for every host update. An isolated functional regression does not block a release; a security, data-loss, configuration-corruption, or lifecycle defect still does.",
  },
};

/* -------------------------------------------------------------- system layer */

const SYSTEM_DEFAULTS = {
  device: { "device.name": "Trackball BLE", "device.address": "" },
  global: {
    "hud.always_on_top": true,
    "hud.click_through": true,
    "hud.last_binding_timeout": 3.0,
    "hud.margin": 16,
    "hud.opacity": 0.9,
    "hud.visible": true,
    "input.axis_orientation.x.invert": false,
    "input.axis_orientation.x.source": 0,
    "input.axis_orientation.y.invert": false,
    "input.axis_orientation.y.source": 1,
    "input.axis_orientation.z.invert": false,
    "input.axis_orientation.z.source": 2,
    "input.mode.default": "3d",
    "navigation.camera.lock_to_view": false,
    "navigation.fly.speed": 1.0,
    "navigation.level_horizon_on_entry": true,
    "navigation.mode": "orbit",
    "navigation.orbit.lock_horizon": false,
    "navigation.orbit.pivot": "screen_center",
    "navigation.orbit.pivot_fallbacks": ["cursor_3d", "camera", "object", "origin"],
    "navigation.orbit.pivot_hold_seconds": 0.5,
    "navigation.orbit.selection_override": true,
    "navigation.orbit.sensitivity": 1.0,
    "navigation.orbit.style": "free",
    "navigation.orbit.twist_action": "roll",
    "navigation.pan.gain": 1.0,
    "navigation.pan.scales_with_distance": true,
    "navigation.refresh_rate": 30,
    "navigation.unity.override_dynamic_clip": true,
    "navigation.unity.pivot_extent_multiplier": 8.0,
    "navigation.walk.speed": 1.0,
    "navigation.zoom.behavior": "zoom",
    "navigation.zoom.cursor_hold_seconds": 0.5,
    "navigation.zoom.dominance": 1.7,
    "navigation.zoom.gain": 1.0,
    "navigation.zoom.target": "to_center",
    "pointer.cursor.gain": 216.0,
    "pointer.scroll.deadzone": 0.004,
    "pointer.scroll.dominance": 1.7,
    "pointer.scroll.gain": 29.0,
  },
  apps: {
    blender: { "navigation.orbit.pivot": "camera" },
    freecad: {},
    sketchup: { "navigation.zoom.behavior": "dolly" },
    unreal: { "navigation.zoom.behavior": "dolly" },
    unity: { "navigation.zoom.behavior": "dolly" },
    rhino: { "navigation.zoom.behavior": "dolly" },
    fusion360: {},
    solidworks: {},
    onshape: {},
    autocad: {},
  },
};

/* routing system defaults, mirrored from system_defaults.json */
Object.assign(SYSTEM_DEFAULTS.global, {
  "navigation.routing.orbit.x.source": 0, "navigation.routing.orbit.x.invert": false,
  "navigation.routing.orbit.y.source": 1, "navigation.routing.orbit.y.invert": false,
  "navigation.routing.orbit.z.source": 2, "navigation.routing.orbit.z.invert": false,
  "navigation.routing.pan.x.source": 1, "navigation.routing.pan.x.invert": false,
  "navigation.routing.pan.y.source": 0, "navigation.routing.pan.y.invert": false,
  "navigation.routing.zoom.source": 2, "navigation.routing.zoom.invert": false,
  "navigation.routing.orbit.pitch.source": 0, "navigation.routing.orbit.pitch.invert": false,
  "navigation.routing.orbit.yaw.source": 1, "navigation.routing.orbit.yaw.invert": false,
  "navigation.routing.orbit.twist.source": 2, "navigation.routing.orbit.twist.invert": false,
  "navigation.routing.orbit.pan_x.source": 0, "navigation.routing.orbit.pan_x.invert": false,
  "navigation.routing.orbit.pan_y.source": 1, "navigation.routing.orbit.pan_y.invert": false,
  "navigation.routing.orbit.zoom.source": 2, "navigation.routing.orbit.zoom.invert": false,
  "navigation.routing.camera.pitch.source": 0, "navigation.routing.camera.pitch.invert": false,
  "navigation.routing.camera.yaw.source": 1, "navigation.routing.camera.yaw.invert": false,
  "navigation.routing.camera.roll.source": 2, "navigation.routing.camera.roll.invert": false,
  "navigation.routing.fly.pitch.source": 0, "navigation.routing.fly.pitch.invert": false,
  "navigation.routing.fly.yaw.source": 1, "navigation.routing.fly.yaw.invert": false,
  "navigation.routing.fly.bank.source": 2, "navigation.routing.fly.bank.invert": false,
  "navigation.routing.fly.forward.source": 1, "navigation.routing.fly.forward.invert": false,
  "navigation.routing.fly.strafe.source": 0, "navigation.routing.fly.strafe.invert": false,
  "navigation.routing.fly.vertical.source": 2, "navigation.routing.fly.vertical.invert": false,
  "navigation.routing.walk.pitch.source": 0, "navigation.routing.walk.pitch.invert": false,
  "navigation.routing.walk.yaw.source": 1, "navigation.routing.walk.yaw.invert": false,
  "navigation.routing.walk.forward.source": 1, "navigation.routing.walk.forward.invert": false,
  "navigation.routing.walk.strafe.source": 0, "navigation.routing.walk.strafe.invert": false,
  "navigation.routing.walk.vertical.source": 2, "navigation.routing.walk.vertical.invert": false,
});

/* ---------------------------------------------------------------- hardware */

const DEVICE_DESCRIPTORS = [
  {
    id: "astrolabe_5way", source: "ble.astrolabe", label: "Astrolabe five-way switch",
    advertised: "Astrolabe", role: "Product firmware (validation build)",
    service: "2cad0001-6e64-0146-b139-9cf2a4cd57fc",
    motion: "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
    input: "2cad0003-6e64-0146-b139-9cf2a4cd57fc",
    controls: [
      ["fiveway.up", "Astrolabe Up", 0], ["fiveway.down", "Astrolabe Down", 1],
      ["fiveway.left", "Astrolabe Left", 2], ["fiveway.right", "Astrolabe Right", 3],
      ["fiveway.center", "Astrolabe Center", 4],
    ],
    notes: "active-low internal pull-up · firmware-defined debounce · mechanically exclusive, not enforced · protocol v1",
    present: true,
  },
  {
    id: "xiao3389_3button", source: "ble.xiao3389", label: "XIAO3389 three-button test bench",
    advertised: "Trackball BLE", role: "Bench hardware",
    service: "2cad0001-6e64-0146-b139-9cf2a4cd57fc",
    motion: "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
    input: "2cad0003-6e64-0146-b139-9cf2a4cd57fc",
    controls: [
      ["button.left", "Test-bench Left", 0], ["button.right", "Test-bench Right", 1],
      ["button.middle", "Test-bench Middle", 2],
    ],
    notes: "independent controls · protocol v1 · hardware role: test bench",
    present: false,
  },
  {
    id: "astrolabe_legacy", source: "ble.astrolabe", label: "Legacy rotation-only firmware",
    advertised: "Trackball BLE / Astrolabe", role: "Compatibility adapter",
    service: "2cad0001-6e64-0146-b139-9cf2a4cd57fc",
    motion: "2cad0002-6e64-0146-b139-9cf2a4cd57fc",
    input: "— none published —",
    controls: [],
    notes: "rotation stream only; no input-state characteristic, so device controls are unavailable and the keyboard-only profile applies",
    present: false,
  },
];

/* --------------------------------------------------------------- keybindings */

const COMMON_ACTIONS = [
  ["input.toggle", "Toggle Pointer / 3D"],
  ["input.hold_3d", "Hold 3D mode"],
  ["input.hold_pointer", "Hold Pointer mode"],
  ["navigation.hold_pan", "Hold Pan / Zoom controls"],
  ["navigation.hold_secondary", "Hold secondary Orbit controls"],
  ["navigation.set_orbit", "Switch navigation to Orbit"],
  ["navigation.set_fly", "Switch navigation to Fly"],
  ["navigation.set_walk", "Switch navigation to Walk"],
  ["navigation.cycle", "Cycle Orbit / Fly / Walk"],
  ["pointer.left", "Hold Left click"],
  ["pointer.right", "Hold Right click"],
  ["pointer.middle", "Hold Middle click"],
];

const BINDING_PROFILES = {
  astrolabe_5way: {
    id: "astrolabe_5way", label: "Astrolabe hardware",
    bindings: [
      { id: "keyboard.ctrl.3d", label: "Ctrl: 3D", enabled: true, chord: ["keyboard:ctrl"], match: "allow_extra_modifiers", action: "input.hold_3d", priority: 0, apps: [] },
      { id: "keyboard.shift.pan", label: "Shift: Pan / Zoom", enabled: true, chord: ["keyboard:shift"], match: "allow_extra_modifiers", action: "navigation.hold_pan", priority: 0, apps: [] },
      { id: "keyboard.ctrl_shift.pan", label: "Ctrl + Shift: Pan / Zoom", enabled: true, chord: ["keyboard:ctrl", "keyboard:shift"], match: "exact", action: "navigation.hold_pan", priority: 0, apps: [] },
      { id: "astrolabe.center.toggle_mode", label: "Center: Toggle Pointer / 3D", enabled: true, chord: ["ble.astrolabe:fiveway.center"], match: "exact", action: "input.toggle", priority: 0, apps: [] },
      { id: "astrolabe.up.orbit", label: "Up: Orbit", enabled: true, chord: ["ble.astrolabe:fiveway.up"], match: "exact", action: "navigation.set_orbit", priority: 0, apps: [] },
      { id: "astrolabe.right.fly", label: "Right: Fly", enabled: true, chord: ["ble.astrolabe:fiveway.right"], match: "exact", action: "navigation.set_fly", priority: 0, apps: [] },
      { id: "astrolabe.down.walk", label: "Down: Walk", enabled: true, chord: ["ble.astrolabe:fiveway.down"], match: "exact", action: "navigation.set_walk", priority: 0, apps: [] },
      { id: "astrolabe.left.secondary", label: "Left: Secondary controls", enabled: true, chord: ["ble.astrolabe:fiveway.left"], match: "exact", action: "navigation.hold_secondary", priority: 0, apps: [] },
      { id: "xiao.left.pointer_button", label: "XIAO Left click", enabled: true, chord: ["ble.xiao3389:button.left"], match: "exact", action: "pointer.left", priority: 0, apps: [] },
      { id: "xiao.right.pointer_button", label: "XIAO Right click", enabled: true, chord: ["ble.xiao3389:button.right"], match: "exact", action: "pointer.right", priority: 0, apps: [] },
      { id: "xiao.middle.pointer_button", label: "XIAO Middle click", enabled: true, chord: ["ble.xiao3389:button.middle"], match: "exact", action: "pointer.middle", priority: 0, apps: [] },
    ],
  },
  keyboard_only: {
    id: "keyboard_only", label: "Keyboard only",
    bindings: [
      { id: "keyboard.ctrl.3d", label: "Ctrl: 3D", enabled: true, chord: ["keyboard:ctrl"], match: "allow_extra_modifiers", action: "input.hold_3d", priority: 0, apps: [] },
      { id: "keyboard.shift.pan", label: "Shift: Pan / Zoom", enabled: true, chord: ["keyboard:shift"], match: "allow_extra_modifiers", action: "navigation.hold_pan", priority: 0, apps: [] },
      { id: "keyboard.ctrl_shift.pan", label: "Ctrl + Shift: Pan / Zoom", enabled: true, chord: ["keyboard:ctrl", "keyboard:shift"], match: "exact", action: "navigation.hold_pan", priority: 0, apps: [] },
      { id: "keyboard.f12.toggle_mode", label: "F12: Toggle Pointer / 3D", enabled: true, chord: ["keyboard:f12"], match: "exact", action: "input.toggle", priority: 0, apps: [] },
    ],
  },
};

const INPUT_CAPABILITY = {
  astrolabe_5way: [
    { source: "ble.astrolabe", controls: "fiveway.center, fiveway.down, fiveway.left, fiveway.right, fiveway.up", status: "running", detail: "input-state snapshots subscribed" },
    { source: "ble.xiao3389", controls: "button.left, button.middle, button.right", status: "not present", detail: "No active provider reported this source." },
    { source: "keyboard", controls: "ctrl, shift", status: "running", detail: "Windows Raw Input observing passively" },
  ],
  keyboard_only: [
    { source: "keyboard", controls: "ctrl, f12, shift", status: "running", detail: "Windows Raw Input observing passively" },
  ],
};

/* ------------------------------------------------------------------ runtime */
/* A frozen snapshot standing in for RuntimeStore; the panel treats it as live. */

const RUNTIME = {
  connection: "subscribed — Astrolabe five-way switch is live",
  connectionState: "ok",
  foreground: { app: "blender", process: "blender.exe", label: "Blender" },
  lastBinding: "Ctrl: 3D",
  heldBindings: [],
  broker: { state: "healthy", detail: "loopback broker listening on 127.0.0.1:51701; 2 subscribers" },
  services: [
    ["navigation-broker", "healthy", "loopback broker listening on 127.0.0.1:51701; 2 subscribers"],
    ["solidworks", "healthy", "COM owner attached to one running SOLIDWORKS session"],
    ["onshape", "waiting", "certificate trusted; no subscribed browser controller yet"],
    ["autocad", "disabled", "integration disabled"],
  ],
  startAtLogin: true,
};
