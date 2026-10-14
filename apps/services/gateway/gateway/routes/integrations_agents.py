"""``/integrations/agent-connections`` — every agent connection of the org (WS-55 EA-4b).

Spec ``project-docs/specs/external_agent_access.md`` §5.4 and §15 EA-4b ·
decision D-EA-9 (owner, 2026-10-14) · ``integrations_console.md`` §3.5.

    GET    /integrations/agent-connections
    DELETE /integrations/agent-connections/{connection_id}

The "All agent connections" table of the AI agents view in the Integrations
app reads and revokes through these two routes. A member's own pair is
``GET`` and ``DELETE /agent-access/connections`` in ``routes/agent_access.py``.

## The rules that are easy to get wrong

1. ⚠️ **Two gates, both at the router.** ``feature:integrations`` AND
   ``integrations:manage``. A member who holds the feature and not the
   permission gets 403, as a member with neither does. A new route on this
   router is gated by construction.
2. ⚠️ **The organization is the bound tenant (R11).** The list is every live
   row of that organization and of no other. A revoke matches the
   organization, so an id of another organization is 404 and changes nothing.
3. **The flag.** While ``agent_access_enabled`` is off, each route answers
   404, as each route of ``/agent-access`` does.
4. **No allowlist check.** As the member's pair, these routes do not read
   ``agent_access_orgs``: a revoke only removes access.
5. **One audit row per revoke**, through ``agent_access.audit_revoke`` with
   ``admin=True``. Not through ``integrations.audit_integration``, which is
   the WS-54 helper of the outbound console.
6. **No answer holds a token, a hash or a client id.** The row shape is
   ``agent_access.AgentConnectionOut``.

Fence: ``tests/unit/test_agent_connections_r8.py`` and
``tests/unit/test_org_access_enforcement.py`` (``GATED_ROUTERS``).
"""

from __future__ import annotations

from uuid import UUID

from acb_auth import (
    UserContext,
    agent_oauth,
    agent_tokens,
    get_current_user,
    require_feature_router,
    require_permission,
)
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from gateway.db import current_tenant
from gateway.routes.agent_access import (
    AgentConnectionsOut,
    audit_revoke,
    connection_out,
)

#: The permission of rule 1. The same string as ``integrations.MANAGE_PERMISSION``.
MANAGE_PERMISSION = "integrations:manage"

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def _require_enabled() -> None:
    """404 while ``agent_access_enabled`` is off (rule 3)."""
    if not agent_oauth.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[
        Depends(_require_enabled),
        require_feature_router("integrations"),
        require_permission(MANAGE_PERMISSION),
    ],
)


def _org() -> str:
    """The bound tenant (rule 2). None is a 403, never a guess."""
    org = current_tenant()
    if org is None:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(org)


@router.get("/agent-connections", response_model=AgentConnectionsOut)
async def list_org_agent_connections(
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Every live agent connection of the organization, newest first."""
    del user  # the router's gates read it. The organization is the bound tenant.
    rows = await agent_tokens.list_connections(_org())
    body = AgentConnectionsOut(connections=[connection_out(r) for r in rows])
    return JSONResponse(body.model_dump(), headers=_NO_STORE)


@router.delete("/agent-connections/{connection_id}", status_code=204)
async def revoke_org_agent_connection(
    connection_id: UUID,
    user: UserContext = Depends(get_current_user),
) -> None:
    """Revoke any member's agent connection in the organization (rule 2)."""
    org = _org()
    revoked = await agent_tokens.revoke_connection_scoped(org, str(connection_id))
    if not revoked:
        raise HTTPException(status_code=404, detail="No such agent connection.")
    audit_revoke(user.email or "", org, str(connection_id), admin=True)
