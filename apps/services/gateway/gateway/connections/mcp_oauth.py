"""MCP OAuth for an org connection: the protocol half (WS-54 IN-8a, D98).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.3 to §3.6.8. The
routes are ``gateway/routes/integrations_mcp_oauth.py``. This module holds no
route, opens no database session and reads no request.

* **Discovery** (§3.6.4, start steps 1 to 3). A GET to the MCP URL with no
  token, then the protected-resource metadata (RFC 9728), then the auth-server
  metadata (RFC 8414), path-inserted form first. With no protected-resource
  metadata, the auth server is the origin of the MCP URL (MCP 2025-03-26).
* **Registration** (§3.6.3), in the order of the MCP text: a pre-registered
  client (IN-8d, none yet), then CIMD, then DCR. Else ``no_registration``.
  CIMD only with a server that says it sends ``iss`` (RFC 9207), because
  every CIMD flow shares one client id and one redirect URI.
* **PKCE** is S256 only, also when the server offers ``plain``.
* **Every fetch** goes through :func:`gateway.outbound_guard.request_https`:
  ``https`` on port 443, a public address, no redirect, 64 KiB and 10 s
  (§3.6.5). ⚠️ This module never calls ``outbound_guard.request``,
  ``outbound_guard.mcp_client`` or ``httpx`` itself. The AST fence of
  ``tests/unit/test_mcp_oauth_guard.py`` fails when it does.
* **The library** (§3.6.8). Only the pure helpers of ``mcp.client.auth.utils``
  and the pydantic models of ``mcp.shared.auth``. No helper opens a socket.
  ``tests/unit/test_mcp_oauth_contract.py`` pins the names.
* **No secret out.** A token, a code, a verifier and a client secret never
  reach a log line or an :class:`OAuthFailed`. A failure carries a short code.
"""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from acb_auth.agent_oauth import s256
from acb_common import get_logger, get_settings
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    is_valid_client_metadata_url,
    issuers_match,
    should_use_client_metadata_url,
    validate_metadata_issuer,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import ValidationError

from gateway import outbound_guard
from gateway.crm_sources.base import SourceCredential

_log = get_logger("connections.mcp_oauth")

#: The purpose of the signed state (``routes/email/transport/signing.py``).
STATE_PURPOSE = "integrations.mcp.connect"
#: The public path of the callback, on the workbench origin. The BFF route
#: ``api/integrations/mcp/oauth/callback`` forwards it to the gateway.
REDIRECT_PATH = "/api/integrations/mcp/oauth/callback"
#: The public path of the CIMD document, on the workbench origin.
CLIENT_METADATA_PATH = "/oauth/mcp-client.json"
#: The name that a consent screen shows.
CLIENT_NAME = "Metorite"

#: The registration kinds that ``provider_meta.registration`` holds.
REG_PRE_REGISTERED = "pre_registered"
REG_CIMD = "cimd"
REG_DCR = "dcr"

#: The token endpoint auth methods that this client can use.
AUTH_NONE = "none"
AUTH_POST = "client_secret_post"
AUTH_BASIC = "client_secret_basic"

#: The MCP protocol version header of a metadata request.
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_PROTOCOL_VERSION = "2025-11-25"
#: HubSpot answers 406 to a GET that does not also accept an event stream.
_PROBE_ACCEPT = "application/json, text/event-stream"

#: When a token answer has no ``expires_in``, the token does not expire by
#: itself. The connection stores a far date, and a 401 at run time is the
#: signal (IN-8b).
_NO_EXPIRY = timedelta(days=3650)

#: The longest code that a failure carries.
_CODE_CHARS = 40


def oauth_enabled() -> bool:
    """``MCP_OAUTH_ENABLED``, default off. The ONE reader of the flag."""
    return getattr(get_settings(), "mcp_oauth_enabled", False) is True


class OAuthFailed(Exception):
    """A step of the flow failed. ``code`` is short and holds no secret."""

    def __init__(self, code: str) -> None:
        self.code = safe_code(code, "failed")
        super().__init__(self.code)


