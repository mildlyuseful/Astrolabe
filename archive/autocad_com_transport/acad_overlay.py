"""Tiny always-on-top rotating-cube overlay -- the AutoCAD deferred-orbit "gap closer".

WHY: over COM, changing AutoCAD's 3D view direction regenerates the drawing every time (see
autocad_driver.py's docstring and docs/apps/autocad.md 8.10/8.13 -- a full second sweep of
the type library found no regen-free rotation, and the ObjectARX route needs a compiler this machine
doesn't have). So the driver's deferred mode applies the orbit only when the gesture PAUSES -- which
leaves the viewport frozen DURING the gesture. This overlay closes that gap: a small wireframe cube
+ RGB axis tripod (X red, Y green, Z blue -- like the UCS icon), floating over the centre of the
AutoCAD window, rotates smoothly with the ACCUMULATED not-yet-applied orbit, so the user can aim the
orientation live; the drawing then snaps once, cleanly, when they stop. It is pure feedback -- it
never touches AutoCAD or COM.

HOW: a ctypes-only Win32 layered window -- WS_EX_LAYERED|TRANSPARENT (click-through) |NOACTIVATE
(never steals focus) |TOOLWINDOW (no taskbar/alt-tab) |TOPMOST -- colour-keyed so only the wireframe
pixels show, GDI lines into a memory bitmap, blitted per update. No new dependencies.

THREAD RULE: an OrbitOverlay must be created and driven by ONE thread (the AutoCAD driver's worker);
it pumps its own message queue on every call. Any Win32 failure just disables the instance (the
driver additionally guards every call and disables the feature on the first exception).

The projection maths (project_cube) is pure and unit-tested; only the drawing is platform-bound.
"""
import math
import sys

_WIN32 = sys.platform == "win32"

WORLD_UP = (0.0, 0.0, 1.0)            # AutoCAD is Z-up (same convention as autocad_driver)

# unit cube: 8 vertices, the 12 edges join vertices differing in exactly one coordinate
_VERTS = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8)
          if sum(1 for i in range(3) if _VERTS[a][i] != _VERTS[b][i]) == 1]
_AXES = (((1.0, 0.0, 0.0), "x"), ((0.0, 1.0, 0.0), "y"), ((0.0, 0.0, 1.0), "z"))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else (0.0, 0.0, 1.0)


def project_cube(view_dir):
    """Project the wireframe unit cube + axis tripod as seen from ``view_dir`` (AutoCAD's VIEWDIR
    convention: target->camera, world Z-up). Returns ``[(x1, y1, x2, y2, kind)]`` segments with
    coordinates in [-1, 1] (screen-style, +y DOWN) and kind in ``"cube" | "x" | "y" | "z"``.
    Pure math (unit-tested); the Win32 part below just draws these segments."""
    d = _norm(view_dir)
    right = _cross(WORLD_UP, d)
    if (right[0] * right[0] + right[1] * right[1] + right[2] * right[2]) < 1e-9:
        right = (1.0, 0.0, 0.0)        # top/bottom view: same degenerate fallback as the driver
    right = _norm(right)
    up = _norm(_cross(d, right))
    s = 1.0 / math.sqrt(3.0)           # cube corner |(1,1,1)| -> exactly 1.0

    def pt(v, scale):
        return (scale * (v[0] * right[0] + v[1] * right[1] + v[2] * right[2]),
                -scale * (v[0] * up[0] + v[1] * up[1] + v[2] * up[2]))

    segs = []
    for a, b in _EDGES:
        x1, y1 = pt(_VERTS[a], s)
        x2, y2 = pt(_VERTS[b], s)
        segs.append((x1, y1, x2, y2, "cube"))
    for axis, kind in _AXES:
        x2, y2 = pt(axis, 0.95)        # tripod stays just inside the unit box
        segs.append((0.0, 0.0, x2, y2, kind))
    return segs


# --- Win32 layered window (ctypes only; everything below is Windows-specific) ------------------
if _WIN32:
    import ctypes
    from ctypes import wintypes as wt

    _u32 = ctypes.windll.user32
    _g32 = ctypes.windll.gdi32
    _k32 = ctypes.windll.kernel32

    # the WNDPROC must have the exact 64-bit-safe signature or the callback corrupts the stack
    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    _u32.DefWindowProcW.restype = ctypes.c_ssize_t
    _u32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    _u32.CreateWindowExW.restype = wt.HWND

    class _WNDCLASSW(ctypes.Structure):
        # handle fields typed as generic HANDLEs (wintypes lacks HCURSOR on some Python versions)
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", _WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HANDLE), ("hIcon", wt.HANDLE), ("hCursor", wt.HANDLE),
                    ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                    ("lpszClassName", wt.LPCWSTR)]

    _CLASS_NAME = "TrackballAcadOrbitOverlay"
    # keep the WNDPROC thunk referenced for the life of the process (GC'ing it = crash)
    _wndproc_ref = _WNDPROC(lambda h, m, w, l: _u32.DefWindowProcW(h, m, w, l))
    _class_ok = None

    _WS_POPUP = 0x80000000
    _EX_STYLE = (0x00080000 |          # WS_EX_LAYERED     (colour-key/alpha window)
                 0x00000020 |          # WS_EX_TRANSPARENT (click-through)
                 0x00000080 |          # WS_EX_TOOLWINDOW  (no taskbar / alt-tab entry)
                 0x08000000 |          # WS_EX_NOACTIVATE  (never steals focus)
                 0x00000008)           # WS_EX_TOPMOST
    _LWA_KEY_ALPHA = 0x1 | 0x2         # LWA_COLORKEY | LWA_ALPHA
    _HWND_TOPMOST = ctypes.c_void_p(-1)
    _SWP_SHOW_NOACT = 0x0010 | 0x0040  # SWP_NOACTIVATE | SWP_SHOWWINDOW
    _SW_HIDE = 0
    _SRCCOPY = 0x00CC0020
    _PM_REMOVE = 1
    _KEY_COLOR = 0x00000000            # pure black background -> transparent via the colour key
    # pen colours are COLORREF (0x00BBGGRR); axis colours mirror AutoCAD's UCS icon
    _COLORS = {"cube": 0x00EBEBEB,     # light grey
               "x": 0x005050EB,        # red
               "y": 0x0060DC60,        # green
               "z": 0x00FF9660}        # blue

    def _ensure_class():
        global _class_ok
        if _class_ok is not None:
            return _class_ok
        wc = _WNDCLASSW()
        wc.lpfnWndProc = _wndproc_ref
        wc.hInstance = _k32.GetModuleHandleW(None)
        wc.lpszClassName = _CLASS_NAME
        atom = _u32.RegisterClassW(ctypes.byref(wc))
        _class_ok = bool(atom) or _k32.GetLastError() == 1410   # ERROR_CLASS_ALREADY_EXISTS
        return _class_ok


