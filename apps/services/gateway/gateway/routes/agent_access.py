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
   ``redirect_uris`` by ``agent_oauth.redirect_uri_matches``: the exact
   string, or any port on a loopback ``http`` URI with no port (EA-2c, spec
   §10.4). The code keeps the exact URI, port included. The fetch asks for
   ``Accept-Encoding: identity`` and refuses a compressed body, because the
   guard reads raw bytes and never decompresses.
8. **No code, token or hash goes into a log line.** The consent ticket is
   a token too.
9. **ASCII and no control characters** (fix round 1). A client id or a
   redirect URI with a non-ASCII character is refused, so a look-alike host
   cannot show as a verified origin. A C0 control character or DEL in any
   field is a 422.
10. ⚠️ **The consent ticket (EA-4a, spec §10.5, decision EA3-26).** ``check``
    gives a ticket of 32 random bytes. The server keeps only its SHA-256, as
    a key of the tenant-prefixed Redis wrapper, for 300 seconds. The value
    binds the member, the organization and six fields of the request.
    ``approve`` spends it with one ``GETDEL`` BEFORE any other check, so an
    approval cannot skip the screen and a ticket works once. A missing,
    spent, expired or unmatched ticket is 400 ``invalid_request``. A Redis
    outage is 503, never 400. Fence: ``tests/unit/test_agent_oauth_server.py``.
11. ⚠️ **The organization allowlist (EA-10p, spec §10.6).** Each route
    refuses a session whose organization ``agent_oauth.org_allowed`` refuses,
    right after the member check: 403 with the error ``org_not_enabled`` and
    no ``redirect_to``. The refusal comes before the client fetch and the
    ticket, so the ticket is not spent. The consent page shows the text.

12. ⚠️ **The member's own connections (EA-4b, spec §5.4 and §15).**
    ``GET /agent-access/connections`` lists the live connections of the
    member, and ``DELETE /agent-access/connections/{connection_id}`` revokes
    one. The member is :func:`_member` and the organization is
    ``current_tenant()``, never input. They do NOT call
    :func:`_require_org_allowed`: a revoke only removes access, so an
    organization off the list can still list and revoke. An id of another
    member or of another organization is 404 and changes nothing. Each revoke
    writes one audit row through :func:`audit_revoke`. No answer holds a
    token, a hash or a client id. The admin's pair of routes is
    ``routes/integrations_agents.py``, and it shares :func:`connection_out`
    and :func:`audit_revoke`. Fence: ``tests/unit/test_agent_connections_r8.py``.

What is not here: dynamic client registration (EA-2b), pre-registered
clients, the organization switch and the client allow list (EA-4c), the audit
row of each agent call (EA-8) and any rate limit (EA-7). The CSRF check of
the browser request (``Sec-Fetch-Site``) is the BFF's, because this router
sees only the BFF's server call (spec §10.5).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any, NamedTuple
from uuid import UUID

import httpx
from acb_auth import UserContext, agent_oauth, agent_tokens, get_current_user
from acb_common import get_logger, get_settings
from acb_common.tenant_redis import OUTAGE_ERRORS, get_tenant_redis, key, organization_scope
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

#: The consent ticket (rule 10): its random bytes, its life, and the longest
#: ticket ``approve`` reads. ``token_urlsafe(32)`` gives 43 characters.
_TICKET_BYTES = 32
_TICKET_TTL_S = 300
_MAX_TICKET = 128

#: The Redis namespace of the ticket. The wrapper prefixes it with the
#: organization: ``cc:<org>:agent-consent:<sha256>``.
_TICKET_NAMESPACE = "agent-consent"

#: The longest wait for one Redis command. Past it, the answer is the 503.
_REDIS_TIMEOUT_S = 2.0


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
    #: The ticket that ``check`` gave (rule 10). Optional in the model, so a
    #: request without it is the 400 of rule 10 and not a 422.
    consent_ticket: str | None = Field(default=None, max_length=_MAX_TICKET)


class ClientView(BaseModel):
    client_id: str
    #: The host the document came from, in ASCII, with a port if the URL has
    #: one. This is the VERIFIED part, so the consent screen names it in its
    #: headline (EA-4a fix round 1, spec §10.5).
    host: str
    #: The ``client_name`` of the document, or ``None``. It is only a CLAIM:
    #: the screen shows it second, as "It calls itself", never as the headline.
    name: str | None
    #: The scheme and host the document came from. This is the verified part.
    origin: str


class AuthorizeView(BaseModel):
    client: ClientView
    redirect_uri: str
    #: What the screen shows of the redirect (spec §10.5):
    #: ``scheme://host:port`` for http and https, ``scheme://authority`` or
    #: ``scheme:`` for an app. It replaced ``redirect_host``.
    redirect_display: str
    #: True for a scheme other than http and https. The page then says "an
    #: app on this computer".
    redirect_app: bool
    #: The one-time ticket that ``approve`` must present (rule 10).
    consent_ticket: str
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


