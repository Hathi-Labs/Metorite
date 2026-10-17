"""CRM · the suggestions of the mapping pipeline (WS-53 CRM-Q20-2).

Spec: ``project-docs/specs/crm_platform.md`` §18.9.6, "CRM-Q20-2 · The
suggestion, the panel, the overlay and Undo", and §18.9 v4: the admin
approves a result in one or two clicks, sees real records first, and can
undo.

    GET  /crm/mapping/suggestions                  the open suggestions, and
                                                   the slots that a module plays
    POST /crm/mapping/suggestions/{slot}/approve   {"version": n} the module of
                                                   the draft plays the slot
    POST /crm/mapping/suggestions/{slot}/undo      the slot shows its own list
                                                   again
    POST /crm/mapping/suggestions/{slot}/dismiss   {"version": n} the draft's
                                                   module is not offered again

**What a suggestion is.** The mapping pipeline (CRM-Q20-1) writes ``role``,
``target`` and ``stage`` items into the open draft of the plan
(``mapping_plans.open_draft``). A suggestion is a ``role`` of that draft for
a slot that has no approved role, whose module the org mirrors, whose slot
holds no mirrored record (``map_pipeline.slot_empty``), and that the admin
did not dismiss on the schema of today.

**One slot at a time.** An approve takes the items of ONE slot and module
from the draft, and approves them into the version in use through
``mapping_plans.commit``, the one approve. An Undo approves the version in
use with no item of that slot. Each one then moves the open draft onto the
new version (``mapping_plans.carry_draft``), so each pick by hand and each
other suggestion stays. No record moves, so an Undo is exact (option A).

**Dismiss.** ``map_pipeline.dismiss`` records the slot and the module with
the schema hash of the roles of today, in the cursor row
``zoho:map_dismissed`` (no migration). The pipeline then offers the module
again only after the schema changes. An Undo records the same.

⚠️ **Dark twice, and admin only.** The guards of the Field map: the CRM
switch, ``CRM_MIRROR`` (else 404), ``admin:access:manage`` (else 403), and
Mirror mode (else 409 ``not_mirror``). The session binds the organization of
the caller, and the actor comes from ``UserContext`` only (R5, R11).

Fence: ``tests/unit/test_crm_map_suggest_r8.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import _tenant_session, actor, router
from gateway.routes.crm.field_rules import (
    _GUARDS,
    PROVIDER,
    _db_conflict,
    _organization,
    _require_mirror,
)
from gateway.routes.crm.mapping_plans import (
    KIND_STAGE,
    KIND_TARGET,
    _approved,
    _rules_of,
    _version,
    carry_draft,
    commit,
    kind_key,
    lock_plan,
    merge_kinds,
    open_draft,
    roles_of,
    split_rules,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

#: A core slot, as the items of the kinds name it.
SlotWord = Literal["company", "contact", "lead", "deal"]

_COUNT_SQL = (
    "SELECT count(*) FROM crm_module_records WHERE module = :m AND ext_source = :src"
)
_LABELS_SQL = (
    "SELECT key, label FROM crm_field_defs WHERE entity = :e AND source = :src"
)
#: A draft that a dismissal leaves with nothing in it. No other route
#: deletes a version, and only a draft can go.
_DELETE_DRAFT_SQL = "DELETE FROM crm_mapping_plans WHERE version = :v AND state = 'draft'"


class SuggestionTargetModel(BaseModel):
    #: The column of the slot (``name``, ``email`` ...).
    target: str
    #: The fields of the module, by API name, and as Zoho labels them.
    fields: list[str]
    labels: list[str]
    #: Of the sampled records, the filled values that did not map.
    unmapped: int | None = None


class SuggestionStageModel(BaseModel):
    field: str
    label: str
    #: Each value of the field, and its stage word: ``won``, ``lost``, ``open``.
    values: dict[str, str]
    #: The values of the records with no entry.
    unsorted: int | None = None


class SuggestionModel(BaseModel):
    slot: str
    #: The open draft that holds the suggestion.
    version: int
    module: str
    #: The module as Zoho names it.
    label: str
    #: The mirrored records of the module.
    records: int
    targets: list[SuggestionTargetModel]
    stage: SuggestionStageModel | None = None


class OverlayModel(BaseModel):
    """A slot that a module plays now. Undo puts the slot's own list back."""

    slot: str
    module: str
    label: str


