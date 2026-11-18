"""Two group fields and pages of groups, the hermetic rules (CRM-Q34-10).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, CRM-Q34-10. The real
SQL is proved in ``test_crm_two_group_r8.py``. These rules need no database,
so they never skip.

1. Off ``CRM_MODULE_FIND_ORGS``: ``group_field2`` and a page past 1 are a
   422 that names the way without them. Every other call is the call of
   today, and the SQL of one group field does not change.
2. A core entity refuses ``group_field2``. Two dates, a bucket on a value
   field, one field twice and a page size past 200 are each a 422.
3. The order of a page is a total order: a key ends it.
4. The chat tool names the page and the count of groups, and each group as
   ``<label> · <label2>: value``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException


def _scope() -> Any:
    from gateway.routes.crm import field_sql as fs

    fields = {
        "Site": fs.FieldRef(key="Site", type="lookup", label="Site"),
        "Invoice_Date": fs.FieldRef(key="Invoice_Date", type="date", label="Invoice Date"),
        "Paid_On": fs.FieldRef(key="Paid_On", type="datetime", label="Paid On"),
        "Status": fs.FieldRef(key="Status", type="select", label="Status"),
        "Amount": fs.FieldRef(key="Amount", type="currency", label="Amount"),
    }
    return fs.FieldScope(column="fields", owner="module:Invoices", fields=fields)


@pytest.fixture
def agg(monkeypatch):
    from gateway.routes.crm import aggregate

    monkeypatch.setattr(aggregate, "current_tenant", lambda: "org-a")
    return aggregate


def _flag(agg: Any, monkeypatch: Any, on: bool) -> None:
    monkeypatch.setattr(agg, "module_find_allowed", lambda org: on)


def _detail(call: Any) -> str:
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 422
    return str(caught.value.detail)


# ── 1. Off the list ─────────────────────────────────────────────────────────


def test_off_the_list_a_second_field_or_a_later_page_is_a_422(agg, monkeypatch) -> None:
    _flag(agg, monkeypatch, False)
    detail = _detail(lambda: agg._paging("Invoices", "Site", None, "Invoice_Date", None, None))
    assert detail == agg.TWO_GROUP_OFF
    assert "filter by the other" in detail and "field.<b>=<value>" in detail
    assert _detail(lambda: agg._paging("Invoices", "Site", None, None, 2, None)) == (
        agg.TWO_GROUP_OFF)


@pytest.mark.parametrize("on", [False, True])
def test_a_call_with_no_new_parameter_is_the_call_of_today(agg, monkeypatch, on) -> None:
    _flag(agg, monkeypatch, on)
    assert agg._paging("Invoices", "Site", None, None, None, None) is None
    assert agg._paging(None, None, None, None, None, None) is None


def test_off_the_list_page_one_and_a_size_change_nothing(agg, monkeypatch) -> None:
    _flag(agg, monkeypatch, False)
    assert agg._paging("Invoices", "Site", None, None, 1, None) is None
    assert agg._paging("Invoices", "Site", None, None, None, 500) is None


def test_the_sql_of_one_group_field_does_not_change() -> None:
    """The key and the label of each kind of group, as the base code built
    them (origin/main cc9c0a584). The second field binds its own key."""
    from gateway.routes.crm import field_sql as fs

    scope = _scope()
    site = fs.group_sql(scope, scope.ref("Site"), None, zone="UTC")
    d = "agf_d.agf_fields"
    assert site.key == (
        f"(CASE WHEN jsonb_typeof({d} -> :agf_g) = 'object' "
        f"THEN {d} -> :agf_g ->> 'id' ELSE {d} ->> :agf_g END)")
    assert site.label == (
        f"max(CASE WHEN jsonb_typeof({d} -> :agf_g) = 'object' "
        f"THEN {d} -> :agf_g ->> 'name' END)")
    assert site.params == {"agf_g": "Site"} and site.source == fs.DETOAST_JOIN
    status = fs.group_sql(scope, scope.ref("Status"), None, zone="UTC")
    assert (status.key, status.label, status.params) == (
        f"{d} ->> :agf_g", "NULL", {"agf_g": "Status"})
    month = fs.group_sql(scope, scope.ref("Invoice_Date"), "month", zone="Asia/Kolkata")
    assert month.key.startswith("to_char(date_trunc('month', ")
    assert month.params == {"agf_g": "Invoice_Date", "agf_zone": "Asia/Kolkata"}
    second = fs.group_sql(scope, scope.ref("Site"), None, zone="UTC", prefix=fs.GROUP2_KEY)
    assert second.key == site.key.replace(":agf_g", ":agf_g2")
    assert second.params == {"agf_g2": "Site"}


def test_a_group_of_one_field_dumps_the_keys_of_today() -> None:
    from gateway.routes.crm.aggregate import AggregateGroup, AggregateResponse

    group = AggregateGroup(key=None, label="(none)", value=1.0)
    assert group.model_dump() == {"key": None, "label": "(none)", "value": 1.0}
    answer = AggregateResponse(entity="module:Invoices", measure="count", groups=[group],
                               total=1.0)
    assert not {"group_field2", "group_field2_label", "group_field2_type", "group_count",
                "group_page", "group_pages"} & set(answer.model_dump())


# ── 2. The refusals ─────────────────────────────────────────────────────────


def test_a_core_entity_refuses_a_second_field(agg, monkeypatch) -> None:
    _flag(agg, monkeypatch, True)
    detail = _detail(lambda: agg._paging(None, "Site", None, "Status", None, None))
    assert "module entity" in detail


def test_on_the_list_the_page_and_its_size(agg, monkeypatch) -> None:
    _flag(agg, monkeypatch, True)
    assert agg._paging("Invoices", "Site", None, "Status", None, None) == (1, agg.MAX_GROUPS)
    assert agg._paging("Invoices", "Site", None, None, 3, 200) == (3, 200)
    assert "200" in _detail(lambda: agg._paging("Invoices", "Site", None, None, 1, 201))
    assert _detail(lambda: agg._paging("Invoices", "Site", None, None, 1, 0))
    assert "from 1" in _detail(lambda: agg._paging("Invoices", "Site", None, None, 0, None))
    assert "group_field" in _detail(lambda: agg._paging("Invoices", None, None, "Site", 1, None))
    assert "split" in _detail(lambda: agg._paging("Invoices", "Site", "a,b", None, 2, None))


def test_two_dates_are_a_422() -> None:
    from gateway.routes.crm import field_sql as fs

    detail = _detail(lambda: fs.check_groups(_scope(), "Invoice_Date", "Paid_On", "month"))
    assert "both dates" in detail and "one date field" in detail


def test_a_value_field_takes_no_bucket() -> None:
    from gateway.routes.crm import field_sql as fs

    detail = _detail(lambda: fs.check_groups(_scope(), "Site", "Status", "month"))
    assert "bucket goes with a date field" in detail
    assert "Give bucket" in _detail(lambda: fs.check_groups(_scope(), "Site", "Invoice_Date",
                                                             None))
    assert "two different" in _detail(lambda: fs.check_groups(_scope(), "Site", "Site", None))


@pytest.mark.parametrize(("first", "second"), [("Site", "Invoice_Date"),
                                               ("Invoice_Date", "Site")])
def test_the_bucket_goes_to_the_date_field(first: str, second: str) -> None:
    from gateway.routes.crm import field_sql as fs

    a, b = fs.check_groups(_scope(), first, second, "month")
    assert (a.key, b.key) == (first, second)


# ── 3. A total order ────────────────────────────────────────────────────────


def test_the_order_of_a_page_ends_with_each_key(agg) -> None:
    assert agg.GROUP_ORDER == (
        "value DESC NULLS LAST, key ASC NULLS LAST, key2 ASC NULLS LAST")
    sql = agg.paged_sql("SELECT 1", "sum(value)")
    assert f"ORDER BY {agg.GROUP_ORDER}" in sql
    assert ":agf_lo" in sql and ":agf_hi" in sql and "count(*) AS agf_count" in sql
    # The page is cut in SQL, never in Python.
    assert "agf_pos > :agf_lo AND agf_pos <= :agf_hi" in sql


# ── 4. The answer text ──────────────────────────────────────────────────────


def _paged_body(page: int) -> dict:
    return {
        "entity": "module:Invoices", "measure": "sum", "field": "Amount",
        "field_type": "currency", "group_field": "Site", "group_field_label": "Site",
        "group_field2": "Invoice_Date", "group_field2_label": "Invoice Date",
        "bucket": "month", "zone": "UTC", "values": 3, "records": 3,
        "groups": [
            {"key": "s1", "label": "Site 1", "key2": "2026-07", "label2": "2026-07",
             "value": 900.0},
            {"key": None, "label": "(none)", "label2": "(none)", "value": 5.0},
        ],
        "total": 1000.0, "other": None, "omitted": 310,
        "group_count": 312, "group_page": page, "group_pages": 7,
    }


async def test_the_tool_names_the_page_and_each_pair(monkeypatch) -> None:
    import acb_skills.crm_tools as tools

    sent: list[dict] = []

    async def _get(path: str, params: dict | None = None) -> dict:
        if path == "/crm/aggregate":
            sent.append(dict(params or {}))
        return _paged_body(int((params or {}).get("group_page") or 1))

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", measure="sum", field="Amount",
                                    group_field="Site", group_field2="Invoice_Date",
                                    bucket="month", page=2)
    assert sent[-1]["group_field2"] == "Invoice_Date"
    assert (sent[-1]["group_page"], sent[-1]["group_page_size"]) == (2, tools.MAX_ROWS)
    lines = out.splitlines()
    assert "- Site 1 · 2026-07: INR 900" in lines
    assert "- (none) · (none): INR 5" in lines
    assert "Groups 51-52 of 312, page 2 of 7. Ask for page 3 for more." in lines
    assert "Other (" not in out


async def test_the_tool_sends_no_page_for_page_one_of_one_field(monkeypatch) -> None:
    """With no second field, page 1 sends what it sends today. Page 2 was
    always empty (the route sent 50 groups, the tool shows 50), so it now
    asks the route for its page 2."""
    import acb_skills.crm_tools as tools

    sent: list[dict] = []

    async def _get(path: str, params: dict | None = None) -> dict:
        if path == "/crm/aggregate":
            sent.append(dict(params or {}))
        return {"entity": "module:Invoices", "measure": "count", "zone": "UTC",
                "groups": [{"key": "s1", "label": "Site 1", "value": 1}],
                "total": 1, "other": 0, "omitted": 0}

    monkeypatch.setattr(tools, "_get", _get)
    await tools.crm_aggregate("module:Invoices", group_field="Site")
    assert not {"group_field2", "group_page", "group_page_size"} & {
        k for k, v in sent[-1].items() if v is not None}
    await tools.crm_aggregate("module:Invoices", group_field="Site", page=2)
    assert (sent[-1]["group_page"], sent[-1]["group_field2"]) == (2, None)
    await tools.crm_aggregate("deals", group_field="Region", page=2)
    assert "group_page" not in sent[-1]
