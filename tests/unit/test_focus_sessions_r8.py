"""WS-MDC CC-5a — the focus-session log and its routes, on a real database (R8).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.3 and §10 · D188
(D-MD-5) · Metorite task 1972.

The routes of ``gateway/routes/projects/focus_sessions.py`` run through the
REAL ``/projects`` router, on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. ``test_focus_session_rls_r8.py``
holds the tenant and the member walls. This file holds the behaviour.

**The ladder.** The ``promoted`` fixture of the rehearsal calls
``tests.unit._tenant_ladder.apply_ladder`` on a NEW private database, then the
four generated phases. So no test here reads "whatever the ladder left" in a
shared database, and a new pytest-split cut cannot strand it.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL="postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant"
    uv run pytest tests/unit/test_focus_sessions_r8.py -v -rs

**The mutations (R7).** Each one turned the named test red, on a committed
tree, and ``git checkout`` restored it. ``F`` is
``apps/services/gateway/gateway/routes/projects/focus_sessions.py``.

* M1. ``F`` ``_start_in``: no ``_REPLACE_OPEN_SQL`` (and no stamp of it).
  ``test_a_start_ends_the_open_session_as_replaced_and_stamps_its_task``.
* M2. ``F`` ``START_ATTEMPTS = 1``, so a start that meets the index never
  tries again. ``test_a_start_that_meets_an_uncommitted_open_row_tries_once_more``
  (409 in place of 201).
* M3. ``F`` ``_FOCUSED_SECONDS``: drop ``- paused_seconds``.
  ``test_pauses_do_not_count_in_minutes``.
* M4. ``F`` ``_EXTEND_SQL``: drop the ``+ interval``.
  ``test_extend_moves_the_planned_end_by_ten_minutes``.
* M5. ``F`` ``update_focus_session``: drop ``_stamp_end`` of an end.
  ``test_an_end_stamps_actual_end_with_the_answer``.
* M6. ``F`` ``AUTO_CLOSE_SQL``: drop ``AND ended_at IS NULL``.
  ``test_a_stale_session_closes_once_under_two_concurrent_reads`` (the second
  reader closes it a second time).
* M7. ``F`` ``start_focus_session``: drop ``load_visible_task``.
  ``test_a_task_the_member_cannot_see_answers_404_and_writes_nothing``.
* M8. ``F`` ``start_focus_session``: drop ``dependencies=_ON``.
  ``test_the_flag_off_answers_404_on_every_route_and_writes_nothing``.
* M9. ``F`` ``list_focus_sessions``: ``zone = "UTC"`` in place of
  ``stored_zone``. ``test_the_day_is_the_members_local_day``.
* M10. ``F`` ``_RESUME_SQL``: keep ``planned_end`` as it was.
  ``test_pauses_do_not_count_in_minutes`` (the planned end does not move).
* M11. ``F`` ``_start_in``: stamp ``actual_start`` only, so ``actual_end``
  keeps its old value. ``test_a_start_makes_an_open_row_and_stamps_actual_start``.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

import httpx
from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import FastAPI, Request
from gateway.routes.projects import focus_sessions as fs
from gateway.routes.projects import personal
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]

#: Migration 350, found by CONTENT, never by number (R1).
MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS focus_sessions (" in p.read_text(encoding="utf-8"))

ALICE = "alice@focus-r8.test"
BOB = "bob@focus-r8.test"
#: A member of org A, for the tenant wall of the RLS suite.
CAROL = "carol@focus-r8.test"
BASE = "/projects/my/focus-sessions"
MEMBERS = (ALICE, BOB, CAROL)


# ── The world ──────────────────────────────────────────────────────────────


def member(email: str, org: str) -> UserContext:
    """A member of *org* who holds the Projects feature and not
    ``data:org:read``, so the task visibility rule binds."""
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access(["feature:projects"]),
    )


def _app(people: dict[str, UserContext]) -> FastAPI:
    """The real Projects router, signed in as whoever ``x-user-email`` names."""
    from acb_auth import get_current_user
    from gateway.routes.projects import router

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Alice and Bob in org B, Carol in org A, the flag on for both orgs."""
    org_a, org_b = promoted.org_a, promoted.org_b
    with promoted.admin_engine.begin() as c:
        for email, org in ((ALICE, org_b), (BOB, org_b), (CAROL, org_a)):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )

    async def quiet(*_a: Any, **_k: Any) -> None:
        return None

    # A capture emits `pm.task.created` and reads the title for links. Neither
    # is under test here.
    monkeypatch.setattr(personal, "emit", quiet)
    monkeypatch.setattr(personal, "link_from_text", quiet)
    monkeypatch.setattr(get_settings(), "focus_log_orgs", f"{org_a},{org_b}")
    people = {
        ALICE: member(ALICE, org_b), BOB: member(BOB, org_b), CAROL: member(CAROL, org_a),
    }
    yield SimpleNamespace(p=promoted, org=org_b, other=org_a, people=people)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM focus_sessions WHERE member_email = ANY(:e)"),
                  {"e": list(MEMBERS)})
        c.execute(text("DELETE FROM user_settings WHERE lower(user_id) = ANY(:e)"),
                  {"e": list(MEMBERS)})
        c.execute(text("DELETE FROM pm_tasks WHERE created_by = ANY(:e)"),
                  {"e": list(MEMBERS)})
        c.execute(text("DELETE FROM pm_projects WHERE lower(personal_owner) = ANY(:e)"),
                  {"e": list(MEMBERS)})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": list(MEMBERS)})


