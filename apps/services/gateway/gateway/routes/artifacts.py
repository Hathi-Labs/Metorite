"""The artifact registry routes (WS-60 AM-1).

Spec: ``project-docs/specs/artifact_management.md`` §8 (the API) and §11
AM-1.

    GET /artifacts               → {rows, limit, offset}
    GET /artifacts/{id}          → the row
    GET /artifacts/{id}/content  → the bytes (AM-10)

Each reads ``artifacts`` through ``acb_memory.artifacts``, under the tenant
the request bound and the member's own ``can_read`` (rules 1 and 2 of §5.2
in this slice). The content route reads the bytes from ``agent_blob`` by the
row's store key. It is the ONE reader of a file that a chat delete kept
(``outputs/.kept/``), which every workspace route refuses. With
``ARTIFACT_REGISTRY_ENABLED`` off, each answers 404, so the app stays dark.

The router is gated on ``feature:artifacts`` by construction
(``require_feature_router``), so a route added here later is gated too.
"""
from __future__ import annotations

import mimetypes
import uuid
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router
from acb_memory import artifacts as registry
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

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
    try:
        parsed = str(uuid.UUID(artifact_id))
    except ValueError:
        raise _off() from None
    row = await registry.get_artifact(parsed, _user.email or "")
    if row is None:
        raise _off()
    return row


#: The characters a quoted header file name cannot hold.
_HEADER_UNSAFE = frozenset({'"', "\\", "\r", "\n"})


def _header_name(name: str) -> str:
    """*name* as a ``Content-Disposition`` file name: no quote, no line break."""
    return "".join(c for c in name if c not in _HEADER_UNSAFE) or "file"


@router.get("/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str,
    _user: UserContext = Depends(get_current_user),
) -> Response:
    """The bytes of one artifact (AM-10 item 6). AM-2's viewer reads it too.

    The row comes from the tenant session and ``can_read``, as
    ``GET /artifacts/{id}`` does. So a row the member may not read answers
    404, never 403. The bytes come from ``agent_blob`` by the row's agent,
    instance and path, under the caller's tenant from the authenticated
    identity, never from the request. The size cap and the
    ``Content-Disposition`` are those of ``GET /agent/artifacts/file``.
    """
    from acb_memory import get_file
    from gateway.routes.workspace import _MAX_FILE_BYTES

    if not registry.enabled():
        raise _off()
    try:
        parsed = str(uuid.UUID(artifact_id))
    except ValueError:
        raise _off() from None
    row = await registry.get_artifact(parsed, _user.email or "")
    if row is None:
        raise _off()
    too_big = HTTPException(
        status_code=413,
        detail=f"File too large. Maximum is {_MAX_FILE_BYTES} bytes.",
    )
    if int(row.get("size") or 0) > _MAX_FILE_BYTES:
        raise too_big
    data = await get_file(
        str(row["agent_name"]), str(row["path"]), instance=str(row["instance"]),
        organization_id=_user.organization_id,
    )
    if data is None:
        raise _off()
    if len(data) > _MAX_FILE_BYTES:
        raise too_big
    name = _header_name(str(row["path"]).rsplit("/", 1)[-1])
    mime, _ = mimetypes.guess_type(name)
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )
