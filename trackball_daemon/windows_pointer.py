"""Bounded Windows pointer output primitives used by motion and button bindings."""

import ctypes
import sys
import threading


_WIN = sys.platform == "win32"
POINTER_BUTTONS = ("left", "right", "middle", "x1", "x2")

if _WIN:
    from ctypes import wintypes

    INPUT_MOUSE = 0
    WHEEL_DELTA = 120
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_XDOWN = 0x0080
    MOUSEEVENTF_XUP = 0x0100
    MOUSEEVENTF_WHEEL = 0x0800
    XBUTTON1 = 0x0001
    XBUTTON2 = 0x0002

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", _MOUSEINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _SendInput = ctypes.windll.user32.SendInput

    def _send(flags, *, dx=0, dy=0, data=0):
        mi = _MOUSEINPUT(int(dx), int(dy), int(data) & 0xFFFFFFFF, int(flags), 0, None)
        value = _INPUT(INPUT_MOUSE)
        value.mi = mi
        sent = _SendInput(1, ctypes.byref(value), ctypes.sizeof(_INPUT))
        return sent == 1

else:
    _warned = [False]

    def _send(_flags, *, dx=0, dy=0, data=0):
        del dx, dy, data
        if not _warned[0]:
            print("[WARN] pointer output needs Windows SendInput; injection disabled")
            _warned[0] = True
        return True


def send_mouse(dx=0, dy=0, wheel=0):
    flags = 0
    if dx or dy:
        flags |= MOUSEEVENTF_MOVE if _WIN else 0x0001
    if wheel:
        flags |= MOUSEEVENTF_WHEEL if _WIN else 0x0800
    if flags:
        # Motion output historically ignored a rejected SendInput packet. Preserve that behavior so
        # a transient desktop boundary cannot tear down the BLE stream.
        _send(flags, dx=dx, dy=dy, data=int(wheel) * 120)


_BUTTON_FLAGS = {
    "left": (0x0002, 0x0004, 0),
    "right": (0x0008, 0x0010, 0),
    "middle": (0x0020, 0x0040, 0),
    "x1": (0x0080, 0x0100, 1),
    "x2": (0x0080, 0x0100, 2),
}


def send_pointer_button(button, pressed):
    """Inject only one allowlisted pointer button edge; no key/scancode surface exists."""
    try:
        down, up, data = _BUTTON_FLAGS[button]
    except KeyError as exc:
        raise ValueError(f"unsupported pointer button: {button!r}") from exc
    if not _send(down if pressed else up, data=data):
        raise OSError("Windows SendInput rejected a pointer-button event")


class SendInputPointerButtonSink:
    """Identity-aware momentary output sink used by the binding controller."""

    def __init__(self, injector=send_pointer_button):
        self._injector = injector
        self._lock = threading.RLock()
        self._held = {}

    @property
    def held(self):
        with self._lock:
            return dict(self._held)

    def press(self, button, owner):
        if button not in POINTER_BUTTONS:
            raise ValueError(f"unsupported pointer button: {button!r}")
        with self._lock:
            previous = self._held.get(button)
            if previous == owner:
                return
            if previous is not None:
                self._injector(button, False)
            self._injector(button, True)
            self._held[button] = owner

    def release(self, button, owner):
        with self._lock:
            if self._held.get(button) != owner:
                return
            self._injector(button, False)
            self._held.pop(button, None)

    def release_all(self):
        with self._lock:
            for button in tuple(self._held):
                self._injector(button, False)
                self._held.pop(button, None)
