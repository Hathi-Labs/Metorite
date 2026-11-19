"""Two group fields and pages of groups, the hermetic rules (CRM-Q34-10).

Spec: ``project-docs/specs/crm_platform.md`` §18.17, CRM-Q34-10. The real
SQL is proved in ``test_crm_two_group_r8.py``. These rules need no database,
so they never skip.

1. Off ``CRM_MODULE_FIND_ORGS``: ``group_field2`` is a 422 that names the
   way without it. The page parameters are ignored, so every other call is
   the call of today, and the SQL of one group field does not change.
2. A core entity refuses ``group_field2``. Two dates, a bucket on a value
   field, one field twice, a page size past 200 and a page past 1,000,000
   are each a 422.
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


def test_off_the_list_a_second_field_is_a_422(agg, monkeypatch) -> None:
    _flag(agg, monkeypatch, False)
    detail = _detail(lambda: agg._paging("Invoices", "Site", None, "Invoice_Date", None, None))
    assert detail == agg.TWO_GROUP_OFF
    assert "filter by the other" in detail and "field.<b>=<value>" in detail


@pytest.mark.parametrize("on", [False, True])
def test_a_call_with_no_new_parameter_is_the_call_of_today(agg, monkeypatch, on) -> None:
    _flag(agg, monkeypatch, on)
    assert agg._paging("Invoices", "Site", None, None, None, None) is None
    assert agg._paging(None, None, None, None, None, None) is None


def test_off_the_list_the_page_parameters_change_nothing(agg, monkeypatch) -> None:
    """Fix round item 1: off the list the route ignores every page
    parameter, page 2 and a huge page too, so the tool may always send them."""
    _flag(agg, monkeypatch, False)
    for page, size in ((1, None), (None, 500), (2, 50), (10**20, 0), (0, None)):
        assert agg._paging("Invoices", "Site", None, None, page, size) is None
        assert agg._paging(None, "Site", None, None, page, size) is None


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


@pytest.mark.parametrize("on", [False, True])
def test_a_core_entity_refuses_a_second_field(agg, monkeypatch, on) -> None:
    _flag(agg, monkeypatch, on)
    detail = _detail(lambda: agg._paging(None, "Site", None, "Status", None, None))
    assert "module entity" in detail
    # A page alone on a core entity is the call of today.
    assert agg._paging(None, "Site", None, None, 3, 50) is None


def test_on_the_list_the_page_and_its_size(agg, monkeypatch) -> None:
    _flag(agg, monkeypatch, True)
    assert agg._paging("Invoices", "Site", None, "Status", None, None) == (1, agg.MAX_GROUPS)
    assert agg._paging("Invoices", "Site", None, "Day", 3, 200) == (3, 200)
    assert "200" in _detail(lambda: agg._paging("Invoices", "Site", None, "Day", 2, 201))
    assert _detail(lambda: agg._paging("Invoices", "Site", None, "Day", 2, 0))
    assert "1 to 1,000,000" in _detail(lambda: agg._paging("Invoices", "Site", None, "Day", 0,
                                                            None))
    # Fix round item 2: a page past the bound is a 422, never an int8 overflow.
    assert agg.GROUP_PAGES_MAX == 1_000_000
    assert agg._paging("Invoices", "Site", None, None, 1_000_000, None) == (1_000_000, 50)
    for page in (1_000_001, 10**20):
        assert "1 to 1,000,000" in _detail(
            lambda page=page: agg._paging("Invoices", "Site", None, None, page, None))
    assert "group_field" in _detail(lambda: agg._paging("Invoices", None, None, "Site", 1, None))
    assert "split" in _detail(lambda: agg._paging("Invoices", "Site", "a,b", "Day", 1, None))


def test_one_field_pages_only_what_the_old_answer_cannot(agg, monkeypatch) -> None:
    """Second fix round, B: with one field, only a page past 1 of a value
    group is paged. Page 1, a split, a group_by key word (status), ``q`` and
    no group field keep the answer of before."""
    _flag(agg, monkeypatch, True)
    for page, size in ((None, 50), (1, None), (1, 200), (0, None)):
        assert agg._paging("Invoices", "Site", None, None, page, size) is None
    assert agg._paging("Invoices", "Site", "a,b", None, 2, None) is None
    assert agg._paging("Invoices", "@status", None, None, 2, None, status_group=True) is None
    assert agg._paging("Invoices", "Site", None, None, 2, None, q="acme") is None
    assert agg._paging("Invoices", None, None, None, 2, None) is None
    assert agg._paging("Invoices", "Site", None, None, 2, None) == (2, agg.MAX_GROUPS)


def test_the_order_of_one_field_is_the_cut_of_the_old_answer() -> None:
    """Second fix round, B: a later page of one field orders as the answer
    of one page cut its 50 groups: the rounded value down, then the label of
    Python (lookup name, option label, key, "(none)") by code point, then
    the key."""
    import json

    from gateway.routes.crm import field_sql as fs

    scope = _scope()
    order, params = fs.one_group_order(scope.ref("Site"))
    assert order.startswith("round(CAST(value AS numeric), 2) DESC NULLS LAST, COALESCE(")
    assert 'NULLIF(label, \'\')' in order and "'(none)') COLLATE \"C\" ASC" in order
    # Round 3: the key ends the order by code point, a NULL key last.
    assert order.endswith('key COLLATE "C" ASC NULLS LAST')
    assert order.index("NULLIF(label") < order.index("->> key") < order.index("NULLIF(key")
    status = fs.FieldRef(key="Status", type="select", label="Status", options={"P": "Paid"})
    assert json.loads(fs.one_group_order(status)[1]["agf_opts"]) == {"P": "Paid"}
    assert params == {"agf_opts": "{}"}


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


async def test_the_tool_sends_a_page_only_where_the_old_answer_stops(monkeypatch) -> None:
    """Second fix round, A: page 1 of one group sends no page, so it is the
    answer of before (a split, the near pass of q, time order, findings).
    Page 2 on of a module group asks the route for its page. A second group
    always goes to the route, which refuses what it cannot take."""
    import acb_skills.crm_tools as tools

    sent: list[dict] = []

    async def _get(path: str, params: dict | None = None) -> dict:
        if path == "/crm/aggregate":
            sent.append({k: v for k, v in (params or {}).items() if v is not None})
        return {"entity": "module:Invoices", "measure": "count", "zone": "UTC",
                "groups": [{"key": "s1", "label": "Site 1", "value": 1}],
                "total": 1, "other": 0, "omitted": 0}

    monkeypatch.setattr(tools, "_get", _get)
    await tools.crm_aggregate("module:Invoices", group_field="Site", page=1)
    assert not {"group_field2", "group_page", "group_page_size"} & set(sent[-1])
    await tools.crm_aggregate("module:Invoices", group_field="Site", fields=["Qty", "Tax"])
    assert not {"group_page", "group_page_size"} & set(sent[-1])
    await tools.crm_aggregate("module:Invoices", group_field="Site", page=2)
    assert (sent[-1]["group_page"], sent[-1]["group_page_size"]) == (2, tools.MAX_ROWS)
    assert "group_field2" not in sent[-1]
    await tools.crm_aggregate("module:Invoices", group_by="status", group_field2="Site")
    assert sent[-1]["group_field2"] == "Site" and sent[-1]["group_page"] == 1
    await tools.crm_aggregate("deals", group_field="Region", group_field2="Stage", page=2)
    assert sent[-1]["group_field2"] == "Stage"
    await tools.crm_aggregate("deals", group_field="Region", page=2)
    assert "group_page" not in sent[-1]
    await tools.crm_aggregate("module:Invoices")
    assert not {"group_field2", "group_page", "group_page_size"} & set(sent[-1])


@pytest.mark.parametrize(("data", "words"), [
    ({"group_field_label": "Site", "group_field2_label": "Invoice Date",
      "group_field_type": "lookup", "group_field2_type": "date"},
     "Site and Invoice Date (month)"),
    ({"group_field_label": "Invoice Date", "group_field2_label": "Site",
      "group_field_type": "date", "group_field2_type": "lookup"},
     "Invoice Date (month) and Site"),
    ({"group_field_label": "Invoice Date", "group_field_type": "date"}, "Invoice Date (month)"),
])
def test_the_heading_puts_the_bucket_on_the_date_field(data: dict, words: str) -> None:
    """Fix round item 5."""
    import acb_skills.crm_tools as tools

    assert tools._group_words(data, "x", "month") == words


async def test_a_datetime_second_group_names_the_zone(monkeypatch) -> None:
    """Fix round item 6."""
    import acb_skills.crm_tools as tools

    async def _get(path: str, params: dict | None = None) -> dict:
        body = _paged_body(1)
        body.update(zone="Asia/Kolkata", group_field_type="lookup",
                    group_field2_type="datetime")
        return body

    monkeypatch.setattr(tools, "_get", _get)
    out = await tools.crm_aggregate("module:Invoices", group_field="Site",
                                    group_field2="Paid_On", bucket="month")
    assert "Months count in the CRM time zone, Asia/Kolkata." in out.splitlines()


def test_a_page_of_an_empty_module_is_no_filter_miss() -> None:
    """Fix round item 4: the page and the second group filter nothing, so a
    paged total of an empty module never says the filters matched nothing."""
    import acb_skills.crm_tools as tools

    params = {"entity": "module:Invoices", "group_field": "Site", "group_field2": "Day",
              "group_page": 1, "group_page_size": 50}
    assert tools._no_match(params, {"records": 0}, "Invoices") is None


async def test_page_1_says_that_page_2_continues_it(monkeypatch) -> None:
    """Second fix round, D: one short line, only when the route says that
    page 2 continues the answer."""
    import acb_skills.crm_tools as tools

    body = {"entity": "module:Invoices", "measure": "count", "zone": "UTC",
            "groups": [{"key": "s1", "label": "Site 1", "value": 3}],
            "total": 5, "other": 2, "omitted": 2, "records": 5}

    async def _get(path: str, params: dict | None = None) -> dict:
        return dict(body)

    monkeypatch.setattr(tools, "_get", _get)
    plain = await tools.crm_aggregate("module:Invoices", group_field="Site")
    assert "Ask for page 2" not in plain and "Other (2 smaller groups)" in plain
    assert "Narrow the filter to see them." in plain
    body["next_group_page"] = 2
    more = await tools.crm_aggregate("module:Invoices", group_field="Site")
    # Round 3: ONE step in the Other line, page 2 in place of the narrowing.
    other = next(line for line in more.splitlines() if line.startswith("Other ("))
    assert other.endswith("The total counts them. Ask for page 2 for the next groups.")
    assert "Narrow the filter" not in more


def test_one_field_pages_by_the_cut_of_page_1(agg, monkeypatch) -> None:
    """Round 3: page 1 of one field always cuts MAX_GROUPS, so each later
    page holds that many, or page 2 would skip or repeat a group. Two
    fields take any size from 1 to 200. The tool shows MAX_ROWS groups on
    a page, so it must equal MAX_GROUPS."""
    import acb_skills.crm_tools as tools

    assert tools.MAX_ROWS == agg.MAX_GROUPS
    _flag(agg, monkeypatch, True)
    assert agg._paging("Invoices", "Site", None, None, 2, agg.MAX_GROUPS) == (2, 50)
    for size in (1, 49, 51, 200):
        detail = _detail(lambda size=size: agg._paging("Invoices", "Site", None, None, 2, size))
        assert "One group field pages by 50 groups" in detail
    assert agg._paging("Invoices", "Site", None, "Day", 2, 200) == (2, 200)


def test_a_tie_of_value_and_label_breaks_by_the_key(agg) -> None:
    """Round 3: two groups equal on the value and the label sort by the key,
    by code point, None last, whatever their order in. The SQL of a later
    page breaks the tie the same way (``one_group_order``)."""
    from gateway.routes.crm.aggregate import AggregateGroup

    def group(key: str | None, label: str, value: float = 2.0) -> AggregateGroup:
        return AggregateGroup(key=key, label=label, value=value)

    rows = [group(None, "(none)"), group("ka", "Acme Ltd"), group("", "(none)"),
            group("k-b", "Acme Ltd"), group("z", "Big", 3.0)]
    ordered = [(g.key, g.label) for g in sorted(rows, key=agg.cut_key)]
    assert ordered == [("z", "Big"), ("", "(none)"), (None, "(none)"),
                       ("k-b", "Acme Ltd"), ("ka", "Acme Ltd")]
    assert [(g.key, g.label) for g in sorted(reversed(rows), key=agg.cut_key)] == ordered


@pytest.mark.parametrize(("raw", "cents"), [
    (0.125, "0.13"), (2.675, "2.68"), (-0.125, "-0.13"), (0.12, "0.12"), (5, "5.00"),
    (1e300, "1" + "0" * 300 + ".00"), ("0.125", "0.13"),
])
def test_the_cut_rounds_a_half_cent_as_sql_does(agg, raw, cents) -> None:
    """Round 4: page 1 orders by the value rounded as Postgres rounds a
    numeric (half away from zero), never by ``round(float, 2)``, which gives
    0.12 for 0.125. So page 1 and page 2 agree on a half cent."""
    from decimal import Decimal

    value = Decimal(raw) if isinstance(raw, str) else raw
    assert agg.cents(value) == Decimal(cents)


@pytest.mark.parametrize("raw", [None, float("nan"), float("inf"), float("-inf")])
def test_no_value_sorts_last_and_never_crashes(agg, raw) -> None:
    from gateway.routes.crm.aggregate import AggregateGroup

    assert agg.cents(raw) is None
    group = AggregateGroup(key="k", label="x", value=None)
    assert agg.cut_key(group, raw)[0] is True


def test_the_cut_reads_the_raw_value_before_the_answer_rounds_it(agg) -> None:
    """Round 4: the shown value 0.12 of a raw 0.125 sorts as 0.13, above a
    raw 0.12, as SQL sorts them. The name alone would put "A" first."""
    from gateway.routes.crm.aggregate import AggregateGroup

    z = AggregateGroup(key="ka", label="Z", value=0.12)
    a = AggregateGroup(key="kb", label="A", value=0.12)
    order = sorted([(a, 0.12), (z, 0.125)], key=lambda g: agg.cut_key(*g))
    assert [g.label for g, _raw in order] == ["Z", "A"]
    neg = sorted([(AggregateGroup(key="ka", label="A", value=-0.12), -0.125),
                  (AggregateGroup(key="kb", label="Z", value=-0.12), -0.12)],
                 key=lambda g: agg.cut_key(*g))
    assert [g.label for g, _raw in neg] == ["Z", "A"]


@pytest.mark.parametrize(("raw", "cents"), [
    ("1E+500", "1E+500"), ("100.12499999999999999", "100.12"),
    ("1000000000000000.125", "1000000000000000.13"),
    ("-0.12499999999999999999", "-0.12"), ("-10.12499999999999999", "-10.12"),
])
def test_the_cut_rounds_the_exact_numeric(agg, raw, cents) -> None:
    """Round 5: the cut rounds the ``numeric`` of the row, never a float of
    it. A float of 100.12499999999999999 is 100.125, which rounds up."""
    from decimal import Decimal

    assert agg.cents(Decimal(raw)) == Decimal(cents)


def test_a_huge_or_odd_value_never_crashes(agg) -> None:
    """Round 5: 10**400 is no float (OverflowError before), and a NaN
    numeric has no cents."""
    from decimal import Decimal

    assert agg.cents(10**400) == Decimal(10**400)
    assert agg.cents(Decimal("NaN")) is None and agg.cents(None) is None
    assert agg.cents(Decimal("-1E+5000")) == Decimal("-1E+5000")


def test_a_row_keeps_its_exact_value(agg) -> None:
    """Round 5: ``_acc_of`` keeps the numeric of the row in ``raw`` and
    shows the float as before. The cut reads ``raw``."""
    from decimal import Decimal
    from types import SimpleNamespace

    row = SimpleNamespace(value=Decimal("10.12499999999999999"), s=0, n=1, label="Z", c=2)
    acc = agg._acc_of(row, 1)
    assert acc.value == 10.125 and acc.raw == Decimal("10.12499999999999999")
    assert agg.cents(agg._cut_value(acc)) == Decimal("10.12")
    assert agg._acc_of(SimpleNamespace(value=3, s=0, n=0, label=None, c=3), 1).raw == 3
    acc.value = None  # the field is empty in every row
    assert agg._cut_value(acc) is None
