"""CRM · pipeline — status transitions, the kanban board, and lead conversion.

Spec: ``project-docs/specs/crm_app.md`` §3.6, §3.7, §3.9, §4.

    GET  /crm/pipeline               deals grouped by status: per-lane rows,
                                     count and ₹ total
    POST /crm/leads/{id}/convert     §3.7 — contact + organization + deal, with
                                     the one dedup rule and 409 on re-convert

:func:`apply_status_transition` lives here rather than in ``records.py`` because
a status change is the pipeline's semantics, not a field write: it produces
**three** effects and a set of column updates, and ``records.py``'s ``PATCH``
calls it whenever a body moves ``status_id``. The three effects are
non-negotiable — a transition that writes only the new ``status_id`` silently
empties the funnel report the whole model exists to make free.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from gateway.routes.crm.core import (
    CLOSING_TYPES,
    CONTACTS,
    DEALS,
    LEADS,
    MAX_PAGE_SIZE,
    ORGANIZATIONS,
    WEIGHTED_SQL,
    WEIGHTED_TYPES,
    DealModel,
    Entity,
    StatusModel,
    _tenant_session,
    actor,
    has_column,
    insert_row,
    link_deal_contact,
    load_default_status,
    load_row,
    now,
    project_joined,
    record_activity,
    require_row,
    router,
    row_to_dict,
    status_wire,
    update_row,
)
from pydantic import BaseModel
from sqlalchemy import text

#: Entity slug → the ``crm_status_changes.entity_type`` vocabulary. Two rows,
#: written out, because the CHECK constraint only accepts these two.
ENTITY_TYPES: dict[str, str] = {"leads": "lead", "deals": "deal"}


# ── Status transitions ──────────────────────────────────────────────────────

async def _dwell_since(db: Any, entity: Entity, record: Any) -> Any:
    """When the record entered the status it is leaving.

    Deals carry ``status_changed_at`` (§3.4's stage-age clock) and answer this
    without a query. Leads have no such column by design, so the answer comes
    from the change log itself, falling back to ``created_at`` for the very
    first transition. Both paths mean the same thing: dwell is time-in-status,
    not time-since-creation.
    """
    stamped = getattr(record, "status_changed_at", None)
    if stamped is not None:
        return stamped
    last = (await db.execute(
        text(
            "SELECT max(changed_at) FROM crm_status_changes "
            "WHERE entity_type = :entity_type "
            "AND entity_id = CAST(:entity_id AS uuid)"
        ),
        {"entity_type": ENTITY_TYPES[entity.slug], "entity_id": str(record.id)},
    )).scalar()
    return last or getattr(record, "created_at", None)


async def apply_status_transition(
    db: Any,
    entity: Entity,
    record: Any,
    *,
    new_status_id: str,
    patch: dict[str, Any],
    actor_email: str,
) -> dict[str, Any]:
    """Move a lead or deal into a new status. Returns the column updates to apply.

    Three effects, always, in this order:

    1. a ``crm_status_changes`` row carrying ``from``/``to``/``changed_by`` and
       the ``dwell_seconds`` the record spent in the status it is leaving;
    2. a ``status_change`` activity, so the move shows up in the timeline
       alongside the notes and calls that explain it;
    3. the record's own ``status_changed_at`` re-stamped (deals only — leads
       have no stage-age clock).

    Refuses (422) a move into a ``lost``-type status with no lost reason: "why
    did we lose it" is the whole reason the lost vocabulary exists, and a
    nullable column with no gate collects blanks. Entering ``won``/``lost``
    stamps ``closed_at``; a deal with a NULL probability inherits the new
    status's default.

    The caller merges the returned dict into its own single UPDATE, so a PATCH
    that changes status *and* other fields stays one statement.
    """
    if not entity.status_table:
        raise HTTPException(
            status_code=422, detail=f"{entity.slug} has no status pipeline.",
        )
    status = await require_row(db, entity.status_table, new_status_id, "Status")

    lost_reason = patch.get("lost_reason_id", getattr(record, "lost_reason_id", None))
    if status.type == "lost" and not lost_reason:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{status.name}' is a lost-type status: a lost_reason_id is "
                "required to move a record into it."
            ),
        )
    _require_entry_fields(status, record, patch)

    previous_id = getattr(record, "status_id", None)
    previous = (
        await load_row(db, entity.status_table, str(previous_id))
        if previous_id else None
    )
    changed_at = now()
    since = await _dwell_since(db, entity, record)
    await insert_row(db, "crm_status_changes", {
        "entity_type": ENTITY_TYPES[entity.slug],
        "entity_id": str(record.id),
        "from_status": getattr(previous, "name", None),
        "to_status": status.name,
        "changed_by": actor_email,
        "changed_at": changed_at,
        "dwell_seconds": _dwell_seconds(since, changed_at),
    })
    await record_activity(
        db,
        activity_type="status_change",
        created_by=actor_email,
        target_column=entity.activity_column,
        target_id=str(record.id),
        subject=f"{getattr(previous, 'name', None) or '—'} → {status.name}",
    )

    updates: dict[str, Any] = {"status_id": new_status_id}
    if has_column(entity, "status_changed_at"):
        updates["status_changed_at"] = changed_at
    if status.type in CLOSING_TYPES and has_column(entity, "closed_at"):
        updates["closed_at"] = changed_at
    if has_column(entity, "probability"):
        chosen = patch.get("probability", getattr(record, "probability", None))
        if chosen is None and getattr(status, "probability", None) is not None:
            updates["probability"] = status.probability
    return updates


class _NoExistingRecord:
    """The "there is no stored row" shape :func:`_require_entry_fields` accepts.

    A distinct object rather than ``None``, because ``getattr(None, field,
    None)`` also answers ``None``: handing the gate ``None`` would make the
    CREATE path work by coincidence and read, forever after, as somebody having
    forgotten to pass the record. A caller that has no row has to SAY so.

    ``__slots__`` is empty on purpose — the sentinel is a module-level
    singleton, and one that could be given an attribute would be a global
    somebody could make answer a requirement.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — a debugging aid
        return "NO_EXISTING_RECORD"


