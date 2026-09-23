"""WS-39 S8c — meeting actions and the email brief use the one store (R8).

Spec `my_tasks_cutover.md` §5 S8c · board WS-39.

── Why this file exists ─────────────────────────────────────────────────────

Three modules wrote or read `gtd_items` after production moved to `pm_tasks`.
Their replacements are Postgres claims, so they are proven against Postgres:

  1. Approving a meeting action captures a task that answers `_MY_TASKS_SQL`
     for the member, under a STATED INBOX, with the meeting origin.
  2. The action row records the task in `dispatch_ref`. `resulting_task_id`
     stays NULL, because its foreign key names the legacy `task` table. The
     script proves that key refuses a `pm_tasks` id, which is the reason.
  3. Approving twice leaves one task. So does a capture whose row lost its
     ref, because the seam finds the earlier capture by `origin`.
  4. The email digest returns a reply-commitment capture for the account's
     owner, with the latest message of its thread.
  5. The email drafter's calendar returns an open hard-date task.

── How to run ───────────────────────────────────────────────────────────────

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run python tests/live/live_ws39_s8c.py

`LIVE_DSN` wins when set; otherwise `TENANT_LADDER_DATABASE_URL` (the scratch
tenant database) is used with its driver swapped for asyncpg, which is the
driver production runs. Everything runs in one transaction that is rolled
back.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

# The seam still reads the cutover flag on this base. Production serves the
# pm arm, so this script does too.
os.environ["TASKS_LENS"] = "1"

from gateway.routes.email import digest
from gateway.routes.email.automation import drafting
from gateway.routes.notes import actions
from gateway.routes.projects.personal import (
    _MY_TASKS_SQL,
    my_tasks_binds,
)
from gateway.routes.tasks.item_source import item_source
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


async def _mine(db, org, clause: str = "", **extra) -> list:
    binds = {**await my_tasks_binds(db, WHO, archived=False),
             "vis_org": str(org), **extra}
    return (await db.execute(text(_MY_TASKS_SQL + clause), binds)).fetchall()


async def main() -> None:
    eng = create_async_engine(DSN)
    async with eng.connect() as db:
        outer = await db.begin()
        org = (await db.execute(text(
            "INSERT INTO organization (slug, display_name) "
            "VALUES (:slug, 'WS-39 S8c live') RETURNING id"),
            {"slug": f"live-s8c-{TAG}"},
        )).scalar_one()
        await db.execute(text(
            "INSERT INTO app_user (email, organization_id, status) "
            "VALUES (:e, :org, 'active')"), {"e": WHO, "org": org})
        meeting = (await db.execute(text(
            "INSERT INTO meeting (platform, start_at, owner_email, title) "
            "VALUES ('upload', now(), :who, 'S8c live') RETURNING id"),
            {"who": WHO})).scalar_one()
        action_id = (await db.execute(text(
            "INSERT INTO action_item (meeting_id, description, confidence, "
            "due_hint) VALUES (:m, :d, 0.9, 'Friday') RETURNING id"),
            {"m": meeting, "d": f"Send the S8c quote {TAG}"})).scalar_one()

        # The approve route opens its own tenant session. Point it at this
        # transaction, so the whole run rolls back.
        @asynccontextmanager
        async def _session(organization_id=None):
            yield db

        actions._tenant_session = _session
        user = SimpleNamespace(email=WHO)

        # ── 1. approve → one INBOX task in my list, meeting origin ────────
        out = await actions.approve_action(str(action_id), user=user)
        rows = await _mine(db, org, " AND t.id = CAST(:tid AS uuid)",
                           tid=out.resulting_task_id)
        row = rows[0] if rows else None
        origin = row.origin if row is not None else None
        if isinstance(origin, str):
            origin = json.loads(origin)
        check("1 approve captures a task in my list, stated INBOX",
              row is not None and row.p_disposition == "INBOX"
              and row.title == f"Send the S8c quote {TAG}"
              and "Due (as stated): Friday." in (row.description or ""),
              f"row={row}")
        check("1b the task carries the meeting origin",
              origin == {"kind": "meeting", "meeting_id": str(meeting),
                         "action_item_id": str(action_id), "segment_ids": []},
              f"origin={origin!r}")

        # ── 2. the link back is dispatch_ref; resulting_task_id is NULL ────
        link = (await db.execute(text(
            "SELECT status, dispatch_ref, resulting_task_id FROM action_item "
            "WHERE id = :a"), {"a": action_id})).fetchone()
        check("2 the row records the task in dispatch_ref only",
              link.status == "created"
              and link.dispatch_ref == out.resulting_task_id
              and link.resulting_task_id is None, f"link={link}")
        refused = ""
        save = await db.begin_nested()
        try:
            await db.execute(text(
                "UPDATE action_item SET resulting_task_id = CAST(:t AS uuid) "
                "WHERE id = :a"), {"t": out.resulting_task_id, "a": action_id})
        except Exception as exc:
            refused = str(exc)
        await save.rollback()
        check("2b resulting_task_id's foreign key refuses a pm_tasks id",
              "action_item_resulting_task_id_fkey" in refused,
              f"refused={refused[:200]!r}")

        # ── 3. approve twice → one task; a lost ref still finds it ─────────
        again = await actions.approve_action(str(action_id), user=user)
        await db.execute(text(
            "UPDATE action_item SET status = 'draft', dispatch_ref = NULL "
            "WHERE id = :a"), {"a": action_id})
        lost = await actions.approve_action(str(action_id), user=user)
        count = len(await _mine(
            db, org, " AND t.origin->>'action_item_id' = :aid",
            aid=str(action_id)))
        check("3 approving twice, and after a lost ref, leaves one task",
              again.resulting_task_id == out.resulting_task_id
              and lost.resulting_task_id == out.resulting_task_id
              and count == 1,
              f"again={again.resulting_task_id} lost={lost.resulting_task_id} "
              f"count={count}")

        # ── 4. the digest returns a reply-commitment capture ───────────────
        account = (await db.execute(text(
            "INSERT INTO email_accounts (user_id, provider, email_address, "
            "credentials_encrypted) VALUES (:who, 'gmail', :who, 'x') "
            "RETURNING id"), {"who": WHO})).scalar_one()
        thread = f"th-{TAG}"
        message = (await db.execute(text(
            "INSERT INTO email_messages (account_id, provider_message_id, "
            "thread_id, from_address, to_addresses, received_at) "
            "VALUES (:a, :p, :t, '{}'::jsonb, '[]'::jsonb, now()) "
            "RETURNING id"),
            {"a": account, "p": f"pm-{TAG}", "t": thread})).scalar_one()
        due = datetime.now(UTC) + timedelta(days=1)
        promise = await item_source().insert_capture(db, WHO, {
            "title": "Send Priya the deck", "disposition": "NEXT",
            "due_at": due,
        }, {"kind": "email", "account_id": str(account), "thread_id": thread,
            "commitment": True})
        brief = await digest._digest_commitments(db, str(account))
        check("4 the digest returns the reply commitment with its message",
              [c["task_id"] for c in brief] == [promise]
              and brief[0]["thread_id"] == thread
              and brief[0]["message_id"] == str(message)
              and brief[0]["overdue"] is False
              and brief[0]["due"] == due.strftime("%b %d"),
              f"brief={brief}")
        # Closing it drops it from the brief: the seam's open rule, not ours.
        await item_source().mark_done_by_thread(db, WHO, thread)
        check("4b a closed commitment leaves the brief",
              await digest._digest_commitments(db, str(account)) == [],
              "a closed commitment is still listed")

        # ── 5. the drafter's calendar reads an open hard-date task ─────────
        slot = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
        await item_source().insert_capture(db, WHO, {
            "title": f"Board meeting {TAG}", "disposition": "NEXT",
            "due_at": slot, "is_hard_date": True,
        }, None)
        await item_source().insert_capture(db, WHO, {
            "title": "Soft date", "disposition": "NEXT", "due_at": slot,
        }, None)
        cal = await drafting._fetch_calendar_context(db, str(account))
        check("5 the calendar context lists the hard date and not the soft",
              f"Board meeting {TAG}" in cal and "Soft date" not in cal
              and slot.strftime("%a %b %d, %H:%M") in cal,
              f"cal={cal!r}")

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
