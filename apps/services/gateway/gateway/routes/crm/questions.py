"""CRM · the open questions (WS-53 CRM-Q32, ``crm_platform.md`` §18.14).

    GET  /crm/questions                      the open questions, ranked
                                             (``?all=1`` adds each part in use)

The CRM shows the admin each question that Jev could not answer with
confidence about the data of the org. The kinds, the gate of ``open``, the
order, the ids and the sentences of why live in ``crm_sync/questions.py`` as
pure functions. This module reads their inputs, in ONE ``_tenant_session()``:
the catalogue (``catalogue._load``), the approved plan and the open draft,
the cursor rows of the pipeline and of the questions, the empty slots, the
values of each approved stage field, and the masked samples of the modules
that the body shows.

**The masked samples.** The read takes the 200 records with the lowest ids
of each module that a shown question names, as ``map_pipeline`` does, and
shows 3 values of each choice through ``map_pipeline.masked``. A person's
email, phone or name shows as its pattern class only. Only an admin reads
them.

⚠️ **Dark twice, and admin only.** The guards of the Field map: the CRM
switch, ``CRM_MIRROR`` (else 404), ``admin:access:manage`` (else 403), and
Mirror mode (else 409 ``not_mirror``). An org outside
``CRM_QUESTIONS_ORGS`` (``flags.questions_allowed``) gets 404
``questions_off``. The session binds the organization of the caller, and the
org comes from ``UserContext`` only (R5, R11).

Fences: ``tests/unit/test_crm_questions.py`` and
``tests/unit/test_crm_questions_r8.py`` (R8).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query
from gateway.crm_sync import questions as qs
from gateway.routes.crm.core import _tenant_session, from_jsonb, router
from gateway.routes.crm.field_rules import _GUARDS, _organization, _require_mirror
from gateway.routes.crm.flags import questions_allowed
from pydantic import BaseModel, Field
from sqlalchemy import text

_log = get_logger("crm.questions")

#: The code of the 404 that an org outside ``CRM_QUESTIONS_ORGS`` gets.
QUESTIONS_OFF = "questions_off"

#: The records of each module that the samples read: the lowest ids.
SAMPLE_ROWS = 200

#: The most questions one read shows (``?limit=``).
MAX_LIMIT = 200

#: The lowest ids of each module, with the org in the predicate and RLS
#: under it. The module names are bound.
_SAMPLES_SQL = (
    "SELECT module, name, fields FROM (SELECT module, name, fields, "
    "row_number() OVER (PARTITION BY module ORDER BY id) AS rn "
    "FROM crm_module_records WHERE organization_id = CAST(:org AS uuid) "
    "AND module = ANY(CAST(:modules AS text[])) AND ext_source = 'zoho') s "
    "WHERE rn <= :n ORDER BY module, rn"
)

#: The options of the stage fields, for the hash of a stage question.
_OPTIONS_SQL = (
    "SELECT entity, key, options FROM crm_field_defs "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND entity = ANY(CAST(:entities AS text[])) AND key = ANY(CAST(:keys AS text[]))"
)


def questions_off() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": QUESTIONS_OFF,
        "message": "The open questions are off for this organization.",
    })


# ── The inputs ───────────────────────────────────────────────────────────────


async def _suggested(db: Any, others: Sequence[Mapping[str, Any]], mirrored: Any,
                     digest: str | None, dismissed: Mapping[str, Any]) -> set[str]:
    """The slots that the open draft suggests a module for, by the rule of
    ``suggestions.list_suggestions``: a role of the draft for a slot with no
    approved role, whose module the org mirrors, and that the admin did not
    dismiss on the schema of today."""
    from gateway.crm_sync import map_pipeline as mp
    from gateway.routes.crm.mapping_plans import (
        _approved,
        _rules_of,
        open_draft,
        roles_of,
        split_rules,
    )

    approved = await _approved(db)
    draft = await open_draft(db, approved) if approved is not None else None
    if draft is None:
        return set()
    in_use = roles_of(others)
    drafted = roles_of(split_rules(_rules_of(draft))[1])
    return {
        slot for slot, module in drafted.items()
        if slot not in in_use and module in mirrored
        and not mp.is_dismissed(dismissed, slot, module, digest)
    }


async def _stages(db: Any, org: str, others: Sequence[Mapping[str, Any]],
                  labels: Mapping[str, str]) -> list[qs.StageState]:
    """Each approved ``stage`` item, with the values of its field that the
    records hold (``field_sql.value_counts``) and the hash of its options."""
    from gateway.crm_sources.zoho.mapping import module_entity
    from gateway.crm_sync.map_pipeline import DISTINCT_LIMIT
    from gateway.routes.crm.field_sql import value_counts

    items = [o for o in others if o.get("kind") == "stage"
             and isinstance(o.get("module"), str) and isinstance(o.get("field_api_name"), str)]
    if not items:
        return []
    options: dict[tuple[str, str], Any] = {}
    rows = (await db.execute(text(_OPTIONS_SQL), {
        "org": org, "entities": [module_entity(str(i["module"])) for i in items],
        "keys": [str(i["field_api_name"]) for i in items],
    })).fetchall()
    for r in rows:
        options[(str(r.entity), str(r.key))] = from_jsonb(r.options)
    out = []
    for item in items:
        module, key = str(item["module"]), str(item["field_api_name"])
        values = item.get("values") if isinstance(item.get("values"), Mapping) else {}
        out.append(qs.StageState(
            slot=str(item.get("slot")), module=module, label=labels.get(module, module),
            field=key, values={str(k): str(v) for k, v in values.items()},
            seen=await value_counts(db, org, module, key, DISTINCT_LIMIT),
            options_hash=qs.options_hash(options.get((module_entity(module), key)), key),
        ))
    return out


async def load(db: Any, org: str, *, now: datetime | None = None,
               full: bool = True, loaded: Any = None) -> qs.Inputs | None:
    """The inputs of the questions of *org*, in the caller's tenant session.
    ``None`` when the CRM is not a Mirror. With *full* False, only the
    inputs of the ``purpose`` and ``key_field`` questions: the catalogue,
    the mode, the cache and the dismissals. *loaded* is the answer of
    ``catalogue._load`` when the caller read it already."""
    from gateway.crm_sync import catalogue as cat
    from gateway.crm_sync import executor, runs
    from gateway.crm_sync import map_pipeline as mp
    from gateway.routes.crm.catalogue import _load
    from gateway.routes.crm.mapping_plans import roles_of
    from gateway.routes.crm.modules import mirrored_of

    mode, sources, roles, others = loaded if loaded is not None else await _load(db, org)
    if mode != "mirror":
        return None
    entries = cat.build(sources, roles, others)
    cursors = await runs.read_cursors(db, org, "zoho:")

    def state(row: str) -> Any:
        return getattr(cursors.get(row), "state", None)

    inputs = qs.Inputs(
        entries=entries, sources={s.module: s for s in sources},
        stored=cat.stored_items(others), mode=mp.modes_now()[mp.FEATURE_CATALOGUE],
        cache=mp.Cache.of(state(mp.CURSOR_ROW)), now=now or datetime.now(UTC),
        dismissed=qs.dismissed_of(state(qs.QUESTIONS_ROW)),
    )
    if not full:
        return inputs
    from gateway.crm_sync import profiles

    latest = await profiles.latest(db, org)
    mirrored = mirrored_of(latest.profile if latest is not None else None)
    digest = mp.slots_digest(state(mp.CURSOR_ROW))
    dismissed_roles = mp.dismissed_of(state(mp.DISMISSED_ROW))
    approved_roles = roles_of(others)
    suggested = await _suggested(db, others, mirrored, digest, dismissed_roles)
    slots = []
    for slot in executor.SLOTS:
        role = approved_roles.get(slot)
        empty = (role is None and slot not in suggested
                 and await mp.slot_empty(db, slot, cursors))
        slots.append(qs.SlotState(slot=slot, empty=empty, role=role,
                                  suggested=slot in suggested))
    labels = {e.module: e.label for e in entries}
    return replace(
        inputs, slots=tuple(slots), doubts=dict(inputs.cache.doubts),
        dismissed_roles=dismissed_roles, slots_digest=digest,
        stages=tuple(await _stages(db, org, others, labels)),
    )


async def samples(db: Any, org: str, inputs: qs.Inputs,
                  questions: Sequence[qs.Question]) -> qs.Inputs:
    """*inputs* with the masked samples of each choice and the masked record
    names of each slot candidate, of the modules that *questions* name. ONE
    statement, the 200 lowest ids of each module."""
    from gateway.crm_sync import executor
    from gateway.crm_sync.map_pipeline import masked

    modules = [m for m in qs.sampled_modules(questions)
               if getattr(inputs.sources.get(m), "kind", None) == "module"]
    if not modules:
        return inputs
    wanted: dict[str, set[str]] = {}
    for q in questions:
        if q.kind == qs.KIND_KEY and q.module:
            wanted.setdefault(q.module, set()).update(c.value for c in q.choices)
    rows = (await db.execute(text(_SAMPLES_SQL), {
        "org": org, "modules": modules, "n": SAMPLE_ROWS})).fetchall()
    values: dict[str, dict[str, list[str]]] = {}
    names: dict[str, list[str]] = {}
    for r in rows:
        module = str(r.module)
        fields = from_jsonb(r.fields) or {}
        src = inputs.sources.get(module)
        shown = fields.get(src.display_field) if src is not None and src.display_field else None
        name = shown if not executor.blank(shown) else r.name
        if not executor.blank(name) and len(names.setdefault(module, [])) < qs.SAMPLE_LIMIT:
            names[module].append(masked(name))
        for key in wanted.get(module, ()):
            found = values.setdefault(module, {}).setdefault(key, [])
            value = fields.get(key) if isinstance(fields, Mapping) else None
            if len(found) < qs.SAMPLE_LIMIT and not executor.blank(value):
                found.append(masked(value))
    return qs.with_samples(inputs, values, names)


async def count_open(db: Any, org: str) -> int:
    """The count of the open questions of *org* (Sync health and the
    questions step), in the caller's tenant session. 0 outside
    ``CRM_QUESTIONS_ORGS`` and in Native mode."""
    if not questions_allowed(org):
        return 0
    inputs = await load(db, org)
    return len(qs.build(inputs)) if inputs is not None else 0


async def module_question_ids(db: Any, org: str | None,
                              loaded: Any = None) -> dict[str, list[str]]:
    """The ids of the open ``purpose`` and ``key_field`` questions of each
    module (the catalogue entry gains ``questions``, §18.14.5). Empty outside
    ``CRM_QUESTIONS_ORGS``. It reads the cursor rows once. *loaded* is the
    answer of ``catalogue._load`` when the caller read it already."""
    if not org or not questions_allowed(str(org)):
        return {}
    inputs = await load(db, str(org), full=False, loaded=loaded)
    if inputs is None:
        return {}
    out: dict[str, list[str]] = {}
    for q in qs.module_questions(inputs):
        out.setdefault(str(q.module), []).append(q.id)
    return out


# ── The wire ────────────────────────────────────────────────────────────────


class QuestionChoiceModel(BaseModel):
    value: str
    label: str
    best: bool = False
    #: A ``key_field`` choice: its share of filled records, and its masked
    #: samples.
    fill: float | None = None
    samples: list[str] | None = None
    #: A ``slot`` choice: its record count.
    records: int | None = None
    #: A ``revenue`` choice: the labels of its modules.
    modules: list[str] | None = None


class QuestionOptionModel(BaseModel):
    value: str
    label: str
    fill: float | None = None


class QuestionModel(BaseModel):
    """One question (§18.14.3, "The wire")."""

    id: str
    kind: str
    module: str | None = None
    label: str
    part: str | None = None
    #: ``open``, ``unsure``, ``stale``, or ``answered`` for a part in use.
    reason: str
    records: int
    money: bool
    why: str
    choices: list[QuestionChoiceModel]
    none: bool
    other: bool
    #: The answer in use, for a correction. ``None`` on an open question.
    current: str | None = None
    #: Who chose the answer in use: ``admin``, ``ai`` or ``code``.
    by: str | None = None
    #: False for ``revenue``, which takes no dismissal.
    dismissible: bool = True
    #: Each choice of "Pick another".
    options: list[QuestionOptionModel] | None = None
    slot: str | None = None
    count: int | None = None
    value: str | None = None
    field: str | None = None


class QuestionsModel(BaseModel):
    #: The count of the open questions.
    open: int
    #: The first ``limit`` open questions, by impact.
    shown: list[QuestionModel]
    #: The open questions past ``limit``.
    more: int
    #: With ``all=1``: each part in use, with ``current``.
    answered: list[QuestionModel] = Field(default_factory=list)
    #: The approved version of the plan that the read saw.
    plan: int | None = None


def _model(q: qs.Question) -> QuestionModel:
    return QuestionModel(**qs.wire(q))


@router.get("/questions", response_model=QuestionsModel, dependencies=_GUARDS)
async def list_questions(
    everything: bool = Query(False, alias="all"),
    limit: int = Query(qs.SHOWN, ge=1, le=MAX_LIMIT),
    user: UserContext = Depends(get_current_user),
) -> QuestionsModel:
    """The open questions of the caller's org, by impact (§18.14.7). It
    reads only. ``all=1`` adds each part in use, for a correction."""
    from gateway.routes.crm.mapping_plans import _approved

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        inputs = await load(db, org)
        if inputs is None:
            return QuestionsModel(open=0, shown=[], more=0)
        found = qs.build(inputs)
        extra = qs.answered(inputs) if everything else []
        inputs = await samples(db, org, inputs, [*found[:limit], *extra])
        approved = await _approved(db)
    # The samples change no id and no order, so the read builds once more
    # with them, and with no new statement.
    found = qs.build(inputs)
    return QuestionsModel(
        open=len(found), more=max(0, len(found) - limit),
        shown=[_model(q) for q in found[:limit]],
        answered=[_model(q) for q in (qs.answered(inputs) if everything else [])],
        plan=int(approved.version) if approved is not None else None,
    )


__all__ = [
    "QUESTIONS_OFF",
    "QuestionModel",
    "QuestionsModel",
    "count_open",
    "list_questions",
    "load",
    "module_question_ids",
    "questions_off",
    "samples",
]
