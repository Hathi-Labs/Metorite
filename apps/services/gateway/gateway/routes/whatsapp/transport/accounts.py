"""Transport · accounts — connected WhatsApp Business numbers (list/create/delete).

``POST /whatsapp/accounts`` is the manual path. The member pastes the ids and a
token, Meta confirms them, and the row is saved with ``sync_status='importing'``.
The Embedded Signup path (``connect.embedded_signup``) uses the same
``persist_account``. It subscribes the app to the WABA as a hard step first, so
it saves its row as ``'live'`` (WS-20 WA-C2). There is no polling scheduler,
because Meta pushes events to the webhook. The manual path does not subscribe
the app, so it keeps ``'importing'`` (spec §12.3 F6, open).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.whatsapp.core import (
    WhatsAppAccountModel,
    _instantiate_provider,
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


#: Meta allows the coexistence history sync for 24 hours after the connect
#: (Meta, "Onboard WhatsApp Business app users"). WS-20 WA-C3 P4, P11.
HISTORY_SYNC_WINDOW = timedelta(hours=24)

#: The columns that every account read returns, so the list, the insert and
#: the model cannot disagree.
_ACCOUNT_COLUMNS = """id, phone_number, phone_number_id, waba_id,
                      display_name, avatar_color, sync_status, sync_error,
                      history_import_phase, quality_rating, last_synced_at,
                      is_default, provider, history_sync_state,
                      history_sync_error, history_import_progress, created_at"""


def history_sync_deadline(created_at: Any) -> datetime | None:
    """When the history sync window of an account closes, or None. Pure."""
    if not isinstance(created_at, datetime):
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at + HISTORY_SYNC_WINDOW


def _account_model(row: Any) -> WhatsAppAccountModel:
    from gateway.routes.whatsapp.transport.connect import history_sync_enabled

    state = getattr(row, "history_sync_state", None)
    deadline = history_sync_deadline(getattr(row, "created_at", None))
    progress = getattr(row, "history_import_progress", None)
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
        history_sync_state=state,
        history_sync_error=getattr(row, "history_sync_error", None),
        history_import_progress=int(progress) if progress is not None else None,
        history_sync_deadline=(
            deadline.isoformat() if state is not None and deadline else None),
        history_sync_available=history_sync_enabled(),
    )


@router.get("/accounts", response_model=list[WhatsAppAccountModel])
async def list_accounts(user: UserContext = Depends(get_current_user)):
    """List the WhatsApp Business numbers connected by the current user."""
    async with _tenant_session() as db:
        rows = (await db.execute(
            text(f"""SELECT {_ACCOUNT_COLUMNS}
                    FROM wa_accounts WHERE user_id = :uid
                    ORDER BY is_default DESC, created_at"""),
            {"uid": user.email or "anonymous"},
        )).fetchall()
        return [_account_model(r) for r in rows]


async def verify_cloud_number(
    phone_number_id: str, credentials: dict[str, Any],
) -> dict[str, Any]:
    """Prove that the supplied token can act for this Cloud API number.

    Meta's phone-number profile answers 200 only when the token holds the
    number. Returns that profile. A refusal answers 400 with Meta's own
    message, cleaned by ``friendly_meta_error``.

    WA-C1 review P1. The index of migration 230 makes the first claim of a
    number exclusive. Without this check, a member of org B could register
    org A's ``phone_number_id`` with any token. Org A's inbound would then go
    to org B, and org A would get 409 for good. Call it BEFORE the session
    opens, so no database connection waits on Meta.

    Round 3 of the review: the check reads ONLY the token from the caller's
    blob. A caller ``graph_version`` such as ``v21.0/<waba>/phone_numbers#``
    once turned the node read into an edge read that the caller's own token
    passed. So the version is the server's, and the number must be digits.
    """
    from whatsapp_ingestion.providers.factory import (
        is_phone_number_id,
        safe_graph_version,
    )

    if not is_phone_number_id(phone_number_id):
        raise HTTPException(
            status_code=400,
            detail="phone_number_id must be the numeric id that Meta shows.")
    token = credentials.get("access_token")
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="access_token required")
    creds = {
        "access_token": token,
        "phone_number_id": phone_number_id,
        "graph_version": safe_graph_version(
            os.environ.get("WHATSAPP_GRAPH_VERSION", "").strip() or None),
    }
    try:
        profile = await _instantiate_provider(
            "cloud_api", creds).get_phone_number_profile()
    except HTTPException:
        raise
    except Exception as exc:
        from gateway.routes.whatsapp.transport.connect import friendly_meta_error
        raise HTTPException(
            status_code=400, detail=friendly_meta_error(exc)) from exc
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail="Meta returned no profile.")
    # A node read on Graph always returns the `id` of the node. A missing id
    # means Meta read something that is not this number, such as an edge
    # list. A different id means the token answered for another number.
    if profile.get("id") != phone_number_id:
        raise HTTPException(
            status_code=400,
            detail="Meta answered for a different phone number id.")
    return profile


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
    verified_profile: dict[str, Any],
    sync_status: str = "importing",
    history_sync_state: str | None = None,
) -> Any:
    """Encrypt the credentials + insert a wa_account, returning the row. Shared by
    the manual create route AND the Embedded Signup flow (W12) so both write the
    number the same way. Raises 409 if anyone already connected the number.
    Caller owns the transaction (commit).

    ``verified_profile`` is required and has no default. It is the profile
    that Meta returned for this token and this number, from
    ``verify_cloud_number`` or from the Embedded Signup check. So no path can
    insert a Cloud API row that Meta did not confirm (WA-C1 review P1).

    ``sync_status`` is the first status of the row. The Embedded Signup path
    passes ``'live'``, because it subscribed the app to the WABA before it
    calls this. The manual path keeps the default (WS-20 WA-C2 P3).

    ``history_sync_state`` is ``'pending'`` for a coexistence row, and None
    for every other row (WS-20 WA-C3 P3). The sync call runs after this.

    The read-first check below sees only this member's rows in this tenant.
    Under FORCE RLS a row of another member or another org is invisible to it.
    The INSERT then meets a unique index, and the member gets the same 409,
    never a 500. Two indexes can refuse it: migration 102's
    ``UNIQUE (user_id, phone_number_id)``, and WA-C1's platform-wide index on a
    Cloud API ``phone_number_id`` (F7). The violation aborts the transaction,
    so the caller's session rolls back on the raised 409."""
    if not credentials.get("access_token"):
        raise HTTPException(status_code=400, detail="access_token required")
    if not isinstance(verified_profile, dict):
        raise TypeError("persist_account needs the profile Meta verified")

    # The provider reads phone_number_id/waba_id from the creds blob — fold them
    # in so the stored blob is self-contained. The number is the one Meta
    # verified, so a blob cannot name a second number.
    creds = dict(credentials)
    creds["phone_number_id"] = phone_number_id
    creds.setdefault("waba_id", waba_id)
    # Defence in depth: keep a caller graph_version only when it is a plain
    # version. The provider also refuses a bad one when it reads the blob.
    from whatsapp_ingestion.providers.factory import safe_graph_version
    supplied_version = creds.pop("graph_version", None)
    if supplied_version and safe_graph_version(supplied_version) == supplied_version:
        creds["graph_version"] = supplied_version

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
            text(f"""INSERT INTO wa_accounts
                      (id, user_id, phone_number, phone_number_id, waba_id,
                       display_name, credentials_encrypted, webhook_verify_token,
                       sync_status, is_default, history_sync_state)
                    VALUES
                      (:id, :uid, :phone, :pnid, :waba, :name, :creds, :verify,
                       :sync_status, :is_default, :history_sync_state)
                    RETURNING {_ACCOUNT_COLUMNS}"""),
            {"id": str(uuid4()), "uid": user_id,
             "phone": phone_number, "pnid": phone_number_id,
             "waba": waba_id, "name": display_name, "creds": encrypted,
             "verify": webhook_verify_token, "sync_status": sync_status,
             "is_default": is_first, "history_sync_state": history_sync_state},
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
    """Register a WhatsApp Business number (the manual / guided-wizard path).

    Meta must confirm the supplied token for this number first. Then the
    session opens (WA-C1 review P1)."""
    profile = await verify_cloud_number(req.phone_number_id, req.credentials)
    async with _tenant_session() as db:
        row = await persist_account(
            db, user_id=user.email or "anonymous",
            phone_number=req.phone_number, phone_number_id=req.phone_number_id,
            waba_id=req.waba_id, display_name=req.display_name,
            credentials=req.credentials,
            webhook_verify_token=req.webhook_verify_token,
            verified_profile=profile,
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
