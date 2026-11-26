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

**The locks** (fix round 3, spec §6.6 "The locks"). The member's request
must not wait for provider or model I/O, and it must not deadlock:

- The request WAITS only for the status row. It takes the message rows of the
  thread with ``NOWAIT`` (:func:`_take_thread_rows`). When a writer holds one,
  the request leaves the local labels to its label job.
- An automated writer takes the status row LAST in its block, after each
  message row and each I/O, or in a block of its own. So the request waits at
  most for one statement and a commit.
- A label job takes the message rows with ``NOWAIT`` too, and tries again
  later. A lock that never waits is in no cycle.

**One mechanism heals each race.** An automated writer that asked the hold
before its I/O asks again after it, and puts "Done" back
(:func:`recheck_after_io`). Each sync cycle sweeps the held threads that still
wear an open label (:func:`heal_held`). A label job makes the provider show
the committed local labels, then reads them again, and sends again while they
changed (:func:`converge`). So no older job wins.

⚠️ ``replyzero`` imports this module, so this module imports ``replyzero``
inside its functions only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import BackgroundTasks, Depends, HTTPException
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

#: The reason after a "Move back" that put "Follow-up" back. A job that must
#: put the labels of the move back again reads the Follow-up choice here.
RESTORED_FOLLOW_UP_REASON = "Moved back to Follow-up by you"

#: The reason after a reopen (``done=false`` with no ``restore``). A reopen is
#: a write of the member, as a move back is, so its labels are the member's.
REOPENED_REASON = "Reopened by you"

#: The reasons of a write of the member through this module.
_RESTORED_REASONS = (RESTORED_REASON, RESTORED_FOLLOW_UP_REASON, REOPENED_REASON)
_MEMBER_REASONS = (MANUAL_DONE_REASON, *_RESTORED_REASONS)

#: How long after a move back the sync reads a label that disagrees with the
#: status as a stale one. The label job of the move back runs in this time.
PENDING_PUSH_WINDOW = "10 minutes"

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

#: Each label of the status of a conversation, the old names, and "Follow-up".
#: On a thread that the member owns, the mirror is the truth for these.
_CONVERSATION_LABELS = (
    "Needs Reply", "Awaiting Reply", "Done", "FYI",
    "Reply", "To Reply", "Actioned", "Follow-up")
_CONVERSATION_LOWER = frozenset(lab.lower() for lab in _CONVERSATION_LABELS)

#: The labels that a held thread must not wear.
_NOT_DONE_LABELS = tuple(lab for lab in _CONVERSATION_LABELS if lab != "Done")

#: How many held threads one sweep puts right.
_HEAL_LIMIT = 50

#: The waits, in seconds, of a label job of the member before each try to take
#: the message rows. A writer that holds them asks the hold after its I/O, so
#: after the last try the job leaves the rows to that writer and the sweep.
SETTLE_DELAYS: tuple[float, ...] = (0, 0.2, 0.5, 1, 2, 4, 8)

#: The reads of :func:`converge`: one push, and the reads that check it.
_CONVERGE_ROUNDS = 3

#: SQLSTATE ``lock_not_available``: a ``NOWAIT`` found the row locked.
_LOCK_NOT_AVAILABLE = "55P03"


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

def from_other_sql(m: str) -> str:
    """The SQL test "the message *m* comes from another person".

    The sender decides, not the folder. A copy of the member's own mail in the
    inbox, the archive, the trash or junk is still the member's own (D7). The
    self set is the one of ``identity`` (D-EM-27). A draft and a Sent copy are
    the member's own too, for a sender that the self set does not name.
    """
    from gateway.routes.email.automation.identity import _member_mailboxes_from

    return (
        f"(LOWER(COALESCE({m}.folder, '')) NOT IN ('sent', 'drafts') "
        f"AND COALESCE(LOWER({m}.from_address->>'email'), '') NOT IN "
        f"(SELECT LOWER(o.email_address) "
        f"{_member_mailboxes_from(f'{m}.account_id')}))")


