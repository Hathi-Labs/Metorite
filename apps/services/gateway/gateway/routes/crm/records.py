"""CRM · records — CRUD and the shared list contract for the four entities.

Spec: ``project-docs/specs/crm_app.md`` §4 (``records.py`` row).

    GET    /crm/{leads,deals,contacts,organizations}          → {rows, total}
    POST   /crm/{leads,deals,contacts,organizations}
    GET    /crm/<entity>/{id}
    PATCH  /crm/<entity>/{id}
    DELETE /crm/<entity>/{id}                                 → what cascaded

**Every path here is written out literally**, one route per entity, rather than
served by a single ``/crm/{entity}`` template. That is not verbosity for its own
sake: a generic two-segment template would collide with
``PATCH /crm/activities/{id}``, and the resolution would be *route registration
order* — the exact trap ``routes/workflows/__init__.py`` documents. Explicit
paths make the package's import order irrelevant, so nothing here can break by
being alphabetised later.

The four handlers per verb are three lines each; the behaviour lives in the
shared ``_list`` / ``create_record`` / ``patch_record`` / ``delete_record``
functions below, so there is exactly one implementation of each rule.
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
    ContactIn,
    DealIn,
    Entity,
    LeadIn,
    ListResponse,
    OrganizationIn,
    _tenant_session,
    actor,
    clean_payload,
    compute_lead_name,
    count_where,
    has_column,
    insert_row,
    lead_name_is_derived,
    list_contract,
    load_default_status,
    now,
    record_zoho_tombstone,
    require_row,
    router,
    row_to_dict,
    run_list,
    update_row,
    validate_source,
)
from gateway.routes.crm.pipeline import (
    NO_EXISTING_RECORD,
    _require_entry_fields,
    apply_status_transition,
)
from pydantic import BaseModel
from sqlalchemy import text


class ListParams:
    """The list contract's query parameters — declared once, bound four times.

    A FastAPI class dependency rather than eight repeated parameters per
    handler: the contract is supposed to be the same for every entity, and the
    way that stops being true is one endpoint quietly growing a different cap.
    ``page_size``'s ceiling is enforced here by ``le=``, so a caller asking for
    10 000 rows gets a 422 rather than a slow answer.
    """

    def __init__(
        self,
        q: str | None = None,
        sort: str | None = None,
        direction: str = Query("desc", alias="dir"),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
        status_id: str | None = None,
        owner: str | None = None,
        source: str | None = None,
        include_converted: bool = False,
    ) -> None:
        self.q = q
        self.sort = sort
        self.direction = direction
        self.page = page
        self.page_size = page_size
        self.status_id = status_id
        self.owner = owner
        self.source = source
        self.include_converted = include_converted


class DeleteResponse(BaseModel):
    """R7/R8 — a destructive route says what it destroyed, per table."""

    deleted: str
    entity: str
    cascaded: dict[str, int]
    #: WS-26b — true when this record had a Zoho twin and a
    #: ``crm_zoho_tombstones`` row was written, so the next sync cycle will
    #: delete it there too. The caller is told the deletion leaves this app.
    zoho_delete_queued: bool = False


# ── Shared behaviour ────────────────────────────────────────────────────────

async def _list(entity: Entity, params: ListParams) -> ListResponse:
    extra: tuple[str, ...] = ()
    if entity.hides_converted and not params.include_converted:
        # §3.3/B6 — a lead is "converted" while the deal it became still
        # exists. Keying on converted_at instead would strand the lead
        # invisibly forever if that deal were deleted (the FK SET-NULLs the
        # link; the timestamp survives as history).
        extra = ("converted_deal_id IS NULL",)
    query = list_contract(
        entity,
        q=params.q, sort=params.sort, direction=params.direction,
        page=params.page, page_size=params.page_size,
        status_id=params.status_id, owner=params.owner, source=params.source,
        extra_where=extra,
    )
    async with _tenant_session() as db:
        return await run_list(db, entity, query)


async def _get(entity: Entity, record_id: str) -> dict:
    async with _tenant_session() as db:
        row = await require_row(db, entity.table, record_id, entity.label)
        return row_to_dict(row, entity.model)


async def _resolve_status(
    db: Any, entity: Entity, values: dict[str, Any],
) -> Any | None:
    """The status a new record starts in, plus the rules that ride on it.

    A record created straight into a ``lost``-type status needs a lost reason
    for the same reason a transition into one does — the gate belongs to the
    status, not to the verb that reached it. That doctrine is scoped to the
    TERMINAL case and stays there: a deal is lost or it is not, however it got
    there.

    **Entry requirements are the other shape, and they are applied only when
    the caller CHOSE the stage** (WS-26h2 / **D-CRM-13**). ``chosen`` is that
    distinction, and it is the whole rule: a ``status_id`` in the body is a
    claim that this deal has EARNED its way into that lane and is checked;
    a stage that came from :func:`load_default_status` is where deals start,
    has been claimed by nobody, and is never gated. Without that condition a
    requirement on the default lane would 422 ``QuickCreateModal`` (which sends
    no ``status_id``) and every lead conversion (``ConvertDeal`` has no such
    field) — and ``company_id`` is requirable while neither surface can
    supply one, so those leads would become permanently unconvertible.

    The permissive direction is deliberate and cheaply reversible: deleting the
    ``if chosen`` condition yields the strict rule. The reverse is not.
    """
    if not entity.status_table:
        return None
    chosen = values.get("status_id")
    status = (
        await require_row(db, entity.status_table, str(chosen), "Status")
        if chosen
        else await load_default_status(db, entity.status_table)
    )
    if status.type == "lost" and not values.get("lost_reason_id"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{status.name}' is a lost-type status: a lost_reason_id is "
                "required to create a record in it."
            ),
        )
    if chosen:
        # Beside the lost refusal and in the same order as the move path, so a
        # stage that is both lost-type and gated answers the lost question
        # first. `values` is the caller's own body — including the
        # `owner_email` default `create_record` has already applied — so the
        # requirement is judged on the state this write will LEAVE, and there
        # is no stored row to fall back to.
        _require_entry_fields(status, NO_EXISTING_RECORD, values)
    values["status_id"] = str(status.id)
    if has_column(entity, "probability") and values.get("probability") is None:
        # §3.4 — a deal with no stated probability inherits its stage's default.
        values["probability"] = getattr(status, "probability", None)
    if (
        status.type in CLOSING_TYPES
        and has_column(entity, "closed_at")
        and not values.get("closed_at")
    ):
        # §3.6 — entering a won/lost status stamps closed_at, and creating a
        # record directly in one IS entering it (same reach as the lost gate).
        values["closed_at"] = now()
    return status


async def create_record(
    entity: Entity, payload: BaseModel, user: UserContext,
) -> dict:
    values = clean_payload(payload)
    validate_source(values)
    missing = [
        name for name in entity.required
        if not str(values.get(name) or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required field(s) for {entity.slug}: {missing}.",
        )
    if entity is LEADS:
        values["lead_name"] = compute_lead_name(values)
    # An explicit `null` is "deliberately unassigned" and is left alone;
    # only an ABSENT owner defaults to the person creating the record, because
    # an unassigned record is nobody's follow-up and the `owner` filter would
    # have nothing to match.
    values.setdefault("owner_email", actor(user))

    async with _tenant_session() as db:
        await _resolve_status(db, entity, values)
        row = await insert_row(db, entity.table, values)
        return row_to_dict(row, entity.model)


async def patch_record(
    entity: Entity, record_id: str, payload: BaseModel, user: UserContext,
) -> dict:
    """Update a record. A body that moves ``status_id`` is a status TRANSITION.

    Which is why this delegates to ``pipeline.apply_status_transition`` rather
    than writing the column: the transition's three effects (dwell log, timeline
    activity, re-stamped clock) are the pipeline's semantics and must not depend
    on which endpoint moved the record.
    """
    values = clean_payload(payload)
    validate_source(values)
    async with _tenant_session() as db:
        record = await require_row(db, entity.table, record_id, entity.label)
        wanted = values.get("status_id")
        if wanted and str(getattr(record, "status_id", "") or "") != str(wanted):
            values.update(await apply_status_transition(
                db, entity, record,
                new_status_id=str(wanted), patch=values, actor_email=actor(user),
            ))
        if entity is LEADS:
            _recompute_lead_name(record, values)
        if not values:
            return row_to_dict(record, entity.model)
        row = await update_row(db, entity.table, record_id, values)
        return row_to_dict(row, entity.model)


#: The lead fields the display name is derived from. Touching any of them may
#: re-derive it; touching none of them never does.
_LEAD_NAME_INPUTS = frozenset(
    {"first_name", "last_name", "organization_name", "email", "lead_name"}
)


def _recompute_lead_name(record: Any, values: dict[str, Any]) -> None:
    """Keep ``lead_name`` in step with the fields it is derived FROM — unless
    somebody typed it.

    Three cases, and the third is the one WS-26c dw 3 exists to fix:

    * the body sets ``lead_name`` → that is the name, blank-guarded by the
      fallback chain;
    * the body moves a name input and the stored name is still the chain's own
      output → re-derive, so renaming a lead's company renames the lead;
    * the body moves a name input but the stored name was hand-edited → leave
      it. Correcting a lead's email address must not silently rename
      "Ravi (Bosch, via Anil)" back to "ravi".
    """
    if not (_LEAD_NAME_INPUTS & values.keys()):
        return
    current = row_to_dict(record, LEADS.model)
    merged = {**current, **values}
    if "lead_name" not in values:
        if not lead_name_is_derived(current):
            return
        # Blank the stored name so the fallback chain actually re-derives
        # rather than returning the value it is supposed to be replacing.
        merged["lead_name"] = None
    values["lead_name"] = compute_lead_name(merged)


async def delete_record(
    entity: Entity, record_id: str, user: UserContext,
) -> DeleteResponse:
    """Delete a record and report what Postgres took with it.

    The counts are read BEFORE the delete, in the same transaction: afterwards
    the rows are gone and the honest number is unobtainable. R7/R8 — "deleted"
    with no blast radius is the response that hides a cascade.

    A record with a ``zoho_id`` also leaves a ``crm_zoho_tombstones`` row
    **inside this transaction** (§7.1). Same reason as the counts, one step
    further: after the DELETE the ``zoho_id`` the sync needs to propagate the
    deletion upstream no longer exists anywhere, and a delete that rolls back
    must not leave a tombstone that would delete a live Zoho record.
    """
    async with _tenant_session() as db:
        record = await require_row(db, entity.table, record_id, entity.label)
        cascaded: dict[str, int] = {}
        for table, column in entity.cascades:
            cascaded[table] = await count_where(db, table, column, record_id)
        queued = await record_zoho_tombstone(
            db, entity, record, deleted_by=actor(user),
        )
        await db.execute(
            text(f"DELETE FROM {entity.table} WHERE id = CAST(:id AS uuid)"),
            {"id": record_id},
        )
        return DeleteResponse(
            deleted=record_id, entity=entity.slug, cascaded=cascaded,
            zoho_delete_queued=queued,
        )


# ── Leads ───────────────────────────────────────────────────────────────────

@router.get("/leads", response_model=ListResponse)
async def list_leads(
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> ListResponse:
    """Leads, newest first. Converted leads are hidden unless asked for."""
    return await _list(LEADS, params)


@router.post("/leads", status_code=201)
async def create_lead(
    payload: LeadIn, user: UserContext = Depends(get_current_user),
) -> dict:
    return await create_record(LEADS, payload, user)


@router.get("/leads/{record_id}")
async def get_lead(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    return await _get(LEADS, record_id)


@router.patch("/leads/{record_id}")
async def patch_lead(
    record_id: str, payload: LeadIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await patch_record(LEADS, record_id, payload, user)


@router.delete("/leads/{record_id}", response_model=DeleteResponse)
async def delete_lead(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    return await delete_record(LEADS, record_id, user)


# ── Deals ───────────────────────────────────────────────────────────────────

@router.get("/deals", response_model=ListResponse)
async def list_deals(
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> ListResponse:
    return await _list(DEALS, params)


@router.post("/deals", status_code=201)
async def create_deal(
    payload: DealIn, user: UserContext = Depends(get_current_user),
) -> dict:
    return await create_record(DEALS, payload, user)


@router.get("/deals/{record_id}")
async def get_deal(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    return await _get(DEALS, record_id)


@router.patch("/deals/{record_id}")
async def patch_deal(
    record_id: str, payload: DealIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await patch_record(DEALS, record_id, payload, user)


@router.delete("/deals/{record_id}", response_model=DeleteResponse)
async def delete_deal(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    return await delete_record(DEALS, record_id, user)


# ── Contacts ────────────────────────────────────────────────────────────────

@router.get("/contacts", response_model=ListResponse)
async def list_contacts(
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> ListResponse:
    return await _list(CONTACTS, params)


@router.post("/contacts", status_code=201)
async def create_contact(
    payload: ContactIn, user: UserContext = Depends(get_current_user),
) -> dict:
    return await create_record(CONTACTS, payload, user)


@router.get("/contacts/{record_id}")
async def get_contact(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    return await _get(CONTACTS, record_id)


@router.patch("/contacts/{record_id}")
async def patch_contact(
    record_id: str, payload: ContactIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await patch_record(CONTACTS, record_id, payload, user)


@router.delete("/contacts/{record_id}", response_model=DeleteResponse)
async def delete_contact(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    return await delete_record(CONTACTS, record_id, user)


# ── Organizations ───────────────────────────────────────────────────────────

@router.get("/organizations", response_model=ListResponse)
async def list_organizations(
    params: ListParams = Depends(),
    user: UserContext = Depends(get_current_user),
) -> ListResponse:
    return await _list(ORGANIZATIONS, params)


@router.post("/organizations", status_code=201)
async def create_organization(
    payload: OrganizationIn, user: UserContext = Depends(get_current_user),
) -> dict:
    return await create_record(ORGANIZATIONS, payload, user)


@router.get("/organizations/{record_id}")
async def get_organization(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    return await _get(ORGANIZATIONS, record_id)


@router.patch("/organizations/{record_id}")
async def patch_organization(
    record_id: str, payload: OrganizationIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    return await patch_record(ORGANIZATIONS, record_id, payload, user)


@router.delete("/organizations/{record_id}", response_model=DeleteResponse)
async def delete_organization(
    record_id: str, user: UserContext = Depends(get_current_user),
) -> DeleteResponse:
    return await delete_record(ORGANIZATIONS, record_id, user)
