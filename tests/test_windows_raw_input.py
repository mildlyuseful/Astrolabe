"""Pure normalization, provider lifecycle, and native Raw Input registration tests."""

import sys
import time

import pytest

from trackball_daemon.input import InputAggregator, InputPhase, ProviderStatus
from trackball_daemon.input.windows_raw_input import (
    RI_KEY_BREAK,
    RI_KEY_E0,
    RIDEV_INPUTSINK,
    SOURCE_ID,
    VK_CONTROL,
    VK_MENU,
    VK_RETURN,
    VK_SHIFT,
    WINDOWS_AVAILABLE,
    RawKeyboardPacket,
    WindowsRawInputProvider,
    WindowsRawInputReceiver,
    expand_required_controls,
    normalize_raw_keyboard,
)


def _packet(vk, *, scan=0, flags=0, extra=0, timestamp=10.0):
    return RawKeyboardPacket(vk, scan, flags, 0, extra, True, timestamp)


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class _FakeNative:
    instances = []

    def __init__(self, on_packet, on_lifecycle, on_failure):
        self.on_packet = on_packet
        self.on_lifecycle = on_lifecycle
        self.on_failure = on_failure
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _provider(*, down=(), accessible=True, ignored=()):
    _FakeNative.instances.clear()
    aggregator = InputAggregator()
    state = set(down)
    provider = WindowsRawInputProvider(
        aggregator.accept_many, aggregator.update_health,
        native_factory=_FakeNative,
        key_state=lambda vk: vk in state,
        desktop_accessible=lambda: accessible,
        ignored_extra_information=ignored,
    )
    aggregator.register_controls(provider.controls)
    return provider, aggregator, state


def test_raw_keyboard_normalization_preserves_modifier_sides_and_phases():
    assert normalize_raw_keyboard(_packet(VK_SHIFT, scan=0x2A)) == (
        "shift.left", InputPhase.PRESSED)
    assert normalize_raw_keyboard(_packet(VK_SHIFT, scan=0x36, flags=RI_KEY_BREAK)) == (
        "shift.right", InputPhase.RELEASED)
    assert normalize_raw_keyboard(_packet(VK_CONTROL)) == ("ctrl.left", InputPhase.PRESSED)
    assert normalize_raw_keyboard(_packet(VK_CONTROL, flags=RI_KEY_E0)) == (
        "ctrl.right", InputPhase.PRESSED)
    assert normalize_raw_keyboard(_packet(VK_MENU, flags=RI_KEY_E0)) == (
        "alt.right", InputPhase.PRESSED)
    assert normalize_raw_keyboard(_packet(VK_RETURN, flags=RI_KEY_E0)) == (
        "numpad.enter", InputPhase.PRESSED)
    assert normalize_raw_keyboard(_packet(0xFF)) is None


def test_generic_modifiers_expand_to_physical_controls_and_unknown_keys_fail():
    assert expand_required_controls(("ctrl", "shift.left", "f24")) == frozenset({
        "ctrl.left", "ctrl.right", "shift.left", "f24"})
    with pytest.raises(ValueError, match="unknown keyboard control"):
        expand_required_controls(("definitely-not-a-key",))


def test_provider_registers_lazily_and_empty_profile_never_starts_receiver():
    provider, aggregator, _state = _provider()
    assert provider.health.status is ProviderStatus.DISABLED
    provider.configure(())
    assert _FakeNative.instances == []
    assert aggregator.snapshot().provider_health == {}

    provider.configure(("ctrl",))
    assert len(_FakeNative.instances) == 1
    assert _FakeNative.instances[0].started
    assert provider.health.status is ProviderStatus.RUNNING
    assert provider.required_controls == frozenset({"ctrl"})
    provider.stop()


def test_raw_packets_are_filtered_to_required_controls_and_repeats_are_edges_only():
    provider, aggregator, _state = _provider(ignored=(0xA5701ABE,))
    provider.configure(("ctrl",))
    native = _FakeNative.instances[-1]
    changes = []
    aggregator.add_listener(changes.append)

    native.on_packet(_packet(ord("A")))
    native.on_packet(_packet(VK_CONTROL))
    native.on_packet(_packet(VK_CONTROL, timestamp=11.0))
    native.on_packet(_packet(VK_CONTROL, flags=RI_KEY_BREAK, timestamp=12.0))
    native.on_packet(_packet(VK_CONTROL, extra=0xA5701ABE, timestamp=13.0))
    _wait_until(lambda: len(changes) == 2)

    assert [change.events[0].phase for change in changes] == [
        InputPhase.PRESSED, InputPhase.RELEASED]
    assert changes[0].events[0].metadata["delivery"] == "input_sink"
    assert aggregator.snapshot().pressed_tokens == ()
    provider.stop()


