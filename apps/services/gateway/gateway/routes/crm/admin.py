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

**Change history** (WS-CRM CRM-M1, ``crm_conversational.md`` §8). A create
and a PATCH of a status or a lost reason write one row through
``change_log.log_change``, in the same transaction, at the ``team`` level of
the ladder. A refused write (409, 422) writes none, and a PATCH that changes
no value writes none. Each Undo first locks the row (``FOR UPDATE``) and
checks that it still holds the values of the change, so a row that changed
again since is a 409 ``changed_since``. The Undo of a create deletes the row through the
helper of the DELETE route, and it refuses while records name the row. The
Undo of a PATCH whose old name another row holds now is a 409
``name_taken`` (:func:`_name_free`). The
Undo of a PATCH writes the old values through :func:`patch_status_in` or
:func:`patch_lost_reason_in`, the helpers of the PATCH routes. The DELETE
routes log in PR 2.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm import change_log
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.change_log import Change, Logged, log_change, quoted
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
    load_row,
    lock_pipeline_creates,
    pipelines_holding,
    read_status,
    read_statuses,
    require_row,
    router,
    status_wire,
    update_row,
)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
    #: Both kinds since migration 288. The chance, in percent, of a win.
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
    #: Both kinds since migration 288.
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
    "required_fields", "max_dwell_days",
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

    The mode is read ``FOR SHARE`` (CRM-Z10b): an overlay edit that read
    ``mirror`` commits before the switch folds the overlay, and one that
    starts during the switch reads ``native`` and refuses a ``ui_*`` field.
    """
    if await is_mirror_mode(db, current_tenant(), lock=True):
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
    # Both run BEFORE the clamp's early return below: a PATCH naming neither
    # `type` nor `probability` is not a forecast decision, but it can still
    # carry a bad `required_fields` or a 0-day rot threshold. They are deal
    # only. The clamp below binds both kinds (migration 288, CRM-U6).
    if kind == "deal":
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


#: The fields whose change can take a row out of the pickers, or make a
#: hidden row the one that each new record lands in.
VISIBILITY_FIELDS: frozenset[str] = frozenset({"hidden", "is_default", "type"})


async def _check_visibility(
    db: Any, kind: str, table: str, values: dict[str, Any], row: Any = None,
) -> None:
    """Native mode: some rows must stay in the pickers (CRM-C3, item 15).

    It judges the state AFTER the write: the payload over the stored row. So
    ``{"hidden": true}`` on the default, ``{"is_default": true}`` on a hidden
    row and ``{"type": "open"}`` on the only visible ``won`` stage are each
    one 422, on a create and on a PATCH.

    * A hidden row can never be the default. The default holds each new
      record, and no picker would offer it.
    * A deal stage that is the only visible ``won`` or ``lost`` stage is the
      one way to close a deal as won or lost. A write that hides it or
      retypes it is a 422.
    """
    if not (VISIBILITY_FIELDS & values.keys()):
        return
    hidden = bool(_effective(values, row, "hidden", False))
    if hidden and bool(_effective(values, row, "is_default", False)):
        raise HTTPException(
            status_code=422,
            detail=(
                "A hidden status cannot be the default, because the default "
                "holds each new record. Make another the default first."
            ),
        )
    if kind != "deal" or row is None:
        return
    was_type = getattr(row, "type", None)
    was_visible = not bool(getattr(row, "hidden", False))
    new_type = _effective(values, row, "type", "open")
    if not was_visible or was_type not in CLAMPED_PROBABILITY:
        return
    if not hidden and new_type == was_type:
        return
    others = (await db.execute(
        text(
            f"SELECT count(*) FROM {table} WHERE type = :type "
            "AND NOT hidden AND id <> CAST(:id AS uuid)"
        ),
        {"type": was_type, "id": str(row.id)},
    )).scalar() or 0
    if not int(others):
        raise HTTPException(
            status_code=422,
            detail=(
                f"This is the only visible '{was_type}' stage. A deal cannot "
                f"close as {was_type} without it, so it stays visible and keeps "
                "its type."
            ),
        )


# ── Change history (CRM-M1) ─────────────────────────────────────────────────

#: URL segment → the area of its rows in the Change history.
STATUS_AREAS: dict[str, str] = {"deal": "deal_stage", "lead": "lead_status"}
#: The noun of each kind in a summary.
_STATUS_NOUNS: dict[str, str] = {"deal": "deal stage", "lead": "lead status"}

#: The columns of a status that a create row keeps as its "after".
STATUS_LOG_KEYS: tuple[str, ...] = (
    "name", "color", "position", "type", "is_default", "probability",
    "required_fields", "max_dwell_days", "hidden", "ui_position",
    "ui_probability", "ui_color",
)
#: The columns of a lost reason that a create row keeps.
REASON_LOG_KEYS: tuple[str, ...] = ("label", "position", "hidden")

#: Each field of a PATCH, in the words of a summary.
_FIELD_WORDS: dict[str, str] = {
    "name": "name", "label": "name", "color": "colour", "ui_color": "colour",
    "position": "place", "ui_position": "place", "type": "type",
    "is_default": "default", "probability": "win chance",
    "ui_probability": "win chance", "required_fields": "required fields",
    "max_dwell_days": "warning days", "hidden": "hidden",
}


def _snapshot(row: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: getattr(row, k) for k in keys if hasattr(row, k)}


def _change_summary(noun: str, name: Any, before: dict[str, Any],
                    after: dict[str, Any], name_key: str) -> str:
    if set(after) == {name_key}:
        return f"Renamed the {noun} {quoted(before[name_key])} to {quoted(after[name_key])}"
    words = sorted({_FIELD_WORDS.get(k, k) for k in after})
    return f"Changed the {noun} {quoted(name)}: {', '.join(words)}"


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
    async with _tenant_session() as db:
        row = await create_status_in(db, user, kind, values)
        return status_wire(await read_status(db, table, str(row.id)) or row)


async def create_status_in(
    db: Any, user: Any, kind: str, values: dict[str, Any], *,
    into_default: bool = True, via: str = change_log.VIA_UI,
) -> Any:
    """The body of ``POST /crm/statuses/{kind}``, in the caller's
    transaction. Returns the new row.

    It is the ONE way to make a stage or a lead status. The route calls it,
    and so does the create of a pipeline (CRM-M3a), for each stage name that
    the CRM does not hold yet. A new deal stage joins the default pipeline,
    unless *into_default* is False: a stage made for a new pipeline sits in
    that pipeline only. It logs one row (CRM-M1) with *via*.
    """
    table, _ = _kind(kind)
    _validate_status(kind, values)
    if not (values.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="A status needs a name.")
    values.setdefault("type", "open")
    mirror = await _check_mode(db, table, values)
    if not mirror:
        await _check_visibility(db, kind, table, values)
    if values.get("position") is None:
        values["position"] = await _next_position(db, table)
    row = await insert_row(db, table, values)
    if kind == "deal" and into_default:
        await add_stage_to_default_pipeline(db, str(row.id))
    await log_change(db, user, Change(
        area=STATUS_AREAS[kind], action=change_log.CREATE,
        summary=f"Added the {_STATUS_NOUNS[kind]} {quoted(row.name)}",
        ladder=change_log.TEAM, entity_id=str(row.id),
        after=_snapshot(row, STATUS_LOG_KEYS),
    ), via=via)
    return row


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
        # FOR SHARE (CRM-Z10b): the column follows the mode that this write
        # holds until it commits, so the switch folds this order or waits.
        mirror = await is_mirror_mode(db, current_tenant(), lock=True)
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
        if not await is_mirror_mode(db, current_tenant(), lock=True):
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
    _kind(kind)
    values = payload.model_dump(exclude_unset=True)
    async with _tenant_session() as db:
        return await patch_status_in(db, user, kind, status_id, values)


async def patch_status_in(
    db: Any, user: Any, kind: str, status_id: str, values: dict[str, Any],
    *, undoes: str | None = None,
) -> StatusModel:
    """The body of ``PATCH /crm/statuses/{kind}/{id}``, in the caller's
    transaction. The route and the Undo of a change both call it, so an
    Undo meets every check of the route. It logs one row when a value
    changes (CRM-M1)."""
    table, _ = _kind(kind)
    row = await require_row(db, table, status_id, "Status")
    mirror = await _check_mode(db, table, values)
    _validate_status(kind, values, existing=row)
    _validate_overlay(values)
    if not mirror:
        await _check_visibility(db, kind, table, values, row)
    if values:
        updated = await update_row(db, table, status_id, values, touch=False)
        before, after = change_log.diff(
            {k: getattr(row, k, None) for k in values},
            {k: getattr(updated, k, None) for k in values},
        )
        if after:
            await log_change(db, user, Change(
                area=STATUS_AREAS[kind], action=change_log.CHANGE,
                summary=_change_summary(
                    _STATUS_NOUNS[kind], row.name, before, after, "name"),
                ladder=change_log.TEAM, entity_id=str(row.id),
                before=before, after=after,
            ), undoes=undoes)
    return status_wire(await read_status(db, table, status_id) or row)


async def status_in_use(db: Any, kind: str, status_id: str) -> int:
    """The records that sit in one status."""
    _, referencing = _kind(kind)
    return int((await db.execute(
        text(
            f"SELECT count(*) FROM {referencing} "
            "WHERE status_id = CAST(:status_id AS uuid)"
        ),
        {"status_id": status_id},
    )).scalar() or 0)


def _names(names: list[str]) -> str:
    """'"A"', '"A" and "B"', or '"A", "B" and "C"'."""
    shown = [quoted(n) for n in names]
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"


def _held_by_pipelines(
    kind: str, name: Any, holders: list[str], *, undo: bool,
) -> HTTPException:
    """409 ``in_use``: pipelines hold the status, so it stays (CRM-M4a repair 2).

    The words name each pipeline by its name now, say what did not happen,
    and give the next step. A lead pipeline has no route that takes a status
    out, so its Undo is the one step. A deal pipeline also has the route that
    takes a stage out (``DELETE /crm/pipelines/{id}/stages/{status_id}``)."""
    many = len(holders) > 1
    pipeline = ("lead pipeline" if kind == "lead" else "pipeline") + ("s" if many else "")
    which = f"the {pipeline} {_names(holders)}"
    outcome = "nothing was undone" if undo else "it was not deleted"
    then = "undo this" if undo else f"delete the {_STATUS_NOUNS[kind]}"
    first = f"{which[0].upper()}{which[1:]} {'use' if many else 'uses'} the " \
        f"{_STATUS_NOUNS[kind]} {quoted(name)}, so {outcome}."
    if kind == "lead":
        step = f"Undo {which} in Change history first, and then {then}."
    else:
        step = (f"Undo {which} in Change history first, or take the stage out of "
                f"{'them' if many else 'it'}. Then {then}.")
    return change_log.in_use(f"{first} {step}")


async def delete_status_in(db: Any, kind: str, status_id: str, *, undo: bool = False) -> Any:
    """The body of ``DELETE /crm/statuses/{kind}/{id}``. Returns the row that
    went. The Undo of a create calls it too, with *undo* for its words.

    CRM-M4a repair 2: it takes the pipeline create lock, then the row ``FOR
    UPDATE``, as a pipeline create does. It refuses (409 ``in_use``) while a
    pipeline holds the status (``core.pipelines_holding``). The cascade
    would take the status out of that pipeline, and then the Undo of the
    pipeline answers 409 ``changed_since`` for ever."""
    table, _ = _kind(kind)
    await lock_pipeline_creates(db)
    row = await load_row(db, table, status_id, lock=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Status not found")
    in_use = await status_in_use(db, kind, status_id)
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This status still holds {in_use} record(s). Move them "
                "to another status first — deleting it would orphan them."
            ),
        )
    await assert_writable(db, current_tenant(), table)
    holders = await pipelines_holding(db, table, status_id)
    if holders:
        raise _held_by_pipelines(kind, row.name, holders, undo=undo)
    await db.execute(
        text(f"DELETE FROM {table} WHERE id = CAST(:id AS uuid)"),
        {"id": status_id},
    )
    return row


@router.delete("/statuses/{kind}/{status_id}", dependencies=ADMIN_ONLY)
async def delete_status(
    kind: str, status_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict:
    _kind(kind)
    async with _tenant_session() as db:
        await delete_status_in(db, kind, status_id)
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
        await log_change(db, user, Change(
            area="lost_reason", action=change_log.CREATE,
            summary=f"Added the lost reason {quoted(row.label)}",
            ladder=change_log.TEAM, entity_id=str(row.id),
            after=_snapshot(row, REASON_LOG_KEYS),
        ))
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
        return await patch_lost_reason_in(db, user, reason_id, values)


async def patch_lost_reason_in(
    db: Any, user: Any, reason_id: str, values: dict[str, Any],
    *, undoes: str | None = None,
) -> LostReasonModel:
    """The body of ``PATCH /crm/lost-reasons/{id}``, in the caller's
    transaction. The route and the Undo of a change both call it. It logs
    one row when a value changes (CRM-M1)."""
    row = await require_row(db, "crm_lost_reasons", reason_id, "Lost reason")
    await _check_mode(db, "crm_lost_reasons", values)
    if not values:
        return _reason_wire(row)
    updated = await update_row(
        db, "crm_lost_reasons", reason_id, values, touch=False,
    )
    before, after = change_log.diff(
        {k: getattr(row, k, None) for k in values},
        {k: getattr(updated, k, None) for k in values},
    )
    if after:
        await log_change(db, user, Change(
            area="lost_reason", action=change_log.CHANGE,
            summary=_change_summary("lost reason", row.label, before, after, "label"),
            ladder=change_log.TEAM, entity_id=str(row.id),
            before=before, after=after,
        ), undoes=undoes)
    return _reason_wire(updated)


#: The records that may cite a lost reason. Both keys are ON DELETE SET NULL.
_REASON_REFERENCES: tuple[str, ...] = ("crm_deals", "crm_leads")


async def reason_in_use(db: Any, reason_id: str) -> int:
    """The records that cite one lost reason."""
    total = 0
    for table in _REASON_REFERENCES:
        total += int((await db.execute(
            text(
                f"SELECT count(*) FROM {table} "
                "WHERE lost_reason_id = CAST(:reason_id AS uuid)"
            ),
            {"reason_id": reason_id},
        )).scalar() or 0)
    return total


async def delete_lost_reason_in(db: Any, reason_id: str) -> Any:
    """The body of ``DELETE /crm/lost-reasons/{id}``. Returns the row that
    went. The Undo of a create calls it too."""
    row = await require_row(db, "crm_lost_reasons", reason_id, "Lost reason")
    await assert_writable(db, current_tenant(), "crm_lost_reasons")
    await db.execute(
        text("DELETE FROM crm_lost_reasons WHERE id = CAST(:id AS uuid)"),
        {"id": reason_id},
    )
    return row


@router.delete("/lost-reasons/{reason_id}", dependencies=ADMIN_ONLY)
async def delete_lost_reason(
    reason_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Deleting a lost reason is safe: both FKs are ``ON DELETE SET NULL``, so
    the records that cited it keep their ``lost_note`` and lose the label."""
    async with _tenant_session() as db:
        await delete_lost_reason_in(db, reason_id)
        return {"deleted": reason_id}


