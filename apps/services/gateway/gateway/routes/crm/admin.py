"""CRM · admin — managing the pipeline itself: statuses and lost reasons.

Spec: ``project-docs/specs/crm_app.md`` §3.6, §3.10, §4 (``admin.py`` row).

    GET/POST          /crm/statuses/{lead|deal}
    PUT               /crm/statuses/{lead|deal}/order
    PATCH/DELETE      /crm/statuses/{lead|deal}/{status_id}
    GET/POST          /crm/lost-reasons
    PATCH/DELETE      /crm/lost-reasons/{reason_id}

**The reads pass the router's gates. The six writes also need
``admin:access:manage``** (WS-53 CRM-T5, finding CR-10 in
``project-docs/specs/crm_platform.md``). D-CRM-3 let each ``feature:crm``
holder rename or delete a stage. With a second organization and members who
are not the owner, one member could then empty the funnel of all the others.
The floor is the one the Zoho import and ``PATCH /crm/settings`` use. The
reads stay open, because each board and each record sheet needs them.

**The overlay** (WS-53 CRM-C3, ``crm_platform.md`` §20.6). Each stage, status
and lost reason has source values and Metorite-only overlay values
(``core.MIRROR_OVERLAY_COLUMNS``). In Mirror mode an admin edits the overlay
only, and any other field is 409 ``mirror_mode_settings``. In Native mode an
admin edits the source columns and ``hidden``, and a ``ui_*`` field is 422.

``DELETE`` on a status still holding records is a **409**. The FK is
``ON DELETE RESTRICT``, so Postgres would refuse it anyway — but it would
surface as a driver IntegrityError and a 500. Counting first turns "the
database said no" into "Proposal still has 4 deals in it".
"""

from __future__ import annotations

import re
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import (
    MIRROR_OVERLAY_COLUMNS,
    STAGE_REQUIREABLE_FIELDS,
    STATUS_TYPES,
    LostReasonModel,
    StatusModel,
    _tenant_session,
    add_stage_to_default_pipeline,
    assert_writable,
    current_tenant,
    insert_row,
    is_mirror_mode,
    read_status,
    read_statuses,
    require_row,
    router,
    status_wire,
    update_row,
)
from pydantic import BaseModel
from sqlalchemy import text

#: The floor of the six writes below (CR-10). One object, so the wiring test in
#: ``tests/unit/test_crm_access.py`` can find it on each route.
ADMIN_ONLY = [require_permission(ADMIN_PERMISSION)]

#: URL segment → (statuses table, the table whose rows reference it). Two
#: entries, written out: the segment is matched against this dict and never
#: interpolated, so ``/crm/statuses/{kind}`` cannot name a table we did not
#: choose.
STATUS_KINDS: dict[str, tuple[str, str]] = {
    "lead": ("crm_lead_statuses", "crm_leads"),
    "deal": ("crm_deal_statuses", "crm_deals"),
}


class StatusIn(BaseModel):
    name: str | None = None
    color: str | None = None
    position: int | None = None
    type: str | None = None
    is_default: bool | None = None
    #: Deal statuses only; ignored (and rejected) for lead statuses.
    probability: int | None = None
    #: Deal statuses only (WS-26h) — see :data:`core.STAGE_REQUIREABLE_FIELDS`.
    required_fields: list[str] | None = None
    #: Deal statuses only (WS-26h) — ``None`` means this stage never rots.
    max_dwell_days: int | None = None
    #: CRM-C3: no picker offers a hidden row. Both modes.
    hidden: bool | None = None
    #: CRM-C3, Mirror mode only: the overlay. ``null`` clears it, so the source
    #: value applies again. A Native admin edits the source column instead.
    ui_position: int | None = None
    #: Deal statuses only.
    ui_probability: int | None = None
    ui_color: str | None = None


class LostReasonIn(BaseModel):
    label: str | None = None
    position: int | None = None
    #: CRM-C3: the one overlay column of a lost reason. Both modes.
    hidden: bool | None = None


class OrderIn(BaseModel):
    """``PUT /crm/statuses/{kind}/order`` — every id of the kind, in order."""

    ids: list[str]


