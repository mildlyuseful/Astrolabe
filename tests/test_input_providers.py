"""Normalized provider model and pressed-set lifecycle contracts."""

from dataclasses import FrozenInstanceError

import pytest

from trackball_daemon.input import (
    InputAggregator,
    InputControlDescriptor,
    InputEvent,
    InputPhase,
    ProviderHealth,
    ProviderStatus,
    InputProvider,
)


def _descriptor(source, control, *, aliases=()):
    return InputControlDescriptor(
        source, control, control.title(), "key" if source == "keyboard" else "button",
        aliases=aliases, metadata={"group": ["test"]})


def _event(source, control, phase, **kwargs):
    return InputEvent(source, control, phase, timestamp=10.0, **kwargs)


def test_input_records_are_immutable_detached_and_namespaced():
    metadata = {"scan": [42]}
    event = InputEvent("keyboard", "shift.left", "pressed", 1.5, metadata=metadata)
    metadata["scan"].append(54)
    assert event.token == "keyboard:shift.left"
    assert event.metadata["scan"] == (42,)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        event.control_id = "shift.right"

    descriptor = _descriptor("keyboard", "shift.left", aliases=("shift",))
    assert descriptor.token == "keyboard:shift.left"
    assert descriptor.alias_tokens == ("keyboard:shift",)
    assert descriptor.metadata["group"] == ("test",)


def test_disconnected_event_is_source_scoped_and_validated():
    disconnected = InputEvent("keyboard", None, InputPhase.DISCONNECTED, 1.0)
    assert disconnected.token is None
    with pytest.raises(ValueError, match="cannot identify"):
        InputEvent("keyboard", "ctrl.left", InputPhase.DISCONNECTED, 1.0)
    with pytest.raises(ValueError, match="non-negative"):
        InputEvent("keyboard", "ctrl.left", InputPhase.PRESSED, -1)


def test_aggregator_rejects_repeat_edges_but_preserves_pressed_state():
    aggregator = InputAggregator()
    aggregator.register_controls((_descriptor("keyboard", "ctrl.left"),))
    changes = []
    aggregator.add_listener(changes.append)

    first = aggregator.accept(_event("keyboard", "ctrl.left", "pressed"))
    repeat = aggregator.accept(_event("keyboard", "ctrl.left", "pressed", sequence=2))
    assert first.snapshot.pressed_tokens == ("keyboard:ctrl.left",)
    assert repeat is None
    assert aggregator.snapshot().pressed_tokens == ("keyboard:ctrl.left",)
    assert len(changes) == 1

    released = aggregator.accept(_event("keyboard", "ctrl.left", "released"))
    duplicate_release = aggregator.accept(_event("keyboard", "ctrl.left", "released"))
    assert released.snapshot.pressed_tokens == ()
    assert duplicate_release is None


def test_disconnect_releases_one_provider_atomically_and_preserves_other_sources():
    aggregator = InputAggregator()
    aggregator.register_controls((
        _descriptor("keyboard", "ctrl.left", aliases=("ctrl",)),
        _descriptor("keyboard", "shift.left", aliases=("shift",)),
        _descriptor("ble:unit", "fiveway.up"),
    ))
    aggregator.accept(_event("keyboard", "ctrl.left", "pressed"))
    aggregator.accept(_event("keyboard", "shift.left", "pressed"))
    aggregator.accept(_event("ble:unit", "fiveway.up", "pressed"))
    changes = []
    aggregator.add_listener(changes.append)

    transition = aggregator.accept(InputEvent(
        "keyboard", None, "disconnected", 11.0, metadata={"reason": "receiver_failure"}))

    assert len(changes) == 1
    assert transition.snapshot.pressed_tokens == ("ble:unit:fiveway.up",)
    assert [event.phase for event in transition.events] == [
        InputPhase.RELEASED, InputPhase.RELEASED, InputPhase.DISCONNECTED]
    assert all(event.metadata.get("synthetic") for event in transition.events[:2])


def test_provider_failure_releases_holds_before_health_snapshot():
    aggregator = InputAggregator()
    aggregator.register_controls((_descriptor("keyboard", "alt.right"),))
    aggregator.accept(_event("keyboard", "alt.right", "pressed"))
    changes = []
    aggregator.add_listener(changes.append)

    health = ProviderHealth("keyboard", ProviderStatus.FAILED, "receiver stopped", 12.0, 3)
    aggregator.update_health(health)

    assert len(changes) == 1
    assert changes[0].reason == "provider_failed"
    assert changes[0].events[0].phase is InputPhase.RELEASED
    assert changes[0].snapshot.provider_health["keyboard"] == health
    assert changes[0].snapshot.pressed_tokens == ()


