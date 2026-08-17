"""WS-28d against a REAL Postgres (R8).

What only a database answers here:

* the résumé signal's ``ILIKE`` + ``DISTINCT ON (person_id) … ORDER BY
  uploaded_at DESC`` really returns one row per person, the NEWEST — a fake
  cannot tell DISTINCT ON from DISTINCT;
* the whole path composes over real rows: structured skills (WS-28h's table),
  the CV text, the roster read, the availability lookup — one search, every
  signal live;
* the semantic signal degrades to ABSENT (and says so) on a cluster with no
  embeddings, rather than erroring the search.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws28d.py

⚠️ Writes and deletes `gtd_people` rows under `@ws28d.invalid`. Scratch only.
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
from gateway.person_skills import replace_skills
from gateway.routes.people import search as people_search
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


HR = user("hr@ws28d.invalid", "feature:people", "admin:members:read",
          "admin:members:manage")
COLLEAGUE = user("nobody@ws28d.invalid", "feature:people")


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    try:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28d.invalid'"))
        await db.commit()

        priya = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Priya WS28D",
                                     email="priya@ws28d.invalid",
                                     domain="firmware"), HR)
        await tasks_people.create_person(
            tasks_people.PersonWrite(name="Ravi WS28D",
                                     email="ravi@ws28d.invalid",
                                     skills=["firmware"]), HR)
        # ⚠️ Seeding goes through `tenant_session`, not the raw `get_db()`
        # session: the raw one never binds the GUC, the tenant defaults NULL
        # and the NOT NULL refuses — the fail-closed property live_ws28k
        # proved, correctly biting the harness that forgot it.
        async with tenant_session(str(org.id)) as scoped:
            await replace_skills(scoped, priya.id, [
                {"skill": "firmware", "level": "expert", "years": 6,
                 "last_used_year": 2026, "evidence": "manual"},
                {"skill": "altium", "level": "working", "years": None,
                 "last_used_year": None, "evidence": "manual"},
            ], "seed")
            # Two CVs for Priya — DISTINCT ON must pick the NEWER.
            for age_days, line in (
                    (30, "Old CV: once touched firmware."),
                    (1, "New CV: shipped extruder firmware at Acme.")):
                await scoped.execute(text(
                    "INSERT INTO gtd_person_resumes "
                    "  (person_id, filename, parsed_text, uploaded_by, "
                    "   uploaded_at) "
                    "VALUES (CAST(:p AS uuid), 'cv.txt', :t, 'seed', "
                    "        now() - make_interval(days => :age))"),
                    {"p": priya.id, "t": line, "age": age_days})
            # An absence covering this week, for the availability line.
            await scoped.execute(text(
                "INSERT INTO gtd_person_absences "
                "  (person_id, starts_on, ends_on, kind, created_by) "
                "VALUES (CAST(:p AS uuid), :s, :e, 'holiday', 'seed')"),
                {"p": priya.id, "s": date.today(),
                 "e": date.today() + timedelta(days=2)})

        # ── 1. The gate ────────────────────────────────────────────────────
        try:
            await people_search.search_people("firmware", COLLEAGUE)
            check("the search needs admin:members:read", "served", "403")
        except Exception as exc:
            check("the search needs admin:members:read",
                  getattr(exc, "status_code", None), 403)

        # ── 2. Every signal, over real rows ────────────────────────────────
        out = await people_search.search_people("extruder firmware", HR)
        rows = {r.name: r for r in out.rows}
        check("both matches are found", sorted(rows),
              ["Priya WS28D", "Ravi WS28D"])
        p = rows["Priya WS28D"]
        kinds = {s["kind"] for s in p.signals}
        check("skill + domain + resume signals fire", kinds,
              {"skill", "domain", "resume"})
        quote = next(s for s in p.signals if s["kind"] == "resume")
        check("the quote is from the NEWEST CV (DISTINCT ON works)",
              "shipped extruder firmware" in quote["quote"].lower(), True)
        check("the score is the sum of the shown parts",
              p.score, round(sum(s["points"] for s in p.signals), 2))
        check("the expert outranks the bare hit",
              next(r.name for r in out.rows), "Priya WS28D")
        check("…and the availability warning travels",
              any("Away" in w for w in p.warnings), True)
        check("semantic is reported absent, not silent",
              out.semantic_available, False)

        # ── 3. Ravi's flat-only skill still matches (the projection's table
        #      row from create_person, WS-28h) ─────────────────────────────
        r = rows["Ravi WS28D"]
        check("a flat-created skill matches through the table",
              [s["kind"] for s in r.signals], ["skill"])
        check("…at plain weight", r.score, 1.0)

        # ── 4. Nobody matching is empty, not the roster ────────────────────
        none = await people_search.search_people("juggling", HR)
        check("no match is an empty list", none.total, 0)
    finally:
        await db.rollback()
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28d.invalid'"))
        await db.commit()
        release_tenant(token)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
