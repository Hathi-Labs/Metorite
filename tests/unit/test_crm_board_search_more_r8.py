"""R8 — the deals board: ``?q=`` and a lane's next page (CRM-X1 on the board).

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal:

1. ``GET /crm/pipeline?q=`` applies the SAME search predicate as the deals
   list (``core.search_where``): a name, a company name, a digit string. The
   lane counts and totals follow the filtered deals.
2. ``?lane_status_id=&lane_offset=`` reads the next page of ONE lane. The
   pages have no gap and no repeat, and the lane totals never change.
3. Another organization's deals never appear, also through the company
   sub-select of the search.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_find_and_page_r8 import (  # noqa: F401
    _FIRST_STAGE,
    _TABLES,
    _admin,
    _call,
    _make,
    _native,
    ns,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


async def _board(p: Any, org: str, query: str = "") -> list[dict]:
    res = await _call(p, _admin(org), "GET", f"/crm/pipeline{query}")
    assert res.status_code == 200, res.text
    return res.json()["lanes"]


def _names(lanes: list[dict]) -> list[str]:
    return sorted(row["name"] for lane in lanes for row in lane["rows"])


async def test_q_narrows_the_lanes_and_matches_the_list(ns, app_engine) -> None:  # noqa: F811
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    zephyr = await _make(ns, a, "/crm/organizations", {"name": f"Zephyr{tag} Robotics"})
    await _make(ns, a, "/crm/deals", {"name": "Spare parts", "amount": 100, "company_id": zephyr})
    await _make(ns, a, "/crm/deals", {"name": f"Zephyr{tag} retrofit", "amount": 50})
    await _make(ns, a, "/crm/deals", {"name": "Unrelated", "amount": 7})
    # Organization B: a deal pointing at A's company, and a same-name deal.
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, company_id, status_id) "
            "VALUES (CAST(:o AS uuid), 'Bee deal', CAST(:c AS uuid), "
            f"{_FIRST_STAGE})"), {"o": b, "c": zephyr})
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id) "
            f"VALUES (CAST(:o AS uuid), 'Zephyr{tag} B only', {_FIRST_STAGE})"), {"o": b})

    lanes = await _board(ns, a, f"?q=zephyr{tag}")
    # The company name and the deal name both match. Nothing else does.
    assert _names(lanes) == ["Spare parts", f"Zephyr{tag} retrofit"]
    assert sum(lane["count"] for lane in lanes) == 2
    assert sum(lane["amount"] for lane in lanes) == 150
    # The same deals as the list with the same q.
    listed = await _call(ns, _admin(a), "GET", f"/crm/deals?q=zephyr{tag}")
    assert sorted(r["name"] for r in listed.json()["rows"]) == _names(lanes)
    # Digits that match no deal text give an empty board, as the list does.
    nothing = await _board(ns, a, "?q=9999999999")
    listed_none = await _call(ns, _admin(a), "GET", "/crm/deals?q=9999999999")
    assert _names(nothing) == [] == [r["name"] for r in listed_none.json()["rows"]]
    assert sum(lane["count"] for lane in nothing) == 0
    # With no q the board holds all of A's deals (3), and none of B's.
    whole = await _board(ns, a)
    assert _names(whole) == sorted(["Spare parts", f"Zephyr{tag} retrofit", "Unrelated"])
    # B's search reaches B's deals only: A's company name stays out of it.
    bee = await _board(ns, b, f"?q=zephyr{tag}")
    assert _names(bee) == [f"Zephyr{tag} B only"]
    assert _names(await _board(ns, a, "?q=bee")) == []


async def test_a_lane_offset_reads_the_next_page_with_stable_totals(ns, app_engine) -> None:  # noqa: F811
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    with ns.admin_engine.begin() as c:
        # 130 deals in the first stage, with ties on every sort key, so only
        # the id tie-breaker keeps the pages stable.
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, amount, status_id) "
            f"SELECT CAST(:o AS uuid), 'P{tag}-' || g, 10, {_FIRST_STAGE} "
            "FROM generate_series(1, 130) g"), {"o": a})
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, amount, status_id) "
            f"SELECT CAST(:o AS uuid), 'B{tag}-' || g, 10, {_FIRST_STAGE} "
            "FROM generate_series(1, 5) g"), {"o": b})
    full = await _board(ns, a, f"?q=P{tag}")
    lane = next(item for item in full if item["count"] == 130)
    sid = lane["status"]["id"]
    assert len(lane["rows"]) == 50

    for sort in ("stage_age", "recently_changed", "close_date", "amount", "next_activity"):
        got: list[str] = []
        for offset in (0, 50, 100):
            q = f"?q=P{tag}&card_sort={sort}&lane_status_id={sid}&lane_offset={offset}"
            lanes = await _board(ns, a, q)
            # One lane only, with the totals of the whole lane.
            assert [item["status"]["id"] for item in lanes] == [sid]
            only = lanes[0]
            assert (only["count"], only["amount"]) == (130, 1300.0)
            got.extend(row["id"] for row in only["rows"])
        assert len(got) == 130, sort
        assert len(set(got)) == 130, f"{sort}: a card repeated"
        # No gap: the pages are the whole lane, and none of B's deals.
        with ns.admin_engine.begin() as c:
            held = {str(r[0]) for r in c.execute(text(
                "SELECT id FROM crm_deals WHERE organization_id = CAST(:o AS uuid) "
                "AND name LIKE :p"), {"o": a, "p": f"P{tag}-%"})}
        assert set(got) == held, f"{sort}: a gap"
    # Past the end is an empty lane page, and the totals still hold.
    tail = await _board(ns, a, f"?q=P{tag}&lane_status_id={sid}&lane_offset=130")
    assert tail[0]["rows"] == [] and tail[0]["count"] == 130
    # B's lane id read as A returns no B deal.
    assert _names(await _board(ns, a, f"?q=B{tag}&lane_status_id={sid}&lane_offset=0")) == []
    # A bad offset or lane id is a 422.
    bad = await _call(ns, _admin(a), "GET", "/crm/pipeline?lane_offset=-1")
    assert bad.status_code == 422
    bad = await _call(ns, _admin(a), "GET", "/crm/pipeline?lane_status_id=nope")
    assert bad.status_code == 422


async def test_a_lane_keeps_each_currency_apart(ns, app_engine) -> None:  # noqa: F811
    """Task 887 (H-333): one lane of INR and USD deals gives a total for each
    currency, over the WHOLE lane and not over the page, and another
    organization's deal reaches no figure."""
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    await _native(ns, a)
    await _native(ns, b)
    with ns.admin_engine.begin() as c:
        for currency, amount, probability, count in (
            ("INR", 10, 50, 60), ("USD", 1, 20, 60), (" usd ", 5, 100, 1),
            ("", 4, 50, 1),
        ):
            c.execute(text(
                "INSERT INTO crm_deals (organization_id, name, amount, "
                "probability, currency, status_id) "
                f"SELECT CAST(:o AS uuid), 'C{tag}-' || :n || '-' || g, :amount, "
                f":p, :cur, {_FIRST_STAGE} FROM generate_series(1, :k) g"),
                {"o": a, "n": amount, "amount": amount, "p": probability,
                 "cur": currency, "k": count})
        c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, amount, probability, "
            f"currency, status_id) VALUES (CAST(:o AS uuid), 'C{tag}-bee', 99999, "
            f"100, 'USD', {_FIRST_STAGE})"), {"o": b})

    lanes = await _board(ns, a, f"?q=C{tag}")
    lane = next(item for item in lanes if item["count"] == 122)
    sid = lane["status"]["id"]
    assert len(lane["rows"]) == 50
    # INR is the primary: 60 * 10 plus the blank code at 4. Weighted at 50%.
    assert (lane["currency"], lane["amount"], lane["weighted"]) == ("INR", 604.0, 302.0)
    # USD: 60 * 1 plus the padded " usd " at 5. Weighted: 60 * 20% + 5.
    assert [(o["currency"], o["amount"], o["weighted"]) for o in lane["others"]] == [
        ("USD", 65.0, 17.0),
    ]
    # A later page holds the same totals. B's USD deal is in no figure.
    page = await _board(
        ns, a, f"?q=C{tag}&lane_status_id={sid}&lane_offset=100",
    )
    assert len(page) == 1 and len(page[0]["rows"]) == 22
    assert (page[0]["count"], page[0]["currency"], page[0]["amount"]) == (122, "INR", 604.0)
    assert page[0]["others"] == lane["others"]
    # Organization B sees its own USD deal alone, as the primary currency.
    bee = await _board(ns, b, f"?q=C{tag}")
    held = next(item for item in bee if item["count"] == 1)
    assert (held["currency"], held["amount"], held["others"]) == ("USD", 99999.0, [])
