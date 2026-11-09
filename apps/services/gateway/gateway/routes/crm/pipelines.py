"""CRM · pipelines — the deal pipelines and the source profile (WS-53 CRM-Q1, CRM-Q2).

Spec: ``project-docs/specs/crm_platform.md`` §18.3 and §18.5.

    GET    /crm/pipelines                              the deal pipelines, with stages
    GET    /crm/pipelines?kind=lead                    the lead pipelines (CRM-M4a)
    POST   /crm/pipelines                              create (Native mode, admin, CRM-M3a)
    GET    /crm/pipelines/preview                      what a create would make; it reads only
    PATCH  /crm/pipelines/{id}                         rename (Native mode, admin)
    POST   /crm/pipelines/{id}/stages                  add a stage (Native mode, admin)
    PATCH  /crm/pipelines/{id}/stages/{status_id}      the win chance and the warning
                                                       days in one pipeline (CRM-M3b)
    DELETE /crm/pipelines/{id}/stages/{status_id}      remove a stage (Native mode, admin)
    GET    /crm/source-profile                         the shape of the customer's Zoho

**A pipeline is not an object in Zoho.** It is an ordered list of deal stages.
A stage (``crm_deal_statuses``) stays one global list, and
``crm_pipeline_stages`` says which stages sit in which pipeline, in what order.
So the same stage can sit in two pipelines.

**The win chance and the warning days of a stage in a pipeline** (WS-CRM
CRM-M3b, D166, migration 394). They sit on the membership row
(``crm_pipeline_stages.probability`` and ``max_dwell_days``), and NULL there
reads the stage row. So a win chance edited in one pipeline does not change
the other. ``PATCH /crm/pipelines/{id}/stages/{status_id}`` sets them, Native
mode and an admin only, behind ``CRM_PIPELINES_ORGS``, with the clamp of
D-CRM-10. It logs one ``pipeline_stage`` row with the action ``change``, and
its Undo puts the old values back. The old route
``PATCH /crm/statuses/deal/{id}`` sets a value for every pipeline: it writes
the stage row and each membership (``core.fan_out_stage_values``). Each new
membership copies the values: of the copied pipeline on a copy, else of the
stage row. A take-out keeps them in its row, and its Undo puts them back.

**Who may do what.** ``GET /crm/pipelines`` is open to each member the CRM access
rule admits, because every board needs it. The three writes need
``admin:access:manage``. In Mirror mode each write answers 409
``mirror_mode_settings`` through the one write guard (``assert_writable``),
because Zoho owns its pipelines. ``GET /crm/source-profile`` needs the admin
floor and ``CRM_MIRROR``. It returns the edition, the currency, the modules and
a pipeline count. It never returns a record, a token or a company name.

The organization comes from the bound tenant (R5), never from the request.

**Change history** (WS-CRM CRM-M1, ``crm_conversational.md`` §8). Each of
the three writes logs one row through ``change_log.log_change``, in its own
transaction: a rename at the ``company`` level, a stage in or out of a
pipeline at the ``team`` level. A write that changes nothing logs nothing.
The Undo of a rename writes the old name, the Undo of an added stage takes
it out (409 while a deal of the pipeline sits in it), and the Undo of a
removed stage puts it back at its old place. Each Undo goes through the
helper of the route.

**Create a pipeline** (WS-CRM CRM-M3a, ``crm_conversational.md`` §8 CRM-M3).
``POST /crm/pipelines`` takes a name and exactly one start: ``copy_from``
(the id of a pipeline, whose stages it takes in order) or ``stages`` (an
ordered list of ``{name, type}``). A stage name that matches one deal stage
reuses that row, so two pipelines share one "Proposal" (D166 item 2). Two
matches are a 409. A new name makes its stage through
``admin.create_status_in``, the helper of ``POST /crm/statuses/{kind}``, and
that stage joins the new pipeline only. A new pipeline holds at least one
open stage (type ``open`` or ``ongoing``). With no won stage or no lost stage
in its list, the create adds the org's own, so that a deal in it can close.
``GET /crm/pipelines/preview`` runs the same plan and reads only: the chat
card and the settings dialog show it. Both need ``CRM_PIPELINES_ORGS``
(404) and the admin floor (403). Mirror mode is 409 ``mirror_mode_settings``,
and a name that a pipeline holds is 409 ``name_taken``.

The create logs one ``pipeline`` row at the ``company`` level, with the name
and the ordered stages in "after", and each new stage logs its own
``deal_stage`` row. ``via`` is ``chat`` only for a call that the CRM
assistant made (``change_log.via_of``). The Undo of the create deletes the
pipeline, and refuses with the count while a deal names it. It also deletes
each stage that the create made, while no record and no other pipeline holds
it, and logs one row for each. It locks those stages before the pipeline, in
the order of a deal write, so the two cannot wait on each other.

**A lead pipeline** (WS-CRM CRM-M4a, ``crm_conversational.md`` §8 CRM-M4).
It is a row of ``crm_pipelines`` with ``kind = 'lead'`` (migration 386). Its
stages are lead statuses (``crm_lead_statuses``), and
``crm_lead_pipeline_stages`` holds which status sits where. ``PipelineKind``
holds what is particular to each kind, so the create, the preview, the plan
and the Undo are ONE path with a ``kind``. The body takes ``kind`` (``deal``
by default, or ``lead``) and, for a lead pipeline only, ``feeds``: the name of
the deal pipeline that a converted lead's deal joins. It resolves under row
level security, so a name of another org is unknown. A lead pipeline holds the
same minimum as a deal one: one open status (``open`` or ``ongoing``) that no
picker hides, and the org's own ``won`` and ``lost`` statuses when its list has
none. For a lead, ``won`` is "converted" (``pipeline.convert_lead`` moves a
converted lead to the first ``won`` status) and ``lost`` is "lost" or "junk".
Every deal surface reads ``core.DEAL_PIPELINE_SQL``, so a lead pipeline never
shows on the Deals board, in a deal's stage menu, the reports, the export,
the switch or the default. ``GET /crm/pipelines?kind=lead`` lists the lead
pipelines. A lead with no pipeline stays on the default lead list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException, Query, Request
from gateway.routes.crm import change_log
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.admin import (
    _NAME_INDEX,
    CLAMPED_PROBABILITY,
    STATUS_AREAS,
    STATUS_LOG_KEYS,
    _violates,
    create_status_in,
    delete_status_in,
    status_in_use,
)
from gateway.routes.crm.change_log import Change, Logged, log_change, quoted
from gateway.routes.crm.core import (
    EFFECTIVE_STATUS_COLUMNS,
    MEMBERSHIP_COLUMNS,
    PIPELINE_STAGE_COLUMNS,
    STATUS_TYPES,
    WEIGHTED_TYPES,
    _tenant_session,
    assert_writable,
    checked_uuid,
    copied_values_sql,
    current_tenant,
    default_pipeline_id,
    insert_row,
    load_row,
    lock_pipeline_creates,
    pipelines_holding,
    provider_link_context,
    require_deal_pipeline,
    require_row,
    router,
    update_row,
)
from gateway.routes.crm.flags import require_crm_mirror, require_crm_pipelines
from gateway.routes.projects.core import is_transient
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

ADMIN_ONLY = [require_permission(ADMIN_PERMISSION)]

#: The longest name a pipeline may have.
MAX_NAME = 120


class PipelineStageModel(BaseModel):
    id: str
    name: str
    position: int = 0
    type: str = "open"
    #: CRM-M3b: the win chance of the stage in THIS pipeline (D166).
    probability: int | None = None
    forecast_category: str | None = None
    #: CRM-M3b: the warning days of the stage in THIS pipeline. ``None``: the
    #: stage never shows the warning. A lead status has none.
    max_dwell_days: int | None = None


class PipelineModel(BaseModel):
    id: str
    name: str
    is_default: bool = False
    position: int = 0
    #: A link to the Deals tab in the provider's app. ``None`` for a native
    #: pipeline and while the org domain is not known.
    ext_url: str | None = None
    stages: list[PipelineStageModel] = []
    #: CRM-M4a: ``deal`` or ``lead``. The stages of a lead pipeline are lead
    #: statuses.
    kind: str = "deal"
    #: CRM-M4a: the deal pipeline that a lead pipeline feeds. Left out when
    #: none is named: a deal pipeline, or a lead pipeline that feeds the
    #: default deal pipeline.
    feeds_pipeline_id: str | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class PipelineIn(BaseModel):
    name: str | None = None


class StageIn(BaseModel):
    status_id: str
    position: int | None = None


class NewStageIn(BaseModel):
    """One stage of a new pipeline: a name, and the type of a NEW stage.

    ``type`` stays out for a stage that the CRM holds already. A new stage
    with no type is ``open``, unless its whole name is a closing word
    ("Won", "Closed Lost"): then it is ``won`` or ``lost``."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    type: str | None = None