#: Pass as ``record`` when the write CREATES the row rather than moving one —
#: ``records._resolve_status`` is the only caller (WS-26h2 / D-CRM-13).
NO_EXISTING_RECORD = _NoExistingRecord()


def _require_entry_fields(
    status: Any, record: Any | _NoExistingRecord, patch: dict[str, Any],
) -> None:
    """WS-26h — 422 unless the deal carries everything the TARGET stage demands.

    The generalisation of the lost-reason rule directly above, and deliberately
    shaped the same way: refused **before** any of the transition's three
    effects, naming what is missing, and satisfied by the SAME PATCH that moves
    the record. "Move it and fill this in" would be two requests, and the half
    that lands is the one that moved the deal.

    Three properties worth stating, because each is a decision:

    **Entry-only.** This is reached from :func:`apply_status_transition`, which
    ``records.py`` calls only when a body actually moves ``status_id``. A deal
    already sitting in a stage it no longer satisfies is never blocked from
    being edited — including from being edited towards satisfying it. A rule
    that made a lane un-editable would be a lock, and §5.1 chose visibility
    over locks.

    **The patch counts.** ``patch`` is the caller's own body, so
    ``{"status_id": …, "amount": 400000}`` satisfies an ``amount`` requirement
    in one statement. Read through the same "payload, else row" idiom
    ``admin._effective`` uses, for the same reason: what matters is the state
    the write LEAVES, not what was stored a moment ago. On a CREATE there is no
    row to fall back to, which is a case the caller STATES by passing
    :data:`NO_EXISTING_RECORD` rather than one this function infers.

    **Empty is not missing-but-fine.** ``""`` and ``0`` are treated
    differently on purpose: a blank string is an unfilled text box, while a
    genuine zero ``amount`` is a number somebody typed. Only ``None`` and
    blank/whitespace text count as absent.

    Stages with no requirements — every stage until somebody sets one, and
    every LEAD status, whose table has no such column — return immediately.

    ⚠️ **CREATE reaches this gate from ONE of its THREE paths, and the other
    two must stay open** (WS-26h2, and the rule is **D-CRM-13**: entry
    requirements gate the stage a caller CHOSE, never the stage the server
    DEFAULTED to — a deal in the default lane has claimed nothing yet, so
    demanding proof of progress to enter it is a category error).

    1. ``POST /crm/<entity>`` **with an explicit ``status_id``** →
       ``records._resolve_status`` calls this with :data:`NO_EXISTING_RECORD`.
       **Gated.** Creating straight into a late stage by API *is* a claim.
    2. ``POST /crm/<entity>`` with **no** ``status_id`` (the server defaults the
       lane), and the convert path's :func:`_create_deal`, which always calls
       ``load_default_status`` and has no ``status_id`` field at all →
       **ungated, deliberately.** ``QuickCreateModal`` sends no stage and
       ``ConvertModal`` offers no organization picker, so gating the defaulted
       lane would 422 quick-create and *every* lead conversion the moment an
       owner put a requirement on it — which ``admin.py`` permits unrestricted.
    3. ``import_zoho.apply_record`` → ``core.upsert_by_zoho_id``, reached by the
       backfill route **and by ``sync_zoho.pull_phase`` — the ENABLED 600s
       production loop** → **ungated, and it must stay that way.** It builds its
       own ``INSERT … ON CONFLICT (zoho_id)`` and calls neither
       ``core.insert_row`` nor ``records.create_record``. A pulled deal carries
       whatever stage Zoho has it in and owes nothing to a requirement set here
       afterwards; a gate on that path would start refusing rows from the live
       upstream tenant on the next cycle after any settings-grid save, which is
       an OWNER-GATE change to a running loop (``work_plan.md`` §6 WS-26 (a)).
       ``test_crm_pipeline.py`` fences this **twice**, both over the AST of
       ``routes/crm/*.py``: the gate's call sites are exactly ``pipeline.py`` +
       ``records.py`` (aliases and module-attribute forms resolved; a comment
       naming it is not a call), and no static call path from
       ``sync_zoho.pull_phase`` / ``import_zoho.apply_module`` reaches it. The
       second fence is the load-bearing one — routing ``apply_record`` through
       ``records._resolve_status`` adds no call site at all, and this function
       is reached with ``chosen`` truthy because ``apply_record`` already sets
       ``values["status_id"]`` itself.

    ⚠️ This paragraph previously said CREATE was not gated **at all** and
    counted the ungated create paths as **two**. Both were wrong — path 3 was
    missing. Corrected under WS-26h2; the same undercount in ``crm_app.md`` was
    struck in the commit that recorded D-CRM-13.
    """
    if record is None:
        # Not defensive noise: `getattr(None, field, None)` answers None for
        # every field, so a miswired caller would silently refuse a move it
        # should have allowed. "No row" has a name; None means somebody lost
        # the record on the way here.
        raise TypeError(
            "_require_entry_fields takes a record or NO_EXISTING_RECORD, "
            "never None."
        )

    def stated(field: str) -> Any:
        """"Payload, else row" — with "there is no row" as a stated case."""
        if field in patch:
            return patch[field]
        if record is NO_EXISTING_RECORD:
            return None
        return getattr(record, field, None)

    required = getattr(status, "required_fields", None) or []
    missing = [field for field in required if _is_blank(stated(field))]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{status.name}' requires {missing} before a deal can enter "
                "it. Send the missing field(s) in the same request."
            ),
        )


def _is_blank(value: Any) -> bool:
    """Absent for the purposes of an entry requirement.

    ``0`` and ``False`` are values; ``None`` and whitespace-only text are not.
    A plain falsiness test here would let a stage requiring ``amount`` refuse a
    genuine ₹0 deal, which is a real thing a pipeline holds.
    """
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _dwell_seconds(since: Any, changed_at: Any) -> int | None:
    """Seconds between two instants, or None when we cannot honestly say.

    Returns None rather than 0 for the first transition: zero is a measurement
    and "we have no earlier timestamp" is not one.
    """
    if since is None:
        return None
    try:
        return max(0, int((changed_at - since).total_seconds()))
    except TypeError:
        # A naive/aware mismatch means the stored value cannot be compared to
        # our clock. An unusable dwell is better recorded as absent than as 0.
        return None


# ── The board ───────────────────────────────────────────────────────────────
#
# ⚠️ ``WEIGHTED_SQL`` used to be defined here. It moved to ``core.py`` beside
# ``WEIGHTED_TYPES`` when WS-26g's reports became its second consumer: the
# formula is read out of the statement text by ``_crm_fakes._WEIGHTED_SUM_RE``
# precisely so a drifted copy changes the tests' answer, and a second copy is
# exactly what defeats that. It is still importable from this module (it is in
# this namespace), so no existing caller had to move.


