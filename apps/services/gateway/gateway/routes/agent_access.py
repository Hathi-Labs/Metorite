"""``/agent-access`` — the authenticated half of the authorization server (WS-55 EA-2a).

Spec ``project-docs/specs/external_agent_access.md`` §5.5, §7, §8, §10 and §15
EA-2a · decision D97 · ``user_management_contract.md`` R11.

The consent page that EA-4 builds (``https://app.metorite.com/connect/authorize``)
calls these two routes as the signed-in member, through the BFF:

* ``POST /agent-access/authorize/check`` — validate an authorize request and
  return what the consent screen needs: the verified client name and origin,
  the redirect host and the requested scopes.
* ``POST /agent-access/authorize/approve`` — validate it again, create the
  connection, mint a code, and return the redirect URL that carries ``code``,
  ``state`` and ``iss`` (RFC 9207).

The public half (metadata, ``token``, ``revoke``) is the edge unit
``apps/services/agent_edge``. Both halves take their rules from
``acb_auth.agent_oauth`` and their store from ``acb_auth.agent_tokens``.

## The rules that are easy to get wrong

1. ⚠️ **The member and the tenant never come from the body (R11).** The member
   is ``user.email`` of the authenticated ``UserContext``. The organization is
   the tenant that ``get_current_user`` bound. ``create_connection`` takes no
   organization parameter at all. Both request models forbid extra fields, so a
   body that names ``organization_id`` or ``member_email`` is a 422 and writes
   nothing. Fence: ``tests/unit/test_agent_oauth_server.py``.
2. **Behind default-deny.** This router is NOT in ``PUBLIC_ROUTES``. A browser
   reaches it through a Next BFF route that holds the session (gateway
   ``AGENTS.md``, "Authentication posture").
3. **The flag.** While ``agent_access_enabled`` is off, each route answers 404
   for an authenticated caller and does nothing else. An unauthenticated caller
   still gets the 401 of the app-wide guard first.
4. **Two kinds of error.** Until the client and its redirect URI are verified,
   an error names no redirect, because the consent page must not send the
   browser to an unverified address. After that, an error carries
   ``redirect_to``: the client's redirect URI with ``error``, ``state`` and
   ``iss``.
5. **PKCE S256 only.** No ``code_challenge``, or a method other than ``S256``
   (``plain`` included), is refused.
6. **The member can narrow, never widen.** ``scopes`` in an approval must be a
   subset of what the client requested. ``include_personal`` defaults false
   (D-EA-4).
7. ⚠️ **The client's metadata comes through ``gateway.outbound_guard``**, the
   one SSRF seam, with a size cap and a time cap of its own. Only this half
   fetches. The edge never imports the guard. The document must name its own
   URL as ``client_id``, and the redirect URI must match one of its
   ``redirect_uris`` exactly, as a string. The fetch asks for
   ``Accept-Encoding: identity`` and refuses a compressed body, because the
   guard reads raw bytes and never decompresses.
8. **No code, token or hash goes into a log line.**
9. **ASCII and no control characters** (fix round 1). A client id or a
   redirect URI with a non-ASCII character is refused, so a look-alike host
   cannot show as a verified origin. A C0 control character or DEL in any
   field is a 422.

What is not here: dynamic client registration (EA-2b), pre-registered
clients, the organization switch and the client allow list (EA-4), the audit
row (EA-8) and any rate limit (EA-7).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from acb_auth import UserContext, agent_oauth, agent_tokens, get_current_user
from acb_common import get_logger, get_settings
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from gateway import outbound_guard
from gateway.db import current_tenant
from pydantic import BaseModel, ConfigDict, Field, field_validator

_log = get_logger("gateway.agent_access")

#: The most redirect URIs a client document may list.
_MAX_REDIRECT_URIS = 20

#: The longest client name the consent screen shows.
_MAX_CLIENT_NAME = 100

#: The longest ``state`` the server carries back.
_MAX_STATE = 1024

#: The most projects one connection may pin.
_MAX_PINS = 50

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def _require_enabled() -> None:
    """404 while ``agent_access_enabled`` is off (rule 3)."""
    if not agent_oauth.enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/agent-access",
    tags=["agent-access"],
    dependencies=[Depends(_require_enabled)],
)


# ── The request and response shapes ─────────────────────────────────────────


class AuthorizeRequest(BaseModel):
    """The parameters of an OAuth authorize request, as the consent page got them.

    ⚠️ ``extra="forbid"``: a field this model does not name is a 422. That is
    what refuses an ``organization_id`` or a ``member_email`` (rule 1).
    """

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(max_length=agent_oauth.MAX_URL_CHARS)
    redirect_uri: str = Field(max_length=agent_oauth.MAX_URL_CHARS)
    response_type: str = Field(max_length=32)
    code_challenge: str | None = Field(default=None, max_length=128)
    code_challenge_method: str | None = Field(default=None, max_length=16)
    resource: str | None = Field(default=None, max_length=agent_oauth.MAX_URL_CHARS)
    scope: str | None = Field(default=None, max_length=agent_oauth.MAX_SCOPE_CHARS)
    state: str | None = Field(default=None, max_length=_MAX_STATE)

    @field_validator("*", mode="after")
    @classmethod
    def _no_control_characters(cls, value: Any) -> Any:
        """A C0 control character or DEL in any text is a 422 (fix round 1).

        Postgres refuses a NUL in ``text``, so one byte was a 500 before.
        """
        texts = value if isinstance(value, list) else [value]
        for text in texts:
            if isinstance(text, str) and any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
                raise ValueError("a field holds a control character")
        return value


class ApproveRequest(AuthorizeRequest):
    """An authorize request plus what the member chose on the consent screen."""

    #: A subset of the requested scopes. ``None`` keeps every requested scope.
    scopes: list[str] | None = Field(default=None, max_length=len(agent_oauth.SUPPORTED_SCOPES))
    #: Project ids that limit the connection. Empty means every project the
    #: member sees (spec §7).
    pinned_projects: list[UUID] = Field(default_factory=list, max_length=_MAX_PINS)
    #: D-EA-4: personal projects stay out unless the member opts in.
    include_personal: bool = False


class ClientView(BaseModel):
    client_id: str
    #: The ``client_name`` of the document, or the host when it names none.
    name: str
    #: The scheme and host the document came from. This is the verified part.
    origin: str


class AuthorizeView(BaseModel):
    client: ClientView
    redirect_uri: str
    redirect_host: str
    #: The requested scopes the server supports, in the canonical order.
    scopes: list[str]
    resource: str
    state: str | None
    issuer: str
    #: Where the consent page sends the browser when the member says no.
    deny_redirect_to: str


# ── Errors ──────────────────────────────────────────────────────────────────


def _error(error: str, description: str, *, redirect_to: str | None = None) -> HTTPException:
    """A 400 with an OAuth error code. ``redirect_to`` only after rule 4 allows it."""
    return HTTPException(
        status_code=400,
        detail={
            "error": error,
            "error_description": description,
            "redirect_to": redirect_to,
        },
    )


def _member(user: UserContext) -> str:
    """The signed-in member's email, when a tenant is bound. Else 403 (rule 1)."""
    email = user.email or ""
    if "@" not in email or not user.access.is_active or current_tenant() is None:
        raise HTTPException(
            status_code=403,
            detail="An active member of an organization must approve an agent.",
        )
    return email


