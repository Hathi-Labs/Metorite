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
* **§8.4 item 5.** Four writes of a member's own view that emit no event (the
  overlay PATCH, mark-read, a nudge and an assignee removal) each move a
  version after the commit, and emit no new event.
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
OTHER = f"cache-other-{TAG}@md5a.test"


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
        for email in (ME, OTHER):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
                {"e": email, "o": org})
        c.execute(text(
            "INSERT INTO user_settings (user_id, timezone, day_start_hour, day_end_hour, "
            "daily_capacity_mins, organization_id) VALUES "
            "(:e, :z, 1, 23, 360, CAST(:o AS uuid))"), {"e": ME, "z": zone, "o": org})
        project, lane = _project(c, org=org, name=f"Cache {TAG}", owner=None, grant_org=True)
        task = _task(c, org=org, project=project, lane=lane, title="Ship the bracket",
                     due=NOW - timedelta(days=1), assignee=ME, number=1)
    return {"org": org, "task": task, "project": project, "lane": lane}


def _member(org: str, email: str = ME) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
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
        assert redis.ops("set") == [version]
        assert redis.ops("incr") == []
        stamp, token = redis.store[version][0].split("-")
        assert int(stamp) > 0 and len(token) == 8


_NUMBER = iter(range(100, 10_000))


def _seed_task(p, seeded, *assignees: str, waiting: bool = False) -> str:
    """A fresh task of the seeded project, held by ``assignees``. With
    ``waiting``, ME's overlay says WAITING, so ME may nudge the others."""
    org = seeded["org"]
    with p.admin_engine.begin() as c:
        task = _task(c, org=org, project=seeded["project"], lane=seeded["lane"],
                     title=f"Own write {TAG}", due=NOW + timedelta(days=3), assignee=None,
                     number=next(_NUMBER))
        for who in assignees:
            c.execute(text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
                "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
                {"t": task, "a": who, "by": ME, "o": org})
        if waiting:
            c.execute(text(
                "INSERT INTO pm_task_personal (task_id, member_email, disposition, "
                "organization_id) VALUES (CAST(:t AS uuid), :e, 'WAITING', CAST(:o AS uuid))"),
                {"t": task, "e": ME, "o": org})
    return task


class TestOwnWritesMoveAVersion:
    """§8.4 item 5: four writes of a member's own view emit no ``pm.*`` event.
    Each moves a version after its commit, through the real route, on a real
    database. None of them emits a new event, so workflow triggers do not
    change."""

    @pytest.fixture(autouse=True)
    def _no_event(self, monkeypatch):
        from ingestion import event_hooks

        seen: list[tuple[str, str]] = []

        async def record(source, event_type, payload):
            seen.append((source, event_type))

        monkeypatch.setattr(event_hooks, "_SINKS", [record])
        self.events = seen

    async def test_the_overlay_patch_moves_the_member(self, promoted, seeded, redis, switch):  # noqa: F811
        from gateway.routes.projects import personal

        org = seeded["org"]
        task = _seed_task(promoted, seeded, ME)
        async with _as_member(promoted, org):
            await personal.set_personal(task, personal.PersonalIn(disposition="SOMEDAY"),
                                        user=_member(org))
        assert redis.ops("incr") == [f"cc:{org}:my_day:ver:{ME}"]
        assert self.events == []

    async def test_mark_read_moves_the_member(self, promoted, seeded, redis, switch):  # noqa: F811
        from gateway.routes.projects import notifications

        org = seeded["org"]
        task = _seed_task(promoted, seeded, ME)
        with promoted.admin_engine.begin() as c:
            note = str(c.execute(text(
                "INSERT INTO pm_notifications (recipient, kind, task_id, actor, "
                "organization_id) VALUES (:r, 'assigned', CAST(:t AS uuid), :a, "
                "CAST(:o AS uuid)) RETURNING id"),
                {"r": ME, "t": task, "a": OTHER, "o": org}).scalar_one())
        async with _as_member(promoted, org):
            out = await notifications.mark_read(notifications.ReadIn(ids=[note]),
                                                user=_member(org))
            assert out == {"marked": 1}
            assert redis.ops("incr") == [f"cc:{org}:my_day:ver:{ME}"]
            # Nothing left to mark: no write, so no bump.
            again = await notifications.mark_read(notifications.ReadIn(ids=[note]),
                                                  user=_member(org))
        assert again == {"marked": 0}
        assert len(redis.ops("incr")) == 1
        assert self.events == []

    async def test_a_nudge_moves_the_person_it_reaches(self, promoted, seeded, redis, switch):  # noqa: F811
        from gateway.routes.projects import personal

        org = seeded["org"]
        task = _seed_task(promoted, seeded, ME, OTHER, waiting=True)
        async with _as_member(promoted, org):
            out = await personal.nudge_task(task, user=_member(org))
        assert out["notified"], out
        assert redis.ops("incr") == [f"cc:{org}:my_day:ver:{OTHER}"]
        assert self.events == []

    async def test_an_assignee_removal_moves_the_org(self, promoted, seeded, redis, switch):  # noqa: F811
        from gateway.routes.projects import tasks

        org = seeded["org"]
        task = _seed_task(promoted, seeded, ME, OTHER)
        async with _as_member(promoted, org):
            await tasks.set_assignees(task, tasks.AssigneesIn(assignees=[ME]),
                                      user=_member(org))
        assert redis.ops("set") == [f"cc:{org}:my_day:ver"]
        assert self.events == [], "a removal must not emit a new event"

    async def test_with_the_switch_off_no_write_reaches_redis(
            self, promoted, seeded, redis, switch):  # noqa: F811
        from gateway.routes.projects import personal, tasks

        switch(False)
        org = seeded["org"]
        task = _seed_task(promoted, seeded, ME, OTHER)
        async with _as_member(promoted, org):
            await personal.set_personal(task, personal.PersonalIn(disposition="SOMEDAY"),
                                        user=_member(org))
            await tasks.set_assignees(task, tasks.AssigneesIn(assignees=[ME]),
                                      user=_member(org))
        assert redis.calls == []
