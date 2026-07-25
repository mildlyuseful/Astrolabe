# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Frozen Phase 4 broker hello/frame contracts and target-isolation acceptance tests."""
import json
from types import SimpleNamespace

import pytest

from trackball_daemon.app_registry import APP_SPECS, TransportKind
from trackball_daemon.navbroker import NavBroker
from trackball_daemon.navigation_router import NavigationEnvelope, NavigationRouter


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


def test_navigation_envelope_is_immutable_validated_and_targeted():
    orbit = [1, 2, 3]
    envelope = NavigationEnvelope("blender", orbit, [4, 5], 6, 7)
    orbit[0] = 99
    assert envelope.orbit == (1.0, 2.0, 3.0)
    with pytest.raises(ValueError, match="unknown navigation target"):
        NavigationEnvelope("missing", (1, 2, 3), (4, 5), 6, 7)
    with pytest.raises(ValueError, match="finite"):
        NavigationEnvelope("blender", (float("nan"), 2, 3), (4, 5), 6, 7)


class _DeliveryConnection:
    def __init__(self, *, fail=False):
        self.payloads = []
        self.fail = fail

    def sendall(self, payload):
        if self.fail:
            raise OSError("disconnected")
        self.payloads.append(json.loads(payload))


def _attach(broker, app_id, *, fail=False):
    conn = _DeliveryConnection(fail=fail)
    broker._clients.append(SimpleNamespace(
        conn=conn, app=app_id, version="test", pid=len(broker._clients) + 1))
    return conn


def _submit(broker, target, value, *, revision=0):
    return broker.submit(target, value, value + 1, value + 2,
                         value + 3, value + 4, value + 5,
                         state_revision=revision)


def test_broker_delivers_only_to_clients_matching_the_active_target():
    broker = NavBroker(0)
    blender = _attach(broker, "blender")
    blender_second = _attach(broker, "blender")
    fusion = _attach(broker, "fusion360")

    broker.activate_target("blender")
    assert _submit(broker, "blender", 1)
    assert not _submit(broker, "fusion360", 20)
    broker._flush_once(force=True)

    assert blender.payloads == blender_second.payloads
    assert blender.payloads[0]["o"] == [1, 2, 3]
    assert fusion.payloads == []


def test_focus_switch_discards_old_target_motion_instead_of_relabeling_it():
    broker = NavBroker(0)
    blender = _attach(broker, "blender")
    fusion = _attach(broker, "fusion360")
    broker.activate_target("blender")
    _submit(broker, "blender", 1)

    broker.activate_target("fusion360")
    _submit(broker, "fusion360", 20)
    broker._flush_once(force=True)

    assert blender.payloads == []
    assert fusion.payloads[0]["o"] == [20, 21, 22]
    assert broker.delivery_state("blender")["discarded_samples"] == 1


def test_profiles_rates_and_accumulators_are_owned_per_target():
    broker = NavBroker(0)
    blender = _attach(broker, "blender")
    fusion = _attach(broker, "fusion360")
    broker.set_rate("blender", 120)
    broker.set_rate("fusion360", 20)
    broker.set_scheme("blender", "camera", "turntable", "to_cursor",
                      advanced={"nav_mode": "fly"}, profile_revision=4)
    broker.set_scheme("fusion360", "object", "free", "to_center", profile_revision=7)

    assert broker.delivery_state("blender")["period"] == pytest.approx(1 / 120)
    assert broker.delivery_state("fusion360")["period"] == pytest.approx(1 / 20)
    assert broker.delivery_state("blender")["profile_revision"] == 4
    assert broker.delivery_state("fusion360")["profile_revision"] == 7

    broker.activate_target("blender")
    _submit(broker, "blender", 1)
    broker._flush_once(force=True)
    broker.activate_target("fusion360")
    _submit(broker, "fusion360", 10)
    broker._flush_once(force=True)

    assert blender.payloads[0]["op"] == "camera"
    assert blender.payloads[0]["adv"] == {"nav_mode": "fly"}
    assert fusion.payloads[0]["op"] == "object"
    assert "adv" not in fusion.payloads[0]


def test_broker_scheme_state_is_detached_from_callers_and_diagnostics():
    broker = NavBroker(0)
    advanced = {"invert": {"orbit": ["x"]}}
    broker.set_scheme("blender", "camera", "free", "to_center", advanced=advanced)
    advanced["invert"]["orbit"].append("y")
    state = broker.delivery_state("blender")
    assert state["scheme"]["adv"]["invert"]["orbit"] == ["x"]
    state["scheme"]["adv"]["invert"]["orbit"].append("z")
    assert broker.delivery_state("blender")["scheme"]["adv"]["invert"]["orbit"] == ["x"]


def test_profile_change_discards_motion_accumulated_under_the_old_profile():
    broker = NavBroker(0)
    conn = _attach(broker, "blender")
    broker.activate_target("blender")
    _submit(broker, "blender", 1)
    broker.set_scheme("blender", "camera", "free", "to_center", profile_revision=2)
    broker._flush_once(force=True)
    assert conn.payloads == []
    assert broker.delivery_state("blender")["discarded_samples"] == 1


