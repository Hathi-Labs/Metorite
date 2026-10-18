"""R8 — the chat asks before it answers, through the real router (WS-53 CRM-Q32-8).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.10, CRM-Q32-8 (owner
direction 2026-10-18, two parts). Against a REAL Postgres, as the
non-privileged role of the phase-4 rehearsal, with the seed of
``test_crm_questions_r8.py``.

* Item 2: ``GET /crm/questions/ask`` takes 1 or 2 ids, for each member with
  CRM access, and ``all=1`` matches a part in use.
* Item 8: ``@default`` holds the question for one member for a day.
* Item 9: an admin's reply writes through the code of ``/answer``, also for
  a part in use, and its Undo.
* Items 10, 11 and 17d: a member's reply is a PROPOSAL, and the plan stays.
* Items 12 and 17c: Confirm, and the display name ("A member", never an
  email).
* Item 13: a member never writes the plan: not through ``/reply``, not
  through ``/answer``, and not through the tool.
* Item 14: the tenancy fences.
* Items 15 and 16: a member's choice never reaches another member.
* Items 17 and 17a: the step and two sessions at once.
* Item 17b: each admin write of a part clears its proposals.
* Item 23: no decide call.

The Redis of a hold is a fake behind the one wrapper's call
(``routes/crm/questions._redis``): the key is still a ``TenantKey`` built
inside ``organization_scope``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_questions_answer_r8 import approved, item_of, versions
from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    call,
    cursor,
    cursor_state,
    q_env,
    seed_a,
    seed_b,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

Q = "/crm/questions"
KEY = "key:Fee_Receipts:amount"
ANSWERED = "key:Invoices:amount"


def member(org: str, n: int = 1, *, crm: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=f"member{n}@crm-q32-8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm"] if crm else []), organization_id=org,
    )


class FakeHold:
    """The Redis of a hold: ``exists`` and ``setex`` on a ``TenantKey``."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[int, str]] = {}

    async def exists(self, key: Any) -> int:
        return int(key.value in self.store)

    async def setex(self, key: Any, seconds: int, value: str) -> bool:
        self.store[key.value] = (seconds, value)
        return True


@pytest.fixture
def hold(monkeypatch) -> FakeHold:
    from gateway.routes.crm import questions as routes

    fake = FakeHold()
    monkeypatch.setattr(routes, "_redis", lambda: fake)
    return fake


