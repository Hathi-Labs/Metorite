"""CRM · re-run the AI analysis on demand (WS-53 CRM-Q32-10).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.14.

    POST  /crm/analysis/rerun    (admin) start the analysis now, 202
    GET   /crm/analysis/status   (admin) the last analysis of this process, and
                                 whether a step can run for the org

**What it runs.** The same steps as the end of a sync run, through
``crm_sync/scheduler.start_analysis``: the mapping pipeline (the catalogue,
the roles, the targets and the stages), the note labels, and then the rebuild
of the open questions. No second pipeline. Each decide cap, each cost limit
and each mode still binds, and the pipeline never replaces a part that an
admin answered.

**One at a time.** A second press while an analysis runs returns the one
that runs, with ``started: false``. A step that a sync run started is waited
for, and not started twice.

**The gates.** The guards of the Field map: the CRM switch, ``CRM_MIRROR``
(else 404), ``admin:access:manage`` (else 403) and Mirror mode (else 409
``not_mirror``). When no step can run for the org (each ``crm.map_*`` mode
and the label mode are ``off``, and the org is not on
``CRM_QUESTIONS_ORGS``), it answers 404 ``analysis_off``.

The organization comes from the session (``UserContext``), never from the
request (R5, R11).

Fences: ``tests/unit/test_crm_analysis_rerun.py`` and
``tests/unit/test_crm_analysis_rerun_r8.py`` (R8).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.field_rules import _GUARDS, _organization, _require_mirror
from gateway.routes.crm.flags import questions_allowed
from pydantic import BaseModel

if TYPE_CHECKING:
    # Imported in the handlers at run time: ``gateway.crm_sync`` imports
    # ``routes.crm.core``, so a top-level import would be a cycle.
    from gateway.crm_sync.scheduler import Analysis

ANALYSIS_OFF = "analysis_off"


class AnalysisModel(BaseModel):
    """One analysis. States and times only, never a field or a value."""

    id: str
    #: ``running`` or ``finished``.
    state: str
    started_at: datetime
    finished_at: datetime | None = None
    #: ``mapping``, ``labels`` and ``questions``: ``running``, ``done`` or ``skipped``.
    steps: dict[str, str]
    #: ``done``, ``limit`` or ``failed``, once finished.
    outcome: str | None = None


class AnalysisStartedModel(AnalysisModel):
    #: False when an analysis already ran, and this is that one.
    started: bool


class AnalysisStatusModel(BaseModel):
    run: AnalysisModel | None = None
    #: False when no step can run for the org, so the screen hides the button.
    allowed: bool = True


def _model(run: Analysis) -> dict[str, Any]:
    return {
        "id": run.id,
        "state": "running" if run.running else "finished",
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "steps": dict(run.steps),
        "outcome": run.outcome,
    }


def analysis_allowed(org: str) -> bool:
    """Whether a step of the analysis can run for *org*. It reads the
    settings and nothing else."""
    from gateway.crm_sync import map_pipeline, note_labels

    return (map_pipeline.enabled_for(org) or questions_allowed(org)
            or note_labels.enabled_for(org))


def _off() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": ANALYSIS_OFF,
        "message": "The AI analysis is not on for this organization.",
    })


@router.post("/analysis/rerun", response_model=AnalysisStartedModel, status_code=202,
             dependencies=_GUARDS)
async def rerun_analysis(user: UserContext = Depends(get_current_user)) -> AnalysisStartedModel:
    """Start the analysis of the caller's org now. It answers before the
    analysis ends."""
    from gateway.crm_sync import scheduler

    org = _organization(user)
    async with _tenant_session() as db:
        await _require_mirror(db, org)
    if not analysis_allowed(org):
        raise _off()
    run, started = scheduler.start_analysis(org)
    return AnalysisStartedModel(**_model(run), started=started)


@router.get("/analysis/status", response_model=AnalysisStatusModel, dependencies=_GUARDS)
async def analysis_status(user: UserContext = Depends(get_current_user)) -> AnalysisStatusModel:
    """The last analysis of the caller's org in this gateway process, or
    ``run: null``. It reads only."""
    from gateway.crm_sync import scheduler

    org = _organization(user)
    run = scheduler.analysis_of(org)
    return AnalysisStatusModel(run=AnalysisModel(**_model(run)) if run is not None else None,
                               allowed=analysis_allowed(org))


__all__ = ["ANALYSIS_OFF", "analysis_allowed"]
