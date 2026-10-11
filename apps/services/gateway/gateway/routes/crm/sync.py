"""CRM · sync — the status of the mirror sync, and Sync now (WS-53 CRM-Z5).

Spec: ``project-docs/specs/crm_platform.md`` §5.4 (Sync now, Health).

    GET   /crm/sync/status
    GET   /crm/sync/health      (admin; CRM-Q9, the Sync health card)
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
from typing import Any, Literal

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.connections import store
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.flags import require_crm_mirror
from pydantic import BaseModel
from sqlalchemy import text

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
    failed: int = 0
    estimate: dict[str, int] | None = None
    error: str | None = None
    next_run_at: datetime | None = None


class HealthRunModel(BaseModel):
    """One run, as the health card lists it. The error is a short code."""

    kind: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    credits_used: int | None = None


class HealthEntityModel(BaseModel):
    """One entity: the Zoho count, our count and what the last run did."""

    key: str
    zoho_count: int | None = None
    ours: int | None = None
    #: The count keys the last run wrote, passed through (``seen``, ``failed``,
    #: ``unavailable`` and any key that a later slice adds).
    last_run: dict[str, int] = {}


class HealthModuleModel(BaseModel):
    api_name: str
    label: str
    custom: bool = False
    api_supported: bool = False
    mirrored: bool = False


class HealthCreditsModel(BaseModel):
    budget_24h: int
    used_24h: int


class SyncHealthModel(BaseModel):
    """``GET /crm/sync/health``. Counts, codes and times. No token, no record."""

    last_run: HealthRunModel | None = None
    last_success_at: datetime | None = None
    next_run_at: datetime | None = None
    entities: list[HealthEntityModel] = []
    modules: list[HealthModuleModel] = []
    history: list[HealthRunModel] = []
    credits: HealthCreditsModel


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


def _failed(counts: dict) -> int:
    """The records of the last run that failed to write."""
    return sum(
        int(v.get("failed") or 0) for v in counts.values() if isinstance(v, dict)
    )


def _estimate(estimate: dict | None) -> dict[str, int] | None:
    """The estimate when it covers all five keys, else ``None``."""
    if not estimate or any(key not in estimate for key in COUNT_KEYS):
        return None
    return {key: int(estimate[key] or 0) for key in COUNT_KEYS}


def _is_live(latest: Any, runs: Any) -> bool:
    """A running row with an old heartbeat is dead. The next run takes it over."""
    return bool(
        latest.status == runs.STATUS_RUNNING
        and latest.heartbeat_at is not None
        and datetime.now(UTC) - latest.heartbeat_at
        < timedelta(seconds=runs.STALE_AFTER_SECONDS)
    )


def _next_run(
    latest: Any, live: bool, connection: Any, budget: int | None, engine: Any,
) -> datetime | None:
    """When the loop reads next: the end of the last run plus the spaced wait."""
    if (
        live
        or latest.finished_at is None
        or connection is None
        or connection.status != store.STATUS_CONNECTED
    ):
        return None
    return latest.finished_at + timedelta(
        seconds=engine.next_interval(latest.credits_used, budget))


@router.get(
    "/sync/status", response_model=SyncStatusModel,
    dependencies=[Depends(require_crm_mirror)],
)
async def get_sync_status(
    user: UserContext = Depends(get_current_user),
) -> SyncStatusModel:
    """The latest run of the org, as the UI shows it. No token, no Zoho payload."""
    from gateway.crm_sync import engine, profiles, runs

    org = _organization(user)
    async with _tenant_session() as db:
        latest = await runs.latest(db, org)
        if latest is None:
            return SyncStatusModel()
        last_ok = await runs.last_success_at(db, org)
        connection = await store.get(db, org, engine.PROVIDER, engine.OWNING_APP)
        budget = await profiles.budget_for(db, org)
    live = _is_live(latest, runs)
    next_run = _next_run(latest, live, connection, budget, engine)
    return SyncStatusModel(
        state="running" if live else "idle",
        kind=latest.kind,  # type: ignore[arg-type]
        started_at=latest.started_at,
        finished_at=latest.finished_at,
        last_success_at=last_ok,
        counts=_seen(latest.counts),
        failed=_failed(latest.counts),
        estimate=_estimate(latest.estimate),
        error=latest.error,
        next_run_at=next_run,
    )


#: The local table of each entity key, for ``ours``. A key that is not here
#: (``tasks``, ``converted_leads``) has no local count of its own.
_OURS_TABLES: dict[str, str] = {
    "organizations": "crm_companies",
    "contacts": "crm_contacts",
    "leads": "crm_leads",
    "deals": "crm_deals",
    "activities": "crm_activities",
}


def _run_model(row: Any) -> HealthRunModel:
    return HealthRunModel(
        kind=row.kind, status=row.status, started_at=row.started_at,
        finished_at=row.finished_at, error=row.error, credits_used=row.credits_used,
    )


def _int(value: Any) -> int | None:
    """A count as an int. Anything else is ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _last_run_counts(value: Any) -> dict[str, int]:
    """The keys of one entity in the counts. A bare number is ``seen``."""
    if isinstance(value, dict):
        return {str(k): n for k, v in value.items() if (n := _int(v)) is not None}
    n = _int(value)
    return {"seen": n} if n is not None else {}


