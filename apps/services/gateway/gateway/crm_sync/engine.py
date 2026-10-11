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
credits (:data:`DAILY_CREDIT_BUDGET`), counted over 24 hours. It stops with
the code ``budget`` before a call that would pass it. It also stops when the
provider reports fewer than :data:`CREDITS_FLOOR` credits left.

**Writes.** Every record write goes through ``core.upsert_from_source`` and
every delete through ``core.delete_from_source``. Those two are the ONLY
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
    UntrustedHost,
)
from gateway.crm_sources.registry import mapping_for, source_class
from gateway.crm_sync import runs
from gateway.crm_sync.runs import CursorRow, RunLost, SyncRunning
from gateway.routes.crm.core import (
    compute_lead_name,
    delete_from_source,
    savepoint,
    upsert_from_source,
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
    "company", "contact", "lead", "deal", "note", "call", "meeting",
)

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
}
SLUG_ORDER: tuple[str, ...] = ("organizations", "contacts", "leads", "deals", "activities")

#: The ``type`` of a ``crm_activities`` row for an activity entity.
ACTIVITY_TYPES = {"note": "note", "call": "call", "meeting": "meeting"}

LEAD_STATUSES = "crm_lead_statuses"
DEAL_STATUSES = "crm_deal_statuses"
#: The status of a record that names none. ``status_id`` is NOT NULL.
UNKNOWN_STATUS = "Unknown"

#: ``created_by`` of an activity whose owner is not a member.
SYNC_ACTOR = "crm:zoho-sync"

#: The time between two runs of one organization.
INTERVAL_SECONDS = 10 * 60

#: 10 % of an assumed 50,000 daily credits (the base of Standard and
#: Professional, §5.2 item 6). The real cap depends on the edition.
DAILY_CREDIT_BUDGET = 5_000

#: Stop when the provider reports fewer credits than this.
CREDITS_FLOOR = 1_000

RECONCILE_EVERY = timedelta(hours=24)
#: The provider keeps a delete for 60 days. Past 50 days without a good run,
#: only a full id compare finds the deletes.
GAP_RECONCILE = timedelta(days=50)
#: A deleted list reads from this long before the start of the pass, so a
#: delete near the edge is read twice and never missed. A repeat is harmless.
DELETED_MARGIN = timedelta(minutes=10)

#: A test sets this to put a fake transport under every provider call.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class NotConnected(Exception):
    """The organization has no live connection to the provider."""


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
        if row is None or row.initial_done_at is None:
            return runs.KIND_IMPORT, frozenset()
    due: set[str] = set()
    for entity in ENTITY_ORDER:
        row = cursors[changed_module(entity)]
        if (row.state or {}).get("offset_paged") and (
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
    if isinstance(exc, Stopped):
        return "shutdown"
    return "internal"


def _fatal(exc: BaseException) -> bool:
    return isinstance(exc, NeedsReconnect | BudgetExhausted | RateLimited)


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
        slug: {"seen": 0, "upserted": 0, "deleted": 0, "unlinked": 0, "failed": 0}
        for slug in SLUG_ORDER
    }


def _merge_counts(saved: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts = _blank_counts()
    for slug, values in (saved or {}).items():
        if slug in counts and isinstance(values, dict):
            for key in counts[slug]:
                counts[slug][key] = int(values.get(key) or 0)
    return counts


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

    # ── Provider calls ──────────────────────────────────────────────────────

    def _budget_left(self) -> int | None:
        if self.budget_total is None:
            return None
        return max(self.budget_total - self.credits, 0)

    async def call(self, op: Callable[[CrmSource], Awaitable[T]]) -> T:
        """One provider call, with a fresh shared credential and the budget.

        No database session is open while the call runs.
        """
        if self.should_stop():
            raise Stopped()
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
            if self.credits_left is not None and self.credits_left <= CREDITS_FLOOR:
                raise BudgetExhausted(
                    "The provider reports few credits left",
                    used=self.credits, budget=self.budget_total or 0,
                )

    # ── Pages ───────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Any]:
        """One ``tenant_session`` for one page. It writes the progress, and the
        heartbeat, in the same transaction as the page.

        On a failure the in-memory counts return to what the last commit holds.
        """
        snapshot = copy.deepcopy(self.counts)
        try:
            async with tenant_session(self.org) as db:
                yield db
                await runs.progress(
                    db, self.org, self.handle.run_id, self.counts, self.estimate,
                    self.credits,
                )
        except BaseException:
            self.counts = snapshot
            raise

    def bump(self, entity: str, name: str, amount: int = 1) -> None:
        self.counts[SLUGS[entity]][name] += amount

    # ── Lookups ─────────────────────────────────────────────────────────────

    async def load_owners(self) -> dict[str, str]:
        if self.owners is None:
            owners: dict[str, str] = {}
            try:
                users = await self.call(lambda s: s.list_users())
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
    "INSERT INTO crm_deal_contacts (deal_id, contact_id, is_primary) "
    "SELECT CAST(:deal_id AS uuid), CAST(:contact_id AS uuid), "
    "       NOT EXISTS (SELECT 1 FROM crm_deal_contacts "
    "                    WHERE deal_id = CAST(:deal_id AS uuid) AND is_primary) "
    "ON CONFLICT DO NOTHING"
)

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


