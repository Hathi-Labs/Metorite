"""``/agent-access`` — the authenticated half of the authorization server (WS-55 EA-2a).

Spec ``project-docs/specs/external_agent_access.md`` §5.5, §7, §8, §10 and §15
EA-2a · decision D97 · ``user_management_contract.md`` R11.

The consent page that EA-4 builds (``https://app.metorite.com/connect/authorize``)
calls these two routes as the signed-in member, through the BFF:

* ``POST /agent-access/authorize/check`` — validate an authorize request and
  return what the consent screen needs: the verified client name and origin,
  the redirect host and the requested scopes. For a first-party client that
  the member already approved, it returns the redirect with a code instead
  (rule 13, D-EA-11).
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
   subset of what the client requested. ``include_personal`` is false in a
   body that leaves it out, so a broken client fails closed. The consent page
   ticks it by default, and sends it on each approve
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

13. ⚠️ **First-party clients (D-EA-11, spec §10.7).** A client whose id is,
    string for string, on ``agent_first_party_client_ids`` is first-party.
    The default list is empty, so nothing below runs until an operator sets
    it. For such a client, ``check`` runs every check of rules 1 to 11 and
    :func:`_validate` first. Then it answers ``{"first_party": true,
    "redirect_to": ...}`` with a code, and no ticket, only when ALL of these
    hold: the request carries ``state``, the redirect URI is ``https``, on
    the client's own origin, and in the document's list as the exact
    string, and the member holds a live connection of this client in this
    organization whose scopes cover every requested scope. The code goes on
    that connection, so the pins and the personal switch stay as the member
    chose them. Any other case gets the consent screen as before, never an
    error. ``approve`` for a first-party client replaces the member's older
    connections of that client, and revokes their tokens, in the
    transaction of the insert. Nothing changes for a client that is not on
    the list: the screen, the ticket and the approve gate of the page stay.
    Fence: ``tests/unit/test_agent_first_party.py``.

14. ⚠️ **Change access (EA-17c-1, D-EA-13, spec §10.8).**
    ``PATCH /agent-access/connections/{connection_id}`` changes the scopes,
    the pins, the left-out list and the personal switch of one of the
    member's own connections, with no reconnect. It ships dark: while
    ``agent_oauth.change_access_allowed`` refuses the organization of the
    session, each request gets 404, a malformed one too. The member is
    :func:`_member` and the organization is ``current_tenant()``, never
    input. A signed agent gets 404. Each pin and each left-out id must be a
    project that the member sees (``tree.grantable_project_ids``), else 422,
    and the scopes of the agent play no part. The store then refuses an id
    of another member, of another organization, a revoked one or an unknown
    one with 404, and a scope outside the ceiling of the live tokens, a
    grant with no scope and a change to ``offline_access`` with 422. The
    ceiling binds a turn-on only, so a narrowing always passes. A mark that
    the member no longer sees stays, and only a new unseen id is a 422. An
    id in both lists is a 422 too. The answer is the new row. A real change
    logs ``agent_access.connection_changed`` with the id and the counts, and
    writes one audit row (:func:`audit_change`). A body that equals the row
    writes nothing, logs nothing and audits nothing. It does not call
    :func:`_require_org_allowed`, as the list and the revoke do not. Fence:
    ``tests/unit/test_agent_change_access.py``. A box connection is never
    changed here (EA-19c): the store reads no box, so a box id is 404.

15. ⚠️ **Box tokens (EA-19c, D-EA-5, spec §10.9).** Four routes.
    ``POST /agent-access/box-tokens/check`` gives a ticket in its own
    namespace ``box-mint``, never ``agent-consent``. The ticket binds the
    member, the organization and each field of the body: the box name, the
    scopes, the days and the reach for a mint, the connection id, ``days``
    and ``now`` for a rotation. ``POST /agent-access/box-tokens`` (the mint)
    and ``POST /agent-access/box-tokens/{connection_id}/rotate`` spend it
    with one ``GETDEL`` before each other check of the body. Each of the
    three is for a person only: a signed agent gets 404, and none of them
    is in ``agent_oauth.MCP_ROUTES``. The member is :func:`_member` and the
    organization is ``current_tenant()``. While ``agent_box_tokens_enabled``
    is off, the three answer 404 (:func:`_require_box_tokens`). The scopes
    follow the consent rule, less ``offline_access``, and a write scope
    needs the writes flag. The reach follows §10.8. The mint and the
    rotation answer the raw token ONCE, with ``Cache-Control: no-store``,
    and no log line or audit row holds it. ``POST
    /agent-access/box-tokens/revoke-all`` is for an admin, with the gates of
    ``routes/integrations_agents.py``. It revokes every box connection of
    the organization with the reason ``flag_off``, and it stays live with
    the flag off. Both list routes write the audit row
    ``box_token.new_address`` once for each fresh mark
    (:func:`audit_new_addresses`). The ``Sec-Fetch-Site`` check is the
    BFF's (``box-tokens/relay.ts``), because this router sees only the
    server call of the BFF. Fence:
    ``tests/unit/test_agent_box_token_routes.py``.

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
from typing import Annotated, Any, NamedTuple
from uuid import UUID

import httpx
from acb_auth import (
    UserContext,
    agent_oauth,
    agent_tokens,
    get_current_user,
    require_feature_router,
    require_permission,
)
from acb_common import get_logger, get_settings
from acb_common.tenant_redis import OUTAGE_ERRORS, get_tenant_redis, key, organization_scope
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from gateway import outbound_guard
from gateway.db import current_tenant
from gateway.routes.integrations import MANAGE_PERMISSION
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)

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
    #: D-EA-12 (spec §10.8, EA-17a): project ids that the reach stops at.
    #: Each id and every project beneath it stay out, unless a pin beneath
    #: it starts again. The cap is the cap of the pins. An id that the
    #: member cannot see is stored as a bad pin is. It can only narrow the
    #: reach: a hidden parent left out also removes the projects beneath
    #: it, and the member's live access still bounds the rest (§7.1).
    left_out_projects: list[UUID] = Field(default_factory=list, max_length=_MAX_PINS)
    #: D-EA-4 (amended 2026-10-30): the consent page ticks it by default and
    #: always sends it. A body that leaves it out reads as false.
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
        # No `scope`: the advertised resource scopes, so the scope of an app
        # that ships dark is never granted by default (MCP-P1a).
        scopes = (
            agent_oauth.parse_scope(req.scope)
            if req.scope is not None
            else agent_oauth.advertised_resource_scopes()
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


def _ticket_key(ticket: str, namespace: str = _TICKET_NAMESPACE) -> Any:
    """The tenant-prefixed key of a ticket: the SHA-256 of it, never the ticket.

    *namespace* keeps two purposes apart: ``agent-consent`` (rule 10) and
    ``box-mint`` (rule 15). A ticket of one never opens the other.
    """
    return key(namespace, hashlib.sha256(ticket.encode("ascii")).hexdigest())


def _redis_unavailable() -> HTTPException:
    """A Redis outage is 503, never 400 (rule 10)."""
    return HTTPException(
        status_code=503,
        detail="The consent service is busy. Try again.",
        headers={"Retry-After": "2", **_NO_STORE},
    )


async def _mint_ticket(binding: Any, *, namespace: str = _TICKET_NAMESPACE) -> str:
    """A new ticket for *binding*. The server keeps its SHA-256 for 300 s.

    *binding* is a ``NamedTuple`` with an ``organization_id`` field, and each
    field a JSON string, number or boolean, so a spend reads it back equal.
    """
    ticket = secrets.token_urlsafe(_TICKET_BYTES)
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_S):
            with organization_scope(binding.organization_id):
                await get_tenant_redis().setex(
                    _ticket_key(ticket, namespace), _TICKET_TTL_S,
                    json.dumps(list(binding)))
    except OUTAGE_ERRORS:
        raise _redis_unavailable() from None
    return ticket


async def _take_ticket(ticket: str | None, binding: Any, *, namespace: str) -> bool:
    """Spend *ticket* with one ``GETDEL``, and say whether it bound *binding*.

    The ticket is gone after this call, whatever the answer. A missing,
    spent, expired or unmatched ticket is ``False``. A Redis outage is the
    503 of :func:`_redis_unavailable`. The organization of the key is the
    one of *binding*, which the caller took from the session.
    """
    if not ticket or not ticket.isascii():
        return False
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_S):
            with organization_scope(binding.organization_id):
                raw = await get_tenant_redis().getdel(_ticket_key(ticket, namespace))
    except OUTAGE_ERRORS:
        raise _redis_unavailable() from None
    if raw is None:
        return False
    try:
        stored = type(binding)(*json.loads(raw))
    except (TypeError, ValueError):
        return False
    return stored == binding


async def _spend_ticket(req: ApproveRequest, binding: _TicketBinding) -> None:
    """Spend the ticket of *req* with one ``GETDEL``, then match its binding.

    The ticket is gone after this call, whatever the answer. A missing,
    spent, expired or unmatched ticket is 400 ``invalid_request`` with no
    ``redirect_to``, because nothing in the request is verified yet.
    """
    if not await _take_ticket(req.consent_ticket, binding, namespace=_TICKET_NAMESPACE):
        raise _error("invalid_request", "The consent ticket is missing or not valid.")


# ── First-party clients (rule 13, D-EA-11) ──────────────────────────────────


async def _first_party_redirect(
    req: AuthorizeRequest, email: str, validated: _Validated
) -> str | None:
    """The redirect with a new code when the member already approved this
    first-party client for these scopes (rule 13), else ``None``.

    ``None`` means "show the consent screen as before". Each condition fails
    to the screen, never to an error, and in this order: the client is on
    the list, the request carries ``state``, the redirect URI is in the
    document's list as the exact string, :func:`agent_oauth.first_party_redirect_allowed`
    passes it, and one live connection of this member and client covers
    every requested scope. :func:`_validate` already checked PKCE, the
    response type, the resource and the redirect. The member is the
    session's, and the organization is the bound tenant (rule 1).
    """
    if not agent_oauth.is_first_party(req.client_id):
        return None
    if not req.state:
        return None
    if req.redirect_uri not in validated.metadata.get("redirect_uris", []):
        return None
    if not agent_oauth.first_party_redirect_allowed(req.client_id, req.redirect_uri):
        return None
    granted = agent_oauth.scope_string(validated.scopes)
    for connection in await agent_tokens.live_connections_of_client(email, req.client_id):
        if not all(agent_oauth.scope_holds(connection.scopes, s) for s in validated.scopes):
            continue
        try:
            code = await agent_tokens.mint_code(
                connection.id,
                client_id=req.client_id,
                code_challenge=req.code_challenge or "",
                redirect_uri=req.redirect_uri,
                resource=agent_oauth.resource(),
                scope=granted,
            )
        except LookupError:
            # A revoke landed after the read. The screen asks again.
            return None
        _log.info(
            "agent_access.first_party_continued",
            connection_id=connection.id,
            client_host=agent_oauth.client_origin(req.client_id),
            scopes=granted,
        )
        return _redirect(req, {"code": code})
    return None


# ── The two routes ──────────────────────────────────────────────────────────


@router.post("/authorize/check", response_model=AuthorizeView)
async def check_authorize(
    req: AuthorizeRequest,
    user: UserContext = Depends(get_current_user),
) -> AuthorizeView | JSONResponse:
    """Validate an authorize request for the consent screen, and give the
    one-time consent ticket (rule 10). Writes nothing else except the cache
    of the client's metadata.

    ⚠️ The one exception is rule 13: for a first-party client that the
    member already approved for these scopes, the answer is
    ``{"first_party": true, "redirect_to": ...}`` with a new code, and no
    ticket. The answer then carries a code, so no cache may keep it.
    """
    email = _member(user)
    _require_org_allowed()
    validated = await _validate(req)
    continued = await _first_party_redirect(req, email, validated)
    if continued is not None:
        return JSONResponse(
            {"first_party": True, "redirect_to": continued}, headers=_NO_STORE)
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
        left_out_projects=[str(p) for p in req.left_out_projects],
        # D-EA-13: what the agent asked for, before the member cleared a box.
        # The ticket bound this same request, so it is the screen's list.
        requested_scopes=list(validated.scopes),
        # Rule 13: a first-party approval replaces the member's older
        # connection of this client. False for every other client.
        replace_same_client=agent_oauth.is_first_party(req.client_id),
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
            await agent_tokens.revoke_connection(
                tenant, connection_id,
                reason=agent_tokens.REVOKE_APPROVE_ROLLBACK)
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
    left_out_projects: list[str]
    include_personal: bool
    created_at: str
    last_used_at: str | None
    expires_at: str
    #: What the agent asked for at the connect (EA-17a, D-EA-13). ``None`` on
    #: a connection from before EA-17a.
    requested_scopes: list[str] | None
    #: The resource scopes that Change access may turn on: the scopes that
    #: every live token of the connection covers (D-EA-13 rule 2, EA-17c-1).
    ceiling_scopes: list[str]
    #: EA-19c (spec §10.9): ``oauth`` or ``box``. For a box, ``last_used_at``
    #: is the last call, and ``expires_at`` never slides.
    credential: str
    #: EA-19c: the last client address of a box, and the time of its last
    #: new-address mark. ``None`` for an OAuth connection.
    last_seen_addr: str | None
    addr_changed_at: str | None


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
        left_out_projects=list(row.left_out_projects),
        include_personal=row.include_personal,
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        expires_at=row.expires_at.isoformat(),
        requested_scopes=(
            None if row.requested_scopes is None else list(row.requested_scopes)),
        ceiling_scopes=list(row.ceiling_scopes),
        credential=row.credential,
        last_seen_addr=row.last_seen_addr,
        addr_changed_at=row.addr_changed_at.isoformat() if row.addr_changed_at else None,
    )


#: The actor of a new-address audit row: the edge saw the address, and the
#: list route writes the row for it (spec §10.9 question 5).
_EDGE_ACTOR = "system:agent_edge"


async def audit_new_addresses(org: str, rows: list[agent_tokens.ConnectionRow]) -> None:
    """Write the audit row ``box_token.new_address`` for the newest mark of each live box (rule 15).

    The edge marks ``addr_changed_at`` and writes no audit row, because the
    audit writer opens a sync engine that the edge must not hold. So both
    list routes call this. The row id comes from the connection and the time
    of the mark (``agent_tokens.box_address_event_id``). A mark whose row
    exists already writes nothing, and the primary key holds one row when
    two reads race. A mark that a later mark replaces before any list read gets no row, and neither does a mark on a box that is revoked or expired first. It never blocks the list: a failure only logs.
    """
    marks = {
        agent_tokens.box_address_event_id(row.id, row.addr_changed_at): row
        for row in rows
        if row.credential == agent_tokens.BOX and row.addr_changed_at is not None
    }
    if not marks:
        return
    try:
        from acb_audit import AuditEvent, record

        done = await agent_tokens.audited_events(org, list(marks))
        for event_id, row in marks.items():
            if event_id in done:
                continue
            record(AuditEvent(
                actor=_EDGE_ACTOR,
                action=agent_tokens.BOX_NEW_ADDRESS_ACTION,
                target=f"agent_connection:{row.id}",
                payload={"addr": row.last_seen_addr,
                         "marked_at": row.addr_changed_at.isoformat()},
                id=UUID(event_id),
                at=row.addr_changed_at,
                organization_id=org,
            ))
    except Exception as exc:  # an audit backend that is down must not block the list
        _log.warning("agent_access.audit_failed", error=type(exc).__name__)


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
    org = str(current_tenant())
    rows = await agent_tokens.list_connections(org, member_email=email)
    await audit_new_addresses(org, rows)
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
        org, str(connection_id), member_email=email,
        reason=agent_tokens.REVOKE_MEMBER)
    if not revoked:
        raise _not_found()
    audit_revoke(email, org, str(connection_id), admin=False)


# ── Change access (rule 14, EA-17c-1, D-EA-13) ──────────────────────────────


#: The audit action of a change of a grant (rule 14).
CHANGE_ACTION = "agent_connection.change"


def _grant_summary(row: agent_tokens.ConnectionRow) -> dict[str, Any]:
    """What an audit row keeps of one grant: the scopes, the two counts and
    the personal switch. Never a project id or a project name."""
    return {
        "scopes": list(row.scopes),
        "pinned": len(row.pinned_projects),
        "left_out": len(row.left_out_projects),
        "include_personal": row.include_personal,
    }


def audit_change(actor_email: str, org: str, change: agent_tokens.GrantChange) -> None:
    """One audit row of a change of a grant, before and after (rule 14).

    It never blocks the change: an audit backend that is down only logs. The
    organization rides on the event, as in :func:`audit_revoke`.
    """
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{actor_email}",
            action=CHANGE_ACTION,
            target=f"agent_connection:{change.after.id}",
            payload={"before": _grant_summary(change.before),
                     "after": _grant_summary(change.after)},
            organization_id=org,
        ))
    except Exception as exc:  # an audit backend that is down must not block this
        _log.warning("agent_access.audit_failed", error=type(exc).__name__)


#: The longest body of a change. Two lists of 50 ids and every scope need
#: about 5 KiB, so this leaves room and still bounds the read.
_MAX_CHANGE_BODY = 16_384

#: The longest scope name that a change may send. A real one is far shorter.
_MAX_SCOPE_NAME = 64


class ChangeAccessRequest(BaseModel):
    """The new grant of one connection (rule 14).

    ⚠️ ``extra="forbid"``: a field that this model does not name is a 422.
    So a body cannot name an organization, a member or ``requested_scopes``.
    ⚠️ Each of the four fields is required. A missing list must not read as
    an empty one, because no pin means every project the member sees.
    """

    model_config = ConfigDict(extra="forbid")

    #: The new scopes. ``offline_access`` must be in it exactly when the
    #: connection holds it now (D-EA-13 rule 3).
    scopes: list[Annotated[str, Field(max_length=_MAX_SCOPE_NAME)]] = Field(
        max_length=len(agent_oauth.SUPPORTED_SCOPES))
    pinned_projects: list[UUID] = Field(max_length=_MAX_PINS)
    left_out_projects: list[UUID] = Field(max_length=_MAX_PINS)
    #: A JSON boolean only. ``"true"`` and ``1`` are a 422.
    include_personal: StrictBool


async def _require_change_access(
    user: UserContext = Depends(get_current_user),
) -> None:
    """404 unless ``agent_change_access_orgs`` holds the session's
    organization (rule 14). The organization is the bound tenant.

    ⚠️ A dependency of the route, so it runs before FastAPI reads the path
    id. The handler reads the body itself, after it. So an organization off
    the list gets the 404 for each request, a malformed id or body too.
    """
    del user  # only so that the tenant is bound first
    if not agent_oauth.change_access_allowed(current_tenant()):
        raise HTTPException(status_code=404, detail="Not Found")


async def _capped_body(request: Request, cap: int) -> bytes:
    """The raw body of *request*, read with a cap. Past *cap* bytes, 413."""
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > cap:
            raise HTTPException(status_code=413, detail="The body is too large.")
    return bytes(raw)


def _validated(model: type[BaseModel], raw: bytes) -> Any:
    """*raw* checked by *model*. A body that is not JSON, or that the model
    refuses, is the 422 of FastAPI, with no input echoed back."""
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(
            include_url=False, include_context=False, include_input=False,
        )) from None


async def _change_body(request: Request) -> ChangeAccessRequest:
    """The body of a change, read with a cap and checked by the model.

    Past :data:`_MAX_CHANGE_BODY` bytes the answer is 413. A body that is
    not JSON, or that the model refuses, is the 422 of FastAPI, with no
    input echoed back.
    """
    return _validated(ChangeAccessRequest, await _capped_body(request, _MAX_CHANGE_BODY))


def _unprocessable(error: str, description: str, **extra: Any) -> HTTPException:
    """A 422 of rule 14, with a code that the AI agents view reads."""
    return HTTPException(
        status_code=422,
        detail={"error": error, "error_description": description, **extra},
        headers=_NO_STORE,
    )


#: The words of each 422 of the store, by its reason.
_CHANGE_REFUSALS: dict[str, str] = {
    agent_tokens.CHANGE_NO_SCOPE: "Choose one scope or more.",
    agent_tokens.CHANGE_OFFLINE_ACCESS: (
        "Offline access stays as it is. Keep it as the connection has it."),
    agent_tokens.CHANGE_ABOVE_CEILING: (
        "The agent's token does not carry this scope. Reconnect the agent to "
        "turn it on."),
    agent_tokens.CHANGE_PROJECT_NOT_SEEN: (
        "You can only choose a project that you can see."),
}


@router.patch(
    "/connections/{connection_id}",
    response_model=AgentConnectionOut,
    dependencies=[Depends(_require_change_access)],
)
async def change_my_connection(
    connection_id: UUID,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Change the grant of one of the member's own connections (rule 14).

    The checks run in this order, and each refusal writes nothing:

    1. The organization on ``agent_change_access_orgs``, else 404.
    2. The member of the session (:func:`_member`), else 403. A signed
       agent is refused with 404: only its member changes a grant.
    3. The body (:class:`ChangeAccessRequest`), else 413 or 422. A copy of
       an id drops out, and the order stays. An id in both lists is 422
       ``project_in_both_lists``, because such a grant reaches nothing.
    4. The sight of the member of the session (``tree.grantable_project_ids``)
       finds each unseen id. The scopes of the agent play no part.
    5. The store, in one transaction (``agent_tokens.update_connection_grant``):
       a live connection of this member in this organization, else 404. Then
       the scope rules of D-EA-13, and an unseen id that is new to its list,
       else 422.

    The answer is the new row, in the shape of the list. A body that equals
    the row writes nothing, logs no change and writes no audit row.
    """
    email = _member(user)
    if user.agent is not None:
        raise _not_found()
    change = await _change_body(request)
    pins = list(dict.fromkeys(str(p) for p in change.pinned_projects))
    left_out = list(dict.fromkeys(str(p) for p in change.left_out_projects))
    left_set = set(left_out)
    both = [p for p in pins if p in left_set]
    if both:
        raise _unprocessable(
            "project_in_both_lists",
            "A project cannot be chosen and left out at once.",
            projects=both,
        )
    from gateway.routes.projects.tree import grantable_project_ids

    seen = await grantable_project_ids(user) if pins or left_out else frozenset()
    result = await agent_tokens.update_connection_grant(
        str(current_tenant()),
        str(connection_id),
        member_email=email,
        scopes=change.scopes,
        pinned_projects=pins,
        left_out_projects=left_out,
        include_personal=change.include_personal,
        unseen_pins=[p for p in pins if p not in seen],
        unseen_left_out=[p for p in left_out if p not in seen],
    )
    if isinstance(result, agent_tokens.ChangeRefused):
        if result.reason == agent_tokens.CHANGE_NOT_FOUND:
            raise _not_found()
        extra: dict[str, Any] = (
            {"projects": list(result.projects)}
            if result.reason == agent_tokens.CHANGE_PROJECT_NOT_SEEN
            else {"scopes": list(result.scopes)})
        raise _unprocessable(result.reason, _CHANGE_REFUSALS[result.reason], **extra)
    row = result.after
    if result.changed:
        # Ids and counts only. A project name never goes into a log line.
        _log.info(
            "agent_access.connection_changed",
            connection_id=row.id,
            scopes=len(row.scopes),
            pinned=len(row.pinned_projects),
            left_out=len(row.left_out_projects),
            include_personal=row.include_personal,
        )
        audit_change(email, str(current_tenant()), result)
    return JSONResponse(connection_out(row).model_dump(), headers=_NO_STORE)


