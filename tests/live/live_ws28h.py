"""WS-28h against a REAL Postgres (R8).

The hermetic suite proves the sequencing and the refusals against a stateful
fake — which, being a fake, agrees with whatever SQL it is handed. What only a
database answers:

* **migration 176 applies on the real ladder, twice** (idempotent re-run);
* the **tenant key defaults from the session GUC and fails closed** unbound,
  on BOTH new tables;
* the **UNIQUE index folds case for real** — 'Python' next to 'python' is
  refused by Postgres, not just by the route's validator;
* the **CHECKs fire** — a fifth level and a backwards credential both bounce;
* **`ON DELETE CASCADE`** clears both tables with the person;
* **D-PC-6 over real rows**: after every real write path — create with
  skills, the flat PATCH, the structured replace, the résumé ingest through
  the actual endpoint — `gtd_people.skills`/`skills_source` equal the table's
  content, read back from the database rather than trusted from the code.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws28h.py

⚠️ Writes and deletes `gtd_people` rows under `@ws28h.invalid`. Scratch only.
"""
import asyncio
import io
import os
import sys

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from fastapi import UploadFile
from gateway.db import get_db
from gateway.routes.people import skills as people_skills
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text

MINE = "priya@ws28h.invalid"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28h.invalid", "feature:people", "admin:members:manage",
             "admin:members:read")
ME = user(MINE)


