"""Tasks · sync — pull existing provider tasks into the GTD mirror (§9.3 #1).

Until now sync was push-only: locally clarified items could be pushed to a
connected workspace, but the tasks that ALREADY live there never appeared in
Metorite. This module adds the pull:

  POST /tasks/sync {account_id?, full?}
    → for one account (or every sync-enabled account of the user), pull the
      workspace's tasks through the provider interface layer and upsert them
      into ``gtd_items`` as SYNCED rows. Incremental by default (provider
      ``updated_since_ms`` cursor stored in ``task_accounts.last_delta_token``);
      ``full=true`` re-pulls everything.

GTD lens applied to NEW pulled rows (the reverse of the push mapping P7 —
someday→Backlog / actioned→To-do):

  closed in the tool          → DONE (completed_at from the tool)
  backlog-ish stage           → SOMEDAY
  assigned to me              → NEXT      (actioned in the tool = clarified)
  assigned to someone else    → WAITING   (+ open ``gtd_waiting`` record,
                                           is_mine=false — a monitored task)
  unassigned                  → NEXT, is_mine=false (team pool, not my list)

Re-syncs only refresh the MIRRORED fields (title, description, stage,
assignee, due, completion) — the user's GTD overlay (context, energy,
project refile, a deliberate disposition) is never clobbered. The one
exception is completion state, where the provider is the source of truth
for SYNCED rows (§5.1): closed upstream forces DONE; reopened upstream
un-DONEs back to the mapped open disposition.

Read-only toward the provider (constraint C-04 untouched: the only upstream
write remains the explicit ``POST /items/{id}/push``).
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.tasks.core import (
    _assert_account_owner,
    _key_store,
    _log,
    _parse_jsonb,
    _tenant_session,
    _uid,
    router,
)
from gateway.routes.tasks.providers import build_provider
from pydantic import BaseModel
from sqlalchemy import text

# Stage names that read as "parked / not actioned yet" across PM tools.
_BACKLOG_STAGES = ("backlog", "icebox", "someday", "later", "parked", "on hold")

# A synced project with no open task touched in this window reads as dormant
# (passive) and is demoted to SOMEDAY so clarify stops proposing it. Passed as
# a bound query parameter (make_interval), never string-interpolated.
_DORMANT_DAYS = 45


class SyncRequest(BaseModel):
    account_id: str | None = None   # None → every sync-enabled account
    full: bool = False              # ignore the incremental cursor


class AccountSyncResult(BaseModel):
    account_id: str
    label: str = ""
    pulled: int = 0                 # tasks returned by the provider
    created: int = 0                # new gtd_items rows
    updated: int = 0                # existing rows refreshed
    completed: int = 0              # rows flipped to DONE this run
    skipped: int = 0                # new closed tasks not mirrored (see toggle)
    error: str | None = None


def map_pulled_task(task: dict[str, Any], my_provider_id: str) -> dict[str, Any]:
    """Pure GTD mapping for ONE pulled provider task (unit-tested).

    Returns the fields the upsert binds: disposition, is_mine, assignee
    (JSON-ready dict or None), completed_at_ms, waiting_on (dict or None —
    set only for the WAITING case).
    """
    assignees = task.get("assignees") or []
    mine = any(
        str(a.get("provider_user_id") or "") == str(my_provider_id or "")
        for a in assignees
    ) if my_provider_id else False

    closed = bool(task.get("closed_at_ms")) or (
        (task.get("status_type") or "").lower() in ("closed", "done")
    )
    stage = (task.get("status") or "").lower()
    backlogish = any(b in stage for b in _BACKLOG_STAGES)

    # Prefer me among the assignees for the display assignee; else the first.
    assignee = None
    if assignees:
        assignee = next(
            (a for a in assignees
             if str(a.get("provider_user_id") or "") == str(my_provider_id or "")),
            assignees[0],
        )

    if closed:
        disposition = "DONE"
    elif backlogish:
        disposition = "SOMEDAY"
    elif assignees and not mine:
        disposition = "WAITING"
    else:
        disposition = "NEXT"

    return {
        "disposition": disposition,
        "is_mine": mine,
        "assignee": assignee,
        # The FULL owner set (ClickUp allows several) — kept so a shared task
        # shows every owner, not just the display one.
        "assignees": assignees,
        "completed_at_ms": task.get("closed_at_ms") if closed else None,
        # A monitored task: record who we're waiting on (drives gtd_waiting).
        "waiting_on": assignee if disposition == "WAITING" else None,
    }


def _dt(ms: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC) if ms else None
    except (TypeError, ValueError, OSError):
        return None


_UPSERT_SQL = text("""
    INSERT INTO gtd_items
        (id, user_id, source, account_id, provider_task_id, provider_url,
         title, description, disposition, project_id, provider_status,
         assignee, assignees, is_mine, due_at, completed_at, sync_state, synced_at)
    VALUES
        (:id, :uid, 'SYNCED', :aid, :tid, :url,
         :title, :descr, :disp, :pid, :status,
         :assignee, :assignees, :mine, :due, :completed, 'synced', now())
    ON CONFLICT (account_id, provider_task_id) WHERE source <> 'LOCAL'
    DO UPDATE SET
        title           = EXCLUDED.title,
        description     = coalesce(EXCLUDED.description, gtd_items.description),
        provider_url    = coalesce(EXCLUDED.provider_url, gtd_items.provider_url),
        provider_status = EXCLUDED.provider_status,
        assignee        = EXCLUDED.assignee,
        assignees       = EXCLUDED.assignees,
        is_mine         = EXCLUDED.is_mine,
        due_at          = EXCLUDED.due_at,
        completed_at    = EXCLUDED.completed_at,
        -- Provider owns completion for SYNCED rows; the user owns the rest of
        -- the GTD overlay, so an open task keeps its current disposition
        -- unless the row was DONE and got reopened upstream.
        disposition = CASE
            WHEN EXCLUDED.completed_at IS NOT NULL THEN 'DONE'
            WHEN gtd_items.disposition = 'DONE'
                 AND EXCLUDED.completed_at IS NULL THEN EXCLUDED.disposition
            ELSE gtd_items.disposition
        END,
        project_id  = coalesce(gtd_items.project_id, EXCLUDED.project_id),
        sync_state  = 'synced',
        synced_at   = now(),
        updated_at  = now()
    RETURNING id, (xmax = 0) AS inserted, disposition, completed_at
