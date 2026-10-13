"""MCP OAuth for an org connection: the routes (WS-54 IN-8a, D98).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.3 to §3.6.6.

    POST  /integrations/mcp/{name}/oauth/start
    GET   /integrations/mcp/oauth/callback
    GET   /integrations/mcp/oauth/client-metadata

⚠️ **Dark.** With ``MCP_OAUTH_ENABLED`` off, each route answers 404, before
any other check (:func:`require_mcp_oauth`, the first dependency of the
router). The protocol half is ``gateway.connections.mcp_oauth``.

**Who may do what (§3.5).** The start and the callback need
``integrations:manage``. The client-metadata document is the same for every
caller and names nobody: the BFF serves it at ``/oauth/mcp-client.json`` on
the workbench origin, so an auth server can fetch it with no session. It is
exempt from the feature gate only. The app-wide authentication still holds.

**D98.** The connection acts as the admin who connected it. ``connected_by``
is that admin, and a reconnect by another admin changes it.

**The start**, in the order of §3.6.4: the server row of the caller's org,
discovery, registration, a new PKCE verifier, the signed state (purpose
``integrations.mcp.connect``, bound to the org and the member, ten minutes),
and the pending flow in Redis under the nonce of the state, through
``TenantRedis``. The verifier never goes into the state or a URL. A DCR
``client_secret`` is sealed before it goes to Redis.

**The callback**, in the order of the Zoho callback
(``routes/crm/connection.py``): the state first, then the pending flow, read
and deleted in ONE command, so a replayed state finds nothing. Then ``iss``
(RFC 9207), then a provider ``error``, then the code for tokens through the
guard, then the save on the registry row under its row lock, then the revoke
of the old refresh token, best effort, after the commit.

**Tenancy (R5).** The org and the member come from the server-side
``UserContext``, never from the request. The registry is read and written
inside ``tenant_session(org)``, and each statement names the org. The Redis
key carries the org (``TenantRedis``).

**No secret out.** A code, a token, a verifier and a client secret never
reach a response body, a redirect URL or a log line. A refusal carries a short
code. Fences: ``tests/unit/test_mcp_oauth_*.py``.
"""

from __future__ import annotations

import json
from typing import Any

from acb_auth import UserContext, get_current_user, require_feature_router, require_permission
from acb_common import get_logger, tenant_redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from gateway.connections import mcp as mcp_registry
from gateway.connections import mcp_oauth, store
from gateway.connections.mcp_oauth import OAuthFailed
from gateway.db import tenant_session
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_state,
    verify_state,
)
from pydantic import BaseModel

_log = get_logger("gateway.integrations.mcp_oauth")

MANAGE_PERMISSION = "integrations:manage"

#: The route paths, after the router prefix.
START_ROUTE = "/mcp/{name}/oauth/start"
CALLBACK_ROUTE = "/mcp/oauth/callback"
CLIENT_METADATA_ROUTE = "/mcp/oauth/client-metadata"

#: The Redis namespace and the life of one pending flow.
PENDING_NAMESPACE = "mcp-oauth-pending"
PENDING_TTL_S = 600

#: Where the callback sends the browser. The BFF route passes only these.
RETURN_OK = "/integrations?mcp_connected="
RETURN_FAILED = "/integrations?mcp_connect_error="

#: A failure that the customer's URL caused, and not the provider.
_CLIENT_FAULTS = frozenset({"refused", "insecure_endpoint"})


async def require_mcp_oauth() -> None:
    """404 unless ``MCP_OAUTH_ENABLED`` is on. It reads nothing else."""
    if not mcp_oauth.oauth_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/integrations", tags=["integrations"],
    # ⚠️ The ORDER is load-bearing: the flag runs first, so a dark route is a
    # 404 for every caller, also one that the feature gate would refuse.
    dependencies=[
        Depends(require_mcp_oauth),
        require_feature_router(
            "integrations", exempt=[f"/integrations{CLIENT_METADATA_ROUTE}"]),
    ],
)


class StartOut(BaseModel):
    url: str