def _kind(kind: str) -> tuple[str, str]:
    try:
        return STATUS_KINDS[kind]
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown status kind '{kind}'. One of: {sorted(STATUS_KINDS)}.",
        ) from None


#: D-CRM-10 — a won-type lane forecasts 100 and a lost-type lane 0.
#:
#: Enforced here rather than trusted downstream, and as a **422 on the
#: contradiction** rather than a silent rewrite: probability *informs* and
#: ``type`` *decides*, so a stage typed won at 40% cannot close a deal but it
#: can quietly halve the weighted pipeline every forecast surface reads. The
#: owner who typed it is the one who should be told.
CLAMPED_PROBABILITY: dict[str, int] = {"won": 100, "lost": 0}


#: The two fields whose movement the clamp judges. A PATCH naming neither is
#: not a forecast decision and must never be refused on forecast grounds.
CLAMPED_FIELDS: frozenset[str] = frozenset({"type", "probability"})

#: Columns `crm_deal_statuses` has and `crm_lead_statuses` has not. Naming one
#: of them on a lead status is a 422 rather than a silently dropped value or a
#: driver error about a column that does not exist.
#:
#: Hand-kept, and therefore FENCED rather than trusted: a fourth deal-only
#: column added without being added here would be silently accepted on a lead
#: status — the exact defect WS-26c dw 3 recorded for `probability`.
#: `test_crm_migration.py::test_the_deal_only_field_set_matches_the_schema`
#: derives the same set from the two CREATE TABLE bodies plus the WS-26h ALTER
#: and fails if this one drifts from it.
DEAL_ONLY_FIELDS: frozenset[str] = frozenset({
    "probability", "required_fields", "max_dwell_days", "ui_probability",
})

#: The overlay fields that only Mirror mode takes (CRM-C3, §20.6 item 3). A
#: Native admin edits the source column directly, so one of these is a 422.
#: ``hidden`` is not here: both modes take it.
UI_FIELDS: frozenset[str] = frozenset({"ui_position", "ui_probability", "ui_color"})

#: A colour token, as ``src/lib/statusAccent.ts`` names its hues: a word, not a
#: hex value. The picker offers ``ACCENT_HUES``. The grid of a theme decides
#: the paint, so the server keeps the token short and plain.
_COLOR_TOKEN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def _validate_required_fields(values: dict[str, Any]) -> None:
    """`required_fields` names real, requirable deal columns — or it is a 422.

    Judged when the column is WRITTEN, never when it is read. A name that is
    not a deal column can never be satisfied by any deal, so a lane holding one
    would refuse every move into it with a message about a field that does not
    exist — a lane bricked by a typo in a settings grid. Refusing the save is
    the cheap end of that.
    """
    if "required_fields" not in values:
        return
    supplied = values["required_fields"]
    if supplied is None:
        # An explicit null on a NOT NULL DEFAULT column: `insert_row` /
        # `update_row`'s own guard owns that message, and answering it here
        # would give one mistake two different 422s — the same division of
        # labour the clamp above already keeps.
        return
    if not isinstance(supplied, list):
        raise HTTPException(
            status_code=422,
            detail="'required_fields' is a list of deal column names.",
        )
    unknown = [name for name in supplied if name not in STAGE_REQUIREABLE_FIELDS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{unknown} cannot be required on a stage. One of: "
                f"{list(STAGE_REQUIREABLE_FIELDS)}."
            ),
        )
    if len(set(supplied)) != len(supplied):
        # Not cosmetic: the 422 a blocked move raises LISTS what is missing,
        # and a duplicated name would name the same field twice in it.
        raise HTTPException(
            status_code=422,
            detail=f"'required_fields' lists a field twice: {supplied}.",
        )


