"""Lazy Windows Raw Input keyboard provider with fail-safe lifecycle release.

The native window callback only copies a compact keyboard packet into a bounded queue. Control
normalization, pressed-set changes, reconciliation, and downstream work happen on a worker thread.
Raw Input is registered without ``RIDEV_NOLEGACY`` so keys continue to reach the foreground app.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import queue
import sys
import threading
import time

from .model import (
    InputControlDescriptor,
    InputEvent,
    InputPhase,
    InputProvider,
    ProviderHealth,
    ProviderStatus,
)


SOURCE_ID = "keyboard"

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMEAUTOMATIC = 0x0012
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
RIM_TYPEKEYBOARD = 1
RIM_INPUTSINK = 1
RID_INPUT = 0x10000003
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RI_KEY_BREAK = 0x0001
RI_KEY_E0 = 0x0002
RI_KEY_E1 = 0x0004

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5


_NAMED_VKS = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x13: "pause",
    0x14: "caps_lock", 0x1B: "escape", 0x20: "space", 0x21: "page_up",
    0x22: "page_down", 0x23: "end", 0x24: "home", 0x25: "arrow.left",
    0x26: "arrow.up", 0x27: "arrow.right", 0x28: "arrow.down",
    0x2C: "print_screen", 0x2D: "insert", 0x2E: "delete",
    0x5B: "meta.left", 0x5C: "meta.right", 0x5D: "context_menu",
    0x6A: "numpad.multiply", 0x6B: "numpad.add", 0x6C: "numpad.separator",
    0x6D: "numpad.subtract", 0x6E: "numpad.decimal", 0x6F: "numpad.divide",
    0x90: "num_lock", 0x91: "scroll_lock",
    VK_LSHIFT: "shift.left", VK_RSHIFT: "shift.right",
    VK_LCONTROL: "ctrl.left", VK_RCONTROL: "ctrl.right",
    VK_LMENU: "alt.left", VK_RMENU: "alt.right",
}
for _vk in range(ord("0"), ord("9") + 1):
    _NAMED_VKS[_vk] = chr(_vk).lower()
for _vk in range(ord("A"), ord("Z") + 1):
    _NAMED_VKS[_vk] = chr(_vk).lower()
for _index, _vk in enumerate(range(0x60, 0x6A)):
    _NAMED_VKS[_vk] = f"numpad.{_index}"
for _index, _vk in enumerate(range(0x70, 0x88), 1):
    _NAMED_VKS[_vk] = f"f{_index}"

_GENERIC_MODIFIERS = {
    "shift": ("shift.left", "shift.right"),
    "ctrl": ("ctrl.left", "ctrl.right"),
    "alt": ("alt.left", "alt.right"),
    "meta": ("meta.left", "meta.right"),
}


def _control_for_vk(vk):
    if vk in _NAMED_VKS:
        return _NAMED_VKS[vk]
    if 0x01 <= vk <= 0xFE:
        return f"vk.{vk:02x}"
    return None


def _vk_for_control(control_id):
    if control_id == "numpad.enter":
        return VK_RETURN
    for vk, candidate in _NAMED_VKS.items():
        if candidate == control_id:
            return vk
    if control_id.startswith("vk."):
        try:
            vk = int(control_id[3:], 16)
        except ValueError:
            return None
        return vk if 0x01 <= vk <= 0xFE else None
    return None


def expand_required_controls(control_ids):
    expanded = set()
    for control_id in control_ids:
        if not isinstance(control_id, str):
            raise ValueError("keyboard control IDs must be strings")
        control_id = control_id.strip().lower()
        if control_id in _GENERIC_MODIFIERS:
            expanded.update(_GENERIC_MODIFIERS[control_id])
        elif _vk_for_control(control_id) is not None:
            expanded.add(control_id)
        else:
            raise ValueError(f"unknown keyboard control: {control_id}")
    return frozenset(expanded)


def keyboard_control_descriptors():
    descriptors = []
    for control_id in sorted(set(_NAMED_VKS.values()) | {"numpad.enter"}):
        aliases = tuple(
            generic for generic, physical in _GENERIC_MODIFIERS.items()
            if control_id in physical)
        vk = _vk_for_control(control_id)
        descriptors.append(InputControlDescriptor(
            SOURCE_ID, control_id, control_id.replace(".", " ").replace("_", " ").title(),
            "key", aliases=aliases, metadata={"virtual_key": vk}))
    return tuple(descriptors)


KEYBOARD_CONTROLS = keyboard_control_descriptors()


@dataclass(frozen=True)
class RawKeyboardPacket:
    virtual_key: int
    scan_code: int
    flags: int
    message: int
    extra_information: int
    input_sink: bool
    timestamp: float


def normalize_raw_keyboard(packet):
    """Return ``(control_id, phase)`` for one compact RAWKEYBOARD packet."""
    if not isinstance(packet, RawKeyboardPacket):
        raise TypeError("expected RawKeyboardPacket")
    vk = packet.virtual_key
    if vk in (0, 0xFF):
        return None
    if vk == VK_SHIFT:
        vk = VK_RSHIFT if packet.scan_code == 0x36 else VK_LSHIFT
    elif vk == VK_CONTROL:
        vk = VK_RCONTROL if packet.flags & RI_KEY_E0 else VK_LCONTROL
    elif vk == VK_MENU:
        vk = VK_RMENU if packet.flags & RI_KEY_E0 else VK_LMENU
    control_id = _control_for_vk(vk)
    if control_id is None:
        return None
    if vk == VK_RETURN and packet.flags & RI_KEY_E0:
        control_id = "numpad.enter"
    phase = InputPhase.RELEASED if packet.flags & RI_KEY_BREAK else InputPhase.PRESSED
    return control_id, phase


WINDOWS_AVAILABLE = sys.platform == "win32"


if WINDOWS_AVAILABLE:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    HBRUSH = wintypes.HANDLE
    HCURSOR = wintypes.HANDLE
    HICON = wintypes.HANDLE
    WNDPROC = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", HICON),
            ("hCursor", HCURSOR), ("hbrBackground", HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [
            ("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
            ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND),
        ]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [
            ("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
            ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM),
        ]

    class RAWKEYBOARD(ctypes.Structure):
        _fields_ = [
            ("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
            ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
            ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG),
        ]

    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.RegisterRawInputDevices.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
    _user32.RegisterRawInputDevices.restype = wintypes.BOOL
    _user32.GetRawInputData.argtypes = [
        wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT), wintypes.UINT]
    _user32.GetRawInputData.restype = wintypes.UINT
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _user32.OpenInputDesktop.restype = wintypes.HANDLE
    _user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    _wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    _wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
    _wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]


def _win_error(operation):
    return ctypes.WinError(ctypes.get_last_error(), operation)


def async_key_down(virtual_key):
    return bool(WINDOWS_AVAILABLE and (_user32.GetAsyncKeyState(virtual_key) & 0x8000))


def input_desktop_accessible():
    if not WINDOWS_AVAILABLE:
        return False
    desktop = _user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not desktop:
        return False
    _user32.CloseDesktop(desktop)
    return True


class WindowsRawInputReceiver:
    """Native message-only receiver. Callbacks must remain enqueue-only."""

    def __init__(self, on_packet, on_lifecycle, on_failure):
        if not WINDOWS_AVAILABLE:
            raise OSError("Windows Raw Input is unavailable on this platform")
        self._on_packet = on_packet
        self._on_lifecycle = on_lifecycle
        self._on_failure = on_failure
        self._ready = threading.Event()
        self._error = None
        self._hwnd = None
        self._raw_registered = False
        self._wts_registered = False
        self._class_name = f"AstrolabeRawInput_{os.getpid()}_{id(self)}"
        self._wndproc = WNDPROC(self._window_proc)
        self._thread = threading.Thread(
            target=self._run, name="astrolabe-raw-input-window", daemon=True)

    @property
    def running(self):
        return bool(self._hwnd and self._thread.is_alive())

    def start(self):
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Raw Input receiver did not become ready")
        if self._error is not None:
            raise self._error

    def stop(self):
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._thread.join(5)
        if self._thread.is_alive():
            raise RuntimeError("Raw Input receiver did not stop")

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_INPUT:
                self._read_keyboard(lparam)
            elif message == WM_WTSSESSION_CHANGE:
                if int(wparam) == WTS_SESSION_LOCK:
                    self._on_lifecycle("session_lock")
                elif int(wparam) == WTS_SESSION_UNLOCK:
                    self._on_lifecycle("session_unlock")
            elif message == WM_POWERBROADCAST:
                if int(wparam) == PBT_APMSUSPEND:
                    self._on_lifecycle("suspend")
                elif int(wparam) == PBT_APMRESUMEAUTOMATIC:
                    self._on_lifecycle("resume")
            elif message == WM_CLOSE:
                self._unregister(hwnd)
                _user32.DestroyWindow(hwnd)
                return 0
            elif message == WM_DESTROY:
                self._hwnd = None
                _user32.PostQuitMessage(0)
                return 0
        except BaseException as exc:
            self._on_failure(exc)
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _unregister(self, hwnd):
        if self._raw_registered:
            removal = RAWINPUTDEVICE(0x01, 0x06, RIDEV_REMOVE, None)
            _user32.RegisterRawInputDevices(
                ctypes.byref(removal), 1, ctypes.sizeof(RAWINPUTDEVICE))
            self._raw_registered = False
        if self._wts_registered:
            _wtsapi32.WTSUnRegisterSessionNotification(hwnd)
            self._wts_registered = False

    def _read_keyboard(self, lparam):
        size = wintypes.UINT()
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        result = _user32.GetRawInputData(
            wintypes.HANDLE(lparam), RID_INPUT, None, ctypes.byref(size), header_size)
        if result != 0 or size.value < header_size + ctypes.sizeof(RAWKEYBOARD):
            return
        buffer = ctypes.create_string_buffer(size.value)
        result = _user32.GetRawInputData(
            wintypes.HANDLE(lparam), RID_INPUT, buffer, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF:
            return
        header = RAWINPUTHEADER.from_buffer_copy(buffer.raw[:header_size])
        if header.dwType != RIM_TYPEKEYBOARD:
            return
        keyboard = RAWKEYBOARD.from_buffer_copy(
            buffer.raw[header_size:header_size + ctypes.sizeof(RAWKEYBOARD)])
        self._on_packet(RawKeyboardPacket(
            int(keyboard.VKey), int(keyboard.MakeCode), int(keyboard.Flags),
            int(keyboard.Message), int(keyboard.ExtraInformation),
            int(header.wParam) == RIM_INPUTSINK, time.monotonic()))

    def _run(self):
        hinstance = _kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = hinstance
        window_class.lpszClassName = self._class_name
        registered = False
        try:
            if not _user32.RegisterClassW(ctypes.byref(window_class)):
                raise _win_error("RegisterClassW")
            registered = True
            self._hwnd = _user32.CreateWindowExW(
                0, self._class_name, self._class_name, 0,
                0, 0, 0, 0, wintypes.HWND(-3), None, hinstance, None)
            if not self._hwnd:
                raise _win_error("CreateWindowExW")
            device = RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self._hwnd)
            if not _user32.RegisterRawInputDevices(
                    ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                raise _win_error("RegisterRawInputDevices")
            self._raw_registered = True
            if not _wtsapi32.WTSRegisterSessionNotification(self._hwnd, 0):
                raise _win_error("WTSRegisterSessionNotification")
            self._wts_registered = True
            self._ready.set()
            message = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            self._on_failure(exc)
        finally:
            hwnd = self._hwnd
            if hwnd:
                self._unregister(hwnd)
                _user32.DestroyWindow(hwnd)
                self._hwnd = None
            if registered:
                _user32.UnregisterClassW(self._class_name, hinstance)


class WindowsRawInputProvider(InputProvider):
    """Normalize configured Raw Input controls and own their physical pressed state."""

    def __init__(self, publish_events, publish_health, *, native_factory=None,
                 key_state=None, desktop_accessible=None, queue_capacity=512,
                 ignored_extra_information=(), held_release_poll_interval=0.1):
        if not callable(publish_events) or not callable(publish_health):
            raise TypeError("Raw Input provider callbacks must be callable")
        self._publish_events = publish_events
        self._publish_health_callback = publish_health
        self._native_factory = native_factory or WindowsRawInputReceiver
        self._key_state = key_state or async_key_down
        self._desktop_accessible = desktop_accessible or input_desktop_accessible
        self._queue_capacity = max(8, int(queue_capacity))
        self._ignored_extra = frozenset(int(value) for value in ignored_extra_information)
        self._held_release_poll_interval = (
            None if held_release_poll_interval is None
            else max(0.05, float(held_release_poll_interval)))
        self._next_held_release_poll = 0.0
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._required_requested = frozenset()
        self._required = frozenset()
        self._pressed = set()
        self._suspended = False
        self._generation = 0
        self._health = ProviderHealth(SOURCE_ID, ProviderStatus.DISABLED)
        self._queue = None
        self._overflow = threading.Event()
        self._worker_stop = threading.Event()
        self._worker = None
        self._native = None

    @property
    def source_id(self):
        return SOURCE_ID

    @property
    def controls(self):
        return KEYBOARD_CONTROLS

    @property
    def health(self):
        with self._lock:
            return self._health

    @property
    def required_controls(self):
        with self._lock:
            return self._required_requested

    def _set_health(self, status, detail=""):
        health = ProviderHealth(
            SOURCE_ID, status, detail, generation=self._generation)
        with self._lock:
            self._health = health
        self._publish_health_callback(health)

    def configure(self, required_control_ids):
        requested = frozenset(str(item).strip().lower() for item in required_control_ids)
        expanded = expand_required_controls(requested)
        with self._lifecycle_lock:
            with self._lock:
                if requested == self._required_requested:
                    return self.health
                was_running = self._native is not None
            self._release_pressed("profile_reload")
            with self._lock:
                self._required_requested = requested
                self._required = expanded
            if not expanded:
                self._stop_runtime(ProviderStatus.DISABLED, "no configured keyboard controls")
                return self.health
            if not was_running:
                self._start_runtime()
            self.reconcile("profile_reload")
            return self.health

    def _start_runtime(self):
        if not WINDOWS_AVAILABLE and self._native_factory is WindowsRawInputReceiver:
            self._set_health(ProviderStatus.FAILED, "Raw Input requires Windows")
            return
        self._generation += 1
        self._set_health(ProviderStatus.STARTING, "registering Raw Input")
        self._queue = queue.Queue(self._queue_capacity)
        self._overflow.clear()
        self._worker_stop.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="astrolabe-keyboard-events", daemon=True)
        self._worker.start()
        try:
            self._native = self._native_factory(
                self._enqueue_packet, self._enqueue_lifecycle, self._enqueue_failure)
            self._native.start()
        except BaseException as exc:
            self._native = None
            self._set_health(ProviderStatus.FAILED, f"Raw Input startup failed: {exc}")
            self._worker_stop.set()
            self._queue.put_nowait(None)
            return
        self._suspended = False
        self._set_health(ProviderStatus.RUNNING, "Raw Input registered")

    def _stop_runtime(self, status, detail):
        native = self._native
        self._native = None
        if native is not None:
            try:
                native.stop()
            except Exception as exc:
                detail = f"{detail}; receiver stop failed: {exc}"
                status = ProviderStatus.FAILED
        self._worker_stop.set()
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(5)
            if worker.is_alive():
                status = ProviderStatus.FAILED
                detail = f"{detail}; event worker did not stop"
        self._worker = None
        self._queue = None
        self._set_health(status, detail)

    def stop(self, reason="shutdown"):
        with self._lifecycle_lock:
            self._release_pressed(reason)
            with self._lock:
                self._required_requested = frozenset()
                self._required = frozenset()
            self._stop_runtime(ProviderStatus.STOPPED, reason)

    def restart(self, reason="receiver_restart"):
        with self._lifecycle_lock:
            with self._lock:
                enabled = bool(self._required)
            self._release_pressed(reason)
            self._stop_runtime(ProviderStatus.STOPPED, reason)
            if enabled:
                self._start_runtime()
                self.reconcile(reason)

    def _enqueue(self, item):
        target = self._queue
        if target is None:
            return
        try:
            target.put_nowait(item)
        except queue.Full:
            self._overflow.set()

    def _enqueue_packet(self, packet):
        self._enqueue(("packet", packet))

    def _enqueue_lifecycle(self, event):
        self._enqueue(("lifecycle", str(event)))

    def _enqueue_failure(self, exc):
        self._enqueue(("failure", repr(exc)))

    def _worker_loop(self):
        while not self._worker_stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                item = None
            if self._overflow.is_set():
                self._overflow.clear()
                self._set_health(ProviderStatus.DEGRADED, "input queue overflow")
                self._release_pressed("queue_overflow")
                self.reconcile("queue_overflow")
            if item is None:
                self._poll_held_releases_if_due()
                continue
            kind, value = item
            if kind == "packet":
                self._handle_packet(value)
            elif kind == "lifecycle":
                self._handle_lifecycle(value)
            elif kind == "failure":
                with self._lock:
                    self._suspended = True
                self._release_pressed("receiver_failure")
                native = self._native
                self._native = None
                if native is not None:
                    try:
                        native.stop()
                    except Exception:
                        pass
                self._set_health(ProviderStatus.FAILED, value)

    def _poll_held_releases_if_due(self):
        interval = self._held_release_poll_interval
        if interval is None or self._worker_stop.is_set():
            return
        now = time.monotonic()
        with self._lock:
            held = tuple(sorted(self._pressed))
            should_poll = bool(held and self._native is not None and not self._suspended)
        if not should_poll:
            self._next_held_release_poll = now + interval
            return
        if now < self._next_held_release_poll:
            return
        self._next_held_release_poll = now + interval
        if not self._desktop_accessible():
            with self._lock:
                self._suspended = True
            self._release_pressed("access_ambiguous")
            self._set_health(
                ProviderStatus.SUSPENDED,
                "held_state_poll: input desktop or key state is inaccessible")
            return
        try:
            released = {
                control_id for control_id in held
                if not self._key_state(_vk_for_control(control_id))}
        except Exception as exc:
            with self._lock:
                self._suspended = True
            self._release_pressed("access_ambiguous")
            self._set_health(
                ProviderStatus.SUSPENDED,
                f"held_state_poll: key state failed: {exc}")
            return
        with self._lock:
            released.intersection_update(self._pressed)
            self._pressed.difference_update(released)
        if released:
            self._publish_events(tuple(InputEvent(
                SOURCE_ID, control_id, InputPhase.RELEASED, now,
                metadata={"synthetic": True, "reason": "held_state_poll"})
                for control_id in sorted(released)), "held_state_poll")

    def _handle_packet(self, packet):
        if packet.extra_information in self._ignored_extra:
            return
        normalized = normalize_raw_keyboard(packet)
        if normalized is None:
            return
        control_id, phase = normalized
        with self._lock:
            if self._suspended or control_id not in self._required:
                return
            if phase is InputPhase.PRESSED:
                if control_id in self._pressed:
                    return
                self._pressed.add(control_id)
            else:
                if control_id not in self._pressed:
                    return
                self._pressed.remove(control_id)
        self._publish_events((InputEvent(
            SOURCE_ID, control_id, phase, packet.timestamp,
            metadata={
                "scan_code": packet.scan_code,
                "raw_flags": packet.flags,
                "delivery": "input_sink" if packet.input_sink else "foreground",
            }),), "raw_input")

    def _handle_lifecycle(self, event):
        if event in {"session_lock", "suspend"}:
            with self._lock:
                self._suspended = True
            self._release_pressed(event)
            self._set_health(ProviderStatus.SUSPENDED, event)
        elif event in {"session_unlock", "resume"}:
            with self._lock:
                self._suspended = False
            self.reconcile(event)

    def _release_pressed(self, reason):
        with self._lock:
            controls = tuple(sorted(self._pressed))
            self._pressed.clear()
        if controls:
            now = time.monotonic()
            self._publish_events(tuple(InputEvent(
                SOURCE_ID, control_id, InputPhase.RELEASED, now,
                metadata={"synthetic": True, "reason": reason})
                for control_id in controls), reason)

    def reconcile(self, reason="manual"):
        with self._lock:
            if not self._required or self._native is None:
                return None
            required = tuple(sorted(self._required))
        if not self._desktop_accessible():
            with self._lock:
                self._suspended = True
            self._release_pressed("access_ambiguous")
            self._set_health(
                ProviderStatus.SUSPENDED,
                f"{reason}: input desktop or key state is inaccessible")
            return None
        desired = {
            control_id for control_id in required
            if self._key_state(_vk_for_control(control_id))
        }
        with self._lock:
            current = set(self._pressed)
            self._pressed = set(desired)
            self._suspended = False
        now = time.monotonic()
        events = [InputEvent(
            SOURCE_ID, control_id, InputPhase.RELEASED, now,
            metadata={"synthetic": True, "reason": reason})
            for control_id in sorted(current - desired)]
        events.extend(InputEvent(
            SOURCE_ID, control_id, InputPhase.PRESSED, now,
            metadata={"synthetic": True, "reason": reason})
            for control_id in sorted(desired - current))
        self._set_health(ProviderStatus.RUNNING, f"reconciled: {reason}")
        if events:
            return self._publish_events(tuple(events), reason)
        return None
