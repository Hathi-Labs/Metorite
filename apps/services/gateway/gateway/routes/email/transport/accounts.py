"""Transport · accounts — connected-mailbox CRUD (list/create/update/delete).

Also the admin count of connected mailboxes (``GET /email/admin/connections``,
EM-T3d), which returns integers only.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user, require_permission
from email_ingestion.storage import storage_limit_bytes
from fastapi import Depends, HTTPException, status
from gateway.routes.email.core import (
    _account_scope,
    _decrypt_credentials,
    _default_label,
    _instantiate_provider,
    _log,
    _tenant_session,
    router,
)
from gateway.routes.email.mailbox_identity import (
    NEXT_SLOT_SQL,
    default_labels,
    display_labels,
    reserved_label,
    valid_slot,
    work_domain,
)
from gateway.routes.email.memory_purge import schedule_mailbox_memory_purge
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import text

#: The bound on the best-effort Graph call of a disconnect (EM-T4f). A slow or
#: failed call must never hold back the 204 of the route.
SUBSCRIPTION_DELETE_TIMEOUT_S = 5.0

#: The bound on the row locks that the DELETE of a disconnect waits for
#: (EM-T4f). A sync outside the loop holds a KEY SHARE lock on the account row
#: until its phase commits. Without a bound, the DELETE waits for the whole
#: statement timeout (2 minutes in production). ``SET LOCAL`` ends with the
#: block, as the tenant binding does.
DISCONNECT_LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s'"

#: SQLSTATE ``lock_not_available``: a statement waited longer than its
#: ``lock_timeout``.
_LOCK_NOT_AVAILABLE = "55P03"

#: The 409 detail of a disconnect that a sync holds back. A member reads it.
DISCONNECT_BUSY_DETAIL = (
    "A sync is still writing mail for this mailbox. Try again in a moment."
)

#: Graph statuses that mean the subscription is gone: 204 deleted it now, and
#: 404 says it was gone before.
_SUBSCRIPTION_GONE = frozenset({204, 404})


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
    #: The import floor that the member chose at the first connect, as ISO
    #: text. ``None`` for a mailbox that connected before EM-T6a.
    import_since: str | None = None
    #: True when the member closed the guided setup (EM-T6a item 11).
    onboarding_done: bool = False
    #: The progress of the first import (EM-T6b item 11). The phase is
    #: ``counting``, ``importing``, ``done`` or ``limit`` (EM-T6c), and
    #: ``None`` before the first import or for a mailbox from before EM-T6.
    #: ``import_reached_at`` is the oldest mail written so far, as ISO text.
    #: ``import_estimate`` is ``None`` when the provider gives no count.
    import_phase: str | None = None
    import_count: int | None = None
    import_estimate: int | None = None
    import_reached_at: str | None = None
    #: The slot of the categorical ramp of the mailbox chip, 1 to 12. ``None``
    #: for a row that old code wrote, and the UI hashes the id (EM-T8b).
    color_slot: int | None = None
    #: The label to show, by the rules of §11.4: the label the member chose,
    #: else one made from the address. Unique among the mailboxes of the
    #: member unless the member chose two equal labels (EM-T8b, D-EM-21).
    display_label: str = ""
    #: The label the mailbox shows when the member clears its name. The
    #: rename dialog draws it for a blank name (EM-T8b).
    default_label: str = ""
    #: The domain of the address when it is an organization domain, else
    #: ``None``. The From row warns from it (EM-T8c, §11.4).
    work_domain: str | None = None
    #: True when the last sync failed on the sign-in of the provider, so the
    #: mailbox cannot send until the member reconnects it. Any other sync
    #: error leaves it False: a send does not read the sync status (EM-T8c).
    needs_reconnect: bool = False
    #: When the member connected the mailbox, as ISO text with six digits of
    #: microseconds, so two values sort as text in the order of time. A
    #: disconnect makes the oldest mailbox that is left the default
    #: (``ORDER BY created_at, id`` in ``delete_account``). The UI names that
    #: mailbox before the removal (EM-T8f-1 item 3). ``None`` for a NULL.
    created_at: str | None = None
    #: False when the member keeps the mailbox separate (EM-T8g-1, D-EM-28).
    #: A read of more than one mailbox then leaves it out. Migration 229 gives
    #: each row true, so the model default is true too.
    in_all_inboxes: bool = True
    #: The storage meter of the mailbox in bytes, and its limit (EM-T6c item
    #: 14). ``stored_bytes`` is ``None`` before the first meter run.
    stored_bytes: int | None = None
    storage_limit_bytes: int = Field(default_factory=storage_limit_bytes)


#: The longest label a member can give a mailbox (EM-T8b).
MAX_LABEL_LEN = 40

#: The account columns that every account read returns, after the base ones.
_PROGRESS_COLUMNS = (
    "import_phase, import_count, import_estimate, import_reached_at, "
    "stored_bytes")


def _progress(row: Any) -> dict[str, Any]:
    """The import progress and the meter of an account row."""
    return {
        "import_phase": row.import_phase,
        "import_count": row.import_count,
        "import_estimate": row.import_estimate,
        "import_reached_at": _iso(row.import_reached_at),
        "stored_bytes": row.stored_bytes,
    }


class AccountUpdateModel(BaseModel):
    #: A blank label goes back to the default label (EM-T8b).
    label: str | None = None
    sync_enabled: bool | None = None
    #: True closes the guided setup, and false opens it again (EM-T6a).
    onboarding_done: bool | None = None
    #: The slot of the mailbox chip, 1 to 12 (EM-T8b). Strict, so JSON
    #: ``true`` or ``"3"`` answers 422 instead of turning into a slot.
    color_slot: StrictInt | None = None
    #: False keeps the mailbox separate, and true puts it back in All inboxes
    #: (EM-T8g-1, D-EM-28). Strict, so ``"yes"``, ``"false"`` or ``0`` answers
    #: 422 instead of turning into a choice the member did not make.
    in_all_inboxes: StrictBool | None = None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _iso_us(value: Any) -> str | None:
    """ISO text that always holds microseconds (EM-T8f-1).

    ``isoformat()`` drops the fraction when it is zero, so two values would
    not compare as text. The fixed form keeps the order of time.
    """
    return value.isoformat(timespec="microseconds") if value else None


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


async def _unread_counts(
    db: Any, owner: str, account_id: str | None = None,
) -> dict[str, int]:
    """The unread count of each mailbox of ``owner``, in ONE grouped read.

    EM-T4e (``email_app_master_plan.md`` §10.4.6). Before it, each account
    row cost one ``COUNT``. Both account reads that return ``unread_count``
    call this. ``_account_scope`` holds the owner predicate, so the read
    counts only the mailboxes of ``owner``. ``account_id`` narrows it to one
    of them. A mailbox with no unread mail has no row here, and reads 0.

    EM-T8d (MB-13): the count is the unread mail of the INBOX that the member
    sees, so junk, deleted mail and snoozed mail do not count. The switcher
    shows it for each mailbox, and All inboxes shows the sum.
    """
    params: dict[str, Any] = {"uid": owner}
    scope = _account_scope(account_id, params)
    rows = (await db.execute(text(
        "SELECT em.account_id, count(*) AS unread FROM email_messages em "
        f"WHERE {scope} AND em.is_read = false "
        "AND LOWER(COALESCE(em.folder, '')) = 'inbox' "
        "AND (em.snoozed_until IS NULL OR em.snoozed_until <= now()) "
        "GROUP BY em.account_id"
    ), params)).fetchall()
    return {str(r.account_id): int(r.unread) for r in rows}


#: The texts of a sync error that mean the sign-in failed. The scheduler
#: writes ``str(exc)``: a failed ``authenticate``, a refused refresh at the
#: token endpoint of Microsoft or Google, or a blob with no refresh token.
_AUTH_FAILURE = re.compile(
    r"Provider authentication failed|oauth2/v2\.0/token|oauth2\.googleapis\.com/token"
    r"|accounts\.google\.com/o/oauth2|Missing OAuth credentials|invalid_grant"
    r"|AADSTS(?:50173|50076|50078|50079|700082|70008|54005|65001|500011)\b",
    re.IGNORECASE,
)


def needs_reconnect(sync_status: str | None, sync_error: str | None) -> bool:
    """True when the last sync failed on the sign-in (EM-T8c review).

    Only a sign-in failure blocks a send. A 429 or a 503 during an import also
    writes ``sync_status = 'error'``, and a send still works then.
    """
    return sync_status == "error" and bool(_AUTH_FAILURE.search(sync_error or ""))


async def _display_labels(db: Any, owner: str) -> tuple[dict[str, str], dict[str, str]]:
    """The display label of each mailbox of ``owner``, in one read.

    A default label depends on the other mailboxes of the member, so a route
    that returns one mailbox still reads them all (EM-T8b, §11.4).
    """
    rows = (await db.execute(text(
        "SELECT id, email_address, label FROM email_accounts "
        "WHERE user_id = :uid"
    ), {"uid": owner})).fetchall()
    triples = [(str(r.id), r.email_address, r.label) for r in rows]
    return display_labels(triples), default_labels(triples)


@router.get("/accounts", response_model=list[EmailAccountModel])
async def list_accounts(
    user: UserContext = Depends(get_current_user),
):
    """List all connected email accounts for the current user.

    Two reads for any number of accounts: the rows, then one grouped count
    (EM-T4e).
    """
    owner = user.email or "anonymous"
    async with _tenant_session() as db:
        result = await db.execute(
            text(
                f"""SELECT id, provider, email_address, label, avatar_color,
                          sync_enabled, sync_status, sync_error, last_synced_at,
                          is_default, initial_sync_done, import_since,
                          onboarding_done_at, color_slot, created_at,
                          in_all_inboxes, {_PROGRESS_COLUMNS}
                   FROM email_accounts
                   WHERE user_id = :user_id
                   ORDER BY is_default DESC, created_at"""
            ),
            {"user_id": owner},
        )
        rows = result.fetchall()
        unread_by_account = await _unread_counts(db, owner) if rows else {}
        triples = [(str(r.id), r.email_address, r.label) for r in rows]
        labels, defaults = display_labels(triples), default_labels(triples)
        accounts: list[EmailAccountModel] = []
        for row in rows:
            unread = unread_by_account.get(str(row.id), 0)

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
                import_since=_iso(row.import_since),
                onboarding_done=row.onboarding_done_at is not None,
                color_slot=row.color_slot,
                display_label=labels.get(str(row.id), row.email_address),
                default_label=defaults.get(str(row.id), row.email_address),
                work_domain=work_domain(row.email_address),
                needs_reconnect=needs_reconnect(row.sync_status, row.sync_error),
                created_at=_iso_us(row.created_at),
                in_all_inboxes=bool(row.in_all_inboxes),
                **_progress(row),
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
                f"""INSERT INTO email_accounts
                   (id, user_id, provider, email_address, label,
                    avatar_color, credentials_encrypted, is_default,
                    organization_id, color_slot)
                   VALUES (:id, :user_id, :provider, :email, :label,
                           :color, :creds,
                           NOT EXISTS (SELECT 1 FROM email_accounts
                                       WHERE user_id = :user_id
                                         AND organization_id = CAST(:org AS uuid)),
                           CAST(:org AS uuid), {NEXT_SLOT_SQL})
                   RETURNING is_default, color_slot, created_at,
                             in_all_inboxes"""
            ),
            {
                "id": account_id,
                "user_id": user.email,
                "member": user.email,
                "org": org,
                "provider": req.provider,
                "email": req.email_address,
                "label": req.label or _default_label(req.provider),
                "color": "#6366f1",
                "creds": encrypted_creds,
            },
        )
        created = is_default_row.one()
        created_default = bool(created.is_default)
        labels, defaults = await _display_labels(db, user.email)

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
        color_slot=created.color_slot,
        display_label=labels.get(account_id, req.email_address),
        default_label=defaults.get(account_id, req.email_address),
        work_domain=work_domain(req.email_address),
        created_at=_iso_us(created.created_at),
        in_all_inboxes=bool(created.in_all_inboxes),
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
                f"""UPDATE email_accounts
                   SET is_default = true, updated_at = now()
                   WHERE id = :id AND user_id = :uid
                   RETURNING id, provider, email_address, label, avatar_color,
                             sync_enabled, sync_status, sync_error,
                             last_synced_at, is_default, initial_sync_done,
                             import_since, onboarding_done_at, color_slot,
                             created_at, in_all_inboxes, {_PROGRESS_COLUMNS}"""
            ),
            {"id": account_id, "uid": owner},
        )
        row = result.fetchone()

        unread = (await _unread_counts(db, owner, account_id)).get(
            str(row.id), 0)
        labels, defaults = await _display_labels(db, owner)
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
            import_since=_iso(row.import_since),
            onboarding_done=row.onboarding_done_at is not None,
            color_slot=row.color_slot,
            display_label=labels.get(str(row.id), row.email_address),
            default_label=defaults.get(str(row.id), row.email_address),
            work_domain=work_domain(row.email_address),
            needs_reconnect=needs_reconnect(row.sync_status, row.sync_error),
            created_at=_iso_us(row.created_at),
            in_all_inboxes=bool(row.in_all_inboxes),
            **_progress(row),
        )


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Remove an email account and all its synced messages.

    EM-T4f (``email_app_master_plan.md`` §10.4.6), part 1:

    1. A short block reads the row with the owner predicate. A row that is
       absent, or that another member owns, gives 404 and stops no loop. The
       organization of the row is the one a restart of the loop uses.
    2. With NO block open, the sync loop stops. ``remove_account_sync`` waits
       for the task. Before EM-T4f that wait ran inside the block of the
       ``DELETE``, so the ``DELETE`` held its locks until the task ended.
    3. A new block sets ``lock_timeout`` and runs the ``DELETE`` and the
       default re-election. A sync outside the loop holds a KEY SHARE lock on
       the row until its phase commits. When the wait passes the bound, the
       route answers 409 with ``DISCONNECT_BUSY_DETAIL``. On any failure of
       this block, the loop that step 2 stopped starts again, so a failed
       disconnect does not leave a mailbox with no sync. ``RETURNING`` reads
       the subscription id after the loop stopped, because the loop can
       replace the subscription until then.

    Then, with no block open, a best-effort Graph call removes the push
    subscription of a Microsoft mailbox. Each phase is one block with no
    ``commit()`` (mechanism (A) of §10.4.2). EM-T4f part 2 makes one sync
    run at a time for each mailbox (``email_ingestion.scheduler``). The fence
    is ``tests/unit/test_email_disconnect_order.py``.

    EM-T8f-1 (MB-17): after the block of step 3 commits, a task deletes the
    Mem0 drafting memory of the mailbox (``memory_purge.py``). A 404, a 409 or
    a failed ``DELETE`` raises first, so it starts no purge. The route does
    not wait for the task. The fence is
    ``tests/unit/test_email_disconnect_memory_purge.py``.
    """
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
    org = str(found.organization_id) if found.organization_id else None

    # ── 2. stop the sync loop, with no session open ─────────────────────────
    had_loop = await _stop_sync(account_id)

    # ── 3. the delete and the default re-election, with a lock bound ────────
    try:
        async with _tenant_session() as db:
            await db.execute(text(DISCONNECT_LOCK_TIMEOUT_SQL))
            result = await db.execute(
                text(
                    """DELETE FROM email_accounts
                       WHERE id = :id AND user_id = :user_id
                       RETURNING is_default, provider, credentials_encrypted,
                                 webhook_subscription_id"""
                ),
                {"id": account_id, "user_id": owner},
            )
            deleted = result.fetchone()
            if deleted is None:
                raise HTTPException(status_code=404, detail="Account not found")
            # If the deleted account was the default, re-elect the
            # earliest-created remaining account so the user always has a
            # default to land on.
            if bool(deleted.is_default):
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET is_default = true, updated_at = now()
                           WHERE id = (
                               SELECT id FROM email_accounts
                               WHERE user_id = :uid
                               ORDER BY created_at, id
                               LIMIT 1
                           )"""
                    ),
                    {"uid": owner},
                )
    except HTTPException:
        # The row is gone, so there is no loop to start again.
        raise
    except (Exception, asyncio.CancelledError) as exc:
        # A cancel rolls the DELETE back too, so it also starts the loop again.
        # The restart is shielded: a cancel scope can cancel each later await
        # of this task again, and the restart must still finish.
        if had_loop:
            await _restart_sync_shielded(account_id, org)
        if _is_lock_timeout(exc):
            _log.warning("email.disconnect.busy", account_id=account_id)
            raise HTTPException(
                status_code=409, detail=DISCONNECT_BUSY_DETAIL) from None
        raise

    # ── 4. the Mem0 memory of the mailbox, in a task, after the commit ──────
    # The owner predicate of the DELETE matched, so the row's user_id is
    # ``owner``. The writers key the memory on that member (EM-T8f-1).
    schedule_mailbox_memory_purge(owner, account_id)

    # ── 5. the Graph subscription, best effort, with no session open ────────
    await _drop_graph_subscription(account_id, deleted)


async def _stop_sync(account_id: str) -> bool:
    """Stop the sync loop of a mailbox. True when a loop ran before the stop.

    The answer tells a failed disconnect whether to start the loop again. A
    mailbox with sync off, or a box with ``EMAIL_SYNC_ENABLED`` off, has no
    loop, and a failed disconnect must not start one.
    """
    try:
        from email_ingestion.scheduler import (
            get_scheduler_status,
            remove_account_sync,
        )
        had_loop = account_id in get_scheduler_status()["accounts"]
        await remove_account_sync(account_id)
    except Exception as exc:
        _log.warning("email.disconnect.stop_sync_failed",
                     account_id=account_id, error=type(exc).__name__)
        return False
    return had_loop


#: Restarts of a sync loop that a failed disconnect started. The set holds a
#: strong reference to each task until it ends, so a shielded restart runs on
#: after the request that started it is cancelled.
_RESTARTS: set[asyncio.Task[None]] = set()


async def _restart_sync_shielded(account_id: str, org: str | None) -> None:
    """Run ``_restart_sync`` in its own task, and shield the wait for it.

    A failed disconnect restarts the loop from inside an ``except`` block,
    and that block can run because the request was cancelled. Under a cancel
    scope, each later await of the request task is cancelled again. So the
    restart runs as a task of its own, and ``asyncio.shield`` keeps a second
    cancel of the request from cancelling it (EM-T4f fix round 2).
    """
    task = asyncio.ensure_future(_restart_sync(account_id, org))
    _RESTARTS.add(task)
    task.add_done_callback(_RESTARTS.discard)
    await asyncio.shield(task)


async def _restart_sync(account_id: str, org: str | None) -> None:
    """Start the loop again after a failed disconnect, best effort.

    The organization comes from the row that step 1 read, never from request
    input. A failure logs ``email.disconnect.restart_sync_failed``.
    """
    try:
        from email_ingestion.scheduler import refresh_account_sync
        await refresh_account_sync(account_id, organization_id=org)
    except Exception as exc:
        _log.warning("email.disconnect.restart_sync_failed",
                     account_id=account_id, error=type(exc).__name__)
        return
    _log.info("email.disconnect.sync_restarted", account_id=account_id)


def _is_lock_timeout(err: BaseException) -> bool:
    """True when ``err``, or a driver error under it, is SQLSTATE 55P03.

    SQLAlchemy wraps the asyncpg error, and the adapted error carries
    ``sqlstate``. So the check walks ``orig`` and ``__cause__``.
    """
    seen: BaseException | None = err
    for _ in range(5):
        if seen is None:
            return False
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if code == _LOCK_NOT_AVAILABLE:
            return True
        seen = getattr(seen, "orig", None) or seen.__cause__
    return False


async def _drop_graph_subscription(account_id: str, row: Any) -> None:
    """Remove the Graph push subscription of a deleted mailbox, best effort.

    Only a ``microsoft`` row with a ``webhook_subscription_id`` gets a call.
    The provider comes from ``_instantiate_provider``, the gateway adapter over
    ``build_provider``, as ``_ensure_subscription`` builds it. The whole call
    has a bound of ``SUBSCRIPTION_DELETE_TIMEOUT_S``.

    ``delete_subscription`` returns the HTTP status of Graph. 204 and 404 mean
    the subscription is gone, and log ``email.disconnect.subscription_deleted``.
    Any other status, an error or a timeout logs
    ``email.disconnect.subscription_delete_failed``, and the route still
    answers 204. The log holds the status or the error class, never a token
    or an error text. A refreshed token needs no write, because the row is
    gone.

    The provider has no close method, so the httpx client of this call stays
    open until the process collects it. §10.4.6 records that follow-up.
    """
    sub_id = row.webhook_subscription_id
    if row.provider != "microsoft" or not sub_id:
        return
    try:
        async with asyncio.timeout(SUBSCRIPTION_DELETE_TIMEOUT_S):
            creds, _store = _decrypt_credentials(row.credentials_encrypted)
            provider = _instantiate_provider(row.provider, creds)
            graph_status = await provider.delete_subscription(str(sub_id))
    except Exception as exc:
        response = getattr(exc, "response", None)
        _log.warning(
            "email.disconnect.subscription_delete_failed",
            account_id=account_id,
            error=type(exc).__name__,
            status=getattr(response, "status_code", None),
        )
        return
    if graph_status in _SUBSCRIPTION_GONE:
        _log.info("email.disconnect.subscription_deleted",
                  account_id=account_id, sub=str(sub_id)[:12],
                  status=graph_status)
        return
    _log.warning("email.disconnect.subscription_delete_failed",
                 account_id=account_id, error=None, status=graph_status)


@router.patch("/accounts/{account_id}", response_model=EmailAccountModel)
async def update_account(
    account_id: str,
    updates: AccountUpdateModel,
    user: UserContext = Depends(get_current_user),
):
    """Update account settings (label, colour, sync toggle, the guided setup).

    ``onboarding_done`` true writes ``onboarding_done_at = now()``, and false
    writes NULL (EM-T6a item 11). The owner predicate binds every field.

    EM-T8b: a blank ``label`` writes NULL, so the mailbox takes its default
    label again. A label longer than ``MAX_LABEL_LEN`` answers 400.
    ``color_slot`` must be 1 to 12, or the route answers 400. Neither field
    restarts the sync loop.

    EM-T8g-1: ``in_all_inboxes`` false keeps the mailbox separate, and true
    puts it back in All inboxes (D-EM-28). The owner predicate binds it, so
    the mailbox of another member answers 404 and changes nothing. It does
    not restart the sync loop, because the loop does not read it.
    """
    if updates.color_slot is not None and not valid_slot(updates.color_slot):
        raise HTTPException(status_code=400, detail="color_slot is a whole number from 1 to 12.")
    if updates.label is not None and len(updates.label.strip()) > MAX_LABEL_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"A mailbox name has at most {MAX_LABEL_LEN} characters.",
        )
    if updates.label is not None and reserved_label(updates.label):
        raise HTTPException(
            status_code=400,
            detail=(
                "Outlook, Gmail and Email are kept for the default name. "
                "Choose another name, or leave it blank."
            ),
        )
    owner = user.email or "anonymous"
    async with _tenant_session() as db:
        set_clauses = []
        params: dict[str, Any] = {"id": account_id, "user_id": owner}

        if updates.label is not None:
            set_clauses.append("label = :label")
            params["label"] = updates.label.strip() or None
        if updates.color_slot is not None:
            set_clauses.append("color_slot = :color_slot")
            params["color_slot"] = updates.color_slot
        if updates.sync_enabled is not None:
            set_clauses.append("sync_enabled = :sync_enabled")
            params["sync_enabled"] = updates.sync_enabled
        if updates.onboarding_done is not None:
            set_clauses.append(
                "onboarding_done_at = now()" if updates.onboarding_done
                else "onboarding_done_at = NULL")
        if updates.in_all_inboxes is not None:
            set_clauses.append("in_all_inboxes = :in_all_inboxes")
            params["in_all_inboxes"] = updates.in_all_inboxes

        if not set_clauses:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses.append("updated_at = now()")

        result = await db.execute(
            text(
                f"""UPDATE email_accounts
                    SET {', '.join(set_clauses)}
                    WHERE id = :id AND user_id = :user_id
                    RETURNING id, provider, email_address, label, avatar_color,
                              sync_enabled, sync_status, sync_error, last_synced_at,
                              initial_sync_done, import_since,
                              onboarding_done_at, color_slot, created_at,
                              in_all_inboxes, {_PROGRESS_COLUMNS}"""
            ),
            params,
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Account not found")
        labels, defaults = await _display_labels(db, owner)

    model = EmailAccountModel(
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
        initial_sync_done=bool(row.initial_sync_done),
        import_since=_iso(row.import_since),
        onboarding_done=row.onboarding_done_at is not None,
        color_slot=row.color_slot,
        display_label=labels.get(str(row.id), row.email_address),
        default_label=defaults.get(str(row.id), row.email_address),
        work_domain=work_domain(row.email_address),
        needs_reconnect=needs_reconnect(row.sync_status, row.sync_error),
        created_at=_iso_us(row.created_at),
        in_all_inboxes=bool(row.in_all_inboxes),
        **_progress(row),
    )
    # Only the sync toggle changes what the sync loop reads. A restart cancels
    # a sync in flight, so a rename, a colour, the guided setup or "Keep
    # separate" does not restart it (EM-T8b, EM-T8g-1; before, a rename did).
    if updates.sync_enabled is None:
        return model

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

    return model
