"""The semantic kinds of the CRM plan (WS-SEM SEM-7, task 3737).

Spec: ``project-docs/specs/semantic_model.md`` §3.2 to §3.4, §6.2 and §6.3.

Database-free, so it never skips. The serve path on a real Postgres is in
``test_org_facts_r8.py``.

1. The flag: ``SEMANTIC_MODEL_ORGS`` has one reader,
   ``flags.semantic_model_allowed``, in the grammar of ``CRM_ORGS``. The
   default is empty.
2. Each kind (``measure``, ``join``, ``query``) passes ``check_kinds`` in its
   legal shape, and each illegal shape is a 422 that names the part.
3. No value: a total, a parameter value or a literal in the question words
   is refused by name.
4. Flag off: ``check_kinds``, ``kind_key`` and ``merge_kinds`` leave an item
   of these kinds alone, and a snapshot keeps it byte for byte.
5. The catalogue check, and the facts: a stale item serves nothing, a query
   fact shows its question words and parameter names only, and the provider
   serves none with the flag off.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.crm_sync import catalogue as cat
from gateway.routes.crm import facts, flags
from gateway.routes.crm import mapping_plans as mpl

from tests.unit._crm_fakes import crm_switch_on

_ORG = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"

MEASURE = {
    "kind": "measure", "key": "billed", "label": "Billed amount", "module": "Invoices",
    "aggregate": "sum", "fields": ["Net_Value"],
    "filters": [{"field": "Status", "options": ["Sent", "Paid"]}], "unit": "currency",
}
JOIN = {"kind": "join", "module": "Invoices", "field": "Buyer", "target": "Buyer_Clients"}
QUERY = {
    "kind": "query", "key": "billed_for_customer",
    "question": "Billed amount for {customer} in {month}",
    "params": [{"name": "customer", "kind": "record"}, {"name": "month", "kind": "month"}],
    "query": {"module": "Invoices", "measure": "billed",
              "where": [{"field": "Buyer", "op": "eq", "param": "customer"},
                        {"field": "Invoice_Date", "op": "on", "param": "month"}]},
    "model_version": 3,
}
ROLE = {"kind": "role", "slot": "deal", "module": "Commercials"}


def _sources() -> dict[str, cat.Source]:
    invoices = cat.Source(
        module="Invoices", entity="module:Invoices", kind="module", label="Invoices",
        fields=(
            cat.Field("Net_Value", "Net Value", "currency"),
            cat.Field("Status", "Status", "select", options=("Draft", "Sent", "Paid")),
            cat.Field("Buyer", "Buyer", "lookup", target="Buyer_Clients"),
            cat.Field("Invoice_Date", "Invoice Date", "date"),
            cat.Field("Name", "Name", "text"),
        ),
    )
    buyers = cat.Source(module="Buyer_Clients", entity="module:Buyer_Clients",
                        kind="module", label="Buyer Clients",
                        fields=(cat.Field("Name", "Name", "text"),))
    return {"Invoices": invoices, "Buyer_Clients": buyers}


def _refused(items: list[dict], code: str, *, semantic: bool = True) -> dict:
    with pytest.raises(HTTPException) as exc:
        mpl.check_kinds(items, semantic=semantic)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == code, exc.value.detail
    return exc.value.detail


# ── 1. The flag ──────────────────────────────────────────────────────────────


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "semantic_model_orgs", value, raising=False)


def test_the_settings_default_is_empty() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["semantic_model_orgs"].default == ""


def test_the_flag_follows_the_list_and_the_crm_switch(monkeypatch) -> None:
    crm_switch_on(monkeypatch, "*")
    _flag(monkeypatch, "")
    assert flags.semantic_model_allowed(_ORG) is False
    _flag(monkeypatch, f" {_ORG.upper()} ")
    assert flags.semantic_model_allowed(_ORG) is True
    assert flags.semantic_model_allowed(_OTHER) is False
    assert flags.semantic_model_allowed(None) is False
    crm_switch_on(monkeypatch, _OTHER)
    assert flags.semantic_model_allowed(_ORG) is False


def test_the_flag_has_one_reader() -> None:
    """Only ``flags.py`` names the setting."""
    root = Path(__file__).resolve().parents[2]
    readers = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for base in ("apps", "packages")
        for p in (root / base).rglob("*.py")
        if "semantic_model_orgs" in p.read_text(encoding="utf-8", errors="ignore")
    )
    assert readers == [
        "apps/services/gateway/gateway/routes/crm/flags.py",
        "packages/acb_common/acb_common/settings.py",
    ]


# ── 2. Each kind, legal and not ──────────────────────────────────────────────


def test_each_kind_is_legal_in_its_shape() -> None:
    mpl.check_kinds([MEASURE, JOIN, QUERY], semantic=True)
    count = {"kind": "measure", "key": "deals", "label": "Deals", "module": "Invoices",
             "aggregate": "count", "unit": "count"}
    labelled = {**JOIN, "label": "The buyer of an invoice"}
    plain = {**QUERY, "key": "plain", "question": "Billed amount this year", "params": [],
             "query": {"module": "Invoices", "aggregate": "sum", "fields": ["Net_Value"],
                       "group_by": ["Status"]}}
    mpl.check_kinds([count, {**labelled, "field": "Other"}, plain], semantic=True)
    assert mpl.kind_key(MEASURE, semantic=True) == ("measure", "billed")
    assert mpl.kind_key(JOIN, semantic=True) == ("join", "Invoices", "Buyer")
    assert mpl.kind_key(QUERY, semantic=True) == ("query", "billed_for_customer")


def _with(base: dict, **changes: Any) -> dict:
    out = copy.deepcopy(base)
    for key, value in changes.items():
        if value is ...:
            out.pop(key, None)
        else:
            out[key] = value
    return out


@pytest.mark.parametrize(("item", "code", "part"), [
    (_with(MEASURE, key="Billed Amount"), "bad_measure", "key"),
    (_with(MEASURE, label=""), "bad_measure", "label"),
    (_with(MEASURE, label="x" * 81), "bad_measure", "label"),
    (_with(MEASURE, label="Billed\x07amount"), "bad_measure", "label"),
    (_with(MEASURE, module="@org"), "bad_measure", "module"),
    (_with(MEASURE, aggregate="median"), "bad_measure", "aggregate"),
    (_with(MEASURE, fields=[]), "bad_measure", "fields"),
    (_with(MEASURE, aggregate="avg", fields=["A", "B"]), "bad_measure", "fields"),
    (_with(MEASURE, aggregate="count"), "bad_measure", "fields"),
    (_with(MEASURE, fields=["A+B"]), "bad_measure", "fields"),
    (_with(MEASURE, filters=[{"field": "Status", "value": "Paid"}]), "bad_measure", "filters"),
    (_with(MEASURE, filters=[{"field": "Status", "options": []}]), "bad_measure", "filters"),
    (_with(MEASURE, filters=[{"field": "Status", "options": ["x" * 81]}]),
     "bad_measure", "filters"),
    (_with(MEASURE, unit="INR"), "bad_measure", "unit"),
    (_with(MEASURE, unit=...), "bad_measure", "unit"),
    (_with(JOIN, module=""), "bad_join", "module"),
    (_with(JOIN, field=7), "bad_join", "field"),
    (_with(JOIN, target=...), "bad_join", "target"),
    (_with(JOIN, label="y" * 81), "bad_join", "label"),
    (_with(QUERY, key=...), "bad_query", "key"),
    (_with(QUERY, question=""), "bad_query", "question"),
    (_with(QUERY, question="x" * 301), "bad_query", "question"),
    (_with(QUERY, question="Billed for {customer} in {month} {"), "bad_query", "question"),
    (_with(QUERY, question="Billed for {customer}"), "bad_query", "params"),
    (_with(QUERY, params=[{"name": "customer", "kind": "person"},
                          {"name": "month", "kind": "month"}]), "bad_query", "params"),
    (_with(QUERY, params=[{"name": "customer", "kind": "record"},
                          {"name": "customer", "kind": "record"}]), "bad_query", "params"),
    (_with(QUERY, query={"module": "Invoices", "measure": "billed",
                         "where": [{"field": "Buyer", "op": "eq", "param": "customer"}]}),
     "bad_query", "query"),
    (_with(QUERY, query={**QUERY["query"], "aggregate": "sum"}), "bad_query", "query"),
    (_with(QUERY, query={**QUERY["query"], "sql": "SELECT 1"}), "bad_query", "query"),
    (_with(QUERY, query={**QUERY["query"], "where": [
        {"field": "Buyer", "op": "like", "param": "customer"},
        {"field": "Invoice_Date", "op": "on", "param": "month"}]}), "bad_query", "query"),
    (_with(QUERY, model_version=0), "bad_query", "model_version"),
    (_with(QUERY, model_version=True), "bad_query", "model_version"),
])
def test_each_illegal_shape_is_refused_by_name(item: dict, code: str, part: str) -> None:
    items = [MEASURE, item] if item["kind"] == "query" else [item]
    detail = _refused(items, code)
    assert f"({part})" in detail["message"], detail


def test_a_query_that_names_a_measure_needs_it_in_the_plan() -> None:
    _refused([QUERY], "measure_missing")


def test_two_items_with_one_identity_are_a_duplicate() -> None:
    _refused([MEASURE, {**MEASURE, "label": "Another"}], "duplicate")
    _refused([JOIN, {**JOIN, "label": "Again"}], "duplicate")


# ── 3. No value: no total, no parameter value, no literal ────────────────────


@pytest.mark.parametrize(("items", "code", "part"), [
    ([_with(MEASURE, total=125000)], "bad_measure", "keys"),
    ([_with(JOIN, value="Acme Ltd")], "bad_join", "keys"),
    ([MEASURE, _with(QUERY, total=125000)], "bad_query", "keys"),
    ([MEASURE, _with(QUERY, values={"customer": "Acme Ltd"})], "bad_query", "keys"),
    ([MEASURE, _with(QUERY, question="Billed amount for {customer} in 2024")],
     "bad_query", "question_value"),
    ([MEASURE, _with(QUERY, question="Top 5 buyers for {customer} in {month}")],
     "bad_query", "question_value"),
    ([MEASURE, _with(QUERY, params=[{"name": "4242", "kind": "number"},
                                    {"name": "month", "kind": "month"}])],
     "bad_query", "param_value"),
    ([MEASURE, _with(QUERY, params=[{"name": 2024, "kind": "year"},
                                    {"name": "month", "kind": "month"}])],
     "bad_query", "param_value"),
    ([MEASURE, _with(QUERY, query={**QUERY["query"], "where": [
        {"field": "Buyer", "op": "eq", "param": "customer", "value": "Acme Ltd"},
        {"field": "Invoice_Date", "op": "on", "param": "month"}]})],
     "bad_query", "query_value"),
    ([MEASURE, _with(QUERY, query={**QUERY["query"], "where": [
        {"field": "Buyer", "op": "eq", "param": "customer"},
        {"field": "Invoice_Date", "op": "on", "param": "2024-04"}]})],
     "bad_query", "query_value"),
])
def test_a_value_where_a_name_must_be_is_refused(items: list, code: str, part: str) -> None:
    detail = _refused(items, code)
    assert f"({part})" in detail["message"], detail


def test_number_like_reads_numbers_dates_and_amounts() -> None:
    for value in (4242, 1.5, "4242", " 1,25,000.50 ", "2024-04-01", "12/04", "-3"):
        assert cat.number_like(value), value
    for value in ("customer", "Q1", "month_2", True, None, ""):
        assert not cat.number_like(value), value


def test_the_label_cap_is_80_and_the_question_cap_is_the_rule_cap() -> None:
    from gateway.routes.crm.rules import BODY_MAX

    assert cat.LABEL_MAX == 80
    mpl.check_kinds([_with(MEASURE, label="x" * 80)], semantic=True)
    words = "Billed amount for {customer} in {month} "
    long = (words + "y" * BODY_MAX)[:BODY_MAX]
    mpl.check_kinds([MEASURE, _with(QUERY, question=long)], semantic=True)


# ── 4. Flag off: the items stay as they are ──────────────────────────────────


def test_flag_off_runs_no_check_on_these_kinds() -> None:
    bad = [_with(MEASURE, total=125000), _with(JOIN, field=7), _with(QUERY, model_version=0),
           _with(QUERY, model_version=0)]
    mpl.check_kinds([ROLE, *bad])
    mpl.check_kinds([ROLE, *bad], semantic=False)
    for item in bad:
        assert mpl.kind_key(item) is None


def test_flag_off_merge_leaves_these_kinds_alone() -> None:
    others = [ROLE, MEASURE]
    merged = mpl.merge_kinds(others, [{**MEASURE, "label": "New"}])
    assert merged == others
    merged = mpl.merge_kinds(others, [{**MEASURE, "label": "New"}], semantic=True)
    assert merged == [ROLE, {**MEASURE, "label": "New"}]


def test_a_snapshot_keeps_an_item_of_these_kinds_byte_for_byte() -> None:
    rules = [
        {"kind": "field", "entity": "deal", "field_api_name": "Amount",
         "field_type": "currency", "target_column": "amount", "profile_version": 2,
         "opened_empty": False},
        ROLE, MEASURE, JOIN, QUERY, {"kind": "future_kind", "x": [1, 2]},
    ]
    fields, others = mpl.split_rules(json.loads(json.dumps(rules)))
    assert others == [ROLE, MEASURE, JOIN, QUERY, {"kind": "future_kind", "x": [1, 2]}]
    assert json.loads(mpl._dump(fields, others)) == rules


# ── 5. The catalogue check and the facts ─────────────────────────────────────


@pytest.mark.parametrize(("item", "part"), [
    (MEASURE, None), (JOIN, None), (QUERY, None),
    (_with(MEASURE, module="Gone"), "module"),
    (_with(MEASURE, fields=["Gone"]), "field"),
    (_with(MEASURE, fields=["Name"]), "type"),
    (_with(MEASURE, filters=[{"field": "Status", "options": ["Lost"]}]), "options"),
    (_with(MEASURE, filters=[{"field": "Net_Value", "options": ["Paid"]}]), "options"),
    (_with(JOIN, field="Gone"), "field"),
    (_with(JOIN, target="Towers"), "target"),
    (_with(JOIN, field="Name"), "target"),
    (_with(QUERY, query={**QUERY["query"], "where": [
        {"field": "Gone", "op": "eq", "param": "customer"},
        {"field": "Invoice_Date", "op": "on", "param": "month"}]}), "field"),
])
def test_the_catalogue_check_names_what_left(item: dict, part: str | None) -> None:
    assert cat.semantic_ref_problem(item, _sources()) == part


def test_the_facts_serve_each_kind_and_leave_a_stale_item_out() -> None:
    stale = _with(JOIN, field="Status", target="Towers")
    rows = facts.semantic_facts(
        [ROLE, MEASURE, JOIN, QUERY, stale, _with(MEASURE, key="x", total=5)],
        list(_sources().values()), "2026-11-18")
    by = {r.subject: r for r in rows}
    assert sorted(by) == ["join:Invoices.Buyer", "measure:billed", "query:billed_for_customer"]
    assert all(r.source == "admin" and r.set_by == "admin" for r in rows)
    assert by["measure:billed"].meaning == (
        'The measure "Billed amount" is the sum of Net Value of Invoices, '
        "where Status is Sent or Paid, in currency.")
    assert by["join:Invoices.Buyer"].meaning == (
        "Buyer of Invoices links each record to a Buyer Clients record.")


def test_a_query_fact_shows_the_question_and_the_parameter_names_only() -> None:
    (row,) = facts.semantic_facts([MEASURE, QUERY], list(_sources().values()), None)[1:]
    assert row.subject == "query:billed_for_customer"
    assert row.meaning == ('A verified question: "Billed amount for {customer} in {month}". '
                           "Its parameters: customer, month.")
    text = json.dumps(row.as_json())
    for word in ("Buyer", "Invoice_Date", '"eq"', "record", "model_version", "where"):
        assert word not in text, word


def _approved_db(approved_at: Any = None) -> Any:
    class _Db:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(first=lambda: SimpleNamespace(approved_at=approved_at))

    return _Db()


def test_the_provider_serves_them_only_with_the_flag(monkeypatch) -> None:
    """``crm_facts`` reads the approved items through ``catalogue._load``
    (``approved_others``), and serves the semantic kinds only for an org on
    the list."""
    from gateway.crm_sync import runs
    from gateway.routes.crm import catalogue as route

    crm_switch_on(monkeypatch, "*")
    from acb_common import get_settings

    for name in ("crm_catalogue_orgs", "crm_questions_orgs"):
        monkeypatch.setattr(get_settings(), name, "*", raising=False)

    async def _load(_db: Any, _org: str, **_k: Any) -> Any:
        return "mirror", list(_sources().values()), {}, [MEASURE, JOIN, QUERY]

    async def _access(_user: Any) -> None:
        return None

    async def _cursors(*_a: Any, **_k: Any) -> dict:
        return {}

    monkeypatch.setattr(route, "_load", _load)
    monkeypatch.setattr(facts, "require_crm_access", _access)
    monkeypatch.setattr(runs, "read_cursors", _cursors)
    user = SimpleNamespace(organization_id=_ORG, email="admin@example.test")

    def subjects() -> set[str]:
        rows = asyncio.run(facts.crm_facts(_approved_db(), user))
        return {r.subject.split(":")[0] for r in rows}

    _flag(monkeypatch, "")
    assert not subjects() & {"measure", "join", "query"}
    _flag(monkeypatch, _OTHER)
    assert not subjects() & {"measure", "join", "query"}
    _flag(monkeypatch, _ORG)
    assert {"measure", "join", "query"} <= subjects()
