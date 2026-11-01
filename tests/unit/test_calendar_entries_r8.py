"""WS-CAL CE-4 — the event store and its routes, on a real database (R8).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §3.3, §3.4, §4.1,
§9 and §10 CE-4 · D138.

The routes of ``gateway/routes/tasks/entries.py`` and the read of
``GET /projects/my/calendar`` run through the REAL ``/tasks`` and
``/projects`` routers, on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Two organizations, and two
members in org B. The same address also holds an entry in org A, so the org
test proves that RLS, and not the member filter, keeps org A out.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_calendar_entries_r8.py -v -rs

**Acceptance (§10 CE-4).** I see my entries. A colleague who is not an
attendee sees none. Org B sees no row of org A. The flag off gives today's
read. Also (the audit of 2026-10-31): a PATCH or a DELETE of an entry of
another member, or of an ``external`` entry, answers 404 and changes nothing.
A naive time is 422. A purged member leaves no entry. An all-day entry
returns the same dates in every ``tz``.

**The mutations (R7).** Each turned the named test red on 2026-10-31.
Commit before each.

* M1. ``entries.py`` ``read_my_entries``: drop ``VISIBLE_SQL`` (the owner or
  attendee predicate) from the read. Bob read Alice's entry.
  ``test_r8_a_colleague_who_is_not_an_attendee_sees_none_and_changes_nothing``.
* M2. ``entries.py`` ``MINE_SQL``: drop ``owner_email = :me``, the rule of
  the PATCH load. Bob's DELETE answered 204.
  ``test_r8_a_colleague_who_is_not_an_attendee_sees_none_and_changes_nothing``.
* M3. ``entries.py``: drop ``dependencies=_ON`` from the PATCH route. The
  PATCH answered 200 with the flag off.
  ``test_r8_the_flag_off_gives_todays_read_and_404_on_every_route``.
* M4. ``entries.py`` ``_instant``: read a time with no offset as UTC. A
  naive ``ends_at`` answered 201.
  ``test_r8_a_naive_time_and_a_bad_pair_are_a_422_and_write_nothing``.
* M5. ``321_calendar_entries.sql``: drop the CHECK
  ``calendar_entries_external_pair`` (a half pair was stored), or the index
  ``calendar_entries_external_uq`` (a twin was stored). Each run alone.
  ``test_r8_a_written_out_event_gets_no_twin``.
* M6. ``members.py``: drop the org predicate of the ``calendar_entries``
  purge row. The superuser run deleted org A's entry too (3, not 2).
  ``test_r8_a_member_purge_leaves_no_entry[superuser]``.
* M7. ``members.py``: drop the ``calendar_entries`` purge row. The
  Knowledge connection delete then failed on
  ``calendar_entries_connection_id_fkey``, so the order is load-bearing.
  ``test_r8_a_member_purge_leaves_no_entry[app]``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.routes.projects import personal
from gateway.routes.tasks import entries as ce
from sqlalchemy import exc as sa_exc
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
    if "CREATE TABLE IF NOT EXISTS calendar_entries (" in p.read_text(encoding="utf-8"))

ALICE = "alice@entries-r8.test"
BOB = "bob@entries-r8.test"
#: Holds `feature:projects` and not `feature:tasks`.
CAROL = "carol@entries-r8.test"
PEOPLE = (ALICE, BOB, CAROL)
BASE = "/tasks/calendar/entries"
READ = "/projects/my/calendar"
#: One week in Kolkata.
WEEK = {"start": "2026-03-09T00:00:00+05:30", "end": "2026-03-16T00:00:00+05:30"}
TIMED = {"starts_at": "2026-03-10T09:00:00+05:30", "ends_at": "2026-03-10T10:00:00+05:30"}


# ── The world ──────────────────────────────────────────────────────────────


def _user(email: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)))


def _app() -> FastAPI:
    """The real Projects and Tasks routers, signed in as ``x-user-email``."""
    from acb_auth import get_current_user
    from gateway.routes.projects import router as projects_router
    from gateway.routes.tasks import router as tasks_router

    people = {
        ALICE: _user(ALICE, "feature:projects", "feature:tasks"),
        BOB: _user(BOB, "feature:projects", "feature:tasks"),
        CAROL: _user(CAROL, "feature:projects"),
    }

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_user] = who
    return app


def _flag(monkeypatch: Any, on: bool) -> None:
    monkeypatch.setattr(ce, "get_settings", lambda: SimpleNamespace(calendar_events=on))


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Alice, Bob and Carol in the directory of org B, the flag on, and the
    `pm.*` events of a task write silenced."""
    org = promoted.org_b
    with promoted.admin_engine.begin() as c:
        for email in PEOPLE:
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )

    async def emit(event_type: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(personal, "emit", emit)
    _flag(monkeypatch, True)
    yield SimpleNamespace(p=promoted, org=org, other=promoted.org_a)
    who = list(PEOPLE)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM calendar_entries WHERE owner_email = ANY(:e)"), {"e": who})
        c.execute(
            text("DELETE FROM integration_connections WHERE member_email = ANY(:e)"),
            {"e": who},
        )
        c.execute(
            text(
                "DELETE FROM pm_tasks WHERE organization_id = CAST(:o AS uuid) "
                "AND created_by = ANY(:e)"
            ),
            {"o": org, "e": who},
        )
        c.execute(
            text(
                "DELETE FROM pm_projects WHERE organization_id = CAST(:o AS uuid) "
                "AND lower(personal_owner) = ANY(:e)"
            ),
            {"o": org, "e": who},
        )
        c.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"), {"e": who})


@asynccontextmanager
async def _client(world: Any, org: str) -> AsyncIterator[httpx.AsyncClient]:
    """Bind ``org``, point the shared engine at the app role, and talk to the
    routers in-process."""
    token = bind_tenant(org)
    try:
        url = world.p.app_url.render_as_string(hide_password=False)
        async with tenant_engine_scope(url):
            transport = httpx.ASGITransport(app=_app())
            async with httpx.AsyncClient(
                transport=transport, base_url="http://gateway.test",
            ) as client:
                yield client
    finally:
        release_tenant(token)


def _as(email: str) -> dict[str, str]:
    return {"x-user-email": email}


async def _create(c: httpx.AsyncClient, email: str, **extra: Any) -> dict:
    body = {"title": "Meet Sharath", **TIMED, "tz": "Asia/Kolkata", **extra}
    resp = await c.post(BASE, json=body, headers=_as(email))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _read(c: httpx.AsyncClient, email: str, window: dict | None = None) -> dict:
    resp = await c.get(READ, params=window or WEEK, headers=_as(email))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(world: Any, entry_id: str) -> Any:
    """The row as the superuser sees it, past RLS and past the person."""
    with world.p.admin_engine.connect() as c:
        return c.execute(
            text("SELECT * FROM calendar_entries WHERE id = CAST(:i AS uuid)"), {"i": entry_id},
        ).fetchone()


def _attendees(world: Any, entry_id: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return list(c.execute(
            text(
                "SELECT member_email, email, role, rsvp, organization_id::text AS org "
                "FROM calendar_entry_attendees WHERE entry_id = CAST(:i AS uuid) "
                "ORDER BY role DESC, member_email"
            ),
            {"i": entry_id},
        ))


def _seed_attendee(world: Any, entry_id: str, *, member: str | None,
                   email: str | None = None, org: str | None = None) -> None:
    """What CE-9 will write: an attendee row, here by direct SQL."""
    with world.p.admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO calendar_entry_attendees "
                "(organization_id, entry_id, member_email, email, role) "
                "VALUES (CAST(:o AS uuid), CAST(:i AS uuid), :m, :e, 'attendee')"
            ),
            {"o": org or world.org, "i": entry_id, "m": member, "e": email},
        )


