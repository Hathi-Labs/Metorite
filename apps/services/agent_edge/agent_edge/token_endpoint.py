"""``POST /oauth/token`` and ``POST /oauth/revoke`` — the public half (WS-55 EA-2a).

Spec ``project-docs/specs/external_agent_access.md`` §5.3, §5.5, §10 and §15
EA-2a. The store is ``acb_auth.agent_tokens``. The shared rules are
``acb_auth.agent_oauth``. This module adds the HTTP shape and nothing else.

## The rules that are easy to get wrong

1. **A parameter in the URL is refused.** The token and the revoke request
   carry their parameters in a form body only (RFC 6749 §3.2). A query string
   of any kind is ``invalid_request``, so a code or a token never reaches an
   access log. The app also refuses ``access_token`` in the URL of any route.
2. **Every refusal of a grant is ``invalid_grant``.** An unknown, spent,
   expired or revoked code, a wrong client, redirect URI, resource or PKCE
   verifier, and every ``Refused`` of the store answer the same body. The
   reason goes to the log only. ``invalid_client`` is for a missing or unknown
   client, ``invalid_request`` for malformed input, ``unsupported_grant_type``
   for any other grant.
3. **The checks are part of the spend** (fix round 1). :func:`consume_code`
   is one ``UPDATE ... WHERE used_at IS NULL`` that also matches the client,
   the redirect URI, the S256 of the verifier and the resource. So two
   callers cannot both win, and a presentation that fails any check spends
   nothing. A thief who intercepted a code cannot burn it without the
   verifier, and the real client can still use it.
4. **A replayed code revokes its connection** (OAuth 2.1 §4.1.2). When
   :func:`consume_code` finds nothing, :func:`revoke_replayed_code` revokes the
   connection of a code that was already spent, when this presentation
   proves the same four values. It acts only inside
   ``agent_code_replay_window_s`` after the code expired.
5. **``issue_tokens`` runs once for each spent code.**
6. **No token, code or hash goes into a log line.** Ids and reasons only.
7. **No rate limit here.** EA-7 owns the limits of ``token`` and ``revoke``.
   The body size is capped (``agent_oauth_max_body_bytes``, a 413).
8. **No control character in a form field** (fix round 1). A NUL, any other
   C0 character or DEL in a key or a value is ``invalid_request``. Postgres
   refuses a NUL in ``text``, so without this check one byte was a 500.

Fence: ``tests/unit/test_agent_oauth_server.py``.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from acb_auth import agent_oauth, agent_tokens
from acb_auth.agent_tokens import IssuedTokens, Refused
from acb_common import get_logger, get_settings
from fastapi import Request
from fastapi.responses import JSONResponse, Response

_log = get_logger("agent_edge.token")

AUTHORIZATION_CODE = "authorization_code"
REFRESH_TOKEN = "refresh_token"

#: The longest code or token the edge reads. A real one is 43 characters.
_MAX_TOKEN_CHARS = 512

#: The most form fields one request may carry.
_MAX_FIELDS = 32

#: RFC 6749 §5.1: a token response must not be cached.
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_FORM = "application/x-www-form-urlencoded"


class OAuthError(Exception):
    """An OAuth error response (RFC 6749 §5.2). The body names the error only."""

    def __init__(self, error: str, description: str = "", status: int = 400) -> None:
        super().__init__(error)
        self.error = error
        self.description = description
        self.status = status


def error_response(exc: OAuthError) -> JSONResponse:
    body: dict[str, str] = {"error": exc.error}
    if exc.description:
        body["error_description"] = exc.description
    return JSONResponse(body, status_code=exc.status, headers=NO_STORE)


def _invalid_request(description: str) -> OAuthError:
    return OAuthError("invalid_request", description)


def _invalid_client() -> OAuthError:
    return OAuthError("invalid_client", "unknown client", status=401)


def _invalid_grant() -> OAuthError:
    # One body for every case, on purpose (rule 2).
    return OAuthError("invalid_grant", "the grant is not valid")


def _client_host(client_id: str) -> str:
    try:
        return urlsplit(client_id).hostname or ""
    except ValueError:
        return ""


# ── The form body ───────────────────────────────────────────────────────────


async def _read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                raise OAuthError("invalid_request", "the body is too large", 413)
        except ValueError:
            raise _invalid_request("bad content-length") from None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > cap:
            raise OAuthError("invalid_request", "the body is too large", 413)
    return bytes(body)


async def read_form(request: Request) -> dict[str, str]:
    """The form fields of a token or revoke request, or raise :class:`OAuthError`.

    Refuses a query string (rule 1), an ``Authorization`` header (this server
    serves public clients only), another content type, a body over the cap, a
    body that is not UTF-8, and a field sent twice (RFC 6749 §3.2).
    """
    if request.url.query:
        raise _invalid_request("send the parameters in the body, never in the URL")
    if request.headers.get("authorization"):
        raise OAuthError(
            "invalid_client", "this server serves public clients only", 401)
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != _FORM:
        raise _invalid_request(f"use {_FORM}")
    raw = await _read_capped(request, int(get_settings().agent_oauth_max_body_bytes))
    try:
        text = raw.decode("utf-8")
        pairs = parse_qsl(
            text, keep_blank_values=True, max_num_fields=_MAX_FIELDS,
            errors="strict")
    except (UnicodeDecodeError, ValueError):
        raise _invalid_request("the body is not a valid form") from None
    form: dict[str, str] = {}
    for key, value in pairs:
        if has_control_character(key) or has_control_character(value):
            raise _invalid_request("a field holds a control character")
        if key in form:
            raise _invalid_request(f"{key[:40]} is sent more than once")
        form[key] = value
    return form


def has_control_character(value: str) -> bool:
    """True when *value* holds a C0 control character or DEL (rule 8)."""
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def _field(form: dict[str, str], name: str, *, limit: int) -> str:
    """A required field, present, not empty and not over *limit* characters."""
    value = form.get(name)
    if not value:
        raise _invalid_request(f"{name} is missing")
    if len(value) > limit:
        raise _invalid_request(f"{name} is too long")
    return value


# ── The token endpoint ──────────────────────────────────────────────────────


async def token(request: Request) -> Response:
    """``POST /oauth/token`` for ``authorization_code`` and ``refresh_token``."""
    try:
        form = await read_form(request)
        grant_type = form.get("grant_type")
        if not grant_type:
            raise _invalid_request("grant_type is missing")
        if grant_type not in (AUTHORIZATION_CODE, REFRESH_TOKEN):
            raise OAuthError("unsupported_grant_type")
        client_id = await _known_client(form)
        if grant_type == AUTHORIZATION_CODE:
            issued = await _exchange_code(form, client_id)
        else:
            issued = await _refresh(form, client_id)
    except OAuthError as exc:
        return error_response(exc)
    return _token_response(issued)


async def _known_client(form: dict[str, str]) -> str:
    """The ``client_id`` of the request, when the server has seen that client.

    A client becomes known when a member approves it and the gateway caches
    its metadata in ``agent_client``. The row stays after its cache expires,
    so a refresh 30 days later still finds it.
    """
    client_id = form.get("client_id") or ""
    if not client_id or len(client_id) > agent_oauth.MAX_URL_CHARS:
        raise _invalid_client()
    if await agent_tokens.get_client(client_id) is None:
        raise _invalid_client()
    return client_id


def _refuse(reason: str, client_id: str, connection_id: str | None = None) -> OAuthError:
    """Log why a grant was refused, and return the one ``invalid_grant``."""
    _log.info(
        "agent_edge.grant_refused",
        reason=reason,
        client_host=_client_host(client_id),
        connection_id=connection_id,
    )
    return _invalid_grant()


async def _exchange_code(form: dict[str, str], client_id: str) -> IssuedTokens:
    """Spend a code that this request proves, then issue the first tokens.

    Rules 3, 4 and 5. The proof is the client, the redirect URI, the S256 of
    the verifier and the resource. The store matches all four inside the
    spend, so a request that fails any of them spends nothing.
    """
    code = _field(form, "code", limit=_MAX_TOKEN_CHARS)
    redirect_uri = _field(form, "redirect_uri", limit=agent_oauth.MAX_URL_CHARS)
    resource = _field(form, "resource", limit=agent_oauth.MAX_URL_CHARS)
    verifier = _field(form, "code_verifier", limit=128)
    if not agent_oauth.is_valid_verifier(verifier):
        raise _invalid_request("code_verifier is malformed")
    proof = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": agent_oauth.s256(verifier),
        "resource": resource,
    }

    grant = await agent_tokens.consume_code(code, **proof)
    if grant is None:
        replayed = await agent_tokens.revoke_replayed_code(code, **proof)
        raise _refuse(
            "code_replayed" if replayed else "code_invalid", client_id, replayed)

    scopes = agent_oauth.parse_scope(grant.scope)
    issued = await agent_tokens.issue_tokens(
        grant, with_refresh=agent_oauth.OFFLINE_ACCESS in scopes)
    if isinstance(issued, Refused):
        raise _refuse(issued.reason, client_id, issued.connection_id)
    _log.info(
        "agent_edge.tokens_issued",
        grant_type=AUTHORIZATION_CODE,
        client_host=_client_host(client_id),
        connection_id=issued.connection_id,
    )
    return issued


async def _refresh(form: dict[str, str], client_id: str) -> IssuedTokens:
    """Rotate a refresh token. ``resource`` may be left out (RFC 8707)."""
    raw = _field(form, "refresh_token", limit=_MAX_TOKEN_CHARS)
    resource: str | None = None
    if "resource" in form:
        resource = _field(form, "resource", limit=agent_oauth.MAX_URL_CHARS)
    if "scope" in form:
        await _check_refresh_scope(raw, form["scope"])

    result = await agent_tokens.rotate_refresh(raw, client_id=client_id, resource=resource)
    if isinstance(result, Refused):
        raise _refuse(result.reason, client_id, result.connection_id)
    _log.info(
        "agent_edge.tokens_issued",
        grant_type=REFRESH_TOKEN,
        client_host=_client_host(client_id),
        connection_id=result.connection_id,
    )
    return result


async def _check_refresh_scope(raw: str, requested: str) -> None:
    """Refuse a refresh that asks for a scope the connection does not hold.

    v1 issues the scope the member granted and does not narrow it on refresh.
    The response names that scope (RFC 6749 §5.1). The check runs BEFORE the
    rotation, so a refused request spends nothing.
    """
    tokens = [t for t in requested.split(" ") if t]
    if len(requested) > agent_oauth.MAX_SCOPE_CHARS or any(
        t not in agent_oauth.SUPPORTED_SCOPES for t in tokens
    ):
        raise OAuthError("invalid_scope")
    found = await agent_tokens.lookup_token(raw, kind=agent_tokens.REFRESH)
    if found is None:
        return  # the rotation decides, and it detects a reuse
    held = set(agent_oauth.parse_scope(found.scope))
    if not set(tokens) <= held:
        raise OAuthError("invalid_scope")


def _token_response(issued: IssuedTokens) -> JSONResponse:
    body: dict[str, Any] = {
        "access_token": issued.access_token,
        "token_type": "Bearer",
        "expires_in": issued.expires_in,
        "scope": issued.scope or "",
    }
    if issued.refresh_token:
        body["refresh_token"] = issued.refresh_token
    return JSONResponse(body, headers=NO_STORE)


# ── The revocation endpoint (RFC 7009) ──────────────────────────────────────


async def revoke(request: Request) -> Response:
    """``POST /oauth/revoke``. 200 for an unknown token too (RFC 7009 §2.2).

    A token of the presenting client revokes its whole connection: the client
    disconnects, and every access and refresh token of it stops working
    (RFC 7009 §2.1 lets a server revoke the related tokens). A token of
    another client changes nothing, and the answer is the same 200.
    """
    try:
        form = await read_form(request)
        raw = _field(form, "token", limit=_MAX_TOKEN_CHARS * 4)
        client_id = form.get("client_id") or ""
        if not client_id or len(client_id) > agent_oauth.MAX_URL_CHARS:
            raise _invalid_client()
    except OAuthError as exc:
        return error_response(exc)

    if len(raw) <= _MAX_TOKEN_CHARS:
        kinds = (agent_tokens.REFRESH, agent_tokens.ACCESS)
        if form.get("token_type_hint") == "access_token":
            kinds = (agent_tokens.ACCESS, agent_tokens.REFRESH)
        for kind in kinds:
            found = await agent_tokens.lookup_token(raw, kind=kind)
            if found is None:
                continue
            if found.client_id == client_id:
                await agent_tokens.revoke_connection(
                    found.organization_id, found.connection_id)
                _log.info(
                    "agent_edge.revoked",
                    client_host=_client_host(client_id),
                    connection_id=found.connection_id,
                )
            break
    return Response(status_code=200, headers=NO_STORE)