#: The error of rule 11. The consent page reads this code.
ORG_NOT_ENABLED = "org_not_enabled"


def _require_org_allowed() -> None:
    """403 ``org_not_enabled`` unless the session's organization is on the
    list (rule 11). The organization is the bound tenant, never the body.

    It runs right after :func:`_member`, so a refused organization gets no
    client fetch, no consent ticket, no connection and no code.
    """
    if not agent_oauth.org_allowed(current_tenant()):
        _log.info("agent_access.org_not_allowed")
        raise HTTPException(
            status_code=403,
            detail={
                "error": ORG_NOT_ENABLED,
                "error_description": (
                    "Your organization has not turned on agent connections yet."),
                "redirect_to": None,
            },
            headers=_NO_STORE,
        )


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


#: Letters that draw nothing. Unicode files them as letters (``Lo``), so a
#: category check alone passes a name made of them. A name of only these
#: draws as an empty string beside the claim label (EA-4a fix round 1).
_INVISIBLE_LETTERS = frozenset({
    "ᅟ",  # HANGUL CHOSEONG FILLER
    "ᅠ",  # HANGUL JUNGSEONG FILLER
    "ㅤ",  # HANGUL FILLER
    "ﾠ",  # HALFWIDTH HANGUL FILLER
})


def _shows_something(name: str) -> bool:
    """True when *name* holds one letter or digit that draws.

    A visible name needs a character of a Unicode letter (``L*``) or number
    (``N*``) category that is not an invisible filler. A name of spaces,
    marks, symbols or fillers alone is no name.
    """
    return any(
        unicodedata.category(c)[0] in ("L", "N") and c not in _INVISIBLE_LETTERS
        for c in name
    )


def _clean_name(value: Any) -> str:
    """The claimed name, printable and at most 100 characters, or ``""``.

    ``""`` means "no name": the screen then shows the verified host only.
    """
    if not isinstance(value, str):
        return ""
    printable = "".join(c for c in value if c.isprintable())
    name = printable.strip()[:_MAX_CLIENT_NAME]
    return name if _shows_something(name) else ""


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
    if not agent_oauth.redirect_uri_matches(
        req.redirect_uri, metadata.get("redirect_uris", [])
    ) or not agent_oauth.is_allowed_redirect_uri(req.redirect_uri):
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
    """The verified host and origin, and the claimed name if it shows anything.

    The name is cleaned again here, so a row that an older build cached keeps
    to the rule of :func:`_clean_name`.
    """
    origin = agent_oauth.client_origin(client_id)
    host = origin.partition("://")[2]
    name = _clean_name(metadata.get("client_name")) or None
    return ClientView(client_id=client_id, host=host, name=name, origin=origin)


# ── The consent ticket (rule 10) ────────────────────────────────────────────


class _TicketBinding(NamedTuple):
    """What one ticket binds. ``approve`` must match every field.

    ``scope`` is the REQUESTED scope string, not the member's choice. The
    member narrows on the screen, and the approval names the narrowing.
    """

    member_email: str
    organization_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str | None
    state: str | None
    resource: str | None
    scope: str | None


def _binding(req: AuthorizeRequest, email: str, org: str) -> _TicketBinding:
    """The binding of *req* for this member in this organization."""
    return _TicketBinding(
        email, org, req.client_id, req.redirect_uri, req.code_challenge,
        req.state, req.resource, req.scope)


def _ticket_key(ticket: str) -> Any:
    """The tenant-prefixed key of a ticket: the SHA-256 of it, never the ticket."""
    return key(_TICKET_NAMESPACE, hashlib.sha256(ticket.encode("ascii")).hexdigest())


def _redis_unavailable() -> HTTPException:
    """A Redis outage is 503, never 400 (rule 10)."""
    return HTTPException(
        status_code=503,
        detail="The consent service is busy. Try again.",
        headers={"Retry-After": "2", **_NO_STORE},
    )


async def _mint_ticket(binding: _TicketBinding) -> str:
    """A new ticket for *binding*. The server keeps its SHA-256 for 300 s."""
    ticket = secrets.token_urlsafe(_TICKET_BYTES)
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_S):
            with organization_scope(binding.organization_id):
                await get_tenant_redis().setex(
                    _ticket_key(ticket), _TICKET_TTL_S, json.dumps(list(binding)))
    except OUTAGE_ERRORS:
        raise _redis_unavailable() from None
    return ticket