# ── The client's metadata document (CIMD), through the SSRF seam ────────────


class ClientRefused(Exception):
    """The client's metadata could not be fetched or is not valid."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: ⚠️ ``identity`` (fix round 1). The guard reads RAW bytes off the wire
#: (``aiter_raw``), so it never decompresses. httpx asks for gzip by default,
#: and a server that obliged sent bytes that ``json.loads`` cannot read.
_FETCH_HEADERS = {"Accept": "application/json", "Accept-Encoding": "identity"}

#: The first bytes of a gzip body and of a zlib (``deflate``) body. A server
#: that compresses although we asked for ``identity`` is refused by name.
#: ``outbound_guard.Answer`` carries no headers, and WS-55 may not edit the
#: guard, so the check reads the body instead of ``Content-Encoding``. A
#: Brotli body has no magic, and it fails as "not json".
_COMPRESSED_MAGIC = (b"\x1f\x8b", b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda")


def _cap() -> int:
    """The body cap. Always under the guard's own cap, so a cut body is refused."""
    configured = int(get_settings().agent_cimd_max_bytes)
    return max(1, min(configured, outbound_guard.MAX_ANSWER_BYTES - 1))


async def _fetch_document(client_id: str) -> dict[str, Any]:
    """GET the metadata document through ``outbound_guard`` (rule 7)."""
    timeout_s = float(get_settings().agent_cimd_timeout_s)
    try:
        async with asyncio.timeout(timeout_s):
            answer = await outbound_guard.request("GET", client_id, headers=_FETCH_HEADERS)
    except TimeoutError:
        raise ClientRefused("timeout") from None
    except outbound_guard.OutboundRefused as exc:
        raise ClientRefused(exc.reason or "refused") from None
    except httpx.HTTPError:
        raise ClientRefused("unreachable") from None
    if answer.status_code != 200:
        raise ClientRefused(f"status {answer.status_code}")
    if len(answer.body) > _cap():
        raise ClientRefused("too large")
    if answer.body.startswith(_COMPRESSED_MAGIC):
        raise ClientRefused("compressed")
    try:
        document = json.loads(answer.body)
    except (UnicodeDecodeError, ValueError):
        raise ClientRefused("not json") from None
    if not isinstance(document, dict):
        raise ClientRefused("not a json object")
    return document