def _seed_connection(world: Any, *, member: str, owning_app: str = "calendar",
                     provider: str = "google", org: str | None = None) -> str:
    """A member connection row, with no blob, as the superuser."""
    with world.p.admin_engine.begin() as c:
        return str(c.execute(
            text(
                "INSERT INTO integration_connections (organization_id, provider, "
                "owning_app, scope_kind, member_email) "
                "VALUES (CAST(:o AS uuid), :p, :a, 'member', :m) RETURNING id"
            ),
            {"o": org or world.org, "p": provider, "a": owning_app, "m": member},
        ).scalar_one())


def _count(world: Any, email: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(
            text("SELECT count(*) FROM calendar_entries WHERE owner_email = :e"), {"e": email},
        ).scalar_one())


# ── The migration and /version ───────────────────────────────────────────


def _check(sql: str, name: str) -> str:
    """The body of ONE named CHECK of the migration."""
    found = re.search(rf"CONSTRAINT {name} CHECK \((.*?)\)\),?\s*$", sql, re.S | re.M)
    assert found, f"no CHECK {name} in {_MIGRATION.name}"
    return found.group(1)


def test_the_migration_holds_the_rules_of_the_spec() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    for table in ("calendar_entries", "calendar_entry_attendees"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in sql

    def values(name: str) -> set[str]:
        return set(re.findall(r"'([a-z_]+)'", _check(sql, name)))

    assert values("calendar_entries_kind") == {"event", "external"}
    assert values("calendar_entries_source") == {"metorite", "google", "outlook"}
    assert values("calendar_entries_status") == {"confirmed", "tentative", "cancelled"}
    assert values("calendar_entry_attendees_role") == {"organizer", "attendee"}
    assert values("calendar_entry_attendees_rsvp") == {
        "needs_action", "accepted", "tentative", "declined"}
    # The module's list is the CHECK's list.
    assert set(ce.STATUSES) == values("calendar_entries_status")
    # `end_date` is exclusive: the CHECK refuses an empty all-day entry.
    assert "end_date > start_date" in _check(sql, "calendar_entries_time")
    # No cascade and no SET NULL on the connection (the supervisor's ruling).
    line = next(x for x in sql.splitlines() if x.strip().startswith("connection_id"))
    assert "REFERENCES integration_connections (id)" in line
    assert "ON DELETE" not in line
    # The key of a provider event names no kind and no source (W1).
    index = re.search(r"calendar_entries_external_uq\s+ON calendar_entries(.*?);", sql, re.S)
    assert index and "kind" not in index.group(1) and "source" not in index.group(1)


@pytest.mark.parametrize("on", [True, False])
async def test_r8_version_carries_the_flag(monkeypatch, on: bool) -> None:
    from gateway import main

    _flag(monkeypatch, on)
    assert (await main.version()).calendar_events is on


# ── I see my entries ─────────────────────────────────────────────────────


async def test_r8_i_see_my_entries(world) -> None:
    async with _client(world, world.org) as c:
        made = await _create(c, ALICE, location="Room 2",
                             conference_url="https://meet.example.test/abc")
        body = await _read(c, ALICE)
    assert made["kind"] == "event" and made["source"] == "metorite"
    assert made["is_mine"] is True and made["owner_email"] == ALICE
    assert made["my_role"] == "organizer" and made["my_rsvp"] == "accepted"
    assert made["all_day"] is False
    assert datetime.fromisoformat(made["starts_at"]) == datetime(2026, 3, 10, 3, 30, tzinfo=UTC)
    assert made["starts_at"].endswith("+00:00"), "the wire carries UTC"
    [shown] = body["entries"]
    assert shown == made
    row = _row(world, made["id"])
    assert str(row.organization_id) == world.org and row.owner_email == ALICE
    [organizer] = _attendees(world, made["id"])
    assert organizer.member_email == ALICE and organizer.role == "organizer"
    assert organizer.rsvp == "accepted" and organizer.org == world.org


async def test_r8_a_cancelled_entry_is_read_with_its_status(world) -> None:
    async with _client(world, world.org) as c:
        made = await _create(c, ALICE)
        resp = await c.patch(f"{BASE}/{made['id']}", json={"status": "cancelled"},
                             headers=_as(ALICE))
        assert resp.status_code == 200, resp.text
        [shown] = (await _read(c, ALICE))["entries"]
    assert shown["status"] == "cancelled"


# ── A colleague who is not an attendee sees none ──────────────────────────


async def test_r8_a_colleague_who_is_not_an_attendee_sees_none_and_changes_nothing(
    world,
) -> None:
    async with _client(world, world.org) as c:
        made = await _create(c, ALICE)
        assert (await _read(c, BOB))["entries"] == []
        resp = await c.patch(f"{BASE}/{made['id']}", json={"title": "Bob was here"},
                             headers=_as(BOB))
        assert resp.status_code == 404, resp.text
        resp = await c.delete(f"{BASE}/{made['id']}", headers=_as(BOB))
        assert resp.status_code == 404, resp.text
        assert _row(world, made["id"]).title == "Meet Sharath"

        # §4.1: an attendee row with Bob's member_email gives Bob the entry.
        _seed_attendee(world, made["id"], member=BOB)
        [seen] = (await _read(c, BOB))["entries"]
        assert seen["id"] == made["id"] and seen["is_mine"] is False
        assert seen["my_role"] == "attendee" and seen["my_rsvp"] == "needs_action"
        # An attendee does not change the entry of the owner.
        resp = await c.patch(f"{BASE}/{made['id']}", json={"title": "Bob was here"},
                             headers=_as(BOB))
        assert resp.status_code == 404, resp.text
        resp = await c.delete(f"{BASE}/{made['id']}", headers=_as(BOB))
        assert resp.status_code == 404, resp.text

        # A GUEST row with Bob's address and no member_email gives nobody the
        # entry, Bob included.
        guest = await _create(c, ALICE, title="Guest only")
        _seed_attendee(world, guest["id"], member=None, email=BOB)
        assert [e["id"] for e in (await _read(c, BOB))["entries"]] == [made["id"]]
    assert _row(world, made["id"]).title == "Meet Sharath"
    assert _row(world, guest["id"]) is not None


# ── Org B sees no row of org A ───────────────────────────────────────────


async def test_r8_org_b_sees_no_row_of_org_a(world, app_engine) -> None:  # noqa: F811
    async with _client(world, world.other) as c:
        theirs = await _create(c, ALICE, title="Org A only")
    async with _client(world, world.org) as c:
        mine = await _create(c, ALICE, title="Org B")
        assert [e["id"] for e in (await _read(c, ALICE))["entries"]] == [mine["id"]]
        resp = await c.patch(f"{BASE}/{theirs['id']}", json={"title": "Crossed"},
                             headers=_as(ALICE))
        assert resp.status_code == 404, resp.text
        resp = await c.delete(f"{BASE}/{theirs['id']}", headers=_as(ALICE))
        assert resp.status_code == 404, resp.text
    row = _row(world, theirs["id"])
    assert row.title == "Org A only" and str(row.organization_id) == world.other
    assert [a.org for a in _attendees(world, theirs["id"])] == [world.other]
    # Counted as org B, through the app role: no row of org A, in either table.
    with app_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.org})
        for table in ("calendar_entries", "calendar_entry_attendees"):
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE organization_id = CAST(:a AS uuid)"),
                {"a": world.other},
            ).scalar_one()
            assert n == 0, table