def test_profile_reload_releases_all_keyboard_controls_before_reconcile():
    provider, aggregator, _state = _provider()
    provider.configure(("ctrl", "shift"))
    native = _FakeNative.instances[-1]
    native.on_packet(_packet(VK_CONTROL))
    native.on_packet(_packet(VK_SHIFT, scan=0x2A))
    _wait_until(lambda: len(aggregator.snapshot().pressed_tokens) == 2)
    changes = []
    aggregator.add_listener(changes.append)

    provider.configure(("f24",))

    assert changes[0].reason == "profile_reload"
    assert len(changes[0].events) == 2
    assert changes[0].snapshot.pressed_tokens == ()
    assert len(_FakeNative.instances) == 1  # profile swap reuses the registered receiver
    provider.stop()


def test_empty_profile_unregisters_receiver_and_releases_held_control():
    provider, aggregator, _state = _provider()
    provider.configure(("ctrl",))
    native = _FakeNative.instances[-1]
    native.on_packet(_packet(VK_CONTROL))
    _wait_until(lambda: bool(aggregator.snapshot().pressed_tokens))

    provider.configure(())

    assert native.stopped
    assert provider.health.status is ProviderStatus.DISABLED
    assert aggregator.snapshot().pressed_tokens == ()


def test_shutdown_unregisters_receiver_and_releases_held_control():
    provider, aggregator, _state = _provider()
    provider.configure(("alt",))
    native = _FakeNative.instances[-1]
    native.on_packet(_packet(VK_MENU, flags=RI_KEY_E0))
    _wait_until(lambda: bool(aggregator.snapshot().pressed_tokens))

    provider.stop("daemon_shutdown")

    assert native.stopped
    assert provider.health.status is ProviderStatus.STOPPED
    assert aggregator.snapshot().pressed_tokens == ()


def test_lock_releases_and_unlock_reconciles_current_physical_state():
    provider, aggregator, state = _provider()
    provider.configure(("ctrl",))
    native = _FakeNative.instances[-1]
    native.on_packet(_packet(VK_CONTROL))
    _wait_until(lambda: aggregator.snapshot().pressed_tokens == ("keyboard:ctrl.left",))

    native.on_lifecycle("session_lock")
    _wait_until(lambda: provider.health.status is ProviderStatus.SUSPENDED)
    assert aggregator.snapshot().pressed_tokens == ()

    state.add(0xA2)  # VK_LCONTROL
    native.on_lifecycle("session_unlock")
    _wait_until(lambda: aggregator.snapshot().pressed_tokens == ("keyboard:ctrl.left",))
    assert provider.health.status is ProviderStatus.RUNNING
    provider.stop()


def test_inaccessible_desktop_fails_safe_to_release_all():
    accessible = [True]
    _FakeNative.instances.clear()
    aggregator = InputAggregator()
    provider = WindowsRawInputProvider(
        aggregator.accept_many, aggregator.update_health,
        native_factory=_FakeNative, key_state=lambda _vk: False,
        desktop_accessible=lambda: accessible[0])
    aggregator.register_controls(provider.controls)
    provider.configure(("alt",))
    native = _FakeNative.instances[-1]
    native.on_packet(_packet(VK_MENU, flags=RI_KEY_E0))
    _wait_until(lambda: aggregator.snapshot().pressed_tokens == ("keyboard:alt.right",))

    accessible[0] = False
    provider.reconcile("access_boundary")

    assert provider.health.status is ProviderStatus.SUSPENDED
    assert aggregator.snapshot().pressed_tokens == ()
    provider.stop()


def test_receiver_failure_releases_and_explicit_restart_reconciles():
    provider, aggregator, state = _provider()
    provider.configure(("ctrl.left",))
    native = _FakeNative.instances[-1]
    native.on_packet(_packet(VK_CONTROL))
    _wait_until(lambda: bool(aggregator.snapshot().pressed_tokens))
    native.on_failure(RuntimeError("receiver died"))
    _wait_until(lambda: provider.health.status is ProviderStatus.FAILED)
    assert aggregator.snapshot().pressed_tokens == ()

    state.add(0xA2)
    generation = provider.health.generation
    provider.restart()
    assert provider.health.generation == generation + 1
    assert aggregator.snapshot().pressed_tokens == ("keyboard:ctrl.left",)
    assert native.stopped
    provider.stop()


def test_raw_input_registration_flags_are_pass_through():
    assert RIDEV_INPUTSINK == 0x00000100
    source = open(
        "trackball_daemon/input/windows_raw_input.py", encoding="utf-8").read()
    registration = "RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self._hwnd)"
    assert registration in source
    assert "RIDEV_NOLEGACY" not in source.replace(
        "without ``RIDEV_NOLEGACY``", "").replace(
        "without `RIDEV_NOLEGACY`", "")


@pytest.mark.skipif(not WINDOWS_AVAILABLE, reason="native Raw Input requires Windows")
def test_native_message_only_receiver_registers_and_stops():
    failures = []
    receiver = WindowsRawInputReceiver(lambda _packet: None, lambda _event: None, failures.append)
    receiver.start()
    assert receiver.running
    receiver.stop()
    assert not failures
