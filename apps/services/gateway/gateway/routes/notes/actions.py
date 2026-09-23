"""Action-item HITL — approve a draft item into a task, or reject it.

The Meeting→Task counterpart of tasks/capture_email.py's Email→Task flow
(spec §3.9). A meeting's draft ``action_item`` rows only become real tasks when
a human approves them. On approval the task is captured through the ONE task
seam, ``item_source()`` (WS-39 S8c), into the member's personal root as an
INBOX capture, with an ``origin`` provenance link back to the meeting.

The link back is ``action_item.dispatch_ref``, the column every other dispatch
kind already writes (migration 129). ``resulting_task_id`` stays NULL: it
carries a foreign key to the legacy ``task`` table (01_schema.sql), which
refuses a ``pm_tasks`` id. Idempotent twice over: an item that already has a
ref returns it, and a capture already made for the item (``origin
action_item_id``) is found before a second one is written.
"""

from __future__ import annotations

import json

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.notes.core import (
    OWNED_MEETING_PREDICATE,
    _log,
    _tenant_session,
    load_owned_meeting,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text


class ApproveResponse(BaseModel):
    action_id: str
    status: str
    resulting_task_id: str | None = None


def task_ref(action) -> str | None:
    """The task an action item already became, or None.

    ``dispatch_ref`` for a task captured since WS-39 S8c, and
    ``resulting_task_id`` for one written before it. Only a TASK's ref is a
    task id: an email's ref is ``sent:…`` and a document's is ``artifact:…``.
    """
    if getattr(action, "resulting_task_id", None):
        return str(action.resulting_task_id)
    kind = getattr(action, "kind", None) or "task"
    ref = getattr(action, "dispatch_ref", None)
    return str(ref) if kind == "task" and ref else None


async def _create_task_from_action(db, user_email: str, action) -> str:
    """Capture a task from an action_item row through the one seam; return its id.

    The task lands in the member's personal root as a stated INBOX capture
    (``insert_capture``, S8a's rule in ``create_personal_task``). The free-text
    ``due_hint`` is kept in the notes body rather than force-parsed into a date.

    ⚠️ Idempotent by ORIGIN, not only by the action row. ``_dispatch`` commits
    the task in one session and marks the row in a second, so a failure between
    the two used to leave a draft whose next dispatch wrote a second task.
    """
    from gateway.routes.tasks.item_source import item_source

    src = item_source()
    existing = await src.find_by_origin(
        db, user_email, "action_item_id", str(action.id))
    if existing is not None:
        return str(existing.id)
    notes = f"From meeting notes. Confidence {action.confidence:.0%}."
    if action.due_hint:
        notes += f" Due (as stated): {action.due_hint}."
    origin = {
        "kind": "meeting",
        "meeting_id": str(action.meeting_id),
        "action_item_id": str(action.id),
        "segment_ids": [str(s) for s in (action.segment_ids or [])],
    }
    return await src.insert_capture(db, user_email, {
        "title": action.description[:500],
        "description": notes,
        "disposition": "INBOX",
    }, origin)


async def _load_action(db, action_id: str, owner_email: str | None):
    """The action item — if its meeting belongs to ``owner_email``. 404 else.

    An ``action_item`` has no owner of its own; it inherits the one on its
    meeting, so the scope is a join rather than a column. Written here, at the
    loader both single-item routes share, so neither can acquire the hole
    separately — the same shape ``load_owned_meeting`` has for meetings.

    404 and the same "action item not found" detail either way, matching
    ``dispatch.dispatch_action`` (``dispatch.py:614-622``): "exists but is not
    yours" confirms an id to whoever guessed it.
    """
    row = (
        await db.execute(
            text(
                "SELECT a.id, a.meeting_id, a.description, a.confidence, "
                "a.status, a.due_hint, a.segment_ids, a.resulting_task_id, "
                "a.kind, a.dispatch_ref "
                "FROM action_item a JOIN meeting m ON m.id = a.meeting_id "
                f"WHERE a.id = :id AND {OWNED_MEETING_PREDICATE}"
            ),
            {"id": action_id, "owner": owner_email or ""},
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="action item not found")
    return row


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    user: UserContext = Depends(get_current_user),
) -> ApproveResponse:
    """Promote a draft action item into a real GTD task.

    Owner only. Two distinct harms, both closed by the loader: approving
    somebody else's item flips THEIR triage state to ``created``, and the
    task it writes is filed under ``user_id = the caller`` with the
    colleague's ``description`` copied into its title — an exfiltration with
    a durable row to show for it, not just a nuisance edit.
    """
    async with _tenant_session() as db:
        action = await _load_action(db, action_id, user.email)
        done = task_ref(action)
        if done:  # idempotent
            return ApproveResponse(
                action_id=action_id, status=action.status,
                resulting_task_id=done,
            )
        task_id = await _create_task_from_action(db, user.email or "anonymous", action)
        # `dispatch_ref`, never `resulting_task_id`: that column's foreign key
        # names the legacy `task` table and refuses a `pm_tasks` id.
        await db.execute(
            text(
                "UPDATE action_item SET status='created', dispatch_ref=:tid, "
                "dispatched_at=now(), dispatch_error=NULL WHERE id=:id"
            ),
            {"tid": task_id, "id": action_id},
        )
        await db.execute(
            text(
                "INSERT INTO audit_event (actor, action, target, payload) VALUES "
                "(:actor, 'notes.action_approved', :target, CAST(:p AS JSONB))"
            ),
            {
                "actor": user.email or "unknown",
                "target": f"action_item:{action_id}",
                "p": json.dumps({"task_id": task_id, "meeting_id": str(action.meeting_id)}),
            },
        )
    _log.info("notes.action_approved", action_id=action_id, task_id=task_id)
    return ApproveResponse(action_id=action_id, status="created", resulting_task_id=task_id)


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    user: UserContext = Depends(get_current_user),
) -> ApproveResponse:
    """Dismiss a draft action item. Owner only — the item is the owner's
    triage queue, and a rejection is not reversible through this API."""
    async with _tenant_session() as db:
        action = await _load_action(db, action_id, user.email)
        if task_ref(action):
            raise HTTPException(
                status_code=409, detail="already created as a task; cannot reject"
            )
        await db.execute(
            text("UPDATE action_item SET status='rejected' WHERE id=:id"),
            {"id": action_id},
        )
    return ApproveResponse(action_id=action_id, status="rejected")


