import json
import threading

import pytest

from trackball_daemon.app import App
from trackball_daemon.autocad_driver import AutoCADPluginLoader
from trackball_daemon.navbroker import NavBroker
from trackball_daemon.onshape_bridge import OnshapeBridge
from trackball_daemon.service_health import ServiceHealth, ServiceHealthState
from trackball_daemon.solidworks_driver import SolidWorksDriver


class _HelloConnection:
    def __init__(self, value):
        payload = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        self._chunks = [payload + b"\n", b""]
        self.closed = False

    def settimeout(self, _seconds):
        pass

    def recv(self, _size):
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("hello", "detail"),
    [
        (b"{", "not valid JSON"),
        ({"type": "motion", "app": "blender", "version": "1", "pid": 1}, "message type"),
        ({"type": "hello", "app": "missing", "version": "1", "pid": 1}, "unsupported app"),
        ({"type": "hello", "app": "blender", "version": "", "pid": 1}, "version"),
        ({"type": "hello", "app": "blender", "version": "1", "pid": True}, "pid"),
    ],
)
def test_broker_rejects_bad_handshakes_and_retains_actionable_health(hello, detail):
    health = []
    changes = []
    broker = NavBroker(
        47999,
        changes.append,
        on_health_changed=health.append,
    )

    connection = _HelloConnection(hello)
    broker._handle_client(connection)

    assert broker.client_infos() == []
    assert changes == []
    assert connection.closed
    assert health[-1].state is ServiceHealthState.DEGRADED
    assert detail in health[-1].detail


def test_valid_broker_handshake_recovers_health_and_publishes_only_supported_client():
    health = []
    changes = []
    broker = NavBroker(
        47999,
        changes.append,
        on_health_changed=health.append,
    )

    broker._handle_client(_HelloConnection({
        "type": "hello",
        "app": "autocad",
        "version": "0.3.20",
        "pid": 42,
    }))

    assert changes == [[("autocad", "0.3.20", 42)], []]
    assert ServiceHealthState.HEALTHY in {item.state for item in health}
    assert health[-1].state is ServiceHealthState.WAITING
    assert "waiting for an add-on handshake" in health[-1].detail


def test_broker_sender_failure_is_visible(monkeypatch):
    health = []
    broker = NavBroker(47999, on_health_changed=health.append)
    monkeypatch.setattr(
        broker, "_next_wait", lambda: (_ for _ in ()).throw(RuntimeError("sender broke")))

    broker._sender()

    assert health[-1].state is ServiceHealthState.FAILED
    assert "sender broke" in health[-1].detail


def test_onshape_worker_failure_is_visible(monkeypatch):
    health = []
    bridge = OnshapeBridge(on_health_changed=health.append)
    monkeypatch.setattr(
        bridge,
        "_run_worker_loop",
        lambda: (_ for _ in ()).throw(RuntimeError("worker broke")),
    )

    bridge._run_worker()

    assert health[-1].state is ServiceHealthState.FAILED
    assert "worker broke" in health[-1].detail


@pytest.mark.parametrize(
    "factory",
    [
        lambda callback: SolidWorksDriver(on_health_changed=callback),
        lambda callback: OnshapeBridge(on_health_changed=callback),
        lambda callback: AutoCADPluginLoader(on_health_changed=callback),
    ],
)
def test_gated_host_workers_expose_disabled_waiting_and_disabled_transitions(factory):
    health = []
    worker = factory(health.append)

    assert health[-1].state is ServiceHealthState.DISABLED
    worker.set_enabled(True)
    assert health[-1].state is ServiceHealthState.WAITING
    assert health[-1].detail
    worker.set_enabled(False)
    assert health[-1].state is ServiceHealthState.DISABLED


def test_app_health_summary_prioritizes_failed_then_degraded_then_waiting():
    app = App.__new__(App)
    app._health_lock = threading.Lock()
    app.service_health = {
        "navigation-broker": ServiceHealth(
            "navigation-broker", ServiceHealthState.WAITING, "waiting"),
        "onshape": ServiceHealth("onshape", ServiceHealthState.DEGRADED, "certificate"),
        "solidworks": ServiceHealth("solidworks", ServiceHealthState.FAILED, "COM"),
        "autocad": ServiceHealth("autocad", ServiceHealthState.DISABLED, "disabled"),
    }

    assert app.runtime_health_summary() == "failed: solidworks"
    app.service_health["solidworks"] = ServiceHealth(
        "solidworks", ServiceHealthState.HEALTHY, "connected")
    assert app.runtime_health_summary() == "degraded: onshape"
    app.service_health["onshape"] = ServiceHealth(
        "onshape", ServiceHealthState.DISABLED, "disabled")
    assert app.runtime_health_summary() == "waiting: navigation-broker"
