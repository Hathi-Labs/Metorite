"""RESUME-2a: a late Approve runs the stored call once, on a REAL database (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.4 item 2, case B,
and the RESUME-2a row of §4.12.7 (task 3261). The hermetic half is
``test_resume_continuation.py``.

The rows of ``chat_pending_ask`` run as real SQL on the phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role (the
fixtures of ``test_pending_ask_store.py``). The late-approve records run on a
PRIVATE real Redis, through the real ``TenantRedis`` wrapper. The card is a
real ``request_confirmation`` that parks and stores its call (RESUME-1a). The
late answer is the real ``respond_user_input`` route, with the real claim.
The continuation is the real executor prologue (``_resume_prologue``) with
the run's own wrapped stand-in tool. Only the stream relay is the fake of
``test_chat_deploy_recovery.py``, and the run route that starts the
continuation is a stand-in that calls the prologue: the stand-in CRM tool
counts each write as one PATCH, and no third party is reached.

R7, each with the mutation that turns it red:

* ``test_the_production_sequence_writes_once_with_one_card`` — the replay of
  2026-11-14. One card, one write, 200 ``resumed``, no 409. Skip the yes, or
  call the raw function, and a second card shows.
* ``test_two_claims_at_once_and_exactly_one_wins`` — the claim is ONE
  statement. Split it into a read and a move, and both win.
* ``test_another_member_thread_or_org_runs_nothing`` — drop the actor or the
  thread predicate of the claim.
* ``test_a_changed_deal_makes_no_write_and_draws_a_new_card`` — compare the
  act keys only.
* ``test_twin_cards_with_both_flags_never_write_twice`` — drop the SPENT read
  of the yes.
* ``test_twin_rows_approved_at_the_same_moment_write_once`` (review, P1-A
  (d)) — split the ONE ``SET … GET`` of ``late_approve._spend`` into a GET
  and a SET, and both continuations write.
* ``test_the_spent_mark_outlives_the_twin_row`` (review, P1-A (c)) — drop the
  row life from ``late_approve.spent_ttl_s``.
* ``test_a_twin_claim_in_the_window_of_a_drop_still_writes_once`` (follow-up,
  P2-1) — put back the GET and then the DELETE in ``late_approve.drop_offer``,
  and the twin writes a second time.
* ``test_flag_off_the_answer_is_as_before`` (review, P1-A (a)) — RESUME with
  ``CHAT_LATE_APPROVE_ORGS`` off resumes, so the twin guard has no mark.
* ``test_a_refusal_after_the_claim_gives_the_row_back`` (review, P2) — drop
  ``_give_back_claim``, or a predicate of ``pending_ask._RESTORE_SQL``.

RESUME-2b (task 3460), case A. These tests run the stream relay on the
PRIVATE real Redis too, and the real run route with a stand-in executor that
writes its events to that stream. The fold, the mint and the rows are real.

* ``test_case_a_goes_on_in_the_same_reply`` — a parked turn that is the last
  turn goes on in ONE reply row, with the parked run's id, and the stream
  keeps the parked part with no parked end. Reset the stream, or mint a new
  row, and the old words or the one row are gone.
* ``test_the_case_a_checks_on_real_rows`` — ``reply_is_last_turn`` under RLS
  and ``parked_end_of`` on the real stream. Drop the later-row test, the org
  predicate, or the run id test, and a doubt reads as case A.
* ``test_a_lost_parked_end_keeps_the_old_row_and_opens_a_new_reply`` — the
  stream goes between the check and the start. Fall through to a reset, and
  the fold writes the tail over the old words.
* ``test_a_real_park_keeps_the_stream_24h`` (D-RES-7) — the real
  ``pending_ask.park`` and the real drain. Expire with ``STREAM_TTL_SECONDS``
  in ``mark_inactive``, and the life is 1 hour.

``HELD_R8_REDIS_URL`` names a PRIVATE Redis, because the suite runs FLUSHDB.
Never point it at a shared server. With it or the database unset, the suite
skips, and a skip is not a pass. Run::

    docker run -d --rm --name resume2-r8-redis -p 127.0.0.1:16401:6379 redis:7-alpine
    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db> \\
    HELD_R8_REDIS_URL=redis://127.0.0.1:16401/0 \\
        uv run pytest tests/unit/test_resume_continuation_r8.py -v -rs

The tests of RESUME-2b point the stream relay at the same private Redis.
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import json
import os
import threading
import uuid
from typing import Any

import pytest

pending_ask = pytest.importorskip("orchestrator.pending_ask")
stream_relay = pytest.importorskip("orchestrator.stream_relay")
executor = pytest.importorskip("orchestrator.executor")
pytest.importorskip("sqlalchemy")

from acb_skills import late_approve  # noqa: E402
from acb_skills.ask_tools import card_call, request_confirmation  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_chat_deploy_recovery import (  # noqa: E402, F401
    fake_redis,
    liveness,
    no_persist,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_pending_ask_store import (  # noqa: E402, F401
    _ALICE,
    _CAROL,
    as_app,
    world,
)

REDIS_URL_ENV = "HELD_R8_REDIS_URL"
REDIS_URL = os.environ.get(REDIS_URL_ENV, "").strip()

pytestmark = [
    _DB_GATE,
    pytest.mark.skipif(not REDIS_URL, reason=f"{REDIS_URL_ENV} unset: R8 needs a PRIVATE "
                       "real Redis. A skip is not a pass."),
]

_BOB = "bob@pa-a.test"
#: True inside a continuation run of the stand-in run route.
IN_CONT: contextvars.ContextVar[bool] = contextvars.ContextVar("r8_in_cont", default=False)
#: Each PATCH of the stand-in CRM transport, in order.
PATCHES: list[tuple[str, str]] = []
DEALS: dict[str, str] = {}
#: The continuation runs that the stand-in run route started.
STARTED: list[asyncio.Task[Any]] = []
NOTES: list[str] = []
#: Each entry makes the stand-in run route refuse ONE start after the claim.
REFUSE: list[bool] = []


async def update_deal_status(deal_id: str, stage: str, lost_reason: str | None = None) -> str:
    """A stand-in of ``agent-crm`` ``update_deal_status``, with its pinned
    parameters and its annotation: one card, then one PATCH on approve. The
    PATCH goes to a fake transport, never to Zoho."""
    title = DEALS[deal_id]
    ok = await request_confirmation(
        title=f"Move this deal to {stage}?",
        detail=f"{title} → {stage}",
        context=f"deal: {title}\nstage: {stage}",
    )
    if not ok:
        return "not moved"
    PATCHES.append((deal_id, stage))
    return f"{title} moved to {stage}."


update_deal_status.__tool_risk__ = {  # type: ignore[attr-defined]
    "read_only": False, "destructive": True, "idempotent": False,
    "open_world": True, "card_write": True}


def _wrapped(fn: Any) -> Any:
    """The gate wrapper's part that the yes needs: ``card_call``."""

    @functools.wraps(fn)
    async def _agated(*args: Any, **kwargs: Any) -> Any:
        with card_call(fn, args, kwargs):
            return await fn(*args, **kwargs)

    return _agated


