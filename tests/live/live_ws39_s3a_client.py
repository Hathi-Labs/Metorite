"""WS-39 S3a-client — the REAL `_MY_TASKS_SQL`, against a real Postgres (R8).

Board WS-39 slice S3a-client · decisions D52/D53/D54 · spec
`task_manager_app.md` §13.5.

── Why this file exists ─────────────────────────────────────────────────────

This slice changes the one query in the app that answers "what is on my plate",
in four ways, and every one of them is the sort a hermetic fake cannot judge:

  * `s.name AS workflow_stage` is a second column named off the
    `pm_task_statuses` join, selected alongside `t.*`. Whether that ALIAS
    collides with anything in `t.*` is a question for Postgres, not for a mirror
    that answers by attribute lookup.
  * the archived filter stopped being a literal and became `CAST(:archived AS
    boolean)` — a BOOLEAN bind over bare `text()`, which declares no column
    types to asyncpg. That is the exact shape that produced two 500s in S3a and
    one in S3a-server-2. A fake stores whatever it is handed.
  * `subtask_count` is a correlated subquery with its own `archived_at` clause.
  * the singular read is the same SQL plus `AND t.id = CAST(:tid AS uuid)`.

So this script imports the module's own `_MY_TASKS_SQL` — it does not restate
it. A live test that retypes the query proves the retyped query works.

── How to run ───────────────────────────────────────────────────────────────

    LIVE_DSN="postgresql+asyncpg://acb:<pw>@localhost:5443/acb_tenant" \
        uv run python tests/live/live_ws39_s3a_client.py

── Result, 2026-08-25, PostgreSQL 16 (tenant-scratch), asyncpg: 10/10 PASS ──

     1 archived=False binds and hides filed work ....... PASS
     2 archived=True reaches the archive ............... PASS
     3 a bogus :archived value is refused .............. PASS  DBAPIError
     4 omitting :archived raises rather than leaking ... PASS  StatementError
     5 workflow_stage is the status name ............... PASS
     6 subtask_count counts live children only ......... PASS
     7 is_mine follows the assignee .................... PASS
     8 the single-task clause returns exactly one ...... PASS
     9 somebody else's task is not readable singly ..... PASS
    10 another tenant sees none of it .................. PASS

Checks 3 and 4 are the ones worth reading. 3 says the `CAST(... AS boolean)` is
doing work rather than decorating — a bogus value is REFUSED, not coerced to
false. 4 says the bind has no default anywhere in the stack, so the R7 fence
this slice claims is real: a future caller who forgets `:archived` gets an
exception at the seam, not a list quietly containing everything they ever filed.
"""
from __future__ import annotations

import asyncio
import os
import uuid