def held_sql(row: str) -> str:
    """The SQL test "this status row is held", over the table or alias *row*.

    The anchor is ``last_message_at`` of the row, which Mark done sets to the
    newest classified ``received_at`` of the thread (:func:`_anchor_sql`). A
    message from another person that is newer ends the hold. The text holds
    no bind parameter, so it fits inside any statement.
    """
    return (
        f"({row}.status = 'DONE' AND {row}.reason = '{MANUAL_DONE_REASON}' "
        "AND NOT EXISTS (SELECT 1 FROM email_messages held_m "
        f"WHERE held_m.account_id = {row}.account_id "
        f"AND held_m.thread_id = {row}.thread_id "
        f"AND held_m.received_at > COALESCE({row}.last_message_at, "
        "CAST('-infinity' AS timestamptz)) "
        f"AND {from_other_sql('held_m')}))")


def owned_sql(row: str, *, recent: bool) -> str:
    """The SQL test "the member owns the labels of this row": it is held, or a
    move back wrote it. With *recent*, only a move back inside
    :data:`PENDING_PUSH_WINDOW` counts."""
    restored = (f"{row}.reason IN ('{RESTORED_REASON}', "
                f"'{RESTORED_FOLLOW_UP_REASON}')")
    if recent:
        restored += (f" AND {row}.classified_at > now() - "
                     f"CAST('{PENDING_PUSH_WINDOW}' AS interval)")
    return f"({held_sql(row)} OR ({restored}))"


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
    automated: bool, status_expr: str, reminder_expr: str,
) -> tuple[str, str, str, str, str]:
    """The SET expressions of the status upsert for ``status``,
    ``last_message_at``, ``reason``, ``classified_at`` and
    ``follow_up_reminded_at``. *reminder_expr* is the plain expression of the
    last one.

    An automated write keeps all five values of a held row. It still moves
    ``last_message_id``, so the backfill does not select the thread again.
    The reminder stays too, so that move does not re-arm a nudge (F6). With
    the flag off, or for a write of the member, the expressions are the plain
    ones, so the statement is the statement of before.
    """
    plain = (status_expr, "EXCLUDED.last_message_at", "EXCLUDED.reason", "now()",
             reminder_expr)
    if not (automated and mark_done_on()):
        return plain
    held = held_sql("email_thread_status")
    status, at, reason, classified, reminded = (
        f"CASE WHEN {held} THEN email_thread_status.{column} ELSE {new} END"
        for column, new in zip(
            ("status", "last_message_at", "reason", "classified_at",
             "follow_up_reminded_at"), plain,
            strict=True))
    return status, at, reason, classified, reminded


