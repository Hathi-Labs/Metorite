"""Transport · accounts — connected-mailbox CRUD (list/create/update/delete).

Also the admin count of connected mailboxes (``GET /email/admin/connections``,
EM-T3d), which returns integers only.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user, require_permission
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
    #: False until the first deep sync of the mailbox completes. The connect
    #: UI shows first-sync progress from it (EM-T3a item 5).
    initial_sync_done: bool = False


class AccountUpdateModel(BaseModel):
    label: str | None = None
    sync_enabled: bool | None = None


class CreateAccountRequest(BaseModel):
    """Manual account creation (IMAP/SMTP or other manual config)."""
    provider: str  # 'imap' | 'gmail' | 'microsoft'
    email_address: str
    label: str = ""
    credentials: dict[str, Any]  # Provider-specific credential dict


class OrgConnectionCounts(BaseModel):
    """How many members of the organization connected a mailbox (EM-T3d).

    D-EM-4: an admin sees this count and never the mail. Every field is an
    ``int`` on purpose. A string field could carry an address, a member or an
    account id out of a read across members, so
    ``tests/unit/test_email_org_connection_counts.py`` fails on one.
    """
    #: Distinct members, by ``lower(user_id)``. A removed member counts until
    #: a purge deletes their mailbox (no join to ``app_user``).
    members: int
    mailboxes: int
    microsoft: int
    gmail: int
    imap: int
    #: Mailboxes in sync status ``error``. The error text never leaves.
    sync_errors: int
    #: Mailboxes whose first deep sync has not finished.
    first_sync_pending: int


#: One SELECT for all seven counts. The organization predicate holds on top of
#: FORCE RLS, so a binding that disagrees with the session reads no row. The
#: owner predicate of every other email route is absent on purpose: this read
#: crosses members, and the admin gate on the route is what permits that.
_ORG_CONNECTION_COUNTS_SQL = """
    SELECT count(DISTINCT lower(user_id))                      AS members,
           count(*)                                            AS mailboxes,
           count(*) FILTER (WHERE provider = 'microsoft')      AS microsoft,
           count(*) FILTER (WHERE provider = 'gmail')          AS gmail,
           count(*) FILTER (WHERE provider = 'imap')           AS imap,
           count(*) FILTER (WHERE sync_status = 'error')       AS sync_errors,
           count(*) FILTER (WHERE NOT initial_sync_done)       AS first_sync_pending
      FROM email_accounts
     WHERE organization_id = CAST(:org AS uuid)
"""


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
                          is_default, initial_sync_done
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
                initial_sync_done=bool(row.initial_sync_done),
            ))
        return accounts


@router.get(
    "/admin/connections",
    response_model=OrgConnectionCounts,
    dependencies=[require_permission("admin:members:read")],
)
async def org_connection_counts(
    user: UserContext = Depends(get_current_user),
) -> OrgConnectionCounts:
    """How many members of the caller's organization connected a mailbox.

    Owning spec: ``email_app_master_plan.md`` §10.4.3, EM-T3d. The one email
    route that reads every mailbox row of an organization, so three things
    limit it. ``admin:members:read`` is the gate, the same test that
    ``/auth/me`` reports as ``is_admin``. The router adds ``feature:email``.
    The organization comes from the session only (R11). The SQL names it on
    top of FORCE RLS. The answer is seven integers and nothing else.
    """
    if not user.organization_id:
        raise HTTPException(
            status_code=403,
            detail="Mailbox counts need a signed-in admin of an organization.",
        )
    org = str(user.organization_id)
    async with _tenant_session() as db:
        row = (await db.execute(
            text(_ORG_CONNECTION_COUNTS_SQL), {"org": org})).one()
    return OrgConnectionCounts(
        members=int(row.members),
        mailboxes=int(row.mailboxes),
        microsoft=int(row.microsoft),
        gmail=int(row.gmail),
        imap=int(row.imap),
        sync_errors=int(row.sync_errors),
        first_sync_pending=int(row.first_sync_pending),
    )


@router.post("/accounts", response_model=EmailAccountModel, status_code=201)
async def create_account(
    req: CreateAccountRequest,
    user: UserContext = Depends(get_current_user),
):
    """Add a new email account manually (IMAP/SMTP or pre-configured OAuth creds).

    For OAuth-based providers (gmail, microsoft), use the /oauth/{provider}/authorize
    flow instead — it handles token exchange automatically.

    The organization and the member come from the session and from nowhere
    else (``user_management_contract.md`` R11). With either one missing, the
    route refuses with 403, as the OAuth authorize leg does (EM-T2a item 7).
    """
    if not user.organization_id or not user.email:
        raise HTTPException(
            status_code=403,
            detail="Connecting a mailbox needs a signed-in member of an organization.",
        )
    org = str(user.organization_id)

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

    async with _tenant_session(org) as db:
        # Check for duplicate account. The organization is in the predicate
        # as well as in the bound tenant, so the check matches the per-tenant
        # unique index of migration 223 with or without row level security.
        existing = await db.execute(
            text(
                """SELECT id FROM email_accounts
                   WHERE organization_id = CAST(:org AS uuid)
                     AND user_id = :user_id
                     AND provider = :provider
                     AND email_address = :email"""
            ),
            {
                "org": org,
                "user_id": user.email,
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
        # The member's FIRST mailbox in this organization becomes the default
        # (the inbox the UI lands on). The partial unique index of migration
        # 223 allows one default per member per organization. The INSERT
        # names organization_id: a write names its tenant (R5).
        is_default_row = await db.execute(
            text(
                """INSERT INTO email_accounts
                   (id, user_id, provider, email_address, label,
                    avatar_color, credentials_encrypted, is_default,
                    organization_id)
                   VALUES (:id, :user_id, :provider, :email, :label,
                           :color, :creds,
                           NOT EXISTS (SELECT 1 FROM email_accounts
                                       WHERE user_id = :user_id
                                         AND organization_id = CAST(:org AS uuid)),
                           CAST(:org AS uuid))
                   RETURNING is_default"""
            ),
            {
                "id": account_id,
                "user_id": user.email,
                "org": org,
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
                             last_synced_at, is_default, initial_sync_done"""
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
            initial_sync_done=bool(row.initial_sync_done),
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
                              sync_enabled, sync_status, last_synced_at,
                              initial_sync_done"""
            ),
            params,
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")

    # Refresh background sync: start/stop loop for this account. It runs AFTER
    # the tenant block, so the update is committed first (EM-T1b-1 item 5).
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
        initial_sync_done=bool(row.initial_sync_done),
    )
