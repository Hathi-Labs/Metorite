"""CRM · pipelines — the deal pipelines and the source profile (WS-53 CRM-Q1, CRM-Q2).

Spec: ``project-docs/specs/crm_platform.md`` §18.3 and §18.5.

    GET    /crm/pipelines                              the deal pipelines, with stages
    PATCH  /crm/pipelines/{id}                         rename (Native mode, admin)
    POST   /crm/pipelines/{id}/stages                  add a stage (Native mode, admin)
    DELETE /crm/pipelines/{id}/stages/{status_id}      remove a stage (Native mode, admin)
    GET    /crm/source-profile                         the shape of the customer's Zoho

**A pipeline is not an object in Zoho.** It is an ordered list of deal stages.
A stage (``crm_deal_statuses``) stays one global list, and
``crm_pipeline_stages`` says which stages sit in which pipeline, in what order.
So the same stage can sit in two pipelines, and the stage keeps one probability.

**Who may do what.** ``GET /crm/pipelines`` is open to each member the CRM access
rule admits, because every board needs it. The three writes need
``admin:access:manage``. In Mirror mode each write answers 409
``mirror_mode_settings`` through the one write guard (``assert_writable``),
because Zoho owns its pipelines. ``GET /crm/source-profile`` needs the admin
floor and ``CRM_MIRROR``. It returns the edition, the currency, the modules and
a pipeline count. It never returns a record, a token or a company name.

The organization comes from the bound tenant (R5), never from the request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from fastapi import Depends, HTTPException
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import (
    _tenant_session,
    assert_writable,
    checked_uuid,
    current_tenant,
    provider_link_context,
    require_row,
    router,
    update_row,
)
from gateway.routes.crm.flags import require_crm_mirror
from pydantic import BaseModel
from sqlalchemy import text

ADMIN_ONLY = [require_permission(ADMIN_PERMISSION)]

#: The longest name a pipeline may have.
MAX_NAME = 120


class PipelineStageModel(BaseModel):
    id: str
    name: str
    position: int = 0
    type: str = "open"
    probability: int | None = None
    forecast_category: str | None = None


class PipelineModel(BaseModel):
    id: str
    name: str
    is_default: bool = False
    position: int = 0
    #: A link to the Deals tab in the provider's app. ``None`` for a native
    #: pipeline and while the org domain is not known.
    ext_url: str | None = None
    stages: list[PipelineStageModel] = []


class PipelineIn(BaseModel):
    name: str | None = None


class StageIn(BaseModel):
    status_id: str
    position: int | None = None


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


_PIPELINES_SQL = (
    "SELECT id, name, position, is_default, ext_source, ext_id "
    "FROM crm_pipelines ORDER BY is_default DESC, position, created_at, id"
)

_STAGES_SQL = (
    "SELECT ps.pipeline_id, ps.position AS stage_position, s.id, s.name, s.type, "
    "s.probability, s.forecast_category "
    "FROM crm_pipeline_stages ps JOIN crm_deal_statuses s ON s.id = ps.status_id "
    "ORDER BY ps.pipeline_id, ps.position, s.name"
)


def _stage(row: Any) -> PipelineStageModel:
    return PipelineStageModel(
        id=str(row.id),
        name=row.name,
        position=int(getattr(row, "stage_position", 0) or 0),
        type=getattr(row, "type", "open") or "open",
        probability=getattr(row, "probability", None),
        forecast_category=getattr(row, "forecast_category", None),
    )


async def read_pipelines(db: Any, only: str | None = None) -> list[PipelineModel]:
    """The pipelines of the bound tenant, each with its stages in order."""
    from gateway.crm_sources.zoho import links

    pipelines = (await db.execute(text(_PIPELINES_SQL), {})).fetchall()
    stages = (await db.execute(text(_STAGES_SQL), {})).fetchall()
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
        ))
    return out


async def _one(db: Any, pipeline_id: str) -> PipelineModel:
    found = await read_pipelines(db, only=pipeline_id)
    if not found:
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    return found[0]


@router.get("/pipelines", response_model=list[PipelineModel])
async def list_pipelines(
    user: UserContext = Depends(get_current_user),
) -> list[PipelineModel]:
    """The deal pipelines of the organization, default first."""
    async with _tenant_session() as db:
        return await read_pipelines(db)


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
        await require_row(db, "crm_pipelines", wanted, "Pipeline")
        await update_row(db, "crm_pipelines", wanted, {"name": name}, touch=False)
        return await _one(db, wanted)


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
        await require_row(db, "crm_pipelines", wanted, "Pipeline")
        await require_row(db, "crm_deal_statuses", status_id, "Status")
        await assert_writable(db, current_tenant(), "crm_pipeline_stages")
        position = payload.position
        if position is None:
            position = int((await db.execute(
                text(
                    "SELECT COALESCE(max(position), 0) + 1 FROM crm_pipeline_stages "
                    "WHERE pipeline_id = CAST(:p AS uuid)"
                ),
                {"p": wanted},
            )).scalar() or 1)
        await db.execute(
            text(
                "INSERT INTO crm_pipeline_stages (pipeline_id, status_id, position) "
                "VALUES (CAST(:p AS uuid), CAST(:s AS uuid), :pos) "
                "ON CONFLICT (pipeline_id, status_id) DO NOTHING"
            ),
            {"p": wanted, "s": status_id, "pos": position},
        )
        return await _one(db, wanted)


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
        await require_row(db, "crm_pipelines", wanted, "Pipeline")
        await assert_writable(db, current_tenant(), "crm_pipeline_stages")
        in_use = int((await db.execute(
            text(
                "SELECT count(*) FROM crm_deals WHERE pipeline_id = CAST(:p AS uuid) "
                "AND status_id = CAST(:s AS uuid)"
            ),
            {"p": wanted, "s": stage},
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
        await db.execute(
            text(
                "DELETE FROM crm_pipeline_stages WHERE pipeline_id = CAST(:p AS uuid) "
                "AND status_id = CAST(:s AS uuid)"
            ),
            {"p": wanted, "s": stage},
        )
        return await _one(db, wanted)


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