class _Tool:
    def __init__(self, func: Any) -> None:
        self.func = func
        self.name = func.__name__


class _Agent:
    def __init__(self) -> None:
        self.default_options = {"tools": [_Tool(_wrapped(update_deal_status))]}


@pytest.fixture
def scene(as_app, liveness, no_persist, monkeypatch):
    """The real rows, the flags on, a short park window, and the stand-in
    run route that starts the real prologue."""
    from acb_common import get_settings
    from gateway.routes import agent as agent_routes

    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
    orgs = f"{as_app.org_a},{as_app.org_b}"
    monkeypatch.setattr(get_settings(), "chat_resume_orgs", orgs)
    # Review, P1-A (a): RESUME needs the late-approve flag too.
    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", orgs)
    monkeypatch.setattr(pending_ask, "park_after_seconds", lambda: 0.05)

    def _no_shared_redis() -> Any:
        raise RuntimeError("this test runs with no Redis: never a shared server")

    # A test that needs Redis calls `_use_redis` with the PRIVATE server.
    monkeypatch.setattr(late_approve, "_redis", _no_shared_redis)

    async def _ok(*_a, **_k):
        return None

    async def _endpoint(req, _request, *, user, background_tasks, _continuation):
        launch = _continuation
        if REFUSE:
            # Review, P2: the run route refuses after the claim (another
            # member's run took the thread in the window).
            from fastapi import HTTPException

            REFUSE.pop()
            raise HTTPException(status_code=409, detail={"error": "run_in_progress"})

        async def _cont() -> None:
            IN_CONT.set(True)
            _bind_run(req.thread_id, user.organization_id, user.email)
            payload: dict[str, Any] = {"message": ""}
            async for _line in executor._resume_prologue(
                    [_Agent()], "orchestrator", launch.as_kwarg(), payload):
                pass
            NOTES.append(payload["message"])

        task = asyncio.create_task(_cont())
        stream_relay._DETACHED_TASKS[req.thread_id] = task
        STARTED.append(task)
        return agent_routes._ContinuationStarted(run_id=req.run_id, thread_id=req.thread_id)

    monkeypatch.setattr(agent_routes, "run_agent_stream_endpoint", _endpoint)
    monkeypatch.setattr(agent_routes, "assert_can_run_agent_in_session", _ok)
    pending_ask._reset_for_tests()
    executor._pending_user_input.clear()
    PATCHES.clear()
    STARTED.clear()
    NOTES.clear()
    REFUSE.clear()
    DEALS.clear()
    DEALS.update({"d-neuro": "Neuropeak", "d-orbit": "Orbit"})
    yield as_app, liveness
    pending_ask._reset_for_tests()
    executor._pending_user_input.clear()


def _bind_run(tid: str, org: str, member: str) -> None:
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant

    executor._stream_relay_thread_id.set(tid)
    bind_tenant(org)
    bind_run_context(user=member, member_verified=True, agent="orchestrator", thread_id=tid)


async def _client():
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    return client


def _use_redis(monkeypatch, client) -> None:
    from acb_common.tenant_redis import TenantRedis

    monkeypatch.setattr(late_approve, "_redis", lambda: TenantRedis(client))


def _cards(r, tid: str) -> list[dict[str, Any]]:
    from tests.unit.test_pending_ask_flow import _events

    return [e["value"] for e in _events(r, tid) if e.get("name") == "confirmation_requested"]


async def _park(r, tid: str, org: str, *, member: str = _ALICE,
                deal_id: str = "d-neuro", stage: str = "Payment Complete") -> str:
    """One chat run on *tid* whose card parks. The request id of its card."""
    from orchestrator.run_liveness import record_instance_run

    r.store[f"cc:runactor:{tid}"] = member
    await record_instance_run(tid, {"agent": "orchestrator", "runId": uuid.uuid4().hex,
                                    "org": org})
    seen = len(_cards(r, tid))

    async def _the_run() -> None:
        _bind_run(tid, org, member)
        await _wrapped(update_deal_status)(deal_id=deal_id, stage=stage)

    run = asyncio.create_task(_the_run())
    stream_relay._DETACHED_TASKS[tid] = run
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await asyncio.wait_for(run, timeout=60)
    assert len(_cards(r, tid)) == seen + 1, "the card showed"
    stream_relay._DETACHED_TASKS.pop(tid, None)
    await r.delete(f"cc:active:{tid}")
    return _cards(r, tid)[-1]["request_id"]