# ── The flag off gives today's read ──────────────────────────────────────


async def test_r8_the_flag_off_gives_todays_read_and_404_on_every_route(
    world, monkeypatch,
) -> None:
    async with _client(world, world.org) as c:
        resp = await c.post(
            "/projects/my/tasks",
            json={"title": "Write the brief", "scheduled_start": TIMED["starts_at"],
                  "scheduled_end": TIMED["ends_at"]},
            headers=_as(ALICE),
        )
        assert resp.status_code == 201, resp.text
        made = await _create(c, ALICE)
        on = await _read(c, ALICE)
        assert set(on) == {"rows", "total", "entries"} and len(on["rows"]) == 1
        # A member without `feature:tasks` gets no entry query with the flag on.
        assert set(await _read(c, CAROL)) == {"rows", "total"}

        _flag(monkeypatch, False)

        async def no_query(*args: Any, **kwargs: Any) -> list:
            raise AssertionError("the flag is off, so no entry query may run")

        monkeypatch.setattr(personal, "read_my_entries", no_query)
        off = await _read(c, ALICE)
        # Today's body: the rows and the total, unchanged, and no `entries`.
        assert set(off) == {"rows", "total"}
        assert off == {k: v for k, v in on.items() if k != "entries"}

        before = _count(world, ALICE)
        for method, path, body in (
            ("POST", BASE, {"title": "Off", **TIMED}),
            # Valid JSON of the wrong shape answers 404 too: the flag runs
            # before FastAPI checks the shape.
            ("POST", BASE, {"nonsense": True}),
            ("PATCH", f"{BASE}/{made['id']}", {"title": "Off"}),
            ("DELETE", f"{BASE}/{made['id']}", None),
        ):
            resp = await c.request(method, path, json=body, headers=_as(ALICE))
            assert resp.status_code == 404, (method, path, resp.text)
    assert _count(world, ALICE) == before
    assert _row(world, made["id"]).title == "Meet Sharath"