class PipelineCreateIn(BaseModel):
    """``POST /crm/pipelines`` and its preview. Exactly one of ``copy_from``
    and ``stages``. No other key: the actor, the tenant and ``via`` never
    come from the body (R11)."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    #: ``deal`` (the default) or ``lead`` (CRM-M4a), a key of :data:`PIPELINE_KINDS`.
    kind: str = "deal"
    #: The id of the pipeline whose stages the new one takes, in order. It is
    #: a pipeline of the same kind.
    copy_from: str | None = None
    stages: list[NewStageIn] | None = None
    #: CRM-M4a, a lead pipeline only: the NAME of the deal pipeline that it
    #: feeds. It resolves under row level security, and the row keeps its id.
    #: With none, the row keeps NULL: a converted lead's deal joins the org's
    #: default deal pipeline of that moment.
    feeds: str | None = None
    #: The stage ids of the plan that the member saw, in order, ``None`` for a
    #: stage that the create makes (the chat card sends it). When the plan of
    #: the create differs, the create refuses with 409 ``plan_changed``.
    planned: list[str | None] | None = None
    #: CRM-M4a repair 1: the id of the deal pipeline that the card showed as
    #: the feed. With ``planned``, a feed that resolves to another id now (a
    #: rename between the card and Apply) refuses with 409 ``plan_changed``.
    planned_feeds: str | None = None


class PlannedStageModel(BaseModel):
    #: ``None`` for a stage that the create makes.
    id: str | None = None
    name: str
    type: str
    #: ``existing``: a stage that the CRM holds, and the pipeline shares it.
    #: ``new``: the create makes it. ``added``: the org's won or lost stage,
    #: which the create adds so that a deal in the pipeline can close.
    source: str
    #: True for a hidden stage of a copied pipeline. No picker offers it, and
    #: it does not count as the open, won or lost stage of the pipeline.
    hidden: bool = False


class PipelinePlanModel(BaseModel):
    """What a create makes, in order. The preview answers it."""

    name: str
    kind: str = "deal"
    #: The name of the pipeline that the create copies, or ``None``.
    copy_of: str | None = None
    stages: list[PlannedStageModel] = []
    #: CRM-M4a: the name of the deal pipeline that a lead pipeline feeds, or
    #: ``None`` (the default deal pipeline). Always ``None`` for a deal one.
    feeds: str | None = None
    #: CRM-M4a repair 1: the id of that deal pipeline, which the chat card
    #: sends back as ``planned_feeds``.
    feeds_id: str | None = None
    #: CRM-M4a repair 1: the name of the org's default deal pipeline now, for a
    #: lead pipeline with no feed. The card says "the default deal pipeline
    #: (now <name>)". ``None`` for a deal pipeline.
    default_deal_pipeline: str | None = None


class ProfileModuleModel(BaseModel):
    api_name: str
    label: str
    custom: bool = False
    api_supported: bool = True
    mirrored: bool = False


class SourceProfileModel(BaseModel):
    version: int
    fetched_at: datetime | None = None
    edition: str | None = None
    currency: str | None = None
    modules: list[ProfileModuleModel] = []
    pipelines: int = 0


@dataclass(frozen=True)
class PipelineKind:
    """What is particular to one kind of pipeline: ``deal`` or ``lead``.

    The read, the plan, the create and the Undo take one, so a lead pipeline
    (CRM-M4a) has no second path. The words go into the refusals, the Change
    history rows and the answers, so each kind reads in its own nouns."""

    #: The ``{kind}`` of ``POST /crm/statuses/{kind}``, and ``crm_pipelines.kind``.
    status_kind: str
    status_table: str
    #: The records that sit in a pipeline of this kind.
    records_table: str
    #: The noun of one record in a sentence.
    record_noun: str
    #: Which statuses sit in which pipeline of this kind, in order.
    stages_table: str = "crm_pipeline_stages"
    #: The noun of one stage in a sentence, and its plural.
    stage_noun: str = "stage"
    stage_nouns: str = "stages"
    #: The noun of one pipeline of this kind in a sentence.
    pipeline_noun: str = "pipeline"
    #: An open stage, as the example of a refusal.
    open_example: str = "Qualify"
    #: The whole names that make a new stage with no type a closing stage.
    closing_words: tuple[tuple[str, str], ...] = (
        ("won", "won"), ("closed won", "won"), ("lost", "lost"), ("closed lost", "lost"),
    )


PIPELINE_KINDS: dict[str, PipelineKind] = {
    "deal": PipelineKind("deal", "crm_deal_statuses", "crm_deals", "deal"),
    # CRM-M4a. For a lead, `won` is "converted" and `lost` is "lost" or
    # "junk" (`pipeline.convert_lead` moves a converted lead to the first
    # `won` status). `lib/newPipeline.ts` holds the same words.
    "lead": PipelineKind(
        "lead", "crm_lead_statuses", "crm_leads", "lead",
        stages_table="crm_lead_pipeline_stages", stage_noun="status",
        stage_nouns="statuses", pipeline_noun="lead pipeline", open_example="Contacted",
        closing_words=(
            ("won", "won"), ("converted", "won"), ("lost", "lost"), ("junk", "lost"),
        ),
    ),
}

#: The deal pipelines: the shape of ``GET /crm/pipelines`` before CRM-M4a.
DEAL = PIPELINE_KINDS["deal"]

_PIPELINES_SQL = (
    "SELECT id, name, position, is_default, ext_source, ext_id, kind, feeds_pipeline_id "
    "FROM crm_pipelines WHERE kind = :kind "
    "ORDER BY is_default DESC, position, created_at, id"
)

#: CRM-C3: the stage columns come from the one SELECT list, so
#: ``probability`` is the effective value (§20.6 item 6). CRM-C7 reads this.
#: CRM-M4a: one statement for each kind, over its own two tables.
#: CRM-M3b: a deal stage reads the values of its membership ``ps``
#: (``core.PIPELINE_STAGE_COLUMNS``), so one pipeline shows its own win chance.
_STAGES_SQL = (
    "SELECT ps.pipeline_id, ps.position AS stage_position, "
    "{columns} "
    "FROM {stages} ps JOIN {table} s ON s.id = ps.status_id "
    "ORDER BY ps.pipeline_id, ps.position, s.name"
)


def _stages_sql(kind: PipelineKind) -> str:
    columns = (
        PIPELINE_STAGE_COLUMNS if kind.status_kind == "deal"
        else EFFECTIVE_STATUS_COLUMNS[kind.status_table]
    )
    return _STAGES_SQL.format(
        columns=columns, stages=kind.stages_table, table=kind.status_table,
    )


def _stage(row: Any) -> PipelineStageModel:
    return PipelineStageModel(
        id=str(row.id),
        name=row.name,
        position=int(getattr(row, "stage_position", 0) or 0),
        type=getattr(row, "type", "open") or "open",
        probability=getattr(row, "probability", None),
        forecast_category=getattr(row, "forecast_category", None),
        max_dwell_days=getattr(row, "max_dwell_days", None),
    )


async def read_pipelines(
    db: Any, only: str | None = None, kind: PipelineKind = DEAL,
) -> list[PipelineModel]:
    """The pipelines of one kind of the bound tenant, each with its stages in
    order. The deal pipelines by default, so a lead pipeline never reaches a
    deal surface (CRM-M4a)."""
    from gateway.crm_sources.zoho import links

    pipelines = (await db.execute(
        text(_PIPELINES_SQL), {"kind": kind.status_kind},
    )).fetchall()
    stages = (await db.execute(text(_stages_sql(kind)), {})).fetchall()
    by_pipeline: dict[str, list[PipelineStageModel]] = {}
    for row in stages:
        by_pipeline.setdefault(str(row.pipeline_id), []).append(_stage(row))
    context = None
    if any(getattr(p, "ext_source", None) == "zoho" for p in pipelines):
        context = await provider_link_context(db)
    out: list[PipelineModel] = []
    for row in pipelines:
        if only is not None and str(row.id) != only:
            continue
        mirrored = getattr(row, "ext_source", None) == "zoho"
        feeds = getattr(row, "feeds_pipeline_id", None)
        out.append(PipelineModel(
            id=str(row.id),
            name=row.name,
            is_default=bool(row.is_default),
            position=int(row.position or 0),
            ext_url=(
                links.tab_link(
                    context.accounts_server, context.domain_name, "deal", context.tabs,
                ) if mirrored and context is not None else None
            ),
            stages=by_pipeline.get(str(row.id), []),
            kind=kind.status_kind,
            feeds_pipeline_id=str(feeds) if feeds else None,
        ))
    return out


async def _native_pipeline(db: Any, pipeline_id: str) -> Any:
    """The row of a DEAL pipeline. 404 for an unknown pipeline and for a lead
    pipeline (CRM-M4a), 409 ``mirror_mode_settings`` in Mirror mode.

    The rename and the stage routes act on deal pipelines only, so a lead
    pipeline never takes a deal stage.

    CRM-Z10c: the lock follows the mode and not ``ext_source``. An org that went
    from Mirror back to Native keeps its imported pipelines, and it may edit
    them. ``assert_writable`` is the only guard.
    """
    row = await require_deal_pipeline(db, pipeline_id)
    await assert_writable(db, current_tenant(), "crm_pipelines")
    return row


async def _one(db: Any, pipeline_id: str, kind: PipelineKind = DEAL) -> PipelineModel:
    found = await read_pipelines(db, only=pipeline_id, kind=kind)
    if not found:
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    return found[0]


@router.get("/pipelines", response_model=list[PipelineModel])
async def list_pipelines(
    kind: str = Query("deal", description="deal (the default) or lead (CRM-M4a)."),
    user: UserContext = Depends(get_current_user),
) -> list[PipelineModel]:
    """The deal pipelines of the organization, default first. With
    ``kind=lead``, the lead pipelines, each with its lead statuses (CRM-M4a)."""
    wanted = _kind_of(kind)
    async with _tenant_session() as db:
        return await read_pipelines(db, kind=wanted)


# ── Create a pipeline (CRM-M3a, CRM-M4a) ────────────────────────────────────

#: The most stages that a new pipeline may list.
MAX_STAGES = 50

#: The two types that close a record. A new pipeline holds one of each.
_CLOSING: tuple[str, ...] = ("won", "lost")

#: A name as the SQL folds it: inner spaces folded, in lower case. The same
#: rule as :func:`_fold`.
_FOLDED_NAME = "lower(regexp_replace(btrim(name), '\\s+', ' ', 'g'))"

#: A pipeline of the same kind that holds the name already, in any case and
#: with its inner spaces folded, as :func:`_named_stages` folds a stage name.
#: A deal pipeline and a lead pipeline may share a name (CRM-M4a), so "Trade
#: show" leads can feed "Trade show" deals.
_NAME_HOLDER_SQL = (
    f"SELECT name FROM crm_pipelines WHERE kind = :kind AND {_FOLDED_NAME} = lower(:name) "
    "LIMIT 1"
)

#: CRM-M4a: the deal pipelines that a name names, for ``feeds``.
_FEEDS_SQL = (
    f"SELECT id, name FROM crm_pipelines WHERE kind = 'deal' AND {_FOLDED_NAME} = lower(:name) "
    "ORDER BY position, created_at, id"
)

#: A stage that no picker offers: hidden, or gone from the Zoho picklist.
_UNUSABLE = "(s.hidden OR s.source_gone_at IS NOT NULL)"

#: The stages of one pipeline, in its order. ``{stages}`` and ``{table}`` are
#: the two tables of a :class:`PipelineKind`.
_PIPELINE_STAGES_SQL = (
    f"SELECT s.id, s.name, s.type, {_UNUSABLE} AS unusable FROM {{stages}} ps "
    "JOIN {table} s ON s.id = ps.status_id "
    "WHERE ps.pipeline_id = CAST(:p AS uuid) ORDER BY ps.position, s.name"
)


def _pipeline_stages_sql(kind: PipelineKind) -> str:
    return _PIPELINE_STAGES_SQL.format(stages=kind.stages_table, table=kind.status_table)


@dataclass(frozen=True)
class _Planned:
    id: str | None
    name: str
    type: str
    source: str
    #: Hidden, or gone from the source: it counts for no check.
    hidden: bool = False


@dataclass(frozen=True)
class _Plan:
    """What a create makes: the kind, the name, the copy, the stages in order
    and, for a lead pipeline, the deal pipeline that it feeds."""

    kind: PipelineKind
    name: str
    copy_of: str | None
    stages: list[_Planned]
    feeds_id: str | None = None
    feeds_name: str | None = None
    #: The name of the default deal pipeline now, for a lead plan.
    default_deal: str | None = None


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _plan_changed(kind: PipelineKind, *, feeds: bool = False) -> HTTPException:
    """The plan of the create differs from the card: the stages, or the feed.

    The words say which of the two changed, and the next step. ``changed``
    says it in code (``stages`` or ``feeds``), so the chat tool can tell the
    member which one (CRM-M4a repair 2)."""
    if feeds:
        message = (
            "The deal pipeline that this lead pipeline feeds changed after the card "
            "showed it, so nothing was made. Ask for the card again, and check where "
            "its deals go."
        )
    else:
        message = (
            f"The {kind.stage_nouns} changed after the card showed them, so nothing "
            f"was made. Ask for the card again, and check the {kind.stage_nouns}."
        )
    return HTTPException(status_code=409, detail={
        "code": "plan_changed", "message": message,
        "changed": "feeds" if feeds else "stages",
    })


def _kind_of(kind: str) -> PipelineKind:
    found = PIPELINE_KINDS.get((kind or "").strip().lower())
    if found is None:
        raise HTTPException(status_code=422, detail=(
            f"A pipeline kind is one of: {', '.join(PIPELINE_KINDS)}."
        ))
    return found


def _fold(name: str) -> str:
    """A name as two names compare: inner spaces folded, in lower case."""
    return " ".join(name.split()).lower()


def _clean_name(raw: str | None, what: str) -> str:
    name = (raw or "").strip()
    if not name or len(name) > MAX_NAME:
        raise HTTPException(status_code=422, detail=(
            f"{what} needs a name of 1 to {MAX_NAME} characters."
        ))
    return name


async def _default_closing(db: Any, kind: PipelineKind, closing: str) -> _Planned | None:
    """The org's own stage of type *closing*. A hidden stage, or one gone from
    the source, never counts, because no picker offers it.

    A deal stage: the one that the default pipeline lists first
    (``core.default_pipeline_id``, the one seam), else the first one in lane
    order. A lead status (CRM-M4a): the first visible one in lane order,
    because a lead has no default pipeline. ``pipeline.convert_lead`` moves a
    converted lead to the first ``won`` status with no visibility filter, so
    the two can differ while the first ``won`` status is hidden. CRM-M4b puts
    one rule in both places."""
    if kind.status_kind != "deal":
        row = (await db.execute(text(
            f"SELECT s.id, s.name, s.type FROM {kind.status_table} s "
            f"WHERE s.type = :t AND NOT {_UNUSABLE} ORDER BY s.position, s.name LIMIT 1"
        ), {"t": closing})).first()
    else:
        row = (await db.execute(text(
            f"SELECT s.id, s.name, s.type FROM {kind.status_table} s "
            "LEFT JOIN crm_pipeline_stages ps ON ps.status_id = s.id "
            "AND ps.pipeline_id = CAST(:dp AS uuid) "
            f"WHERE s.type = :t AND NOT {_UNUSABLE} "
            "ORDER BY (ps.status_id IS NULL), ps.position, s.position, s.name LIMIT 1"
        ), {"t": closing, "dp": await default_pipeline_id(db)})).first()
    if row is None:
        return None
    return _Planned(str(row.id), str(row.name), closing, "added")


async def _feeds_of(
    db: Any, kind: PipelineKind, raw: str | None, share: str,
    pinned: str | None = None,
) -> tuple[str | None, str | None]:
    """The id and the name of the deal pipeline that a new lead pipeline feeds
    (CRM-M4a), or ``(None, None)``. It resolves the name under row level
    security, so a deal pipeline of another org is unknown here.

    *pinned* is the id that the card showed (``planned_feeds``). When the
    name now resolves to no pipeline or to another one, the create refuses
    with 409 ``plan_changed``, as for a stage."""
    wanted = (raw or "").strip()
    if not wanted:
        if pinned:
            raise _plan_changed(kind, feeds=True)
        return None, None
    if kind.status_kind != "lead":
        raise HTTPException(status_code=422, detail=(
            "Only a lead pipeline feeds a deal pipeline. Leave out 'feeds' for a "
            "deal pipeline."
        ))
    rows = (await db.execute(
        text(_FEEDS_SQL + share), {"name": _fold(wanted)},
    )).fetchall()
    if pinned is not None and (len(rows) != 1 or str(rows[0].id) != pinned.strip().lower()):
        raise _plan_changed(kind, feeds=True)
    if not rows:
        raise _refuse(404, "no_pipeline", (
            f"There is no deal pipeline called {quoted(wanted)}. Pick one of the "
            "deal pipelines in CRM settings."
        ))
    if len(rows) > 1:
        raise _refuse(409, "feeds_ambiguous", (
            f"Two deal pipelines are called {quoted(wanted)}. Rename one of them in "
            "CRM settings, and then try again."
        ))
    return str(rows[0].id), str(rows[0].name)


async def plan_pipeline(db: Any, payload: PipelineCreateIn, *, lock: bool = False) -> _Plan:
    """The checks and the stage list of a create, in order. It reads only.

    The preview calls it, and so does the create, after it took the create
    lock. With *lock*, it reads the stages, the copied pipeline and the fed
    pipeline ``FOR SHARE``, so none changes before the create writes. Each
    refusal says what happened and what to do next.
    """
    kind = _kind_of(payload.kind)
    noun = kind.pipeline_noun
    name = _clean_name(payload.name, f"A {noun}")
    if (payload.copy_from is None) == (payload.stages is None):
        raise HTTPException(status_code=422, detail=(
            f"Start the {noun} from a copy of a {noun} (copy_from) or from a "
            f"list of {kind.stage_nouns} (stages). Give one of the two."
        ))
    # Mirror mode: Zoho owns the pipelines (409 `mirror_mode_settings`).
    await assert_writable(db, current_tenant(), "crm_pipelines", creating=True)
    holder = (await db.execute(
        text(_NAME_HOLDER_SQL), {"name": _fold(name), "kind": kind.status_kind},
    )).first()
    if holder is not None:
        raise _refuse(409, "name_taken", (
            f"A {noun} named {quoted(holder.name)} exists already. "
            "Give the new pipeline another name."
        ))
    share = " FOR SHARE" if lock else ""
    pinned = payload.planned_feeds if payload.planned is not None else None
    feeds_id, feeds_name = await _feeds_of(db, kind, payload.feeds, share, pinned)
    copy_of: str | None = None
    planned: list[_Planned] = []
    if payload.copy_from is not None:
        source = (await db.execute(text(
            "SELECT id, name FROM crm_pipelines WHERE id = CAST(:id AS uuid) "
            "AND kind = :kind" + share
        ), {"id": checked_uuid(payload.copy_from, "copy_from"),
            "kind": kind.status_kind})).first()
        if source is None:
            raise _refuse(404, "no_pipeline", (
                f"There is no {noun} to copy with that id. Pick one of the "
                f"{noun}s in CRM settings."
            ))
        copy_of = str(source.name)
        rows = (await db.execute(text(
            _pipeline_stages_sql(kind) + share
        ), {"p": str(source.id)})).fetchall()
        planned = [_Planned(str(r.id), str(r.name), str(r.type), "existing",
                            hidden=bool(r.unusable)) for r in rows]
    else:
        planned = await _named_stages(db, kind, payload.stages or [], share)
    # Only a stage that a picker offers counts: a hidden one cannot hold a record.
    if not any(p.type in WEIGHTED_TYPES and not p.hidden for p in planned):
        raise HTTPException(status_code=422, detail=(
            f"A {noun} needs at least one open {kind.stage_noun}, where a "
            f"{kind.record_noun} sits before it closes. Add one, such as "
            f"\"{kind.open_example}\"."
        ))
    for closing in _CLOSING:
        if any(p.type == closing and not p.hidden for p in planned):
            continue
        found = await _default_closing(db, kind, closing)
        if found is None:
            raise HTTPException(status_code=422, detail=(
                f"This CRM has no {closing} {kind.stage_noun} yet. Add a "
                f"{kind.stage_noun} of the type {closing} to the list."
            ))
        planned.append(found)
    default_deal: str | None = None
    if kind.status_kind == "lead":
        row = (await db.execute(
            text("SELECT name FROM crm_pipelines WHERE id = CAST(:d AS uuid)"),
            {"d": await default_pipeline_id(db)},
        )).first()
        default_deal = str(row.name) if row is not None else None
    return _Plan(kind, name, copy_of, planned, feeds_id, feeds_name, default_deal)


async def _named_stages(
    db: Any, kind: PipelineKind, stages: list[NewStageIn], share: str,
) -> list[_Planned]:
    """Each named stage, resolved against the stages that the CRM holds."""
    noun, nouns = kind.stage_noun, kind.stage_nouns
    if len(stages) > MAX_STAGES:
        raise HTTPException(status_code=422, detail=(
            f"A {kind.pipeline_noun} holds at most {MAX_STAGES} {nouns}. This list "
            f"has {len(stages)}."
        ))
    rows = (await db.execute(text(
        f"SELECT s.id, s.name, s.type, {_UNUSABLE} AS unusable, "
        "(s.source_gone_at IS NOT NULL) AS gone "
        f"FROM {kind.status_table} s ORDER BY s.position, s.name" + share
    ), {})).fetchall()
    by_name: dict[str, list[Any]] = {}
    for row in rows:
        by_name.setdefault(_fold(str(row.name)), []).append(row)
    closing_words = dict(kind.closing_words)
    seen: set[str] = set()
    planned: list[_Planned] = []
    for index, stage in enumerate(stages, start=1):
        name = _clean_name(stage.name, f"{noun.capitalize()} {index}")
        key = _fold(name)
        if key in seen:
            raise HTTPException(status_code=422, detail=(
                f"The list names the {noun} {quoted(name)} twice. Keep it once."
            ))
        seen.add(key)
        wanted = (stage.type or "").strip().lower() or None
        if wanted is not None and wanted not in STATUS_TYPES:
            raise HTTPException(status_code=422, detail=(
                f"The type of {quoted(name)} is one of: {', '.join(STATUS_TYPES)}."
            ))
        matches = by_name.get(key, [])
        if len(matches) > 1:
            names = ", ".join(quoted(m.name) for m in matches)
            raise _refuse(409, "stage_ambiguous", (
                f"Two {nouns} match {quoted(name)}: {names}. Rename one of them "
                "in CRM settings, and then try again."
            ))
        if matches:
            row = matches[0]
            # A stage that no picker offers is never reused in silence.
            if row.gone:
                raise _refuse(409, "stage_gone", (
                    f"Zoho no longer lists the {noun} {quoted(row.name)}. Use "
                    f"another name for this {noun}."
                ))
            if row.unusable:
                raise _refuse(409, "stage_hidden", (
                    f"The {noun} {quoted(row.name)} is hidden. Show it in CRM "
                    "settings first, or use another name."
                ))
            if wanted is not None and wanted != str(row.type):
                raise HTTPException(status_code=422, detail=(
                    f"The {noun} {quoted(row.name)} exists, and its type is "
                    f"{row.type}. Leave out the type to use it, or give the new "
                    f"{noun} another name."
                ))
            planned.append(_Planned(str(row.id), str(row.name), str(row.type), "existing"))
        else:
            # "Won" or "Closed Lost" with no type closes a record, as it reads.
            planned.append(_Planned(
                None, name, wanted or closing_words.get(key, "open"), "new"))
    return planned


def _plan_model(plan: _Plan) -> PipelinePlanModel:
    return PipelinePlanModel(
        name=plan.name, kind=plan.kind.status_kind, copy_of=plan.copy_of,
        stages=[PlannedStageModel(id=p.id, name=p.name, type=p.type, source=p.source,
                                  hidden=p.hidden) for p in plan.stages],
        feeds=plan.feeds_name, feeds_id=plan.feeds_id,
        default_deal_pipeline=plan.default_deal,
    )


#: The stages of a preview, as one JSON value of the query: a list of
#: ``{name, type}``, the shape of the ``stages`` of the body of a create.
_STAGE_LIST = TypeAdapter(list[NewStageIn])


def _preview_stages(raw: str | None) -> list[NewStageIn] | None:
    if raw is None:
        return None
    try:
        return _STAGE_LIST.validate_json(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail=(
            "'stages' is a JSON list of stages, each with a name and, for a new "
            "stage, a type: [{\"name\": \"Qualify\"}, {\"name\": \"Won\", \"type\": \"won\"}]."
        )) from None


@router.get(
    "/pipelines/preview", response_model=PipelinePlanModel,
    dependencies=[Depends(require_crm_pipelines), *ADMIN_ONLY],
)
async def preview_pipeline(
    name: str = Query("", description="The name of the new pipeline."),
    kind: str = Query("deal", description="deal (the default) or lead (CRM-M4a)."),
    copy_from: str | None = Query(None, description="The id of a pipeline to copy."),
    stages: str | None = Query(None, description="A JSON list of {name, type}."),
    feeds: str | None = Query(None, description="A lead pipeline: the deal pipeline it feeds."),
    user: UserContext = Depends(get_current_user),
) -> PipelinePlanModel:
    """What ``POST /crm/pipelines`` would make, with every refusal of the
    create. It reads only, so it is a GET: the chat tool reads it before its
    card, and a write tool sends only reads before its card
    (``test_crm_agent_write.py``). The settings dialog shows it too."""
    payload = PipelineCreateIn(
        name=name, kind=kind, copy_from=copy_from, stages=_preview_stages(stages),
        feeds=feeds,
    )
    async with _tenant_session() as db:
        plan = await plan_pipeline(db, payload)
    return _plan_model(plan)


@router.post(
    "/pipelines", response_model=PipelineModel, status_code=201,
    dependencies=[Depends(require_crm_pipelines), *ADMIN_ONLY],
)
async def create_pipeline(
    payload: PipelineCreateIn, request: Request,
    user: UserContext = Depends(get_current_user),
) -> PipelineModel:
    """Create a deal pipeline, or a lead pipeline with ``kind=lead``. Native
    mode only: Mirror mode answers 409."""
    via = await change_log.via_of(request)
    async with _tenant_session() as db:
        new_id = await create_pipeline_in(db, user, payload, via=via)
        return await _one(db, new_id, _kind_of(payload.kind))


async def create_pipeline_in(
    db: Any, user: Any, payload: PipelineCreateIn, *, via: str = change_log.VIA_UI,
) -> str:
    """The write of ``POST /crm/pipelines``, in the caller's transaction.
    Returns the id of the new pipeline.

    It takes the create lock of the org, plans (:func:`plan_pipeline`),
    makes each new stage through ``admin.create_status_in``, and puts the
    stages in the pipeline in the planned order, in the stage table of the
    kind. It logs one ``pipeline`` row, and each new stage logs its own
    ``deal_stage`` or ``lead_status`` row (CRM-M1)."""
    await lock_pipeline_creates(db)
    plan = await plan_pipeline(db, payload, lock=True)
    kind = plan.kind
    if payload.planned is not None and [p.id for p in plan.stages] != [
        (str(i).strip().lower() if i else None) for i in payload.planned
    ]:
        raise _plan_changed(kind)
    position = int((await db.execute(text(
        "SELECT COALESCE(max(position), -1) + 1 FROM crm_pipelines WHERE kind = :kind"
    ), {"kind": kind.status_kind})).scalar() or 0)
    values: dict[str, Any] = {
        "name": plan.name, "position": position, "is_default": False,
        "kind": kind.status_kind,
    }
    if plan.feeds_id is not None:
        values["feeds_pipeline_id"] = plan.feeds_id
    pipeline = await insert_row(db, "crm_pipelines", values)
    made: list[_Planned] = []
    for stage in plan.stages:
        if stage.id is not None:
            made.append(stage)
            continue
        status: dict[str, Any] = {"name": stage.name, "type": stage.type}
        if stage.type in CLAMPED_PROBABILITY:
            status["probability"] = CLAMPED_PROBABILITY[stage.type]
        try:
            row = await create_status_in(
                db, user, kind.status_kind, status, into_default=False, via=via,
            )
        except IntegrityError as exc:
            if not _violates(exc, _NAME_INDEX[kind.status_table]):
                raise
            raise _refuse(409, "stage_taken", (
                f"A {kind.stage_noun} named {quoted(stage.name)} was added at the "
                f"same moment. Try again, and the pipeline uses that {kind.stage_noun}."
            )) from None
        made.append(_Planned(str(row.id), str(row.name), str(row.type), stage.source))
    await assert_writable(db, current_tenant(), kind.stages_table, creating=True)
    source = checked_uuid(payload.copy_from, "copy_from") if payload.copy_from else None
    for place, stage in enumerate(made):
        if kind.status_kind == "deal":
            # CRM-M3b: a deal membership takes the win chance and the warning
            # days of the copied pipeline, else of the stage row.
            await db.execute(text(
                "INSERT INTO crm_pipeline_stages (pipeline_id, status_id, position, "
                f"probability, max_dwell_days) SELECT CAST(:p AS uuid), s.id, :pos, "
                f"{copied_values_sql('src')} FROM crm_deal_statuses s "
                "LEFT JOIN crm_pipeline_stages src ON src.status_id = s.id "
                "AND src.pipeline_id = CAST(:src AS uuid) WHERE s.id = CAST(:s AS uuid)"
            ), {"p": str(pipeline.id), "s": stage.id, "pos": place, "src": source})
            continue
        await db.execute(text(
            f"INSERT INTO {kind.stages_table} (pipeline_id, status_id, position) "
            "VALUES (CAST(:p AS uuid), CAST(:s AS uuid), :pos)"
        ), {"p": str(pipeline.id), "s": stage.id, "pos": place})
    count = f"{len(made)} " + (kind.stage_noun if len(made) == 1 else kind.stage_nouns)
    noun = kind.pipeline_noun
    summary = (
        f"Created the {noun} {quoted(plan.name)}, a copy of {quoted(plan.copy_of)}, "
        f"with {count}"
        if plan.copy_of is not None
        else f"Created the {noun} {quoted(plan.name)} with {count}"
    )
    if plan.feeds_name is not None:
        summary += f". It feeds deals into {quoted(plan.feeds_name)}"
    after: dict[str, Any] = {
        "name": plan.name,
        "kind": kind.status_kind,
        "stages": [{"id": s.id, "name": s.name, "type": s.type, "source": s.source}
                   for s in made],
    }
    if plan.copy_of is not None:
        after["copy_of"] = plan.copy_of
    if plan.feeds_id is not None:
        after["feeds"] = {"id": plan.feeds_id, "name": plan.feeds_name}
    await log_change(db, user, Change(
        area="pipeline", action=change_log.CREATE, summary=summary,
        ladder=change_log.COMPANY, entity_id=str(pipeline.id), after=after,
    ), via=via)
    return str(pipeline.id)


async def _stage_ids(
    db: Any, pipeline_id: str, *, lock: bool = False, kind: PipelineKind = DEAL,
) -> list[str]:
    """The stage ids of one pipeline, in its order. *lock* reads them FOR UPDATE."""
    rows = (await db.execute(text(
        f"SELECT status_id FROM {kind.stages_table} WHERE pipeline_id = CAST(:p AS uuid) "
        "ORDER BY position, status_id" + (" FOR UPDATE" if lock else "")
    ), {"p": pipeline_id})).fetchall()
    return [str(r.status_id) for r in rows]


#: CRM-M4a: the lead pipelines that feed one deal pipeline.
_FED_BY_SQL = (
    "SELECT name FROM crm_pipelines WHERE kind = 'lead' "
    "AND feeds_pipeline_id = CAST(:p AS uuid) ORDER BY position, name"
)


async def delete_pipeline_in(
    db: Any, user: Any, row: Any, *, undoes: str | None = None,
    kind: PipelineKind = DEAL,
) -> None:
    """Delete one pipeline that holds no record, in the caller's transaction.

    The Undo of a create calls it. The caller holds the pipeline row ``FOR
    UPDATE``, so no record can join it before the DELETE: the key check of a
    deal or a lead write waits on that lock. It removes no stage: the Undo of
    a create removes the stages that the create made (:func:`_undo_pipeline`).
    A deal pipeline that a lead pipeline feeds stays (CRM-M4a): the feed of
    that lead pipeline would change with no row. A new lead pipeline that
    names it reads it ``FOR SHARE``, so the two never pass each other. It
    logs one row (CRM-M1)."""
    await assert_writable(db, current_tenant(), "crm_pipelines")
    noun = kind.pipeline_noun
    held = int((await db.execute(text(
        f"SELECT count(*) FROM {kind.records_table} WHERE pipeline_id = CAST(:p AS uuid)"
    ), {"p": str(row.id)})).scalar() or 0)
    if held:
        records = kind.record_noun + ("" if held == 1 else "s")
        raise change_log.in_use(
            f"The {noun} {quoted(row.name)} holds {held} {records} now. Move them "
            "to another pipeline first, and then undo this change."
        )
    if kind.status_kind == "deal":
        feeders = [str(r.name) for r in (await db.execute(
            text(_FED_BY_SQL), {"p": str(row.id)},
        )).fetchall()]
        if feeders:
            names = ", ".join(quoted(n) for n in feeders)
            which = (f"The lead pipeline {names} feeds" if len(feeders) == 1
                     else f"The lead pipelines {names} feed")
            raise change_log.in_use(
                f"{which} deals into the pipeline {quoted(row.name)}. Undo the "
                "lead pipeline first, and then undo this change."
            )
    stages = (await db.execute(text(_pipeline_stages_sql(kind)), {"p": str(row.id)})).fetchall()
    await db.execute(text(
        "DELETE FROM crm_pipelines WHERE id = CAST(:p AS uuid)"
    ), {"p": str(row.id)})
    await log_change(db, user, Change(
        area="pipeline", action=change_log.DELETE,
        summary=f"Removed the {noun} {quoted(row.name)}",
        ladder=change_log.COMPANY, entity_id=str(row.id),
        before={
            "name": row.name,
            "kind": kind.status_kind,
            "stages": [{"id": str(s.id), "name": s.name, "type": s.type} for s in stages],
        },
    ), undoes=undoes)


async def _lock_new_stages(
    db: Any, stages: list[dict[str, Any]], kind: PipelineKind = DEAL,
) -> list[Any]:
    """The rows of the stages that the create made (``source`` ``new``), each
    locked ``FOR UPDATE``, in the order of the create. A stage that is gone is
    left out.

    The Undo calls this BEFORE it locks the pipeline. A deal write takes the
    key lock of its stage before the key lock of its pipeline, so the Undo
    takes them in the same order and never waits on a deal write in a cycle
    (40P01). A lead write takes them in the same order. The locks go in id
    order, so two Undos also agree."""
    wanted = [str(s.get("id") or "") for s in stages if s.get("source") == "new"]
    locked: dict[str, Any] = {}
    for stage_id in sorted(set(wanted)):
        row = await load_row(db, kind.status_table, stage_id, lock=True)
        if row is not None:
            locked[stage_id] = row
    return [locked[i] for i in wanted if i in locked]


async def _new_stages_to_remove(
    db: Any, pipeline_id: str, rows: list[Any], kind: PipelineKind = DEAL,
) -> list[Any]:
    """Of the locked stages that the create made, the ones that no other
    pipeline of the kind holds now. The Undo of the create removes them.

    A new stage that holds a record refuses the Undo with the name of the
    stage and the count. A stage that another pipeline holds now stays. So
    does a stage that another pipeline needs for the Undo of an open change
    (CRM-M4a repairs 3 and 4, ``core.pipelines_holding``): it stays, and its own
    create row stays open."""
    doomed: list[Any] = []
    noun = kind.stage_noun
    for row in rows:
        held = await status_in_use(db, kind.status_kind, str(row.id))
        if held:
            records = kind.record_noun + ("" if held == 1 else "s")
            raise change_log.in_use(
                f"The {noun} {quoted(row.name)}, which this change made, holds "
                f"{held} {records} now. Move them to another {noun} first, and then "
                "undo this change."
            )
        shared = int((await db.execute(text(
            f"SELECT count(*) FROM {kind.stages_table} WHERE status_id = CAST(:s AS uuid) "
            "AND pipeline_id <> CAST(:p AS uuid)"
        ), {"s": str(row.id), "p": pipeline_id})).scalar() or 0)
        others = [h for h in await pipelines_holding(db, kind.status_table, str(row.id))
                  if h.id != str(pipeline_id).strip().lower()]
        if not shared and not others:
            doomed.append(row)
    return doomed


async def _remove_new_stage(
    db: Any, user: Any, row: Any, pipeline_name: str, kind: PipelineKind = DEAL,
) -> None:
    """Delete one stage that the create made, through the helper of
    ``DELETE /crm/statuses/{kind}/{id}``, and log one row. The row undoes the
    create row of the stage, so that row offers no second Undo."""
    await delete_status_in(db, kind.status_kind, str(row.id))
    area = STATUS_AREAS[kind.status_kind]
    opened = await change_log.open_change(db, area, change_log.CREATE, str(row.id))
    await log_change(db, user, Change(
        area=area, action=change_log.DELETE,
        summary=(
            f"Removed the {kind.status_kind} {kind.stage_noun} {quoted(row.name)} with "
            f"the Undo of the {kind.pipeline_noun} {quoted(pipeline_name)}"
        ),
        ladder=change_log.TEAM, entity_id=str(row.id),
        before={k: getattr(row, k) for k in STATUS_LOG_KEYS if hasattr(row, k)},
        reversible=False,
    ), undoes=opened)


@router.patch(
    "/pipelines/{pipeline_id}", response_model=PipelineModel, dependencies=ADMIN_ONLY,
)
async def rename_pipeline(
    pipeline_id: str, payload: PipelineIn,
    user: UserContext = Depends(get_current_user),
) -> PipelineModel:
    """Rename a pipeline. Native mode only: Mirror mode answers 409."""
    wanted = checked_uuid(pipeline_id, "pipeline_id")
    name = (payload.name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise HTTPException(
            status_code=422,
            detail=f"A pipeline needs a name of 1 to {MAX_NAME} characters.",
        )
    async with _tenant_session() as db:
        await rename_pipeline_in(db, user, wanted, name)
        return await _one(db, wanted)


async def rename_pipeline_in(
    db: Any, user: Any, pipeline_id: str, name: str, *, undoes: str | None = None,
) -> None:
    """The write of ``PATCH /crm/pipelines/{id}``, in the caller's
    transaction. The route and the Undo of a rename both call it. It logs
    one row when the name changes (CRM-M1)."""
    row = await _native_pipeline(db, pipeline_id)
    await update_row(db, "crm_pipelines", pipeline_id, {"name": name}, touch=False)
    if row.name != name:
        await log_change(db, user, Change(
            area="pipeline", action=change_log.CHANGE,
            summary=f"Renamed the pipeline {quoted(row.name)} to {quoted(name)}",
            ladder=change_log.COMPANY, entity_id=str(pipeline_id),
            before={"name": row.name}, after={"name": name},
        ), undoes=undoes)


@router.post(
    "/pipelines/{pipeline_id}/stages", response_model=PipelineModel,
    status_code=201, dependencies=ADMIN_ONLY,
)
async def add_pipeline_stage(
    pipeline_id: str, payload: StageIn,
    user: UserContext = Depends(get_current_user),
) -> PipelineModel:
    """Put an existing deal stage in a pipeline. Native mode only.

    A stage that already sits in the pipeline keeps its place. A new stage goes
    to the end unless ``position`` says where.
    """
    wanted = checked_uuid(pipeline_id, "pipeline_id")
    status_id = checked_uuid(payload.status_id, "status_id")
    async with _tenant_session() as db:
        await add_stage_in(db, user, wanted, status_id, payload.position)
        return await _one(db, wanted)


#: Where a stage sits in a pipeline, or no row.
_PLACE_SQL = (
    "SELECT position FROM crm_pipeline_stages WHERE pipeline_id = CAST(:p AS uuid) "
    "AND status_id = CAST(:s AS uuid)"
)


async def add_stage_in(
    db: Any, user: Any, pipeline_id: str, status_id: str, position: int | None,
    *, undoes: str | None = None, restore: dict[str, Any] | None = None,
) -> bool:
    """The write of ``POST /crm/pipelines/{id}/stages``, in the caller's
    transaction. The route and the Undo of a removal both call it. It logs
    one row when ITS statement put the stage in the pipeline (CRM-M1), and
    returns whether it did. A write of another request that put the stage
    there first, between the check and the INSERT, makes the INSERT do
    nothing, so no row is logged. It takes the pipeline create lock first
    (CRM-M4a repair 3), because it changes who holds the stage.

    CRM-M3b: the new membership copies the win chance and the warning days
    of the stage row. *restore* holds the values of a membership that the
    Undo of a take-out puts back, as the take-out row stored them."""
    await lock_pipeline_creates(db)
    pipeline = await _native_pipeline(db, pipeline_id)
    stage = await require_row(db, "crm_deal_statuses", status_id, "Status")
    await assert_writable(db, current_tenant(), "crm_pipeline_stages")
    if (await db.execute(text(_PLACE_SQL), {"p": pipeline_id, "s": status_id})).first():
        return False  # It sits there already, and keeps its place.
    if position is None:
        position = int((await db.execute(
            text(
                "SELECT COALESCE(max(position), 0) + 1 FROM crm_pipeline_stages "
                "WHERE pipeline_id = CAST(:p AS uuid)"
            ),
            {"p": pipeline_id},
        )).scalar() or 1)
    if restore is None:
        values_sql, kept = copied_values_sql(), {}
    else:
        values_sql = "CAST(:probability AS smallint), CAST(:max_dwell_days AS smallint)"
        kept = {c: restore.get(c) for c in MEMBERSHIP_COLUMNS}
    inserted = (await db.execute(
        text(
            "INSERT INTO crm_pipeline_stages (pipeline_id, status_id, position, "
            f"probability, max_dwell_days) SELECT CAST(:p AS uuid), s.id, :pos, {values_sql} "
            "FROM crm_deal_statuses s WHERE s.id = CAST(:s AS uuid) "
            "ON CONFLICT (pipeline_id, status_id) DO NOTHING "
            "RETURNING position, probability, max_dwell_days"
        ),
        {"p": pipeline_id, "s": status_id, "pos": position, **kept},
    )).first()
    if inserted is None:
        return False  # Another write put it there first. This one changed nothing.
    await log_change(db, user, Change(
        area="pipeline_stage", action=change_log.CREATE,
        summary=(
            f"Added the stage {quoted(stage.name)} to the pipeline "
            f"{quoted(pipeline.name)}"
        ),
        ladder=change_log.TEAM, entity_id=str(pipeline_id),
        after={"status_id": status_id, "position": position, "stage": stage.name,
               **{c: getattr(inserted, c, None) for c in MEMBERSHIP_COLUMNS}},
    ), undoes=undoes)
    return True


@router.delete(
    "/pipelines/{pipeline_id}/stages/{status_id}", response_model=PipelineModel,
    dependencies=ADMIN_ONLY,
)
async def remove_pipeline_stage(
    pipeline_id: str, status_id: str,
    user: UserContext = Depends(get_current_user),
) -> PipelineModel:
    """Take a stage out of a pipeline. Native mode only.

    The stage itself stays, because it is one global list. A stage that still
    holds a deal of this pipeline cannot leave it: that is a 409.
    """
    wanted = checked_uuid(pipeline_id, "pipeline_id")
    stage = checked_uuid(status_id, "status_id")
    async with _tenant_session() as db:
        await remove_stage_in(db, user, wanted, stage)
        return await _one(db, wanted)


async def remove_stage_in(
    db: Any, user: Any, pipeline_id: str, status_id: str,
    *, undoes: str | None = None,
) -> bool:
    """The write of ``DELETE /crm/pipelines/{id}/stages/{status_id}``, in the
    caller's transaction. The route and the Undo of an added stage both call
    it. It logs one row when ITS statement took the stage out (CRM-M1), and
    returns whether it did. The place in the row comes from ``RETURNING``,
    so a removal by another request at the same time logs nothing here. It
    takes the pipeline create lock first (CRM-M4a repair 3), because it
    changes who holds the stage."""
    await lock_pipeline_creates(db)
    pipeline = await _native_pipeline(db, pipeline_id)
    await assert_writable(db, current_tenant(), "crm_pipeline_stages")
    in_use = int((await db.execute(
        text(
            "SELECT count(*) FROM crm_deals WHERE pipeline_id = CAST(:p AS uuid) "
            "AND status_id = CAST(:s AS uuid)"
        ),
        {"p": pipeline_id, "s": status_id},
    )).scalar() or 0)
    if in_use:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stage_in_use",
                "message": (
                    f"This stage still holds {in_use} deal(s) of this pipeline. "
                    "Move them to another stage first."
                ),
            },
        )
    place = (await db.execute(
        text(
            "DELETE FROM crm_pipeline_stages WHERE pipeline_id = CAST(:p AS uuid) "
            "AND status_id = CAST(:s AS uuid) RETURNING position, probability, max_dwell_days"
        ),
        {"p": pipeline_id, "s": status_id},
    )).first()
    if place is None:
        return False  # It was not there, or another write took it. Nothing changed here.
    stage = await load_row(db, "crm_deal_statuses", status_id)
    name = getattr(stage, "name", None) or "a stage"
    # CRM-M3b: "before" keeps the win chance and the warning days of the
    # membership, so the Undo puts them back.
    await log_change(db, user, Change(
        area="pipeline_stage", action=change_log.DELETE,
        summary=f"Took the stage {quoted(name)} out of the pipeline {quoted(pipeline.name)}",
        ladder=change_log.TEAM, entity_id=str(pipeline_id),
        before={"status_id": status_id, "position": int(place.position or 0), "stage": name,
                **{c: getattr(place, c, None) for c in MEMBERSHIP_COLUMNS}},
    ), undoes=undoes)
    return True


# ── The win chance and the warning days of a stage in a pipeline (CRM-M3b) ──


class StageValuesIn(BaseModel):
    """``PATCH /crm/pipelines/{id}/stages/{status_id}``: one value or both.

    Whole numbers only, so ``"40"``, ``40.0`` and ``true`` each give 422. No
    other key: the actor, the tenant and ``via`` never come from the body
    (R11)."""

    model_config = ConfigDict(extra="forbid")

    #: The win chance of the stage in this pipeline, 0 to 100.
    probability: StrictInt | None = None
    #: The days before the warning, 1 to 32767. ``null`` turns it off.
    max_dwell_days: StrictInt | None = None


#: The words of each value in a summary.
_VALUE_WORDS: dict[str, str] = {"probability": "win chance", "max_dwell_days": "warning days"}

#: The membership of one stage in one pipeline, locked until the write.
_MEMBERSHIP_SQL = (
    "SELECT position, probability, max_dwell_days FROM crm_pipeline_stages "
    "WHERE pipeline_id = CAST(:p AS uuid) AND status_id = CAST(:s AS uuid) FOR UPDATE"
)

#: The words of a membership that is gone. The status code is 404 for the
#: route and 409 for an Undo.
_NOT_IN_PIPELINE = (
    "The stage {stage} is not in the pipeline {pipeline}. Add the stage to the "
    "pipeline first, or pick a stage that the pipeline lists."
)


def _check_stage_values(values: dict[str, Any], stage: Any) -> None:
    """422 for a value that the route does not take, with words that say what
    to do. The clamp of D-CRM-10 judges the state that the write leaves: a
    ``won`` stage forecasts 100 and a ``lost`` stage 0, in each pipeline."""
    if not values:
        raise HTTPException(status_code=422, detail=(
            "Send the win chance (probability), the warning days (max_dwell_days), "
            "or both."
        ))
    if "probability" in values:
        chance = values["probability"]
        if chance is None or not 0 <= int(chance) <= 100:
            raise HTTPException(status_code=422, detail=(
                "A win chance is a whole number from 0 to 100. Send a number in "
                "that range."
            ))
        pinned = CLAMPED_PROBABILITY.get(str(getattr(stage, "type", "") or ""))
        if pinned is not None and int(chance) != pinned:
            raise HTTPException(status_code=422, detail=(
                f"A '{stage.type}' stage always forecasts {pinned}%, in every "
                f"pipeline. Set the win chance to {pinned}, or give the stage "
                "another type first (D-CRM-10)."
            ))
    if "max_dwell_days" in values:
        days = values["max_dwell_days"]
        if days is not None and not 1 <= int(days) <= 32767:
            raise HTTPException(status_code=422, detail=(
                "'max_dwell_days' is a whole number of days from 1 to 32767, or "
                "null for a stage that never shows the warning."
            ))
        limit = getattr(stage, "max_dwell_days", None)
        if days is None and limit is not None:
            # NULL on a membership reads the stage row (migration 394), so it
            # cannot mean "never" while the stage row holds a limit.
            raise HTTPException(status_code=422, detail=(
                f"The stage {quoted(stage.name)} shows the warning after {limit} "
                "days in each pipeline, so one pipeline cannot turn it off yet. Set "
                "a number of days for this pipeline, or turn the warning off for "
                "the stage in CRM settings."
            ))


@router.patch(
    "/pipelines/{pipeline_id}/stages/{status_id}", response_model=PipelineModel,
    dependencies=[Depends(require_crm_pipelines), *ADMIN_ONLY],
)
async def set_pipeline_stage_values(
    pipeline_id: str, status_id: str, payload: StageValuesIn, request: Request,
    user: UserContext = Depends(get_current_user),
) -> PipelineModel:
    """Set the win chance or the warning days of one stage in ONE deal
    pipeline (CRM-M3b, D166). Each other pipeline that holds the stage keeps
    its own values. Native mode and an admin only, behind
    ``CRM_PIPELINES_ORGS``. Mirror mode answers 409, because Zoho owns the
    stages. A lead pipeline, or a stage that the pipeline does not list,
    answers 404. A lock that another write holds answers 409, and the words
    say to try again."""
    wanted = checked_uuid(pipeline_id, "pipeline_id")
    stage = checked_uuid(status_id, "status_id")
    values = payload.model_dump(exclude_unset=True)
    via = await change_log.via_of(request)
    try:
        async with _tenant_session() as db:
            await set_stage_values_in(db, user, wanted, stage, values, via=via)
            return await _one(db, wanted)
    except DBAPIError as exc:
        if not is_transient(exc):
            raise
        raise _refuse(409, "try_again", (
            "Someone changed this pipeline at the same moment, so nothing changed. "
            "Try again."
        )) from exc


async def set_stage_values_in(
    db: Any, user: Any, pipeline_id: str, status_id: str, values: dict[str, Any],
    *, undoes: str | None = None, via: str = change_log.VIA_UI,
) -> bool:
    """The write of ``PATCH /crm/pipelines/{id}/stages/{status_id}``, in the
    caller's transaction. The route and the Undo of the change both call it,
    so an Undo meets every check of the route. It logs one ``pipeline_stage``
    row when a value changes, and returns whether one did.

    The lock order of CRM-M4a: the pipeline create lock, then the membership
    row ``FOR UPDATE``, then the read of the stage row. A type change of the
    stage locks the memberships too, so the clamp of D-CRM-10 reads one state.
    Ids compare as canonical UUIDs (:func:`checked_uuid`)."""
    await lock_pipeline_creates(db)
    try:
        pipeline = await _native_pipeline(db, pipeline_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        raise _refuse(404, "no_pipeline", (
            "There is no deal pipeline with that id. Pick a deal pipeline in CRM "
            "settings. A lead pipeline has no win chance for each status."
        )) from None
    await assert_writable(db, current_tenant(), "crm_pipeline_stages", values)
    member = (await db.execute(
        text(_MEMBERSHIP_SQL), {"p": pipeline_id, "s": status_id},
    )).first()
    stage = await load_row(db, "crm_deal_statuses", status_id)
    if stage is None:
        raise _refuse(404, "no_stage", (
            "There is no deal stage with that id. Pick a stage of the pipeline."
        ))
    if member is None:
        raise _refuse(404, "stage_not_in_pipeline", _NOT_IN_PIPELINE.format(
            stage=quoted(stage.name), pipeline=quoted(pipeline.name)))
    _check_stage_values(values, stage)
    # "before" holds the value that the pipeline showed: its own, else the stage row's.
    shown = {
        c: (getattr(member, c, None) if getattr(member, c, None) is not None
            else getattr(stage, c, None))
        for c in values
    }
    before, after = change_log.diff(shown, dict(values))
    if not after:
        return False
    await db.execute(
        text(
            "UPDATE crm_pipeline_stages SET "
            + ", ".join(f"{c} = :{c}" for c in after)
            + " WHERE pipeline_id = CAST(:p AS uuid) AND status_id = CAST(:s AS uuid)"
        ),
        {**{c: values[c] for c in after}, "p": pipeline_id, "s": status_id},
    )
    words = ", ".join(_VALUE_WORDS[c] for c in after)
    which = {"status_id": status_id, "stage": stage.name}
    await log_change(db, user, Change(
        area="pipeline_stage", action=change_log.CHANGE,
        summary=(
            f"Changed the stage {quoted(stage.name)} in the pipeline "
            f"{quoted(pipeline.name)}: {words}"
        ),
        ladder=change_log.TEAM, entity_id=str(pipeline_id),
        before={**which, **before}, after={**which, **after},
    ), undoes=undoes, via=via)
    return True


# ── The Undo of a change (CRM-M1) ───────────────────────────────────────────


@change_log.undoes("pipeline")
async def _undo_pipeline(db: Any, user: Any, logged: Logged) -> None:
    """The Undo of a ``pipeline`` row. The pipeline row is locked first, so
    each check holds until the write.

    A create (CRM-M3a, CRM-M4a): the pipeline goes through
    :func:`delete_pipeline_in` while it still has the name, the kind and the
    stages of the create, in order, and holds no record. Each stage that the
    create made goes too, through ``admin.delete_status_in``, while no record
    and no other pipeline of the kind holds it. A new stage that holds a
    record refuses the Undo, by name. A reused stage stays. The kind comes
    from "after" (a row from before CRM-M4a names none, and is a deal one),
    so the new stages are locked before the pipeline, in the order of a
    record write (:func:`_lock_new_stages`). A rename: the old name goes back
    through :func:`rename_pipeline_in`."""
    after = logged.after or {}
    stages = [s for s in after.get("stages") or [] if isinstance(s, dict)]
    creates = logged.action == change_log.CREATE
    kind = PIPELINE_KINDS.get(str(after.get("kind") or "deal"), DEAL) if creates else DEAL
    if creates:
        # CRM-M4a repair 1: the create lock of the org first, as a create takes
        # it before it reads a copied pipeline and its stages. A copy and this
        # Undo then never wait on each other in a cycle.
        await lock_pipeline_creates(db)
    new_rows = await _lock_new_stages(db, stages, kind) if creates else []
    row = await load_row(db, "crm_pipelines", str(logged.entity_id or ""), lock=True)
    if row is None:
        raise change_log.gone(logged.area)
    if creates:
        made = [str(s.get("id")) for s in stages]
        current = await _stage_ids(db, str(row.id), lock=True, kind=kind)
        if (row.name != after.get("name") or bool(row.is_default) or current != made
                or str(getattr(row, "kind", "deal") or "deal") != kind.status_kind):
            raise change_log.changed_since()
        doomed = await _new_stages_to_remove(db, str(row.id), new_rows, kind)
        await delete_pipeline_in(db, user, row, undoes=logged.id, kind=kind)
        for stage in doomed:
            await _remove_new_stage(db, user, stage, str(row.name), kind)
        return
    if not change_log.same_values({"name": row.name}, logged.after):
        raise change_log.changed_since()
    await rename_pipeline_in(
        db, user, str(row.id), str((logged.before or {}).get("name") or ""),
        undoes=logged.id,
    )


#: The keys of an added stage that its Undo checks. The win chance and the
#: warning days are there since CRM-M3b, so an edit after the add must be
#: undone first. A row from before CRM-M3b names neither, and is checked on
#: the place alone.
_ADDED_KEYS: tuple[str, ...] = ("status_id", "position", *MEMBERSHIP_COLUMNS)


@change_log.undoes("pipeline_stage")
async def _undo_stage(db: Any, user: Any, logged: Logged) -> None:
    """An added stage goes out. A removed stage goes back at its old place,
    with its win chance and its warning days. A changed value goes back
    (CRM-M3b). The pipeline row and the place of the stage are locked first."""
    pipeline_id = str(logged.entity_id or "")
    if await load_row(db, "crm_pipelines", pipeline_id, lock=True) is None:
        raise change_log.gone(logged.area)
    if logged.action == change_log.CREATE:
        after = logged.after or {}
        status_id = str(after.get("status_id") or "")
        place = (await db.execute(
            text(_MEMBERSHIP_SQL), {"p": pipeline_id, "s": status_id},
        )).first()
        # The stage must still sit in the pipeline at the place of this
        # change. The name of the stage in "after" is for the reader only.
        if place is None or not change_log.same_values(
            {"status_id": status_id, "position": int(place.position or 0),
             **{c: getattr(place, c, None) for c in MEMBERSHIP_COLUMNS}},
            {k: after[k] for k in _ADDED_KEYS if k in after},
        ):
            raise change_log.changed_since()
        if not await remove_stage_in(db, user, pipeline_id, status_id, undoes=logged.id):
            raise change_log.changed_since()
        return
    if logged.action == change_log.CHANGE:
        await _undo_stage_values(db, user, pipeline_id, logged)
        return
    before = logged.before or {}
    status_id = str(before.get("status_id") or "")
    # Locked, as each other read of this Undo (fix round 2).
    if await load_row(db, "crm_deal_statuses", status_id, lock=True) is None:
        raise change_log.gone("deal_stage")
    if (await db.execute(text(_PLACE_SQL), {"p": pipeline_id, "s": status_id})).first():
        raise change_log.changed_since()
    # CRM-M3b: the stored values go back. A row from before CRM-M3b names
    # none, and the stage copies the values of its stage row.
    restore = (
        {c: before[c] for c in MEMBERSHIP_COLUMNS}
        if all(c in before for c in MEMBERSHIP_COLUMNS) else None
    )
    # False: another request put the stage back between the check and the
    # INSERT. That is a change since, and not an Undo with no row.
    if not await add_stage_in(
        db, user, pipeline_id, status_id, int(before.get("position") or 0),
        undoes=logged.id, restore=restore,
    ):
        raise change_log.changed_since()


async def _undo_stage_values(db: Any, user: Any, pipeline_id: str, logged: Logged) -> None:
    """The Undo of a win chance or warning days edit in one pipeline (CRM-M3b).

    The membership must still show the values of "after", else 409
    ``changed_since``. A membership that is gone answers 409 ``gone`` with
    words, and the change stays open: the Undo of the take-out puts the stage
    back with these values, and then this Undo works. It goes back through
    :func:`set_stage_values_in`, the helper of the route. A refusal of that
    helper means that the stage changed since (a new type, or a limit on the
    stage row), so it answers 409 ``changed_since`` too."""
    before, after = logged.before or {}, logged.after or {}
    status_id = str(after.get("status_id") or before.get("status_id") or "")
    columns = [c for c in MEMBERSHIP_COLUMNS if c in after]
    # The stage row before its membership: the order of a stage edit
    # (``admin.patch_status_in``) and of its Undo, so none waits in a cycle.
    stage = await load_row(db, "crm_deal_statuses", status_id, lock=True)
    member = (await db.execute(
        text(_MEMBERSHIP_SQL), {"p": pipeline_id, "s": status_id},
    )).first()
    if member is None or stage is None:
        name = quoted(after.get("stage") or before.get("stage") or "a stage")
        raise _refuse(409, "gone", (
            f"The stage {name} is not in this pipeline now, so this change cannot "
            "be undone. Undo the change that took it out first, and then undo this."
        ))
    shown = {
        c: (getattr(member, c, None) if getattr(member, c, None) is not None
            else getattr(stage, c, None))
        for c in columns
    }
    if not change_log.same_values(shown, {c: after[c] for c in columns}):
        raise change_log.changed_since()
    try:
        done = await set_stage_values_in(
            db, user, pipeline_id, status_id, {c: before.get(c) for c in columns},
            undoes=logged.id,
        )
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        raise change_log.changed_since() from None
    if not done:
        raise change_log.changed_since()


@router.get(
    "/source-profile", response_model=SourceProfileModel,
    dependencies=[Depends(require_crm_mirror), *ADMIN_ONLY],
)
async def get_source_profile(
    user: UserContext = Depends(get_current_user),
) -> SourceProfileModel:
    """The latest source profile of the organization (CRM-Q1).

    404 ``no_source_profile`` until the sync has run its first discovery. The
    wire carries no record data and no token.
    """
    from gateway.crm_sync import profiles

    if not user.organization_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: you are in no organization.",
        )
    async with _tenant_session() as db:
        latest = await profiles.latest(db, str(user.organization_id))
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "no_source_profile",
                "message": "The sync has not read the shape of the account yet.",
            },
        )
    profile = latest.profile
    org = profile.get("org") or {}
    return SourceProfileModel(
        version=latest.version,
        fetched_at=latest.fetched_at,
        edition=org.get("edition"),
        currency=org.get("currency"),
        modules=[
            ProfileModuleModel(
                api_name=m["api_name"],
                label=m.get("label") or m["api_name"],
                custom=bool(m.get("custom")),
                api_supported=bool(m.get("api_supported")),
                mirrored=bool(m.get("mirrored")),
            )
            for m in profile.get("modules") or []
            if isinstance(m, dict) and m.get("api_name")
        ],
        pipelines=len(profile.get("pipelines") or []),
    )