# ── Box tokens (rule 15, EA-19c, D-EA-5, spec §10.9) ────────────────────────


#: The Redis namespace of the box ticket. It is not the namespace of the
#: consent ticket, so a ticket of one purpose never opens the other.
_BOX_TICKET_NAMESPACE = "box-mint"

#: The two purposes of a box ticket.
_BOX_MINT = "mint"
_BOX_ROTATE = "rotate"

#: The longest body of a box route. Two lists of 50 ids and every scope fit.
_MAX_BOX_BODY = 16_384

#: The audit actions of the box routes. The new-address row is
#: ``agent_tokens.BOX_NEW_ADDRESS_ACTION``.
BOX_MINT_ACTION = "box_token.create"
BOX_ROTATE_ACTION = "box_token.rotate"
BOX_REVOKE_ALL_ACTION = "box_token.revoke_all"


async def _require_box_tokens() -> None:
    """404 while ``agent_box_tokens_enabled`` is off (rule 15).

    A dependency of the check, the mint and the rotate, so it runs before
    the path id and the body are read. The list, the revoke of one
    connection and the revoke of all box tokens do not take it: they stay
    live with the flag off, so an admin can still remove access.
    """
    if not agent_oauth.box_tokens_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _box_name_ok(value: str) -> str:
    """The box name, trimmed: 1 to 100 characters with no control character.

    A control character is any of Unicode category ``Cc``, so C0, DEL and C1.
    """
    name = value.strip()
    if not name or len(name) > agent_tokens.BOX_NAME_MAX:
        raise ValueError(f"a box name has 1 to {agent_tokens.BOX_NAME_MAX} characters")
    if any(unicodedata.category(c) == "Cc" for c in name):
        raise ValueError("a box name holds a control character")
    return name