class PipelineLane(BaseModel):
    status: StatusModel
    rows: list[dict]
    count: int
    amount: float
    #: Sum of amount * probability/100 over this lane — 0 on a lane whose type is
    #: not in ``WEIGHTED_TYPES``, because won revenue is closed and a lost or
    #: on-hold deal forecasts nothing (D-CRM-10).
    weighted: float = 0.0


class PipelineResponse(BaseModel):
    lanes: list[PipelineLane]


@router.get("/pipeline", response_model=PipelineResponse)
async def get_pipeline(
    owner: str | None = None,
    per_lane: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    user: UserContext = Depends(get_current_user),
) -> PipelineResponse:
    """Deals grouped by status — the kanban read.

    Lanes are ``crm_deal_statuses`` ordered by ``position``, so an empty lane
    still renders (a kanban that hides its empty columns is a list). Count, ₹
    total and ₹ weighted are aggregated over the WHOLE lane, not over the page
    of rows returned, or the header would lie about a lane with more than
    ``per_lane`` deals in it.
    """
    async with _tenant_session() as db:
        lanes = (await db.execute(
            text("SELECT * FROM crm_deal_statuses ORDER BY position, name"), {},
        )).fetchall()
        where = "status_id = CAST(:status_id AS uuid)"
        params: dict[str, Any] = {}
        if owner:
            where += " AND lower(owner_email) = :owner"
            params["owner"] = owner.strip().lower()

        out: list[PipelineLane] = []
        for lane in lanes:
            scope = {
                **params,
                "status_id": str(lane.id),
                # The lane's own default is the inheritance fallback, bound
                # rather than interpolated like every other caller value here.
                "stage_probability": int(getattr(lane, "probability", 0) or 0),
            }
            agg = (await db.execute(
                text(
                    "SELECT count(*) AS count, COALESCE(SUM(amount), 0) AS amount, "
                    f"{WEIGHTED_SQL} FROM crm_deals WHERE {where}"
                ),
                scope,
            )).fetchone()
            inner = (
                f"SELECT * FROM crm_deals WHERE {where} "
                "ORDER BY status_changed_at DESC NULLS LAST, id DESC "
                "LIMIT :limit"
            )
            rows = (await db.execute(
                # Cards print the account name; the board must not make the
                # browser join a paged organization list to render them
                # (WS-26c dw 2).
                text(project_joined(
                    DEALS, inner,
                    "base.status_changed_at DESC NULLS LAST, base.id DESC",
                )),
                {**scope, "limit": per_lane},
            )).fetchall()
            out.append(PipelineLane(
                status=status_wire(lane),
                rows=[row_to_dict(r, DealModel) for r in rows],
                count=int(getattr(agg, "count", 0) or 0),
                amount=float(getattr(agg, "amount", 0) or 0),
                # The open/ongoing restriction is applied per LANE rather than
                # inside the SUM: a lane IS one status, so the filter is a
                # property of the lane and expressing it in the predicate would
                # be a WHERE clause that is either always true or always false.
                weighted=(
                    float(getattr(agg, "weighted", 0) or 0)
                    if getattr(lane, "type", "open") in WEIGHTED_TYPES else 0.0
                ),
            ))
        return PipelineResponse(lanes=out)


