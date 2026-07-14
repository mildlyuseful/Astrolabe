"""Atomic store, immutable snapshot, event, concurrency, and v8 migration contracts."""
import json
from pathlib import Path
import threading

import pytest

from trackball_daemon.app_registry import APP_IDS
from trackball_daemon.config_store import ConfigChangeEvent, ConfigStore


FIXTURES = Path(__file__).with_name("fixtures")


def _store_path(tmp_path):
    return tmp_path / "TrackballDaemon" / "config.json"


def _load_fixture_store(tmp_path, name):
    path = _store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = (FIXTURES / name).read_text(encoding="utf-8")
    path.write_text(source, encoding="utf-8")
    return ConfigStore(path).load(), path, source


def test_fresh_install_persists_empty_override_maps_and_resolves_system_values(tmp_path):
    path = _store_path(tmp_path)
    store = ConfigStore(path).load()
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert store.first_run
    assert disk["version"] == 9
    assert disk["global_overrides"] == {}
    assert disk["app_overrides"] == {app_id: {} for app_id in APP_IDS}
    assert disk["internal_global_overrides"] == {}
    assert disk["internal_app_overrides"] == {app_id: {} for app_id in APP_IDS}
    assert store.snapshot().global_value("input.mode.default") == "3d"
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "camera"
    assert "buttons" not in store.snapshot().general_profile


def test_snapshot_is_copy_on_publish_and_deeply_immutable(tmp_path):
    store = ConfigStore(_store_path(tmp_path)).load()
    before = store.snapshot()
    with pytest.raises(TypeError):
        before.global_values["pointer.cursor.gain"] = 999
    with pytest.raises(TypeError):
        before.app_profiles["blender"]["bindings"]["orbit"]["sensitivity"] = 999
    event = store.set_global("pointer.cursor.gain", 300.0)
    after = store.snapshot()
    assert event.snapshot is after
    assert before.global_value("pointer.cursor.gain") == 216.0
    assert after.global_value("pointer.cursor.gain") == 300.0
    assert before.revision + 1 == after.revision


def test_transaction_is_atomic_and_publishes_one_structured_event(tmp_path):
    store = ConfigStore(_store_path(tmp_path)).load()
    events = []
    store.add_listener(events.append)
    with store.transaction() as tx:
        tx.set_global("navigation.orbit.sensitivity", 1.5)
        tx.set_app("blender", "navigation.orbit.sensitivity", 2.0)
        tx.set_selected_app("fusion360")
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, ConfigChangeEvent)
    assert len(event.changes) == 3
    assert event.snapshot.selected_app == "fusion360"
    assert event.snapshot.global_value("navigation.orbit.sensitivity") == 1.5
    assert event.snapshot.app_value("blender", "navigation.orbit.sensitivity") == 2.0


def test_invalid_transaction_does_not_publish_or_touch_disk(tmp_path):
    path = _store_path(tmp_path)
    store = ConfigStore(path).load()
    before = path.read_text(encoding="utf-8")
    revision = store.snapshot().revision
    with pytest.raises(ValueError):
        with store.transaction() as tx:
            tx.set_global("pointer.cursor.gain", 400.0)
            tx.set_app("godot", "navigation.orbit.style", "free")
    assert path.read_text(encoding="utf-8") == before
    assert store.snapshot().revision == revision


def test_axis_source_updates_must_be_atomic_permutations(tmp_path):
    store = ConfigStore(_store_path(tmp_path)).load()
    with pytest.raises(ValueError, match="permutation"):
        store.set_global("input.axis_orientation.x.source", 1)
    store.set_ui_value(("general", "axis_orientation", "source"), [1, 0, 2])
    assert store.ui_value(("general", "axis_orientation", "source")) == [1, 0, 2]


def test_link_unlink_and_system_reset_operations_have_distinct_semantics(tmp_path):
    store = ConfigStore(_store_path(tmp_path)).load()
    store.set_global("navigation.orbit.pivot", "selection")
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "selection"

    store.transaction().unlink_all_app("blender").commit()
    store.set_global("navigation.orbit.pivot", "origin")
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "selection"

    store.transaction().link_all_app("blender").commit()
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "origin"

    store.transaction().reset_app_setting_to_system(
        "blender", "navigation.orbit.pivot").commit()
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "camera"
    store.set_global("navigation.orbit.pivot", "object")
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "camera"


