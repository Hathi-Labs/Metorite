"""Transport · storage — "remove older mail from Metorite" (WS-17 EM-T6c).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7, the part
"EM-T6c", items 8 to 13 and the gaps G1 to G5. Owner decision D-EM-14
(§10.2).

Two routes. Each one carries the owner predicate on ``user_id``, so a member
who does not own the mailbox gets 404 (D-EM-4):

* ``GET /email/accounts/{id}/storage/older?before=<date>`` returns the count
  of messages received before that date, and the bytes that their removal
  would free. It writes nothing, and it counts no draft (G1).
* ``POST /email/accounts/{id}/storage/remove-older`` with
  ``{"before": "<date>"}`` removes that mail from Metorite. It holds the
  mailbox lock of the sync (G3), and it answers 409 when a sync holds the
  mailbox for longer than ``REMOVAL_LOCK_WAIT_S``. Under the lock, its first
  block moves ``import_since``, before any delete. Then it works in chunks of
  1,000 messages, and it keeps each draft (G1). Each block is one
  ``_tenant_session(org)`` with no ``commit()``, and the organization comes
  from the account row. The last block deletes the empty thread statuses and
  the orphan drafts of the AI (G5), runs the meter, and ends the ``limit``
  phase under the limit (G4).

⚠️ **Metorite's copy ONLY (D-EM-14).** No code in this module builds a
provider or calls one. Metorite never deletes or changes mail in the Outlook
mailbox of the member. The fence is ``tests/unit/test_email_storage_limit.py``,
which reads this module and ``email_ingestion/storage.py`` for an import of
``email_ingestion.providers``, ``build_provider`` or ``provider_session``. It
also walks the calls from each route, and no provider method is reachable.

⚠️ An agent must not run the removal route on a production mailbox. That is a
production one-off (§10.4.7, Gate).
"""

from __future__ import annotations

from datetime import UTC, datetime

from acb_auth import UserContext, get_current_user
from email_ingestion import storage as ingest_storage
from fastapi import Depends, HTTPException, Query
from gateway.routes.email.core import (
    _assert_account_owner,
    _log,
    _tenant_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

#: How long a removal waits for a sync that holds the mailbox (G3). A sync
#: cycle of a mailbox at the limit fetches new mail only, so it ends in a few
#: seconds. A longer wait means an import or a deep download runs, and the
#: member tries again later. The Control Plane proxy gives a POST 30 seconds.
REMOVAL_LOCK_WAIT_S = 5.0

#: The 409 detail of a removal that a sync holds back. A member reads it.
REMOVAL_BUSY_DETAIL = (
    "A sync is running for this mailbox. Try again when it ends."
)


class OlderMailPreview(BaseModel):
    """What a removal before ``before`` would remove from Metorite."""

    #: The cutoff, as ISO text in UTC.
    before: str
    #: The messages received before the cutoff, with no draft (G1).
    messages: int
    #: The bytes of the meter that their removal would free.
    bytes: int


class RemoveOlderRequest(BaseModel):
    #: An ISO date or date and time. A value with no zone is UTC.
    before: str


class RemoveOlderResult(BaseModel):
    """The answer of a removal: the count removed and the new meter."""

    before: str
    removed: int
    stored_bytes: int | None
    storage_limit_bytes: int


def _cutoff(value: str | None) -> datetime:
    """The ``before`` of a request, in UTC. 400 when it is not a date in the
    past (EM-T6c item 9)."""
    raw = (value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="'before' must be an ISO date, for example 2026-09-01.",
        ) from None
    cutoff = (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None
              else parsed.astimezone(UTC))
    if cutoff >= datetime.now(UTC):
        raise HTTPException(
            status_code=400, detail="'before' must be a date in the past.")
    return cutoff


@router.get("/accounts/{account_id}/storage/older",
            response_model=OlderMailPreview)
async def preview_older_mail(
    account_id: str,
    before: str = Query(..., description="ISO date. Mail received before it."),
    user: UserContext = Depends(get_current_user),
) -> OlderMailPreview:
    """The count and the bytes of the mail received before ``before``.

    It writes nothing (EM-T6c item 8), and it counts no draft (G1).
    ``_assert_account_owner`` answers 404 for a mailbox that the member does
    not own."""
    cutoff = _cutoff(before)
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, user.email or "anonymous")
        older = await ingest_storage.preview_older(db, account_id, cutoff)
    return OlderMailPreview(before=cutoff.isoformat(), messages=older.messages,
                            bytes=older.bytes)


