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
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
LATEST = "2099-11-02T10:00:00+05:30"


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


def test_the_migration_holds_the_lists_of_the_spec() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    for table in ("reminders", "reminder_events"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in sql
    assert "state IN ('scheduled', 'due', 'unconfirmed', 'done', 'dropped', 'cancelled')" in sql
    assert "resolution IN ('done', 'dropped', 'evidence', 'promoted', 'gave_up')" in sql
    for event in ("created", "fired", "check_back", "briefed", "rescheduled", "snoozed",
                  "opened", "done", "dropped", "promoted", "gave_up", "evidence",
                  "delivered", "failed", "edited"):
        assert f"'{event}'" in sql
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

        resp = await c.post(f"{BASE}/{rid}/snooze", json={"fire_at": LATEST},
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
            ("POST", f"{BASE}/{rid}/snooze", {"fire_at": LATER}),
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
                ("POST", f"{BASE}/{rid}/snooze", {"fire_at": LATER}),
                ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": LATER}),
                ("POST", f"{BASE}/{rid}/resolve", {"action": "drop"}),
                ("POST", f"{BASE}/{rid}/resolve", {"action": "promote"}),
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
            ("POST", f"{BASE}/{theirs}/snooze", {"fire_at": LATER}),
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
        rid = (await _create(c, ALICE, title="Renew the lease", notes="Before Friday"))["id"]
        resp = await c.post(f"{BASE}/{rid}/resolve", json={"action": "promote"},
                            headers=_as(ALICE))
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["state"] == "done" and out["resolution"] == "promoted"
        task_id = out["promoted_task_id"]
        assert task_id
        again = await c.post(f"{BASE}/{rid}/resolve", json={"action": "promote"},
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
        assert task.description == "Before Friday"
        # Private: the member's own personal root, which no team read lists.
        assert task.personal_owner == ALICE and task.parent_project_id is None
        assert str(task.organization_id) == world.org
        assignees = [r[0] for r in c.execute(
            text("SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": task_id},
        )]
        assert assignees == [ALICE]
    assert [e for e, _ in world.events] == ["pm.task.created"]
    assert _ledger(world, rid) == ["created", "promoted"]
    assert str(_row(world, rid).promoted_task_id) == task_id


async def test_r8_two_promotes_make_one_task(world) -> None:
    async with _client(world, world.org) as c:
        rid = (await _create(c, ALICE, title="Book the venue"))["id"]
        answers = await asyncio.gather(*(
            c.post(f"{BASE}/{rid}/resolve", json={"action": "promote"}, headers=_as(ALICE))
            for _ in range(2)
        ))
    assert sorted(a.status_code for a in answers) == [200, 409], [a.text for a in answers]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM pm_tasks WHERE created_by = :e AND title = :t"),
            {"e": ALICE, "t": "Book the venue"},
        ).scalar_one() == 1
    assert _ledger(world, rid) == ["created", "promoted"]


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
            ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": "smart"}),
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
            # A bad body still answers 404: the flag runs before the body.
            ("POST", BASE, {"nonsense": True}),
            ("PATCH", f"{BASE}/{rid}", {"title": "off"}),
            ("POST", f"{BASE}/{rid}/resolve", {"action": "done"}),
            ("POST", f"{BASE}/{rid}/snooze", {"fire_at": LATER}),
            ("POST", f"{BASE}/{rid}/reschedule", {"fire_at": LATER}),
            ("POST", f"{BASE}/{other}/resolve", {"action": "promote"}),
        ):
            resp = await c.request(method, path, json=body, headers=_as(ALICE))
            assert resp.status_code == 404, (method, path, resp.text)
    row = _row(world, rid)
    assert row.title == "Get a haircut" and row.state == "scheduled"
    assert _ledger(world, rid) == ["created"]
    with world.p.admin_engine.connect() as c:
        assert c.execute(
            text("SELECT count(*) FROM reminders WHERE member_email = :e"), {"e": ALICE},
        ).scalar_one() == 1
