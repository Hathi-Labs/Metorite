"""WS-66 RM-1 — the reminder store and its API, on a real database (R8).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.1 to §5.3, §5.8,
§9 and §10.3 · D114.

The routes of ``gateway/routes/projects/reminders.py`` run through the REAL
``/projects`` router, on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Two organizations, and two
members in org B. The same address also holds a reminder in org A, so the
org test proves that RLS, and not the member filter, keeps org A out.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_reminders_r8.py -v -rs

**Acceptance (§10.1 RM-1).** Member B never reads, changes or counts a
reminder of member A. Org B reads no row of org A. A promote makes one
private task and ends the reminder. Each change writes one ledger row, a
naive time is refused, and the flag off gives 404.

**The mutations (R7).** Each turned the named test red. Commit before each.

* M1. ``reminders.py``: drop ``member_email = :me`` from the list clause and
  from ``_MINE_FOR_UPDATE_SQL``.
  ``test_r8_member_b_never_reads_changes_or_counts_member_a``.
* M2. ``reminders.py`` ``_instant``: accept a time with no offset.
  ``test_r8_a_naive_time_is_refused_and_writes_nothing``.
* M3. ``reminders.py`` ``resolve_reminder``: drop ``FOR UPDATE`` from the
  load, or ``_require_open``.
  ``test_r8_two_promotes_make_one_task``.
* M4. ``reminders.py``: drop ``dependencies=_ON`` from a route.
  ``test_r8_the_flag_off_answers_404_on_every_route``.
* M5. ``reminders.py`` ``update_reminder``: drop the ``edited`` ledger write.
  ``test_r8_an_edit_writes_one_edited_row_that_names_the_fields``.
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

import httpx
from acb_common.db import bind_tenant, release_tenant
from fastapi import FastAPI, Request
from gateway.routes.projects import personal
from gateway.routes.projects import reminders as rm
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
    if "CREATE TABLE IF NOT EXISTS reminders (" in p.read_text(encoding="utf-8"))

ALICE = "alice@reminders-r8.test"
BOB = "bob@reminders-r8.test"
BASE = "/projects/my/reminders"
#: 09:00 in Kolkata is 03:30 UTC.
AT = "2099-10-31T09:00:00+05:30"
AT_UTC = "2099-10-31T03:30:00+00:00"
LATER = "2099-11-01T10:00:00+05:30"
#: A snooze must land after now and within 366 days, so it is relative.
SNOOZE_TO = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()
PR_URL = "https://github.com/acme/site/pull/7"
NOTES = f"Before Friday. See {PR_URL}"


# ── The world ──────────────────────────────────────────────────────────────


def _app() -> FastAPI:
    """The real Projects router, signed in as whoever ``x-user-email`` names."""
    from acb_auth import get_current_user
    from gateway.routes.projects import router

    from tests.unit._projects_fakes import member_user

    people = {ALICE: member_user(ALICE), BOB: member_user(BOB)}

    async def who(request: Request) -> Any:
        return people[request.headers["x-user-email"].lower()]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = who
    return app


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Alice and Bob in the directory of org B, the flag on, and a recorder
    for the ``pm.*`` events that a promote emits."""
    org = promoted.org_b
    with promoted.admin_engine.begin() as c:
        for email in (ALICE, BOB):
            c.execute(
                text(
                    "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                    "SELECT :e, :e, 'employee', 'active', CAST(:o AS uuid) WHERE NOT EXISTS "
                    "(SELECT 1 FROM app_user WHERE lower(email) = :e)"
                ),
                {"e": email, "o": org},
            )
    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(personal, "emit", emit)
    monkeypatch.setattr(
        rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=True),
    )
    yield SimpleNamespace(p=promoted, org=org, other=promoted.org_a, events=events)
    who = [ALICE, BOB]
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM reminders WHERE member_email = ANY(:e)"), {"e": who})
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
    router in-process."""
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
    body = {"title": "Get a haircut", "fire_at": AT, "tz": "Asia/Kolkata", **extra}
    resp = await c.post(BASE, json=body, headers=_as(email))
    assert resp.status_code == 201, resp.text
    return resp.json()


def _row(world: Any, reminder_id: str) -> Any:
    """The row as the superuser sees it, past RLS and past the member."""
    with world.p.admin_engine.connect() as c:
        return c.execute(
            text("SELECT * FROM reminders WHERE id = CAST(:i AS uuid)"), {"i": reminder_id},
        ).fetchone()


def _ledger(world: Any, reminder_id: str) -> list[str]:
    with world.p.admin_engine.connect() as c:
        return [r.event for r in c.execute(
            text(
                "SELECT event FROM reminder_events WHERE reminder_id = CAST(:i AS uuid) "
                "ORDER BY at, id"
            ),
            {"i": reminder_id},
        )]


def _ledger_rows(world: Any, reminder_id: str) -> list[Any]:
    """The ledger with its details, in order."""
    with world.p.admin_engine.connect() as c:
        return list(c.execute(
            text(
                "SELECT event, detail FROM reminder_events "
                "WHERE reminder_id = CAST(:i AS uuid) ORDER BY at, id"
            ),
            {"i": reminder_id},
        ))


def _make_due(world: Any, reminder_id: str) -> None:
    """What the loop of RM-2 will do at ``fire_at``: the row is ``due``."""
    with world.p.admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE reminders SET state = 'due', first_fired_at = now(), "
                "next_check_at = now() + interval '1 hour' WHERE id = CAST(:i AS uuid)"
            ),
            {"i": reminder_id},
        )


# ── The migration and /version ───────────────────────────────────────────


def _check_values(sql: str, name: str) -> set[str]:
    """The quoted values of ONE named CHECK of the migration, and no other.

    Read per constraint, so a value that another CHECK holds (`done` is a
    state, a resolution and an event) cannot stand in for a missing one.
    """
    found = re.search(rf"CONSTRAINT {name} CHECK \((.*?)\)\),?\s*$", sql, re.S | re.M)
    assert found, f"no CHECK {name} in {_MIGRATION.name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def test_the_migration_holds_the_lists_of_the_spec() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    for table in ("reminders", "reminder_events"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in sql
    assert _check_values(sql, "reminders_state") == {
        "scheduled", "due", "unconfirmed", "done", "dropped", "cancelled",
    }
    assert _check_values(sql, "reminders_resolution") == {
        "done", "dropped", "evidence", "promoted", "gave_up",
    }
    # The fourteen of §5.2, `edited` and `reopened`. The route module names
    # each one it writes, so the two lists must agree.
    events = _check_values(sql, "reminder_events_event")
    assert events == {
        "created", "fired", "check_back", "briefed", "rescheduled", "snoozed",
        "opened", "done", "dropped", "promoted", "gave_up", "evidence",
        "delivered", "failed", "edited", "reopened",
    }
    module = Path(rm.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'_ledger\(db, row\.id, "([a-z_]+)"', module))
    written |= {e for _s, _r, e in rm.RESOLUTIONS.values()}
    written |= {"snoozed", "rescheduled"}  # the event argument of `_move`
    assert written <= events, written - events
    for index in ("(organization_id, state, fire_at)",
                  "(organization_id, state, next_check_at)",
                  "(organization_id, member_email, state)"):
        assert f"ON reminders {index}" in sql


@pytest.mark.parametrize("on", [False, True])
async def test_version_carries_the_flag(monkeypatch, on: bool) -> None:
    from gateway import main

    monkeypatch.setattr(rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=on))
    assert (await main.version()).calendar_reminders is on


# ── R8 ─────────────────────────────────────────────────────────────────────


async def test_r8_a_reminder_round_trip_writes_a_ledger_row_per_change(
    world, app_engine,  # noqa: F811
) -> None:
    from tests.unit.test_email_keep_separate import _assert_non_priv

    _assert_non_priv(app_engine)
    async with _client(world, world.org) as c:
        made = await _create(c, ALICE, notes="Ravi's place", estimate_minutes=45)
        rid = made["id"]
        assert made["state"] == "scheduled" and made["kind"] == "action"
        assert made["fire_at"] == AT_UTC and made["tz"] == "Asia/Kolkata"
        assert made["snoozes"] == 0 and made["cycles"] == 0 and made["source"] == "app"
        assert "member_email" not in made and "organization_id" not in made

        listed = (await c.get(BASE, headers=_as(ALICE))).json()
        assert [r["id"] for r in listed["rows"]] == [rid] and listed["total"] == 1

        # A text edit moves no state and no time. It writes one `edited` row.
        resp = await c.patch(f"{BASE}/{rid}", json={"title": "Haircut"}, headers=_as(ALICE))
        assert resp.status_code == 200 and resp.json()["snoozes"] == 0

        # A member move from `due`: back to scheduled, snoozes + 1.
        _make_due(world, rid)
        resp = await c.patch(f"{BASE}/{rid}", json={"fire_at": LATER}, headers=_as(ALICE))
        moved = resp.json()
        assert moved["state"] == "scheduled" and moved["snoozes"] == 1
        assert moved["next_check_at"] is None and moved["cycles"] == 0

        resp = await c.post(f"{BASE}/{rid}/snooze", json={"fire_at": SNOOZE_TO},
                            headers=_as(ALICE))
        assert resp.json()["snoozes"] == 2
        resp = await c.post(f"{BASE}/{rid}/reschedule",
                            json={"fire_at": AT, "tz": "Europe/Berlin"}, headers=_as(ALICE))
        assert resp.json()["snoozes"] == 3 and resp.json()["tz"] == "Europe/Berlin"

        resp = await c.post(f"{BASE}/{rid}/resolve", json={"action": "done"},
                            headers=_as(ALICE))
        done = resp.json()
        assert done["state"] == "done" and done["resolution"] == "done"
        assert done["resolved_at"] is not None and done["cycles"] == 0

        # A closed reminder stays closed.
        for method, path, body in (
            ("POST", f"{BASE}/{rid}/resolve", {"action": "drop"}),
            ("POST", f"{BASE}/{rid}/snooze", {"fire_at": SNOOZE_TO}),
            ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": LATER}),
            ("PATCH", f"{BASE}/{rid}", {"title": "again"}),
        ):
            resp = await c.request(method, path, json=body, headers=_as(ALICE))
            assert resp.status_code == 409, (method, path, resp.text)
        assert (await c.get(BASE, headers=_as(ALICE))).json()["total"] == 0
        closed = (await c.get(BASE, params={"state": "done"}, headers=_as(ALICE))).json()
        assert [r["id"] for r in closed["rows"]] == [rid]

    assert _ledger(world, rid) == [
        "created", "edited", "rescheduled", "snoozed", "rescheduled", "done",
    ]
    row = _row(world, rid)
    assert str(row.organization_id) == world.org and row.member_email == ALICE


async def test_r8_an_edit_writes_one_edited_row_that_names_the_fields(world) -> None:
    task = str(uuid.uuid4())
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE))["id"]

        # Text, the link and `important`: one row, the NAMES in column order.
        resp = await c.patch(f"{BASE}/{rid}", json={
            "important": True, "title": "Haircut at Ravi's", "notes": "Cash only",
            "subject_kind": "task", "subject_id": task,
        }, headers=_as(ALICE))
        out = resp.json()
        assert resp.status_code == 200 and out["state"] == "scheduled"
        assert out["snoozes"] == 0 and out["fire_at"] == AT_UTC

        # A move and an edit in one PATCH: both rows, the move first.
        resp = await c.patch(f"{BASE}/{rid}", json={"fire_at": LATER, "kind": "notice"},
                             headers=_as(ALICE))
        assert resp.json()["snoozes"] == 1 and resp.json()["kind"] == "notice"

        # A field sent with its stored value is not a change: no row.
        resp = await c.patch(f"{BASE}/{rid}", json={"title": "Haircut at Ravi's"},
                             headers=_as(ALICE))
        assert resp.status_code == 200

        # The zone alone moves no instant: an edit, not a move.
        resp = await c.patch(f"{BASE}/{rid}", json={"tz": "Europe/Berlin"},
                             headers=_as(ALICE))
        assert resp.json()["snoozes"] == 1

        # Bob's edit of Alice's reminder is a miss, and writes nothing.
        resp = await c.patch(f"{BASE}/{rid}", json={"title": "Bob"}, headers=_as(BOB))
        assert resp.status_code == 404

    rows = _ledger_rows(world, rid)
    assert [r.event for r in rows] == ["created", "edited", "rescheduled", "edited", "edited"]
    assert rows[1].detail == {
        "fields": ["title", "notes", "subject_kind", "subject_id", "important"],
        "by": "member",
    }
    assert rows[2].detail["to"].startswith("2099-11-01T04:30:00")
    assert rows[3].detail["fields"] == ["kind"]
    assert rows[4].detail["fields"] == ["tz"]
    # The detail carries names, never the text of a field.
    assert all("Cash only" not in str(r.detail) for r in rows)


async def test_r8_a_patch_with_no_real_change_writes_nothing(world) -> None:
    """The same instant in another offset is not a move, and a PATCH that
    changes nothing writes no row and leaves ``updated_at`` as it was."""
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE))["id"]
        _make_due(world, rid)
        before = _row(world, rid)

        # 09:00 in Kolkata, sent as 03:30 UTC: the stored instant.
        resp = await c.patch(f"{BASE}/{rid}", json={"fire_at": AT_UTC}, headers=_as(ALICE))
        out = resp.json()
        assert resp.status_code == 200 and out["snoozes"] == 0 and out["state"] == "due"

        # The stored title and the stored instant, together: still nothing.
        resp = await c.patch(f"{BASE}/{rid}", json={
            "title": "Get a haircut", "fire_at": AT, "tz": "Asia/Kolkata",
        }, headers=_as(ALICE))
        assert resp.status_code == 200 and resp.json()["snoozes"] == 0

    after = _row(world, rid)
    assert after.updated_at == before.updated_at and after.state == "due"
    assert after.next_check_at == before.next_check_at and after.snoozes == 0
    assert _ledger(world, rid) == ["created"]


async def test_r8_member_b_never_reads_changes_or_counts_member_a(world) -> None:
    async with _client(world, world.org) as c:
        first = (await _create(c, ALICE))["id"]
        second = (await _create(c, ALICE, title="Call the bank"))["id"]
        _make_due(world, second)
        mine = (await _create(c, BOB, title="Bob's own"))["id"]

        for state in (None, "scheduled", "due", "unconfirmed", "done", "dropped"):
            params = {"state": state} if state else {}
            seen = (await c.get(BASE, params=params, headers=_as(BOB))).json()
            assert all(r["id"] == mine for r in seen["rows"]), (state, seen)
            assert seen["total"] == (1 if state in (None, "scheduled") else 0), (state, seen)

        for rid in (first, second):
            for method, path, body in (
                ("PATCH", f"{BASE}/{rid}", {"title": "Bob was here"}),
                ("PATCH", f"{BASE}/{rid}", {"fire_at": LATER}),
                ("POST", f"{BASE}/{rid}/snooze", {"fire_at": SNOOZE_TO}),
                ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": LATER}),
                ("POST", f"{BASE}/{rid}/resolve", {"action": "drop"}),
                ("POST", f"{BASE}/{rid}/promote", None),
                ("POST", f"{BASE}/{rid}/reopen", None),
            ):
                resp = await c.request(method, path, json=body, headers=_as(BOB))
                assert resp.status_code == 404, (method, path, resp.text)

        alice = (await c.get(BASE, headers=_as(ALICE))).json()
        assert {r["id"] for r in alice["rows"]} == {first, second} and alice["total"] == 2

    for rid, state in ((first, "scheduled"), (second, "due")):
        row = _row(world, rid)
        assert row.state == state and row.snoozes == 0 and row.resolution is None
        assert row.title in ("Get a haircut", "Call the bank")
        assert _ledger(world, rid) == ["created"]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM pm_tasks WHERE created_by = :e"), {"e": BOB},
        ).scalar_one() == 0


async def test_r8_org_b_reads_no_row_of_org_a(world, app_engine) -> None:  # noqa: F811
    # The SAME address holds a reminder in org A. The member filter alone
    # would match it, so only RLS keeps it out of org B.
    async with _client(world, world.other) as c:
        theirs = (await _create(c, ALICE, title="Org A only"))["id"]
    assert str(_row(world, theirs).organization_id) == world.other

    async with _client(world, world.org) as c:
        ours = (await _create(c, ALICE))["id"]
        seen = (await c.get(BASE, headers=_as(ALICE))).json()
        assert [r["id"] for r in seen["rows"]] == [ours] and seen["total"] == 1
        for method, path, body in (
            ("PATCH", f"{BASE}/{theirs}", {"title": "crossed"}),
            ("POST", f"{BASE}/{theirs}/snooze", {"fire_at": SNOOZE_TO}),
            ("POST", f"{BASE}/{theirs}/resolve", {"action": "done"}),
        ):
            resp = await c.request(method, path, json=body, headers=_as(ALICE))
            assert resp.status_code == 404, (method, path, resp.text)

    # The app role with org B bound sees no row of org A in either table,
    # and cannot write one into org A.
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world.org})
        orgs = {str(r[0]) for r in c.execute(text("SELECT organization_id FROM reminders"))}
        ledger = {str(r[0]) for r in c.execute(
            text("SELECT organization_id FROM reminder_events"))}
        assert orgs == {world.org} and ledger == {world.org}
    with pytest.raises(Exception, match="row-level security"), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world.org})
        c.execute(
            text(
                "INSERT INTO reminders (organization_id, member_email, title, fire_at) "
                "VALUES (CAST(:o AS uuid), :e, 'smuggled', now())"
            ),
            {"o": world.other, "e": ALICE},
        )
    row = _row(world, theirs)
    assert row.title == "Org A only" and row.state == "scheduled"
    assert _ledger(world, theirs) == ["created"]


async def test_r8_a_promote_makes_one_private_task_and_ends_the_reminder(world) -> None:
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE, title="Renew the lease", notes=NOTES))["id"]
        resp = await c.post(f"{BASE}/{rid}/promote", headers=_as(ALICE))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["state"] == "done" and out["resolution"] == "promoted"
        task_id = out["promoted_task_id"]
        assert task_id
        again = await c.post(f"{BASE}/{rid}/promote",
                             headers=_as(ALICE))
        assert again.status_code == 409

    with world.p.admin_engine.connect() as c:
        tasks = c.execute(
            text(
                "SELECT t.id, t.title, t.description, p.personal_owner, "
                "p.parent_project_id, t.organization_id FROM pm_tasks t "
                "JOIN pm_projects p ON p.id = t.project_id WHERE t.created_by = :e"
            ),
            {"e": ALICE},
        ).fetchall()
        assert len(tasks) == 1
        task = tasks[0]
        assert str(task.id) == task_id and task.title == "Renew the lease"
        assert task.description == NOTES
        # Private: the member's own personal root, which no team read lists.
        assert task.personal_owner == ALICE and task.parent_project_id is None
        assert str(task.organization_id) == world.org
        assignees = [r[0] for r in c.execute(
            text("SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": task_id},
        )]
        assert assignees == [ALICE]
        # WS-57 TR-5: the URL in the notes is a reference of the new task,
        # written after the commit, as a capture writes one.
        refs = c.execute(
            text("SELECT url, via FROM pm_task_refs WHERE task_id = CAST(:t AS uuid)"),
            {"t": task_id},
        ).fetchall()
        assert [(r.url, r.via) for r in refs] == [(PR_URL, "text")]
    assert [e for e, _ in world.events] == ["pm.task.created"]
    assert _ledger(world, rid) == ["created", "promoted"]
    assert str(_row(world, rid).promoted_task_id) == task_id


async def test_r8_two_promotes_make_one_task(world) -> None:
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE, title="Book the venue"))["id"]
        answers = await asyncio.gather(*(
            c.post(f"{BASE}/{rid}/promote", headers=_as(ALICE))
            for _ in range(2)
        ))
    assert sorted(a.status_code for a in answers) == [200, 409], [a.text for a in answers]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM pm_tasks WHERE created_by = :e AND title = :t"),
            {"e": ALICE, "t": "Book the venue"},
        ).scalar_one() == 1
    assert _ledger(world, rid) == ["created", "promoted"]


async def test_r8_reopen_undoes_a_done_and_a_drop(world) -> None:
    """D-CF-5: the Undo of a chip. A done goes back to the state it left,
    the check time too, and a drop does the same."""
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE))["id"]
        _make_due(world, rid)
        due = _row(world, rid)
        await c.post(f"{BASE}/{rid}/resolve", json={"action": "done"}, headers=_as(ALICE))
        resp = await c.post(f"{BASE}/{rid}/reopen", headers=_as(ALICE))
        assert resp.status_code == 200, resp.text
        back = resp.json()
        assert back["state"] == "due" and back["resolution"] is None
        assert back["resolved_at"] is None
        assert _row(world, rid).next_check_at == due.next_check_at

        other = (await _create(c, ALICE, title="Call the bank"))["id"]
        await c.post(f"{BASE}/{other}/resolve", json={"action": "drop"}, headers=_as(ALICE))
        resp = await c.post(f"{BASE}/{other}/reopen", headers=_as(ALICE))
        assert resp.json()["state"] == "scheduled" and resp.json()["resolution"] is None
        assert _row(world, other).next_check_at is None

        # A reopened reminder is open, so a second reopen is 409.
        resp = await c.post(f"{BASE}/{other}/reopen", headers=_as(ALICE))
        assert resp.status_code == 409

    rows = _ledger_rows(world, rid)
    assert [r.event for r in rows] == ["created", "done", "reopened"]
    assert rows[1].detail["from_state"] == "due"
    assert rows[2].detail == {"from_state": "done", "to_state": "due", "by": "member"}
    assert _ledger(world, other) == ["created", "dropped", "reopened"]


async def test_r8_reopen_refuses_a_promote_an_open_one_and_another_member(world) -> None:
    async with _client(world, world.org) as c:
        promoted_one = (await _create(c, ALICE, title="Renew the lease"))["id"]
        assert (await c.post(f"{BASE}/{promoted_one}/promote",
                             headers=_as(ALICE))).status_code == 200
        resp = await c.post(f"{BASE}/{promoted_one}/reopen", headers=_as(ALICE))
        assert resp.status_code == 409 and "promoted" in resp.json()["detail"]

        still_open = (await _create(c, ALICE, title="Open"))["id"]
        resp = await c.post(f"{BASE}/{still_open}/reopen", headers=_as(ALICE))
        assert resp.status_code == 409

        done = (await _create(c, ALICE, title="Done"))["id"]
        await c.post(f"{BASE}/{done}/resolve", json={"action": "done"}, headers=_as(ALICE))
        resp = await c.post(f"{BASE}/{done}/reopen", headers=_as(BOB))
        assert resp.status_code == 404
        # `/resolve` no longer promotes.
        resp = await c.post(f"{BASE}/{still_open}/resolve", json={"action": "promote"},
                            headers=_as(ALICE))
        assert resp.status_code == 422

    assert _row(world, promoted_one).state == "done"
    assert _row(world, done).state == "done" and _row(world, done).resolution == "done"
    assert _ledger(world, done) == ["created", "done"]
    assert _ledger(world, still_open) == ["created"]


async def test_r8_a_naive_time_is_refused_and_writes_nothing(world) -> None:
    async with _client(world, world.org) as c:
        for bad in ("2099-10-31T09:00:00", "2099-10-31", "tomorrow 9am"):
            resp = await c.post(BASE, json={"title": "x", "fire_at": bad}, headers=_as(ALICE))
            assert resp.status_code == 422, (bad, resp.text)
        assert "UTC offset" in (await c.post(
            BASE, json={"title": "x", "fire_at": "2099-10-31T09:00:00"}, headers=_as(ALICE),
        )).json()["detail"]
        assert (await c.post(BASE, json={"title": "x", "fire_at": AT, "tz": "Mars/Base"},
                             headers=_as(ALICE))).status_code == 422
        rid = (await _create(c, ALICE))["id"]
        for method, path, body in (
            ("PATCH", f"{BASE}/{rid}", {"fire_at": "2099-11-01T10:00:00"}),
            ("POST", f"{BASE}/{rid}/snooze", {"fire_at": "2099-11-01T10:00:00"}),
            ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": "2099-11-01T10:00:00"}),
            # RM-2: only /reschedule takes the smart slot
            # (test_reminder_loop.py), so a snooze and a PATCH refuse it.
            ("POST", f"{BASE}/{rid}/snooze", {"fire_at": "smart"}),
            ("PATCH", f"{BASE}/{rid}", {"fire_at": "smart"}),
        ):
            resp = await c.request(method, path, json=body, headers=_as(ALICE))
            assert resp.status_code == 422, (method, path, resp.text)
        resp = await c.get(BASE, params={"start": "2099-10-01T00:00:00"}, headers=_as(ALICE))
        assert resp.status_code == 422
    row = _row(world, rid)
    assert row.snoozes == 0 and row.state == "scheduled"
    assert _ledger(world, rid) == ["created"]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM reminders WHERE member_email = :e"), {"e": ALICE},
        ).scalar_one() == 1


@pytest.mark.parametrize("role", ["app", "superuser"])
async def test_r8_a_member_purge_leaves_no_reminder_behind(
    world, monkeypatch, role: str,
) -> None:
    """The admin purge deletes the member's reminders of THIS org, and their
    ledger by cascade. A colleague's reminders stay, and so do the reminders
    of the same address in another org. The superuser run bypasses RLS, so
    only the clause's own org predicate keeps org A's row safe there."""
    from gateway.db import tenant_session
    from gateway.routes.admin import members
    from gateway.routes.admin._common import get_member
    from gateway.routes.crm import questions

    async def _no_keys(org: str, email: str) -> int:
        return 0

    monkeypatch.setattr(members, "record_admin_change", lambda *a, **k: None)
    monkeypatch.setattr(questions, "forget_member_keys", _no_keys)
    # The purge refuses to leave org B with no owner, so org B gets one.
    owner = "owner-b@reminders-r8.test"
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
    async with _client(world, world.org) as c:
        mine = [(await _create(c, ALICE))["id"],
                (await _create(c, ALICE, title="Call the bank"))["id"]]
        await c.patch(f"{BASE}/{mine[0]}", json={"title": "Haircut"}, headers=_as(ALICE))
        bobs = (await _create(c, BOB, title="Bob's own"))["id"]
    assert _ledger(world, mine[0]) == ["created", "edited"]

    engine = world.p.app_url if role == "app" else world.p.admin_engine.url
    async with (
        tenant_engine_scope(engine.render_as_string(hide_password=False)),
        tenant_session(world.org) as session,
    ):
        member = await get_member(session, world.org, ALICE)
        deleted, _kept, _plans = await members.purge_member_rows(
            session, world.org, member, actor_email="owner-b@reminders-r8.test")
    assert deleted["reminders"] == 2
    for rid in mine:
        assert _row(world, rid) is None and _ledger(world, rid) == []
    assert _row(world, bobs).title == "Bob's own" and _ledger(world, bobs) == ["created"]
    assert _row(world, theirs).title == "Org A only" and _ledger(world, theirs) == ["created"]


@pytest.mark.parametrize("edge", ["0001-01-01T00:00:00+05:30", "9999-12-31T23:59:59-05:00"])
async def test_r8_a_time_at_the_edge_of_the_calendar_is_a_422(world, edge: str) -> None:
    """Year 1 with a positive offset and year 9999 with a negative one have
    no UTC form. Each is a 422 on every route that takes a time, never a 500."""
    async with _client(world, world.org) as c:
        resp = await c.post(BASE, json={"title": "x", "fire_at": edge}, headers=_as(ALICE))
        assert resp.status_code == 422 and "out of range" in resp.json()["detail"]
        rid = (await _create(c, ALICE))["id"]
        for method, path in (("PATCH", f"{BASE}/{rid}"), ("POST", f"{BASE}/{rid}/snooze"),
                             ("POST", f"{BASE}/{rid}/reschedule")):
            resp = await c.request(method, path, json={"fire_at": edge}, headers=_as(ALICE))
            assert resp.status_code == 422, (method, path, resp.text)
        for param in ("start", "end"):
            resp = await c.get(BASE, params={param: edge}, headers=_as(ALICE))
            assert resp.status_code == 422, (param, resp.text)
    assert _ledger(world, rid) == ["created"]


async def test_r8_a_bad_zone_estimate_or_snooze_is_a_422(world) -> None:
    async with _client(world, world.org) as c:
        # "America" names a directory of the zone database, not a zone.
        for extra in ({"tz": "America"}, {"tz": "../etc/passwd"},
                      {"estimate_minutes": 0}, {"estimate_minutes": 10081}):
            resp = await c.post(BASE, json={"title": "x", "fire_at": AT, **extra},
                                headers=_as(ALICE))
            assert resp.status_code == 422, (extra, resp.text)
        week = await c.post(BASE, json={"title": "A week", "fire_at": AT,
                                        "estimate_minutes": 10080}, headers=_as(ALICE))
        assert week.status_code == 201
        rid = week.json()["id"]
        for body in ({"tz": "America"}, {"estimate_minutes": 10081}):
            resp = await c.patch(f"{BASE}/{rid}", json=body, headers=_as(ALICE))
            assert resp.status_code == 422, (body, resp.text)
        resp = await c.post(f"{BASE}/{rid}/reschedule", json={"fire_at": AT, "tz": "America"},
                            headers=_as(ALICE))
        assert resp.status_code == 422

        now = datetime.now(UTC)
        for when in (now - timedelta(hours=1), now + timedelta(days=367)):
            resp = await c.post(f"{BASE}/{rid}/snooze", json={"fire_at": when.isoformat()},
                                headers=_as(ALICE))
            assert resp.status_code == 422 and "within 366 days" in resp.json()["detail"]
        ok = await c.post(f"{BASE}/{rid}/snooze",
                          json={"fire_at": (now + timedelta(days=365)).isoformat()},
                          headers=_as(ALICE))
        assert ok.status_code == 200 and ok.json()["snoozes"] == 1
    assert _ledger(world, rid) == ["created", "snoozed"]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM reminders WHERE member_email = :e"), {"e": ALICE},
        ).scalar_one() == 1


async def test_r8_the_flag_off_answers_404_on_every_route(world, monkeypatch) -> None:
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE))["id"]
        monkeypatch.setattr(
            rm, "get_settings", lambda: SimpleNamespace(calendar_reminders=False),
        )
        other = str(uuid.uuid4())
        for method, path, body in (
            ("GET", BASE, None),
            ("POST", BASE, {"title": "x", "fire_at": AT}),
            # Valid JSON of the wrong shape still answers 404: the flag runs
            # before FastAPI checks the shape.
            ("POST", BASE, {"nonsense": True}),
            ("PATCH", f"{BASE}/{rid}", {"title": "off"}),
            ("POST", f"{BASE}/{rid}/resolve", {"action": "done"}),
            ("POST", f"{BASE}/{rid}/snooze", {"fire_at": SNOOZE_TO}),
            ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": LATER}),
            ("POST", f"{BASE}/{other}/promote", None),
            ("POST", f"{BASE}/{rid}/reopen", None),
        ):
            resp = await c.request(method, path, json=body, headers=_as(ALICE))
            assert resp.status_code == 404, (method, path, resp.text)
        # A body that is not JSON at all is a 422 with the flag off, because
        # FastAPI decodes the JSON before any dependency runs. It writes
        # nothing either. The docstring of `_require_on` says so.
        resp = await c.post(BASE, content=b"not json", headers={
            **_as(ALICE), "content-type": "application/json",
        })
        assert resp.status_code == 422, resp.text
    row = _row(world, rid)
    assert row.title == "Get a haircut" and row.state == "scheduled"
    assert _ledger(world, rid) == ["created"]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM reminders WHERE member_email = :e"), {"e": ALICE},
        ).scalar_one() == 1


# ── RM-4: a tap opens the chat (§5.7) ──────────────────────────────────────
#
# `POST /chat/sessions/{id}/reminder-open` (`routes/chat_reminder.py`) with
# the real room check, the real minter and the real message seam. Those open
# `acb_graph` sessions, which the `chat` fixture points at the app role, as
# `test_chat_brief_turn_r8.py` does. The handler runs under the bound tenant,
# as the app's auth binds it.
#
# **Acceptance (§10.1 RM-4).** An open writes ONE assistant row with the
# reminder's text and no model call, and one `opened` ledger row. A `notice`
# is `done`, and an `action` stays `due`. Done, then the Undo of the chip,
# brings it back to `due`. A second member and a second org get 404 and no
# row. A second open is safe.
#
# **The mutations (R7).** Each turned the named test red. Commit before each.
#
# * M6. `reminders.open_reminder`: write `opened` for a closed reminder too
#   (drop `and row.state in OPEN_STATES`).
#   `test_r8_a_second_open_is_safe_and_writes_no_second_row`.
# * M7. `chat_reminder.open_reminder_in_chat`: pass `record=True` always.
#   `test_r8_a_second_open_is_safe_and_writes_no_second_row`.
# * M8. `reminders.open_reminder`: close an `action` reminder on open too.
#   `test_r8_an_open_writes_one_row_and_an_action_stays_due`.


@pytest.fixture
def chat(world, monkeypatch):
    """``acb_graph`` sessions as the app role, and the chat function granted
    to it. It yields the list of the sessions a test makes, and removes them
    after."""
    import acb_graph.db as graph_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    from tests.unit.test_h3_rls_promotion_rehearsal import _APP_ROLE

    with world.p.admin_engine.begin() as c:
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    eng = create_engine(world.p.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    made: list[str] = []
    try:
        yield made
    finally:
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM chat_session WHERE id = ANY(:s)"), {"s": made})
        eng.dispose()


def _chatter(email: str, org: str) -> Any:
    """A member who holds the chat and the Projects features, in *org*."""
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access(["feature:chat", "feature:projects"]),
    )


def _new_chat(made: list[str], email: str, org: str) -> str:
    """The session row the browser makes first (``sessions.ts``)."""
    from gateway.routes.chat import SessionUpsertRequest, _upsert_session

    sid = f"rm4-{uuid.uuid4().hex[:12]}"
    made.append(sid)
    _upsert_session(email, SessionUpsertRequest(id=sid, title="RM-4"), organization_id=org)
    return sid


async def _tap(world: Any, user: Any, sid: str, rid: str) -> Any:
    """The real handler, under the bound tenant and the app role."""
    from fastapi import Response
    from gateway.routes.chat_reminder import ReminderOpenIn, open_reminder_in_chat

    token = bind_tenant(user.organization_id)
    try:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await open_reminder_in_chat(
                sid, ReminderOpenIn(reminder_id=uuid.UUID(rid)), Response(), user=user)
    finally:
        release_tenant(token)


def _chat_rows(world: Any, sid: str) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, role, content, custom_events, author_kind, run_member_email, "
            "run_final_at IS NOT NULL AS sealed, organization_id::text AS org "
            "FROM chat_message WHERE session_id = :s ORDER BY timestamp_ms"), {"s": sid}).fetchall()


async def test_r8_an_open_writes_one_row_and_an_action_stays_due(world, chat) -> None:
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE, notes="At Ravi's"))["id"]
    _make_due(world, rid)
    sid = _new_chat(chat, ALICE, world.org)

    got = await _tap(world, _chatter(ALICE, world.org), sid, rid)

    assert got["posted"] is True and got["message_id"] == f"reminder-{rid}"
    [row] = _chat_rows(world, sid)
    assert row.id == f"reminder-{rid}" and row.role == "assistant" and row.author_kind == "agent"
    assert row.content == got["preview"]
    assert row.content.startswith("Reminder · Get a haircut · ")
    assert row.content.endswith("Did you do it?")
    assert row.run_member_email == ALICE and row.sealed and row.org == world.org
    [event] = row.custom_events
    assert event["name"] == "generative_ui"
    card = event["value"]["props"]
    assert card["name"] == "reminderCard"
    assert card["data"]["reminderId"] == rid and card["data"]["state"] == "due"
    assert card["data"]["zone"] == "Asia/Kolkata"
    # The context block: the id, the fenced title and notes, and the ledger.
    facts = got["context"]["facts"]
    assert f"reminder_id: {rid}" in facts[0]
    assert "Title: «Get a haircut»" in facts
    assert "Notes: «At Ravi's»" in facts
    assert facts[-1].startswith("History, oldest first: created ")
    assert "opened" in facts[-1]
    assert got["context"]["label"] == "Reminder · Get a haircut"
    # An open is not an answer: the action stays due.
    assert got["reminder"]["state"] == "due" and _row(world, rid).state == "due"
    rows = _ledger_rows(world, rid)
    assert [r.event for r in rows] == ["created", "opened"]
    assert rows[1].detail == {"by": "member"}

    # The Done chip, then its Undo: the exact calls of the card.
    async with _client(world, world.org) as c:
        done = await c.post(f"{BASE}/{rid}/resolve", json={"action": "done"}, headers=_as(ALICE))
        assert done.status_code == 200 and done.json()["state"] == "done"
        back = await c.post(f"{BASE}/{rid}/reopen", json={}, headers=_as(ALICE))
        assert back.status_code == 200 and back.json()["state"] == "due"
    assert _ledger(world, rid) == ["created", "opened", "done", "reopened"]


async def test_r8_a_notice_closes_on_open_and_the_undo_reopens_it(world, chat) -> None:
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE, title="The office closes early", kind="notice"))["id"]
    _make_due(world, rid)
    due = _row(world, rid)
    sid = _new_chat(chat, ALICE, world.org)

    got = await _tap(world, _chatter(ALICE, world.org), sid, rid)

    assert got["reminder"]["state"] == "done" and got["reminder"]["resolution"] == "done"
    [row] = _chat_rows(world, sid)
    assert row.content.endswith("today.") or row.content.endswith(".")
    assert "Did you do it?" not in row.content
    rows = _ledger_rows(world, rid)
    assert [r.event for r in rows] == ["created", "opened", "done"]
    assert rows[2].detail["from_state"] == "due" and rows[2].detail["via"] == "open"
    async with _client(world, world.org) as c:
        back = await c.post(f"{BASE}/{rid}/reopen", headers=_as(ALICE))
    assert back.json()["state"] == "due"
    assert _row(world, rid).next_check_at == due.next_check_at


async def test_r8_a_second_open_is_safe_and_writes_no_second_row(world, chat) -> None:
    async with _client(world, world.org) as c:
        action = (await _create(c, ALICE))["id"]
        notice = (await _create(c, ALICE, title="Priya's birthday", kind="notice"))["id"]
    _make_due(world, action)
    _make_due(world, notice)
    alice = _chatter(ALICE, world.org)
    sid, other = _new_chat(chat, ALICE, world.org), _new_chat(chat, ALICE, world.org)

    first = await _tap(world, alice, sid, action)
    again = await _tap(world, alice, sid, action)
    # The same chat asked again: no second row, and no second `opened`.
    assert first["posted"] is True and again["posted"] is False
    assert [r.id for r in _chat_rows(world, sid)] == [f"reminder-{action}"]
    assert _ledger(world, action) == ["created", "opened"]
    assert again["preview"] == first["preview"]

    # A second tap opens a second chat: one row there, and one more `opened`.
    second = await _tap(world, alice, other, action)
    assert second["posted"] is True
    assert [r.id for r in _chat_rows(world, other)] == [f"reminder-{action}"]
    assert _ledger(world, action) == ["created", "opened", "opened"]

    # A closed notice: a repeat tap writes nothing and is no 409.
    await _tap(world, alice, _new_chat(chat, ALICE, world.org), notice)
    repeat = await _tap(world, alice, _new_chat(chat, ALICE, world.org), notice)
    assert repeat["reminder"]["state"] == "done"
    assert _ledger(world, notice) == ["created", "opened", "done"]


async def test_r8_another_member_gets_404_and_no_row(world, chat) -> None:
    from fastapi import HTTPException

    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE))["id"]
    _make_due(world, rid)
    bob = _chatter(BOB, world.org)
    # Bob's own chat, Alice's reminder: the member filter misses.
    sid = _new_chat(chat, BOB, world.org)
    with pytest.raises(HTTPException) as err:
        await _tap(world, bob, sid, rid)
    assert err.value.status_code == 404 and err.value.detail == "Reminder not found"
    # Alice's chat: Bob cannot read it, so the session misses first.
    hers = _new_chat(chat, ALICE, world.org)
    with pytest.raises(HTTPException) as err:
        await _tap(world, bob, hers, rid)
    assert err.value.status_code == 404
    assert _chat_rows(world, sid) == [] and _chat_rows(world, hers) == []
    assert _ledger(world, rid) == ["created"] and _row(world, rid).state == "due"


async def test_r8_another_org_gets_404_and_no_row(world, chat) -> None:
    from fastapi import HTTPException

    # The SAME address holds the reminder in org B and a chat in org A. Only
    # RLS keeps org B's row out of org A's tap.
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE))["id"]
    _make_due(world, rid)
    sid = _new_chat(chat, ALICE, world.other)
    with pytest.raises(HTTPException) as err:
        await _tap(world, _chatter(ALICE, world.other), sid, rid)
    assert err.value.status_code == 404
    assert _chat_rows(world, sid) == []
    assert _ledger(world, rid) == ["created"]


async def test_r8_a_linked_task_shows_only_to_a_member_who_can_open_it(world, chat) -> None:
    # A promote makes a private task in Alice's personal project.
    async with _client(world, world.org) as c:
        made = (await _create(c, ALICE, title="Renew the lease"))["id"]
        task_id = (await c.post(f"{BASE}/{made}/promote", headers=_as(ALICE))).json()[
            "promoted_task_id"]
        mine = (await _create(c, ALICE, title="Chase the landlord",
                              subject_kind="task", subject_id=task_id))["id"]
        bobs = (await _create(c, BOB, title="Ask about the lease",
                              subject_kind="task", subject_id=task_id))["id"]
    for rid in (mine, bobs):
        _make_due(world, rid)

    got = await _tap(world, _chatter(ALICE, world.org), _new_chat(chat, ALICE, world.org), mine)
    assert got["preview"].split("\n\n")[1].startswith("Task: Renew the lease · ")
    assert any(f.startswith("Linked task: «Renew the lease»") for f in got["context"]["facts"])

    # Bob links Alice's private task. He sees its kind, and no word of it.
    seen = await _tap(world, _chatter(BOB, world.org), _new_chat(chat, BOB, world.org), bobs)
    said = seen["preview"] + " ".join(seen["context"]["facts"])
    assert seen["preview"].endswith("Linked: a task that you cannot open.")
    assert "Renew the lease" not in said and task_id not in said
    assert _ledger(world, bobs) == ["created", "opened"]
