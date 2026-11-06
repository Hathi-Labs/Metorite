"""When the chat greets (PZ-39, D5): the gap rule and the prime step.

The rule is pure (``today.greet_due``). The prime step reads STORED rows and
never ``event_payload["messages"]`` (R5). A fault, a timeout and an unknown
result give no greeting and never fail the turn. The R8 half is
``tests/unit/test_executor_greet_due_r8.py``.
"""
from __future__ import annotations

import asyncio

import pytest
from acb_common.today import GREET_GAP_HOURS, greet_due, part_of_day_line

H = 3600 * 1000
NOW = 1_800_000_000_000


def test_one_constant_names_the_four_hours() -> None:
    assert GREET_GAP_HOURS == 4


def test_a_first_reply_greets() -> None:
    assert greet_due(None, NOW, first_reply=True) is True


def test_the_gap_edge() -> None:
    assert greet_due(NOW - (4 * H - 60_000), NOW, first_reply=False) is False  # 3 h 59 m
    assert greet_due(NOW - 4 * H, NOW, first_reply=False) is True               # 4 h
    assert greet_due(NOW - 5 * H, NOW, first_reply=False) is True


def test_a_second_reply_within_the_gap_does_not_greet() -> None:
    assert greet_due(NOW - 30_000, NOW, first_reply=False) is False


def test_an_unknown_time_does_not_greet() -> None:
    assert greet_due(None, NOW, first_reply=False) is False


def test_the_line_names_greet_now_only_when_due() -> None:
    assert "Greet now." not in part_of_day_line("Asia/Kolkata")
    assert "Greet now." in part_of_day_line("Asia/Kolkata", greet=True)


# ── The prime step ──────────────────────────────────────────────────────────


@pytest.fixture
def ex():
    from orchestrator import executor
    return executor


def _decide(ex, monkeypatch, read):
    monkeypatch.setattr(ex, "_greet_read", read)
    monkeypatch.setattr(ex, "_briefing_now",
                        lambda: __import__("datetime").datetime.fromtimestamp(NOW / 1000, __import__("datetime").UTC))
    return asyncio.run(ex._greet_decision("org", "sid"))


def test_prime_decisions(ex, monkeypatch) -> None:
    async def first(org, sid):
        return (True, None)

    async def recent(org, sid):
        return (False, NOW - 59 * 60_000)

    async def old(org, sid):
        return (False, NOW - 4 * H)

    assert _decide(ex, monkeypatch, first) is True
    assert _decide(ex, monkeypatch, recent) is False
    assert _decide(ex, monkeypatch, old) is True


def test_a_faulty_read_gives_no_greeting_and_does_not_raise(ex, monkeypatch) -> None:
    async def boom(org, sid):
        raise RuntimeError("db down")

    assert _decide(ex, monkeypatch, boom) is False


def test_a_slow_read_gives_no_greeting(ex, monkeypatch) -> None:
    async def slow(org, sid):
        await asyncio.sleep(5)
        return (True, None)

    monkeypatch.setattr(ex, "_GREET_TIMEOUT_S", 0.05)
    assert _decide(ex, monkeypatch, slow) is False


def test_the_payload_messages_cannot_change_the_result(ex) -> None:
    """The prime step takes no payload. Its signature has no `event_payload`,
    and the source never names `messages`."""
    import inspect

    for fn in (ex._prime_greet_due, ex._greet_decision, ex._greet_read, ex._greet_due_now):
        assert "payload" not in str(inspect.signature(fn))
        assert '["messages"]' not in inspect.getsource(fn)
        assert ".get(\"messages\")" not in inspect.getsource(fn)


def test_no_binding_means_no_greeting(ex) -> None:
    assert ex._greet_due_now() is False
