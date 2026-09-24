"""WS-39 S6d — the AI seam's pm arm, against a real Postgres (R8).

Spec `my_tasks_cutover.md` §5 S6d · **D73** · board WS-39.

── Why this file exists ─────────────────────────────────────────────────────

The pm arm (`routes/projects/item_lens.py`) composes on `_MY_TASKS_SQL`,
writes `pm_tasks.origin` as JSONB through `insert_row`, upserts the overlay
through `_upsert_personal`, and completes a task through
`complete_for_member`. Every one of those is a shape a hermetic fake agrees
with whatever it is handed: a jsonb bind without its CAST, an origin key the
expression index does not serve, a status walk that refuses a child of the
personal root, a CHECK on the overlay's disposition. So this script drives the
REAL seam methods, importing them rather than restating their SQL, and reads
the rows back with plain SQL.

Two members in one organization and one member in another, so the questions
tenancy and membership must answer are asked, not assumed.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh
    eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s6d.py

It reads `LIVE_DSN`, or derives an asyncpg DSN from
`TENANT_LADDER_DATABASE_URL`. Everything runs inside one transaction that is
ROLLED BACK at the end, so the scratch database is left as it was found.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.environ.get("LIVE_GATEWAY_PATH", "apps/services/gateway"))

from gateway.routes.projects.item_lens import PM_ITEMS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = os.environ.get("LIVE_DSN") or os.environ[
    "TENANT_LADDER_DATABASE_URL"].replace("+psycopg", "+asyncpg")
ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION = ROOT / "infra" / "postgres" / "211_pm_tasks_origin.sql"

ALICE = "alice.s6d@fracktal.in"
BOB = "bob.s6d@fracktal.in"
CAROL = "carol.s6d@other.example"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


class _Plan:
    def __init__(self, name, description, tasks):
        self.name, self.description, self.phases = name, description, tasks


class _Task:
    def __init__(self, title, subtasks=(), assignee=None, due=None):
        self.title = title
        self.description = None
        self.assignee = assignee
        self.due_offset_days = due
        self.context = "@computer"
        self.energy = "medium"
        self.subtasks = list(subtasks)


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()

        # ── 0. the migration, applied twice ─────────────────────────────
        raw = await db.get_raw_connection()
        sql = MIGRATION.read_text(encoding="utf-8")
        for attempt in (1, 2):
            try:
                await raw.driver_connection.execute(sql)
                check(f"0.{attempt} migration 211 applies (pass {attempt})", True)
            except Exception as exc:
                check(f"0.{attempt} migration 211 applies (pass {attempt})",
                      False, f"{type(exc).__name__}: {exc}")
        cols = {r.column_name for r in (await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'pm_tasks' AND column_name = 'origin' "
            "UNION ALL SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'wa_commitments' AND column_name = 'task_id'"
        ))).fetchall()}
        check("0.3 both columns exist", cols == {"origin", "task_id"}, str(cols))
        idx = {r.indexname for r in (await db.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'pm_tasks' "
            "AND indexname LIKE 'idx_pm_tasks_origin_%'"))).fetchall()}
        check("0.4 four origin indexes, once each", len(idx) == 4, str(sorted(idx)))
        # S8 PR 2 (migration 217) ran the contract half: the old column is gone.
        await db.execute(text("SELECT k.task_id FROM wa_commitments k LIMIT 0"))
        legacy = (await db.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'wa_commitments' AND column_name = 'gtd_item_id'"
        ))).scalar()
        check("0.5 the digest reads task_id alone (217 dropped the old column)",
              legacy == 0, f"legacy={legacy}")

        # ── seed: two members in one org, one in another ─────────────────
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6d live') RETURNING id"),
            {"slug": "live-s6d-" + uuid.uuid4().hex[:8]})).scalar_one()
        org2 = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S6d live, other') RETURNING id"),
            {"slug": "live-s6d-o-" + uuid.uuid4().hex[:8]})).scalar_one()
        for who, o in ((ALICE, org), (BOB, org), (CAROL, org2)):
            await db.execute(text(
                "INSERT INTO app_user (email, organization_id, status) "
                "VALUES (:e, :org, 'active')"), {"e": who, "org": o})

        # ── 1. capture with origin ──────────────────────────────────────
        origin = {"kind": "email", "account_id": "acct-1", "email_id": "m-1",
                  "thread_id": "th-1", "subject": "Quote"}
        item_id = await PM_ITEMS.insert_capture(db, ALICE, {
            "title": "Send Priya the quote", "description": "the revised one",
            "disposition": "CALENDAR", "next_action": "Open the sheet",
            "context": "@computer", "energy": "low", "time_estimate_mins": 15,
            "due_at": datetime.now(UTC) + timedelta(days=2),
            "is_hard_date": True, "clarified_at": datetime.now(UTC),
            "subtasks": ["Check the numbers", "Attach the PDF"],
        }, origin)
        row = (await db.execute(text(
            "SELECT t.origin, t.source, t.project_id, proj.personal_owner, "
            "       proj.parent_project_id, p.disposition, p.context, "
            "       p.is_hard_date, t.estimate_mins, "
            "       (SELECT count(*) FROM pm_task_assignees a "
            "         WHERE a.task_id = t.id AND a.assignee = :who) AS mine, "
            "       (SELECT count(*) FROM pm_tasks c "
            "         WHERE c.parent_task_id = t.id) AS subs "
            "  FROM pm_tasks t "
            "  JOIN pm_projects proj ON proj.id = t.project_id "
            "  LEFT JOIN pm_task_personal p ON p.task_id = t.id "
            "   AND p.member_email = :who "
            " WHERE t.id = CAST(:tid AS uuid)"),
            {"tid": item_id, "who": ALICE})).fetchone()
        check("1.1 origin round-trips as jsonb",
              row.origin is not None and "m-1" in str(row.origin), str(row.origin))
        check("1.2 lands in my personal ROOT, self-assigned",
              row.personal_owner == ALICE and row.parent_project_id is None
              and row.mine == 1,
              f"owner={row.personal_owner} parent={row.parent_project_id} mine={row.mine}")
        # D77 (S6f): the drafter's estimate is the TASK's one estimate, so
        # it lands on `pm_tasks.estimate_mins`, not the overlay.
        check("1.3 source is 'email' and the overlay carries the routing",
              row.source == "email" and row.disposition == "NEXT"
              and row.context == "@computer" and row.is_hard_date is True
              and row.estimate_mins == 15,
              f"{row.source} {row.disposition} {row.context} {row.is_hard_date} "
              f"est={row.estimate_mins}")
        check("1.4 subtasks are children", row.subs == 2, f"subs={row.subs}")

        # ── 2. idempotency by origin ────────────────────────────────────
        found = await PM_ITEMS.find_by_origin(db, ALICE, "email_id", "m-1")
        check("2.1 find_by_origin(email_id) returns the capture",
              found is not None and found.id == item_id,
              f"got {getattr(found, 'id', None)}")
        check("2.2 the row wears the consumer names, origin decoded",
              found is not None and found.origin == origin
              and found.description == "the revised one"
              and found.disposition == "NEXT" and found.is_mine is True,
              f"{getattr(found, 'origin', None)}")
        same_thread = await PM_ITEMS.items_by_origin(
            db, ALICE, "thread_id", "th-1", exclude_email_id="m-1")
        check("2.3 same-thread lookup excludes this email", same_thread == [],
              f"{[r.id for r in same_thread]}")
        bob_sees = await PM_ITEMS.find_by_origin(db, BOB, "email_id", "m-1")
        check("2.4 another member does not find it", bob_sees is None)
        try:
            await PM_ITEMS.fetch_item(db, CAROL, item_id)
            check("2.5 another tenant gets a 404", False, "fetched")
        except Exception as exc:
            check("2.5 another tenant gets a 404",
                  getattr(exc, "status_code", None) == 404, type(exc).__name__)

        # ── 3. set_context ──────────────────────────────────────────────
        await PM_ITEMS.set_context(db, ALICE, item_id, "@calls")
        ctx = (await db.execute(text(
            "SELECT context FROM pm_task_personal "
            "WHERE task_id = CAST(:tid AS uuid) AND member_email = :who"),
            {"tid": item_id, "who": ALICE})).scalar_one()
        check("3.1 set_context upserts my overlay", ctx == "@calls", ctx)
        contexts = await PM_ITEMS.contexts_for(db, ALICE)
        check("3.2 contexts_for reads it back", "@calls" in contexts, str(contexts))

        # ── 4. the commitment mark and the update path ──────────────────
        await PM_ITEMS.update_origin(db, ALICE, item_id, {"commitment": True})
        marked = await PM_ITEMS.find_by_origin(
            db, ALICE, "thread_id", "th-1", commitment=True)
        check("4.1 update_origin merges and the commitment lookup finds it",
              marked is not None and marked.id == item_id
              and marked.origin.get("email_id") == "m-1",
              f"{getattr(marked, 'origin', None)}")

        # ── 5. insights, two members ────────────────────────────────────
        a1 = await PM_ITEMS.insight_counts(db, ALICE)
        b1 = await PM_ITEMS.insight_counts(db, BOB)
        check("5.1 alice's insights count her capture and its steps",
              a1["counts"].get("NEXT") == 3, str(a1))
        check("5.2 bob's insights do not count alice's private task",
              b1["counts"] == {} and b1["oldest_inbox_at"] is None, str(b1))
        bob_item = await PM_ITEMS.insert_capture(db, BOB, {
            "title": "Bob's own", "disposition": "INBOX",
        }, {"kind": "whatsapp", "wa_message_id": "w-1", "wa_chat_id": "c-1"})
        b2 = await PM_ITEMS.insight_counts(db, BOB)
        a2 = await PM_ITEMS.insight_counts(db, ALICE)
        check("5.3 bob's capture counts for bob only",
              b2["counts"] == {"INBOX": 1} and b2["oldest_inbox_at"] is not None
              and a2["counts"] == a1["counts"], f"bob={b2} alice={a2}")
        loops = await PM_ITEMS.items_by_origin(db, BOB, "wa_chat_id", "c-1", limit=10)
        check("5.4 the WhatsApp chat lookup finds bob's capture",
              [r.id for r in loops] == [bob_item], str([r.id for r in loops]))
        await PM_ITEMS.record_waiting(
            db, BOB, bob_item, {"name": "Ravi"},
            datetime.now(UTC) - timedelta(days=9))
        b3 = await PM_ITEMS.insight_counts(db, BOB)
        check("5.5 a Waiting-For nine days old with no nudge is stale",
              b3["counts"] == {"WAITING": 1} and b3["stale_waiting"] == 1, str(b3))

        # ── 6. plan_apply: a child of the personal root ─────────────────
        plan = _Plan("Launch the newsletter", "monthly", [
            _Task("Draft issue one", ["Outline", "Write"], due=3),
            _Task("Pick the tool", assignee={"name": "Ravi", "email": "ravi@x"}),
        ])
        created = await PM_ITEMS.plan_apply(db, ALICE, plan, plan.phases)
        child = (await db.execute(text(
            "SELECT personal_owner, parent_project_id, kind, "
            "       (SELECT count(*) FROM pm_tasks t WHERE t.project_id = p.id "
            "          AND t.parent_task_id IS NULL) AS tasks, "
            "       (SELECT count(*) FROM pm_tasks t WHERE t.project_id = p.id "
            "          AND t.parent_task_id IS NOT NULL) AS subs "
            "  FROM pm_projects p WHERE p.id = CAST(:pid AS uuid)"),
            {"pid": created["project_id"]})).fetchone()
        check("6.1 the plan is a private child project with its tasks",
              child.personal_owner == ALICE and child.parent_project_id is not None
              and child.tasks == 2 and child.subs == 2
              and created["tasks_created"] == 2 and created["subtasks_created"] == 2,
              f"{child} {created}")
        tree = await PM_ITEMS.projects_for(db, ALICE)
        check("6.2 projects_for lists the root and the new Area",
              [p.is_root for p in tree] == [True, False]
              and tree[1].outcome == "Launch the newsletter", str(tree))
        spaces, folders = await PM_ITEMS.local_tree(db, ALICE)
        check("6.3 local_tree is the Areas, no folders",
              [s.name for s in spaces] == ["Launch the newsletter"] and folders == [])
        a3 = await PM_ITEMS.insight_counts(db, ALICE)
        check("6.4 the delegated plan task is WAITING, the Area has a NEXT",
              a3["counts"].get("WAITING") == 1
              and a3["projects_without_next_action"] == 0, str(a3))
        sib = await PM_ITEMS.siblings(db, ALICE, created["project_id"], item_id, 40)
        check("6.5 siblings reads the Area's top-level tasks",
              sorted(r.title for r in sib) == ["Draft issue one", "Pick the tool"],
              str([r.title for r in sib]))

        # ── 7. mark_done_by_thread: the one completion path ─────────────
        closed = await PM_ITEMS.mark_done_by_thread(db, ALICE, "th-1")
        state = (await db.execute(text(
            "SELECT s.category, t.completed_at, p.disposition "
            "  FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id "
            "  LEFT JOIN pm_task_personal p ON p.task_id = t.id "
            "   AND p.member_email = :who "
            " WHERE t.id = CAST(:tid AS uuid)"),
            {"tid": item_id, "who": ALICE})).fetchone()
        check("7.1 the thread's task moves to the done lane, overlay DONE",
              closed == [item_id] and state.category == "done"
              and state.completed_at is not None and state.disposition == "DONE",
              f"{closed} {state}")
        again = await PM_ITEMS.find_by_origin(db, ALICE, "email_id", "m-1")
        check("7.2 a closed capture no longer blocks re-capture", again is None)
        check("7.3 a second close is a no-op",
              await PM_ITEMS.mark_done_by_thread(db, ALICE, "th-1") == [])

        # ── 8. the backfill read and the load hint ──────────────────────
        bare = await PM_ITEMS.insert_capture(db, ALICE, {
            "title": "No context yet", "disposition": "NEXT"}, None)
        todo = await PM_ITEMS.context_less_actionables(db, ALICE, 40)
        check("8.1 context_less_actionables finds the bare NEXT",
              bare in {r.id for r in todo}, str([r.title for r in todo]))
        load = await PM_ITEMS.assignee_load(db, ALICE)
        by_em = {r.em: r.n for r in load}
        check("8.2 assignee_load counts MY open work, never bob's private task",
              by_em.get(ALICE, 0) >= 3 and BOB not in by_em, str(by_em))
        bob_load = {r.em: r.n for r in await PM_ITEMS.assignee_load(db, BOB)}
        check("8.3 bob's load is his own capture and none of alice's",
              bob_load == {BOB: 1}, str(bob_load))

        await outer.rollback()
    await eng.dispose()

    width = max(len(n) for n, _ok, _d in results)
    failed = 0
    for name, ok, detail in results:
        failed += 0 if ok else 1
        line = f"{name.ljust(width, '.')} {'PASS' if ok else 'FAIL'}  {detail}"
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode())
    print(f"\n{len(results) - failed}/{len(results)} PASS")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
