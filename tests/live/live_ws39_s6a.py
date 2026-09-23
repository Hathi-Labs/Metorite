"""WS-39 S6a — the CRUD tail's gateway helpers, against a real Postgres (R8).

Spec `my_tasks_cutover.md` §5 S6a · D73 · board WS-39.

── Why this file exists ─────────────────────────────────────────────────────

S6a's three routes make TRANSACTION claims, and a hermetic fake cannot judge
one of those: it commits on a clean exit and never rolls anything back, so
"the 4th capture fails and the first 3 go with it" passes against the fake
whether or not it is true. Four claims below are Postgres claims:

  * `capture_batch` is all-or-nothing (S6a scope: "one transaction");
  * `organize` rolls its overlay write back when a subtask insert fails
    (S6a done-when 4);
  * a personal CHILD carries `personal_owner` and the team tree's own
    predicate (`personal_owner IS NULL`) does not list it — and it INHERITS
    the root's lanes (migration 196) rather than owning a copy;
  * `bulk` action `personal` leaves two members holding two dispositions on
    one task, through the same upsert `set_personal` uses.

Plus the arm S6a added to `MY_TASKS_FROM`: a task I delegated AWAY stays in
my list while my overlay says WAITING. Measured as a 404 on `organize`'s
read-back before the arm existed.

The script imports the module's own helpers — it does not restate their SQL.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s6a.py

`LIVE_DSN` wins when set; otherwise `TENANT_LADDER_DATABASE_URL` (the scratch
tenant database) is used with its driver swapped for asyncpg, which is the
driver production runs.

── Result, 2026-09-23 (re-run after S8a), PostgreSQL 16, asyncpg: 13/13 ─

     1 three captures land, in my inbox's own query ......................... PASS
     2 a failing 4th capture rolls the first 3 back ......................... PASS
     3 organize NEXT writes the overlay and two self-assigned children ...... PASS
     4 a failed subtask insert rolls the overlay back ....................... PASS
     5 delegate replaces the assignee and the WAITING arm keeps it mine ..... PASS
    5b a WAITING task never enters carry_forward or candidates ............. PASS
    5c revoking the grant takes the delegated task out of my lists ......... PASS
     6 kind=project mints a private child the team tree cannot see .......... PASS
     7 the child inherits the root's lanes, and the task kept its lane ...... PASS
     8 a task filed in the child is still in my inbox ....................... PASS
     9 bulk personal leaves two members with two dispositions on one task ... PASS
    10 the overlay writes stamp clarified_at and never move the board ....... PASS
    11 another tenant sees none of it ...................................... PASS

Checks 2 and 4 are the ones a fake cannot give: each provokes a failure AFTER
rows are written and proves the earlier rows are gone. Check 5 is the arm
this slice added to the one membership query, proven on the real join; 5c is
its BOUND (the overlay may narrow a grant, never widen one), and 5b is the
planner refusing what the arm admits.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from gateway.routes.projects.bulk import _act_on_one
from gateway.routes.projects.core import (
    Visibility,
    load_default_status,
    status_owner_id,
)
from gateway.routes.projects.personal import (
    _MY_TASKS_SQL,
    OrganizeAssignee,
    OrganizeIn,
    _organize,
    _read_my_task,
    _upsert_personal,
    create_personal_task,
    ensure_personal_project,
    my_tasks_binds,
)
from gateway.routes.projects.planning import _LensSource
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg", "+asyncpg",
)
#: Per-run addresses: `app_user.email` is not unique on the ladder, so a
#: fixed address would collide with a row an earlier run left behind.
TAG = uuid.uuid4().hex[:8]
WHO = f"alice-{TAG}@fracktal.in"
OTHER = f"bob-{TAG}@fracktal.in"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def capture(db, root, title: str, **cols):
    """One capture through the ONE capture path, the way the batch route
    and the organize subtasks reach it."""
    status = await load_default_status(db, str(root.id))
    return await create_personal_task(
        db, WHO, str(root.id), str(root.id), str(status.id),
        {"title": title, "source": "manual", **cols},
    )


async def _titles(db, org, who: str, archived: bool = True) -> set[str]:
    # Through the ONE bind assembler, so the closure binds ride with the
    # tenant. `org` is overridden only by check 11, which asks the question
    # from another tenant's side.
    binds = {**await my_tasks_binds(db, who, archived=archived), "vis_org": str(org)}
    rows = (await db.execute(text(_MY_TASKS_SQL), binds)).fetchall()
    return {r.title for r in rows}


async def main() -> None:
    eng = create_async_engine(DSN)
    # `connect()` + an explicit rollback, NOT `engine.begin()`, for the reasons
    # `live_ws39_s3a_client.py` gives: nothing this script writes survives it,
    # and the checks that provoke a failure run inside SAVEPOINTs.
    async with eng.connect() as db:
        outer = await db.begin()
        tag = TAG
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6a live') RETURNING id"),
            {"slug": f"live-s6a-{tag}"},
        )).scalar_one()
        for who in (WHO, OTHER):
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, status) "
                "VALUES (:e, :org, 'active')"),
                {"e": who, "org": org})
        vis = Visibility(unrestricted=False, email=WHO, groups=(), organization_id=str(org))

        # ── 1. batch: three captures, one transaction ──────────────────────
        root = await ensure_personal_project(db, WHO)
        async with db.begin_nested():
            for title in ("One", "Two", "Three"):
                await capture(db, root, title)
        mine = await _titles(db, org, WHO)
        check("1 three captures land, in my inbox's own query",
              {"One", "Two", "Three"} <= mine, f"got {sorted(mine)}")

        # ── 2. …and a failing 4th takes the first 3 with it ────────────────
        #     The 4th carries a malformed instant: `coerce_write_values` raises
        #     the 422 INSIDE `insert_row`, after three rows are written. The
        #     route's `_tenant_session` rolls back on the exception; the
        #     savepoint here is the same contract.
        try:
            async with db.begin_nested():
                for title in ("Four", "Five", "Six"):
                    await capture(db, root, title)
                await capture(db, root, "Seven", due_at="not-a-date")
            check("2 a failing 4th capture rolls the first 3 back", False,
                  "the 4th was ACCEPTED")
        except HTTPException as exc:
            after = await _titles(db, org, WHO)
            check("2 a failing 4th capture rolls the first 3 back",
                  exc.status_code == 422 and not ({"Four", "Five", "Six"} & after),
                  f"status={exc.status_code} survivors={sorted(after & {'Four', 'Five', 'Six'})}")

        # ── 3. organize: NEXT with subtasks, self-assigned, in order ───────
        task = await capture(db, root, "Plan the offsite")
        await _organize(db, vis, WHO, task, OrganizeIn(
            kind="next", next_action="Book the venue", context="@computer",
            subtasks=["Shortlist venues", "Call the top two"],
        ))
        read = await _read_my_task(db, WHO, str(task.id))
        kids = (await db.execute(text(
            "SELECT t.title, t.project_id, "
            "  EXISTS (SELECT 1 FROM pm_task_assignees a "
            "           WHERE a.task_id = t.id AND a.assignee = :who) AS mine "
            "FROM pm_tasks t WHERE t.parent_task_id = :p ORDER BY t.task_number"),
            {"p": task.id, "who": WHO})).fetchall()
        check("3 organize NEXT writes the overlay and two self-assigned children",
              read["disposition"] == "NEXT" and read["next_action"] == "Book the venue"
              and read["subtask_count"] == 2
              and [k.title for k in kids] == ["Shortlist venues", "Call the top two"]
              and all(k.mine for k in kids)
              and {str(k.project_id) for k in kids} == {str(task.project_id)},
              f"disposition={read['disposition']} kids={[(k.title, k.mine) for k in kids]}")

        # ── 4. a failing subtask insert rolls the overlay back ─────────────
        #     A trigger that refuses one title stands in for any late failure
        #     (a NOT NULL, a CHECK, a lost connection). It lives inside this
        #     transaction and dies with it.
        await db.execute(text(
            "CREATE OR REPLACE FUNCTION pg_temp.s6a_boom() RETURNS trigger AS $$ "
            "BEGIN IF NEW.title = 'BOOM' THEN RAISE EXCEPTION 's6a boom'; END IF; "
            "RETURN NEW; END $$ LANGUAGE plpgsql"))
        await db.execute(text(
            "CREATE TRIGGER s6a_boom BEFORE INSERT ON pm_tasks "
            "FOR EACH ROW EXECUTE FUNCTION pg_temp.s6a_boom()"))
        victim = await capture(db, root, "Half clarified")
        try:
            async with db.begin_nested():
                await _organize(db, vis, WHO, victim, OrganizeIn(
                    kind="next", next_action="x", context="@rolled-back",
                    subtasks=["fine", "BOOM"],
                ))
            check("4 a failed subtask insert rolls the overlay back", False,
                  "the boom did not fire")
        except Exception as exc:  # the refusal IS the assertion
            overlay = (await db.execute(text(
                "SELECT disposition, context FROM pm_task_personal "
                "WHERE task_id = :t AND member_email = :who"),
                {"t": victim.id, "who": WHO})).fetchone()
            kids = (await db.execute(text(
                "SELECT count(*) FROM pm_tasks WHERE parent_task_id = :p"),
                {"p": victim.id})).scalar()
            # S8a: a capture states INBOX, so the capture's own overlay row
            # exists BEFORE the savepoint. The rollback must leave exactly
            # that row: still INBOX, and none of organize's writes.
            check("4 a failed subtask insert rolls the overlay back",
                  overlay is not None and overlay[0] == "INBOX"
                  and overlay[1] is None and kids == 0,
                  f"{type(exc).__name__}; overlay={overlay} children={kids}")
        await db.execute(text("DROP TRIGGER s6a_boom ON pm_tasks"))

        # ── 5. delegate: assignee replaced, WAITING recorded, still mine ───
        team = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :org, 'S6a team', :who, true)"),
            {"id": team, "org": org, "who": WHO})
        for who in (WHO, OTHER):
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (:p, :s, :s)"), {"p": team, "s": who})
        team_status = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, category, position) "
            "VALUES (:id, :p, 'To do', 'todo', 10)"), {"id": team_status, "p": team})
        team_task = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "  task_number, status_id, title, created_by) "
            "VALUES (:id, :org, :p, :p, 1, :s, 'Draft the quote', :who)"),
            {"id": team_task, "org": org, "p": team, "s": team_status, "who": WHO})
        await db.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "VALUES (:t, :a, :a)"), {"t": team_task, "a": WHO})
        row = (await db.execute(text(
            "SELECT * FROM pm_tasks WHERE id = :id"), {"id": team_task})).fetchone()
        await _organize(db, vis, WHO, row, OrganizeIn(
            kind="delegate", next_action="Draft it",
            assignee=OrganizeAssignee(name="Bob", email=OTHER),
            due_at="2026-10-01T00:00:00+00:00",
        ))
        assignees = {r.assignee for r in (await db.execute(text(
            "SELECT assignee FROM pm_task_assignees WHERE task_id = :t"),
            {"t": team_task})).fetchall()}
        try:
            back = await _read_my_task(db, WHO, str(team_task))
            still_mine = back["disposition"] == "WAITING" and back["is_mine"] is False
            detail = f"assignees={sorted(assignees)} disposition={back['disposition']}"
        except HTTPException as exc:
            still_mine = False
            detail = f"read-back {exc.status_code}: the WAITING arm is missing"
        check("5 delegate replaces the assignee and the WAITING arm keeps it mine",
              assignees == {OTHER} and still_mine, detail)

        # ── 5b. the planner never packs a WAITING task the arm admitted (F4)
        #     Give it a block in the past, as if a plan had been applied
        #     before the hand-off: carry_forward must still refuse it, and
        #     candidates never offered it.
        yesterday = datetime.now(UTC) - timedelta(days=1)
        await _upsert_personal(db, str(team_task), WHO, {
            "scheduled_start": yesterday,
            "scheduled_end": yesterday + timedelta(hours=1),
        })
        lens = _LensSource("pm")
        carried = {r.id for r in await lens.carry_forward(db, WHO, datetime.now(UTC))}
        offered = {r.id for r in await lens.candidates(db, WHO)}
        check("5b a WAITING task never enters carry_forward or candidates",
              str(team_task) not in carried and str(team_task) not in offered,
              f"carried={str(team_task) in carried} offered={str(team_task) in offered}")

        # ── 5c. the arm is BOUNDED: revoke the grant and the chase is gone ──
        #     P0. The overlay may narrow a grant, never widen one: with no
        #     grant on the team project, WAITING keeps nothing in my list.
        await db.execute(text(
            "DELETE FROM pm_project_grants WHERE project_id = :p AND subject = :s"),
            {"p": team, "s": WHO})
        gone_from_list = "Draft the quote" not in await _titles(db, org, WHO)
        try:
            await _read_my_task(db, WHO, str(team_task))
            gone_singly = False
        except HTTPException as exc:
            gone_singly = exc.status_code == 404
        check("5c revoking the grant takes the delegated task out of my lists",
              gone_from_list and gone_singly,
              f"in_list={not gone_from_list} single_404={gone_singly}")

        # ── 6. kind=project mints a private child the team tree cannot see ─
        proj_task = await capture(db, root, "Redo the kitchen")
        await _organize(db, vis, WHO, proj_task, OrganizeIn(
            kind="project", next_action="Measure", outcome="Kitchen renovated",
        ))
        child = (await db.execute(text(
            "SELECT * FROM pm_projects WHERE name = 'Kitchen renovated' "
            "AND parent_project_id = :root"), {"root": root.id})).fetchone()
        moved = (await db.execute(text(
            "SELECT project_id, status_id FROM pm_tasks WHERE id = :id"),
            {"id": proj_task.id})).fetchone()
        # The Projects app's own predicate (`tree.py`), for an UNRESTRICTED
        # caller — the widest read there is, and it must still not see it.
        wide = Visibility(unrestricted=True, email="", groups=(), organization_id=str(org))
        listed = {str(r.id) for r in (await db.execute(text(
            f"SELECT id FROM pm_projects WHERE {wide.project_clause()} "
            "AND personal_owner IS NULL"), wide.params)).fetchall()}
        check("6 kind=project mints a private child the team tree cannot see",
              child is not None and child.personal_owner == WHO
              and str(moved.project_id) == str(child.id)
              and str(child.id) not in listed and str(team) in listed,
              f"child={None if child is None else child.personal_owner} "
              f"moved_into_child={child is not None and str(moved.project_id) == str(child.id)} "
              f"listed={str(child.id) in listed if child else '?'}")

        # ── 7. …and the child INHERITS the root's lanes (no remap) ─────────
        owner = await status_owner_id(db, str(child.id))
        check("7 the child inherits the root's lanes, and the task kept its lane",
              owner == str(root.id) and str(moved.status_id) == str(proj_task.status_id),
              f"owner={owner} root={root.id}")

        # ── 8. the task in the child is still in my inbox (191, every depth)
        deep = await _titles(db, org, WHO)
        check("8 a task filed in the child is still in my inbox",
              "Redo the kitchen" in deep, f"got {sorted(deep)}")

        # ── 9. bulk personal: two members, two dispositions, one task ──────
        shared = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "  task_number, status_id, title, created_by) "
            "VALUES (:id, :org, :p, :p, 2, :s, 'Shared', :who)"),
            {"id": shared, "org": org, "p": team, "s": team_status, "who": WHO})
        for who in (WHO, OTHER):
            await db.execute(text(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
                "VALUES (:t, :a, :a)"), {"t": shared, "a": who})
        shared_row = (await db.execute(text(
            "SELECT * FROM pm_tasks WHERE id = :id"), {"id": shared})).fetchone()
        a = await _act_on_one(db, shared_row, "personal", by=WHO,
                              personal={"disposition": "SOMEDAY", "context": "@home"})
        b = await _act_on_one(db, shared_row, "personal", by=OTHER,
                              personal={"disposition": "NEXT"})
        alice = await _read_my_task(db, WHO, str(shared))
        bob = await _read_my_task(db, OTHER, str(shared))
        check("9 bulk personal leaves two members with two dispositions on one task",
              a == ("applied", "personal") and b == ("applied", "personal")
              and alice["disposition"] == "SOMEDAY" and alice["context"] == "@home"
              and bob["disposition"] == "NEXT" and bob["context"] is None,
              f"alice={alice['disposition']}/{alice['context']} bob={bob['disposition']}")

        # ── 10. bulk personal's clarified_at is real, and the board did not move
        stamped = (await db.execute(text(
            "SELECT count(*) FROM pm_task_personal "
            "WHERE task_id = :t AND clarified_at IS NOT NULL"), {"t": shared})).scalar()
        board = (await db.execute(text(
            "SELECT status_id FROM pm_tasks WHERE id = :t"), {"t": shared})).scalar()
        check("10 the overlay writes stamp clarified_at and never move the board",
              stamped == 2 and str(board) == str(team_status),
              f"stamped={stamped} status_moved={str(board) != str(team_status)}")

        # ── 11. another tenant sees none of it ─────────────────────────────
        other_org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'other') RETURNING id"),
            {"slug": f"live-s6a-other-{tag}"},
        )).scalar_one()
        leaked = await _titles(db, other_org, WHO)
        check("11 another tenant sees none of it", leaked == set(), f"got {sorted(leaked)}")

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