class BoxMintFields(BaseModel):
    """What a member asks for when they make a box token (spec §10.9 q. 2).

    ⚠️ ``extra="forbid"``: a body cannot name an organization or a member.
    The member is the one of the session, and the organization is the bound
    tenant (rule 1).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=4 * agent_tokens.BOX_NAME_MAX)
    #: The default is ``projects:read projects:write``. An app scope is on
    #: only when the member asks for it by name.
    scopes: list[Annotated[str, Field(max_length=_MAX_SCOPE_NAME)]] = Field(
        default_factory=lambda: [agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE],
        max_length=len(agent_oauth.SUPPORTED_SCOPES))
    days: StrictInt = Field(
        default=agent_tokens.BOX_DAYS_DEFAULT, ge=1, le=agent_tokens.BOX_DAYS_MAX)
    pinned_projects: list[UUID] = Field(default_factory=list, max_length=_MAX_PINS)
    left_out_projects: list[UUID] = Field(default_factory=list, max_length=_MAX_PINS)
    include_personal: StrictBool = False

    @field_validator("name", mode="after")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _box_name_ok(value)


class BoxMintRequest(BoxMintFields):
    """A mint: the fields of the check, and the ticket that the check gave."""

    ticket: str | None = Field(default=None, max_length=_MAX_TICKET)


class BoxRotateFields(BaseModel):
    """A rotation (spec §10.9 question 4). ``now`` kills the old token at
    once. Else the old token lives 24 hours more at most."""

    model_config = ConfigDict(extra="forbid")

    days: StrictInt = Field(
        default=agent_tokens.BOX_DAYS_DEFAULT, ge=1, le=agent_tokens.BOX_DAYS_MAX)
    now: StrictBool = False


class BoxRotateCheck(BoxRotateFields):
    """The check of a rotation names the connection. The ticket binds it."""

    connection_id: UUID


class BoxRotateRequest(BoxRotateFields):
    """A rotation: the fields of the check, and its ticket."""

    ticket: str | None = Field(default=None, max_length=_MAX_TICKET)


class _BoxBinding(NamedTuple):
    """What one box ticket binds. The mint or the rotation must match each
    field. The lists are joined with commas, so the JSON form reads back
    equal. ``connection_id`` is empty for a mint."""

    purpose: str
    member_email: str
    organization_id: str
    connection_id: str
    name: str
    scopes: str
    days: int
    now: bool
    pinned_projects: str
    left_out_projects: str
    include_personal: bool


def _mint_binding(req: BoxMintFields, email: str, org: str) -> _BoxBinding:
    """The binding of a mint, from the body as the member sent it."""
    return _BoxBinding(
        _BOX_MINT, email, org, "", req.name, " ".join(req.scopes), req.days, False,
        ",".join(str(p) for p in req.pinned_projects),
        ",".join(str(p) for p in req.left_out_projects),
        req.include_personal)


def _rotate_binding(
    connection_id: str, req: BoxRotateFields, email: str, org: str
) -> _BoxBinding:
    """The binding of a rotation of *connection_id*."""
    return _BoxBinding(
        _BOX_ROTATE, email, org, connection_id, "", "", req.days, req.now, "", "", False)


def _box_refusal(status: int, error: str, description: str, **extra: Any) -> HTTPException:
    """A refusal of a box route, with a code that the dialog reads."""
    return HTTPException(
        status_code=status,
        detail={"error": error, "error_description": description, **extra},
        headers=_NO_STORE,
    )


def _no_ticket() -> HTTPException:
    return _box_refusal(400, "invalid_request", "The box ticket is missing or not valid.")


def _box_person(user: UserContext) -> str:
    """The member of the session, for a person only (rule 15).

    A signed agent gets 404: branch 0 already refuses each pair that is not
    in ``MCP_ROUTES``, and this is the second lock. Then the organization
    must be on the allowlist.
    """
    email = _member(user)
    if user.agent is not None:
        raise _not_found()
    _require_org_allowed()
    return email


def _box_scopes(asked: list[str]) -> tuple[str, ...]:
    """The scopes of a box token, by the consent rule (spec §10.9 q. 2).

    ``agent_oauth.parse_scope`` reads them, as ``_validate`` does for the
    consent page. ``offline_access`` drops out, because a box has no
    refresh token. A scope that the server does not know is a 422 here, not
    a silent drop, so the member sees what the token cannot hold. A write
    scope needs ``agent_oauth.writes_enabled``.

    SW-77: the box scope ``agent_oauth.BOX_RUNNER`` is known here, and only
    here (``agent_oauth.parse_box_scope``). It stands alone: with another
    scope it is 422 ``agent_oauth.BOX_RUNNER_ALONE``.
    """
    words = [w for item in asked for w in item.split(" ") if w]
    try:
        parsed = agent_oauth.parse_box_scope(" ".join(words))
    except ValueError:
        raise _box_refusal(422, "invalid_scope", "A scope is malformed.") from None
    unknown = [w for w in dict.fromkeys(words) if w not in parsed]
    if unknown:
        raise _box_refusal(
            422, "invalid_scope", "This server does not know a scope.", scopes=unknown)
    kept = tuple(s for s in parsed if s != agent_oauth.OFFLINE_ACCESS)
    if not kept:
        raise _box_refusal(422, "no_scope", "Choose one scope or more.")
    if agent_oauth.box_scopes_in(kept) and len(kept) > 1:
        raise _box_refusal(
            422, agent_oauth.BOX_RUNNER_ALONE,
            "A runner token holds box:runner and no other scope. Make a second token.")
    if not agent_oauth.writes_enabled() and any(agent_oauth.covered_by(s) for s in kept):
        raise _box_refusal(
            422, agent_oauth.WRITES_DISABLED_CODE,
            "Changes are turned off for agents. Choose only read scopes.")
    return kept


async def _box_reach(
    user: UserContext, req: BoxMintFields
) -> tuple[list[str], list[str]]:
    """The pins and the left-out list of a box, by the rules of §10.8.

    A copy of an id drops out, and the order stays. An id in both lists is
    422 ``project_in_both_lists``. Each id must be a project that the member
    of the session sees, or a personal root of the member
    (``tree.grantable_project_ids``), else 422 ``project_not_seen``. The cap
    is the cap of the pins, in the model.
    """
    pins = list(dict.fromkeys(str(p) for p in req.pinned_projects))
    left_out = list(dict.fromkeys(str(p) for p in req.left_out_projects))
    left_set = set(left_out)
    both = [p for p in pins if p in left_set]
    if both:
        raise _box_refusal(
            422, "project_in_both_lists",
            "A project cannot be chosen and left out at once.", projects=both)
    if not pins and not left_out:
        return pins, left_out
    from gateway.routes.projects.tree import grantable_project_ids

    seen = await grantable_project_ids(user)
    unseen = [p for p in (*pins, *left_out) if p not in seen]
    if unseen:
        raise _box_refusal(
            422, agent_tokens.CHANGE_PROJECT_NOT_SEEN,
            "You can only choose a project that you can see.", projects=unseen)
    return pins, left_out


async def _own_box(org: str, email: str, connection_id: str) -> agent_tokens.ConnectionRow:
    """The live box connection *connection_id* of the member, else 404.

    The read names the member, so a box of another member, an OAuth
    connection, a revoked one and an unknown id are all 404.
    """
    for row in await agent_tokens.list_connections(org, member_email=email):
        if row.id == connection_id and row.credential == agent_tokens.BOX:
            return row
    raise _not_found()


#: The words of each refusal of the store, by its reason.
_BOX_REFUSALS: dict[str, tuple[int, str]] = {
    agent_tokens.BOX_OFF: (404, "Not Found"),
    agent_tokens.ORG_NOT_ALLOWED: (
        403, "Your organization has not turned on agent connections yet."),
    agent_tokens.BOX_MEMBER_FULL: (
        409, "You hold the most box tokens you may. Revoke one first."),
    agent_tokens.BOX_ORG_FULL: (
        409, "Your organization holds the most box tokens it may."),
    agent_tokens.BOX_NOT_FOUND: (404, "No such agent connection."),
}


def _store_refusal(refused: agent_tokens.BoxRefused) -> HTTPException:
    status, words = _BOX_REFUSALS.get(refused.reason, (409, "The box token was refused."))
    if status == 404:
        return HTTPException(status_code=404, detail=words)
    error = ORG_NOT_ENABLED if refused.reason == agent_tokens.ORG_NOT_ALLOWED else refused.reason
    return _box_refusal(status, error, words)


def _audit_box(actor_email: str, org: str, action: str, target: str,
               payload: dict[str, Any]) -> None:
    """One audit row of a box act. Ids and counts only, never a token."""
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{actor_email}", action=action, target=target,
            payload=payload, organization_id=org))
    except Exception as exc:  # an audit backend that is down must not block this
        _log.warning("agent_access.audit_failed", error=type(exc).__name__)


def _issued(made: agent_tokens.BoxToken, name: str) -> JSONResponse:
    """The ONE answer that holds the raw token. No cache may keep it."""
    return JSONResponse(
        {
            "token": made.token,
            "connection_id": made.connection_id,
            "name": name,
            "scopes": [s for s in made.scope.split(" ") if s],
            "expires_at": made.expires_at.isoformat(),
        },
        headers=_NO_STORE,
    )


@router.post("/box-tokens/check", dependencies=[Depends(_require_box_tokens)])
async def check_box_token(
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Check a mint or a rotation, and give the one-time ``box-mint`` ticket.

    A body that names ``connection_id`` is the check of a rotation
    (:class:`BoxRotateCheck`). Each other body is the check of a mint
    (:class:`BoxMintFields`). The check runs every rule of the act that it
    names, and writes nothing but the ticket. The ticket binds the member,
    the organization and each field of the body, for 300 seconds.
    """
    email = _box_person(user)
    org = str(current_tenant())
    raw = await _capped_body(request, _MAX_BOX_BODY)
    try:
        peek = json.loads(raw)
    except ValueError:
        peek = None
    if isinstance(peek, dict) and "connection_id" in peek:
        rotate = _validated(BoxRotateCheck, raw)
        connection_id = str(rotate.connection_id)
        await _own_box(org, email, connection_id)
        ticket = await _mint_ticket(
            _rotate_binding(connection_id, rotate, email, org),
            namespace=_BOX_TICKET_NAMESPACE)
        return JSONResponse(
            {"ticket": ticket, "connection_id": connection_id,
             "days": rotate.days, "now": rotate.now},
            headers=_NO_STORE)
    mint = _validated(BoxMintFields, raw)
    scopes = _box_scopes(mint.scopes)
    await _box_reach(user, mint)
    ticket = await _mint_ticket(
        _mint_binding(mint, email, org), namespace=_BOX_TICKET_NAMESPACE)
    return JSONResponse(
        {"ticket": ticket, "name": mint.name, "scopes": list(scopes), "days": mint.days},
        headers=_NO_STORE)