@asynccontextmanager
async def client(world: Any, org: str) -> AsyncIterator[httpx.AsyncClient]:
    """Bind ``org``, point the shared engine at the app role, and talk to the
    router in-process. ``tenant_session(org)`` works inside the block too."""
    token = bind_tenant(org)
    try:
        url = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            transport = httpx.ASGITransport(app=_app(world.people))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://gateway.test",
            ) as c:
                yield c
    finally:
        release_tenant(token)


def as_(email: str) -> dict[str, str]:
    return {"x-user-email": email}


async def capture(c: httpx.AsyncClient, email: str, title: str, **extra: Any) -> str:
    """A personal task of *email*, through the real capture route."""
    resp = await c.post("/projects/my/tasks", json={"title": title, **extra}, headers=as_(email))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def start(
    c: httpx.AsyncClient, email: str, task_id: str, mode: str = "25/5",
) -> dict[str, Any]:
    resp = await c.post(BASE, json={"task_id": task_id, "mode": mode}, headers=as_(email))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def patch(c: httpx.AsyncClient, email: str, sid: str, **body: Any) -> httpx.Response:
    return await c.patch(f"{BASE}/{sid}", json=body, headers=as_(email))


async def day_of(c: httpx.AsyncClient, email: str, day: str | None = None) -> dict[str, Any]:
    resp = await c.get(BASE, params={"day": day} if day else None, headers=as_(email))
    assert resp.status_code == 200, resp.text
    return resp.json()


def admin(world: Any, sql: str, **binds: Any) -> None:
    """A write as the superuser, past RLS: the test moves the clock of a row."""
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), binds)


def row(world: Any, sid: str) -> Any:
    """The row as the superuser sees it, past RLS and past the member."""
    with world.p.admin_engine.connect() as c:
        return c.execute(
            text("SELECT * FROM focus_sessions WHERE id = CAST(:i AS uuid)"), {"i": sid},
        ).fetchone()


