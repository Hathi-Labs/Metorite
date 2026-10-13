"""The CRM mirror sync engine (WS-53 CRM-Z4 and CRM-Z5).

Spec: ``project-docs/specs/crm_platform.md`` §3.5, §4.3, §5.1, §5.2 and §5.4.

One run reads one organization's records from its provider and writes them to
the five mirror tables. **It reads only.** No call in this package writes to
the provider. The engine is generic: it calls the :class:`CrmSource` protocol
and the mapping of the provider (``crm_sources/registry.py``). It names Zoho
only in the constants below, and builds the adapter through the registry.

**One run.**

* ``import`` (the first run, and every run until each entity has had one full
  read): field definitions, then the records in dependency order (companies,
  contacts, leads, deals, then the notes, calls and meetings).
* ``incremental``: the records changed since the cursor of each entity, then
  the deleted list of each entity.
* ``reconcile``: an incremental run that also compares ids. A module that
  hides ``Modified_Time`` is read by offset, so its read is a full id list.
  Local rows whose id is missing from that list are deleted. It runs at most
  once in 24 hours for each such entity. It also runs when the last good run
  is older than 50 days, because the provider keeps a delete for 60 days only.

**Where a run touches the database.** One ``tenant_session(org)`` for each
step and each page, never one long transaction, and never a session open
across a provider call (§4.3). The cursor of an entity moves in the SAME
transaction as the page it covers, so a stopped run resumes with no gap and
no repeat. The key ``(organization_id, ext_source, ext_id)`` also makes a
replay safe. A SAVEPOINT wraps each record, so one bad record does not lose
the page. A bad record is counted. Its id and the class of its error are
logged. Its content is not.

**The lock.** ``crm_sync_runs`` holds one ``running`` row for each
organization (a partial unique index, migration 246). It works across gateway
workers. A run with a heartbeat older than 15 minutes is taken over.

**The credential.** Each provider call first asks
``connection.fresh_zoho_credential(org)`` for the shared token, which refreshes
under a row lock and marks ``needs_reconnect`` itself. A dead token stops the
run with the code ``needs_reconnect``. The engine never retries a dead token.

**The budget.** A run spends at most the credits left of 10 % of the daily
credits of the edition, counted over 24 hours (CRM-Q1). The edition comes from
the source profile (:func:`daily_budget`). With no profile, or an edition that
the provider does not name, the budget is the 500-credit floor of CRM-Z5
(:data:`DAILY_CREDIT_BUDGET`). A run stops with the code ``budget`` before a
call that would pass it. It also stops when the provider reports fewer than
:data:`CREDITS_FLOOR` credits left.

**Discovery** (CRM-Q1, :func:`_discover`). At the first import, and once in 24
hours after that, the run reads the shape of the customer's account. That is
the org, the modules, the layouts, the fields and the deal pipelines. It reads
metadata only and never a record. The result is the versioned source profile
(``profiles.py``). The same step writes the deal pipelines and their stages
through the mirror bypass (CRM-Q2) and the org domain into ``provider_meta``.

**Writes.** Every record write goes through ``core.upsert_from_source`` and
every delete through ``core.delete_from_source``. The stages of a pipeline go
through ``core.set_pipeline_stages_from_source``. Those three are the ONLY
bypass of the write guard, and ``tests/unit/test_crm_upsert_from_source_fence.py``
keeps them out of routes, agents and skills.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import httpx
from acb_common import get_logger
from acb_common.db import tenant_session
from fastapi import HTTPException
from sqlalchemy import text

from gateway.connections import store
from gateway.crm_sources.base import (
    BudgetExhausted,
    CrmSource,
    NeedsReconnect,
    RateLimited,
    SourceCredential,
    SourceCursor,
    SourceError,
    SourceRecord,
    SourceScopeError,
    UntrustedHost,
)
from gateway.crm_sources.registry import mapping_for, source_class
from gateway.crm_sync import profiles, runs
from gateway.crm_sync.runs import CursorRow, RunLost, SyncRunning
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import flags
from gateway.routes.crm.core import (
    compute_lead_name,
    delete_from_source,
    delete_module_records_from_source,
    savepoint,
    set_deal_contacts_from_source,
    set_pipeline_stages_from_source,
    upsert_from_source,
    upsert_module_records_from_source,
)
from gateway.routes.crm.import_zoho import guess_status_type

_log = get_logger("crm.sync")

T = TypeVar("T")

PROVIDER = "zoho"
OWNING_APP = "crm"
#: The ``ext_source`` of every row this provider writes.
EXT_SOURCE = "zoho"

#: Dependency order: a parent is read before its children.
ENTITY_ORDER: tuple[str, ...] = (
    "company", "contact", "lead", "deal", "note", "call", "meeting", "task",
)

#: An entity the run may skip when the connection lacks its scope (CRM-Q5). A
#: connection made before the scope existed must reconnect to grant it. Until
#: then the run reports the module as unavailable and goes on. A missing scope
#: is not a dead token, so it never marks the connection ``needs_reconnect``.
#: Each generic module (``module:<ApiName>``, CRM-Q6a) follows the same rule
#: (:func:`_is_optional`).
OPTIONAL_ENTITIES: frozenset[str] = frozenset({"task"})
#: An unavailable module is asked again after this long.
UNAVAILABLE_RETRY = timedelta(hours=1)

#: The entities that have field definitions.
FIELD_DEF_ENTITIES: tuple[str, ...] = ("company", "contact", "lead", "deal")

#: The API name of each entity in counts, estimate and ``GET /crm/sync/status``.
SLUGS: dict[str, str] = {
    "company": "organizations",
    "contact": "contacts",
    "lead": "leads",
    "deal": "deals",
    "note": "activities",
    "call": "activities",
    "meeting": "activities",
    "task": "activities",
}
SLUG_ORDER: tuple[str, ...] = ("organizations", "contacts", "leads", "deals", "activities")

#: The ``type`` of a ``crm_activities`` row for an activity entity.
ACTIVITY_TYPES = {"note": "note", "call": "call", "meeting": "meeting", "task": "task"}

#: The steps that report under a key of their own in ``crm_sync_runs.counts``
#: (CRM-Q5), so the health panel can show each one. ``unlinked`` is the number
#: of Zoho ids that still have no local row. ``skipped`` is the number of
#: records or deals the step left alone. ``unavailable`` is 1 when the module
#: could not be read (a missing scope).
STEP_KEYS: tuple[str, ...] = ("converted_leads", "tasks", "deal_contacts")
STEP_COUNTS: tuple[str, ...] = (
    "seen", "upserted", "deleted", "unlinked", "skipped", "unavailable",
)

#: The cursor row that queues the deals whose contact roles are still unread.
ROLES_MODULE = f"{PROVIDER}:deal_roles"
#: A role read costs one credit for each deal. The step stops while this many
#: credits remain, so it never starves the record passes of the next run.
ROLES_RESERVE = 20
ROLES_PENDING_CAP = 20_000
#: A deal whose roles fail to read this many times is dropped from the queue.
ROLES_MAX_ATTEMPTS = 3

#: The generic record store (CRM-Q6a). Each module reads after the known
#: entities and the CRM-Q5 steps, and the step stops while this many credits
#: remain, as the roles step does. The next run goes on from the cursor.
MODULES_RESERVE = 20
#: The count keys of a generic module, under ``module:<ApiName>``.
MODULE_COUNTS: tuple[str, ...] = (
    "seen", "upserted", "deleted", "failed", "skipped", "unavailable",
)
#: The cursor row that keeps the provider count of each generic module, for
#: the estimate. A count is read again after :data:`DISCOVERY_EVERY`.
MODULE_COUNTS_ROW = f"{PROVIDER}:module_counts"
MODULE_TABLE = "crm_module_records"
#: After its first import, a generic module reads its changed list at most
#: this often, the cadence of the deleted lists. A module with no change
#: still costs one credit for each read, and ten idle modules on every run
#: would double the cost of a run and slow the Leads and Deals.
MODULE_READ_EVERY = timedelta(hours=1)

LEAD_STATUSES = "crm_lead_statuses"
DEAL_STATUSES = "crm_deal_statuses"
#: The status of a record that names none. ``status_id`` is NOT NULL.
UNKNOWN_STATUS = "Unknown"

#: ``created_by`` of an activity whose owner is not a member.
SYNC_ACTOR = "crm:zoho-sync"

#: The time between two runs of one organization.
INTERVAL_SECONDS = 10 * 60

#: The edition of the customer is not known, so assume the Free edition: 5,000
#: credits a day (§5.2 item 6). The sync may spend 10 % of that in 24 hours.
#: Zoho sends ``X-API-CREDITS-REMAINING`` only after half of the day is spent,
#: so that header alone cannot protect a small organization.
ASSUMED_DAILY_CREDITS = 5_000
BUDGET_FRACTION = 0.10
DAILY_CREDIT_BUDGET = int(ASSUMED_DAILY_CREDITS * BUDGET_FRACTION)

#: Stop when the provider reports fewer credits than this.
CREDITS_FLOOR = 1_000

#: CRM-Q10: the credits that discovery may spend when the org has no profile
#: and the run uses the default budget, also when that budget is spent. One
#: discovery costs 2 + 2 x 4 calls plus one for each Deals layout, so 11 with
#: one layout (``test_crm_discovery.py``). 25 covers 15 Deals layouts, or 14
#: retries. Without it, an org whose profile was dropped after it spent the
#: 500-credit floor could not discover, and the mirror froze for up to 24 hours.
DISCOVERY_ALLOWANCE = 25

RECONCILE_EVERY = timedelta(hours=24)
#: The source profile is read again after this long (CRM-Q1).
DISCOVERY_EVERY = timedelta(hours=24)
#: The deleted lists cost 2 credits each, so an idle run reads them hourly.
DELETED_EVERY = timedelta(hours=1)
#: The longest wait that the spend of a run can add between two runs.
MAX_INTERVAL_SECONDS = 6 * 3600
#: A record that fails to write is read again for this many runs.
RETRY_LIMIT = 5
FAILED_CAP = 200
#: A reconcile refuses to delete more than this share of the local rows, once
#: an organization holds more than ``REFUSE_MIN_ROWS`` of them.
REFUSE_FRACTION = 0.2
REFUSE_MIN_ROWS = 10


def daily_budget(profile: dict[str, Any] | None) -> int:
    """The credits a sync may spend in 24 hours (CRM-Q1).

    It is 10 % of the daily credits of the edition in the source profile. With
    no profile, or an edition that is not known, it is
    :data:`DAILY_CREDIT_BUDGET`, the 500-credit floor of CRM-Z5.
    """
    return profiles.sync_budget(profile) or DAILY_CREDIT_BUDGET


def next_interval(credits_used: int | None, budget: int | None = None) -> int:
    """Seconds between two runs. A run that spent credits spaces the next one,
    so the average spend stays inside the daily budget. ``budget`` is
    :func:`daily_budget`. With none given it is :data:`DAILY_CREDIT_BUDGET`."""
    spent = int(credits_used or 0)
    spaced = -(-spent * 86_400 // max(int(budget or DAILY_CREDIT_BUDGET), 1))
    return min(max(INTERVAL_SECONDS, spaced), MAX_INTERVAL_SECONDS)
#: The provider keeps a delete for 60 days. Past 50 days without a good run,
#: only a full id compare finds the deletes.
GAP_RECONCILE = timedelta(days=50)
#: A deleted list reads from this long before the start of the pass, so a
#: delete near the edge is read twice and never missed. A repeat is harmless.
DELETED_MARGIN = timedelta(minutes=10)

#: A test sets this to put a fake transport under every provider call.
TRANSPORT: httpx.AsyncBaseTransport | None = None

#: The mode, read by each page with a share lock (CRM-X0). ``POST /crm/reset``
#: holds the row ``FOR UPDATE`` while it deletes, so a page never writes a
#: record that the start over does not see.
MODE_SHARE_SQL = (
    "SELECT mode FROM crm_settings "
    "WHERE organization_id = CAST(:org AS uuid) FOR SHARE"
)


class NotConnected(Exception):
    """The organization has no live connection to the provider."""


class ModeChanged(Exception):
    """The organization is no longer in Mirror mode, or the switch went off."""


class Stopped(Exception):
    """The caller asked the run to stop (a shutdown)."""


SourceFactory = Callable[[SourceCredential, int | None], CrmSource]


def _make_source(credential: SourceCredential, budget: int | None) -> CrmSource:
    """The adapter for the provider, built through the registry."""
    from gateway.routes.crm.connection import zoho_oauth_config

    return source_class(PROVIDER)(
        zoho_oauth_config(), credential, transport=TRANSPORT, credit_budget=budget,
    )


#: Replaced by a test. Production uses :func:`_make_source`.
source_factory: SourceFactory = _make_source


async def _fresh_credential(org: str) -> SourceCredential | None:
    from gateway.routes.crm.connection import fresh_zoho_credential

    return await fresh_zoho_credential(org, transport=TRANSPORT)


def _now() -> datetime:
    return datetime.now(UTC)


def changed_module(entity: str) -> str:
    return f"{PROVIDER}:{entity}"


def deleted_module(entity: str) -> str:
    return f"{PROVIDER}:{entity}:deleted"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _table(entity: str) -> str:
    return mapping_for(PROVIDER).ENTITY_TABLES[entity]


# ── Cursor state ────────────────────────────────────────────────────────────


def cursor_state(cursor: SourceCursor, **extra: Any) -> dict[str, Any]:
    """A read that stops, as JSON. :func:`cursor_from` reads it back."""
    return {
        "in_progress": True,
        "since": _iso(cursor.since),
        "page": cursor.page,
        "page_token": cursor.page_token,
        "seen_ids": list(cursor.seen_ids),
        **extra,
    }


def cursor_from(state: dict[str, Any]) -> SourceCursor:
    return SourceCursor(
        since=_parse(state.get("since")),
        page=int(state.get("page") or 1),
        page_token=state.get("page_token") or None,
        seen_ids=tuple(str(i) for i in state.get("seen_ids") or ()),
    )


# ── The plan ────────────────────────────────────────────────────────────────


def plan(
    cursors: dict[str, CursorRow],
    now: datetime,
    last_ok: datetime | None,
) -> tuple[str, frozenset[str]]:
    """The kind of the next run, and the entities that need a full id compare."""
    for entity in ENTITY_ORDER:
        row = cursors.get(changed_module(entity))
        if entity in OPTIONAL_ENTITIES:
            # An optional module that never read must not hold the plan at
            # import for ever (CRM-Q5). The incremental run reads it.
            continue
        if row is None or row.initial_done_at is None:
            return runs.KIND_IMPORT, frozenset()
    due: set[str] = set()
    for entity in ENTITY_ORDER:
        row = cursors.get(changed_module(entity))
        if row is None:
            continue
        state = row.state or {}
        if state.get("in_progress") and state.get("walk_start"):
            due.add(entity)  # a walk that stopped resumes first
        elif state.get("offset_paged") and (
            row.last_reconciled_at is None or now - row.last_reconciled_at >= RECONCILE_EVERY
        ):
            due.add(entity)
    if last_ok is not None and now - last_ok >= GAP_RECONCILE:
        due = set(ENTITY_ORDER)
    if due:
        return runs.KIND_RECONCILE, frozenset(due)
    return runs.KIND_INCREMENTAL, frozenset()


# ── The safe code of a failure ──────────────────────────────────────────────


def error_code(exc: BaseException) -> str:
    """A short code for ``crm_sync_runs.error``. Never free text from the provider."""
    if isinstance(exc, NeedsReconnect):
        return "needs_reconnect"
    if isinstance(exc, BudgetExhausted):
        return "budget"
    if isinstance(exc, RateLimited):
        return "rate_limited"
    if isinstance(exc, UntrustedHost):
        return "untrusted_host"
    if isinstance(exc, SourceError):
        return store.safe_error_code(exc.code) if exc.code else "source_error"
    if isinstance(exc, httpx.HTTPError | TimeoutError):
        return "transport"
    if isinstance(exc, HTTPException):
        return "not_configured"
    if isinstance(exc, ModeChanged):
        return "mode_changed"
    if isinstance(exc, Stopped):
        return "shutdown"
    return "internal"


def _fatal(exc: BaseException) -> bool:
    return isinstance(exc, NeedsReconnect | BudgetExhausted | RateLimited)


def _module(entity: str) -> str | None:
    """The API name of a generic module entity (``module:<ApiName>``), or None."""
    return mapping_for(PROVIDER).module_of(entity)


def _is_optional(entity: str) -> bool:
    """An optional module: Tasks (CRM-Q5), or any generic module (CRM-Q6a)."""
    return entity in OPTIONAL_ENTITIES or _module(entity) is not None


def _skip_optional(entity: str, exc: BaseException) -> bool:
    """True when an optional module's read failure means "not available here".

    A plain HTTP 401 is a token that went stale mid-run, not a missing module,
    so it fails the run like any other module would. A 401 that names a scope
    arrives as ``SourceScopeError`` and is still skipped.
    """
    if not _is_optional(entity) or _fatal(exc) or isinstance(exc, UntrustedHost):
        return False
    return isinstance(exc, SourceScopeError) or "HTTP 401" not in str(exc)


# ── The run ─────────────────────────────────────────────────────────────────


@dataclass
class RunHandle:
    """A run that holds the lock and has not started to read."""

    org: str
    run_id: str
    kind: str
    connection_id: str
    reconcile: frozenset[str]
    cursors: dict[str, CursorRow]
    counts: dict[str, dict[str, int]]
    estimate: dict[str, int] | None
    #: The scopes of the connection. They decide which generic module the
    #: profile mirrors (CRM-Q6a).
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunResult:
    run_id: str
    kind: str
    status: str
    error: str | None
    counts: dict[str, dict[str, int]]
    credits_used: int


def _blank_counts() -> dict[str, dict[str, int]]:
    return {
        slug: {
            "seen": 0, "upserted": 0, "deleted": 0, "unlinked": 0, "failed": 0,
            "refused": 0,
        }
        for slug in SLUG_ORDER
    } | {key: dict.fromkeys(STEP_COUNTS, 0) for key in STEP_KEYS}


def _merge_counts(saved: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts = _blank_counts()
    for slug, values in (saved or {}).items():
        if _module(slug) and isinstance(values, dict):
            counts[slug] = dict.fromkeys(MODULE_COUNTS, 0)
        if slug in counts and isinstance(values, dict):
            for key in counts[slug]:
                counts[slug][key] = int(values.get(key) or 0)
    return counts


def _enabled(org: str) -> bool:
    """The kill switch, ``CRM_MIRROR`` and the org lists, read in one place."""
    return flags.mirror_sync_enabled() and flags.crm_mirror_allowed(org)


async def acquire(org: str) -> RunHandle:
    """Take the run lock for ``org`` and plan the run.

    Raises :class:`NotConnected` with no live connection and
    :class:`SyncRunning` when a run holds the lock. It makes no provider call.
    """
    now = _now()
    async with tenant_session(org) as db:
        connection = await store.get(db, org, PROVIDER, OWNING_APP)
        if connection is None or connection.status != store.STATUS_CONNECTED:
            raise NotConnected(connection.status if connection else "none")
        if not _enabled(org):
            raise NotConnected("disabled")
        settings = await crm_access.read_settings(db, org)
        if getattr(settings, "mode", None) != "mirror":
            raise NotConnected("mode")
        cursors = await runs.read_cursors(db, org, f"{PROVIDER}:")
        last_ok = await runs.last_success_at(db, org)
        kind, reconcile = plan(cursors, now, last_ok)
        saved_counts: dict[str, Any] = {}
        saved_estimate: dict[str, Any] | None = None
        if kind == runs.KIND_IMPORT:
            saved_counts, saved_estimate = await runs.resumable_import(db, org)
        counts = _merge_counts(saved_counts)
        run_id = await runs.start(
            db, org, connection.id, kind, counts=counts, estimate=saved_estimate,
        )
    return RunHandle(
        org=org, run_id=run_id, kind=kind, connection_id=connection.id,
        reconcile=reconcile, cursors=cursors, counts=counts, estimate=saved_estimate,
        scopes=tuple(connection.scopes or ()),
    )


class _Ctx:
    """The state of one run."""

    def __init__(
        self,
        handle: RunHandle,
        *,
        should_stop: Callable[[], bool],
        credit_budget: int | None,
    ) -> None:
        self.handle = handle
        self.org = handle.org
        self.kind = handle.kind
        self.cursors = dict(handle.cursors)
        self.reconcile = handle.reconcile
        self.counts = handle.counts
        self.estimate = handle.estimate
        self.should_stop = should_stop
        self.mapping = mapping_for(PROVIDER)
        self.started = _now()
        self.credits = 0
        self.credits_left: int | None = None
        self.budget_total: int | None = credit_budget
        self.members: frozenset[str] = frozenset()
        self.owners: dict[str, str] | None = None
        #: The start of a full id walk. A written row is stamped with it.
        self.stamp: datetime | None = None
        #: The newest source profile, and the credits of the last 24 hours that
        #: other runs spent. The budget is the edition's share minus that.
        self.profile: profiles.ProfileRow | None = None
        self.used_24h = 0
        self.budget_is_default = credit_budget is None
        #: Discovery reads at most once in a run, also when it failed (CRM-Q10).
        self.discovery_tried = False
        #: CRM-Q10: the early discovery of the allowance stored the profile but
        #: left its pipelines to the normal slot, after the Stage picklist.
        self.pipelines_deferred = False
        #: The local pipelines of the org, loaded once for the deal writes.
        self.pipelines: _PipelineIndex | None = None
        #: The deals whose contact roles are not read yet, oldest first.
        self.roles_pending: list[str] | None = None
        #: Failed reads for each queued deal, and whether the queue was seeded.
        self.roles_attempts: dict[str, int] = {}
        self.roles_seeded = False
        #: The generic modules (CRM-Q6a): API name -> its profile row, and the
        #: ``provider_meta`` of the connection, for the deep link.
        self.modules: dict[str, dict[str, Any]] = {}
        self.link_meta: dict[str, Any] = {}

    # ── Provider calls ──────────────────────────────────────────────────────

    def _budget_left(self) -> int | None:
        if self.budget_total is None:
            return None
        return max(self.budget_total - self.credits, 0)

    async def call(
        self, op: Callable[[CrmSource], Awaitable[T]], *, floor: bool = True,
    ) -> T:
        """One provider call, with a fresh shared credential and the budget.

        No database session is open while the call runs.
        """
        if self.should_stop():
            raise Stopped()
        if not _enabled(self.org):
            raise ModeChanged()
        if floor and self.credits_left is not None and self.credits_left <= CREDITS_FLOOR:
            # Checked BEFORE the next call, so the page already paid for is kept.
            raise BudgetExhausted(
                "The provider reports few credits left",
                used=self.credits, budget=self.budget_total or 0,
            )
        left = self._budget_left()
        if left is not None and left <= 0:
            raise BudgetExhausted(
                "The credit budget of this run is spent",
                used=self.credits, budget=self.budget_total or 0,
            )
        credential = await _fresh_credential(self.org)
        if credential is None:
            raise NotConnected("disconnected")
        source = source_factory(credential, left)
        try:
            return await op(source)
        finally:
            self.credits += source.credits_used
            reported = await source.credits_remaining()
            if reported is not None:
                self.credits_left = reported

    # ── Pages ───────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Any]:
        """One ``tenant_session`` for one page. It writes the progress, and the
        heartbeat, in the same transaction as the page.

        On a failure the in-memory counts return to what the last commit holds.

        The page reads the mode ``FOR SHARE`` (CRM-X0). The start over takes the
        same row ``FOR UPDATE``, so it waits until this page commits, and a page
        that starts after it reads the new mode and stops here.
        """
        snapshot = copy.deepcopy(self.counts)
        try:
            async with tenant_session(self.org) as db:
                mode = (await db.execute(
                    text(MODE_SHARE_SQL), {"org": self.org},
                )).scalar()
                if mode != "mirror":
                    raise ModeChanged()
                yield db
                await runs.progress(
                    db, self.org, self.handle.run_id, self.counts, self.estimate,
                    self.credits,
                )
        except BaseException:
            self.counts = snapshot
            raise

    def bump(self, entity: str, name: str, amount: int = 1) -> None:
        if _module(entity):
            self.module_step(entity, name, amount)
            return
        self.counts[SLUGS[entity]][name] += amount
        if entity == "task":
            self.step("tasks", name, amount)

    def module_step(self, entity: str, name: str, amount: int = 1) -> None:
        """Count under ``module:<ApiName>`` (CRM-Q6a). A name that a module does
        not keep (``unlinked``, ``refused``) is ignored."""
        counts = self.counts.setdefault(entity, dict.fromkeys(MODULE_COUNTS, 0))
        if name in counts:
            counts[name] += amount

    def step(self, key: str, name: str, amount: int = 1) -> None:
        """Count under the key of a step. A name that the step does not keep
        (``failed``, ``refused``) is ignored."""
        if name in self.counts[key]:
            self.counts[key][name] += amount

    # ── Lookups ─────────────────────────────────────────────────────────────

    async def load_owners(self) -> dict[str, str]:
        if self.owners is None:
            owners: dict[str, str] = {}
            try:
                # One credit, and it must not drop the page that waits for it.
                users = await self.call(lambda s: s.list_users(), floor=False)
            except (NotConnected, Stopped):
                raise
            except SourceError as exc:
                if _fatal(exc):
                    raise
                _log.warning("crm.sync.users_unreadable: %s", error_code(exc))
            else:
                for user in users:
                    email = user.fields.get("email")
                    if isinstance(email, str) and email.strip():
                        owners[user.ext_id] = email.strip().lower()
            self.owners = owners
        return self.owners


# ── Writing one record ──────────────────────────────────────────────────────

_ACTIVITY_TARGETS = ("lead_id", "deal_id", "contact_id", "company_id")

_LOCAL_ID_SQL = (
    "SELECT id FROM {table} WHERE organization_id = CAST(:org AS uuid) "
    "AND ext_source = :src AND ext_id = :ext"
)

_DEAL_CONTACT_SQL = (
    "INSERT INTO crm_deal_contacts (deal_id, contact_id, is_primary, ext_source) "
    "SELECT CAST(:deal_id AS uuid), CAST(:contact_id AS uuid), "
    "       NOT EXISTS (SELECT 1 FROM crm_deal_contacts "
    "                    WHERE deal_id = CAST(:deal_id AS uuid) AND is_primary), "
    "       'zoho' "
    "ON CONFLICT DO NOTHING"
)

#: The key of ``custom`` that keeps the Zoho ids of a converted lead.
CONVERTED_KEY = "_converted"
#: The canonical entity of each Zoho id -> the column of ``crm_leads`` it fills.
_CONVERSION_COLUMNS = {
    "contact": "converted_contact_id",
    "company": "converted_organization_id",
    "deal": "converted_deal_id",
}

_BUMP_SQL = (
    "UPDATE {table} SET last_activity_at = :at WHERE id = CAST(:id AS uuid) "
    "AND (last_activity_at IS NULL OR last_activity_at < :at)"
)


async def _local_id(
    ctx: _Ctx, db: Any, entity: str, ext_id: str, cache: dict[tuple[str, str], str | None],
) -> str | None:
    key = (entity, ext_id)
    if key not in cache:
        row = (await db.execute(
            text(_LOCAL_ID_SQL.format(table=_table(entity))),
            {"org": ctx.org, "src": EXT_SOURCE, "ext": ext_id},
        )).first()
        cache[key] = str(row.id) if row is not None else None
    return cache[key]


# The stage tables are written with raw SQL here and never through
# ``core.insert_row``, which refuses a write in Mirror mode (CRM-Z6). The key
# is ``(organization_id, name)``: one stage name is one stage (§5.2 row 11).
# ``table`` is one of two constants of this module, never caller input.
_STATUS_INSERT_SQL = (
    "INSERT INTO {table} (name, position, type{prob_col}) VALUES (:name, "
    "COALESCE((SELECT max(position) + 1 FROM {table}), 0), :type{prob_val}) "
    "ON CONFLICT (organization_id, name) DO NOTHING"
)
_STATUS_ID_SQL = "SELECT id FROM {table} WHERE name = :name"


_FORECAST_SQL = (
    "UPDATE crm_deal_statuses SET forecast_category = :category "
    "WHERE id = CAST(:id AS uuid) AND forecast_category IS DISTINCT FROM :category"
)

#: A pipeline stage that names ``won`` or ``lost`` gives its row that type, also
#: a row that exists already. The insert above never changes an existing row,
#: so without this a stage first seen as ``open`` stayed open for good.
_STAGE_TYPE_SQL = (
    "UPDATE crm_deal_statuses SET type = :type "
    "WHERE organization_id = CAST(:org AS uuid) AND name = :name "
    "AND type IS DISTINCT FROM :type"
)
#: The stage types a pipeline can state. A stage with no type keeps its row.
STAGE_TYPES = ("won", "lost")


def stage_types(pipelines: list[dict[str, Any]]) -> dict[str, str]:
    """Stage name → ``won`` or ``lost``, from the pipelines of a profile.

    A stage with no type is absent. When two pipelines give one stage two
    types, the first pipeline in the profile decides.
    """
    found: dict[str, str] = {}
    for pipeline in pipelines:
        if not isinstance(pipeline, dict):
            continue
        for stage in pipeline.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            name, kind = stage.get("name"), stage.get("type")
            if isinstance(name, str) and name and kind in STAGE_TYPES:
                found.setdefault(name, kind)
    return found


async def _apply_stage_types(db: Any, org: str, pipelines: list[dict[str, Any]]) -> None:
    """Give each mirrored stage the type its pipeline names (won or lost)."""
    for name, kind in stage_types(pipelines).items():
        await db.execute(text(_STAGE_TYPE_SQL), {"org": org, "name": name, "type": kind})


async def _ensure_status(
    db: Any, table: str, name: str, *, type_: str | None = None,
    position: int | None = None, probability: int | None = None,
    forecast_category: str | None = None,
) -> str:
    """The id of the status called ``name``. A new one is appended.

    ``forecast_category`` (deal stages only) is kept when the provider names one.
    """
    deal = table == DEAL_STATUSES
    sql = _STATUS_INSERT_SQL.format(
        table=table, prob_col=", probability" if deal else "",
        prob_val=", :probability" if deal else "",
    )
    params: dict[str, Any] = {"name": name, "type": type_ or guess_status_type(name)}
    if deal:
        params["probability"] = probability or 0
    if position is not None:
        sql = sql.replace(
            "COALESCE((SELECT max(position) + 1 FROM " + table + "), 0)", ":position",
        )
        params["position"] = position
    await db.execute(text(sql), params)
    row = (await db.execute(text(_STATUS_ID_SQL.format(table=table)), {"name": name})).first()
    if deal and forecast_category:
        await db.execute(text(_FORECAST_SQL), {"id": str(row.id), "category": forecast_category})
    return str(row.id)


async def _status_id(
    db: Any, table: str, name: str | None, cache: dict[tuple[str, str], str],
) -> str:
    wanted = name if name and name != "-None-" else UNKNOWN_STATUS
    key = (table, wanted)
    if key not in cache:
        cache[key] = await _ensure_status(db, table, wanted)
    return cache[key]


_STATUS_FIELDS = {"lead": ("Lead_Status", LEAD_STATUSES), "deal": ("Stage", DEAL_STATUSES)}


def _picklist_type(item: dict[str, Any], name: str) -> str:
    forecast = item.get("forecast_category")
    label = str(forecast.get("name") if isinstance(forecast, dict) else forecast or "").lower()
    if "won" in label:
        return "won"
    if "lost" in label:
        return "lost"
    return guess_status_type(name)


async def _mirror_statuses(
    ctx: _Ctx, db: Any, entity: str, definitions: list[dict[str, Any]],
) -> None:
    """Mirror the Stage / Lead_Status picklist into the org's status table."""
    api_name, table = _STATUS_FIELDS[entity]
    for definition in definitions:
        if definition.get("api_name") != api_name:
            continue
        items = [i for i in definition.get("pick_list_values") or [] if isinstance(i, dict)]
        items.sort(key=lambda i: int(i.get("sequence_number") or 0))
        for position, item in enumerate(items):
            name = str(item.get("actual_value") or item.get("display_value") or "").strip()
            if not name or name == "-None-":
                continue
            raw_prob = item.get("probability")
            prob = raw_prob if isinstance(raw_prob, int) and 0 <= raw_prob <= 100 else None
            async with savepoint(db):
                await _ensure_status(
                    db, table, name, type_=_picklist_type(item, name),
                    position=position, probability=prob,
                    forecast_category=ctx.mapping.forecast_category_of(item),
                )


