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

from .product import (
    DISPLAY_NAME,
    LEGACY_STARTUP_VALUE_NAME,
    PRODUCT_NAME,
    STARTUP_REGISTRY_KEY,
    STARTUP_VALUE_NAME,
)

# --- "Start at login" (Windows HKCU Run key) -------------------------------------------
# Two value names, one setting. The daemon writes only the current name, but a machine that ran an
# earlier build has the registration under the legacy one, so reads accept either and both writes
# clear the legacy name. Leaving both registered would launch two copies at login, and the second
# would greet the user with an "already running" dialog.
_RUN_KEY = STARTUP_REGISTRY_KEY
_RUN_NAME = STARTUP_VALUE_NAME
_LEGACY_RUN_NAME = LEGACY_STARTUP_VALUE_NAME


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


def _read_startup_value(key, name):
    import winreg
    try:
        value, _kind = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return None


def _delete_startup_value(key, name):
    import winreg
    try:
        winreg.DeleteValue(key, name)
    except OSError:
        pass


# The three functions below take the subkey as an argument for the same reason SingleInstanceGuard
# takes its mutex calls: the registry is the behaviour under test, and tests must be able to exercise
# it somewhere other than the key that governs the user's actual login.
def is_startup_enabled(run_key=_RUN_KEY):
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as k:
            return any(_read_startup_value(k, name) is not None
                       for name in (_RUN_NAME, _LEGACY_RUN_NAME))
    except OSError:
        return False


def set_startup_enabled(enable, run_key=_RUN_KEY):
    if sys.platform != "win32":
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as k:
        if enable:
            winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            _delete_startup_value(k, _RUN_NAME)
        _delete_startup_value(k, _LEGACY_RUN_NAME)


def migrate_startup_entry(run_key=_RUN_KEY):
    """Move an earlier build's Start at login registration onto the current value name.

    The legacy value's command is copied verbatim rather than regenerated. It records which copy of
    the daemon the user registered, and a source-tree run must not quietly repoint an installed
    build's login entry at itself. The new name is written before the old one is removed, so an
    interruption leaves a duplicate that the next start cleans up, never a lost setting -- and never
    two registrations that would both fire at login.

    Returns the command carried across, or None when there was nothing to carry.
    """
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            command = _read_startup_value(k, _LEGACY_RUN_NAME)
            if command is None:
                return None
            if _read_startup_value(k, _RUN_NAME) is None:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, command)
            _delete_startup_value(k, _LEGACY_RUN_NAME)
            return command
    except OSError:
        return None


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
            PRODUCT_NAME, _make_icon_image(), self._title_text(),
            menu=self._build_menu(),
        )
        self._thread = None

    def _title_text(self):
        connection = "Connected" if self.app.is_connected() else "Disconnected"
        return f"{DISPLAY_NAME} — {connection} — {self.app.battery_status_text()}"

    def _status_text(self, _item):
        return f"Status: {'Connected' if self.app.is_connected() else 'Disconnected'}"

    def _battery_text(self, _item):
        return self.app.battery_status_text()

    def _apps_text(self, _item):
        return f"Apps: {self.app.app_connection_summary()}"

    def _health_text(self, _item):
        return f"Health: {self.app.runtime_health_summary()}"

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Open Settings", lambda icon, item: self.app.open_settings(),
                             default=True),
            pystray.MenuItem("Setup guide…", lambda icon, item: self.app.open_onboarding()),
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.MenuItem(self._battery_text, None, enabled=False),
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
            self.icon.title = self._title_text()
            self.icon.update_menu()
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
