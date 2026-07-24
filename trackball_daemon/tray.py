# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""System-tray icon + menu (pystray). Owns nothing but the icon; lifecycle calls go to App.

Runs the icon on its own thread so the Tk mainloop can own the main thread. Menu actions
that touch the UI are marshalled back to the Tk thread by App (root.after).
"""
import sys
import threading

import pystray
from PIL import Image, ImageDraw

# --- "Start at login" (Windows HKCU Run key) -------------------------------------------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "TrackballDaemon"


def _startup_command():
    if getattr(sys, "frozen", False):          # packaged exe (Nuitka/PyInstaller)
        return f'"{sys.executable}"'
    # script run: prefer pythonw so no console appears at login
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        import os
        if os.path.exists(pyw):
            exe = pyw
    return f'"{exe}" -m trackball_daemon'


def is_startup_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _RUN_NAME)
            return True
    except OSError:
        return False


def set_startup_enabled(enable):
    if sys.platform != "win32":
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
        if enable:
            winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(k, _RUN_NAME)
            except OSError:
                pass


def _make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((5, 5, 59, 59), fill=(38, 120, 214, 255))      # ball
    d.ellipse((20, 18, 40, 38), fill=(235, 238, 245, 255))   # highlight
    return img


class TrayController:
    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon(
            "TrackballDaemon", _make_icon_image(), "Trackball Daemon",
            menu=self._build_menu(),
        )
        self._thread = None

    def _status_text(self, _item):
        return f"Status: {'Connected' if self.app.is_connected() else 'Disconnected'}"

    def _apps_text(self, _item):
        return f"Apps: {self.app.app_connection_summary()}"

    def _health_text(self, _item):
        return f"Health: {self.app.runtime_health_summary()}"

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Open Settings", lambda icon, item: self.app.open_settings(),
                             default=True),
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.MenuItem(self._apps_text, None, enabled=False),
            pystray.MenuItem(self._health_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Recenter 3D view", lambda icon, item: self.app.engine.reset_view()),
            pystray.MenuItem("Show control panel", self._toggle_hud,
                             checked=lambda item: self.app.config.snapshot().global_value(
                                 "hud.visible")),
            pystray.MenuItem("Start at login", self._toggle_startup,
                             checked=lambda item: is_startup_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self.app.quit()),
        )

    def _toggle_startup(self, icon, item):
        set_startup_enabled(not is_startup_enabled())

    def _toggle_hud(self, icon, _item):
        visible = self.app.config.snapshot().global_value("hud.visible")
        self.app.set_control_hud_visible(not visible)
        try:
            icon.update_menu()
        except Exception:
            pass

    def start(self):
        self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        self._thread.start()

    def refresh(self):
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