async def _ensure_status(
    db: Any, table: str, name: str, *, type_: str | None = None,
    position: int | None = None, probability: int | None = None,
) -> str:
    """The id of the status called ``name``. A new one is appended."""
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
                )


@dataclass
class _Caches:
    links: dict[tuple[str, str], str | None] = field(default_factory=dict)
    statuses: dict[tuple[str, str], str] = field(default_factory=dict)


async def _write_record(
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
            return False

    if entity in ("lead", "deal"):
        values["status_id"] = await _status_id(
            db, LEAD_STATUSES if entity == "lead" else DEAL_STATUSES,
            mapped.status_name, caches.statuses,
        )
    if "lead_name" in mapped.derived:
        values["lead_name"] = compute_lead_name(values)

    row = await upsert_from_source(db, mapped.table, values)

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
    if mapped.table == "crm_activities":
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


async def _apply_records(
    ctx: _Ctx, db: Any, entity: str, records: tuple[SourceRecord, ...],
) -> None:
    caches = _Caches()
    for record in records:
        ctx.bump(entity, "seen")
        try:
            async with savepoint(db):
                written = await _write_record(ctx, db, entity, record, caches)
        except Exception as exc:  # one bad record must not lose the page
            ctx.bump(entity, "failed")
            # The id and the class of the error only. A database error text
            # can quote a value, and a value is the customer's data.
            _log.warning(
                "crm.sync.record_failed entity=%s ext_id=%s error=%s",
                entity, record.ext_id, type(exc).__name__,
            )
            continue
        if written:
            ctx.bump(entity, "upserted")


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


async def _changed_pass(ctx: _Ctx, entity: str) -> None:
    """Read the records changed since the cursor, one page at a time."""
    module = changed_module(entity)
    row = _row(ctx, module)
    fresh = entity in ctx.reconcile
    saved = None if fresh else row.state
    resumed = bool(saved and saved.get("in_progress"))
    hidden = bool(saved and saved.get("offset_paged")) if resumed else False
    if resumed and saved is not None:
        cursor = cursor_from(saved)
        newest = _parse(saved.get("max_modified"))
        newest_ids = set(saved.get("watermark_ids") or ())
    else:
        since = None if fresh else row.last_pulled_at
        ids = tuple((saved or {}).get("watermark_ids") or ()) if since is not None else ()
        cursor = SourceCursor(since=since, seen_ids=ids)
        newest = since
        newest_ids = set(ids)
    walked: set[str] | None = set() if fresh else None
    first_pass = row.initial_done_at is None and not resumed
    table = _table(entity)

    while True:
        page = await ctx.call(lambda s, c=cursor: s.list_changed(entity, c))
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
        if walked is not None:
            walked.update(r.ext_id for r in page.records)
        last_page = page.next_cursor is None
        async with ctx.page() as db:
            await _apply_records(ctx, db, entity, page.records)
            if not last_page and page.next_cursor is not None:
                state = cursor_state(
                    page.next_cursor,
                    max_modified=_iso(newest),
                    watermark_ids=sorted(newest_ids),
                    offset_paged=hidden,
                )
                row = CursorRow(
                    module, row.last_pulled_at, state, row.initial_done_at,
                    row.last_reconciled_at,
                )
            else:
                complete_at = _now()
                row = CursorRow(
                    module,
                    None if hidden else _later(row.last_pulled_at, newest),
                    {
                        "in_progress": False,
                        "offset_paged": hidden,
                        "watermark_ids": [] if hidden else sorted(newest_ids),
                    },
                    row.initial_done_at or complete_at,
                    complete_at if (fresh or first_pass) else row.last_reconciled_at,
                )
            await _save_cursor(ctx, db, row, "ok" if last_page else "partial")
        ctx.cursors[module] = row
        if last_page:
            break
        cursor = page.next_cursor  # type: ignore[assignment]

    if walked is not None:
        await _drop_missing(ctx, entity, table, walked)


async def _drop_missing(ctx: _Ctx, entity: str, table: str, walked: set[str]) -> None:
    """Delete the local rows whose id the provider no longer lists (reconcile).

    A walk that found nothing, while rows exist here, is not trusted: a provider
    fault looks the same, and a delete cannot be undone.
    """
    kind_clause = " AND type = :type" if table == "crm_activities" else ""
    params: dict[str, Any] = {"org": ctx.org, "src": EXT_SOURCE}
    if kind_clause:
        params["type"] = ACTIVITY_TYPES[entity]
    async with ctx.page() as db:
        local = {
            str(r.ext_id) for r in (await db.execute(
                text(
                    f"SELECT ext_id FROM {table} WHERE organization_id = "
                    f"CAST(:org AS uuid) AND ext_source = :src{kind_clause}"
                ),
                params,
            )).fetchall()
        }
        missing = local - walked
        if local and not walked:
            _log.warning("crm.sync.reconcile_skipped entity=%s reason=empty_walk", entity)
            return
        if missing:
            removed = await delete_from_source(db, table, EXT_SOURCE, sorted(missing))
            ctx.bump(entity, "deleted", removed)


async def _deleted_pass(ctx: _Ctx, entity: str) -> None:
    """Read the deleted list since the cursor, and delete those rows here."""
    module = deleted_module(entity)
    row = _row(ctx, module)
    saved = row.state
    resumed = bool(saved and saved.get("in_progress"))
    if resumed and saved is not None:
        cursor = cursor_from(saved)
        window = _parse(saved.get("window_start")) or ctx.started - DELETED_MARGIN
    else:
        cursor = SourceCursor(since=row.last_pulled_at)
        window = ctx.started - DELETED_MARGIN
    table = _table(entity)
    while True:
        page = await ctx.call(lambda s, c=cursor: s.list_deleted(entity, c))
        last_page = page.next_cursor is None
        async with ctx.page() as db:
            removed = await delete_from_source(
                db, table, EXT_SOURCE, [r.ext_id for r in page.records],
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
                    module, window, {"in_progress": False},
                    row.initial_done_at or _now(), row.last_reconciled_at,
                )
            await _save_cursor(ctx, db, row, "ok" if last_page else "partial")
        ctx.cursors[module] = row
        if last_page:
            break
        cursor = page.next_cursor  # type: ignore[assignment]


async def _start_deleted_windows(ctx: _Ctx) -> None:
    """On the first import, mark where the deleted lists start.

    An import mirrors what exists, so a delete before the import is nothing to
    undo. The window opens at the start of the import: a record deleted after
    this moment is read by the first incremental run.
    """
    missing = [e for e in ENTITY_ORDER if deleted_module(e) not in ctx.cursors]
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
    if ctx.budget_total is None:
        ctx.budget_total = max(DAILY_CREDIT_BUDGET - used, 0)
    if ctx.kind == runs.KIND_IMPORT:
        await _start_deleted_windows(ctx)
        if ctx.estimate is None:
            await _estimate(ctx)
    if ctx.kind in (runs.KIND_IMPORT, runs.KIND_RECONCILE):
        await _field_defs(ctx)
    for entity in ENTITY_ORDER:
        await _changed_pass(ctx, entity)
    if ctx.kind != runs.KIND_IMPORT:
        for entity in ENTITY_ORDER:
            await _deleted_pass(ctx, entity)


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
            exc, NeedsReconnect | BudgetExhausted | Stopped | NotConnected,
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
    "SLUG_ORDER",
    "SLUGS",
    "NotConnected",
    "RunHandle",
    "RunResult",
    "Stopped",
    "SyncRunning",
    "acquire",
    "error_code",
    "execute",
    "plan",
    "run_org",
]
