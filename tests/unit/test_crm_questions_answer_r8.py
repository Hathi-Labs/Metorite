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
import re
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


# ── CRM-Q32-9a: an amount can be the sum of several fields ──────────────────
#
# crm_platform.md §18.14.10, items 6, 7 and 8. Collection (Fee_Receipts) has
# two currency parts, Part_A and Part_B.

AMOUNT_Q = "key:Fee_Receipts:amount"
HOSTILE = ["a'--", "@amount", "x" * 300]


class _Statements:
    """Each statement that the driver gets, as text, while the block runs."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.params: list[Any] = []

    def _record(self, _conn, _cursor, statement, parameters, *_args) -> None:
        self.seen.append(statement)
        self.params.append(parameters)

    def __enter__(self) -> _Statements:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        event.listen(Engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *_exc: Any) -> None:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        event.remove(Engine, "before_cursor_execute", self._record)

    def reading_records(self) -> list[str]:
        return [s for s in self.seen if "crm_module_records" in s]


async def test_an_answer_of_a_sum_applies_at_once_and_undo_takes_it_back(
    q_env, app_engine,
) -> None:
    """Item 8: the answer route takes a list, the catalogue reads it, and
    Undo of the list sends "@auto"."""
    p = q_env
    seed_a(p)
    before = len(versions(p, p.org_a))
    body = (await call(p, _user(p.org_a), "GET", f"{Q}?limit=200")).json()
    card = next(q for q in body["shown"] if q["id"] == AMOUNT_Q)
    # The offer rule: Part_A and Part_B each fill 50% or more.
    assert [s["value"] for s in card["sums"]] == ["Part_A", "Part_B"]
    got = await answer(p, p.org_a, AMOUNT_Q, ["Part_A", "Part_B"])
    assert got.status_code == 200, got.text
    assert got.json()["line"] == "Totals of Collection now use the sum of Part A and Part B."
    assert got.json()["undo"] == {"module": "Fee_Receipts", "part": "amount",
                                  "value": "@auto", "question": None}
    item = item_of(p, p.org_a, "Fee_Receipts")
    assert (item["amount"], item["by"]["amount"]) == (["Part_A", "Part_B"], "admin")
    assert len(versions(p, p.org_a)) == before + 1
    assert AMOUNT_Q not in await open_ids(p, p.org_a)
    cat = (await call(p, _user(p.org_a), "GET", "/crm/catalogue")).json()
    entry = next(e for e in cat["entries"] if e["module"] == "Fee_Receipts")
    assert entry["fields"]["amount"]["key"] == "Part_A+Part_B"
    assert [f["key"] for f in entry["fields"]["amount"]["parts"]] == ["Part_A", "Part_B"]
    done = (await call(p, _user(p.org_a), "GET", f"{Q}?all=1&limit=200")).json()
    used = next(q for q in done["answered"] if q["id"] == AMOUNT_Q)
    assert used["current"] == ["Part_A", "Part_B"]
    undo = await call(p, _user(p.org_a), "PUT", "/crm/catalogue/Fee_Receipts",
                      {"amount": "@auto"})
    assert undo.status_code == 200, undo.text
    assert "amount" not in (item_of(p, p.org_a, "Fee_Receipts") or {})
    assert AMOUNT_Q in await open_ids(p, p.org_a)


async def test_the_put_takes_a_sum_and_refuses_a_bad_one(q_env, app_engine) -> None:
    """Item 8: PUT /crm/catalogue/{module} takes a list for amount only."""
    p = q_env
    seed_a(p)
    put = await call(p, _user(p.org_a), "PUT", "/crm/catalogue/Fee_Receipts",
                     {"amount": ["Part_B", "Part_A"]})
    assert put.status_code == 200, put.text
    assert put.json()["entry"]["fields"]["amount"]["key"] == "Part_B+Part_A"
    before = versions(p, p.org_a)
    for body in ({"amount": ["Part_A"]}, {"amount": ["Part_A", "Part_A"]},
                 {"amount": ["Part_A", "Paid_On"]}, {"date": ["Paid_On", "Part_A"]},
                 {"amount": ["Part_A", "Part_B", "Track", "Buyer", "Name", "Paid_On"]}):
        got = await call(p, _user(p.org_a), "PUT", "/crm/catalogue/Fee_Receipts", body)
        assert got.status_code == 422, (body, got.text)
        assert got.json()["detail"]["code"] == "bad_answer", body
    assert versions(p, p.org_a) == before


async def test_a_hostile_key_in_a_sum_is_a_422_and_never_reaches_the_sql_text(
    q_env, app_engine,
) -> None:
    """Item 6: each hostile key gives 422 at the answer route, the PUT and
    the sum check, and no statement holds it."""
    p = q_env
    seed_a(p)
    before = versions(p, p.org_a)
    with _Statements() as sql:
        for key in HOSTILE:
            got = await answer(p, p.org_a, AMOUNT_Q, ["Part_A", key])
            assert got.status_code == 422, (key, got.text)
            put = await call(p, _user(p.org_a), "PUT", "/crm/catalogue/Fee_Receipts",
                             {"amount": ["Part_A", key]})
            assert put.status_code == 422, (key, put.text)
            check = await call(p, _user(p.org_a), "GET",
                               f"{Q}/{AMOUNT_Q}/sum-check?fields=Part_A&fields={key}")
            assert check.status_code == 422, (key, check.text)
    assert sql.seen, "the listener saw the statements"
    for statement in sql.seen:
        for key in HOSTILE:
            assert key not in statement, (key, statement)
    assert versions(p, p.org_a) == before


async def test_the_sum_check_is_one_bounded_statement_with_the_org(q_env, app_engine) -> None:
    """Item 7: the coverage of a ticked set, and a warning when a pair holds
    the same value on half or more of the records that set both."""
    p = q_env
    seed_a(p)
    seed_b(p)
    path = f"{Q}/{AMOUNT_Q}/sum-check?fields=Part_A,Part_B"
    with _Statements() as sql:
        got = await call(p, _user(p.org_a), "GET", path)
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["records"], body["covered"], body["overlap"], body["capped"]) == (
        3, 3, 0, False)
    assert body["line"] == "These 2 fields cover 3 of 3 records (100%)."
    assert body["warnings"] == []
    assert body["pairs"] == [{"fields": ["Part_A", "Part_B"], "labels": ["Part A", "Part B"],
                              "both": 0, "equal": 0}]
    (statement,) = sql.reading_records()
    bound = sql.params[sql.seen.index(statement)]
    assert re.search(r"organization_id = CAST\(\$\d+ AS uuid\)", statement), statement
    # The fix round reads each row once, detoasted, under the LIMIT.
    assert re.search(r"ORDER BY id LIMIT \$\d+\) d\) r$", statement), statement
    assert statement.count("CAST('{}' AS jsonb) AS agf_fields") == 1
    assert "Part_" not in statement
    values = list(bound.values()) if isinstance(bound, dict) else list(bound)
    assert 10_000 in values and str(p.org_a) in {str(v) for v in values}
    # Two rows set both parts to one value, and one row sets both to two.
    with p.admin_engine.begin() as c:
        for name, fields in (("R-4", {"Part_A": 50, "Part_B": "50"}),
                             ("R-5", {"Part_A": 7, "Part_B": 7}),
                             ("R-6", {"Part_A": 1, "Part_B": 2}),
                             ("R-7", {"Track": "x"})):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Fee_Receipts', 'zoho', "
                ":e, :e, CAST(:f AS jsonb))"),
                {"o": p.org_a, "e": name, "f": json.dumps({"Name": name, **fields})})
    body = (await call(p, _user(p.org_a), "GET", path)).json()
    assert (body["records"], body["covered"], body["overlap"]) == (7, 6, 3)
    assert body["line"] == "These 2 fields cover 6 of 7 records (86%)."
    assert body["warnings"] == [
        "Part A and Part B hold the same value on 67% of records. A sum counts it twice."]


async def test_two_parts_of_zero_are_no_same_value(q_env, app_engine) -> None:
    """Fix round P2: a TDS and a bank charge that are both 0 hold no money
    twice, so they give no warning. Two equal amounts that are not 0 do."""
    p = q_env
    seed_a(p)
    with p.admin_engine.begin() as c:
        for name, fields in (("Z-1", {"Part_A": 0, "Part_B": 0}),
                             ("Z-2", {"Part_A": "0", "Part_B": 0.0}),
                             ("Z-3", {"Part_A": 0, "Part_B": "0.00"}),
                             ("Z-4", {"Part_A": 5, "Part_B": 9})):
            c.execute(text(
                "INSERT INTO crm_module_records (organization_id, module, ext_source, "
                "ext_id, name, fields) VALUES (CAST(:o AS uuid), 'Fee_Receipts', 'zoho', "
                ":e, :e, CAST(:f AS jsonb))"),
                {"o": p.org_a, "e": name, "f": json.dumps({"Name": name, **fields})})
    path = f"{Q}/{AMOUNT_Q}/sum-check?fields=Part_A,Part_B"
    body = (await call(p, _user(p.org_a), "GET", path)).json()
    assert body["pairs"][0]["both"] == 4 and body["pairs"][0]["equal"] == 0
    assert body["warnings"] == []
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_module_records (organization_id, module, ext_source, ext_id, "
            "name, fields) VALUES (CAST(:o AS uuid), 'Fee_Receipts', 'zoho', 'Z-5', 'Z-5', "
            "CAST(:f AS jsonb)), (CAST(:o AS uuid), 'Fee_Receipts', 'zoho', 'Z-6', 'Z-6', "
            "CAST(:f AS jsonb))"), {"o": p.org_a, "f": json.dumps({"Part_A": 70, "Part_B": 70})})
    body = (await call(p, _user(p.org_a), "GET", path)).json()
    assert (body["pairs"][0]["both"], body["pairs"][0]["equal"]) == (6, 2)
    assert body["warnings"] == []  # 2 of 6 is below half
    with p.admin_engine.begin() as c:
        c.execute(text(
            "DELETE FROM crm_module_records WHERE organization_id = CAST(:o AS uuid) AND "
            "ext_id IN ('Z-1', 'Z-2')"), {"o": p.org_a})
    body = (await call(p, _user(p.org_a), "GET", path)).json()
    assert (body["pairs"][0]["both"], body["pairs"][0]["equal"]) == (4, 2)
    assert body["warnings"] == [
        "Part A and Part B hold the same value on 50% of records. A sum counts it twice."]


async def test_the_sum_check_refuses_a_bad_set_before_any_record_is_read(
    q_env, app_engine, monkeypatch,
) -> None:
    """Item 7: a bad shape is a 422 before any statement, and a key that is
    not a currency field is a 422 before the read of a record. It needs a
    CRM admin and questions_allowed, and org B cannot check org A."""
    from acb_common import get_settings

    p = q_env
    seed_a(p)
    seed_b(p)
    with _Statements() as sql:
        for fields in ("Part_A", "Part_A,Part_A", "Part_A,Part_B,A,B,C,D"):
            got = await call(p, _user(p.org_a), "GET",
                             f"{Q}/{AMOUNT_Q}/sum-check?fields={fields}")
            assert got.status_code == 422, (fields, got.text)
    assert sql.seen == [], "a bad shape runs no statement"
    with _Statements() as sql:
        for fields in ("Part_A,Paid_On", "Part_A,Gone", "Part_A,Track"):
            got = await call(p, _user(p.org_a), "GET",
                             f"{Q}/{AMOUNT_Q}/sum-check?fields={fields}")
            assert got.status_code == 422, (fields, got.text)
            assert got.json()["detail"]["code"] == "bad_answer"
    assert sql.reading_records() == []
    other = await call(p, _user(p.org_a), "GET", f"{Q}/key:Fee_Receipts:date/sum-check"
                       "?fields=Part_A,Part_B")
    assert other.status_code == 422, other.text
    member = await call(p, _user(p.org_a, admin=False), "GET",
                        f"{Q}/{AMOUNT_Q}/sum-check?fields=Part_A,Part_B")
    assert member.status_code == 403, member.text
    b = await call(p, _user(p.org_b), "GET", f"{Q}/{AMOUNT_Q}/sum-check?fields=Part_A,Part_B")
    assert b.status_code == 404 and b.json()["detail"]["code"] == "unknown_module"
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    off = await call(p, _user(p.org_a), "GET", f"{Q}/{AMOUNT_Q}/sum-check?fields=Part_A,Part_B")
    assert off.status_code == 404 and off.json()["detail"]["code"] == "questions_off"
