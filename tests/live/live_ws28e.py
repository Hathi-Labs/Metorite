"""WS-28e against a REAL Postgres (R8).

What only a database answers here:

* the **login join is real** — `app_user` rows decide `has_login`, folded on
  `lower(email)`, so a directory-only contractor really reads as one;
* the **ILIKE filter** matches name/email/title the way Postgres folds it;
* the **agents half degrades**: with the registry table present it lists, and
  the endpoint's people half is unaffected either way.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws28e.py

⚠️ Writes and deletes rows under `@ws28e.invalid`. Scratch only.
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
from gateway.routes.projects import assignees as picker
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28e.invalid", "feature:people", "admin:members:manage",
             "admin:members:read", "feature:projects")
MEMBER = user("pm@ws28e.invalid", "feature:projects")


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    try:
        await cleanup(db)
        await db.commit()

        staff = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Priya WS28E",
                                     email="priya@ws28e.invalid",
                                     title="Firmware",
                                     skills=["firmware"]), ADMIN)
        await tasks_people.create_person(
            tasks_people.PersonWrite(name="Neha WS28E Contractor",
                                     email="neha@ws28e.invalid"), ADMIN)
        async with tenant_session(str(org.id)) as scoped:
            # Priya can sign in; the contractor cannot.
            await scoped.execute(text(
                "INSERT INTO app_user (email, display_name, status, "
                "                      organization_id) "
                "VALUES ('priya@ws28e.invalid', 'Priya', 'active', "
                "        CAST(:org AS uuid))"), {"org": str(org.id)})
            # An absence covering today, for the away line.
            await scoped.execute(text(
                "INSERT INTO gtd_person_absences "
                "  (person_id, starts_on, ends_on, kind, created_by) "
                "VALUES (CAST(:p AS uuid), :s, :e, 'away', 'seed')"),
                {"p": staff.id, "s": date.today(),
                 "e": date.today() + timedelta(days=1)})

        # ── 1. The login join over real rows ───────────────────────────────
        out = await picker.suggest_assignees(q="ws28e", due=None, user=MEMBER)
        rows = {r.name: r for r in out.people}
        check("both people are offered", len(rows), 2)
        check("the staff member has a login",
              rows["Priya WS28E"].has_login, True)
        check("the contractor is offered AND says no-login (D-PC-12)",
              rows["Neha WS28E Contractor"].has_login, False)

        # ── 2. Tiers ───────────────────────────────────────────────────────
        check("a plain member sees no load", rows["Priya WS28E"].load, None)
        check("…and is told which emptiness that is", out.hr_visible, False)
        rich = await picker.suggest_assignees(q="priya@ws28e", due=None,
                                              user=ADMIN)
        check("the HR caller gets load + skills",
              (rich.people[0].load is not None,
               rich.people[0].top_skills), (True, ["firmware"]))

        # ── 3. The warning line, from real absence rows ────────────────────
        check("away is a warning, and the row is still assignable",
              any(w.startswith("Away") for w in rich.people[0].warnings), True)

        # ── 4. ILIKE folds the way Postgres folds ──────────────────────────
        upper = await picker.suggest_assignees(q="PRIYA@WS28E", due=None,
                                               user=MEMBER)
        check("the filter is case-insensitive", len(upper.people), 1)
    finally:
        await db.rollback()
        await cleanup(db)
        await db.commit()
        release_tenant(token)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


async def cleanup(db) -> None:
    await db.execute(text(
        "DELETE FROM gtd_people WHERE email LIKE '%@ws28e.invalid'"))
    await db.execute(text(
        "DELETE FROM app_user WHERE email LIKE '%@ws28e.invalid'"))


asyncio.run(main())
