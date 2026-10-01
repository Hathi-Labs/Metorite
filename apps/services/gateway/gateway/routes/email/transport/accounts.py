"""Transport · accounts — connected-mailbox CRUD (list/create/update/delete)."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, status
from gateway.routes.email.core import _default_label, _log, _tenant_session, router
from pydantic import BaseModel
from sqlalchemy import text


class EmailAccountModel(BaseModel):
    id: str
    provider: str  # 'gmail' | 'microsoft' | 'imap'
    email_address: str
    label: str = ""
    avatar_color: str = "#6366f1"
    sync_enabled: bool = True
    sync_status: str = "idle"
    sync_error: str | None = None
    last_synced_at: str | None = None
    unread_count: int = 0
    is_default: bool = False


class AccountUpdateModel(BaseModel):
    label: str | None = None
    sync_enabled: bool | None = None


class CreateAccountRequest(BaseModel):
    """Manual account creation (IMAP/SMTP or other manual config)."""
    provider: str  # 'imap' | 'gmail' | 'microsoft'
    email_address: str
    label: str = ""
    credentials: dict[str, Any]  # Provider-specific credential dict


@router.get("/accounts", response_model=list[EmailAccountModel])
async def list_accounts(
    user: UserContext = Depends(get_current_user),
):
    """List all connected email accounts for the current user."""
    async with _tenant_session() as db:
        result = await db.execute(
            text(
                """SELECT id, provider, email_address, label, avatar_color,
                          sync_enabled, sync_status, sync_error, last_synced_at,
                          is_default
                   FROM email_accounts
                   WHERE user_id = :user_id
                   ORDER BY is_default DESC, created_at"""
            ),
            {"user_id": user.email or "anonymous"},
        )
        rows = result.fetchall()
        accounts: list[EmailAccountModel] = []
        for row in rows:
            # Count unread messages for this account
            unread_result = await db.execute(
                text(
                    """SELECT COUNT(*) FROM email_messages
                       WHERE account_id = :account_id AND is_read = false"""
                ),
                {"account_id": row.id},
            )
            unread = unread_result.scalar() or 0

            accounts.append(EmailAccountModel(
                id=str(row.id),
                provider=row.provider,
                email_address=row.email_address,
                label=row.label or "",
                avatar_color=row.avatar_color or "#6366f1",
                sync_enabled=row.sync_enabled,
                sync_status=row.sync_status or "idle",
                sync_error=row.sync_error,
                last_synced_at=row.last_synced_at.isoformat()
                if row.last_synced_at else None,
                unread_count=unread,
                is_default=bool(row.is_default),
            ))
        return accounts


@router.post("/accounts", response_model=EmailAccountModel, status_code=201)
async def create_account(
    req: CreateAccountRequest,
    user: UserContext = Depends(get_current_user),
):
    """Add a new email account manually (IMAP/SMTP or pre-configured OAuth creds).

    For OAuth-based providers (gmail, microsoft), use the /oauth/{provider}/authorize
    flow instead — it handles token exchange automatically.
    """
    # Validate provider
    if req.provider not in ("gmail", "microsoft", "imap"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {req.provider}. Supported: gmail, microsoft, imap",
        )

    # For IMAP, validate required credential fields
    if req.provider == "imap":
        required = ["imap_host", "imap_port", "imap_username", "imap_password",
                     "smtp_host", "smtp_port"]
        missing = [k for k in required if k not in req.credentials]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing IMAP credential fields: {', '.join(missing)}",
            )

    # Encrypt credentials
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    encrypted_creds = store.encrypt(json.dumps(req.credentials))

    async with _tenant_session() as db:
        # Check for duplicate account
        existing = await db.execute(
            text(
                """SELECT id FROM email_accounts
                   WHERE user_id = :user_id
                     AND provider = :provider
                     AND email_address = :email"""
            ),
            {
                "user_id": user.email or "anonymous",
                "provider": req.provider,
                "email": req.email_address,
            },
        )
        if existing.fetchone():
            raise HTTPException(
                status_code=409,
                detail=f"Account {req.email_address} already exists",
            )

        account_id = str(uuid4())
        # The user's FIRST connected mailbox becomes their default (the inbox the
        # UI lands on). The partial unique index guarantees at most one default.
        is_default_row = await db.execute(
            text(
                """INSERT INTO email_accounts
                   (id, user_id, provider, email_address, label,
                    avatar_color, credentials_encrypted, is_default)
                   VALUES (:id, :user_id, :provider, :email, :label,
                           :color, :creds,
                           NOT EXISTS (SELECT 1 FROM email_accounts
                                       WHERE user_id = :user_id))
                   RETURNING is_default"""
            ),
            {
                "id": account_id,
                "user_id": user.email or "anonymous",
                "provider": req.provider,
                "email": req.email_address,
                "label": req.label or _default_label(req.provider),
                "color": "#6366f1",
                "creds": encrypted_creds,
            },
        )
        created_default = bool(is_default_row.scalar())

    # Start background sync for this account. It runs AFTER the tenant block,
    # so the row is committed and the interval read of the scheduler sees it.
    # The loop binds the organization of the session (EM-T1b-1 item 5).
    try:
        from email_ingestion.scheduler import refresh_account_sync
        await refresh_account_sync(
            account_id, organization_id=user.organization_id)
    except Exception as exc:
        _log.warning("email.refresh_sync_failed", error=str(exc)[:200])

    return EmailAccountModel(
        id=account_id,
        provider=req.provider,
        email_address=req.email_address,
        label=req.label or _default_label(req.provider),
        avatar_color="#6366f1",
        sync_enabled=True,
        sync_status="idle",
        last_synced_at=None,
        unread_count=0,
        is_default=created_default,
    )


@router.post("/accounts/{account_id}/default", response_model=EmailAccountModel)
async def set_default_account(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Make this account the user's default mailbox (the inbox the UI opens on).

    Clears the flag on the user's other accounts first so the partial unique
    index (one default per user) is never violated, then sets it on this one.
    """
    async with _tenant_session() as db:
        owner = user.email or "anonymous"
        # Verify ownership before mutating anything.
        owned = await db.execute(
            text("SELECT 1 FROM email_accounts WHERE id = :id AND user_id = :uid"),
            {"id": account_id, "uid": owner},
        )
        if not owned.fetchone():
            raise HTTPException(status_code=404, detail="Account not found")

        # Demote the current default(s), then promote this one — same txn so the
        # one-default-per-user index can't transiently see two.
        await db.execute(
            text(
                """UPDATE email_accounts SET is_default = false, updated_at = now()
                   WHERE user_id = :uid AND is_default AND id <> :id"""
            ),
            {"uid": owner, "id": account_id},
        )
        result = await db.execute(
            text(
                """UPDATE email_accounts
                   SET is_default = true, updated_at = now()
                   WHERE id = :id AND user_id = :uid
                   RETURNING id, provider, email_address, label, avatar_color,
                             sync_enabled, sync_status, sync_error,
                             last_synced_at, is_default"""
            ),
            {"id": account_id, "uid": owner},
        )
        row = result.fetchone()

        unread_result = await db.execute(
            text(
                """SELECT COUNT(*) FROM email_messages
                   WHERE account_id = :account_id AND is_read = false"""
            ),
            {"account_id": account_id},
        )
        unread = unread_result.scalar() or 0
        return EmailAccountModel(
            id=str(row.id),
            provider=row.provider,
            email_address=row.email_address,
            label=row.label or "",
            avatar_color=row.avatar_color or "#6366f1",
            sync_enabled=row.sync_enabled,
            sync_status=row.sync_status or "idle",
            sync_error=row.sync_error,
            last_synced_at=row.last_synced_at.isoformat()
            if row.last_synced_at else None,
            unread_count=unread,
            is_default=bool(row.is_default),
        )


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Remove an email account and all its synced messages."""
    async with _tenant_session() as db:
        owner = user.email or "anonymous"
        result = await db.execute(
            text(
                """DELETE FROM email_accounts
                   WHERE id = :id AND user_id = :user_id
                   RETURNING is_default"""
            ),
            {"id": account_id, "user_id": owner},
        )
        deleted = result.fetchone()
        if deleted is None:
            raise HTTPException(status_code=404, detail="Account not found")
        # If the deleted account was the default, re-elect the earliest-created
        # remaining account so the user always has a default to land on.
        if bool(deleted.is_default):
            await db.execute(
                text(
                    """UPDATE email_accounts SET is_default = true, updated_at = now()
                       WHERE id = (
                           SELECT id FROM email_accounts
                           WHERE user_id = :uid
                           ORDER BY created_at, id
                           LIMIT 1
                       )"""
                ),
                {"uid": owner},
            )

        # Stop background sync for this account
        try:
            from email_ingestion.scheduler import remove_account_sync
            await remove_account_sync(account_id)
        except Exception:
            pass


@router.patch("/accounts/{account_id}", response_model=EmailAccountModel)
async def update_account(
    account_id: str,
    updates: AccountUpdateModel,
    user: UserContext = Depends(get_current_user),
):
    """Update account settings (label, sync toggle)."""
    async with _tenant_session() as db:
        set_clauses = []
        params: dict[str, Any] = {"id": account_id, "user_id": user.email or "anonymous"}

        if updates.label is not None:
            set_clauses.append("label = :label")
            params["label"] = updates.label
        if updates.sync_enabled is not None:
            set_clauses.append("sync_enabled = :sync_enabled")
            params["sync_enabled"] = updates.sync_enabled

        if not set_clauses:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses.append("updated_at = now()")

        result = await db.execute(
            text(
                f"""UPDATE email_accounts
                    SET {', '.join(set_clauses)}
                    WHERE id = :id AND user_id = :user_id
                    RETURNING id, provider, email_address, label, avatar_color,
                              sync_enabled, sync_status, last_synced_at"""
            ),
            params,
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")

        # Refresh background sync: start/stop loop for this account
        try:
            from email_ingestion.scheduler import refresh_account_sync, remove_account_sync
            if row.sync_enabled:
                await refresh_account_sync(
                    account_id, organization_id=user.organization_id)
            else:
                await remove_account_sync(account_id)
        except Exception as exc:
            _log.warning("email.refresh_sync_failed", error=str(exc)[:200])

        return EmailAccountModel(
            id=str(row.id),
            provider=row.provider,
            email_address=row.email_address,
            label=row.label or "",
            avatar_color=row.avatar_color or "#6366f1",
            sync_enabled=row.sync_enabled,
            sync_status=row.sync_status or "idle",
            last_synced_at=row.last_synced_at.isoformat()
            if row.last_synced_at else None,
        )
