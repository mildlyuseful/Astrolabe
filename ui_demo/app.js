// Astrolabe UI Demo Interactivity Script

document.addEventListener('DOMContentLoaded', () => {
  initTabNavigation();
  initModeToggle();
  initBindingsDashboard();
  initThemeToggle();
});

// --- Theme Toggle (Light / Dark Marble) ---
function initThemeToggle() {
  const themeToggleBtn = document.getElementById('theme-toggle');
  const sunIcon = themeToggleBtn.querySelector('.sun-icon');
  const moonIcon = themeToggleBtn.querySelector('.moon-icon');

  themeToggleBtn.addEventListener('click', () => {
    const isLight = document.body.classList.toggle('light-theme');
    if (isLight) {
      sunIcon.style.display = 'none';
      moonIcon.style.display = 'block';
      flashGlobalStatus('Switched to Light Marble Theme');
    } else {
      sunIcon.style.display = 'block';
      moonIcon.style.display = 'none';
      flashGlobalStatus('Switched to Dark Marble Theme');
    }
  });
}

// --- Tab Navigation ---
function initTabNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  const panels = document.querySelectorAll('.tab-panel');
  const pageTitle = document.getElementById('page-title');

  navItems.forEach(item => {
    item.addEventListener('click', () => {
      // Deactivate all nav items & panels
      navItems.forEach(nav => nav.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));

      // Activate clicked tab
      item.classList.add('active');
      const targetTabId = item.getAttribute('data-tab');
      const targetPanel = document.getElementById(targetTabId);
      if (targetPanel) {
        targetPanel.classList.add('active');
      }

      // Update Header Title
      if (targetTabId === 'tab-apps') {
        pageTitle.textContent = '3D App Integrations';
      } else if (targetTabId === 'tab-bindings') {
        pageTitle.textContent = 'Per-App Bindings';
      } else if (targetTabId === 'tab-general') {
        pageTitle.textContent = 'General Preferences';
      }
    });
  });
}

// --- Operation Mode Toggle (3D Mode / Cursor) ---
function initModeToggle() {
  const btn3D = document.getElementById('btn-mode-3d');
  const btnCursor = document.getElementById('btn-mode-cursor');
  const footerStatus = document.getElementById('footer-connection-status');
  const ticker = document.getElementById('console-ticker');

  btn3D.addEventListener('click', () => {
    btn3D.classList.add('active');
    btnCursor.classList.remove('active');
    footerStatus.textContent = 'Connection: active • connected (v0.1.44) [3D MODE]';
    ticker.textContent = 'navbroker: 127.0.0.1:6000 streaming frames | active_app: blender';
    flashGlobalStatus('Switched to 3D Navigation Mode');
  });

  btnCursor.addEventListener('click', () => {
    btnCursor.classList.add('active');
    btn3D.classList.remove('active');
    footerStatus.textContent = 'Connection: active • connected (v0.1.44) [CURSOR MODE]';
    ticker.textContent = 'mouse-synthesizer: active | mapping: radians → pixels';
    flashGlobalStatus('Switched to 2D Mouse Cursor Mode');
  });
}

function flashGlobalStatus(msg) {
  const statusText = document.querySelector('#global-status-badge .badge-text');
  const statusBadge = document.getElementById('global-status-badge');
  const oldText = statusText.textContent;
  
  statusText.textContent = msg;
  statusBadge.style.borderColor = 'var(--accent-color)';
  statusBadge.style.color = 'var(--accent-color)';
  document.querySelector('#global-status-badge .badge-dot').style.backgroundColor = 'var(--accent-color)';
  
  setTimeout(() => {
    statusText.textContent = oldText;
    statusBadge.style.borderColor = '';
    statusBadge.style.color = '';
    document.querySelector('#global-status-badge .badge-dot').style.backgroundColor = '';
  }, 2500);
}

// --- Modals Interactivity ---
function openOnshapeDialog() {
  document.getElementById('modal-onshape').classList.add('open');
}

function closeOnshapeDialog() {
  document.getElementById('modal-onshape').classList.remove('open');
  document.getElementById('onshape-copy-status').style.display = 'none';
}

