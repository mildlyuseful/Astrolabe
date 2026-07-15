"""Passive, text-only control-state HUD.

Runtime and config callbacks may arrive on worker threads.  They only publish immutable snapshots
to latest-state-wins mailboxes; every Tk and Win32 window operation remains on the Tk thread.
"""

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import ntpath
import sys
import threading
import time
import tkinter as tk

from .app_registry import APP_SPECS_BY_ID


@dataclass(frozen=True)
class HudOptions:
    visible: bool = True
    always_on_top: bool = True
    click_through: bool = True
    opacity: float = 0.9
    margin: int = 16
    last_binding_timeout: float = 3.0

    @classmethod
    def from_config(cls, snapshot):
        return cls(
            visible=bool(snapshot.global_value("hud.visible")),
            always_on_top=bool(snapshot.global_value("hud.always_on_top")),
            click_through=bool(snapshot.global_value("hud.click_through")),
            opacity=float(snapshot.global_value("hud.opacity")),
            margin=int(snapshot.global_value("hud.margin")),
            last_binding_timeout=float(
                snapshot.global_value("hud.last_binding_timeout")),
        )


@dataclass(frozen=True)
class HudText:
    product: str
    context: str
    help: str
    binding: str

    @property
    def lines(self):
        return self.product, self.context, self.help, self.binding


@dataclass(frozen=True)
class WorkArea:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self):
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("work area must have positive width and height")


@dataclass(frozen=True)
class PlacementState:
    work_area: WorkArea
    foreground_hwnd: int = 0
    monitor_handle: int = 0
    dpi: int = 96


def bottom_right_origin(work_area, width, height, margin):
    """Return a clamped bottom-right origin in one monitor work area."""
    width = max(1, int(width))
    height = max(1, int(height))
    margin = max(0, int(margin))
    return (
        max(work_area.left, work_area.right - width - margin),
        max(work_area.top, work_area.bottom - height - margin),
    )


