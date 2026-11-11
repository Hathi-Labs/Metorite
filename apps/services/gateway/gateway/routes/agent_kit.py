"""``/agent-kit`` — the download of the agent kit (KIT-4a, ``orchestration_playbook.md`` §6a).

Two read-only routes for a signed-in member:

* ``GET /agent-kit`` — ``{kit_version, clients}``, for the Integrations control (KIT-4b).
* ``GET /agent-kit/{client}.zip`` — the kit of one client, built from
  ``packages/acb_playbook`` by :func:`acb_playbook.build_kit_zip`.

Both are dark behind ``PLAYBOOK_ORGS``: 404 (not 403) while the BOUND tenant is
not listed, with a fixed body that echoes no input. An unknown client is the
same 404. The tenant comes from the session (R5), never from the request. The
zip holds no token and no organization data. The routes write nothing, so they
need no database session and no audit row.

Fence: ``tests/unit/test_kit_download_route.py`` and ``tests/unit/test_kit_zip.py``.
"""

from __future__ import annotations

from typing import Any

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_common import get_settings
from acb_common.db import current_tenant
from acb_playbook import CLIENTS, build_kit_zip, kit_version
from fastapi import APIRouter, Depends, HTTPException, Response

NOT_FOUND = "The agent kit is not available."


async def require_kit(user: UserContext = Depends(get_current_user)) -> None:
    """401 with no session. 404 while ``playbook_orgs`` does not hold the bound tenant."""
    if not getattr(user, "email", None):
        raise HTTPException(status_code=401, detail="Sign in to download the agent kit.")
    org = current_tenant() or getattr(user, "organization_id", None)
    if not agent_oauth.playbook_allowed(org):
        raise HTTPException(status_code=404, detail=NOT_FOUND)


router = APIRouter(prefix="/agent-kit", tags=["agent-kit"], dependencies=[Depends(require_kit)])


@router.get("")
async def kit_index() -> dict[str, Any]:
    """The kit version and the clients that have a zip."""
    return {"kit_version": kit_version(), "clients": list(CLIENTS)}


@router.get("/{client}.zip")
async def kit_download(client: str) -> Response:
    """The zip of one client. 404 for an unknown client."""
    if client not in CLIENTS:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    body = build_kit_zip(client, get_settings().agent_resource)
    return Response(
        content=body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="metorite-agent-kit-{client}-v{kit_version()}.zip"',
            "Cache-Control": "no-store",
        },
    )
