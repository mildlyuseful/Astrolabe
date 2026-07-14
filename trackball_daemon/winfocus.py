"""Foreground process detection plus independent change publication."""
import logging
import sys
import threading


logger = logging.getLogger("trackball_daemon.winfocus")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

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


class ForegroundMonitor:
    """Poll foreground identity independently of motion and publish only stable changes."""

    def __init__(self, callback, *, query=foreground_process_name, poll_interval=0.2):
        if not callable(callback) or not callable(query):
            raise TypeError("foreground callback and query must be callable")
        self._callback = callback
        self._query = query
        self._poll_interval = max(0.02, float(poll_interval))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force = True
        self._current = None
        self._initialized = False
        self._thread = None

    @property
    def current_process(self):
        with self._lock:
            return self._current

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run, name="astrolabe-foreground", daemon=True)
        self._thread.start()

    def refresh(self):
        with self._lock:
            self._force = True
        self._wake.set()

    def poll_once(self, *, force=False):
        try:
            process_name = self._query()
        except Exception:
            logger.exception("Foreground process query failed")
            process_name = None
        with self._lock:
            changed = force or self._force or not self._initialized or process_name != self._current
            self._force = False
            self._initialized = True
            self._current = process_name
        if changed:
            try:
                self._callback(process_name)
            except Exception:
                logger.exception("Foreground change callback failed")
        return process_name

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._wake.wait(self._poll_interval)
            self._wake.clear()

    def stop(self):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError("foreground monitor did not stop")
        self._thread = None
