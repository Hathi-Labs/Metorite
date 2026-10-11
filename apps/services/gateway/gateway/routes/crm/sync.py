"""CRM · sync — the status of the mirror sync, and Sync now (WS-53 CRM-Z5).

Spec: ``project-docs/specs/crm_platform.md`` §5.4 (Sync now, Health).

    GET   /crm/sync/status
    POST  /crm/sync/zoho

⚠️ **Dark twice.** Each route needs the CRM kill switch (``CRM_ENABLED`` and
``CRM_ORGS``, at the router) AND ``CRM_MIRROR`` (``flags.require_crm_mirror``).
With ``CRM_MIRROR`` off, each route answers 404.

**Who may do what.** ``GET`` is open to each member that the CRM access rule
admits. It returns counts, codes and times, never a token and never a Zoho
payload. ``POST`` needs ``admin:access:manage``, the rule that the connection
routes use.

**Sync now.** The request takes the run lock, then starts the run in the
background and answers 202 at once. It answers 409 ``sync_running`` while a
run holds the lock, and 409 ``not_connected`` with no live Zoho connection.

The engine is imported inside the handlers. ``gateway.crm_sync`` imports
``routes.crm.core``, and this module loads while the ``routes.crm`` package
loads, so a top-level import would be a cycle.

The organization comes from the server (``UserContext``), never from the
request (R5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from gateway.connections import store
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.flags import require_crm_mirror

#: The five keys of ``counts`` and ``estimate``. Mirrors ``engine.SLUG_ORDER``.
COUNT_KEYS: tuple[str, ...] = ("organizations", "contacts", "leads", "deals", "activities")


class SyncStatusModel(BaseModel):
    """The state of the mirror sync of the caller's organization."""

    state: Literal["never", "running", "idle"] = "never"
    kind: Literal["import", "incremental", "reconcile"] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_success_at: datetime | None = None
    counts: dict[str, int] = {}
    estimate: dict[str, int] | None = None
    error: str | None = None
    next_run_at: datetime | None = None


class StartedModel(BaseModel):
    started: bool = True


def _organization(user: UserContext) -> str:
    """The caller's organization, from the server. None is a 403."""
    if not user.organization_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: you are in no organization.",
        )
    return str(user.organization_id)


def _seen(counts: dict) -> dict[str, int]:
    """``seen`` for each of the five keys, from the run's counts."""
    out: dict[str, int] = {}
    for key in COUNT_KEYS:
        value = counts.get(key)
        out[key] = int(value.get("seen") or 0) if isinstance(value, dict) else int(value or 0)
    return out


def _estimate(estimate: dict | None) -> dict[str, int] | None:
    """The estimate when it covers all five keys, else ``None``."""
    if not estimate or any(key not in estimate for key in COUNT_KEYS):
        return None
    return {key: int(estimate[key] or 0) for key in COUNT_KEYS}


@router.get(
    "/sync/status", response_model=SyncStatusModel,
    dependencies=[Depends(require_crm_mirror)],
)
async def get_sync_status(
    user: UserContext = Depends(get_current_user),
) -> SyncStatusModel:
    """The latest run of the org, as the UI shows it. No token, no Zoho payload."""
    from gateway.crm_sync import engine, runs

    org = _organization(user)
    async with _tenant_session() as db:
        latest = await runs.latest(db, org)
        if latest is None:
            return SyncStatusModel()
        last_ok = await runs.last_success_at(db, org)
        connection = await store.get(db, org, engine.PROVIDER, engine.OWNING_APP)
    # A running row with an old heartbeat is dead. The next run takes it over.
    live = (
        latest.status == runs.STATUS_RUNNING
        and latest.heartbeat_at is not None
        and datetime.now(UTC) - latest.heartbeat_at
        < timedelta(seconds=runs.STALE_AFTER_SECONDS)
    )
    next_run = None
    if (
        not live
        and latest.finished_at is not None
        and connection is not None
        and connection.status == store.STATUS_CONNECTED
    ):
        next_run = latest.finished_at + timedelta(seconds=engine.INTERVAL_SECONDS)
    return SyncStatusModel(
        state="running" if live else "idle",
        kind=latest.kind,  # type: ignore[arg-type]
        started_at=latest.started_at,
        finished_at=latest.finished_at,
        last_success_at=last_ok,
        counts=_seen(latest.counts),
        estimate=_estimate(latest.estimate),
        error=latest.error,
        next_run_at=next_run,
    )


@router.post(
    "/sync/zoho", response_model=StartedModel, status_code=202,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def sync_now(
    user: UserContext = Depends(get_current_user),
) -> StartedModel:
    """Start a run now. It answers before the run ends."""
    from gateway.crm_sync import engine, scheduler

    org = _organization(user)
    try:
        handle = await engine.acquire(org)
    except engine.NotConnected as exc:
        raise HTTPException(status_code=409, detail="not_connected") from exc
    except engine.SyncRunning as exc:
        raise HTTPException(status_code=409, detail="sync_running") from exc
    scheduler.spawn(handle)
    return StartedModel(started=True)
