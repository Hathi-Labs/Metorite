"""HRD-22: a late approve acts once, on a REAL database and a REAL Redis (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` S2 item 8, and
``chat_failure_modes.md`` row C6 and slice HRD-22 (task 3231). Module:
``acb_skills.late_approve``. The hermetic half is ``test_late_approve_grant.py``.

The rows of ``chat_pending_ask`` run as real SQL on the phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS role
(the fixtures of ``test_pending_ask_store.py``). The records run on a PRIVATE
real Redis, through the real ``TenantRedis`` wrapper. The run is a real
``request_confirmation`` that parks, and the late answer is the real
``respond_user_input`` route. Only the stream relay is the fake of
``test_chat_deploy_recovery.py``.

R7, each with the mutation that turns it red:

* ``test_the_production_sequence_writes_once_with_one_card`` — the replay of
  2026-11-14 (three cards for one write). Now one card and one write, and a
  second use finds nothing. Drop the use in the card gate, or read the grant
  with GET.
* ``test_a_stale_twin_never_writes_after_a_live_approve`` — the review P1.
  Drop the live cleanup, and the twin writes twice.
* ``test_a_changed_card_gets_a_normal_card`` — compare the act keys only.
* ``test_a_late_decline_writes_nothing`` — write a grant on a decline, or keep
  the offer after the row closed.
* ``test_no_grant_crosses_a_thread_a_member_or_an_org`` — key the grant by
  the thread alone, or by the member alone.

``HELD_R8_REDIS_URL`` names a PRIVATE Redis, because the suite runs FLUSHDB.
Never point it at a shared server. With it or the database unset, the suite
skips, and a skip is not a pass. Run::

    docker run -d --rm --name late-approve-r8-redis -p 127.0.0.1:16398:6379 redis:7-alpine
    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db> \\
    HELD_R8_REDIS_URL=redis://127.0.0.1:16398/0 \\
        uv run pytest tests/unit/test_late_approve_grant_r8.py -v -rs
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

pending_ask = pytest.importorskip("orchestrator.pending_ask")
stream_relay = pytest.importorskip("orchestrator.stream_relay")
executor = pytest.importorskip("orchestrator.executor")
pytest.importorskip("sqlalchemy")

from acb_skills import late_approve  # noqa: E402
from acb_skills.ask_tools import (  # noqa: E402
    PlainCard,
    act_keys,
    card_call,
    request_confirmation,
)
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
WRITES: list[tuple[str, str]] = []
DEALS: dict[str, str] = {}


async def update_deal_status(deal_id: str, stage: str, lost_reason: str | None = None) -> str:
    """A stand-in of ``agent-crm`` ``update_deal_status``, with its pinned
    parameters and its annotation: one card, then one write on approve."""
    title = DEALS[deal_id]
    ok = await request_confirmation(
        title=f"Move this deal to {stage}?",
        detail=f"{title} → {stage}",
        context=f"deal: {title}\nstage: {stage}",
    )
    if not ok:
        return "not moved"
    WRITES.append((deal_id, stage))
    return "moved"


update_deal_status.__tool_risk__ = {  # type: ignore[attr-defined]
    "read_only": False, "destructive": True, "idempotent": False,
    "open_world": True, "card_write": True}


def _card(deal_id: str, stage: str) -> PlainCard:
    """The card of the stand-in call, as the gate builds it."""
    title = DEALS[deal_id]
    return PlainCard(
        f"Move this deal to {stage}?", f"{title} → {stage}", f"deal: {title}\nstage: {stage}",
        None, act_keys(update_deal_status.__module__, "update_deal_status",
                       {"deal_id": deal_id, "stage": stage}),
        update_deal_status, False)


@pytest.fixture
def scene(as_app, liveness, no_persist, monkeypatch):
    """The real rows, the flags on, a short park window, and a clean world."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)
    monkeypatch.setattr(get_settings(), "chat_late_approve_orgs",
                        f"{as_app.org_a},{as_app.org_b}")
    monkeypatch.setattr(pending_ask, "park_after_seconds", lambda: 0.05)
    pending_ask._reset_for_tests()
    executor._pending_user_input.clear()
    WRITES.clear()
    DEALS.clear()
    DEALS.update({"d-neuro": "Neuropeak", "d-orbit": "Orbit"})
    yield as_app, liveness
    pending_ask._reset_for_tests()
    executor._pending_user_input.clear()


def _use_redis(monkeypatch, client) -> None:
    from acb_common.tenant_redis import TenantRedis

    monkeypatch.setattr(late_approve, "_redis", lambda: TenantRedis(client))


def _bind_run(tid: str, org: str, member: str, *, verified: bool = True) -> None:
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant

    executor._stream_relay_thread_id.set(tid)
    bind_tenant(org)
    bind_run_context(user=member, member_verified=verified, agent="orchestrator",
                     thread_id=tid)


