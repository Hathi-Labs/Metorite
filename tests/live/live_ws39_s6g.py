"""WS-39 S6g — Clarify's promote carries the required fields, against a real Postgres (R8).

Spec `my_tasks_cutover.md` §5 S6g · D62 · board WS-39.

── Why this file exists ─────────────────────────────────────────────────────

Before S6g, Clarify's organize could not carry the answers the Move dialog
collects. A personal capture filed into a company project with a REQUIRED
custom field (migration 192) was refused, after the card had already moved
the row. S6g gives `OrganizeIn` a `custom_fields` (and an `assignees`), and
`_organize` passes them into the ONE `MoveTask` it builds. These are the
claims a hermetic fake cannot judge:

  * with the answer, the move lands: the task is on the board, the value is
    stored under the destination's key, my overlay says NEXT, and I am still
    an assignee, so the task stays in my lists;
  * without the answer, the decision is refused with the field's name, and
    the refusal rolls back EVERYTHING the decision wrote: the move, the
    overlay, the activity row. Nothing moves;
  * an owner list that leaves me out is refused before anything is written;
  * an owner list that keeps me adds the colleague in the same transaction.

The script imports the module's own helpers. It does not restate their SQL.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s6g.py

`LIVE_DSN` wins when set. Otherwise `TENANT_LADDER_DATABASE_URL` is used with
its driver swapped for asyncpg, which is the driver production runs.

── Result, 2026-09-24, PostgreSQL 16, asyncpg, a FRESH database ─────────────

The run used `acb_tenant_s6g`, built from this branch's own ladder (01 plus
215 files, through 217), and dropped afterwards. See the S6g build record.
"""
from __future__ import annotations

import asyncio
import os
import uuid

from fastapi import HTTPException
from gateway.routes.projects.core import (
    Visibility,
    load_default_status,
)
from gateway.routes.projects.personal import (
    OrganizeIn,
    _organize,
    _read_my_task,
    create_personal_task,
    ensure_personal_project,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg", "+asyncpg",
)
TAG = uuid.uuid4().hex[:8]
WHO = f"alice-{TAG}@fracktal.in"
OTHER = f"bob-{TAG}@fracktal.in"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def capture(db, root, title: str):
    """One capture through the ONE capture path."""
    status = await load_default_status(db, str(root.id))
    return await create_personal_task(
        db, WHO, str(root.id), str(root.id), str(status.id),
        {"title": title, "source": "manual"},
    )


async def task_row(db, task_id):
    return (await db.execute(
        text("SELECT * FROM pm_tasks WHERE id = CAST(:id AS uuid)"), {"id": str(task_id)},
    )).fetchone()


async def overlay(db, task_id):
    return (await db.execute(text(
        "SELECT disposition, next_action FROM pm_task_personal "
        "WHERE task_id = CAST(:t AS uuid) AND member_email = :m"),
        {"t": str(task_id), "m": WHO})).fetchone()