def _organization(user: UserContext) -> str:
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(user.organization_id)


def _member(user: UserContext) -> str:
    return (user.email or "").strip().lower()


def _redis() -> tenant_redis.TenantRedis:
    """The tenant-prefixed client. A test replaces it."""
    return tenant_redis.get_tenant_redis()


def _failed(code: str) -> RedirectResponse:
    return RedirectResponse(
        f"{RETURN_FAILED}{mcp_oauth.safe_code(code, 'failed')}", status_code=302)


def _audit(user: UserContext, org: str, name: str, **payload: Any) -> None:
    # Lazy: the module of the old routes is large.
    from gateway.routes.integrations import audit_integration

    audit_integration(user, org, mcp_registry.provider_for(name),
                      action="integration.mcp.oauth_connected", **payload)


# ── The pending flow ────────────────────────────────────────────────────────


def _pending_record(
    plan: mcp_oauth.Plan, client: mcp_oauth.Client, *, name: str, member: str,
    verifier: str,
) -> str:
    sealed = store.seal_value(client.secret) if client.secret else ""
    return json.dumps({
        "name": name, "member": member, "server_url": plan.server_url,
        "resource": plan.resource, "issuer": plan.issuer,
        "iss_required": plan.iss_required, "token_endpoint": plan.token_endpoint,
        "revocation_endpoint": plan.revocation_endpoint,
        "client_id": client.client_id, "registration": client.kind,
        "token_auth": client.token_auth, "scope": plan.scope,
        "verifier": verifier, "client_secret_sealed": sealed,
    }, separators=(",", ":"))


def _pending_from(raw: Any) -> mcp_oauth.Pending | None:
    try:
        data = json.loads(raw)
        secret = ""
        if data.get("client_secret_sealed"):
            secret = store.open_value(str(data["client_secret_sealed"])) or ""
            if not secret:
                return None
        return mcp_oauth.Pending(
            name=str(data["name"]), member=str(data["member"]),
            server_url=str(data["server_url"]), resource=str(data["resource"]),
            issuer=str(data["issuer"]), iss_required=data["iss_required"] is True,
            token_endpoint=str(data["token_endpoint"]),
            revocation_endpoint=(str(data["revocation_endpoint"])
                                 if data.get("revocation_endpoint") else None),
            client_id=str(data["client_id"]), registration=str(data["registration"]),
            token_auth=str(data["token_auth"]),
            scope=str(data["scope"]) if data.get("scope") else None,
            verifier=str(data["verifier"]), client_secret=secret,
        )
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


async def _put_pending(org: str, nonce: str, record: str) -> None:
    with tenant_redis.organization_scope(org):
        client = _redis()
        await client.setex(client.key(PENDING_NAMESPACE, nonce), PENDING_TTL_S, record)