# ── Time ─────────────────────────────────────────────────────────────────


async def test_r8_an_all_day_entry_keeps_its_dates_in_every_zone(world) -> None:
    kolkata_day = {"start": "2026-03-08T00:00:00+05:30", "end": "2026-03-09T00:00:00+05:30"}
    # 8 March 2026 is the day Los Angeles moves its clock.
    la_day = {"start": "2026-03-08T00:00:00-08:00", "end": "2026-03-09T00:00:00-07:00"}
    async with _client(world, world.org) as c:
        made = []
        for zone in ("Asia/Kolkata", "America/Los_Angeles"):
            resp = await c.post(BASE, json={
                "title": f"Holiday {zone}", "start_date": "2026-03-08",
                "end_date": "2026-03-09", "tz": zone,
            }, headers=_as(ALICE))
            assert resp.status_code == 201, resp.text
            made.append(resp.json()["id"])
        for window in (kolkata_day, la_day):
            shown = {e["id"]: e for e in (await _read(c, ALICE, window))["entries"]}
            assert set(shown) == set(made), window
            for entry in shown.values():
                assert entry["all_day"] is True
                assert (entry["start_date"], entry["end_date"]) == ("2026-03-08", "2026-03-09")
                assert entry["starts_at"] is None and entry["ends_at"] is None
        # The end is exclusive, so an end on the start day is empty: 422.
        for first, last in (("2026-03-08", "2026-03-08"), ("2026-03-09", "2026-03-08")):
            resp = await c.post(BASE, json={"title": "Empty", "start_date": first,
                                            "end_date": last}, headers=_as(ALICE))
            assert resp.status_code == 422, resp.text
    for entry_id in made:
        row = _row(world, entry_id)
        assert (str(row.start_date), str(row.end_date)) == ("2026-03-08", "2026-03-09")