def _clean_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    printable = "".join(c for c in value if c.isprintable())
    return printable.strip()[:_MAX_CLIENT_NAME]


def _validated_metadata(client_id: str, document: dict[str, Any]) -> dict[str, Any]:
    """The parts of a document the server keeps, after the CIMD checks."""
    if document.get("client_id") != client_id:
        raise ClientRefused("client_id does not match the URL")
    if "client_secret" in document:
        raise ClientRefused("a metadata document holds no secret")
    method = document.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise ClientRefused("token_endpoint_auth_method must be none")
    grant_types = document.get("grant_types")
    if grant_types is not None and (
        not isinstance(grant_types, list) or "authorization_code" not in grant_types
    ):
        raise ClientRefused("grant_types must include authorization_code")
    response_types = document.get("response_types")
    if response_types is not None and (
        not isinstance(response_types, list) or "code" not in response_types
    ):
        raise ClientRefused("response_types must include code")
    listed = document.get("redirect_uris")
    if not isinstance(listed, list) or not listed or len(listed) > _MAX_REDIRECT_URIS:
        raise ClientRefused("redirect_uris must list 1 to 20 URIs")
    allowed = [
        uri for uri in listed
        if isinstance(uri, str) and agent_oauth.is_allowed_redirect_uri(uri)
    ]
    if not allowed:
        raise ClientRefused("no redirect URI is allowed")
    return {
        "client_id": client_id,
        "client_name": _clean_name(document.get("client_name")),
        "redirect_uris": allowed,
        "token_endpoint_auth_method": "none",
    }


async def verified_client(client_id: str) -> dict[str, Any]:
    """The verified metadata of *client_id*, from the cache or a fresh fetch."""
    if not agent_oauth.is_cimd_client_id(client_id):
        raise ClientRefused("client_id is not a metadata document URL")
    cached = await agent_tokens.get_client(client_id)
    if cached is not None:
        if cached.registered_via != "cimd":
            raise ClientRefused("unknown client")
        if cached.live:
            return cached.metadata
    try:
        metadata = _validated_metadata(client_id, await _fetch_document(client_id))
    except ClientRefused as exc:
        _log.warning(
            "agent_access.client_refused",
            client_host=agent_oauth.client_origin(client_id),
            reason=exc.reason,
        )
        raise
    if not await agent_tokens.cache_cimd_client(client_id, metadata):
        raise ClientRefused("unknown client")
    return metadata


# ── The validation both routes share ────────────────────────────────────────


@dataclass(frozen=True)
class _Validated:
    metadata: dict[str, Any]
    scopes: tuple[str, ...]


def _redirect(req: AuthorizeRequest, params: dict[str, str | None]) -> str:
    """The client's redirect URI with *params*, ``state`` and ``iss`` (RFC 9207)."""
    return agent_oauth.authorization_redirect(
        req.redirect_uri, {**params, "state": req.state, "iss": agent_oauth.issuer()})


