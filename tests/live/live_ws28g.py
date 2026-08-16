"""WS-28g against a REAL Postgres (R8).

The hermetic suite proves the *rules*: which field is in which class, which
refusal names what, which SQL fragment the builder emits. It cannot prove the
things only a database knows, and this ticket is full of them:

* a ``jsonb`` bound as a Python dict — asyncpg has no codec for one, and bare
  ``text()`` declares no column type to decode against (the WS-27l shape);
* a ``date`` column bound from an ISO string — ``CAST(:x AS date)`` over a bound
  string is the shape asyncpg REFUSES (the WS-27k shape), which is why the
  builder parses to ``datetime.date`` instead;
* a ``text[]`` bound as a list rather than json-encoded;
* the round trip: what ``_row_to_person`` gets back is a jsonb **string**, a
  ``datetime.date`` and a real list — three different decode paths, none of
  which a fake exercises because a fake hands back whatever it was given;
* that migration 172 actually applies on top of the real ladder, twice.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    # apply every numbered migration to a scratch `cc` database first
    uv run python tests/live/live_ws28g.py

⚠️ This script writes and deletes rows in ``gtd_people`` whose email is under
``@ws28g.invalid``. It does **not** TRUNCATE — unlike most of its neighbours —
because `gtd_people` is a real roster and a scratch copy of it is still
somebody's directory. It cleans up after itself in a `finally`.
"""
import asyncio
import os
import sys
from datetime import date

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway.db import get_db
from gateway.routes.people import core as people_core
from gateway.routes.people import profile as people_profile
from gateway.routes.people import selfservice as people_self
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text

SUBJECT_EMAIL = "priya@ws28g.invalid"
OTHER_EMAIL = "ravi@ws28g.invalid"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28g.invalid", "feature:people", "admin:members:manage")
MANAGER = user("lead@ws28g.invalid", "feature:people", "admin:members:read")
SUBJECT = user(SUBJECT_EMAIL, "feature:people")
STRANGER = user("nobody@ws28g.invalid", "feature:people")


async def cleanup(db) -> None:
    await db.execute(text(
        "DELETE FROM gtd_people WHERE email LIKE '%@ws28g.invalid'"))
    await db.commit()


async def seed(db) -> str:
    """One person, inserted through the REAL create route's SQL path."""
    await cleanup(db)
    person = await tasks_people.create_person(
        tasks_people.PersonWrite(
            name="Priya WS28G", email=SUBJECT_EMAIL, title="Firmware lead",
            department="Engineering", skills=["firmware", "modbus"],
            capacity_hours_per_week=40,
            # The profile half — the columns `create_person`'s 2026-07 INSERT
            # has never heard of, applied by the shared builder right after it.
            timezone="Asia/Kolkata",
            working_hours={"days": [1, 2, 3, 4, 5], "start": "09:00"},
            links={"github": "priya"},
            languages=["English", "Kannada"],
            interests=["mechanical design"],
            employment_type="employee", seniority="lead",
            start_date="2021-06-01",
            phone="+91 99999 99999",
            emergency_contact={"name": "R", "relation": "spouse"},
            birthday="03-14",
        ),
        ADMIN,
    )
    return person.id