@router.post("/box-tokens", dependencies=[Depends(_require_box_tokens)])
async def mint_box_token(
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Make a box token for the member of the session (rule 15).

    The checks run in this order, and each refusal writes nothing:

    1. The flag ``agent_box_tokens_enabled``, else 404.
    2. A person of an organization (:func:`_member`), else 403. A signed
       agent gets 404. The organization on the allowlist, else 403.
    3. The body (:class:`BoxMintRequest`), else 413 or 422.
    4. The ``box-mint`` ticket, spent with one ``GETDEL`` BEFORE each other
       check of the body. It must bind this member, this organization and
       each field of the body, else 400.
    5. The scopes by the consent rule (:func:`_box_scopes`), the reach by
       §10.8 (:func:`_box_reach`), then the store with its caps.

    The answer holds the raw token ONCE, with ``Cache-Control: no-store``.
    No log line and no audit row holds it.
    """
    email = _box_person(user)
    org = str(current_tenant())
    req = _validated(BoxMintRequest, await _capped_body(request, _MAX_BOX_BODY))
    if not await _take_ticket(
            req.ticket, _mint_binding(req, email, org), namespace=_BOX_TICKET_NAMESPACE):
        raise _no_ticket()
    scopes = _box_scopes(req.scopes)
    pins, left_out = await _box_reach(user, req)
    made = await agent_tokens.create_box_connection(
        email,
        req.name,
        scopes=scopes,
        days=req.days,
        pinned_projects=pins,
        include_personal=req.include_personal,
        left_out_projects=left_out,
    )
    if isinstance(made, agent_tokens.BoxRefused):
        raise _store_refusal(made)
    _log.info("agent_access.box_token_created", connection_id=made.connection_id,
              scopes=made.scope, days=req.days)
    _audit_box(email, org, BOX_MINT_ACTION, f"agent_connection:{made.connection_id}",
               {"scopes": list(scopes), "days": req.days, "pinned": len(pins),
                "left_out": len(left_out), "include_personal": req.include_personal})
    return _issued(made, req.name)


@router.post(
    "/box-tokens/{connection_id}/rotate", dependencies=[Depends(_require_box_tokens)])
async def rotate_my_box_token(
    connection_id: UUID,
    request: Request,
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Give one of the member's own box connections a new token (rule 15).

    A rotation hands out a new secret, so it has every control of a mint:
    the flag, a person, the allowlist, and a ``box-mint`` ticket that binds
    this connection id, ``days`` and ``now``. The store takes the connection
    from the member's own rows only, so a box of another member is 404.
    """
    email = _box_person(user)
    org = str(current_tenant())
    req = _validated(BoxRotateRequest, await _capped_body(request, _MAX_BOX_BODY))
    target = str(connection_id)
    if not await _take_ticket(
            req.ticket, _rotate_binding(target, req, email, org),
            namespace=_BOX_TICKET_NAMESPACE):
        raise _no_ticket()
    # Read the name first, so a revoke that lands between the two reads can
    # never lose a token that the store already made.
    row = await _own_box(org, email, target)
    made = await agent_tokens.rotate_box_token(
        org, target, member_email=email, now=req.now, days=req.days)
    if isinstance(made, agent_tokens.BoxRefused):
        raise _store_refusal(made)
    _log.info("agent_access.box_token_rotated", connection_id=made.connection_id,
              now=req.now, days=req.days)
    _audit_box(email, org, BOX_ROTATE_ACTION, f"agent_connection:{made.connection_id}",
               {"days": req.days, "now": req.now})
    return _issued(made, row.client_name)


@router.post(
    "/box-tokens/revoke-all",
    dependencies=[require_feature_router("integrations"),
                  require_permission(MANAGE_PERMISSION)],
)
async def revoke_all_box_tokens(
    user: UserContext = Depends(get_current_user),
) -> JSONResponse:
    """Revoke every box token of the organization, for an admin (rule 15).

    The gates of ``routes/integrations_agents.py``: ``feature:integrations``
    and ``integrations:manage``. It stays live with the box flag off, so an
    admin revokes before an operator turns the flag on again (spec §10.9
    question 5). The reason is ``flag_off``. Each revoked connection writes
    the revoke audit row of an admin. The answer is the count.
    """
    email = _member(user)
    if user.agent is not None:
        raise _not_found()
    org = str(current_tenant())
    ids = await agent_tokens.revoke_box_connections(
        org, reason=agent_tokens.REVOKE_FLAG_OFF)
    for connection_id in ids:
        audit_revoke(email, org, connection_id, admin=True)
    _audit_box(email, org, BOX_REVOKE_ALL_ACTION, f"organization:{org}",
               {"count": len(ids)})
    return JSONResponse({"revoked": len(ids)}, headers=_NO_STORE)