def test_new_runtime_revision_discards_pending_motion_and_stale_revision_is_rejected():
    broker = NavBroker(0)
    conn = _attach(broker, "blender")
    broker.activate_target("blender")
    _submit(broker, "blender", 1, revision=3)
    _submit(broker, "blender", 20, revision=4)
    assert not _submit(broker, "blender", 40, revision=3)
    broker._flush_once(force=True)

    assert conn.payloads[0]["o"] == [20, 21, 22]
    state = broker.delivery_state("blender")
    assert state["accepted_state_revision"] == 4
    assert state["delivery_serial"] == 1


def test_no_matching_client_discards_pending_motion_and_reconnect_starts_clean():
    broker = NavBroker(0)
    broker.activate_target("blender")
    _submit(broker, "blender", 1)
    broker._flush_once(force=True)
    assert broker.delivery_state("blender")["pending"] == (0.0,) * 6

    conn = _attach(broker, "blender")
    broker._flush_once(force=True)
    assert conn.payloads == []
    _submit(broker, "blender", 10)
    broker._flush_once(force=True)
    assert conn.payloads[0]["o"] == [10, 11, 12]


def test_dead_client_is_removed_without_blocking_a_live_matching_client():
    changes = []
    broker = NavBroker(0, changes.append)
    dead = _attach(broker, "blender", fail=True)
    live = _attach(broker, "blender")
    broker.activate_target("blender")
    _submit(broker, "blender", 1)
    broker._flush_once(force=True)

    assert dead.payloads == []
    assert live.payloads
    assert broker.client_infos() == [("blender", "test", 2)]


class _FakeBroker:
    def __init__(self):
        self.active = []
        self.discarded = []
        self.submitted = []
        self.rates = []
        self.schemes = []

    def activate_target(self, target):
        self.active.append(target)

    def discard_pending(self, target):
        self.discarded.append(target)

    def submit(self, target, *values, state_revision):
        self.submitted.append((target, values, state_revision))
        return True

    def set_rate(self, target, hz):
        self.rates.append((target, hz))

    def set_scheme(self, target, *values, **kwargs):
        self.schemes.append((target, values, kwargs))

    def delivery_state(self, target):
        return {"broker_target": target}


class _FakeDirectDriver:
    def __init__(self):
        self.discards = 0
        self.submitted = []
        self.rates = []
        self.schemes = []
        self.pivot_holds = []
        self.zoom_holds = []

    def discard_pending(self):
        self.discards += 1

    def submit(self, *values):
        self.submitted.append(values)

    def set_rate(self, hz):
        self.rates.append(hz)

    def set_scheme(self, *values, **kwargs):
        self.schemes.append((values, kwargs))

    def set_pivot_hold(self, seconds):
        self.pivot_holds.append(seconds)

    def set_zoom_hold(self, seconds):
        self.zoom_holds.append(seconds)


def _envelope(target, value=1, revision=0):
    return NavigationEnvelope(target, (value, value + 1, value + 2),
                              (value + 3, value + 4), value + 5, revision)


def test_router_uses_one_boundary_for_socket_and_direct_transports():
    broker = _FakeBroker()
    solidworks = _FakeDirectDriver()
    onshape = _FakeDirectDriver()
    router = NavigationRouter(broker, solidworks, onshape)

    router.activate("blender")
    assert router.submit(_envelope("blender"))
    router.activate("solidworks")
    assert not router.submit(_envelope("blender", 10))
    assert router.submit(_envelope("solidworks", 20))
    router.activate("onshape")
    assert router.submit(_envelope("onshape", 30))
    router.activate(None)  # connected-background Onshape has no navigation target
    assert not router.submit(_envelope("onshape", 40))

    assert [call[0] for call in broker.submitted] == ["blender"]
    assert solidworks.submitted == [(20, 21, 22, 23, 24, 25)]
    assert onshape.submitted == [(30, 31, 32, 33, 34, 35)]
    assert broker.active == ["blender", None, None, None]
    assert solidworks.discards >= 2
    assert onshape.discards >= 2


def test_router_revisions_apply_equally_to_direct_transports():
    broker = _FakeBroker()
    solidworks = _FakeDirectDriver()
    onshape = _FakeDirectDriver()
    router = NavigationRouter(broker, solidworks, onshape)
    router.activate("solidworks")

    assert router.submit(_envelope("solidworks", 1, revision=5))
    assert not router.submit(_envelope("solidworks", 10, revision=4))
    assert router.submit(_envelope("solidworks", 20, revision=6))
    assert [values[0] for values in solidworks.submitted] == [1, 20]


def test_router_profile_signature_is_detached_from_mutable_config():
    broker = _FakeBroker()
    router = NavigationRouter(broker, _FakeDirectDriver(), _FakeDirectDriver())
    advanced = {"invert": {"orbit": ["x"]}}
    first = router.set_scheme(
        "blender", orbit_pivot="camera", orbit_style="free", zoom_mode="to_center",
        advanced=advanced)
    advanced["invert"]["orbit"].append("y")
    second = router.set_scheme(
        "blender", orbit_pivot="camera", orbit_style="free", zoom_mode="to_center",
        advanced=advanced)
    assert (first, second) == (1, 2)