def automated_reason(reason: str) -> str:
    """The reason that an automated write may store. The marks of the member
    are the member's alone. A model writes the reason of a rule match, so mail
    text must not be able to hold a thread or claim a move back. With the flag
    off the reason stands."""
    if reason in _MEMBER_REASONS and mark_done_on():
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
    block, and return what changed. :func:`push_labels` or :func:`settle_now`
    then tells the provider.

    It runs ``replyzero._reconcile_thread_labels`` with no provider, so the
    rule of the labels stays in one place. That reconcile computes its removals
    from a read, so a label that another block wrote while this one waited for
    the row lock stays. A second statement removes each such label, and the
    change names it, so the provider loses it too. ``follow_up`` puts
    "Follow-up" back on the latest message that is not a draft.
    """
    from gateway.routes.email.automation.replyzero import _reconcile_thread_labels

    before = await _thread_labels(db, account_id, thread_id)
    await _reconcile_thread_labels(db, None, account_id, thread_id, keep_label)
    # A new statement: it reads each label that a block committed meanwhile.
    seen = await _thread_labels(db, account_id, thread_id)
    keep = await held_label(db, account_id, thread_id, keep_label) or keep_label
    stale = [lab for lab in _CONVERSATION_LABELS if lab != keep]
    if keep == "Awaiting Reply" or follow_up:
        stale.remove("Follow-up")
    await db.execute(text(
        "UPDATE email_messages SET categories = ARRAY("
        "  SELECT c FROM unnest(categories) AS c WHERE NOT (c = ANY(:rm))"
        "), updated_at = now() "
        "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
        "AND categories && CAST(:rm AS text[])"
    ), {"aid": account_id, "tid": thread_id, "rm": stale})
    if follow_up:
        await db.execute(text(
            "UPDATE email_messages SET categories = CASE "
            "WHEN 'Follow-up' = ANY(categories) THEN categories "
            "ELSE array_append(categories, 'Follow-up') END, updated_at = now() "
            "WHERE id = (SELECT id FROM email_messages "
            "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
            "AND LOWER(COALESCE(folder, '')) <> 'drafts' "
            "ORDER BY received_at DESC NULLS LAST LIMIT 1)"
        ), {"aid": account_id, "tid": thread_id})
    after = await _thread_labels(db, account_id, thread_id)
    changes = []
    for message_id, labels in after.items():
        old = before.get(message_id, [])
        had = set(old) | set(seen.get(message_id, []))
        add = tuple(lab for lab in labels if lab not in old)
        remove = tuple(sorted(lab for lab in had if lab not in labels))
        if add or remove:
            changes.append(LabelChange(message_id, add, remove))
    return changes


# ── Taking the rows of a thread ──────────────────────────────────────────────

def _lock_busy(err: BaseException) -> bool:
    """True when *err*, or a driver error under it, is SQLSTATE 55P03.
    SQLAlchemy wraps the driver error, so the check walks ``orig`` and
    ``__cause__``."""
    seen: BaseException | None = err
    for _ in range(5):
        if seen is None:
            return False
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if code == _LOCK_NOT_AVAILABLE:
            return True
        seen = getattr(seen, "orig", None) or seen.__cause__
    return False


async def _take_thread_rows(db: Any, account_id: str, thread_id: str) -> bool:
    """Lock each message row of the thread for this block, or none of them.

    ``NOWAIT``: when another block holds one of the rows, this returns False
    at once, and the block goes on with no lock. A lock that never waits is in
    no cycle, so the member's request and the label jobs cannot deadlock with
    a writer (§6.6 "The locks"). Inside a savepoint, so a refusal does not
    abort the block.

    ``FOR NO KEY UPDATE``: the block then writes ``categories`` and
    ``updated_at`` only, which are no key columns. So the lock does not
    conflict with the ``FOR KEY SHARE`` that each insert of a child row
    takes on its message row.
    """
    try:
        async with _savepoint(db):
            await db.execute(text(
                "SELECT id FROM email_messages "
                "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
                "ORDER BY id FOR NO KEY UPDATE NOWAIT"
            ), {"aid": account_id, "tid": thread_id})
    except Exception as exc:
        if _lock_busy(exc):
            return False
        raise
    return True


# ── The provider: it shows the local labels ──────────────────────────────────

async def _mirror_state(
    db: Any, account_id: str, thread_id: str,
) -> dict[str, frozenset[str]]:
    """The conversation labels of each message of the thread, by provider id,
    as *db* reads them. A draft is left out: no conversation label goes on a
    draft (D3), and the provider gives a draft a new id at each save."""
    rows = (await db.execute(text(
        "SELECT provider_message_id, categories FROM email_messages "
        "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
        "AND provider_message_id IS NOT NULL "
        "AND LOWER(COALESCE(folder, '')) <> 'drafts'"
    ), {"aid": account_id, "tid": thread_id})).fetchall()
    return {r.provider_message_id: frozenset(
        lab for lab in (r.categories or []) if lab in _CONVERSATION_LABELS)
        for r in rows}


async def _push_state(provider: Any, state: dict[str, frozenset[str]]) -> None:
    """Send each message its whole set of conversation labels: the ones it
    has, and the removal of the rest. Best effort for each message."""
    for pmid, labels in sorted(state.items()):
        with contextlib.suppress(Exception):
            await provider.set_labels(
                pmid, add=sorted(labels),
                remove=[lab for lab in _CONVERSATION_LABELS if lab not in labels])


async def converge(provider: Any, account_id: str, thread_id: str) -> None:
    """Make the provider show the COMMITTED local labels of the thread.

    It reads them in a block of its own, sends them with no block open, and
    reads them again. While the read differs from what it sent, it sends
    again, up to :data:`_CONVERGE_ROUNDS` reads. A writer that changed the
    labels committed before it sent them, so the last read sees each such
    change. So an older job never leaves its labels at the provider over the
    labels of a newer write, of the member or of a writer (D2a, F3).
    """
    sent: dict[str, frozenset[str]] | None = None
    for _ in range(_CONVERGE_ROUNDS):
        async with _tenant_session() as db:
            state = await _mirror_state(db, account_id, thread_id)
        if state == sent:
            return
        await _push_state(provider, state)
        sent = state


# ── The writers: ask the hold again after the I/O ────────────────────────────

async def recheck_after_io(
    db: Any, provider: Any, account_id: str, thread_ids: list[str],
) -> set[str]:
    """Ask the hold again after provider or model I/O, inside the writer's
    block, and settle each thread that is held now: "Done" goes back in the
    block, and *provider* gets the whole state of the thread at once.
    *provider* may be None (the local labels only). Returns those threads.
    No read with the flag off.

    The member's request does not wait for the rows of a writer, so it
    commits during the I/O, and this ask sees it (F2)."""
    held = await held_threads(db, account_id, sorted({t for t in thread_ids if t}))
    for thread_id in sorted(held):
        await settle_labels(db, account_id, thread_id, "Done")
        if provider is not None:
            await _push_state(
                provider, await _mirror_state(db, account_id, thread_id))
    return held


async def recheck_in_own_block(
    provider: Any, account_id: str, thread_ids: list[str],
) -> set[str]:
    """:func:`recheck_after_io` for a writer that holds no row: each held
    thread settles in a short block of its own, and the provider hears the
    committed state with no block open (:func:`converge`). The follow-up scan
    uses this with the flag on. No block with the flag off."""
    ids = sorted({t for t in thread_ids if t})
    if not ids or not mark_done_on():
        return set()
    async with _tenant_session() as db:
        held = await held_threads(db, account_id, ids)
    for thread_id in sorted(held):
        async with _tenant_session() as db:
            await settle_labels(db, account_id, thread_id, "Done")
        if provider is not None:
            await converge(provider, account_id, thread_id)
    return held


# ── The label job of the member ──────────────────────────────────────────────

async def _provider_for(account_id: str) -> tuple[Any, Any] | None:
    async with _tenant_session() as db:
        acc = (await db.execute(text(
            "SELECT provider, credentials_encrypted FROM email_accounts "
            "WHERE id = :id"
        ), {"id": account_id})).fetchone()
    if not acc:
        return None
    from acb_llm.key_store import get_key_store

    store = get_key_store()
    creds = json.loads(store.decrypt(acc.credentials_encrypted))
    provider = _instantiate_provider(acc.provider, creds)
    if not await provider.authenticate():
        _log.warning("email.mark_done_labels_auth_failed", account_id=account_id)
        return None
    return provider, store


async def _own_labels(
    db: Any, account_id: str, thread_id: str,
) -> tuple[str, bool] | None:
    """The label and the Follow-up choice of a row that the member owns, or
    None when another write wrote the row last."""
    row = (await db.execute(text(
        "SELECT s.status, s.reason FROM email_thread_status s "
        "WHERE s.account_id = CAST(:aid AS uuid) AND s.thread_id = :tid "
        f"AND {owned_sql('s', recent=False)}"
    ), {"aid": account_id, "tid": thread_id})).fetchone()
    if row is None:
        return None
    if row.status == "DONE":
        return "Done", False
    label = _RESTORE_LABELS.get(row.status)
    if label is None:
        return None
    return label, row.reason == RESTORED_FOLLOW_UP_REASON


async def _settle_mirror(
    account_id: str, thread_id: str, delays: tuple[float, ...],
) -> bool:
    """Put the labels of the member's last write on the local rows, in a
    short block. It takes the rows with ``NOWAIT``, and waits *delays* between
    tries. True when the rows are right, or when another write owns them now.
    False when a writer held the rows at each try: that writer asks the hold
    after its I/O, and the sweep heals what is left."""
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        async with _tenant_session() as db:
            own = await _own_labels(db, account_id, thread_id)
            if own is None:
                return True
            if not await _take_thread_rows(db, account_id, thread_id):
                continue
            await settle_labels(db, account_id, thread_id, own[0], follow_up=own[1])
            return True
    _log.info("email.mark_done_rows_busy", account_id=account_id)
    return False


async def push_labels(
    account_id: str, thread_id: str,
    delays: tuple[float, ...] | None = None,
) -> None:
    """Background, after a write of the member: the local labels, then the
    provider. Best effort.

    It settles the local rows to the member's LAST write, not to the write
    that queued it (:func:`_settle_mirror`). Then the provider shows the
    committed rows (:func:`converge`). The provider calls run with no block
    open. *delays* defaults to :data:`SETTLE_DELAYS`.
    """
    await _push_threads(
        account_id, [thread_id], SETTLE_DELAYS if delays is None else delays)


async def _push_threads(
    account_id: str, thread_ids: list[str], delays: tuple[float, ...],
) -> None:
    """:func:`push_labels` for each of *thread_ids*, with ONE provider. It
    builds and authenticates the provider at the first thread that needs it.
    A fault on one thread does not stop the others. Best effort."""
    got: tuple[Any, Any] | None = None
    for thread_id in thread_ids:
        try:
            if not await _settle_mirror(account_id, thread_id, delays):
                continue
            if got is None:
                got = await _provider_for(account_id)
                if got is None:
                    return
            await converge(got[0], account_id, thread_id)
        except Exception as exc:
            _log.warning("email.mark_done_labels_failed",
                         account_id=account_id, error=str(exc)[:160])
    if got is None:
        return
    provider, store = got
    try:
        if provider.credentials_dirty():
            async with _tenant_session() as db:
                await _persist_rotated_creds(db, store, account_id, provider)
    except Exception as exc:
        _log.warning("email.mark_done_labels_failed",
                     account_id=account_id, error=str(exc)[:160])


# ── The sync: a stale label that comes back ──────────────────────────────────

async def owned_among(account_id: str, thread_ids: list[str]) -> set[str]:
    """The threads of *thread_ids* whose labels the member owns now: each held
    one, and each one that a move back or a reopen wrote inside
    :data:`PENDING_PUSH_WINDOW`. A block of its own. No block with the flag off.
    """
    ids = [t for t in thread_ids if t]
    if not ids or not mark_done_on():
        return set()
    async with _tenant_session() as db:
        rows = (await db.execute(text(
            "SELECT s.thread_id FROM email_thread_status s "
            "WHERE s.account_id = CAST(:aid AS uuid) AND s.thread_id = ANY(:tids) "
            f"AND {owned_sql('s', recent=True)}"
        ), {"aid": account_id, "tids": ids})).fetchall()
    return {r.thread_id for r in rows}


async def settle_owned(account_id: str, thread_id: str) -> None:
    """Put the labels of the member's last write back on the thread, here and
    at the provider. One try: the sync does not wait for a busy row, and the
    next cycle tries again. Best effort."""
    await push_labels(account_id, thread_id, delays=(0,))


async def reclean_returned_labels(account_id: str, changes: list) -> set[str]:
    """A sync changed a conversation label on some messages. For each thread
    among them whose labels the member owns, put the member's labels back.
    Returns those threads, so the learner learns nothing from them.

    *changes* is the list of ``(message, old_categories)`` pairs of the sync.
    The provider may still hold an old label because a label job had not run
    yet, or ran out of order. With the flag off it does nothing.
    """
    if not changes or not mark_done_on():
        return set()
    touched: set[str] = set()
    for message, old in changes:
        had = {str(c).strip().lower() for c in (old or [])}
        has = {str(c).strip().lower()
               for c in (getattr(message, "categories", None) or [])}
        thread_id = getattr(message, "thread_id", None)
        if thread_id and (has ^ had) & _CONVERSATION_LOWER:
            touched.add(thread_id)
    try:
        owned = await owned_among(account_id, sorted(touched))
    except Exception as exc:
        _log.warning("email.mark_done_reclean_failed",
                     account_id=account_id, error=str(exc)[:160])
        return set()
    for thread_id in sorted(owned):
        await settle_owned(account_id, thread_id)
    return owned


async def heal_held(account_id: str) -> int:
    """The sweep: each held thread of the mailbox that still wears an open
    label in the mirror gets "Done" back, here and at the provider.

    A label can reach a held thread with no delta for the learner to see: an
    import, a folder load, or a writer that asked the hold before its I/O.
    Each sync cycle runs this, after the follow-up scan. It reads one statement
    when nothing is wrong. Returns the count of threads. No read with the flag
    off.
    """
    if not mark_done_on():
        return 0
    try:
        async with _tenant_session() as db:
            rows = (await db.execute(text(
                "SELECT s.thread_id FROM email_thread_status s "
                "WHERE s.account_id = CAST(:aid AS uuid) AND s.status = 'DONE' "
                f"AND s.reason = '{MANUAL_DONE_REASON}' "
                "AND EXISTS (SELECT 1 FROM email_messages m "
                "WHERE m.account_id = s.account_id AND m.thread_id = s.thread_id "
                "AND m.categories && CAST(:open AS text[])) "
                f"AND {held_sql('s')} "
                "ORDER BY s.thread_id LIMIT :lim"
            ), {"aid": account_id, "open": list(_NOT_DONE_LABELS),
                "lim": _HEAL_LIMIT})).fetchall()
    except Exception as exc:
        _log.warning("email.mark_done_heal_failed",
                     account_id=account_id, error=str(exc)[:160])
        return 0
    # One provider for the sweep, not one for each thread.
    await _push_threads(account_id, [r.thread_id for r in rows], (0,))
    return len(rows)


# ── The two writes of the member ─────────────────────────────────────────────

#: The latest message, in the order of the backfill (``replyzero.
#: _NEEDS_STATUS_SQL``), so the backfill does not select the thread again.
_LATEST_SQL = """
    (SELECT id FROM email_messages
      WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid
      ORDER BY received_at DESC LIMIT 1)"""


def _anchor_sql() -> str:
    """The newest ``received_at`` of the thread that the automation has seen:
    the member's own mail, and mail that the rules or a status write accounted
    for. Inbound mail that waits for its classification is left out, so it
    ends the hold and its own classification sets the status (D8). Such mail
    has no ``rules_processed_at``, and it arrived and was stored after the last
    write of the status. A thread with no status row counts each message as
    seen."""
    since = ("COALESCE((SELECT p.classified_at FROM email_thread_status p "
             "WHERE p.account_id = CAST(:aid AS uuid) AND p.thread_id = :tid), "
             "CAST('infinity' AS timestamptz))")
    return f"""
    (SELECT MAX(m.received_at) FROM email_messages m
      WHERE m.account_id = CAST(:aid AS uuid) AND m.thread_id = :tid
        AND NOT ({from_other_sql('m')}
                 AND m.rules_processed_at IS NULL
                 AND m.created_at > {since}
                 AND m.received_at > {since}))"""


def _done_upsert_sql() -> str:
    """The write of Mark done. ``follow_up_reminded_at`` stays, so "Move back"
    gives no second nudge."""
    return f"""
    INSERT INTO email_thread_status
        (account_id, thread_id, status, last_message_id, last_message_at,
         reason, classified_at)
    VALUES (CAST(:aid AS uuid), :tid, 'DONE', {_LATEST_SQL}, {_anchor_sql()},
            :reason, now())
    ON CONFLICT (account_id, thread_id) DO UPDATE SET
        status = EXCLUDED.status,
        last_message_id = EXCLUDED.last_message_id,
        last_message_at = EXCLUDED.last_message_at,
        reason = EXCLUDED.reason,
        classified_at = now()
    RETURNING classified_at
