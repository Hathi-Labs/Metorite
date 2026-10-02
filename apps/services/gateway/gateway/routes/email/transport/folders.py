"""Transport · folders & labels — folder/label listing and per-folder backfill."""

from __future__ import annotations

import json

from acb_auth import UserContext, get_current_user
from email_ingestion import import_window
from fastapi import Depends, HTTPException
from gateway.routes.email.core import (
    _tenant_session,
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _upsert_message,
    router,
)
from pydantic import BaseModel, Field
from sqlalchemy import text


class BackfillRequest(BaseModel):
    folder: str = "inbox"
    page_token: str | None = None
    max_pages: int = Field(default=3, ge=1, le=10)


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class LabelInfo(BaseModel):
    """A user-applicable label/category with its canonical colour token."""
    name: str
    # 'preset0'..'preset24' (see providers/label_colors.py) or null if unset.
    color: str | None = None


class SetLabelColorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(pattern=r"^preset(2[0-4]|1[0-9]|[0-9])$")


class EmailFolderModel(BaseModel):
    provider_folder_id: str
    name: str
    type: str = "system"  # 'system' | 'user'
    message_count: int = 0
    unread_count: int = 0


@router.get("/accounts/{account_id}/folders", response_model=list[EmailFolderModel])
async def list_folders(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """List folders/labels for a connected email account.

    Fetches live from the provider (Gmail labels, Outlook folders, IMAP mailboxes)
    so the UI always shows the current folder structure.
    """
    async with _tenant_session() as db:
        try:
            result = await db.execute(
                text(
                    """SELECT provider, credentials_encrypted
                       FROM email_accounts
                       WHERE id = :id AND user_id = :user_id"""
                ),
                {"id": account_id, "user_id": user.email or "anonymous"},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            # Decrypt credentials
            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))

            # Instantiate provider
            provider = _instantiate_provider(row.provider, creds)

            # Authenticate and fetch folders
            if not await provider.authenticate():
                raise HTTPException(
                    status_code=401,
                    detail="Email account authentication failed — token may have expired",
                )

            folders = await provider.list_folders()

            # Persist rotated OAuth tokens so a later sync doesn't reuse a stale one.
            if provider.credentials_dirty():
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET credentials_encrypted = :creds, updated_at = now()
                           WHERE id = :id"""
                    ),
                    {
                        "id": account_id,
                        "creds": store.encrypt(
                            json.dumps(provider.export_credentials())
                        ),
                    },
                )

            return [
                EmailFolderModel(
                    provider_folder_id=f.provider_folder_id,
                    name=f.name,
                    type=f.type,
                    message_count=f.message_count,
                    unread_count=f.unread_count,
                )
                for f in folders
            ]
        except HTTPException:
            raise
        except Exception as exc:
            _log.error("list_folders.failed", account_id=account_id, error=str(exc)[:200])
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list folders: {str(exc)}",
            )


@router.post(
    "/accounts/{account_id}/folders", response_model=EmailFolderModel
)
async def create_folder(
    account_id: str,
    req: CreateFolderRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create (or reuse) a folder/label on the connected account and persist it.

    Backs the rule editor's "Create new folder" affordance.  Idempotent — the
    provider returns the existing folder if one with the same name already
    exists (Outlook get-or-create, Gmail label create).
    """
    async with _tenant_session() as db:
        try:
            row = (await db.execute(
                text(
                    """SELECT provider, credentials_encrypted
                       FROM email_accounts
                       WHERE id = :id AND user_id = :user_id"""
                ),
                {"id": account_id, "user_id": user.email or "anonymous"},
            )).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))
            provider = _instantiate_provider(row.provider, creds)
            if not await provider.authenticate():
                raise HTTPException(
                    status_code=401,
                    detail="Email account authentication failed — reconnect.",
                )

            try:
                folder = await provider.create_folder(req.name)
            except NotImplementedError:
                raise HTTPException(
                    status_code=400,
                    detail="This account type doesn't support creating folders.",
                )

            # Mirror into email_folders so the folder is queryable immediately.
            await db.execute(
                text(
                    """INSERT INTO email_folders
                         (account_id, provider_folder_id, name, type)
                       VALUES (:aid, :pid, :name, :type)
                       ON CONFLICT (account_id, provider_folder_id)
                       DO UPDATE SET name = EXCLUDED.name"""
                ),
                {"aid": account_id, "pid": folder.provider_folder_id,
                 "name": folder.name, "type": folder.type},
            )
            await _persist_rotated_creds(db, store, account_id, provider)

            return EmailFolderModel(
                provider_folder_id=folder.provider_folder_id,
                name=folder.name,
                type=folder.type,
                message_count=folder.message_count,
                unread_count=folder.unread_count,
            )
        except HTTPException:
            raise
        except Exception as exc:
            _log.error(
                "create_folder.failed", account_id=account_id, error=str(exc)[:200]
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to create folder: {str(exc)}"
            )