from gateway.routes.projects.personal import _MY_TASKS_SQL
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ["LIVE_DSN"]
WHO = "alice@fracktal.in"
OTHER = "bob@fracktal.in"
ONE_TASK = _MY_TASKS_SQL + " AND t.id = CAST(:tid AS uuid)"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def main() -> None:
    eng = create_async_engine(DSN)
    keys = ("project", "status", "task", "sub", "sub_archived", "archived",
            "theirs")
    ids = {k: uuid.uuid4() for k in keys}

    # ⚠️ `connect()` + an explicit rollback, NOT `engine.begin()`. Two reasons,
    # and the second one bit: `begin()` COMMITS on exit, which would leave this
    # fixture in the scratch database for the next run to trip over; and checks
    # 3 and 4 deliberately provoke driver errors, which put a Postgres
    # transaction into a failed state where every later statement answers
    # "current transaction is aborted" — so those two run inside SAVEPOINTs.
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S3a-client live') RETURNING id"),
            {"slug": "live-s3a-client-" + uuid.uuid4().hex[:8]},
        )).scalar_one()

        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by) "
            "VALUES (:id, :org, 'S3a-client live', :who)"),
            {"id": ids["project"], "org": org, "who": WHO})
        await db.execute(text(
            "INSERT INTO pm_task_statuses "
            "  (id, project_id, name, category, is_default, organization_id) "
            "VALUES (:id, :proj, 'In progress', 'in_progress', true, :org)"),
            {"id": ids["status"], "proj": ids["project"], "org": org})

        async def seed(key, title, parent=None, archived=False, assignee=WHO):
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                "  root_project_id, task_number, status_id, title, created_by, "
                "  parent_task_id, archived_at) "
                "VALUES (:id, :org, :proj, :proj, :n, :st, :title, :who, "
                "        :parent, CASE WHEN :arch THEN now() ELSE NULL END)"),
                {"id": ids[key], "org": org, "proj": ids["project"],
                 "n": keys.index(key) + 1, "st": ids["status"],
                 "title": title, "who": WHO, "parent": parent,
                 "arch": archived})
            await db.execute(text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
                "VALUES (:t, :a, :a)"), {"t": ids[key], "a": assignee})

        await seed("task", "Ship the lens")
        await seed("sub", "A child", parent=ids["task"])
        await seed("sub_archived", "A filed child", parent=ids["task"],
                   archived=True)
        await seed("archived", "Filed last month", archived=True)
        await seed("theirs", "Somebody else", assignee=OTHER)

        base = {"who": WHO, "vis_org": str(org)}

        # ── 1. the boolean bind is REAL over bare text() ────────────────────
        rows = (await db.execute(text(_MY_TASKS_SQL),
                                 {**base, "archived": False})).fetchall()
        titles = {r.title for r in rows}
        check("1 archived=False binds and hides filed work",
              titles == {"Ship the lens", "A child"},
              f"got {sorted(titles)}")

        # ── 2. and True is not the same answer ──────────────────────────────
        rows_all = (await db.execute(text(_MY_TASKS_SQL),
                                     {**base, "archived": True})).fetchall()
        titles_all = {r.title for r in rows_all}
        check("2 archived=True reaches the archive",
              titles_all == {"Ship the lens", "A child", "A filed child",
                             "Filed last month"},
              f"got {sorted(titles_all)}")

        # ── 3. a bogus string is not silently accepted ──────────────────────
        #     The half that makes the cast load-bearing rather than cosmetic.
        try:
            async with db.begin_nested():
                await db.execute(text(_MY_TASKS_SQL),
                                 {**base, "archived": "no"})
            check("3 a bogus :archived value is refused", False,
                  "ACCEPTED - the cast is doing nothing")
        # The refusal IS the assertion, so a broad catch is the point here.
        except Exception as exc:
            check("3 a bogus :archived value is refused", True,
                  type(exc).__name__)

        # ── 4. a MISSING bind fails loudly (the R7 fence itself) ────────────
        try:
            async with db.begin_nested():
                await db.execute(text(_MY_TASKS_SQL), base)
            check("4 omitting :archived raises rather than leaking", False,
                  "ACCEPTED - a forgetful caller would leak archived rows")
        except Exception as exc:
            check("4 omitting :archived raises rather than leaking", True,
                  type(exc).__name__)

        by_title = {r.title: r for r in rows}
        parent = by_title["Ship the lens"]

        # ── 5. workflow_stage is the NAME, and t.* did not shadow it ────────
        check("5 workflow_stage is the status name",
              parent.workflow_stage == "In progress",
              f"got {parent.workflow_stage!r}")

        # ── 6. subtask_count excludes archived children ─────────────────────
        check("6 subtask_count counts live children only",
              parent.subtask_count == 1, f"got {parent.subtask_count}")

        # ── 7. is_mine follows the assignee, both ways ──────────────────────
        theirs = (await db.execute(text(ONE_TASK), {
            "who": OTHER, "vis_org": str(org), "archived": False,
            "tid": str(ids["theirs"])})).fetchall()
        check("7 is_mine follows the assignee",
              parent.is_mine is True and len(theirs) == 1
              and theirs[0].is_mine is True,
              f"mine={parent.is_mine} theirs={[r.is_mine for r in theirs]}")

        # ── 8. the singular read is the plural, narrowed ────────────────────
        one = (await db.execute(text(ONE_TASK), {
            **base, "archived": True, "tid": str(ids["task"])})).fetchall()
        check("8 the single-task clause returns exactly one",
              len(one) == 1 and one[0].title == "Ship the lens",
              f"got {[r.title for r in one]}")

        # ── 9. a task that is not mine is not reachable singly ──────────────
        #     404-by-absence: one clause decides both, so a task cannot be
        #     readable one-at-a-time and invisible in the list.
        none = (await db.execute(text(ONE_TASK), {
            **base, "archived": True, "tid": str(ids["theirs"])})).fetchall()
        check("9 somebody else's task is not readable singly",
              none == [], f"got {[r.title for r in none]}")

        # ── 10. the tenant clause still holds above both arms (WS-29b) ──────
        other_org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'other') RETURNING id"),
            {"slug": "live-s3a-other-" + uuid.uuid4().hex[:8]},
        )).scalar_one()
        leaked = (await db.execute(text(_MY_TASKS_SQL), {
            "who": WHO, "vis_org": str(other_org), "archived": True},
        )).fetchall()
        check("10 another tenant sees none of it", leaked == [],
              f"got {[r.title for r in leaked]}")

        # Nothing this script wrote survives it. A live harness that leaves
        # fixtures behind makes the NEXT run's answer depend on how many times
        # it has been run before, which is the opposite of evidence.
        await outer.rollback()

    await eng.dispose()

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        pad = "." * (width + 4 - len(name))
        print(f"  {name} {pad} {'PASS' if ok else 'FAIL: ' + detail}")
        failed += 0 if ok else 1
    print("")
    print(f"{len(results) - failed}/{len(results)} PASS")
    raise SystemExit(1 if failed else 0)


asyncio.run(main())