""")


async def _sync_account(db: Any, account: Any, *, full: bool) -> AccountSyncResult:
    """Pull one account's tasks and upsert the mirror. Commits on success.

    H2 note: the trailing commit is this helper's LAST database action, so a
    request handler may run it as the sole occupant of a `_tenant_session`
    block — every statement above the commit runs under the tenant GUC, and
    the wrapper's own commit on clean exit is then an empty no-op. Do not add
    statements after the commit, and do not call this mid-block."""
    result = AccountSyncResult(account_id=str(account.id),
                               label=account.label or "")
    creds = json.loads(_key_store().decrypt(account.credentials_encrypted))
    provider = build_provider(account.provider, creds, account.workspace_id)

    # Whose list is "mine": the identity that connected this workspace.
    identity = await provider.verify()
    my_id = str((identity.get("user") or {}).get("provider_user_id") or "")

    # Incremental cursor: epoch-ms of the previous sync start (safe overlap).
    since_ms: int | None = None
    if not full and account.last_delta_token:
        try:
            since_ms = int(account.last_delta_token)
        except ValueError:
            since_ms = None
    run_started_ms = int(time.time() * 1000)

    tasks = await provider.list_tasks(account.workspace_id,
                                      updated_since_ms=since_ms)
    result.pulled = len(tasks)

    # Keep the member cache honest on every sync: someone removed in the
    # tool must drop out of the delegate picker, not linger until the next
    # full schema refresh. Same pass folds in any status names seen on the
    # pulled tasks that the schema didn't already carry — ground truth for
    # list-level statuses (e.g. a "Done" a list defines but the space doesn't),
    # so they reach the status-mapping settings and the stage picker.
    try:
        members = await provider.list_members(account.workspace_id)
        cache = _parse_jsonb(account.schema_cache) or {}
        cache["members"] = members
        statuses = [s for s in cache.get("statuses") or [] if isinstance(s, str)]
        have = {s.lower() for s in statuses}
        for t in tasks:
            name = (t.get("status") or "").strip()
            if name and name.lower() not in have:
                statuses.append(name)
                have.add(name.lower())
        cache["statuses"] = statuses
        await db.execute(
            text("""UPDATE task_accounts
                    SET schema_cache = :cache WHERE id = :id"""),
            {"id": str(account.id), "cache": json.dumps(cache)},
        )
    except Exception as exc:
        _log.warning("tasks.sync.members_refresh_failed",
                     error=str(exc)[:120])

    # provider list/project ref → mirrored gtd_projects id (for linkage).
    proj_rows = (await db.execute(
        text("""SELECT id, provider_ref FROM gtd_projects
                WHERE account_id = :aid AND source <> 'LOCAL'"""),
        {"aid": str(account.id)},
    )).fetchall()
    project_by_ref = {r.provider_ref: str(r.id) for r in proj_rows
                      if r.provider_ref}

    # Unless the user opts in, we don't import a provider's completed-task
    # backlog — it would swamp the working board. We still reflect completion
    # of tasks we ALREADY mirror, so preload their provider ids and only skip a
    # DONE task when it's brand-new to us.
    from gateway.routes.tasks.settings import gtd_toggles
    mirror_done = (await gtd_toggles(db, account.user_id)).get(
        "mirror_done_tasks", False)
    known_ids: set[str] = set()
    if not mirror_done:
        known_rows = (await db.execute(
            text("""SELECT provider_task_id FROM gtd_items
                    WHERE account_id = :aid AND source <> 'LOCAL'
                      AND provider_task_id IS NOT NULL"""),
            {"aid": str(account.id)},
        )).fetchall()
        known_ids = {r.provider_task_id for r in known_rows}

    # Email-linked tasks that were ALREADY DONE before this sync — so when the
    # provider reports one done we only propagate to the email thread on the real
    # open→DONE transition, not on every re-sync (which would re-close a thread
    # the user deliberately reopened while the upstream task stayed closed).
    done_email_before = {
        r.provider_task_id for r in (await db.execute(text(
            """SELECT provider_task_id FROM gtd_items
                WHERE account_id = :aid AND source <> 'LOCAL'
                  AND provider_task_id IS NOT NULL
                  AND disposition = 'DONE'
                  AND origin->>'thread_id' IS NOT NULL"""),
            {"aid": str(account.id)},
        )).fetchall()
    }

    for task in tasks:
        tid = task.get("provider_task_id")
        if not tid:
            continue
        mapped = map_pulled_task(task, my_id)
        # Skip NEW completed tasks when done-mirroring is off. (Tasks we already
        # mirror fall through so their completion is reflected.)
        if (not mirror_done and mapped["disposition"] == "DONE"
                and tid not in known_ids):
            result.skipped += 1
            continue
        row = (await db.execute(_UPSERT_SQL, {
            "id": str(uuid4()),
            "uid": account.user_id,
            "aid": str(account.id),
            "tid": tid,
            "url": task.get("provider_url"),
            "title": task.get("title") or "Untitled",
            "descr": task.get("description"),
            "disp": mapped["disposition"],
            "pid": project_by_ref.get(task.get("project_ref")),
            "status": task.get("status"),
            "assignee": json.dumps(mapped["assignee"])
            if mapped["assignee"] else None,
            "assignees": json.dumps(mapped.get("assignees") or []),
            "mine": mapped["is_mine"],
            "due": _dt(task.get("due_at_ms")),
            "completed": _dt(mapped["completed_at_ms"]),
        })).fetchone()

        if row.inserted:
            result.created += 1
        else:
            result.updated += 1

        if row.completed_at is not None:
            result.completed += 1
            # A finished task can't be waited on — resolve open records.
            await db.execute(
                text("""UPDATE gtd_waiting SET resolved = true
                        WHERE item_id = :iid AND resolved = false"""),
                {"iid": str(row.id)},
            )
            # Closed upstream just now (open→DONE this sync) AND linked to an
            # email thread → mark that thread Done. Only on the transition, so a
            # thread the user reopened isn't re-closed on every subsequent sync.
            if tid not in done_email_before:
                orow = (await db.execute(text(
                    "SELECT origin FROM gtd_items WHERE id = :id"),
                    {"id": str(row.id)})).fetchone()
                if orow is not None and orow.origin is not None:
                    from gateway.routes.tasks.email_link import (
                        propagate_task_done_to_thread)
                    with contextlib.suppress(Exception):  # best-effort
                        await propagate_task_done_to_thread(db, orow)
        elif row.disposition == "WAITING" and mapped["waiting_on"]:
            # Monitored task (assigned to someone else): keep exactly one
            # open waiting-for record pointing at the current assignee.
            # `expected_by` stays NULL — the provider's due date is the TASK's
            # deadline (already upserted into gtd_items.due_at above), not a
            # promise the assignee made us, and a copy of it here would never
            # move again when the date is rescheduled upstream. The overdue
            # line falls back to the live `due_at` instead.
            await db.execute(
                text("""INSERT INTO gtd_waiting
                            (item_id, waiting_on, delegated_at)
                        SELECT :iid, :who, :delegated
                        WHERE NOT EXISTS (SELECT 1 FROM gtd_waiting
                                          WHERE item_id = :iid
                                            AND resolved = false)"""),
                {"iid": str(row.id),
                 "who": json.dumps(mapped["waiting_on"]),
                 "delegated": _dt(task.get("created_at_ms"))
                 or datetime.now(tz=UTC)},
            )

    # Active vs passive (dormant) provider projects. A synced project with no
    # OPEN task touched in the activity window reads as dormant → demote to
    # SOMEDAY so clarify stops proposing it as a home for new work.
    #
    # ONE-DIRECTIONAL BY DESIGN: we only demote ACTIVE → SOMEDAY. We do NOT
    # auto-promote SOMEDAY → ACTIVE, because sync-derived dormancy and a user's
    # deliberate "park this" share the one `status` column — auto-promoting
    # would silently overwrite the user's choice the moment a teammate touched
    # any task. A freshly-synced project enters as ACTIVE (via _refresh_schema),
    # so genuinely-active projects stay ACTIVE and only real dormancy demotes;
    # a user's manual SOMEDAY (or DONE/DROPPED) is never resurrected. The WHERE
    # `status = 'ACTIVE'` + the dormant predicate means only rows that actually
    # change are written (no updated_at churn). Bound param, not an f-string.
    await db.execute(
        text("""
            UPDATE gtd_projects p
               SET status = 'SOMEDAY', updated_at = now()
             WHERE p.account_id = :aid
               AND p.source <> 'LOCAL'
               AND p.status = 'ACTIVE'
               AND NOT EXISTS (
                   SELECT 1 FROM gtd_items i
                    WHERE i.project_id = p.id
                      AND i.disposition NOT IN ('DONE', 'TRASH')
                      AND i.updated_at > now() - make_interval(days => :dormant_days)
               )
        """),
        {"aid": str(account.id), "dormant_days": _DORMANT_DAYS},
    )

    await db.execute(
        text("""UPDATE task_accounts
                SET sync_status = 'idle', sync_error = NULL,
                    last_synced_at = now(), last_delta_token = :cursor,
                    updated_at = now()
                WHERE id = :id"""),
        {"id": str(account.id), "cursor": str(run_started_ms)},
    )
    await db.commit()
    return result


@router.get("/sync/status")
async def sync_status(user: UserContext = Depends(get_current_user)):
    """Freshness + background-scheduler health for the user's workspaces.

    Per account: sync_enabled/interval, last_synced_at, current sync_status,
    any error, and whether it's overdue (now - last_synced_at > interval → the
    background loop hasn't refreshed it in time, e.g. loop not yet running).
    Plus the live scheduler state (which account loops are running)."""
    uid = _uid(user)
    async with _tenant_session() as db:
        rows = (await db.execute(
            text("""SELECT id, label, provider, sync_enabled, sync_interval_secs,
                           sync_status, sync_error, last_synced_at,
                           EXTRACT(EPOCH FROM (now() - last_synced_at)) AS age_secs
                    FROM task_accounts WHERE user_id = :uid
                    ORDER BY created_at"""),
            {"uid": uid},
        )).fetchall()

    try:
        from gateway.routes.tasks.scheduler import get_scheduler_status
        sched = get_scheduler_status()
    except Exception:
        sched = {"running": False, "accounts": [], "count": 0}

    running = set(sched.get("accounts") or [])
    accounts = []
    for r in rows:
        interval = r.sync_interval_secs or 300
        age = float(r.age_secs) if r.age_secs is not None else None
        # Overdue = enabled but the last successful sync is older than one
        # interval (with a grace multiple to avoid flapping mid-cycle).
        overdue = bool(
            r.sync_enabled and (age is None or age > interval * 2)
        )
        accounts.append({
            "account_id": str(r.id),
            "label": r.label or "",
            "provider": r.provider,
            "sync_enabled": bool(r.sync_enabled),
            "sync_interval_secs": interval,
            "sync_status": r.sync_status or "idle",
            "sync_error": r.sync_error,
            "last_synced_at": r.last_synced_at.isoformat()
            if r.last_synced_at else None,
            "age_secs": int(age) if age is not None else None,
            "loop_running": str(r.id) in running,
            "overdue": overdue,
        })
    return {"scheduler_running": bool(sched.get("running")), "accounts": accounts}


@router.post("/sync", response_model=list[AccountSyncResult])
async def sync_tasks(
    req: SyncRequest,
    user: UserContext = Depends(get_current_user),
):
    """Pull provider tasks into the GTD mirror for one or all accounts.

    Sequential per account (a user has a handful of workspaces, and the
    provider rate limits are per token anyway). One account failing records
    its error on the account row and in the response — it doesn't abort the
    other accounts' syncs.
    """
    uid = _uid(user)
    async with _tenant_session() as db:
        if req.account_id:
            rows = [await _assert_account_owner(db, req.account_id, uid)]
        else:
            rows = (await db.execute(
                text("""SELECT * FROM task_accounts
                        WHERE user_id = :uid AND sync_enabled = true
                        ORDER BY created_at"""),
                {"uid": uid},
            )).fetchall()
        if not rows:
            raise HTTPException(status_code=400,
                                detail="No sync-enabled accounts to sync")

    # One transaction per step, per account (H2 restructure of the old
    # commit-as-you-go shape): the 'syncing' marker must be visible before the
    # potentially slow provider pull, and an account's failure must roll back
    # only its own pull while still recording the error.
    results: list[AccountSyncResult] = []
    for account in rows:
        async with _tenant_session() as db:
            await db.execute(
                text("""UPDATE task_accounts SET sync_status = 'syncing',
                        updated_at = now() WHERE id = :id"""),
                {"id": str(account.id)},
            )
        try:
            async with _tenant_session() as db:
                results.append(await _sync_account(db, account, full=req.full))
        except Exception as exc:
            msg = str(getattr(exc, "detail", None) or exc)[:500]
            _log.warning("tasks.sync.account_failed",
                         account_id=str(account.id)[:12], error=msg)
            async with _tenant_session() as db:
                await db.execute(
                    text("""UPDATE task_accounts SET sync_status = 'error',
                            sync_error = :e, updated_at = now()
                            WHERE id = :id"""),
                    {"id": str(account.id), "e": msg},
                )
            results.append(AccountSyncResult(
                account_id=str(account.id),
                label=account.label or "", error=msg,
            ))
    return results
