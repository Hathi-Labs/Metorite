"""CRM · activities — the timeline: notes, calls, meetings, follow-up tasks.

Spec: ``project-docs/specs/crm_app.md`` §3.8, §4 (``activities.py`` row).

    GET    /crm/<entity>/{id}/timeline     activities + status changes, merged
    POST   /crm/<entity>/{id}/activities   log a note / call / meeting / task
    PATCH  /crm/activities/{aid}           edit a note, complete a task
    DELETE /crm/activities/{aid}

A deal's timeline **unions its originating lead's history**, labelled with the
source, because everything said before conversion was said about this deal —
losing it at the conversion boundary is the failure the ``lead_id`` provenance
column exists to prevent (§5.3).

Email threads are the third source (WS-26d-email), joined by address with no
link table (D-CRM-5). ⚠️ **That join is CALLER-scoped, never record-scoped**:
the CRM is org-visible to every ``feature:crm`` holder (D-CRM-3) while a
mailbox belongs to one person, so the timeline shows email *the caller can
already read*, not email *the record has*. Two holders opening the same lead
may legitimately see different threads, and a holder with no mailbox sees
none. That is why :func:`_timeline` takes the caller.

Paths are written out per entity for the same reason as ``records.py``: a
generic ``/crm/{entity}/{id}/…`` template would make route registration order
load-bearing, and ``PATCH /crm/activities/{aid}`` sits at the depth it would
compete with.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.crm.core import (
    ACTIVITY_TYPES,
    CONTACTS,
    DEALS,
    LEADS,
    ORGANIZATIONS,
    TABLE_BY_ACTIVITY_TARGET,
    ActivityModel,
    Entity,
    _tenant_session,
    actor,
    assert_writable,
    bump_last_activity,
    current_tenant,
    insert_row,
    require_row,
    router,
    row_to_dict,
    update_row,
    wire,
)
from pydantic import BaseModel
from sqlalchemy import text

#: Types a human may log through this API. ``status_change`` is deliberately
#: NOT here: the pipeline writes those and a hand-written one would be a
#: funnel event with no transition behind it.
LOGGABLE_TYPES: tuple[str, ...] = ("note", "call", "meeting", "task")


class ActivityIn(BaseModel):
    type: str = "note"
    subject: str | None = None
    body: str | None = None
    occurred_at: str | None = None
    due_at: str | None = None
    meta: dict | None = None


class ActivityPatch(BaseModel):
    subject: str | None = None
    body: str | None = None
    occurred_at: str | None = None
    due_at: str | None = None
    #: Sent as an ISO instant to complete a task, or ``null`` to reopen it.
    completed_at: str | None = None


class TimelineEntry(BaseModel):
    """One merged timeline row. ``kind`` says which source it came from."""

    kind: str
    at: str | None = None
    #: 'own' for the record's own history, 'lead' for a deal inheriting its
    #: lead's. The UI labels inherited entries rather than pretending they
    #: happened to the deal.
    origin: str = "own"
    activity: dict | None = None
    status_change: dict | None = None
    #: The email thread's display row. Every ``kind`` carries its OWN payload
    #: field — a third kind with no payload of its own renders an empty line,
    #: and the browser's `entry.activity!` would read the wrong one. Mirrored
    #: by `EmailThread` in `src/app/crm/lib/types.ts`, whose union is closed
    #: so tsc fails if only one side of this pair is updated.
    email_thread: dict | None = None


class TimelineResponse(BaseModel):
    entries: list[TimelineEntry]
    #: Set only when the caller pages (``offset``): whether an older entry
    #: exists past this page. The unpaged read leaves it out.
    has_more: bool | None = None


#: The deepest ``offset`` a timeline page may start at. Each source reads
#: ``offset + limit + 1`` rows, so this bounds one request's work.
MAX_TIMELINE_OFFSET = 5000

#: The tie-break between two sources at the same instant. Any fixed order
#: works. It only has to be the same on every page.
_KIND_RANK = {"activity": 0, "status_change": 1, "email_thread": 2}


def _entry_key(entry: TimelineEntry) -> tuple[bool, datetime | str, int, str]:
    """The ONE total order of a paged timeline, newest first under reverse.

    The instant, then the source, then the row's own id. Every source query
    in paged mode orders by the same instant and id (``strict``), so the
    first ``n`` merged rows always come from the first ``n`` rows of each
    source. ``at`` is None last.
    """
    payload = entry.activity or entry.status_change or entry.email_thread or {}
    row_id = str(payload.get("message_id") or payload.get("id") or "")
    if not entry.at:
        return (False, "", _KIND_RANK.get(entry.kind, 9), row_id)
    return (True, datetime.fromisoformat(entry.at), _KIND_RANK.get(entry.kind, 9), row_id)


def merge_page(
    entries: list[TimelineEntry], offset: int, limit: int,
) -> tuple[list[TimelineEntry], bool]:
    """One page of the merged timeline, and whether another one exists.

    ⚠️ Paged mode has NO email budget. The budget (:func:`_email_budget`)
    reserves half of the unpaged read for the record's own history, and it
    makes each page a different mix: the rows of a larger read do not begin
    with the rows of a smaller one, so a page 2 cut from a bigger read
    repeats notes and skips mail. A page here is a plain slice of one order.
    """
    ordered = sorted(entries, key=_entry_key, reverse=True)
    return ordered[offset:offset + limit], len(ordered) > offset + limit


# ── Shared behaviour ────────────────────────────────────────────────────────

async def _timeline(
    entity: Entity, record_id: str, limit: int, user: UserContext,
    offset: int | None = None,
) -> TimelineResponse:
    """The merged timeline. ``user`` is required because one of the three
    sources — email — is scoped to the CALLER's mailboxes, not to the record.

    With ``offset`` the read is a PAGE of one total order (:func:`merge_page`),
    and each source reads ``offset + limit + 1`` rows. Without it the read is
    the unpaged one the CRM panel draws, with the email budget."""
    # A direct call (the hermetic suites) passes no offset, and the default is
    # then FastAPI's `Query` object, not None.
    if not isinstance(offset, int) or isinstance(offset, bool):
        offset = None
    strict = offset is not None
    want = (offset or 0) + limit + 1 if strict else limit
    async with _tenant_session() as db:
        record = await require_row(db, entity.table, record_id, entity.label)
        sources: list[tuple[Entity, str, str]] = [(entity, record_id, "own")]
        inherited = getattr(record, "lead_id", None)
        if inherited:
            # §5.3 — the deal inherits the lead it was converted from.
            sources.append((LEADS, str(inherited), "lead"))

        entries: list[TimelineEntry] = []
        for source_entity, source_id, origin in sources:
            entries.extend(await _activity_entries(
                db, source_entity, source_id, origin, want, strict=strict))
            entries.extend(await _status_entries(
                db, source_entity, source_id, origin, want, strict=strict))
        # Email is resolved ONCE for the whole record rather than per source:
        # a deal's addresses already include its originating lead's, so running
        # it per source would return every inherited thread twice.
        entries.extend(await _email_entries(
            db,
            await _record_addresses(db, entity, record_id, record),
            user,
            want if strict else _email_budget(limit),
            strict=strict,
        ))
        if strict:
            page, more = merge_page(entries, offset or 0, limit)
            return TimelineResponse(entries=page, has_more=more)
        # Newest first. `at` is None only for a row with no timestamp at all,
        # which sorts last rather than crashing the comparison.
        entries.sort(key=lambda e: (e.at or ""), reverse=True)
        return TimelineResponse(entries=entries[:limit])


#: The column of ``crm_activities`` that names a generic module record
#: (CRM-Q23a, migration 275). ``routes/crm/modules.py`` reads a module
#: record's timeline through it.
MODULE_ACTIVITY_COLUMN = "module_record_id"

#: The parent columns that :func:`activity_entries_by` may name: the four of
#: the entity registry and the module one. A column is a literal of this set,
#: never caller text.
ACTIVITY_PARENT_COLUMNS: frozenset[str] = frozenset(
    {*TABLE_BY_ACTIVITY_TARGET, MODULE_ACTIVITY_COLUMN},
)


async def _activity_entries(
    db: Any, entity: Entity, record_id: str, origin: str, limit: int,
    *, strict: bool = False,
) -> list[TimelineEntry]:
    return await activity_entries_by(
        db, entity.activity_column, record_id, origin, limit, strict=strict,
    )


async def activity_entries_by(
    db: Any, column: str, record_id: str, origin: str, limit: int,
    *, strict: bool = False,
) -> list[TimelineEntry]:
    """The activities whose parent ``column`` is *record_id*, as timeline
    entries. ``column`` is one of :data:`ACTIVITY_PARENT_COLUMNS`."""
    if column not in ACTIVITY_PARENT_COLUMNS:
        raise ValueError(f"not an activity parent column: {column!r}")
    # Paged mode orders by the instant the entry SHOWS (`occurred_at`, else
    # `created_at`) and the id, the order `merge_page` merges by. The unpaged
    # read keeps its `created_at` order.
    order = ("COALESCE(occurred_at, created_at) DESC, id DESC" if strict
             else "created_at DESC")
    rows = (await db.execute(
        text(
            "SELECT * FROM crm_activities "
            f"WHERE {column} = CAST(:record_id AS uuid) "
            f"ORDER BY {order} LIMIT :limit"
        ),
        {"record_id": record_id, "limit": limit},
    )).fetchall()
    return [
        TimelineEntry(
            kind="activity",
            at=wire(getattr(row, "occurred_at", None) or row.created_at),
            origin=origin,
            activity=row_to_dict(row, ActivityModel),
        )
        for row in rows
    ]


async def _status_entries(
    db: Any, entity: Entity, record_id: str, origin: str, limit: int,
    *, strict: bool = False,
) -> list[TimelineEntry]:
    """Status changes are merged in from their own log, not re-read from the
    activity that mirrors them: the log carries ``dwell_seconds``, which is the
    part a human actually reads off a timeline ("sat in Proposal for 11 days")."""
    from gateway.routes.crm.pipeline import ENTITY_TYPES

    entity_type = ENTITY_TYPES.get(entity.slug)
    if entity_type is None:
        return []
    rows = (await db.execute(
        text(
            "SELECT * FROM crm_status_changes "
            "WHERE entity_type = :entity_type "
            "AND entity_id = CAST(:record_id AS uuid) "
            + ("ORDER BY changed_at DESC NULLS LAST, id DESC LIMIT :limit" if strict
               else "ORDER BY changed_at DESC LIMIT :limit")
        ),
        {"entity_type": entity_type, "record_id": record_id, "limit": limit},
    )).fetchall()
    return [
        TimelineEntry(
            kind="status_change",
            at=wire(row.changed_at),
            origin=origin,
            status_change={
                "id": str(row.id),
                "from_status": row.from_status,
                "to_status": row.to_status,
                "changed_by": row.changed_by,
                "changed_at": wire(row.changed_at),
                "dwell_seconds": (
                    int(row.dwell_seconds)
                    if getattr(row, "dwell_seconds", None) is not None else None
                ),
            },
        )
        for row in rows
    ]


# ── Email threads — the third source ────────────────────────────────────────

def _email_budget(limit: int) -> int:
    """How many email threads may compete for a timeline of ``limit`` rows.

    **Half, rounded up — and the halving is the whole point.** Bounding each
    source at ``limit`` is NOT enough on its own: the merge sorts newest-first
    and then truncates to ``limit``, so ``limit`` recent threads evict every
    activity and every status change the record has. That is the common case,
    not the pathological one: the CRM panel draws one unpaged read, and twenty
    recent emails is an ordinary week on an active deal.

    The budget applies to the unpaged read only. The CRM agent's
    ``get_timeline`` pages with ``offset``, and a paged read has no budget
    (:func:`merge_page` says why). Its hint line leads to the older history.

    Rounded UP so a limit of 1 still admits email, and so the reserved half is
    the one that can be empty: a record with no activity at all still fills the
    page with mail, because the other sources simply return fewer rows and the
    truncation takes whatever is there.
    """
    return (limit + 1) // 2


def _email_account_scope(account_id: str | None, params: dict[str, Any]) -> str:
    """Return a SQL fragment scoping email_messages `em` to the user's accounts.

    Adds :uid (and optionally :aid) to `params`. The caller must have already
    set params["uid"] to the user's email.

    ⚠️ **A verbatim copy of ``routes/email/core.py``'s ``_account_scope``**, and
    the copy is deliberate (D-CRM-4, §9 WS-26d-email). Importing another route
    package's private helper is the cross-app coupling this package already
    declined once (``broker_handlers.broker_gate``); promoting it to a shared
    module is correct eventually but drags ``routes/email``'s callers into a
    WS-26 PR. Two copies is a coincidence — **if this predicate is copied a
    THIRD time anywhere, promote it instead of copying it again.**

    It hardcodes the alias ``em`` (``email/automation/analytics.py`` had to
    ``.replace()`` it), so the query below aliases ``email_messages`` as ``em``
    and the fragment drops in unchanged.
    """
    frag = "em.account_id IN (SELECT id FROM email_accounts WHERE user_id = :uid"
    if account_id:
        frag += " AND id = :aid"
        params["aid"] = account_id
    frag += ")"
    return frag


async def _record_addresses(
    db: Any, entity: Entity, record_id: str, record: Any,
) -> dict[str, str]:
    """The addresses this record reaches email by → the origin label each earns.

    Organizations, contacts and leads carry their own ``email`` column.
    ``crm_deals`` deliberately does not (§3.4): a deal reaches email through its
    originating ``lead_id`` and through the contacts linked to it, and v1 takes
    **both, unioned** — the same union ``_timeline`` already applies to activity
    history. Organizations do NOT join by email domain: an ``@fracktal.in``
    match would attach the whole company mailbox to our own org record.

    Lead addresses are collected first so that an address that is BOTH the
    lead's and a linked contact's ends up labelled ``own`` — the contact is the
    deal's own, and the more specific label is the truer one.
    """
    origins: dict[str, str] = {}

    def remember(value: Any, origin: str) -> None:
        address = str(value or "").strip().lower()
        if address:
            origins[address] = origin

    if entity is not DEALS:
        remember(getattr(record, "email", None), "own")
        return origins

    inherited = getattr(record, "lead_id", None)
    if inherited:
        rows = (await db.execute(
            text("SELECT email FROM crm_leads WHERE id = CAST(:record_id AS uuid)"),
            {"record_id": str(inherited)},
        )).fetchall()
        for row in rows:
            remember(getattr(row, "email", None), "lead")

    rows = (await db.execute(
        text(
            "SELECT c.email AS email FROM crm_deal_contacts dc "
            "JOIN crm_contacts c ON c.id = dc.contact_id "
            "WHERE dc.deal_id = CAST(:record_id AS uuid)"
        ),
        {"record_id": record_id},
    )).fetchall()
    for row in rows:
        remember(getattr(row, "email", None), "own")
    return origins


async def _email_entries(
    db: Any, origins: dict[str, str], user: UserContext, limit: int,
    *, strict: bool = False,
) -> list[TimelineEntry]:
    """Email threads from these addresses, in the CALLER's own mailboxes.

    **The unit is the thread, not the message.** There is no ``email_threads``
    table; ``email_messages.thread_id`` is the grouping column, and the
    conversation is already the unit of classification and snooze across the
    email app. ``DISTINCT ON`` keeps the newest message per
    ``(account_id, thread)`` for the display row — a row-per-message timeline
    double-counts every conversation. A message with a NULL ``thread_id``
    stands for itself (``COALESCE(…, em.id::text)``, the contact card's rule):
    without it Postgres would treat every un-threaded message in an account as
    one group and collapse them into a single entry.

    ``email_thread_status`` is LEFT-joined on its own composite key for the
    badge; a thread nobody classified simply has no status.

    ⚠️ ``limit`` here is the **email budget**, not the caller's limit — see
    :func:`_email_budget`. The caller's limit is what the MERGED list is
    truncated to, so bounding this source at the same number would let a chatty
    mailbox evict the record's entire history and still be "bounded".
    """
    if not origins:
        # No address means no query — an empty IN list is a syntax error, and
        # "match nothing" must never degrade into "match the whole mailbox".
        return []

    params: dict[str, Any] = {"uid": user.email or "anonymous", "limit": limit}
    scope = _email_account_scope(None, params)
    placeholders: list[str] = []
    for index, address in enumerate(sorted(origins)):
        key = f"addr_{index}"
        params[key] = address
        placeholders.append(f":{key}")

    rows = (await db.execute(
        text(
            "SELECT * FROM ("
            " SELECT DISTINCT ON (em.account_id, COALESCE(em.thread_id, em.id::text))"
            " em.id AS message_id,"
            " em.account_id AS account_id,"
            " COALESCE(em.thread_id, em.id::text) AS thread_id,"
            " em.subject AS subject,"
            " em.snippet AS snippet,"
            " em.folder AS folder,"
            " em.from_address->>'name' AS from_name,"
            " em.from_address->>'email' AS from_email,"
            " em.received_at AS received_at,"
            " ts.status AS thread_status"
            " FROM email_messages em"
            " LEFT JOIN email_thread_status ts"
            " ON ts.account_id = em.account_id AND ts.thread_id = em.thread_id"
            f" WHERE {scope}"
            f" AND LOWER(em.from_address->>'email') IN ({', '.join(placeholders)})"
            " ORDER BY em.account_id, COALESCE(em.thread_id, em.id::text),"
            " em.received_at DESC"
            + (") t ORDER BY received_at DESC NULLS LAST, message_id DESC LIMIT :limit"
               if strict else ") t ORDER BY received_at DESC LIMIT :limit")
        ),
        params,
    )).fetchall()

    return [
        TimelineEntry(
            kind="email_thread",
            at=wire(row.received_at),
            origin=origins.get(str(row.from_email or "").lower(), "own"),
            email_thread={
                "thread_id": str(row.thread_id),
                "account_id": wire(row.account_id),
                "message_id": wire(row.message_id),
                "subject": row.subject,
                "snippet": row.snippet,
                "folder": row.folder,
                "from_name": row.from_name,
                "from_email": row.from_email,
                "received_at": wire(row.received_at),
                "status": row.thread_status,
            },
        )
        for row in rows
    ]


async def _log_activity(
    entity: Entity, record_id: str, payload: ActivityIn, user: UserContext,
) -> dict:
    if payload.type not in LOGGABLE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot log an activity of type '{payload.type}'. "
                f"One of: {list(LOGGABLE_TYPES)}. "
                "('status_change' and 'system' are written by the platform.)"
            ),
        )
    async with _tenant_session() as db:
        await require_row(db, entity.table, record_id, entity.label)
        # `entity.activity_column` is one of four registry literals, so the
        # CHECK requiring at least one target cannot be reached with all four
        # NULL through this path — the target is structural, not optional.
        row = await insert_row(db, "crm_activities", {
            "type": payload.type,
            "subject": payload.subject,
            "body": payload.body,
            "occurred_at": payload.occurred_at,
            "due_at": payload.due_at,
            "meta": payload.meta,
            "created_by": actor(user),
            entity.activity_column: record_id,
        })
        await bump_last_activity(db, entity.table, record_id)
        return row_to_dict(row, ActivityModel)


# ── Timelines ───────────────────────────────────────────────────────────────

@router.get("/leads/{record_id}/timeline", response_model=TimelineResponse)
async def lead_timeline(
    record_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    offset: int | None = Query(None, ge=0, le=MAX_TIMELINE_OFFSET),
) -> TimelineResponse:
    return await _timeline(LEADS, record_id, limit, user, offset)


@router.get("/deals/{record_id}/timeline", response_model=TimelineResponse)
async def deal_timeline(
    record_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    offset: int | None = Query(None, ge=0, le=MAX_TIMELINE_OFFSET),
) -> TimelineResponse:
    """A deal's own history, unioned with its originating lead's (§5.3)."""
    return await _timeline(DEALS, record_id, limit, user, offset)


@router.get("/contacts/{record_id}/timeline", response_model=TimelineResponse)
async def contact_timeline(
    record_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    offset: int | None = Query(None, ge=0, le=MAX_TIMELINE_OFFSET),
) -> TimelineResponse:
    return await _timeline(CONTACTS, record_id, limit, user, offset)


@router.get("/organizations/{record_id}/timeline", response_model=TimelineResponse)
async def organization_timeline(
    record_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
    offset: int | None = Query(None, ge=0, le=MAX_TIMELINE_OFFSET),
) -> TimelineResponse:
    return await _timeline(ORGANIZATIONS, record_id, limit, user, offset)


# ── Logging ─────────────────────────────────────────────────────────────────

@router.post("/leads/{record_id}/activities", status_code=201)
async def log_lead_activity(
    record_id: str, payload: ActivityIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await _log_activity(LEADS, record_id, payload, user)


@router.post("/deals/{record_id}/activities", status_code=201)
async def log_deal_activity(
    record_id: str, payload: ActivityIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await _log_activity(DEALS, record_id, payload, user)


@router.post("/contacts/{record_id}/activities", status_code=201)
async def log_contact_activity(
    record_id: str, payload: ActivityIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await _log_activity(CONTACTS, record_id, payload, user)


@router.post("/organizations/{record_id}/activities", status_code=201)
async def log_organization_activity(
    record_id: str, payload: ActivityIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await _log_activity(ORGANIZATIONS, record_id, payload, user)


# ── Editing one activity ────────────────────────────────────────────────────

@router.patch("/activities/{activity_id}")
async def patch_activity(
    activity_id: str, payload: ActivityPatch,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Edit a note or complete a follow-up task.

    ``type`` is deliberately not editable: a call that becomes a note rewrites
    history, and re-logging is one request.
    """
    values = payload.model_dump(exclude_unset=True)
    async with _tenant_session() as db:
        row = await require_row(db, "crm_activities", activity_id, "Activity")
        if row.type in ("status_change", "system"):
            # Same guard as delete_activity: one rule, both verbs. An edited
            # status_change would disagree with crm_status_changes with no way
            # to tell which one is lying.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Platform-written activities cannot be edited — a funnel "
                    "with editable history is not a record of anything."
                ),
            )
        if not values:
            return row_to_dict(row, ActivityModel)
        # `crm_activities` has no `updated_at` — an activity is a log entry.
        row = await update_row(
            db, "crm_activities", activity_id, values, touch=False,
        )
        return row_to_dict(row, ActivityModel)