async def test_r8_a_naive_time_and_a_bad_pair_are_a_422_and_write_nothing(world) -> None:
    async with _client(world, world.org) as c:
        made = await _create(c, ALICE)
        before = _count(world, ALICE)
        for body in (
            {"title": "Naive", "starts_at": "2026-03-10T09:00:00",
             "ends_at": "2026-03-10T10:00:00+05:30"},
            {"title": "Naive", "starts_at": "2026-03-10T09:00:00+05:30",
             "ends_at": "2026-03-10T10:00:00"},
            {"title": "Both", **TIMED, "start_date": "2026-03-10", "end_date": "2026-03-11"},
            {"title": "Neither"},
            {"title": "Half", "starts_at": TIMED["starts_at"]},
            {"title": "Backwards", "starts_at": TIMED["ends_at"], "ends_at": TIMED["starts_at"]},
            {"title": "Zone", **TIMED, "tz": "America"},
            {"title": "Link", **TIMED, "conference_url": "javascript:alert(1)"},
            {"title": "", **TIMED},
            # The server decides the owner, the kind and the source, and CE-4
            # takes no attendee.
            {"title": "Owner", **TIMED, "owner_email": BOB},
            {"title": "Kind", **TIMED, "kind": "external"},
            {"title": "Guests", **TIMED, "attendees": [{"email": "x@y.test"}]},
        ):
            resp = await c.post(BASE, json=body, headers=_as(ALICE))
            assert resp.status_code == 422, (body, resp.text)
        for patch in (
            {"starts_at": "2026-03-10T11:00:00"},
            {"starts_at": "2026-03-10T11:00:00+05:30"},
            {"start_date": "2026-03-10"},
            {"title": None},
            {},
        ):
            resp = await c.patch(f"{BASE}/{made['id']}", json=patch, headers=_as(ALICE))
            assert resp.status_code == 422, (patch, resp.text)
    assert _count(world, ALICE) == before
    row = _row(world, made["id"])
    assert row.title == "Meet Sharath" and row.start_date is None
    assert row.starts_at == datetime(2026, 3, 10, 3, 30, tzinfo=UTC)


async def test_r8_a_patch_switches_the_pair_and_a_delete_takes_the_attendees(world) -> None:
    async with _client(world, world.org) as c:
        made = await _create(c, ALICE)
        _seed_attendee(world, made["id"], member=BOB)
        resp = await c.patch(f"{BASE}/{made['id']}", json={
            "title": "Off-site", "start_date": "2026-03-11", "end_date": "2026-03-13",
        }, headers=_as(ALICE))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["all_day"] is True and out["starts_at"] is None
        assert (out["start_date"], out["end_date"]) == ("2026-03-11", "2026-03-13")
        resp = await c.patch(f"{BASE}/{made['id']}", json=TIMED, headers=_as(ALICE))
        assert resp.status_code == 200 and resp.json()["start_date"] is None
        # A series link names one of MY events only.
        resp = await c.patch(f"{BASE}/{made['id']}", json={"series_id": made["id"]},
                             headers=_as(ALICE))
        assert resp.status_code == 422, resp.text
        bobs = await _create(c, BOB, title="Bob's own")
        resp = await c.patch(f"{BASE}/{made['id']}", json={"series_id": bobs["id"]},
                             headers=_as(ALICE))
        assert resp.status_code == 422, resp.text
        resp = await c.delete(f"{BASE}/{made['id']}", headers=_as(ALICE))
        assert resp.status_code == 204, resp.text
        assert [e["id"] for e in (await _read(c, BOB))["entries"]] == [bobs["id"]]
    assert _row(world, made["id"]) is None and _attendees(world, made["id"]) == []
    assert _row(world, bobs["id"]).title == "Bob's own"