async def _answer(tid: str, rid: str, member: str, org: str, *, answer: str = "APPROVE") -> Any:
    """``POST /agent/respond-input`` as *member* of *org*: the route itself."""
    from acb_auth import UserContext, UserRole
    from fastapi import HTTPException
    from gateway.routes import agent as agent_routes

    req = agent_routes.UserInputResponseRequest(request_id=rid, answer=answer, thread_id=tid)
    user = UserContext(email=member, role=UserRole.EMPLOYEE, organization_id=org)
    try:
        return await agent_routes.respond_user_input(req, user)
    except HTTPException as exc:
        return exc


async def _settle() -> None:
    """Let each continuation run end. A new card that waits is answered
    REJECT, so the run ends and writes nothing."""
    for _ in range(600):
        if all(t.done() for t in STARTED):
            return
        for fut in list(executor._pending_user_input.values()):
            if not fut.done():
                fut.set_result({"answer": "REJECT"})
        await asyncio.sleep(0.01)
    raise AssertionError("a continuation did not end")


def _row(w, org: str, rid: str) -> tuple[Any, ...]:
    with w.admin_engine.connect() as c:
        return tuple(c.execute(text(
            "SELECT state, answer, stored_call IS NULL FROM chat_pending_ask "
            "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
            {"o": org, "r": rid}).one())


def _user_rows(w, tid: str) -> int:
    with w.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM chat_message WHERE session_id = :t AND role = 'user'"),
            {"t": tid}).scalar_one())


# ---------------------------------------------------------------------------

def test_the_production_sequence_writes_once_with_one_card(scene, monkeypatch):
    """The replay of 2026-11-14: one card, a park, a late Approve, one PATCH,
    200 ``resumed``, no 409 and no member turn."""
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            rid = await _park(r, tid, w.org_a)
            assert _row(w, w.org_a, rid)[0] == "parked"
            assert pending_ask.read_stored_call(w.org_a, rid) is not None
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert isinstance(res, dict), res
            assert res["ok"] is True and res["resumed"] is True and res["threadId"] == tid
            await _settle()
            assert PATCHES == [("d-neuro", "Payment Complete")]
            assert len(_cards(r, tid)) == 1, "the member saw ONE card"
            assert _row(w, w.org_a, rid) == ("answered", "APPROVE", True)
            assert NOTES and "Metorite ran the tool" in NOTES[0]
            assert _user_rows(w, tid) == 0, "no member turn"
            again = await _answer(tid, rid, _ALICE, w.org_a)
            assert getattr(again, "status_code", None) == 409
            await _settle()
            assert PATCHES == [("d-neuro", "Payment Complete")], "a second Approve runs nothing"
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_two_claims_at_once_and_exactly_one_wins(scene):
    w, r = scene
    tid = w.t_alice
    rid = asyncio.run(_park(r, tid, w.org_a))
    results: list[Any] = []
    gate = threading.Barrier(2)

    def _claim() -> None:
        gate.wait()
        results.append(pending_ask.claim_stored_call(
            w.org_a, rid, member=_ALICE, thread_id=tid, answer="APPROVE"))

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    won = [x for x in results if x is not None]
    assert len(results) == 2 and len(won) == 1, results
    assert won[0]["tool"] == "update_deal_status" and won[0]["arguments"]["deal_id"] == "d-neuro"
    assert _row(w, w.org_a, rid) == ("answered", "APPROVE", True)


def test_another_member_thread_or_org_runs_nothing(scene, monkeypatch):
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            from gateway.routes import agent as agent_routes

            rid = await _park(r, tid, w.org_a)
            real_room = agent_routes._resolve_room

            class _SharedRoom:
                can_send = True
                is_shared = True

            # Bob may send in Alice's shared room of org A, and the card asked
            # Alice. The room alone is not enough.
            monkeypatch.setattr(agent_routes, "_resolve_room", lambda *_a, **_k: _SharedRoom())
            bob = await _answer(tid, rid, _BOB, w.org_a)
            assert getattr(bob, "status_code", None) == 409
            monkeypatch.setattr(agent_routes, "_resolve_room", real_room)
            # Another thread, and Carol of org B: the real room gate.
            other = await _answer("t-other-thread", rid, _ALICE, w.org_a)
            assert getattr(other, "status_code", None) in (403, 409)
            carol = await _answer(tid, rid, _CAROL, w.org_b)
            assert getattr(carol, "status_code", None) in (403, 409)
            assert STARTED == [] and PATCHES == []
            assert _row(w, w.org_a, rid)[0] == "parked", "nobody moved the row"
            # The claim itself refuses each of them too.
            for member, thread, org in ((_BOB, tid, w.org_a), (_ALICE, "t-x", w.org_a),
                                        (_ALICE, tid, w.org_b)):
                assert pending_ask.claim_stored_call(
                    org, rid, member=member, thread_id=thread, answer="APPROVE") is None
            assert _row(w, w.org_a, rid)[0] == "parked"
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_a_changed_deal_makes_no_write_and_draws_a_new_card(scene, monkeypatch):
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            rid = await _park(r, tid, w.org_a)
            DEALS["d-neuro"] = "Neuropeak Labs"  # the record changed after the card
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert isinstance(res, dict) and res["resumed"] is True
            await _settle()
            assert PATCHES == [], "no PATCH"
            assert len(_cards(r, tid)) == 2, "a new card showed"
            assert "The record changed after the card was made" in NOTES[0]
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_an_expired_row_runs_nothing(scene, monkeypatch):
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            rid = await _park(r, tid, w.org_a)
            with w.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE chat_pending_ask SET expires_at = now() - interval '1 minute' "
                    "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
                    {"o": w.org_a, "r": rid})
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert getattr(res, "status_code", None) == 409
            assert pending_ask.claim_stored_call(
                w.org_a, rid, member=_ALICE, thread_id=tid, answer="APPROVE") is None
            assert STARTED == [] and PATCHES == []
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


