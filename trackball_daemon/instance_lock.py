"""Process-lifetime single-controller ownership for the Windows desktop session."""

import ctypes
import sys


MUTEX_NAME = r"Local\TrackballDaemon.Controller.v1"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """Hold a named mutex handle for the daemon process lifetime."""

    def __init__(self, *, create_mutex=None, close_handle=None, get_last_error=None):
        if create_mutex is None and sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            create_mutex = kernel32.CreateMutexW
            create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            create_mutex.restype = ctypes.c_void_p
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_bool
            get_last_error = kernel32.GetLastError
            get_last_error.argtypes = ()
            get_last_error.restype = ctypes.c_ulong
        self._create_mutex = create_mutex
        self._close_handle = close_handle
        self._get_last_error = get_last_error
        self._handle = None

    @property
    def acquired(self):
        return self._handle is not None or self._create_mutex is None

    def acquire(self):
        if self._create_mutex is None:  # non-Windows development/test host
            return True
        if self._handle is not None:
            return True
        handle = self._create_mutex(None, False, MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(self._get_last_error())
        if self._get_last_error() == ERROR_ALREADY_EXISTS:
            self._close_handle(handle)
            return False
        self._handle = handle
        return True

    def close(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            self._close_handle(handle)

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("another Trackball Daemon instance already owns the controller")
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()


def notify_already_running():
    message = (
        "Trackball Daemon is already running. Close the existing tray instance before starting "
        "another installed or source-tree copy.")
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            None, message, "Trackball Daemon already running", 0x00000030)
    else:
        print(message, file=sys.stderr)
    return message