class LatestSnapshotMailbox:
    """A bounded, thread-safe mailbox whose capacity is exactly one snapshot."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest = None

    def publish(self, snapshot):
        revision = getattr(snapshot, "revision", None)
        if type(revision) is not int:
            raise TypeError("coalesced snapshots require an integer revision")
        with self._lock:
            if self._latest is None or revision >= self._latest.revision:
                self._latest = snapshot

    def take(self):
        with self._lock:
            snapshot = self._latest
            self._latest = None
            return snapshot

    @property
    def pending_count(self):
        with self._lock:
            return int(self._latest is not None)


def _foreground_label(context):
    spec = APP_SPECS_BY_ID.get(context.app_id)
    if spec is not None:
        return spec.display_name
    executable = ntpath.basename(context.executable or "")
    return executable or "Desktop"


def project_hud_text(snapshot, binding_line="Binding: —"):
    """Pure projection from one runtime snapshot and one already-resolved binding line."""
    mode = "3D" if snapshot.effective_input_mode == "3d" else "Pointer"
    if snapshot.effective_input_mode == "3d":
        nav = snapshot.effective_navigation_mode.title()
        layer = ("Primary" if snapshot.effective_navigation_layer == "primary"
                 else snapshot.control_help.state_label)
        context = f"{_foreground_label(snapshot.focused_context)} · {nav} · {layer}"
    else:
        context = f"{_foreground_label(snapshot.focused_context)} · Pointer"
    return HudText(
        product=f"Astrolabe · {mode}",
        context=context,
        help=snapshot.control_help.current_help,
        binding=binding_line,
    )


class HudTextProjector:
    """Resolve held-versus-last precedence while keeping time outside runtime state."""

    def __init__(self):
        self._last_event = None
        self._last_event_at = None

    def project(self, snapshot, *, now=None, timeout=3.0):
        now = time.monotonic() if now is None else float(now)
        event = snapshot.last_binding_event
        if event != self._last_event:
            self._last_event = event
            self._last_event_at = now if event is not None else None
        if snapshot.held_bindings:
            labels = " · ".join(binding.label for binding in snapshot.held_bindings)
            binding_line = f"Held: {labels}"
        elif (event is not None and self._last_event_at is not None and
              now - self._last_event_at <= max(0.0, float(timeout))):
            binding_line = f"Last: {event.label or event.binding_id}"
        else:
            binding_line = "Binding: —"
        return project_hud_text(snapshot, binding_line)


class _POINT(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class _RECT(ctypes.Structure):
    _fields_ = (("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG))


class _MONITORINFO(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD))


def _user32():
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = (_POINT, wintypes.DWORD)
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetCursorPos.argtypes = (ctypes.POINTER(_POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO))
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetParent.argtypes = (wintypes.HWND,)
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    user32.SetWindowPos.argtypes = (
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if get_dpi is not None:
        get_dpi.argtypes = (wintypes.HWND,)
        get_dpi.restype = wintypes.UINT
    return user32


def windows_foreground_placement():
    """Return foreground-monitor placement, falling back cursor then primary monitor."""
    if sys.platform != "win32":
        return None
    user32 = _user32()
    hwnd = int(user32.GetForegroundWindow() or 0)
    monitor = user32.MonitorFromWindow(hwnd, 0) if hwnd else 0
    if not monitor:
        point = _POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            monitor = user32.MonitorFromPoint(point, 0)
    if not monitor:
        monitor = user32.MonitorFromPoint(_POINT(0, 0), 1)
    if not monitor:
        return None
    info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    dpi = 96
    get_dpi = getattr(user32, "GetDpiForWindow", None)
    if hwnd and get_dpi is not None:
        dpi = int(get_dpi(hwnd) or 96)
    work = info.rcWork
    return PlacementState(
        WorkArea(work.left, work.top, work.right, work.bottom),
        foreground_hwnd=hwnd, monitor_handle=int(monitor), dpi=dpi)


def _native_hwnd(window):
    if sys.platform != "win32":
        return 0
    user32 = _user32()
    child = int(window.winfo_id())
    parent = int(user32.GetParent(child) or 0)
    return parent or child


def _apply_native_styles(window, options):
    if sys.platform != "win32":
        return
    user32 = _user32()
    hwnd = _native_hwnd(window)
    if not hwnd:
        return
    get_long = user32.GetWindowLongW
    set_long = user32.SetWindowLongW
    style = int(get_long(hwnd, -20))
    style |= 0x00000080 | 0x08000000  # WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if options.click_through:
        style |= 0x00000020            # WS_EX_TRANSPARENT
    else:
        style &= ~0x00000020
    set_long(hwnd, -20, style)
    insert_after = wintypes.HWND(
        -1 if options.always_on_top else -2)  # TOPMOST / NOTOPMOST
    user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                        0x0001 | 0x0002 | 0x0010 | 0x0020)


def _show_without_activation(window):
    # WS_EX_NOACTIVATE is applied before this call.  Let Tk keep its mapped-state bookkeeping,
    # then repeat the native show mode defensively so no ordinary activation path is used.
    window.deiconify()
    if sys.platform == "win32":
        _user32().ShowWindow(_native_hwnd(window), 4)  # SW_SHOWNOACTIVATE


def _hide_window(window):
    if sys.platform == "win32":
        _user32().ShowWindow(_native_hwnd(window), 0)
    window.withdraw()


def _move_window(window, x, y, width, height):
    if sys.platform == "win32":
        _user32().SetWindowPos(
            _native_hwnd(window), wintypes.HWND(0), int(x), int(y),
            int(width), int(height), 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
    else:  # pragma: no cover - Windows is the supported runtime
        window.geometry(f"{width}x{height}+{x}+{y}")


class ControlHUD:
    """Tk owner for the non-activating text control panel."""

    POLL_MS = 50
    PLACEMENT_MS = 250

    def __init__(self, root, runtime, config):
        self.root = root
        self.runtime = runtime
        self.config = config
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(background="#20242b", padx=12, pady=10)
        self._variables = [tk.StringVar(self.window) for _ in range(4)]
        for index, variable in enumerate(self._variables):
            tk.Label(
                self.window, textvariable=variable, anchor="w", justify="left",
                background="#20242b", foreground="#f2f3f5" if index < 2 else "#c8ccd2",
                font=("Segoe UI", 10, "bold" if index == 0 else "normal"),
            ).pack(fill="x", pady=(0, 3 if index < 3 else 0))
        self._runtime_mailbox = LatestSnapshotMailbox()
        self._config_mailbox = LatestSnapshotMailbox()
        self._projector = HudTextProjector()
        self._snapshot = None
        self._options = HudOptions()
        self._visible = False
        self._placement = None
        self._after_id = None
        self._stopped = False
        self.window.update_idletasks()
        self.window.bind("<Map>", lambda _event: _apply_native_styles(
            self.window, self._options))
        self.runtime.add_listener(self._on_runtime_event)
        self.config.add_listener(self._on_config_event)
        self._runtime_mailbox.publish(self.runtime.snapshot())
        self._config_mailbox.publish(self.config.snapshot())
        self._after_id = self.root.after(0, self._drain)

    def _on_runtime_event(self, event):
        self._runtime_mailbox.publish(event.snapshot)

    def _on_config_event(self, event):
        self._config_mailbox.publish(event.snapshot)

    def _fallback_placement(self):
        return PlacementState(WorkArea(
            0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()))

    def _reposition(self, force=False):
        if not self._visible:
            return
        placement = windows_foreground_placement() or self._fallback_placement()
        if not force and placement == self._placement:
            return
        self._placement = placement
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        x, y = bottom_right_origin(
            placement.work_area, width, height, self._options.margin)
        _move_window(self.window, x, y, width, height)

    def _apply_options(self, options):
        changed = options != self._options
        self._options = options
        try:
            self.window.attributes("-alpha", options.opacity)
        except tk.TclError:  # pragma: no cover - old/non-Windows Tk
            pass
        _apply_native_styles(self.window, options)
        if options.visible and not self._visible:
            self._visible = True
            self._reposition(force=True)
            _show_without_activation(self.window)
        elif not options.visible and self._visible:
            _hide_window(self.window)
            self._visible = False
        elif changed:
            self._reposition(force=True)

    def _render(self):
        if self._snapshot is None:
            return
        text = self._projector.project(
            self._snapshot, timeout=self._options.last_binding_timeout)
        previous = tuple(variable.get() for variable in self._variables)
        if text.lines != previous:
            for variable, line in zip(self._variables, text.lines):
                variable.set(line)
            self._reposition(force=True)

    def _drain(self):
        if self._stopped:
            return
        config_snapshot = self._config_mailbox.take()
        if config_snapshot is not None:
            self._apply_options(HudOptions.from_config(config_snapshot))
        runtime_snapshot = self._runtime_mailbox.take()
        if runtime_snapshot is not None:
            self._snapshot = runtime_snapshot
        self._render()
        self._reposition()
        self._after_id = self.root.after(self.POLL_MS, self._drain)

    def stop(self):
        """Detach subscribers and destroy the window; must run on the Tk thread."""
        if self._stopped:
            return
        self._stopped = True
        self.runtime.remove_listener(self._on_runtime_event)
        self.config.remove_listener(self._on_config_event)
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass
