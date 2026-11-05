"""WS-BRIEF BR-2a-2 slice 1 — the phrase step on a real database (R8).

Spec: ``project-docs/specs/core_brief.md`` §4.2, §4.4, §4.5 and §10 row 4.
Code: ``gateway/brief/{phrase,run,store}.py``. The world (two orgs, the
members, the fake feed, the worker's own ``run_org``) is the one of
``test_brief_run_r8.py``. Here ``BRIEF_COMPOSE`` is on, and
``acb_llm.context.acompletion_with_fallback`` is a fake that counts each
call. No test makes a real model call.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_brief_phrase_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS. A skip is not a pass.

**The mutations (R7).** Commit before each, and restore with git after.

* P1. ``store._MARK_PHRASING_SQL``: drop ``AND state = 'gathered'``.
  ``test_r8_mark_phrasing_wins_once_and_only_on_a_gathered_row`` goes red.
* P2. ``run._gather_and_phrase``: call ``phrase.phrase`` inside the first
  ``tenant_session`` block. ``test_r8_four_claims_make_one_call_and_a_
  model_lead`` goes red, because the fake sees a session open.
* P3. ``run._run_slot``: drop the ``phrasing`` branch, so a stuck run
  answers ``exists``. ``test_r8_a_cancel_during_the_call_gives_the_slot_
  back_and_the_retry_makes_no_call`` goes red.
"""

# ruff: noqa: F811 - each test takes the imported `world` fixture by name.
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.brief import flags, phrase, planner, store
from gateway.jobs import queue, worker
from gateway.reach import plan
from sqlalchemy import text

from tests.unit.test_brief_run_r8 import (  # noqa: F401
    ANA,
    BEN,
    SLOT,
    T1,
    D,
    _claims,
    _go,
    _jobs,
    _ledger,
    _ledger_on,
    _runs,
    _seed_and_run,
    _tick,
    card,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

#: Four cards of ANA, so four claims. Each line holds no digit, so a lead
#: with no number passes the fence.
FOUR = ["email:acc:t1", "email:acc:t2", "email:acc:t3", "email:acc:t4"]


def _cards(n: int = 4) -> list[dict]:
    return [card(sid) for sid in FOUR[:n]]


def _compose(monkeypatch: pytest.MonkeyPatch, world: Any, *, on: bool = True,
             in_app: bool = False, routed: bool = True) -> None:
    import acb_llm.routed

    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        brief_enabled=True, brief_orgs=world.p.org_a, brief_compose=on, brief_in_app=in_app))
    monkeypatch.setattr(acb_llm.routed, "routing_is_on", lambda: routed)


def _answer(*sentences: tuple[str, list[str]]) -> str:
    return json.dumps({"sentences": [{"text": t, "claims": c} for t, c in sentences]})


#: The checked-out connections of the app engine, read in the fake call.
def _open_sessions() -> int:
    from acb_common import db as acb_db

    return int(acb_db.get_engine().pool.checkedout())


def _model(monkeypatch: pytest.MonkeyPatch, outcome: Any, *,
           first_delay: float = 0.0) -> list[dict]:
    """Fake the model seam. *outcome* is the answer text, or an exception
    to raise. Each call is counted, with the sessions open at the time."""
    import acb_llm.context

    calls: list[dict] = []

    async def fake(**kw: Any) -> Any:
        calls.append({**kw, "_open": _open_sessions()})
        if first_delay and len(calls) == 1:
            await asyncio.sleep(first_delay)
        if isinstance(outcome, BaseException):
            raise outcome
        msg = SimpleNamespace(content=outcome)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")]), "m"

    monkeypatch.setattr(acb_llm.context, "acompletion_with_fallback", fake)
    return calls


def _ana(world: Any) -> Any:
    return next(r for r in _runs(world) if r.member_email == ANA)


def _template(cards: list[dict]) -> str:
    return plan.brief_lead(plan.brief_parts(cards, D, now=T1))


# ── The call ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("in_app", [False, True])
def test_r8_four_claims_make_one_call_and_a_model_lead(world, monkeypatch, in_app) -> None:
    """Acceptance: one call with ``source="brief"`` and ``client_ref`` =
    the stored ``model_request_id``. The model lead cites claims of the
    run, and the lines stay ``plan.line`` of their cards. Mutation P2: the
    call sees no session of this run open."""
    _compose(monkeypatch, world, in_app=in_app)
    cards = _cards()
    world.cards[ANA] = cards
    calls = _model(monkeypatch, _answer(("Priya waits on you in several threads.", ["c1", "c3"])))
    _seed_and_run(world)
    (call,) = calls
    assert call["source"] == "brief" and call["model"] == "tier-fast"
    assert call["feature"] == "brief.phrase"
    assert call["_open"] == 0, "the call ran with a session open"
    run = _ana(world)
    assert run.model_request_id and call["client_ref"] == run.model_request_id
    assert run.state == ("delivered" if in_app else "ready") and run.ready_at == T1
    assert run.lead == "Priya waits on you in several threads." and run.lead_source == "model"
    got = _claims(world, str(run.id))
    assert run.lead_claim_ids and {str(i) for i in run.lead_claim_ids} <= {
        str(c.id) for c in got}
    assert [str(i) for i in run.lead_claim_ids] == [str(got[0].id), str(got[2].id)]
    shown = plan.brief_items(cards)[:plan.BRIEF_LINES]
    assert [c.text for c in got] == [plan.line(c, T1) for c in shown]
    # BEN has no card: empty, and no call for him.
    assert next(r for r in _runs(world) if r.member_email == BEN).state == "empty"


def _refused() -> str:
    return _answer(("Priya waits.", ["c9"]))


@pytest.mark.parametrize("outcome", ["fence", "error", "router_402", "timeout"])
def test_r8_a_failed_phrase_keeps_the_template_lead_and_still_delivers(
        world, monkeypatch, capsys, outcome) -> None:
    """Acceptance: a refused answer, a model error, a balance refusal and a
    timeout each keep the template lead. The run still reaches
    ``delivered``, and one call went out."""
    from acb_llm.routed import RoutedRefusal

    _compose(monkeypatch, world, in_app=True)
    cards = _cards()
    world.cards[ANA] = cards
    if outcome == "timeout":
        monkeypatch.setattr(phrase, "CALL_TIMEOUT_S", 0.05)
    got = {"fence": _refused(), "error": RuntimeError("boom"),
           "router_402": RoutedRefusal(402, "no credit"),
           "timeout": _answer(("Hi.", ["c1"]))}[outcome]
    calls = _model(monkeypatch, got, first_delay=5 if outcome == "timeout" else 0)
    _seed_and_run(world)
    assert len(calls) == 1
    run = _ana(world)
    assert run.state == "delivered" and run.lead == _template(cards)
    assert run.lead_source == "template" and list(run.lead_claim_ids) == []
    assert run.model_request_id == calls[0]["client_ref"]
    out = capsys.readouterr().out
    want = {"fence": "brief.phrase_refused", "error": "RuntimeError",
            "router_402": "RoutedRefusal", "timeout": "TimeoutError"}[outcome]
    assert want in out
    job = next(j for j in _jobs(world, planner.RUN_KIND) if j.payload["member"] == ANA)
    assert job.state == "done"


@pytest.mark.parametrize("case", ["three_claims", "compose_off", "router_off"])
def test_r8_no_call_and_the_template_lead_when_the_step_is_off(
        world, monkeypatch, case) -> None:
    """Acceptance: with 3 claims, ``BRIEF_COMPOSE`` off, or the Router off,
    no call runs. The lead is ``plan.brief_lead``, and the run never passes
    through ``phrasing``: no ``model_request_id``."""
    _compose(monkeypatch, world, on=case != "compose_off", routed=case != "router_off")
    cards = _cards(3 if case == "three_claims" else 4)
    world.cards[ANA] = cards
    calls = _model(monkeypatch, _answer(("Hi.", ["c1"])))
    _seed_and_run(world)
    assert calls == []
    run = _ana(world)
    assert run.state == "ready" and run.lead == _template(cards)
    assert run.lead_source == "template" and run.model_request_id is None