"""


def _restore_sql() -> str:
    """The write of a move back. Its WHERE asks the hold in the write itself,
    so a move back of a thread that new mail or another write changed since
    Done finds no row (D4). A message of the member that is newer than the
    anchor finds no row either (F4): the member replied after Done, so the
    state of before is no longer true. A draft does not count."""
    return f"""
    UPDATE email_thread_status SET
        status = :st,
        last_message_id = {_LATEST_SQL},
        last_message_at = (SELECT MAX(received_at) FROM email_messages
                            WHERE account_id = CAST(:aid AS uuid)
                              AND thread_id = :tid),
        reason = :reason,
        classified_at = now()
    WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid
      AND {held_sql('email_thread_status')}
      AND NOT EXISTS (SELECT 1 FROM email_messages newer
                       WHERE newer.account_id = email_thread_status.account_id
                         AND newer.thread_id = email_thread_status.thread_id
                         AND LOWER(COALESCE(newer.folder, '')) <> 'drafts'
                         AND newer.received_at > COALESCE(
                             email_thread_status.last_message_at,
                             CAST('-infinity' AS timestamptz)))
    RETURNING classified_at
"""


#: The write of a reopen with the flag on. The status is the one of before
#: (``replyzero.resolve_thread``). The reason makes it a write of the member.
_REOPEN_SQL = """
    UPDATE email_thread_status SET status = :st, reason = :reason,
        classified_at = now()
    WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid
    RETURNING classified_at