# ── Lead → deal conversion ──────────────────────────────────────────────────

class ConvertDeal(BaseModel):
    """The optional deal overrides a caller may supply to ``/convert``."""

    name: str | None = None
    amount: float | None = None
    currency: str | None = None
    expected_close_date: str | None = None
    owner_email: str | None = None
    next_step: str | None = None
    description: str | None = None


class ConvertRequest(BaseModel):
    #: Caller-chosen matches, resolved by the convert modal (§5 surface 4).
    #: Absent means "work it out": email for the contact, exact name for the org.
    contact_id: str | None = None
    company_id: str | None = None
    deal: ConvertDeal | None = None


class ConvertResponse(BaseModel):
    lead: dict
    contact: dict | None = None
    organization: dict | None = None
    deal: dict


@router.post("/leads/{lead_id}/convert", response_model=ConvertResponse)
async def convert_lead(
    lead_id: str,
    body: ConvertRequest | None = None,
    user: UserContext = Depends(get_current_user),
) -> ConvertResponse:
    """Turn a lead into contact + organization + deal (§3.7).

    Converting a lead whose deal still exists is a **409**, not a second deal:
    the conversion stamps are provenance, and a lead that produced two deals
    cannot say which one it became. The guard keys on ``converted_deal_id``,
    not ``converted_at`` (B6): deleting the deal SET-NULLs the link, and the
    lead becomes convertible again instead of being stranded forever.
    """
    body = body or ConvertRequest()
    who = actor(user)
    async with _tenant_session() as db:
        lead = await require_row(db, LEADS.table, lead_id, "Lead")
        if getattr(lead, "converted_deal_id", None):
            raise HTTPException(
                status_code=409,
                detail="This lead has already been converted.",
            )

        contact = await _resolve_contact(db, lead, body)
        organization = await _resolve_organization(db, lead, body)
        deal = await _create_deal(db, lead, body, contact, organization, who)
        lead = await _stamp_converted(db, lead, contact, organization, deal, who)

        return ConvertResponse(
            lead=row_to_dict(lead, LEADS.model),
            contact=(
                row_to_dict(contact, CONTACTS.model)
                if contact is not None else None
            ),
            organization=(
                row_to_dict(organization, ORGANIZATIONS.model)
                if organization is not None else None
            ),
            deal=row_to_dict(deal, DealModel),
        )


async def _resolve_contact(db: Any, lead: Any, body: ConvertRequest) -> Any | None:
    """Caller's choice → email match → create. The one dedup rule is email.

    Matching is on ``lower(email)`` because an address is the same person in
    any casing, and the import will bring in both spellings of it.
    """
    if body.contact_id:
        return await require_row(db, CONTACTS.table, body.contact_id, "Contact")

    email = (getattr(lead, "email", None) or "").strip()
    if email:
        existing = (await db.execute(
            text(
                "SELECT * FROM crm_contacts WHERE lower(email) = :email "
                "ORDER BY created_at LIMIT 1"
            ),
            {"email": email.lower()},
        )).fetchone()
        if existing is not None:
            return existing

    first = (getattr(lead, "first_name", None) or "").strip()
    return await insert_row(db, CONTACTS.table, {
        # `first_name` is NOT NULL; a lead with no person name still has a
        # display name, and using it beats inventing 'Unknown'.
        "first_name": first or lead.lead_name,
        "last_name": getattr(lead, "last_name", None) if first else None,
        "email": getattr(lead, "email", None),
        "phone": getattr(lead, "phone", None),
        "mobile": getattr(lead, "mobile", None),
        "owner_email": getattr(lead, "owner_email", None),
        "source": getattr(lead, "source", "manual"),
    })


