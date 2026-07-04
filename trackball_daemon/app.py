"""Orchestrator: wires config + output engine + BLE thread + tray + settings window.

Threading model (Windows):
  * main thread  -> hidden Tk root + mainloop (owns all GUI; window shown/hidden on demand)
  * tray thread  -> pystray icon loop (menu callbacks marshalled to Tk via root.after)
  * ble thread   -> asyncio BLE loop (unchanged data path)
  * debug thread -> optional pygame cube (--debug)
The process stays alive on the Tk mainloop and exits only when tray -> Quit tears it down.
"""
import json
import threading
import tkinter as tk

from . import integrations
from .autocad_driver import AutoCADPluginLoader
from .ble import start_ble_thread
from .config import Config, effective_scheme
from .navbroker import NavBroker
from .onshape_bridge import OnshapeBridge
from .output import OutputEngine
from .paths import user_config_dir
from .solidworks_driver import SolidWorksDriver
from .tray import TrayController
from .ui import SettingsWindow
from .util import get_logger
from .winfocus import foreground_process_name


class App:
    def __init__(self, debug=False):
        self.debug = debug
        self.log = get_logger()
        self.config = Config().load()
        self.first_run = self.config.first_run
        self.engine = OutputEngine(self.config)
        self.stop_event = threading.Event()
        self._status = "starting"
        self._last_pushed = None
        self.root = None
        self.ui = None
        self.tray = None
        self.broker = None
        self.sw_driver = None
        self.onshape_bridge = None
        self.acad_loader = None
        self.config.add_listener(self.on_config_changed)

        # 3D-app nav bridge (127.0.0.1). The engine forwards orbit/pan/zoom deltas here,
        # gated on the focused CAD app; socket add-ons connect and drive their app's camera.
        self.broker = NavBroker(self.config.data["bridge"]["port"], self._on_clients_changed,
                                rate_hz=self.config.data["bridge"].get("rate_hz", 30))
        # SolidWorks is driven by external COM automation, not a socket add-in: this in-process
        # driver attaches to a running SolidWorks and moves its camera directly. It lives parallel
        # to the broker; _nav_sink routes solidworks frames here instead of to the broker.
        self.sw_driver = SolidWorksDriver(self._on_sw_connection_changed,
                                          rate_hz=self.config.data["bridge"].get("rate_hz", 30))
        # Onshape (browser) is driven by an in-process bridge that impersonates the 3Dconnexion
        # local NL-Proxy service Onshape's page connects to (TLS WebSocket on 127.51.68.120:8181).
        # Like the SW driver it lives parallel to the broker; _nav_sink routes onshape frames here.
        ocfg = self.config.data.get("onshape", {})
        self.onshape_bridge = OnshapeBridge(
            self._on_onshape_connection_changed,
            rate_hz=self.config.data["bridge"].get("rate_hz", 30),
            host=ocfg.get("address") or None, port=ocfg.get("port") or None,
            cert_path=ocfg.get("cert_path") or None, key_path=ocfg.get("key_path") or None)
        # AutoCAD is a BROKER app: its compiled NETLOAD plugin (plugin_src/autocad) drives the
        # live GraphicsSystem view in-process and connects to the nav broker like the other
        # socket add-ons. COM's only remaining job is DELIVERY -- this loader NETLOADs the
        # bundled plugin into a running AutoCAD (the old COM nav transport is archived at
        # archive/autocad_com_transport/; it raced the plugin for the early frames).
        self.acad_loader = AutoCADPluginLoader()
        self.engine.nav_sink = self._nav_sink
        # Connected-app status feeds the tray "Apps:" line + the 3D-Apps rows. It merges the
        # broker's socket add-ons with the SolidWorks COM driver and the Onshape bridge states.
        self._apps_lock = threading.Lock()
        self._broker_apps = []                     # [(app, version, pid)] from the broker
        self._sw_apps = []                         # [(app, version, pid)] from the SW driver (0/1)
        self._onshape_apps = []                    # [(app, version, pid)] from the Onshape bridge
        self.connected_apps = []
        self._last_apps_pushed = None
        self._engine_app = None
        self._last_scheme_pushed = None

    # --- BLE wiring (unchanged data path) -----------------------------------------
    def get_ble_params(self):
        d = self.config.data["device"]
        return d["name"], d.get("address", ""), d["char_uuid"]

    def set_status(self, text):
        # Called from the BLE thread; just store + log. The Tk poll pushes it to the GUI.
        self._status = text
        self.log.info(text)

    def status_text(self):
        return self._status

    def is_connected(self):
        return self._status.startswith(("connected", "subscribed"))

    def on_config_changed(self):
        self.engine.apply_config()
        self._apply_rates()
        self._apply_schemes()

    def _effective_scheme(self, key):
        g = self.config.data["general"].get("scheme", {})
        a = (self.config.data["apps"].get(key) or {}).get("bindings", {}).get("scheme", {})
        return effective_scheme(g, a)

    def _broker_excluded_keys(self):
        """Apps whose frames do NOT go to the socket broker (in-process transports). AutoCAD is a
        broker app -- its NETLOADed plugin is the sole transport (the COM one is archived)."""
        return ("solidworks", "onshape")

    def _apply_schemes(self):
        """Push each CAD app's effective control scheme to its driver. Mirrors _apply_rates:
        the broker gets the focused socket app's scheme; the SW driver gets SolidWorks'."""
        if self.broker is not None:
            key = self._engine_app
            if not key or key in self._broker_excluded_keys():
                key = self.config.data.get("active_app", "fusion360")
            scheme = self._effective_scheme(key)
            # Apps with a richer nav set (Blender, Unreal) carry their own "advanced" options. Attach
            # the FOCUSED broker app's advanced block as the additive "adv" object on every frame; the
            # add-on reads its own settings and apps without an advanced block (Fusion/FreeCAD) ignore
            # the extra key. `key` is the focused broker app (it persists across a focus loss, so a
            # mode change made while the settings window is up still targets the right app), so
            # nav_mode delivery is robust -- switching to fly/walk reaches the add-on on focus.
            adv = (self.config.data["apps"].get(key) or {}).get("advanced")
            self.broker.set_scheme(**scheme, advanced=adv)
            nav = (adv or {}).get("nav_mode")
            sig = (key, scheme["orbit_pivot"], scheme["orbit_style"], scheme["zoom_mode"], nav)
            if sig != self._last_scheme_pushed:
                self._last_scheme_pushed = sig
                self.log.info("scheme -> %s: pivot=%s style=%s zoom=%s nav=%s"
                              % (key, scheme["orbit_pivot"], scheme["orbit_style"],
                                 scheme["zoom_mode"], nav))
        if self.sw_driver is not None:
            self.sw_driver.set_scheme(**self._effective_scheme("solidworks"))
            swcfg = self.config.data["apps"].get("solidworks") or {}
            self.sw_driver.set_pivot_hold(swcfg.get("view_pivot_hold_sec", 0.5))
        if self.onshape_bridge is not None:
            self.onshape_bridge.set_scheme(**self._effective_scheme("onshape"))

    def _app_rate(self, key):
        """Effective viewport/flush rate (Hz) for app `key`: its per-app override, or the global
        bridge default when the per-app value is 0/unset."""
        appcfg = self.config.data["apps"].get(key) or {}
        rate = appcfg.get("rate_hz") or 0
        if rate and rate > 0:
            return rate
        return self.config.data["bridge"].get("rate_hz", 30)

    def _apply_rates(self):
        """Run each CAD component at its app's configured rate. The SolidWorks driver always uses
        SolidWorks' rate; the socket broker uses the focused socket app's rate (falling back to the
        configured active_app). Called on focus change and on any config edit, so per-app sliders
        apply live."""
        if self.sw_driver is not None:
            self.sw_driver.set_rate(self._app_rate("solidworks"))
        if self.onshape_bridge is not None:
            self.onshape_bridge.set_rate(self._app_rate("onshape"))
        if self.broker is not None:
            key = self._engine_app
            if not key or key in self._broker_excluded_keys():
                key = self.config.data.get("active_app", "fusion360")
            self.broker.set_rate(self._app_rate(key))

    # --- 3D-app nav routing -------------------------------------------------------
    _APP_PROC_HINTS = {
        "fusion360": ("fusion",),
        "blender": ("blender",),
        "freecad": ("freecad",),
        "sketchup": ("sketchup",),
        "unreal": ("unrealeditor", "ue4editor"),
        "solidworks": ("sldworks",),
        "autocad": ("acad",),
    }
    _BROWSER_PROCS = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")

    def _foreground_app_key(self):
        proc = foreground_process_name()
        if not proc:
            return None
        for key, hints in self._APP_PROC_HINTS.items():
            if any(h in proc for h in hints):
                return key
        # Onshape runs in a browser, so the foreground PROCESS is the browser (chrome/msedge/...),
        # not "onshape". We can't match by window title either -- Onshape titles the tab with the
        # document name (e.g. "monstera leaf | Part Studio 1"), not "Onshape". Instead: a browser is
        # foreground AND an Onshape tab has completed the 3Dconnexion handshake (bridge connected).
        # The bridge's own focus signal (Onshape reports when its 3D view is active) is the finer
        # gate, applied in the driver before any camera move -- so we never move a backgrounded tab.
        if any(b in proc for b in self._BROWSER_PROCS):
            if self.onshape_bridge is not None and self.onshape_bridge.is_connected():
                return "onshape"
        return None

    def _active_app_key(self):
        key = self._foreground_app_key()
        if key is None:
            return None
        appcfg = self.config.data["apps"].get(key)
        if not appcfg or not appcfg.get("enabled"):
            return None
        return key

    def _nav_sink(self, ox, oy, oz, px, py, zoom):
        # Called on the BLE thread. Stream only when a supported, enabled app is focused.
        key = self._active_app_key()
        if key is None:
            return
        if key != self._engine_app:               # drive each app with its own bindings + rate
            self._engine_app = key
            self.engine.set_active_bindings(key)
            self._apply_rates()
            self._apply_schemes()
        if key == "onshape":                      # browser bridge (NL-Proxy emulation), not the broker
            self.onshape_bridge.submit(ox, oy, oz, px, py, zoom)
        elif key == "solidworks":                 # external COM automation, not the socket broker
            self.sw_driver.submit(ox, oy, oz, px, py, zoom)
        else:                                     # socket add-ons (Fusion, AutoCAD, ...) via the broker
            self.broker.submit(ox, oy, oz, px, py, zoom)

    def _on_clients_changed(self, infos):
        # Broker add-on handshakes changed (any broker thread). Merge with the SW driver state.
        with self._apps_lock:
            self._broker_apps = list(infos)
            self._refresh_connected_apps()

    def _on_sw_connection_changed(self, connected, version):
        # SolidWorks COM driver connected/disconnected (driver worker thread).
        with self._apps_lock:
            self._sw_apps = [("solidworks", version or "COM", 0)] if connected else []
            self._refresh_connected_apps()

    def _on_onshape_connection_changed(self, connected, version):
        # Onshape browser bridge connected/disconnected (bridge server/reader thread).
        with self._apps_lock:
            self._onshape_apps = [("onshape", version or "web", 0)] if connected else []
            self._refresh_connected_apps()

    def _refresh_connected_apps(self):
        # Caller holds _apps_lock. Rebuild the combined list (a new object => lockless readers ok).
        # AutoCAD appears via _broker_apps: its plugin handshakes with the broker like any add-on.
        apps = self._broker_apps + self._sw_apps + self._onshape_apps
        self.connected_apps = apps
        self.log.info("3D apps: " + (", ".join(f"{a} v{v}" for a, v, _ in apps) or "none"))

    def app_connection_summary(self):
        if not self.connected_apps:
            return "none"
        return ", ".join(f"{a} v{v}" for a, v, _ in self.connected_apps)

    # --- lifecycle ----------------------------------------------------------------
    def start(self):
        self.root = tk.Tk()
        self.root.withdraw()                       # headless: no window on startup
        self.ui = SettingsWindow(self.root, self)

        self.tray = TrayController(self)
        self.tray.start()

        # Publish the bridge port for the add-ons, then start the broker.
        try:
            with open(user_config_dir() / "bridge.json", "w", encoding="utf-8") as f:
                json.dump({"port": self.config.data["bridge"]["port"]}, f)
        except OSError:
            pass
        self.broker.start()
        self.sw_driver.start()                     # attaches to SolidWorks if/when it's running
        self.onshape_bridge.start()                # serves the NL-Proxy endpoint for Onshape
        self.acad_loader.start()                   # NETLOADs the AutoCAD plugin if/when it's running

        # One-click-free add-in refresh: re-copy any installed add-in the daemon now ships a
        # newer version of (e.g. this release's viewport-refresh fix). Takes effect on the
        # CAD app's next launch.
        for key, old, new in integrations.auto_update(self.config):
            self.log.info(f"updated {key} add-in {old} -> {new} (restart {key} to apply)")

        notify_cb = lambda sender, data: self.engine.handle_packet(bytes(data))
        start_ble_thread(self.get_ble_params, notify_cb, self.set_status, self.stop_event)

        if self.debug:
            from .debugview import start_debug_view
            start_debug_view(self.engine, self, self.stop_event)

        if self.first_run:
            self.root.after(500, self.open_settings)
        self.root.after(300, self._poll)
        self.root.mainloop()

    def open_settings(self):
        # Safe from any thread: marshal the GUI work onto the Tk thread.
        if self.root is not None:
            self.root.after(0, self.ui.show)

    def _poll(self):
        if self._status != self._last_pushed:
            self._last_pushed = self._status
            if self.ui is not None:
                self.ui.update_status(self._status)
            if self.tray is not None:
                self.tray.refresh()
        summary = self.app_connection_summary()
        if summary != self._last_apps_pushed:
            self._last_apps_pushed = summary
            if self.tray is not None:
                self.tray.refresh()
            if self.ui is not None:
                self.ui.update_app_connections(self.connected_apps)
        if not self.stop_event.is_set():
            self.root.after(300, self._poll)

    def quit(self):
        # Called from the tray thread.
        self.stop_event.set()
        if self.broker is not None:
            self.broker.stop()
        if self.sw_driver is not None:
            self.sw_driver.stop()
        if self.onshape_bridge is not None:
            self.onshape_bridge.stop()
        if self.acad_loader is not None:
            self.acad_loader.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.root is not None:
            self.root.after(0, self._shutdown)

    def _shutdown(self):
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
