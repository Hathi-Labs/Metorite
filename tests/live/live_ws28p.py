"""WS-28p against a REAL Postgres (R8).

The hermetic suite proves the arithmetic — which layer wins, what a bad policy
is refused with, who a change moves. It cannot prove the parts that only a
database answers, and this ticket has three:

* the policy is a **JSONB round trip** through `org_settings`: written with a
  ``CAST(:value AS JSONB)`` over a json STRING, read back as a string asyncpg
  hands over undecoded (bare ``text()`` declares no column type — the WS-27l
  shape). A fake stores the dict it was given and agrees with itself.
* the ``ON CONFLICT (key) DO UPDATE`` upsert actually replaces rather than
  duplicating — `org_settings.key` is the primary key, and a fake has no
  constraints.
* the calendar seed reaches a real ``gtd_settings`` read through
  ``routes/tasks/settings._load``, joined to a real ``gtd_people`` row on
  ``lower(email)``.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    # apply every numbered migration to a scratch `cc` database first
    uv run python tests/live/live_ws28p.py

⚠️ Writes and deletes `gtd_people` rows under `@ws28p.invalid`, and **replaces
the org's `work_schedule` setting**, restoring whatever was there at the end.
Point it at a scratch database.
"""
import asyncio
import os
import sys

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway.db import get_db
from gateway.routes.people import core as people_core
from gateway.routes.people import schedule as people_schedule
from gateway.routes.tasks import people as tasks_people
from gateway.routes.tasks import settings as tasks_settings
from sqlalchemy import text

SUBJECT_EMAIL = "priya@ws28p.invalid"
HALF_EMAIL = "sam@ws28p.invalid"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28p.invalid", "feature:people", "admin:members:manage",
             "admin:members:read")