def test_r8_a_cancel_during_the_call_gives_the_slot_back_and_the_retry_makes_no_call(
        world, monkeypatch) -> None:
    """Acceptance, and mutation P3. The worker's time limit cancels the
    handler during the call. The ledger slot goes back. The run stays in
    ``phrasing`` with the template lead. The retry finds ``phrasing``,
    makes NO call, and reaches ``ready`` with the template lead."""
    _ledger_on(monkeypatch, True)
    monkeypatch.setattr(worker, "HANDLER_TIMEOUT", 0.5)
    _compose(monkeypatch, world)
    cards = _cards()
    world.cards[ANA] = cards
    calls = _model(monkeypatch, _answer(("Hi.", ["c1"])), first_delay=30)
    _seed_and_run(world)
    assert len(calls) == 1
    run = _ana(world)
    assert run.state == "phrasing" and run.model_request_id == calls[0]["client_ref"]
    assert _ledger(world, ANA) == []  # given back, under the cancel
    job = next(j for j in _jobs(world, planner.RUN_KIND) if j.payload["member"] == ANA)
    assert job.state == "queued" and job.last_error.startswith("HandlerTimeout")
    later = T1 + queue.backoff(1) + timedelta(seconds=1)
    _tick(world, world.p.org_a, later)
    assert len(calls) == 1, "the retry made a second call"
    (run,) = _runs(world, ANA)
    assert run.state == "ready" and run.lead == _template(cards)
    assert run.lead_source == "template" and len(_claims(world, str(run.id))) == 4
    (row,) = _ledger(world, ANA)
    assert row.state == "ok" and str(run.unattended_run_id) == str(row.id)


def test_r8_a_stuck_run_with_no_lead_takes_the_lead_of_its_stored_claims(
        world, monkeypatch) -> None:
    """§4.2 step 6. A run in ``phrasing`` with no lead (a row of an older
    try) takes the template lead from its stored claims, with no call."""
    _compose(monkeypatch, world)
    calls = _model(monkeypatch, _answer(("Hi.", ["c1"])))
    claims = [store.ClaimIn(position=n, section="needs_you", text=f"Line {chr(65 + n)}",
                            source_app="email", source_kind="needs_reply",
                            source_id=f"email:acc:s{n}", href="/email?x=1")
              for n in range(4)]

    async def seed() -> None:
        async with tenant_session(world.p.org_a) as db:
            run = (await store.create_run(db, ANA, kind="brief", local_date=D,
                                          slot_at=SLOT)).run
            await store.add_claims(db, ANA, run.id, claims)
            assert await store.set_state(db, ANA, run.id, "gathered", now=T1,
                                         from_state="planned")
            assert await store.mark_phrasing(db, ANA, run.id, ref="brief-old")
            # The run job of the slot, as the planner enqueues it.
            out = await queue.enqueue(db, planner.RUN_KIND, {
                "member": ANA, "local_date": D.isoformat(), "slot_at": SLOT.isoformat()},
                dedupe_key=planner.dedupe_key(ANA, "brief", D), now=T1)
            assert out.created

    _go(world, seed)
    world.cards[ANA] = _cards()
    _tick(world, world.p.org_a, T1)
    assert calls == []
    (run,) = _runs(world, ANA)
    assert run.state == "ready" and run.lead == "Your day, Tue 10 Mar. 4 things need you."
    assert run.lead_source == "template" and run.model_request_id == "brief-old"
    assert [c.text for c in _claims(world, str(run.id))] == [c.text for c in claims]