@dataclass
class _PipelineIndex:
    """The local pipelines, so a deal finds its own by id, by name, or the default."""

    by_ext: dict[str, str] = field(default_factory=dict)
    by_name: dict[str, str] = field(default_factory=dict)
    default: str | None = None


_PIPELINE_INDEX_SQL = (
    "SELECT id, name, ext_id, is_default FROM crm_pipelines "
    "WHERE ext_source = :src ORDER BY position, created_at, id"
)


async def _pipeline_index(ctx: _Ctx, db: Any) -> _PipelineIndex:
    if ctx.pipelines is None:
        index = _PipelineIndex()
        for row in (await db.execute(text(_PIPELINE_INDEX_SQL), {"src": EXT_SOURCE})).fetchall():
            index.by_ext[str(row.ext_id)] = str(row.id)
            index.by_name.setdefault(str(row.name).strip().lower(), str(row.id))
            if row.is_default or index.default is None:
                index.default = str(row.id)
        ctx.pipelines = index
    return ctx.pipelines


async def _deal_pipeline_id(
    ctx: _Ctx, db: Any, ref: tuple[str | None, str | None] | None,
) -> str | None:
    """The local pipeline of a deal: by the id Zoho sent, else by its name, else
    the default pipeline. ``None`` when the org has no pipeline yet."""
    index = await _pipeline_index(ctx, db)
    ext_id, name = ref or (None, None)
    if ext_id and ext_id in index.by_ext:
        return index.by_ext[ext_id]
    if name and name.strip().lower() in index.by_name:
        return index.by_name[name.strip().lower()]
    # A deal that names a pipeline we do not know stays unlinked. Only a deal
    # that names none joins the default pipeline.
    return None if (ext_id or name) else index.default


