"""WS-CAL CE-13 — Metorite task blocks out to the calendar "Metorite" (R8).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §8.1a points 7 to 9 ·
D215 · migration ``calendar_task_links``.

A real database, the real writers (``create_personal_task``,
``_upsert_personal``), the real ``tenant_session`` as the non-privileged app
role (so FORCE RLS binds), and a FAKE provider that records each call. No
request leaves the test. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_calendar_sync_tasks.py -v -rs

**Acceptance (task 64).** One block makes one provider event, and the next
pass makes no copy. A move in Metorite updates the event. A move at the
provider moves the block. A done task deletes the event. The flag off sends no
request. Org B never writes into the connection of org A.

**The mutations (R7).** Commit before each.

* M1. ``task_out.py`` ``plan``: drop the ``synced_block_hash`` test. Every
  pass sends every block again.
  ``test_r8_one_block_makes_one_event_and_the_next_pass_makes_no_copy``.
* M2. ``task_out.py`` ``reconcile_task_blocks``: drop the ``write_allowed``
  return. The flag off sent a request.
  ``test_r8_the_flag_off_sends_no_request``.
* M3. ``task_links.py`` ``route_remote_change``: drop the ``etag`` echo test.
  The echo of our own write moved the block.
  ``test_r8_a_move_at_the_provider_moves_the_block_and_an_echo_changes_nothing``.
* M4. ``task_out.py`` ``_DESIRED``: drop the ``s.category NOT IN`` line. A done
  task kept its event.
  ``test_r8_done_a_cleared_block_and_a_deleted_task_delete_the_event``.
* M5. ``425_calendar_task_links.sql``: drop the RLS block. Org B read the link
  of org A.
  ``test_r8_org_b_never_writes_into_the_connection_of_org_a``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant, tenant_session
from gateway.calendar_sync import connections, flags, task_links, task_out
from gateway.calendar_sync.provider import (
    NeedsReconnect,
    OutResult,
    ProviderError,
    RemoteEvent,
    SyncResult,
)
from gateway.routes.projects import personal
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_MIGRATION = next(
    p for p in (Path(__file__).resolve().parents[2] / "infra" / "postgres").glob("*.sql")
    if "CREATE TABLE IF NOT EXISTS calendar_task_links (" in p.read_text(encoding="utf-8"))

ALICE = "alice@task-out.test"
BOB = "bob@task-out.test"
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
SECRET = "ya29.SECRET-TOKEN-VALUE"


@dataclass
class FakeProvider:
    """Records every call. Never touches a network."""

    provider: str = "google_calendar"
    source: str = "google"
    calls: list[tuple] = field(default_factory=list)
    fail_with: Exception | None = None
    _n: int = 0
    client_ids: list[tuple] = field(default_factory=list)

    async def list_calendars(self, cred):
        return []

    async def create_calendar(self, cred, name):
        self.calls.append(("create_calendar", name))
        return "cal-metorite"

    async def upsert_event(self, cred, calendar_id, event, external_id):
        self.calls.append(("upsert", calendar_id, event.title, event.starts_at,
                           event.ends_at, external_id, event.task_url))
        self.client_ids.append((external_id, event.client_id))
        if self.fail_with is not None:
            raise self.fail_with
        if external_id is None:
            self._n += 1
            external_id = f"ev{self._n}"
        return OutResult(external_id=external_id, etag=f'"e{len(self.calls)}"')

    async def delete_event(self, cred, calendar_id, external_id):
        self.calls.append(("delete", calendar_id, external_id))
        if self.fail_with is not None:
            raise self.fail_with

    reads: list[Any] = field(default_factory=list)
    feed: Any = None  # a SyncResult for the next read

    async def sync(self, cred, cursor, window, *, calendar_id=None):
        self.calls.append(("sync", calendar_id, cursor))
        return self.feed

    def sends(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("upsert", "delete")]


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    org_a, org_b = promoted.org_a, promoted.org_b
    ids = {"a": str(uuid.uuid4()), "b": str(uuid.uuid4())}
    with promoted.admin_engine.begin() as c:
        for key, org, who in (("a", org_a, ALICE), ("b", org_b, BOB)):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": who, "o": org},
            )
            c.execute(text(
                "INSERT INTO integration_connections (id, organization_id, provider, "
                "owning_app, scope_kind, member_email, status, provider_meta) "
                "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), 'google_calendar', 'calendar', "
                "'member', :m, 'connected', CAST(:meta AS jsonb))"),
                {"id": ids[key], "o": org, "m": who, "meta": '{"metorite_calendar_id": "cal-metorite"}'})
    flag = {"on": True}
    monkeypatch.setattr(flags, "write_allowed", lambda org, member=None: flag["on"])

    async def cred(org, conn, provider):
        return SimpleNamespace(access_token=SECRET)

    monkeypatch.setattr(connections, "fresh_credential", cred)

    async def emit(event_type: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(personal, "emit", emit)
    yield SimpleNamespace(p=promoted, org_a=org_a, org_b=org_b, conn=ids, flag=flag)
    with promoted.admin_engine.begin() as c:
        for tbl in ("calendar_task_links",):
            c.execute(text(f"DELETE FROM {tbl} WHERE member_email = ANY(:e)"), {"e": [ALICE, BOB]})
        c.execute(text("DELETE FROM integration_connections WHERE id = ANY(CAST(:i AS uuid[]))"),
                  {"i": list(ids.values())})
        c.execute(text("DELETE FROM pm_tasks WHERE created_by = ANY(:e)"), {"e": [ALICE, BOB]})
        c.execute(text("DELETE FROM pm_projects WHERE lower(personal_owner) = ANY(:e)"),
                  {"e": [ALICE, BOB]})
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": [ALICE, BOB]})


@asynccontextmanager
async def _in(world: Any, org: str) -> AsyncIterator[None]:
    token = bind_tenant(org)
    try:
        url = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            yield
    finally:
        release_tenant(token)


async def _task(org: str, who: str, title: str, start: datetime | None, mins: int = 60) -> str:
    async with tenant_session(org) as db:
        project = await personal.ensure_personal_project(db, who)
        status = await personal.load_default_status(db, str(project.id))
        task = await personal.create_personal_task(
            db, who, str(project.id), str(project.id), str(status.id),
            {"title": title, "source": "manual"}, None)
        if start is not None:
            await personal._upsert_personal(db, str(task.id), who, {
                "scheduled_start": start, "scheduled_end": start + timedelta(minutes=mins)})
        return str(task.id)


async def _move(org: str, who: str, task_id: str, start: datetime | None, mins: int = 60) -> None:
    async with tenant_session(org) as db:
        await personal._upsert_personal(db, task_id, who, {
            "scheduled_start": start,
            "scheduled_end": None if start is None else start + timedelta(minutes=mins)})


async def _close(org: str, task_id: str) -> None:
    async with tenant_session(org) as db:
        done = (await db.execute(text(
            "SELECT s.id FROM pm_task_statuses s JOIN pm_tasks t ON t.root_project_id = s.project_id "
            "WHERE t.id = CAST(:t AS uuid) AND s.category = 'done' LIMIT 1"), {"t": task_id})).fetchone()
        if done is None:
            pytest.skip("no done status in this scratch database")
        await db.execute(text("UPDATE pm_tasks SET status_id = :s WHERE id = CAST(:t AS uuid)"),
                         {"s": done.id, "t": task_id})


def _links(world: Any, org: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT * FROM calendar_task_links WHERE organization_id = CAST(:o AS uuid) "
            "ORDER BY created_at"), {"o": org}))


async def _pass(world: Any, org: str, conn_id: str, fake: FakeProvider, now: datetime = NOW):
    return await task_out.reconcile_task_blocks(org, conn_id, provider=fake, now=lambda: now)


async def _block(org: str, who: str, task_id: str) -> Any:
    async with tenant_session(org) as db:
        return (await db.execute(text(
            "SELECT scheduled_start, scheduled_end, updated_at FROM pm_task_personal "
            "WHERE task_id = CAST(:t AS uuid) AND lower(member_email) = :m"),
            {"t": task_id, "m": who})).fetchone()


# ── The tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_r8_one_block_makes_one_event_and_the_next_pass_makes_no_copy(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Write the spec", T0)
        s1 = await _pass(world, world.org_a, world.conn["a"], fake)
        s2 = await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=1))
    assert s1.created == 1 and s2.created == 0 and s2.updated == 0
    up = [c for c in fake.calls if c[0] == "upsert"]
    assert len(up) == 1
    assert up[0][1:6] == ("cal-metorite", "Write the spec", T0, T0 + timedelta(hours=1), None)
    assert up[0][6] and tid in up[0][6]  # a link back to the task, no attendees
    (link,) = _links(world, world.org_a)
    assert link.sync_state == "sent" and link.external_id == "ev1" and link.attempts == 0


@pytest.mark.asyncio
async def test_r8_a_task_with_no_block_makes_no_event(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "No time yet", None)
        s = await _pass(world, world.org_a, world.conn["a"], fake)
    assert s.skipped == "nothing_to_do" and fake.sends() == [] and _links(world, world.org_a) == []


@pytest.mark.asyncio
async def test_r8_a_move_and_a_resize_in_metorite_update_the_same_event(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Review", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        await _move(world.org_a, ALICE, tid, T0 + timedelta(hours=2), mins=90)
        s = await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=1))
    assert s.updated == 1 and s.created == 0
    up = [c for c in fake.calls if c[0] == "upsert"]
    assert len(up) == 2 and up[1][5] == "ev1"  # a patch of the SAME event id
    assert up[1][3] == T0 + timedelta(hours=2) and up[1][4] == T0 + timedelta(hours=3, minutes=30)
    assert len(_links(world, world.org_a)) == 1


@pytest.mark.asyncio
async def test_r8_done_a_cleared_block_and_a_deleted_task_delete_the_event(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        done = await _task(world.org_a, ALICE, "Done soon", T0)
        cleared = await _task(world.org_a, ALICE, "Cleared soon", T0 + timedelta(hours=2))
        gone = await _task(world.org_a, ALICE, "Deleted soon", T0 + timedelta(hours=4))
        await _pass(world, world.org_a, world.conn["a"], fake)
        assert len(_links(world, world.org_a)) == 3
        await _close(world.org_a, done)
        await _move(world.org_a, ALICE, cleared, None)
        async with tenant_session(world.org_a) as db:
            await db.execute(text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": gone})
        s = await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=1))
    assert s.deleted == 3
    assert sorted(c[2] for c in fake.calls if c[0] == "delete") == ["ev1", "ev2", "ev3"]
    assert _links(world, world.org_a) == []


@pytest.mark.asyncio
async def test_r8_the_flag_off_sends_no_request(world):
    fake = FakeProvider()
    world.flag["on"] = False
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "Dark", T0)
        s = await _pass(world, world.org_a, world.conn["a"], fake)
    assert s.skipped == "flag_off" and fake.calls == [] and _links(world, world.org_a) == []


@pytest.mark.asyncio
async def test_r8_a_move_at_the_provider_moves_the_block_and_an_echo_changes_nothing(world):
    fake = FakeProvider()
    new_start = T0 + timedelta(days=1)
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Both ways", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        (link,) = _links(world, world.org_a)
        conn = SimpleNamespace(id=world.conn["a"], member_email=ALICE)
        later = datetime.now(UTC) + timedelta(minutes=5)

        async def route(ev):
            async with tenant_session(world.org_a) as db:
                return await task_links.route_remote_change(db, world.org_a, conn, ev)

        # The echo of our own write: same etag, nothing moves.
        echo = RemoteEvent(external_id="ev1", etag=link.etag, starts_at=T0 + timedelta(hours=9),
                           ends_at=T0 + timedelta(hours=10), updated=later)
        assert await route(echo) == task_links.ECHO
        b = await _block(world.org_a, ALICE, tid)
        assert b.scheduled_start == T0
        # A newer move at the provider moves the block.
        ev = RemoteEvent(external_id="ev1", etag='"x9"', starts_at=new_start,
                         ends_at=new_start + timedelta(minutes=45), updated=later)
        assert await route(ev) == task_links.MOVED
        b = await _block(world.org_a, ALICE, tid)
        assert b.scheduled_start == new_start and b.scheduled_end == new_start + timedelta(minutes=45)
        # The pass after it sends nothing: the hash follows the block.
        n = len(fake.calls)
        s = await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=2))
        assert s.created == s.updated == s.deleted == 0 and len(fake.calls) == n
        # An OLDER change at the provider loses to the block.
        old = RemoteEvent(external_id="ev1", etag='"x10"', starts_at=T0, ends_at=T0 + timedelta(hours=1),
                          updated=datetime(2020, 1, 1, tzinfo=UTC))
        assert await route(old) == task_links.KEPT
        assert (await _block(world.org_a, ALICE, tid)).scheduled_start == new_start
        # An event that is not ours is ignored.
        assert await route(RemoteEvent(external_id="stranger", etag="z")) == task_links.IGNORED


@pytest.mark.asyncio
async def test_r8_a_delete_or_cancel_at_the_provider_unschedules_the_block_and_keeps_the_task(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        t1 = await _task(world.org_a, ALICE, "Gone at Google", T0)
        t2 = await _task(world.org_a, ALICE, "Cancelled at Google", T0 + timedelta(hours=3))
        await _pass(world, world.org_a, world.conn["a"], fake)
        conn = SimpleNamespace(id=world.conn["a"], member_email=ALICE)
        async with tenant_session(world.org_a) as db:
            assert await task_links.route_remote_change(
                db, world.org_a, conn, RemoteEvent(external_id="ev1", deleted=True)) == task_links.UNSCHEDULED
            assert await task_links.route_remote_change(
                db, world.org_a, conn, RemoteEvent(external_id="ev2", status="cancelled")) == task_links.UNSCHEDULED
        for t in (t1, t2):
            b = await _block(world.org_a, ALICE, t)
            assert b.scheduled_start is None and b.scheduled_end is None
            async with tenant_session(world.org_a) as db:
                assert (await db.execute(text("SELECT 1 FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                                         {"t": t})).fetchone() is not None
        n = len(fake.calls)
        await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=1))
        assert len(fake.calls) == n  # no event is made again
    assert _links(world, world.org_a) == []


@pytest.mark.asyncio
async def test_r8_a_failure_keeps_a_short_error_with_no_token_and_backs_off(world):
    fake = FakeProvider(fail_with=ProviderError("http_503", f"boom {SECRET}"))
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "Flaky", T0)
        s = await _pass(world, world.org_a, world.conn["a"], fake)
        assert s.failed == 1
        (link,) = _links(world, world.org_a)
        assert link.sync_state == "failed" and link.attempts == 1
        assert link.sync_error == "http_503" and SECRET not in link.sync_error and len(link.sync_error) <= 500
        assert link.next_attempt_at is not None and link.next_attempt_at > NOW
        # Inside the backoff: no request. After it: a retry, and it succeeds.
        n = len(fake.sends())
        await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(seconds=5))
        assert len(fake.sends()) == n
        fake.fail_with = None
        s = await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=10))
    assert s.created == 1
    (link,) = _links(world, world.org_a)
    assert link.sync_state == "sent" and link.attempts == 0 and link.sync_error is None


@pytest.mark.asyncio
async def test_r8_the_pull_reads_only_the_metorite_calendar_and_moves_and_unschedules(world):
    fake = FakeProvider()
    later = datetime.now(UTC) + timedelta(minutes=5)
    async with _in(world, world.org_a):
        moved = await _task(world.org_a, ALICE, "Moves at Google", T0)
        lost = await _task(world.org_a, ALICE, "Lost at Google", T0 + timedelta(hours=3))
        await _pass(world, world.org_a, world.conn["a"], fake)
        new = T0 + timedelta(days=2)
        fake.feed = SyncResult(
            changes=[RemoteEvent(external_id="ev1", etag='"n1"', starts_at=new,
                                 ends_at=new + timedelta(hours=1), updated=later)],
            next_cursor="c1", full=False)
        n = await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake,
                                               now=lambda: NOW)
        assert n == 1
        assert ("sync", "cal-metorite", None) in fake.calls  # the Metorite calendar, by id
        assert (await _block(world.org_a, ALICE, moved)).scheduled_start == new
        # The cursor is kept apart, and the next read sends it.
        fake.feed = SyncResult(changes=[], next_cursor="c2", full=False)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        assert ("sync", "cal-metorite", "c1") in fake.calls
        # A FULL list that no longer holds ev2 means the member deleted it.
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE integration_connections SET provider_meta = provider_meta - 'task_cursor' "
                           "WHERE id = CAST(:i AS uuid)"), {"i": world.conn["a"]})
        with world.p.admin_engine.begin() as c:  # the links are old enough (P2: the 5-minute age)
            c.execute(text("UPDATE calendar_task_links SET updated_at = now() - interval '1 hour'"))
        fake.feed = SyncResult(changes=[RemoteEvent(external_id="ev1", etag='"n1"')], next_cursor="c3", full=True)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        b = await _block(world.org_a, ALICE, lost)
        assert b.scheduled_start is None
        assert (await _block(world.org_a, ALICE, moved)).scheduled_start == new
    assert [l.external_id for l in _links(world, world.org_a)] == ["ev1"]


@pytest.mark.asyncio
async def test_r8_the_flag_off_reads_nothing(world):
    fake = FakeProvider()
    world.flag["on"] = False
    async with _in(world, world.org_a):
        n = await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
    assert n == 0 and fake.calls == []


@pytest.mark.asyncio
async def test_r8_an_unrelated_edit_does_not_make_the_block_win_over_a_provider_move(world):
    fake = FakeProvider()
    new = T0 + timedelta(days=1)
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Edited note", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        # The provider moves the event at t1, then the member edits a NOTE
        # (bumps updated_at past t1). The block time is untouched.
        t1 = datetime.now(UTC)
        async with tenant_session(world.org_a) as db:
            await personal._upsert_personal(db, tid, ALICE, {"flexible": True})
        conn = SimpleNamespace(id=world.conn["a"], member_email=ALICE)
        ev = RemoteEvent(external_id="ev1", etag='"m1"', starts_at=new,
                         ends_at=new + timedelta(hours=1), updated=t1)
        async with tenant_session(world.org_a) as db:
            assert await task_links.route_remote_change(db, world.org_a, conn, ev) == task_links.MOVED
        assert (await _block(world.org_a, ALICE, tid)).scheduled_start == new


def test_backoff_doubles_and_is_capped():
    assert task_out.backoff(1) == timedelta(seconds=30)
    assert task_out.backoff(2) == timedelta(seconds=60)
    assert task_out.backoff(30) == timedelta(hours=1)


@pytest.mark.asyncio
async def test_r8_a_dead_token_stops_the_pass_and_marks_nothing_sent(world):
    fake = FakeProvider(fail_with=NeedsReconnect("revoked"))
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "A", T0)
        await _task(world.org_a, ALICE, "B", T0 + timedelta(hours=2))
        s = await _pass(world, world.org_a, world.conn["a"], fake)
    assert s.skipped == "needs_reconnect" and len(fake.sends()) == 1
    assert all(l.sync_state != "sent" and l.next_attempt_at is None for l in _links(world, world.org_a))


@pytest.mark.asyncio
async def test_r8_org_b_never_writes_into_the_connection_of_org_a(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "Alice's block", T0)
    # Org B names the connection of org A: RLS hides it, nothing is sent.
    async with _in(world, world.org_b):
        await _task(world.org_b, BOB, "Bob's block", T0)
        s = await _pass(world, world.org_b, world.conn["a"], fake)
        assert s.skipped == "no_connection" and fake.calls == []
        # Its own connection works, and only Bob's block goes out.
        s = await _pass(world, world.org_b, world.conn["b"], fake)
        assert s.created == 1
        assert [c[2] for c in fake.calls if c[0] == "upsert"] == ["Bob's block"]
        # Under org B, the link table shows nothing of org A.
        async with tenant_session(world.org_b) as db:
            seen = (await db.execute(text("SELECT count(*) AS n FROM calendar_task_links "
                                          "WHERE member_email = :m"), {"m": ALICE})).fetchone()
        assert seen.n == 0
        # A link row for org A cannot be written under org B.
        with pytest.raises(Exception):
            async with tenant_session(world.org_b) as db:
                await db.execute(text(
                    "INSERT INTO calendar_task_links (organization_id, task_id, member_email, connection_id) "
                    "VALUES (CAST(:o AS uuid), gen_random_uuid(), :m, CAST(:c AS uuid))"),
                    {"o": world.org_a, "m": ALICE, "c": world.conn["a"]})
    assert {str(l.organization_id) for l in _links(world, world.org_b)} == {world.org_b}
    assert _links(world, world.org_a) == []


def test_the_migration_is_tenant_scoped_and_has_no_key_to_tasks():
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "FORCE  ROW LEVEL SECURITY" in sql or "FORCE ROW LEVEL SECURITY" in sql
    assert "organization_id" in sql and "app.tenant_id" in sql
    assert "REFERENCES pm_tasks" not in sql  # a deleted task must leave its link
    assert "char_length(sync_error) <= 500" in sql


# ── Opus review fixes (P1/P2) ─────────────────────────────────────────────
#
# * M6. ``task_out.py`` ``_lease``: drop the ``external_id IS NULL AND
#   sync_state <> 'sent'`` guard. Two workers both create.
#   ``test_r8_a_create_lease_is_refused_when_an_event_exists``.
# * M7. ``task_out.py`` ``calendar_identity``: return False at once. A new
#   calendar unschedules every block.
#   ``test_r8_a_new_metorite_calendar_drops_the_links_and_unschedules_nothing``.
# * M8. ``task_out.py`` ``pull_remote_changes``: drop the ``and live`` test.
#   ``test_r8_an_empty_full_list_unschedules_nothing``.
# * M9. ``task_links.py`` ``route_remote_change``: drop ``_block_exists``.
#   ``test_r8_a_provider_delete_for_a_deleted_task_drops_the_link_and_goes_on``.
# * M10. ``task_out.py`` ``_DESIRED``: drop the ``pm_projects`` line.
#   ``test_r8_a_task_in_an_archived_project_goes_out_no_more``.
# * M11. ``task_out.py`` ``reconcile_task_blocks``: drop ``client_event_id``.
#   ``test_r8_a_create_carries_a_stable_client_id_and_an_update_does_not``.


@pytest.mark.asyncio
async def test_r8_a_create_lease_is_refused_when_an_event_exists(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Leased", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)  # ev1, state sent
        later = lambda: NOW + timedelta(hours=1)  # noqa: E731
        async with tenant_session(world.org_a) as db:
            # A second worker that still thinks "no link" must not create.
            assert not await task_out._lease(
                db, world.org_a, world.conn["a"], ALICE, tid, later, create=True)
            # An update lease on a free link is fine.
            assert await task_out._lease(
                db, world.org_a, world.conn["a"], ALICE, tid, later, create=False)


@pytest.mark.asyncio
async def test_r8_a_create_carries_a_stable_client_id_and_an_update_does_not(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Retry safe", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        await _move(world.org_a, ALICE, tid, T0 + timedelta(hours=1))
        await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=1))
    (first, second) = fake.client_ids
    assert first[0] is None and first[1] == task_out.client_event_id(tid, world.conn["a"])
    assert second == ("ev1", None)
    cid = first[1]
    assert 5 <= len(cid) <= 1024 and set(cid) <= set("0123456789abcdefghijklmnopqrstuv")


@pytest.mark.asyncio
async def test_r8_a_new_metorite_calendar_drops_the_links_and_unschedules_nothing(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Survives", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        fake.feed = SyncResult(changes=[], next_cursor="c1", full=False)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        # The member deletes the calendar. The provider made a NEW one.
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE integration_connections SET provider_meta = provider_meta || "
                           "CAST(:m AS jsonb) WHERE id = CAST(:i AS uuid)"),
                      {"i": world.conn["a"], "m": '{"metorite_calendar_id": "cal-new"}'})
            c.execute(text("UPDATE calendar_task_links SET updated_at = now() - interval '1 hour'"))
        # A full list of the new calendar holds a stranger only.
        fake.feed = SyncResult(changes=[RemoteEvent(external_id="other", etag="o")],
                               next_cursor="c9", full=True)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        b = await _block(world.org_a, ALICE, tid)
        assert b.scheduled_start == T0  # the block is untouched
        assert _links(world, world.org_a) == []
        # The next push makes a new event in the new calendar.
        s = await _pass(world, world.org_a, world.conn["a"], fake, NOW + timedelta(minutes=1))
    assert s.created == 1
    assert [c[1] for c in fake.calls if c[0] == "upsert"][-1] == "cal-new"


@pytest.mark.asyncio
async def test_r8_an_empty_full_list_unschedules_nothing(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Kept", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE calendar_task_links SET updated_at = now() - interval '1 hour'"))
        fake.feed = SyncResult(changes=[], next_cursor="c1", full=True)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        assert (await _block(world.org_a, ALICE, tid)).scheduled_start == T0
    assert len(_links(world, world.org_a)) == 1


@pytest.mark.asyncio
async def test_r8_a_provider_delete_for_a_deleted_task_drops_the_link_and_goes_on(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        gone = await _task(world.org_a, ALICE, "Deleted here first", T0)
        moved = await _task(world.org_a, ALICE, "Still moves", T0 + timedelta(hours=3))
        await _pass(world, world.org_a, world.conn["a"], fake)
        async with tenant_session(world.org_a) as db:
            await db.execute(text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": gone})
        new = T0 + timedelta(days=3)
        fake.feed = SyncResult(changes=[
            RemoteEvent(external_id="ev1", deleted=True),
            RemoteEvent(external_id="ev2", etag='"n"', starts_at=new, ends_at=new + timedelta(hours=1),
                        updated=datetime.now(UTC) + timedelta(minutes=5)),
        ], next_cursor="c1", full=False)
        n = await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        assert n == 2
        assert (await _block(world.org_a, ALICE, moved)).scheduled_start == new
    assert [l.external_id for l in _links(world, world.org_a)] == ["ev2"]


@pytest.mark.asyncio
async def test_r8_one_bad_event_does_not_hold_the_cursor(world, monkeypatch):
    fake = FakeProvider()
    real = task_links.route_remote_change

    async def flaky(db, org, conn, ev):
        if ev.external_id == "ev1":
            raise RuntimeError("boom")
        return await real(db, org, conn, ev)

    monkeypatch.setattr(task_links, "route_remote_change", flaky)
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "Bad", T0)
        moved = await _task(world.org_a, ALICE, "Good", T0 + timedelta(hours=3))
        await _pass(world, world.org_a, world.conn["a"], fake)
        new = T0 + timedelta(days=3)
        fake.feed = SyncResult(changes=[
            RemoteEvent(external_id="ev1", etag="x"),
            RemoteEvent(external_id="ev2", etag='"n"', starts_at=new, ends_at=new + timedelta(hours=1),
                        updated=datetime.now(UTC) + timedelta(minutes=5)),
        ], next_cursor="c7", full=False)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        assert (await _block(world.org_a, ALICE, moved)).scheduled_start == new
    with world.p.admin_engine.connect() as c:
        cur = c.execute(text("SELECT provider_meta->>'task_cursor' FROM integration_connections "
                             "WHERE id = CAST(:i AS uuid)"), {"i": world.conn["a"]}).scalar()
    assert cur == "c7"


@pytest.mark.asyncio
async def test_r8_a_task_in_an_archived_project_goes_out_no_more(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "In an archived project", T0)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_projects SET archived_at = now() "
                           "WHERE lower(personal_owner) = :m"), {"m": ALICE})
        s = await _pass(world, world.org_a, world.conn["a"], fake)
    assert s.skipped == "nothing_to_do" and fake.sends() == []


@pytest.mark.asyncio
async def test_r8_a_young_link_is_not_unscheduled_by_a_full_list(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        tid = await _task(world.org_a, ALICE, "Just pushed", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)  # the link is seconds old
        fake.feed = SyncResult(changes=[RemoteEvent(external_id="stranger", etag="s")],
                               next_cursor="c1", full=True)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        assert (await _block(world.org_a, ALICE, tid)).scheduled_start == T0


@pytest.mark.asyncio
async def test_r8_disconnect_deletes_the_links_and_the_task_keys(world):
    fake = FakeProvider()
    async with _in(world, world.org_a):
        await _task(world.org_a, ALICE, "Linked", T0)
        await _pass(world, world.org_a, world.conn["a"], fake)
        fake.feed = SyncResult(changes=[], next_cursor="c1", full=False)
        await task_out.pull_remote_changes(world.org_a, world.conn["a"], provider=fake, now=lambda: NOW)
        assert len(_links(world, world.org_a)) == 1
        from gateway.connections import store

        async with tenant_session(world.org_a) as db:
            conn = await store.lock_by_id(db, world.org_a, world.conn["a"])
            await connections.disconnect_connection(db, world.org_a, conn)
    assert _links(world, world.org_a) == []
    with world.p.admin_engine.connect() as c:
        meta = c.execute(text("SELECT provider_meta FROM integration_connections "
                              "WHERE id = CAST(:i AS uuid)"), {"i": world.conn["a"]}).scalar()
    assert "task_cursor" not in meta and "task_calendar_id" not in meta
