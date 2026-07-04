"""Foreground-window process detection (Windows). Used to route navigation only to the
CAD app the user is actually looking at -- SpaceMouse-style. Fails open (returns None) on
non-Windows or on any error, so callers can choose to stream anyway."""
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def foreground_window_title():
        """Title text of the foreground window, or "" on failure. Used to recognize browser-based
        apps (e.g. Onshape) by their tab title, since the foreground PROCESS is just the browser."""
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = _user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value or ""
        except Exception:
            return ""

    def foreground_process_name():
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None
            h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not h:
                return None
            try:
                buf = ctypes.create_unicode_buffer(512)
                size = wintypes.DWORD(len(buf))
                if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    path = buf.value
                    return path.rsplit("\\", 1)[-1].lower()
            finally:
                _kernel32.CloseHandle(h)
        except Exception:
            return None
        return None
else:
    def foreground_process_name():
        return None

    def foreground_window_title():
        return ""
