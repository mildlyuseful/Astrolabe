"""Frozen Phase 4 broker hello/frame contracts and target-isolation acceptance tests."""
import json

import pytest

from trackball_daemon.app_registry import APP_SPECS, TransportKind
from trackball_daemon.navbroker import NavBroker


BROKER_APP_IDS = tuple(
    spec.app_id for spec in APP_SPECS if spec.transport is TransportKind.BROKER)


class _HelloConnection:
    def __init__(self, hello):
        self._chunks = [(json.dumps(hello) + "\n").encode("utf-8"), b""]
        self.closed = False

    def settimeout(self, _seconds):
        pass

    def recv(self, _size):
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("app_id", BROKER_APP_IDS)
def test_every_registered_broker_app_uses_the_existing_hello_identity(app_id):
    changes = []
    broker = NavBroker(0, changes.append)
    conn = _HelloConnection({
        "type": "hello", "app": app_id, "version": "frozen", "host": "host", "pid": 42,
    })
    broker._handle_client(conn)
    assert changes[0] == [(app_id, "frozen", 42)]
    assert changes[-1] == []
    assert conn.closed


def test_broker_frame_wire_shape_is_unchanged_before_target_isolation():
    scheme = {"op": "screen_center", "os": "free", "zm": "to_center", "adv": None}
    assert NavBroker._build_frame([1, 2, 3, 4, 5, 6], scheme) == {
        "o": [1, 2, 3], "p": [4, 5], "z": 6,
        "op": "screen_center", "os": "free", "zm": "to_center",
    }
    scheme["adv"] = {"nav_mode": "fly"}
    assert NavBroker._build_frame([0] * 6, scheme)["adv"] == {"nav_mode": "fly"}