@pytest.mark.parametrize("flag", ["chat_resume_orgs", "chat_late_approve_orgs"])
def test_flag_off_the_answer_is_as_before(flag, scene, monkeypatch):
    """Either flag off: no stored call is read, nothing resumes and no PATCH
    goes out. The answer is the 409 ``run_restarted`` of before, so the new
    run draws its card (review, P1-A (a))."""
    from acb_common import get_settings

    w, r = scene
    tid = w.t_alice
    reads: list[str] = []
    real = pending_ask.read_stored_call

    def _spy(org, rid):
        reads.append(rid)
        return real(org, rid)

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            rid = await _park(r, tid, w.org_a)
            monkeypatch.setattr(get_settings(), flag, "")
            monkeypatch.setattr(pending_ask, "read_stored_call", _spy)
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert getattr(res, "status_code", None) == 409
            assert res.detail["error"] == "run_restarted"
            assert reads == [] and STARTED == [] and PATCHES == []
            assert _row(w, w.org_a, rid)[:2] == ("answered", "APPROVE")
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_twin_cards_with_both_flags_never_write_twice(scene, monkeypatch):
    """Both flags on. Two parked cards of ONE act: the first late Approve
    writes and marks the act SPENT. The twin's late Approve writes nothing:
    its new card shows instead."""
    from acb_common import get_settings

    w, r = scene
    tid = w.t_alice
    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", w.org_a)

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            first = await _park(r, tid, w.org_a)
            twin = await _park(r, tid, w.org_a)
            res = await _answer(tid, first, _ALICE, w.org_a)
            assert isinstance(res, dict) and res["resumed"] is True
            await _settle()
            assert PATCHES == [("d-neuro", "Payment Complete")]
            res2 = await _answer(tid, twin, _ALICE, w.org_a)
            assert isinstance(res2, dict) and res2["resumed"] is True
            await _settle()
            assert PATCHES == [("d-neuro", "Payment Complete")], "no second write"
            assert "already ran in this chat" in NOTES[-1]
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Review of RESUME-2a: P1-A (c) and (d), and P2
# ---------------------------------------------------------------------------