# ── The Undo of a change (CRM-M1) ───────────────────────────────────────────

_KIND_OF_AREA: dict[str, str] = {area: kind for kind, area in STATUS_AREAS.items()}


async def _current(db: Any, table: str, record_id: str | None, area: str) -> Any:
    """The row of a change, locked ``FOR UPDATE``, or 409 ``gone``. The lock
    holds the values that the Undo checks until it writes."""
    row = await load_row(db, table, str(record_id or ""), lock=True) if record_id else None
    if row is None:
        raise change_log.gone(area)
    return row


@change_log.undoes("deal_stage", "lead_status")
async def _undo_status(db: Any, user: Any, logged: Logged) -> None:
    """A create goes, unless records sit in it or a pipeline holds it. A PATCH
    goes back. Each path first checks that the status still holds the values
    of this change: a status that changed again since is a 409, and the
    create stays."""
    kind = _KIND_OF_AREA[logged.area]
    table, _ = _kind(kind)
    creates = logged.action == change_log.CREATE
    if creates:
        # CRM-M4a repair 2: the pipeline create lock before the row lock, in
        # the order of a pipeline create. A create that reuses this status
        # then never passes between the check and the delete.
        await lock_pipeline_creates(db)
    row = await _current(db, table, logged.entity_id, logged.area)
    if not change_log.same_values(_snapshot(row, STATUS_LOG_KEYS), logged.after):
        raise change_log.changed_since()
    if creates:
        held = await status_in_use(db, kind, str(row.id))
        if held:
            raise change_log.in_use(
                f"The {_STATUS_NOUNS[kind]} {quoted(row.name)} now holds {held} "
                "record(s). Move them to another stage first."
            )
        # It refuses while a pipeline holds the status, and names it.
        await delete_status_in(db, kind, str(row.id), undo=True)
        await log_change(db, user, Change(
            area=logged.area, action=change_log.DELETE,
            summary=f"Removed the {_STATUS_NOUNS[kind]} {quoted(row.name)}",
            ladder=change_log.TEAM, entity_id=str(row.id),
            before=_snapshot(row, STATUS_LOG_KEYS),
        ), undoes=logged.id)
        return None
    async with _name_free(table, _STATUS_NOUNS[kind], (logged.before or {}).get("name")):
        await patch_status_in(
            db, user, kind, str(row.id), dict(logged.before or {}), undoes=logged.id,
        )
    return None