def test_r8_the_member_block_reaches_the_prompt(world, monkeypatch) -> None:
    """The member block is read on the real tables, in the bound tenant,
    and it reaches the system prompt of the call."""
    from acb_common import assistant_profile as ap

    monkeypatch.setattr(ap, "enabled_for", lambda org: True)
    with world.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": ANA})
        c.execute(text(
            "INSERT INTO user_settings (user_id, organization_id, assistant_profile) "
            "VALUES (:u, CAST(:org AS uuid), CAST(:p AS jsonb))"),
            {"u": ANA, "org": world.p.org_a,
             "p": json.dumps({"v": 1, "address": {"name": "Anaya"}})})
    _compose(monkeypatch, world)
    world.cards[ANA] = _cards()
    calls = _model(monkeypatch, _answer(("Priya waits on you.", ["c1"])))
    _seed_and_run(world)
    (call,) = calls
    system = call["messages"][0]["content"]
    assert ap.BLOCK_HEADER in system and "Anaya" in system
    assert _ana(world).lead_source == "model"


# ── The store, on the CHECKs of migration 315 ───────────────────────────────


def test_r8_mark_phrasing_wins_once_and_only_on_a_gathered_row(world) -> None:
    """Acceptance, and mutation P1. ``gathered`` to ``phrasing`` to ``ready``
    passes the guards and the CHECKs. ``mark_phrasing`` answers False on a
    row that is not ``gathered``. Of two tries at once, only one wins."""
    org = world.p.org_a
    claim_in = store.ClaimIn(position=0, section="now", text="Line", source_app="email",
                             source_kind="needs_reply", source_id="email:acc:x",
                             href="/email?x=1")

    async def make() -> str:
        async with tenant_session(org) as db:
            run = (await store.create_run(db, ANA, kind="brief", local_date=D,
                                          slot_at=SLOT)).run
            await store.add_claims(db, ANA, run.id, [claim_in])
            assert not await store.mark_phrasing(db, ANA, run.id, ref="r0")  # planned
            assert await store.set_state(db, ANA, run.id, "gathered", now=T1,
                                         from_state="planned")
            return run.id

    run_id = _go(world, make)

    async def mark(ref: str) -> bool:
        async with tenant_session(org) as db:
            got = await store.mark_phrasing(db, ANA, run_id, ref=ref)
            await asyncio.sleep(0.2)  # hold the row lock, so the two overlap
            return got

    async def race() -> list[bool]:
        return list(await asyncio.gather(mark("r1"), mark("r2")))

    assert sorted(_go(world, race)) == [False, True]

    async def finish() -> list[Any]:
        async with tenant_session(org) as db:
            out: list[Any] = [await store.mark_phrasing(db, BEN, run_id, ref="r3")]
            (cid,) = [c.id for c in await store.read_claims(db, ANA, run_id)]
            out.append(await store.set_state(db, ANA, run_id, "ready", now=T1,
                                             from_state="phrasing"))
            out.append(await store.set_lead(db, ANA, run_id, "A lead.", source="model",
                                            claim_ids=(cid,)))
            out.append(await store.mark_phrasing(db, ANA, run_id, ref="r4"))  # ready
            out.append(await store.set_state(db, ANA, run_id, "ready", now=T1,
                                             from_state="phrasing"))
            return out

    assert _go(world, finish) == [False, True, True, False, False]
    run = _ana(world)
    assert run.state == "ready" and run.model_request_id in {"r1", "r2"}
    assert run.lead_source == "model" and len(run.lead_claim_ids) == 1

    async def no_cites() -> None:
        async with tenant_session(org) as db:
            await store.set_lead(db, ANA, run_id, "A lead.", source="model")

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        _go(world, no_cites)


def test_r8_an_empty_ref_is_refused_before_any_statement(world) -> None:
    async def go() -> None:
        async with tenant_session(world.p.org_a) as db:
            await store.mark_phrasing(db, ANA, "00000000-0000-0000-0000-000000000000", ref=" ")

    with pytest.raises(store.BriefStoreError):
        _go(world, go)
