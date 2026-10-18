"""CRM · the open questions (WS-53 CRM-Q32, ``crm_platform.md`` §18.14).

    GET  /crm/questions                      the open questions, ranked
                                             (``?all=1`` adds each part in use)
    POST /crm/questions/{id}/answer          {"value": <choice>} answers one
    POST /crm/questions/{id}/dismiss         "Not relevant"
    DELETE /crm/questions/{id}/dismiss       its Undo, and the Undo of "None of
                                             these" on a slot
    POST /crm/questions/accept-best          {"ids": [...], "dry_run": true}
    GET  /crm/questions/{id}/sum-check       ?fields=A,B the check of a sum
                                             (CRM-Q32-9)
    GET  /crm/questions/ask?ids=             the questions of a chat card, for
                                             each member with CRM access
                                             (CRM-Q32-8)
    POST /crm/questions/{id}/reply           {"choice": <key>}: an answer of a
                                             CRM admin, a proposal of each
                                             other member, or "@default"

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

**The chat asks first (CRM-Q32-8, owner direction 2026-10-18).** The two
chat routes need the CRM access rule, not the admin permission. ``/reply``
decides by the permission of the caller, BEFORE any write: a CRM admin
writes the org fact through the code of ``/answer``, and each other member
records a PROPOSAL in ``zoho:questions`` and writes nothing in the plan.
``@default`` holds a question for one member for 24 hours, in Redis through
``acb_common.tenant_redis``. Each admin write of a part clears its
proposals. Only this module and ``crm_sync/questions.py`` read
``proposals``.

Fences: ``tests/unit/test_crm_questions.py`` and
``tests/unit/test_crm_questions_r8.py`` (R8), and for the chat
``tests/unit/test_crm_questions_chat.py`` and
``tests/unit/test_crm_questions_chat_r8.py``.
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
from gateway.routes.crm import access
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.catalogue import SumKeys
from gateway.routes.crm.core import _tenant_session, from_jsonb, router
from gateway.routes.crm.field_rules import _GUARDS, _organization, _require_mirror
from gateway.routes.crm.flags import questions_allowed, require_crm_mirror
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
               full: bool = True, loaded: Any = None,
               proposals: bool = False) -> qs.Inputs | None:
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
        # CRM-Q32-8 fix round: the proposals of the members only for the two
        # reads that show them (the admin list and the card of /ask). The
        # count, the catalogue and the 422 of the aggregate never get them.
        proposals=qs.proposals_of(state(qs.QUESTIONS_ROW)) if proposals else {},
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


async def refresh_seen(db: Any, org: str) -> None:
    """Write ``seen`` and ``money`` of ``zoho:questions`` again from the open
    set of now, in the caller's transaction (follow-up of CRM-Q32-4). An
    answer, a dismissal, its Undo and a batch call it after their write, so
    the count of Sync health (``len(seen)``) agrees with the rail at once,
    and not only after the next sync run. Before the first questions step
    the row has no ``seen``, and this writes nothing: that step must still
    see each id as new, for the first card of My Day."""
    from gateway.crm_sync import runs

    cursors = await runs.read_cursors(db, org, qs.QUESTIONS_ROW)
    state = getattr(cursors.get(qs.QUESTIONS_ROW), "state", None)
    if not isinstance((state or {}).get("seen"), list):
        return
    inputs = await load(db, org)
    if inputs is None:
        return
    await runs.merge_cursor_keys(db, org, qs.QUESTIONS_ROW, qs.seen_keys(qs.build(inputs)))


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


class ProposalModel(BaseModel):
    """CRM-Q32-8: one proposal of a member, for a CRM admin only. ``name``
    is the display name from the member directory, or "A member". It is
    never an email."""

    name: str
    value: str | None = None
    #: The label of the value: a field, a purpose, Billed or Collected.
    label: str
    at: str


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
    #: The answer in use, for a correction. ``None`` on an open question. A
    #: sum of fields (CRM-Q32-9) is the list of its keys.
    current: str | list[str] | None = None
    #: Who chose the answer in use: ``admin``, ``ai`` or ``code``.
    by: str | None = None
    #: False for ``revenue``, which takes no dismissal.
    dismissible: bool = True
    #: Each choice of "Pick another".
    options: list[QuestionOptionModel] | None = None
    #: CRM-Q32-9: the fields of the picker of a sum, on an ``amount``
    #: question when 2 or more currency fields each fill 20% or more.
    sums: list[QuestionOptionModel] | None = None
    slot: str | None = None
    count: int | None = None
    value: str | None = None
    field: str | None = None
    #: CRM-Q32-8: the proposals of the members, newest last. A proposal that
    #: equals the answer in use is left out.
    proposals: list[ProposalModel] = Field(default_factory=list)


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


def _model(q: qs.Question, proposals: Sequence[ProposalModel] = ()) -> QuestionModel:
    return QuestionModel(**qs.wire(q), proposals=list(proposals))


async def _proposals_wire(db: Any, inputs: qs.Inputs, questions: Sequence[qs.Question],
                          ) -> dict[str, list[ProposalModel]]:
    """The proposals of each question of *questions*, with the display name
    of each member from the one read of the directory
    (``routes/people/core.names_of``). A member with no name is "A member",
    never the email (CRM-Q32-8). One read of the directory, and none when
    no question has a proposal."""
    from gateway.routes.people.core import names_of

    live = {q.id: (q, qs.live_proposals(inputs.proposals.get(q.id, ()), q))
            for q in questions}
    members = {p["member"] for _q, found in live.values() for p in found}
    if not members:
        return {}
    names = await names_of(db, members)
    return {
        qid: [ProposalModel(name=names.get(p["member"]) or qs.A_MEMBER, value=p["value"],
                            label=qs.value_label(q, p["value"]), at=p["at"]) for p in found]
        for qid, (q, found) in live.items() if found
    }


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
        inputs = await load(db, org, proposals=True)
        if inputs is None:
            return QuestionsModel(open=0, shown=[], more=0)
        found = qs.build(inputs)
        extra = qs.answered(inputs) if everything else []
        inputs = await samples(db, org, inputs, [*found[:limit], *extra])
        approved = await _approved(db)
        # The samples change no id and no order, so the read builds once more
        # with them, and with no new statement.
        found = qs.build(inputs)
        extra = qs.answered(inputs) if everything else []
        proposals = await _proposals_wire(db, inputs, [*found[:limit], *extra])
    return QuestionsModel(
        open=len(found), more=max(0, len(found) - limit),
        shown=[_model(q, proposals.get(q.id, ())) for q in found[:limit]],
        answered=[_model(q, proposals.get(q.id, ())) for q in extra],
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
    a plan that moved gives 409 ``plan_moved``. CRM-Q32-9: the ``amount``
    of a ``key_field`` also takes a list of 2 to 5 currency fields, a sum."""

    model_config = ConfigDict(extra="forbid")

    value: str | SumKeys | None
    version: int | None = None


