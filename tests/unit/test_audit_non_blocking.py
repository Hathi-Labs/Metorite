"""BO-10 — ``acb_audit.record()`` must not block the event loop.

The DB write is synchronous (``acb_graph.get_session()`` is a sync engine) and
most callers are sync code. But the gateway's route handlers are async and call
``record()`` on the loop, where a blocking psycopg round-trip stalls every other
request in the process — behind a write that is best-effort by contract.

These tests pin the three properties that make the fix safe rather than merely
fast: the loop is not blocked, sync callers keep their old blocking behaviour,
and an in-flight write is not lost at shutdown.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from acb_audit import log as audit

#: A hang guard, never a budget (#947). A held write waits at most this long.
HANG_S = 30.0


def _event() -> audit.AuditEvent:
    return audit.AuditEvent(actor="user:t@x", action="test", target="thing:1")


@pytest.mark.asyncio
async def test_record_does_not_block_the_event_loop(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def held_persist(event: audit.AuditEvent) -> None:
        started.set()
        release.wait(HANG_S)
        finished.set()

    monkeypatch.setattr(audit, "_persist", held_persist)

    audit.record(_event())
    # The write is held until the test lets it go. So record() came back
    # before the write ended, and it did not run the write on the loop. An
    # inline write would hold record() for the whole guard, then finish. A
    # bound of 0.1 s against a write of 0.5 s measured the runner too.
    assert not finished.is_set(), "record() ran the write on the loop"
    release.set()

    # ...and it really is running, not silently dropped.
    await audit.drain(timeout=HANG_S)
    assert started.is_set() and finished.is_set()


@pytest.mark.asyncio
async def test_record_runs_the_write_off_the_loop_thread(monkeypatch) -> None:
    """Not just deferred — actually on another thread.

    A `loop.call_soon` style deferral would also return fast while still
    blocking the loop later, which is the bug wearing a disguise.
    """
    loop_thread = threading.get_ident()
    seen: list[int] = []

    monkeypatch.setattr(
        audit, "_persist", lambda event: seen.append(threading.get_ident())
    )

    audit.record(_event())
    await audit.drain(timeout=HANG_S)

    assert seen and seen[0] != loop_thread


def test_sync_callers_still_write_inline(monkeypatch) -> None:
    """No event loop → no thread hand-off.

    The orchestrator's agents call `record()` from plain sync code and expect
    the row to exist when it returns. Deferring for them would need a drain
    they have nowhere to call.
    """
    calls: list[audit.AuditEvent] = []
    monkeypatch.setattr(audit, "_persist", calls.append)

    ev = _event()
    audit.record(ev)

    assert calls == [ev]


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_writes(monkeypatch) -> None:
    """Shutdown must not cancel a write that is still running.

    This is the one real regression risk in making the call non-blocking: the
    old code finished the write before the handler returned.
    """
    finished: list[str] = []

    def slow_persist(event: audit.AuditEvent) -> None:
        time.sleep(0.2)
        finished.append(event.target)

    monkeypatch.setattr(audit, "_persist", slow_persist)

    audit.record(_event())
    assert finished == []          # still in flight
    await audit.drain(timeout=HANG_S)
    assert finished == ["thing:1"]  # completed, not cancelled


@pytest.mark.asyncio
async def test_drain_is_bounded_by_its_timeout(monkeypatch) -> None:
    """A wedged audit DB must not hold the shutdown open forever.

    The write is held until the test lets it go, so drain() can only come
    back at its own timeout. A drain that ignored it would wait for the
    write, which ends only at the hang guard. A bound of 1.5 s on a drain of
    0.2 s measured the runner too."""
    release = threading.Event()
    finished = threading.Event()

    def wedged(event: audit.AuditEvent) -> None:
        release.wait(HANG_S)
        finished.set()

    monkeypatch.setattr(audit, "_persist", wedged)

    audit.record(_event())
    await audit.drain(timeout=0.2)
    assert not finished.is_set(), "drain() waited for the wedged write"

    # Leave no thread running into the next test.
    release.set()
    await audit.drain(timeout=HANG_S)
    assert finished.is_set()


@pytest.mark.asyncio
async def test_a_failing_write_never_reaches_the_caller(monkeypatch) -> None:
    """Best-effort by contract, on the threaded path too.

    `_persist` swallows its own errors; this guards the wiring around it, where
    an exception would surface as an unretrieved-task warning instead.
    """
    def boom(event: audit.AuditEvent) -> None:
        raise RuntimeError("audit db down")

    monkeypatch.setattr(audit, "_persist", boom)

    audit.record(_event())  # must not raise
    with pytest.raises(RuntimeError):
        # The task itself does surface it — drain() gathers, it does not hide.
        await asyncio.wait_for(next(iter(audit._PENDING)), timeout=HANG_S)


@pytest.mark.asyncio
async def test_pending_set_does_not_grow_without_bound(monkeypatch) -> None:
    """The strong-reference set is bounded by concurrency, not by uptime."""
    monkeypatch.setattr(audit, "_persist", lambda event: None)
    before = len(audit._PENDING)
    for _ in range(5):
        audit.record(_event())
    await audit.drain(timeout=HANG_S)
    assert len(audit._PENDING) == before
