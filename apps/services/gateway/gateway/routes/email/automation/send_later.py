"""Send later: a draft that goes at a time the member chose (H-375, §19).

Owner, 2026-10-20: the chat must do each email task. "Send this tomorrow at
9" had no backend. This module is the whole of it:

* ``POST /email/drafts/schedule`` records one draft, its time, and the To,
  Cc and Bcc that the member's card showed (``expect``). The caller's own
  mailbox only, a draft only, a time in the future only. One open schedule
  for each draft (a unique index), so a draft never goes twice.
* ``GET /email/drafts/scheduled`` lists the caller's open schedules.
* ``POST /email/drafts/scheduled/{id}/cancel`` cancels a queued one.
* The job (:func:`run_tick`, every :data:`TICK_SECONDS`) sends each due row
  through the ONE draft-send path, ``send_draft_endpoint``, with ``expect``.
  So a draft whose recipients changed after the card answers 409, and the
  row is ``failed``, never sent to new people.

**No double send.** The job claims a row with ``FOR UPDATE SKIP LOCKED`` and
moves it to ``sending`` in the same transaction, so two gateway workers never
take one row. A row left in ``sending`` (a crash in the send) is marked
``failed`` after :data:`STUCK_AFTER`, and never sent again: a mail that might
have gone must not go twice. The member sees it in the list.

**Tenancy (R5).** The job reads the orgs from the one tenant-discovery read of
email (``email_ingestion.scheduler._list_organizations``). Every statement
runs in ``tenant_session(org)``, and the send runs with the org bound. The
member of a send is the ``owner_email`` of the row, which the schedule route
took from the session, never from request input.

**Dark by default.** ``email_send_later`` is off. Then the routes answer 404,
and :func:`start_send_later_loop` starts no task.
Fence: ``tests/unit/test_email_send_later.py`` (hermetic and R8).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_auth.roles import UserRole
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import BackgroundTasks, Depends, HTTPException
from gateway.routes.email.automation.drafting import (
    DraftExpect,
    DraftSendRequest,
    _assert_account_owner,
    send_draft_endpoint,
)
from gateway.routes.email.core import _log, _tenant_session, router
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: Seconds between two ticks of the job.
TICK_SECONDS = 60
#: The most rows that one org sends in one tick.
BATCH = 20
#: A row in ``sending`` for this long crashed in the send. It is never retried.
STUCK_AFTER = timedelta(minutes=15)
#: The latest time that a schedule may name.
MAX_AHEAD = timedelta(days=365)
#: The seconds that a stop waits for a tick in progress.
STOP_GRACE_SECONDS = 30

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def send_later_enabled() -> bool:
    """The switch of the whole feature (``email_send_later``, default off)."""
    return bool(getattr(get_settings(), "email_send_later", False))


def _off() -> None:
    if not send_later_enabled():
        raise HTTPException(status_code=404, detail="Send later is not on.")


def _when(value: str) -> datetime:
    """A schedule time: ISO 8601 with an offset, in the future, within a year."""
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="send_at is not an ISO 8601 time.") from None
    if when.tzinfo is None:
        raise HTTPException(status_code=422, detail="send_at needs an offset, such as +05:30.")
    now = datetime.now(UTC)
    if when <= now:
        raise HTTPException(status_code=422, detail="send_at has passed.")
    if when > now + MAX_AHEAD:
        raise HTTPException(status_code=422, detail="send_at is more than a year ahead.")
    return when


class ScheduleRequest(BaseModel):
    """One draft to send later. ``expect`` is what the member's card showed."""

    model_config = ConfigDict(extra="forbid")

    account_id: str
    draft_id: str
    send_at: str
    expect: DraftExpect


class ScheduleRow(BaseModel):
    id: str
    account_id: str
    draft_id: str
    send_at: str
    status: str
    subject: str = ""
    to: list[str] = Field(default_factory=list)
    error: str | None = None


def _row(r: Any) -> ScheduleRow:
    exp = r.expect if isinstance(r.expect, dict) else json.loads(r.expect or "{}")
    return ScheduleRow(
        id=str(r.id), account_id=str(r.account_id), draft_id=str(r.draft_id),
        send_at=r.send_at.isoformat(), status=r.status,
        subject=str(getattr(r, "subject", "") or ""),
        to=[str(a) for a in exp.get("to") or []], error=r.error)


