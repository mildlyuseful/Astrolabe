# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

import json
import threading
from types import SimpleNamespace

import pytest

from trackball_daemon import app as app_module
from trackball_daemon.app import App


_START_STAGES = (
    "tray",
    "broker",
    "solidworks",
    "onshape",
    "autocad",
    "foreground",
    "auto_update",
    "usb",
    "ble",
    "mainloop",
)


class _Boundary:
    def __init__(self, name, events, fail_at=None):
        self.name = name
        self.events = events
        self.fail_at = fail_at

    def start(self):
        self.events.append(("start", self.name))
        if self.fail_at == self.name:
            raise RuntimeError(f"{self.name} startup failed")

    def stop(self):
        self.events.append(("stop", self.name))


class _Root:
    def __init__(self, events, fail_at=None):
        self.events = events
        self.fail_at = fail_at

    def withdraw(self):
        self.events.append(("ui", "withdraw"))

    def after(self, _delay, callback):
        self.events.append(("ui", "after"))
        if callback.__name__ == "_shutdown" and not self.fail_at:
            callback()

    def mainloop(self):
        self.events.append(("start", "mainloop"))
        if self.fail_at == "mainloop":
            raise RuntimeError("mainloop startup failed")

    def quit(self):
        self.events.append(("ui", "quit"))

    def destroy(self):
        self.events.append(("ui", "destroy"))


def _lifecycle_app(monkeypatch, tmp_path, *, fail_at=None):
    events = []
    app = App.__new__(App)
    app.debug = False
    app.first_run = False
    app.log = SimpleNamespace(
        info=lambda *_a, **_k: None,
        exception=lambda message, *_a, **_k: events.append(("error", message)),
    )
    app.config = SimpleNamespace(snapshot=lambda: SimpleNamespace(bridge_port=47900))
    app.runtime = object()
    app.device_adapters = object()
    app.device_descriptors = ()
    app.ble_input_providers = {}
    app.stop_event = threading.Event()
    app.ble_enabled_event = threading.Event()
    app.ble_enabled_event.set()
    app.transport_handover_lock = threading.RLock()
    app.root = app.ui = app.hud = app.tray = None
    app.broker = _Boundary("broker", events, fail_at)
    app.sw_driver = _Boundary("solidworks", events, fail_at)
    app.onshape_bridge = _Boundary("onshape", events, fail_at)
    app.acad_loader = _Boundary("autocad", events, fail_at)
    app.foreground_monitor = _Boundary("foreground", events, fail_at)
    app.binding_controller = SimpleNamespace(
        release_all=lambda reason: events.append(("release", f"bindings:{reason}"))
    )
    app.pointer_button_output = SimpleNamespace(
        release_all=lambda: events.append(("release", "pointer-buttons"))
    )
    app.input_aggregator = SimpleNamespace(
        shutdown=lambda reason: events.append(("release", f"inputs:{reason}"))
    )
    app._quit_started = False
    app._shutdown_complete = False
    app._apply_service_gates = lambda: events.append(("start", "service-gates"))
    app.get_ble_params = lambda: ()
    app._handle_ble_motion = lambda *_args: None
    app.set_status = lambda *_args: None
    app._poll = lambda: None

    root = _Root(events, fail_at)
    monkeypatch.setattr(app_module.tk, "Tk", lambda: root)
    monkeypatch.setattr(
        app_module,
        "SettingsWindow",
        lambda *_args: events.append(("ui", "settings")) or object(),
    )
    monkeypatch.setattr(
        app_module,
        "ControlHUD",
        lambda *_args: SimpleNamespace(stop=lambda: events.append(("stop", "hud"))),
    )
    monkeypatch.setattr(
        app_module,
        "TrayController",
        lambda *_args: _Boundary("tray", events, fail_at),
    )
    monkeypatch.setattr(app_module, "publish_bridge_port",
                        lambda port: events.append(("publish", port)) or ())
    monkeypatch.setattr(app_module, "migrate_startup_entry", lambda: None)

    def auto_update(_config):
        events.append(("start", "auto_update"))
        if fail_at == "auto_update":
            raise RuntimeError("auto-update startup failed")
        return []

    def start_ble(*_args, **kwargs):
        assert kwargs["enabled_event"] is app.ble_enabled_event
        assert kwargs["handover_lock"] is app.transport_handover_lock
        events.append(("start", "ble"))
        if fail_at == "ble":
            raise RuntimeError("BLE startup failed")

    def start_usb(*_args, **kwargs):
        assert kwargs["enabled_event"] is app.ble_enabled_event
        assert kwargs["handover_lock"] is app.transport_handover_lock
        assert kwargs["external_power_callback"] == app.set_external_power
        events.append(("start", "usb"))
        if fail_at == "usb":
            raise RuntimeError("USB startup failed")

    monkeypatch.setattr(app_module.integrations, "auto_update", auto_update)
    monkeypatch.setattr(app_module, "start_usb_thread", start_usb)
    monkeypatch.setattr(app_module, "start_ble_thread", start_ble)
    return app, events


def test_whole_application_startup_and_shutdown_smoke_uses_every_boundary(
        monkeypatch, tmp_path):
    app, events = _lifecycle_app(monkeypatch, tmp_path)

    app.start()
    # The add-ons cannot find a broker they were never told the port of, so publication has to
    # happen, and it has to happen before the broker starts accepting connections.
    assert events.index(("publish", 47900)) < events.index(("start", "broker"))
    app.quit()

    assert [event for event in events if event[0] == "release"] == [
        ("release", "bindings:daemon_shutdown"),
        ("release", "pointer-buttons"),
        ("release", "inputs:daemon_shutdown"),
    ]
    for name in (
            "tray", "broker", "solidworks", "onshape", "autocad", "foreground", "usb", "ble"):
        assert ("start", name) in events
    for name in ("foreground", "broker", "solidworks", "onshape", "autocad", "tray", "hud"):
        assert ("stop", name) in events
    assert ("ui", "destroy") in events


@pytest.mark.parametrize("fail_at", _START_STAGES)
def test_every_startup_stage_failure_releases_controls_and_stops_all_boundaries(
        monkeypatch, tmp_path, fail_at):
    app, events = _lifecycle_app(monkeypatch, tmp_path, fail_at=fail_at)

    with pytest.raises(RuntimeError, match="startup failed"):
        app.start()

    first_stop = min(index for index, event in enumerate(events) if event[0] == "stop")
    last_release = max(index for index, event in enumerate(events) if event[0] == "release")
    assert last_release < first_stop
    assert app.stop_event.is_set()
    assert ("ui", "destroy") in events
    for name in ("foreground", "broker", "solidworks", "onshape", "autocad", "tray"):
        assert ("stop", name) in events


def test_shutdown_continues_when_one_owner_raises(monkeypatch, tmp_path):
    app, events = _lifecycle_app(monkeypatch, tmp_path)
    app.start()

    def fail_binding_release(_reason):
        events.append(("release", "bindings:failed"))
        raise RuntimeError("release failed")

    app.binding_controller.release_all = fail_binding_release
    app.quit()

    assert ("release", "pointer-buttons") in events
    assert ("release", "inputs:daemon_shutdown") in events
    assert ("stop", "autocad") in events
    assert any(event[0] == "error" for event in events)