def open_rows(world: Any, email: str, org: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return c.execute(
            text(
                "SELECT * FROM focus_sessions WHERE member_email = :e "
                "AND organization_id = CAST(:o AS uuid) AND ended_at IS NULL"
            ),
            {"e": email, "o": org},
        ).fetchall()


def count(world: Any, email: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(
            text("SELECT count(*) FROM focus_sessions WHERE member_email = :e"), {"e": email},
        ).scalar() or 0)


def overlay(world: Any, task_id: str, email: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(
            text(
                "SELECT actual_start, actual_end FROM pm_task_personal "
                "WHERE task_id = CAST(:t AS uuid) AND lower(member_email) = :e"
            ),
            {"t": task_id, "e": email},
        ).fetchone()


def instant(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


# ── Hermetic ───────────────────────────────────────────────────────────────


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+tests/unit/test_focus_sessions_r8\.py \\?$", workflow, re.M), name


def test_the_auto_close_is_one_update_with_a_literal_interval() -> None:
    """§9.3: ONE statement, never a read and then a write. The stale test
    reads the open state in the same WHERE, so a second reader updates no
    row. An interval is a literal, never a bound text (``CAST(:x AS
    interval)``)."""
    sql = " ".join(fs.AUTO_CLOSE_SQL.split())
    assert sql.startswith("UPDATE focus_sessions SET ended_at = planned_end,")
    assert "outcome = 'auto_closed'" in sql
    assert "AND ended_at IS NULL" in sql
    assert "planned_end < now() - interval '15 minutes'" in sql
    assert "member_email = :me" in sql
    assert "SELECT" not in sql
    source = Path(fs.__file__).read_text(encoding="utf-8")
    assert not re.search(r"AS\s+interval", source, re.I)


def test_open_session_taken_reads_only_the_open_index() -> None:
    """A retry runs only for the race of two starts, never for another
    integrity error (a deleted task, a CHECK)."""

    class Raw(Exception):
        def __init__(self, sqlstate: str, constraint_name: str) -> None:
            super().__init__(f"{sqlstate} {constraint_name}")
            self.sqlstate = sqlstate
            self.constraint_name = constraint_name

    class Wrapped(Exception):
        def __init__(self, orig: BaseException) -> None:
            super().__init__(str(orig))
            self.orig = orig

    assert fs.open_session_taken(Wrapped(Raw("23505", fs.OPEN_INDEX)))
    assert not fs.open_session_taken(Wrapped(Raw("23505", "focus_sessions_pkey")))
    assert not fs.open_session_taken(Wrapped(Raw("23503", "focus_sessions_task_id_fkey")))


def test_the_migration_has_the_shape_of_section_9_3() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert MIGRATION.name == "350_focus_sessions.sql"
    flat = " ".join(sql.split())
    assert ("organization_id UUID NOT NULL REFERENCES organization (id) ON DELETE CASCADE "
            "DEFAULT current_setting('app.tenant_id', true)::uuid") in flat
    assert "task_id UUID NOT NULL REFERENCES pm_tasks (id) ON DELETE CASCADE" in flat
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS focus_sessions_one_open ON focus_sessions "
            "(organization_id, member_email) WHERE ended_at IS NULL") in flat
    assert ("CREATE INDEX IF NOT EXISTS idx_focus_sessions_member_started ON focus_sessions "
            "(organization_id, member_email, started_at)") in flat
    assert "mode IN ('25/5', '50/10', 'flow')" in flat
    for outcome in fs.OUTCOMES:
        assert f"'{outcome}'" in flat
    assert "ALTER TABLE focus_sessions FORCE ROW LEVEL SECURITY" in flat
    assert "CREATE POLICY focus_sessions_tenant_isolation ON focus_sessions" in flat


# ── R8 ─────────────────────────────────────────────────────────────────────


async def test_a_start_makes_an_open_row_and_stamps_actual_start(world) -> None:
    """A 25/5 start: one open row, the planned end 25 minutes on, and the
    overlay pair restarted (``actual_end`` cleared, as the room does). M11."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Write the brief")
        admin(world, (
            "INSERT INTO pm_task_personal (task_id, member_email, actual_start, actual_end, "
            "organization_id) VALUES (CAST(:t AS uuid), :e, now() - interval '2 days', "
            "now() - interval '2 days' + interval '30 minutes', CAST(:o AS uuid)) "
            "ON CONFLICT (task_id, member_email) DO UPDATE SET actual_start = EXCLUDED.actual_start, "
            "actual_end = EXCLUDED.actual_end"), t=task, e=ALICE, o=world.org)
        s = await start(c, ALICE, task)
    assert s["outcome"] is None and s["ended_at"] is None and s["mode"] == "25/5"
    assert instant(s["planned_end"]) - instant(s["started_at"]) == timedelta(minutes=25)
    stored = row(world, s["id"])
    assert stored.member_email == ALICE and str(stored.organization_id) == world.org
    pair = overlay(world, task, ALICE)
    assert pair.actual_start == instant(s["started_at"])
    assert pair.actual_end is None


async def test_a_start_ends_the_open_session_as_replaced_and_stamps_its_task(world) -> None:
    """M1. The old session ends as ``replaced``, and its own task gets the end."""
    async with client(world, world.org) as c:
        first_task = await capture(c, ALICE, "First")
        second_task = await capture(c, ALICE, "Second")
        first = await start(c, ALICE, first_task)
        second = await start(c, ALICE, second_task, mode="50/10")
    old = row(world, first["id"])
    assert old.outcome == "replaced" and old.ended_at is not None
    assert overlay(world, first_task, ALICE).actual_end == old.ended_at
    assert overlay(world, second_task, ALICE).actual_start == instant(second["started_at"])
    assert [str(r.id) for r in open_rows(world, ALICE, world.org)] == [second["id"]]


async def test_a_start_that_meets_an_uncommitted_open_row_tries_once_more(world) -> None:
    """The race of §9.3, made exact. A second transaction holds a new open
    row of Alice and has not committed. A start waits on the open index, then
    meets it, and tries once more: it replaces that row and inserts its own.
    One open row stays. M2."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Race")
        rival_cm = tenant_session(world.org)
        rival = await rival_cm.__aenter__()
        committed = False
        try:
            await rival.execute(
                text(
                    "INSERT INTO focus_sessions (member_email, task_id, started_at, "
                    "planned_end, mode) VALUES (:e, CAST(:t AS uuid), now(), "
                    "now() + interval '25 minutes', '25/5')"
                ),
                {"e": ALICE, "t": task},
            )
            post = asyncio.create_task(
                c.post(BASE, json={"task_id": task, "mode": "25/5"}, headers=as_(ALICE)))
            await asyncio.sleep(1.0)
            assert not post.done(), "the start did not wait on the open index"
            await rival_cm.__aexit__(None, None, None)
            committed = True
            resp = await post
        finally:
            if not committed:
                await rival_cm.__aexit__(None, None, None)
    assert resp.status_code == 201, resp.text
    mine = open_rows(world, ALICE, world.org)
    assert [str(r.id) for r in mine] == [resp.json()["id"]]
    assert count(world, ALICE) == 2
    with world.p.admin_engine.connect() as conn:
        outcomes = sorted(r.outcome or "open" for r in conn.execute(text(
            "SELECT outcome FROM focus_sessions WHERE member_email = :e"), {"e": ALICE}))
    assert outcomes == ["open", "replaced"]


async def test_two_concurrent_starts_leave_one_open_row(world) -> None:
    async with client(world, world.org) as c:
        one = await capture(c, ALICE, "One")
        two = await capture(c, ALICE, "Two")
        a, b = await asyncio.gather(
            c.post(BASE, json={"task_id": one, "mode": "25/5"}, headers=as_(ALICE)),
            c.post(BASE, json={"task_id": two, "mode": "flow"}, headers=as_(ALICE)),
        )
    assert {a.status_code, b.status_code} == {201}, (a.text, b.text)
    assert len(open_rows(world, ALICE, world.org)) == 1
    assert count(world, ALICE) == 2


async def test_pauses_do_not_count_in_minutes(world) -> None:
    """30 minutes on the clock, 5 minutes paused: 25 minutes. A resume moves
    the planned end by the pause. An end during a second pause of 2 minutes
    counts it too: 23 minutes. M3, M10."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Deep work")
        s = await start(c, ALICE, task)
        admin(world, (
            "UPDATE focus_sessions SET started_at = now() - interval '30 minutes', "
            "planned_end = now() + interval '10 minutes' WHERE id = CAST(:i AS uuid)"), i=s["id"])
        paused = await patch(c, ALICE, s["id"], action="pause")
        assert paused.status_code == 200 and paused.json()["paused"] is True
        again = await patch(c, ALICE, s["id"], action="pause")
        assert again.json()["paused"] is True
        admin(world, "UPDATE focus_sessions SET paused_at = now() - interval '5 minutes' "
                     "WHERE id = CAST(:i AS uuid)", i=s["id"])
        before = row(world, s["id"]).planned_end
        resumed = (await patch(c, ALICE, s["id"], action="resume")).json()
        assert resumed["paused"] is False
        assert 300 <= resumed["paused_seconds"] <= 302
        moved = instant(resumed["planned_end"]) - before
        assert timedelta(seconds=300) <= moved <= timedelta(seconds=302)
        listed = await day_of(c, ALICE)
        [item] = listed["sessions"]
        assert item["minutes"] == 25
        assert listed["total_minutes"] == 25
        await patch(c, ALICE, s["id"], action="pause")
        admin(world, "UPDATE focus_sessions SET paused_at = now() - interval '2 minutes' "
                     "WHERE id = CAST(:i AS uuid)", i=s["id"])
        ended = (await patch(c, ALICE, s["id"], action="end", outcome="done")).json()
    assert ended["minutes"] == 23 and ended["paused"] is False
    assert 420 <= ended["paused_seconds"] <= 424


async def test_extend_moves_the_planned_end_by_ten_minutes(world) -> None:
    """M4. A planned end in the future moves by 10 minutes. A planned end that
    has passed moves to 10 minutes from now."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Almost there")
        s = await start(c, ALICE, task)
        resp = await patch(c, ALICE, s["id"], action="extend")
        assert resp.status_code == 200, resp.text
        assert instant(resp.json()["planned_end"]) - instant(s["planned_end"]) == timedelta(
            minutes=10)
        admin(world, (
            "UPDATE focus_sessions SET started_at = now() - interval '40 minutes', "
            "planned_end = now() - interval '5 minutes' WHERE id = CAST(:i AS uuid)"), i=s["id"])
        late = (await patch(c, ALICE, s["id"], action="extend")).json()
    ahead = instant(late["planned_end"]) - datetime.now(UTC)
    assert timedelta(minutes=9) < ahead <= timedelta(minutes=10, seconds=5)


