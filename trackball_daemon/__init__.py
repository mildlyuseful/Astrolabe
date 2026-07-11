"""Trackball Daemon — system-tray app + settings UI around the BLE trackball.

Module layout (kept deliberately separate so it bundles cleanly with Nuitka onedir
or installs from GitHub without source changes):

  paths        - per-user config directory
  util         - logging (file-based, safe under pythonw / no console)
  config       - the single source of truth (JSON in the per-user config dir)
  output       - UNCHANGED output/injection math (SendInput + quaternion + routing),
                 reading its numbers from config instead of module constants
  ble          - UNCHANGED BLE/data-ingestion loop (scan/connect/subscribe/reconnect)
  winfocus     - foreground-window process detection (route nav to the focused app)
  navbroker    - 127.0.0.1 socket broker streaming nav deltas to the socket add-ons
  solidworks_driver - in-process SolidWorks COM driver (no add-in)
  onshape_bridge    - in-process Onshape TLS-WebSocket bridge (NL-Proxy emulator)
  autocad_driver    - AutoCAD plugin loader (COM NETLOAD delivery only)
  integrations - 3D-app registry (detect / install / enable / auto-update add-ons)
  tray         - pystray system-tray icon + menu (process lifecycle)
  ui           - Tkinter settings window (hides to tray on close)
  debugview    - optional pygame cube window (--debug only)
  app          - orchestrator that wires the above together
"""

__version__ = "0.1.62"