class _Meet:
    """The real ``TenantRedis``, with ONE meeting point: in a continuation
    run, the first op of each run on the offer key waits until both runs
    reached it. So the two stored-call yeses race in the worst order. The
    real code then still writes once, because its mark and its test are ONE
    ``SET … GET``."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._seen: set[int] = set()
        self._here = 0
        self._both = asyncio.Event()

    async def _meet(self, k: Any) -> None:
        task = asyncio.current_task()
        if (not IN_CONT.get() or getattr(k, "namespace", "") != late_approve.OFFER_NAMESPACE
                or task is None or id(task) in self._seen):
            return
        self._seen.add(id(task))
        self._here += 1
        if self._here >= 2:
            self._both.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._both.wait(), timeout=10)

    async def get(self, k: Any) -> Any:
        await self._meet(k)
        return await self._inner.get(k)

    async def set(self, k: Any, value: Any, **kwargs: Any) -> Any:
        await self._meet(k)
        return await self._inner.set(k, value, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_twin_rows_approved_at_the_same_moment_write_once(scene, monkeypatch):
    """P1-A (d). Two parked twin cards of ONE act, approved at the same
    moment. Each claim takes its own row, and two continuations run at once.
    Their yeses meet at the offer key, and exactly one writes. The other
    shows its card. Mutation: a GET, then a SET in ``late_approve._spend`` ->
    both read no mark -> two PATCHes -> red."""
    from acb_common.tenant_redis import TenantRedis

    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        try:
            _use_redis(monkeypatch, client)
            first = await _park(r, tid, w.org_a)
            twin = await _park(r, tid, w.org_a)
            meet = _Meet(TenantRedis(client))
            monkeypatch.setattr(late_approve, "_redis", lambda: meet)
            a, b = await asyncio.gather(
                _answer(tid, first, _ALICE, w.org_a), _answer(tid, twin, _ALICE, w.org_a))
            assert isinstance(a, dict) and a["resumed"] is True, a
            assert isinstance(b, dict) and b["resumed"] is True, b
            assert len(STARTED) == 2, "two continuations ran"
            await _settle()
            assert meet._here == 2, "non-vacuity: both yeses met at the offer key"
            assert PATCHES == [("d-neuro", "Payment Complete")], "ONE write"
            assert len(_cards(r, tid)) == 3, "two parked cards, and one new card"
            assert sum("already ran in this chat" in n for n in NOTES) == 1
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


class _DropWindow:
    """The real ``TenantRedis``, with ONE window in a route's drop of the
    offer (outside a continuation run): the hook runs after a GET of the
    offer key, or just before the one atomic step of ``delete_unless``. A
    continuation's first write of the mark waits for :attr:`release`, so it
    can only land inside that window."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.hook: Any = None
        self.fired = 0
        self.release = asyncio.Event()

    async def set(self, k: Any, value: Any, **kwargs: Any) -> Any:
        if (IN_CONT.get() and getattr(k, "namespace", "") == late_approve.OFFER_NAMESPACE
                and not self.release.is_set()):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.release.wait(), timeout=20)
        return await self._inner.set(k, value, **kwargs)

    async def _gap(self, k: Any) -> None:
        if (self.hook is None or IN_CONT.get()
                or getattr(k, "namespace", "") != late_approve.OFFER_NAMESPACE):
            return
        hook, self.hook = self.hook, None
        self.fired += 1
        await hook()

    async def get(self, k: Any) -> Any:
        out = await self._inner.get(k)
        await self._gap(k)
        return out

    async def delete_unless(self, k: Any, value: str) -> Any:
        await self._gap(k)
        return await self._inner.delete_unless(k, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_a_twin_claim_in_the_window_of_a_drop_still_writes_once(scene, monkeypatch):
    """RESUME-2a follow-up, P2-1, end to end. Twin rows A and B of one act,
    approved seconds apart. B's ``_resume_from_row`` drops its offer, and
    A's continuation runs its claim in the window of that drop (SPENT). The
    drop keeps the mark, so B's continuation reads SPENT, shows its card,
    and writes nothing: ONE PATCH. Mutation: put back the GET and then the
    DELETE in ``late_approve.drop_offer`` -> the mark goes -> two PATCHes
    -> red."""
    from acb_common.tenant_redis import TenantRedis

    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        try:
            _use_redis(monkeypatch, client)
            first = await _park(r, tid, w.org_a)
            twin = await _park(r, tid, w.org_a)
            win = _DropWindow(TenantRedis(client))
            monkeypatch.setattr(late_approve, "_redis", lambda: win)
            a = await _answer(tid, first, _ALICE, w.org_a)
            assert isinstance(a, dict) and a["resumed"] is True, a
            # A's continuation waits at its claim. B's drop opens the window,
            # and A's claim and write land in it.
            async def _a_claims_in_the_window() -> None:
                win.release.set()
                await _settle()

            win.hook = _a_claims_in_the_window
            b = await _answer(tid, twin, _ALICE, w.org_a)
            assert isinstance(b, dict) and b["resumed"] is True, b
            assert win.fired == 1, "non-vacuity: A's claim landed in B's drop"
            assert PATCHES == [("d-neuro", "Payment Complete")], "A wrote in the window"
            await _settle()
            assert len(STARTED) == 2, "two continuations ran"
            assert PATCHES == [("d-neuro", "Payment Complete")], "ONE write"
            assert len(_cards(r, tid)) == 3, "two parked cards, and one new card"
            assert "Metorite ran the tool" in NOTES[0]
            assert "already ran in this chat" in NOTES[1]
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_the_spent_mark_outlives_the_twin_row(scene, monkeypatch):
    """P1-A (c). After the first card writes, the SPENT mark on the real
    Redis lives at least as long as the twin row that still waits, so the
    twin never finds it gone. Mutation: drop the row life from
    ``spent_ttl_s`` -> a TTL of 1800 s against a row of 7 days -> red."""
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            first = await _park(r, tid, w.org_a)
            twin = await _park(r, tid, w.org_a)
            res = await _answer(tid, first, _ALICE, w.org_a)
            assert isinstance(res, dict) and res["resumed"] is True
            await _settle()
            assert PATCHES == [("d-neuro", "Payment Complete")]
            spent = [k for k in await client.keys(f"cc:{w.org_a}:{late_approve.OFFER_NAMESPACE}:*")
                     if await client.get(k) == late_approve._SPENT]
            assert len(spent) == 1, "non-vacuity: one SPENT mark"
            ttl = await client.ttl(spent[0])
            with w.admin_engine.connect() as c:
                left = float(c.execute(text(
                    "SELECT EXTRACT(EPOCH FROM expires_at - now()) FROM chat_pending_ask "
                    "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
                    {"o": w.org_a, "r": twin}).scalar_one())
            assert ttl >= left - 5, (ttl, left)
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_a_refusal_after_the_claim_gives_the_row_back(scene, monkeypatch):
    """P2 on the real rows. The run route refuses once after the claim. The
    real ``restore_stored_call`` puts the row back to ``parked`` with its
    stored call, so a second Approve resumes it and writes once. The give-back
    of another member, another thread and another org moves nothing.
    Mutation: drop ``_give_back_claim`` -> the second Approve is the plain 409
    and nothing writes -> red."""
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            rid = await _park(r, tid, w.org_a)
            REFUSE.append(True)
            refused = await _answer(tid, rid, _ALICE, w.org_a)
            assert getattr(refused, "status_code", None) == 409, refused
            assert _row(w, w.org_a, rid) == ("parked", None, False), "the row waits again"
            stored = pending_ask.read_stored_call(w.org_a, rid)
            assert stored is not None and stored["arguments"]["deal_id"] == "d-neuro"
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert isinstance(res, dict) and res["resumed"] is True, res
            await _settle()
            assert PATCHES == [("d-neuro", "Payment Complete")]
            assert _row(w, w.org_a, rid) == ("answered", "APPROVE", True)
            # The give-back keeps each predicate of the claim.
            for member, thread, org in ((_BOB, tid, w.org_a), (_ALICE, "t-x", w.org_a),
                                        (_ALICE, tid, w.org_b)):
                assert pending_ask.restore_stored_call(
                    org, rid, member=member, thread_id=thread, stored=stored) is False
            assert _row(w, w.org_a, rid) == ("answered", "APPROVE", True)
            assert pending_ask.restore_stored_call(
                w.org_a, rid, member=_ALICE, thread_id=tid, stored=stored) is True
            assert pending_ask.restore_stored_call(
                w.org_a, rid, member=_ALICE, thread_id=tid, stored=stored) is False, "once"
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# RESUME-2b (task 3460): case A, the same reply, on the real stream and rows
# ---------------------------------------------------------------------------

_TITLE_A = "Move this deal to Payment Complete?"
#: The events of each stand-in continuation run, by thread.
RUNS: dict[str, list[dict[str, Any]]] = {}


@pytest.fixture
def relay_scene(as_app, monkeypatch):
    """The flags on, and the real run route with stand-ins for the agent
    check, the memory and the executor. The relay and the late-approve
    records go to the PRIVATE Redis that :func:`_real_relay` binds."""
    import gateway.memory_preread as mp
    from acb_common import get_settings
    from acb_skills import egress
    from gateway.routes import agent as agent_routes
    from orchestrator import run_liveness

    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
    orgs = f"{as_app.org_a},{as_app.org_b}"
    monkeypatch.setattr(get_settings(), "chat_resume_orgs", orgs)
    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", orgs)
    monkeypatch.setattr(stream_relay, "_DETACHED_TASKS", {})
    monkeypatch.setattr(stream_relay, "_LOCAL_CONTROL_HANDLERS", {})
    monkeypatch.setattr(stream_relay, "_CONTROL_LISTENERS", {})
    monkeypatch.setattr(stream_relay, "_PARKED_STREAMS", {})
    monkeypatch.setattr(stream_relay, "CONTROL_ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(run_liveness, "_LOCAL_RUNS", set())
    monkeypatch.setattr(run_liveness, "ensure_heartbeat", lambda: None)
    monkeypatch.setattr(run_liveness, "_ON_INTERRUPTED", None)

    async def _ok(*_a, **_k):
        return None

    async def _no_mail(*_a, **_k):
        return False

    async def _memory(**_kw):
        return ""

    def _run_agent_stream(agent_name, payload, *, run_id=None, thread_id=None,
                          resume_call=None, **_kw):
        async def _gen():
            # The executor tees each event to the relay (`_sse`), so the
            # stand-in pushes each one too.
            rid = (resume_call or {}).get("request_id")
            events = [
                {"type": "RUN_STARTED", "runId": run_id, "threadId": thread_id},
                {"type": "CUSTOM", "name": "confirmation_resolved",
                 "value": {"request_id": rid, "answer": "APPROVE"}},
                {"type": "TOOL_CALL_START", "toolCallId": "call_abc",
                 "toolCallName": "update_deal_status"},
                {"type": "TOOL_CALL_RESULT", "toolCallId": "call_abc",
                 "content": "Neuropeak moved to Payment Complete.", "success": True},
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": "seg-2",
                 "delta": "The deal moved."},
                {"type": "RUN_FINISHED", "threadId": thread_id},
            ]
            RUNS.setdefault(thread_id, []).append({"run_id": run_id, "resume": resume_call})
            for evt in events:
                await stream_relay.push_event(thread_id, evt)
                yield f"data: {json.dumps(evt)}\n\n"
        return _gen()

    monkeypatch.setattr(agent_routes, "assert_can_run_agent_in_session", _ok)
    monkeypatch.setattr(agent_routes, "_prepare_if_new_thread", _ok)
    monkeypatch.setattr(agent_routes, "_apply_turn_decision", _ok)
    monkeypatch.setattr(agent_routes, "_address_agent", lambda req, room, org=None: "orchestrator")
    monkeypatch.setattr(agent_routes, "_extract_run_memory", _ok)
    monkeypatch.setattr(mp, "memory_preread", _memory)
    monkeypatch.setattr(egress, "run_holds_mail", _no_mail)
    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    pending_ask._reset_for_tests()
    RUNS.clear()
    yield as_app
    pending_ask._reset_for_tests()


def _real_relay(monkeypatch, client) -> None:
    """The stream relay, the run liveness, the run cap and the late-approve
    records all on the PRIVATE real Redis."""
    async def _get_client():
        return client

    monkeypatch.setattr(stream_relay, "_get_client", _get_client)
    _use_redis(monkeypatch, client)


def _new_chat(w, org: str, member: str = _ALICE) -> str:
    tid = f"r2b-{uuid.uuid4().hex[:10]}"
    with w.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, organization_id) "
            "VALUES (:id, :u, 'orchestrator', 'a chat', :o)"), {"id": tid, "u": member, "o": org})
    return tid