async def _validate(req: AuthorizeRequest) -> _Validated:
    """Every check of an authorize request, in the order of rule 4."""
    try:
        metadata = await verified_client(req.client_id)
    except ClientRefused as exc:
        raise _error("invalid_client", f"The client was refused: {exc.reason}.") from None
    if req.redirect_uri not in metadata.get("redirect_uris", []) or (
        not agent_oauth.is_allowed_redirect_uri(req.redirect_uri)
    ):
        raise _error("invalid_request", "redirect_uri is not registered for this client.")

    def fail(error: str, description: str) -> HTTPException:
        return _error(error, description, redirect_to=_redirect(req, {"error": error}))

    if req.response_type != "code":
        raise fail("unsupported_response_type", "response_type must be code.")
    if (
        req.code_challenge_method != agent_oauth.S256
        or not agent_oauth.is_valid_challenge(req.code_challenge)
    ):
        raise fail("invalid_request", "PKCE with code_challenge_method S256 is required.")
    if req.resource != agent_oauth.resource():
        raise fail("invalid_target", "resource must name this server's MCP endpoint.")
    try:
        scopes = (
            agent_oauth.parse_scope(req.scope)
            if req.scope is not None
            else agent_oauth.RESOURCE_SCOPES
        )
    except ValueError:
        raise fail("invalid_scope", "scope is malformed.") from None
    if not scopes:
        raise fail("invalid_scope", "scope names no scope this server supports.")
    return _Validated(metadata=metadata, scopes=scopes)


def _client_view(client_id: str, metadata: dict[str, Any]) -> ClientView:
    origin = agent_oauth.client_origin(client_id)
    name = metadata.get("client_name") or agent_oauth.redirect_host(client_id)
    return ClientView(client_id=client_id, name=name, origin=origin)


# ── The two routes ──────────────────────────────────────────────────────────


@router.post("/authorize/check", response_model=AuthorizeView)
async def check_authorize(
    req: AuthorizeRequest,
    user: UserContext = Depends(get_current_user),
) -> AuthorizeView:
    """Validate an authorize request for the consent screen. Writes nothing
    except the cache of the client's metadata."""
    _member(user)
    validated = await _validate(req)
    return AuthorizeView(
        client=_client_view(req.client_id, validated.metadata),
        redirect_uri=req.redirect_uri,
        redirect_host=agent_oauth.redirect_host(req.redirect_uri),
        scopes=list(validated.scopes),
        resource=agent_oauth.resource(),
        state=req.state,
        issuer=agent_oauth.issuer(),
        deny_redirect_to=_redirect(req, {"error": "access_denied"}),
    )


@router.post("/authorize/approve")
async def approve_authorize(
    req: ApproveRequest,
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Create the connection, mint a code, and return the redirect URL.

    The member is ``user.email`` and the organization is the bound tenant
    (rule 1). The answer carries a code, so no cache may keep it.
    """
    _member(user)
    validated = await _validate(req)
    requested = set(validated.scopes)
    chosen = set(req.scopes) if req.scopes is not None else requested
    if not chosen <= requested:
        raise _error("invalid_scope", "A member can narrow the scopes, never widen them.")
    if not chosen:
        raise _error("invalid_scope", "Choose one scope or more.")
    granted = agent_oauth.scope_string(chosen)
    view = _client_view(req.client_id, validated.metadata)

    connection_id = await agent_tokens.create_connection(
        user.email,
        req.client_id,
        client_name=view.name,
        client_origin=view.origin,
        scopes=granted.split(" "),
        pinned_projects=[str(p) for p in req.pinned_projects],
        include_personal=req.include_personal,
    )
    try:
        code = await agent_tokens.mint_code(
            connection_id,
            client_id=req.client_id,
            code_challenge=req.code_challenge or "",
            redirect_uri=req.redirect_uri,
            resource=agent_oauth.resource(),
            scope=granted,
        )
    except Exception:
        tenant = current_tenant()
        if tenant is not None:
            await agent_tokens.revoke_connection(tenant, connection_id)
        raise
    _log.info(
        "agent_access.approved",
        connection_id=connection_id,
        client_host=view.origin,
        scopes=granted,
    )
    return JSONResponse(
        {"redirect_to": _redirect(req, {"code": code})}, headers=_NO_STORE)