function copyOnshapeScript() {
  const onshapeScriptCode = `// ==UserScript==
// @name         Astrolabe Onshape Pointer Bridge
// @namespace    http://tampermonkey.net/
// @version      0.1
// @description  Reports Onshape canvas cursor coordinates to the Astrolabe local bridge for under-cursor orbit pivots.
// @match        https://*.onshape.com/*
// @grant        none
// ==/UserScript==

(function() {
    'use strict';
    // Pointer coordinate reporting script logic...
    window.addEventListener('mousemove', (e) => {
       // Send mouse events to the local WebSocket
    });
})();`;

  navigator.clipboard.writeText(onshapeScriptCode).then(() => {
    const banner = document.getElementById('onshape-copy-status');
    banner.style.display = 'flex';
    setTimeout(() => {
      banner.style.opacity = '1';
    }, 10);
  });
}

function confirmOnshapeDialog() {
  const enabledCheckbox = document.getElementById('checkbox-onshape-enabled');
  enabledCheckbox.checked = true;
  closeOnshapeDialog();
  flashGlobalStatus('Onshape web bridge enabled');
}

function openBlenderDialog() {
  document.getElementById('modal-blender').classList.add('open');
}

function closeBlenderDialog() {
  document.getElementById('modal-blender').classList.remove('open');
}

