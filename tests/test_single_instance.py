# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

from trackball_daemon.instance_lock import ERROR_ALREADY_EXISTS, MUTEX_NAME, SingleInstanceGuard


def test_first_instance_holds_named_mutex_until_close():
    calls = []
    guard = SingleInstanceGuard(
        create_mutex=lambda security, owner, name: calls.append(
            (security, owner, name)) or 101,
        close_handle=lambda handle: calls.append(("close", handle)),
        get_last_error=lambda: 0)

    assert guard.acquire() is True
    assert guard.acquire() is True
    assert calls == [(None, False, MUTEX_NAME)]
    guard.close()
    assert calls[-1] == ("close", 101)


def test_second_instance_closes_duplicate_handle_and_cannot_acquire():
    closed = []
    guard = SingleInstanceGuard(
        create_mutex=lambda _security, _owner, _name: 202,
        close_handle=closed.append,
        get_last_error=lambda: ERROR_ALREADY_EXISTS)

    assert guard.acquire() is False
    assert closed == [202]
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