def _validate_max_dwell_days(values: dict[str, Any]) -> None:
    """A rot threshold is a positive number of days, or NULL for "never".

    Zero is refused rather than accepted as a synonym for NULL: a 0-day
    threshold would paint every card in the stage amber the moment it arrived,
    which reads as a bug rather than as a policy. The upper bound is the
    column's own — `SMALLINT` — and a threshold nobody reaches is NULL's job.
    """
    if "max_dwell_days" not in values:
        return
    supplied = values["max_dwell_days"]
    if supplied is None:
        return
    days = int(supplied)
    if not 1 <= days <= 32767:
        raise HTTPException(
            status_code=422,
            detail=(
                "'max_dwell_days' is a whole number of days from 1 to 32767, or "
                f"null for a stage that never rots: {days} is neither."
            ),
        )


async def _check_mode(db: Any, table: str, values: dict[str, Any]) -> bool:
    """The field rules of the two modes (CRM-C3, §20.6 items 2 and 3).

    Mirror mode: a field outside the overlay is 409 ``mirror_mode_settings``,
    through the one guard, BEFORE any other check. So a PATCH of ``name`` or
    ``probability`` is 409 and never a 422 about the clamp. Native mode: a
    ``ui_*`` field is 422. Returns True in Mirror mode.
    """
    if await is_mirror_mode(db, current_tenant()):
        if not set(values) <= MIRROR_OVERLAY_COLUMNS.get(table, frozenset()):
            await assert_writable(db, current_tenant(), table, values)
        return True
    offered = sorted(UI_FIELDS & values.keys())
    if offered:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{', '.join(offered)} is a Zoho overlay. In Native mode, edit "
                "the stage itself: position, probability or color."
            ),
        )
    return False


def _validate_overlay(values: dict[str, Any]) -> None:
    """The overlay values are in range, or a 422. ``null`` clears a ``ui_*``."""
    if values.get("ui_position") is not None and int(values["ui_position"]) < 0:
        raise HTTPException(
            status_code=422, detail="'ui_position' is a whole number from 0 up.",
        )
    if values.get("ui_probability") is not None and not (
        0 <= int(values["ui_probability"]) <= 100
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"A stage probability is a percentage: {values['ui_probability']} "
                "is outside 0-100."
            ),
        )
    supplied = values.get("ui_color")
    if supplied is not None and not _COLOR_TOKEN.match(str(supplied)):
        raise HTTPException(
            status_code=422,
            detail=f"'ui_color' is a colour name such as 'blue', not {supplied!r}.",
        )


def _effective(
    values: dict[str, Any], existing: Any, column: str, fallback: Any,
) -> Any:
    """The value a write will LEAVE on the row — payload, else row, else default.

    Once the clamp has decided to fire, it has to read this rather than the
    payload alone: a PATCH naming only ``type`` and a PATCH naming only
    ``probability`` each contradict the rule on their own, and a check that saw
    only what was sent would wave both through and leave the row lying.
    """
    if column in values:
        return values[column]
    if existing is not None:
        return getattr(existing, column, fallback)
    return fallback


