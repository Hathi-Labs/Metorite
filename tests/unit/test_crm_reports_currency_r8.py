"""R8 — the CRM report routes never add two currencies (HANDOFF H-333).

Spec: ``project-docs/specs/crm_app.md`` §5.1 system 2.

Against a REAL Postgres, through the real ``/crm`` router, as the
non-privileged role of the phase-4 rehearsal:

1. ``/crm/reports/pipeline``: one open lane holds INR and USD deals. The
   scalars hold the INR sum and ``others`` holds the USD sum. The weighted
   figure applies each deal's own probability, else the stage's.
2. ``/crm/reports/win-loss``: won, lost and lost-reason amounts split by
   currency.
3. ``/crm/reports/owners``: weighted and won split by currency.
4. No figure equals the cross-currency sum.
5. A USD deal of organization B reaches no figure of organization A.

A hermetic fake agrees with any SQL it is handed, so none of these is proved
without the database.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_TABLES = (
    "crm_status_changes", "crm_deal_contacts", "crm_activities", "crm_deals",
    "crm_leads", "crm_contacts", "crm_companies", "crm_field_defs",
    "crm_pipeline_stages", "crm_pipelines", "crm_settings",
    "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)

_BIG_B = 99999.0


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _admin(org: str) -> Any:
    return _principal("admin@h333-r8.test", org, "feature:crm", "admin:access:manage")


async def _call(p: Any, user: Any, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


async def _get(p: Any, org: str, path: str) -> Any:
    res = await _call(p, _admin(org), "GET", path)
    assert res.status_code == 200, res.text
    return res.json()


def _stage(c: Any, org: str, name: str) -> str:
    return str(c.execute(text(
        "SELECT id FROM crm_deal_statuses WHERE organization_id = CAST(:o AS uuid) "
        "AND name = :n"), {"o": org, "n": name}).scalar())


def _reason(c: Any, org: str, label: str) -> str:
    return str(c.execute(text(
        "SELECT id FROM crm_lost_reasons WHERE organization_id = CAST(:o AS uuid) "
        "AND label = :n"), {"o": org, "n": label}).scalar())


def _deal(c: Any, org: str, name: str, stage: str, amount: float, *, owner: str,
          currency: str = "INR", probability: int | None = None,
          closed_days: int | None = None, reason: str | None = None) -> None:
    c.execute(text(
        "INSERT INTO crm_deals (organization_id, name, status_id, amount, currency, "
        "probability, owner_email, closed_at, lost_reason_id) VALUES "
        "(CAST(:o AS uuid), :n, CAST(:s AS uuid), :a, :cur, :p, :w, "
        "CASE WHEN CAST(:d AS int) IS NULL THEN NULL "
        "ELSE now() - make_interval(days => CAST(:d AS int)) END, "
        "CAST(:r AS uuid))"),
        {"o": org, "n": name, "s": _stage(c, org, stage), "a": amount,
         "cur": currency, "p": probability, "w": owner, "d": closed_days,
         "r": reason})


@pytest.fixture
async def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    for org in (promoted.org_a, promoted.org_b):
        res = await _call(promoted, _admin(org), "PATCH", "/crm/settings",
                          {"mode": "native"})
        assert res.status_code == 200, res.text
    a, b = promoted.org_a, promoted.org_b
    with promoted.admin_engine.begin() as c:
        # One open lane (Proposal, 50%) holds both currencies.
        _deal(c, a, "P1", "Proposal", 1000, owner="o1@h333.test")  # 50% -> 500
        _deal(c, a, "P2", "Proposal", 2000, owner="o1@h333.test", probability=10)
        _deal(c, a, "P3", "Proposal", 3000, owner="o2@h333.test", probability=20)
        _deal(c, a, "U1", "Proposal", 400, owner="o1@h333.test", currency="USD")
        _deal(c, a, "U2", "Proposal", 600, owner="o2@h333.test", currency="USD",
              probability=100)
        # The 90-day window: one won in each currency, and two lost.
        _deal(c, a, "W1", "Closed Won", 5000, owner="o1@h333.test", closed_days=5)
        _deal(c, a, "W2", "Closed Won", 700, owner="o2@h333.test", currency="USD",
              closed_days=5)
        _deal(c, a, "L1", "Closed Lost", 111, owner="o2@h333.test", closed_days=6)
        _deal(c, a, "L2", "Closed Lost", 300, owner="o2@h333.test", currency="USD",
              closed_days=6, reason=_reason(c, a, "Price"))
        # Organization B: a large USD deal that must reach no figure of A.
        _deal(c, b, "B1", "Proposal", _BIG_B, owner="o1@h333.test", currency="USD")
        _deal(c, b, "B2", "Closed Won", _BIG_B, owner="o1@h333.test", currency="USD",
              closed_days=3)
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (a, b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


def _others(rows: list[dict]) -> dict[str, dict]:
    return {o["currency"]: o for o in rows}


async def test_pipeline_lane_holds_the_inr_sum_and_others_the_usd_sum(ns, app_engine) -> None:  # noqa: F811
    body = await _get(ns, ns.org_a, "/crm/reports/pipeline")
    lane = {s["status"]["name"]: s for s in body["stages"]}["Proposal"]
    assert lane["currency"] == "INR"
    assert (lane["count"], lane["amount"]) == (5, 6000)
    # 1000 inherits 50% (500) + 2000 at 10% (200) + 3000 at 20% (600).
    assert lane["weighted"] == 1300
    usd = _others(lane["others"])
    assert list(usd) == ["USD"]
    # 400 inherits 50% (200) + 600 at 100% (600): each deal's own probability.
    assert (usd["USD"]["amount"], usd["USD"]["weighted"]) == (1000, 800)
    assert body["currency"] == "INR"
    assert (body["amount"], body["weighted"], body["count"]) == (6000, 1300, 5)
    assert _others(body["others"])["USD"]["amount"] == 1000
    assert _others(body["others"])["USD"]["weighted"] == 800


async def test_no_pipeline_figure_is_the_cross_currency_sum(ns, app_engine) -> None:  # noqa: F811
    body = await _get(ns, ns.org_a, "/crm/reports/pipeline")
    forbidden = {7000, 2100}
    seen = {body["amount"], body["weighted"]}
    for stage in body["stages"]:
        seen |= {stage["amount"], stage["weighted"]}
    assert not seen & forbidden
    assert _BIG_B not in seen


async def test_win_loss_splits_won_lost_and_reasons_by_currency(ns, app_engine) -> None:  # noqa: F811
    body = await _get(ns, ns.org_a, "/crm/reports/win-loss")
    assert body["currency"] == "INR"
    assert (body["won"], body["lost"]) == (2, 2)
    assert (body["won_amount"], body["lost_amount"]) == (5000, 111)
    usd = _others(body["others"])["USD"]
    assert (usd["won_amount"], usd["lost_amount"]) == (700, 300)
    shares = {r["label"]: r for r in body["lost_reasons"]}
    assert (shares["Price"]["currency"], shares["Price"]["amount"]) == ("INR", 0)
    assert _others(shares["Price"]["others"])["USD"]["amount"] == 300
    assert shares["Not recorded"]["amount"] == 111
    assert shares["Not recorded"]["others"] == []
    assert body["won_amount"] != 5700 and body["lost_amount"] != 411


async def test_owners_split_weighted_and_won_by_currency(ns, app_engine) -> None:  # noqa: F811
    body = await _get(ns, ns.org_a, "/crm/reports/owners")
    rows = {r["owner_email"]: r for r in body["owners"]}
    o1, o2 = rows["o1@h333.test"], rows["o2@h333.test"]
    assert (o1["currency"], o1["weighted"], o1["won_amount"]) == ("INR", 700, 5000)
    assert (o1["open_deals"]) == 3
    usd1 = _others(o1["others"])["USD"]
    assert (usd1["weighted"], usd1["won_amount"]) == (200, 0)
    assert (o2["currency"], o2["weighted"], o2["won_amount"]) == ("INR", 600, 0)
    assert (o2["open_deals"]) == 2
    usd2 = _others(o2["others"])["USD"]
    assert (usd2["weighted"], usd2["won_amount"]) == (600, 700)


async def test_organization_b_usd_deal_reaches_no_figure_of_a(ns, app_engine) -> None:  # noqa: F811
    for kind in ("pipeline", "win-loss", "owners"):
        body = await _get(ns, ns.org_a, f"/crm/reports/{kind}")
        assert str(_BIG_B) not in str(body) and "99999" not in str(body), kind
    other = await _get(ns, ns.org_b, "/crm/reports/pipeline")
    assert other["currency"] == "USD" and other["amount"] == _BIG_B
    assert other["others"] == []