function actionBlenderInstall(autoStart) {
  closeBlenderDialog();
  flashGlobalStatus(autoStart ? 'Blender integration set up with auto-start shim' : 'Blender add-on installed manually');
  
  // Highlight Blender card state
  const blenderCard = document.querySelector('[data-app="blender"]');
  blenderCard.style.borderColor = 'var(--success-color)';
  setTimeout(() => {
    blenderCard.style.borderColor = '';
  }, 2000);

// --- Interactive Per-App Bindings Dashboard Manager ---

const bindingsState = {
  activeApp: 'generic',
  activeMode: 'orbit',
  apps: {
    generic: {
      global: { rate: 'Default', hold: '0.5' },
      modes: {
        orbit: {
          switchKey: 'shift',
          switchType: 'hold',
          primary: {
            x: { name: 'Yaw (L/R)', sens: '1.0', invert: false },
            y: { name: 'Pitch (U/D)', sens: '1.0', invert: false },
            z: { name: 'Roll (Twist)', sens: '1.0', invert: false },
            linked: true
          },
          switched: {
            x: { name: 'Pan X (Strafe)', sens: '0.005', invert: false },
            y: { name: 'Pan Y (Vertical)', sens: '0.005', invert: false },
            z: { name: 'Zoom', sens: '0.02', invert: false },
            linked: true,
            zoomMode: 'to_cursor',
            zoomDominance: '0.8',
            zoomMouse: true
          },
          pivot: 'cursor',
          style: 'turntable',
          lockHorizon: true
        }
      }
    }
  }
};

function populateInitialState() {
  const appKeys = ['blender', 'freecad', 'sketchup', 'unreal', 'fusion360', 'solidworks', 'onshape', 'autocad'];
  appKeys.forEach(app => {
    // Deep clone generic layout
    bindingsState.apps[app] = JSON.parse(JSON.stringify(bindingsState.apps.generic));
    
    // Custom configurations for Walk/Fly apps
    if (app === 'blender' || app === 'sketchup' || app === 'unreal') {
      bindingsState.apps[app].modes.fly = {
        switchKey: 'ctrl',
        switchType: 'toggle',
        primary: {
          x: { name: 'Look Yaw', sens: '1.0', invert: false },
          y: { name: 'Look Pitch', sens: '1.0', invert: false },
          z: { name: 'Look Roll', sens: '1.0', invert: false },
          linked: true
        },
        switched: {
          x: { name: 'Strafe X', sens: '10.0', invert: false },
          y: { name: 'Forward/Back', sens: '10.0', invert: false },
          z: { name: 'Rise Up/Down', sens: '10.0', invert: false },
          linked: true
        },
        speed: '10.0',
        hold: '0.5'
      };
      
      bindingsState.apps[app].modes.walk = {
        switchKey: 'alt',
        switchType: 'hold',
        primary: {
          x: { name: 'Look Yaw', sens: '1.0', invert: false },
          y: { name: 'Look Pitch', sens: '1.0', invert: false },
          z: { name: 'Look Roll', sens: '1.0', invert: false },
          linked: true
        },
        switched: {
          x: { name: 'Strafe X', sens: '2.5', invert: false },
          y: { name: 'Forward/Back', sens: '2.5', invert: false },
          z: { name: 'Rise Up/Down', sens: '2.5', invert: false },
          linked: true
        },
        speed: '2.5',
        hold: '0.5'
      };
    }

    // App specific overrides
    if (app === 'blender') {
      bindingsState.apps.blender.modes.orbit.pivot = 'view'; // Default Auto-depth
    }
  });
}

function initBindingsDashboard() {
  populateInitialState();
  
  const appSelect = document.getElementById('bindings-app-select');
  const modePills = document.getElementById('dashboard-mode-pills');
  const hotkeySelect = document.getElementById('hotkey-select');
  const hotkeyToggle = document.getElementById('hotkey-toggle-type');
  
  // App switcher dropdown listener
  appSelect.addEventListener('change', () => {
    bindingsState.activeApp = appSelect.value;
    updatePillsVisibility();
    selectMode('orbit'); // reset mode to orbit on app switch
    loadAppGlobalSettings();
  });

  // Switch Key selector listener
  hotkeySelect.addEventListener('change', () => {
    const modeState = getActiveModeState();
    if (modeState) {
      modeState.switchKey = hotkeySelect.value;
      flashGlobalStatus(`Switch key updated to ${hotkeySelect.value.toUpperCase()} for ${bindingsState.activeMode.toUpperCase()}`);
    }
  });

  // Switch Type toggle listener
  hotkeyToggle.addEventListener('change', () => {
    const modeState = getActiveModeState();
    if (modeState) {
      modeState.switchType = hotkeyToggle.checked ? 'toggle' : 'hold';
      flashGlobalStatus(`Switch behavior set to ${hotkeyToggle.checked ? 'TOGGLE' : 'HOLD'} for ${bindingsState.activeMode.toUpperCase()}`);
    }
  });

  // Copy userscript button listener in sidebar (onshape only)
  const copySidebarBtn = document.getElementById('btn-copy-userscript-sidebar');
  if (copySidebarBtn) {
    copySidebarBtn.addEventListener('click', openOnshapeDialog);
  }

  // Setup mode pills click listeners
  const pills = modePills.querySelectorAll('.mode-pill');
  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      const mode = pill.getAttribute('data-mode');
      selectMode(mode);
    });
  });

  // Setup lock toggles click listeners for both primary & switched groups
  ['x', 'y', 'z'].forEach(axis => {
    document.getElementById(`lock-primary-${axis}`).addEventListener('click', () => {
      toggleLockState(axis, 'primary');
    });
    document.getElementById(`lock-switched-${axis}`).addEventListener('click', () => {
      toggleLockState(axis, 'switched');
    });
  });

  // Input fields and checkbox event listeners
  ['x', 'y', 'z'].forEach(axis => {
    // Primary Group inputs
    const inputPrim = document.getElementById(`sens-input-primary-${axis}`);
    inputPrim.addEventListener('input', () => {
      handleSensitivityInput(axis, 'primary', inputPrim.value);
    });
    const checkPrim = document.getElementById(`invert-check-primary-${axis}`);
    checkPrim.addEventListener('change', () => {
      saveInvertState(axis, 'primary', checkPrim.checked);
    });

    // Switched Group inputs
    const inputSwitch = document.getElementById(`sens-input-switched-${axis}`);
    inputSwitch.addEventListener('input', () => {
      handleSensitivityInput(axis, 'switched', inputSwitch.value);
    });
    const checkSwitch = document.getElementById(`invert-check-switched-${axis}`);
    checkSwitch.addEventListener('change', () => {
      saveInvertState(axis, 'switched', checkSwitch.checked);
    });
  });

  // Setup bindings select listeners for other options
  document.getElementById('select-orbit-pivot').addEventListener('change', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.pivot = e.target.value;
  });
  document.getElementById('select-orbit-style').addEventListener('change', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.style = e.target.value;
  });
  document.getElementById('check-lock-horizon').addEventListener('change', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.lockHorizon = e.target.checked;
  });
  document.getElementById('select-zoom-mode').addEventListener('change', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.switched.zoomMode = e.target.value;
  });
  document.getElementById('input-zoom-dominance').addEventListener('input', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.switched.zoomDominance = e.target.value;
  });
  document.getElementById('check-zoom-mouse').addEventListener('change', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.switched.zoomMouse = e.target.checked;
  });
  document.getElementById('input-flywalk-speed').addEventListener('input', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.speed = e.target.value;
  });
  document.getElementById('input-flywalk-hold').addEventListener('input', (e) => {
    const modeState = getActiveModeState();
    if (modeState) modeState.hold = e.target.value;
  });

  // Global settings changes
  document.getElementById('app-global-rate').addEventListener('change', (e) => {
    getActiveAppGlobal().rate = e.target.value;
  });
  document.getElementById('app-global-hold').addEventListener('input', (e) => {
    getActiveAppGlobal().hold = e.target.value;
  });

  // Initialize view
  updatePillsVisibility();
  selectMode('orbit');
  loadAppGlobalSettings();
  
  // Recalculate slider position on window resize to ensure correct alignment
  window.addEventListener('resize', () => {
    selectMode(bindingsState.activeMode);
  });
}

