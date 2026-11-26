"""Mark done: a Done by hand that stays Done (task 3995).

Spec: ``email_app_master_plan.md`` §6.6. Dark behind ``EMAIL_MARK_DONE_ORGS``.
With the flag off, nothing here runs a statement or changes one.

The member marks a thread done through ``POST /email/reply-zero/resolve``,
the one route of Reply Zero. This module holds what that route does with the
flag on, and the **hold** that makes the choice stay:

- A held thread is DONE with the reason :data:`MANUAL_DONE_REASON`, and no
  mail from another person is newer than its anchor. :func:`held_sql` is the
  ONE definition.
- No automated writer moves a held thread: the status upsert, the label
  reconcile, the rules runner and the label learner of the sync each ask here.
- A new inbound message ends the hold. So does a write of the member (Fix,
  reopen, dismiss, or "Move back").

⚠️ ``replyzero`` imports this module, so this module imports ``replyzero``
inside its functions only.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends
from gateway.routes.email.core import (
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _savepoint,
    _tenant_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

#: The reason on the row of a thread that the member marked done by hand. It is
#: the mark of the hold, so no other writer may use these words.
MANUAL_DONE_REASON = "Marked done by you"

#: The reason on the row after "Move back". It ends the hold.
RESTORED_REASON = "Moved back by you"

#: The History reason of a rule that did not run because of the hold.
HELD_SKIP_REASON = "Suppressed: you marked this conversation done."

#: The suppression key of such a match (``runner._apply_and_log_match``).
HELD_SUPPRESSION = "held"

#: The statuses that "Move back" may restore, and the label of each one.
_RESTORE_LABELS = {
    "NEEDS_REPLY": "Needs Reply",
    "AWAITING": "Awaiting Reply",
    "FYI": "FYI",
}

#: The labels that say a thread is open. A Done thread wears none of them.
_NEEDS_REPLY_LABELS = ("Needs Reply", "Reply", "To Reply")
_OPEN_LABELS = frozenset(
    lab.lower() for lab in (*_NEEDS_REPLY_LABELS, "Awaiting Reply", "Follow-up"))


# ── The flag ─────────────────────────────────────────────────────────────────

def mark_done_on(org: object = None) -> bool:
    """True when ``EMAIL_MARK_DONE_ORGS`` names *org*. THE reader.

    The value is a comma list of organization ids, or ``*`` for each one.
    Empty means off, the default. It reads the settings at each call, so a
    flag reload applies a change with no restart. *org* defaults to the bound
    tenant of the request or the job. No request field reaches this function
    (R5). It fails closed: a fault, or a frame with no tenant under a list of
    ids, reads as off.
    """
    try:
        from acb_common import get_settings
        from acb_common.assistant_profile import _parse_orgs

        orgs = _parse_orgs(
            str(getattr(get_settings(), "email_mark_done_orgs", "") or ""),
            star=True)
        if not orgs or "*" in orgs:
            return bool(orgs)
        if org is None:
            from acb_common.db import current_tenant

            org = current_tenant()
        return str(org or "").strip().lower() in orgs
    except Exception:
        return False


# ── The hold: one definition ─────────────────────────────────────────────────

def held_sql(row: str) -> str:
    """The SQL test "this status row is held", over the table or alias *row*.

    The anchor is ``last_message_at`` of the row, which Mark done sets to the
    newest ``received_at`` of the thread. A message that is newer, and that is
    not the member's own sent mail or a draft, ends the hold. The text holds
    no bind parameter, so it fits inside any statement.
    """
    return (
        f"({row}.status = 'DONE' AND {row}.reason = '{MANUAL_DONE_REASON}' "
        "AND NOT EXISTS (SELECT 1 FROM email_messages held_m "
        f"WHERE held_m.account_id = {row}.account_id "
        f"AND held_m.thread_id = {row}.thread_id "
        f"AND held_m.received_at > COALESCE({row}.last_message_at, "
        f"{row}.classified_at) "
        "AND LOWER(COALESCE(held_m.folder, '')) NOT IN ('sent', 'drafts')))")


async def held_threads(
    db: Any, account_id: str, thread_ids: list[str],
) -> set[str]:
    """The thread ids of *thread_ids* that are held. No read with the flag off."""
    ids = [t for t in thread_ids if t]
    if not ids or not mark_done_on():
        return set()
    rows = (await db.execute(text(
        "SELECT s.thread_id FROM email_thread_status s "
        "WHERE s.account_id = CAST(:aid AS uuid) AND s.thread_id = ANY(:tids) "
        f"AND {held_sql('s')}"
    ), {"aid": account_id, "tids": ids})).fetchall()
    return {r.thread_id for r in rows}


async def thread_is_held(db: Any, account_id: str, thread_id: str | None) -> bool:
    """True when the thread is held. No read with the flag off."""
    if not thread_id:
        return False
    return thread_id in await held_threads(db, account_id, [thread_id])


async def held_label(
    db: Any, account_id: str, thread_id: str, keep_label: str | None,
) -> str | None:
    """The one conversation label that a thread may keep: "Done" for a held
    thread, else *keep_label*. No read for "Done" or with the flag off."""
    if keep_label != "Done" and await thread_is_held(db, account_id, thread_id):
        return "Done"
    return keep_label


def upsert_expressions(
    automated: bool, status_expr: str,
) -> tuple[str, str, str, str]:
    """The SET expressions of the status upsert for ``status``,
    ``last_message_at``, ``reason`` and ``classified_at``.

    An automated write keeps all four values of a held row. It still moves
    ``last_message_id``, so the backfill does not select the thread again.
    With the flag off, or for a write of the member, the expressions are the
    plain ones, so the statement is the statement of before.
    """
    plain = (status_expr, "EXCLUDED.last_message_at", "EXCLUDED.reason", "now()")
    if not (automated and mark_done_on()):
        return plain
    held = held_sql("email_thread_status")
    status, at, reason, classified = (
        f"CASE WHEN {held} THEN email_thread_status.{column} ELSE {new} END"
        for column, new in zip(
            ("status", "last_message_at", "reason", "classified_at"), plain,
            strict=True))
    return status, at, reason, classified


def automated_reason(reason: str) -> str:
    """The reason that an automated write may store. The mark of the hold is
    the member's alone. A model writes the reason of a rule match, so mail text
    must not be able to hold a thread. With the flag off the reason stands."""
    if reason == MANUAL_DONE_REASON and mark_done_on():
        return "Auto-classified"
    return reason


def hold_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each match suppressed because of the hold. None is dropped, so History
    can say why a rule did not run."""
    return [{**m, "suppressed": HELD_SUPPRESSION} for m in matches]


