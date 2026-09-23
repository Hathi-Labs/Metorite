"""WS-39 S8a — a capture states INBOX, and the inbox pages in one order (R8).

Spec `my_tasks_cutover.md` §5 S8a · `task_manager_app.md` §13.5a decision 4.

── Why this file exists ─────────────────────────────────────────────────────

Two of S8a's gateway claims are Postgres claims:

  * a capture through the ONE capture path (`create_personal_task`, what
    `POST /projects/my/tasks` and the batch route call) answers
    `_MY_TASKS_SQL` filtered on `p.disposition = 'INBOX'`, and NOT the
    untriaged predicate (`p.disposition IS NULL`) S6e's `untriaged=true`
    will key on — a personal-root capture is not "from Projects";
  * `_INBOX_ORDER` is valid SQL against the real join, and paging a 3-row set
    two at a time sees each row exactly once.

Plus: `origin` survives the round trip as jsonb, and a subtask gets no
default overlay.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s8a.py

`LIVE_DSN` wins when set; otherwise `TENANT_LADDER_DATABASE_URL` (the scratch
tenant database) is used with its driver swapped for asyncpg, which is the
driver production runs.
"""
from __future__ import annotations

import asyncio
import os
import uuid

from gateway.routes.projects.core import load_default_status
from gateway.routes.projects.personal import (
    _INBOX_ORDER,
    _MY_TASKS_SQL,
    _project_task,
    create_personal_task,
    ensure_personal_project,
    my_tasks_binds,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ["TENANT_LADDER_DATABASE_URL"].replace(
    "+psycopg", "+asyncpg",
)
TAG = uuid.uuid4().hex[:8]
WHO = f"alice-{TAG}@fracktal.in"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def capture(db, root, title: str, overlay=None, **cols):
    status = await load_default_status(db, str(root.id))
    return await create_personal_task(
        db, WHO, str(root.id), str(root.id), str(status.id),
        {"title": title, "source": "manual", **cols}, overlay,
    )


async def _rows(db, org, clause: str = "", order: str = "") -> list:
    binds = {**await my_tasks_binds(db, WHO, archived=False), "vis_org": str(org)}
    return (await db.execute(text(_MY_TASKS_SQL + clause + order), binds)).fetchall()


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S8a live') RETURNING id"),
            {"slug": f"live-s8a-{TAG}"},
        )).scalar_one()
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, status) "
            "VALUES (:e, :org, 'active')"), {"e": WHO, "org": org})
        root = await ensure_personal_project(db, WHO)

        # ── 1. a capture states INBOX ──────────────────────────────────────
        task = await capture(db, root, "A thought")
        stated = await _rows(db, org, " AND p.disposition = 'INBOX'")
        overlay = (await db.execute(text(
            "SELECT disposition, clarified_at FROM pm_task_personal "
            "WHERE task_id = :t AND member_email = :who"),
            {"t": task.id, "who": WHO})).fetchone()
        check("1 a capture answers the inbox query under INBOX",
              [str(r.id) for r in stated] == [str(task.id)]
              and overlay is not None and overlay.disposition == "INBOX"
              and overlay.clarified_at is None,
              f"stated={[str(r.id) for r in stated]} overlay={overlay}")

        # ── 2. …and not as untriaged (S6e's predicate) ─────────────────────
        untriaged = await _rows(db, org, " AND p.disposition IS NULL")
        check("2 the capture is not untriaged, so it is not 'from Projects'",
              str(task.id) not in {str(r.id) for r in untriaged},
              f"untriaged={[str(r.id) for r in untriaged]}")

        # ── 3. the derived disposition would have been SOMEDAY ─────────────
        #     The lane the root hands a capture is `backlog`; that is the P0.
        lane = (await db.execute(text(
            "SELECT category FROM pm_task_statuses WHERE id = :s"),
            {"s": task.status_id})).scalar()
        projected, effective = _project_task(stated[0])
        check("3 the lane is backlog, and the STATED inbox wins over it",
              lane == "backlog" and effective == "INBOX"
              and projected["is_triaged"] is True,
              f"lane={lane} effective={effective}")

        # ── 4. paging: three rows, two a page, each seen once ──────────────
        second = await capture(db, root, "Second")
        third = await capture(db, root, "Third")
        # `now()` is fixed for the whole transaction, so the three rows share
        # one `created_at`. Stagger them, or DESC has nothing to order by.
        for row, minutes in ((task, 2), (second, 1)):
            await db.execute(text(
                "UPDATE pm_tasks SET created_at = now() - make_interval(mins => :m) "
                "WHERE id = :t"), {"m": minutes, "t": row.id})
        want = {str(task.id), str(second.id), str(third.id)}
        ordered = [str(r.id) for r in await _rows(db, org, "", _INBOX_ORDER)]
        pages = [ordered[0:2], ordered[2:4]]  # the route's Python slice
        again = [str(r.id) for r in await _rows(db, org, "", _INBOX_ORDER)]
        limited = []
        for offset in (0, 2):
            limited += [str(r.id) for r in (await db.execute(
                text(_MY_TASKS_SQL + _INBOX_ORDER + " LIMIT 2 OFFSET :o"),
                {**await my_tasks_binds(db, WHO, archived=False),
                 "vis_org": str(org), "o": offset},
            )).fetchall()]
        seen = pages[0] + pages[1]
        check("4 the ordered inbox pages 3 rows two at a time, each once",
              sorted(seen) == sorted(want) and len(set(seen)) == 3
              and ordered == again and limited == ordered
              and ordered == [str(third.id), str(second.id), str(task.id)],
              f"ordered={ordered} limited={limited}")

        # ── 5. origin rides the row as jsonb ───────────────────────────────
        mailed = await capture(db, root, "Vendor quote", source="email",
                               origin={"kind": "email", "from_name": "Sanjay Rao",
                                       "email_id": f"m-{TAG}"})
        row = next(r for r in await _rows(db, org) if str(r.id) == str(mailed.id))
        projected, _ = _project_task(row)
        check("5 origin survives the round trip and is projected",
              projected["origin"] == {"kind": "email", "from_name": "Sanjay Rao",
                                      "email_id": f"m-{TAG}"},
              f"origin={projected['origin']!r}")

        # ── 6. a subtask gets no default overlay ───────────────────────────
        step = await capture(db, root, "Step", parent_task_id=str(task.id))
        step_overlay = (await db.execute(text(
            "SELECT count(*) FROM pm_task_personal WHERE task_id = :t"),
            {"t": step.id})).scalar()
        check("6 a subtask states nothing", step_overlay == 0,
              f"overlay rows={step_overlay}")

        # ── 7. a stated disposition is kept ────────────────────────────────
        kept = await capture(db, root, "Clarified on arrival",
                             overlay={"disposition": "NEXT", "next_action": "Reply"})
        kept_row = (await db.execute(text(
            "SELECT disposition FROM pm_task_personal WHERE task_id = :t"),
            {"t": kept.id})).scalar()
        check("7 a stated disposition is not overwritten by the default",
              kept_row == "NEXT", f"disposition={kept_row}")

        await outer.rollback()
    await eng.dispose()

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        pad = "." * (width - len(name) + 3)
        print(f"  {name} {pad} {'PASS' if ok else 'FAIL: ' + detail}")
        failed += 0 if ok else 1
    print(f"{len(results) - failed}/{len(results)} PASS")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