class SuggestionsModel(BaseModel):
    suggestions: list[SuggestionModel]
    overlays: list[OverlayModel]


class SuggestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The open draft that holds the suggestion.
    version: int


class SuggestionDoneModel(BaseModel):
    slot: str
    module: str
    #: The version in use after the change.
    version: int


def _items_of(others: Sequence[Mapping[str, Any]], slot: str, module: str) -> list[dict]:
    """The items of the kinds of one slot and module."""
    return [dict(o) for o in others if kind_key(o) is not None
            and o.get("slot") == slot and o.get("module") == module]


async def _field_labels(db: Any, module: str) -> dict[str, str]:
    from gateway.crm_sources.zoho import mapping

    rows = (await db.execute(text(_LABELS_SQL), {
        "e": mapping.module_entity(module), "src": PROVIDER,
    })).fetchall()
    return {str(r.key): str(r.label or r.key) for r in rows}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def _suggestion(
    db: Any, version: int, slot: str, module: str, row: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> SuggestionModel:
    labels = await _field_labels(db, module)
    count = int((await db.execute(text(_COUNT_SQL), {
        "m": module, "src": PROVIDER})).scalar() or 0)
    targets = [
        SuggestionTargetModel(
            target=str(i["target"]), fields=[str(f) for f in i.get("fields") or []],
            labels=[labels.get(str(f), str(f)) for f in i.get("fields") or []],
            unmapped=_int(i.get("unmapped")),
        )
        for i in items if i.get("kind") == KIND_TARGET
    ]
    stage = next((i for i in items if i.get("kind") == KIND_STAGE), None)
    return SuggestionModel(
        slot=slot, version=version, module=module,
        label=str(row.get("label") or module), records=count, targets=targets,
        stage=SuggestionStageModel(
            field=str(stage["field_api_name"]),
            label=labels.get(str(stage["field_api_name"]), str(stage["field_api_name"])),
            values={str(k): str(v) for k, v in (stage.get("values") or {}).items()},
            unsorted=_int(stage.get("unsorted")),
        ) if stage is not None else None,
    )


@router.get("/mapping/suggestions", response_model=SuggestionsModel, dependencies=_GUARDS)
async def list_suggestions(user: UserContext = Depends(get_current_user)) -> SuggestionsModel:
    """The open suggestions, one for each slot, and the slots that a module
    plays now. It reads only."""
    from gateway.crm_sync import map_pipeline, runs
    from gateway.routes.crm.modules import mirrored_modules

    org = _organization(user)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        approved = await _approved(db)
        if approved is None:
            return SuggestionsModel(suggestions=[], overlays=[])
        mirrored = await mirrored_modules(db)
        _fields, others = split_rules(_rules_of(approved))
        in_use = roles_of(others)
        overlays = [
            OverlayModel(slot=slot, module=module,
                         label=str(mirrored[module].get("label") or module))
            for slot, module in sorted(in_use.items()) if module in mirrored
        ]
        draft = await open_draft(db, approved)
        suggestions: list[SuggestionModel] = []
        if draft is not None:
            cursors = await runs.read_cursors(db, org, "zoho:")
            digest = map_pipeline.slots_digest(
                getattr(cursors.get(map_pipeline.CURSOR_ROW), "state", None))
            dismissed = map_pipeline.dismissed_of(
                getattr(cursors.get(map_pipeline.DISMISSED_ROW), "state", None))
            _drafted, drafted = split_rules(_rules_of(draft))
            for slot, module in sorted(roles_of(drafted).items()):
                if slot in in_use or module not in mirrored:
                    continue
                if map_pipeline.is_dismissed(dismissed, slot, module, digest):
                    continue
                if not await map_pipeline.slot_empty(db, slot, cursors):
                    continue
                suggestions.append(await _suggestion(
                    db, int(draft.version), slot, module, mirrored[module],
                    _items_of(drafted, slot, module)))
    return SuggestionsModel(suggestions=suggestions, overlays=overlays)


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


async def _open_suggestion(db: Any, approved: Any, version: int, slot: str) -> tuple[Any, str]:
    """The draft *version* and the module of its role for *slot*. The draft
    must be the open draft (``mapping_plans.open_draft``): 409 ``not_draft``
    or ``plan_moved``, and 404 ``no_suggestion`` when it names no module for
    the slot.

    ⚠️ Round 2 review P1: the base alone is not enough. An older draft on the
    same base passes that test, and ``carry_draft`` of it then drops each
    pick of the newer draft. So only the newest draft on the version in use
    is open."""
    draft = await _version(db, version)
    if draft.state != "draft":
        raise _conflict("not_draft", "This suggestion is no longer open.")
    current = await open_draft(db, approved)
    if (draft.based_on != approved.version or current is None
            or int(current.version) != int(draft.version)):
        raise _conflict("plan_moved", "The field map changed after this suggestion.")
    module = roles_of(split_rules(_rules_of(draft))[1]).get(slot)
    if module is None:
        raise HTTPException(status_code=404, detail={
            "code": "no_suggestion", "message": "Nothing is suggested for this list.",
        })
    return draft, module


@router.post(
    "/mapping/suggestions/{slot}/approve", response_model=SuggestionDoneModel,
    dependencies=_GUARDS,
)
async def approve_suggestion(
    slot: SlotWord, payload: SuggestionIn, user: UserContext = Depends(get_current_user),
) -> SuggestionDoneModel:
    """The module of the draft plays *slot*: its role, targets and stage
    join the version in use. Nothing else of the draft is approved, and the
    rest of the draft stays open."""
    org = _organization(user)
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            approved = await lock_plan(db, by)
            draft, module = await _open_suggestion(db, approved, payload.version, slot)
            items = _items_of(split_rules(_rules_of(draft))[1], slot, module)
            fields, others = split_rules(_rules_of(approved))
            row = await commit(
                db, org=org, fields=fields, others=merge_kinds(others, items), by=by,
                based_on=int(approved.version),
            )
            await carry_draft(db, draft, row)
    except DBAPIError as exc:
        conflict = _db_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return SuggestionDoneModel(slot=slot, module=module, version=int(row.version))


@router.post(
    "/mapping/suggestions/{slot}/undo", response_model=SuggestionDoneModel,
    dependencies=_GUARDS,
)
async def undo_overlay(
    slot: SlotWord, user: UserContext = Depends(get_current_user),
) -> SuggestionDoneModel:
    """*slot* shows its own list again: the version in use is approved with
    no item of the slot. The module is not offered again until the schema
    changes. No record moved, so the Undo is exact."""
    from gateway.crm_sync import map_pipeline

    org = _organization(user)
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            approved = await lock_plan(db, by)
            fields, others = split_rules(_rules_of(approved))
            module = roles_of(others).get(slot)
            if module is None:
                raise HTTPException(status_code=404, detail={
                    "code": "no_overlay", "message": "No module plays this list.",
                })
            kept = [o for o in others
                    if not (kind_key(o) is not None and o.get("slot") == slot)]
            draft = await open_draft(db, approved)
            row = await commit(db, org=org, fields=fields, others=kept, by=by,
                               based_on=int(approved.version))
            if draft is not None:
                await carry_draft(db, draft, row, drop_slot=slot)
            await map_pipeline.dismiss(db, org, slot, module)
    except DBAPIError as exc:
        conflict = _db_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return SuggestionDoneModel(slot=slot, module=module, version=int(row.version))


@router.post(
    "/mapping/suggestions/{slot}/dismiss", response_model=SuggestionDoneModel,
    dependencies=_GUARDS,
)
async def dismiss_suggestion(
    slot: SlotWord, payload: SuggestionIn, user: UserContext = Depends(get_current_user),
) -> SuggestionDoneModel:
    """Do not offer the module of the draft for *slot* again, until the
    schema changes. The open draft loses the items of the slot, so the Field
    map card cannot approve them either."""
    from gateway.crm_sync import map_pipeline

    org = _organization(user)
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            approved = await lock_plan(db, by)
            draft, module = await _open_suggestion(db, approved, payload.version, slot)
            await map_pipeline.dismiss(db, org, slot, module)
            # The draft is the open draft (`_open_suggestion`), so the card
            # can never approve the items of the dismissed slot.
            carried = await carry_draft(db, draft, approved, drop_slot=slot)
            if carried is None:
                await db.execute(text(_DELETE_DRAFT_SQL), {"v": int(draft.version)})
    except DBAPIError as exc:
        conflict = _db_conflict(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return SuggestionDoneModel(slot=slot, module=module, version=int(approved.version))


__all__ = [
    "approve_suggestion",
    "dismiss_suggestion",
    "list_suggestions",
    "undo_overlay",
]
