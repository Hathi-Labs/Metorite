"""WS-28k against a REAL Postgres (R8).

The hermetic suite proves the arithmetic and the refusals. What only a database
answers:

* the **tenant key defaults from the session GUC** and an unbound insert fails
  closed — the whole point of declaring `organization_id` on day one, and a
  fake has no GUC and no NOT NULL;
* `starts_on`/`ends_on` are real DATEs bound as `date` objects, not
  `CAST(:x AS date)` over a string (the WS-27k shape asyncpg refuses);
* the CHECKs actually fire — a fake has no constraints, so `ends_on < starts_on`
  and a fourth `kind` look fine to it;
* the delete's `AND person_id = …` really is what stops one person removing
  another's span;
* `ON DELETE CASCADE` clears absences with the person, so the availability
  query cannot answer for somebody who is gone.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws28k.py

⚠️ Writes and deletes `gtd_people` rows under `@ws28k.invalid`. Scratch only.
"""
import asyncio
import os
import sys
from datetime import date, timedelta

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant, tenant_session
from gateway.db import get_db
from gateway.routes.people import absences as people_absences
from gateway.routes.people import core as people_core
from gateway.routes.people import selfservice as people_self
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text

MINE = "priya@ws28k.invalid"
THEIRS = "ravi@ws28k.invalid"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28k.invalid", "feature:people", "admin:members:manage",
             "admin:members:read")
ME = user(MINE)                               # a colleague with NO grants
THEM = user(THEIRS)


def absence(starts: date, ends: date, kind: str = "away",
            hours: float | None = None) -> people_absences.AbsenceIn:
    return people_absences.AbsenceIn(
        starts_on=starts.isoformat(), ends_on=ends.isoformat(),
        kind=kind, hours_per_day=hours)


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    try:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28k.invalid'"))
        await db.commit()

        mine = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Priya WS28K", email=MINE), ADMIN)
        theirs = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Ravi WS28K", email=THEIRS), ADMIN)

        # ── 1. A member with no grants records their own ───────────────────
        # NEXT Monday, computed — not a constant. The first version pinned
        # date(2026, 8, 10) and went stale the night the session crossed
        # midnight: the span fell wholly into the past and the person read
        # correctly dropped it. A live harness that can only pass on the day
        # it was written is a calendar, not a check.
        monday = date.today() + timedelta(days=8 - date.today().isoweekday())
        created = await people_self.add_my_absence(
            absence(monday, monday + timedelta(days=4)), ME)
        check("an ungranted member records their own absence",
              created["kind"], "away")

        row = (await db.execute(
            text("SELECT starts_on, ends_on, organization_id, created_by "
                 "  FROM gtd_person_absences WHERE id = CAST(:id AS uuid)"),
            {"id": created["id"]})).fetchone()
        check("the dates are real DATEs", (row.starts_on, row.ends_on),
              (monday, monday + timedelta(days=4)))
        check("the tenant defaulted from the bound session",
              str(row.organization_id), str(org.id))
        check("the author is recorded", row.created_by, MINE)

        # ── 2. The tenant key fails CLOSED outside a bound session ─────────
        release_tenant(token)
        try:
            await db.execute(
                text("INSERT INTO gtd_person_absences "
                     "  (person_id, starts_on, ends_on, created_by) "
                     "VALUES (CAST(:p AS uuid), :s, :e, 'probe')"),
                {"p": mine.id, "s": monday, "e": monday})
            await db.commit()
            check("an unbound insert is refused", "accepted", "refused")
        except Exception:
            await db.rollback()
            check("an unbound insert is refused", "refused", "refused")
        token = bind_tenant(str(org.id))

        # ── 3. The CHECKs are the database's, not just the route's ─────────
        for label, sql, params in (
            ("backwards dates",
             "INSERT INTO gtd_person_absences (person_id, starts_on, ends_on, "
             "created_by) VALUES (CAST(:p AS uuid), :s, :e, 'probe')",
             {"p": mine.id, "s": monday, "e": monday - timedelta(days=1)}),
            ("a fourth kind",
             "INSERT INTO gtd_person_absences (person_id, starts_on, ends_on, "
             "kind, created_by) VALUES (CAST(:p AS uuid), :s, :s, 'sick', 'probe')",
             {"p": mine.id, "s": monday}),
        ):
            try:
                await db.execute(text(sql), params)
                await db.commit()
                check(f"the database refuses {label}", "accepted", "refused")
            except Exception:
                await db.rollback()
                check(f"the database refuses {label}", "refused", "refused")

        # ── 4. Availability, computed over the real rows ───────────────────
        person_row = (await db.execute(
            text("SELECT * FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": mine.id})).fetchone()
        payload = await people_core.person_payload(db, person_row, ADMIN)
        check("the upcoming span is on the person read",
              len(payload["absences"]), 1)
        check("…and a colleague-visible away flag exists as a field",
              "away" in payload, True)

        spans = await people_absences.rows_for_availability(db, mine.id)
        from gateway import work_schedule as ws
        schedule = ws.effective_schedule(await ws.load_policy(db), None)
        check("a full week away leaves no working hours",
              ws.working_hours_between(schedule, monday,
                                       monday + timedelta(days=6), spans),
              0.0)
        check("…and the week after is untouched",
              ws.working_hours_between(schedule, monday + timedelta(days=7),
                                       monday + timedelta(days=13), spans),
              40.0)

        # ── 5. One person cannot delete another's span ─────────────────────
        try:
            await people_self.remove_my_absence(created["id"], THEM)
            check("somebody else's id deletes nothing", "deleted", "404")
        except Exception as exc:
            check("somebody else's id deletes nothing",
                  getattr(exc, "status_code", None), 404)
        still = (await db.execute(
            text("SELECT count(*) AS n FROM gtd_person_absences "
                 " WHERE id = CAST(:id AS uuid)"),
            {"id": created["id"]})).fetchone()
        check("…and the span is still there", int(still.n), 1)

        # Their own, they can.
        await people_self.remove_my_absence(created["id"], ME)
        gone = (await db.execute(
            text("SELECT count(*) AS n FROM gtd_person_absences "
                 " WHERE id = CAST(:id AS uuid)"),
            {"id": created["id"]})).fetchone()
        check("their own, they can", int(gone.n), 0)

        # ── 6. CASCADE: absences go with the person ────────────────────────
        #
        # Through `tenant_session`, not the raw `db`: this script's own session
        # comes from `get_db()` and never sets `app.tenant_id`, so an insert on
        # it defaults the tenant to NULL and fails the NOT NULL — which is
        # exactly what check 2 above proves, and exactly what aborted this
        # transaction the first time this harness was run. The fail-closed
        # property is the feature; using the right session is the caller's job.
        async with tenant_session(str(org.id)) as scoped:
            await people_absences.create_absence(
                scoped, theirs.id, absence(monday, monday), "probe")
        await db.execute(text("DELETE FROM gtd_people WHERE id = CAST(:id AS uuid)"),
                         {"id": theirs.id})
        await db.commit()
        orphans = (await db.execute(
            text("SELECT count(*) AS n FROM gtd_person_absences "
                 " WHERE person_id = CAST(:id AS uuid)"),
            {"id": theirs.id})).fetchone()
        check("deleting a person clears their absences", int(orphans.n), 0)
    finally:
        # A probe that was SUPPOSED to fail leaves the transaction aborted, and
        # every later statement — including this cleanup — is refused until it
        # is rolled back. Unconditional, because the harness cannot know which
        # of its deliberate failures was the last one.
        await db.rollback()
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28k.invalid'"))
        await db.commit()
        release_tenant(token)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
