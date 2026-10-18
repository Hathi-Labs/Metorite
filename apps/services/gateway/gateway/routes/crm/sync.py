"""CRM · sync — the status of the mirror sync, and Sync now (WS-53 CRM-Z5).

Spec: ``project-docs/specs/crm_platform.md`` §5.4 (Sync now, Health).

    GET   /crm/sync/status
    GET   /crm/sync/health      (admin; CRM-Q9, the Sync health card)
    GET   /crm/sync/short-reads (each member; CRM-Q30b, the short reads)
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
from acb_common import get_logger
from fastapi import Depends, HTTPException
from gateway.connections import store
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.flags import require_crm_mirror
from pydantic import BaseModel
from sqlalchemy import text

#: The five keys of ``counts`` and ``estimate``. Mirrors ``engine.SLUG_ORDER``.
COUNT_KEYS: tuple[str, ...] = ("organizations", "contacts", "leads", "deals", "activities")

#: The keys of ``counts`` that are not an entity, so no Sync health row and
#: no ``failed`` sum reads them. ``activity_drops`` counts the activities that
#: the sync did not store, by parent module (CRM-Q23a). ``activity_recovered``
#: counts the kept activities that the recovery step linked, by parent module
#: (CRM-Q24). They mirror ``engine.ACTIVITY_DROPS_KEY`` and
#: ``engine.ACTIVITY_RECOVERED_KEY``, and a test pins each pair.
NOT_ENTITY_KEYS: frozenset[str] = frozenset({"activity_drops", "activity_recovered"})


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
    #: ``unavailable`` and any key that a later slice adds). CRM-Q19 adds
    #: ``unmapped``: the values that a field rule found filled and could not
    #: map. A count only, so the shape of this route does not change.
    last_run: dict[str, int] = {}
    #: CRM-Q24: Zoho's count minus our rows at the end of the last full read,
    #: when above 0. The connected Zoho user cannot see those records. Null
    #: when the read was whole, and for the activities.
    short_by: int | None = None


class HealthActivitiesModel(BaseModel):
    """The activities that the mirror does not hold, and why (CRM-Q24).
    Numbers only, never an id. A number that is 0 is left out."""

    #: The activities that name no parent record, by activity entity
    #: (``note``, ``task`` …), from the last full read of each.
    parentless: dict[str, int] = {}
    #: The activities of the last full reads whose parent module the sync
    #: did not hold, by the Zoho API name of that module.
    dropped: dict[str, int] = {}
    #: The kept activities that wait for their parent record, by entity.
    waiting: dict[str, int] = {}
    #: The kept activities that linked in the runs of the last 7 days, by
    #: the Zoho API name of the parent module.
    recovered_7d: dict[str, int] = {}
    #: The drops of a module that the sync reads, which no ``waiting`` entry
    #: holds, by the Zoho API name of the module (CRM-Q24 fix round). The
    #: sync keeps no drop of a core module (Deals, Accounts, Contacts,
    #: Leads), so each one counts. A mirrored generic module counts only the
    #: drops past a full wait list (``recover.CAP``). Without this number,
    #: those drops show nowhere on the card.
    unkept: dict[str, int] = {}


class HealthModuleModel(BaseModel):
    api_name: str
    label: str
    custom: bool = False
    api_supported: bool = False
    mirrored: bool = False


class HealthCreditsModel(BaseModel):
    budget_24h: int
    used_24h: int
    #: CRM-Q10: when the credits of the 24-hour window fall below the budget,
    #: so the card can say when a ``budget`` pause ends. Null while the window
    #: is below the budget.
    resumes_at: datetime | None = None


class SyncHealthModel(BaseModel):
    """``GET /crm/sync/health``. Counts, codes and times. No token, no record."""

    last_run: HealthRunModel | None = None
    last_success_at: datetime | None = None
    next_run_at: datetime | None = None
    entities: list[HealthEntityModel] = []
    modules: list[HealthModuleModel] = []
    history: list[HealthRunModel] = []
    #: True only for a running row with a fresh heartbeat. A crashed run is not live.
    running: bool = False
    credits: HealthCreditsModel
    #: CRM-Q16: the hold entries of the org, by count key.
    holds: dict[str, HoldModel] = {}
    #: CRM-Q24: the activities that the mirror does not hold.
    activities: HealthActivitiesModel = HealthActivitiesModel()
    #: CRM-Q32-4: the count of the open questions (``questions.count_open``),
    #: so "Needs you" stays pure. ``None`` outside ``CRM_QUESTIONS_ORGS``.
    open_questions: int | None = None


class StartedModel(BaseModel):
    started: bool = True


class HoldModel(BaseModel):
    """One hold entry (CRM-Q16). ``count`` is the provider count when it was set."""

    held: bool
    by: str
    count: int | None = None
    at: datetime | None = None


class SyncNowIn(BaseModel):
    """``POST /crm/sync/zoho``. ``only`` syncs one count key on its own (CRM-Q16)."""

    only: str | None = None


class HoldIn(BaseModel):
    """``PUT /crm/sync/modules/{key}``. ``sync: false`` holds the key."""

    sync: bool


class HoldsModel(BaseModel):
    holds: dict[str, HoldModel] = {}


SyncHealthModel.model_rebuild()


def _hold_key_known(key: str, profile_body: dict | None) -> bool:
    """A count key that a hold may name: a fixed key, or a GENERIC module of
    the profile. A core module (Accounts, Deals) syncs under its fixed key, so
    ``module:Accounts`` would hold nothing and is refused."""
    from gateway.crm_sync import holds

    if key in holds.FIXED_KEYS:
        return True
    if not key.startswith(holds.MODULE_PREFIX):
        return False
    name = key[len(holds.MODULE_PREFIX):]
    return any(
        isinstance(m, dict) and m.get("api_name") == name and m.get("generic")
        for m in (profile_body or {}).get("modules") or []
    )


def _holds_out(entries: dict[str, dict[str, Any]]) -> dict[str, HoldModel]:
    return {
        k: HoldModel(
            held=bool(v.get("held")), by=str(v.get("by") or ""),
            count=_int(v.get("count")), at=v.get("at"),
        )
        for k, v in entries.items()
    }


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
        int(v.get("failed") or 0) for k, v in counts.items()
        if isinstance(v, dict) and k not in NOT_ENTITY_KEYS
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


#: The count key of a generic module (CRM-Q6a). Mirrors ``engine._module``.
MODULE_KEY_PREFIX = "module:"

#: ``ours`` of each generic module, from the generic record store.
_MODULE_OURS_SQL = (
    "SELECT module, count(*) AS n FROM crm_module_records "
    "WHERE organization_id = CAST(:org AS uuid) AND ext_source = :src "
    "GROUP BY module"
)

#: ``ours`` of one generic module (CRM-Q30b).
_MODULE_OURS_ONE_SQL = (
    "SELECT count(*) AS n FROM crm_module_records "
    "WHERE organization_id = CAST(:org AS uuid) AND ext_source = :src AND module = :module"
)


#: CRM-Q24: the ``activity_recovered`` counts of the runs that started in the
#: last 7 days, and after the last start over, summed by module. The key is
#: a bound parameter. A value that is not a whole number is skipped.
_RECOVERED_7D_SQL = (
    "SELECT e.key AS module, sum(CAST(e.value AS bigint)) AS n "
    "FROM crm_sync_runs r CROSS JOIN LATERAL jsonb_each_text("
    "CASE WHEN jsonb_typeof(r.counts -> CAST(:key AS text)) = 'object' "
    "THEN r.counts -> CAST(:key AS text) ELSE '{}'::jsonb END) AS e "
    "WHERE r.organization_id = CAST(:org AS uuid) "
    "AND r.started_at > now() - interval '7 days' "
    "AND r.started_at > COALESCE((SELECT reset_at FROM crm_settings "
    "WHERE organization_id = CAST(:org AS uuid)), '-infinity'::timestamptz) "
    "AND e.value ~ '^[0-9]{1,12}$' "
    "GROUP BY e.key"
)

#: The cursor modules of the entities, ``zoho:<entity>``. A deleted list
#: ends with this, and has no ``short_by``.
_DELETED_SUFFIX = ":deleted"


def _short_by_keys(cursors: dict[str, Any], engine: Any) -> dict[str, int]:
    """``short_by`` of each count key, from the cursor of its entity (CRM-Q24).

    Only a core entity that is not an activity and a generic module carry it.
    Every other cursor row (the field rules, the module counts …) is skipped.
    """
    prefix = f"{engine.PROVIDER}:"
    out: dict[str, int] = {}
    for module, row in cursors.items():
        if not module.startswith(prefix) or module.endswith(_DELETED_SUFFIX):
            continue
        entity = module[len(prefix):]
        if entity in engine.ACTIVITY_TYPES:
            continue
        if entity in engine.SLUGS:
            key = engine.SLUGS[entity]
        elif entity.startswith(MODULE_KEY_PREFIX):
            key = entity
        else:
            continue
        value = _int((row.state or {}).get(engine.SHORT_BY_KEY))
        if value is not None and value > 0:
            out[key] = value
    return out


class ShortReadModel(BaseModel):
    """One entity or module that Zoho holds more of than the mirror can read
    (CRM-Q30b). Counts only."""

    #: The count key: a core slug (``contacts``) or ``module:<ApiName>``.
    key: str
    #: Zoho's count: the count of the last run, else ours plus the gap.
    zoho_count: int
    #: What the connected Zoho user can read: Zoho's count minus the gap.
    readable: int
    #: The gap of the cursor, ``short_by`` (CRM-Q24).
    short_by: int


async def _ours_of(db: Any, org: str, key: str, engine: Any) -> int:
    """The mirrored rows of one count key."""
    if key.startswith(MODULE_KEY_PREFIX):
        row = (await db.execute(text(_MODULE_OURS_ONE_SQL), {
            "org": org, "src": engine.EXT_SOURCE, "module": key[len(MODULE_KEY_PREFIX):],
        })).first()
        return int(row.n or 0) if row is not None else 0
    table = _OURS_TABLES.get(key)
    if table is None:
        return 0
    row = (await db.execute(text(
        f"SELECT count(*) AS n FROM {table} "
        "WHERE organization_id = CAST(:org AS uuid) AND ext_source = :src"),
        {"org": org, "src": engine.EXT_SOURCE})).first()
    return int(row.n or 0) if row is not None else 0


async def short_reads(db: Any, org: str) -> list[ShortReadModel]:
    """Each key with a short read, and its two counts (WS-53 CRM-Q30b).

    The ONE reader of the short reads for the read tools. It reads the gap
    through :func:`_short_by_keys`, as the health route does. The two counts
    follow the rule of the card (``syncHealth.ts`` ``shortReadSentence``):
    Zoho's count is the count of the last run, else ours plus the gap, and
    the readable count is Zoho's count minus the gap. With no gap it reads
    no run and no count, so the common case costs one statement.
    """
    from gateway.crm_sync import engine, runs

    cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
    short = _short_by_keys(cursors, engine)
    if not short:
        return []
    latest = await runs.latest(db, org)
    estimate = (latest.estimate if latest is not None else None) or {}
    out = []
    for key, gap in sorted(short.items()):
        zoho = _int(estimate.get(key))
        if zoho is None or zoho <= 0:
            zoho = await _ours_of(db, org, key, engine) + gap
        out.append(ShortReadModel(key=key, zoho_count=zoho, readable=max(zoho - gap, 0),
                                  short_by=gap))
    return out


@router.get(
    "/sync/short-reads", response_model=list[ShortReadModel],
    dependencies=[Depends(require_crm_mirror)],
)
async def get_short_reads(
    user: UserContext = Depends(get_current_user),
) -> list[ShortReadModel]:
    """The short reads, for each member that the CRM access rule admits
    (CRM-Q30b). The read tools of ``crm_tools.py`` add one line for each.
    Counts only. A Native org gets an empty list."""
    from gateway.routes.crm.core import is_mirror_mode

    org = _organization(user)
    async with _tenant_session() as db:
        if not await is_mirror_mode(db, org):
            return []
        return await short_reads(db, org)


def _unkept(
    dropped: dict[str, int], cursors: dict[str, Any], profile: Any, engine: Any,
) -> dict[str, int]:
    """The drops of a synced module that no kept entry holds (CRM-Q24 fix
    round). See :attr:`HealthActivitiesModel.unkept`.

    A core module is never kept (``engine._note_dropped``), so its whole
    count is unkept. A mirrored generic module is unkept only when a kept
    list overflowed for it, and then by the drops that the list does not
    hold. A module that the sync does not read is not here: the card says
    so through ``dropped``.
    """
    from gateway.crm_sources import registry
    from gateway.crm_sync import recover

    mapping = registry.mapping_for(engine.PROVIDER)
    generic = {
        str(m["api_name"]) for m in mapping.generic_modules(profile)
        if m.get("mirrored") and m.get("api_name")
    }
    kept_by_module: dict[str, int] = {}
    overflow: set[str] = set()
    for entity in recover.ACTIVITY_ENTITIES:
        row = cursors.get(engine.changed_module(entity))
        state = row.state if row is not None else None
        for entry in recover.entries_of(state).values():
            kept_by_module[entry[0]] = kept_by_module.get(entry[0], 0) + 1
        if (state or {}).get(recover.OVERFLOW_KEY):
            overflow |= recover.overflow_modules_of(state)
    out: dict[str, int] = {}
    for module, n in dropped.items():
        if module in generic:
            left = n - kept_by_module.get(module, 0) if module in overflow else 0
        elif module in mapping.MIRRORED_MODULES:
            left = n
        else:
            left = 0
        if left > 0:
            out[module] = left
    return out


def _activities(
    cursors: dict[str, Any], recovered: dict[str, int], engine: Any, profile: Any = None,
) -> HealthActivitiesModel:
    """The ``activities`` block of Sync health (CRM-Q24 item 10). Numbers only."""
    from gateway.crm_sync import recover

    drops_row = cursors.get(engine.ACTIVITY_DROPS_ROW)
    parentless: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for entity, by_module in engine.activity_drop_maps(
        drops_row.state if drops_row is not None else None,
    ).items():
        for module, n in by_module.items():
            if module == engine.NO_PARENT_MODULE:
                parentless[entity] = parentless.get(entity, 0) + n
            else:
                dropped[module] = dropped.get(module, 0) + n
    waiting: dict[str, int] = {}
    for entity in recover.ACTIVITY_ENTITIES:
        row = cursors.get(engine.changed_module(entity))
        n = len(recover.entries_of(row.state if row is not None else None))
        if n:
            waiting[entity] = n
    return HealthActivitiesModel(
        parentless=parentless, dropped=dropped, waiting=waiting,
        recovered_7d={k: v for k, v in recovered.items() if v > 0},
        unkept=_unkept(dropped, cursors, profile, engine),
    )


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
    """The five keys in order, then any other key of the counts or the estimate.
    A key of :data:`NOT_ENTITY_KEYS` is no entity, so it gets no row."""
    extra = sorted((set(counts) | set(estimate)) - set(COUNT_KEYS) - NOT_ENTITY_KEYS)
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
    from gateway.crm_sync import engine, holds, profiles, runs

    org = _organization(user)
    ours: dict[str, int] = {}
    async with _tenant_session() as db:
        hold_entries = await holds.read(db, org)
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
        # CRM-Q6a: each generic module counts its rows in the generic store,
        # under the key the engine uses (``module:<ApiName>``).
        for row in (await db.execute(text(_MODULE_OURS_SQL), {
            "org": org, "src": engine.EXT_SOURCE,
        })).fetchall():
            ours[f"{MODULE_KEY_PREFIX}{row.module}"] = int(row.n or 0)
        body = profile.profile if profile is not None else None
        budget = engine.daily_budget(body)
        resumes = await runs.resumes_at(db, org, budget) if used >= budget else None
        # CRM-Q24: the cursors hold the short reads, the drops of the last
        # full reads and the kept activities. The runs hold the recoveries.
        cursors = await runs.read_cursors(db, org, f"{engine.PROVIDER}:")
        recovered = {
            str(r.module): int(r.n or 0)
            for r in (await db.execute(text(_RECOVERED_7D_SQL), {
                "org": org, "key": engine.ACTIVITY_RECOVERED_KEY,
            })).fetchall()
        }
        # CRM-Q32-4: the count of the open questions, only for an org on
        # CRM_QUESTIONS_ORGS, so a dark box sends no new statement.
        open_questions = await _open_questions(db, org)
    counts = latest.counts if latest is not None else {}
    estimate = (latest.estimate if latest is not None else None) or {}
    module_ours = {k: v for k, v in ours.items() if k.startswith(MODULE_KEY_PREFIX)}
    short = _short_by_keys(cursors, engine)
    entities = [
        HealthEntityModel(
            key=key,
            zoho_count=_int(estimate.get(key)),
            # A module with no row here has 0, not "no local table".
            ours=ours.get(key, 0 if key.startswith(MODULE_KEY_PREFIX) else None),
            last_run=_last_run_counts(counts.get(key)),
            short_by=short.get(key),
        )
        for key in _entity_keys(counts, {**module_ours, **short, **estimate})
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
        running=live,
        credits=HealthCreditsModel(budget_24h=budget, used_24h=used, resumes_at=resumes),
        holds=_holds_out(hold_entries),
        activities=_activities(cursors, recovered, engine, body),
        open_questions=open_questions,
    )


async def _open_questions(db: Any, org: str) -> int | None:
    """The open count for the health card (CRM-Q32-4), or ``None``: outside
    ``CRM_QUESTIONS_ORGS``, before the first questions step, or when the read
    fails. Fix round P2: it reads ONE cursor row, ``zoho:questions``, as My
    Day does (``seen`` of the last step), and builds no question. So a poll
    of the card costs one statement, and the card never fails on it."""
    from gateway.crm_sync import questions as qs
    from gateway.crm_sync import runs
    from gateway.routes.crm.flags import questions_allowed

    if not questions_allowed(org):
        return None
    try:
        # A savepoint, so a failed read leaves the session of the card whole.
        async with db.begin_nested():
            cursors = await runs.read_cursors(db, org, qs.QUESTIONS_ROW)
    except Exception as exc:  # the count is advice: the health card answers anyway
        get_logger("crm.sync").warning("crm.sync.open_questions_unread",
                                       error_type=type(exc).__name__)
        return None
    return qs.open_count(getattr(cursors.get(qs.QUESTIONS_ROW), "state", None))


@router.post(
    "/sync/zoho", response_model=StartedModel, status_code=202,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def sync_now(
    payload: SyncNowIn | None = None,
    user: UserContext = Depends(get_current_user),
) -> StartedModel:
    """Start a run now. It answers before the run ends.

    ``only`` (CRM-Q16) runs one count key on its own, also when it is held.
    An unknown key answers 422 and starts nothing.
    """
    from gateway.crm_sync import engine, profiles, scheduler

    org = _organization(user)
    only: frozenset[str] | None = None
    if payload is not None and payload.only:
        async with _tenant_session() as db:
            profile = await profiles.latest(db, org)
        if not _hold_key_known(payload.only, profile.profile if profile else None):
            raise HTTPException(status_code=422, detail="unknown_module")
        only = frozenset({payload.only})
    try:
        handle = await engine.acquire(org, only=only)
    except engine.NotConnected as exc:
        raise HTTPException(status_code=409, detail="not_connected") from exc
    except engine.SyncRunning as exc:
        raise HTTPException(status_code=409, detail="sync_running") from exc
    scheduler.spawn(handle)
    return StartedModel(started=True)


@router.put(
    "/sync/modules/{key}", response_model=HoldsModel,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def set_module_sync(
    key: str,
    payload: HoldIn,
    user: UserContext = Depends(get_current_user),
) -> HoldsModel:
    """Turn the scheduled sync of one count key on or off (CRM-Q16).

    ``sync: false`` holds the key. ``sync: true`` releases it, and the entry
    stays with ``held: false``, so the next import does not hold it again.
    It deletes nothing and starts no run.
    """
    from gateway.crm_sync import holds, profiles

    org = _organization(user)
    async with _tenant_session() as db:
        profile = await profiles.latest(db, org)
        if not _hold_key_known(key, profile.profile if profile else None):
            raise HTTPException(status_code=422, detail="unknown_module")
        written = await holds.write(
            db, org, key, held=not payload.sync, by=holds.BY_ADMIN,
            count=_int(((await holds.read(db, org)).get(key) or {}).get("count")),
        )
        if written is None:
            raise HTTPException(status_code=409, detail="no_settings")
    return HoldsModel(holds=_holds_out(written))