class BulkApproveRequest(BaseModel):
    min_confidence: float = 0.8


class BulkApproveResponse(BaseModel):
    created: list[str] = []  # action_ids approved this call


@router.post("/meetings/{meeting_id}/actions/approve-all")
async def approve_all(
    meeting_id: str,
    body: BulkApproveRequest,
    user: UserContext = Depends(get_current_user),
) -> BulkApproveResponse:
    """Dispatch every draft action item at or above a confidence threshold —
    each to its kind's system (task / email / document), not all to tasks.

    Already safe at the seam — every item goes through ``_dispatch``, which
    refuses a cross-owner actor (PR #346) — so a colleague could never make
    this send anything. What it *could* do was read a colleague's draft items
    and get a 200 back, which said "your meeting, nothing qualified" where the
    truth was "not your meeting". The load is owner-scoped here so the answer
    is the same 404 the single-item routes give, and the item rows are never
    read at all. The ``_dispatch`` refusal stays: it is the seam, and this
    check is a second lock on one of its doors, not a replacement for it."""
    # Deferred import: dispatch imports helpers from this module.
    from gateway.routes.notes import dispatch as notes_dispatch

    async with _tenant_session() as db:
        meeting = await load_owned_meeting(
            db, meeting_id, user.email,
            columns="m.id, m.title, m.owner_email, m.attendees, "
                    "m.summary_md, m.start_at",
        )
        rows = (
            await db.execute(
                text(
                    notes_dispatch._ACTION_COLS
                    + "WHERE meeting_id=:mid AND status='draft' "
                    "AND resulting_task_id IS NULL AND dispatch_ref IS NULL "
                    "AND confidence >= :min"
                ),
                {"mid": meeting_id, "min": body.min_confidence},
            )
        ).fetchall()
    created: list[str] = []
    for action in rows:
        ref, error = await notes_dispatch._dispatch(
            action, meeting, user.email or "anonymous"
        )
        if error is None and ref is not None:
            created.append(str(action.id))
    if created:
        async with _tenant_session() as db:
            await db.execute(
                text(
                    "INSERT INTO audit_event (actor, action, target, payload) VALUES "
                    "(:actor, 'notes.actions_bulk_approved', :target, CAST(:p AS JSONB))"
                ),
                {
                    "actor": user.email or "unknown",
                    "target": f"meeting:{meeting_id}",
                    "p": json.dumps({"count": len(created), "min_confidence": body.min_confidence}),
                },
            )
    _log.info("notes.actions_bulk_approved", meeting_id=meeting_id, count=len(created))
    return BulkApproveResponse(created=created)
