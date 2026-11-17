# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""The settings UI's server and API: who may talk to it, and that its actions reach the config."""

import http.client
import json
from types import SimpleNamespace

import pytest

from trackball_daemon.config_store import ConfigStore
from trackball_daemon.input import InputAggregator, load_system_binding_profiles
from trackball_daemon.settings_schema import SETTING_SPECS
from trackball_daemon.webui.api import ACTIONS, UiApi
from trackball_daemon.webui.server import UiServer


@pytest.fixture
def app(tmp_path):
    return SimpleNamespace(
        config=ConfigStore(tmp_path / "config.json").load(),
        binding_catalog=load_system_binding_profiles(),
        input_aggregator=InputAggregator(),
        first_run=True,
    )


@pytest.fixture
def api(app, monkeypatch):
    ui = UiApi(app)
    monkeypatch.setattr(ui, "detect", lambda force=False: None)
    return ui


@pytest.fixture
def server(app):
    ui = UiServer(app)
    ui.start()
    yield ui
    ui.stop()


def _request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response, payload


def _action(server, action, params=None, **headers):
    body = json.dumps({"action": action, "params": params or {}})
    response, payload = _request(server, "POST", "/api/action", body, {
        "Content-Type": "application/json", "X-Astrolabe-Token": server.token, **headers})
    return response.status, json.loads(payload)


# --- gatekeeping ---------------------------------------------------------------------------

def test_the_page_is_public_but_the_api_needs_the_token(server):
    page, body = _request(server, "GET", "/")
    assert page.status == 200 and b"app.js" in body
    assert "script-src 'self'" in page.getheader("Content-Security-Policy")

    denied, _ = _request(server, "GET", "/api/state")
    assert denied.status == 403
    allowed, state = _request(server, "GET", "/api/state",
                              headers={"X-Astrolabe-Token": server.token})
    assert allowed.status == 200 and "schema" in json.loads(state)


def test_a_foreign_host_or_origin_is_refused(server):
    # DNS rebinding: a page on another name resolving to 127.0.0.1 sends its own Host header.
    rebinding, _ = _request(server, "GET", "/api/state", headers={
        "Host": "attacker.example", "X-Astrolabe-Token": server.token})
    assert rebinding.status == 403
    status, reply = _action(server, "settings.resetGlobals", Origin="http://attacker.example")
    assert status == 403 and not reply["ok"]


def test_static_paths_cannot_leave_the_static_directory(server):
    for path in ("/../server.py", "/..%5Cserver.py", "/.hidden", "/static/../api.py"):
        response, _ = _request(server, "GET", path)
        assert response.status == 404, path


def test_a_failing_action_reports_instead_of_raising(server):
    status, reply = _action(server, "settings.set", {"id": "pointer.cursor.gain", "value": "fast"})
    assert status == 200 and reply["ok"] is False and reply["error"]
    status, reply = _action(server, "no.such.action")
    assert reply == {"ok": False, "error": "unknown action: no.such.action"}


# --- projections ---------------------------------------------------------------------------

def test_every_setting_is_described_and_placed(api):
    state = api.state()
    described = {item["id"] for item in state["schema"]["settings"]}
    assert described == {spec.id for spec in SETTING_SPECS}
    assert set(state["global"]) == described
    categories = {item["id"] for item in state["schema"]["categories"]}
    assert {spec.category for spec in SETTING_SPECS} <= categories, \
        "a new category needs a line in settings_ui_model.CATEGORIES"
    json.dumps(state)  # the whole projection is plain JSON


def test_app_settings_are_exactly_the_ones_that_apply(api):
    for app in api.state()["apps"]:
        expected = {item["id"] for item in api.state()["schema"]["settings"]
                    if app["id"] in item["apps"]}
        assert set(app["settings"]) == expected, app["id"]


# --- actions reach the config --------------------------------------------------------------

def test_global_set_and_reset(api, app):
    api.run("settings.set", {"id": "pointer.cursor.gain", "value": 321.0})
    assert app.config.snapshot().global_value("pointer.cursor.gain") == 321.0
    assert api.state()["global"]["pointer.cursor.gain"]["overridden"] is True
    api.run("settings.reset", {"id": "pointer.cursor.gain"})
    assert api.state()["global"]["pointer.cursor.gain"]["overridden"] is False


def test_app_link_unlink_and_reset(api, app):
    setting = "navigation.orbit.sensitivity"
    api.run("settings.set", {"app": "blender", "id": setting, "value": 2.5})
    view = next(a for a in api.state()["apps"] if a["id"] == "blender")["settings"][setting]
    assert view["value"] == 2.5 and view["linked"] is False

    api.run("settings.link", {"app": "blender", "id": setting})
    view = next(a for a in api.state()["apps"] if a["id"] == "blender")["settings"][setting]
    assert view["linked"] is True
    assert app.config.snapshot().app_value("blender", setting) == \
        app.config.snapshot().global_value(setting)


def test_orientation_sources_stay_a_permutation(api, app):
    api.run("settings.set", {"id": "input.axis_orientation.x.source", "value": 1})
    snapshot = app.config.snapshot()
    sources = [snapshot.global_value(f"input.axis_orientation.{axis}.source") for axis in "xyz"]
    assert sorted(sources) == [0, 1, 2] and sources[0] == 1


def test_bindings_round_trip_through_the_simple_form(api):
    bindings = api.state()["bindings"]
    binding_id = bindings["nextId"]
    api.run("bindings.saveSimple", {
        "id": binding_id, "label": "F9 toggles", "enabled": True, "chord": ["keyboard:f9"],
        "match": "exact", "target": "input.toggle", "when": {"apps": ["blender"]}})
    row = next(item for item in api.state()["bindings"]["rows"] if item["row"]["id"] == binding_id)
    assert row["row"]["chord"] == ["keyboard:f9"] and row["row"]["when"] == {"apps": ["blender"]}
    assert row["simple"]["target"] == "input.toggle" and row["modified"]

    api.run("bindings.delete", {"id": binding_id})
    assert all(item["row"]["id"] != binding_id for item in api.state()["bindings"]["rows"])


def test_a_built_in_binding_can_be_changed_and_restored(api):
    api.run("bindings.saveSimple", {
        "id": "astrolabe.up.orbit", "label": "Up: Fly", "enabled": True,
        "chord": ["ble.astrolabe:fiveway.up"], "match": "exact", "target": "navigation.set_fly"})
    row = next(item for item in api.state()["bindings"]["rows"]
               if item["row"]["id"] == "astrolabe.up.orbit")
    assert row["system"] and row["modified"] and row["simple"]["target"] == "navigation.set_fly"
    api.run("bindings.restore", {"id": "astrolabe.up.orbit"})
    row = next(item for item in api.state()["bindings"]["rows"]
               if item["row"]["id"] == "astrolabe.up.orbit")
    assert not row["modified"] and row["simple"]["target"] == "navigation.set_orbit"


def test_every_action_is_reachable_by_name():
    assert {"settings.set", "settings.reset", "settings.link", "apps.setup", "bindings.saveSimple",
            "runtime.inputMode", "device.recenter"} <= set(ACTIONS)
