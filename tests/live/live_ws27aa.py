"""WS-27aa against a REAL Postgres. Two organizations, the real background paths.

The two tenancy residues the Projects app owned:

* ``projects/automation.run_lifecycle_sweep`` — a SCHEDULED sweep that used to
  read ``SELECT * FROM pm_projects WHERE parent_project_id IS NULL`` with no
  tenant predicate, i.e. one customer's schedule archiving and closing
  everybody's work;
* ``projects/agent_dispatch.on_event`` — an event sink that ran on the unbound
  ``get_db()`` because a sink must not inherit an ambient tenant.

What only a database can answer, and why a fake cannot:

* does ``AND organization_id = CAST(:org AS uuid)`` bind a Python ``str`` on
  ``pm_projects`` without asyncpg complaining about the inferred type? (The
  fake has no type system; WS-27r and WS-27k were both real CAST defects that
  a green hermetic suite shipped.)
* does the owner join — ``FROM workflows w JOIN app_user au ON lower(au.email)
  = lower(w.owner_email) WHERE w.id = CAST(:wid AS uuid)`` — actually resolve,
  on a ``workflows`` table that carries **no** ``organization_id`` column?
* does ``tenant_session(org)``'s ``SET LOCAL app.tenant_id`` survive the whole
  sweep transaction (it is a no-op outside one, and a silent one)?
* and the whole point: with two organizations holding IDENTICALLY stale tasks
  under IDENTICAL policies, does a sweep bound to A leave B's rows alone, and
  does a dispatch bound to A write an activity row stamped A?

Run:
    su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D <datadir> \
        -o '-k /var/tmp -p 55432' start"
    uv run python tests/live/live_ws27aa.py

⚠️ ``seed()`` TRUNCATEs ``pm_projects CASCADE``. Scratch databases only.
"""
import asyncio
import os
import pathlib
import sys

os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://postgres@/cc?host=/var/tmp&port=55432"
)
# ⚠️ Derived from THIS file, not hard-coded like the older harnesses: run from
# a git worktree, an absolute `/home/user/Metorite/...` path silently
# imports the MAIN checkout's gateway and the script then reports on code that
# is not the code under test. Measured, not theorised — it happened here first.
sys.path.insert(0, str(
    pathlib.Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway"
))

from acb_common.db import TenantUnbound, current_tenant
from gateway.db import get_db
from gateway.routes.projects import agent_dispatch
from gateway.routes.projects.automation import run_lifecycle_sweep
from gateway.routes.workflows import service as wf_service
from sqlalchemy import text

ANA = "ana@alpha.example"      # owns the alpha workflow
BEN = "ben@beta.example"       # owns the beta workflow
ORPHAN = "orphan@alpha.example"  # an app_user row with NO organization

A_PROJ = "aaaaaaaa-0000-4000-8000-0000000000a1"
B_PROJ = "bbbbbbbb-0000-4000-8000-0000000000b1"
A_DONE = "aaaaaaaa-0000-4000-8000-0000000000a2"
B_DONE = "bbbbbbbb-0000-4000-8000-0000000000b2"
A_TASK = "aaaaaaaa-0000-4000-8000-0000000000a3"
B_TASK = "bbbbbbbb-0000-4000-8000-0000000000b3"
A_AGENT_TASK = "aaaaaaaa-0000-4000-8000-0000000000a4"

A_WF = "aaaaaaaa-0000-4000-8000-0000000000af"
B_WF = "bbbbbbbb-0000-4000-8000-0000000000bf"
ORPHAN_WF = "cccccccc-0000-4000-8000-0000000000cf"

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


