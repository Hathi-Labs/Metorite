"""Verified queries of the CRM plan (WS-SEM SEM-8, task 3738).

Spec: ``project-docs/specs/semantic_model.md`` §3.3, §5 steps 5 to 10, §6.2
and §6.3.

Database-free, so it never skips. The real Postgres suite is
``test_crm_verified_queries_r8.py``.

1. Save: each literal becomes a named parameter. The item keeps no value,
   no total and no record text. A figure that stays in the question words is
   refused.
2. Match: by the words and the kinds of the parameters, with the term
   matcher's words.
3. Run: the stored query lowers to the parameters of the existing aggregate,
   and the run calls ``aggregate.compute``, the one body of the route.
4. Stale: derived at read time from the catalogue. A stale item never lowers.
5. Restore: an item that the restored version held comes back and is marked
   stale. A new stale item is refused.
6. One repair: the closest remaining field, kept only when it runs.
7. Flag off: the routes answer 404, and the tool and the block are those of
   before, byte for byte.
"""

from __future__ import annotations

import inspect
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.crm_sync import catalogue as cat
from gateway.routes.crm import mapping_plans as mpl
from gateway.routes.crm import verified_queries as vq

from tests.unit._crm_fakes import crm_switch_on

_ORG = "11111111-1111-1111-1111-111111111111"

MEASURE = {"kind": "measure", "key": "billed", "label": "Billed amount", "module": "Invoices",
           "aggregate": "sum", "fields": ["Net_Value"], "unit": "currency"}
QUERY = {
    "kind": "query", "key": "billed_for_customer",
    "question": "Billed amount for {customer} in {month}",
    "params": [{"name": "customer", "kind": "record"}, {"name": "month", "kind": "month"}],
    "query": {"module": "Invoices", "measure": "billed",
              "where": [{"field": "Buyer", "op": "eq", "param": "customer"},
                        {"field": "Invoice_Date", "op": "on", "param": "month"}]},
    "model_version": 3,
}
#: A query with its own aggregate, so a repair can map its field.
INLINE = {
    "kind": "query", "key": "net_by_customer",
    "question": "Net value for {customer}",
    "params": [{"name": "customer", "kind": "record"}],
    "query": {"module": "Invoices", "aggregate": "sum", "fields": ["Net_Value"],
              "where": [{"field": "Buyer", "op": "eq", "param": "customer"}]},
    "model_version": 3,
}


def _invoices(*fields: cat.Field) -> cat.Source:
    return cat.Source(module="Invoices", entity="module:Invoices", kind="module",
                      label="Invoices", fields=fields,
                      fills={"Net_Value": 0.9, "Net_Amount": 0.8, "Gross_Value": 0.5})


def _sources(*, net: bool = True, rename: bool = False) -> dict[str, cat.Source]:
    fields = [
        cat.Field("Status", "Status", "select", options=("Draft", "Sent")),
        cat.Field("Buyer", "Buyer", "lookup", target="Buyer_Clients"),
        cat.Field("Invoice_Date", "Invoice Date", "date"),
        cat.Field("Name", "Name", "text"),
        cat.Field("Gross_Value", "Gross Value", "currency"),
    ]
    if net:
        fields.insert(0, cat.Field("Net_Value", "Net Value", "currency"))
    else:
        fields.insert(0, cat.Field("Net_Amount", "Net Amount", "currency"))
    if rename:
        # The source renamed the key, and the label stays "Net Value".
        fields.append(cat.Field("NetValueNew", "Net Value", "currency"))
    return {"Invoices": _invoices(*fields)}


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "semantic_model_orgs", value, raising=False)


def _user(*, admin: bool = True) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    grants = ["feature:crm", "admin:access:manage"] if admin else ["feature:crm"]
    return UserContext(email="admin@sem8.test" if admin else "member@sem8.test",
                       role=UserRole.EMPLOYEE, access=build_access(grants),
                       organization_id=_ORG)


def _code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.detail["code"]


# ── 1. Save ──────────────────────────────────────────────────────────────────


def _payload(**over: Any) -> vq.SaveIn:
    body = {
        "question": "Billed amount for Acme Traders in October 2026",
        "module": "Invoices", "measure": "billed",
        "where": [{"field": "Buyer", "op": "eq", "literal": "Acme Traders",
                   "name": "customer"},
                  {"field": "Invoice_Date", "op": "on", "literal": "October 2026",
                   "name": "month", "kind": "month"}],
    }
    body.update(over)
    return vq.SaveIn(**body)


def test_save_keeps_parameter_names_and_no_value_or_total() -> None:
    item = vq.build_item(_payload(), _sources(), 7)
    assert item["question"] == "Billed amount for {customer} in {month}"
    assert item["params"] == [{"name": "customer", "kind": "record"},
                              {"name": "month", "kind": "month"}]
    assert item["query"]["where"] == [
        {"field": "Buyer", "op": "eq", "param": "customer"},
        {"field": "Invoice_Date", "op": "on", "param": "month"}]
    assert item["model_version"] == 7 and item["key"] == "billed_amount__customer_month"
    stored = json.dumps(item)
    for value in ("Acme", "Traders", "October", "2026", "total"):
        assert value not in stored, value
    assert set(item) <= cat._QUERY_KEYS
    assert cat.semantic_problem(item) is None