@router.post("/accounts/{account_id}/storage/remove-older",
             response_model=RemoveOlderResult)
async def remove_older_mail(
    account_id: str,
    req: RemoveOlderRequest,
    user: UserContext = Depends(get_current_user),
) -> RemoveOlderResult:
    """Remove the mail of the mailbox received before ``req.before``, from
    Metorite only (EM-T6c items 9 to 13, gaps G1 to G5).

    1. One block reads the row with the owner predicate. A row that is
       absent, or that another member owns, gives 404. The organization of
       the row binds each later block. The read comes before the lock, so a
       stranger gets 404 and never learns that a sync runs.
    2. With NO block open, the route takes the mailbox lock of the sync
       (``hold_mailbox``, G3). It waits ``REMOVAL_LOCK_WAIT_S``, then 409.
    3. Under the lock, the first block moves ``import_since`` to the later of
       itself and ``before``, before any delete (G3).
    4. Chunks of ``REMOVE_CHUNK`` messages, oldest first, with no draft (G1).
       Each chunk is one block: the ``email_executed_rules`` rows of the
       chunk, then its messages. The attachment rows and the embeddings
       cascade.
    5. One last block deletes each thread status and each draft of the AI
       whose thread has no message left (G5), runs the meter, and ends the
       ``limit`` phase when the meter is under the limit (G4).

    No block calls ``commit()``, and no block calls the provider or Mem0."""
    cutoff = _cutoff(req.before)
    owner = user.email or "anonymous"

    # ── 1. the ownership read ───────────────────────────────────────────────
    async with _tenant_session() as db:
        found = (await db.execute(
            text(
                """SELECT organization_id FROM email_accounts
                   WHERE id = :id AND user_id = :uid"""
            ),
            {"id": account_id, "uid": owner},
        )).fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="Account not found")
    org = str(found.organization_id)

    # ── 2. the mailbox lock, with no block open (G3) ────────────────────────
    from email_ingestion.scheduler import MailboxBusy, hold_mailbox

    try:
        async with hold_mailbox(account_id, wait_secs=REMOVAL_LOCK_WAIT_S):
            removed, stored = await _remove_under_lock(account_id, org, cutoff)
    except MailboxBusy:
        _log.info("email.storage.remove_busy", account_id=account_id)
        raise HTTPException(status_code=409, detail=REMOVAL_BUSY_DETAIL) from None

    _log.info("email.storage.removed_older", account_id=account_id,
              removed=removed, stored_bytes=stored)
    return RemoveOlderResult(
        before=cutoff.isoformat(), removed=removed, stored_bytes=stored,
        storage_limit_bytes=ingest_storage.storage_limit_bytes())


async def _remove_under_lock(
    account_id: str, org: str, cutoff: datetime,
) -> tuple[int, int | None]:
    """Steps 3 to 5 of ``remove_older_mail``. The caller holds the mailbox
    lock. Returns the count removed and the new meter."""
    # ── 3. the import floor, before any delete (G3) ─────────────────────────
    async with _tenant_session(org) as db:
        await ingest_storage.advance_import_since(db, account_id, cutoff)

    # ── 4. the chunks, one block each ───────────────────────────────────────
    removed = 0
    chunk = ingest_storage.REMOVE_CHUNK
    while True:
        async with _tenant_session(org) as db:
            gone = await ingest_storage.remove_older_chunk(
                db, account_id, cutoff, chunk=chunk)
        removed += gone
        if gone < chunk:
            break

    # ── 5. the orphan rows, the meter and the limit phase (G4, G5) ──────────
    async with _tenant_session(org) as db:
        await ingest_storage.delete_empty_thread_status(db, account_id)
        await ingest_storage.delete_orphan_ai_drafts(db, account_id)
        stored = await ingest_storage.measure_stored_bytes(db, account_id)
        await ingest_storage.end_limit_phase(db, account_id, stored)
    return removed, stored