async def test_an_end_stamps_actual_end_with_the_answer(world) -> None:
    """M5. An end writes the answer and stamps ``actual_end``. A second act
    on the ended session gets 409."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Finish it")
        s = await start(c, ALICE, task)
        resp = await patch(c, ALICE, s["id"], action="end", outcome="not_now")
        assert resp.status_code == 200, resp.text
        again = await patch(c, ALICE, s["id"], action="pause")
    ended = row(world, s["id"])
    assert ended.outcome == "not_now" and ended.ended_at is not None
    assert overlay(world, task, ALICE).actual_end == ended.ended_at
    assert again.status_code == 409 and again.json()["detail"]["code"] == "session_ended"


async def test_an_end_needs_one_of_the_four_answers(world) -> None:
    """The member answers "Is it done?". A server outcome, a missing answer,
    an answer on another act and a key that names a member each get 422."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Answers")
        s = await start(c, ALICE, task)
        for body in (
            {"action": "end"},
            {"action": "end", "outcome": "replaced"},
            {"action": "end", "outcome": "auto_closed"},
            {"action": "pause", "outcome": "done"},
            {"action": "end", "outcome": "done", "member_email": BOB},
        ):
            resp = await patch(c, ALICE, s["id"], **body)
            assert resp.status_code == 422, (body, resp.text)
        bad = await c.post(BASE, json={"task_id": task, "mode": "25/5", "member_email": BOB},
                           headers=as_(ALICE))
        assert bad.status_code == 422
        odd = await c.post(BASE, json={"task_id": task, "mode": "90/0"}, headers=as_(ALICE))
        assert odd.status_code == 422
    assert row(world, s["id"]).ended_at is None
    assert count(world, BOB) == 0


async def test_a_stale_session_closes_once_under_two_concurrent_reads(world) -> None:
    """§9.3: a session 30 minutes past its planned end. The first reader
    closes it and has not committed. The second reader waits on the row lock,
    then updates no row. The row ends at ``planned_end`` as ``auto_closed``,
    and the overlay gets ``planned_end`` as its end. M6."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Walked away")
        s = await start(c, ALICE, task)
        admin(world, (
            "UPDATE focus_sessions SET started_at = now() - interval '60 minutes', "
            "planned_end = now() - interval '30 minutes' WHERE id = CAST(:i AS uuid)"),
            i=s["id"])

        async def second_reader() -> int:
            async with tenant_session(world.org) as db:
                return await fs.auto_close(db, ALICE)

        first_cm = tenant_session(world.org)
        first_db = await first_cm.__aenter__()
        done = False
        try:
            first = await fs.auto_close(first_db, ALICE)
            other = asyncio.create_task(second_reader())
            await asyncio.sleep(1.0)
            assert not other.done(), "the second reader did not wait on the row lock"
            await first_cm.__aexit__(None, None, None)
            done = True
            second = await other
        finally:
            if not done:
                await first_cm.__aexit__(None, None, None)
        listed = await day_of(c, ALICE)
    assert (first, second) == (1, 0)
    closed = row(world, s["id"])
    assert closed.outcome == "auto_closed"
    assert closed.ended_at == closed.planned_end
    assert overlay(world, task, ALICE).actual_end == closed.planned_end
    [item] = listed["sessions"]
    assert item["outcome"] == "auto_closed" and item["minutes"] == 30


async def test_two_reads_of_a_stale_session_through_the_routes_agree(world) -> None:
    """The same close through two GETs at once: one row, closed once."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Two readers")
        s = await start(c, ALICE, task)
        admin(world, (
            "UPDATE focus_sessions SET started_at = now() - interval '60 minutes', "
            "planned_end = now() - interval '20 minutes' WHERE id = CAST(:i AS uuid)"),
            i=s["id"])
        one, two = await asyncio.gather(day_of(c, ALICE), day_of(c, ALICE))
    for listed in (one, two):
        [item] = listed["sessions"]
        assert item["outcome"] == "auto_closed"
    closed = row(world, s["id"])
    assert closed.ended_at == closed.planned_end