#: The unique index of the name of each table (migration 241).
_NAME_INDEX: dict[str, str] = {
    "crm_deal_statuses": "uq_crm_deal_statuses_org_name",
    "crm_lead_statuses": "uq_crm_lead_statuses_org_name",
    "crm_lost_reasons": "uq_crm_lost_reasons_org_label",
}


@asynccontextmanager
async def _name_free(table: str, noun: str, name: Any) -> AsyncIterator[None]:
    """The Undo of a rename, with the old name taken again, is a 409
    ``name_taken`` and not a 500 (fix round 2). The PATCH route keeps its
    own behaviour. The transaction rolls back with the refusal."""
    try:
        yield
    except IntegrityError as exc:
        if not _violates(exc, _NAME_INDEX[table]):
            raise
        raise change_log.name_taken(noun, name) from None


def _violates(exc: BaseException, index: str) -> bool:
    """True when ``exc`` is a unique violation (23505) of ``index``.

    It reads the driver's own fields along the exception chain, never the
    message text: that text also holds the SQL and the bound values."""
    codes: set[str] = set()
    names: set[str] = set()
    seen: BaseException | None = exc
    while seen is not None:
        code = getattr(seen, "sqlstate", None) or getattr(seen, "pgcode", None)
        if isinstance(code, str):
            codes.add(code)
        name = getattr(seen, "constraint_name", None) or getattr(
            getattr(seen, "diag", None), "constraint_name", None,
        )
        if isinstance(name, str):
            names.add(name)
        seen = getattr(seen, "orig", None) or seen.__cause__
    return "23505" in codes and index in names