MEMBER = user(SUBJECT_EMAIL, "feature:people")


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    saved_policy = (await db.execute(
        text("SELECT value FROM org_settings WHERE key = 'work_schedule'")
    )).fetchone()
    try:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28p.invalid'"))
        await db.execute(text(
            "DELETE FROM org_settings WHERE key = 'work_schedule'"))
        await db.commit()

        # ── 1. No policy row at all — the org still has a working week ──────
        check("an unconfigured org gets the default week",
              (await people_schedule.get_policy(ADMIN)).contracted_hours_per_week,
              40.0)

        # ── 2. The JSONB round trip ─────────────────────────────────────────
        policy = {
            "working_days": [1, 2, 3, 4, 5, 6],
            "hours_per_day": 7.5,
            "default_timezone": "Asia/Kolkata",
            "shifts": [{"name": "general", "start": "09:30", "end": "18:00",
                        "days": [1, 2, 3, 4, 5]}],
            "holidays": ["2026-08-15"],
        }
        out = await people_schedule.put_policy(
            people_schedule.PolicyWrite(policy=policy), ADMIN)
        check("the policy saved", out.saved, True)

        read = await people_schedule.get_policy(ADMIN)
        check("jsonb round-trips the day list", read.policy["working_days"],
              [1, 2, 3, 4, 5, 6])
        check("…and the float", read.policy["hours_per_day"], 7.5)
        check("…and the nested shift", read.policy["shifts"][0]["start"], "09:30")
        check("…and the holiday", read.policy["holidays"], ["2026-08-15"])
        check("the whole-company week is derived, not stored",
              read.contracted_hours_per_week, 45.0)
        check("the write is attributed", read.updated_by, ADMIN.email)

        # ── 3. The upsert REPLACES ──────────────────────────────────────────
        await people_schedule.put_policy(
            people_schedule.PolicyWrite(policy={"hours_per_day": 8}), ADMIN)
        rows = (await db.execute(text(
            "SELECT count(*) AS n FROM org_settings WHERE key = 'work_schedule'"
        ))).fetchone()
        check("one policy row, not two", int(rows.n), 1)
        check("…and it is the new one",
              (await people_schedule.get_policy(ADMIN)).policy["hours_per_day"],
              8.0)

        # ── 4. The layering against real rows ───────────────────────────────
        await people_schedule.put_policy(
            people_schedule.PolicyWrite(policy=policy), ADMIN)
        full = await tasks_people.create_person(
            tasks_people.PersonWrite(
                name="Priya WS28P", email=SUBJECT_EMAIL,
                capacity_hours_per_week=40), ADMIN)
        half = await tasks_people.create_person(
            tasks_people.PersonWrite(
                name="Sam WS28P", email=HALF_EMAIL,
                working_hours={"fraction": 0.5, "start": "10:00"}), ADMIN)

        row = (await db.execute(
            text("SELECT * FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": full.id})).fetchone()
        payload = await people_core.person_payload(db, row, ADMIN)
        check("the person rides the org policy",
              payload["contracted_hours_per_week"], 45.0)
        check("…and the source names the layer",
              payload["schedule"]["source"]["days"], "org")
        check("the typed capacity is flagged, not corrected",
              payload["capacity_conflict"], -5.0)

        half_row = (await db.execute(
            text("SELECT * FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": half.id})).fetchone()
        half_payload = await people_core.person_payload(db, half_row, ADMIN)
        check("a half-timer gets half the week",
              half_payload["contracted_hours_per_week"], 22.5)
        check("…and their own start time wins",
              half_payload["schedule"]["start"], "10:00")
        check("…which the source attributes to them",
              half_payload["schedule"]["source"]["start"], "person")

        # A member with no `admin:members:read` sees the schedule (directory
        # tier) and NOT the hours (HR tier) — on their own row they see both.
        stranger = await people_core.person_payload(db, half_row, MEMBER)
        check("a colleague sees WHEN somebody works",
              stranger["schedule"]["start"], "10:00")
        check("…and not how many hours they are contracted for",
              stranger["contracted_hours_per_week"], None)

        # ── 5. The impact, over real rows ───────────────────────────────────
        dry = await people_schedule.put_policy(
            people_schedule.PolicyWrite(
                policy={"working_days": [1, 2, 3, 4, 5], "hours_per_day": 8},
                dry_run=True),
            ADMIN)
        check("the dry run wrote nothing",
              (await people_schedule.get_policy(ADMIN)).policy["hours_per_day"],
              7.5)
        check("…and counted at least the two we made",
              dry.impact.changed >= 2, True)

        # ── 6. The calendar seed reaches a real gtd_settings read ───────────
        await db.execute(text("DELETE FROM gtd_settings WHERE user_id = :uid"),
                         {"uid": SUBJECT_EMAIL})
        await db.commit()
        seeded = await tasks_settings._load(db, SUBJECT_EMAIL)
        # 09:30-18:00 with an hour of margin either side.
        check("the calendar day starts an hour before the shift",
              seeded.day_start_hour, 8)
        check("…and ends an hour after", seeded.day_end_hour, 19)
        check("…with focus capacity below the contracted day",
              seeded.daily_capacity_mins, round(7.5 * 0.75 * 60))

        # Nothing was written: the seed is a read-time default.
        still_empty = (await db.execute(
            text("SELECT count(*) AS n FROM gtd_settings WHERE user_id = :uid"),
            {"uid": SUBJECT_EMAIL})).fetchone()
        check("the seed wrote no row", int(still_empty.n), 0)

        # A person with a row keeps THEIR value — seeded once, never mirrored.
        await db.execute(text(
            "INSERT INTO gtd_settings (user_id, day_start_hour) "
            "VALUES (:uid, 5)"), {"uid": SUBJECT_EMAIL})
        await db.commit()
        check("an existing preference is never overwritten",
              (await tasks_settings._load(db, SUBJECT_EMAIL)).day_start_hour, 5)

        # Somebody with no directory row still gets a working calendar.
        ghost = await tasks_settings._load(db, "ghost@ws28p.invalid")
        check("no directory row → migration 77's own defaults",
              ghost.day_start_hour, 7)
    finally:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28p.invalid'"))
        await db.execute(text(
            "DELETE FROM gtd_settings WHERE user_id LIKE '%@ws28p.invalid'"))
        await db.execute(text(
            "DELETE FROM org_settings WHERE key = 'work_schedule'"))
        if saved_policy is not None:
            await db.execute(
                text("INSERT INTO org_settings (key, value) "
                     "VALUES ('work_schedule', CAST(:v AS JSONB))"),
                {"v": saved_policy.value if isinstance(saved_policy.value, str)
                 else __import__("json").dumps(saved_policy.value)})
        await db.commit()
        release_tenant(token)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