def safe_code(code: Any, default: str) -> str:
    """*code* when it looks like an OAuth error code, else *default*."""
    value = str(code or "").strip()
    if value and len(value) <= _CODE_CHARS and value.replace("_", "").isalnum():
        return value.lower()
    return default


# ── Public URLs ─────────────────────────────────────────────────────────────


def _workbench() -> str:
    # Lazy: the email package imports much more than this module needs.
    from gateway.routes.email.transport.oauth import _workbench_public_url

    return _workbench_public_url()


def redirect_uri() -> str:
    """The one redirect URI of every consent, and of the CIMD document."""
    return f"{_workbench()}{REDIRECT_PATH}"


def client_metadata_url() -> str:
    """The URL of the CIMD document. It is the ``client_id`` of a CIMD client."""
    return f"{_workbench()}{CLIENT_METADATA_PATH}"


def client_metadata_document() -> dict[str, Any]:
    """The CIMD document (§3.6.3). ``client_id`` equals its own URL."""
    return {
        "client_id": client_metadata_url(),
        "client_name": CLIENT_NAME,
        "client_uri": _workbench(),
        "redirect_uris": [redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": AUTH_NONE,
    }


# ── The guard ───────────────────────────────────────────────────────────────


async def _fetch(
    method: str, url: str, *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    json_body: Any = None,
) -> outbound_guard.Answer:
    """ONE hop, through the https-only mode of the guard. A refusal is
    :class:`OAuthFailed` ``refused``, and a transport fault is ``unreachable``.
    The log line names the host and the reason, never the path or the query.

    ⚠️ ``Accept-Encoding: identity`` on EVERY hop (review of #931, P1). The
    guard reads the raw bytes and never decompresses them, so a gzip answer
    would never parse as JSON. ``agent_access.py`` states the same rule."""
    sent = {**(headers or {}), "Accept-Encoding": "identity"}
    try:
        return await outbound_guard.request_https(
            method, url, headers=sent, content=content, json=json_body)
    except outbound_guard.OutboundRefused as exc:
        _log.warning("mcp.oauth_refused", host=exc.host, reason=exc.reason)
        raise OAuthFailed("refused") from None
    except Exception as exc:  # a transport fault: the class only
        _log.warning("mcp.oauth_unreachable", host=_host(url), error=type(exc).__name__)
        raise OAuthFailed("unreachable") from None


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "")[:100]
    except ValueError:
        return ""


