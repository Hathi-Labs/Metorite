"""HRD-22b (task 3294): the second witness of a late approve, on a REAL database (R8).

Code: ``orchestrator.pending_ask.twin_approved_since`` over ``chat_pending_ask``
(the statement ``_TWIN_APPROVED_SQL``). Hermetic twin: the HRD-22b cases of
``test_late_approve_grant.py``. Run on a PRIVATE ladder database as
``test_member_action_r8.py`` says.

a. A confirmation row of the same member, in ANOTHER thread, with the same
   title, detail and context, answered ``APPROVE`` after this row was asked,
   is a twin that ran: True.
b. No twin is found for: an earlier answer (before ``asked_at``), a REJECT, a
   row still open or parked, another title, detail or context, another
   member, another org, and the row itself.
c. A row that cannot be compared (no title, truncated, no ``asked_at``) reads
   True, the fail-closed side.
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import uuid
from typing import Any

import pytest

pending_ask = pytest.importorskip("orchestrator.pending_ask")
pytest.importorskip("sqlalchemy")

from sqlalchemy import text  # noqa: E402

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_pending_ask_store import (  # noqa: E402, F401
    _ALICE,
    _CAROL,
    as_app,
    world,
)

pytestmark = _DB_GATE

@pytest.fixture(autouse=True)
def _clean_rows(world):
    """The ladder database is shared by the tests of a run: each test starts
    with no row of these members, so an old twin never answers for a new one."""
    def wipe() -> None:
        with world.admin_engine.begin() as c:
            c.execute(text(
                "DELETE FROM chat_pending_ask WHERE actor_email = ANY(:who)"),
                {"who": [_ALICE, _CAROL, "dan@pa-a.test"]})
    wipe()
    yield
    wipe()


_TITLE, _DETAIL, _CONTEXT = "Move Neuropeak to Won?", "one deal", "stage: Won"


def _card(rid: str, **over: Any) -> dict[str, Any]:
    return {"request_id": rid, "title": _TITLE, "detail": _DETAIL, "context": _CONTEXT, **over}


def _ask(org: str, tid: str, *, actor: str = _ALICE, payload: dict[str, Any] | None = None) -> str:
    rid = uuid.uuid4().hex
    assert pending_ask.insert_ask(org, {
        "request_id": rid, "thread_id": tid, "actor_email": actor, "kind": "confirmation",
        "question": _TITLE, "payload": payload if payload is not None else _card(rid),
    })
    return rid


def _set(world: Any, org: str, rid: str, state: str, answer: str | None, shift: str) -> None:
    """Put a row in *state* with *answer*, answered *shift* after its ask."""
    with world.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE chat_pending_ask SET state = :s, answer = :a, "
            "answered_at = asked_at + CAST(:d AS interval) "
            "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
            {"s": state, "a": answer, "d": shift, "o": org, "r": rid})


def _row(world: Any, org: str, rid: str) -> dict[str, Any]:
    with world.admin_engine.connect() as c:
        r = c.execute(text(
            "SELECT request_id, thread_id, actor_email, kind, payload, state, answer, "
            "asked_at FROM chat_pending_ask WHERE organization_id = CAST(:o AS uuid) "
            "AND request_id = :r"), {"o": org, "r": rid}).mappings().one()
    return {**r, "asked_at": r["asked_at"].isoformat()}


def _stale(world: Any, org: str, *, thread: str) -> dict[str, Any]:
    """The stale card: parked in *thread*, asked first. Its row is read as the route reads it."""
    rid = _ask(org, thread)
    _set(world, org, rid, "parked", None, "0 seconds")
    return _row(world, org, rid)


def _twin_in(world: Any, org: str, thread: str, **kw: Any) -> str:
    """A twin row that is asked AFTER the stale one, in *thread*."""
    return _ask(org, thread, **kw)


def test_r8_an_approved_twin_in_another_thread_is_found(as_app):
    w = as_app
    stale = _stale(w, w.org_a, thread=w.t_alice)
    other = f"pa-a2-{uuid.uuid4().hex[:8]}"
    with w.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, organization_id) "
            "VALUES (:id, :u, 'orchestrator', 'b chat', :o)"),
            {"id": other, "u": _ALICE, "o": w.org_a})
    twin = _twin_in(w, w.org_a, other)
    _set(w, w.org_a, twin, "answered", "approve ", "5 seconds")
    assert pending_ask.twin_approved_since(w.org_a, _ALICE.upper(), stale) is True


def test_r8_an_approve_with_edits_is_a_twin_that_ran(as_app):
    """Review P1: the edit card (EAC-1) answers ``APPROVE {...}``, and the act
    ran with the edited values. The row keeps that text, not the bare word."""
    w = as_app
    stale = _stale(w, w.org_a, thread=w.t_alice)
    twin = _twin_in(w, w.org_a, w.t_alice)
    _set(w, w.org_a, twin, "answered", 'APPROVE {"edit":{"title":"New"}}', "5 seconds")
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, stale) is True


@pytest.mark.parametrize("state,answer,shift", [
    ("answered", "REJECT", "5 seconds"),
    ("answered", "APPROVER", "5 seconds"),    # a word that only starts like APPROVE
    ("parked", None, "5 seconds"),
    ("open", None, "5 seconds"),
    ("closed", None, "5 seconds"),
    ("answered", "APPROVE", "-5 seconds"),   # answered BEFORE the stale row was asked
])
def test_r8_no_twin_is_found_for_a_row_that_did_not_run(as_app, state, answer, shift):
    w = as_app
    stale = _stale(w, w.org_a, thread=w.t_alice)
    twin = _twin_in(w, w.org_a, w.t_alice)
    _set(w, w.org_a, twin, state, answer, shift)
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, stale) is False


@pytest.mark.parametrize("over", [
    {"title": "Move Orbit to Won?"}, {"detail": "two deals"}, {"context": "stage: Lost"},
])
def test_r8_another_card_is_no_twin(as_app, over):
    w = as_app
    stale = _stale(w, w.org_a, thread=w.t_alice)
    rid = uuid.uuid4().hex
    other = _ask(w.org_a, w.t_alice, payload=_card(rid, **over))
    _set(w, w.org_a, other, "answered", "APPROVE", "5 seconds")
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, stale) is False


def test_r8_another_member_another_org_and_the_row_itself_are_no_twin(as_app):
    w = as_app
    stale = _stale(w, w.org_a, thread=w.t_alice)
    # The row itself, answered later (the late tap moved it).
    _set(w, w.org_a, stale["request_id"], "answered", "APPROVE", "5 seconds")
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, stale) is False
    # Another member, with the same words, in the same org.
    with w.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', :o) ON CONFLICT DO NOTHING"),
            {"e": "dan@pa-a.test", "o": w.org_a})
    dan = _ask(w.org_a, w.t_alice, actor="dan@pa-a.test")
    _set(w, w.org_a, dan, "answered", "APPROVE", "5 seconds")
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, stale) is False
    # The same words in another org.
    theirs = _ask(w.org_b, w.t_carol, actor=_CAROL)
    _set(w, w.org_b, theirs, "answered", "APPROVE", "5 seconds")
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, stale) is False
    assert pending_ask.twin_approved_since(w.org_b, _ALICE, stale) is False


def test_r8_a_row_that_cannot_be_compared_reads_true(as_app):
    w = as_app
    stale = _stale(w, w.org_a, thread=w.t_alice)
    assert pending_ask.twin_approved_since(
        w.org_a, _ALICE, {**stale, "payload": {"request_id": "x", "truncated": True}}) is True
    assert pending_ask.twin_approved_since(
        w.org_a, _ALICE, {**stale, "payload": {"request_id": "x"}}) is True
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, {**stale, "asked_at": None}) is True
    assert pending_ask.twin_approved_since(w.org_a, _ALICE, {**stale, "payload": None}) is True
