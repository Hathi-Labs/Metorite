"""RESUME-4a-1: a continuation marks the stored native session stale (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.7, the RESUME-4a
row (task 3478), and ``maf_coding_engine.md`` §15.9.5 "As built
(RESUME-4a-1)". The hermetic half is section 11 of
``test_resume_continuation.py``.

The defect (audit of 2026-11-16, case B with ``MAF_NATIVE_SESSIONS`` on).
The member writes turn N+2 after the park, and that turn saves a session
whose digest runs through user N+2. A late Approve then runs a continuation
that skips the session (``resume_note``) and adds a reply row with no user
row before it. At turn N+3 the covered rows still end at user N+2, so the
load is a ``hit``, and the model never reads the reply of the continuation.

Every statement here is the real store SQL on the phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role,
with the chat helpers of ``test_native_session_persistence.py``.

R7, each with the mutation that turns it red:

* ``test_r8_the_mark_makes_the_next_turn_read_the_text_history`` - write a
  real digest (``SET transcript_digest = transcript_digest``), and the next
  load is a ``hit`` again.
* ``test_r8_without_the_mark_the_next_turn_is_a_stale_hit`` - the defect
  itself, so the case above is not vacuous.
* ``test_r8_org_b_cannot_mark_org_a`` - RLS refuses org B, and the mark
  touches only its own key.
* ``test_r8_a_key_with_no_row_makes_no_row`` - an upsert in place of the
  UPDATE makes a row.
* ``test_r8_a_second_mark_is_a_no_op`` - drop ``transcript_digest <>
  :stale``, and the second mark changes a row.

With ``TENANT_LADDER_DATABASE_URL`` unset, the suite skips, and a skip is
not a pass. Run it on a PRIVATE database::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db> \\
        uv run pytest tests/unit/test_resume_native_stale_r8.py -v -rs
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import asyncio
from typing import Any

import pytest

store = pytest.importorskip("orchestrator.native_session_store",
                            reason="orchestrator not installed")
executor = pytest.importorskip("orchestrator.executor", reason="orchestrator not installed")

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    promoted,
)

# Fixtures and helpers, resolved by name. The imports are load-bearing.
from tests.unit.test_native_session_persistence import (  # noqa: E402, F401
    _AGENT_Y,
    _PROBE_AGENT,
    _admin,
    _agent_row,
    _begin,
    _chat,
    _finish,
    _LogSpy,
    _message,
    _one_turn,
    _session_catalog,
    _tid,
    _user_row,
    graph_as_app,
)

pytestmark = _DB_GATE

_LIST = "List my deals."
_LISTED = "Neuropeak and Orbit."
_MOVE = "Move Neuropeak to Payment Complete."
_CARD = "Move this deal to Payment Complete?"
_AFTER = "While that waits, what is due today?"
_AFTER_ANSWER = "Two tasks are due today."
_REPLY = "Done: Neuropeak is now in Payment Complete."
_NEXT = "Thanks. What next?"


@pytest.fixture
def store_log(monkeypatch) -> _LogSpy:
    spy = _LogSpy()
    monkeypatch.setattr(store, "_log", spy)
    return spy


def _mark(org: str, tid: str, agent: str = _PROBE_AGENT) -> str:
    return asyncio.run(store.mark_stale(organization_id=org, thread_id=tid, agent_name=agent))


def _row(cat: Any, tid: str, agent: str = _PROBE_AGENT) -> tuple[Any, ...] | None:
    rows = _admin(cat, "SELECT transcript_digest, updated_at, session_json::text "
                       "FROM maf_agent_session WHERE thread_id = :t AND agent_name = :a",
                  t=tid, a=agent)
    return tuple(rows[0]) if rows else None


def _browser() -> list[dict[str, str]]:
    """The browser's copy of the thread before turn N+3, with the reply."""
    return [{"role": "user", "content": _LIST}, {"role": "assistant", "content": _LISTED},
            {"role": "user", "content": _MOVE}, {"role": "assistant", "content": _CARD},
            {"role": "user", "content": _AFTER},
            {"role": "assistant", "content": _AFTER_ANSWER},
            {"role": "assistant", "content": _REPLY}]


def _case_b(org: str, tid: str, *, mark: bool) -> None:
    """Case B as production writes it, up to the user row of turn N+3.

    Turn N-1 saves a session. Turn N draws the card, and the park saves no
    session. Turn N+2 saves a session through its own user row. Then the
    continuation starts: the prologue marks (or not), and the fold writes
    its reply with no user row before it.
    """
    _chat(org, tid)
    _one_turn(org, tid, _LIST, _LISTED)
    _user_row(org, tid, _MOVE)
    _agent_row(org, tid, _CARD)
    _one_turn(org, tid, _AFTER, _AFTER_ANSWER)
    if mark:
        assert _mark(org, tid) == "marked"
    _agent_row(org, tid, _REPLY)
    _user_row(org, tid, _NEXT)


