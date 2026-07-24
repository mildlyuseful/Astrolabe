"""Trackball Daemon — system-tray app + settings UI around the BLE trackball.

Module layout (kept deliberately separate so it bundles cleanly with Nuitka onedir
or installs from GitHub without source changes):

  paths        - per-user config directory
  util         - logging (file-based, safe under pythonw / no console)
  config_store - sparse persistent configuration authority and immutable snapshots
  config       - legacy migration/normalization helpers
  runtime_state - immutable live interaction state
  output       - output/injection math (SendInput + quaternion + app-aware routing)
  devices      - BLE/data-ingestion providers and descriptors
  winfocus     - read-only foreground-window process detection
  navigation_router - target/revision isolation and transport delivery
  navbroker    - 127.0.0.1 socket broker streaming target-matched deltas to add-ons
  solidworks_driver - daemon-side SolidWorks COM direct transport (no add-in)
  onshape_bridge    - daemon-side Onshape TLS-WebSocket bridge (NL-Proxy emulator)
  autocad_driver    - AutoCAD plugin loader (COM staging/trust/NETLOAD only)
  app_registry - canonical app identity, order, transport, modes, and capabilities
  integrations - host detection, setup, install, enable, and add-on auto-update
  tray         - pystray system-tray icon + menu (process lifecycle)
  ui           - Tkinter settings window (hides to tray on close)
  debugview    - optional pygame cube window (--debug only)
  app          - orchestrator that wires the above together
"""

__version__ = "0.1.75"