def _entity_keys(counts: dict, estimate: dict) -> list[str]:
    """The five keys in order, then any other key of the counts or the estimate."""
    extra = sorted((set(counts) | set(estimate)) - set(COUNT_KEYS))
    return [*COUNT_KEYS, *extra]


@router.get(
    "/sync/health", response_model=SyncHealthModel,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def get_sync_health(
    user: UserContext = Depends(get_current_user),
) -> SyncHealthModel:
    """The state of the mirror for the Sync health card (CRM-Q9). Read only.

    Counts, codes and times. No token, no Zoho payload, no record. A key in the
    counts that this module does not know passes through, so a later slice
    adds an entity with no change here.
    """
    from gateway.crm_sync import engine, profiles, runs

    org = _organization(user)
    ours: dict[str, int] = {}
    async with _tenant_session() as db:
        latest = await runs.latest(db, org)
        last_ok = await runs.last_success_at(db, org)
        connection = await store.get(db, org, engine.PROVIDER, engine.OWNING_APP)
        profile = await profiles.latest(db, org)
        used = await runs.credits_used_24h(db, org)
        history = await runs.recent(db, org, 10)
        for key, table in _OURS_TABLES.items():
            row = (await db.execute(text(
                f"SELECT count(*) AS n FROM {table} "
                "WHERE organization_id = CAST(:org AS uuid) AND ext_source = :src"),
                {"org": org, "src": engine.EXT_SOURCE})).first()
            ours[key] = int(row.n or 0)
    body = profile.profile if profile is not None else None
    budget = engine.daily_budget(body)
    counts = latest.counts if latest is not None else {}
    estimate = (latest.estimate if latest is not None else None) or {}
    entities = [
        HealthEntityModel(
            key=key,
            zoho_count=_int(estimate.get(key)),
            ours=ours.get(key),
            last_run=_last_run_counts(counts.get(key)),
        )
        for key in _entity_keys(counts, estimate)
    ]
    modules = [
        HealthModuleModel(
            api_name=str(m["api_name"]),
            label=str(m.get("label") or m["api_name"]),
            custom=bool(m.get("custom")),
            api_supported=bool(m.get("api_supported")),
            mirrored=bool(m.get("mirrored")),
        )
        for m in (body or {}).get("modules") or []
        if isinstance(m, dict) and m.get("api_name")
    ]
    live = latest is not None and _is_live(latest, runs)
    return SyncHealthModel(
        last_run=_run_model(latest) if latest is not None else None,
        last_success_at=last_ok,
        next_run_at=(
            _next_run(latest, live, connection, budget, engine)
            if latest is not None else None
        ),
        entities=entities,
        modules=modules,
        history=[_run_model(r) for r in history],
        credits=HealthCreditsModel(budget_24h=budget, used_24h=used),
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