# ── An external entry is the sync's, never a route's ────────────────────


async def test_r8_patch_and_delete_of_an_external_entry_answer_404(world) -> None:
    conn = _seed_connection(world, member=ALICE)
    with world.p.admin_engine.begin() as c:
        ext = str(c.execute(
            text(
                "INSERT INTO calendar_entries (organization_id, owner_email, kind, source, "
                "title, starts_at, ends_at, connection_id, external_id) VALUES "
                "(CAST(:o AS uuid), :me, 'external', 'google', 'From Google', "
                "'2026-03-10T05:00:00Z', '2026-03-10T06:00:00Z', CAST(:c AS uuid), 'g-1') "
                "RETURNING id"
            ),
            {"o": world.org, "me": ALICE, "c": conn},
        ).scalar_one())
    async with _client(world, world.org) as c:
        [seen] = (await _read(c, ALICE))["entries"]
        assert seen["id"] == ext and seen["kind"] == "external" and seen["is_mine"]
        assert "connection_id" not in seen and "external_id" not in seen
        resp = await c.patch(f"{BASE}/{ext}", json={"title": "Mine now"}, headers=_as(ALICE))
        assert resp.status_code == 404, resp.text
        resp = await c.delete(f"{BASE}/{ext}", headers=_as(ALICE))
        assert resp.status_code == 404, resp.text
    assert _row(world, ext).title == "From Google"


# ── The CE-10 and CE-11 columns ──────────────────────────────────────────


def _insert(conn: Any, org: str, **cols: Any) -> Any:
    base = {"owner_email": ALICE, "kind": "event", "source": "metorite", "title": "x",
            "starts_at": "2026-03-10T05:00:00Z", "ends_at": "2026-03-10T06:00:00Z"}
    base.update(cols)
    names = ", ".join(base)
    binds = ", ".join(
        f"CAST(:{k} AS uuid)" if k == "connection_id" else f":{k}" for k in base)
    return conn.execute(
        text(
            f"INSERT INTO calendar_entries (organization_id, {names}) "
            f"VALUES (CAST(:org AS uuid), {binds}) RETURNING id, kind, source, title"
        ),
        {"org": org, **base},
    ).one()


def _refused(world: Any, org: str, constraint: str, **cols: Any) -> None:
    """The insert fails, and the named constraint is the one that refused
    it. A refusal for another reason would prove nothing."""
    with pytest.raises(sa_exc.IntegrityError) as refused, world.p.admin_engine.begin() as conn:
        _insert(conn, org, **cols)
    assert f'"{constraint}"' in str(refused.value), str(refused.value)


async def test_r8_a_written_out_event_gets_no_twin(world) -> None:
    """W1: after CE-11 writes a Metorite event out, its provider id sits on
    the SAME row. The next sync finds that row by the key and updates it.
    It inserts no `external` twin. A half pair would escape the key, so the
    pair CHECK refuses it."""
    conn_id = _seed_connection(world, member=ALICE)
    with world.p.admin_engine.begin() as conn:
        first = _insert(conn, world.org, connection_id=conn_id, external_id="ev-1",
                        push_to_provider="google", provider_sync_state="sent")
    _refused(world, world.org, "calendar_entries_external_uq", kind="external",
             source="google", connection_id=conn_id, external_id="ev-1")
    with world.p.admin_engine.begin() as conn:
        updated = conn.execute(
            text(
                "INSERT INTO calendar_entries (organization_id, owner_email, kind, source, "
                "title, starts_at, ends_at, connection_id, external_id) VALUES "
                "(CAST(:o AS uuid), :me, 'external', 'google', 'Renamed at Google', "
                "'2026-03-10T05:00:00Z', '2026-03-10T06:00:00Z', CAST(:c AS uuid), 'ev-1') "
                "ON CONFLICT (organization_id, connection_id, external_id) "
                "WHERE connection_id IS NOT NULL AND external_id IS NOT NULL "
                "DO UPDATE SET title = EXCLUDED.title RETURNING id, kind, source, title"
            ),
            {"o": world.org, "me": ALICE, "c": conn_id},
        ).one()
        twins = conn.execute(
            text("SELECT count(*) FROM calendar_entries WHERE external_id = 'ev-1' "
                 "AND organization_id = CAST(:o AS uuid)"), {"o": world.org},
        ).scalar_one()
    assert updated.id == first.id and updated.title == "Renamed at Google"
    assert (updated.kind, updated.source) == ("event", "metorite")
    assert twins == 1
    # A half pair escapes the key, so the CHECK refuses it.
    _refused(world, world.org, "calendar_entries_external_pair", external_id="ev-2")
    _refused(world, world.org, "calendar_entries_external_pair", connection_id=conn_id)
    # Two rows of one pair in one org fail. The same pair in another org is
    # another key.
    with world.p.admin_engine.begin() as conn:
        _insert(conn, world.org, connection_id=conn_id, external_id="ev-3")
    _refused(world, world.org, "calendar_entries_external_uq", connection_id=conn_id,
             external_id="ev-3")
    with world.p.admin_engine.begin() as conn:
        _insert(conn, world.other, connection_id=conn_id, external_id="ev-3")


