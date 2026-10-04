"""Transport · storage — "remove older mail from Metorite" (WS-17 EM-T6c).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.7, the part
"EM-T6c", items 8 to 13. Owner decision D-EM-14 (§10.2).

Two routes. Each one carries the owner predicate on ``user_id``, so a member
who does not own the mailbox gets 404 (D-EM-4):

* ``GET /email/accounts/{id}/storage/older?before=<date>`` returns the count
  of messages received before that date, and the bytes that their removal
  would free. It writes nothing.
* ``POST /email/accounts/{id}/storage/remove-older`` with
  ``{"before": "<date>"}`` removes that mail from Metorite. It works in
  chunks of 1,000 messages. Each chunk is one ``_tenant_session(org)`` block
  with no ``commit()``, and the organization comes from the account row. The
  last block deletes the empty thread statuses, moves ``import_since`` to
  ``before`` and runs the meter again.

⚠️ **Metorite's copy ONLY (D-EM-14).** No code in this module builds a
provider or calls one. Metorite never deletes or changes mail in the Outlook
mailbox of the member. The fence is ``tests/unit/test_email_storage_limit.py``,
which reads this module and ``email_ingestion/storage.py`` for an import of
``email_ingestion.providers``, ``build_provider`` or ``provider_session``.

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


class OlderMailPreview(BaseModel):
    """What a removal before ``before`` would remove from Metorite."""

    #: The cutoff, as ISO text in UTC.
    before: str
    #: The messages received before the cutoff.
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

    It writes nothing (EM-T6c item 8). ``_assert_account_owner`` answers 404
    for a mailbox that the member does not own."""
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
    Metorite only (EM-T6c items 9 to 13).

    1. One block reads the row with the owner predicate. A row that is
       absent, or that another member owns, gives 404. The organization of
       the row binds each later block.
    2. Chunks of ``REMOVE_CHUNK`` messages, oldest first. Each chunk is one
       block: the ``email_executed_rules`` rows of the chunk, then its
       messages. The attachment rows and the embeddings cascade.
    3. One last block deletes each thread status with no message left, moves
       ``import_since`` to the later of itself and ``before``, and runs the
       meter.

    No block calls ``commit()``, and no block calls the provider."""
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

    # ── 2. the chunks, one block each ───────────────────────────────────────
    removed = 0
    chunk = ingest_storage.REMOVE_CHUNK
    while True:
        async with _tenant_session(org) as db:
            gone = await ingest_storage.remove_older_chunk(
                db, account_id, cutoff, chunk=chunk)
        removed += gone
        if gone < chunk:
            break

    # ── 3. the thread statuses, the import floor and the meter ──────────────
    async with _tenant_session(org) as db:
        await ingest_storage.delete_empty_thread_status(db, account_id)
        await ingest_storage.advance_import_since(db, account_id, cutoff)
        stored = await ingest_storage.measure_stored_bytes(db, account_id)

    _log.info("email.storage.removed_older", account_id=account_id,
              removed=removed, stored_bytes=stored)
    return RemoveOlderResult(
        before=cutoff.isoformat(), removed=removed, stored_bytes=stored,
        storage_limit_bytes=ingest_storage.storage_limit_bytes())