@dataclass
class _Caches:
    links: dict[tuple[str, str], str | None] = field(default_factory=dict)
    statuses: dict[tuple[str, str], str] = field(default_factory=dict)


async def _write_record(  # noqa: C901 - one record, one linear mapping to columns
    ctx: _Ctx, db: Any, entity: str, record: SourceRecord, caches: _Caches,
) -> bool:
    """Map and write one record. False when the record has no parent here."""
    mapped = ctx.mapping.map_record(
        entity, record.fields, modified_at=record.modified_at, owners=ctx.owners or {},
    )
    values = dict(mapped.values)
    custom = dict(values.get("custom") or {})
    values["ext_source"] = EXT_SOURCE
    values["ext_id"] = record.ext_id
    if ctx.stamp is not None:
        values["ext_seen_at"] = ctx.stamp

    member = mapped.owner_email if mapped.owner_email in ctx.members else None
    if member is None and mapped.owner_name:
        custom[ctx.mapping.OWNER_NAME_KEY] = mapped.owner_name
    values["custom"] = custom
    if mapped.table == "crm_activities":
        values["created_by"] = member or SYNC_ACTOR
    else:
        values["owner_email"] = member

    unlinked = 0
    for link in mapped.links:
        for column, _kind in link.candidates:
            values.setdefault(column, None)
        for column, kind in link.candidates:
            local = await _local_id(ctx, db, kind, link.ext_id, caches.links)
            if local:
                values[column] = local
                break
        else:
            unlinked += 1
    if entity in ("contact", "deal"):
        values.setdefault("company_id", None)
    if mapped.table == "crm_activities":
        for column in _ACTIVITY_TARGETS:
            values.setdefault(column, None)
        if not any(values[c] for c in _ACTIVITY_TARGETS):
            ctx.bump(entity, "unlinked", max(unlinked, 1))
            if entity == "task":
                ctx.step("tasks", "skipped")
            return False

    if entity in ("lead", "deal"):
        values["status_id"] = await _status_id(
            db, LEAD_STATUSES if entity == "lead" else DEAL_STATUSES,
            mapped.status_name, caches.statuses,
        )
    if entity == "deal":
        values["pipeline_id"] = await _deal_pipeline_id(ctx, db, mapped.pipeline)
    if "lead_name" in mapped.derived:
        values["lead_name"] = compute_lead_name(values)
    if mapped.conversion is not None:
        # The Zoho ids stay in ``custom``, so a later pass resolves the ones
        # that were not written yet (a deal is read after its lead).
        values["custom"] = {**values["custom"], CONVERTED_KEY: mapped.conversion}
        for key, column in _CONVERSION_COLUMNS.items():
            ext = mapped.conversion.get(key)
            values[column] = (
                await _local_id(ctx, db, key, ext, caches.links) if ext else None
            )

    row = await upsert_from_source(db, mapped.table, values)
    if mapped.conversion is not None:
        ctx.step("converted_leads", "seen")
        ctx.step("converted_leads", "upserted")
        if not mapped.conversion:
            ctx.step("converted_leads", "skipped")

    if mapped.deal_contact is not None:
        contact = await _local_id(
            ctx, db, "contact", mapped.deal_contact.ext_id, caches.links,
        )
        if contact:
            await db.execute(
                text(_DEAL_CONTACT_SQL), {"deal_id": str(row.id), "contact_id": contact},
            )
        else:
            unlinked += 1
    if mapped.table == "crm_activities" and entity != "task":
        when = values.get("occurred_at") or record.modified_at
        if when is not None:
            for column, table in (
                ("lead_id", "crm_leads"), ("deal_id", "crm_deals"),
                ("contact_id", "crm_contacts"), ("company_id", "crm_companies"),
            ):
                if values.get(column):
                    await db.execute(
                        text(_BUMP_SQL.format(table=table)),
                        {"id": values[column], "at": when},
                    )
    if unlinked:
        ctx.bump(entity, "unlinked", unlinked)
    return True


