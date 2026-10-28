"""R8 — the order of the cards in a deal lane (CRM-U14).

Two things are proved on a REAL Postgres. First, ``GET /crm/pipeline`` orders
the cards of a lane by each ``card_sort`` value and never changes the lane
totals. Second, ``core.restamp_deal_stage_from_source`` copies the provider's
stage time into ``status_changed_at`` for mirrored deals only, in one
organization only, in bounded batches, skips a bad stored value, and a repeat writes nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
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

#: Child tables first, so each DELETE meets no foreign key.
_TABLES = (
    "crm_activities", "crm_deal_contacts", "crm_deals", "crm_leads",
    "crm_contacts", "crm_companies", "crm_pipeline_stages", "crm_pipelines",
    "crm_settings", "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)

IMPORTED = datetime(2026, 10, 11, 19, 16, tzinfo=UTC)


def _dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def _admin(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="admin@crm-u14-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


async def _call(p: Any, org: str, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _admin(org)

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


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _native(p: Any, org: str) -> dict[str, str]:
    """Native mode seeds the stages. Returns stage name → id."""
    res = await _call(p, org, "PATCH", "/crm/settings", {"mode": "native"})
    assert res.status_code == 200, res.text
    with p.admin_engine.begin() as c:
        rows = c.execute(text(
            "SELECT name, id FROM crm_deal_statuses "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).fetchall()
    return {r.name: str(r.id) for r in rows}


def _deal(p: Any, org: str, name: str, status_id: str, **cols: Any) -> str:
    cols = {"status_changed_at": datetime.now(UTC), **cols}
    names = ", ".join(cols)
    marks = ", ".join(f":{c}" for c in cols)
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            f"INSERT INTO crm_deals (organization_id, name, status_id, {names}) "
            f"VALUES (CAST(:o AS uuid), :n, CAST(:s AS uuid), {marks}) RETURNING id"),
            {"o": org, "n": name, "s": status_id, **cols}).scalar())


def _mirrored(p: Any, org: str, name: str, status: str, ext_id: str,
              stage_time: str | None) -> str:
    custom = {} if stage_time is None else {"Stage_Modified_Time": stage_time}
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, "
            "status_changed_at, ext_source, ext_id, custom) VALUES "
            "(CAST(:o AS uuid), :n, CAST(:s AS uuid), :t, 'zoho', :e, "
            "CAST(:c AS jsonb)) RETURNING id"),
            {"o": org, "n": name, "s": status, "e": ext_id, "t": IMPORTED,
             "c": json.dumps(custom)}).scalar())


def _lane_names(payload: dict, lane: str) -> list[str]:
    return [r["name"] for x in payload["lanes"] if x["status"]["id"] == lane
            for r in x["rows"]]


def _totals(payload: dict) -> list[tuple]:
    return [(x["status"]["id"], x["count"], x["amount"], x["weighted"])
            for x in payload["lanes"]]


async def test_each_card_sort_orders_a_lane_and_keeps_the_totals(ns, app_engine) -> None:  # noqa: F811
    a = ns.org_a
    lane = (await _native(ns, a))["Proposal"]
    now = datetime.now(UTC)
    d = date.today()
    # moved: B newest.  changed: D has only updated_at (now), C newest ext.
    # close: A soonest, C none.  amount: C largest, D none.
    _deal(ns, a, "A", lane, status_changed_at=now - timedelta(days=3),
          ext_modified_at=now - timedelta(days=5),
          expected_close_date=d + timedelta(days=1), amount=100.0)
    _deal(ns, a, "B", lane, status_changed_at=now - timedelta(days=1),
          ext_modified_at=now - timedelta(days=9),
          expected_close_date=d + timedelta(days=9), amount=300.0)
    _deal(ns, a, "C", lane, status_changed_at=now - timedelta(days=2),
          ext_modified_at=now - timedelta(days=1), amount=900.0)
    _deal(ns, a, "D", lane, status_changed_at=now - timedelta(days=4),
          expected_close_date=d + timedelta(days=5))
    base = await _call(ns, a, "GET", "/crm/pipeline")
    assert base.status_code == 200, base.text
    expected = {
        "stage_age": ["B", "C", "A", "D"],
        "recently_changed": ["D", "C", "A", "B"],
        "close_date": ["A", "D", "B", "C"],
        "amount": ["C", "B", "A", "D"],
    }
    assert _lane_names(base.json(), lane) == expected["stage_age"]
    for sort, names in expected.items():
        res = await _call(ns, a, "GET", f"/crm/pipeline?card_sort={sort}")
        assert res.status_code == 200, (sort, res.text)
        assert _lane_names(res.json(), lane) == names, sort
        assert _totals(res.json()) == _totals(base.json()), sort
    refused = await _call(ns, a, "GET", "/crm/pipeline?card_sort=bogus")
    assert refused.status_code == 422, refused.text


async def test_restamp_copies_the_stage_time_for_mirrored_deals_only(ns, app_engine) -> None:  # noqa: F811
    from acb_common.db import tenant_session
    from gateway.routes.crm import core

    a, b = ns.org_a, ns.org_b
    lane = (await _native(ns, a))["Proposal"]
    await _native(ns, b)
    with ns.admin_engine.begin() as c:
        lane_b = str(c.execute(text(
            "SELECT id FROM crm_deal_statuses WHERE organization_id = CAST(:o AS uuid) "
            "LIMIT 1"), {"o": b}).scalar())
    ids = {
        "z1": _mirrored(ns, a, "Z1", lane, "z-1", "2026-10-26T15:10:59+05:30"),
        "z2": _mirrored(ns, a, "Z2", lane, "z-2", "not a time"),
        "z3": _mirrored(ns, a, "Z3", lane, "z-3", None),
        "native": _deal(ns, a, "Native", lane, status_changed_at=IMPORTED),
        "other": _mirrored(ns, b, "OtherOrg", lane_b, "z-9", "2026-10-26T15:10:59+05:30"),
    }

    def stamp(deal: str) -> datetime:
        with ns.admin_engine.begin() as c:
            return c.execute(text(
                "SELECT status_changed_at FROM crm_deals WHERE id = CAST(:i AS uuid)"),
                {"i": deal}).scalar()

    async def run(after: str | None = None) -> Any:
        async with tenant_engine_scope(_dsn(ns)), tenant_session(a) as db:
            return await core.restamp_deal_stage_from_source(
                db, "zoho", organization_id=a, after_id=after)

    first = await run()
    assert first.written == 1
    assert first.skipped_bad == 1  # "not a time"; the deal with no key is not read
    assert stamp(ids["z1"]) == datetime(2026, 10, 26, 9, 40, 59, tzinfo=UTC)
    for key in ("z2", "z3", "native", "other"):
        assert stamp(ids[key]) == IMPORTED, key
    assert (await run()).written == 0


@pytest.mark.parametrize("bad", [
    "2026-13-45T25:61:61+05:30", "2026-10-26T15:10:59", "",
])
async def test_restamp_skips_a_bad_stored_value_and_finishes(ns, app_engine, bad) -> None:  # noqa: F811
    from acb_common.db import tenant_session
    from gateway.routes.crm import core

    a = ns.org_a
    lane = (await _native(ns, a))["Proposal"]
    good1 = _mirrored(ns, a, "G1", lane, "g-1", "2026-10-26T15:10:59+05:30")
    broken = _mirrored(ns, a, "Bad", lane, "b-1", bad)
    good2 = _mirrored(ns, a, "G2", lane, "g-2", "2026-10-27T01:00:00Z")

    def stamp(deal: str) -> datetime:
        with ns.admin_engine.begin() as c:
            return c.execute(text(
                "SELECT status_changed_at FROM crm_deals WHERE id = CAST(:i AS uuid)"),
                {"i": deal}).scalar()

    async with tenant_engine_scope(_dsn(ns)), tenant_session(a) as db:
        res = await core.restamp_deal_stage_from_source(db, "zoho", organization_id=a)
    assert (res.written, res.scanned, res.skipped_bad) == (2, 3, 1)
    assert stamp(broken) == IMPORTED
    assert stamp(good1) == datetime(2026, 10, 26, 9, 40, 59, tzinfo=UTC)
    assert stamp(good2) == datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


async def test_restamp_leaves_a_dirty_deal_alone(ns, app_engine) -> None:  # noqa: F811
    from acb_common.db import tenant_session
    from gateway.routes.crm import core

    a = ns.org_a
    lane = (await _native(ns, a))["Proposal"]
    dirty = _mirrored(ns, a, "Dirty", lane, "d-1", "2026-10-26T15:10:59+05:30")
    newer = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_deals SET zoho_dirty = true, status_changed_at = :t "
            "WHERE id = CAST(:i AS uuid)"), {"t": newer, "i": dirty})
    async with tenant_engine_scope(_dsn(ns)), tenant_session(a) as db:
        res = await core.restamp_deal_stage_from_source(db, "zoho", organization_id=a)
    assert res.written == 0
    with ns.admin_engine.begin() as c:
        got = c.execute(text(
            "SELECT status_changed_at FROM crm_deals WHERE id = CAST(:i AS uuid)"),
            {"i": dirty}).scalar()
    assert got == newer


async def test_restamp_is_bounded_by_the_batch(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    from acb_common.db import tenant_session
    from gateway.routes.crm import core

    a = ns.org_a
    lane = (await _native(ns, a))["Proposal"]
    for i in range(5):
        _mirrored(ns, a, f"Z{i}", lane, f"z-{i}", "2026-10-26T15:10:59+05:30")
    monkeypatch.setattr(core, "STAGE_RESTAMP_BATCH", 2)
    seen = []
    after = None
    for _ in range(3):
        async with tenant_engine_scope(_dsn(ns)), tenant_session(a) as db:
            res = await core.restamp_deal_stage_from_source(
                db, "zoho", organization_id=a, after_id=after)
        seen.append((res.written, res.scanned))
        after = res.last_id
    assert seen == [(2, 2), (2, 2), (1, 1)]
