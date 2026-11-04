"""WS-BRIEF BR-2d — the delivery monitor on a real database (R8).

Spec: ``project-docs/specs/core_brief.md`` §7 and §10 row 6. Code:
``gateway/brief/monitor.py`` and ``gateway/routes/admin_brief_health.py``.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_brief_monitor_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green.

**Acceptance.** Over 7 days, a run counts when its slot is 5 minutes old. A
delivered run in 5 minutes is on time. A late, ``ready`` or really failed run
is a miss. ``empty``, ``slot_missed``, ``opted_out``, ``member_*`` runs and a
run of another org stay out. The hourly seed makes one job for each org and
hour. The check logs ``brief.slo_missed`` once when the share is under 99%.

**Mutations (R7).**

* M1. ``monitor._STATS_SQL``: drop the ``organization_id`` predicate. The
  test stays green, because FORCE RLS binds the statement. The predicate is
  the second belt, as in ``store.py``.
* M2. ``monitor.NOT_OWED_ERRORS``: empty it.
  ``test_r8_the_stats_count_the_right_runs`` goes red.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException
from gateway.brief import flags, monitor, store
from gateway.routes import admin_brief_health
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@br2d.test"
LIKE = f"%.{TAG}@br2d.test"
NOW = datetime(2099, 3, 10, 12, 0, tzinfo=UTC)
BASE = date(2099, 3, 1)


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _on(*orgs: str) -> Any:
    return SimpleNamespace(brief_enabled=True, brief_orgs=",".join(orgs), brief_in_app=True)


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    p = promoted
    monkeypatch.setattr(flags, "get_settings", lambda: _on(p.org_a))
    monkeypatch.setattr(admin_brief_health, "_now", lambda: NOW)
    yield p
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM brief_runs WHERE member_email LIKE :t"), {"t": LIKE})
        c.execute(text("DELETE FROM jobs WHERE kind = 'brief.slo'"))


async def _seed(org: str, day: int, *, state_: str, slot_ago: timedelta,
                lag: timedelta | None = None, error: str | None = None) -> None:
    """One run of ANA on day *day*, with a slot *slot_ago* before NOW. It is
    moved through the store's own guarded moves, then its stamp is set."""
    slot = NOW - slot_ago
    async with tenant_session(org) as db:
        run = (await store.create_run(db, ANA, kind="brief", local_date=BASE + timedelta(days=day),
                                      slot_at=slot)).run
        if state_ in ("empty", "failed"):
            await store.set_state(db, ANA, run.id, state_, now=slot, from_state="planned",
                                  error=error)
            return
        if state_ == "planned":
            return
        await store.set_state(db, ANA, run.id, "gathered", now=slot, from_state="planned")
        await store.set_state(db, ANA, run.id, "ready", now=slot, from_state="gathered")
        if state_ == "delivered":
            assert lag is not None
            await store.set_state(db, ANA, run.id, "delivered", now=slot + lag,
                                  from_state="ready")


async def _fill(p: Any) -> None:
    h = timedelta(hours=1)
    await _seed(p.org_a, 0, state_="delivered", slot_ago=h, lag=timedelta(seconds=60))  # on time
    await _seed(p.org_a, 1, state_="delivered", slot_ago=h, lag=timedelta(seconds=400))  # late
    await _seed(p.org_a, 2, state_="ready", slot_ago=h)  # still not delivered
    await _seed(p.org_a, 3, state_="failed", slot_ago=h, error="boom")  # a miss
    await _seed(p.org_a, 4, state_="failed", slot_ago=h, error="slot_missed")  # not owed
    await _seed(p.org_a, 5, state_="failed", slot_ago=h, error="member_departed")  # not owed
    await _seed(p.org_a, 6, state_="failed", slot_ago=h, error="opted_out")  # not owed
    await _seed(p.org_a, 7, state_="empty", slot_ago=h)  # not owed
    await _seed(p.org_a, 8, state_="ready", slot_ago=timedelta(minutes=2))  # not due yet
    await _seed(p.org_a, 9, state_="delivered", slot_ago=timedelta(days=8),
                lag=timedelta(seconds=900))  # older than 7 days
    await _seed(p.org_b, 10, state_="delivered", slot_ago=h, lag=timedelta(seconds=30))


async def test_r8_the_stats_count_the_right_runs(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _fill(p)
        async with tenant_session(p.org_a) as db:
            s = await monitor.stats(db, NOW)
    assert (s.counted, s.on_time) == (4, 1)
    assert s.share == 0.25 and s.met is False
    assert s.p50_s == 230.0 and s.max_s == 400.0


async def test_r8_the_stats_count_the_bound_org_only(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _fill(p)
        async with tenant_session(p.org_b) as db:
            s = await monitor.stats(db, NOW)
    assert (s.counted, s.on_time) == (1, 1)
    assert s.met is True


async def test_r8_an_org_with_no_run_meets_the_target(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)), tenant_session(p.org_a) as db:
        s = await monitor.stats(db, NOW)
    assert (s.counted, s.share, s.met, s.p95_s) == (0, None, True, None)


async def test_r8_the_check_logs_one_error_under_the_target(world, monkeypatch) -> None:
    p = world
    logged: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(monitor, "_log", SimpleNamespace(
        error=lambda event, **kw: logged.append((event, kw))))
    monkeypatch.setattr(monitor, "datetime", SimpleNamespace(now=lambda tz=None: NOW))
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _fill(p)
        async with tenant_session(p.org_a) as db:
            await monitor.check(db, {})
        async with tenant_session(p.org_b) as db:
            await monitor.check(db, {})
    assert [e for e, _ in logged] == ["brief.slo_missed"]
    assert logged[0][1]["counted"] == 4 and logged[0][1]["on_time"] == 1


async def test_r8_the_seed_makes_one_job_for_each_org_and_hour(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await monitor.seed(p.org_a, NOW)
        await monitor.seed(p.org_a, NOW + timedelta(minutes=20))
        await monitor.seed(p.org_a, NOW + timedelta(hours=1))
        await monitor.seed(p.org_b, NOW)  # not on the list
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, dedupe_key FROM jobs WHERE kind = 'brief.slo' "
            "ORDER BY dedupe_key")).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        (p.org_a, "slo:2099-03-10T12"), (p.org_a, "slo:2099-03-10T13")]


def _admin(org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=ANA, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(list(perms), roles=["admin"]))


async def test_r8_the_health_route_answers_the_bound_org(world) -> None:
    p = world
    async with tenant_engine_scope(_dsn(p.app_url)):
        await _fill(p)
        token = bind_tenant(p.org_a)
        try:
            body = await admin_brief_health.brief_health(user=_admin(p.org_a))
        finally:
            release_tenant(token)
        token = bind_tenant(p.org_b)
        try:
            with pytest.raises(HTTPException) as off:  # org B is not on the list
                await admin_brief_health.brief_health(user=_admin(p.org_b))
        finally:
            release_tenant(token)
    assert off.value.status_code == 404
    assert body["counted"] == 4 and body["on_time"] == 1 and body["met"] is False
    assert body["lag_seconds"]["max"] == 400.0 and body["target"] == 0.99
