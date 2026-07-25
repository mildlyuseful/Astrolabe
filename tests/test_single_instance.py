# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from trackball_daemon import product
from trackball_daemon.instance_lock import (
    ERROR_ALREADY_EXISTS,
    MUTEX_NAMES,
    SingleInstanceGuard,
)


def _handing_out(handles, *, last_error=0):
    """A CreateMutexW stand-in that returns the given handles in order."""
    remaining = list(handles)
    calls = []

    def create_mutex(security, owner, name):
        calls.append((security, owner, name))
        return remaining.pop(0)

    return create_mutex, calls, (lambda: last_error)


def test_first_instance_holds_every_name_until_close():
    create_mutex, calls, last_error = _handing_out([101, 102])
    closed = []
    guard = SingleInstanceGuard(
        create_mutex=create_mutex, close_handle=closed.append, get_last_error=last_error)

    assert guard.acquire() is True
    assert guard.acquire() is True                      # idempotent: no second round of creates
    assert calls == [(None, False, name) for name in MUTEX_NAMES]
    assert closed == []
    guard.close()
    assert closed == [101, 102]


def test_both_generations_of_the_name_are_claimed():
    """The rename may not open a window where an old and a new build cannot see each other."""
    assert set(MUTEX_NAMES) == {
        product.SINGLE_INSTANCE_MUTEX, product.LEGACY_SINGLE_INSTANCE_MUTEX}


def test_second_instance_closes_duplicate_handle_and_cannot_acquire():
    closed = []
    guard = SingleInstanceGuard(
        create_mutex=lambda _security, _owner, _name: 202,
        close_handle=closed.append,
        get_last_error=lambda: ERROR_ALREADY_EXISTS)

    assert guard.acquire() is False
    assert closed == [202]
    assert guard.acquired is False


def test_losing_a_later_name_gives_up_the_ones_already_held():
    """A build that took the new name but found the old one taken must not keep holding either."""
    errors = iter([0, ERROR_ALREADY_EXISTS])
    create_mutex, calls, _unused = _handing_out([301, 302])
    closed = []
    guard = SingleInstanceGuard(
        create_mutex=create_mutex, close_handle=closed.append,
        get_last_error=lambda: next(errors))

    assert guard.acquire() is False
    assert len(calls) == 2
    assert closed == [302, 301]                         # the duplicate, then what was already held
    assert guard.acquired is False


def test_entrypoint_rejects_duplicate_before_constructing_app(monkeypatch):
    from trackball_daemon import __main__ as entrypoint

    events = []
    guard = type("Guard", (), {
        "acquire": lambda self: False,
        "close": lambda self: events.append("close"),
    })()
    monkeypatch.setattr(entrypoint, "App", lambda **_kwargs: events.append("app"))
    monkeypatch.setattr(entrypoint, "notify_already_running",
                        lambda: events.append("notification"))

    assert entrypoint.main([], instance_guard=guard) == 2
    assert events == ["notification"]


def test_entrypoint_releases_single_instance_guard_after_app_stops(monkeypatch):
    from trackball_daemon import __main__ as entrypoint

    events = []
    guard = type("Guard", (), {
        "acquire": lambda self: events.append("acquire") or True,
        "close": lambda self: events.append("close"),
    })()
    app = type("FakeApp", (), {"start": lambda self: events.append("start")})()
    monkeypatch.setattr(entrypoint, "App", lambda **_kwargs: app)

    assert entrypoint.main([], instance_guard=guard) == 0
    assert events == ["acquire", "start", "close"]