class UndoModel(BaseModel):
    """What the Undo of an answer sends. With ``question``, the answer route
    of that id again with ``value`` (a ``stage_value``). Else
    ``PUT /crm/catalogue/{module}`` with ``{part: value}``."""

    module: str | None = None
    part: str | None = None
    #: ``"@auto"``, or an earlier value, which can be a sum (CRM-Q32-9).
    value: str | list[str] | None = None
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


def _answer_line(q: qs.Question, value: Any, src: Any = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        # CRM-Q32-9: "Totals of Collection now use the sum of A, B and C."
        return qs.applied_line(q, qs.sum_choice(src, value)) if src is not None else None
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
        line=_answer_line(q, payload.value, inputs.sources.get(module)), module=module,
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
            answered = await _answer(db, org, by, question_id, payload)
            await refresh_seen(db, org)
            # CRM-Q32-8: an admin write of a part clears its proposals. The
            # plan lock is held, so the cursor row comes second.
            await clear_proposals(db, org, [question_id])
    except DBAPIError as exc:
        conflict = _guard_db(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    # After the commit: the card of this question is closed for each member.
    await forget_late_answers(org, [question_id])
    return answered


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
        await refresh_seen(db, org)
        await clear_proposals(db, org, [q.id])
    await forget_late_answers(org, [q.id])
    return DismissOut(id=q.id)


@router.delete("/questions/{question_id}/dismiss", response_model=DismissOut,
               dependencies=_GUARDS)
async def undo_dismiss_question(
    question_id: str, user: UserContext = Depends(get_current_user),
) -> DismissOut:
    """The Undo of "Not relevant", and of "None of these" on a slot: remove
    the one dismissal of *question_id* from ``zoho:questions`` of the
    caller's org, so the question is open again when it still applies. 404
    ``not_dismissed`` when the org holds no such dismissal. The guards of
    the dismiss, and the org of the session (R5, R11)."""
    from gateway.crm_sync import runs

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        found = await runs.drop_cursor_entry(db, org, qs.QUESTIONS_ROW, "dismissed",
                                             question_id)
        if found:
            await refresh_seen(db, org)
    if not found:
        raise HTTPException(status_code=404, detail={
            "code": "not_dismissed", "message": "This question is not dismissed.",
        })
    return DismissOut(id=question_id, dismissed=False)


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
                await refresh_seen(db, org)
                await clear_proposals(db, org, [q.id for q, _best in guesses])
    except DBAPIError as exc:
        conflict = _guard_db(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    if not payload.dry_run:
        await forget_late_answers(org, [q.id for q, _best in guesses])
    return AcceptBestOut(
        dry_run=payload.dry_run,
        items=[BestGuessModel(id=q.id, label=q.label, part=q.part, value=best.value,
                              value_label=best.label, line=qs.applied_line(q, best))
               for q, best in guesses],
        version=int(row.version) if row is not None else None,
        undo=[] if payload.dry_run else [
            UndoModel(module=q.module, part=q.part, value=AUTO) for q, _best in guesses],
    )


# ── The chat asks first, and a member proposes (CRM-Q32-8) ─────────────────

#: How long ``@default`` holds a question for one member (§18.14.10).
DEFAULT_HOLD_HOURS = 24

#: The Redis namespace of a hold. The key holds the member and the id.
HOLD_NAMESPACE = "crm_question_hold"

#: The Redis namespace of a late answer of a member (CRM-Q32-8, the late
#: pick, after the security review). When a member answers a parked CRM
#: question card late, :func:`record_late_answer` writes ``key(
#: ANSWERED_NAMESPACE, question id, member)`` with the key of the option
#: that the member picked, for :data:`LATE_ANSWER_TTL_S`. ``acb_skills.
#: crm_tools`` reads it once (GETDEL) and takes the pick only when the
#: choice equals it. The same name lives in ``crm_tools.py``, and a test
#: holds the two equal.
ANSWERED_NAMESPACE = "crm_question_answered"

#: How long a late answer waits for the run that sends its pick.
LATE_ANSWER_TTL_S = 30 * 60


def _card_option_key(payload: Any, answer: str) -> tuple[str, str, str] | None:
    """The CRM question id, the key of the option that *answer* picked, and
    its label, from the payload of a parked ``questions`` card, or None. The
    card must ask ONE CRM question, and *answer* must be the label of
    exactly ONE option of it: ``ask_questions`` cuts a long label, so two
    options can show one label, and then nothing is recorded. The id and
    the key come from the card that the server stored, never from the
    request."""
    from acb_skills.ask_tools import card_option_key, crm_card_tag

    found = payload.get("questions") if isinstance(payload, Mapping) else None
    if not isinstance(found, list) or len(found) != 1:
        return None
    question = found[0]
    qid = crm_card_tag(question)
    if qid is None:
        return None
    said = (answer or "").strip()
    hits = [o for o in question.get("options") or []
            if isinstance(o, Mapping) and str(o.get("label") or "").strip() == said]
    if len(hits) != 1:
        return None
    key = card_option_key(hits[0])
    return (qid, key, said) if key else None


async def _server_label(org: str, question_id: str, option: str) -> str | None:
    """The label that the server's own card of *question_id* shows for the
    option *option* now, or None: the question as ``/ask`` reads it, with
    its masked samples, through ``crm_tools.card_option_label``. One tenant
    session of *org*, which the caller took from the session (R5)."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.crm_tools import card_option_label

    token = bind_tenant(org)
    try:
        async with _tenant_session(org) as db:
            inputs = await load(db, org, full=False)
            if inputs is None:
                return None
            q = _chat_questions(inputs, True).get(question_id)
            if q is None:
                return None
            inputs = await samples(db, org, inputs, [q])
            q = _chat_questions(inputs, True).get(question_id)
            if q is None:
                return None
    finally:
        release_tenant(token)
    wire = _ask_model(q, inputs, member="", admin=False, held=False).model_dump()
    return card_option_label(wire, option)


async def record_late_answer(org: str, member: str, kind: str, payload: Any,
                             answer: str) -> bool:
    """Record the member's late answer of a parked CRM question card, so the
    pick that the next run sends passes the gate of ``crm_answer_question``
    once. The org and the member come from the caller's session
    (``UserContext``), never from the request (R5, R11). Only the human act
    writes this: a card that nobody answered opens no gate. A Redis fault
    only logs: the late answer still reaches the chat, and the pick then
    gives the default."""
    from acb_common.tenant_redis import key, organization_scope

    if kind != "questions" or not org or "@" not in (member or ""):
        return False
    picked = _card_option_key(payload, answer)
    if picked is None:
        return False
    qid, option, label = picked
    # The card that ``ask_questions`` stored came from the model's JSON. So
    # the key counts only when the server's own card of that question shows
    # the same label for it: a forged card ("Yes, continue" over the key
    # payment_in) records nothing.
    try:
        expected = await _server_label(org, qid, option)
    except Exception as exc:
        _log.warning("crm.questions.late_answer_check_failed", organization_id=org,
                     error_type=type(exc).__name__)
        return False
    if expected is None or expected != label:
        _log.info("crm.questions.late_answer_label_mismatch", organization_id=org)
        return False
    try:
        with organization_scope(org):
            await _redis().setex(key(ANSWERED_NAMESPACE, qid, member.strip().lower()),
                                 LATE_ANSWER_TTL_S, option)
    except Exception as exc:
        _log.warning("crm.questions.late_answer_write_failed", organization_id=org,
                     error_type=type(exc).__name__)
        return False
    return True


async def forget_late_answers(org: str, question_ids: Sequence[str], *,
                       member: str | None = None) -> None:
    """Remove the late answer of each of *question_ids*: for *member* only,
    or for each member when *member* is None (an admin wrote the answer). It runs after the commit of the write. Any fault of
    Redis only logs: the answer stands, and a stale record lets a late
    pick reach ``/reply``, which checks the question again."""
    from acb_common.tenant_redis import OUTAGE_ERRORS, key, match, organization_scope

    wanted = [str(i) for i in dict.fromkeys(question_ids) if i]
    if not wanted:
        return
    try:
        with organization_scope(org):
            redis = _redis()
            for qid in wanted:
                if member is not None:
                    await redis.delete(key(ANSWERED_NAMESPACE, qid, member))
                    continue
                head = key(ANSWERED_NAMESPACE, qid).value + ":"
                async for raw in redis.scan_iter(match(ANSWERED_NAMESPACE, qid)):
                    name = raw.decode() if isinstance(raw, bytes) else str(raw)
                    who = name[len(head):] if name.startswith(head) else ""
                    if who and ":" not in who:
                        await redis.delete(key(ANSWERED_NAMESPACE, qid, who))
    except Exception as exc:  # the write committed: a clear never fails it
        _log.warning("crm.questions.shown_clear_failed", organization_id=org,
                     error_type=type(exc).__name__, outage=isinstance(exc, OUTAGE_ERRORS))

#: The most ids that one card read takes.
ASK_IDS_MAX = 2

#: The guards of the two chat routes: the CRM switch, ``CRM_MIRROR`` and the
#: CRM access rule. NOT the admin permission: each member with CRM access
#: reads a card and replies. ``/reply`` decides the admin branch itself.
_CHAT_GUARDS = [Depends(require_crm_mirror), Depends(access.require_crm_access)]


async def clear_proposals(db: Any, org: str, question_ids: Sequence[str]) -> None:
    """Remove the proposals of *question_ids* from ``zoho:questions``, in
    the caller's transaction (CRM-Q32-8, "Each admin write clears"). It
    locks the cursor row first, and ``lock_cursor`` makes the row when it is
    absent, so a call with an id writes that row. A caller that holds
    the plan lock took it before, so the lock order of the CRM holds. It
    removes only the entries of the ids that hold a proposal."""
    from gateway.crm_sync import runs

    wanted = [str(i) for i in dict.fromkeys(question_ids) if i]
    if not wanted:
        return
    found = qs.proposals_of(await runs.lock_cursor(db, org, qs.QUESTIONS_ROW))
    for qid in wanted:
        if qid in found:
            await runs.drop_cursor_entry(db, org, qs.QUESTIONS_ROW, qs.PROPOSALS, qid)


async def after_admin_write(db: Any, org: str, question_ids: Sequence[str]) -> None:
    """The step of each admin write of a part that another module makes
    (the catalogue PUT): it clears what the members proposed for those
    questions. Only this module and ``crm_sync/questions.py`` know the
    proposals, and a scan fences it (CRM-Q32-8 fix round)."""
    await clear_proposals(db, org, question_ids)


def _member(user: UserContext) -> str:
    """The email key of the caller, from ``UserContext`` only (R5, R11)."""
    email = (getattr(user, "email", None) or "").strip().lower()
    if not email:
        raise HTTPException(status_code=403, detail="Forbidden: no member for this call.")
    return email


def _redis() -> Any:
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


def _hold_key(member: str, question_id: str) -> Any:
    from acb_common.tenant_redis import key

    return key(HOLD_NAMESPACE, member, question_id)


async def held_ids(org: str, member: str, question_ids: Sequence[str]) -> set[str]:
    """The ids of *question_ids* that ``@default`` holds for *member*. A
    Redis outage holds nothing, so the card shows again (never a 500)."""
    from acb_common.tenant_redis import OUTAGE_ERRORS, organization_scope

    out: set[str] = set()
    try:
        with organization_scope(org):
            redis = _redis()
            for qid in question_ids:
                if await redis.exists(_hold_key(member, qid)):
                    out.add(qid)
    except OUTAGE_ERRORS:
        _log.warning("crm.questions.hold_read_failed", organization_id=org)
        return set()
    return out


async def hold(org: str, member: str, question_id: str) -> None:
    """Hold *question_id* for *member* for :data:`DEFAULT_HOLD_HOURS`. A
    Redis outage is a 503 with ``Retry-After``: the hold is the whole act."""
    from acb_common.tenant_redis import OUTAGE_ERRORS, organization_scope

    try:
        with organization_scope(org):
            await _redis().setex(_hold_key(member, question_id),
                                 DEFAULT_HOLD_HOURS * 3600, "1")
    except OUTAGE_ERRORS as exc:
        raise HTTPException(status_code=503, headers={"Retry-After": "30"}, detail={
            "code": "hold_unavailable",
            "message": "The default could not be kept now. Try again soon.",
        }) from exc


def _ask_ids(raw: Sequence[str]) -> list[str]:
    """The ids of ``?ids=``: a repeated name, or one name with commas. 422
    for none, and for more than :data:`ASK_IDS_MAX`."""
    found = list(dict.fromkeys(i.strip() for value in raw for i in str(value).split(",")
                               if i.strip()))
    if not found or len(found) > ASK_IDS_MAX:
        raise HTTPException(status_code=422, detail={
            "code": "bad_ids", "message": f"Ask for 1 to {ASK_IDS_MAX} question ids."})
    return found


def _chat_questions(inputs: qs.Inputs, everything: bool) -> dict[str, qs.Question]:
    """The questions that a chat card can ask, by id: each open one of
    :data:`qs.CHAT_KINDS`, and with *everything* each part in use too."""
    out = {q.id: q for q in qs.build(inputs) if q.kind in qs.CHAT_KINDS}
    if everything:
        for q in qs.answered(inputs):
            if q.kind in qs.CHAT_KINDS:
                out.setdefault(q.id, q)
    return out


def _purposes(inputs: qs.Inputs) -> dict[str, str]:
    """Each module of the catalogue → its purpose, for the override of a
    meaning of revenue."""
    return {str(e.module): str(e.purpose) for e in inputs.entries
            if getattr(e, "kind", None) == "module" and e.purpose}


class AskChoiceModel(BaseModel):
    value: str
    label: str
    best: bool = False
    fill: float | None = None
    #: Masked samples, never a raw value of a person.
    samples: list[str] = Field(default_factory=list)


class MyProposalModel(BaseModel):
    value: str | None = None
    label: str
    at: str


class AskQuestionModel(BaseModel):
    """One question of a chat card (CRM-Q32-8). It names no other member:
    ``my_proposal`` is the open proposal of the caller only."""

    id: str
    kind: str
    module: str | None = None
    label: str
    part: str | None = None
    reason: str
    #: The question as a full sentence, the short title, and why it matters.
    question: str
    header: str
    why: str
    choices: list[AskChoiceModel]
    #: "None of these" is a legal answer.
    none: bool
    #: The answer in use of a part in use, and its label.
    current: str | list[str] | None = None
    current_label: str | None = None
    #: True for a CRM admin: a choice writes the org fact.
    can_answer: bool
    #: True while ``@default`` holds the question for the caller.
    held: bool = False
    my_proposal: MyProposalModel | None = None
    #: The override of the caller's proposal on an OPEN question: what the
    #: caller's own answer reads in place of the key word. ``None`` on a part
    #: in use, where the org fact stays.
    override: dict[str, Any] | None = None
    #: An ``amount`` that could be a sum of fields (CRM-Q32-9): a CRM admin
    #: sets a sum in CRM settings.
    sums: bool = False


class AskOut(BaseModel):
    questions: list[AskQuestionModel]
    #: Where a CRM admin answers each question (``crm_settings_href``).
    href: str


def _ask_model(q: qs.Question, inputs: qs.Inputs, *, member: str, admin: bool,
               held: bool) -> AskQuestionModel:
    mine = qs.my_proposal(inputs.proposals.get(q.id, ()), member)
    if mine is not None and qs.equals_current(q, mine["value"]):
        mine = None
    open_q = q.reason != qs.REASON_ANSWERED
    current = list(q.current) if isinstance(q.current, tuple) else q.current
    return AskQuestionModel(
        id=q.id, kind=q.kind, module=q.module, label=q.label, part=q.part, reason=q.reason,
        question=qs.ask_sentence(q), header=qs.ask_header(q), why=q.why,
        choices=[AskChoiceModel(value=c.value, label=c.label, best=c.best, fill=c.fill,
                                samples=list(c.samples)) for c in q.choices],
        none=q.none, current=current,
        current_label=(qs.value_label(q, q.current) if not open_q
                       and not isinstance(q.current, tuple) else None),
        can_answer=admin, held=held,
        my_proposal=(MyProposalModel(value=mine["value"], label=qs.value_label(q, mine["value"]),
                                     at=mine["at"]) if mine is not None else None),
        override=(qs.override_of(q, mine["value"], _purposes(inputs))
                  if mine is not None and open_q else None),
        sums=bool(q.sums),
    )


@router.get("/questions/ask", response_model=AskOut, dependencies=_CHAT_GUARDS)
async def ask_questions_read(
    ids: list[str] = Query(default_factory=list),
    everything: bool = Query(False, alias="all"),
    user: UserContext = Depends(get_current_user),
) -> AskOut:
    """The questions of a chat card (CRM-Q32-8), for each member with CRM
    access. ``ids`` takes 1 or 2 ids, and only those questions come back.
    With ``all=1`` an id of a part in use matches too, for a change. Each
    question has its sentence, why it matters, its choices with masked
    samples, ``can_answer``, ``held`` and ``my_proposal``. It never names
    another member. It reads only, and the org comes from the session."""
    from gateway.retrieval.sources import crm_settings_href

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    wanted = _ask_ids(ids)
    member = _member(user)
    admin = user.has_permission(ADMIN_PERMISSION)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        inputs = await load(db, org, full=False, proposals=True)
        if inputs is None:
            return AskOut(questions=[], href=crm_settings_href("questions"))
        found = _chat_questions(inputs, everything)
        picked = [found[i] for i in wanted if i in found]
        if picked:
            inputs = await samples(db, org, inputs, picked)
            found = _chat_questions(inputs, everything)
            picked = [found[q.id] for q in picked]
    holds = await held_ids(org, member, [q.id for q in picked]) if picked else set()
    return AskOut(
        questions=[_ask_model(q, inputs, member=member, admin=admin, held=q.id in holds)
                   for q in picked],
        href=crm_settings_href("questions"),
    )


class ReplyIn(BaseModel):
    """``{"choice": <key>}``: the key of the option that the member picked.
    A field key, a purpose word, a revenue word, ``none`` or ``@default``."""

    model_config = ConfigDict(extra="forbid")

    choice: str = Field(min_length=1, max_length=200)


class ReplyOut(BaseModel):
    id: str
    kind: str
    #: ``answer``: a CRM admin wrote the org fact. ``proposal``: the choice
    #: of a member waits for a CRM admin. ``default``: the question is held
    #: for the caller. ``kept``: the choice is the answer in use already.
    result: str
    value: str | None = None
    label: str | None = None
    #: The sentence of what changed, for the chat.
    line: str
    #: A proposal on an OPEN question: what the member's own answer reads.
    override: dict[str, Any] | None = None
    #: An admin answer: what Undo sends (on ``/answer`` or the PUT).
    undo: UndoModel | None = None
    version: int | None = None


def _bad_choice() -> HTTPException:
    return HTTPException(status_code=422, detail={
        "code": "bad_choice",
        "message": "That is not an option of this question. Send the key of an option.",
    })


def _chat_kind(question_id: str) -> None:
    """422 for a question that a chat card does not answer: a ``slot`` or a
    ``stage_value`` changes what records mean, so it goes through the
    preview of the CRM app (§18.14.6)."""
    if question_id == qs.REVENUE_ID or question_id.startswith(("purpose:", "key:")):
        return
    raise HTTPException(status_code=422, detail={
        "code": "not_in_chat",
        "message": "A CRM admin answers this question in CRM settings, Open questions.",
    })


async def _admin_reply(db: Any, org: str, by: str, q: qs.Question, value: str | None,
                       inputs: qs.Inputs) -> ReplyOut:
    """The admin branch of ``/reply``: the org fact, through the one plan
    seam. An open question runs the code of ``/answer``. A part in use
    writes through ``catalogue.write_answer``, as the PUT does. Each clears
    the proposals of the question after the plan lock (the lock order)."""
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm.catalogue import AUTO, check_parts, write_answer
    from gateway.routes.crm.mapping_plans import lock_plan

    label = qs.value_label(q, value)
    if q.reason != qs.REASON_ANSWERED:
        done = await _answer(db, org, by, q.id, AnswerIn(value=value))
        await refresh_seen(db, org)
        await clear_proposals(db, org, [q.id])
        line = done.line or f"{q.label}: {label}, for everyone."
        return ReplyOut(id=q.id, kind=q.kind, result="answer", value=value, label=label,
                        line=line, undo=done.undo, version=done.version)
    approved = await lock_plan(db, by)
    module = cat.ORG_MODULE if q.kind == qs.KIND_REVENUE else str(q.module)
    part = "revenue" if q.kind == qs.KIND_REVENUE else str(q.part)
    before: Any = AUTO
    if q.by == "admin":
        before = list(q.current) if isinstance(q.current, tuple) else q.current
    check_parts(module, {part: value}, inputs.sources.get(module))
    row = await write_answer(db, org, by=by, module=module, parts={part: value})
    await refresh_seen(db, org)
    await clear_proposals(db, org, [q.id])
    return ReplyOut(id=q.id, kind=q.kind, result="answer", value=value, label=label,
                    line=f"{q.label}: {label}, for everyone.",
                    undo=UndoModel(module=module, part=part, value=before),
                    version=int((row or approved).version))


async def _member_reply(db: Any, org: str, member: str, q: qs.Question, value: str | None,
                        inputs: qs.Inputs) -> ReplyOut:
    """The member branch of ``/reply``: a PROPOSAL, and no write of the
    plan. It locks the cursor row first, then reads, changes and writes the
    list of the question in one transaction, so two members at once keep
    both. On an open question the reply gives the override of the member's
    own answer. On a part in use the org fact stays."""
    from gateway.crm_sync import runs

    label = qs.value_label(q, value)
    if qs.equals_current(q, value):
        return ReplyOut(id=q.id, kind=q.kind, result="kept", value=value, label=label,
                        line=f"{label} is the answer in use already.")
    state = await runs.lock_cursor(db, org, qs.QUESTIONS_ROW)
    entries = qs.proposals_of(state).get(q.id, [])
    at = datetime.now(UTC).isoformat()
    await runs.merge_cursor_entry(db, org, qs.QUESTIONS_ROW, qs.PROPOSALS, q.id,
                                  qs.with_proposal(entries, value, member, at))
    if q.reason == qs.REASON_ANSWERED:
        return ReplyOut(id=q.id, kind=q.kind, result="proposal", value=value, label=label,
                        line=qs.CHANGE_WAITS)
    return ReplyOut(
        id=q.id, kind=q.kind, result="proposal", value=value, label=label,
        line=f"This uses {label}, as you chose. A CRM admin can confirm it for everyone.",
        override=qs.override_of(q, value, _purposes(inputs)),
    )


@router.post("/questions/{question_id}/reply", response_model=ReplyOut,
             dependencies=_CHAT_GUARDS)
async def reply_question(
    question_id: str, payload: ReplyIn, user: UserContext = Depends(get_current_user),
) -> ReplyOut:
    """The reply of a chat card (CRM-Q32-8). The server decides by the
    permission of the caller, never by the tool, and BEFORE any write:

    * a CRM admin (``admin:access:manage``, the permission of ``_GUARDS``)
      writes the org fact through the code of ``/answer``, also for a part
      in use (``all=1`` of ``/ask``);
    * each other member with CRM access records a PROPOSAL and writes
      nothing in the plan;
    * ``@default`` writes nothing and records no proposal. It holds the
      question for the caller for 24 hours.

    ``@auto`` gives 422 for each caller: Undo stays on ``/answer`` and on
    the PUT. A key that no option has gives 422 and writes nothing. The org
    comes from the session and the member from ``UserContext`` (R5, R11)."""
    from gateway.routes.crm.core import actor

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    choice = payload.choice.strip()
    if choice == qs.AUTO:
        raise HTTPException(status_code=422, detail={
            "code": "no_undo_here",
            "message": "Undo an answer in CRM settings, Your answers.",
        })
    _chat_kind(question_id)
    member = _member(user)
    # The branch is chosen here, before any read or write.
    admin = user.has_permission(ADMIN_PERMISSION)
    by = actor(user).lower()
    try:
        async with _tenant_session() as db:
            await _require_mirror(db, org)
            inputs = await load(db, org, full=False)
            q = (_chat_questions(inputs, True).get(question_id)
                 if inputs is not None else None)
            if inputs is None or q is None:
                raise question_moved()
            if choice == qs.DEFAULT_KEY:
                if q.reason == qs.REASON_ANSWERED:
                    raise _bad_choice()
                await hold(org, member, q.id)
                out = ReplyOut(id=q.id, kind=q.kind, result="default",
                               line="Metorite uses the default for you for 24 hours.")
            else:
                ok, value = qs.chat_choice(q, choice)
                if not ok:
                    raise _bad_choice()
                out = (await _admin_reply(db, org, by, q, value, inputs) if admin
                       else await _member_reply(db, org, member, q, value, inputs))
    except DBAPIError as exc:
        conflict = _guard_db(exc)
        if conflict is not None:
            raise conflict from exc
        raise
    # After the commit: the pick closes the card of this member. An admin's
    # answer closes it for each member, because the question is answered.
    await forget_late_answers(org, [q.id], member=None if out.result == "answer" else member)
    return out


# ── The check of a sum (CRM-Q32-9, §18.14.10) ───────────────────────────────


class SumPairModel(BaseModel):
    """One pair of parts: the records that set both, and the records where
    both hold the same value."""

    fields: list[str]
    labels: list[str]
    both: int
    equal: int


class SumCheckOut(BaseModel):
    """What the picker of a sum shows: the coverage line, and a warning for
    each pair that holds the same value on half or more of the records
    that set both. The admin can still save."""

    id: str
    module: str
    fields: list[str]
    #: The records that the check read: the first 10,000 of the module, by id.
    records: int
    #: The records with any part set, and with 2 or more parts set.
    covered: int
    overlap: int
    #: True when the module may hold more records than the check read.
    capped: bool
    line: str
    pairs: list[SumPairModel]
    warnings: list[str]


def _sum_keys(raw: Sequence[str]) -> list[str]:
    """The keys of ``?fields=``: a repeated name, or one name with commas."""
    return [k.strip() for value in raw for k in str(value).split(",") if k.strip()]


@router.get("/questions/{question_id}/sum-check", response_model=SumCheckOut,
            dependencies=_GUARDS)
async def sum_check(
    question_id: str,
    fields: list[str] = Query(default_factory=list),
    user: UserContext = Depends(get_current_user),
) -> SumCheckOut:
    """The check of a ticked set of fields for the ``amount`` question
    *question_id* (CRM-Q32-9). 422 ``bad_answer`` for a set that is not 2 to
    5 distinct keys, before any SQL, and for a key that is not a currency
    field of the module, before the read of a record. Then ONE statement
    over the first 10,000 records by id, with the org in its predicate. A
    CRM admin only, and the org must be on ``CRM_QUESTIONS_ORGS``. The org
    comes from ``UserContext`` only (R5, R11)."""
    from gateway.crm_sync import catalogue as cat
    from gateway.routes.crm import field_sql
    from gateway.routes.crm.catalogue import _source_of, bad_answer

    org = _organization(user)
    if not questions_allowed(org):
        raise questions_off()
    head, _sep, rest = question_id.partition(":")
    module, _sep, part = rest.rpartition(":")
    if head != "key" or part != cat.SUM_PART or not module:
        raise bad_answer("Only the amount of a module takes a sum.", part or None)
    keys = _sum_keys(fields)
    if cat.sum_shape_problem(keys):
        raise bad_answer("A sum takes 2 to 5 different fields.", cat.SUM_PART)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
        src = await _source_of(db, org, module)
        if qs.sum_problem(src, keys):
            raise bad_answer("Each field of a sum must be a currency field of the module.",
                             cat.SUM_PART)
        parts = [src.field(k) for k in keys]
        refs = tuple(field_sql.FieldRef(key=f.key, type=f.type, label=f.label) for f in parts)
        counted = await field_sql.sum_check(db, org, module, refs)
    labels = [f.label for f in parts]
    report = qs.sum_report(labels, counted.records, counted.covered, counted.pairs,
                           capped=counted.records >= field_sql.SUM_CHECK_ROWS)
    return SumCheckOut(
        id=question_id, module=module, fields=keys, records=counted.records,
        covered=counted.covered, overlap=counted.overlap,
        capped=counted.records >= field_sql.SUM_CHECK_ROWS, line=report.line,
        pairs=[SumPairModel(fields=[keys[i], keys[j]], labels=[labels[i], labels[j]],
                            both=both, equal=equal)
               for i, j, both, equal in counted.pairs],
        warnings=list(report.warnings),
    )


__all__ = [
    "ANSWERED_NAMESPACE",
    "ASK_IDS_MAX",
    "DEFAULT_HOLD_HOURS",
    "QUESTIONS_OFF",
    "AcceptBestIn",
    "AcceptBestOut",
    "AnswerIn",
    "AnswerOut",
    "AskOut",
    "AskQuestionModel",
    "ProposalModel",
    "QuestionModel",
    "QuestionsModel",
    "ReplyIn",
    "ReplyOut",
    "SumCheckOut",
    "SumPairModel",
    "accept_best",
    "after_admin_write",
    "answer_question",
    "ask_questions_read",
    "clear_proposals",
    "count_open",
    "dismiss_question",
    "forget_late_answers",
    "held_ids",
    "hold",
    "list_questions",
    "load",
    "module_question_ids",
    "open_parts",
    "questions_after_run",
    "questions_card",
    "questions_off",
    "record_late_answer",
    "refresh_seen",
    "reply_question",
    "samples",
    "sum_check",
    "undo_dismiss_question",
]