def _chat_row(w, tid: str, org: str, mid: str, *, role: str, content: str, ts: int,
              kind: str) -> None:
    with w.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "  organization_id, author_email, author_kind) "
            "VALUES (:id, :s, :r, :c, :t, CAST(:o AS uuid), :a, :k)"),
            {"id": mid, "s": tid, "r": role, "c": content, "t": ts, "o": org,
             "a": _ALICE if kind == "human" else "orchestrator", "k": kind})


async def _parked_turn(w, tid: str, org: str) -> tuple[str, str, str]:
    """A turn whose card parked, as the real run leaves it: the member's
    turn, the parked stream, the reply row folded at the park, and the
    parked row with its stored call. ``(request id, run id, reply id)``."""
    import time

    from gateway.chat_fold import persist_final_assistant_message
    from gateway.routes import agent as agent_routes

    rid, run_id, mid = uuid.uuid4().hex, f"run-{uuid.uuid4().hex[:8]}", f"m-{uuid.uuid4().hex[:8]}"
    _chat_row(w, tid, org, f"u-{uuid.uuid4().hex[:8]}", role="user",
              content="Move Neuropeak to Payment Complete.",
              ts=int(time.time() * 1000) - 60_000, kind="human")
    agent_routes._mint_run_row(tid, mid, member=_ALICE, agent_name="orchestrator",
                               organization_id=org)
    for evt in (
        {"type": "RUN_STARTED", "runId": run_id, "threadId": tid},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "seg-1", "delta": "Let me move it."},
        {"type": "TOOL_CALL_START", "toolCallId": "call_abc",
         "toolCallName": "update_deal_status"},
        {"type": "CUSTOM", "name": "confirmation_requested",
         "value": {"request_id": rid, "title": _TITLE_A}},
        {"type": "RUN_FINISHED", "threadId": tid, "parked": True},
    ):
        await stream_relay.push_event(tid, evt)
    folded = await persist_final_assistant_message(
        tid, mid, user_id=_ALICE, agent_name="orchestrator", run_id=run_id,
        organization_id=org, expect_run_id=run_id, holds_mail=False)
    assert folded is not None and "Let me move it." in folded["content"]
    stored = {"v": 1, "kind": "confirmation", "tool": "update_deal_status",
              "module": "agent:orchestrator", "agent": "orchestrator",
              "arguments": {"deal_id": "d-neuro", "stage": "Payment Complete"},
              "call_id": "call_abc", "card_text": "x", "act_keys": [], "digest": "d",
              "message_id": mid}
    assert pending_ask.insert_ask(org, {
        "request_id": rid, "thread_id": tid, "run_id": run_id, "actor_email": _ALICE,
        "agent_name": "orchestrator", "kind": "confirmation", "question": _TITLE_A,
        "payload": {"request_id": rid, "title": _TITLE_A}, "stored_call": stored})
    assert pending_ask.move_ask(org, rid, to="parked", from_states=("open",)) is not None
    return rid, run_id, mid