async def _run(r, tid: str, org: str, *, member: str = _ALICE, live: str | None = None,
               **call: Any) -> dict[str, Any]:
    """One chat run on *tid* that calls the stand-in tool once. With *live*,
    the member answers the card at once, so it never parks."""
    r.store[f"cc:runactor:{tid}"] = member
    out: dict[str, Any] = {}
    seen = len(_cards(r, tid))

    async def _the_run() -> None:
        _bind_run(tid, org, member)
        with card_call(update_deal_status, (), call):
            out["result"] = await update_deal_status(**call)

    async def _answer_live() -> None:
        while len(_cards(r, tid)) == seen:
            await asyncio.sleep(0.002)
        rid = _cards(r, tid)[-1]["request_id"]
        executor._pending_user_input[rid].set_result({"answer": live})

    saved = pending_ask.park_after_seconds
    if live:
        pending_ask.park_after_seconds = lambda: 30.0
    run = asyncio.create_task(_the_run())
    stream_relay._DETACHED_TASKS[tid] = run
    helper = asyncio.create_task(_answer_live()) if live else None
    try:
        await asyncio.wait_for(run, timeout=60)
    except asyncio.CancelledError:
        out["parked"] = True
    finally:
        pending_ask.park_after_seconds = saved
        if helper is not None:
            helper.cancel()
    return out


async def _try(tid: str, org: str, member: str, *, verified: bool = True,
               deal_id: str = "d-neuro", stage: str = "Payment Complete") -> bool:
    """The card gate's own read of a grant, in a run bound to *tid*, *org*
    and *member*, for the card of the stand-in call."""
    async def _in_run() -> bool:
        _bind_run(tid, org, member, verified=verified)
        return await late_approve.take_grant(_card(deal_id, stage))

    return await asyncio.create_task(_in_run())


async def _answer(tid: str, rid: str, member: str, org: str, *, answer: str = "APPROVE",
                  declined: bool = False) -> Any:
    """``POST /agent/respond-input`` as *member* of *org*: the route itself."""
    from acb_auth import UserContext, UserRole
    from fastapi import HTTPException
    from gateway.routes import agent as agent_routes

    req = agent_routes.UserInputResponseRequest(
        request_id=rid, answer=answer, thread_id=tid, declined=declined)
    user = UserContext(email=member, role=UserRole.EMPLOYEE, organization_id=org)
    try:
        return await agent_routes.respond_user_input(req, user)
    except HTTPException as exc:
        return exc


def _cards(r, tid: str) -> list[dict[str, Any]]:
    from tests.unit.test_pending_ask_flow import _events

    return [e["value"] for e in _events(r, tid) if e.get("name") == "confirmation_requested"]