@router.post("/drafts/schedule", response_model=ScheduleRow)
async def schedule_draft(
    req: ScheduleRequest,
    user: UserContext = Depends(get_current_user),
) -> ScheduleRow:
    """Schedule one draft of the caller's own mailbox. A second open
    schedule of the same draft is a 409."""
    _off()
    owner = (user.email or "").strip().lower()
    if not owner:
        raise HTTPException(status_code=401, detail="No member.")
    when = _when(req.send_at)
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, user.email or "anonymous")
        draft = (await db.execute(text(
            "SELECT id, subject FROM email_messages WHERE id = CAST(:id AS uuid) "
            "AND account_id = CAST(:aid AS uuid) AND LOWER(folder) IN ('drafts', 'draft')"
        ), {"id": req.draft_id, "aid": req.account_id})).fetchone()
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        taken = (await db.execute(text(
            "SELECT 1 FROM email_scheduled_sends WHERE draft_id = CAST(:id AS uuid) "
            "AND status IN ('queued', 'sending')"), {"id": req.draft_id})).fetchone()
        if taken is not None:
            raise HTTPException(status_code=409, detail="This draft is already scheduled.")
        row = (await db.execute(text(
            "INSERT INTO email_scheduled_sends "
            "(account_id, draft_id, owner_email, send_at, expect, subject) VALUES "
            "(CAST(:aid AS uuid), CAST(:did AS uuid), :owner, :at, CAST(:exp AS jsonb), "
            ":subject) "
            "RETURNING id, account_id, draft_id, send_at, status, expect, error, subject"
        ), {"aid": req.account_id, "did": req.draft_id, "owner": owner, "at": when,
            "exp": json.dumps(req.expect.model_dump()),
            "subject": str(draft.subject or "")[:500]})).fetchone()
    out = _row(row)
    _log.info("email.send_later_scheduled", schedule_id=out.id)
    return out


@router.get("/drafts/scheduled", response_model=list[ScheduleRow])
async def list_scheduled(
    user: UserContext = Depends(get_current_user),
) -> list[ScheduleRow]:
    """The caller's open schedules, and those that failed in the last week."""
    _off()
    owner = (user.email or "").strip().lower()
    async with _tenant_session() as db:
        rows = (await db.execute(text(
            "SELECT s.id, s.account_id, s.draft_id, s.send_at, s.status, s.expect, "
            "s.error, s.subject FROM email_scheduled_sends s "
            "WHERE s.owner_email = :owner AND (s.status IN ('queued', 'sending') "
            "OR (s.status = 'failed' AND s.updated_at > now() - interval '7 days')) "
            "ORDER BY s.send_at LIMIT 100"), {"owner": owner})).fetchall()
    return [_row(r) for r in rows]


@router.post("/drafts/scheduled/{schedule_id}/cancel", response_model=ScheduleRow)
async def cancel_scheduled(
    schedule_id: str,
    user: UserContext = Depends(get_current_user),
) -> ScheduleRow:
    """Cancel one queued schedule of the caller. A row that is sending, sent
    or done is a 409, and a row of another member is a 404."""
    _off()
    owner = (user.email or "").strip().lower()
    async with _tenant_session() as db:
        row = (await db.execute(text(
            "SELECT id, status FROM email_scheduled_sends "
            "WHERE id = CAST(:id AS uuid) AND owner_email = :owner FOR UPDATE"
        ), {"id": schedule_id, "owner": owner})).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        if row.status != "queued":
            raise HTTPException(status_code=409, detail=f"This schedule is {row.status}.")
        done = (await db.execute(text(
            "UPDATE email_scheduled_sends SET status = 'cancelled', updated_at = now() "
            "WHERE id = CAST(:id AS uuid) "
            "RETURNING id, account_id, draft_id, send_at, status, expect, error, subject"
        ), {"id": schedule_id})).fetchone()
    return _row(done)


# ── The job ──────────────────────────────────────────────────────────────────


async def _orgs() -> list[str]:
    from email_ingestion.scheduler import _list_organizations  # noqa: PLC0415

    return await _list_organizations()


async def _claim(org: str) -> list[Any]:
    """Due rows of *org*, moved to ``sending`` in one transaction."""
    async with tenant_session(org) as db:
        await db.execute(text(
            "UPDATE email_scheduled_sends SET status = 'failed', updated_at = now(), "
            "error = 'The send stopped part way. It was not sent again, so it does "
            "not go twice. Check Sent, and send it by hand if it is not there.' "
            "WHERE status = 'sending' AND updated_at < :stuck"),
            {"stuck": datetime.now(UTC) - STUCK_AFTER})
        return list((await db.execute(text(
            "UPDATE email_scheduled_sends SET status = 'sending', updated_at = now() "
            "WHERE id IN (SELECT id FROM email_scheduled_sends "
            "  WHERE status = 'queued' AND send_at <= now() "
            "  ORDER BY send_at LIMIT :n FOR UPDATE SKIP LOCKED) "
            "RETURNING id, account_id, draft_id, owner_email, expect"
        ), {"n": BATCH})).fetchall())


async def _finish(org: str, schedule_id: str, *, error: str | None) -> None:
    """Mark a row sent or failed. A sent draft's row is already gone with the
    draft (ON DELETE CASCADE), so the update then changes nothing."""
    async with tenant_session(org) as db:
        await db.execute(text(
            "UPDATE email_scheduled_sends SET status = CAST(:st AS text), "
            "error = CAST(:err AS text), updated_at = now(), "
            "sent_at = CASE WHEN CAST(:st AS text) = 'sent' THEN now() END "
            "WHERE id = CAST(:id AS uuid)"),
            {"st": "failed" if error else "sent", "err": (error or None) and error[:500],
             "id": schedule_id})


def _why(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        if exc.status_code == 409:
            return ("The recipients of the draft changed after it was scheduled, "
                    "so it was not sent.")
        if exc.status_code == 404:
            return "The draft is gone, so it was not sent."
        return f"The send was refused: {exc.detail}"[:500]
    return f"The send failed ({type(exc).__name__})."


async def _send_one(org: str, row: Any) -> str | None:
    """Send one claimed row as its owner. ``None`` when it went, else why not."""
    exp = row.expect if isinstance(row.expect, dict) else json.loads(row.expect or "{}")
    member = UserContext(email=row.owner_email, role=UserRole.EMPLOYEE, organization_id=org)
    background = BackgroundTasks()
    try:
        await send_draft_endpoint(
            DraftSendRequest(account_id=str(row.account_id), draft_id=str(row.draft_id),
                             expect=DraftExpect(**exp)),
            background, member)
    except Exception as exc:  # noqa: BLE001 - each row reports its own failure
        return _why(exc)
    with contextlib.suppress(Exception):
        await background()
    return None


async def run_tick() -> dict[str, int]:
    """One pass over every org: send the due rows. Never raises."""
    if not send_later_enabled():
        return {}
    sent: dict[str, int] = {}
    for org in await _orgs():
        if _stopping.is_set():
            break
        token = bind_tenant(org)
        try:
            for row in await _claim(org):
                why = await _send_one(org, row)
                await _finish(org, str(row.id), error=why)
                if why is None:
                    sent[org] = sent.get(org, 0) + 1
                    _log.info("email.send_later_sent", schedule_id=str(row.id))
                else:
                    _log.warning("email.send_later_failed", schedule_id=str(row.id),
                                 reason=why[:120])
        except Exception as exc:  # noqa: BLE001 - one org never stops the rest
            _log.warning("email.send_later_org_failed", error=type(exc).__name__)
        finally:
            release_tenant(token)
    return sent


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let one tick end the loop
            _log.warning("email.send_later_loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_send_later_loop() -> bool:
    """Start the job when ``email_send_later`` is on. Idempotent."""
    global _task
    if not send_later_enabled():
        _log.info("email.send_later_loop_disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="email-send-later")
    _log.info("email.send_later_loop_started", tick_secs=TICK_SECONDS)
    return True


async def stop_send_later_loop() -> None:
    """Stop the job and wait for a tick in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still
