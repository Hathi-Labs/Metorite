"""A late fold must not copy another run's reply under its own id (task 1533, B3).

A run that a new run cancels folds LATE. The new run has reset the thread's
event stream by then, so the replay holds the NEW run's events. Before the fix
the old run wrote them again under the old message id: one reply, two
``chat_message`` rows with the same content, tool events and time.

Fence: the run route passes ``expect_run_id`` to the fold, and the fold refuses
a stream whose ``RUN_STARTED`` names another run.
"""
from __future__ import annotations

import asyncio
import inspect

from gateway import chat_fold, run_trace
from gateway.routes import agent as agent_routes
from gateway.routes import chat as chat_routes


def _stream(monkeypatch, events):
    import orchestrator.stream_relay as relay

    async def _replay(*_a, **_k):
        return list(events)

    async def _no_trace(**_kw):
        return None

    monkeypatch.setattr(relay, "replay_events", _replay)
    monkeypatch.setattr(run_trace, "record_run_trace", _no_trace)


def test_two_folds_of_one_stream_write_one_row(monkeypatch):
    """Run 1 (cancelled) and run 2 both fold the stream that run 2 owns.
    With the guard that the route now passes, only run 2 writes."""
    rows: dict[str, str] = {}

    def _upsert(_tid, recs, **_k):
        for r in recs:
            rows[r.id] = r.content
        return []

    monkeypatch.setattr(chat_routes, "_ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(chat_routes, "_upsert_messages", _upsert)
    _stream(monkeypatch, [
        {"type": "RUN_STARTED", "runId": "run-2"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Drafting a fresh one now"},
    ])

    for mid, rid in (("asst-1", "run-1"), ("asst-2", "run-2")):
        asyncio.run(chat_fold.persist_final_assistant_message(
            "t-1", mid, user_id="a@x.io", run_id=rid, organization_id="org",
            expect_run_id=rid,
        ))
    assert rows == {"asst-2": "Drafting a fresh one now"}


def test_without_the_guard_the_old_run_writes_a_second_row(monkeypatch):
    """The bug itself: no expect_run_id, two ids, one reply."""
    rows: dict[str, str] = {}
    monkeypatch.setattr(chat_routes, "_ensure_session", lambda *_a, **_k: None)
    monkeypatch.setattr(
        chat_routes, "_upsert_messages",
        lambda _t, recs, **_k: rows.update({r.id: r.content for r in recs}) or [],
    )
    _stream(monkeypatch, [
        {"type": "RUN_STARTED", "runId": "run-2"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "same reply"},
    ])
    for mid in ("asst-1", "asst-2"):
        asyncio.run(chat_fold.persist_final_assistant_message(
            "t-1", mid, user_id="a@x.io", organization_id="org",
        ))
    assert len(rows) == 2


def test_the_run_route_passes_its_run_id_to_the_fold():
    """The route's run-end hook binds the fold to its own run."""
    src = inspect.getsource(agent_routes)
    start = src.index("async def _persist_on_complete")
    block = src[start:start + 2500]
    assert "persist_final_assistant_message(" in block
    assert "expect_run_id=run_id" in block