# ── The labels ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LabelChange:
    """What the provider must learn about one message."""

    message_id: str
    add: tuple[str, ...]
    remove: tuple[str, ...]


async def _thread_labels(db: Any, account_id: str, thread_id: str) -> dict[str, list[str]]:
    rows = (await db.execute(text(
        "SELECT id, categories FROM email_messages "
        "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid"
    ), {"aid": account_id, "tid": thread_id})).fetchall()
    return {str(r.id): list(r.categories or []) for r in rows}


async def settle_labels(
    db: Any, account_id: str, thread_id: str, keep_label: str,
    *, follow_up: bool = False,
) -> list[LabelChange]:
    """Set the labels of the thread in the LOCAL mirror, inside the caller's
    block, and return what changed. :func:`push_labels` then tells the
    provider with no request waiting on it.

    It runs ``replyzero._reconcile_thread_labels`` with no provider, so the
    rule of the labels stays in one place. ``follow_up`` puts "Follow-up" back
    on the latest message of the thread.
    """
    from gateway.routes.email.automation.replyzero import _reconcile_thread_labels

    before = await _thread_labels(db, account_id, thread_id)
    await _reconcile_thread_labels(db, None, account_id, thread_id, keep_label)
    if follow_up:
        await db.execute(text(
            "UPDATE email_messages SET categories = CASE "
            "WHEN 'Follow-up' = ANY(categories) THEN categories "
            "ELSE array_append(categories, 'Follow-up') END, updated_at = now() "
            "WHERE id = (SELECT id FROM email_messages "
            "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
            "ORDER BY received_at DESC NULLS LAST LIMIT 1)"
        ), {"aid": account_id, "tid": thread_id})
    after = await _thread_labels(db, account_id, thread_id)
    changes = []
    for message_id, labels in after.items():
        old = before.get(message_id, [])
        add = tuple(lab for lab in labels if lab not in old)
        remove = tuple(lab for lab in old if lab not in labels)
        if add or remove:
            changes.append(LabelChange(message_id, add, remove))
    return changes