async def _resolve_organization(
    db: Any, lead: Any, body: ConvertRequest,
) -> Any | None:
    """Caller's choice → exact-name match → create. Skipped when the lead has
    no ``organization_name``: an individual buyer is not a company, and minting
    an organization row named after a person is how a CRM fills with noise."""
    if body.company_id:
        return await require_row(
            db, ORGANIZATIONS.table, body.company_id, "Organization",
        )

    name = (getattr(lead, "organization_name", None) or "").strip()
    if not name:
        return None

    existing = (await db.execute(
        text(
            "SELECT * FROM crm_companies WHERE name = :name "
            "ORDER BY created_at LIMIT 1"
        ),
        {"name": name},
    )).fetchone()
    if existing is not None:
        return existing

    return await insert_row(db, ORGANIZATIONS.table, {
        "name": name,
        "website": getattr(lead, "website", None),
        "industry": getattr(lead, "industry", None),
        "no_of_employees": getattr(lead, "no_of_employees", None),
        "annual_revenue": getattr(lead, "annual_revenue", None),
        "owner_email": getattr(lead, "owner_email", None),
        "source": getattr(lead, "source", "manual"),
    })


async def _create_deal(
    db: Any, lead: Any, body: ConvertRequest, contact: Any, organization: Any,
    who: str,
) -> Any:
    """The deal the lead became — carrying its provenance and its primary contact."""
    wanted = body.deal or ConvertDeal()
    status = await load_default_status(db, DEALS.status_table or "crm_deal_statuses")
    name = (
        (wanted.name or "").strip()
        or (getattr(lead, "organization_name", None) or "").strip()
        or lead.lead_name
    )
    deal = await insert_row(db, DEALS.table, {
        "name": name,
        "company_id": str(organization.id) if organization is not None else None,
        "status_id": str(status.id),
        "amount": wanted.amount,
        "currency": wanted.currency or "INR",
        "probability": getattr(status, "probability", None),
        "expected_close_date": wanted.expected_close_date,
        "next_step": wanted.next_step,
        "description": wanted.description,
        # Provenance (§3.4) — this is what lets the deal's timeline union the
        # lead's history instead of losing everything said before conversion.
        "lead_id": str(lead.id),
        "owner_email": (
            wanted.owner_email or getattr(lead, "owner_email", None) or who
        ),
        "source": getattr(lead, "source", "manual"),
    })
    if contact is not None:
        # Through the shared seam, not a bare INSERT: "at most one primary per
        # deal" (§3.5) is enforced in code, and code with two writers is a
        # convention (WS-26c dw 2).
        await link_deal_contact(
            db, str(deal.id), str(contact.id), is_primary=True,
        )
    return deal


async def _stamp_converted(
    db: Any, lead: Any, contact: Any, organization: Any, deal: Any, who: str,
) -> Any:
    """Write the conversion provenance and move the lead to its won status.

    The status move goes through :func:`apply_status_transition` rather than a
    bare column write, so a conversion appears in the funnel log and on the
    lead's timeline like any other move. A conversion that leaves no trace is
    the one funnel event you most want to count.
    """
    updates: dict[str, Any] = {
        "converted_at": now(),
        "converted_contact_id": str(contact.id) if contact is not None else None,
        "converted_organization_id": (
            str(organization.id) if organization is not None else None
        ),
        "converted_deal_id": str(deal.id),
    }
    won = (await db.execute(
        text(
            "SELECT * FROM crm_lead_statuses WHERE type = 'won' "
            "ORDER BY position, name LIMIT 1"
        ),
        {},
    )).fetchone()
    if won is not None and str(getattr(lead, "status_id", "")) != str(won.id):
        updates.update(await apply_status_transition(
            db, LEADS, lead,
            new_status_id=str(won.id), patch={}, actor_email=who,
        ))
    return await update_row(db, LEADS.table, str(lead.id), updates)