async def _run_ends(tid: str) -> None:
    for _ in range(500):
        task = stream_relay._DETACHED_TASKS.get(tid)
        if task is None or task.done():
            if task is not None:
                with contextlib.suppress(BaseException):
                    await task
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the continuation did not end")


async def _stream(client, tid: str) -> list[dict[str, Any]]:
    return [json.loads(f["event"]) for _eid, f in await client.xrange(f"cc:stream:{tid}")]


def _assistant_rows(w, tid: str) -> list[Any]:
    with w.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT id, content, tool_events, custom_events FROM chat_message "
            "WHERE session_id = :t AND role = 'assistant' ORDER BY timestamp_ms, id"),
            {"t": tid}))


def test_case_a_goes_on_in_the_same_reply(relay_scene, monkeypatch):
    """The production sequence of case A on the real stream and rows: one
    reply row, the parked run's id, the parked part kept, and no parked end."""
    w = relay_scene

    async def go() -> None:
        client = await _client()
        _real_relay(monkeypatch, client)
        try:
            tid = _new_chat(w, w.org_a)
            rid, run_id, mid = await _parked_turn(w, tid, w.org_a)
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert isinstance(res, dict), res
            assert res["resumed"] is True and res["sameReply"] is True, res
            assert res["runId"] == run_id and res["messageId"] == mid
            await _run_ends(tid)
            assert [r["run_id"] for r in RUNS[tid]] == [run_id]
            assert RUNS[tid][0]["resume"]["request_id"] == rid
            events = await _stream(client, tid)
            assert events[0]["type"] == "RUN_STARTED" and events[0]["runId"] == run_id
            assert not [e for e in events if e.get("parked") is True], "the parked end went"
            texts = [e.get("delta") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
            assert texts == ["Let me move it.", "The deal moved."], "no reset"
            assert events[-1]["type"] == "RUN_FINISHED"
            rows = _assistant_rows(w, tid)
            assert [r[0] for r in rows] == [mid], "ONE reply row"
            assert "Let me move it." in rows[0][1] and "The deal moved." in rows[0][1]
            tools = rows[0][2]
            assert [(t["id"], t["status"]) for t in tools] == [("call_abc", "done")]
            names = [e["name"] for e in rows[0][3]]
            assert "confirmation_requested" in names and "confirmation_resolved" in names
            assert _user_rows(w, tid) == 1, "no member turn"
            assert _row(w, w.org_a, rid) == ("answered", "APPROVE", True)
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_the_case_a_checks_on_real_rows(relay_scene, monkeypatch):
    """``reply_is_last_turn`` under RLS, and ``parked_end_of`` on the real
    stream, through ``_same_reply_of``."""
    import time

    from gateway.routes import agent as agent_routes
    from gateway.routes.chat import reply_is_last_turn

    w = relay_scene

    async def go() -> None:
        client = await _client()
        _real_relay(monkeypatch, client)
        try:
            tid = _new_chat(w, w.org_a)
            rid, run_id, mid = await _parked_turn(w, tid, w.org_a)
            row = pending_ask.read_ask(w.org_a, rid)
            stored = pending_ask.read_stored_call(w.org_a, rid)
            assert stored is not None and stored["message_id"] == mid
            same = agent_routes._same_reply_of
            assert reply_is_last_turn(tid, mid, organization_id=w.org_a) is True
            assert reply_is_last_turn(tid, mid, organization_id=w.org_b) is False, "RLS"
            assert reply_is_last_turn(tid, "m-none", organization_id=w.org_a) is False
            assert await same(w.org_a, tid, row, stored) == (run_id, mid)

            later = int(time.time() * 1000) + 60_000
            # The empty agent row of a refused start shows to no reader.
            _chat_row(w, tid, w.org_a, "hidden-1", role="assistant", content="",
                      ts=later, kind="agent")
            assert reply_is_last_turn(tid, mid, organization_id=w.org_a) is True
            _chat_row(w, tid, w.org_a, "u-later", role="user", content="And Orbit?",
                      ts=later, kind="human")
            assert reply_is_last_turn(tid, mid, organization_id=w.org_a) is False
            assert await same(w.org_a, tid, row, stored) is None, "not the last turn"
            with w.admin_engine.begin() as c:
                c.execute(text("DELETE FROM chat_message WHERE id = 'u-later'"))
            assert await same(w.org_a, tid, row, stored) == (run_id, mid), "non-vacuity"

            assert await same(w.org_a, tid, row, {**stored, "message_id": None}) is None
            assert await same(w.org_a, tid, {**row, "run_id": "run-other"}, stored) is None
            assert await same(w.org_a, tid, {**row, "state": "open"}, stored) is None
            await stream_relay.push_event(tid, {"type": "RUN_FINISHED", "cancelled": True})
            assert await stream_relay.parked_end_of(tid, run_id) is None, "a Stop came after"
            assert await same(w.org_a, tid, row, stored) is None
            await client.delete(f"cc:stream:{tid}")
            assert await same(w.org_a, tid, row, stored) is None, "the stream expired"
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_a_lost_parked_end_keeps_the_old_row_and_opens_a_new_reply(relay_scene, monkeypatch):
    """The stream goes after ``_same_reply_of`` read it and before the
    start. The real ``start_detached`` refuses before any state changes, and
    the same claim opens a NEW reply. The old row keeps its words."""
    w = relay_scene
    real_start = stream_relay.start_detached

    async def go() -> None:
        client = await _client()
        _real_relay(monkeypatch, client)
        lost: list[str] = []

        async def _start(thread_id, gen, **kw):
            if kw.get("keep_stream") and not lost:
                lost.append(thread_id)
                await client.delete(f"cc:stream:{thread_id}")  # it expired just now
            return await real_start(thread_id, gen, **kw)

        monkeypatch.setattr(stream_relay, "start_detached", _start)
        try:
            tid = _new_chat(w, w.org_a)
            rid, run_id, mid = await _parked_turn(w, tid, w.org_a)
            res = await _answer(tid, rid, _ALICE, w.org_a)
            assert isinstance(res, dict), res
            assert lost == [tid], "non-vacuity: the stream went at the start"
            assert res["resumed"] is True and res["sameReply"] is False, res
            assert res["runId"] != run_id and res["messageId"] != mid
            await _run_ends(tid)
            rows = {r[0]: r[1] for r in _assistant_rows(w, tid)}
            assert set(rows) == {mid, res["messageId"]}, "the old row and ONE new row"
            assert "Let me move it." in rows[mid] and "The deal moved." not in rows[mid]
            assert "The deal moved." in rows[res["messageId"]]
            assert _row(w, w.org_a, rid) == ("answered", "APPROVE", True), "ONE claim"
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


@pytest.mark.parametrize("both", [True, False], ids=["both flags", "late approve off"])
def test_a_real_park_keeps_the_stream_24h(both, relay_scene, monkeypatch):
    """D-RES-7 with the real ``pending_ask.park`` and the real drain of
    ``start_detached``. The second ``mark_inactive`` stands for the
    executor's. With a flag off, the stream keeps the life of before."""
    from acb_common import get_settings

    w = relay_scene
    if not both:
        monkeypatch.setattr(get_settings(), "chat_late_approve_orgs", "")

    async def go() -> None:
        client = await _client()
        _real_relay(monkeypatch, client)
        try:
            tid = _new_chat(w, w.org_a)
            rid = uuid.uuid4().hex
            assert pending_ask.insert_ask(w.org_a, {
                "request_id": rid, "thread_id": tid, "run_id": "run-p", "actor_email": _ALICE,
                "agent_name": "orchestrator", "kind": "confirmation", "question": _TITLE_A,
                "payload": {"request_id": rid, "title": _TITLE_A}})
            hold = asyncio.Event()

            async def _waits():
                await stream_relay.push_event(
                    tid, {"type": "RUN_STARTED", "runId": "run-p", "threadId": tid})
                await hold.wait()
                yield ""

            task = await stream_relay.start_detached(
                tid, _waits(), actor=_ALICE, organization_id=w.org_a,
                record={"runId": "run-p", "messageId": "m-p", "agent": "orchestrator"})
            for _ in range(200):
                if await client.exists(f"cc:stream:{tid}"):
                    break
                await asyncio.sleep(0.01)

            async def _org() -> str:
                return w.org_a

            pending_ask._INSERTS[rid] = asyncio.ensure_future(_org())
            fut = asyncio.get_running_loop().create_future()
            assert await pending_ask.park(rid, tid, fut) is True
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, timeout=10)
            await stream_relay.mark_inactive(tid)
            events = await _stream(client, tid)
            assert events[-1] == {"type": "RUN_FINISHED", "threadId": tid, "parked": True}
            ttl = await client.ttl(f"cc:stream:{tid}")
            if both:
                assert stream_relay.STREAM_TTL_SECONDS < ttl <= stream_relay.PARKED_STREAM_TTL_SECONDS
            else:
                assert 0 < ttl <= stream_relay.STREAM_TTL_SECONDS, ttl
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())
