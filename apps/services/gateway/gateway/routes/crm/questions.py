"""CRM · the open questions (WS-53 CRM-Q32, ``crm_platform.md`` §18.14).

    GET  /crm/questions                      the open questions, ranked
                                             (``?all=1`` adds each part in use)
    POST /crm/questions/{id}/answer          {"value": <choice>} answers one
    POST /crm/questions/{id}/dismiss         "Not relevant"
    POST /crm/questions/accept-best          {"ids": [...], "dry_run": true}

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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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


async def open_parts(db: Any, org: str | None, module: str) -> frozenset[str]:
    """The key parts of *module* that are an open question (CRM-Q32-6),
    for the 422 ``no_key_field`` of the aggregate. Empty outside
    ``CRM_QUESTIONS_ORGS``. It reads in the caller's tenant session."""
    ids = (await module_question_ids(db, org)).get(module, [])
    return frozenset(qid.rsplit(":", 1)[1] for qid in ids if qid.startswith("key:"))


# ── The questions step and My Day (CRM-Q32-4, §18.14.5) ────────────────────


async def questions_after_run(org: str, *, now: datetime | None = None) -> int | None:
    """The questions step after a sync run, for *org*. Never raises.

    It runs for an org where ``questions_allowed`` is true, and no mode of
    the mapping pipeline gates it. It builds the open ids and compares them
    with ``seen`` of ``zoho:questions``. It writes ``seen``, ``money`` and,
    only when an id is new, ``new_at`` and ``new``. So an answer that only
    closes questions sets no ``new_at``. The org comes from the scheduler,
    never from a request (R5). Returns the open count, or ``None``."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.crm_sync import runs

    if not questions_allowed(org):
        return None
    token = bind_tenant(org)
    try:
        moment = now or datetime.now(UTC)
        async with _tenant_session(org) as db:
            inputs = await load(db, org, now=moment)
            if inputs is None:
                return None
            found = qs.build(inputs)
            cursors = await runs.read_cursors(db, org, qs.QUESTIONS_ROW)
            state = getattr(cursors.get(qs.QUESTIONS_ROW), "state", None)
            keys = qs.step_state(state, [q.id for q in found], qs.count_money(found), moment)
            await runs.merge_cursor_keys(db, org, qs.QUESTIONS_ROW, keys)
        _log.info("crm.questions.step", organization_id=org, open=len(found),
                  new=int(keys.get("new") or 0), money=bool(keys["money"]))
        return len(found)
    except Exception as exc:  # the step never breaks the sync loop
        _log.warning("crm.questions.step_failed", organization_id=org,
                     error_type=type(exc).__name__)
        return None
    finally:
        release_tenant(token)


async def questions_card(db: Any, org: str, now: datetime) -> dict[str, Any] | None:
    """The My Day card of the questions, or ``None`` (§18.14.5). It reads
    ONE cursor row, ``zoho:questions``, in the caller's session, and builds
    no question. The caller checks ``questions_allowed`` and the admin."""
    from gateway.crm_sync import runs

    cursors = await runs.read_cursors(db, org, qs.QUESTIONS_ROW)
    return qs.card_of(getattr(cursors.get(qs.QUESTIONS_ROW), "state", None), now)


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


# ── Answering (CRM-Q32-2, §18.14.6) ─────────────────────────────────────────


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def question_moved() -> HTTPException:
    """409 ``question_moved``: the question is no longer open, so the card
    reads the list again."""
    return _conflict("question_moved", "This question is no longer open.")


def plan_moved() -> HTTPException:
    return _conflict("plan_moved", "The mapping plan changed after this question.")


class AnswerIn(BaseModel):
    """``{"value": <choice>}``. ``null`` is "None of these". ``version`` is
    the plan version that the card read (``GET /crm/questions`` ``plan``):
    a plan that moved gives 409 ``plan_moved``."""

    model_config = ConfigDict(extra="forbid")

    value: str | None
    version: int | None = None


class UndoModel(BaseModel):
    """What the Undo of an answer sends. With ``question``, the answer route
    of that id again with ``value`` (a ``stage_value``). Else
    ``PUT /crm/catalogue/{module}`` with ``{part: value}``."""

    module: str | None = None
    part: str | None = None
    value: str | None = None
    question: str | None = None


class AnswerOut(BaseModel):
    id: str
    kind: str
    #: The approved version of the plan after the answer, or the open draft
    #: of a ``slot`` answer (its panel approves it).
    version: int | None = None
    #: What changed, for the toast.
    line: str | None = None
    undo: UndoModel | None = None
    #: A ``stage_value`` answer: the records that leave Unsorted.
    moved: int | None = None
    slot: str | None = None
    module: str | None = None


def _answer_line(q: qs.Question, value: Any) -> str | None:
    if value is None:
        return None
    choice = next((c for c in q.choices if c.value == value), None)
    if choice is None and q.kind in (qs.KIND_PURPOSE, qs.KIND_KEY):
        choice = next((c for c in q.options if c.value == value), None)
    return qs.applied_line(q, choice) if choice is not None and q.kind in (
        qs.KIND_PURPOSE, qs.KIND_KEY) else None


async def _open_question(db: Any, org: str, question_id: str) -> tuple[qs.Inputs, qs.Question]:
    """The inputs and the open question *question_id*, or 409
    ``question_moved``."""
    inputs = await load(db, org)
    found = next((q for q in qs.build(inputs) if q.id == question_id),
                 None) if inputs is not None else None
    if inputs is None or found is None:
        raise question_moved()
    return inputs, found


async def _commit_stage(db: Any, org: str, by: str, approved: Any, slot: str,
                        module: str | None, change: Any) -> Any:
    """Commit the approved ``stage`` item of *slot* with *change* applied to
    its values, then carry the open draft, as ``approve_suggestion`` does.
    409 ``plan_moved`` when the stage item of the question is gone."""
    from gateway.routes.crm.mapping_plans import (
        _rules_of,
        carry_draft,
        commit,
        merge_kinds,
        open_draft,
        split_rules,
    )

    fields, others = split_rules(_rules_of(approved))
    stage = next((o for o in others if o.get("kind") == "stage" and o.get("slot") == slot
                  and (module is None or o.get("module") == module)), None)
    if stage is None:
        raise plan_moved()
    values = dict(stage.get("values") or {})
    change(values)
    draft = await open_draft(db, approved)
    row = await commit(db, org=org, fields=fields,
                       others=merge_kinds(others, [{**stage, "values": values}]), by=by,
                       based_on=int(approved.version))
    if draft is not None:
        await carry_draft(db, draft, row)
    return row, stage


async def _undo_stage(db: Any, org: str, by: str, approved: Any, question_id: str) -> AnswerOut:
    """The Undo of a ``stage_value`` answer: commit the item without the
    value, the same way (CRM-Q32-2 item 7)."""
    parts = question_id.split(":")
    if len(parts) != 3:
        raise question_moved()
    from gateway.routes.crm.mapping_plans import _rules_of, split_rules

    others = split_rules(_rules_of(approved))[1]
    stage = next((o for o in others if o.get("kind") == "stage" and o.get("slot") == parts[1]),
                 None)
    value = qs.stage_for(stage, question_id) if stage is not None else None
    if value is None:
        raise question_moved()
    row, _stage = await _commit_stage(db, org, by, approved, parts[1], stage.get("module"),
                                      lambda values: values.pop(value, None))
    return AnswerOut(id=question_id, kind=qs.KIND_STAGE, version=int(row.version),
                     slot=parts[1], module=str(stage.get("module")))


async def _answer_slot(db: Any, org: str, by: str, q: qs.Question,
                       value: str | None) -> AnswerOut:
    """A ``slot`` answer. "None of these" records the id in
    ``zoho:questions`` with the hash of the roles, and never
    ``map_pipeline.dismiss``, because that row keeps one module for each
    slot. A module writes the ``role`` item into the open draft, by the
    admin. Its panel shows real records first, then approves it."""
    from gateway.crm_sync import profiles, runs
    from gateway.routes.crm.mapping_plans import _approved, open_draft, write_draft

    if value is None:
        await runs.merge_cursor_entry(db, org, qs.QUESTIONS_ROW, "dismissed", q.id,
                                      {"schema": q.hash})
        return AnswerOut(id=q.id, kind=q.kind, slot=q.slot)
    latest = await profiles.latest(db, org)
    item = {"kind": "role", "slot": q.slot, "module": value,
            "profile_version": int(latest.version) if latest is not None else 0}
    row = await write_draft(db, items=[item], by=by, org=org)
    if row is None:
        approved = await _approved(db)
        row = await open_draft(db, approved) if approved is not None else None
    return AnswerOut(id=q.id, kind=q.kind, slot=q.slot, module=value,
                     version=int(row.version) if row is not None else None)


async def _answer(db: Any, org: str, by: str, question_id: str, payload: AnswerIn) -> AnswerOut:
    """One answer, under the plan lock (§18.14.6, the table)."""
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.catalogue import AUTO, bad_answer, write_answer
    from gateway.routes.crm.mapping_plans import lock_plan

    approved = await lock_plan(db, by)
    if payload.version is not None and int(payload.version) != int(approved.version):
        raise plan_moved()
    if question_id.startswith("stage:") and payload.value == AUTO:
        return await _undo_stage(db, org, by, approved, question_id)
    inputs, q = await _open_question(db, org, question_id)
    if qs.answer_problem(q, payload.value, inputs.sources.get(q.module or "")) is not None:
        raise bad_answer("That is not an answer of this question.", q.part)
    if q.kind == qs.KIND_SLOT:
        return await _answer_slot(db, org, by, q, payload.value)
    if q.kind == qs.KIND_STAGE:
        row, _stage = await _commit_stage(
            db, org, by, approved, str(q.slot), q.module,
            lambda values: values.__setitem__(str(q.value), str(payload.value)))
        return AnswerOut(id=q.id, kind=q.kind, version=int(row.version), moved=q.count,
                         slot=q.slot, module=q.module,
                         undo=UndoModel(module=q.module, question=q.id, value=AUTO))
    item = qs.answer_item(q, payload.value)
    module = str(item["module"])
    parts = {k: item[k] for k in cat.parts_of(module) if k in item}
    row = await write_answer(db, org, by=by, module=module, parts=parts)
    part = next(iter(parts))
    return AnswerOut(
        id=q.id, kind=q.kind, version=int((row or approved).version),
        line=_answer_line(q, payload.value), module=module,
        undo=UndoModel(module=module, part=part, value=AUTO),
    )


def _guard_db(exc: DBAPIError) -> HTTPException | None:
    from gateway.routes.crm.field_rules import _db_conflict

    return _db_conflict(exc)


@router.post("/questions/{question_id}/answer", response_model=AnswerOut,
             dependencies=_GUARDS)
async def answer_question(
    question_id: str, payload: AnswerIn, user: UserContext = Depends(get_current_user),
) -> AnswerOut:
    """Answer one open question (§18.14.6). A ``purpose``, a ``key_field``
    and ``revenue`` apply at once through the one writer
    (``catalogue.write_answer``). A ``slot`` writes its role into the open
    draft. A ``stage_value`` commits the stage item with the value.

    422 ``bad_answer`` for a value that is not a choice. 409
    ``question_moved`` for a question that is no longer open, and
    ``plan_moved`` for a plan that moved. The actor comes from
    ``UserContext`` only (R5, R11)."""
    from gateway.routes.crm.core import actor

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            return await _answer(db, org, by, question_id, payload)
    except DBAPIError as exc:
        conflict = _guard_db(exc)
        if conflict is not None:
            raise conflict from exc
        raise


class DismissOut(BaseModel):
    id: str
    dismissed: bool = True


@router.post("/questions/{question_id}/dismiss", response_model=DismissOut,
             dependencies=_GUARDS)
async def dismiss_question(
    question_id: str, user: UserContext = Depends(get_current_user),
) -> DismissOut:
    """"Not relevant": record the id in ``zoho:questions`` with the hash of
    the question. A changed hash asks again. ``revenue`` takes no
    dismissal, because it has a default (§18.14.6)."""
    from gateway.crm_sync import runs
    from gateway.routes.crm.catalogue import bad_answer

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        _inputs, q = await _open_question(db, org, question_id)
        if q.hash is None:
            raise bad_answer("The meaning of revenue takes no dismissal. It has a default.")
        await runs.merge_cursor_entry(db, org, qs.QUESTIONS_ROW, "dismissed", q.id,
                                      {"schema": q.hash})
    return DismissOut(id=q.id)


class AcceptBestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=MAX_LIMIT)
    dry_run: bool = True
    version: int | None = None


class BestGuessModel(BaseModel):
    id: str
    label: str
    part: str | None = None
    value: str
    value_label: str
    #: What changes, for the dry run.
    line: str


class AcceptBestOut(BaseModel):
    dry_run: bool
    items: list[BestGuessModel]
    #: The approved version after the batch. ``None`` on a dry run.
    version: int | None = None
    #: The Undo of the batch: ``"@auto"`` for each part.
    undo: list[UndoModel] = Field(default_factory=list)


def _best_guesses(inputs: qs.Inputs, ids: Sequence[str]) -> list[tuple[qs.Question, qs.Choice]]:
    """Jev's best guess of each id. 409 ``question_moved`` for an id that is
    not open, and 422 for one that may not join a batch."""
    from gateway.routes.crm.catalogue import bad_answer

    found = {q.id: q for q in qs.build(inputs)}
    out = []
    for qid in dict.fromkeys(ids):
        q = found.get(qid)
        if q is None:
            raise question_moved()
        best = q.best()
        if not qs.batch_joins(q) or best is None:
            raise bad_answer("Only a purpose or a key field with a best guess joins a batch.",
                             q.part)
        out.append((q, best))
    return out


@router.post("/questions/accept-best", response_model=AcceptBestOut, dependencies=_GUARDS)
async def accept_best(
    payload: AcceptBestIn, user: UserContext = Depends(get_current_user),
) -> AcceptBestOut:
    """Accept Jev's best guesses (§18.14.6, "Batch"). ``dry_run`` reads and
    writes nothing. Without it, ONE ``apply_catalogue`` call writes one plan
    version. Only ``purpose`` and ``key_field`` questions with a best guess
    join, so a ``slot`` id gives 422."""
    from gateway.routes.crm.catalogue import AUTO
    from gateway.routes.crm.core import actor
    from gateway.routes.crm.mapping_plans import apply_catalogue, lock_plan

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            approved = None if payload.dry_run else await lock_plan(db, by)
            if (approved is not None and payload.version is not None
                    and int(payload.version) != int(approved.version)):
                raise plan_moved()
            inputs = await load(db, org)
            if inputs is None:
                raise question_moved()
            guesses = _best_guesses(inputs, payload.ids)
            items = [qs.answer_item(q, best.value) for q, best in guesses]
            row = None
            if not payload.dry_run:
                row = await apply_catalogue(db, items, by=by, org=org) or approved
    except DBAPIError as exc:
        conflict = _guard_db(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    return AcceptBestOut(
        dry_run=payload.dry_run,
        items=[BestGuessModel(id=q.id, label=q.label, part=q.part, value=best.value,
                              value_label=best.label, line=qs.applied_line(q, best))
               for q, best in guesses],
        version=int(row.version) if row is not None else None,
        undo=[] if payload.dry_run else [
            UndoModel(module=q.module, part=q.part, value=AUTO) for q, _best in guesses],
    )


__all__ = [
    "QUESTIONS_OFF",
    "AcceptBestIn",
    "AcceptBestOut",
    "AnswerIn",
    "AnswerOut",
    "QuestionModel",
    "QuestionsModel",
    "accept_best",
    "answer_question",
    "count_open",
    "dismiss_question",
    "list_questions",
    "load",
    "module_question_ids",
    "open_parts",
    "questions_after_run",
    "questions_card",
    "questions_off",
    "samples",
]