async def test_a_session_inside_the_grace_stays_open(world) -> None:
    """Ten minutes past the planned end is inside the 15 minutes of §9.3."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Overrun")
        s = await start(c, ALICE, task)
        admin(world, (
            "UPDATE focus_sessions SET started_at = now() - interval '40 minutes', "
            "planned_end = now() - interval '10 minutes' WHERE id = CAST(:i AS uuid)"),
            i=s["id"])
        listed = await day_of(c, ALICE)
    [item] = listed["sessions"]
    assert item["outcome"] is None and row(world, s["id"]).ended_at is None


async def test_a_task_the_member_cannot_see_answers_404_and_writes_nothing(world) -> None:
    """Alice's personal task is hers alone. Bob's start on it is 404, and no
    row or overlay changes. M7."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Private")
        resp = await c.post(BASE, json={"task_id": task, "mode": "25/5"}, headers=as_(BOB))
        unknown = await c.post(BASE, json={"task_id": str(uuid.uuid4()), "mode": "flow"},
                               headers=as_(BOB))
    assert resp.status_code == 404 and unknown.status_code == 404
    assert count(world, BOB) == 0
    assert overlay(world, task, BOB) is None


async def test_the_flag_off_answers_404_on_every_route_and_writes_nothing(
    world, monkeypatch,
) -> None:
    """With the org off the list, each route answers 404 before any database
    work: the session seam raises if a route reaches it. M8."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Dark")
        s = await start(c, ALICE, task)
        rows_before = count(world, ALICE)
        pair_before = overlay(world, task, ALICE)

        def no_db(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("a dark route opened a session")

        monkeypatch.setattr(fs, "_tenant_session", no_db)
        # Off for every org, then on for org A only: org B is dark both times.
        for value in ("", world.other):
            monkeypatch.setattr(get_settings(), "focus_log_orgs", value)
            answers = [
                await c.post(BASE, json={"task_id": task, "mode": "25/5"}, headers=as_(ALICE)),
                await patch(c, ALICE, s["id"], action="end", outcome="done"),
                await c.get(BASE, headers=as_(ALICE)),
            ]
            assert [r.status_code for r in answers] == [404, 404, 404], [r.text for r in answers]
    assert count(world, ALICE) == rows_before
    assert row(world, s["id"]).ended_at is None
    assert overlay(world, task, ALICE) == pair_before


async def test_the_day_is_the_members_local_day(world) -> None:
    """Alice keeps Asia/Kolkata (UTC+05:30). 20:00 UTC on 1 November is 01:30
    on 2 November for her, and 18:00 UTC is 23:30 on 1 November. A UTC day
    would put both on 1 November. M9."""
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Late night")
        admin(world, (
            "INSERT INTO user_settings (organization_id, user_id, timezone) "
            "VALUES (CAST(:o AS uuid), :e, 'Asia/Kolkata')"), o=world.org, e=ALICE)
        for at, minutes in (("2026-11-01T20:00:00+00:00", 40), ("2026-11-01T18:00:00+00:00", 20)):
            admin(world, (
                "INSERT INTO focus_sessions (organization_id, member_email, task_id, "
                "started_at, ended_at, planned_end, mode, outcome) VALUES (CAST(:o AS uuid), "
                ":e, CAST(:t AS uuid), CAST(:at AS timestamptz), "
                "CAST(:at AS timestamptz) + make_interval(mins => :m), "
                "CAST(:at AS timestamptz) + interval '25 minutes', '25/5', 'done')"),
                o=world.org, e=ALICE, t=task, at=at, m=minutes)
        second = await day_of(c, ALICE, "2026-11-02")
        first = await day_of(c, ALICE, "2026-11-01")
        bad = await c.get(BASE, params={"day": "2 Nov"}, headers=as_(ALICE))
    assert second["timezone"] == "Asia/Kolkata"
    assert [s["minutes"] for s in second["sessions"]] == [40]
    assert [s["minutes"] for s in first["sessions"]] == [20]
    assert second["total_minutes"] == 40 and first["total_minutes"] == 20
    assert bad.status_code == 422


async def test_the_total_of_a_day_is_the_sum_of_its_rows(world) -> None:
    async with client(world, world.org) as c:
        task = await capture(c, ALICE, "Sum")
        for minutes, seconds in ((12, 59), (7, 30), (0, 45)):
            admin(world, (
                "INSERT INTO focus_sessions (organization_id, member_email, task_id, "
                "started_at, ended_at, planned_end, mode, outcome) VALUES (CAST(:o AS uuid), "
                ":e, CAST(:t AS uuid), now() - interval '3 hours', "
                "now() - interval '3 hours' + make_interval(mins => :m, secs => :s), "
                "now() - interval '2 hours', '25/5', 'done')"),
                o=world.org, e=ALICE, t=task, m=minutes, s=seconds)
        listed = await day_of(c, ALICE)
    assert sorted(s["minutes"] for s in listed["sessions"]) == [0, 7, 12]
    assert listed["total_minutes"] == 19
    assert listed["total_seconds"] == (12 * 60 + 59) + (7 * 60 + 30) + 45
    assert listed["more"] is False


async def test_flow_takes_the_end_of_the_block_that_runs_now(world) -> None:
    """Flow has no timer. Its planned end is the end of Alice's block of the
    task when that block runs now, else none, and no auto close applies."""
    now = datetime.now(UTC).replace(microsecond=0)
    block_end = now + timedelta(minutes=50)
    async with client(world, world.org) as c:
        blocked = await capture(
            c, ALICE, "Blocked time",
            scheduled_start=(now - timedelta(minutes=10)).isoformat(),
            scheduled_end=block_end.isoformat())
        loose = await capture(c, ALICE, "No block")
        a = await start(c, ALICE, blocked, mode="flow")
        b = await start(c, BOB, await capture(c, BOB, "Bob's"), mode="flow")
        assert b["planned_end"] is None
        c_ = await start(c, ALICE, loose, mode="flow")
    assert instant(a["planned_end"]) == block_end
    assert c_["planned_end"] is None