def test_save_derives_the_name_and_the_kind_from_the_catalogue() -> None:
    item = vq.build_item(_payload(where=[
        {"field": "Buyer", "op": "eq", "literal": "Acme Traders"},
        {"field": "Invoice_Date", "op": "on", "literal": "October 2026"},
    ]), _sources(), 2)
    assert item["params"] == [{"name": "buyer", "kind": "record"},
                              {"name": "invoice_date", "kind": "period"}]
    assert item["question"] == "Billed amount for {buyer} in {invoice_date}"


def test_a_literal_that_stays_in_the_question_is_refused() -> None:
    # The date is not marked as a condition, so a figure stays in the words.
    with pytest.raises(HTTPException) as exc:
        vq.build_item(_payload(where=[{"field": "Buyer", "op": "eq",
                                       "literal": "Acme Traders"}]), _sources(), 2)
    assert _code(exc) == "bad_query" and "question_value" in exc.value.detail["message"]
    with pytest.raises(HTTPException) as exc:
        vq.build_item(_payload(where=[{"field": "Buyer", "op": "eq", "literal": "Nobody"}],
                               question="Billed amount for Acme"), _sources(), 2)
    assert _code(exc) == "literal_not_in_question"


async def test_save_writes_a_new_version_through_commit(monkeypatch) -> None:
    """A save is ``commit`` of the version in use with the item merged in.
    Only an admin saves, and the flag gates it."""
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    from acb_common import get_settings

    for name, value in {"crm_mirror": True, "crm_mirror_orgs": "*"}.items():
        monkeypatch.setattr(get_settings(), name, value, raising=False)
    approved = SimpleNamespace(version=4, rules=[MEASURE])
    seen: dict[str, Any] = {}

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def lock_plan(_db: Any, by: str) -> Any:
        return approved

    async def state(_db: Any, _org: str) -> Any:
        return _sources(), [MEASURE]

    async def open_draft(_db: Any, _approved: Any) -> Any:
        return None

    async def commit(_db: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(version=5)

    monkeypatch.setattr(vq, "_tenant_session", session)
    monkeypatch.setattr(vq, "lock_plan", lock_plan)
    monkeypatch.setattr(vq, "_state", state)
    monkeypatch.setattr(vq, "open_draft", open_draft)
    monkeypatch.setattr(vq, "commit", commit)

    async def next_version(_db: Any) -> int:
        return 5

    monkeypatch.setattr(mpl, "_next_version", next_version)
    row = await vq.save(_user(), _payload())
    assert row.version == 5 and seen["based_on"] == 4 and seen["org"] == _ORG
    saved = [o for o in seen["others"] if o.get("kind") == "query"]
    # P3-5: the item names the version that the save commits.
    assert len(saved) == 1 and saved[0]["model_version"] == 5

    # P2-5: the SAME question under its key is an update. A DIFFERENT
    # question under the key is a 409.
    held = vq.build_item(_payload(), _sources(), 4)
    approved.rules = [MEASURE, held]
    seen.clear()
    await vq.save(_user(), _payload())
    assert [o["model_version"] for o in seen["others"] if o.get("kind") == "query"] == [5]
    approved.rules = [MEASURE, {**held, "question": "Billed value for {customer} in {month}"}]
    with pytest.raises(HTTPException) as exc:
        await vq.save(_user(), _payload())
    assert exc.value.status_code == 409 and _code(exc) == "key_exists"
    approved.rules = [MEASURE]
    # A shape that cannot run is refused at save, by name.
    with pytest.raises(HTTPException) as exc:
        await vq.save(_user(), _payload(where=[
            {"field": "Buyer", "op": "ne", "literal": "Acme Traders", "name": "customer"},
            {"field": "Invoice_Date", "op": "on", "literal": "October 2026",
             "name": "month", "kind": "month"}]))
    assert _code(exc) == "not_runnable" and "operator ne" in exc.value.detail["message"]
    with pytest.raises(HTTPException) as exc:
        await vq.save(_user(admin=False), _payload())
    assert exc.value.status_code == 403
    _flag(monkeypatch, "")
    with pytest.raises(HTTPException) as exc:
        await vq.save(_user(), _payload())
    assert exc.value.status_code == 404 and _code(exc) == "semantic_off"


# ── 2. Match ─────────────────────────────────────────────────────────────────


def test_match_by_words_and_parameter_kinds() -> None:
    other = {**INLINE, "key": "collected", "question": "Collected amount for {customer}"}
    asked = "What is the billed amount for Asha Rao in October 2026?"
    assert [m["key"] for m in vq.match(asked, [QUERY, INLINE, other])] == [
        "billed_for_customer"]
    # A plural and another case still match (the prefix rule of the term matcher).
    assert vq.match("BILLED AMOUNTS for asha in last month", [QUERY])
    # A month parameter needs a time in the words.
    assert vq.match("billed amount for Asha Rao", [QUERY]) == []
    # A word of the question that the member did not say: no match.
    assert vq.match("amount for Asha in October 2026", [QUERY]) == []
    assert vq.wanted_words(QUERY["question"]) == ["billed", "amount"]


# ── 3. Run: lower to the existing aggregate ──────────────────────────────────


def test_run_lowers_to_the_parameters_of_the_aggregate() -> None:
    lowered = vq.lower(QUERY, {"customer": "Asha Rao", "month": "2026-10"},
                       {"billed": MEASURE}, _sources())
    assert lowered.params() == {
        "entity": "module:Invoices", "measure": "sum", "field": "Net_Value",
        "field.Buyer": "Asha Rao", "field.Invoice_Date.from": "2026-10-01",
        "field.Invoice_Date.to": "2026-10-31"}
    inline = vq.lower({**INLINE, "query": {**INLINE["query"], "group_by": ["Invoice_Date"]}},
                      {"customer": "Asha"}, {}, _sources())
    assert (inline.group_field, inline.bucket) == ("Invoice_Date", "month")
    with pytest.raises(HTTPException) as exc:
        vq.lower(QUERY, {"customer": "Asha"}, {"billed": MEASURE}, _sources())
    assert _code(exc) == "param_missing" and exc.value.detail["params"] == ["month"]
    with pytest.raises(HTTPException) as exc:
        vq.lower(QUERY, {"customer": "A", "month": "soon"}, {"billed": MEASURE}, _sources())
    assert _code(exc) == "bad_value"
    with pytest.raises(HTTPException) as exc:
        vq.lower(QUERY, {"customer": "A", "month": "2026-10", "x": "1"},
                 {"billed": MEASURE}, _sources())
    assert _code(exc) == "param_unknown"
    assert vq.period("2026-Q4") == ("2026-10-01", "2026-12-31")
    assert vq.period("2024-02") == ("2024-02-01", "2024-02-29")
    assert vq.period("2026") == ("2026-01-01", "2026-12-31")


async def test_run_calls_the_one_aggregate_body_as_the_member(monkeypatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    from gateway.routes.crm import aggregate

    seen: dict[str, Any] = {}

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def state(_db: Any, _org: str) -> Any:
        return _sources(), [MEASURE, QUERY]

    async def compute(entity: str, **kwargs: Any) -> Any:
        seen.update(kwargs, entity=entity)
        return {"total": 1.0}

    monkeypatch.setattr(vq, "_tenant_session", session)
    monkeypatch.setattr(vq, "_state", state)
    monkeypatch.setattr(aggregate, "compute", compute)
    member = _user(admin=False)
    out = await vq.run_query(member, "billed_for_customer",
                             {"customer": "Asha", "month": "2026-10"})
    assert out["verified"] is True and out["model_version"] == 3
    assert seen["entity"] == "module:Invoices" and seen["user"] is member
    assert seen["pairs"] == [("Buyer", "Asha"), ("Invoice_Date.from", "2026-10-01"),
                             ("Invoice_Date.to", "2026-10-31")]


def test_the_module_holds_no_sql() -> None:
    """The run reuses the aggregate: this module holds no statement."""
    source = inspect.getsource(vq)
    for word in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "text("):
        assert word not in source, word


# ── 4. Stale, derived at read time ───────────────────────────────────────────


def test_stale_is_derived_from_the_catalogue_of_today() -> None:
    measures = {"billed": MEASURE}
    assert vq.stale_reason(QUERY, _sources(), measures) is None
    assert vq.stale_reason(QUERY, _sources(net=False), measures) == "measure:billed"
    assert vq.stale_reason(INLINE, _sources(net=False), {}) == "field:Net_Value"
    assert vq.stale_reason(QUERY, {}, measures) == "module:Invoices"
    assert vq.stale_reason(QUERY, _sources(), {}) == "measure:billed"
    with pytest.raises(HTTPException) as exc:
        vq.lower(INLINE, {"customer": "A"}, {}, _sources(net=False))
    assert exc.value.status_code == 409 and _code(exc) == "stale_query"
    assert exc.value.detail["stale_reason"] == "field:Net_Value"


async def test_the_match_route_hides_a_stale_query_and_shows_it_to_an_admin(
    monkeypatch,
) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def state(_db: Any, _org: str) -> Any:
        return _sources(net=False), [MEASURE, QUERY, INLINE]

    monkeypatch.setattr(vq, "_tenant_session", session)
    monkeypatch.setattr(vq, "_state", state)
    asked = vq.MatchIn(q="net value for Asha")
    member = await vq.match_verified_queries(asked, _user(admin=False))
    assert member.matches == [] and member.stale == []
    admin = await vq.match_verified_queries(asked, _user())
    assert {(s.key, s.stale_reason) for s in admin.stale} == {
        ("billed_for_customer", "measure:billed"), ("net_by_customer", "field:Net_Value")}


# ── 5. Restore keeps a stale item, a new stale item is refused ───────────────


def _load_with(monkeypatch: pytest.MonkeyPatch, sources: dict[str, cat.Source]) -> None:
    from gateway.routes.crm import catalogue as route

    async def _load(_db: Any, _org: str, **_kw: Any) -> Any:
        return "mirror", list(sources.values()), {}, []

    monkeypatch.setattr(route, "_load", _load)


async def test_a_restore_keeps_an_item_that_its_version_held(monkeypatch) -> None:
    _load_with(monkeypatch, _sources(net=False))
    # The restored version held the item, so it comes back as it was.
    await mpl._check_semantic_refs(object(), _ORG, [INLINE], None, held=[INLINE])
    # A new item that does not fit the catalogue is refused.
    with pytest.raises(HTTPException) as exc:
        await mpl._check_semantic_refs(object(), _ORG, [INLINE], None)
    assert _code(exc) == "stale_query"
    # A changed item is new against both versions, so it is refused too.
    changed = {**INLINE, "question": "Net value of {customer}"}
    with pytest.raises(HTTPException) as exc:
        await mpl._check_semantic_refs(object(), _ORG, [changed], None, held=[INLINE])
    assert _code(exc) == "stale_query"


def test_the_restore_route_passes_the_old_items_as_held() -> None:
    source = inspect.getsource(mpl.restore_mapping_plan)
    assert "held=without_catalogue(others)" in source
    assert "held" in inspect.signature(mpl.commit).parameters


# ── 6. One repair ────────────────────────────────────────────────────────────


def test_one_repair_maps_the_dropped_field_to_the_closest_one() -> None:
    fixed = vq.repair(INLINE, _sources(net=False, rename=True), {}, 9)
    assert fixed is not None
    # The label "Net Value" is the word set of the gone key: it wins.
    assert fixed["query"]["fields"] == ["NetValueNew"]
    assert fixed["params"] == INLINE["params"] and fixed["question"] == INLINE["question"]
    assert fixed["query"]["aggregate"] == "sum" and fixed["model_version"] == 9
    assert vq.stale_reason(fixed, _sources(net=False, rename=True), {}) is None
    # The same shape gives the same one repair.
    assert vq.repair(INLINE, _sources(net=False, rename=True), {}, 9) == fixed
    # A fresh query, a gone module and a gone measure get none.
    assert vq.repair(INLINE, _sources(), {}, 9) is None
    assert vq.repair(INLINE, {}, {}, 9) is None
    assert vq.repair(QUERY, _sources(net=False), {"billed": MEASURE}, 9) is None


def test_no_repair_when_no_field_is_close() -> None:
    src = _invoices(cat.Field("Buyer", "Buyer", "lookup", target="Buyer_Clients"),
                    cat.Field("Tax", "Tax", "currency"), cat.Field("Fee", "Fee", "currency"))
    assert vq.repair(INLINE, {"Invoices": src}, {}, 9) is None


def _repair_env(monkeypatch: pytest.MonkeyPatch, compute: Any, draft: Any = None) -> dict:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    from acb_common import get_settings
    from gateway.routes.crm import aggregate

    for name, value in {"crm_mirror": True, "crm_mirror_orgs": "*"}.items():
        monkeypatch.setattr(get_settings(), name, value, raising=False)
    written: dict[str, Any] = {}

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def approved(_db: Any) -> Any:
        return SimpleNamespace(version=8, rules=[INLINE])

    async def state(_db: Any, _org: str) -> Any:
        return _sources(net=False, rename=True), [INLINE]

    async def open_draft(_db: Any, _approved: Any) -> Any:
        return draft

    async def write_draft(_db: Any, **kwargs: Any) -> Any:
        written.update(kwargs)
        return SimpleNamespace(version=9)

    monkeypatch.setattr(vq, "_tenant_session", session)
    monkeypatch.setattr(vq, "_approved", approved)
    monkeypatch.setattr(vq, "_state", state)
    monkeypatch.setattr(vq, "open_draft", open_draft)
    monkeypatch.setattr(vq, "write_draft", write_draft)
    monkeypatch.setattr(aggregate, "compute", compute)
    return written


async def test_a_repair_is_kept_as_a_draft_only_when_it_runs(monkeypatch) -> None:
    ran: list[Any] = []

    async def runs(entity: str, **kwargs: Any) -> Any:
        ran.append(kwargs)
        return {"total": None}

    written = _repair_env(monkeypatch, runs)
    row = await vq.propose_repair(_user(), "net_by_customer")
    assert row.version == 9 and written["by"] == mpl.SUGGEST_ACTOR
    assert written["items"][0]["query"]["fields"] == ["NetValueNew"]
    # The test run left out the condition with no value.
    assert ran[0]["pairs"] == [] and ran[0]["field"] == "NetValueNew"

    async def fails(entity: str, **kwargs: Any) -> Any:
        raise HTTPException(status_code=422, detail={"code": "unknown_field"})

    written = _repair_env(monkeypatch, fails)
    with pytest.raises(HTTPException) as exc:
        await vq.propose_repair(_user(), "net_by_customer")
    assert _code(exc) == "repair_failed" and written == {}


async def test_at_most_one_repair_while_a_draft_holds_one(monkeypatch) -> None:
    async def runs(entity: str, **kwargs: Any) -> Any:
        return {}

    fixed = vq.repair(INLINE, _sources(net=False, rename=True), {}, 8)
    draft = SimpleNamespace(version=9, rules=[fixed])
    written = _repair_env(monkeypatch, runs, draft)
    with pytest.raises(HTTPException) as exc:
        await vq.propose_repair(_user(), "net_by_customer")
    assert _code(exc) == "repair_pending" and written == {}
    with pytest.raises(HTTPException) as exc:
        await vq.propose_repair(_user(admin=False), "net_by_customer")
    assert exc.value.status_code == 403


# ── 7. Flag off ──────────────────────────────────────────────────────────────


async def test_flag_off_the_routes_answer_404(monkeypatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "")
    with pytest.raises(HTTPException) as exc:
        await vq.match_verified_queries(vq.MatchIn(q="x"), _user())
    assert exc.value.status_code == 404 and _code(exc) == "semantic_off"
    with pytest.raises(HTTPException) as exc:
        await vq.run_query(_user(), "billed_for_customer", {})
    assert _code(exc) == "semantic_off"


#: The text of ``org_facts`` before SEM-8. The flag-off tool list is the same
#: list, byte for byte, so the tool keeps this text and its arguments.
_ORG_FACTS_DOC = (
    "The fixed facts of the organization's data: what each CRM module is\n"
    "    for, which field holds its amount, date, status and customer, each\n"
    "    approved stage, and what revenue means. A CRM admin answered each fact,\n"
    "    or Metorite and Jev found it, so use it as given. app is 'crm'. query\n"
    "    narrows the facts by a word of the subject or the meaning.")


def test_the_tool_schema_is_byte_identical() -> None:
    from acb_skills import org_facts_tools as oft

    assert inspect.cleandoc(oft.org_facts.__doc__ or "") == inspect.cleandoc(_ORG_FACTS_DOC)
    params = inspect.signature(oft.org_facts).parameters
    assert list(params) == ["app", "query", "offset"]


def _rows(*subjects: str) -> list[dict]:
    return [{"subject": s, "key": s.split(":")[1] if s.startswith("query:") else "amount",
             "meaning": f"Fact {s}.", "set_by": "admin"} for s in subjects]


async def test_flag_off_the_tool_answers_as_before(monkeypatch) -> None:
    from acb_skills import org_facts_tools as oft

    async def get(path: str, params: Any = None, **_kw: Any) -> Any:
        assert path == oft.ORG_FACTS_PATH, path
        return {"facts": _rows("field:Invoices.Net_Value")}

    async def post(path: str, body: Any) -> Any:
        raise AssertionError("no match read off the flag")

    monkeypatch.setattr(oft, "_get", get)
    monkeypatch.setattr(oft, "_post", post)
    monkeypatch.setattr(oft, "_member_words", lambda: "billed amount for Asha")
    out = await oft.org_facts("crm")
    assert out == ("Facts of this organization (fixed answers):\n"
                   "- Fact field:Invoices.Net_Value. (field:Invoices.Net_Value; set by admin)")
    # The block of an org with no verified question is the block of before.
    rows = _rows(*(f"field:M.F{i}" for i in range(14)))
    block = oft.crm_facts_block(rows)
    assert "verified" not in block and len(block.splitlines()) == oft.BLOCK_LINES


async def test_the_run_form_off_the_flag_reads_the_facts(monkeypatch) -> None:
    """P2-2: off the flag, no facts read served a verified question. So the
    run form makes no other gateway call and spends no read: the facts read
    of before answers, with the same call."""
    from acb_skills import crm_tools
    from acb_skills import org_facts_tools as oft

    oft._VERIFIED_SEEN.set(False)
    sent: list[tuple[str, Any]] = []

    async def get(path: str, params: Any = None, **_kw: Any) -> Any:
        sent.append((path, params))
        return {"facts": []}

    reads = crm_tools.RunReads()
    monkeypatch.setattr(oft, "_get", get)
    monkeypatch.setattr(oft, "_run_reads", lambda: reads)
    monkeypatch.setattr(crm_tools, "_run_reads", lambda: reads)
    out = await oft.org_facts("crm", "run:billed customer=Asha")
    assert out.startswith("No facts of this organization match.")
    assert sent == [(oft.ORG_FACTS_PATH, {"app": "crm", "q": "run:billed customer=Asha"})]
    assert (reads.total, reads.aggregates) == (0, 0)


async def test_flag_on_the_tool_matches_and_runs_with_the_mark(monkeypatch) -> None:
    from acb_skills import org_facts_tools as oft

    oft._VERIFIED_SEEN.set(False)
    sent: list[tuple[str, Any]] = []

    async def get(path: str, params: Any = None, **_kw: Any) -> Any:
        sent.append((path, params))
        if path == oft.ORG_FACTS_PATH:
            return {"facts": _rows("query:billed_for_customer")}
        return {"key": "billed_for_customer", "question": QUERY["question"],
                "verified": True, "model_version": 3, "entity": "module:Invoices",
                "aggregate": {"entity": "module:Invoices", "measure": "sum",
                              "field": "Net_Value", "field.Buyer": "Asha Rao"}}

    async def post(path: str, body: Any) -> Any:
        sent.append((path, body))
        return {"matches": [{"key": "billed_for_customer", "question": QUERY["question"],
                             "params": QUERY["params"], "model_version": 3}]}

    async def total(slug: str, params: dict, page: Any) -> str:
        sent.append(("aggregate", (slug, params)))
        return "Invoices, the sum of Net Value: INR 12"

    monkeypatch.setattr(oft, "_get", get)
    monkeypatch.setattr(oft, "_post", post)
    monkeypatch.setattr(oft, "_aggregate_text", total)
    monkeypatch.setattr(oft, "_member_words", lambda: "billed amount for Asha Rao in Oct 2026")
    listed = await oft.org_facts("crm")
    assert 'key billed_for_customer, give customer (record), month (month)' in listed
    assert oft.RUN_LINE in listed
    assert ("/crm/verified-queries/match", {"q": "billed amount for Asha Rao in Oct 2026"}) in sent
    sent.clear()
    out = await oft.org_facts("crm", "run:billed_for_customer customer=Asha Rao; month=2026-10")
    assert out.startswith('Verified question (model version 3): "Billed amount for')
    assert out.endswith("INR 12")
    assert sent[0] == ("/crm/verified-queries/billed_for_customer",
                       {"param.customer": "Asha Rao", "param.month": "2026-10"})
    assert sent[1][0] == "aggregate" and sent[1][1][0] == "module:Invoices"
    # A key that is no key never reaches a URL.
    sent.clear()
    assert "No verified question" in await oft.org_facts("crm", "run:../crm/x a=b")
    assert sent == []
    # The block names the verified questions inside the same cap.
    rows = [*_rows(*(f"field:M.F{i}" for i in range(14))), *_rows("query:a")]
    block = oft.crm_facts_block(rows).splitlines()
    assert len(block) == oft.BLOCK_LINES and block[-1].startswith("- 1 verified questions")


# ── Review round 1 (P1 to P3) ────────────────────────────────────────────────


def _two_field(**over: Any) -> dict:
    item = {**INLINE, "key": "net_and_gross", "question": "Net and gross for {customer}",
            "query": {**INLINE["query"], "fields": ["Net_Value", "Gross_Value"]}}
    item.update(over)
    return item


def test_a_sum_of_two_fields_lowers_to_one_total() -> None:
    """P1-A: ONE total of A+B, never the split ``fields=A,B``."""
    lowered = vq.lower(_two_field(), {"customer": "Asha"}, {}, _sources())
    assert (lowered.measure, lowered.field, lowered.fields) == (
        "sum", "Net_Value+Gross_Value", None)
    assert "fields" not in lowered.params()
    two = {**MEASURE, "key": "both", "fields": ["Net_Value", "Gross_Value"]}
    query = {**QUERY, "query": {**QUERY["query"], "measure": "both"}}
    assert vq.lower(query, {"customer": "A", "month": "2026-10"}, {"both": two},
                    _sources()).field == "Net_Value+Gross_Value"
    core = cat.Source(module="deals", entity="deals", kind="core", label="Deals",
                      fields=(cat.Field("Net_Value", "Net", "currency"),
                              cat.Field("Gross_Value", "Gross", "currency"),
                              cat.Field("Buyer", "Buyer", "lookup", target="x")))
    item = _two_field(query={**_two_field()["query"], "module": "deals"})
    with pytest.raises(HTTPException) as exc:
        vq.lower(item, {"customer": "A"}, {}, {"deals": core})
    assert _code(exc) == "not_runnable"


def test_a_repair_with_no_clear_winner_is_none() -> None:
    """P2-1: "Net Amount" and "Gross Value" each share one word with
    "Net_Value", so no field clearly stands for it."""
    assert vq.repair(INLINE, _sources(net=False), {}, 9) is None
    src = _invoices(cat.Field("Buyer", "Buyer", "lookup", target="Buyer_Clients"),
                    cat.Field("Net_Value_A", "Net Value A", "currency"),
                    cat.Field("Net_Value_B", "Net Value B", "currency"))
    # Both share the SAME two words: the fill share breaks the tie.
    src = cat.Source(**{**src.__dict__, "fills": {"Net_Value_A": 0.2, "Net_Value_B": 0.7}})
    fixed = vq.repair(INLINE, {"Invoices": src}, {}, 9)
    assert fixed is not None and fixed["query"]["fields"] == ["Net_Value_B"]


def test_a_number_between_splits_and_checks_each_side() -> None:
    """P2-3: a range of numbers lowers end to end."""
    item = {**INLINE, "key": "net_range", "question": "Net value between {range}",
            "params": [{"name": "range", "kind": "number"}],
            "query": {**INLINE["query"],
                      "where": [{"field": "Gross_Value", "op": "between", "param": "range"}]}}
    lowered = vq.lower(item, {"range": "1000..5000"}, {}, _sources())
    assert lowered.pairs == (("Gross_Value.from", "1000"), ("Gross_Value.to", "5000"))
    for bad in ("1000..x", "1..2..3", "..5"):
        with pytest.raises(HTTPException) as exc:
            vq.lower(item, {"range": bad}, {}, _sources())
        assert _code(exc) == "bad_value", bad


def test_save_refuses_each_shape_that_cannot_run() -> None:
    """P2-4: what ``lower`` refuses, the save refuses by name."""
    measures = {"billed": MEASURE}
    for op in ("ne", "in", "gt", "lt", "contains"):
        with pytest.raises(HTTPException) as exc:
            vq.build_item(_payload(where=[
                {"field": "Buyer", "op": op, "literal": "Acme Traders", "name": "customer"},
                {"field": "Invoice_Date", "op": "on", "literal": "October 2026",
                 "name": "month", "kind": "month"}]), _sources(), 2, measures)
        assert _code(exc) == "not_runnable" and exc.value.detail["why"] == f"operator {op}"
    with pytest.raises(HTTPException) as exc:
        vq.build_item(_payload(group_by=["Status", "Buyer"]), _sources(), 2, measures)
    assert exc.value.detail["why"] == "more than one group field"
    wide = {**MEASURE, "filters": [{"field": "Status", "options": ["Draft", "Sent"]}]}
    with pytest.raises(HTTPException) as exc:
        vq.build_item(_payload(), _sources(), 2, {"billed": wide})
    assert exc.value.detail["why"] == "a fixed filter of more than one option"
    # The matcher never offers a stored shape that cannot run.
    stored = {**QUERY, "query": {**QUERY["query"], "group_by": ["Status", "Buyer"]}}
    assert vq.runnable_problem(stored, measures, _sources()) == "more than one group field"
    assert vq.runnable_problem(QUERY, measures, _sources()) is None


async def test_the_match_route_never_offers_a_query_that_cannot_run(monkeypatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")
    stored = {**QUERY, "query": {**QUERY["query"], "group_by": ["Status", "Buyer"]}}

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def state(_db: Any, _org: str) -> Any:
        return _sources(), [MEASURE, stored]

    monkeypatch.setattr(vq, "_tenant_session", session)
    monkeypatch.setattr(vq, "_state", state)
    got = await vq.match_verified_queries(
        vq.MatchIn(q="billed amount for Asha in October 2026"), _user(admin=False))
    assert got.matches == []


def test_two_questions_with_the_same_words_get_two_keys() -> None:
    """P2-5: the key holds the parameter names."""
    one = vq.build_item(vq.SaveIn(question="Net value for Acme", module="Invoices",
                                  aggregate="sum", fields=["Net_Value"],
                                  where=[{"field": "Buyer", "op": "eq", "literal": "Acme",
                                          "name": "customer"}]), _sources(), 2)
    two = vq.build_item(vq.SaveIn(question="Net value in October 2026", module="Invoices",
                                  aggregate="sum", fields=["Net_Value"],
                                  where=[{"field": "Invoice_Date", "op": "on",
                                          "literal": "October 2026", "name": "month"}]),
                        _sources(), 2)
    assert (one["key"], two["key"]) == ("net_value__customer", "net_value__month")


def test_a_where_on_a_fixed_filter_field_never_overwrites_it() -> None:
    """P3-1: one list of pairs for the route and for compute. A condition
    on the field of a fixed filter is refused, never written over it."""
    fixed = {**MEASURE, "filters": [{"field": "Status", "options": ["Sent"]}]}
    item = {**QUERY, "params": [{"name": "status", "kind": "option"}],
            "question": "Billed amount with {status}",
            "query": {"module": "Invoices", "measure": "billed",
                      "where": [{"field": "Status", "op": "eq", "param": "status"}]}}
    with pytest.raises(HTTPException) as exc:
        vq.lower(item, {"status": "Draft"}, {"billed": fixed}, _sources())
    assert exc.value.detail["why"] == "two conditions on one field"
    assert vq.runnable_problem(item, {"billed": fixed}, _sources()) is not None
    lowered = vq.lower(QUERY, {"customer": "A", "month": "2026-10"},
                       {"billed": {**MEASURE, "filters": [{"field": "Status",
                                                            "options": ["Sent"]}]}},
                       _sources())
    assert lowered.params() == {"entity": lowered.entity, "measure": lowered.measure,
                                "field": lowered.field,
                                **{f"field.{n}": v for n, v in lowered.pairs}}
    assert len(lowered.params()) == 3 + len(lowered.pairs)


async def test_the_lower_route_names_the_stale_reason_to_an_admin_only(monkeypatch) -> None:
    """P3-2."""
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "*")

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def state(_db: Any, _org: str) -> Any:
        return _sources(net=False), [INLINE]

    monkeypatch.setattr(vq, "_tenant_session", session)
    monkeypatch.setattr(vq, "_state", state)
    request = SimpleNamespace(query_params={"param.customer": "A"})
    with pytest.raises(HTTPException) as exc:
        await vq.lower_verified_query("net_by_customer", request, _user(admin=False))
    assert exc.value.status_code == 409 and "stale_reason" not in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await vq.lower_verified_query("net_by_customer", request, _user())
    assert exc.value.detail["stale_reason"] == "field:Net_Value"


def test_an_option_value_must_be_an_option_key() -> None:
    """P3-3."""
    item = {**INLINE, "key": "net_by_status", "question": "Net value with {status}",
            "params": [{"name": "status", "kind": "option"}],
            "query": {**INLINE["query"],
                      "where": [{"field": "Status", "op": "eq", "param": "status"}]}}
    assert vq.lower(item, {"status": "sent"}, {}, _sources()).pairs == (("Status", "Sent"),)
    with pytest.raises(HTTPException) as exc:
        vq.lower(item, {"status": "Lost"}, {}, _sources())
    assert _code(exc) == "not_an_option" and exc.value.detail["options"] == ["Draft", "Sent"]


def test_a_literal_replaces_a_whole_word_span_once() -> None:
    """P3-4: "in" never hits "Invoices" and never hits a later "in"."""
    assert vq._swap("Invoices of Pune in Pune", "Pune", "city") == "Invoices of {city} in Pune"
    assert vq._swap("Net value in   Pune", "in", "x") == "Net value {x}   Pune"
    # build_item folds the spaces of the question, and _swap those of the literal.
    assert vq._swap("Net value for Acme Traders", "Acme   Traders", "c") == "Net value for {c}"
    with pytest.raises(HTTPException):
        vq._swap("Invoices", "in", "x")


def test_a_retyped_where_field_is_stale() -> None:
    """P3-5: a date that became text no longer takes a month."""
    src = _sources()["Invoices"]
    fields = tuple(cat.Field("Invoice_Date", "Invoice Date", "text")
                   if f.key == "Invoice_Date" else f for f in src.fields)
    retyped = {"Invoices": cat.Source(**{**src.__dict__, "fields": fields})}
    assert vq.stale_reason(QUERY, retyped, {"billed": MEASURE}) == "type:Invoice_Date"
    assert vq.stale_reason(QUERY, _sources(), {"billed": MEASURE}) is None


async def test_a_restored_item_of_an_illegal_shape_is_never_served(monkeypatch) -> None:
    """P3-6: ``held`` skips the shape check of a restored item, and each
    reader drops it."""
    from gateway.routes.crm import facts

    illegal = {**INLINE, "total": 4242}
    mpl.check_kinds([illegal], semantic=True, in_use=[illegal])
    _load_with(monkeypatch, _sources())
    await mpl._check_semantic_refs(object(), _ORG, [illegal], None, held=[illegal])
    assert vq.queries_of([illegal]) == []
    assert facts.semantic_facts([illegal], list(_sources().values()), None) == []


async def test_a_long_message_or_a_failed_match_keeps_the_facts(monkeypatch) -> None:
    """P2-2: the words are cut to the match limit, and any failure of the
    match read serves the facts with no verified lines."""
    from acb_skills import org_facts_tools as oft

    sent: list[Any] = []

    async def get(path: str, params: Any = None, **_kw: Any) -> Any:
        return {"facts": _rows("query:billed_for_customer", "field:Invoices.Net_Value")}

    async def post(path: str, body: Any) -> Any:
        sent.append(body)
        raise RuntimeError("422 from the gateway")

    monkeypatch.setattr(oft, "_get", get)
    monkeypatch.setattr(oft, "_post", post)
    monkeypatch.setattr(oft, "_member_words", lambda: "billed " * 1000)
    out = await oft.org_facts("crm")
    assert len(sent[0]["q"]) == oft.MATCH_WORDS_MAX
    assert out.startswith("Facts of this organization (fixed answers):")
    assert "Fact field:Invoices.Net_Value." in out and oft.RUN_LINE not in out