async def seed():
    db = await get_db()
    try:
        await db.execute(text("TRUNCATE pm_projects CASCADE"))
        await db.execute(
            text("DELETE FROM workflows WHERE id = ANY(CAST(:w AS uuid[]))"),
            {"w": [A_WF, B_WF, ORPHAN_WF]},
        )
        await db.execute(
            text("DELETE FROM app_user WHERE email = ANY(:e)"),
            {"e": [ANA, BEN, ORPHAN]},
        )
        await db.execute(
            text("DELETE FROM organization WHERE slug IN ('alpha', 'beta')")
        )
        orgs = {}
        for slug in ("alpha", "beta"):
            row = (await db.execute(text(
                "INSERT INTO organization (slug, display_name) "
                "VALUES (:s, :s) RETURNING id"), {"s": slug})).fetchone()
            orgs[slug] = str(row.id)
        # ⚠️ `role` is CHECK-constrained; 'employee' is in the vocabulary.
        for email, slug in ((ANA, "alpha"), (BEN, "beta")):
            await db.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": orgs[slug]})
        # The refusal fixture: a real person with NO organization.
        await db.execute(text(
            "INSERT INTO app_user (email, display_name, role, status) "
            "VALUES (:e, :e, 'employee', 'active')"), {"e": ORPHAN})

        # Two ROOT projects with IDENTICAL lifecycle policies. Anything that
        # tells them apart afterwards can only be the tenant.
        for pid, name, slug in (
            (A_PROJ, "Alpha delivery", "alpha"),
            (B_PROJ, "Beta delivery", "beta"),
        ):
            await db.execute(text(
                "INSERT INTO pm_projects (id, name, source, created_by, "
                "organization_id, archive_after_months, close_after_months) "
                "VALUES (CAST(:p AS uuid), :n, 'manual', :who, "
                "CAST(:o AS uuid), 1, 2)"),
                {"p": pid, "n": name, "who": ANA, "o": orgs[slug]})
        for sid, pid in ((A_DONE, A_PROJ), (B_DONE, B_PROJ)):
            # ⚠️ organization_id deliberately NOT supplied — migration 161's
            # trigger must derive it from the project.
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, "
                "category, is_default) VALUES (CAST(:s AS uuid), "
                "CAST(:p AS uuid), 'Shipped', 10, 'done', true)"),
                {"s": sid, "p": pid})
        # IDENTICALLY stale: both closed five years ago.
        for tid, pid, sid, title in (
            (A_TASK, A_PROJ, A_DONE, "Alpha, shipped long ago"),
            (B_TASK, B_PROJ, B_DONE, "Beta, shipped long ago"),
        ):
            await db.execute(text(
                "INSERT INTO pm_tasks (id, project_id, root_project_id, "
                "status_id, title, task_number, created_by, updated_at) VALUES "
                "(CAST(:t AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
                "CAST(:s AS uuid), :ti, 1, :who, now() - interval '5 years')"),
                {"t": tid, "p": pid, "s": sid, "ti": title, "who": ANA})
        # A fresh alpha task for the dispatch half.
        await db.execute(text(
            "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
            "title, task_number, created_by) VALUES (CAST(:t AS uuid), "
            "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), "
            "'Research the thing', 2, :who)"),
            {"t": A_AGENT_TASK, "p": A_PROJ, "s": A_DONE, "who": ANA})

        for wid, owner in (
            (A_WF, ANA), (B_WF, BEN), (ORPHAN_WF, ORPHAN),
        ):
            await db.execute(text(
                "INSERT INTO workflows (id, name, owner_email, status) VALUES "
                "(CAST(:w AS uuid), 'Nightly lifecycle', :o, 'published')"),
                {"w": wid, "o": owner})
        await db.commit()
        return orgs
    finally:
        await db.close()


async def archived(task_id: str) -> bool:
    db = await get_db()
    try:
        row = (await db.execute(text(
            "SELECT archived_at FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
            {"t": task_id})).fetchone()
        return row is not None and row.archived_at is not None
    finally:
        await db.close()


async def activities(task_id: str) -> list:
    db = await get_db()
    try:
        return list((await db.execute(text(
            "SELECT type, created_by, organization_id FROM pm_activities "
            "WHERE task_id = CAST(:t AS uuid) ORDER BY created_at, id"),
            {"t": task_id})).fetchall())
    finally:
        await db.close()


async def resolution_checks(orgs):
    """The owner join, on a `workflows` table with no `organization_id`."""
    db = await get_db()
    try:
        check("owner join resolves alpha",
              await wf_service._workflow_organization(db, A_WF), orgs["alpha"])
        check("owner join resolves beta",
              await wf_service._workflow_organization(db, B_WF), orgs["beta"])
        # No `organization_id` column on `workflows` — the reason the tenant
        # comes from the owner at all. Asserted against the live catalog so
        # this script fails LOUDLY the day H3 phase 1 adds it, which is the
        # day `_workflow_organization` should become a column read.
        present = (await db.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name = "
            "'workflows' AND column_name = 'organization_id'"))).fetchone()
        check("workflows still has no organization_id column",
              present is None, True)
        for label, wid in (
            ("owner with no organization", ORPHAN_WF),
            ("workflow that does not exist", "dddddddd-0000-4000-8000-00000000000d"),
        ):
            try:
                await wf_service._workflow_organization(db, wid)
                check(f"refuses: {label}", "no exception", "NodeExecutionError")
            except Exception as exc:
                check(f"refuses: {label}", type(exc).__name__,
                      "NodeExecutionError")
    finally:
        await db.close()