async def _write_module_record(
    ctx: _Ctx, db: Any, entity: str, record: SourceRecord, caches: _Caches,
) -> bool:
    """Map and write one record of a generic module (CRM-Q6a).

    ``name`` is the value of the module's display field. The owner is kept
    only when it is a member, as for a known record. A lookup stays in
    ``fields`` as the provider sends it. The write goes through the module
    door, which sets no ``zoho_id``. False for a replay that changed nothing.
    """
    api_name = _module(entity) or ""
    info = ctx.modules.get(api_name) or {}
    values = ctx.mapping.map_module_record(
        api_name, record.fields, modified_at=record.modified_at,
        owners=ctx.owners or {}, display_field=info.get("display_field"),
    )
    if values.get("owner_email") not in ctx.members:
        values["owner_email"] = None
    values["ext_url"] = ctx.mapping.module_record_link(
        ctx.link_meta, info.get("tab"), record.ext_id,
    )
    return await upsert_module_records_from_source(
        db, EXT_SOURCE, api_name, record.ext_id, values,
    )


async def _apply_records(
    ctx: _Ctx, db: Any, entity: str, records: tuple[SourceRecord, ...],
) -> tuple[set[str], list[tuple[str, str | None]]]:
    """Write the records. Returns the ids that landed, and the ids that failed
    with their ``Modified_Time`` (for the retry)."""
    caches = _Caches()
    landed: set[str] = set()
    failed: list[tuple[str, str | None]] = []
    write = _write_module_record if _module(entity) else _write_record
    for record in records:
        ctx.bump(entity, "seen")
        try:
            async with savepoint(db):
                written = await write(ctx, db, entity, record, caches)
        except Exception as exc:  # one bad record must not lose the page
            ctx.bump(entity, "failed")
            # The id and the class of the error only. A database error text
            # can quote a value, and a value is the customer's data.
            _log.warning(
                "crm.sync.record_failed entity=%s ext_id=%s error=%s",
                entity, record.ext_id, type(exc).__name__,
            )
            stamp = record.fields.get("Modified_Time")
            failed.append((record.ext_id, stamp if isinstance(stamp, str) else None))
            continue
        landed.add(record.ext_id)
        if written:
            ctx.bump(entity, "upserted")
    return landed, failed


# ── The passes ──────────────────────────────────────────────────────────────


def _row(ctx: _Ctx, module: str) -> CursorRow:
    return ctx.cursors.get(module) or CursorRow(module, None, None, None, None)


async def _save_cursor(ctx: _Ctx, db: Any, row: CursorRow, status: str) -> None:
    await runs.write_cursor(db, ctx.org, row, status)