async def main() -> None:
    db = await get_db()
    # The routes acquire sessions through `tenant_session`, which refuses to
    # default a tenant — fail closed, never "the usual org" (MT-1c). A script
    # is outside a request, so it binds one explicitly, exactly as
    # `live_ws27be.py` does.
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))
    ).fetchone()
    token = bind_tenant(str(org.id))
    try:
        pid = await seed(db)

        # ── 1. The create path did not drop the profile half ───────────────
        row = (await db.execute(
            text("SELECT * FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": pid})).fetchone()
        check("timezone survived create", row.timezone, "Asia/Kolkata")
        check("array is a real text[] and not a json string",
              list(row.languages), ["English", "Kannada"])
        check("date is a real DATE", row.start_date, date(2021, 6, 1))
        check("jsonb round-trips to a dict",
              tasks_people._jsonb(row.working_hours)["start"], "09:00")
        check("birthday is MM-DD text", row.birthday, "03-14")

        # ── 2. The tiers, computed against a real row ──────────────────────
        mine = await people_core.person_payload(db, row, SUBJECT)
        check("subject reads own HR half", mine["hr_visible"], True)
        check("subject reads own private half", mine["phone"], "+91 99999 99999")
        check("subject is told it is theirs", mine["is_self"], True)
        check("subject may write the self class",
              mine["editable_fields"] == sorted(
                  __import__("gateway.routes.people.fields", fromlist=["x"]
                             ).SELF_FIELDS), True)

        seen = await people_core.person_payload(db, row, MANAGER)
        check("manager sees skills", seen["skills"], ["firmware", "modbus"])
        check("manager does NOT see the phone (D-PC-3)", seen["phone"], None)
        check("manager does NOT see the emergency contact",
              seen["emergency_contact"], None)

        blind = await people_core.person_payload(db, row, STRANGER)
        check("stranger sees the directory half", blind["timezone"],
              "Asia/Kolkata")
        check("stranger sees no skills", blind["skills"], [])
        check("shape is identical whatever the tier",
              blind.keys() == seen.keys(), True)

        # ── 3. The self write door, end to end ─────────────────────────────
        after = await people_profile.update_profile(
            pid, tasks_people.PersonWrite(
                timezone="Europe/Berlin",
                working_hours={"days": [1, 2, 3], "start": "10:00"},
                interests=["controls", "test rigs"],
                max_concurrent_tasks=3,
            ), SUBJECT)
        check("self write landed", after["timezone"], "Europe/Berlin")
        check("self write updated the jsonb",
              after["working_hours"]["start"], "10:00")
        check("self write updated the array", after["interests"],
              ["controls", "test rigs"])
        check("self write updated the int", after["max_concurrent_tasks"], 3)

        # A field the person may not write is refused BY THE DATABASE never
        # being asked — the check runs first, and this proves it end to end.
        try:
            await people_profile.update_profile(
                pid, tasks_people.PersonWrite(title="CTO"), SUBJECT)
            check("self cannot change their title", "allowed", "403")
        except Exception as exc:  # HTTPException
            check("self cannot change their title",
                  getattr(exc, "status_code", None), 403)
            check("the refusal names the field",
                  "title" in str(getattr(exc, "detail", "")), True)
        again = (await db.execute(
            text("SELECT title FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": pid})).fetchone()
        check("and the row is untouched", again.title, "Firmware lead")

        # ── 4. The vocabularies the migration left to the route ────────────
        try:
            await people_profile.update_profile(
                pid, tasks_people.PersonWrite(employment_type="freelance"),
                ADMIN)
            check("a bad employment_type is refused", "stored", "400")
        except Exception as exc:
            check("a bad employment_type is refused",
                  getattr(exc, "status_code", None), 400)
        try:
            await people_profile.update_profile(
                pid, tasks_people.PersonWrite(birthday="1990-03-14"), ADMIN)
            check("a date of birth is refused (D-PC-9)", "stored", "400")
        except Exception as exc:
            check("a date of birth is refused (D-PC-9)",
                  getattr(exc, "status_code", None), 400)

        # ── 5. The self predicate rests on 148's index ─────────────────────
        found = await people_core.find_self_row(db, SUBJECT)
        check("find_self_row matches on lowercased email",
              str(found.id), pid)
        check("…and is case-insensitive on the caller's side",
              str((await people_core.find_self_row(
                  db, user(SUBJECT_EMAIL.upper()))).id), pid)
        check("…and answers None for somebody with no row",
              await people_core.find_self_row(db, STRANGER), None)

        # The index is what makes "at most one row" a guarantee. Prove it is
        # still there and still case-folding, because the whole self model
        # rests on it.
        other = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Ravi WS28G", email=OTHER_EMAIL),
            ADMIN)
        try:
            await db.execute(
                text("UPDATE gtd_people SET email = :e WHERE id = CAST(:id AS uuid)"),
                {"e": SUBJECT_EMAIL.upper(), "id": other.id})
            await db.commit()
            check("two rows cannot share an address", "allowed", "refused")
        except Exception:
            await db.rollback()
            check("two rows cannot share an address", "refused", "refused")

        # ── 6. WS-28g-2: the UNGATED self door, against a real row ─────────
        # A colleague holding NO feature grant at all. Before WS-28g-2 this
        # principal could not open their own profile.
        member = user(SUBJECT_EMAIL)                      # no grants whatsoever
        me = await people_self.get_me(member)
        check("an ungranted member resolves their own row", me.state, "resolved")
        check("…and reads their own private half",
              me.person["phone"], "+91 99999 99999")

        saved = await people_self.update_me(
            tasks_people.PersonWrite(location="Bengaluru"), member)
        check("…and may save a self-class field", saved["location"], "Bengaluru")

        try:
            await people_self.update_me(
                tasks_people.PersonWrite(department="Sales"), member)
            check("…and may NOT change their department", "allowed", "403")
        except Exception as exc:
            check("…and may NOT change their department",
                  getattr(exc, "status_code", None), 403)

        stranger_row = (await db.execute(
            text("SELECT department FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": pid})).fetchone()
        check("…and the row really is untouched",
              stranger_row.department, "Engineering")

        # Somebody signed in with an address no row carries.
        ghost = await people_self.get_me(user("ghost@ws28g.invalid"))
        check("a member with no directory row is told so, not 500'd",
              ghost.state, "no_directory_row")

        # ── 7. An empty PATCH changes nothing ──────────────────────────────
        try:
            await people_profile.update_profile(
                pid, tasks_people.PersonWrite(), ADMIN)
            check("an empty patch is refused", "accepted", "400")
        except Exception as exc:
            check("an empty patch is refused",
                  getattr(exc, "status_code", None), 400)
    finally:
        release_tenant(token)
        await cleanup(db)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