async def sweep_checks(orgs):
    """Two orgs, identical policies, identically stale tasks."""
    # Alpha's scheduled workflow runs the REAL closure — resolution, binding
    # and sweep, exactly as the engine node reaches it.
    result = await wf_service._pm_lifecycle_sweeper(A_WF)()
    check("alpha sweep touched one project", result["projects"], 1)
    check("alpha sweep archived one task", result["archived"], 1)
    check("alpha's stale task is archived", await archived(A_TASK), True)
    check("BETA's stale task is untouched by alpha's sweep",
          await archived(B_TASK), False)

    # The mirror — without it, a sweep matching NOTHING would pass the
    # isolation assertion for the wrong reason.
    result = await wf_service._pm_lifecycle_sweeper(B_WF)()
    check("beta sweep archived one task", result["archived"], 1)
    check("beta's stale task is archived", await archived(B_TASK), True)

    # The activity the sweep wrote is stamped with the sweeping tenant.
    rows = await activities(A_TASK)
    check("alpha's archive activity exists", len(rows), 1)
    check("alpha's archive activity is stamped alpha",
          str(rows[0].organization_id) if rows else None, orgs["alpha"])

    # The refusal, on a real session.
    db = await get_db()
    try:
        for label, org in (("empty string", ""), ("whitespace", "  ")):
            try:
                await run_lifecycle_sweep(db, organization_id=org, actor="x")
                check(f"sweep refuses a tenant that is {label}",
                      "no exception", "TenantUnbound")
            except TenantUnbound:
                check(f"sweep refuses a tenant that is {label}",
                      "TenantUnbound", "TenantUnbound")
    finally:
        await db.close()

    # The orphan workflow cannot sweep at all.
    try:
        await wf_service._pm_lifecycle_sweeper(ORPHAN_WF)()
        check("a workflow with no resolvable org cannot sweep",
              "no exception", "NodeExecutionError")
    except Exception as exc:
        check("a workflow with no resolvable org cannot sweep",
              type(exc).__name__, "NodeExecutionError")


async def dispatch_checks(orgs):
    """The event sink: an explicit tenant, or nothing at all."""
    dispatched: list[tuple] = []

    async def _fake_run(agent, message, task_id, organization_id):
        dispatched.append((agent, task_id, organization_id))

    real = agent_dispatch._run_and_record
    agent_dispatch._run_and_record = _fake_run
    try:
        # No tenant on the payload: refuse. Nothing written, nothing run.
        await agent_dispatch.on_event("projects", "pm.task.assigned", {
            "task_id": A_AGENT_TASK, "assignees": ["agent:researcher"],
        })
        check("an event with no tenant dispatches nothing", dispatched, [])
        check("an event with no tenant writes nothing",
              await activities(A_AGENT_TASK), [])

        # The tenant on the payload is bound, and the row is stamped with it.
        await agent_dispatch.on_event("projects", "pm.task.assigned", {
            "task_id": A_AGENT_TASK, "assignees": ["agent:researcher"],
            "organization_id": orgs["alpha"],
        })
        check("a bound dispatch runs the agent",
              dispatched, [("researcher", A_AGENT_TASK, orgs["alpha"])])
        rows = await activities(A_AGENT_TASK)
        check("a bound dispatch writes one activity", len(rows), 1)
        check("the activity is stamped alpha",
              str(rows[0].organization_id) if rows else None, orgs["alpha"])

        # The outcome write, on its own session, under the threaded tenant.
        await agent_dispatch._record_outcome(
            A_AGENT_TASK, "researcher", orgs["alpha"], ok=True, detail="done",
        )
        rows = await activities(A_AGENT_TASK)
        check("the outcome activity is stamped alpha too",
              [str(r.organization_id) for r in rows],
              [orgs["alpha"], orgs["alpha"]])
    finally:
        agent_dispatch._run_and_record = real

    # Nothing above left an ambient tenant behind for the next caller.
    check("no ambient tenant leaked into this context", current_tenant(), None)


async def main():
    orgs = await seed()
    await resolution_checks(orgs)
    await sweep_checks(orgs)
    await dispatch_checks(orgs)
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("all checks passed")


asyncio.run(main())