def _later(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return right if right > left else left


async def _safe_count(ctx: _Ctx, entity: str) -> int | None:
    """The provider count of an entity, or None. A failure other than a stop is
    not an answer, so the reconcile then refuses to delete (``count_unknown``).

    It runs BEFORE the walk reads its first page, and it skips the credit floor,
    so a stop here can never drop a page that was already paid for.
    """
    try:
        return await ctx.call(lambda s: s.count_records(entity), floor=False)
    except (NotConnected, Stopped, ModeChanged):
        raise
    except SourceError as exc:
        if _fatal(exc):
            raise
        return None


def _merge_failed(
    failed: dict[str, list[Any]],
    landed: set[str],
    bad: list[tuple[str, str | None]],
) -> dict[str, list[Any]]:
    """The ids to read again: ``{id: [modified_time, runs_retried]}``, capped."""
    merged = {k: v for k, v in failed.items() if k not in landed}
    for ext_id, stamp in bad:
        merged.setdefault(ext_id, [stamp, 0])
    while len(merged) > FAILED_CAP:
        merged.pop(next(iter(merged)))
    return merged


def _tick_failed(failed: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """One run spends one retry of each failed id. An id past its retries
    expires. The log gets the id only."""
    kept: dict[str, list[Any]] = {}
    for ext_id, (stamp, used) in failed.items():
        if int(used) + 1 >= RETRY_LIMIT:
            _log.warning("crm.sync.record_expired ext_id=%s", ext_id)
            continue
        kept[ext_id] = [stamp, int(used) + 1]
    return kept


def _pin(failed: dict[str, list[Any]]) -> datetime | None:
    """The earliest time that a failed record needs a re-read from."""
    stamps = [t for t in (_parse(v[0]) for v in failed.values() if v[0]) if t is not None]
    return min(stamps) - timedelta(seconds=1) if stamps else None


async def _retry_page(
    ctx: _Ctx, entity: str, failed: dict[str, list[Any]],
) -> dict[str, list[Any]]:
    """Read failed records again: ONE page from the oldest of them, no more.

    An old failing record must not make each run re-read many pages.
    """
    failed = _tick_failed(failed)
    since = _pin(failed)
    if since is None:
        return failed
    page = await ctx.call(lambda s: s.list_changed(entity, SourceCursor(since=since)))
    if page.records and ctx.owners is None:
        await ctx.load_owners()
    async with ctx.page() as db:
        landed, bad = await _apply_records(ctx, db, entity, page.records)
    return _merge_failed(failed, landed, bad)


async def _changed_pass(ctx: _Ctx, entity: str) -> None:  # noqa: C901 - one resumable loop
    """Read the records changed since the cursor, one page at a time.

    A module that hides ``Modified_Time`` has no cursor to move, so an
    incremental run does not list it. A reconcile (a full id walk) reads it, at
    most once in 24 hours. A walk stamps each row it reads with its start, and
    it can stop and resume across runs. The first import is also a walk.

    A walk deletes only a row that the PREVIOUS complete walk and this one both
    missed (two strikes), and only when the walk looks whole
    (:func:`_drop_stale`).
    """
    module = changed_module(entity)
    row = _row(ctx, module)
    saved = row.state or {}
    if _unavailable_now(ctx, entity):
        return
    generic = _module(entity) is not None
    # A generic module is never walked: it has no ``ext_seen_at`` stamp, and
    # its deletes come from the deleted list only (CRM-Q6a).
    walking = entity in ctx.reconcile and not generic
    rereading = generic and _module_reread(ctx, row)
    if generic and not rereading and _module_read_recently(ctx, row):
        return
    if (
        row.initial_done_at is not None and saved.get("offset_paged") and not walking
        and not rereading and not (generic and saved.get("in_progress"))
    ):
        return
    stamping = walking or row.initial_done_at is None
    resumed = bool(saved.get("in_progress")) and (bool(saved.get("walk_start")) or not stamping)
    backfill = (
        entity == "lead" and row.initial_done_at is not None
        and not saved.get("converted_both") and not resumed
    )
    hidden = bool(saved.get("offset_paged")) if resumed else False
    walk_start = ctx.started
    walk_count = 0
    known: int | None = None
    if resumed and stamping:
        walk_start = _parse(saved.get("walk_start")) or ctx.started
        walk_count = int(saved.get("walk_count") or 0)
        known = saved.get("walk_known")
    elif walking:
        known = await _safe_count(ctx, entity)
    prev_walk = _parse(saved.get("prev_walk_start"))
    ctx.stamp = walk_start if stamping else None
    failed_map: dict[str, list[Any]] = {
        str(k): list(v) for k, v in (saved.get("failed") or {}).items()
    }
    if not stamping and not resumed and failed_map:
        failed_map = await _retry_page(ctx, entity, failed_map)
    if resumed:
        cursor = cursor_from(saved)
        newest = _parse(saved.get("max_modified"))
        newest_ids = set(saved.get("watermark_ids") or ())
    else:
        since = None if walking or backfill or rereading else row.last_pulled_at
        ids = tuple(saved.get("watermark_ids") or ()) if since is not None else ()
        cursor = SourceCursor(since=since, seen_ids=ids)
        newest = since
        newest_ids = set(ids)
    first_pass = row.initial_done_at is None and not resumed
    # Leads were read without ``converted=both`` before CRM-Q5, and an incremental
    # read never returns a lead that did not change. So the first read of a lead
    # cursor made earlier is a full read, once (CRM-Q5). It does not stamp and
    # it deletes nothing.
    marks: dict[str, Any] = {"converted_both": True} if entity == "lead" else {}
    if rereading:
        # Kept in the cursor while the full re-read runs, so a stop at the
        # reserve resumes it as a re-read (CRM-Q6a).
        marks["reread"] = True
    table = MODULE_TABLE if generic else _table(entity)
    try:
        while True:
            if generic and _budget_low(ctx, MODULES_RESERVE):
                # The cursor of the last page is saved, so the next run goes
                # on from it (CRM-Q6a).
                _log.info("crm.sync.module_paused entity=%s reason=reserve", entity)
                return
            try:
                page = await ctx.call(lambda s, c=cursor: s.list_changed(entity, c))
            except (SourceScopeError, SourceError) as exc:
                # An optional module that cannot be read for ANY reason that is
                # not a dead token, a spent budget or a throttle is skipped.
                if not _skip_optional(entity, exc):
                    raise
                await _mark_unavailable(ctx, entity, row)
                return
            if page.records and ctx.owners is None:
                await ctx.load_owners()
            for record in page.records:
                stamp = _parse(record.fields.get("Modified_Time"))
                if stamp is None:
                    hidden = True
                    continue
                if newest is None or stamp > newest:
                    newest, newest_ids = stamp, {record.ext_id}
                elif stamp == newest:
                    newest_ids.add(record.ext_id)
            walk_count += len(page.records)
            last_page = page.next_cursor is None
            async with ctx.page() as db:
                landed, bad = await _apply_records(ctx, db, entity, page.records)
                failed_map = _merge_failed(failed_map, landed, bad)
                if entity == "deal" and landed:
                    await _queue_roles(ctx, db, landed)
                if not last_page and page.next_cursor is not None:
                    extra: dict[str, Any] = {}
                    if stamping:
                        extra = {
                            "walk_start": _iso(walk_start), "walk_count": walk_count,
                            "walk_known": known,
                        }
                    state = cursor_state(
                        page.next_cursor,
                        max_modified=_iso(newest),
                        watermark_ids=sorted(newest_ids),
                        offset_paged=hidden,
                        failed=failed_map,
                        retry_from=_iso(_pin(failed_map)),
                        prev_walk_start=_iso(prev_walk),
                        **marks,
                        **extra,
                    )
                    row = CursorRow(
                        module, row.last_pulled_at, state, row.initial_done_at,
                        row.last_reconciled_at,
                    )
                else:
                    complete_at = _now()
                    # A walk of nothing says nothing about the paging mode.
                    final_hidden = hidden if walk_count else bool(saved.get("offset_paged"))
                    next_prev = prev_walk
                    if walking:
                        if await _drop_stale(
                            ctx, db, entity, table, prev_walk, walk_count, known,
                            set(failed_map),
                        ):
                            next_prev = walk_start
                    elif stamping:
                        next_prev = walk_start
                    mark = None if final_hidden else _later(row.last_pulled_at, newest)
                    row = CursorRow(
                        module,
                        mark,
                        {
                            "in_progress": False,
                            "offset_paged": final_hidden,
                            "watermark_ids": [] if final_hidden else sorted(newest_ids),
                            "failed": failed_map,
                            "retry_from": _iso(_pin(failed_map)),
                            "prev_walk_start": _iso(next_prev),
                            **marks,
                            # The hourly cadence of a generic module (CRM-Q6a).
                            **({"checked_at": _iso(ctx.started)} if generic else {}),
                        },
                        row.initial_done_at or complete_at,
                        # A generic first import that ran over several runs is
                        # still a full read, so it counts as the re-read too.
                        complete_at if (
                            walking or first_pass or rereading
                            or (generic and row.initial_done_at is None)
                        )
                        else row.last_reconciled_at,
                    )
                await _save_cursor(ctx, db, row, "ok" if last_page else "partial")
            ctx.cursors[module] = row
            if last_page:
                break
            cursor = page.next_cursor  # type: ignore[assignment]
    finally:
        ctx.stamp = None


def _module_reread(ctx: _Ctx, row: CursorRow) -> bool:
    """True when a generic module that hides ``Modified_Time`` is due for its
    full re-read (CRM-Q6a), or one that stopped at the reserve goes on.

    Such a module pages by offset and has no cursor to move, so an incremental
    read cannot see a new record. It is read again in full once each
    :data:`RECONCILE_EVERY`. The door skips each record that did not change,
    so a re-read that finds nothing new writes nothing. It deletes nothing.
    """
    state = row.state or {}
    if state.get("in_progress"):
        return bool(state.get("reread"))
    if row.initial_done_at is None or not state.get("offset_paged"):
        return False
    done = row.last_reconciled_at
    return done is None or ctx.started - done >= RECONCILE_EVERY


def _module_read_recently(ctx: _Ctx, row: CursorRow) -> bool:
    """True when a generic module read its changed list less than
    :data:`MODULE_READ_EVERY` ago (CRM-Q6a). A module whose first import is not
    done, or that stopped mid-read at the reserve, reads on every run."""
    state = row.state or {}
    if row.initial_done_at is None or state.get("in_progress") or state.get("unavailable"):
        return False
    checked = _parse(state.get("checked_at"))
    return checked is not None and ctx.started - checked < MODULE_READ_EVERY


def _unavailable_now(ctx: _Ctx, entity: str) -> bool:
    """True when the module was found unavailable less than an hour ago.

    The run then reads nothing and counts the module as unavailable. After the
    hour it asks once more, so a reconnect that grants the scope heals the sync.
    """
    state = _row(ctx, changed_module(entity)).state or {}
    if not state.get("unavailable"):
        return False
    checked = _parse(state.get("checked_at"))
    if checked is not None and ctx.started - checked >= UNAVAILABLE_RETRY:
        return False
    _count_unavailable(ctx, entity)
    return True


def _count_unavailable(ctx: _Ctx, entity: str) -> None:
    """Set ``unavailable`` under the step key of an optional module."""
    if entity == "task":
        ctx.counts["tasks"]["unavailable"] = 1
    elif _module(entity):
        ctx.module_step(entity, "unavailable", 0)  # makes the key
        ctx.counts[entity]["unavailable"] = 1


async def _mark_unavailable(ctx: _Ctx, entity: str, row: CursorRow) -> None:
    """Record that the connection cannot read ``entity``, and go on.

    The row is marked done, so a module that never reads does not hold the
    plan at ``import`` for ever. The error text is not kept, only the fact.
    """
    now = _now()
    _log.warning("crm.sync.module_unavailable entity=%s reason=scope", entity)
    marked = CursorRow(
        changed_module(entity), row.last_pulled_at,
        {"in_progress": False, "unavailable": True, "checked_at": _iso(now)},
        row.initial_done_at or now, row.last_reconciled_at,
    )
    async with ctx.page() as db:
        _count_unavailable(ctx, entity)
        await _save_cursor(ctx, db, marked, "unavailable")
    ctx.cursors[changed_module(entity)] = marked


async def _drop_stale(
    ctx: _Ctx, db: Any, entity: str, table: str, prev_walk: datetime | None,
    walked: int, known: int | None, failed_ids: set[str],
) -> bool:
    """Delete the rows that two complete walks in a row did not read (reconcile).

    The delete cannot be undone, so it refuses when the walk may be short:

    * ``count_unknown``: the provider count is not known, so nothing proves
      that the walk read everything;
    * ``empty_walk``: the walk read nothing while rows exist here;
    * ``short_walk``: the walk read fewer records than the provider counts;
    * ``large_delete``: more than 20 % of more than 10 local rows.

    Offset paging can skip a row when the provider changes under the walk, so a
    row goes only when its stamp is older than the start of the previous complete
    walk. A refusal keeps the rows and their stamps, logs ``reconcile_refused``
    with the figures and counts in ``refused``. It returns False for a refusal,
    and the caller then keeps its old previous-walk time. A record that failed
    to write is never deleted.
    """
    kind_clause = " AND type = :type" if table == "crm_activities" else ""
    params: dict[str, Any] = {"org": ctx.org, "src": EXT_SOURCE}
    if kind_clause:
        params["type"] = ACTIVITY_TYPES[entity]
    base = (
        f"FROM {table} WHERE organization_id = CAST(:org AS uuid) "
        f"AND ext_source = :src{kind_clause}"
    )
    local = (await db.execute(text(f"SELECT count(*) AS n {base}"), params)).first().n
    stale: list[str] = []
    if prev_walk is not None:
        stale = [
            str(r.ext_id) for r in (await db.execute(
                text(f"SELECT ext_id {base} AND ((ext_seen_at IS NULL AND created_at < :prev) OR ext_seen_at < :prev)"),
                {**params, "prev": prev_walk},
            )).fetchall()
            if str(r.ext_id) not in failed_ids
        ]
    reason = None
    if known is None:
        reason = "count_unknown"
    elif local and walked == 0:
        reason = "empty_walk"
    elif walked < known:
        reason = "short_walk"
    elif local > REFUSE_MIN_ROWS and len(stale) > REFUSE_FRACTION * local:
        reason = "large_delete"
    if reason:
        ctx.bump(entity, "refused")
        _log.warning(
            "crm.sync.reconcile_refused entity=%s reason=%s walked=%s known=%s "
            "local=%s stale=%s", entity, reason, walked, known, local, len(stale),
        )
        return False
    if stale:
        removed = await delete_from_source(db, table, EXT_SOURCE, sorted(stale))
        ctx.bump(entity, "deleted", removed)
    return True


async def _deleted_pass(ctx: _Ctx, entity: str) -> None:
    """Read the deleted list since the cursor, and delete those rows here."""
    module = deleted_module(entity)
    row = _row(ctx, module)
    saved = row.state
    if _is_optional(entity) and (
        (_row(ctx, changed_module(entity)).state or {}).get("unavailable")
    ):
        return
    generic = _module(entity)
    resumed = bool(saved and saved.get("in_progress"))
    checked = _parse((saved or {}).get("checked_at"))
    if (
        not resumed and checked is not None and entity not in ctx.reconcile
        and ctx.started - checked < DELETED_EVERY
    ):
        return
    if resumed and saved is not None:
        cursor = cursor_from(saved)
        window = _parse(saved.get("window_start")) or ctx.started - DELETED_MARGIN
    else:
        cursor = SourceCursor(since=row.last_pulled_at)
        window = ctx.started - DELETED_MARGIN
    table = MODULE_TABLE if generic else _table(entity)
    while True:
        if generic and _budget_low(ctx, MODULES_RESERVE):
            return
        try:
            page = await ctx.call(lambda s, c=cursor: s.list_deleted(entity, c))
        except SourceError as exc:
            if not _skip_optional(entity, exc):
                raise
            _log.warning("crm.sync.module_unavailable entity=%s reason=deleted", entity)
            return
        last_page = page.next_cursor is None
        async with ctx.page() as db:
            gone = [r.ext_id for r in page.records]
            removed = await (
                delete_module_records_from_source(db, EXT_SOURCE, generic, gone)
                if generic else delete_from_source(db, table, EXT_SOURCE, gone)
            )
            ctx.bump(entity, "deleted", removed)
            if not last_page and page.next_cursor is not None:
                row = CursorRow(
                    module, row.last_pulled_at,
                    cursor_state(page.next_cursor, window_start=_iso(window)),
                    row.initial_done_at, row.last_reconciled_at,
                )
            else:
                row = CursorRow(
                    module, window,
                    {"in_progress": False, "checked_at": _iso(ctx.started)},
                    row.initial_done_at or _now(), row.last_reconciled_at,
                )
            await _save_cursor(ctx, db, row, "ok" if last_page else "partial")
        ctx.cursors[module] = row
        if last_page:
            break
        cursor = page.next_cursor  # type: ignore[assignment]


async def _start_deleted_windows(
    ctx: _Ctx, entities: tuple[str, ...] = ENTITY_ORDER,
) -> None:
    """On the first import, mark where the deleted lists start.

    An import mirrors what exists, so a delete before the import is nothing to
    undo. The window opens at the start of the import: a record deleted after
    this moment is read by the first incremental run. A generic module opens
    its window before its first read, in whatever run that is (CRM-Q6a).
    """
    missing = [e for e in entities if deleted_module(e) not in ctx.cursors]
    if not missing:
        return
    opened = ctx.started - DELETED_MARGIN
    async with ctx.page() as db:
        for entity in missing:
            module = deleted_module(entity)
            row = CursorRow(module, opened, {"in_progress": False}, None, None)
            await _save_cursor(ctx, db, row, "ok")
            ctx.cursors[module] = row


# ── Field definitions and the estimate ──────────────────────────────────────

_FIELD_DEF_SQL = (
    "INSERT INTO crm_field_defs (entity, key, label, type, options, source, "
    "provider_api_name, position, visible, updated_at) VALUES (:entity, :key, "
    ":label, :type, CAST(:options AS jsonb), :source, :api_name, :position, "
    ":visible, now()) ON CONFLICT (organization_id, entity, key) DO UPDATE SET "
    "label = EXCLUDED.label, type = EXCLUDED.type, options = EXCLUDED.options, "
    "source = EXCLUDED.source, provider_api_name = EXCLUDED.provider_api_name, "
    "position = EXCLUDED.position, visible = EXCLUDED.visible, updated_at = now()"
)


async def _field_defs(ctx: _Ctx) -> None:
    """The custom fields of each entity, so the UI knows what ``custom`` holds."""
    import json

    for entity in FIELD_DEF_ENTITIES:
        try:
            definitions = await ctx.call(lambda s, e=entity: s.list_field_defs(e))
        except (NotConnected, Stopped):
            raise
        except SourceError as exc:
            if _fatal(exc):
                raise
            # A missing scope must not block the import of the records.
            _log.warning("crm.sync.field_defs_unreadable entity=%s error=%s",
                         entity, error_code(exc))
            continue
        async with ctx.page() as db:
            if entity in _STATUS_FIELDS:
                await _mirror_statuses(ctx, db, entity, definitions)
            for position, definition in enumerate(definitions):
                mapped = ctx.mapping.map_field_def(entity, definition, position)
                if mapped is None:
                    continue
                async with savepoint(db):
                    await db.execute(text(_FIELD_DEF_SQL), {
                        **mapped,
                        "options": json.dumps(mapped["options"])
                        if mapped["options"] is not None else None,
                        "source": EXT_SOURCE,
                        "api_name": mapped["provider_api_name"],
                    })


def _discovery_due(ctx: _Ctx) -> bool:
    """First import, a new connection, a profile older than 24 hours, or a
    profile that no longer fits the connection.

    CRM-Q6a adds two cases. A connection whose scope set differs from the one
    the profile was built with (a reconnect that grants or drops a module
    scope) makes discovery due at once. A profile that still waits for the
    fields of a module (the reserve stopped their read) is due after an hour.
    """
    current = ctx.profile
    if current is None or current.fetched_at is None:
        return True
    if current.connection_id != ctx.handle.connection_id:
        return True
    if current.profile.get("scope_set") != ctx.mapping.scope_set_key(ctx.handle.scopes):
        return True
    age = ctx.started - current.fetched_at
    pending = any(
        row.get("fields_pending") for row in ctx.mapping.generic_modules(current.profile)
    )
    if pending and age >= DELETED_EVERY:
        return True
    return age >= DISCOVERY_EVERY


def _discovery_allowance_due(ctx: _Ctx) -> bool:
    """CRM-Q10: the edition is not known, discovery is due, the run uses the
    default budget, and less than :data:`DISCOVERY_ALLOWANCE` of it is left.

    "Not known" is no profile, or a profile whose edition the provider did not
    name, so the budget is the floor. Then discovery may spend the allowance,
    and it reads before the other steps. ``_discovery_due`` holds it to once in
    24 hours, also when the org section stays unreadable. A known edition never
    gets it, and neither does a test budget (``credit_budget``).
    """
    if not ctx.budget_is_default:
        return False
    if profiles.sync_budget(ctx.profile.profile if ctx.profile else None) is not None:
        return False
    if not _discovery_due(ctx):
        return False
    left = ctx._budget_left()
    return left is not None and left < DISCOVERY_ALLOWANCE


_NAMED = (
    "lower(btrim(CASE jsonb_typeof(custom->'Pipeline') "
    "WHEN 'string' THEN custom->>'Pipeline' "
    "WHEN 'object' THEN custom->'Pipeline'->>'name' END))"
)
_UNLINKED = (
    "UPDATE crm_deals SET pipeline_id = CAST(:p AS uuid) "
    "WHERE ext_source = :src AND pipeline_id IS NULL AND "
)
_RELINK_BY_EXT = _UNLINKED + "custom->'Pipeline'->>'id' = :ext"
_RELINK_BY_NAME = _UNLINKED + _NAMED + " = :name"
_RELINK_DEFAULT = _UNLINKED + (
    "(custom->'Pipeline' IS NULL OR jsonb_typeof(custom->'Pipeline') = 'null' "
    "OR (jsonb_typeof(custom->'Pipeline') = 'string' AND btrim(custom->>'Pipeline') = ''))"
)


async def _relink_deals(db: Any) -> None:
    """Give a mirrored deal with no pipeline the pipeline its stored Zoho
    ``Pipeline`` value names (CRM-Q2 review). A deal read before the pipelines
    existed keeps that value in ``custom``. By id first, then by name in
    pipeline order. A deal that names no pipeline joins the default one, and a
    deal that names an unknown one stays unlinked."""
    # These raw UPDATEs on ``crm_deals`` are the sync's own write, like
    # ``upsert_from_source``. ``crm_sync/`` is the only place they may live, and
    # the fence holds that by directory: no route, agent or skill scans in. They
    # leave ``zoho_dirty`` and ``updated_at`` alone on purpose, because a re-link
    # is not a native edit and must not push the deal back to Zoho. The partial
    # index of migration 247 (``crm_deals_unlinked_idx``) serves them.
    rows = (await db.execute(text(_PIPELINE_INDEX_SQL), {"src": EXT_SOURCE})).fetchall()
    for row in rows:
        await db.execute(text(_RELINK_BY_EXT), {"p": str(row.id), "src": EXT_SOURCE, "ext": str(row.ext_id)})
    for row in rows:
        await db.execute(text(_RELINK_BY_NAME), {
            "p": str(row.id), "src": EXT_SOURCE, "name": str(row.name).strip().lower()})
    default = next((r for r in rows if r.is_default), rows[0] if rows else None)
    if default is not None:
        await db.execute(text(_RELINK_DEFAULT), {"p": str(default.id), "src": EXT_SOURCE})


_RELINK_LEAD_SQL = (
    "UPDATE crm_leads l SET {column} = t.id FROM {table} t "
    "WHERE l.ext_source = :src AND l.{column} IS NULL "
    "AND t.ext_source = :src AND t.organization_id = l.organization_id "
    "AND t.ext_id = l.custom->'{key}'->>'{kind}'"
)
_UNRESOLVED_LEAD_SQL = (
    "SELECT count(*) AS n FROM crm_leads WHERE ext_source = :src AND ("
    "(custom->'{key}'->>'contact' IS NOT NULL AND converted_contact_id IS NULL) OR "
    "(custom->'{key}'->>'company' IS NOT NULL AND converted_organization_id IS NULL) OR "
    "(custom->'{key}'->>'deal' IS NOT NULL AND converted_deal_id IS NULL))"
)
_CONVERSION_TABLES = {"contact": "crm_contacts", "company": "crm_companies", "deal": "crm_deals"}


async def _relink_leads(ctx: _Ctx) -> None:
    """Give a converted lead the local contact, company and deal that its stored
    Zoho ids name (CRM-Q5). A lead is read before the deals, so a deal id is not
    resolvable on the first pass. A later run, or this pass, fills it in. An id
    that names a record that is not here stays NULL, and ``unlinked`` counts it.

    Like :func:`_relink_deals`, these raw UPDATEs are the sync's own write. They
    leave ``zoho_dirty`` and ``updated_at`` alone, because a re-link is not a
    native edit and must not push the lead back to Zoho.
    """
    async with ctx.page() as db:
        for kind, column in _CONVERSION_COLUMNS.items():
            await db.execute(text(_RELINK_LEAD_SQL.format(
                column=column, table=_CONVERSION_TABLES[kind], key=CONVERTED_KEY, kind=kind,
            )), {"src": EXT_SOURCE})
        row = (await db.execute(
            text(_UNRESOLVED_LEAD_SQL.format(key=CONVERTED_KEY)), {"src": EXT_SOURCE},
        )).first()
        # A gauge, not a sum: the leads that still wait for a record.
        ctx.counts["converted_leads"]["unlinked"] = int(row.n if row is not None else 0)


async def _roles_pending(ctx: _Ctx) -> list[str]:
    if ctx.roles_pending is None:
        state = _row(ctx, ROLES_MODULE).state or {}
        ctx.roles_pending = [str(i) for i in state.get("pending") or ()]
        ctx.roles_attempts = {
            str(k): int(v) for k, v in (state.get("attempts") or {}).items()
        }
        ctx.roles_seeded = bool(state.get("seeded"))
    return ctx.roles_pending


async def _save_roles_pending(ctx: _Ctx, db: Any) -> None:
    pending = ctx.roles_pending or []
    if len(pending) > ROLES_PENDING_CAP:
        _log.warning("crm.sync.roles_queue_capped dropped=%s", len(pending) - ROLES_PENDING_CAP)
        pending = pending[-ROLES_PENDING_CAP:]
        ctx.roles_pending = pending
    old = _row(ctx, ROLES_MODULE)
    live = set(pending)
    attempts = {k: v for k, v in ctx.roles_attempts.items() if k in live}
    row = CursorRow(ROLES_MODULE, old.last_pulled_at,
                    {"pending": pending, "attempts": attempts, "seeded": ctx.roles_seeded},
                    old.initial_done_at or _now(), old.last_reconciled_at)
    await _save_cursor(ctx, db, row, "ok")
    ctx.cursors[ROLES_MODULE] = row


async def _queue_roles(ctx: _Ctx, db: Any, landed: set[str]) -> None:
    """Remember the deals that changed, in the SAME transaction as their page.

    A run that stops before it reads the roles still owes them to the next run.
    """
    pending = await _roles_pending(ctx)
    known = set(pending)
    pending.extend(sorted(i for i in landed if i not in known))
    await _save_roles_pending(ctx, db)


def _budget_low(ctx: _Ctx, reserve: int) -> bool:
    """True when the run has *reserve* credits or fewer left, by its own
    budget or by the count the provider reports."""
    left = ctx._budget_left()
    if left is not None and left <= reserve:
        return True
    return ctx.credits_left is not None and ctx.credits_left <= CREDITS_FLOOR + reserve


def _roles_budget_low(ctx: _Ctx) -> bool:
    return _budget_low(ctx, ROLES_RESERVE)


_SEED_ROLES_SQL = (
    "SELECT ext_id FROM crm_deals WHERE ext_source = :src AND ext_id IS NOT NULL "
    "ORDER BY ext_id"
)


async def _seed_roles(ctx: _Ctx) -> None:
    """Queue every mirrored deal once (CRM-Q5). A deal that was mirrored before
    the roles step existed would never be queued, because its cursor is past
    it. The queue drains over several runs, inside the budget and the reserve."""
    await _roles_pending(ctx)
    if ctx.roles_seeded:
        return
    async with ctx.page() as db:
        rows = (await db.execute(text(_SEED_ROLES_SQL), {"src": EXT_SOURCE})).fetchall()
        known = set(ctx.roles_pending or [])
        ctx.roles_pending = [*(ctx.roles_pending or []),
                             *(str(r.ext_id) for r in rows if str(r.ext_id) not in known)]
        ctx.roles_seeded = True
        await _save_roles_pending(ctx, db)


async def _deal_roles(ctx: _Ctx) -> None:
    """Read the contact roles of the deals that changed (CRM-Q5), one call each.

    The deals wait in ``zoho:deal_roles``. Each call costs one credit, so the
    step stops when the budget is low and leaves the rest queued. ``skipped``
    counts the deals it left. A role that Zoho no longer lists is removed, and a
    contact that is not here is counted in ``unlinked``. A scope refusal or a
    failed read keeps the deal queued, and the third failure drops it.
    """
    await _seed_roles(ctx)
    pending = list(ctx.roles_pending or [])
    if not pending:
        return
    done: list[str] = []
    for ext_id in list(pending):
        if _roles_budget_low(ctx):
            break
        try:
            roles = await ctx.call(lambda s, i=ext_id: s.list_deal_contact_roles(i))
        except (NotConnected, Stopped, ModeChanged):
            raise
        except SourceScopeError:
            _log.warning("crm.sync.deal_roles_unavailable reason=scope")
            ctx.counts["deal_contacts"]["unavailable"] = 1
            break
        except SourceError as exc:
            if _fatal(exc):
                raise
            _log.warning("crm.sync.deal_roles_unreadable deal=%s error=%s",
                         ext_id, error_code(exc))
            tries = ctx.roles_attempts.get(ext_id, 0) + 1
            ctx.roles_attempts[ext_id] = tries
            if tries >= ROLES_MAX_ATTEMPTS:
                _log.warning("crm.sync.deal_roles_dropped deal=%s", ext_id)
                ctx.step("deal_contacts", "skipped")
                done.append(ext_id)
            continue
        ctx.step("deal_contacts", "seen")
        async with ctx.page() as db:
            await _write_roles(ctx, db, ext_id, roles)
            done.append(ext_id)
            gone = set(done)
            ctx.roles_pending = [i for i in pending if i not in gone]
            await _save_roles_pending(ctx, db)
    if pending and len(done) < len(pending):
        ctx.step("deal_contacts", "skipped", len(pending) - len(done))
    if done or ctx.roles_attempts:
        # Save the queue and the attempt counts of the reads that failed.
        async with ctx.page() as db:
            gone = set(done)
            ctx.roles_pending = [i for i in pending if i not in gone]
            await _save_roles_pending(ctx, db)


async def _write_roles(
    ctx: _Ctx, db: Any, deal_ext_id: str, roles: list[tuple[str, str | None]],
) -> None:
    deal = await _local_id(ctx, db, "deal", deal_ext_id, {})
    if deal is None:
        ctx.step("deal_contacts", "skipped")  # the deal was deleted since
        return
    cache: dict[tuple[str, str], str | None] = {}
    wanted: list[tuple[str, str | None]] = []
    for contact_ext, role in roles:
        local = await _local_id(ctx, db, "contact", contact_ext, cache)
        if local is None:
            ctx.step("deal_contacts", "unlinked")
        else:
            wanted.append((local, role))
    if roles and not wanted:
        # No contact here yet: removing rows now would drop real links.
        return
    async with savepoint(db):
        written, removed = await set_deal_contacts_from_source(db, deal, wanted)
    ctx.step("deal_contacts", "upserted", written)
    ctx.step("deal_contacts", "deleted", removed)


async def _mirror_pipelines(
    ctx: _Ctx, db: Any, rows: list[dict[str, Any]],
) -> None:
    """Write the discovered deal pipelines and their stages (CRM-Q2).

    A stage is one global row (``crm_deal_statuses``), so a stage that two
    pipelines share is one row with two memberships. A stage the picklist did
    not list is appended. A pipeline that Zoho no longer lists is deleted here,
    and its deals keep their row with a NULL pipeline.
    """
    seen: list[str] = []
    for position, row in enumerate(rows):
        async with savepoint(db):
            pipeline = await upsert_from_source(db, "crm_pipelines", {
                "name": row["name"],
                "position": position,
                "is_default": bool(row["default"]),
                "layout_ext_id": row.get("layout_ext_id"),
                "ext_source": EXT_SOURCE,
                "ext_id": row["ext_id"],
            })
            members: list[tuple[str, int]] = []
            for stage in row.get("stages") or []:
                status_id = await _ensure_status(
                    db, DEAL_STATUSES, stage["name"], type_=stage.get("type"),
                    forecast_category=stage.get("forecast_category"),
                )
                if all(status_id != existing for existing, _ in members):
                    members.append((status_id, len(members)))
            await set_pipeline_stages_from_source(db, str(pipeline.id), members)
        seen.append(row["ext_id"])
    if not seen:
        return
    await _apply_stage_types(db, ctx.org, rows)
    known = [
        str(r.ext_id) for r in (await db.execute(
            text(_PIPELINE_INDEX_SQL), {"src": EXT_SOURCE},
        )).fetchall()
    ]
    gone = [ext_id for ext_id in known if ext_id not in set(seen)]
    if gone:
        await delete_from_source(db, "crm_pipelines", EXT_SOURCE, gone)
    await _relink_deals(db)
    ctx.pipelines = None


async def _read_module_fields(
    ctx: _Ctx, profile: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """The ``settings/fields`` rows of each mirrored generic module (CRM-Q6a),
    and the modules whose read waits for the next discovery.

    Metadata only, one credit for each module. Discovery runs before the known
    entities, so these reads stop while :data:`MODULES_RESERVE` credits remain.
    The skipped modules stay ``fields_pending`` in the profile, and the next
    discovery reads them. A module whose fields cannot be read for another
    reason keeps its old display field, and its records still sync.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    skipped: set[str] = set()
    for row in ctx.mapping.generic_modules(profile):
        if not row.get("mirrored"):
            continue
        if _budget_low(ctx, MODULES_RESERVE):
            skipped.add(row["api_name"])
            continue
        entity = ctx.mapping.module_entity(row["api_name"])
        try:
            found[row["api_name"]] = await ctx.call(
                lambda s, e=entity: s.list_field_defs(e),
            )
        except (NotConnected, Stopped, ModeChanged):
            raise
        except SourceError as exc:
            if _fatal(exc):
                raise
            _log.warning("crm.sync.module_fields_unreadable entity=%s error=%s",
                         entity, error_code(exc))
    return found, skipped


async def _write_module_field_defs(
    ctx: _Ctx, db: Any, module_fields: dict[str, list[dict[str, Any]]],
) -> None:
    """Write the fields of each generic module into ``crm_field_defs`` with
    ``entity = 'module:<ApiName>'`` (CRM-Q6a). The upsert is the one that the
    known entities use."""
    import json

    for api_name, definitions in module_fields.items():
        for position, definition in enumerate(definitions):
            mapped = ctx.mapping.map_module_field_def(api_name, definition, position)
            if mapped is None:
                continue
            async with savepoint(db):
                await db.execute(text(_FIELD_DEF_SQL), {
                    **mapped,
                    "options": json.dumps(mapped["options"])
                    if mapped["options"] is not None else None,
                    "source": EXT_SOURCE,
                    "api_name": mapped["provider_api_name"],
                })


async def _discover(ctx: _Ctx) -> None:
    """Read the shape of the customer's account, once in 24 hours (CRM-Q1).

    It reads metadata only, through ``CrmSource.discover``, and counts each
    call. A section that cannot be read keeps the value of the previous profile.
    A fatal failure (a dead token, a spent budget, a throttle) ends the run, and
    any other failure only costs the section.
    """
    if ctx.discovery_tried or not _discovery_due(ctx):
        return
    ctx.discovery_tried = True
    budget_before = ctx.budget_total
    if _discovery_allowance_due(ctx):
        # CRM-Q10: the discovery call may spend the allowance, and no more.
        # Advisory: the floor check of ``ctx.call`` sees only a count that Zoho
        # reported earlier in this run, and an early discovery is the first
        # call of its run. The cap of the allowance bounds it.
        ctx.budget_total = ctx.credits + DISCOVERY_ALLOWANCE
    try:
        raw = await ctx.call(lambda s: s.discover())
    except (NotConnected, Stopped, ModeChanged):
        raise
    except SourceError as exc:
        ctx.budget_total = budget_before
        if _fatal(exc):
            raise
        _log.warning("crm.sync.discovery_failed: %s", error_code(exc))
        return
    previous = ctx.profile.profile if ctx.profile is not None else None
    profile = ctx.mapping.build_profile(raw, previous, ctx.handle.scopes)
    if ctx.budget_is_default:
        # The edition is known now. The module fields and the rest of the run
        # obey its budget, and the credits of this run count against it.
        ctx.budget_total = max(daily_budget(profile) - ctx.used_24h, 0)
    module_fields, fields_skipped = await _read_module_fields(ctx, profile)
    profile = ctx.mapping.with_module_fields(
        profile, module_fields, previous, pending=fields_skipped,
    )
    org = profile.get("org") or {}
    meta = {
        "domain_name": org.get("domain_name"),
        "edition": org.get("edition"),
        "tabs": ctx.mapping.profile_tabs(profile) or None,
    }
    async with ctx.page() as db:
        await profiles.store(db, ctx.org, ctx.handle.connection_id, profile)
        await store.merge_provider_meta(db, ctx.org, ctx.handle.connection_id, meta)
        readable = "pipelines" not in (profile.get("unreadable") or [])
        if readable and not ctx.pipelines_deferred:
            await _mirror_pipelines(ctx, db, profile.get("pipelines") or [])
        await _write_module_field_defs(ctx, db, module_fields)
        ctx.profile = await profiles.latest(db, ctx.org)


#: The Zoho pipelines that this org already mirrors (CRM-Q10).
_ZOHO_PIPELINES_SQL = "SELECT count(*) FROM crm_pipelines WHERE ext_source = :src"


async def _mirror_deferred_pipelines(ctx: _Ctx) -> None:
    """Write the pipelines of the stored profile at the normal discovery slot,
    after the Stage picklist (CRM-Q10). No provider call.

    Two cases. The early discovery of this run deferred them. Or an earlier run
    stored the profile and stopped before this slot, so the org mirrors no Zoho
    pipeline yet (a start over deletes them). An org that already mirrors a
    Zoho pipeline and deferred nothing is left alone.
    """
    current = ctx.profile
    if current is None or str(current.connection_id) != str(ctx.handle.connection_id):
        return
    profile = current.profile or {}
    pipelines = profile.get("pipelines") or []
    if "pipelines" in (profile.get("unreadable") or []) or not pipelines:
        return
    async with ctx.page() as db:
        if not ctx.pipelines_deferred and (await db.execute(
            text(_ZOHO_PIPELINES_SQL), {"src": EXT_SOURCE},
        )).scalar():
            return
        await _mirror_pipelines(ctx, db, pipelines)
    ctx.pipelines_deferred = False


async def _stage_types_from_profile(ctx: _Ctx) -> None:
    """Apply the stage types of the latest stored profile. No provider call.

    Discovery reads the pipelines once in 24 hours. This pass makes each run
    apply what the profile already holds, so a fix of the type rule reaches an
    org on its next run. ``ctx.page`` refuses an org that is not in Mirror
    mode, so a Native org's stages are never touched.
    """
    current = ctx.profile
    if current is None or str(current.connection_id) != str(ctx.handle.connection_id):
        return
    profile = current.profile or {}
    if "pipelines" in (profile.get("unreadable") or []):
        return
    pipelines = profile.get("pipelines") or []
    if not stage_types(pipelines):
        return
    async with ctx.page() as db:
        await _apply_stage_types(db, ctx.org, pipelines)


async def _estimate(ctx: _Ctx) -> None:
    """The record count of each entity, for the progress bar. Best effort."""
    totals: dict[str, int] = {}
    for entity in ENTITY_ORDER:
        try:
            count = await ctx.call(lambda s, e=entity: s.count_records(e))
        except (NotConnected, Stopped):
            raise
        except SourceError as exc:
            if _fatal(exc):
                raise
            continue
        if count is not None:
            totals[SLUGS[entity]] = totals.get(SLUGS[entity], 0) + count
    ctx.estimate = totals or None
    async with ctx.page():
        pass


# ── Execute ─────────────────────────────────────────────────────────────────


async def _execute(ctx: _Ctx) -> None:
    async with tenant_session(ctx.org) as db:
        ctx.members = await runs.member_emails(db, ctx.org)
        used = await runs.credits_used_24h(db, ctx.org)
        ctx.profile = await profiles.latest(db, ctx.org)
    ctx.used_24h = int(used or 0)
    if ctx.budget_total is None:
        ctx.budget_total = max(
            daily_budget(ctx.profile.profile if ctx.profile else None) - ctx.used_24h, 0,
        )
    if _discovery_allowance_due(ctx):
        # CRM-Q10: with the edition unknown and too little of the default budget
        # left, discovery reads first, on its allowance. It finds the edition,
        # and so the budget that pays for the estimate and the field
        # definitions. It stores the profile, and the pipelines wait for the
        # normal slot below, after the Stage picklist.
        ctx.pipelines_deferred = True
        await _discover(ctx)
    await _stage_types_from_profile(ctx)
    if ctx.kind == runs.KIND_IMPORT:
        await _start_deleted_windows(ctx)
        if ctx.estimate is None:
            await _estimate(ctx)
    if ctx.kind in (runs.KIND_IMPORT, runs.KIND_RECONCILE):
        await _field_defs(ctx)
    # After the stages are mirrored, so a pipeline stage that the picklist did
    # not list is appended and a listed one keeps its place. Before the records,
    # so a deal finds its pipeline.
    await _discover(ctx)
    await _mirror_deferred_pipelines(ctx)
    for entity in ENTITY_ORDER:
        await _changed_pass(ctx, entity)
    if ctx.kind != runs.KIND_IMPORT:
        for entity in ENTITY_ORDER:
            await _deleted_pass(ctx, entity)
    await _relink_leads(ctx)
    await _deal_roles(ctx)
    await _module_records(ctx)


# ── The generic record store (CRM-Q6a) ──────────────────────────────────────


async def _module_records(ctx: _Ctx) -> None:
    """Read each generic module of the profile, after the known entities and
    the CRM-Q5 steps (``crm_platform.md`` §18.7).

    A module whose read scope the connection lacks is counted as unavailable,
    with no call. The run decides that from the scopes of the connection NOW
    (``RunHandle.scopes``), not only from the ``mirrored`` flag that discovery
    stored. A module whose fields wait for the next discovery is not read
    yet, so no record lands without its name. Each other module reads the same
    way as a known entity: the changed records since its cursor
    ``zoho:module:<ApiName>``, then the deleted list. The step stops while
    :data:`MODULES_RESERVE` credits remain, and the next run goes on from the
    cursor. A scope error, a 400 or a 500 marks that one module unavailable,
    with the CRM-Q5 rule.
    """
    rows = ctx.mapping.generic_modules(ctx.profile.profile if ctx.profile else None)
    if not rows:
        return
    ctx.modules = {row["api_name"]: row for row in rows}
    granted = set(ctx.handle.scopes)
    entities: list[str] = []
    for row in rows:
        entity = ctx.mapping.module_entity(row["api_name"])
        if row.get("scope") not in granted:
            _count_unavailable(ctx, entity)
        elif row.get("fields_pending"):
            ctx.module_step(entity, "skipped")
        else:
            entities.append(entity)
    if not entities or _budget_low(ctx, MODULES_RESERVE):
        return
    async with ctx.page() as db:
        connection = await store.get(db, ctx.org, PROVIDER, OWNING_APP)
    ctx.link_meta = dict(connection.provider_meta) if connection is not None else {}
    await _start_deleted_windows(ctx, tuple(entities))
    for entity in entities:
        if _budget_low(ctx, MODULES_RESERVE):
            break
        ctx.module_step(entity, "seen", 0)  # the key shows in the counts
        await _changed_pass(ctx, entity)
        if ctx.kind != runs.KIND_IMPORT and not _unavailable_now(ctx, entity):
            await _deleted_pass(ctx, entity)
        if not _unavailable_now(ctx, entity):
            await _module_estimate(ctx, entity)


async def _module_estimate(ctx: _Ctx, entity: str) -> None:
    """The provider count of one generic module, for Sync health's
    ``zoho_count``. One credit, read again after :data:`DISCOVERY_EVERY`.

    The count is kept in the cursor row :data:`MODULE_COUNTS_ROW`, so an
    incremental run still reports it. A failure costs only the count, and its
    time is kept too, so a module that cannot be counted is asked once a day
    and not on every run.
    """
    row = _row(ctx, MODULE_COUNTS_ROW)
    state = row.state or {}
    counts = {str(k): v for k, v in (state.get("counts") or {}).items()}
    stamps = {str(k): v for k, v in (state.get("at") or {}).items()}
    stamp = _parse(stamps.get(entity))
    due = stamp is None or ctx.started - stamp >= DISCOVERY_EVERY
    if due and not _budget_low(ctx, MODULES_RESERVE):
        try:
            count = await ctx.call(lambda s: s.count_records(entity))
        except (NotConnected, Stopped, ModeChanged):
            raise
        except SourceError as exc:
            if _fatal(exc):
                raise
            count = None
        if count is not None:
            counts[entity] = int(count)
        stamps[entity] = _iso(ctx.started)
        saved = CursorRow(
            MODULE_COUNTS_ROW, row.last_pulled_at,
            {"counts": counts, "at": stamps},
            row.initial_done_at or ctx.started, row.last_reconciled_at,
        )
        if isinstance(counts.get(entity), int):
            ctx.estimate = {**(ctx.estimate or {}), entity: counts[entity]}
        async with ctx.page() as db:
            await _save_cursor(ctx, db, saved, "ok")
        ctx.cursors[MODULE_COUNTS_ROW] = saved
        return
    if isinstance(counts.get(entity), int):
        ctx.estimate = {**(ctx.estimate or {}), entity: counts[entity]}


async def _finish(
    ctx: _Ctx, status: str, error: str | None, *, connection_synced: bool,
) -> None:
    async with tenant_session(ctx.org) as db:
        await runs.finish(
            db, ctx.org, ctx.handle.run_id, status, error, ctx.counts, ctx.estimate,
            ctx.credits,
        )
        if connection_synced:
            await store.mark_synced(db, ctx.org, ctx.handle.connection_id)


async def execute(
    handle: RunHandle,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    credit_budget: int | None = None,
) -> RunResult:
    """Run the sync that :func:`acquire` locked, and close its row.

    It never raises for a failure of the run. It writes the failure to the row
    as a status and a safe code. ``credit_budget`` overrides the default
    budget, for a test.
    """
    ctx = _Ctx(handle, should_stop=should_stop, credit_budget=credit_budget)
    status, error = runs.STATUS_OK, None
    try:
        await _execute(ctx)
    except RunLost:
        _log.warning("crm.sync.run_lost run=%s", handle.run_id)
        return RunResult(handle.run_id, handle.kind, runs.STATUS_FAILED, "stale",
                         ctx.counts, ctx.credits)
    except asyncio.CancelledError:
        await asyncio.shield(_finish(ctx, runs.STATUS_STOPPED, "shutdown",
                                     connection_synced=False))
        raise
    except Exception as exc:
        error = error_code(exc)
        stopped = isinstance(
            exc, NeedsReconnect | BudgetExhausted | Stopped | NotConnected | ModeChanged,
        )
        status = runs.STATUS_STOPPED if stopped else runs.STATUS_FAILED
        if isinstance(exc, NotConnected):
            error = "disconnected"
        # The class only: a message can quote the provider.
        _log.warning("crm.sync.run_ended status=%s error=%s class=%s",
                     status, error, type(exc).__name__)
    try:
        await _finish(ctx, status, error, connection_synced=status == runs.STATUS_OK)
    except Exception as exc:
        _log.error("crm.sync.finish_failed class=%s", type(exc).__name__)
    return RunResult(handle.run_id, handle.kind, status, error, ctx.counts, ctx.credits)


async def run_org(
    org: str,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    credit_budget: int | None = None,
) -> RunResult:
    """:func:`acquire`, then :func:`execute`."""
    handle = await acquire(org)
    return await execute(handle, should_stop=should_stop, credit_budget=credit_budget)


__all__ = [
    "CREDITS_FLOOR",
    "DAILY_CREDIT_BUDGET",
    "ENTITY_ORDER",
    "INTERVAL_SECONDS",
    "MODE_SHARE_SQL",
    "SLUGS",
    "SLUG_ORDER",
    "ModeChanged",
    "NotConnected",
    "RunHandle",
    "RunResult",
    "Stopped",
    "SyncRunning",
    "acquire",
    "daily_budget",
    "error_code",
    "execute",
    "next_interval",
    "plan",
    "run_org",
]
