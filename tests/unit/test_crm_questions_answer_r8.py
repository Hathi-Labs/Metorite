"""R8 — answer, dismiss and batch, through the real router (WS-53 CRM-Q32-2).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.6 and the CRM-Q32-2
done-when list of §18.14.10.

Against a REAL Postgres, as the non-privileged role of the phase-4
rehearsal, with the seed of ``test_crm_questions_r8.py``.

1. Each kind of answer writes as the table of §18.14.6 says, and each error
   answers its code (items 1 and 7).
2. ``"@auto"`` puts back the cached part of the pipeline with ``by: ai`` and
   its rank, in ``on``, and removes the part when the cache holds none
   (item 2).
3. A dismissal writes ``zoho:questions``, and a changed hash asks again
   (item 4).
4. The batch: a dry run writes nothing, and the apply writes one plan
   version. A ``slot`` id gives 422 (item 5).
5. "None of these" on a ``slot`` writes ``zoho:questions`` and leaves
   ``zoho:map_dismissed`` alone (item 8).
6. An admin of org B cannot answer or dismiss a question of org A (item 9).
7. The actor of each version is the admin of the session (item 10).
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    call,
    cursor,
    cursor_state,
    defs,
    ids,
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


def versions(p: Any, org: str) -> list[tuple[int, str, str]]:
    with p.admin_engine.begin() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT version, state, created_by FROM crm_mapping_plans WHERE "
            "organization_id = CAST(:o AS uuid) ORDER BY version"), {"o": org})]


def approved(p: Any, org: str) -> list[dict]:
    with p.admin_engine.begin() as c:
        (rules,) = c.execute(text(
            "SELECT rules FROM crm_mapping_plans WHERE organization_id = CAST(:o AS uuid) "
            "AND state = 'approved'"), {"o": org}).first()
    return list(rules)


def item_of(p: Any, org: str, module: str) -> dict | None:
    return next((i for i in approved(p, org)
                 if i.get("kind") == "catalogue" and i.get("module") == module), None)


async def open_ids(p: Any, org: str) -> list[str]:
    got = await call(p, _user(org), "GET", f"{Q}?limit=200")
    assert got.status_code == 200, got.text
    return ids(got.json())


async def answer(p: Any, org: str, qid: str, value: Any, **more: Any) -> Any:
    return await call(p, _user(org), "POST", f"{Q}/{qid}/answer", {"value": value, **more})


# ── 1. Each kind, and each error ────────────────────────────────────────────


async def test_a_key_field_answer_applies_at_once_by_the_admin(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    before = len(versions(p, p.org_a))
    got = await answer(p, p.org_a, "key:Fee_Receipts:amount", "Part_B")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["line"] == "Totals of Collection now use Part B."
    assert body["undo"] == {"module": "Fee_Receipts", "part": "amount", "value": "@auto",
                            "question": None}
    item = item_of(p, p.org_a, "Fee_Receipts")
    assert (item["amount"], item["by"]["amount"]) == ("Part_B", "admin")
    rows = versions(p, p.org_a)
    assert len(rows) == before + 1 and rows[-1][2] == "admin@crm-q32-r8.test"
    assert "key:Fee_Receipts:amount" not in await open_ids(p, p.org_a)
    # "None of these" stores null by admin: the part is answered.
    got = await answer(p, p.org_a, "key:Fee_Receipts:date", None)
    assert got.status_code == 200, got.text
    item = item_of(p, p.org_a, "Fee_Receipts")
    assert ("date" in item, item["date"], item["by"]["date"]) == (True, None, "admin")
    # The question is no longer open: 409 question_moved.
    again = await answer(p, p.org_a, "key:Fee_Receipts:amount", "Part_A")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "question_moved"


async def test_a_field_of_the_wrong_type_or_a_word_that_is_no_choice_is_422(
    q_env, app_engine,
) -> None:
    p = q_env
    seed_a(p)
    before = versions(p, p.org_a)
    for qid, value in (("key:Fee_Receipts:amount", "Paid_On"),
                       ("key:Fee_Receipts:amount", "No_Such_Field"),
                       ("purpose:Towers", "castle"), ("revenue", "profit"),
                       ("revenue", None)):
        got = await answer(p, p.org_a, qid, value)
        assert got.status_code == 422, (qid, value, got.text)
        assert got.json()["detail"]["code"] == "bad_answer"
    assert versions(p, p.org_a) == before


async def test_a_purpose_and_revenue_apply_at_once(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    got = await answer(p, p.org_a, "purpose:Towers", "asset")
    assert got.status_code == 200, got.text
    assert item_of(p, p.org_a, "Towers")["by"] == {"purpose": "admin"}
    # An open draft (a slot answer) rides onto the version of the next answer.
    slot = await answer(p, p.org_a, "slot:company", "Buyer_Clients")
    assert slot.status_code == 200, slot.text
    got = await answer(p, p.org_a, "revenue", "payment_in")
    assert got.status_code == 200, got.text
    assert item_of(p, p.org_a, "@org") == {
        "kind": "catalogue", "module": "@org", "revenue": "payment_in",
        "by": {"revenue": "admin"}}
    cat = (await call(p, _user(p.org_a), "GET", "/crm/catalogue")).json()
    assert cat["revenue"] == {"value": "payment_in", "by": "admin"}
    assert "revenue" not in await open_ids(p, p.org_a)


async def test_a_stage_value_commits_under_the_lock_and_its_undo_takes_it_back(
    q_env, app_engine,
) -> None:
    from gateway.crm_sync import questions as qs

    p = q_env
    seed_a(p)
    qid = qs.stage_id("deal", "On Hold")
    plan = (await call(p, _user(p.org_a), "GET", Q)).json()["plan"]
    moved = await answer(p, p.org_a, qid, "open", version=plan + 7)
    assert moved.status_code == 409 and moved.json()["detail"]["code"] == "plan_moved"
    bad = await answer(p, p.org_a, qid, "maybe")
    assert bad.status_code == 422
    got = await answer(p, p.org_a, qid, "open", version=plan)
    assert got.status_code == 200, got.text
    assert (got.json()["moved"], got.json()["undo"]["question"]) == (2, qid)
    stage = next(i for i in approved(p, p.org_a) if i.get("kind") == "stage")
    assert stage["values"] == {"Won": "won", "On Hold": "open"}
    assert qid not in await open_ids(p, p.org_a)
    undo = await answer(p, p.org_a, qid, "@auto")
    assert undo.status_code == 200, undo.text
    stage = next(i for i in approved(p, p.org_a) if i.get("kind") == "stage")
    assert stage["values"] == {"Won": "won"}
    assert qid in await open_ids(p, p.org_a)


async def test_a_slot_answer_writes_the_role_into_the_open_draft(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    got = await answer(p, p.org_a, "slot:company", "Buyer_Clients")
    assert got.status_code == 200, got.text
    draft = got.json()["version"]
    rows = versions(p, p.org_a)
    assert (rows[-1][0], rows[-1][1], rows[-1][2]) == (draft, "draft", "admin@crm-q32-r8.test")
    # Nothing is approved yet: the panel approves it after a preview.
    assert not [i for i in approved(p, p.org_a) if i.get("slot") == "company"]
    assert "slot:company" not in await open_ids(p, p.org_a)
    listed = await call(p, _user(p.org_a), "GET", "/crm/mapping/suggestions")
    assert [s["module"] for s in listed.json()["suggestions"]] == ["Buyer_Clients"]


async def test_none_of_these_on_a_slot_leaves_map_dismissed_alone(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    cursor(p, p.org_a, "zoho:map_pipeline", {"schema": {"slots": "s1"}, "items": {}})
    before = cursor_state(p, p.org_a, "zoho:map_dismissed")
    got = await answer(p, p.org_a, "slot:company", None)
    assert got.status_code == 200, got.text
    assert cursor_state(p, p.org_a, "zoho:map_dismissed") == before is None
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert state["dismissed"]["slot:company"] == {"schema": "s1"}
    assert "slot:company" not in await open_ids(p, p.org_a)


# ── 2. ``"@auto"`` in ``on`` ─────────────────────────────────────────────────


async def test_auto_puts_back_the_cached_part_or_removes_it(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings
    from gateway import decide_features as df

    p = q_env
    seed_a(p)
    monkeypatch.setattr(get_settings(), "decide_feature_modes", "crm.map_catalogue=on",
                        raising=False)
    monkeypatch.setattr(get_settings(), "crm_decide_orgs", "*", raising=False)
    df._parse_modes.cache_clear()
    df._parse_orgs.cache_clear()
    cached = {"kind": "catalogue", "module": "Fee_Receipts", "amount": None,
              "by": {"amount": "ai"}, "ranked": {"amount": ["Part_B", "Part_A"]}}
    cursor(p, p.org_a, "zoho:map_pipeline", {
        "schema": {"catalogue:Fee_Receipts": "h"},
        "items": {"catalogue:Fee_Receipts": [cached]}})
    admin = _user(p.org_a)
    put = await call(p, admin, "PUT", "/crm/catalogue/Fee_Receipts",
                     {"amount": "Part_A", "status": "Track"})
    assert put.status_code == 200, put.text
    assert item_of(p, p.org_a, "Fee_Receipts")["by"] == {"amount": "admin", "status": "admin"}
    back = await call(p, admin, "PUT", "/crm/catalogue/Fee_Receipts",
                      {"amount": "@auto", "status": "@auto"})
    assert back.status_code == 200, back.text
    item = item_of(p, p.org_a, "Fee_Receipts")
    # The cache holds the amount: it comes back by ai with its rank. It holds
    # no status: the part leaves the item.
    assert (item["amount"], item["by"], item["ranked"]) == (
        None, {"amount": "ai"}, {"amount": ["Part_B", "Part_A"]})
    assert "status" not in item
    # In `on`, the amount closed as null by ai is a question again, best first.
    body = (await call(p, admin, "GET", f"{Q}?limit=200")).json()
    q = next(q for q in body["shown"] if q["id"] == "key:Fee_Receipts:amount")
    assert (q["reason"], q["choices"][0]["value"], q["choices"][0]["best"]) == (
        "unsure", "Part_B", True)
    assert "key:Fee_Receipts:status" not in ids(body)  # open, and in `on` the AI asks it


# ── 3. Dismiss ──────────────────────────────────────────────────────────────


async def test_a_dismissal_holds_until_the_hash_changes(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    got = await call(p, _user(p.org_a), "POST", f"{Q}/purpose:Towers/dismiss")
    assert got.status_code == 200, got.text
    state = cursor_state(p, p.org_a, "zoho:questions")
    assert state["dismissed"]["purpose:Towers"]["schema"]
    assert "purpose:Towers" not in await open_ids(p, p.org_a)
    # A new field of the module changes its hash: the question shows again.
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_field_defs (organization_id, entity, key, label, type, "
            "source, provider_api_name, position) VALUES (CAST(:o AS uuid), "
            "'module:Towers', 'Height', 'Height', 'number', 'zoho', 'Height', 9)"),
            {"o": p.org_a})
    assert "purpose:Towers" in await open_ids(p, p.org_a)
    revenue = await call(p, _user(p.org_a), "POST", f"{Q}/revenue/dismiss")
    assert revenue.status_code == 422, revenue.text
    gone = await call(p, _user(p.org_a), "POST", f"{Q}/purpose:Nothing/dismiss")
    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "question_moved"


# ── 4. The batch ────────────────────────────────────────────────────────────


async def test_the_batch_dry_run_writes_nothing_and_the_apply_writes_one_version(
    q_env, app_engine,
) -> None:
    p = q_env
    seed_a(p)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_mapping_plans SET rules = rules || CAST(:r AS jsonb) WHERE "
            "organization_id = CAST(:o AS uuid)"),
            {"o": p.org_a, "r": json.dumps([
                {"kind": "catalogue", "module": "Fee_Receipts", "amount": None,
                 "status": None, "by": {"amount": "ai", "status": "ai"},
                 "ranked": {"amount": ["Part_B"], "status": ["Track"]}},
                {"kind": "catalogue", "module": "Towers", "purpose": None,
                 "by": {"purpose": "ai"}, "ranked": {"purpose": ["asset"]}}])})
    wanted = ["key:Fee_Receipts:amount", "key:Fee_Receipts:status", "purpose:Towers"]
    before = versions(p, p.org_a)
    dry = await call(p, _user(p.org_a), "POST", f"{Q}/accept-best",
                     {"ids": wanted, "dry_run": True})
    assert dry.status_code == 200, dry.text
    assert [(i["id"], i["value"]) for i in dry.json()["items"]] == [
        ("key:Fee_Receipts:amount", "Part_B"), ("key:Fee_Receipts:status", "Track"),
        ("purpose:Towers", "asset")]
    assert dry.json()["items"][0]["line"] == "Totals of Collection now use Part B."
    assert versions(p, p.org_a) == before, "a dry run writes nothing"
    slot = await call(p, _user(p.org_a), "POST", f"{Q}/accept-best",
                      {"ids": [*wanted, "slot:company"], "dry_run": False})
    assert slot.status_code == 422, slot.text
    assert versions(p, p.org_a) == before
    got = await call(p, _user(p.org_a), "POST", f"{Q}/accept-best",
                     {"ids": wanted, "dry_run": False})
    assert got.status_code == 200, got.text
    rows = versions(p, p.org_a)
    assert len(rows) == len(before) + 1, "one plan version for the batch"
    assert len(got.json()["undo"]) == 3
    item = item_of(p, p.org_a, "Fee_Receipts")
    assert (item["amount"], item["status"], item["by"]) == (
        "Part_B", "Track", {"amount": "admin", "status": "admin"})
    assert "ranked" not in item
    assert item_of(p, p.org_a, "Towers")["purpose"] == "asset"


# ── 6. Org B never answers for org A ────────────────────────────────────────


async def test_an_admin_of_org_b_cannot_answer_or_dismiss_a_question_of_org_a(
    q_env, app_engine,
) -> None:
    p = q_env
    seed_a(p)
    seed_b(p)
    a_before = approved(p, p.org_a)
    for path, body in ((f"{Q}/key:Fee_Receipts:amount/answer", {"value": "Part_A"}),
                       (f"{Q}/purpose:Towers/dismiss", None),
                       (f"{Q}/accept-best", {"ids": ["purpose:Towers"], "dry_run": False})):
        got = await call(p, _user(p.org_b), "POST", path, body)
        assert got.status_code == 409, (path, got.text)
        assert got.json()["detail"]["code"] == "question_moved"
    assert approved(p, p.org_a) == a_before
    assert cursor_state(p, p.org_a, "zoho:questions") is None
    assert "purpose:Towers" in await open_ids(p, p.org_a)


async def test_a_member_cannot_answer_and_outside_the_list_is_404(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings

    p = q_env
    seed_a(p)
    member = await call(p, _user(p.org_a, admin=False), "POST",
                        f"{Q}/purpose:Towers/answer", {"value": "asset"})
    assert member.status_code == 403, member.text
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    off = await answer(p, p.org_a, "purpose:Towers", "asset")
    assert off.status_code == 404 and off.json()["detail"]["code"] == "questions_off"
    assert item_of(p, p.org_a, "Towers") is None


# ── Fix round item 8: the Undo of a dismissal ───────────────────────────────


async def test_an_undo_of_a_dismissal_opens_the_question_again(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    seed_b(p)
    cursor(p, p.org_a, "zoho:map_pipeline", {"schema": {"slots": "s1"}, "items": {}})
    got = await call(p, _user(p.org_a), "POST", f"{Q}/purpose:Towers/dismiss")
    assert got.status_code == 200, got.text
    none = await answer(p, p.org_a, "slot:company", None)
    assert none.status_code == 200, none.text
    opened = await open_ids(p, p.org_a)
    assert "purpose:Towers" not in opened and "slot:company" not in opened
    # Org B cannot undo a dismissal of org A, and org A keeps it.
    other = await call(p, _user(p.org_b), "DELETE", f"{Q}/purpose:Towers/dismiss")
    assert other.status_code == 404 and other.json()["detail"]["code"] == "not_dismissed"
    assert "purpose:Towers" in cursor_state(p, p.org_a, "zoho:questions")["dismissed"]
    member = await call(p, _user(p.org_a, admin=False), "DELETE",
                        f"{Q}/purpose:Towers/dismiss")
    assert member.status_code == 403, member.text
    for qid in ("purpose:Towers", "slot:company"):
        undo = await call(p, _user(p.org_a), "DELETE", f"{Q}/{qid}/dismiss")
        assert undo.status_code == 200, undo.text
        assert undo.json() == {"id": qid, "dismissed": False}
    opened = await open_ids(p, p.org_a)
    assert "purpose:Towers" in opened and "slot:company" in opened
    assert cursor_state(p, p.org_a, "zoho:questions")["dismissed"] == {}
    again = await call(p, _user(p.org_a), "DELETE", f"{Q}/purpose:Towers/dismiss")
    assert again.status_code == 404
