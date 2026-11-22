"""Task 3865: the off-domain mismatch line logs once per address per hour."""

from __future__ import annotations

from acb_auth import deps


def _clock(monkeypatch, now: list[float]) -> None:
    import time

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    deps._mismatch_logged.clear()


def test_two_calls_in_the_window_log_once(monkeypatch) -> None:
    now = [1000.0]
    _clock(monkeypatch, now)
    assert deps._mismatch_due("a@x.test", "fracktal.in") is True
    now[0] += 3599
    assert deps._mismatch_due("a@x.test", "fracktal.in") is False


def test_another_address_logs(monkeypatch) -> None:
    _clock(monkeypatch, [1000.0])
    assert deps._mismatch_due("a@x.test", "fracktal.in") is True
    assert deps._mismatch_due("b@x.test", "fracktal.in") is True


def test_after_the_window_it_logs_again(monkeypatch) -> None:
    now = [1000.0]
    _clock(monkeypatch, now)
    assert deps._mismatch_due("a@x.test", "fracktal.in") is True
    now[0] += deps._MISMATCH_LOG_WINDOW_S + 1
    assert deps._mismatch_due("a@x.test", "fracktal.in") is True


def test_the_cache_is_bounded(monkeypatch) -> None:
    _clock(monkeypatch, [1000.0])
    for i in range(deps._MISMATCH_LOG_MAX_KEYS + 50):
        deps._mismatch_due(f"u{i}@x.test", "fracktal.in")
    assert len(deps._mismatch_logged) == deps._MISMATCH_LOG_MAX_KEYS


def test_the_route_still_logs_through_the_gate() -> None:
    import inspect

    src = inspect.getsource(deps.get_current_user)
    assert "_mismatch_due(" in src and "auth.identity_domain_mismatch" in src
