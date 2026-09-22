"""Tasks ⇄ email — propagate completion across the commitment link.

A task captured from a sent-reply commitment (or any email capture) carries an
``origin`` linking it back to the email thread (``origin.kind == 'email'`` with a
``thread_id``). This module keeps the two "done" states in sync, both ways:

  task closed  → email thread marked Done   (``propagate_task_done_to_thread``)
  thread Done  → linked open tasks closed   (``propagate_thread_done_to_tasks``)

Both directions are GUARDED against a ping-pong loop: each only writes the OTHER
side, and only when that side isn't already in the terminal state. So closing a
task marks the thread Done; the thread going Done then finds the task already
DONE and stops — one hop each way, no oscillation.

These run best-effort (a failure to propagate never fails the originating
action) and are called from the task patch/sync paths and the email resolve
path. Kept in the tasks package because the linkage lives on the task's
``origin``, whichever store holds it (``item_source``, WS-39 S6d); the email
side imports lazily to avoid a package cycle.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text


def _thread_from_origin(origin: Any) -> tuple[str, str] | None:
    """(account_id, thread_id) from a task's origin, or None if it isn't an
    email-linked item with a usable thread. ``origin`` may be a dict (asyncpg
    JSONB) or a JSON string depending on the driver path."""
    if not origin:
        return None
    if isinstance(origin, str):
        import json  # noqa: PLC0415
        try:
            origin = json.loads(origin)
        except Exception:
            return None
    if not isinstance(origin, dict) or origin.get("kind") != "email":
        return None
    account_id = str(origin.get("account_id") or "").strip()
    thread_id = str(origin.get("thread_id") or "").strip()
    if not account_id or not thread_id:
        return None
    return account_id, thread_id


async def propagate_task_done_to_thread(db: Any, item_row: Any) -> None:
    """A task just closed — if it links back to an email thread, mark that thread
    Done (Reply Zero DONE) and collapse its conversation labels to "Done".

    Guarded: no-op if the thread is already DONE, so the reverse propagation
    (thread Done → close tasks) that may follow finds nothing to do. Best-effort;
    the caller has already committed the task close. Does NOT commit — the caller
    owns the transaction (so a task-patch and this write land together)."""
    link = _thread_from_origin(getattr(item_row, "origin", None))
    if link is None:
        return
    account_id, thread_id = link

    # Already Done? Nothing to do — and critically, don't re-fire the label
    # reconciliation (that's what would ping-pong with the reverse direction).
    cur = (await db.execute(text(
        "SELECT status FROM email_thread_status "
        "WHERE account_id = :aid AND thread_id = :tid"
    ), {"aid": account_id, "tid": thread_id})).fetchone()
    if cur is not None and cur.status == "DONE":
        return

    if cur is not None:
        await db.execute(text(
            "UPDATE email_thread_status SET status = 'DONE', "
            "classified_at = now(), reason = 'Task completed' "
            "WHERE account_id = :aid AND thread_id = :tid"
        ), {"aid": account_id, "tid": thread_id})
    else:
        # Heuristic mode (no stored status yet) — create one as DONE, anchored
        # on the thread's latest message (mirrors resolve_thread's create path).
        lm = (await db.execute(text(
            "SELECT id, received_at FROM email_messages "
            "WHERE account_id = :aid AND thread_id = :tid "
            "ORDER BY received_at DESC NULLS LAST LIMIT 1"
        ), {"aid": account_id, "tid": thread_id})).fetchone()
        await db.execute(text(
            "INSERT INTO email_thread_status (account_id, thread_id, status, "
            "last_message_id, last_message_at, reason) "
            "VALUES (:aid, :tid, 'DONE', :lmid, :lmat, 'Task completed') "
            "ON CONFLICT (account_id, thread_id) "
            "DO UPDATE SET status = 'DONE', classified_at = now()"
        ), {"aid": account_id, "tid": thread_id,
            "lmid": lm.id if lm else None,
            "lmat": lm.received_at if lm else None})

    # Collapse provider/local labels to "Done" (clear stale Reply/Awaiting/
    # Follow-up) — best-effort, mirrors resolve_thread. Lazy import: the email
    # package must not be imported at tasks-module load time (cycle).
    try:
        from gateway.routes.email.automation import _reconcile_labels_bg
        await _reconcile_labels_bg(account_id, thread_id, "Done")
    except Exception:  # noqa: BLE001
        pass


async def propagate_thread_done_to_tasks(
    db: Any, uid: str, account_id: str, thread_id: str,
) -> list[str]:
    """An email thread was just marked Done — close any OPEN tasks captured from
    it (a commitment or an inbound capture on the same thread). Returns the ids
    of the tasks it closed (may be empty).

    Guarded: only touches tasks that aren't already DONE/TRASH, so the forward
    propagation (task Done → thread Done) that fired first finds the thread
    already handled and this finds nothing new. Best-effort; caller commits."""
    if not thread_id:
        return []
    from gateway.routes.tasks.item_source import item_source

    return await item_source().mark_done_by_thread(db, uid, str(thread_id))