def _validate_status(
    kind: str, values: dict[str, Any], existing: Any = None,
) -> None:
    declared = values.get("type")
    if declared is not None and declared not in STATUS_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status type '{declared}'. One of: {list(STATUS_TYPES)}.",
        )
    # Keyed on the field being PRESENT, not on it being non-null:
    # `{"probability": null}` would otherwise reach an INSERT naming a column
    # `crm_lead_statuses` has not got, and surface as a driver error rather
    # than as the 422 this rule already knows how to say (WS-26c dw 3).
    #
    # WS-26h's two columns join `probability` on exactly the same footing:
    # `crm_deal_statuses` has them and `crm_lead_statuses` does not, so ONE
    # check over :data:`DEAL_ONLY_FIELDS` is the one that cannot forget the
    # third of them.
    if kind == "lead":
        offered = sorted(DEAL_ONLY_FIELDS & values.keys())
        if offered:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Lead statuses have no {', '.join(offered)} — "
                    f"{'those columns are' if len(offered) > 1 else 'that column is'}"
                    " deal-only."
                ),
            )
    if kind != "deal":
        return
    # Both run BEFORE the clamp's early return below: a PATCH naming neither
    # `type` nor `probability` is not a forecast decision, but it can still
    # carry a bad `required_fields` or a 0-day rot threshold.
    _validate_required_fields(values)
    _validate_max_dwell_days(values)
    # ⚠️ **The clamp judges a TRANSITION, not a resting state.** It fires only
    # when the payload itself names `type` or `probability`; a PATCH that
    # touches neither is not a forecast decision and cannot be refused on
    # forecast grounds.
    #
    # Reading the stored row unconditionally was a shipped defect. The Zoho
    # importer's `ensure_status` mints an unseen stage at `probability = 0`
    # and guesses its type from the name, so every pull can create a won-type
    # lane at 0% — a row that already contradicts D-CRM-10 the moment it
    # exists. Judging the resting state made those lanes **un-renameable,
    # un-recolourable and un-reorderable**: a position-only PATCH answered 422
    # about a probability the caller never mentioned, which aborted the
    # settings grid's reorder loop partway and left duplicate positions
    # behind. The rule the owner needs is "you may not MOVE a stage into a
    # contradiction", not "you may not touch a stage that is already in one" —
    # and f2's grid is the surface for getting out of one.
    if not (CLAMPED_FIELDS & values.keys()):
        return

    probability = _effective(values, existing, "probability", 0)
    if probability is None:
        # An explicit null on a NOT NULL DEFAULT column: `insert_row` /
        # `update_row`'s own guard owns that message, and answering it here
        # would give one mistake two different 422s.
        return
    probability = int(probability)
    if not 0 <= probability <= 100:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A stage probability is a percentage: {probability} is outside "
                "0-100."
            ),
        )
    status_type = str(_effective(values, existing, "type", "open") or "open")
    pinned = CLAMPED_PROBABILITY.get(status_type)
    if pinned is not None and probability != pinned:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A '{status_type}' stage always forecasts {pinned}% — this one "
                f"would be left at {probability}%. Set probability to {pinned}, "
                "or give the stage a different type (D-CRM-10: probability "
                "informs the forecast, type decides what closes)."
            ),
        )


async def _check_hide(db: Any, kind: str, table: str, row: Any) -> None:
    """Native mode: some rows must stay in the pickers (CRM-C3, item 15).

    The default stage and the default lead status hold each new record, so a
    hide of one is a 422. A deal stage that is the only visible ``won`` or
    ``lost`` stage is the one way to close a deal, so a hide of it is a 422 too.
    """
    if bool(getattr(row, "is_default", False)):
        raise HTTPException(
            status_code=422,
            detail="The default status holds each new record. Make another the "
                   "default before you hide this one.",
        )
    kind_of = getattr(row, "type", None)
    if kind != "deal" or kind_of not in CLAMPED_PROBABILITY:
        return
    others = (await db.execute(
        text(
            f"SELECT count(*) FROM {table} WHERE type = :type "
            "AND NOT hidden AND id <> CAST(:id AS uuid)"
        ),
        {"type": kind_of, "id": str(row.id)},
    )).scalar() or 0
    if not int(others):
        raise HTTPException(
            status_code=422,
            detail=f"This is the only '{kind_of}' stage. A deal cannot close as "
                   f"{kind_of} without it, so it stays in the pickers.",
        )


# ── Statuses ────────────────────────────────────────────────────────────────

@router.get("/statuses/{kind}", response_model=list[StatusModel])
async def list_statuses(
    kind: str, user: UserContext = Depends(get_current_user),
) -> list[StatusModel]:
    """The kanban lanes, in lane order, with the effective values (CRM-C3)."""
    table, _ = _kind(kind)
    async with _tenant_session() as db:
        return [status_wire(r) for r in await read_statuses(db, table)]


@router.post(
    "/statuses/{kind}", response_model=StatusModel, status_code=201,
    dependencies=ADMIN_ONLY,
)
async def create_status(
    kind: str, payload: StatusIn,
    user: UserContext = Depends(get_current_user),
) -> StatusModel:
    table, _ = _kind(kind)
    values = payload.model_dump(exclude_unset=True)
    _validate_status(kind, values)
    if not (values.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="A status needs a name.")
    values.setdefault("type", "open")
    async with _tenant_session() as db:
        await _check_mode(db, table, values)
        if values.get("position") is None:
            values["position"] = await _next_position(db, table)
        row = await insert_row(db, table, values)
        if kind == "deal":
            await add_stage_to_default_pipeline(db, str(row.id))
        return status_wire(await read_status(db, table, str(row.id)) or row)


