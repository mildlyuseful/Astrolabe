"""Phase 0 diagnostic comparing Windows Raw Input with WH_KEYBOARD_LL.

This is deliberately not daemon runtime code. It validates registration/message-loop plumbing and
records the same keyboard events from both candidate backends. Automated mode emits an otherwise
unused F24 down/repeat/up sequence through SendInput. Because that is synthetic rather than
physical-device evidence, a missing Raw Input F24 event is reported as inconclusive rather than as
a backend failure. Interactive mode is the physical-key acceptance path.

Examples:
    python tools/windows_input_spike.py --automated
    python tools/windows_input_spike.py --interactive --seconds 15
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import queue
import sys
import threading
import time


if sys.platform != "win32":
    raise SystemExit("windows_input_spike.py requires Windows")


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HHOOK = wintypes.HANDLE
HCURSOR = wintypes.HANDLE
HICON = wintypes.HANDLE
HBRUSH = wintypes.HANDLE

WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WH_KEYBOARD_LL = 13
HC_ACTION = 0
RIM_TYPEKEYBOARD = 1
RID_INPUT = 0x10000003
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RI_KEY_BREAK = 0x0001
LLKHF_LOWER_IL_INJECTED = 0x00000002
LLKHF_INJECTED = 0x00000010
KEYEVENTF_KEYUP = 0x0002
VK_F24 = 0x87

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT,
                                           wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _win_error(operation: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), operation)


def _phase(message: int, break_flag: bool = False) -> str:
    return "released" if break_flag or message in (WM_KEYUP, WM_SYSKEYUP) else "pressed"


class RawInputReceiver:
    def __init__(self, events: queue.Queue):
        self.events = events
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.hwnd = None
        self._class_name = f"AstrolabeRawInputSpike_{os.getpid()}_{id(self)}"
        self._wndproc = WNDPROC(self._window_proc)
        self._thread = threading.Thread(target=self._run, name="raw-input-spike", daemon=True)

    def start(self):
        self._thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("Raw Input receiver did not become ready")
        if self.error:
            raise self.error

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self._thread.join(5)
        if self._thread.is_alive():
            raise RuntimeError("Raw Input receiver did not stop")

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_INPUT:
            size = wintypes.UINT()
            header_size = ctypes.sizeof(RAWINPUTHEADER)
            result = user32.GetRawInputData(
                wintypes.HANDLE(lparam), RID_INPUT, None, ctypes.byref(size), header_size)
            if result != 0 or not size.value:
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)
            buffer = ctypes.create_string_buffer(size.value)
            result = user32.GetRawInputData(
                wintypes.HANDLE(lparam), RID_INPUT, buffer, ctypes.byref(size), header_size)
            if result == 0xFFFFFFFF:
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)
            header = RAWINPUTHEADER.from_buffer_copy(buffer.raw[:header_size])
            if header.dwType == RIM_TYPEKEYBOARD:
                offset = header_size
                keyboard = RAWKEYBOARD.from_buffer_copy(
                    buffer.raw[offset:offset + ctypes.sizeof(RAWKEYBOARD)])
                self.events.put({
                    "backend": "raw_input",
                    "phase": _phase(keyboard.Message, bool(keyboard.Flags & RI_KEY_BREAK)),
                    "vk": int(keyboard.VKey),
                    "scan": int(keyboard.MakeCode),
                    "flags": int(keyboard.Flags),
                    "delivery": "input_sink" if int(header.wParam) == 1 else "foreground",
                    "time_ns": time.perf_counter_ns(),
                })
        elif message == WM_CLOSE:
            removal = RAWINPUTDEVICE(0x01, 0x06, RIDEV_REMOVE, None)
            user32.RegisterRawInputDevices(
                ctypes.byref(removal), 1, ctypes.sizeof(RAWINPUTDEVICE))
            user32.DestroyWindow(hwnd)
            return 0
        elif message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _run(self):
        hinstance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = hinstance
        window_class.lpszClassName = self._class_name
        try:
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise _win_error("RegisterClassW")
            hwnd_message = wintypes.HWND(-3)
            self.hwnd = user32.CreateWindowExW(
                0, self._class_name, self._class_name, 0,
                0, 0, 0, 0, hwnd_message, None, hinstance, None)
            if not self.hwnd:
                raise _win_error("CreateWindowExW")
            device = RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self.hwnd)
            if not user32.RegisterRawInputDevices(
                    ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                raise _win_error("RegisterRawInputDevices")
            self.ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:  # diagnostic thread must report startup failures
            self.error = exc
            self.ready.set()
        finally:
            self.hwnd = None
            user32.UnregisterClassW(self._class_name, hinstance)


class LowLevelHookReceiver:
    def __init__(self, events: queue.Queue):
        self.events = events
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.thread_id = 0
        self._hook = None
        self._callback = HOOKPROC(self._hook_proc)
        self._thread = threading.Thread(target=self._run, name="keyboard-hook-spike", daemon=True)

    def start(self):
        self._thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("low-level hook did not become ready")
        if self.error:
            raise self.error

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self._thread.join(5)
        if self._thread.is_alive():
            raise RuntimeError("low-level hook did not stop")

    def _hook_proc(self, code, wparam, lparam):
        if code == HC_ACTION:
            data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            self.events.put({
                "backend": "low_level_hook",
                "phase": _phase(int(wparam)),
                "vk": int(data.vkCode),
                "scan": int(data.scanCode),
                "flags": int(data.flags),
                "injected": bool(data.flags & LLKHF_INJECTED),
                "lower_il_injected": bool(data.flags & LLKHF_LOWER_IL_INJECTED),
                "time_ns": time.perf_counter_ns(),
            })
        return user32.CallNextHookEx(self._hook, code, wparam, lparam)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        try:
            # Low-level hooks execute back on this installing thread rather than being injected
            # into target processes, so a Python callback does not have a DLL module handle.
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback, None, 0)
            if not self._hook:
                raise _win_error("SetWindowsHookExW")
            self.ready.set()
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        except BaseException as exc:
            self.error = exc
            self.ready.set()
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


def _send_key(vk: int, *, released: bool = False):
    marker = 0xA5701ABE
    item = INPUT()
    item.type = 1
    item.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if released else 0, 0, marker)
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise _win_error("SendInput")


def _drain(events: queue.Queue):
    out = []
    while True:
        try:
            out.append(events.get_nowait())
        except queue.Empty:
            return out


def run(automated: bool, seconds: float):
    events = queue.Queue()
    raw = RawInputReceiver(events)
    hook = LowLevelHookReceiver(events)
    raw.start()
    hook.start()
    started = time.perf_counter_ns()
    foreground_before = int(user32.GetForegroundWindow() or 0)
    async_down_seen = None
    async_released_seen = None
    try:
        if automated:
            _send_key(VK_F24)
            time.sleep(0.05)
            async_down_seen = user32.GetAsyncKeyState(VK_F24) < 0
            _send_key(VK_F24)  # deliberate repeat-like second key-down
            _send_key(VK_F24, released=True)
            time.sleep(0.5)
            async_released_seen = user32.GetAsyncKeyState(VK_F24) >= 0
        else:
            print(
                "Press/release Left Ctrl, Right Ctrl, Left Shift, Right Shift, then F24/F12. "
                "Leave this console unfocused if testing background delivery.",
                flush=True,
            )
            time.sleep(max(1.0, seconds))
    finally:
        raw.stop()
        hook.stop()
    foreground_after = int(user32.GetForegroundWindow() or 0)
    captured = _drain(events)
    raw_events = [event for event in captured if event["backend"] == "raw_input"]
    hook_events = [event for event in captured if event["backend"] == "low_level_hook"]
    report = {
        "mode": "automated_sendinput_f24" if automated else "interactive_physical_keys",
        "duration_ms": round((time.perf_counter_ns() - started) / 1_000_000, 3),
        "process_id": os.getpid(),
        "structure_sizes": {
            "RAWINPUTHEADER": ctypes.sizeof(RAWINPUTHEADER),
            "RAWKEYBOARD": ctypes.sizeof(RAWKEYBOARD),
            "RAWINPUTDEVICE": ctypes.sizeof(RAWINPUTDEVICE),
            "KBDLLHOOKSTRUCT": ctypes.sizeof(KBDLLHOOKSTRUCT),
            "INPUT": ctypes.sizeof(INPUT),
        },
        "raw_input_events": raw_events,
        "low_level_hook_events": hook_events,
        "raw_input_registered_and_stopped": True,
        "hook_installed_and_stopped": True,
        "foreground_window_unchanged": foreground_before == foreground_after,
        "get_async_key_state_saw_down": async_down_seen,
        "get_async_key_state_saw_release": async_released_seen,
        "automated_note": (
            "SendInput is not a physical-device acceptance test. Missing Raw Input F24 events are "
            "inconclusive; use --interactive for physical-key evidence."
            if automated else None
        ),
    }
    print(json.dumps(report, indent=2))
    if automated:
        hook_f24 = [event for event in hook_events if event["vk"] == VK_F24]
        if len(hook_f24) < 3:
            raise SystemExit("hook did not observe F24 down/repeat/up")
        if not async_down_seen or not async_released_seen:
            raise SystemExit("GetAsyncKeyState did not reconcile F24 down/released state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--automated", action="store_true", help="inject F24 down/repeat/up")
    mode.add_argument("--interactive", action="store_true", help="capture physical keys for a window")
    parser.add_argument("--seconds", type=float, default=15.0, help="interactive capture duration")
    args = parser.parse_args()
    run(args.automated, args.seconds)


if __name__ == "__main__":
    main()