def _json(answer: outbound_guard.Answer) -> dict[str, Any] | None:
    try:
        data = json.loads(answer.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _https_443(url: Any) -> str:
    """*url* as a string when it is ``https`` on port 443 with a host, else
    :class:`OAuthFailed` ``insecure_endpoint``. No DNS: the guard checks the
    address when the gateway fetches it. The consent URL is never fetched, so
    this check is all that it gets."""
    raw = str(url or "")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise OAuthFailed("insecure_endpoint") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or (port or 443) != 443 or "\\" in raw):
        raise OAuthFailed("insecure_endpoint")
    return raw


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _same_resource(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


# ── Discovery (§3.6.4, start steps 1 to 3) ──────────────────────────────────


@dataclass(frozen=True)
class Plan:
    """What discovery learned about one MCP server. No secret."""

    server_url: str
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    revocation_endpoint: str | None
    #: The server says that it sends ``iss`` on the redirect (RFC 9207).
    iss_required: bool
    scope: str | None
    cimd_supported: bool
    token_auth_methods: tuple[str, ...]


async def _probe(server_url: str) -> tuple[str | None, str | None]:
    """The ``resource_metadata`` URL and the scope of the 401, if any."""
    answer = await _fetch("GET", server_url, headers={
        "Accept": _PROBE_ACCEPT, _PROTOCOL_HEADER: _PROTOCOL_VERSION,
    })
    # The two helpers read ``status_code`` and ``headers`` only, and the
    # answer of the guard has both (the contract test pins this).
    return (extract_resource_metadata_from_www_auth(answer),  # type: ignore[arg-type]
            extract_scope_from_www_auth(answer))  # type: ignore[arg-type]


async def _metadata(url: str) -> dict[str, Any] | None:
    """One metadata document, or ``None`` when this URL does not serve one.

    A 4xx tries the next URL. A 5xx stops the flow. A refusal of the guard
    (a private address, ``http``, a port, a redirect) stops the flow too."""
    answer = await _fetch("GET", url, headers={
        "Accept": "application/json", _PROTOCOL_HEADER: _PROTOCOL_VERSION,
    })
    if answer.status_code >= 500:
        raise OAuthFailed("discovery_failed")
    if answer.status_code != 200:
        return None
    return _json(answer)


async def _protected_resource(
    server_url: str, header_url: str | None,
) -> tuple[ProtectedResourceMetadata, dict[str, Any]] | None:
    for url in build_protected_resource_metadata_discovery_urls(header_url, server_url):
        data = await _metadata(_https_443(url))
        if data is None:
            continue
        try:
            return ProtectedResourceMetadata.model_validate(data), data
        except ValidationError:
            continue
    return None


async def _auth_server(
    auth_server_url: str | None, server_url: str, expected_issuer: str,
) -> tuple[OAuthMetadata, dict[str, Any]]:
    for url in build_oauth_authorization_server_metadata_discovery_urls(
            auth_server_url, server_url):
        data = await _metadata(_https_443(url))
        if data is None:
            continue
        try:
            metadata = OAuthMetadata.model_validate(data)
        except ValidationError:
            continue
        try:
            validate_metadata_issuer(metadata, expected_issuer)
        except OAuthFlowError:
            raise OAuthFailed("issuer_mismatch") from None
        return metadata, data
    raise OAuthFailed("no_auth_server")


async def discover(server_url: str) -> Plan:
    """Discovery, steps 1 to 3 of the start. :class:`OAuthFailed` on any fault."""
    server_url = _https_443(server_url)
    header_url, header_scope = await _probe(server_url)
    found = await _protected_resource(server_url, header_url)
    if found is not None:
        prm, prm_raw = found
        resource = str(prm_raw.get("resource") or "")
        if not (_same_resource(resource, server_url)
                or _same_resource(resource, _origin(server_url))):
            raise OAuthFailed("resource_mismatch")
        auth_server = str(prm_raw.get("authorization_servers", [""])[0] or "")
        _https_443(auth_server)
        scope = header_scope
        if scope is None and prm.scopes_supported:
            scope = " ".join(prm.scopes_supported)
    else:
        # The fallback of MCP 2025-03-26: the auth server is the origin of the
        # MCP URL. The metadata must then sit on that origin, and the issuer
        # must be that origin.
        resource = server_url
        auth_server = _origin(server_url)
        scope = header_scope
    metadata, raw = await _auth_server(
        auth_server if found is not None else None, server_url, auth_server)
    methods = tuple(metadata.code_challenge_methods_supported or ())
    if "S256" not in methods:
        # MCP 2025-11-25: a client refuses an auth server that does not say
        # that it supports S256.
        raise OAuthFailed("pkce_unsupported")
    registration = metadata.registration_endpoint
    revocation = metadata.revocation_endpoint
    return Plan(
        server_url=server_url,
        resource=resource,
        issuer=str(raw.get("issuer") or auth_server),
        authorization_endpoint=_https_443(raw.get("authorization_endpoint")),
        token_endpoint=_https_443(raw.get("token_endpoint")),
        registration_endpoint=(
            _https_443(raw.get("registration_endpoint")) if registration else None),
        revocation_endpoint=(
            _https_443(raw.get("revocation_endpoint")) if revocation else None),
        iss_required=raw.get("authorization_response_iss_parameter_supported") is True,
        scope=scope or None,
        cimd_supported=metadata.client_id_metadata_document_supported is True,
        # RFC 8414 §2: no list means client_secret_basic.
        token_auth_methods=tuple(
            metadata.token_endpoint_auth_methods_supported or (AUTH_BASIC,)),
    )


# ── Registration (§3.6.3) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Client:
    """The client of one consent. ``secret`` is a DCR secret, or empty."""

    client_id: str
    kind: str
    token_auth: str
    secret: str = field(default="", repr=False)


def _pre_registered(plan: Plan) -> Client | None:
    """Metorite's own client for this auth server (IN-8d). None yet.

    ⚠️ IN-8d is an OWNER-GATE. The owner creates each OAuth app, and its id
    and secret live in the operator plane (D56), never in a customer row."""
    del plan
    return None


def _dcr_auth_method(plan: Plan) -> str:
    offered = set(plan.token_auth_methods)
    for method in (AUTH_NONE, AUTH_POST, AUTH_BASIC):
        if method in offered:
            return method
    raise OAuthFailed("no_registration")


async def _register_dcr(plan: Plan) -> Client:
    assert plan.registration_endpoint is not None
    method = _dcr_auth_method(plan)
    body: dict[str, Any] = {
        "redirect_uris": [redirect_uri()],
        "client_name": CLIENT_NAME,
        "client_uri": _workbench(),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
    }
    if plan.scope:
        body["scope"] = plan.scope
    answer = await _fetch("POST", plan.registration_endpoint, headers={
        "Content-Type": "application/json", "Accept": "application/json",
    }, json_body=body)
    data = _json(answer) if answer.status_code in (200, 201) else None
    if data is None:
        raise OAuthFailed("registration_failed")
    # The SDK drops ``issuer`` from the wire before it parses, and so does
    # this module (``handle_registration_response``).
    data.pop("issuer", None)
    try:
        info = OAuthClientInformationFull.model_validate(data)
    except ValidationError:
        raise OAuthFailed("registration_failed") from None
    if not info.client_id:
        raise OAuthFailed("registration_failed")
    token_auth = info.token_endpoint_auth_method or method
    if token_auth not in (AUTH_NONE, AUTH_POST, AUTH_BASIC):
        raise OAuthFailed("registration_failed")
    secret = info.client_secret or ""
    if token_auth != AUTH_NONE and not secret:
        raise OAuthFailed("registration_failed")
    return Client(client_id=info.client_id, kind=REG_DCR, token_auth=token_auth, secret=secret)


async def register(plan: Plan) -> Client:
    """The client for *plan*: pre-registered, then CIMD, then DCR."""
    pre = _pre_registered(plan)
    if pre is not None:
        return pre
    url = client_metadata_url()
    # Review of #931 (the mix-up residual). Every CIMD flow shares ONE
    # client_id and ONE redirect URI, so ``iss`` is the only defence against
    # an auth-server mix-up. So CIMD only with a server that sends ``iss``.
    # Else DCR, which gives a client_id per auth server.
    if plan.cimd_supported and plan.iss_required and is_valid_client_metadata_url(url):
        # ``should_use_client_metadata_url`` reads the one flag of the model.
        model = OAuthMetadata.model_construct(client_id_metadata_document_supported=True)
        if should_use_client_metadata_url(model, url):
            return Client(client_id=url, kind=REG_CIMD, token_auth=AUTH_NONE)
    if plan.registration_endpoint:
        return await _register_dcr(plan)
    raise OAuthFailed("no_registration")


# ── PKCE and the consent URL (§3.6.4, start steps 5 and 8) ──────────────────


def new_verifier() -> str:
    """A PKCE verifier of 64 unreserved characters (RFC 7636 §4.1)."""
    return secrets.token_urlsafe(48)


def consent_url(plan: Plan, client: Client, *, state: str, verifier: str) -> str:
    """The URL that the admin's browser opens. S256 only, never ``plain``."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": s256(verifier),
        "code_challenge_method": "S256",
        "resource": plan.resource,
    }
    if plan.scope:
        params["scope"] = plan.scope
    base = _https_443(plan.authorization_endpoint)
    joiner = "&" if urlsplit(base).query else "?"
    return f"{base}{joiner}{urlencode(params)}"


# ── The token endpoint (§3.6.4, callback step 5) ────────────────────────────


@dataclass(frozen=True)
class Pending:
    """The pending flow of one consent, as the callback reads it back."""

    name: str
    member: str
    server_url: str
    resource: str
    issuer: str
    iss_required: bool
    token_endpoint: str
    revocation_endpoint: str | None
    client_id: str
    registration: str
    token_auth: str
    scope: str | None
    verifier: str = field(repr=False)
    client_secret: str = field(default="", repr=False)


def _client_auth(
    token_auth: str, client_id: str, secret: str, form: dict[str, str],
) -> dict[str, str]:
    """Add the client to *form*, and return the extra headers."""
    if token_auth == AUTH_BASIC:
        pair = f"{quote(client_id, safe='')}:{quote(secret, safe='')}"
        return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode("ascii")}
    form["client_id"] = client_id
    if token_auth == AUTH_POST:
        form["client_secret"] = secret
    return {}


def public_meta(pending: Pending) -> dict[str, str]:
    """The public facts of a connection (§3.6.6). No token, no secret.

    ⚠️ The store copies this map into the PLAIN ``provider_meta`` column."""
    meta = {
        "issuer": pending.issuer,
        "token_endpoint": pending.token_endpoint,
        "client_id": pending.client_id,
        "registration": pending.registration,
        "token_auth": pending.token_auth,
        "resource": pending.resource,
    }
    if pending.revocation_endpoint:
        meta["revocation_endpoint"] = pending.revocation_endpoint
    return meta


def _lifetime(expires_in: Any) -> timedelta:
    """The life of a token from ``expires_in`` (review of #931, P2 4).

    No value means the token does not expire by itself: :data:`_NO_EXPIRY`.
    ``0`` means it has ended already, not "never". A value above ten years is
    cut to ten years, so a hostile answer cannot overflow the date after the
    code is spent. A negative value, or one that is not an integer, is
    :class:`OAuthFailed` ``exchange_failed``.
    """
    if expires_in is None:
        return _NO_EXPIRY
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in < 0:
        raise OAuthFailed("exchange_failed")
    return timedelta(seconds=min(expires_in, int(_NO_EXPIRY.total_seconds())))


async def exchange_code(
    pending: Pending, code: str, *, now: datetime | None = None,
) -> SourceCredential:
    """The code for tokens. :class:`OAuthFailed` with the provider's code."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": pending.verifier,
        "resource": pending.resource,
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        # GitHub answers in form encoding unless the client asks for JSON.
        "Accept": "application/json",
        **_client_auth(pending.token_auth, pending.client_id, pending.client_secret, form),
    }
    answer = await _fetch(
        "POST", _https_443(pending.token_endpoint), headers=headers,
        content=urlencode(form).encode("ascii"))
    data = _json(answer)
    if not answer.is_success or data is None or "error" in data:
        code_out = safe_code((data or {}).get("error"), "exchange_failed")
        _log.warning("mcp.oauth_exchange_failed", host=_host(pending.token_endpoint),
                     status=answer.status_code, code=code_out)
        raise OAuthFailed(code_out)
    try:
        token = OAuthToken.model_validate(data)
    except ValidationError:
        raise OAuthFailed("exchange_failed") from None
    if not token.access_token:
        raise OAuthFailed("exchange_failed")
    issued = now or datetime.now(UTC)
    expires_at = issued + _lifetime(token.expires_in)
    scope = token.scope if token.scope is not None else (pending.scope or "")
    return SourceCredential(
        access_token=token.access_token,
        refresh_token=token.refresh_token or "",
        expires_at=expires_at,
        scopes=tuple(scope.split()),
        provider_meta=public_meta(pending),
        client_secret=pending.client_secret,
    )


#: The codes of :func:`refresh` that only a new consent repairs. The runtime
#: sets ``needs_reconnect`` for each (``shared_token._STOP_CODES`` holds the
#: provider codes, and these two are this module's own).
NO_REFRESH_TOKEN = "no_refresh_token"
NO_TOKEN_ENDPOINT = "no_token_endpoint"


async def refresh(
    credential: SourceCredential, *, now: datetime | None = None,
) -> SourceCredential:
    """A new access token from the refresh token (RFC 6749 §6, WS-54 IN-8b).

    Spec: ``integrations_console.md`` §3.6.6 and §3.6.7. The hop goes through
    :func:`_fetch`, so through ``request_https``, like every other hop
    (§3.6.5). :class:`OAuthFailed` carries the provider's code, for example
    ``invalid_grant``, and holds no token.

    * **Rotation.** An answer with a new refresh token replaces the old one.
      The store seals it (``store.update_tokens``). An answer with none keeps
      the old one (RFC 6749 §6).
    * **No revoke of the old refresh token.** A provider that rotates ends
      the old token itself, and none of the six providers of §3.6.2 asks a
      client to revoke it. A reconnect and a delete still revoke (§3.6.4).
    * ``provider_meta`` and ``client_secret`` carry over unchanged. The meta
      holds public facts only, because the store copies it to a plain column.
    """
    meta: Mapping[str, str] = credential.provider_meta
    if not credential.refresh_token:
        raise OAuthFailed(NO_REFRESH_TOKEN)
    endpoint = meta.get("token_endpoint")
    if not endpoint:
        raise OAuthFailed(NO_TOKEN_ENDPOINT)
    form = {"grant_type": "refresh_token", "refresh_token": credential.refresh_token}
    if meta.get("resource"):
        form["resource"] = meta["resource"]  # RFC 8707: the same audience
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        **_client_auth(meta.get("token_auth", AUTH_NONE), meta.get("client_id", ""),
                       credential.client_secret, form),
    }
    answer = await _fetch(
        "POST", _https_443(endpoint), headers=headers,
        content=urlencode(form).encode("ascii"))
    data = _json(answer)
    if not answer.is_success or data is None or "error" in data:
        code_out = safe_code((data or {}).get("error"), "refresh_failed")
        _log.warning("mcp.oauth_refresh_refused", host=_host(endpoint),
                     status=answer.status_code, code=code_out)
        raise OAuthFailed(code_out)
    try:
        token = OAuthToken.model_validate(data)
        life = _lifetime(token.expires_in)
    except (ValidationError, OAuthFailed):
        raise OAuthFailed("refresh_failed") from None
    if not token.access_token:
        raise OAuthFailed("refresh_failed")
    issued = now or datetime.now(UTC)
    scopes = tuple(token.scope.split()) if token.scope is not None else credential.scopes
    return SourceCredential(
        access_token=token.access_token,
        refresh_token=token.refresh_token or credential.refresh_token,
        expires_at=issued + life,
        scopes=scopes,
        provider_meta=dict(meta),
        client_secret=credential.client_secret,
    )