"""


#: The answer of a move back that found the thread changed.
MOVED_ON_DETAIL = (
    "This conversation changed after you marked it done, so it was not moved back.")


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


async def _settle_in_request(
    db: Any, account_id: str, thread_id: str, keep_label: str,
    *, follow_up: bool = False,
) -> None:
    """The local labels, inside the request, when no writer holds a row of
    the thread. When one does, the request does not wait: the label job
    settles them after the writer commits (:func:`push_labels`)."""
    if await _take_thread_rows(db, account_id, thread_id):
        await settle_labels(db, account_id, thread_id, keep_label,
                            follow_up=follow_up)


async def resolve_marked(
    db: Any, background: BackgroundTasks, *, account_id: str, thread_id: str,
    done: bool, restore: ThreadRestore | None, user_email: str,
) -> dict[str, Any]:
    """``POST /email/reply-zero/resolve`` with the flag on, for Mark done,
    "Move back" and a reopen. The route checked the member and the thread
    first.

    The request waits only for the status row (§6.6 "The locks"). It writes
    the status. Then it takes the message rows with ``NOWAIT``, and when it
    gets them it sets the local labels, so the list is right when the answer
    arrives. When a writer holds a row, the label job sets them after that
    writer commits. Either way the provider hears in the label job. A move
    back of a thread that is no longer held answers 409 and writes nothing.
    """
    params = {"aid": account_id, "tid": thread_id}
    if done:
        previous = await _previous_state(db, account_id, thread_id)
        await db.execute(text(_done_upsert_sql()), {
            **params, "reason": MANUAL_DONE_REASON})
        await _settle_in_request(db, account_id, thread_id, "Done")
        # Done closes each open task of the thread, as before. It runs last and
        # in a savepoint, so its failure cannot undo the writes above.
        from gateway.routes.tasks.email_link import propagate_thread_done_to_tasks

        with contextlib.suppress(Exception):
            async with _savepoint(db):
                await propagate_thread_done_to_tasks(
                    db, user_email, account_id, thread_id)
        background.add_task(push_labels, account_id, thread_id)
        return {"ok": True, "thread_id": thread_id, "done": True,
                "previous": previous}
    if restore is None:
        return await _reopen(db, background, account_id, thread_id)
    reason = RESTORED_FOLLOW_UP_REASON if restore.follow_up else RESTORED_REASON
    written = (await db.execute(text(_restore_sql()), {
        **params, "st": restore.status, "reason": reason})).scalar()
    if written is None:
        raise HTTPException(status_code=409, detail=MOVED_ON_DETAIL)
    await _settle_in_request(
        db, account_id, thread_id, _RESTORE_LABELS[restore.status],
        follow_up=restore.follow_up)
    background.add_task(push_labels, account_id, thread_id)
    return {"ok": True, "thread_id": thread_id, "done": False}


async def _reopen(
    db: Any, background: BackgroundTasks, account_id: str, thread_id: str,
) -> dict[str, Any]:
    """A reopen (``done=false`` with no ``restore``) with the flag on (F3).

    The status is the one of before: Awaiting when the latest dated message
    is in Sent, else Needs Reply. The reason :data:`REOPENED_REASON` makes it
    a write of the member, so the label job pushes its labels over each older
    job, and the hold ends. With no status row, nothing is written, and the
    labels follow the reconcile of before.
    """
    from gateway.routes.email.automation.replyzero import _reconcile_labels_bg

    latest = (await db.execute(text(
        "SELECT folder FROM email_messages "
        "WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid "
        "ORDER BY received_at DESC NULLS LAST LIMIT 1"
    ), {"aid": account_id, "tid": thread_id})).fetchone()
    status = "AWAITING" if (
        latest and (latest.folder or "").lower() == "sent") else "NEEDS_REPLY"
    written = (await db.execute(text(_REOPEN_SQL), {
        "aid": account_id, "tid": thread_id, "st": status,
        "reason": REOPENED_REASON})).scalar()
    if written is None:
        background.add_task(
            _reconcile_labels_bg, account_id, thread_id, _RESTORE_LABELS[status])
        return {"ok": True, "thread_id": thread_id, "done": False}
    await _settle_in_request(db, account_id, thread_id, _RESTORE_LABELS[status])
    background.add_task(push_labels, account_id, thread_id)
    return {"ok": True, "thread_id": thread_id, "done": False}


@router.get("/reply-zero/mark-done")
async def mark_done_state(user: UserContext = Depends(get_current_user)):
    """Whether the app shows "Mark done" to this organization."""
    return {"enabled": mark_done_on()}