@router.delete("/activities/{activity_id}")
async def delete_activity(
    activity_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Delete one timeline entry.

    ⚠️ **No Zoho tombstone, deliberately (WS-26b, §7.1).** Records tombstone
    their deletes so the sync propagates them; activities do not, in either
    direction — a note deleted here survives in Zoho, and one deleted in Zoho
    survives here. Accepted v1 cost: an activity is an append-mostly log entry
    whose stale copy misleads nobody about the pipeline, and Zoho is being
    retired. Do not "fix" one direction alone — a native tombstone without the
    matching Zoho→native delete would make the two sides disagree in a new way.
    """
    async with _tenant_session() as db:
        row = await require_row(db, "crm_activities", activity_id, "Activity")
        if row.type in ("status_change", "system"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Platform-written activities cannot be deleted — a funnel "
                    "with editable history is not a record of anything."
                ),
            )
        await assert_writable(
            db, current_tenant(), "crm_activities", None, row=row,
        )
        await db.execute(
            text("DELETE FROM crm_activities WHERE id = CAST(:id AS uuid)"),
            {"id": activity_id},
        )
        return {"deleted": activity_id}


__all__ = [
    "ACTIVITY_PARENT_COLUMNS",
    "ACTIVITY_TYPES",
    "LOGGABLE_TYPES",
    "MODULE_ACTIVITY_COLUMN",
    "ActivityIn",
    "ActivityPatch",
    "TimelineEntry",
    "TimelineResponse",
    "activity_entries_by",
]
