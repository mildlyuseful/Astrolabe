# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Show the settings UI in its own window.

Edge ships with Windows 10 and 11, and its app mode gives a chromeless window around one page, so
the UI needs no bundled browser engine. Chrome works the same way; failing both, the default
browser opens the same URL. A dedicated profile directory keeps the window off the user's browsing
profile, extensions, and session restore.
"""

import logging
import subprocess
import sys
import webbrowser

from ..paths import user_config_dir

logger = logging.getLogger("trackball_daemon.webui")

WINDOW_TITLE = "Astrolabe"
APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def _browser():
    if sys.platform != "win32":
        return None
    import winreg
    for exe in ("msedge.exe", "chrome.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, f"{APP_PATHS}\\{exe}") as key:
                    path, _ = winreg.QueryValueEx(key, "")
                    if path:
                        return path
            except OSError:
                continue
    return None


def _focus_existing():
    """Raise an already-open settings window. True if one was found."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if title.value == WINDOW_TITLE:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    user32.ShowWindow(found[0], 9)  # SW_RESTORE
    user32.SetForegroundWindow(found[0])
    return True


def open_window(url, *, reuse=True):
    if reuse and _focus_existing():
        return
    browser = _browser()
    if browser is None:
        webbrowser.open(url)
        return
    profile = user_config_dir() / "ui-profile"
    args = [browser, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1180,760",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-features=Translate,msEdgeSidebarV2,msHubApps"]
    try:
        subprocess.Popen(args, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError:
        logger.exception("could not start %s; falling back to the default browser", browser)
        webbrowser.open(url)
