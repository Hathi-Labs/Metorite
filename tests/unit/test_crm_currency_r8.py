"""R8 — the aggregate and the weekly digest keep each currency apart (task 888).

Spec: ``project-docs/specs/crm_platform.md``, the H-333 note.

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal:

1. ``GET /crm/aggregate`` ``sum_amount`` and ``weighted`` on deals: the value of
   each group and the total hold the INR sum, and ``others`` holds the USD sum.
   No figure is the cross-currency sum. ``count`` counts every currency.
2. A code that is padded, lower case or empty is read as the screen reads it:
   ``' usd '`` is USD, ``''`` and ``'inr'`` are INR.
3. The weekly digest has one part for each currency, each in its own code.
4. A deal of organization B reaches no figure of organization A.

A hermetic fake agrees with any SQL it is handed, so none of this is proved
without the database. The hermetic half is ``test_crm_currency.py``.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_digest_r8 import (  # noqa: F401
    _DB_GATE,
    NOW,
    _native_org,
    _part,
    _row,
    _stage,
    _step,
    dg8,
)
from tests.unit.test_crm_next_activity_r8 import _call
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_BIG_B = 99999.0


def _deal(p: Any, org: str, status: str, amount: float, *, currency: str = "INR",
          probability: int | None = None, close: date | None = None,
          name: str = "Deal") -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, amount, currency, "
            "probability, expected_close_date) VALUES (CAST(:o AS uuid), :n, "
            "CAST(:s AS uuid), :a, :cur, :p, :c)"),
            {"o": org, "n": name, "s": status, "a": amount, "cur": currency,
             "p": probability, "c": close})


async def _agg(p: Any, org: str, query: str) -> dict[str, Any]:
    res = await _call(p, org, "GET", f"/crm/aggregate?{query}")
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
async def seeded(dg8):
    """Org A: INR and USD deals in one open lane. Org B: a big USD deal."""
    a, b = dg8.org_a, dg8.org_b
    stages_a = await _native_org(dg8, a)
    stages_b = await _native_org(dg8, b)
    dg8.open_id = _stage(stages_a, "open")
    dg8.won_id = _stage(stages_a, "won")
    nov, dec = date(2026, 11, 3), date(2026, 12, 20)
    _deal(dg8, a, dg8.open_id, 1000, probability=50, close=nov)
    _deal(dg8, a, dg8.open_id, 2000, probability=10, close=nov)
    _deal(dg8, a, dg8.open_id, 400, currency="USD", probability=50, close=nov)
    _deal(dg8, a, dg8.open_id, 600, currency="USD", probability=100, close=dec)
    # The screen reads '' and 'inr' as INR, and ' usd ' as USD.
    _deal(dg8, a, dg8.open_id, 30, currency="", probability=100, close=nov)
    _deal(dg8, a, dg8.open_id, 5, currency="inr", probability=100, close=nov)
    _deal(dg8, a, dg8.open_id, 70, currency=" usd ", probability=100, close=dec)
    # A closed USD deal: weighted reads open lanes only.
    _deal(dg8, a, dg8.won_id, 9000, currency="EUR", close=nov)
    _deal(dg8, b, _stage(stages_b, "open"), _BIG_B, currency="USD", probability=100,
          close=nov)
    return dg8


def _others(rows: list[dict]) -> dict[str, dict]:
    return {o["currency"]: o for o in rows}


async def test_sum_amount_by_status_keeps_each_currency_apart(seeded) -> None:
    body = await _agg(seeded, seeded.org_a, "entity=deals&measure=sum_amount&group_by=status")
    assert body["currency"] == "INR"
    # 1000 + 2000 + 30 ('') + 5 ('inr'). The USD and EUR deals are not in it.
    assert body["total"] == 3035
    usd = _others(body["others"])
    assert set(usd) == {"USD", "EUR"}
    assert usd["USD"]["total"] == 1070  # 400 + 600 + 70 (' usd ')
    assert usd["EUR"]["total"] == 9000
    groups = {g["key"]: g for g in body["groups"]}
    open_group = groups[seeded.open_id]
    assert open_group["value"] == 3035
    assert _others(open_group["others"]) == {"USD": {"currency": "USD", "value": 1070}}
    # The EUR deal sits in the Won lane, which holds no INR.
    won_group = groups[seeded.won_id]
    assert won_group["value"] == 0
    assert _others(won_group["others"])["EUR"]["value"] == 9000
    forbidden = {3035 + 1070, 3035 + 1070 + 9000, 3035 + 9000}
    assert not ({body["total"], open_group["value"]} & forbidden)
    assert body["records"] == 8 and body["omitted"] == 0


async def test_sum_amount_with_no_group_is_one_total_per_currency(seeded) -> None:
    body = await _agg(seeded, seeded.org_a, "entity=deals&measure=sum_amount")
    assert (body["currency"], body["total"], body["groups"]) == ("INR", 3035, [])
    assert _others(body["others"])["USD"]["total"] == 1070


async def test_weighted_applies_each_deals_own_probability_per_currency(seeded) -> None:
    body = await _agg(seeded, seeded.org_a, "entity=deals&measure=weighted")
    assert body["currency"] == "INR"
    # 1000 * 50% + 2000 * 10% + 30 + 5. The EUR deal is in a won lane.
    assert body["total"] == 500 + 200 + 30 + 5
    assert list(_others(body["others"])) == ["USD"]
    assert _others(body["others"])["USD"]["total"] == 200 + 600 + 70
    assert body["records"] is None


async def test_a_month_group_splits_by_currency(seeded) -> None:
    body = await _agg(seeded, seeded.org_a,
                      "entity=deals&measure=sum_amount&group_by=close_month")
    months = {g["key"]: g for g in body["groups"]}
    assert months["2026-11"]["value"] == 1000 + 2000 + 30 + 5
    assert _others(months["2026-11"]["others"])["USD"]["value"] == 400
    # December holds USD deals only, so INR is 0 and the USD figures stay apart.
    assert months["2026-12"]["value"] == 0
    assert _others(months["2026-12"]["others"])["USD"]["value"] == 670


async def test_count_is_not_money_and_counts_every_currency(seeded) -> None:
    body = await _agg(seeded, seeded.org_a, "entity=deals&measure=count&group_by=status")
    assert body["total"] == 8
    assert "currency" not in body and "others" not in body


async def test_a_crm_with_one_currency_has_no_others(seeded) -> None:
    body = await _agg(seeded, seeded.org_b, "entity=deals&measure=sum_amount&group_by=status")
    assert (body["currency"], body["total"]) == ("USD", _BIG_B)
    assert "others" not in body
    assert all("others" not in g for g in body["groups"])


async def test_an_empty_crm_reads_as_inr(dg8) -> None:
    await _native_org(dg8, dg8.org_a)
    body = await _agg(dg8, dg8.org_a, "entity=deals&measure=sum_amount")
    assert (body["currency"], body["total"]) == ("INR", 0)
    assert "others" not in body


async def test_organization_b_reaches_no_figure_of_a(seeded) -> None:
    for query in ("measure=sum_amount&group_by=status", "measure=weighted",
                  "measure=sum_amount&group_by=close_month"):
        body = await _agg(seeded, seeded.org_a, f"entity=deals&{query}")
        assert "99999" not in json.dumps(body), query


async def test_the_digest_has_one_money_part_for_each_currency(seeded) -> None:
    got = await _step(seeded, seeded.org_a, NOW)
    assert got.reason == "stored", got
    [row] = _row(seeded, seeded.org_a)
    numbers = row.numbers
    inr = _part(numbers, "amount_by_close_month")
    usd = _part(numbers, "amount_by_close_month_usd")
    eur = _part(numbers, "amount_by_close_month_eur")
    assert (inr["unit"], usd["unit"], eur["unit"]) == ("INR", "USD", "EUR")
    assert inr["total"] == "INR 3,035"
    assert inr["rows"] == [{"label": "November 2026", "value": "INR 3,035"}]
    assert usd["total"] == "USD 1,070"
    assert usd["rows"] == [{"label": "November 2026", "value": "USD 400"},
                           {"label": "December 2026", "value": "USD 670"}]
    assert eur["total"] == "EUR 9,000"
    stage = _part(numbers, "open_amount_by_stage")
    stage_usd = _part(numbers, "open_amount_by_stage_usd")
    assert stage["unit"] == "INR" and stage_usd["unit"] == "USD"
    assert [r["value"] for r in stage["rows"]] == ["INR 3,035"]
    assert [r["value"] for r in stage_usd["rows"]] == ["USD 1,070"]
    # The EUR deal is in a won lane, so no open part holds it.
    assert _part(numbers, "open_amount_by_stage_eur") is None
    # No part holds a figure that adds two currencies.
    blob = json.dumps(numbers)
    for mixed in ("4,105", "4,415", "13,105", "3,035 USD"):
        assert mixed not in blob


async def test_the_digest_of_one_currency_is_unchanged(seeded) -> None:
    got = await _step(seeded, seeded.org_b, NOW)
    assert got.reason == "stored", got
    [row] = _row(seeded, seeded.org_b)
    part = _part(row.numbers, "amount_by_close_month")
    assert part["unit"] == "USD" and part["total"] == "USD 99,999"
    assert _part(row.numbers, "amount_by_close_month_usd") is None
