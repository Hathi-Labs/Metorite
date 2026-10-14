"""WS-58 MD-5a, R8: the read cache of ``GET /shell/feed`` on a REAL Postgres
(``my_day_feed.md`` MD-5a, done-when 2 and 4).

A fake agrees with any SQL, and "a hit runs no SQL" is a claim about the
SQL. So this suite counts the statements that the shared engine sends to a
real Postgres, the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``, as the NON-privileged role with RLS
forced (``_as_member``). The listener hooks the engine, so a read that skips
a helper is counted too.

* **Done-when 2.** The real route, through a real FastAPI app: a miss sends
  SQL, a hit sends NONE, and a hit with the tag answers 304 with none. The
  identity resolve of each request is apart from the count: the test app
  hands the route its member with no lookup, as the CRM-X3 suite does.
* **Done-when 4.** One real task capture (``POST /projects/my/tasks``), which
  emits ``pm.task.created``, sends the same statements with the change sink
  on and off. With it on, the org version moves by one.

Redis is a fake under the real ``TenantRedis``: CI has no Redis.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis
from acb_common.settings import get_settings
from acb_common.tenant_redis import TenantRedis
from gateway.routes.shell import cache, feed
from gateway.routes.shell import needs as shell
from sqlalchemy import event, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures by name)
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_my_day_cache import _FakeRedis
from tests.unit.test_shell_feed import _a_daytime_zone
from tests.unit.test_shell_needs_r8 import NOW, _Frozen, _project, _task

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"cache-me-{TAG}@md5a.test"


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The feed's one clock and the lens read's "today" read ``NOW``."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)


@pytest.fixture
def redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def switch(monkeypatch):
    """Set ``MY_DAY_CACHE`` through its one reader's own source."""
    def _set(value: bool) -> None:
        monkeypatch.setattr(get_settings(), "my_day_cache", value)
    _set(True)
    return _set


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """One member of org B with an overdue task and a block later today, so
    the answer holds a card and each source the member holds reads ``ok``."""
    p = promoted
    org = p.org_b
    zone = _a_daytime_zone(NOW)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"), {"e": ME, "o": org})
        c.execute(text(
            "INSERT INTO user_settings (user_id, timezone, day_start_hour, day_end_hour, "
            "daily_capacity_mins, organization_id) VALUES "
            "(:e, :z, 1, 23, 360, CAST(:o AS uuid))"), {"e": ME, "z": zone, "o": org})
        project, lane = _project(c, org=org, name=f"Cache {TAG}", owner=None, grant_org=True)
        task = _task(c, org=org, project=project, lane=lane, title="Ship the bracket",
                     due=NOW - timedelta(days=1), assignee=ME, number=1)
    return {"org": org, "task": task}


def _member(org: str) -> UserContext:
    return UserContext(email=ME, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(
                           {"feature:projects", "feature:tasks"})))


def _widen(monkeypatch) -> None:
    """Room for a cold database. ``sources`` proves each provider ran."""
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    from gateway.routes.shell import state

    monkeypatch.setattr(state, "STATE_TIMEOUT_S", 30.0)


async def _get(p, user: UserContext, *, tag: str | None = None,
               statements: list[str]) -> Any:
    """One ``GET /shell/feed`` through a real FastAPI app. Each statement the
    shared engine sends during the request lands in ``statements``."""
    import httpx
    from acb_auth import get_current_user
    from acb_common import db as shared
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes.shell import router

    async def _caller() -> UserContext:
        bind_tenant(user.organization_id)
        return user

    def _record(_conn, _cur, statement, _params, _ctx, _many) -> None:
        statements.append(statement)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://shell.test") as client,
        ):
            engine = shared.get_engine().sync_engine
            event.listen(engine, "before_cursor_execute", _record)
            try:
                headers = {"If-None-Match": tag} if tag else {}
                return await client.get("/shell/feed?limit=40", headers=headers)
            finally:
                event.remove(engine, "before_cursor_execute", _record)
    finally:
        release_tenant(scope)


class TestAHitSendsNoSql:
    """Done-when 2."""

    async def test_a_miss_sends_sql_and_a_hit_sends_none(
            self, promoted, seeded, redis, switch, monkeypatch):  # noqa: F811
        _widen(monkeypatch)
        user = _member(seeded["org"])

        miss: list[str] = []
        first = await _get(promoted, user, statements=miss)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["sources"] == {"tasks": "ok", "projects": "ok", "email": "absent",
                                   "approvals": "absent", "calendar": "ok"}
        assert f"tasks:{seeded['task']}" in [c["signal_id"] for c in body["cards"]]
        assert len(miss) > 0, "the miss sent no SQL: the count is blind"
        tag = first.headers["etag"]
        assert tag == cache.etag(cache.body_json(body))

        hit: list[str] = []
        second = await _get(promoted, user, statements=hit)
        assert second.status_code == 200
        assert hit == [], hit
        assert second.json() == body
        assert second.headers["etag"] == tag

        same: list[str] = []
        third = await _get(promoted, user, tag=tag, statements=same)
        assert third.status_code == 304
        assert third.content == b""
        assert third.headers["etag"] == tag
        assert same == []

    async def test_with_the_switch_off_every_read_sends_sql(
            self, promoted, seeded, redis, switch, monkeypatch):  # noqa: F811
        # Non-vacuity: the same two reads with the cache off both reach the
        # database, and Redis hears nothing.
        _widen(monkeypatch)
        switch(False)
        user = _member(seeded["org"])
        for _ in range(2):
            sent: list[str] = []
            answer = await _get(promoted, user, statements=sent)
            assert answer.status_code == 200
            assert len(sent) > 0
            assert "etag" not in answer.headers
        assert redis.calls == []


class TestTheSinkSendsNoSql:
    """Done-when 4."""

    async def _capture(self, p, org: str, title: str) -> list[str]:
        from acb_common import db as shared
        from gateway.routes.projects import personal

        sent: list[str] = []

        def _record(_conn, _cur, statement, _params, _ctx, _many) -> None:
            sent.append(statement)

        async with _as_member(p, org):
            engine = shared.get_engine().sync_engine
            event.listen(engine, "before_cursor_execute", _record)
            try:
                await personal.capture(personal.CaptureIn(title=title), user=_member(org))
            finally:
                event.remove(engine, "before_cursor_execute", _record)
        return sent

    async def test_one_edit_sends_the_same_sql_with_the_sink_on_and_off(
            self, promoted, seeded, redis, switch, monkeypatch):  # noqa: F811
        from ingestion import event_hooks

        monkeypatch.setattr(event_hooks, "_SINKS", [cache.on_event])
        org = seeded["org"]
        # Warm up: the first capture makes the personal project.
        switch(False)
        await self._capture(promoted, org, f"Warm up {TAG}")

        off = await self._capture(promoted, org, f"Sink off {TAG}")
        assert redis.calls == []
        switch(True)
        on = await self._capture(promoted, org, f"Sink on {TAG}")

        assert len(off) > 0
        assert len(on) == len(off), (len(on), len(off))
        version = f"cc:{org}:my_day:ver"
        assert redis.ops("incr") == [version]
        assert redis.store[version][0] == "1"