@pytest.fixture
def decide_calls(monkeypatch) -> list[Any]:
    """Item 23: a fake of ``decide`` that counts each call."""
    import acb_llm

    calls: list[Any] = []

    async def fake(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("CRM-Q32-8 makes no decide call")

    monkeypatch.setattr(acb_llm, "decide", fake, raising=False)
    return calls


def proposals(p: Any, org: str) -> dict:
    return (cursor_state(p, org, "zoho:questions") or {}).get("proposals") or {}


async def reply(p: Any, user: Any, qid: str, choice: str) -> Any:
    return await call(p, user, "POST", f"{Q}/{qid}/reply", {"choice": choice})


async def ask(p: Any, user: Any, ids: str, *, everything: bool = False) -> Any:
    return await call(p, user, "GET", f"{Q}/ask?ids={ids}" + ("&all=1" if everything else ""))


def people(p: Any, org: str, rows: list[tuple[str, str]]) -> None:
    with p.admin_engine.begin() as c:
        for name, email in rows:
            c.execute(text(
                "INSERT INTO people (organization_id, name, email) VALUES "
                "(CAST(:o AS uuid), :n, :e)"), {"o": org, "n": name, "e": email})


@pytest.fixture
def chat_env(q_env):
    yield q_env
    with q_env.admin_engine.begin() as c:
        for org in (q_env.org_a, q_env.org_b):
            c.execute(text("DELETE FROM people WHERE organization_id = CAST(:o AS uuid)"),
                      {"o": org})


# ── Item 2: the read of a card ──────────────────────────────────────────────


async def test_ask_reads_one_or_two_ids_for_each_member_with_crm_access(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    got = await ask(p, member(p.org_a), KEY)
    assert got.status_code == 200, got.text
    (q,) = got.json()["questions"]
    assert (q["id"], q["can_answer"], q["held"], q["my_proposal"]) == (KEY, False, False, None)
    assert q["question"] == "Which field holds the amount of each record in Collection?"
    assert [c["value"] for c in q["choices"]] == ["Part_A", "Part_B"]
    assert sorted(q["choices"][0]["samples"]) == ["100", "200"]
    assert got.json()["href"] == "/crm?tab=settings&section=questions"
    two = await ask(p, member(p.org_a), f"{KEY},revenue")
    assert [x["id"] for x in two.json()["questions"]] == [KEY, "revenue"]
    three = await ask(p, member(p.org_a), f"{KEY},revenue,purpose:Towers")
    assert three.status_code == 422 and three.json()["detail"]["code"] == "bad_ids"
    # A part in use matches only with all=1.
    assert (await ask(p, member(p.org_a), ANSWERED)).json()["questions"] == []
    done = (await ask(p, member(p.org_a), ANSWERED, everything=True)).json()["questions"]
    assert (done[0]["reason"], done[0]["current"]) == ("answered", "Net_Value")
    # A slot or a stage value is no chat card.
    assert (await ask(p, member(p.org_a), "slot:company")).json()["questions"] == []
    # An admin can answer.
    assert (await ask(p, _user(p.org_a), KEY)).json()["questions"][0]["can_answer"] is True
    # A member with no CRM access gets 403.
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'admins' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": p.org_a})
    assert (await ask(p, member(p.org_a), KEY)).status_code == 403
    assert (await reply(p, member(p.org_a), KEY, "Part_A")).status_code == 403


# ── Item 8: "@default" holds the question for one member ────────────────────


async def test_default_writes_nothing_and_holds_for_a_day(chat_env, app_engine, hold) -> None:
    p = chat_env
    seed_a(p)
    before = versions(p, p.org_a)
    got = await reply(p, member(p.org_a), KEY, "@default")
    assert got.status_code == 200, got.text
    assert got.json()["result"] == "default"
    assert versions(p, p.org_a) == before and proposals(p, p.org_a) == {}
    ((key, (seconds, _v)),) = hold.store.items()
    assert seconds == 24 * 3600 and key == f"cc:{p.org_a}:crm_question_hold:member1@crm-q32-8.test:{KEY}"
    # The next run of the member: held, so the tool gives the default line.
    assert (await ask(p, member(p.org_a), KEY)).json()["questions"][0]["held"] is True
    # Another member is not held.
    assert (await ask(p, member(p.org_a, 2), KEY)).json()["questions"][0]["held"] is False
    # After the hold the question comes back.
    hold.store.clear()
    assert (await ask(p, member(p.org_a), KEY)).json()["questions"][0]["held"] is False


# ── Item 9: an admin's reply is the answer, and its Undo ────────────────────


async def test_an_admin_reply_writes_through_the_code_of_answer(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    admin = _user(p.org_a)
    got = await reply(p, admin, KEY, "Part_B")
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["result"], body["line"]) == ("answer", "Totals of Collection now use Part B.")
    assert body["undo"] == {"module": "Fee_Receipts", "part": "amount", "value": "@auto",
                            "question": None}
    item = item_of(p, p.org_a, "Fee_Receipts")
    assert (item["amount"], item["by"]["amount"]) == ("Part_B", "admin")
    assert versions(p, p.org_a)[-1][2] == "admin@crm-q32-r8.test"
    # A part in use takes a change: the earlier answer of the admin is the Undo.
    change = await reply(p, admin, KEY, "Part_A")
    assert change.status_code == 200, change.text
    assert change.json()["undo"]["value"] == "Part_B"
    assert item_of(p, p.org_a, "Fee_Receipts")["amount"] == "Part_A"
    undo = change.json()["undo"]
    back = await call(p, admin, "PUT", f"/crm/catalogue/{undo['module']}",
                      {undo["part"]: undo["value"]})
    assert back.status_code == 200, back.text
    assert item_of(p, p.org_a, "Fee_Receipts")["amount"] == "Part_B"
    # Revenue, open then in use, and a purpose in use.
    assert (await reply(p, admin, "revenue", "payment_in")).json()["result"] == "answer"
    assert (await reply(p, admin, "revenue", "invoice")).json()["undo"]["value"] == "payment_in"
    assert item_of(p, p.org_a, "@org")["revenue"] == "invoice"
    assert (await reply(p, admin, "purpose:Fee_Receipts", "invoice")).status_code == 200
    assert item_of(p, p.org_a, "Fee_Receipts")["purpose"] == "invoice"


async def test_auto_and_a_key_that_no_option_has_are_422_for_each_caller(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    before = versions(p, p.org_a)
    for user in (_user(p.org_a), member(p.org_a)):
        auto = await reply(p, user, KEY, "@auto")
        assert auto.status_code == 422 and auto.json()["detail"]["code"] == "no_undo_here"
        bad = await reply(p, user, KEY, "Invented_Field")
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_choice"
    assert versions(p, p.org_a) == before and proposals(p, p.org_a) == {}


# ── Items 10, 11 and 17d: a member's reply is a proposal ────────────────────


async def test_a_member_reply_is_a_proposal_and_the_plan_version_stays(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    before = versions(p, p.org_a)
    got = await reply(p, member(p.org_a), KEY, "Part_B")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["result"] == "proposal"
    assert body["override"] == {"part": "amount", "word": "@amount", "field": "Part_B",
                                "label": "Part B", "measure": "sum"}
    assert body["line"] == ("This uses Part B, as you chose. A CRM admin can confirm it "
                            "for everyone.")
    assert versions(p, p.org_a) == before
    stored = proposals(p, p.org_a)[KEY]
    assert [(x["value"], x["member"]) for x in stored] == [("Part_B", "member1@crm-q32-8.test")]
    # The next read of that member uses it.
    (q,) = (await ask(p, member(p.org_a), KEY)).json()["questions"]
    assert q["my_proposal"]["value"] == "Part_B" and q["override"]["field"] == "Part_B"
    # The question is still open for everyone.
    listed = (await call(p, _user(p.org_a), "GET", f"{Q}?limit=50")).json()["shown"]
    assert KEY in [x["id"] for x in listed]


async def test_a_member_choice_on_a_part_in_use_is_a_proposed_change(
    chat_env, app_engine, hold,
) -> None:
    """Item 17d: the org fact stays, and the member reads that the change
    waits. A choice that equals the answer in use records nothing."""
    p = chat_env
    seed_a(p)
    got = await reply(p, member(p.org_a), ANSWERED, "Billed_Value")
    assert got.status_code == 200, got.text
    assert (got.json()["result"], got.json()["line"], got.json()["override"]) == (
        "proposal", "Your suggested change is waiting for a CRM admin.", None)
    assert item_of(p, p.org_a, "Invoices")["amount"] == "Net_Value"
    (q,) = (await ask(p, member(p.org_a), ANSWERED, everything=True)).json()["questions"]
    assert q["my_proposal"]["value"] == "Billed_Value" and q["override"] is None
    same = await reply(p, member(p.org_a, 2), ANSWERED, "Net_Value")
    assert same.json()["result"] == "kept"
    assert [x["member"] for x in proposals(p, p.org_a)[ANSWERED]] == ["member1@crm-q32-8.test"]


async def test_the_store_keeps_five_one_for_each_member(chat_env, app_engine, hold) -> None:
    """Item 11."""
    p = chat_env
    seed_a(p)
    for n in range(1, 7):
        assert (await reply(p, member(p.org_a, n), KEY, "Part_A")).status_code == 200
    assert (await reply(p, member(p.org_a, 4), KEY, "Part_B")).status_code == 200
    stored = proposals(p, p.org_a)[KEY]
    # The newest 5: member1 dropped. member4 proposed again, so its second
    # proposal replaced its first and is the newest.
    assert [x["member"].split("@")[0] for x in stored] == [
        "member2", "member3", "member5", "member6", "member4"]
    assert [x["value"] for x in stored if x["member"].startswith("member4")] == ["Part_B"]
    listed = (await call(p, _user(p.org_a), "GET", f"{Q}?limit=50")).json()
    shown = next(q for q in listed["shown"] if q["id"] == KEY)["proposals"]
    assert len(shown) == 5 and {x["value"] for x in shown} == {"Part_A", "Part_B"}


# ── Items 12, 16 and 17c: Confirm, and the display name ─────────────────────


async def test_confirm_clears_and_the_admin_reads_a_name_never_an_email(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    people(p, p.org_a, [("Asha Rao Q32", "Member1@crm-q32-8.test")])
    assert (await reply(p, member(p.org_a, 1), KEY, "Part_B")).status_code == 200
    assert (await reply(p, member(p.org_a, 2), KEY, "Part_A")).status_code == 200
    got = await call(p, _user(p.org_a), "GET", f"{Q}?limit=50")
    shown = next(q for q in got.json()["shown"] if q["id"] == KEY)["proposals"]
    assert [(x["name"], x["value"], x["label"]) for x in shown] == [
        ("Asha Rao Q32", "Part_B", "Part B"), ("A member", "Part_A", "Part A")]
    assert "crm-q32-8.test" not in got.text
    # Item 16: a member reads no name and no email of another member.
    other = await ask(p, member(p.org_a, 3), KEY)
    assert "crm-q32-8.test" not in other.text and "Asha" not in other.text
    assert other.json()["questions"][0]["my_proposal"] is None
    # Confirm is /answer with the value of a proposal, and it clears them.
    confirm = await call(p, _user(p.org_a), "POST", f"{Q}/{KEY}/answer", {"value": "Part_B"})
    assert confirm.status_code == 200, confirm.text
    assert KEY not in proposals(p, p.org_a)
    assert item_of(p, p.org_a, "Fee_Receipts")["amount"] == "Part_B"


# ── Item 13: a member never writes the plan ─────────────────────────────────


async def test_a_member_never_writes_the_plan(chat_env, app_engine, hold) -> None:
    from gateway.crm_sync import questions as qs

    p = chat_env
    seed_a(p)
    before = versions(p, p.org_a)
    plan_before = approved(p, p.org_a)
    stage = qs.stage_id("deal", "On Hold")
    for qid, choice in (("revenue", "payment_in"), (KEY, "Part_A"),
                        ("slot:company", "Buyer_Clients"), (stage, "@auto")):
        got = await reply(p, member(p.org_a), qid, choice)
        assert got.status_code in (200, 422), (qid, got.text)
        assert versions(p, p.org_a) == before, qid
        assert approved(p, p.org_a) == plan_before, qid
    # The slot and the stage are no chat card, and `@auto` is no reply.
    assert (await reply(p, member(p.org_a), "slot:company", "Buyer_Clients")).json()[
        "detail"]["code"] == "not_in_chat"
    # /answer keeps the guards of the Field map: 403 for a member.
    denied = await call(p, member(p.org_a), "POST", f"{Q}/{KEY}/answer", {"value": "Part_A"})
    assert denied.status_code == 403
    # An admin gets 422 for @auto too.
    assert (await reply(p, _user(p.org_a), stage, "@auto")).status_code == 422
    assert versions(p, p.org_a) == before


async def test_a_member_never_writes_the_plan_through_the_tool(
    chat_env, app_engine, hold, monkeypatch,
) -> None:
    """Item 13, through the tool: ``crm_answer_question`` posts to the real
    router as the member, and the plan version stays."""
    import acb_skills.crm_tools as tools
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    p = chat_env
    seed_a(p)
    user = member(p.org_a)
    before = versions(p, p.org_a)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    monkeypatch.setattr(tools, "httpx", SimpleNamespace(AsyncClient=lambda **_kw: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://crm.test")))
    monkeypatch.setattr(tools, "_current_user_email", lambda: user.email)
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            out = await tools.crm_answer_question(KEY, "Part_A")
            refused = await tools.crm_answer_question("revenue", "@auto")
    finally:
        release_tenant(scope)
    assert out.startswith("This uses Part A, as you chose.")
    assert refused.startswith("Refused:")
    assert versions(p, p.org_a) == before
    assert proposals(p, p.org_a)[KEY][0]["value"] == "Part_A"


# ── Item 14: the tenancy fences ─────────────────────────────────────────────


async def test_org_b_cannot_answer_for_org_a_and_its_proposals_stay_in_b(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    seed_b(p)
    before_a = versions(p, p.org_a)
    cross = await reply(p, _user(p.org_b), KEY, "Part_A")
    assert cross.status_code == 409 and cross.json()["detail"]["code"] == "question_moved"
    assert versions(p, p.org_a) == before_a
    assert (await ask(p, _user(p.org_b), KEY)).json()["questions"] == []
    got = await reply(p, member(p.org_b), "purpose:Ledgers", "payment_out")
    assert got.status_code == 200, got.text
    assert "purpose:Ledgers" in proposals(p, p.org_b)
    assert proposals(p, p.org_a) == {}
    listed = await call(p, _user(p.org_a), "GET", f"{Q}?limit=50&all=1")
    assert "Ledgers" not in listed.text and "Spent" not in listed.text


# ── Item 15: a member's override never reaches another member ───────────────


async def test_one_members_override_never_reaches_another_member(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    assert (await reply(p, member(p.org_a, 1), KEY, "Part_B")).status_code == 200
    one = (await ask(p, member(p.org_a, 1), KEY)).json()["questions"][0]
    two = (await ask(p, member(p.org_a, 2), KEY)).json()["questions"][0]
    assert one["override"]["field"] == "Part_B"
    assert (two["override"], two["my_proposal"]) == (None, None)
    # The aggregate of either member still has no amount: no org fact moved.
    item = item_of(p, p.org_a, "Fee_Receipts")
    assert item is None or "amount" not in item


# ── Item 17: the step stores proposals_seen, and the card ──────────────────


async def test_the_step_raises_one_card_for_a_new_proposal(chat_env, app_engine, hold) -> None:
    from datetime import UTC, datetime

    from gateway.routes.crm.questions import questions_after_run, questions_card
    from gateway.routes.shell import feed

    from tests.unit.test_crm_questions_r8 import bound

    p = chat_env
    seed_a(p)

    async def step() -> Any:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await questions_after_run(p.org_a)

    first = await step()
    assert first is not None
    # No question opens, and a member proposes a change of a part in use.
    assert (await reply(p, member(p.org_a), ANSWERED, "Billed_Value")).status_code == 200
    await step()
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert state["proposed"] == 1 and len(state["proposals_seen"]) == 1
    assert state["new"] == 0
    now = datetime.now(UTC)
    card = await bound(p, p.org_a, lambda db: questions_card(db, p.org_a, now))
    assert card["proposed"] == 1
    signal = feed._questions_signal(card)
    assert feed._questions_line(signal.facts).endswith("1 answer proposed by your team.")
    # The next step sees nothing new: no new_at.
    stamp = state["new_at"]
    await step()
    assert cursor_state(p, p.org_a, "zoho:questions")["new_at"] == stamp


# ── Item 17a: two sessions propose at once, and both stay ───────────────────


async def test_two_sessions_propose_at_once_and_both_stay(chat_env, app_engine, monkeypatch) -> None:
    """The first session holds the lock of the cursor row while the second
    one starts. The second waits on ``SELECT ... FOR UPDATE``, then reads the
    list with the first proposal in it. Without the lock, the second would
    read the list before the first write, and one proposal would be lost."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.crm_sync import questions as qs
    from gateway.crm_sync import runs
    from gateway.routes.crm import questions as routes
    from gateway.routes.crm.core import _tenant_session

    p = chat_env
    seed_a(p)
    paused, go = asyncio.Event(), asyncio.Event()
    real_merge = runs.merge_cursor_entry

    async def slow_merge(db, org, module, bucket, key, entry):
        if any(x["member"].startswith("member1") for x in entry) and not go.is_set():
            paused.set()
            await go.wait()
        return await real_merge(db, org, module, bucket, key, entry)

    monkeypatch.setattr(runs, "merge_cursor_entry", slow_merge)

    async def propose(n: int, value: str) -> None:
        async with _tenant_session() as db:
            inputs = await routes.load(db, p.org_a, full=False)
            q = next(x for x in qs.build(inputs) if x.id == KEY)
            await routes._member_reply(db, p.org_a, f"member{n}@crm-q32-8.test", q, value,
                                       inputs)

    scope = clear_tenant()
    token = bind_tenant(p.org_a)
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            one = asyncio.create_task(propose(1, "Part_A"))
            await asyncio.wait_for(paused.wait(), 10)
            two = asyncio.create_task(propose(2, "Part_B"))
            await asyncio.sleep(0.5)
            assert not two.done()  # it waits on the lock of the row
            go.set()
            await asyncio.wait_for(asyncio.gather(one, two), 20)
    finally:
        release_tenant(token)
        release_tenant(scope)
    stored = proposals(p, p.org_a)[KEY]
    assert sorted(x["member"] for x in stored) == [
        "member1@crm-q32-8.test", "member2@crm-q32-8.test"]


# ── Item 17b: each admin write of a part clears its proposals ──────────────


def _proposed(p: Any, org: str, ids: list[str]) -> None:
    entry = [{"value": "x", "member": "member9@crm-q32-8.test", "at": "2026-10-18T09:00:00"}]
    cursor(p, org, "zoho:questions", {"proposals": dict.fromkeys(ids, entry)})


async def test_each_admin_write_of_a_part_clears_its_proposals(
    chat_env, app_engine, hold,
) -> None:
    p = chat_env
    seed_a(p)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_mapping_plans SET rules = rules || CAST(:i AS jsonb) WHERE "
            "organization_id = CAST(:o AS uuid) AND state = 'approved'"),
            {"o": p.org_a, "i": json.dumps([{
                "kind": "catalogue", "module": "Fee_Receipts", "parent": None,
                "by": {"parent": "ai"}, "ranked": {"parent": ["Buyer"]}}])})
    ids = [KEY, "key:Fee_Receipts:date", "key:Fee_Receipts:status",
           "key:Fee_Receipts:parent", "purpose:Towers", "revenue"]
    _proposed(p, p.org_a, ids)
    admin = _user(p.org_a)
    assert (await call(p, admin, "POST", f"{Q}/{KEY}/answer", {"value": "Part_A"})
            ).status_code == 200
    assert KEY not in proposals(p, p.org_a)
    assert (await reply(p, admin, "key:Fee_Receipts:status", "Track")).status_code == 200
    assert "key:Fee_Receipts:status" not in proposals(p, p.org_a)
    assert (await call(p, admin, "PUT", "/crm/catalogue/Fee_Receipts", {"date": "Paid_On"})
            ).status_code == 200
    assert "key:Fee_Receipts:date" not in proposals(p, p.org_a)
    best = await call(p, admin, "POST", f"{Q}/accept-best",
                      {"ids": ["key:Fee_Receipts:parent"], "dry_run": False})
    assert best.status_code == 200, best.text
    assert "key:Fee_Receipts:parent" not in proposals(p, p.org_a)
    assert (await call(p, admin, "POST", f"{Q}/purpose:Towers/dismiss")).status_code == 200
    assert "purpose:Towers" not in proposals(p, p.org_a)
    # Only the part that an admin wrote clears: revenue stays.
    assert list(proposals(p, p.org_a)) == ["revenue"]


# ── Item 23: no decide call ─────────────────────────────────────────────────


async def test_the_chat_card_makes_no_decide_call(
    chat_env, app_engine, hold, decide_calls,
) -> None:
    p = chat_env
    seed_a(p)
    assert (await ask(p, member(p.org_a), f"{KEY},revenue")).status_code == 200
    assert (await reply(p, member(p.org_a), KEY, "Part_A")).status_code == 200
    assert (await reply(p, member(p.org_a, 2), "revenue", "@default")).status_code == 200
    assert (await reply(p, _user(p.org_a), "revenue", "invoice")).status_code == 200
    assert decide_calls == []
