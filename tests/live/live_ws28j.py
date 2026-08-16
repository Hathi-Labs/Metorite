"""WS-28j1 against a REAL Postgres (R8).

The hermetic suite proves the arithmetic and the refusals against a fake, and a
fake agrees with whatever SQL it is handed. What only a database answers:

* the **aggregates** — `count(*) FILTER (…)`, `min(due_at) FILTER (…)`, and
  `count(*)` over a three-way join — actually produce the numbers the rows are
  built from, keyed by `lower(assignee)`;
* the **binds are shapes asyncpg accepts**: a `date` compared against a
  `TIMESTAMPTZ` column, and `make_interval(days => :n)` with a bound integer.
  `CAST(:x AS date)` over a bound string is the shape asyncpg refuses (WS-27k),
  so every new bind on this path is a question a fake cannot answer;
* the **grant closure really excludes** a project the viewer was not granted —
  the control §5.7.5 rests on. A fake returns whatever list it holds no matter
  what clause it is given, so "scoped" is unfalsifiable there;
* the visibility clause composes onto a **`COALESCE(…)` expression** in the
  activity query, not only onto a bare column;
* an **agent assignee** (`agent:<name>`, D-PM-4) survives the whole path and
  arrives with no pill;
* a **second organization's task does not leak** into either viewer. Verified by
  removing the clause and watching the count go 3 → 4 — this cluster has no
  FORCE ROW LEVEL SECURITY, so the WHERE clause is genuinely the only thing
  stopping it, which is the situation the `data:org:read` shortcut would have
  left production in.

Run it::

    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \\
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws28j.py

⚠️ Writes and deletes `gtd_people`, `pm_projects` and their children under
`@ws28j.invalid` / `WS28J`. Scratch only.
"""
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432",
)
sys.path.insert(0, "/home/user/Metorite/apps/services/gateway")

from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, release_tenant
from gateway.db import get_db
from gateway.routes.people import dashboard as people_dashboard
from gateway.routes.tasks import people as tasks_people
from sqlalchemy import text

MINE = "priya@ws28j.invalid"
THEIRS = "ravi@ws28j.invalid"
AGENT = "agent:triage-ws28j"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def user(email, *grants) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = user("admin@ws28j.invalid", "feature:people", "admin:members:manage",
             "admin:members:read", "feature:projects", "data:org:read")
#: A manager who may see the HR tier but holds only ONE project grant. The whole
#: point of check 4: their figures must count that project and nothing else.
SCOPED = user("lead@ws28j.invalid", "feature:people", "admin:members:read",
              "feature:projects")
COLLEAGUE = user("nobody@ws28j.invalid", "feature:people")

NOW = datetime.now(UTC)


async def seed(db, org: str) -> dict:
    """Two projects, two people, an agent, and work with real deadlines.

    ⚠️ **Both viewers need an `app_user` row**, and the reason is the whole
    point of running this against a database at all. `resolve_visibility` reads
    the caller's tenant off `app_user`; a caller the directory does not know
    binds `vis_org = NULL`, and every clause then compares a column to NULL,
    which is never true. So they see nothing — correct fail-closed behaviour
    (`Visibility.organization_id` documents it) and also a way to write a
    scoping assertion that passes because the viewer saw nothing AT ALL.

    Both halves were found here, by this harness, one run apart:

    * the **scoped** viewer without a row reported zero tasks, which a weaker
      assertion than "exactly 2" would have read as proof the closure worked;
    * the **unrestricted** viewer without a row started reporting zero the
      moment `_scope` stopped short-circuiting `data:org:read` to `TRUE` — which
      is the change working, since `project_clause` answers the TENANT subquery
      for that caller rather than `TRUE` (WS-29b), and a tenant of NULL matches
      no project. In production an admin is signed in and has the row; here it
      has to be seeded, and the fact that omitting it turns the page empty
      rather than global is the property being bought.
    """
    for who, name in ((SCOPED.email, "Lead WS28J"), (ADMIN.email, "Admin WS28J")):
        await db.execute(text(
            "INSERT INTO app_user (email, display_name, status, "
            "                      organization_id) "
            "VALUES (:e, :n, 'active', CAST(:org AS uuid))"),
            {"e": who, "n": name, "org": org})
    ids: dict = {}
    for label, granted in (("open", True), ("secret", False)):
        row = (await db.execute(text(
            "INSERT INTO pm_projects (name, task_prefix, created_by, "
            "                         organization_id) "
            "VALUES (:n, :p, 'seed', CAST(:org AS uuid)) RETURNING id"),
            {"n": f"WS28J {label}", "p": f"W{label[0].upper()}", "org": org},
        )).fetchone()
        ids[label] = str(row.id)
        status = (await db.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, category, "
            "                              organization_id) "
            "VALUES (CAST(:p AS uuid), 'Open', 'in_progress', "
            "        CAST(:org AS uuid)) RETURNING id"),
            {"p": ids[label], "org": org})).fetchone()
        ids[f"{label}_status"] = str(status.id)
        if granted:
            # The scoped viewer is granted THIS project only.
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, "
                "                               created_by) "
                "VALUES (CAST(:p AS uuid), :who, 'seed')"),
                {"p": ids[label], "who": SCOPED.email.lower()})
    return ids