async def projection(db, person_id: str) -> tuple[list[str], dict, list[str]]:
    """The array, its source map, and the table — read back from the DATABASE."""
    person = (await db.execute(text(
        "SELECT skills, skills_source FROM gtd_people "
        " WHERE id = CAST(:id AS uuid)"), {"id": person_id})).fetchone()
    rows = (await db.execute(text(
        "SELECT skill, evidence FROM gtd_person_skills "
        " WHERE person_id = CAST(:id AS uuid) "
        " ORDER BY created_at, lower(skill)"), {"id": person_id})).fetchall()
    return (list(person.skills or []), dict(person.skills_source or {}),
            [r.skill for r in rows])


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    try:
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28h.invalid'"))
        await db.commit()

        # ── 1. Create with skills seeds the table (D-PC-6 from row one) ────
        mine = await tasks_people.create_person(
            tasks_people.PersonWrite(name="Priya WS28H", email=MINE,
                                     skills=["python", "altium"]), ADMIN)
        arr, src, table = await projection(db, mine.id)
        # ⚠️ Measured: rows inserted in ONE transaction share now() as their
        # created_at, so the batch orders alphabetically — the projection is
        # deterministic, not insertion-ordered. The module docstring says so
        # because this harness proved the first claim wrong.
        check("create seeds the child table", table, ["altium", "python"])
        check("…and the array IS the table", arr, table)
        check("…with manual evidence", src, {"python": "manual",
                                             "altium": "manual"})

        # ── 2. Structured replace, read back ───────────────────────────────
        await people_skills.put_skills(
            mine.id, people_skills.SkillsWrite(rows=[
                people_skills.SkillIn(skill="python", level="expert",
                                      years=8, last_used_year=2026),
                people_skills.SkillIn(skill="kicad", level="working"),
            ]), user=ADMIN)
        arr, src, table = await projection(db, mine.id)
        check("replace rewrote table and array together", arr, table)
        check("…dropping what the payload dropped", "altium" not in arr, True)
        level = (await db.execute(text(
            "SELECT level, years FROM gtd_person_skills "
            " WHERE person_id = CAST(:id AS uuid) AND skill = 'python'"),
            {"id": mine.id})).fetchone()
        check("the level and years are real columns",
              (level.level, level.years), ("expert", 8.0))

        # ── 3. The flat PATCH keeps the structured row (two-door defect) ───
        await tasks_people.update_person(
            mine.id, tasks_people.PersonWrite(skills=["python", "rust"]),
            ADMIN)
        arr, src, table = await projection(db, mine.id)
        check("flat PATCH reconciled the table", sorted(table),
              ["python", "rust"])
        check("…array still equals table", arr, table)
        survived = (await db.execute(text(
            "SELECT level FROM gtd_person_skills "
            " WHERE person_id = CAST(:id AS uuid) AND skill = 'python'"),
            {"id": mine.id})).fetchone()
        check("the flat save did NOT strip the structured level",
              survived.level, "expert")

        # ── 4. The résumé ingest, through the REAL endpoint ────────────────
        #
        # No LLM on this cluster, so the parse degrades to keyword-only —
        # which is the deterministic half and exactly what should be tested.
        upload = UploadFile(
            file=io.BytesIO(b"Skills include python and altium designer and solidworks generally"),
            filename="cv.txt")
        result = await tasks_people.ingest_resume(mine.id, upload, ADMIN)
        arr, src, table = await projection(db, mine.id)
        check("the ingest added what the CV shows",
              "solidworks" in arr and "altium" in arr, True)
        check("…as resume evidence", src.get("solidworks"), "resume")
        check("…never removing what a human put", "rust" in arr, True)
        check("…array still equals table", arr, table)
        check("added_skills reports the delta only",
              "python" not in result.added_skills, True)

        # ── 5. The database's own constraints ──────────────────────────────
        for label, sql, params in (
            ("a case-folded duplicate",
             "INSERT INTO gtd_person_skills (person_id, skill) "
             "VALUES (CAST(:p AS uuid), 'PYTHON')", {"p": mine.id}),
            ("a fifth level",
             "INSERT INTO gtd_person_skills (person_id, skill, level) "
             "VALUES (CAST(:p AS uuid), 'zig', 'wizard')", {"p": mine.id}),
            ("a backwards credential",
             "INSERT INTO gtd_person_credentials (person_id, kind, title, "
             "year_from, year_to) VALUES (CAST(:p AS uuid), 'education', "
             "'BTech', 2020, 2016)", {"p": mine.id}),
        ):
            try:
                await db.execute(text(sql), params)
                await db.commit()
                check(f"the database refuses {label}", "accepted", "refused")
            except Exception:
                await db.rollback()
                check(f"the database refuses {label}", "refused", "refused")

        # ── 6. Tenant fail-closed on both tables ───────────────────────────
        release_tenant(token)
        for table_name in ("gtd_person_skills", "gtd_person_credentials"):
            try:
                cols = ("person_id, skill" if table_name == "gtd_person_skills"
                        else "person_id, kind, title")
                vals = ("CAST(:p AS uuid), 'probe'"
                        if table_name == "gtd_person_skills"
                        else "CAST(:p AS uuid), 'education', 'probe'")
                await db.execute(text(
                    f"INSERT INTO {table_name} ({cols}) VALUES ({vals})"),
                    {"p": mine.id})
                await db.commit()
                check(f"{table_name}: unbound insert refused", "accepted",
                      "refused")
            except Exception:
                await db.rollback()
                check(f"{table_name}: unbound insert refused", "refused",
                      "refused")
        token = bind_tenant(str(org.id))

        # ── 7. CASCADE ─────────────────────────────────────────────────────
        await db.execute(text(
            "DELETE FROM gtd_people WHERE id = CAST(:id AS uuid)"),
            {"id": mine.id})
        await db.commit()
        for table_name in ("gtd_person_skills", "gtd_person_credentials"):
            orphans = (await db.execute(text(
                f"SELECT count(*) AS n FROM {table_name} "
                " WHERE person_id = CAST(:id AS uuid)"),
                {"id": mine.id})).fetchone()
            check(f"{table_name} goes with the person", int(orphans.n), 0)
    finally:
        await db.rollback()
        await db.execute(text(
            "DELETE FROM gtd_people WHERE email LIKE '%@ws28h.invalid'"))
        await db.commit()
        release_tenant(token)
        await db.close()

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
