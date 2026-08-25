"""WS-39 S3a-client slice 2 — the day planner's LENS source, live (R8).

Board WS-39 · spec `calendar_focus_os.md` §10 · D53/D54.

── Why this file exists ─────────────────────────────────────────────────────

`_LensSource` is five new queries against `pm_tasks` + `pm_task_personal`, and
the claim that matters is not "they run" — it is **which rows they return**, in
a case a hermetic fake is structurally unable to judge:

    A member who has never triaged a task still has NO overlay row for it, so
    `p.disposition` is NULL, and the planner must nonetheless treat it as NEXT
    because `derive_disposition` says an assigned, open task is NEXT.

Get that wrong and "Plan my day" returns an empty plan for every member whose
company board is untriaged — which is every member, on day one. The failure is
a 200 with no blocks: no error, no log line, nothing to notice.

The queries also prune with the STATED disposition (`p.disposition IS NULL OR
p.disposition = 'NEXT'`) before Python rules on the effective one. That prune is
an optimisation that can only be WRONG in one direction — dropping a row that
should have survived — so checks 2 and 3 below drive it from both sides.

This script calls `LENS_SOURCE`'s own methods against a real database. It does
not restate their SQL; a live test that retypes a query proves the retyped query
works.

── How to run ───────────────────────────────────────────────────────────────

    LIVE_DSN="postgresql+asyncpg://acb:<pw>@localhost:5443/acb_tenant" \
        uv run python tests/live/live_ws39_s3a_client2.py

── Result, 2026-08-25, PostgreSQL 16 (tenant-scratch), asyncpg ──────────────

    (recorded by the run itself; see the board record)
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from gateway.routes.projects.planning import LENS_SOURCE
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ["LIVE_DSN"]
# Run-unique addresses: `app_user` carries a FUNCTIONAL unique index on
# `lower(email)` rather than a plain constraint, so `ON CONFLICT (email)`
# is rejected outright — and a fixed address would collide with a previous
# run that did not roll back cleanly.
_RUN = uuid.uuid4().hex[:8]
WHO = f"alice-{_RUN}@fracktal.in"
OTHER = f"bob-{_RUN}@fracktal.in"

NOW = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
DAY0 = NOW.replace(hour=0, minute=0)
DAY1 = DAY0 + timedelta(days=1)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def titles(rows) -> set[str]:
    return {r.title for r in rows}


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S3a-client2 live') RETURNING id"),
            {"slug": "live-s3a-c2-" + uuid.uuid4().hex[:8]})).scalar_one()

        # `resolve_organization_id` reads `app_user`, so the fixture needs one:
        # without it the tenant resolves to NULL and every query answers empty
        # for the RIGHT reason, which would make the whole file pass vacuously.
        for who in (WHO, OTHER):
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, status) "
                "VALUES (:e, :org, 'active')"),
                {"e": who, "org": org})

        proj = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by) "
            "VALUES (:id, :org, 'S3a-c2 live', :who)"),
            {"id": proj, "org": org, "who": WHO})

        statuses = {}
        for name, category in (("To do", "todo"), ("Done", "done"),
                               ("Backlog", "backlog")):
            sid = uuid.uuid4()
            statuses[category] = sid
            await db.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, category,"
                " is_default, organization_id) "
                "VALUES (:id, :p, :n, :c, :d, :org)"),
                {"id": sid, "p": proj, "n": name, "c": category,
                 "d": category == "todo", "org": org})

        n = [0]

        async def task(title, *, category="todo", assignee=WHO, parent=None,
                       overlay=None):
            tid = uuid.uuid4()
            n[0] += 1
            await db.execute(text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, "
                " root_project_id, task_number, status_id, title, created_by, "
                " parent_task_id) "
                "VALUES (:id, :org, :p, :p, :n, :s, :t, :who, :parent)"),
                {"id": tid, "org": org, "p": proj, "n": n[0],
                 "s": statuses[category], "t": title, "who": WHO,
                 "parent": parent})
            if assignee:
                await db.execute(text(
                    "INSERT INTO pm_task_assignees (task_id, assignee, "
                    " assigned_by) VALUES (:t, :a, :a)"),
                    {"t": tid, "a": assignee})
            if overlay:
                cols = ", ".join(overlay)
                binds = ", ".join(f":{c}" for c in overlay)
                await db.execute(text(
                    f"INSERT INTO pm_task_personal (task_id, member_email, "
                    f" {cols}) VALUES (:tid, :who, {binds})"),
                    {"tid": tid, "who": assignee or WHO, **overlay})
            return tid

        # ── the fixture ────────────────────────────────────────────────────
        await task("Untriaged assigned")                       # -> NEXT
        await task("Stated SOMEDAY", overlay={"disposition": "SOMEDAY"})
        await task("Closed on the board", category="done")     # -> DONE
        await task("In the backlog", category="backlog")       # -> SOMEDAY
        await task("Stated NEXT in the backlog", category="backlog",
                   overlay={"disposition": "NEXT"})            # stated wins
        await task("Scheduled today", overlay={
            "scheduled_start": DAY0 + timedelta(hours=9),
            "scheduled_end": DAY0 + timedelta(hours=10)})
        await task("Fixed meeting today", overlay={
            "scheduled_start": DAY0 + timedelta(hours=11),
            "scheduled_end": DAY0 + timedelta(hours=12), "flexible": False})
        await task("Stranded yesterday", overlay={
            "scheduled_start": DAY0 - timedelta(hours=4),
            "scheduled_end": DAY0 - timedelta(hours=3)})
        await task("Somebody else's", assignee=OTHER)
        parent = await task("A parent")
        await task("A subtask", parent=parent)
        await task("Measured yesterday", overlay={
            "scheduled_start": DAY0 - timedelta(hours=8),
            "scheduled_end": DAY0 - timedelta(hours=7),
            "actual_start": DAY0 - timedelta(hours=8),
            "actual_end": DAY0 - timedelta(hours=6)})           # ratio 2.0

        # ── 1. THE claim: an untriaged assigned task is a candidate ────────
        cands = titles(await LENS_SOURCE.candidates(db, WHO))
        check("1 an UNTRIAGED assigned task is a NEXT candidate",
              "Untriaged assigned" in cands, f"got {sorted(cands)}")

        # ── 2. …and the derivation actually rules the others out ───────────
        check("2 derived DONE / SOMEDAY are not candidates",
              not ({"Closed on the board", "In the backlog",
                    "Stated SOMEDAY"} & cands),
              f"got {sorted(cands)}")

        # ── 3. …but a STATED disposition beats the derivation ──────────────
        #     The prune can only be wrong by dropping a row it should keep.
        #     This is that row: backlog (derives SOMEDAY), stated NEXT.
        check("3 a stated NEXT outranks a backlog status",
              "Stated NEXT in the backlog" in cands, f"got {sorted(cands)}")

        # ── 4. scheduled and foreign work stay out of the candidate list ───
        check("4 scheduled, subtasks and other people are excluded",
              not ({"Scheduled today", "A subtask", "Somebody else's",
                    "Fixed meeting today"} & cands),
              f"got {sorted(cands)}")

        # ── 5. today's blocks, including the fixed one ─────────────────────
        today = titles(await LENS_SOURCE.scheduled_today(db, WHO, DAY0, DAY1))
        check("5 scheduled_today returns today's blocks only",
              today == {"Scheduled today", "Fixed meeting today"},
              f"got {sorted(today)}")

        # ── 6. yesterday's leftovers ───────────────────────────────────────
        carry = titles(await LENS_SOURCE.carry_forward(db, WHO, DAY0))
        check("6 carry_forward sweeps a stranded prior day",
              "Stranded yesterday" in carry and "Scheduled today" not in carry,
              f"got {sorted(carry)}")

        over = titles(await LENS_SOURCE.overdue(db, WHO, NOW))
        check("7 overdue finds the unfinished past block",
              "Stranded yesterday" in over, f"got {sorted(over)}")

        busy = titles(await LENS_SOURCE.busy_window(
            db, WHO, DAY0 + timedelta(hours=8), DAY0 + timedelta(hours=13)))
        check("8 busy_window overlaps, not contains",
              busy == {"Scheduled today", "Fixed meeting today"},
              f"got {sorted(busy)}")

        # ── 9. the learned-estimate signal reads the overlay ───────────────
        ratio, samples = await LENS_SOURCE.estimate_ratio(db, WHO)
        check("9 estimate_ratio measures pm_task_personal",
              samples >= 1 and abs(ratio - 2.0) < 0.01,
              f"ratio={ratio} samples={samples}")

        # ── 10. the row wears the names the packer reads ───────────────────
        one = (await LENS_SOURCE.candidates(db, WHO))[0]
        needed = ("id", "title", "disposition", "flexible", "is_mine",
                  "scheduled_start", "scheduled_end", "due_at",
                  "time_estimate_mins", "energy", "context", "important",
                  "leveraged", "deep_work")
        missing = [f for f in needed if not hasattr(one, f)]
        check("10 rows carry every name the packer reads", not missing,
              f"missing {missing}")
        check("11 id is a string, not a uuid",
              isinstance(one.id, str), f"got {type(one.id).__name__}")

        # ── 12. another tenant sees none of it ─────────────────────────────
        other_org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'other') RETURNING id"),
            {"slug": "live-s3a-c2-other-" + uuid.uuid4().hex[:8]},
        )).scalar_one()
        await db.execute(text(
            "UPDATE app_user SET organization_id = :o WHERE email = :e"),
            {"o": other_org, "e": WHO})
        leaked = titles(await LENS_SOURCE.candidates(db, WHO))
        check("12 moving the member's tenant empties their plan",
              leaked == set(), f"got {sorted(leaked)}")

        await outer.rollback()

    await eng.dispose()

    width = max(len(x) for x, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        pad = "." * (width + 4 - len(name))
        print(f"  {name} {pad} {'PASS' if ok else 'FAIL: ' + detail}")
        failed += 0 if ok else 1
    print("")
    print(f"{len(results) - failed}/{len(results)} PASS")
    raise SystemExit(1 if failed else 0)


asyncio.run(main())