@change_log.undoes("lost_reason")
async def _undo_lost_reason(db: Any, user: Any, logged: Logged) -> None:
    """A create goes, unless a record cites it. A PATCH goes back. Each path
    first checks that the reason still holds the values of this change."""
    row = await _current(db, "crm_lost_reasons", logged.entity_id, logged.area)
    if not change_log.same_values(_snapshot(row, REASON_LOG_KEYS), logged.after):
        raise change_log.changed_since()
    if logged.action == change_log.CREATE:
        held = await reason_in_use(db, str(row.id))
        if held:
            raise change_log.in_use(
                f"The lost reason {quoted(row.label)} is on {held} record(s) now, "
                "so the Undo cannot remove it."
            )
        await delete_lost_reason_in(db, str(row.id))
        await log_change(db, user, Change(
            area="lost_reason", action=change_log.DELETE,
            summary=f"Removed the lost reason {quoted(row.label)}",
            ladder=change_log.TEAM, entity_id=str(row.id),
            before=_snapshot(row, REASON_LOG_KEYS),
        ), undoes=logged.id)
        return None
    async with _name_free(
        "crm_lost_reasons", "lost reason", (logged.before or {}).get("label"),
    ):
        await patch_lost_reason_in(
            db, user, str(row.id), dict(logged.before or {}), undoes=logged.id,
        )
    return None