function getActiveAppGlobal() {
  return bindingsState.apps[bindingsState.activeApp].global;
}

function getActiveModeState(mode = bindingsState.activeMode) {
  return bindingsState.apps[bindingsState.activeApp].modes[mode];
}

function updatePillsVisibility() {
  const app = bindingsState.activeApp;
  const pillFly = document.getElementById('pill-fly');
  const pillWalk = document.getElementById('pill-walk');
  
  if (app === 'blender' || app === 'sketchup' || app === 'unreal') {
    pillFly.style.display = 'block';
    pillWalk.style.display = 'block';
  } else {
    pillFly.style.display = 'none';
    pillWalk.style.display = 'none';
  }

  // Sidebar Onshape specific buttons
  const onshapeScript = document.getElementById('onshape-userscript-sidebar-only');
  onshapeScript.style.display = (app === 'onshape') ? 'block' : 'none';
  
  // Blender-only option in Pivot
  const option3d = document.getElementById('option-3d-cursor');
  option3d.style.display = (app === 'blender') ? 'block' : 'none';
}

function loadAppGlobalSettings() {
  const global = getActiveAppGlobal();
  document.getElementById('app-global-rate').value = global.rate;
  document.getElementById('app-global-hold').value = global.hold;
}

function selectMode(mode) {
  bindingsState.activeMode = mode;
  
  // Set active class on pill
  const pills = document.querySelectorAll('.mode-pill');
  let activePill = null;
  pills.forEach(pill => {
    if (pill.getAttribute('data-mode') === mode) {
      pill.classList.add('active');
      activePill = pill;
    } else {
      pill.classList.remove('active');
    }
  });

  // Reposition pill indicator slider
  if (activePill) {
    const slider = document.getElementById('mode-slider');
    const rect = activePill.getBoundingClientRect();
    const parentRect = activePill.parentElement.getBoundingClientRect();
    slider.style.left = `${rect.left - parentRect.left}px`;
    slider.style.width = `${rect.width}px`;
  }

  // Hide/Show scheme footer panels
  const orbitFoot = document.getElementById('orbit-scheme-controls');
  const flywalkFoot = document.getElementById('flywalk-scheme-controls');

  orbitFoot.style.display = 'none';
  flywalkFoot.style.display = 'none';

  if (mode === 'orbit') {
    orbitFoot.style.display = 'flex';
  } else if (mode === 'fly' || mode === 'walk') {
    flywalkFoot.style.display = 'flex';
  }

  // Load Switch Key settings for active mode
  const modeState = getActiveModeState(mode);
  if (modeState) {
    document.getElementById('hotkey-select').value = modeState.switchKey || 'shift';
    document.getElementById('hotkey-toggle-type').checked = (modeState.switchType === 'toggle');
  }

  // Load Axis values
  loadAxisSettings();
}

