"""Tasks · accounts — connected PM-tool workspace CRUD + schema cache.

Connect flow (ClickUp needs more than a bare token — it needs the workspace):
  1. POST /tasks/providers/{provider}/workspaces  {api_token}
       → verifies the token and returns the workspaces it can reach.
  2. POST /tasks/accounts  {provider, api_token, workspace_id, label}
       → stores ONE account row for that workspace (credentials encrypted).
     Repeat with another workspace_id (or another token) to connect several
     ClickUp workspaces/companies side by side — multi-account, like email.
  3. POST /tasks/accounts/{id}/schema/refresh
       → fetch-beforehand schema (§2.2.1): projects/members/statuses into
         task_accounts.schema_cache, provider lists mirrored into
         gtd_projects (source='SYNCED') so Clarify pickers are instant.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, status
from gateway.routes.tasks.core import (
    PersonModel,
    TaskAccountModel,
    _assert_account_owner,
    _key_store,
    _log,
    _parse_jsonb,
    _tenant_session,
    _uid,
    router,
)
from gateway.routes.tasks.providers import build_provider, connector_names
from pydantic import BaseModel
from sqlalchemy import text


class WorkspacesRequest(BaseModel):
    api_token: str


class CreateAccountRequest(BaseModel):
    provider: str                     # 'clickup' | … (see /tasks/providers)
    api_token: str
    workspace_id: str
    label: str = ""


class AccountUpdateRequest(BaseModel):
    label: str | None = None
    sync_enabled: bool | None = None


def _row_to_account(row: Any) -> TaskAccountModel:
    cache = _parse_jsonb(row.schema_cache) or {}
    members = [
        PersonModel(
            name=str(m.get("name") or ""),
            email=m.get("email"),
            provider_user_id=m.get("provider_user_id"),
        )
        for m in cache.get("members") or []
        if isinstance(m, dict) and m.get("name")
    ]
    return TaskAccountModel(
        id=str(row.id),
        provider=row.provider,
        connector_kind=row.connector_kind,
        workspace_id=row.workspace_id,
        label=row.label or "",
        sync_enabled=bool(row.sync_enabled),
        sync_status=row.sync_status or "idle",
        sync_error=row.sync_error,
        last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
        statuses=[s for s in cache.get("statuses") or [] if isinstance(s, str)],
        members=members,
        project_count=len(cache.get("projects") or []),
        hierarchy=[h for h in cache.get("hierarchy") or [] if isinstance(h, dict)],
    )


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    """Registered connector types (the interface layer's registry)."""
    return {"providers": connector_names()}


@router.post("/providers/{provider}/workspaces")
async def list_provider_workspaces(
    provider: str,
    req: WorkspacesRequest,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Step 1 of connect: verify the token, return its reachable workspaces."""
    p = build_provider(provider, {"api_token": req.api_token})
    identity = await p.verify()
    workspaces = await p.list_workspaces()
    return {"user": identity.get("user"), "workspaces": workspaces}


@router.get("/accounts", response_model=list[TaskAccountModel])
async def list_accounts(user: UserContext = Depends(get_current_user)):
    async with _tenant_session() as db:
        rows = (await db.execute(
            text("""SELECT * FROM task_accounts WHERE user_id = :uid
                    ORDER BY created_at"""),
            {"uid": _uid(user)},
        )).fetchall()
        return [_row_to_account(r) for r in rows]


@router.post("/accounts", response_model=TaskAccountModel, status_code=201)
async def create_account(
    req: CreateAccountRequest,
    user: UserContext = Depends(get_current_user),
):
    """Step 2 of connect: store one workspace as an account (encrypted creds),
    then do the first schema fetch so Clarify pickers work immediately."""
    provider = build_provider(req.provider, {"api_token": req.api_token},
                              req.workspace_id)
    # Validate before storing: the token must actually reach this workspace.
    workspaces = {w["id"]: w for w in await provider.list_workspaces()}
    if req.workspace_id not in workspaces:
        raise HTTPException(
            status_code=400,
            detail=f"Workspace {req.workspace_id} is not reachable with this token",
        )

    encrypted = _key_store().encrypt(json.dumps({"api_token": req.api_token}))
    label = req.label or workspaces[req.workspace_id].get("name") or req.provider

    async with _tenant_session() as db:
        dup = (await db.execute(
            text("""SELECT 1 FROM task_accounts
                    WHERE user_id = :uid AND provider = :p AND workspace_id = :w"""),
            {"uid": _uid(user), "p": req.provider, "w": req.workspace_id},
        )).fetchone()
        if dup:
            raise HTTPException(status_code=409,
                                detail="This workspace is already connected")

        account_id = str(uuid4())
        await db.execute(
            text("""INSERT INTO task_accounts
                    (id, user_id, provider, connector_kind, workspace_id, label,
                     credentials_encrypted)
                    VALUES (:id, :uid, :p, 'api', :w, :label, :creds)"""),
            {"id": account_id, "uid": _uid(user), "p": req.provider,
             "w": req.workspace_id, "label": label, "creds": encrypted},
        )
    # The account row is committed above; the first schema fetch runs in its
    # own transaction so a provider failure can't undo the connect (H2
    # restructure of the old commit-then-continue shape).
    try:
        async with _tenant_session() as db:
            await _refresh_schema(db, account_id, _uid(user))
            await _reconcile_people(db, _uid(user))
    except Exception as exc:
        async with _tenant_session() as db:
            await db.execute(
                text("""UPDATE task_accounts SET sync_status='error',
                        sync_error=:e, updated_at=now() WHERE id=:id"""),
                {"id": account_id, "e": str(exc)[:500]},
            )
    async with _tenant_session() as db:
        row = (await db.execute(
            text("SELECT * FROM task_accounts WHERE id = :id"), {"id": account_id},
        )).fetchone()
    # Launch this workspace's background sync loop now (no gateway restart).
    try:
        from gateway.routes.tasks.scheduler import refresh_account_sync
        await refresh_account_sync(account_id)
    except Exception as exc:
        _log.warning("tasks.accounts.scheduler_start_failed",
                     account_id=account_id[:12], error=str(exc)[:160])
    return _row_to_account(row)


@router.patch("/accounts/{account_id}", response_model=TaskAccountModel)
async def update_account(
    account_id: str,
    req: AccountUpdateRequest,
    user: UserContext = Depends(get_current_user),
):
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, _uid(user))
        sets, params = [], {"id": account_id}
        if req.label is not None:
            sets.append("label = :label")
            params["label"] = req.label
        if req.sync_enabled is not None:
            sets.append("sync_enabled = :se")
            params["se"] = req.sync_enabled
        if not sets:
            raise HTTPException(status_code=400, detail="No fields to update")
        sets.append("updated_at = now()")
        row = (await db.execute(
            text(f"UPDATE task_accounts SET {', '.join(sets)} "
                 "WHERE id = :id RETURNING *"),
            params,
        )).fetchone()
    # Reflect a sync_enabled toggle in the background scheduler at runtime —
    # AFTER the block above committed, because the scheduler re-reads the row
    # on its own session and must see the new value.
    if req.sync_enabled is not None:
        try:
            if req.sync_enabled:
                from gateway.routes.tasks.scheduler import refresh_account_sync
                await refresh_account_sync(account_id)
            else:
                from gateway.routes.tasks.scheduler import remove_account_sync
                await remove_account_sync(account_id)
        except Exception as exc:
            _log.warning("tasks.accounts.scheduler_toggle_failed",
                         account_id=account_id[:12], error=str(exc)[:160])
    return _row_to_account(row)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Disconnect a workspace. Its mirrored rows cascade away (FK ON DELETE)."""
    async with _tenant_session() as db:
        res = (await db.execute(
            text("""DELETE FROM task_accounts
                    WHERE id = :id AND user_id = :uid RETURNING id"""),
            {"id": account_id, "uid": _uid(user)},
        )).fetchone()
        if res is None:
            raise HTTPException(status_code=404, detail="Account not found")
    # Stop this workspace's background sync loop (after the delete committed).
    try:
        from gateway.routes.tasks.scheduler import remove_account_sync
        await remove_account_sync(account_id)
    except Exception as exc:
        _log.warning("tasks.accounts.scheduler_remove_failed",
                     account_id=account_id[:12], error=str(exc)[:160])


@router.post("/accounts/{account_id}/schema/refresh", response_model=TaskAccountModel)
async def refresh_account_schema(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Re-fetch the provider schema (projects/members/statuses) on demand."""
    async with _tenant_session() as db:
        await _assert_account_owner(db, account_id, _uid(user))
        await _refresh_schema(db, account_id, _uid(user))
        await _reconcile_people(db, _uid(user))
        row = (await db.execute(
            text("SELECT * FROM task_accounts WHERE id = :id"), {"id": account_id},
        )).fetchone()
        return _row_to_account(row)


class CreateProjectRequest(BaseModel):
    name: str
    space_id: str
    folder_id: str | None = None


@router.post("/accounts/{account_id}/members/refresh",
             response_model=TaskAccountModel)
async def refresh_account_members(
    account_id: str,
    user: UserContext = Depends(get_current_user),
):
    """LIVE member pull (delegate-picker freshness): people removed in the
    tool disappear immediately, without the heavier full schema refresh."""
    async with _tenant_session() as db:
        row = await _assert_account_owner(db, account_id, _uid(user))
        creds = json.loads(_key_store().decrypt(row.credentials_encrypted))
        provider = build_provider(row.provider, creds, row.workspace_id)
        members = await provider.list_members(row.workspace_id)
        cache = _parse_jsonb(row.schema_cache) or {}
        cache["members"] = members
        await db.execute(
            text("""UPDATE task_accounts
                    SET schema_cache = :cache, updated_at = now()
                    WHERE id = :id"""),
            {"id": account_id, "cache": json.dumps(cache)},
        )
    # The member cache is committed above; keep the org roster in step with
    # live membership (§6) in its own transaction, so a reconcile hiccup never
    # loses the refreshed cache (H2 restructure).
    async with _tenant_session() as db:
        await _reconcile_people(db, _uid(user))
        fresh = (await db.execute(
            text("SELECT * FROM task_accounts WHERE id = :id"),
            {"id": account_id},
        )).fetchone()
        return _row_to_account(fresh)


@router.post("/accounts/{account_id}/projects", status_code=201)
async def create_account_project(
    account_id: str,
    req: CreateProjectRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create a NEW project (ClickUp: a List) in the workspace, under the
    chosen space and optional folder — a user-approved provider write (the
    explicit "Create project" action in the picker, same posture as push).
    The new list is mirrored into gtd_projects and the schema cache so the
    picker shows it immediately."""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")
    async with _tenant_session() as db:
        row = await _assert_account_owner(db, account_id, _uid(user))
        creds = json.loads(_key_store().decrypt(row.credentials_encrypted))
        provider = build_provider(
            row.provider, creds, row.workspace_id, str(row.id))
        created = await provider.create_project(
            row.workspace_id, name, req.space_id, req.folder_id)

        # Mirror into gtd_projects (same upsert the schema refresh uses).
        project_id = str(uuid4())
        proj_row = (await db.execute(
            text("""INSERT INTO gtd_projects
                    (id, user_id, source, account_id, provider_ref, outcome,
                     status)
                    VALUES (:id, :uid, 'SYNCED', :aid, :ref, :outcome,
                            'ACTIVE')
                    ON CONFLICT (account_id, provider_ref)
                        WHERE source <> 'LOCAL'
                    DO UPDATE SET outcome = EXCLUDED.outcome,
                                  updated_at = now()
                    RETURNING id"""),
            {"id": project_id, "uid": _uid(user), "aid": account_id,
             "ref": created["id"], "outcome": name},
        )).fetchone()

        # Keep the cached schema + hierarchy in step (picker refreshes from it).
        cache = _parse_jsonb(row.schema_cache) or {}
        space_name = ""
        for sp in cache.get("hierarchy") or []:
            if str(sp.get("id")) != str(req.space_id):
                continue
            space_name = sp.get("name", "")
            node = {"id": created["id"], "name": name}
            if req.folder_id:
                for f in sp.get("folders") or []:
                    if str(f.get("id")) == str(req.folder_id):
                        f.setdefault("lists", []).append(node)
                        break
            else:
                sp.setdefault("lists", []).append(node)
            break
        cache.setdefault("projects", []).append({
            "id": created["id"], "name": name,
            "space": space_name, "space_id": req.space_id,
            "folder_id": req.folder_id, "folder_name": None,
        })
        await db.execute(
            text("""UPDATE task_accounts
                    SET schema_cache = :cache, updated_at = now()
                    WHERE id = :id"""),
            {"id": account_id, "cache": json.dumps(cache)},
        )
        return {
            "project_id": str(proj_row.id),
            "provider_ref": created["id"],
            "name": name,
        }


class CreateAccountFolderRequest(BaseModel):
    name: str
    space_id: str


@router.post("/accounts/{account_id}/folders", status_code=201)
async def create_account_folder(
    account_id: str,
    req: CreateAccountFolderRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create a NEW folder in the workspace under the chosen space (ClickUp:
    space → folder → list) — a user-approved provider write, same posture as
    create_account_project. The folder is appended to the cached hierarchy so
    the Clarify picker can immediately create lists inside it."""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name is required")
    async with _tenant_session() as db:
        row = await _assert_account_owner(db, account_id, _uid(user))
        creds = json.loads(_key_store().decrypt(row.credentials_encrypted))
        provider = build_provider(
            row.provider, creds, row.workspace_id, str(row.id))
        created = await provider.create_folder(
            row.workspace_id, req.space_id, name)

        cache = _parse_jsonb(row.schema_cache) or {}
        for sp in cache.get("hierarchy") or []:
            if str(sp.get("id")) != str(req.space_id):
                continue
            sp.setdefault("folders", []).append({
                "id": created["id"], "name": created["name"], "lists": [],
            })
            break
        await db.execute(
            text("""UPDATE task_accounts
                    SET schema_cache = :cache, updated_at = now()
                    WHERE id = :id"""),
            {"id": account_id, "cache": json.dumps(cache)},
        )
        return {
            "folder_id": created["id"],
            "space_id": req.space_id,
            "name": created["name"],
        }


async def _refresh_schema(db: Any, account_id: str, user_id: str) -> None:
    """Fetch the schema through the connector; cache it + mirror projects.

    Mirrored provider lists become SYNCED ``gtd_projects`` rows (upsert on
    (account_id, provider_ref)) so projects from every source render in the
    one unified picker (§5.1).

    Does NOT commit, and no longer calls ``_reconcile_people`` itself — the
    caller owns the transaction and sequences the reconcile (H2: a mid-helper
    commit inside a ``_tenant_session`` block would drop the tenant GUC for
    every statement after it). Request handlers run inside a tenant block whose
    clean exit commits; the background scheduler commits explicitly.
    """
    row = (await db.execute(
        text("SELECT * FROM task_accounts WHERE id = :id"), {"id": account_id},
    )).fetchone()
    creds = json.loads(_key_store().decrypt(row.credentials_encrypted))
    provider = build_provider(row.provider, creds, row.workspace_id)
    schema = await provider.get_schema(row.workspace_id)

    await db.execute(
        text("""UPDATE task_accounts
                SET schema_cache = :cache, sync_status = 'idle', sync_error = NULL,
                    last_synced_at = now(), updated_at = now()
                WHERE id = :id"""),
        {"id": account_id, "cache": json.dumps(schema)},
    )
    for proj in schema.get("projects") or []:
        name = (proj.get("name") or "").strip()
        if not name:
            continue
        await db.execute(
            text("""INSERT INTO gtd_projects
                    (id, user_id, source, account_id, provider_ref, outcome, status)
                    VALUES (:id, :uid, 'SYNCED', :aid, :ref, :outcome, 'ACTIVE')
                    ON CONFLICT (account_id, provider_ref) WHERE source <> 'LOCAL'
                    DO UPDATE SET outcome = EXCLUDED.outcome, updated_at = now()"""),
            {"id": str(uuid4()), "uid": user_id, "aid": account_id,
             "ref": str(proj.get("id")), "outcome": name},
        )


async def _reconcile_people(db: Any, user_id: str) -> None:
    """Reconcile the org roster (``people``) against CURRENT ClickUp
    membership so assignment suggestions stay honest as people join/leave (§6).

    Runs on every schema refresh. ``people`` is org-global while
    ``task_accounts`` is per-user, so the live membership is the UNION of every
    connected workspace's cached members (the account just refreshed is already
    in the cache). Conservative by design:

    - A member with no matching person → INSERT (source='clickup', active).
    - A person with no ``clickup_user_id`` matched by email/name → LINK the id.
    - A person we auto-added (``source='clickup'``) whose id vanished from every
      workspace → mark ``status='inactive'`` (reappearing → 'active' again).

    NEVER touches manually-added or seed-imported people's status (the user owns
    those). Best-effort — a reconcile failure never breaks the sync/refresh.

    Does NOT commit — the caller owns the transaction (a request handler's
    ``_tenant_session`` block commits on clean exit; the background scheduler
    commits explicitly). H2: a mid-block commit would drop the tenant GUC.
    """
    try:
        acct_rows = (await db.execute(
            text("SELECT schema_cache FROM task_accounts"))).fetchall()
        # Union of live members across every connected workspace.
        live_pids: set[str] = set()
        by_pid: dict[str, dict[str, Any]] = {}
        by_email: dict[str, dict[str, Any]] = {}
        by_name: dict[str, dict[str, Any]] = {}
        for a in acct_rows:
            for m in (_parse_jsonb(a.schema_cache) or {}).get("members") or []:
                if not isinstance(m, dict):
                    continue
                pid = str(m.get("provider_user_id") or "").strip()
                name = (m.get("name") or "").strip()
                if not (pid or name):
                    continue
                if pid:
                    live_pids.add(pid)
                    by_pid[pid] = m
                if m.get("email"):
                    by_email.setdefault(str(m["email"]).strip().lower(), m)
                if name:
                    by_name.setdefault(name.lower(), m)

        people = (await db.execute(text(
            "SELECT id, name, email, clickup_user_id, source, status "
            "FROM people"))).fetchall()
        matched_pids: set[str] = set()
        for p in people:
            pid = str(p.clickup_user_id or "").strip()
            email = (p.email or "").strip().lower()
            name = (p.name or "").strip().lower()
            # Which live member (if any) is this person?
            member = (by_pid.get(pid) if pid else None) \
                or (by_email.get(email) if email else None) \
                or (by_name.get(name) if name else None)
            if member is not None:
                mpid = str(member.get("provider_user_id") or "").strip()
                if mpid:
                    matched_pids.add(mpid)
                # Link the ClickUp id onto a person that didn't have one yet.
                if mpid and not pid:
                    await db.execute(text(
                        "UPDATE people SET clickup_user_id = :pid, "
                        "updated_at = now() WHERE id = :id"),
                        {"pid": mpid, "id": str(p.id)})
                # Reactivate an auto-added person who's back in the workspace.
                if p.source == "clickup" and p.status != "active":
                    await db.execute(text(
                        "UPDATE people SET status = 'active', "
                        "updated_at = now() WHERE id = :id"), {"id": str(p.id)})
            elif (p.source == "clickup" and pid and pid not in live_pids
                  and p.status == "active"):
                # We auto-added this person from ClickUp and their id is gone
                # from every workspace → they left. Deactivate (never delete).
                await db.execute(text(
                    "UPDATE people SET status = 'inactive', "
                    "updated_at = now() WHERE id = :id"), {"id": str(p.id)})

        # Insert workspace members who aren't in the roster at all.
        for pid, m in by_pid.items():
            if pid in matched_pids:
                continue
            name = (m.get("name") or "").strip()
            if not name:
                continue
            await db.execute(text(
                """INSERT INTO people
                   (id, name, email, status, clickup_user_id, source,
                    skills, updated_by, updated_at)
                   VALUES (:id, :name, :email, 'active', :pid, 'clickup',
                           ARRAY[]::text[], 'clickup-sync', now())
                   ON CONFLICT (name) DO UPDATE
                       SET clickup_user_id = COALESCE(
                               people.clickup_user_id, EXCLUDED.clickup_user_id),
                           updated_at = now()"""),
                {"id": str(uuid4()), "name": name,
                 "email": m.get("email"), "pid": pid})
    except Exception as exc:  # noqa: BLE001
        _log.warning("tasks.reconcile_people_failed", error=str(exc)[:200])