async def test_r8_the_write_out_checks_refuse_a_bad_state(world) -> None:
    conn_id = _seed_connection(world, member=ALICE)
    pair = {"connection_id": conn_id, "external_id": "ev-9"}
    # A copy of a provider event is never written back.
    _refused(world, world.org, "calendar_entries_external_no_push", kind="external",
             source="google", **pair, push_to_provider="google")
    _refused(world, world.org, "calendar_entries_external_no_push", kind="external",
             source="google", **pair, provider_sync_state="pending",
             push_to_provider="google")
    # `sent` needs the provider's id. A state needs a provider.
    _refused(world, world.org, "calendar_entries_sent_has_id", push_to_provider="google",
             provider_sync_state="sent")
    _refused(world, world.org, "calendar_entries_sync_needs_push",
             provider_sync_state="pending")
    _refused(world, world.org, "calendar_entries_push_to_provider", push_to_provider="teams")
    _refused(world, world.org, "calendar_entries_provider_sync_error",
             push_to_provider="google", provider_sync_state="failed",
             provider_sync_error="x" * 501)
    # The kind and the source go together.
    _refused(world, world.org, "calendar_entries_external", kind="external",
             source="metorite", **pair)
    _refused(world, world.org, "calendar_entries_external", kind="external",
             source="google")
    _refused(world, world.org, "calendar_entries_event_source", source="google")
    # A failed write keeps the row, with its error.
    with world.p.admin_engine.begin() as conn:
        _insert(conn, world.org, push_to_provider="google", provider_sync_state="failed",
                provider_sync_error="x" * 500)


async def test_r8_a_connection_that_an_entry_names_cannot_be_deleted(world) -> None:
    """NO ACTION, on purpose: a cascade would delete the own events of a
    member on a disconnect. The disconnect of CE-10 clears the five provider
    columns first, and then the delete works."""
    conn_id = _seed_connection(world, member=ALICE)
    with world.p.admin_engine.begin() as conn:
        mine = _insert(conn, world.org, connection_id=conn_id, external_id="ev-1",
                       push_to_provider="google", provider_sync_state="sent")
    with pytest.raises(sa_exc.IntegrityError) as refused, world.p.admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM integration_connections WHERE id = CAST(:c AS uuid)"),
                     {"c": conn_id})
    assert '"calendar_entries_connection_id_fkey"' in str(refused.value)
    with world.p.admin_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE calendar_entries SET connection_id = NULL, external_id = NULL, "
                "push_to_provider = NULL, provider_sync_state = NULL, "
                "provider_sync_error = NULL WHERE id = :i"
            ),
            {"i": mine.id},
        )
        conn.execute(text("DELETE FROM integration_connections WHERE id = CAST(:c AS uuid)"),
                     {"c": conn_id})
    assert _row(world, str(mine.id)).title == "x"


