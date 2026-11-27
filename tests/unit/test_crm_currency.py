"""Aggregate, digest and AI tool keep each currency apart (HANDOFF H-333, task 888).

Spec: ``project-docs/specs/crm_platform.md``, the H-333 note. The app holds no
exchange rate, so no money figure adds two currencies. The reports did this
first (``test_crm_reports.py``). These tests fence the same rule on the other
three surfaces, and on the one helper they share (``currency.py``).

Hermetic. The SQL is proved on a real database in
``test_crm_currency_r8.py``.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from acb_common import number_check
from acb_common.number_check import MONEY, format_figure
from acb_skills import crm_tools as T
from gateway.crm_sync import digest as dg
from gateway.routes.crm import aggregate as agg
from gateway.routes.crm import currency as cur

_CRM = (
    Path(__file__).resolve().parents[2]
    / "apps" / "services" / "gateway" / "gateway" / "routes" / "crm"
)


# ── The shared helper ───────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "code"), [
    ("INR", "INR"), ("usd", "USD"), (" usd ", "USD"), ("", "INR"), ("  ", "INR"),
    (None, "INR"), ("Eur", "EUR"),
])
def test_a_code_is_trimmed_upper_cased_and_empty_reads_inr(raw: Any, code: str) -> None:
    assert cur.normalise_currency(raw) == code


def test_the_codes_lead_with_inr_then_run_in_code_order() -> None:
    assert cur.ordered_currencies(["USD", None, "aed", " eur ", "INR"]) == [
        "INR", "AED", "EUR", "USD",
    ]
    assert cur.ordered_currencies(["usd", "USD"]) == ["USD"]
    assert cur.ordered_currencies([]) == []


def test_the_sql_form_is_the_python_form() -> None:
    """The two forms must agree. The R8 suite runs the SQL on Postgres."""
    assert cur.CURRENCY_SQL == "COALESCE(NULLIF(upper(trim(currency)), ''), 'INR')"
    assert cur.DEFAULT_CURRENCY == "INR"


def test_reports_and_aggregate_share_the_one_helper() -> None:
    from gateway.routes.crm import reports

    assert reports.DEFAULT_CURRENCY is cur.DEFAULT_CURRENCY
    for name in ("reports.py", "aggregate.py"):
        source = (_CRM / name).read_text(encoding="utf-8")
        assert "gateway.routes.crm.currency import" in source
        assert "NULLIF(upper(" not in source, "a second copy of the rule"


def test_every_deal_money_statement_of_the_aggregate_is_bound_to_a_currency() -> None:
    source = inspect.getsource(agg)
    assert 'f"{CURRENCY_SQL} = :agg_currency"' in source
    assert source.count("split_currencies(") >= 2  # def + call


# ── The aggregate ───────────────────────────────────────────────────────────

def _acc(value: float, rows: int = 1, name: str | None = None) -> agg._Acc:
    return agg._Acc(value=value, rows=rows, name=name)


def test_one_currency_is_the_old_answer() -> None:
    per = {"INR": {"a": _acc(10, 2), "b": _acc(5)}}
    values, primary, others = agg.split_currencies(per)
    assert (primary, others) == ("INR", [])
    assert {k: (a.value, a.others) for k, a in values.items()} == {
        "a": (10, {}), "b": (5, {}),
    }


def test_two_currencies_split_and_the_primary_leads() -> None:
    per = {
        "INR": {"a": _acc(10, 2)},
        "USD": {"a": _acc(4, 1), "b": _acc(7, 3)},
    }
    values, primary, others = agg.split_currencies(per)
    assert (primary, others) == ("INR", ["USD"])
    assert values["a"].value == 10 and values["a"].others == {"USD": 4}
    # A group with USD deals only: INR is 0, and the USD figure sits in others.
    assert values["b"].value == 0 and values["b"].others == {"USD": 7}
    # The record counts of both currencies add up.
    assert (values["a"].rows, values["b"].rows) == (3, 3)


def test_a_currency_with_only_zeros_is_not_reported() -> None:
    """The weighted measure reads open lanes only, so a closed USD deal gives 0."""
    per = {"INR": {"a": _acc(10)}, "USD": {"a": _acc(0, 4)}}
    values, primary, others = agg.split_currencies(per)
    assert (primary, others, values["a"].others) == ("INR", [], {})
    assert values["a"].rows == 5


def test_the_primary_is_the_first_currency_even_at_zero() -> None:
    """The reports lead with INR whenever INR is present. So does this."""
    per = {"INR": {"a": _acc(0)}, "USD": {"a": _acc(500)}}
    values, primary, others = agg.split_currencies(per)
    assert (primary, others) == ("INR", ["USD"])
    assert values["a"].value == 0 and values["a"].others == {"USD": 500}


def test_the_digest_leads_with_inr_at_zero_and_keeps_usd_apart() -> None:
    per = {"INR": {"2026-11": _acc(0)}, "USD": {"2026-11": _acc(500)}}
    values, primary, others = agg.split_currencies(per)
    answer = _answer(
        [("2026-11", "2026-11", values["2026-11"].value,
          [agg.CurrencyValue(currency="USD", value=500)])],
        0, currency=primary,
        others=[agg.CurrencyTotal(currency=c, total=500) for c in others])
    inr, usd = dg._parts(_query("amount_by_close_month"), answer, {})
    assert (inr["unit"], usd["unit"]) == ("INR", "USD")
    assert inr["rows"] == [] and inr["total"] == "INR 0"
    assert usd["rows"] == [{"label": "November 2026", "value": "USD 500"}]


def _stub(monkeypatch: pytest.MonkeyPatch, read: agg._Read) -> None:
    @asynccontextmanager
    async def session():
        yield object()

    async def no_overlay(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_read(*_a: Any, **_k: Any) -> agg._Read:
        return read

    monkeypatch.setattr(agg, "_tenant_session", session)
    monkeypatch.setattr(agg, "overlay_of", no_overlay)
    monkeypatch.setattr(agg, "current_tenant", lambda: "org")
    monkeypatch.setattr(agg, "_read", fake_read)


def _read(values: dict, currency: str | None, codes: list[str]) -> agg._Read:
    return agg._Read(values, {}, {}, None, None, "UTC", {}, currency=currency, codes=codes)


def _params() -> Any:
    from gateway.routes.crm.records import ListParams

    return ListParams(direction="desc", page=1, page_size=50)


@pytest.mark.asyncio
async def test_the_response_of_one_currency_has_no_others_on_the_wire(monkeypatch) -> None:
    values = {"a": _acc(1500.5), "b": _acc(10)}
    _stub(monkeypatch, _read(values, "INR", []))
    out = await agg.compute("deals", measure="sum_amount", group_by="owner", params=_params())
    assert out.currency == "INR" and out.others == [] and out.total == 1510.5
    wire = out.model_dump()
    assert "others" not in wire and all("others" not in g for g in wire["groups"])


@pytest.mark.asyncio
async def test_the_response_keeps_each_currency_apart(monkeypatch) -> None:
    per = {
        "INR": {"a": _acc(10, 2), "b": _acc(5)},
        "USD": {"a": _acc(4), "c": _acc(3)},
    }
    values, primary, others = agg.split_currencies(per)
    _stub(monkeypatch, _read(values, primary, others))
    out = await agg.compute("deals", measure="sum_amount", group_by="owner", params=_params())
    assert out.currency == "INR" and out.total == 15
    assert [(o.currency, o.total, o.other) for o in out.others] == [("USD", 7, 0)]
    by_key = {g.key: g for g in out.groups}
    assert [(o.currency, o.value) for o in by_key["a"].others] == [("USD", 4)]
    assert by_key["b"].others == []
    assert by_key["c"].value == 0 and by_key["c"].others[0].value == 3


@pytest.mark.asyncio
async def test_the_other_currencies_fold_past_fifty_groups(monkeypatch) -> None:
    values = {
        f"k{i:02d}": agg._Acc(value=float(100 - i), rows=1, others={"USD": 1.0})
        for i in range(52)
    }
    _stub(monkeypatch, _read(values, "INR", ["USD"]))
    out = await agg.compute("deals", measure="sum_amount", group_by="owner", params=_params())
    assert out.omitted == 2
    (usd,) = out.others
    assert (usd.total, usd.other) == (52, 2)


@pytest.mark.asyncio
async def test_a_count_and_a_module_sum_carry_no_currency(monkeypatch) -> None:
    _stub(monkeypatch, _read({"a": _acc(3)}, None, []))
    out = await agg.compute("deals", measure="count", group_by="owner", params=_params())
    assert out.currency is None and "currency" not in out.model_dump()


# ── The digest ──────────────────────────────────────────────────────────────

def _answer(groups, total, *, currency=None, others=None, other=0, omitted=0):
    return agg.AggregateResponse(
        entity="deals", measure="sum_amount", currency=currency, others=others or [],
        groups=[
            agg.AggregateGroup(key=k, label=lbl, value=v, others=o or [])
            for k, lbl, v, o in groups
        ],
        total=total, other=other, omitted=omitted)


def _query(key: str) -> Any:
    return next(q for q in dg.DIGEST_QUERIES if q.key == key)


def test_one_currency_is_one_part_in_its_own_code() -> None:
    query = _query("amount_by_close_month")
    parts = dg._parts(query, _answer([("2026-11", "2026-11", 1500.5, None)], 1500.5,
                                     currency="INR"), {})
    assert len(parts) == 1
    assert parts[0]["unit"] == "INR" and parts[0]["title"] == query.title
    assert parts[0]["rows"] == [{"label": "November 2026", "value": "INR 1,500.50"}]
    # A CRM with USD only is labelled USD, not INR.
    usd = dg._parts(query, _answer([("2026-11", "2026-11", 4500, None)], 4500,
                                   currency="USD"), {})
    assert usd[0]["unit"] == "USD"
    assert usd[0]["total"] == "USD 4,500"


def test_two_currencies_are_two_parts_with_their_own_codes() -> None:
    query = _query("amount_by_close_month")
    answer = _answer(
        [("2026-11", "2026-11", 1234567, [agg.CurrencyValue(currency="USD", value=4500)]),
         ("2026-12", "2026-12", 10, None)],
        1234577, currency="INR",
        others=[agg.CurrencyTotal(currency="USD", total=4500, other=0)])
    inr, usd = dg._parts(query, answer, {})
    assert (inr["unit"], inr["key"]) == ("INR", "amount_by_close_month")
    assert inr["rows"][0]["value"] == "INR 12,34,567" and inr["total"] == "INR 12,34,577"
    assert (usd["unit"], usd["key"]) == ("USD", "amount_by_close_month_usd")
    # The USD part lists the groups that hold USD only, and its own total.
    assert usd["rows"] == [{"label": "November 2026", "value": "USD 4,500"}]
    assert usd["total"] == "USD 4,500"
    assert "INR" in inr["title"] and "USD" in usd["title"]


def test_a_typed_part_splits_too_and_keeps_its_types() -> None:
    query = _query("open_amount_by_stage")
    answer = _answer(
        [("o", "Qualify", 100, [agg.CurrencyValue(currency="USD", value=9)]),
         ("w", "Won", 50, [agg.CurrencyValue(currency="USD", value=8)])],
        150, currency="INR", others=[agg.CurrencyTotal(currency="USD", total=17)])
    inr, usd = dg._parts(query, answer, {"o": "open", "w": "won"})
    assert inr["rows"] == [{"label": "Qualify", "value": "INR 100"}]
    assert usd["rows"] == [{"label": "Qualify", "value": "USD 9"}]
    assert "total" not in usd


def test_a_module_amount_without_a_currency_keeps_the_query_unit() -> None:
    query = _query("open_amount_by_stage")
    part = dg._parts(query, _answer([("o", "Qualify", 100, None)], 100), {"o": "open"})
    assert len(part) == 1 and part[0]["unit"] == "INR"


@pytest.mark.parametrize(("unit", "text"), [
    ("INR", "INR 12,34,567"), ("USD", "USD 12,34,567"), ("AED", "AED 12,34,567"),
])
def test_fmt_writes_the_code_of_the_part(unit: str, text: str) -> None:
    assert dg.fmt(1234567, unit) == text
    assert dg.fmt(1234567, unit) == format_figure(1234567, MONEY, currency=unit)
    assert dg.fmt(1234567, "deals") == "12,34,567"


def test_the_number_check_passes_each_money_part_and_refuses_an_invented_sum() -> None:
    answer = _answer(
        [("2026-11", "2026-11", 1234567, [agg.CurrencyValue(currency="USD", value=4500)])],
        1234567, currency="INR",
        others=[agg.CurrencyTotal(currency="USD", total=4500, other=0)])
    parts = dg._parts(_query("amount_by_close_month"), answer, {})
    numbers = {"parts": parts, "missing": []}
    assert dg.check("November 2026 holds INR 12,34,567 and USD 4,500.", numbers) == []
    # A figure that mixes the two currencies is in no source.
    mixed = dg.check("In total INR 12,39,067 closes.", numbers)
    assert mixed and "12,39,067" in mixed[0]
    # An unlisted code is read as a plain number, and still has to match.
    aed = dg._parts(_query("amount_by_close_month"), _answer(
        [("2026-11", "2026-11", 4500, None)], 4500, currency="AED"), {})
    assert dg.check("AED 4,500 closes.", {"parts": aed, "missing": []}) == []
    assert dg.check("AED 4,600 closes.", {"parts": aed, "missing": []})


def test_the_formatter_keeps_inr_as_its_default() -> None:
    assert number_check.format_figure(Decimal("5"), MONEY) == "INR 5"


# ── The AI tool ─────────────────────────────────────────────────────────────

def _tool(monkeypatch, data: dict[str, Any]) -> None:
    async def get(path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/crm/aggregate":
            return data
        raise T._GatewayError("off", "off", status=404)

    monkeypatch.setattr(T, "_get", get)


@pytest.mark.asyncio
async def test_the_tool_prints_each_amount_with_its_own_code(monkeypatch) -> None:
    _tool(monkeypatch, {
        "currency": "INR", "zone": "UTC", "total": 1234567,
        "groups": [
            {"key": "a", "label": "Asha", "value": 1234567,
             "others": [{"currency": "USD", "value": 4500}]},
            {"key": "b", "label": "Bo", "value": 0,
             "others": [{"currency": "USD", "value": 10}, {"currency": "EUR", "value": 5}]},
        ],
        "others": [{"currency": "USD", "total": 4510, "other": 0},
                   {"currency": "EUR", "total": 5, "other": 0}],
    })
    out = await T.crm_aggregate("deals", "owner", measure="sum_amount")
    assert "- Asha: INR 1,234,567 and USD 4,500" in out
    assert "- Bo: USD 10 and EUR 5" in out
    assert "Total: INR 1,234,567, USD 4,510 and EUR 5." in out


@pytest.mark.asyncio
async def test_the_tool_prints_one_currency_as_before(monkeypatch) -> None:
    _tool(monkeypatch, {
        "currency": "USD", "zone": "UTC", "total": 4500,
        "groups": [{"key": "a", "label": "Asha", "value": 4500}],
    })
    out = await T.crm_aggregate("deals", "owner", measure="sum_amount")
    assert "- Asha: USD 4,500" in out and "Asha: INR 0" not in out and "Total: USD 4,500." in out
    _tool(monkeypatch, {
        "zone": "UTC", "total": 3, "groups": [{"key": "a", "label": "Asha", "value": 3}],
    })
    out = await T.crm_aggregate("deals", "owner", measure="count")
    assert "- Asha: 3" in out and "INR" not in out


@pytest.mark.asyncio
async def test_the_tool_prints_a_total_with_no_group_per_currency(monkeypatch) -> None:
    _tool(monkeypatch, {
        "currency": "INR", "total": 1000, "records": 3, "groups": [],
        "others": [{"currency": "USD", "total": 50, "other": 0}],
    })
    out = await T.crm_aggregate("deals", measure="sum_amount")
    assert "INR 1,000 and USD 50 across 3 matching records." in out


@pytest.mark.asyncio
async def test_a_module_amount_keeps_inr_when_the_field_is_a_currency(monkeypatch) -> None:
    _tool(monkeypatch, {
        "zone": "UTC", "total": 99, "field_type": "currency",
        "groups": [{"key": "a", "label": "Asha", "value": 99}],
    })
    out = await T.crm_aggregate("deals", "owner", measure="sum", field="amount")
    assert "- Asha: INR 99" in out


@pytest.mark.asyncio
async def test_the_tool_omits_a_zero_main_amount_when_the_group_has_others(monkeypatch) -> None:
    _tool(monkeypatch, {
        "currency": "INR", "zone": "UTC", "total": 0,
        "groups": [
            {"key": "a", "label": "Asha", "value": 0,
             "others": [{"currency": "USD", "value": 4500}]},
            {"key": "b", "label": "Bo", "value": 0},
        ],
        "others": [{"currency": "USD", "total": 4500, "other": 0}],
    })
    out = await T.crm_aggregate("deals", "owner", measure="sum_amount")
    assert "- Asha: USD 4,500" in out and "Asha: INR 0" not in out
    assert "- Bo: INR 0" in out
    assert "Total: INR 0 and USD 4,500." in out