@router.put(
    "/statuses/{kind}/order", response_model=list[StatusModel],
    dependencies=ADMIN_ONLY,
)
async def order_statuses(
    kind: str, payload: OrderIn,
    user: UserContext = Depends(get_current_user),
) -> list[StatusModel]:
    """Set the lane order of one kind, in one transaction (CRM-C3, item 4).

    The body is every id of the kind, in the new order. Native mode writes
    ``position``. Mirror mode writes ``ui_position`` on every row, so only a
    stage that Zoho adds later has none, and it sorts last in its group.
    A list with a missing, an extra, a repeated or a foreign id is a 422.
    """
    table, _ = _kind(kind)
    wanted = [str(i).strip().lower() for i in payload.ids]
    async with _tenant_session() as db:
        mirror = await is_mirror_mode(db, current_tenant())
        column = "ui_position" if mirror else "position"
        await assert_writable(db, current_tenant(), table, {column: 0})
        existing = {
            str(r.id).lower()
            for r in (await db.execute(text(f"SELECT id FROM {table}"), {})).fetchall()
        }
        if len(set(wanted)) != len(wanted) or set(wanted) != existing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Send every {kind} status once, in the new order: "
                    f"{len(existing)} ids, and no other."
                ),
            )
        for index, status_id in enumerate(wanted):
            await db.execute(
                text(
                    f"UPDATE {table} SET {column} = :position "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"position": index * 10, "id": status_id},
            )
        return [status_wire(r) for r in await read_statuses(db, table)]


@router.post(
    "/statuses/{kind}/order/reset", response_model=list[StatusModel],
    dependencies=ADMIN_ONLY,
)
async def reset_status_order(
    kind: str, user: UserContext = Depends(get_current_user),
) -> list[StatusModel]:
    """"Reset order to Zoho" (CRM-C3, item 14): clear ``ui_position`` on every
    row of the kind, in one statement. Mirror mode only. Native mode has no
    overlay order, so it is a 422.
    """
    table, _ = _kind(kind)
    async with _tenant_session() as db:
        if not await is_mirror_mode(db, current_tenant()):
            raise HTTPException(
                status_code=422,
                detail="Only a Zoho mirror has an order to reset to.",
            )
        await assert_writable(db, current_tenant(), table, {"ui_position": None})
        await db.execute(text(f"UPDATE {table} SET ui_position = NULL"), {})
        return [status_wire(r) for r in await read_statuses(db, table)]


@router.patch(
    "/statuses/{kind}/{status_id}", response_model=StatusModel,
    dependencies=ADMIN_ONLY,
)
async def patch_status(
    kind: str, status_id: str, payload: StatusIn,
    user: UserContext = Depends(get_current_user),
) -> StatusModel:
    """Rename, recolour, retype, hide or reorder a lane (reorder = PATCH position).

    The row is loaded BEFORE the payload is validated, because D-CRM-10's clamp
    is about the state the write leaves behind: ``{"type": "won"}`` alone and
    ``{"probability": 40}`` alone each contradict the rule only in combination
    with what is already stored.

    CRM-C3: the mode check runs first. In Mirror mode a field outside the
    overlay is 409, and "Reset to Zoho" is a PATCH of ``ui_probability`` and
    ``ui_color`` to ``null``.
    """
    table, _ = _kind(kind)
    values = payload.model_dump(exclude_unset=True)
    async with _tenant_session() as db:
        row = await require_row(db, table, status_id, "Status")
        mirror = await _check_mode(db, table, values)
        _validate_status(kind, values, existing=row)
        _validate_overlay(values)
        if values.get("hidden") and not mirror:
            await _check_hide(db, kind, table, row)
        if values:
            await update_row(db, table, status_id, values, touch=False)
        return status_wire(await read_status(db, table, status_id) or row)