async def revoke(credential: SourceCredential) -> str:
    """Revoke the refresh token, best effort. Returns an outcome word only.

    Only a provider that publishes a revoke endpoint gets the call (§3.6.4,
    callback step 7)."""
    meta: Mapping[str, str] = credential.provider_meta
    endpoint = meta.get("revocation_endpoint")
    if not endpoint or not credential.refresh_token:
        return "skipped"
    form = {"token": credential.refresh_token, "token_type_hint": "refresh_token"}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        **_client_auth(meta.get("token_auth", AUTH_NONE), meta.get("client_id", ""),
                       credential.client_secret, form),
    }
    try:
        answer = await _fetch(
            "POST", _https_443(endpoint), headers=headers,
            content=urlencode(form).encode("ascii"))
    except OAuthFailed as exc:
        return f"failed:{exc.code}"
    return "confirmed" if answer.is_success else f"failed:{answer.status_code}"


__all__ = [
    "CLIENT_METADATA_PATH",
    "REDIRECT_PATH",
    "STATE_PURPOSE",
    "Client",
    "OAuthFailed",
    "Pending",
    "Plan",
    "client_metadata_document",
    "client_metadata_url",
    "consent_url",
    "discover",
    "exchange_code",
    "issuers_match",
    "new_verifier",
    "oauth_enabled",
    "public_meta",
    "redirect_uri",
    "refresh",
    "register",
    "revoke",
    "safe_code",
]
