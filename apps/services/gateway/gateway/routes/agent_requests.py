"""``/agent-access/.../requests`` — the routes of an app request (MCP-P1b-1).

Spec ``project-docs/specs/external_agent_access.md`` §19.5 (path 2, "The
member routes") and AD-16, and row MCP-P1b-1 of §19.13.

    POST /agent-access/apps/{app}/requests          → {request_id, run_id, state, expires_at, created}
    GET  /agent-access/requests                      → {rows, more}
    GET  /agent-access/requests/{request_id}         → the request
    POST /agent-access/requests/{request_id}/decision → {request_id, run_id, decision, state, created}

The store is ``gateway/app_requests.py``. These routes only check who asks.

## The rules

1. **The ask is an agent's.** ``POST .../apps/{app}/requests`` serves a
   signed agent only (``UserContext.agent``), and 404 for anyone else. It
   needs the door flag, the feature of the app, the scope ``<app>:write``
   and the writes flag. The op must be a door op of class B or C that is not
   a proposal. The class comes from the manifest, never from the body.
   ``build_call`` builds the call. Nothing runs. In MCP-P1b-1 no pair here is
   in ``MCP_ROUTES``, so branch 0 refuses a signed call to it: the edge flow
   is MCP-P1b-2.
2. **The decision is the member's.** ``POST .../decision`` refuses a signed
   agent with 403, and it is in no door set and not in ``MCP_ROUTES``
   (``test_decision_route_not_in_door_sets``). Only the member of the request
   decides. A request of another member is 404.
3. **The list is the member's.** ``GET /agent-access/requests`` serves the
   member only. An agent gets 404.
4. **One read for both.** ``GET .../requests/{request_id}`` gives the member
   their request. An agent reads only a request of its own connection
   (AD-16): another connection is 404, with no oracle.
5. **The org and the member come from the session (R5, R11).** Each body
   forbids extra keys, so no body names an org, a member or a connection.
6. **Dark.** Each route answers 404 while ``APP_REQUESTS_ORGS`` does not
   hold the caller's org (default empty, an owner flip), and while
   ``agent_access_enabled`` is off. The ask also answers 404 while
   ``agent_door_apps`` names no app.

Fences: ``tests/unit/test_app_requests_r8.py`` (R8) and
``tests/unit/test_mcp_edge_coverage.py::test_decision_route_not_in_door_sets``.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_auth import app_requests as store
from acb_common import app_manifests
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from gateway import app_requests
from gateway.db import current_tenant, tenant_session
from gateway.routes.agent_apps import _require_door
from pydantic import BaseModel, ConfigDict, Field

#: The shape of a request id: the id of its elicitation.
_ID = r"^[0-9a-fA-F-]{36}$"


async def _require_access(user: UserContext = Depends(get_current_user)) -> None:
    """404 while agent access is off, or while ``APP_REQUESTS_ORGS`` does not
    hold the caller's org (fix round 1, review P2-1). Agent access is on in
    production, so the org flag is the dark switch."""
    if not agent_oauth.enabled() or not app_requests.requests_allowed(
            getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/agent-access",
    tags=["agent-access"],
    dependencies=[Depends(_require_access)],
)


class AppRequestIn(BaseModel):
    """The ask of an agent: the op of the app and its args. The class, the
    member, the org and the connection never come from here."""

    model_config = ConfigDict(extra="forbid")

    op: str = Field(min_length=1, max_length=48, pattern=f"^{app_manifests.OP_PATTERN}$")
    args: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=128)


class DecisionIn(BaseModel):
    """The member's answer to one request."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "decline"]
    # The key is stored as it came, and a TEXT column refuses U+0000 (review
    # P3-2), so a key that holds it is a 422 here, never a 500.
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00]+$")


def _org(user: UserContext) -> str:
    """The org of the bound session. A request with none is 404."""
    org = current_tenant() or user.organization_id
    if not org or (user.organization_id and str(org) != str(user.organization_id)):
        raise HTTPException(status_code=404, detail="Not Found")
    return str(org)


def _refuse(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail=code)


