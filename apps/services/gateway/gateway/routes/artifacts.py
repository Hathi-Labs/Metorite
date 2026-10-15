"""The artifact registry routes (WS-60 AM-1).

Spec: ``project-docs/specs/artifact_management.md`` §8 (the API) and §11
AM-1.

    GET /artifacts            → {rows, limit, offset}
    GET /artifacts/{id}       → the row

Both read ``artifacts`` through ``acb_memory.artifacts``, under the tenant
the request bound and the member's own ``can_read`` (rules 1 and 2 of §5.2
in this slice). The bytes keep coming from the workspace routes in
``routes/workspace.py``. With ``ARTIFACT_REGISTRY_ENABLED`` off, both answer
404, so the app stays dark.

The router is gated on ``feature:artifacts`` by construction
(``require_feature_router``), so a route added here later is gated too.
"""
from __future__ import annotations

from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_memory import artifacts as registry
from fastapi import APIRouter, Depends, HTTPException, Query

router = APIRouter(
    prefix="/artifacts",
    tags=["artifacts"],
    dependencies=[require_feature_router("artifacts")],
)


def _off() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


@router.get("")
async def list_artifacts(
    q: str | None = Query(None, max_length=200, description="Words to find"),
    kind: str | None = Query(None, max_length=20),
    made_by: str | None = Query(None, max_length=120),
    via: str | None = Query(None, max_length=80),
    session_id: str | None = Query(None, max_length=200),
    scope: str | None = Query(None, max_length=20),
    status: str = Query("active", max_length=20),
    sort: str = Query("recent", max_length=20),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The artifacts the member can read, newest activity first."""
    if not registry.enabled():
        raise _off()
    rows = await registry.list_artifacts(
        _user.email or "",
        q=q, kind=kind, made_by=made_by, via=via, session_id=session_id,
        scope=scope, status=status, sort=sort, limit=limit, offset=offset,
    )
    return {"rows": rows, "limit": limit, "offset": offset}


@router.get("/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One artifact. A row the member cannot read answers 404, never 403."""
    if not registry.enabled():
        raise _off()
    if len(artifact_id) != 36:
        raise _off()
    row = await registry.get_artifact(artifact_id, _user.email or "")
    if row is None:
        raise _off()
    return row