@router.get("/accounts/{account_id}/labels", response_model=list[LabelInfo])
async def list_labels(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """List user-applicable labels/categories (with colours) for an account.

    Each entry is ``{name, color}`` where ``color`` is a canonical preset token
    ('preset0'..'preset24') or null. Gmail = user labels, Outlook = master
    categories, IMAP = none.
    """
    async with _tenant_session() as db:
        try:
            result = await db.execute(
                text(
                    """SELECT provider, credentials_encrypted
                       FROM email_accounts
                       WHERE id = :id AND user_id = :user_id"""
                ),
                {"id": account_id, "user_id": user.email or "anonymous"},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))

            try:
                provider = _instantiate_provider(row.provider, creds)
            except HTTPException:
                # list_labels degrades gracefully for an unknown provider.
                return []

            if not await provider.authenticate():
                raise HTTPException(
                    status_code=401,
                    detail="Email account authentication failed — reconnect.",
                )
            labels = await provider.list_labels()

            if provider.credentials_dirty():
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET credentials_encrypted = :creds, updated_at = now()
                           WHERE id = :id"""
                    ),
                    {
                        "id": account_id,
                        "creds": store.encrypt(
                            json.dumps(provider.export_credentials())
                        ),
                    },
                )
            return labels
        except HTTPException:
            raise
        except Exception as exc:
            _log.error("list_labels.failed", account_id=account_id, error=str(exc)[:200])
            raise HTTPException(status_code=500, detail=f"Failed to list labels: {exc}")


@router.patch("/accounts/{account_id}/labels", response_model=LabelInfo)
async def set_label_color(
    account_id: str,
    req: SetLabelColorRequest,
    user: UserContext = Depends(get_current_user),
):
    """Set a label/category's colour on the provider (Gmail label / Outlook
    master category). The colour is a canonical preset token; it round-trips to
    the real mailbox. No-op for providers without label colours (IMAP)."""
    async with _tenant_session() as db:
        try:
            result = await db.execute(
                text(
                    """SELECT provider, credentials_encrypted
                       FROM email_accounts
                       WHERE id = :id AND user_id = :user_id"""
                ),
                {"id": account_id, "user_id": user.email or "anonymous"},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))
            provider = _instantiate_provider(row.provider, creds)

            if not await provider.authenticate():
                raise HTTPException(
                    status_code=401,
                    detail="Email account authentication failed — reconnect.",
                )
            await provider.set_label_color(req.name, req.color)
            await _persist_rotated_creds(db, store, account_id, provider)
            return LabelInfo(name=req.name, color=req.color)
        except HTTPException:
            raise
        except Exception as exc:
            _log.error(
                "set_label_color.failed", account_id=account_id, error=str(exc)[:200]
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to set label colour: {exc}"
            )


@router.post("/accounts/{account_id}/backfill")
async def backfill_folder(
    account_id: str,
    req: BackfillRequest,
    user: UserContext = Depends(get_current_user),
):
    """Fetch OLDER messages for a folder from the provider and persist them.

    The list view is DB-backed and the initial sync only grabs the newest
    ~100 per folder, so this pages further back through the provider's history
    on demand.  Returns the next page token so the client can keep loading
    older mail until ``exhausted`` is true.

    The ceiling of 180 days binds this path too (D-EM-10, EM-T6a). It writes
    no message older than the ceiling. The pages come newest first, so the
    first page that reaches below the ceiling is the last page, and the answer
    then reads ``exhausted``.
    """
    from email_ingestion.providers.base import canonical_folder

    async with _tenant_session() as db:
        try:
            result = await db.execute(
                text(
                    """SELECT provider, credentials_encrypted
                       FROM email_accounts
                       WHERE id = :id AND user_id = :user_id"""
                ),
                {"id": account_id, "user_id": user.email or "anonymous"},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Account not found")

            from acb_llm.key_store import get_key_store
            store = get_key_store()
            creds = json.loads(store.decrypt(row.credentials_encrypted))

            provider = _instantiate_provider(row.provider, creds)

            if not await provider.authenticate():
                raise HTTPException(
                    status_code=401,
                    detail="Email account authentication failed — reconnect.",
                )

            canon_req = canonical_folder(req.folder)

            # Resolve the provider-native folder id/label for the canonical key so
            # both system and user folders page correctly (Gmail label id, Graph
            # folder id, IMAP mailbox name).
            provider_folder = req.folder
            try:
                for f in await provider.list_folders():
                    if canonical_folder(f.name) == canon_req:
                        provider_folder = f.provider_folder_id
                        break
            except Exception:
                pass

            ceiling = import_window.ceiling()
            token = req.page_token
            synced = 0
            for _ in range(req.max_pages):
                msgs, token = await provider.list_messages(
                    folder=provider_folder,
                    max_results=100,
                    page_token=token,
                    canonical_override=canon_req,
                )
                dropped = 0
                for msg in msgs:
                    if import_window.below_floor(msg.received_at, ceiling):
                        dropped += 1
                        continue
                    await _upsert_message(db, account_id, msg)
                    synced += 1
                if dropped:
                    # Every later page is older still, so stop here.
                    _log.info("backfill.reached_ceiling", account_id=account_id,
                              dropped=dropped)
                    token = None
                if not token:
                    break

            if provider.credentials_dirty():
                await db.execute(
                    text(
                        """UPDATE email_accounts
                           SET credentials_encrypted = :creds, updated_at = now()
                           WHERE id = :id"""
                    ),
                    {
                        "id": account_id,
                        "creds": store.encrypt(
                            json.dumps(provider.export_credentials())
                        ),
                    },
                )

            return {
                "synced": synced,
                "next_page_token": token,
                "exhausted": token is None,
            }
        except HTTPException:
            raise
        except Exception as exc:
            _log.error("backfill.failed", account_id=account_id, error=str(exc)[:200])
            raise HTTPException(status_code=500, detail=f"Backfill failed: {str(exc)}")