@router.delete("/statuses/{kind}/{status_id}", dependencies=ADMIN_ONLY)
async def delete_status(
    kind: str, status_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    table, referencing = _kind(kind)
    async with _tenant_session() as db:
        await require_row(db, table, status_id, "Status")
        in_use = (await db.execute(
            text(
                f"SELECT count(*) FROM {referencing} "
                "WHERE status_id = CAST(:status_id AS uuid)"
            ),
            {"status_id": status_id},
        )).scalar() or 0
        if int(in_use):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This status still holds {int(in_use)} record(s). Move them "
                    "to another status first — deleting it would orphan them."
                ),
            )
        await assert_writable(db, current_tenant(), table)
        await db.execute(
            text(f"DELETE FROM {table} WHERE id = CAST(:id AS uuid)"),
            {"id": status_id},
        )
        return {"deleted": status_id, "kind": kind}


async def _next_position(db: Any, table: str) -> int:
    """Append a new lane at the end rather than at 0, which would silently
    reorder every existing lane."""
    highest = (await db.execute(
        text(f"SELECT max(position) FROM {table}"), {},
    )).scalar()
    return int(highest or 0) + 10


# ── Lost reasons ────────────────────────────────────────────────────────────

def _reason_wire(row: Any) -> LostReasonModel:
    return LostReasonModel(
        id=str(row.id), label=row.label,
        position=int(getattr(row, "position", 0) or 0),
        hidden=bool(getattr(row, "hidden", False)),
    )


@router.get("/lost-reasons", response_model=list[LostReasonModel])
async def list_lost_reasons(
    user: UserContext = Depends(get_current_user),
) -> list[LostReasonModel]:
    async with _tenant_session() as db:
        rows = (await db.execute(
            text("SELECT * FROM crm_lost_reasons ORDER BY position, label"), {},
        )).fetchall()
        return [_reason_wire(r) for r in rows]


@router.post(
    "/lost-reasons", response_model=LostReasonModel, status_code=201,
    dependencies=ADMIN_ONLY,
)
async def create_lost_reason(
    payload: LostReasonIn, user: UserContext = Depends(get_current_user),
) -> LostReasonModel:
    values = payload.model_dump(exclude_unset=True)
    if not (values.get("label") or "").strip():
        raise HTTPException(status_code=422, detail="A lost reason needs a label.")
    async with _tenant_session() as db:
        if values.get("position") is None:
            values["position"] = await _next_position(db, "crm_lost_reasons")
        row = await insert_row(db, "crm_lost_reasons", values)
        return _reason_wire(row)


@router.patch(
    "/lost-reasons/{reason_id}", response_model=LostReasonModel,
    dependencies=ADMIN_ONLY,
)
async def patch_lost_reason(
    reason_id: str, payload: LostReasonIn,
    user: UserContext = Depends(get_current_user),
) -> LostReasonModel:
    """Relabel, reorder or hide a lost reason. In Mirror mode, hide only."""
    values = payload.model_dump(exclude_unset=True)
    async with _tenant_session() as db:
        row = await require_row(db, "crm_lost_reasons", reason_id, "Lost reason")
        await _check_mode(db, "crm_lost_reasons", values)
        if values:
            row = await update_row(
                db, "crm_lost_reasons", reason_id, values, touch=False,
            )
        return _reason_wire(row)


@router.delete("/lost-reasons/{reason_id}", dependencies=ADMIN_ONLY)
async def delete_lost_reason(
    reason_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Deleting a lost reason is safe: both FKs are ``ON DELETE SET NULL``, so
    the records that cited it keep their ``lost_note`` and lose the label."""
    async with _tenant_session() as db:
        await require_row(db, "crm_lost_reasons", reason_id, "Lost reason")
        await assert_writable(db, current_tenant(), "crm_lost_reasons")
        await db.execute(
            text("DELETE FROM crm_lost_reasons WHERE id = CAST(:id AS uuid)"),
            {"id": reason_id},
        )
        return {"deleted": reason_id}