def _row(w, org: str, rid: str) -> tuple[Any, ...]:
    with w.admin_engine.connect() as c:
        return tuple(c.execute(text(
            "SELECT state, answer FROM chat_pending_ask "
            "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
            {"o": org, "r": rid}).one())


async def _keys(client, namespace: str) -> list[str]:
    return sorted(await client.keys(f"cc:*:{namespace}:*"))


async def _live_offers(client) -> list[str]:
    out = []
    for k in await _keys(client, late_approve.OFFER_NAMESPACE):
        if await client.get(k) != late_approve._SPENT:
            out.append(k)
    return out


async def _client():
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    return client


# ---------------------------------------------------------------------------

def test_the_production_sequence_writes_once_with_one_card(scene, monkeypatch):
    """The replay of 2026-11-14. Before HRD-22 the member approved three
    cards for one move. Now the member approves one card, late, and the next
    run makes the one write with no second card."""
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        from gateway.chat_recovery import LATE_APPROVE_NOTE

        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            # 09:13 the card. 09:23 it parks.
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro",
                               stage="Payment Complete")).get("parked")
            rid = _cards(r, tid)[0]["request_id"]
            assert _row(w, w.org_a, rid) == ("parked", None)
            digest = late_approve.act_of(_card("d-neuro", "Payment Complete"),
                                         "orchestrator").digest
            ask = f"cc:{w.org_a}:{late_approve.ASK_NAMESPACE}:{_ALICE}:{tid}:{rid}"
            offer = f"cc:{w.org_a}:{late_approve.OFFER_NAMESPACE}:{_ALICE}:{tid}:{digest}"
            assert await client.get(ask) == digest
            # The records live no longer than the row.
            for k in (ask, offer):
                assert 0 < await client.ttl(k) <= pending_ask._ttl_hours() * 3600

            # 09:32 the member taps Approve.
            exc = await _answer(tid, rid, _ALICE, w.org_a)
            assert exc.status_code == 409 and exc.detail["error"] == "run_restarted"
            assert exc.detail["resumeMessage"].endswith("\n\n" + LATE_APPROVE_NOTE)
            assert _row(w, w.org_a, rid) == ("answered", "APPROVE")
            assert not await client.exists(offer) and not await client.exists(ask)
            grant = f"cc:{w.org_a}:{late_approve.GRANT_NAMESPACE}:{_ALICE}:{tid}:{digest}"
            assert 0 < await client.ttl(grant) <= late_approve.GRANT_TTL_S

            # The new turn makes the same call: one write, no second card.
            out = await _run(r, tid, w.org_a, deal_id="d-neuro", stage="Payment Complete")
            assert "parked" not in out and out["result"] == "moved"
            assert WRITES == [("d-neuro", "Payment Complete")]
            assert len(_cards(r, tid)) == 1
            assert not await client.exists(grant)

            # A second use finds nothing: the same call asks again.
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro",
                               stage="Payment Complete")).get("parked")
            assert len(_cards(r, tid)) == 2
            assert WRITES == [("d-neuro", "Payment Complete")]
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_a_stale_twin_never_writes_after_a_live_approve(scene, monkeypatch):
    """Review P1, on the real rows and Redis. Card C1 parks. A twin card C2 of
    the same act is approved live (write 1). A late tap on C1 writes nothing."""
    w, r = scene
    tid = w.t_alice
    call = {"deal_id": "d-neuro", "stage": "Payment Complete"}

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            assert (await _run(r, tid, w.org_a, **call)).get("parked")
            c1 = _cards(r, tid)[0]["request_id"]
            assert "parked" not in await _run(r, tid, w.org_a, live="APPROVE", **call)
            assert WRITES == [("d-neuro", "Payment Complete")]
            assert await _live_offers(client) == []
            exc = await _answer(tid, c1, _ALICE, w.org_a)
            assert exc.status_code == 409
            assert "If this call already ran" not in exc.detail["resumeMessage"]
            assert await _keys(client, late_approve.GRANT_NAMESPACE) == []
            assert (await _run(r, tid, w.org_a, **call)).get("parked")
            assert WRITES == [("d-neuro", "Payment Complete")]
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_a_changed_card_gets_a_normal_card(scene, monkeypatch):
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro",
                               stage="Payment Complete")).get("parked")
            rid = _cards(r, tid)[0]["request_id"]
            assert (await _answer(tid, rid, _ALICE, w.org_a)).status_code == 409
            # Another stage: the act key is the same, and the text is not.
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro",
                               stage="Lost")).get("parked")
            assert WRITES == []
            # Another act never takes the grant away: its own card takes it.
            assert len(await _keys(client, late_approve.GRANT_NAMESPACE)) == 1
            assert "parked" not in await _run(r, tid, w.org_a, deal_id="d-neuro",
                                              stage="Payment Complete")
            assert WRITES == [("d-neuro", "Payment Complete")]
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_a_late_decline_writes_nothing(scene, monkeypatch):
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro", stage="Won")).get("parked")
            rid = _cards(r, tid)[0]["request_id"]
            assert await _live_offers(client)
            assert await _answer(tid, rid, _ALICE, w.org_a, answer="",
                                 declined=True) == {"ok": True, "declined": True}
            assert _row(w, w.org_a, rid)[0] == "closed"
            assert await client.keys("*") == [], "the offer goes with its row"
            # A second answer finds a closed row, so it can write nothing.
            assert (await _answer(tid, rid, _ALICE, w.org_a)).status_code == 409
            assert await _keys(client, late_approve.GRANT_NAMESPACE) == []
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro", stage="Won")).get("parked")
            assert WRITES == []
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())


def test_no_grant_crosses_a_thread_a_member_or_an_org(scene, monkeypatch):
    w, r = scene
    tid = w.t_alice

    async def go() -> None:
        client = await _client()
        _use_redis(monkeypatch, client)
        try:
            assert (await _run(r, tid, w.org_a, deal_id="d-neuro",
                               stage="Payment Complete")).get("parked")
            rid = _cards(r, tid)[0]["request_id"]

            # Carol of org B cannot answer Alice's card, so she writes nothing.
            carol = await _answer(tid, rid, _CAROL, w.org_b)
            assert carol.status_code == 403
            assert _row(w, w.org_a, rid) == ("parked", None)
            assert await _keys(client, late_approve.GRANT_NAMESPACE) == []

            assert (await _answer(tid, rid, _ALICE, w.org_a)).status_code == 409
            assert len(await _keys(client, late_approve.GRANT_NAMESPACE)) == 1

            # Another thread, another member, another org, no verified member.
            assert not await _try(w.t_carol, w.org_a, _ALICE)
            assert not await _try(tid, w.org_a, _BOB)
            assert not await _try(tid, w.org_b, _ALICE)
            assert not await _try(tid, w.org_a, _ALICE, verified=False)
            assert len(await _keys(client, late_approve.GRANT_NAMESPACE)) == 1, \
                "no cross try reads the grant"

            # Its own run takes it once, and a second use finds nothing.
            assert await _try(tid, w.org_a, _ALICE)
            assert not await _try(tid, w.org_a, _ALICE)
            assert await _keys(client, late_approve.GRANT_NAMESPACE) == []
        finally:
            await client.flushdb()
            await client.aclose()

    asyncio.run(go())
