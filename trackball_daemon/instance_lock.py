# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Process-lifetime single-controller ownership for the Windows desktop session."""

import ctypes
import sys

from .product import (
    LEGACY_SINGLE_INSTANCE_MUTEX,
    PRODUCT_NAME,
    SINGLE_INSTANCE_MUTEX,
)


#: Every name a running daemon claims, and refuses to start if anything else already holds.
#:
#: The legacy name is claimed alongside the current one because an earlier build tests only that
#: name. Dropping it during the rename would have made the two generations invisible to each other,
#: which is the one failure this guard exists to prevent: two copies driving the same controller,
#: each synthesising pointer motion the other does not know about. Holding both names makes the
#: check symmetric -- either generation sees the other, whichever started first.
MUTEX_NAMES = (SINGLE_INSTANCE_MUTEX, LEGACY_SINGLE_INSTANCE_MUTEX)

ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """Hold the named mutexes for the daemon process lifetime."""

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
        self._handles = ()

    @property
    def acquired(self):
        return bool(self._handles) or self._create_mutex is None

    def acquire(self):
        if self._create_mutex is None:  # non-Windows development/test host
            return True
        if self._handles:
            return True
        handles = []
        for name in MUTEX_NAMES:
            handle = self._create_mutex(None, False, name)
            if not handle:
                error = self._get_last_error()          # read before CloseHandle overwrites it
                self._release(handles)
                raise ctypes.WinError(error)
            if self._get_last_error() == ERROR_ALREADY_EXISTS:
                # Someone else owns this name. Give up every name, so a second instance that lost
                # the race leaves nothing behind for a third to trip over.
                self._close_handle(handle)
                self._release(handles)
                return False
            handles.append(handle)
        self._handles = tuple(handles)
        return True

    def close(self):
        handles, self._handles = self._handles, ()
        self._release(handles)

    def _release(self, handles):
        for handle in handles:
            self._close_handle(handle)

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"another {PRODUCT_NAME} instance already owns the controller")
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()


def notify_already_running():
    message = (
        f"{PRODUCT_NAME} is already running. Close the existing tray instance before starting "
        "another installed or source-tree copy.")
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            None, message, f"{PRODUCT_NAME} already running", 0x00000030)
    else:
        print(message, file=sys.stderr)
    return message
