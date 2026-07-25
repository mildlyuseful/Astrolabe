# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Foreground publication is independent of BLE motion and remains lifecycle-safe."""

import time
import threading
from types import SimpleNamespace

from trackball_daemon.app import App
from trackball_daemon.runtime_state import (
    FocusedContext,
    RuntimeBaseState,
    RuntimeStore,
)
from trackball_daemon.commands import SerializedCommandQueue
from trackball_daemon.winfocus import ForegroundMonitor


def test_monitor_publishes_changes_and_forced_refresh_without_duplicates():
    current = ["notepad.exe"]
    observed = []
    monitor = ForegroundMonitor(observed.append, query=lambda: current[0])

    monitor.poll_once()
    monitor.poll_once()
    current[0] = "blender.exe"
    monitor.poll_once()
    monitor.refresh()
    monitor.poll_once()

    assert observed == ["notepad.exe", "blender.exe", "blender.exe"]
    assert monitor.current_process == "blender.exe"


def test_monitor_thread_starts_refreshes_and_stops():
    observed = []
    monitor = ForegroundMonitor(observed.append, query=lambda: "fusion.exe", poll_interval=0.02)
    monitor.start()
    deadline = time.monotonic() + 1
    while not observed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert observed == ["fusion.exe"]
    assert monitor.running
    monitor.stop()
    assert not monitor.running


def test_monitor_callback_failures_do_not_stop_later_publication(daemon_caplog):
    current = ["first.exe"]
    calls = []

    def callback(value):
        calls.append(value)
        if value == "first.exe":
            raise RuntimeError("boom")

    monitor = ForegroundMonitor(callback, query=lambda: current[0])
    monitor.poll_once()
    current[0] = "second.exe"
    monitor.poll_once()
    assert calls == ["first.exe", "second.exe"]
    assert "Foreground change callback failed" in daemon_caplog.text


class _Config:
    class _Snapshot:
        app_operational = {"blender": {"enabled": True}}

    def snapshot(self):
        return self._Snapshot()


def test_app_foreground_change_updates_runtime_without_a_ble_packet():
    app = App.__new__(App)
    app.config = _Config()
    app.runtime = RuntimeStore(lambda _context: RuntimeBaseState("pointer"))
    app.commands = SerializedCommandQueue(app.runtime)
    app.onshape_bridge = SimpleNamespace(
        is_connected=lambda: False, is_viewport_focused=lambda: False)
    app.navigation = SimpleNamespace(targets=[], activate=lambda key: app.navigation.targets.append(key))
    app.engine = SimpleNamespace(bound=[], set_active_bindings=lambda key: app.engine.bound.append(key))
    app._engine_app = None
    app.keyboard_provider = SimpleNamespace(
        reconciliations=[],
        reconcile=lambda reason: app.keyboard_provider.reconciliations.append(reason))

    app._on_foreground_process_changed("blender.exe")

    assert app.runtime.snapshot().focused_context == FocusedContext("blender", "blender.exe")
    assert app.navigation.targets == ["blender"]
    assert app.engine.bound == ["blender"]
    assert app.keyboard_provider.reconciliations == ["foreground_change"]

    app._on_foreground_process_changed("notes.exe")
    assert app.runtime.snapshot().focused_context == FocusedContext(None, "notes.exe")
    assert app.navigation.targets[-1] is None
    assert app.keyboard_provider.reconciliations == [
        "foreground_change", "foreground_change"]


def test_onshape_focus_change_forces_stationary_foreground_refresh():
    app = App.__new__(App)
    app.foreground_monitor = SimpleNamespace(
        refreshes=0,
        refresh=lambda: setattr(
            app.foreground_monitor, "refreshes", app.foreground_monitor.refreshes + 1),
    )

    app._on_onshape_focus_changed(True)
    app._on_onshape_focus_changed(False)

    assert app.foreground_monitor.refreshes == 2


def test_onshape_viewport_focus_recomputes_stationary_runtime_context():
    class OnshapeConfig:
        class Snapshot:
            app_operational = {"onshape": {"enabled": True}}

        def snapshot(self):
            return self.Snapshot()

    app = App.__new__(App)
    app.config = OnshapeConfig()
    app.runtime = RuntimeStore(lambda _context: RuntimeBaseState("pointer"))
    app.commands = SerializedCommandQueue(app.runtime)
    focus = [False]
    app.onshape_bridge = SimpleNamespace(
        is_connected=lambda: True, is_viewport_focused=lambda: focus[0])
    app.navigation = SimpleNamespace(targets=[], activate=lambda key: app.navigation.targets.append(key))
    app.engine = SimpleNamespace(bound=[], set_active_bindings=lambda key: app.engine.bound.append(key))
    app._engine_app = None
    app.keyboard_provider = None
    app._apps_lock = threading.Lock()
    app._broker_apps = []
    app._sw_apps = []
    app._onshape_apps = []
    app._onshape_connection_version = "1.4.8"
    app.connected_apps = []
    app.observed_addin_versions = {}
    app.foreground_monitor = ForegroundMonitor(
        app._on_foreground_process_changed, query=lambda: "chrome.exe")

    app.foreground_monitor.poll_once()
    assert app.runtime.snapshot().focused_context == FocusedContext(None, "chrome.exe")

    focus[0] = True
    app._on_onshape_focus_changed(True)
    app.foreground_monitor.poll_once()
    assert app.runtime.snapshot().focused_context == FocusedContext("onshape", "chrome.exe")
    assert app.navigation.targets[-1] == "onshape"
    assert app.connected_apps == [("onshape", "1.4.8", 0)]

    focus[0] = False
    app._on_onshape_focus_changed(False)
    app.foreground_monitor.poll_once()
    assert app.runtime.snapshot().focused_context == FocusedContext(None, "chrome.exe")
    assert app.navigation.targets[-1] is None
    assert app.connected_apps == []


def test_app_shutdown_releases_inputs_before_stopping_focus_and_transports():
    order = []
    app = App.__new__(App)
    app.stop_event = threading.Event()
    app.input_aggregator = SimpleNamespace(
        shutdown=lambda reason: order.append(("inputs", reason)))
    app.foreground_monitor = SimpleNamespace(stop=lambda: order.append(("foreground", None)))
    app.broker = app.sw_driver = app.onshape_bridge = app.acad_loader = None
    app.tray = app.root = None

    app.quit()

    assert app.stop_event.is_set()
    assert order == [("inputs", "daemon_shutdown"), ("foreground", None)]