async def owners(db, task_id) -> set[str]:
    return {r.assignee for r in (await db.execute(text(
        "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
        {"t": str(task_id)})).fetchall()}


async def moves(db, task_id) -> int:
    return (await db.execute(text(
        "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid) "
        "AND body = 'Task moved'"), {"t": str(task_id)})).scalar_one()


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6g live') RETURNING id"),
            {"slug": f"live-s6g-{TAG}"},
        )).scalar_one()
        for who in (WHO, OTHER):
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, status) "
                "VALUES (:e, :org, 'active')"),
                {"e": who, "org": org})
        vis = Visibility(unrestricted=False, email=WHO, groups=(), organization_id=str(org))
        root = await ensure_personal_project(db, WHO)

        # A company board with one REQUIRED field, both members granted.
        board = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
            "VALUES (:id, :org, 'Printer v3', :who, true)"),
            {"id": board, "org": org, "who": WHO})
        for who in (WHO, OTHER):
            await db.execute(text(
                "INSERT INTO pm_project_grants (project_id, subject, created_by) "
                "VALUES (:p, :s, :s)"), {"p": board, "s": who})
        await db.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, category, position) "
            "VALUES (:id, :p, 'To do', 'todo', 10)"), {"id": uuid.uuid4(), "p": board})
        await db.execute(text(
            "INSERT INTO pm_custom_fields "
            "  (project_id, organization_id, field_key, name, field_type, required, created_by) "
            "VALUES (:p, :org, 'customer_po', 'PO', 'text', true, :who)"),
            {"p": board, "org": org, "who": WHO})

        # ── 1. without the answer: refused, and nothing moves ───────────────
        bare = await capture(db, root, "Order the rollers")
        try:
            async with db.begin_nested():
                await _organize(db, vis, WHO, await task_row(db, bare.id), OrganizeIn(
                    kind="next", next_action="Order them", project_id=str(board),
                ))
            check("1 organize without the required field is refused", False, "ACCEPTED")
        except HTTPException as exc:
            row = await task_row(db, bare.id)
            ov = await overlay(db, bare.id)
            check("1 organize without the required field is refused",
                  exc.status_code == 422 and "PO" in str(exc.detail),
                  f"status={exc.status_code} detail={exc.detail}")
            check("1b the refusal moved nothing and wrote no overlay",
                  str(row.project_id) == str(root.id)
                  and ov is not None and ov.disposition == "INBOX"
                  and ov.next_action is None
                  and await moves(db, bare.id) == 0,
                  f"project={row.project_id} root={root.id} "
                  f"overlay={tuple(ov) if ov else None}")

        # ── 2. with the answer: the promote lands, and it stays mine ────────
        task = await capture(db, root, "Fix the jam")
        await _organize(db, vis, WHO, await task_row(db, task.id), OrganizeIn(
            kind="next", next_action="Fix the jam", project_id=str(board),
            custom_fields={"customer_po": "PO-77"},
        ))
        row = await task_row(db, task.id)
        ov = await overlay(db, task.id)
        fields = row.custom_fields if isinstance(row.custom_fields, dict) else {}
        check("2 organize with the required field moves the task onto the board",
              str(row.project_id) == str(board) and str(row.root_project_id) == str(board),
              f"project={row.project_id}")
        check("2b the answer is stored under the destination's key",
              fields.get("customer_po") == "PO-77", f"custom_fields={row.custom_fields}")
        check("2c my overlay says NEXT and I am still an assignee",
              ov is not None and ov.disposition == "NEXT"
              and await owners(db, task.id) == {WHO},
              f"overlay={tuple(ov) if ov else None} owners={await owners(db, task.id)}")
        mine = await _read_my_task(db, WHO, str(task.id))
        check("2d it stays in my lists (the one membership query)",
              mine.get("project_id") == str(board), f"read={mine.get('project_id')}")
        check("2e the move left its trace on the timeline",
              await moves(db, task.id) == 1, f"moves={await moves(db, task.id)}")

        # ── 3. an owner list without me is refused before any write ─────────
        other = await capture(db, root, "Hand it over")
        try:
            async with db.begin_nested():
                await _organize(db, vis, WHO, await task_row(db, other.id), OrganizeIn(
                    kind="next", next_action="Hand it over", project_id=str(board),
                    custom_fields={"customer_po": "PO-1"}, assignees=[OTHER],
                ))
            check("3 an owner list without me is refused", False, "ACCEPTED")
        except HTTPException as exc:
            row = await task_row(db, other.id)
            check("3 an owner list without me is refused, and nothing moves",
                  exc.status_code == 422 and "Delegate" in str(exc.detail)
                  and str(row.project_id) == str(root.id)
                  and await owners(db, other.id) == {WHO},
                  f"status={exc.status_code} project={row.project_id}")

        # ── 4. an owner list that keeps me adds the colleague, atomically ───
        both = await capture(db, root, "Pair on it")
        await _organize(db, vis, WHO, await task_row(db, both.id), OrganizeIn(
            kind="next", next_action="Pair on it", project_id=str(board),
            custom_fields={"customer_po": "PO-2"}, assignees=[WHO, OTHER],
        ))
        row = await task_row(db, both.id)
        check("4 assignees travel with the move, in one transaction",
              str(row.project_id) == str(board)
              and await owners(db, both.id) == {WHO, OTHER},
              f"owners={await owners(db, both.id)}")

        await outer.rollback()
    await eng.dispose()

    width = max(len(n) for n, _, _ in results) + 4
    passed = 0
    for name, ok, detail in results:
        passed += ok
        line = f"{name} {'.' * (width - len(name))} {'PASS' if ok else 'FAIL'}"
        print(line if ok else f"{line}\n      {detail}")
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