function loadAxisSettings() {
  const mode = bindingsState.activeMode;
  const modeState = getActiveModeState();

  // Axis details mapping for primary and switched groups
  ['x', 'y', 'z'].forEach(axis => {
    // Primary Group
    const primaryState = modeState.primary[axis];
    document.getElementById(`label-primary-${axis}`).textContent = primaryState.name;
    document.getElementById(`sens-input-primary-${axis}`).value = primaryState.sens;
    document.getElementById(`invert-check-primary-${axis}`).checked = primaryState.invert;

    // Switched Group
    const switchedState = modeState.switched[axis];
    document.getElementById(`label-switched-${axis}`).textContent = switchedState.name;
    document.getElementById(`sens-input-switched-${axis}`).value = switchedState.sens;
    document.getElementById(`invert-check-switched-${axis}`).checked = switchedState.invert;
  });

  // Toggle display of Z-axis zoom details in Orbit mode's Switched Zoom settings
  const zoomDetails = document.getElementById('zoom-axis-details');
  if (mode === 'orbit') {
    zoomDetails.style.display = 'flex';
    document.getElementById('select-zoom-mode').value = modeState.switched.zoomMode;
    document.getElementById('input-zoom-dominance').value = modeState.switched.zoomDominance;
    document.getElementById('check-zoom-mouse').checked = modeState.switched.zoomMouse;
  } else {
    zoomDetails.style.display = 'none';
  }

  // Load scheme settings based on mode
  if (mode === 'orbit') {
    document.getElementById('select-orbit-pivot').value = modeState.pivot;
    document.getElementById('select-orbit-style').value = modeState.style;
    document.getElementById('check-lock-horizon').checked = modeState.lockHorizon;
  } else if (mode === 'fly' || mode === 'walk') {
    document.getElementById('input-flywalk-speed').value = modeState.speed;
    document.getElementById('input-flywalk-hold').value = modeState.hold;
  }

  // Set locks visualization
  updateLockVisuals();
}

function updateLockVisuals() {
  const mode = bindingsState.activeMode;
  const modeState = getActiveModeState();

  // Primary Locks
  const primaryLinked = modeState.primary.linked;
  const lockPX = document.getElementById('lock-primary-x');
  const lockPY = document.getElementById('lock-primary-y');
  const lockPZ = document.getElementById('lock-primary-z');
  const wrapPX = lockPX.closest('.linked-input-wrapper');
  const wrapPY = lockPY.closest('.linked-input-wrapper');
  const wrapPZ = lockPZ.closest('.linked-input-wrapper');

  [lockPX, lockPY, lockPZ].forEach(l => l.classList.remove('unlocked'));
  [wrapPX, wrapPY, wrapPZ].forEach(w => w.classList.remove('linked'));

  if (primaryLinked) {
    if (mode === 'orbit') {
      // Orbit primary links all 3: Yaw, Pitch, Roll
      wrapPX.classList.add('linked');
      wrapPY.classList.add('linked');
      wrapPZ.classList.add('linked');
      lockPX.textContent = '🔒';
      lockPY.textContent = '🔒';
      lockPZ.textContent = '🔒';
      lockPX.title = 'Linked to Y/Z';
      lockPY.title = 'Linked to X/Z';
      lockPZ.title = 'Linked to X/Y';
    } else {
      // Fly/Walk primary links X & Y (Yaw & Pitch)
      wrapPX.classList.add('linked');
      wrapPY.classList.add('linked');
      lockPX.textContent = '🔒';
      lockPY.textContent = '🔒';
      lockPX.title = 'Linked to Y';
      lockPY.title = 'Linked to X';

      lockPZ.textContent = '🔓';
      lockPZ.classList.add('unlocked');
      lockPZ.title = 'Unlocked';
    }
  } else {
    [lockPX, lockPY, lockPZ].forEach(l => {
      l.textContent = '🔓';
      l.classList.add('unlocked');
      l.title = 'Unlocked';
    });
  }

  // Switched Locks
  const switchedLinked = modeState.switched.linked;
  const lockSX = document.getElementById('lock-switched-x');
  const lockSY = document.getElementById('lock-switched-y');
  const lockSZ = document.getElementById('lock-switched-z');
  const wrapSX = lockSX.closest('.linked-input-wrapper');
  const wrapSY = lockSY.closest('.linked-input-wrapper');
  const wrapSZ = lockSZ.closest('.linked-input-wrapper');

  [lockSX, lockSY, lockSZ].forEach(l => l.classList.remove('unlocked'));
  [wrapSX, wrapSY, wrapSZ].forEach(w => w.classList.remove('linked'));

  if (switchedLinked) {
    // Switched links link X & Y (Pan X & Pan Y, or Strafe & Forward/Back)
    wrapSX.classList.add('linked');
    wrapSY.classList.add('linked');
    lockSX.textContent = '🔒';
    lockSY.textContent = '🔒';
    lockSX.title = 'Linked to Y';
    lockSY.title = 'Linked to X';

    lockSZ.textContent = '🔓';
    lockSZ.classList.add('unlocked');
    lockSZ.title = 'Unlocked';
  } else {
    [lockSX, lockSY, lockSZ].forEach(l => {
      l.textContent = '🔓';
      l.classList.add('unlocked');
      l.title = 'Unlocked';
    });
  }
}