async def push_labels(account_id: str, changes: list[LabelChange]) -> None:
    """Background: apply *changes* at the provider. Best effort.

    It reads each provider message id again, because Outlook gives a message a
    new id on a move. It drops a change that the mirror no longer holds, so a
    "Move back" that came first is not undone by the older job. The provider
    calls run with no session open.
    """
    if not changes:
        return
    try:
        async with _tenant_session() as db:
            acc = (await db.execute(text(
                "SELECT provider, credentials_encrypted FROM email_accounts "
                "WHERE id = :id"
            ), {"id": account_id})).fetchone()
            rows = (await db.execute(text(
                "SELECT id, provider_message_id, categories FROM email_messages "
                "WHERE account_id = CAST(:aid AS uuid) "
                "AND id = ANY(CAST(:ids AS uuid[]))"
            ), {"aid": account_id,
                "ids": [c.message_id for c in changes]})).fetchall()
        if not acc:
            return
        from acb_llm.key_store import get_key_store

        store = get_key_store()
        creds = json.loads(store.decrypt(acc.credentials_encrypted))
        provider = _instantiate_provider(acc.provider, creds)
        if not await provider.authenticate():
            _log.warning("email.mark_done_labels_auth_failed", account_id=account_id)
            return
        now = {str(r.id): r for r in rows}
        for change in changes:
            row = now.get(change.message_id)
            if row is None:
                continue
            mirror = set(row.categories or [])
            add = [lab for lab in change.add if lab in mirror]
            remove = [lab for lab in change.remove if lab not in mirror]
            if not add and not remove:
                continue
            # One bad message must not stop the rest.
            with contextlib.suppress(Exception):
                await provider.set_labels(
                    row.provider_message_id, add=add, remove=remove)
        if provider.credentials_dirty():
            async with _tenant_session() as db:
                await _persist_rotated_creds(db, store, account_id, provider)
    except Exception as exc:
        _log.warning("email.mark_done_labels_failed",
                     account_id=account_id, error=str(exc)[:160])


# ── The sync: a stale label that comes back ──────────────────────────────────

async def held_among(account_id: str, thread_ids: list[str]) -> set[str]:
    """:func:`held_threads` in a block of its own. No block with the flag off."""
    if not thread_ids or not mark_done_on():
        return set()
    async with _tenant_session() as db:
        return await held_threads(db, account_id, thread_ids)


async def settle_held(account_id: str, thread_id: str) -> None:
    """Put the labels of a held thread back to "Done", here and at the
    provider. Best effort."""
    try:
        async with _tenant_session() as db:
            changes = await settle_labels(db, account_id, thread_id, "Done")
        await push_labels(account_id, changes)
    except Exception as exc:
        _log.warning("email.mark_done_settle_failed",
                     account_id=account_id, error=str(exc)[:160])


async def reclean_returned_labels(account_id: str, changes: list) -> None:
    """A sync brought an open label back to some messages. For each held thread
    among them, remove it again.

    *changes* is the list of ``(message, old_categories)`` pairs of the sync.
    The label learner reacts to the labels of the rules only, so "Follow-up"
    alone needs this step. With the flag off it does nothing.
    """
    if not changes or not mark_done_on():
        return
    returned: set[str] = set()
    for message, old in changes:
        had = {str(c).strip().lower() for c in (old or [])}
        has = {str(c).strip().lower()
               for c in (getattr(message, "categories", None) or [])}
        thread_id = getattr(message, "thread_id", None)
        if thread_id and (has - had) & _OPEN_LABELS:
            returned.add(thread_id)
    try:
        held = await held_among(account_id, sorted(returned))
    except Exception as exc:
        _log.warning("email.mark_done_reclean_failed",
                     account_id=account_id, error=str(exc)[:160])
        return
    for thread_id in sorted(held):
        await settle_held(account_id, thread_id)


# ── The two writes of the member ─────────────────────────────────────────────

