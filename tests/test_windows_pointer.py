import pytest

from trackball_daemon import windows_pointer
from trackball_daemon.windows_pointer import SendInputPointerButtonSink


def test_pointer_sink_is_bounded_and_identity_owned():
    events = []
    sink = SendInputPointerButtonSink(
        lambda button, pressed: events.append((button, pressed)))
    first = ("binding.first", "1")
    second = ("binding.second", "2")

    sink.press("left", first)
    sink.press("left", first)  # repeat is ignored
    sink.release("left", second)  # replaced/non-owner releases are ignored
    sink.press("left", second)  # replacement releases the old owner first
    sink.release("left", first)
    sink.release("left", second)

    assert events == [
        ("left", True), ("left", False), ("left", True), ("left", False)]
    assert sink.held == {}


def test_pointer_sink_release_all_covers_every_allowlisted_button():
    events = []
    sink = SendInputPointerButtonSink(
        lambda button, pressed: events.append((button, pressed)))
    for button in ("left", "right", "middle", "x1", "x2"):
        sink.press(button, (button, "1"))

    sink.release_all()

    assert events[5:] == [
        ("left", False), ("right", False), ("middle", False),
        ("x1", False), ("x2", False)]
    assert sink.held == {}


def test_pointer_sink_rejects_arbitrary_button_or_key_targets():
    sink = SendInputPointerButtonSink(lambda _button, _pressed: None)
    with pytest.raises(ValueError, match="unsupported pointer button"):
        sink.press("keyboard:a", ("bad", "1"))


def test_pointer_button_injection_reports_a_rejected_windows_event(monkeypatch):
    monkeypatch.setattr(windows_pointer, "_send", lambda *_args, **_kwargs: False)
    with pytest.raises(OSError, match="rejected"):
        windows_pointer.send_pointer_button("left", True)