async def _take_pending(org: str, nonce: str) -> Any:
    """Read the pending flow and delete it, in ONE command (GETDEL)."""
    with tenant_redis.organization_scope(org):
        client = _redis()
        return await client.getdel(client.key(PENDING_NAMESPACE, nonce))


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get(CLIENT_METADATA_ROUTE)
async def client_metadata() -> JSONResponse:
    """The CIMD document (§3.6.3). The same for every caller. No secret."""
    return JSONResponse(
        mcp_oauth.client_metadata_document(),
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.post(
    START_ROUTE, response_model=StartOut,
    dependencies=[require_permission(MANAGE_PERMISSION)],
)
async def start_mcp_oauth(
    name: str, user: UserContext = Depends(get_current_user),
) -> StartOut:
    """The consent URL of one MCP server of the caller's org."""
    org = _organization(user)
    member = _member(user)
    if not mcp_registry.NAME_RE.match(name or ""):
        raise HTTPException(status_code=404, detail="no_server")
    server = next((s for s in await mcp_registry.list_servers(org) if s.name == name), None)
    if server is None or not server.url:
        raise HTTPException(status_code=404, detail="no_server")
    try:
        plan = await mcp_oauth.discover(server.url)
        client = await mcp_oauth.register(plan)
    except OAuthFailed as exc:
        _log.warning("mcp.oauth_start_failed", name=name, code=exc.code)
        status = 400 if exc.code in _CLIENT_FAULTS else 502
        raise HTTPException(status_code=status, detail=exc.code) from None
    try:
        state = sign_state(purpose=mcp_oauth.STATE_PURPOSE, org=org, member=member)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_member") from exc
    opened = verify_state(state, purpose=mcp_oauth.STATE_PURPOSE)
    if opened is None:  # a state that this process just signed always opens
        raise HTTPException(status_code=503, detail="signing_unavailable")
    verifier = mcp_oauth.new_verifier()
    try:
        await _put_pending(org, opened["nonce"], _pending_record(
            plan, client, name=name, member=member, verifier=verifier))
    except Exception as exc:  # the class only
        _log.warning("mcp.oauth_pending_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="pending_unavailable") from None
    _log.info("mcp.oauth_started", name=name, registration=client.kind)
    return StartOut(url=mcp_oauth.consent_url(plan, client, state=state, verifier=verifier))


@router.get(CALLBACK_ROUTE, dependencies=[require_permission(MANAGE_PERMISSION)])
async def mcp_oauth_callback(
    code: str | None = Query(default=None, max_length=4096),
    state: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=200),
    iss: str | None = Query(default=None, max_length=2048),
    user: UserContext = Depends(get_current_user),
) -> RedirectResponse:
    """Finish the consent. See the module doc for the order of the checks."""
    org = _organization(user)
    member = _member(user)
    # 1. The state, before any other work. Every refusal is the same 400.
    payload = verify_state(state, purpose=mcp_oauth.STATE_PURPOSE)
    if payload is None or payload["org"] != org or payload["member"] != member:
        raise HTTPException(status_code=400, detail="invalid_state")
    # 2. The pending flow, taken once. A replay finds nothing.
    try:
        raw = await _take_pending(org, payload["nonce"])
    except Exception as exc:  # the class only
        _log.warning("mcp.oauth_pending_unavailable", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="pending_unavailable") from None
    pending = _pending_from(raw) if raw else None
    if pending is None or pending.member != member:
        raise HTTPException(status_code=400, detail="invalid_state")
    # 3. ``iss`` (RFC 9207), before the code goes anywhere. It is checked when
    #    the answer carries it, or when the metadata says that it will.
    iss_due = iss is not None or pending.iss_required
    if iss_due and not (iss and mcp_oauth.issuers_match(iss, pending.issuer)):
        raise HTTPException(status_code=400, detail="invalid_issuer")
    # 4. The admin said no at the provider.
    if error:
        return _failed(error)
    if not code:
        return _failed("no_code")
    # 5. The code for tokens, through the guard.
    try:
        credential = await mcp_oauth.exchange_code(pending, code)
    except OAuthFailed as exc:
        return _failed(exc.code)
    # 6. The save on the row of the server, under its row lock. The row must
    #    still be the server that the consent was for.
    provider = mcp_registry.provider_for(pending.name)
    async with tenant_session(org) as db:
        attached = await store.attach_oauth_credential(
            db, org, provider, mcp_registry.OWNING_APP, credential,
            connected_by=member, scopes=credential.scopes,
            check=lambda row: str(row.provider_meta.get("url") or "") == pending.server_url,
        )
    if attached is None:
        _log.info("mcp.oauth_revoke", why="server_gone",
                  outcome=await mcp_oauth.revoke(credential))
        return _failed("server_gone")
    # 7. A reconnect: revoke the old refresh token, after the commit.
    old = attached.previous
    if old is not None and old.refresh_token and old.refresh_token != credential.refresh_token:
        _log.info("mcp.oauth_revoke", why="reconnect", outcome=await mcp_oauth.revoke(old))
    _audit(user, org, pending.name, registration=pending.registration,
           issuer=pending.issuer)
    _log.info("mcp.oauth_connected", name=pending.name, registration=pending.registration)
    return RedirectResponse(f"{RETURN_OK}{pending.name}", status_code=302)


__all__ = ["require_mcp_oauth", "router"]