async def test_r8_an_organization_delete_takes_entries_and_connections(world) -> None:
    """NO ACTION is checked at the end of the statement, so the cascade of one
    `DELETE FROM organization` through both tables succeeds."""
    org = str(uuid.uuid4())
    with world.p.admin_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO organization (id, slug, display_name) "
                 "VALUES (CAST(:o AS uuid), :s, :s)"),
            {"o": org, "s": f"entries-r8-{org[:8]}"},
        )
    conn_id = _seed_connection(world, member=ALICE, org=org)
    with world.p.admin_engine.begin() as conn:
        event = _insert(conn, org, connection_id=conn_id, external_id="ev-1",
                        push_to_provider="google", provider_sync_state="sent")
        _insert(conn, org, kind="external", source="google", connection_id=conn_id,
                external_id="ev-2")
    _seed_attendee(world, str(event.id), member=BOB, org=org)
    with world.p.admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"), {"o": org})
    with world.p.admin_engine.connect() as conn:
        for table in ("calendar_entries", "calendar_entry_attendees",
                      "integration_connections"):
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org},
            ).scalar_one()
            assert n == 0, table


# ── A purged member leaves no entry ──────────────────────────────────────


@pytest.mark.parametrize("role", ["app", "superuser"])
async def test_r8_a_member_purge_leaves_no_entry(world, monkeypatch, role: str) -> None:
    """The admin purge deletes the member's entries of THIS org, with their
    attendee rows, and the member's seats on the entries of colleagues.

    The member's event names her member Knowledge connection, which the same
    purge deletes after the list. The entry has NO cascade from the
    connection, so the purge succeeds only because the entries go first. A
    colleague's entry stays, and so does the entry of the same address in
    another org. The superuser run bypasses RLS, so only the clause's own
    org predicate keeps org A's row safe there."""
    from gateway.db import tenant_session
    from gateway.routes.admin import members
    from gateway.routes.admin._common import get_member
    from gateway.routes.crm import questions

    async def _no_keys(org: str, email: str) -> int:
        return 0

    monkeypatch.setattr(members, "record_admin_change", lambda *a, **k: None)
    monkeypatch.setattr(questions, "forget_member_keys", _no_keys)
    owner = "owner-b@entries-r8.test"
    with world.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'executive', 'active', CAST(:o AS uuid))"),
            {"e": owner, "o": world.org})
        role_id = c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
            "ON CONFLICT DO NOTHING RETURNING id"), {"o": world.org}).scalar()
        if role_id is None:
            role_id = c.execute(text(
                "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = 'owner'"), {"o": world.org}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e"),
            {"r": role_id, "e": owner, "o": world.org})
    try:
        await _purge_and_check(world, members, get_member, tenant_session, role)
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text(
                "DELETE FROM user_role WHERE user_id IN "
                "(SELECT id FROM app_user WHERE email = :e)"), {"e": owner})
            c.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": owner})


async def _purge_and_check(
    world: Any, members: Any, get_member: Any, tenant_session: Any, role: str,
) -> None:
    async with _client(world, world.other) as c:
        theirs = (await _create(c, ALICE, title="Org A only"))["id"]
    knowledge = _seed_connection(world, member=ALICE, owning_app="knowledge",
                                 provider="google_drive")
    async with _client(world, world.org) as c:
        mine = [(await _create(c, ALICE))["id"],
                (await _create(c, ALICE, title="Dentist"))["id"]]
        bobs = (await _create(c, BOB, title="Bob's review"))["id"]
    with world.p.admin_engine.begin() as conn:
        conn.execute(
            text("UPDATE calendar_entries SET connection_id = CAST(:c AS uuid), "
                 "external_id = 'drive-1' WHERE id = CAST(:i AS uuid)"),
            {"c": knowledge, "i": mine[0]},
        )
    _seed_attendee(world, mine[1], member=BOB)
    _seed_attendee(world, bobs, member=ALICE)

    engine = world.p.app_url if role == "app" else world.p.admin_engine.url
    async with (
        tenant_engine_scope(engine.render_as_string(hide_password=False)),
        tenant_session(world.org) as session,
    ):
        member = await get_member(session, world.org, ALICE)
        deleted, _kept, _plans = await members.purge_member_rows(
            session, world.org, member, actor_email="owner-b@entries-r8.test")
    assert deleted["calendar_entries"] == 2
    assert deleted["calendar_attendances"] == 1
    for entry_id in mine:
        assert _row(world, entry_id) is None and _attendees(world, entry_id) == []
    assert _row(world, bobs).title == "Bob's review"
    assert [a.member_email for a in _attendees(world, bobs)] == [BOB]
    assert _row(world, theirs).title == "Org A only"
    with world.p.admin_engine.connect() as conn:
        left = conn.execute(
            text("SELECT count(*) FROM integration_connections WHERE id = CAST(:c AS uuid)"),
            {"c": knowledge},
        ).scalar_one()
    assert left == 0, "the purge deleted the connection after the entries"