def _input_texts(turn: Any, monkeypatch) -> list[str]:
    """The model input of turn N+3, through the real compose."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "maf_native_sessions", True)
    payload = {"message": _NEXT, "messages": _browser()}
    run_input, _ = executor._compose_maf_run(
        "probe", "run-n3", payload, {}, native=True, session_turn=turn,
    )
    return [m.text for m in run_input]


def test_r8_without_the_mark_the_next_turn_is_a_stale_hit(
    graph_as_app, store_log, monkeypatch,
) -> None:
    """The defect, on the real store: the load is a hit, and the input is
    the current turn only. The reply of the continuation is not in it."""
    org, tid = graph_as_app.org_a, _tid()
    _case_b(org, tid, mark=False)
    turn = _begin(tid, _NEXT, org=org, browser=_browser())
    assert turn.loaded and turn.outcome == "hit"
    held = [m.text for m in turn.session.state[store.HISTORY_SOURCE_ID]["messages"]]
    assert _REPLY not in held
    assert _input_texts(turn, monkeypatch) == [_NEXT]


def test_r8_the_mark_makes_the_next_turn_read_the_text_history(
    graph_as_app, store_log, monkeypatch,
) -> None:
    org, tid = graph_as_app.org_a, _tid()
    _case_b(org, tid, mark=True)
    stored = _row(graph_as_app, tid)
    assert stored is not None and stored[0] == store.STALE_DIGEST
    turn = _begin(tid, _NEXT, org=org, browser=_browser())
    assert turn.outcome == "digest_drop" and not turn.loaded
    loads = store_log.named("native_session.load")
    assert (loads[-1]["outcome"], loads[-1]["reason"]) == ("digest_drop", "resume_stale")
    assert turn.session.state.get(store.HISTORY_SOURCE_ID) is None, "a fresh session"
    texts = _input_texts(turn, monkeypatch)
    assert _REPLY in texts and texts[-1] == _NEXT
    # The turn saves a fresh session, and the turn after it hits again.
    turn.session.state[store.HISTORY_SOURCE_ID] = {"messages": [
        _message("user", _NEXT), _message("assistant", "More work.")]}
    assert _finish(turn) == "saved"
    assert _row(graph_as_app, tid)[0] != store.STALE_DIGEST
    _agent_row(org, tid, "More work.")
    _user_row(org, tid, "ok")
    assert _begin(tid, "ok", org=org).outcome == "hit"


def test_r8_org_b_cannot_mark_org_a(graph_as_app, store_log) -> None:
    from acb_graph import tenant_session
    from sqlalchemy import text

    org_a, org_b = graph_as_app.org_a, graph_as_app.org_b
    tid = _tid()
    _chat(org_a, tid)
    _one_turn(org_a, tid, _LIST, _LISTED)
    _one_turn(org_a, tid, _AFTER, _AFTER_ANSWER, agent=_AGENT_Y)
    before = _row(graph_as_app, tid)
    other = _row(graph_as_app, tid, _AGENT_Y)
    assert before is not None and other is not None
    assert _mark(org_b, tid) == "unchanged"
    assert _row(graph_as_app, tid) == before
    # RLS is the first lock: org B's session that names org A changes nothing.
    with tenant_session(org_b) as s:
        hit = s.execute(text(store._MARK_STALE_SQL), {
            "org": org_a, "tid": tid, "agent": _PROBE_AGENT,
            "stale": store.STALE_DIGEST}).first()
    assert hit is None and _row(graph_as_app, tid) == before
    # The mark of org A touches its own key only, never agent Y's session.
    assert _mark(org_a, tid) == "marked"
    assert _row(graph_as_app, tid)[0] == store.STALE_DIGEST
    assert _row(graph_as_app, tid, _AGENT_Y) == other


def test_r8_a_key_with_no_row_makes_no_row(graph_as_app, store_log) -> None:
    org, tid = graph_as_app.org_a, _tid()
    _chat(org, tid)
    _user_row(org, tid, _MOVE)
    assert _mark(org, tid) == "unchanged"
    assert _mark(org, f"{tid}-no-chat") == "unchanged"
    count = _admin(graph_as_app, "SELECT count(*) FROM maf_agent_session "
                                 "WHERE thread_id IN (:t, :u)", t=tid, u=f"{tid}-no-chat")
    assert count[0][0] == 0


def test_r8_a_second_mark_is_a_no_op(graph_as_app, store_log) -> None:
    org, tid = graph_as_app.org_a, _tid()
    _chat(org, tid)
    _one_turn(org, tid, _LIST, _LISTED)
    assert _mark(org, tid) == "marked"
    first = _row(graph_as_app, tid)
    assert _mark(org, tid) == "unchanged"
    assert _row(graph_as_app, tid) == first
    assert [f["outcome"] for f in store_log.named("native_session.mark_stale")] == [
        "marked", "unchanged"]