async def add_task(db, org, ids, project, *, who, title, due, mins, number):
    row = (await db.execute(text(
        "INSERT INTO pm_tasks (project_id, root_project_id, task_number, "
        "                      status_id, title, estimate_mins, due_at, "
        "                      created_by, organization_id) "
        "VALUES (CAST(:p AS uuid), CAST(:p AS uuid), :n, CAST(:s AS uuid), "
        "        :t, :m, :d, 'seed', CAST(:org AS uuid)) RETURNING id"),
        {"p": ids[project], "s": ids[f"{project}_status"], "n": number,
         "t": title, "m": mins, "d": due, "org": org})).fetchone()
    await db.execute(text(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
        "                               organization_id) "
        "VALUES (CAST(:t AS uuid), :who, 'seed', CAST(:org AS uuid))"),
        {"t": str(row.id), "who": who, "org": org})
    return str(row.id)


async def main() -> None:
    db = await get_db()
    org = (await db.execute(
        text("SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    token = bind_tenant(str(org.id))
    try:
        await cleanup(db)
        await db.commit()

        await tasks_people.create_person(
            tasks_people.PersonWrite(name="Priya WS28J", email=MINE,
                                     department="Engineering"), ADMIN)
        await tasks_people.create_person(
            tasks_people.PersonWrite(name="Ravi WS28J", email=THEIRS,
                                     department="Sales"), ADMIN)

        ids = await seed(db, str(org.id))
        # Priya: one overdue task and one that cannot fit, both in the GRANTED
        # project; plus a big one in the project the scoped viewer cannot see.
        await add_task(db, str(org.id), ids, "open", who=MINE.upper(),
                       title="Overdue thing", due=NOW - timedelta(days=3),
                       mins=6 * 60, number=1)
        await add_task(db, str(org.id), ids, "open", who=MINE,
                       title="Tight firmware thing", due=NOW + timedelta(days=1),
                       mins=30 * 60, number=2)
        await add_task(db, str(org.id), ids, "secret", who=MINE,
                       title="Hidden thing", due=NOW + timedelta(days=2),
                       mins=50 * 60, number=1)
        # No estimate at all, so the hours pills are suppressed for Ravi.
        await add_task(db, str(org.id), ids, "open", who=THEIRS,
                       title="Unestimated", due=None, mins=None, number=3)
        await add_task(db, str(org.id), ids, "open", who=AGENT,
                       title="Bot work", due=NOW + timedelta(days=5),
                       mins=60, number=4)
        await db.execute(text(
            "INSERT INTO pm_activities (task_id, project_id, type, body, "
            "                           created_by, organization_id) "
            "SELECT NULL, CAST(:p AS uuid), 'comment', 'hi', :who, "
            "       CAST(:org AS uuid)"),
            {"p": ids["open"], "who": MINE, "org": str(org.id)})
        await db.commit()

        # ── 1. The gate ────────────────────────────────────────────────────
        try:
            await people_dashboard.get_dashboard(COLLEAGUE)
            check("the dashboard needs admin:members:read", "served", "403")
        except Exception as exc:
            check("the dashboard needs admin:members:read",
                  getattr(exc, "status_code", None), 403)

        # ── 2. The aggregates, over real rows ──────────────────────────────
        out = await people_dashboard.get_dashboard(ADMIN)
        rows = {r.name: r for r in out.rows}
        priya = rows["Priya WS28J"]
        check("the whole roster is there", "Ravi WS28J" in rows, True)
        check("open tasks are counted", priya.open_tasks, 3)
        check("…and their hours summed", priya.committed_hours, 86.0)
        check("the overdue one is found by the FILTER", priya.overdue, 1)
        check("the next deadline is the earliest FUTURE one",
              priya.next_due_at is not None, True)
        check("a missed date is 'behind'", priya.pill, "behind")
        check("…and it says how many", "1 open task past the due date"
              in (priya.reason or ""), True)
        check("both projects are on the row", priya.projects_total, 2)

        # ── 3. The assignee join folds case ────────────────────────────────
        #
        # The overdue task was assigned to the UPPERCASED address. Without
        # `lower(a.assignee)` it would be a fourth row on the dashboard and
        # Priya's week would be missing six hours.
        check("an upper-cased assignee lands on the same person",
              any(r.name == MINE.upper() for r in out.rows), False)

        # ── 4. The grant closure REALLY excludes ───────────────────────────
        scoped = await people_dashboard.get_dashboard(SCOPED)
        srows = {r.name: r for r in scoped.rows}
        check("a scoped viewer counts only what they may open",
              srows["Priya WS28J"].open_tasks, 2)
        check("…and the hidden project's hours are gone",
              srows["Priya WS28J"].committed_hours, 36.0)
        check("…and the hidden project is not named",
              srows["Priya WS28J"].projects_total, 1)
        check("…and the surface SAYS the figures are partial",
              scoped.partial, True)
        check("the unrestricted viewer is not told they are partial",
              out.partial, False)

        # ── 5. `at risk` is the hours before the date, computed live ───────
        risky = srows["Priya WS28J"].at_risk
        check("the tight task is at risk", [t["title"] for t in risky],
              ["Tight firmware thing"])
        check("…and it carries the shortfall, not just a flag",
              risky[0]["shortfall_hours"] > 0, True)
        check("…and its need includes the overdue work",
              risky[0]["needed_hours"], 36.0)

        # ── 6. Missing estimates suppress rather than read as free ─────────
        ravi = rows["Ravi WS28J"]
        check("nothing estimated turns the hours pills off", ravi.hours_basis,
              False)
        check("…and the row says so", "no estimate" in (ravi.note or ""), True)
        check("…and it is not called idle on missing data", ravi.pill,
              "on_track")

        # ── 7. The agent (D-PM-4) ──────────────────────────────────────────
        agent = rows[AGENT]
        check("an agent appears beside the people", agent.kind, "agent")
        check("…holding its work", agent.open_tasks, 1)
        check("…and carrying no pill", agent.pill, None)

        # ── 8. The activity query's COALESCE scope ─────────────────────────
        check("last activity is read through the scoped COALESCE",
              priya.last_activity_at is not None, True)

        # ── 8b. The rollup (WS-28j2) IS the rows, not a second count ───────
        #
        # A fake cannot make this claim interesting: it would agree with any
        # arithmetic. Over real rows it is the §5.9 guarantee — the department
        # figures and the table beneath them are the SAME array.
        by_dept = {d["department"]: d for d in out.departments}
        check("every department in the roster is rolled up",
              sorted(by_dept), ["Engineering", "Sales"])
        check("the org headcount excludes the agent", out.org["headcount"], 2)
        check("…and reports the exclusion", out.org["agents"], 1)
        check("the rollup's contracted total IS the rows' sum",
              out.org["contracted_hours"],
              round(sum(r.contracted_hours for r in out.rows
                        if r.kind != "agent"), 1))
        check("the rollup's committed total IS the rows' sum",
              out.org["committed_hours"],
              round(sum(r.committed_this_week for r in out.rows
                        if r.kind != "agent"), 1))
        check("the pill counts are the rows' pills",
              by_dept["Engineering"]["pills"]["behind"], 1)
        check("the strained department sorts first",
              out.departments[0]["department"], "Engineering")
        check("Ravi is named as having nothing estimated",
              by_dept["Sales"]["unestimated_people"], 1)
        # One person per department here, so a spread is not a spread.
        check("a one-person department reports no spread",
              by_dept["Sales"]["spread"], None)

        # ── 8c. The rebalancing suggestions (WS-28j3), over the same rows ──
        from gateway.person_skills import replace_skills
        from gateway.routes.people import suggestions as sugg
        from acb_common.db import tenant_session as _ts
        ravi_id = (await db.execute(text(
            "SELECT id FROM gtd_people WHERE email = 'ravi@ws28j.invalid'"
        ))).fetchone().id
        async with _ts(str(org.id)) as scoped:
            await replace_skills(scoped, str(ravi_id), [
                {"skill": "firmware", "level": "proficient", "years": 3,
                 "last_used_year": None, "evidence": "manual"}], "seed")
        out_s = await sugg.get_suggestions(ADMIN)
        risky_titles = [x.title for x in out_s.at_risk]
        check("the at-risk task reaches the suggester",
              "Tight firmware thing" in risky_titles, True)
        item = next(x for x in out_s.at_risk
                    if x.title == "Tight firmware thing")
        check("…held by the right person", item.holder["name"], "Priya WS28J")
        check("…with the skilled colleague as candidate",
              [c.name for c in item.candidates], ["Ravi WS28J"])
        cand = item.candidates[0]
        check("…showing all three factors and their product",
              (cand.skill_points, cand.spare_hours > 0,
               cand.rank == round(cand.skill_points * cand.spare_hours, 2)),
              (1.5, True, True))
        check("…and the holder is never their own helper",
              all(c.email != "priya@ws28j.invalid" for c in item.candidates),
              True)

        # ── 9. A SECOND ORGANIZATION does not leak into either viewer ──────
        #
        # The check the whole `_scope` change exists for, and the only one that
        # can fail for the right reason. `data:org:read` is unrestricted WITHIN
        # a tenant, never across them (WS-29b) — and the tempting shortcut
        # (`if vis.unrestricted: return "true"`) makes this endpoint's tenant
        # boundary depend on row-level security being ENFORCED, which is an
        # owner's act and is not on this cluster. So the leak is real here, and
        # the clause is the only thing stopping it.
        other = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES ('ws28j-other', 'WS28J Other') RETURNING id"))).fetchone()
        other_project = (await db.execute(text(
            "INSERT INTO pm_projects (name, task_prefix, created_by, "
            "                         organization_id) "
            "VALUES ('WS28J foreign', 'WF', 'seed', CAST(:o AS uuid)) "
            "RETURNING id"), {"o": str(other.id)})).fetchone()
        other_status = (await db.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, category, "
            "                              organization_id) "
            "VALUES (CAST(:p AS uuid), 'Open', 'in_progress', "
            "        CAST(:o AS uuid)) RETURNING id"),
            {"p": str(other_project.id), "o": str(other.id)})).fetchone()
        await add_task(db, str(other.id),
                       {"x": str(other_project.id),
                        "x_status": str(other_status.id)}, "x",
                       who=MINE, title="Another company's work",
                       due=NOW + timedelta(days=1), mins=99 * 60, number=1)
        await db.commit()

        cross = {r.name: r for r in (await people_dashboard.get_dashboard(
            ADMIN)).rows}["Priya WS28J"]
        check("another organization's task is not counted",
              cross.open_tasks, 3)
        check("…nor are its hours", cross.committed_hours, 86.0)
        check("…nor is its project named", cross.projects_total, 2)
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
        "DELETE FROM pm_projects WHERE name LIKE 'WS28J %'"))
    await db.execute(text(
        "DELETE FROM gtd_people WHERE email LIKE '%@ws28j.invalid'"))
    await db.execute(text(
        "DELETE FROM app_user WHERE email LIKE '%@ws28j.invalid'"))
    await db.execute(text(
        "DELETE FROM organization WHERE slug = 'ws28j-other'"))


asyncio.run(main())