def test_listener_failure_is_logged_and_does_not_hide_or_block_other_subscribers(
        tmp_path, caplog):
    store = ConfigStore(_store_path(tmp_path)).load()
    received = []

    def broken(_event):
        raise RuntimeError("subscriber exploded")

    store.add_listener(broken)
    store.add_listener(received.append)
    with caplog.at_level("ERROR", logger="trackball_daemon.config_store"):
        event = store.set_global("pointer.cursor.gain", 250.0)
    assert received == [event]
    assert "subscriber exploded" in caplog.text


def test_listener_can_commit_reentrantly_after_snapshot_publish(tmp_path):
    store = ConfigStore(_store_path(tmp_path)).load()
    revisions = []

    def listener(event):
        revisions.append(event.revision)
        if len(revisions) == 1:
            store.set_global("pointer.scroll.gain", 35.0)

    store.add_listener(listener)
    store.set_global("pointer.cursor.gain", 250.0)
    assert revisions == sorted(revisions)
    assert len(revisions) == 2
    assert store.snapshot().global_value("pointer.cursor.gain") == 250.0
    assert store.snapshot().global_value("pointer.scroll.gain") == 35.0


def test_concurrent_transactions_do_not_lose_unrelated_updates(tmp_path):
    store = ConfigStore(_store_path(tmp_path)).load()
    barrier = threading.Barrier(3)
    errors = []

    def update(setting_id, value):
        try:
            tx = store.transaction().set_global(setting_id, value)
            barrier.wait(timeout=5)
            tx.commit()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=update, args=("pointer.cursor.gain", 260.0))
    second = threading.Thread(target=update, args=("pointer.scroll.gain", 36.0))
    first.start()
    second.start()
    barrier.wait(timeout=5)
    first.join(timeout=5)
    second.join(timeout=5)
    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert store.snapshot().global_value("pointer.cursor.gain") == 260.0
    assert store.snapshot().global_value("pointer.scroll.gain") == 36.0


def test_v8_fixture_migrates_equal_values_to_links_and_differences_to_pins(tmp_path):
    store, path, _source = _load_fixture_store(tmp_path, "config_v8_migration.json")
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["version"] == 9
    assert disk["app_overrides"]["blender"] == {}
    fusion = disk["app_overrides"]["fusion360"]
    assert fusion["navigation.refresh_rate"] == 75
    assert fusion["navigation.orbit.sensitivity"] == 1.75
    assert fusion["navigation.level_horizon_on_entry"] is False
    assert disk["global_overrides"]["navigation.refresh_rate"] == 48
    assert disk["global_overrides"]["pointer.cursor.gain"] == 240.0
    assert disk["device_overrides"]["device.name"] == "Fixture Trackball"
    assert disk["ui_state"]["selected_app"] == "fusion360"
    assert disk["apps"]["onshape"] == {
        "enabled": True, "installed": True, "addin_version": "web-fixture"}
    assert store.snapshot().app_value("blender", "navigation.refresh_rate") == 48
    assert store.snapshot().app_value("fusion360", "navigation.refresh_rate") == 75


def test_v8_aliases_are_canonical_and_removed_data_is_not_written(tmp_path):
    store, path, _source = _load_fixture_store(tmp_path, "config_v8_cursor.json")
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert store.snapshot().global_value("input.mode.default") == "pointer"
    assert disk["global_overrides"]["input.mode.default"] == "pointer"
    assert disk["ui_state"]["selected_app"] == "rhino"
    serialized = path.read_text(encoding="utf-8")
    assert '"buttons"' not in serialized
    assert '"active_app"' not in serialized
    assert '"cube"' not in serialized
    assert '"cursor"' not in json.dumps(disk["global_overrides"].get("input.mode.default"))


@pytest.mark.parametrize("fixture", [
    "config_v8_malformed.json",
    "config_v8_invalid_shape.json",
])
def test_bad_legacy_input_is_preserved_and_uses_in_memory_defaults(tmp_path, fixture):
    store, path, source = _load_fixture_store(tmp_path, fixture)
    assert path.read_text(encoding="utf-8") == source
    assert store.snapshot().global_value("input.mode.default") == "3d"
    assert store.snapshot().app_value("blender", "navigation.orbit.pivot") == "camera"