class OrbitOverlay:
    """One small click-through overlay window. Create and drive it from ONE thread only."""

    def __init__(self, size=170, alpha=235, margin=16):
        self._size = int(size)
        self._alpha = int(alpha)
        self._margin = int(margin)
        self._hwnd = None
        self._mem = None
        self._bmp = None
        self._old_bmp = None
        self._pens = {}
        self._brush = None
        self._dead = not _WIN32

    # --- lifecycle -----------------------------------------------------------------------
    def _ensure(self):
        if self._hwnd:
            return True
        if self._dead or not _ensure_class():
            self._dead = True
            return False
        sz = self._size
        hwnd = _u32.CreateWindowExW(_EX_STYLE, _CLASS_NAME, "", _WS_POPUP, 0, 0, sz, sz,
                                    None, None, _k32.GetModuleHandleW(None), None)
        if not hwnd:
            self._dead = True
            return False
        _u32.SetLayeredWindowAttributes(hwnd, _KEY_COLOR, self._alpha, _LWA_KEY_ALPHA)
        hdc = _u32.GetDC(hwnd)
        self._mem = _g32.CreateCompatibleDC(hdc)
        self._bmp = _g32.CreateCompatibleBitmap(hdc, sz, sz)
        self._old_bmp = _g32.SelectObject(self._mem, self._bmp)
        _u32.ReleaseDC(hwnd, hdc)
        self._brush = _g32.CreateSolidBrush(_KEY_COLOR)
        for kind, colour in _COLORS.items():
            self._pens[kind] = _g32.CreatePen(0, 2, colour)    # PS_SOLID, 2 px
        self._hwnd = hwnd
        return True

    def show(self, target_hwnd=0):
        """Centre the overlay on ``target_hwnd`` (e.g. ``acad.HWND``; 0 -> the foreground window)
        and show it -- topmost, click-through, without activating it."""
        if not self._ensure():
            return
        rect = wt.RECT()
        tgt = target_hwnd or _u32.GetForegroundWindow()
        if not (tgt and _u32.GetWindowRect(tgt, ctypes.byref(rect))):
            rect = wt.RECT(0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1))
        cx = (rect.left + rect.right - self._size) // 2
        cy = (rect.top + rect.bottom - self._size) // 2
        _u32.SetWindowPos(self._hwnd, _HWND_TOPMOST, cx, cy, self._size, self._size,
                          _SWP_SHOW_NOACT)
        self._pump()

    def update(self, view_dir):
        """Redraw the cube as seen from ``view_dir`` (the driver passes the direction the view WILL
        have once the pending orbit is applied)."""
        if not self._ensure():
            return
        sz = self._size
        rect = wt.RECT(0, 0, sz, sz)
        _u32.FillRect(self._mem, ctypes.byref(rect), self._brush)
        half = sz / 2.0
        scale = half - self._margin
        for x1, y1, x2, y2, kind in project_cube(view_dir):
            _g32.SelectObject(self._mem, self._pens.get(kind) or self._pens["cube"])
            _g32.MoveToEx(self._mem, int(half + x1 * scale), int(half + y1 * scale), None)
            _g32.LineTo(self._mem, int(half + x2 * scale), int(half + y2 * scale))
        hdc = _u32.GetDC(self._hwnd)
        _g32.BitBlt(hdc, 0, 0, sz, sz, self._mem, 0, 0, _SRCCOPY)
        _u32.ReleaseDC(self._hwnd, hdc)
        self._pump()

    def hide(self):
        if self._hwnd:
            _u32.ShowWindow(self._hwnd, _SW_HIDE)
            self._pump()

    def destroy(self):
        if not _WIN32:
            return
        try:
            if self._mem:
                if self._old_bmp:
                    _g32.SelectObject(self._mem, self._old_bmp)
                _g32.DeleteDC(self._mem)
            if self._bmp:
                _g32.DeleteObject(self._bmp)
            for pen in self._pens.values():
                _g32.DeleteObject(pen)
            if self._brush:
                _g32.DeleteObject(self._brush)
            if self._hwnd:
                _u32.DestroyWindow(self._hwnd)
                self._pump()
        finally:
            self._hwnd = None
            self._mem = None
            self._bmp = None
            self._pens = {}
            self._brush = None
            self._dead = True

    # --- plumbing ------------------------------------------------------------------------
    @staticmethod
    def _pump():
        """Drain this thread's message queue (we have no mainloop; a few messages per call keeps the
        window valid)."""
        msg = wt.MSG()
        while _u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))