def test_release_all_is_one_final_state_for_cross_provider_chords():
    aggregator = InputAggregator()
    aggregator.register_controls((
        _descriptor("keyboard", "ctrl.left"),
        _descriptor("ble:unit", "fiveway.center"),
    ))
    aggregator.accept(_event("keyboard", "ctrl.left", "pressed"))
    aggregator.accept(_event("ble:unit", "fiveway.center", "pressed"))
    changes = []
    aggregator.add_listener(changes.append)

    transition = aggregator.release_all("shutdown")
    assert len(changes) == 1
    assert transition.snapshot.pressed_tokens == ()
    assert len(transition.events) == 2
    assert {event.source_id for event in transition.events} == {"keyboard", "ble:unit"}


def test_reconciliation_batch_publishes_only_the_final_chord_state():
    aggregator = InputAggregator()
    aggregator.register_controls((
        _descriptor("keyboard", "ctrl.left"),
        _descriptor("keyboard", "shift.left"),
    ))
    changes = []
    aggregator.add_listener(changes.append)
    transition = aggregator.accept_many((
        _event("keyboard", "ctrl.left", "pressed"),
        _event("keyboard", "shift.left", "pressed"),
    ), "resume_reconcile")

    assert len(changes) == 1
    assert transition.reason == "resume_reconcile"
    assert transition.snapshot.pressed_tokens == (
        "keyboard:ctrl.left", "keyboard:shift.left")


def test_listener_failure_does_not_block_other_listeners_or_reentrant_reads(caplog):
    aggregator = InputAggregator()
    aggregator.register_controls((_descriptor("keyboard", "f24"),))
    observed = []

    def broken(_transition):
        raise RuntimeError("boom")

    aggregator.add_listener(broken)
    aggregator.add_listener(lambda transition: observed.append(
        (transition.snapshot.revision, aggregator.snapshot().revision)))
    aggregator.accept(_event("keyboard", "f24", "pressed"))

    assert observed == [(1, 1)]
    assert "Input listener failed" in caplog.text


def test_generic_modifier_alias_can_describe_both_physical_sides():
    aggregator = InputAggregator()
    aggregator.register_controls((
        _descriptor("keyboard", "ctrl.left", aliases=("ctrl",)),
        _descriptor("keyboard", "ctrl.right", aliases=("ctrl",)),
    ))
    assert {descriptor.token for descriptor in aggregator.descriptors()} == {
        "keyboard:ctrl.left", "keyboard:ctrl.right"}
    with pytest.raises(ValueError, match="unregistered"):
        aggregator.accept(_event("keyboard", "shift.left", "pressed"))


class _FakeProvider(InputProvider):
    def __init__(self, aggregator):
        self._aggregator = aggregator
        self.configurations = []
        self.stops = []
        self._health = ProviderHealth("fake", ProviderStatus.DISABLED)

    @property
    def source_id(self):
        return "fake"

    @property
    def controls(self):
        return (_descriptor("fake", "button"),)

    @property
    def health(self):
        return self._health

    def configure(self, required_control_ids):
        self.configurations.append(frozenset(required_control_ids))
        self._health = ProviderHealth("fake", ProviderStatus.RUNNING)
        self._aggregator.update_health(self._health)
        return self._health

    def reconcile(self, reason="manual"):
        return reason

    def stop(self, reason="shutdown"):
        self.stops.append(reason)
        self._health = ProviderHealth("fake", ProviderStatus.STOPPED)
        self._aggregator.update_health(self._health)


def test_aggregator_manages_provider_configuration_and_shutdown():
    aggregator = InputAggregator()
    provider = _FakeProvider(aggregator)
    aggregator.register_provider(provider)
    assert aggregator.snapshot().provider_health["fake"].status is ProviderStatus.DISABLED

    aggregator.configure_provider("fake", ("button",))
    aggregator.accept(_event("fake", "button", "pressed"))
    aggregator.shutdown("test_shutdown")

    assert provider.configurations == [frozenset({"button"})]
    assert provider.stops == ["test_shutdown"]
    assert aggregator.snapshot().pressed_tokens == ()