function toggleLockState(axis, group) {
  const mode = bindingsState.activeMode;
  const modeState = getActiveModeState();
  const groupState = modeState[group];

  if (group === 'primary' && mode === 'orbit') {
    groupState.linked = !groupState.linked;
    flashGlobalStatus(groupState.linked ? 'Orbit turning sensitivities linked (X/Y/Z)' : 'Orbit turning sensitivities unlinked');
  } else {
    // Links between X & Y
    if (axis === 'x' || axis === 'y') {
      groupState.linked = !groupState.linked;
      flashGlobalStatus(groupState.linked ? `${group === 'primary' ? 'Turning' : 'Movement'} sensitivities linked (X/Y)` : `${group === 'primary' ? 'Turning' : 'Movement'} sensitivities unlinked`);
    }
  }

  // Synchronize values if newly linked
  if (groupState.linked) {
    const baseValue = document.getElementById(`sens-input-${group}-${axis}`).value;
    if (group === 'primary' && mode === 'orbit') {
      ['x', 'y', 'z'].forEach(a => {
        groupState[a].sens = baseValue;
      });
    } else {
      ['x', 'y'].forEach(a => {
        groupState[a].sens = baseValue;
      });
    }
    loadAxisSettings();
  } else {
    updateLockVisuals();
  }
}

function handleSensitivityInput(axis, group, value) {
  const mode = bindingsState.activeMode;
  const modeState = getActiveModeState();
  const groupState = modeState[group];

  // Save value
  groupState[axis].sens = value;

  // Synchronize if linked
  if (groupState.linked) {
    if (group === 'primary' && mode === 'orbit') {
      ['x', 'y', 'z'].forEach(a => {
        if (a !== axis) {
          groupState[a].sens = value;
          document.getElementById(`sens-input-${group}-${a}`).value = value;
        }
      });
    } else {
      if (axis === 'x' || axis === 'y') {
        const otherAxis = (axis === 'x') ? 'y' : 'x';
        groupState[otherAxis].sens = value;
        document.getElementById(`sens-input-${group}-${otherAxis}`).value = value;
      }
    }
  }
}

function saveInvertState(axis, group, isChecked) {
  const modeState = getActiveModeState();
  modeState[group][axis].invert = isChecked;
}

// Hook up the dashboard on page load
document.addEventListener('DOMContentLoaded', () => {
  initBindingsDashboard();
});