@router.post("/apps/{app}/requests", status_code=201, dependencies=[Depends(_require_door)])
async def open_app_request(
    payload: AppRequestIn,
    response: Response,
    app: str = Path(..., pattern=f"^{app_manifests.APP_ID_PATTERN}$"),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """An agent asks the member to approve one door call (§19.5 steps 2 to 6).

    201 with the new request, or 200 with the first request of this
    ``idempotency_key``. Nothing runs until the member approves and the
    caller spends the request.
    """
    agent = user.agent
    if agent is None:
        raise HTTPException(status_code=404, detail="Not Found")
    found = app_manifests.door_app(app)
    if (found is None or not agent_oauth.door_serves(found.id)
            or not user.has_permission(f"feature:{found.feature}")):
        raise HTTPException(status_code=404, detail="Not Found")
    door = app_manifests.door_ops(found).get(payload.op)
    if door is None:
        raise _refuse(422, "unknown_op")
    if door.kind != "write":
        # A read goes to `app_read`, and a proposal runs at once (AD-8).
        raise _refuse(422, "not_an_approval_op")
    if not agent_oauth.scope_holds(agent.scopes, found.write_scope):
        raise _refuse(403, "insufficient_scope")
    if not agent_oauth.writes_enabled():
        raise _refuse(403, agent_oauth.DOOR_WRITES_OFF_CODE)
    if door.route.sends:
        # AD-19. A send waits for `AGENT_DOOR_SENDS` of MCP-P2b, so it fails
        # closed here until then.
        raise _refuse(403, "sends_off")
    try:
        call = app_manifests.build_call(found, payload.op, payload.args)
    except app_manifests.BadCall as bad:
        raise HTTPException(status_code=422, detail={"error": "bad_args", "name": bad.name}) from None
    body = json.loads(call.body) if call.body else None
    org = _org(user)
    async with tenant_session() as db:
        opened = await app_requests.open_request(
            db,
            org=org,
            member=user.email,
            source="mcp",
            app=found.id,
            subject="",
            op=payload.op,
            cls=door.route.cls,
            call={"method": call.method, "template": call.template, "path": call.path,
                  "query": list(call.query), "body": body, "if_match": call.if_match},
            idempotency_key=payload.idempotency_key,
            connection_id=str(agent.connection_id),
            via=str(agent.actor_via),
            title=door.route.title,
            app_name=found.name,
        )
    if not opened.created:
        response.status_code = 200
    return {"request_id": opened.request_id, "run_id": opened.run_id, "state": opened.state,
            "expires_at": opened.expires_at, "created": opened.created}


@router.get("/requests")
async def list_app_requests(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The requests that wait for the member, newest first (rule 3)."""
    if user.agent is not None:
        raise HTTPException(status_code=404, detail="Not Found")
    org = _org(user)
    async with tenant_session() as db:
        found = await app_requests.list_open(db, org=org, member=user.email)
    return {"rows": found.rows, "more": found.more}


@router.get("/requests/{request_id}")
async def read_app_request(
    request_id: str = Path(..., pattern=_ID),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """One request of the member, or of the agent's own connection (rule 4)."""
    binding = None
    if user.agent is not None:
        binding = store.mcp_binding(str(user.agent.connection_id))
    org = _org(user)
    async with tenant_session() as db:
        found = await app_requests.read_request(
            db, org=org, member=user.email, request_id=request_id, binding=binding)
    if found is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return found


@router.post("/requests/{request_id}/decision")
async def decide_app_request(
    payload: DecisionIn,
    request_id: str = Path(..., pattern=_ID),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The member approves or declines one request (rule 2). An approve runs
    nothing: the caller spends the request later."""
    if user.agent is not None:
        raise HTTPException(status_code=403, detail="Only the member decides a request.")
    org = _org(user)
    async with tenant_session() as db:
        decided = await app_requests.decide(
            db, org=org, member=user.email, request_id=request_id,
            decision=payload.decision, idempotency_key=payload.idempotency_key)
    return {"request_id": decided.request_id, "run_id": decided.run_id,
            "decision": decided.decision, "state": decided.state, "created": decided.created}


__all__ = ["AppRequestIn", "DecisionIn", "router"]
