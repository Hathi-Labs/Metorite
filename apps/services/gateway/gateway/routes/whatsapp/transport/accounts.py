"""Transport · accounts — connected WhatsApp Business numbers (list/create/delete).

Creation is the tail of the Meta Embedded Signup flow: the frontend completes the
coexistence handshake with Meta and posts the resulting identifiers + system-user
token here, which we encrypt and store. There is no polling scheduler — Meta
pushes events to the webhook — so an account is "live" as soon as its webhook is
subscribed.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.whatsapp.core import (
    WhatsAppAccountModel,
    _tenant_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

#: SQLSTATE of a unique violation.
_UNIQUE_VIOLATION = "23505"

#: The 409 detail of a number that is already connected, by anyone.
ALREADY_CONNECTED = "Number already connected"


def _is_unique_violation(err: BaseException) -> bool:
    """True when ``err``, or a driver error under it, is SQLSTATE 23505.

    SQLAlchemy wraps the driver error. psycopg carries ``sqlstate`` and the
    asyncpg adapter carries ``pgcode``, so the check walks ``orig`` and
    ``__cause__`` (the shape of ``email/transport/accounts._is_lock_timeout``).
    """
    seen: BaseException | None = err
    for _ in range(5):
        if seen is None:
            return False
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if code == _UNIQUE_VIOLATION:
            return True
        seen = getattr(seen, "orig", None) or seen.__cause__
    return False


class CreateAccountRequest(BaseModel):
    """The identifiers Embedded Signup returns, plus the system-user token."""
    phone_number: str
    phone_number_id: str
    waba_id: str | None = None
    display_name: str = ""
    webhook_verify_token: str | None = None
    credentials: dict[str, Any]     # {access_token, graph_version?}


def _account_model(row: Any) -> WhatsAppAccountModel:
    return WhatsAppAccountModel(
        id=str(row.id),
        phone_number=row.phone_number,
        phone_number_id=row.phone_number_id,
        waba_id=row.waba_id,
        display_name=row.display_name or "",
        avatar_color=row.avatar_color or "#25D366",
        sync_status=row.sync_status or "idle",
        sync_error=row.sync_error,
        history_import_phase=row.history_import_phase or 0,
        quality_rating=row.quality_rating,
        last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
        is_default=bool(row.is_default),
        provider=getattr(row, "provider", None) or "cloud",
    )


@router.get("/accounts", response_model=list[WhatsAppAccountModel])
async def list_accounts(user: UserContext = Depends(get_current_user)):
    """List the WhatsApp Business numbers connected by the current user."""
    async with _tenant_session() as db:
        rows = (await db.execute(
            text("""SELECT id, phone_number, phone_number_id, waba_id,
                           display_name, avatar_color, sync_status, sync_error,
                           history_import_phase, quality_rating, last_synced_at,
                           is_default, provider
                    FROM wa_accounts WHERE user_id = :uid
                    ORDER BY is_default DESC, created_at"""),
            {"uid": user.email or "anonymous"},
        )).fetchall()
        return [_account_model(r) for r in rows]


async def persist_account(
    db: Any,
    *,
    user_id: str,
    phone_number: str,
    phone_number_id: str,
    waba_id: str | None,
    display_name: str,
    credentials: dict[str, Any],
    webhook_verify_token: str | None,
) -> Any:
    """Encrypt the credentials + insert a wa_account, returning the row. Shared by
    the manual create route AND the Embedded Signup flow (W12) so both write the
    number the same way. Raises 409 if anyone already connected the number.
    Caller owns the transaction (commit).

    The read-first check below sees only this member's rows in this tenant.
    Under FORCE RLS a row of another member or another org is invisible to it.
    The INSERT then meets a unique index, and the member gets the same 409,
    never a 500. Two indexes can refuse it: migration 102's
    ``UNIQUE (user_id, phone_number_id)``, and WA-C1's platform-wide index on a
    Cloud API ``phone_number_id`` (F7). The violation aborts the transaction,
    so the caller's session rolls back on the raised 409."""
    if not credentials.get("access_token"):
        raise HTTPException(status_code=400, detail="access_token required")

    # The provider reads phone_number_id/waba_id from the creds blob — fold them
    # in so the stored blob is self-contained.
    creds = dict(credentials)
    creds.setdefault("phone_number_id", phone_number_id)
    creds.setdefault("waba_id", waba_id)

    from acb_llm.key_store import get_key_store
    store = get_key_store()
    encrypted = store.encrypt(json.dumps(creds))

    existing = (await db.execute(
        text("""SELECT id FROM wa_accounts
                WHERE user_id = :uid AND phone_number_id = :pnid"""),
        {"uid": user_id, "pnid": phone_number_id},
    )).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail=ALREADY_CONNECTED)

    # First account for this user becomes the default.
    is_first = (await db.execute(
        text("SELECT COUNT(*) FROM wa_accounts WHERE user_id = :uid"),
        {"uid": user_id},
    )).scalar() == 0

    try:
        inserted = await db.execute(
            text("""INSERT INTO wa_accounts
                      (id, user_id, phone_number, phone_number_id, waba_id,
                       display_name, credentials_encrypted, webhook_verify_token,
                       sync_status, is_default)
                    VALUES
                      (:id, :uid, :phone, :pnid, :waba, :name, :creds, :verify,
                       'importing', :is_default)
                    RETURNING id, phone_number, phone_number_id, waba_id,
                              display_name, avatar_color, sync_status, sync_error,
                              history_import_phase, quality_rating, last_synced_at,
                              is_default"""),
            {"id": str(uuid4()), "uid": user_id,
             "phone": phone_number, "pnid": phone_number_id,
             "waba": waba_id, "name": display_name, "creds": encrypted,
             "verify": webhook_verify_token, "is_default": is_first},
        )
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            raise HTTPException(
                status_code=409, detail=ALREADY_CONNECTED) from exc
        raise
    return inserted.fetchone()


@router.post("/accounts", response_model=WhatsAppAccountModel, status_code=201)
async def create_account(
    req: CreateAccountRequest, user: UserContext = Depends(get_current_user),
):
    """Register a WhatsApp Business number (the manual / guided-wizard path)."""
    async with _tenant_session() as db:
        row = await persist_account(
            db, user_id=user.email or "anonymous",
            phone_number=req.phone_number, phone_number_id=req.phone_number_id,
            waba_id=req.waba_id, display_name=req.display_name,
            credentials=req.credentials,
            webhook_verify_token=req.webhook_verify_token,
        )
        return _account_model(row)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(
    account_id: str, user: UserContext = Depends(get_current_user),
):
    """Disconnect a number. The message archive is kept (rows cascade only if the
    account row is removed) — we remove the account, which cascades its data; the
    UI copy makes that explicit."""
    async with _tenant_session() as db:
        result = await db.execute(
            text("DELETE FROM wa_accounts WHERE id = :id AND user_id = :uid"),
            {"id": account_id, "uid": user.email or "anonymous"},
        )
        # A miss deleted nothing, so the 404's rollback discards an empty
        # transaction — same outcome as the old commit-then-404 ordering.
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Account not found")