async def _spend_ticket(req: ApproveRequest, binding: _TicketBinding) -> None:
    """Spend the ticket of *req* with one ``GETDEL``, then match its binding.

    The ticket is gone after this call, whatever the answer. A missing,
    spent, expired or unmatched ticket is 400 ``invalid_request`` with no
    ``redirect_to``, because nothing in the request is verified yet.
    """
    refused = _error("invalid_request", "The consent ticket is missing or not valid.")
    ticket = req.consent_ticket
    if not ticket or not ticket.isascii():
        raise refused
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_S):
            with organization_scope(binding.organization_id):
                raw = await get_tenant_redis().getdel(_ticket_key(ticket))
    except OUTAGE_ERRORS:
        raise _redis_unavailable() from None
    if raw is None:
        raise refused
    try:
        stored = _TicketBinding(*json.loads(raw))
    except (TypeError, ValueError):
        raise refused from None
    if stored != binding:
        raise refused


# ── The two routes ──────────────────────────────────────────────────────────


@router.post("/authorize/check", response_model=AuthorizeView)
async def check_authorize(
    req: AuthorizeRequest,
    user: UserContext = Depends(get_current_user),
) -> AuthorizeView:
    """Validate an authorize request for the consent screen, and give the
    one-time consent ticket (rule 10). Writes nothing else except the cache
    of the client's metadata."""
    email = _member(user)
    _require_org_allowed()
    validated = await _validate(req)
    ticket = await _mint_ticket(_binding(req, email, str(current_tenant())))
    display, is_app = agent_oauth.redirect_display(req.redirect_uri)
    return AuthorizeView(
        client=_client_view(req.client_id, validated.metadata),
        redirect_uri=req.redirect_uri,
        redirect_display=display,
        redirect_app=is_app,
        consent_ticket=ticket,
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

    ⚠️ The ticket is spent FIRST (rule 10), before the client fetch and
    every other check. So no approval runs without the screen.
    """
    email = _member(user)
    _require_org_allowed()
    await _spend_ticket(req, _binding(req, email, str(current_tenant())))
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
        client_name=view.name or view.host,
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


# ── The member's own connections (rule 12, EA-4b) ───────────────────────────


#: The audit action of a revoke, for a member and for an admin alike.
REVOKE_ACTION = "agent_connection.revoke"


class AgentConnectionOut(BaseModel):
    """One live connection, as the AI agents view shows it.

    ⚠️ It names no token, no hash and no client id (rule 8). The view
    headlines ``client_origin``, the verified part, and shows
    ``client_name`` second, as the consent page does (spec §10.5).
    ``last_used_at`` is the last token issue or refresh, not the last call.
    """

    id: str
    member_email: str
    client_name: str
    client_origin: str
    scopes: list[str]
    pinned_projects: list[str]
    include_personal: bool
    created_at: str
    last_used_at: str | None
    expires_at: str


class AgentConnectionsOut(BaseModel):
    connections: list[AgentConnectionOut]


def connection_out(row: agent_tokens.ConnectionRow) -> AgentConnectionOut:
    """The answer shape of one row. The admin routes use it too."""
    return AgentConnectionOut(
        id=row.id,
        member_email=row.member_email,
        client_name=row.client_name,
        client_origin=row.client_origin,
        scopes=list(row.scopes),
        pinned_projects=list(row.pinned_projects),
        include_personal=row.include_personal,
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        expires_at=row.expires_at.isoformat(),
    )


def audit_revoke(actor_email: str, org: str, connection_id: str, *, admin: bool) -> None:
    """One audit row of a revoke: who, which connection, which organization.

    ``admin`` says whether an admin revoked a connection through the org
    list. It never blocks the revoke: an audit backend that is down only logs.
    The organization rides on the event itself, not in the payload: this
    module names no identity input as a string (rule 1, and the structural
    fence of ``test_agent_oauth_server.py``).
    """
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{actor_email}",
            action=REVOKE_ACTION,
            target=f"agent_connection:{connection_id}",
            payload={"admin": admin},
            organization_id=org,
        ))
    except Exception as exc:  # an audit backend that is down must not block this
        _log.warning("agent_access.audit_failed", error=type(exc).__name__)


def _not_found() -> HTTPException:
    """The one answer for an id the caller may not revoke, or that is gone."""
    return HTTPException(status_code=404, detail="No such agent connection.")


@router.get("/connections", response_model=AgentConnectionsOut)
async def list_my_connections(
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """The member's own live connections, newest first (rule 12)."""
    email = _member(user)
    rows = await agent_tokens.list_connections(
        str(current_tenant()), member_email=email)
    body = AgentConnectionsOut(connections=[connection_out(r) for r in rows])
    return JSONResponse(body.model_dump(), headers=_NO_STORE)


@router.delete("/connections/{connection_id}", status_code=204)
async def revoke_my_connection(
    connection_id: UUID,
    user: UserContext = Depends(get_current_user),
) -> None:
    """Revoke one of the member's own connections (rule 12).

    404 for an id of another member, of another organization, or one that
    is already revoked. Nothing changes then.
    """
    email = _member(user)
    org = str(current_tenant())
    revoked = await agent_tokens.revoke_connection_scoped(
        org, str(connection_id), member_email=email)
    if not revoked:
        raise _not_found()
    audit_revoke(email, org, str(connection_id), admin=False)
