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


def _sources(*, net: bool = True) -> dict[str, cat.Source]:
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
    assert item["model_version"] == 7 and item["key"] == "billed_amount"
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
    row = await vq.save(_user(), _payload())
    assert row.version == 5 and seen["based_on"] == 4 and seen["org"] == _ORG
    saved = [o for o in seen["others"] if o.get("kind") == "query"]
    assert len(saved) == 1 and saved[0]["model_version"] == 4
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
    fixed = vq.repair(INLINE, _sources(net=False), {}, 9)
    assert fixed is not None
    assert fixed["query"]["fields"] == ["Net_Amount"]
    assert fixed["params"] == INLINE["params"] and fixed["question"] == INLINE["question"]
    assert fixed["query"]["aggregate"] == "sum" and fixed["model_version"] == 9
    assert vq.stale_reason(fixed, _sources(net=False), {}) is None
    # The same shape gives the same one repair.
    assert vq.repair(INLINE, _sources(net=False), {}, 9) == fixed
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
        return _sources(net=False), [INLINE]

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
    assert written["items"][0]["query"]["fields"] == ["Net_Amount"]
    # The test run left out the condition with no value.
    assert ran[0]["pairs"] == [] and ran[0]["field"] == "Net_Amount"

    async def fails(entity: str, **kwargs: Any) -> Any:
        raise HTTPException(status_code=422, detail={"code": "unknown_field"})

    written = _repair_env(monkeypatch, fails)
    with pytest.raises(HTTPException) as exc:
        await vq.propose_repair(_user(), "net_by_customer")
    assert _code(exc) == "repair_failed" and written == {}


async def test_at_most_one_repair_while_a_draft_holds_one(monkeypatch) -> None:
    async def runs(entity: str, **kwargs: Any) -> Any:
        return {}

    fixed = vq.repair(INLINE, _sources(net=False), {}, 8)
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
    from acb_skills import crm_tools
    from acb_skills import org_facts_tools as oft

    async def get(path: str, params: Any = None, **_kw: Any) -> Any:
        if path.startswith("/crm/verified-queries/"):
            raise crm_tools._GatewayError("off", "semantic_off", status=404)
        return {"facts": []}

    monkeypatch.setattr(oft, "_get", get)
    out = await oft.org_facts("crm", "run:billed customer=Asha")
    assert out.startswith("No facts of this organization match.")


async def test_flag_on_the_tool_matches_and_runs_with_the_mark(monkeypatch) -> None:
    from acb_skills import org_facts_tools as oft

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
