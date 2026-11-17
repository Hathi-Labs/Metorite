"""Task 3640: a steer that the run never read drops the run's stored session.

Task 2948 made the session digests skip each row that the gateway marked ``steered``,
because a run that TOOK the steer stores it in its own session. A steer that comes back
unread (``steer.return_unconsumed``) is not in that session, so the next turn would hit a
session that lacks it. ``return_unconsumed`` now marks the thread's session stale.

Fence (R7): the cases below. The R8 half is in ``test_native_session_persistence.py``
(``test_r8_an_unread_steer_drops_the_session``).
"""
from __future__ import annotations

from typing import Any

import pytest

steer = pytest.importorskip("orchestrator.steer", reason="orchestrator not installed")
store = pytest.importorskip("orchestrator.native_session_store", reason="orchestrator not installed")

from acb_common.tenant_redis import organization_scope  # noqa: E402


@pytest.fixture
def calls(monkeypatch) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []

    async def fake_mark(*, organization_id: str, thread_id: str) -> str:
        seen.append({"org": organization_id, "thread": thread_id})
        return "marked"

    async def stored(thread_id: str, signal: dict[str, Any]) -> bool:
        return True

    monkeypatch.setattr(store, "mark_stale", fake_mark)
    monkeypatch.setattr(steer, "persist_signal", stored)
    monkeypatch.setattr(steer, "_PENDING_GUIDANCE", {})
    monkeypatch.setattr(steer, "_CLOSED", set())
    return seen


def _flag(monkeypatch, on: bool) -> None:
    import acb_common

    class _S:
        maf_native_sessions = on

    monkeypatch.setattr(acb_common, "get_settings", lambda: _S())


async def test_an_unread_steer_marks_the_session_stale(monkeypatch, calls) -> None:
    _flag(monkeypatch, True)
    steer._PENDING_GUIDANCE["t-1"] = [{"id": "s1", "text": "also the archive", "author": "a@x.test"}]
    with organization_scope("org-a"):
        assert await steer.return_unconsumed("t-1") == 1
    assert calls == [{"org": "org-a", "thread": "t-1"}]


async def test_a_steer_that_could_not_be_stored_still_marks_the_session(monkeypatch, calls) -> None:
    _flag(monkeypatch, True)

    async def not_stored(thread_id: str, signal: dict[str, Any]) -> bool:
        return False

    monkeypatch.setattr(steer, "persist_signal", not_stored)
    steer._PENDING_GUIDANCE["t-2"] = [{"id": "s1", "text": "x"}]
    with organization_scope("org-a"):
        assert await steer.return_unconsumed("t-2") == 0
    assert calls == [{"org": "org-a", "thread": "t-2"}]


async def test_a_run_that_read_every_steer_marks_nothing(monkeypatch, calls) -> None:
    _flag(monkeypatch, True)
    with organization_scope("org-a"):
        assert await steer.return_unconsumed("t-3") == 0
    assert calls == []


async def test_with_the_flag_off_nothing_is_marked(monkeypatch, calls) -> None:
    _flag(monkeypatch, False)
    steer._PENDING_GUIDANCE["t-4"] = [{"id": "s1", "text": "x"}]
    with organization_scope("org-a"):
        await steer.return_unconsumed("t-4")
    assert calls == []


async def test_with_no_tenant_bound_nothing_is_marked(monkeypatch, calls) -> None:
    _flag(monkeypatch, True)
    steer._PENDING_GUIDANCE["t-5"] = [{"id": "s1", "text": "x"}]
    await steer.return_unconsumed("t-5")
    assert calls == []


async def test_a_fault_in_the_mark_never_fails_the_run(monkeypatch, calls) -> None:
    _flag(monkeypatch, True)

    async def boom(*, organization_id: str, thread_id: str) -> str:
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "mark_stale", boom)
    steer._PENDING_GUIDANCE["t-6"] = [{"id": "s1", "text": "x"}]
    with organization_scope("org-a"):
        assert await steer.return_unconsumed("t-6") == 1