_STATUS_UPSERT_SQL = """
    INSERT INTO email_thread_status
        (account_id, thread_id, status, last_message_id, last_message_at,
         reason, classified_at)
    VALUES (CAST(:aid AS uuid), :tid, :st,
            (SELECT id FROM email_messages
              WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid
              ORDER BY received_at DESC LIMIT 1),
            (SELECT MAX(received_at) FROM email_messages
              WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid),
            :reason, now())
    ON CONFLICT (account_id, thread_id) DO UPDATE SET
        status = EXCLUDED.status,
        last_message_id = EXCLUDED.last_message_id,
        last_message_at = EXCLUDED.last_message_at,
        reason = EXCLUDED.reason,
        classified_at = now()
"""
# ``ORDER BY received_at DESC`` is the order of the "latest message" of the
# backfill (``replyzero._NEEDS_STATUS_SQL``), so it does not select the thread
# again. ``follow_up_reminded_at`` stays, so "Move back" gives no second nudge.


async def _write_status(
    db: Any, account_id: str, thread_id: str, status: str, reason: str,
) -> None:
    await db.execute(text(_STATUS_UPSERT_SQL), {
        "aid": account_id, "tid": thread_id, "st": status, "reason": reason})


async def _previous_state(
    db: Any, account_id: str, thread_id: str,
) -> dict[str, Any] | None:
    """What "Move back" restores: ``{status, follow_up}``, or None when the
    thread was Done already and wore no open label."""
    row = (await db.execute(text(
        "SELECT status FROM email_thread_status "
        "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid"
    ), {"aid": account_id, "tid": thread_id})).fetchone()
    labels = {lab for held in (await _thread_labels(db, account_id, thread_id)).values()
              for lab in held}
    follow_up = "Follow-up" in labels
    status = row.status if row is not None else None
    if status not in _RESTORE_LABELS:
        if labels.intersection(_NEEDS_REPLY_LABELS):
            status = "NEEDS_REPLY"
        elif follow_up or "Awaiting Reply" in labels:
            status = "AWAITING"
        elif row is not None and row.status == "DONE":
            return None
        else:
            status = "FYI"
    return {"status": status, "follow_up": follow_up}


class ThreadRestore(BaseModel):
    """The state that "Move back" asks for. The server names the labels."""

    status: Literal["NEEDS_REPLY", "AWAITING", "FYI"]
    follow_up: bool = False


async def resolve_marked(
    db: Any, background: BackgroundTasks, *, account_id: str, thread_id: str,
    done: bool, restore: ThreadRestore | None, user_email: str,
) -> dict[str, Any]:
    """``POST /email/reply-zero/resolve`` with the flag on, for Mark done and
    for "Move back". The route checked the member and the thread first.

    The status and the local labels change inside the request, so the list and
    its counts are right when the answer arrives.
    """
    if done:
        previous = await _previous_state(db, account_id, thread_id)
        await _write_status(db, account_id, thread_id, "DONE", MANUAL_DONE_REASON)
        changes = await settle_labels(db, account_id, thread_id, "Done")
        # Done closes each open task of the thread, as before. It runs last and
        # in a savepoint, so its failure cannot undo the writes above.
        from gateway.routes.tasks.email_link import propagate_thread_done_to_tasks

        with contextlib.suppress(Exception):
            async with _savepoint(db):
                await propagate_thread_done_to_tasks(
                    db, user_email, account_id, thread_id)
        background.add_task(push_labels, account_id, changes)
        return {"ok": True, "thread_id": thread_id, "done": True,
                "previous": previous}
    if restore is None:
        raise ValueError("a move back needs the state to restore")
    await _write_status(db, account_id, thread_id, restore.status, RESTORED_REASON)
    changes = await settle_labels(
        db, account_id, thread_id, _RESTORE_LABELS[restore.status],
        follow_up=restore.follow_up)
    background.add_task(push_labels, account_id, changes)
    return {"ok": True, "thread_id": thread_id, "done": False}


@router.get("/reply-zero/mark-done")
async def mark_done_state(user: UserContext = Depends(get_current_user)):
    """Whether the app shows "Mark done" to this organization."""
    return {"enabled": mark_done_on()}
