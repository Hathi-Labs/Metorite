"""WS-28m + WS-28l against a real Postgres (R8) — coverage, quality, landing.

Run:
    uv run python tests/live/live_ws28ml.py

⚠️ Writes and deletes `gtd_people`, `app_user` and `pm_projects` rows under
`@ws28ml.invalid` / `WS28ML`. Scratch only.

What only a live run can show here:

* the roster/skills/tasks queries against the real schema (the fake agrees
  with whatever SQL it is handed — R8's whole point);
* `gtd_people_status_check` is VALIDATED on this ladder, so a bad status
  cannot even be seeded — the hermetic suite covers the legacy-row branch and
  the live one proves the door is shut for new writes;
* the §5.9 agreement, measured: the landing's quality counts and the §5.10
  panel's counts are THE SAME numbers from THE SAME function, and the load
  half is the dashboard's own rollup passed through.
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
from acb_common.db import bind_tenant, tenant_session
from gateway.db import get_db
from gateway.routes.people import overview as overview_mod
from gateway.routes.people import quality as quality_mod
from gateway.routes.people.dashboard import get_dashboard
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


ADMIN = user("admin@ws28ml.invalid", "feature:people", "admin:members:manage",
             "admin:members:read", "feature:projects", "data:org:read")
COLLEAGUE = user("nobody@ws28ml.invalid", "feature:people")

NOW = datetime.now(UTC)


async def person(body: dict) -> str:
    out = await tasks_people.create_person(
        tasks_people.PersonWrite(**body), ADMIN)
    return str(out.id)


async def main() -> None:
    db = await get_db()
    org = (await db.execute(text(
        "SELECT id FROM organization ORDER BY created_at LIMIT 1"))).fetchone()
    bind_tenant(str(org.id))
    try:
        await cleanup(db)
        await db.commit()

        # The admin needs an `app_user` row: the unused-skill scan goes through
        # `resolve_visibility`, and a caller the directory does not know binds
        # `vis_org = NULL` — fail-closed, and an empty scan (the ws28j lesson).
        await db.execute(text(
            "INSERT INTO app_user (email, display_name, status, "
            "                      organization_id) "
            "VALUES (:e, 'Admin WS28ML', 'active', CAST(:org AS uuid))"),
            {"e": ADMIN.email, "org": str(org.id)})
        await db.commit()

        # ── The roster §5.10 will read ──────────────────────────────────────
        asha = await person({
            "name": "Asha WS28ML", "email": "asha@ws28ml.invalid",
            "department": "R&D", "title": "CTO",
            "timezone": "Asia/Kolkata",
            "working_hours": {"start": "09:30", "end": "18:30"},
            "skills": ["python"]})
        await person({
            "name": "Priya WS28ML", "email": "priya@ws28ml.invalid",
            "department": "R&D", "title": "Senior Firmware Engineer",
            "manager_id": asha, "timezone": "Asia/Kolkata",
            "working_hours": {"start": "09:30", "end": "18:30"},
            "skills": ["marlin"]})
        await person({
            "name": "Ravi WS28ML", "email": "ravi@ws28ml.invalid",
            "department": "Sales", "manager_id": asha,
            "working_hours": {"start": "09:30", "end": "18:30"},
            "skills": ["python"]})  # no timezone → an AI-relevant gap
        await person({
            "name": "NoMail WS28ML", "department": "Sales",
            "manager_id": asha, "timezone": "Asia/Kolkata",
            "working_hours": {"start": "09:30"}, "skills": ["python"]})
        gone = await person({
            "name": "Gone WS28ML", "email": "gone@ws28ml.invalid",
            "department": "R&D", "status": "alumni", "skills": ["cobol"]})
        await person({
            "name": "Dev WS28ML", "email": "dev@ws28ml.invalid",
            "department": "R&D", "manager_id": gone,
            "timezone": "Asia/Kolkata", "working_hours": {"start": "09:30"},
            "skills": ["python"]})
        # Migration 148's quarantine, reproduced the way 148 makes it: the
        # address moved aside, the column left NULL.
        quarantined = await person({
            "name": "Quarantined WS28ML", "department": "Sales",
            "manager_id": asha, "timezone": "Asia/Kolkata",
            "working_hours": {"start": "09:30"}, "skills": ["python"]})
        await db.execute(text(
            "UPDATE gtd_people SET email_conflict = 'dupe@ws28ml.invalid' "
            " WHERE id = CAST(:id AS uuid)"), {"id": quarantined})
        # A NULL status is reachable (49 has no NOT NULL, 148's CHECK passes
        # NULL) and must tell ONE story across the three surfaces.
        await db.execute(text(
            "UPDATE gtd_people SET status = NULL "
            " WHERE id = CAST(:id AS uuid)"), {"id": quarantined})
        await db.commit()

        # ── 1. The status door really is shut (the CHECK is VALIDATED) ─────
        try:
            await person({"name": "Bad WS28ML", "status": "on sabbatical"})
            check("a status outside the vocabulary is refused", "accepted",
                  "refused")
        except Exception:
            check("a status outside the vocabulary is refused", "refused",
                  "refused")

        # ── 2. One task, so 'python' is used and 'marlin' is not ───────────
        proj = (await db.execute(text(
            "INSERT INTO pm_projects (name, task_prefix, created_by, "
            "                         organization_id) "
            "VALUES ('WS28ML proj', 'WM', 'seed', CAST(:org AS uuid)) "
            "RETURNING id"), {"org": str(org.id)})).fetchone()
        status = (await db.execute(text(
            "INSERT INTO pm_task_statuses (project_id, name, category, "
            "                              organization_id) "
            "VALUES (CAST(:p AS uuid), 'Open', 'in_progress', "
            "        CAST(:org AS uuid)) RETURNING id"),
            {"p": str(proj.id), "org": str(org.id)})).fetchone()
        await db.execute(text(
            "INSERT INTO pm_tasks (project_id, root_project_id, task_number, "
            "                      status_id, title, created_by, "
            "                      organization_id) "
            "VALUES (CAST(:p AS uuid), CAST(:p AS uuid), 1, "
            "        CAST(:s AS uuid), 'Fix the python build', 'seed', "
            "        CAST(:org AS uuid))"),
            {"p": str(proj.id), "s": str(status.id), "org": str(org.id)})
        await db.commit()

        # ── 3. §5.10 over the real schema ───────────────────────────────────
        out = await quality_mod.get_quality(user=ADMIN)
        check("bus factor of one names marlin and its only holder",
              [(s.skill, s.person.name) for s in out.coverage.single_holder],
              [("marlin", "Priya WS28ML")])
        firmware = [t for t in out.coverage.title_terms
                    if t.term == "firmware"]
        check("'firmware' is in a title and nobody's skills",
              [t.people for t in firmware], [["Priya WS28ML"]])
        check("the CTO title term is not reported for the alumni-only skill",
              any(t.term == "cobol" for t in out.coverage.title_terms), False)
        unused = {u.skill for u in out.coverage.unused_skills}
        check("'marlin' is declared and never on a task",
              "marlin" in unused, True)
        check("'python' is on a task, so it is not unused",
              "python" in unused, False)
        check("the scan states its basis", out.coverage.tasks_scanned, 1)
        check("no email lists the unreachable, not the quarantined",
              [p.name for p in out.quality.no_email], ["NoMail WS28ML"])
        check("148's quarantine is surfaced",
              [(c.name, c.email_conflict) for c in out.quality.email_conflict],
              [("Quarantined WS28ML", "dupe@ws28ml.invalid")])
        check("a NULL status is listed as (none), not hidden or blank",
              [(r.name, r.status) for r in out.quality.bad_status],
              [("Quarantined WS28ML", "(none)")])
        check("a manager who left is listed",
              [(r.name, r.manager_name) for r in out.quality.manager_alumni],
              [("Dev WS28ML", "Gone WS28ML")])
        check("the unmanaged root is the CTO",
              [p.name for p in out.quality.no_manager], ["Asha WS28ML"])
        check("the AI-relevant gap names its field",
              [(r.name, r.missing) for r in out.quality.missing_ai_fields],
              [("Ravi WS28ML", ["timezone"])])

        # ── 4. §5.9 — the landing agrees because it cannot disagree ────────
        land = await overview_mod.get_overview(user=ADMIN)
        check("landing quality counts ARE the §5.10 counts",
              land.quality_counts, out.counts)
        board = await get_dashboard(ADMIN)
        check("landing org rollup IS the dashboard's",
              land.org == board.org, True)
        check("headcount counts alumni too",
              {(r.department, r.status): r.count for r in land.headcount}[
                  ("R&D", "alumni")], 1)
        check("headcount shows a NULL status as its own bucket, not active",
              {(r.department, r.status): r.count for r in land.headcount}[
                  ("Sales", "(none)")], 1)
        check("total people", land.total_people, 7)
        check("the landing's roots are §5.10's no_manager list",
              [r["name"] for r in land.roots], ["Asha WS28ML"])

        # ── 4b. The org chart (WS-28c) over the same roster ────────────────
        from gateway.routes.people import chart as chart_mod
        grp = (await db.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name, "
            "                       created_by) "
            "VALUES (CAST(:org AS uuid), 'ws28ml-sales', 'WS28ML Sales', "
            "        'seed') RETURNING id"), {"org": str(org.id)})).fetchone()
        ravi_user = (await db.execute(text(
            "INSERT INTO app_user (email, display_name, status, "
            "                      organization_id) "
            "VALUES ('ravi@ws28ml.invalid', 'Ravi WS28ML', 'active', "
            "        CAST(:org AS uuid)) RETURNING id"),
            {"org": str(org.id)})).fetchone()
        await db.execute(text(
            "INSERT INTO org_group_member (group_id, user_id, added_by) "
            "VALUES (CAST(:g AS uuid), CAST(:u AS uuid), 'seed')"),
            {"g": str(grp.id), "u": str(ravi_user.id)})
        await db.commit()
        # Another tenant's identically-shaped group must NOT reach the legend:
        # org_group is EXEMPT from generated RLS, so the predicate is the
        # endpoint's own — measured here, since the fake cannot see it.
        other = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES ('ws28ml-other', 'WS28ML Other') RETURNING id"))).fetchone()
        await db.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name, "
            "                       created_by) "
            "VALUES (CAST(:org AS uuid), 'ws28ml-leak', 'Leaked', 'seed')"),
            {"org": str(other.id)})
        await db.commit()
        chart = await chart_mod.get_chart(user=ADMIN)
        slugs = [g.slug for g in chart.groups]
        check("our group is on the legend", "ws28ml-sales" in slugs, True)
        check("another tenant's group is NOT", "ws28ml-leak" in slugs, False)
        ours = {n.name: n for n in chart.nodes if n.name.endswith("WS28ML")}
        check("alumni are off the chart", "Gone WS28ML" in ours, False)
        check("a NULL-status row stays on the chart",
              "Quarantined WS28ML" in ours, True)
        check("a manager who left resolves to no manager — a visible root",
              ours["Dev WS28ML"].manager_id, None)
        check("a managed person keeps their manager",
              ours["Priya WS28ML"].manager_id, asha)
        check("the group overlay joins through app_user on lowered email",
              ours["Ravi WS28ML"].groups, ["ws28ml-sales"])
        check("the chart says whether this caller may re-parent",
              chart.can_manage, True)

        # ── 5. The gate is the whole surface ────────────────────────────────
        for label, call in (("quality", quality_mod.get_quality),
                            ("overview", overview_mod.get_overview)):
            try:
                await call(user=COLLEAGUE)
                check(f"{label} refuses without admin:members:read",
                      "answered", "403")
            except Exception as exc:
                check(f"{label} refuses without admin:members:read",
                      getattr(exc, "status_code", None), 403)
    finally:
        await cleanup(db)
        await db.commit()

    print(f"\n{len(failures)} failures" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)


async def cleanup(db) -> None:
    await db.execute(text("DELETE FROM pm_projects WHERE name LIKE 'WS28ML%'"))
    await db.execute(text(
        "DELETE FROM gtd_people WHERE name LIKE '%WS28ML'"))
    await db.execute(text(
        "DELETE FROM org_group WHERE slug LIKE 'ws28ml-%'"))
    await db.execute(text(
        "DELETE FROM app_user WHERE email LIKE '%@ws28ml.invalid'"))
    await db.execute(text(
        "DELETE FROM organization WHERE slug = 'ws28ml-other'"))


if __name__ == "__main__":
    asyncio.run(main())
