"""API connections: the connect flow of Zoho Books (API-2, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.2, §2.10 and §2.11. The
pure helpers are in ``gateway/connections/api/connect.py``.

    POST  /integrations/api/zoho_books/start
    GET   /integrations/api/zoho/callback
    POST  /integrations/api/zoho_books/pick
    GET   /integrations/api/zoho_books

⚠️ **Dark by org.** The first dependency of the router answers 404 while
``API_CONNECTIONS_ORGS`` does not hold the org of the session
(``acb_common.api_connections_flag``). It runs before the feature gate and the
permission, so a dark route tells nobody that it exists.

**Who may do what.** Each route needs ``integrations:manage``. Only the admin
role holds it (the owner holds ``*``). The row gets ``grant = admin`` in
``provider_meta``. API-3 and API-4 read it.

**The client.** The CRM's Zoho client (``routes/crm/connection.py``) is the
one reader of its settings. This module calls ``zoho_oauth_config`` through
that module, and it reads no setting of its own. The redirect URI is the one
that the owner registered for the CRM. The BFF callback sends a state with the
mark ``zb.`` here, and every other state to the CRM.

**The callback**, in the order of ``routes/crm/connection.py::zoho_callback``:

1. The state, before any other work: the MAC of :data:`STATE_PURPOSE`, the
   org and the member of the session, then the one-use mark in Redis (GETDEL).
   Each refusal is the same 400, and Zoho gets no call.
2. A provider ``error`` or no code: a redirect with the code.
3. The accounts server and the location, checked against
   ``ZOHO_DATA_CENTRES``. A bad one is 400, and Zoho gets no call.
4. The code for tokens (``ZohoSource.finish_consent``). An API domain that is
   not a host of the template is 400, and the new token is revoked.
5. The context read ``GET /books/v3/organizations`` through
   ``outbound_guard.request_https``. No organization: revoke, and fail. One:
   go on. Several: the sealed credential waits in Redis for 600 s under the
   state nonce, and the admin picks one (``POST .../pick``).
6. The test read of ``list_invoices`` with ``per_page=1``, BEFORE the save.
   ``store.save_credential`` always writes ``connected``, so a failed read
   writes no row, keeps an older row as it is, and revokes the new token.
7. The save under the advisory lock ``api.connect:<org>:api:zoho_books``,
   then the revoke of the old refresh token after the commit.

**API-5.** A new start revokes and deletes the admin's own older pending pick
(``_start_again``, gap 1). The pick reads the member of the entry before it
takes the entry (gap 3). The status of a connected row carries the result card
of IN-9 (``acb_common.connector_result_card``).

**Tenancy (R5).** The org and the member come from the server-side
``UserContext``, never from the request. The row is read and written inside
``tenant_session(org)``. The Redis keys carry the org (``TenantRedis``). The
Zoho ``organization_id`` is a context value of the connection, never a
Metorite organization.

**No secret out.** A code, a token and the client secret never reach a
response body, a redirect, ``provider_meta`` or a log line. A refusal carries
a short code (``auth.safe_code``). Fence:
``tests/unit/test_api_connect_zoho_books.py``. R8:
``tests/unit/test_api_connect_r8.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

import httpx
from acb_auth import UserContext, get_current_user, require_feature_router, require_permission
from acb_common import get_logger, tenant_redis
from acb_common.api_connections_flag import api_connections_allowed
from acb_common.connector_result_card import result_card
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from gateway import outbound_guard
from gateway.connections import store
from gateway.connections.api import connect
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.crm_sources.base import SourceCredential, SourceError, UntrustedHost
from gateway.crm_sources.zoho import auth
from gateway.db import tenant_session
from gateway.routes.crm import connection as crm_connection
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_state,
    verify_state,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

_log = get_logger("gateway.integrations.api_connect")

TEMPLATE = ZOHO_BOOKS
PROVIDER = TEMPLATE.provider
OWNING_APP = "integrations"
MANAGE_PERMISSION = "integrations:manage"
STATE_PURPOSE = connect.STATE_PURPOSE

#: The route paths, after the router prefix.
START_ROUTE = "/api/zoho_books/start"
CALLBACK_ROUTE = "/api/zoho/callback"
PICK_ROUTE = "/api/zoho_books/pick"
STATUS_ROUTE = "/api/zoho_books"

#: The Redis namespaces and the life of a pending entry.
STATE_NAMESPACE = "api-connect-state"
PICK_NAMESPACE = "api-connect-pick"
#: API-5 (gap 1): the nonce of each admin's newest pending pick, under a hash
#: of the member, so a new start finds the old entry and revokes its token.
PICK_MEMBER_NAMESPACE = "api-connect-pick-member"
PENDING_TTL_S = 600

#: Where the callback sends the browser. The BFF passes only these shapes.
RETURN_OK = "/integrations?api_connected=zoho_books"
RETURN_FAILED = "/integrations?api_connect_error="
RETURN_PICK = "/integrations?api_pick="

#: One connect or disconnect at a time for each org and provider.
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"

#: The shape of a pick handle: the nonce of the state.
_PICK_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"


async def require_api_connections(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> None:
    """404 unless ``API_CONNECTIONS_ORGS`` holds the org of the session.

    It reads ``user.organization_id`` and nothing else from the request.
    """
    if not api_connections_allowed(user.organization_id):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/integrations", tags=["integrations"],
    # ⚠️ The ORDER is load-bearing: the flag runs first, so a dark route is a
    # 404 for every caller, also one that the feature gate would refuse.
    dependencies=[
        Depends(require_api_connections),
        require_feature_router("integrations"),
    ],
)


class StartOut(BaseModel):
    url: str


class Choice(BaseModel):
    """One Zoho Books organization of a pending pick."""

    id: str
    name: str


class StatusOut(BaseModel):
    """What an admin may see of the connection. No token, no blob."""

    connected: bool = False
    provider: str = PROVIDER
    status: str | None = None
    location: str | None = None
    organization_id: str | None = None
    grant: str | None = None
    scopes: list[str] = []
    connected_by: str | None = None
    updated_at: datetime | None = None
    #: The organizations of a pending pick, with ``?pick=``.
    choices: list[Choice] = []
    #: API-5: the result card (IN-9) of a connected row, from
    #: ``acb_common.connector_result_card``. ``None`` while not connected.
    card: dict[str, Any] | None = None


class PickIn(BaseModel):
    """The pick of one organization of a pending consent."""

    pick: str = Field(pattern=_PICK_PATTERN)
    zoho_organization_id: str = Field(pattern=r"^[0-9]{1,20}$")


class _TestReadFailed(Exception):
    """The test read did not answer the list of the op."""


def _organization(user: UserContext) -> str:
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(user.organization_id)


def _member(user: UserContext) -> str:
    return (user.email or "").strip().lower()


def _failed(code: str) -> RedirectResponse:
    return RedirectResponse(f"{RETURN_FAILED}{auth.safe_code(code) or 'failed'}", status_code=302)


def _status(row: store.Connection | None, choices: list[Choice] | None = None) -> StatusOut:
    if row is None:
        return StatusOut(choices=choices or [])
    meta = row.provider_meta
    connected = row.status == store.STATUS_CONNECTED
    return StatusOut(
        connected=connected,
        # IN-9: the one card model. Nothing uses the connection yet: the chat
        # tools of API-4 are not built, so the card names no app.
        card=result_card(PROVIDER, apps=(), templates=()) if connected else None,
        status=row.status,
        location=str(meta.get(auth.META_LOCATION) or "") or None,
        organization_id=str(meta.get(connect.META_ORGANIZATION_ID) or "") or None,
        grant=str(meta.get(connect.META_GRANT) or "") or None,
        scopes=list(row.scopes),
        connected_by=row.connected_by,
        updated_at=row.updated_at,
        choices=choices or [],
    )


def _audit(user: UserContext, org: str, **payload: Any) -> None:
    # Lazy: the module of the old routes is large.
    from gateway.routes.integrations import audit_integration

    audit_integration(user, org, PROVIDER, action="integration.api.connected", **payload)


# ── Seams that a test replaces ──────────────────────────────────────────────


def _redis() -> tenant_redis.TenantRedis:
    """The tenant-prefixed client."""
    return tenant_redis.get_tenant_redis()


async def _https(method: str, url: str, headers: dict[str, str]) -> outbound_guard.Answer:
    """One request to a template host: https on 443 only, through the guard."""
    return await outbound_guard.request_https(method, url, headers=headers)


async def _revoke(credential: SourceCredential | None, why: str) -> str | None:
    """Revoke at Zoho, best effort, with the CRM's one revoke of its client."""
    return await crm_connection._best_effort_revoke(credential, f"api:{why}")


# ── Redis: the one-use state and the pending pick ───────────────────────────


async def _put(org: str, namespace: str, nonce: str, value: str) -> None:
    with tenant_redis.organization_scope(org):
        client = _redis()
        await client.setex(client.key(namespace, nonce), PENDING_TTL_S, value)


async def _take(org: str, namespace: str, nonce: str) -> Any:
    """Read the entry and delete it, in ONE command (GETDEL)."""
    with tenant_redis.organization_scope(org):
        client = _redis()
        return await client.getdel(client.key(namespace, nonce))


async def _peek(org: str, namespace: str, nonce: str) -> Any:
    with tenant_redis.organization_scope(org):
        client = _redis()
        return await client.get(client.key(namespace, nonce))


def _unavailable(exc: Exception) -> HTTPException:
    _log.warning("api.connect_pending_unavailable", error=type(exc).__name__)
    return HTTPException(status_code=503, detail="pending_unavailable")


def _member_key(member: str) -> str:
    """The Redis part of a member: a hash, so no address is in a key."""
    return hashlib.sha256(member.encode("utf-8")).hexdigest()[:32]


async def _keep_pending(org: str, member: str, nonce: str, record: str) -> None:
    """Keep a pending pick, and point the member's index at its nonce."""
    await _put(org, PICK_NAMESPACE, nonce, record)
    await _put(org, PICK_MEMBER_NAMESPACE, _member_key(member), nonce)


async def _start_again(org: str, member: str) -> None:
    """API-5, gap 1 of §2.11: drop the admin's own older pending pick.

    A consent that found several organizations keeps its sealed credential in
    Redis until the pick. When the admin starts again, this takes that entry,
    revokes its refresh token at Zoho and deletes it, before the new consent.
    Zoho keeps 20 tokens for each user and client, so a stranded token could
    push out the token of the CRM mirror.

    ⚠️ An entry that expired cannot be revoked: Redis dropped the sealed
    credential with it, so nothing holds the token any more. Only this start
    again path revokes.

    Best effort. A fault here never blocks the new start. The log line holds
    counts only, never a token, a nonce or an address.
    """
    try:
        nonce = await _take(org, PICK_MEMBER_NAMESPACE, _member_key(member))
        raw = await _peek(org, PICK_NAMESPACE, str(nonce)) if nonce else None
        # The entry of another member stays: only its own admin may end it.
        if raw is None or connect.pending_member(raw) != member:
            return
        raw = await _take(org, PICK_NAMESPACE, str(nonce))
    except Exception as exc:  # the class only
        _log.warning("api.connect_restart_unread", error=type(exc).__name__)
        return
    pending = connect.pending_from(raw, store.open_value) if raw else None
    if pending is None:
        return
    revoked, failed = 0, 0
    try:
        outcome = await _revoke(pending.credential, "start_again")
        if outcome is not None:
            revoked, failed = (1, 0) if outcome == "confirmed" else (0, 1)
    except Exception:  # the count only: the error text can hold the token
        failed = 1
    _log.info("api.connect_restarted", revoked=revoked, failed=failed)


# ── The steps ───────────────────────────────────────────────────────────────


async def _organizations(credential: SourceCredential, host: str) -> list[tuple[str, str]]:
    """The context read. Raises :class:`connect.ContextUnreadable`."""
    try:
        answer = await _https("GET", connect.organizations_url(host),
                              connect.auth_headers(TEMPLATE, credential.access_token))
    except (outbound_guard.OutboundRefused, httpx.HTTPError) as exc:
        raise connect.ContextUnreadable(type(exc).__name__) from None
    if not answer.is_success:
        raise connect.ContextUnreadable(f"status {answer.status_code}")
    return connect.parse_organizations(answer.body)


async def _test_read(credential: SourceCredential, host: str, books_org: str) -> bool:
    """One read of :data:`connect.TEST_READ_OP`, with the picked organization."""
    url = connect.call_url(host, TEMPLATE, connect.TEST_READ_OP, connect.TEST_READ_ARGS,
                           {connect.META_ORGANIZATION_ID: books_org})
    try:
        answer = await _https("GET", url, connect.auth_headers(TEMPLATE, credential.access_token))
    except (outbound_guard.OutboundRefused, httpx.HTTPError) as exc:
        _log.warning("api.connect_test_read_failed", error=type(exc).__name__)
        return False
    if not answer.is_success:
        _log.warning("api.connect_test_read_failed", status=answer.status_code)
        return False
    return connect.read_answer_ok(TEMPLATE, connect.TEST_READ_OP, answer.body)


async def _old_credential_locked(db: Any, org: str) -> SourceCredential | None:
    """The stored credential under the connect lock, or ``None``.

    The advisory lock makes two first connects at one time wait for each
    other, so the second one reads the token of the first and revokes it. A
    blob that does not open counts as no old token.
    """
    await db.execute(text(_LOCK_SQL), {"key": f"api.connect:{org}:{PROVIDER}"})
    row = await store.get(db, org, PROVIDER, OWNING_APP)
    if row is None:
        return None
    try:
        return await store.load_credential_locked(db, org, row.id)
    except store.Undecryptable:
        _log.warning("api.connect_old_blob_unopened")
        return None


async def _finish(org: str, member: str, credential: SourceCredential,
                  books_org: str) -> store.Connection:
    """The test read, then the save, then the revoke of the old token.

    Raises :class:`_TestReadFailed` after it revokes the new token. A failure
    writes no row and leaves an older row as it is.
    """
    host = connect.template_host(TEMPLATE, auth.meta(credential, auth.META_API_DOMAIN))
    credential = replace(credential, provider_meta=connect.provider_meta(
        TEMPLATE, credential.provider_meta, organization_id=books_org))
    if not await _test_read(credential, host, books_org):
        await _revoke(credential, "test_read_failed")
        raise _TestReadFailed()
    try:
        async with tenant_session(org) as db:
            old = await _old_credential_locked(db, org)
            row = await store.save_credential(
                db, org, PROVIDER, OWNING_APP, credential,
                connected_by=member, scopes=connect.ZOHO_BOOKS_SCOPES,
            )
    except Exception:
        await _revoke(credential, "save_failed")
        raise
    # A reconnect: revoke the old refresh token, only after the commit, so a
    # failed save never kills the old token.
    if old is not None and old.refresh_token and old.refresh_token != credential.refresh_token:
        await _revoke(old, "reconnect")
    return row


# ── Routes ──────────────────────────────────────────────────────────────────


@router.post(START_ROUTE, response_model=StartOut,
             dependencies=[require_permission(MANAGE_PERMISSION)])
async def start_zoho_books(user: UserContext = Depends(get_current_user)) -> StartOut:
    """The consent URL with a marked, signed state. The admin follows it."""
    org = _organization(user)
    member = _member(user)
    crm_connection.zoho_oauth_config()  # 503 zoho_not_configured
    # API-5 (gap 1): an older pending pick of this admin goes first.
    await _start_again(org, member)
    try:
        state = sign_state(purpose=STATE_PURPOSE, org=org, member=member)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_member") from exc
    opened = verify_state(state, purpose=STATE_PURPOSE)
    if opened is None:  # a state that this process just signed always opens
        raise HTTPException(status_code=503, detail="signing_unavailable")
    try:
        await _put(org, STATE_NAMESPACE, opened["nonce"], member)
    except Exception as exc:  # the class only
        raise _unavailable(exc) from None
    url = crm_connection._source().consent_url(
        scopes=connect.ZOHO_BOOKS_SCOPES, state=connect.mark_state(state))
    _log.info("api.connect_started", template=TEMPLATE.id)
    return StartOut(url=url)


@router.get(CALLBACK_ROUTE, dependencies=[require_permission(MANAGE_PERMISSION)])
async def zoho_books_callback(
    code: str | None = Query(default=None, max_length=2048),
    state: str | None = Query(default=None, max_length=4096),
    error: str | None = Query(default=None, max_length=200),
    location: str | None = Query(default=None, max_length=40),
    accounts_server: str | None = Query(default=None, alias="accounts-server", max_length=200),
    user: UserContext = Depends(get_current_user),
) -> RedirectResponse:
    """Finish the consent. See the module doc for the order of the checks."""
    org = _organization(user)
    member = _member(user)
    # 1. The state, before any other work. Every refusal is the same 400.
    payload = verify_state(connect.unmark_state(state), purpose=STATE_PURPOSE)
    if payload is None or payload["org"] != org or payload["member"] != member:
        raise HTTPException(status_code=400, detail="invalid_state")
    nonce = str(payload["nonce"])
    try:
        started_by = await _take(org, STATE_NAMESPACE, nonce)
    except Exception as exc:  # the class only
        raise _unavailable(exc) from None
    if started_by is None or str(started_by) != member:
        raise HTTPException(status_code=400, detail="invalid_state")
    # 2. The admin said no at Zoho.
    if error:
        return _failed(error)
    if not code:
        return _failed("no_code")
    # 3. The data centre, checked against the allowlist before any call.
    try:
        server = auth.check_accounts_server(accounts_server or auth.CONSENT_SERVER)
        auth.checked_location(location, server)
    except UntrustedHost as exc:
        raise HTTPException(status_code=400, detail="untrusted_host") from exc
    # 4. The code for tokens. Zoho is called here and not before.
    params = {"code": code, "accounts-server": server,
              **({"location": location} if location else {})}
    try:
        credential = await crm_connection._source().finish_consent(
            params=params, scopes=connect.ZOHO_BOOKS_SCOPES)
    except UntrustedHost as exc:
        raise HTTPException(status_code=400, detail="untrusted_host") from exc
    except SourceError as exc:
        _log.warning("api.connect_not_finished", code=auth.safe_code(exc.code) or "error")
        return _failed(exc.code or "exchange_failed")
    try:
        host = connect.template_host(TEMPLATE, auth.meta(credential, auth.META_API_DOMAIN))
    except UntrustedHost as exc:
        await _revoke(credential, "untrusted_host")
        raise HTTPException(status_code=400, detail="untrusted_host") from exc
    # 5. The context: the Zoho Books organizations of the account.
    try:
        choices = await _organizations(credential, host)
    except connect.ContextUnreadable:
        _log.warning("api.connect_context_unreadable")
        await _revoke(credential, "context_unreadable")
        return _failed("context_unreadable")
    if not choices:
        await _revoke(credential, "no_organization")
        return _failed("no_books_organization")
    if len(choices) > 1:
        record = connect.pending_record(
            member, store.seal_value(connect.credential_text(credential)), choices)
        try:
            await _keep_pending(org, member, nonce, record)
        except Exception as exc:  # the class only
            _log.warning("api.connect_pending_unavailable", error=type(exc).__name__)
            await _revoke(credential, "pending_unavailable")
            return _failed("pending_unavailable")
        _log.info("api.connect_pick_pending", template=TEMPLATE.id, choices=len(choices))
        return RedirectResponse(f"{RETURN_PICK}{nonce}", status_code=302)
    # 6 and 7. The test read, the save and the old token.
    try:
        await _finish(org, member, credential, choices[0][0])
    except _TestReadFailed:
        return _failed("test_read_failed")
    _audit(user, org, organizations=1)
    _log.info("api.connect_connected", template=TEMPLATE.id)
    return RedirectResponse(RETURN_OK, status_code=302)


@router.post(PICK_ROUTE, response_model=StatusOut,
             dependencies=[require_permission(MANAGE_PERMISSION)])
async def pick_zoho_books(
    body: PickIn, user: UserContext = Depends(get_current_user),
) -> StatusOut:
    """Finish a consent that found several organizations, with one of them.

    The id must be one of the pending list that the server holds. The entry
    is taken once, so a second pick of one consent is 400.

    API-5 (gap 3 of §2.11): the route reads the member of the entry BEFORE it
    takes the entry. The org is the session's, through the tenant key. So
    another admin of the org who holds the nonce gets 400, and the entry
    stays for the admin who started the consent.
    """
    org = _organization(user)
    member = _member(user)
    try:
        seen = await _peek(org, PICK_NAMESPACE, body.pick)
    except Exception as exc:  # the class only
        raise _unavailable(exc) from None
    if seen is None or connect.pending_member(seen) != member:
        raise HTTPException(status_code=400, detail="invalid_pick")
    try:
        raw = await _take(org, PICK_NAMESPACE, body.pick)
    except Exception as exc:  # the class only
        raise _unavailable(exc) from None
    pending = connect.pending_from(raw, store.open_value) if raw else None
    if pending is None:
        raise HTTPException(status_code=400, detail="invalid_pick")
    if pending.member != member:  # defence: the entry changed after the read
        await _revoke(pending.credential, "pick_refused")
        raise HTTPException(status_code=400, detail="invalid_pick")
    if body.zoho_organization_id not in {i for i, _ in pending.choices}:
        await _revoke(pending.credential, "pick_refused")
        raise HTTPException(status_code=400, detail="unknown_organization")
    try:
        row = await _finish(org, member, pending.credential, body.zoho_organization_id)
    except UntrustedHost as exc:
        await _revoke(pending.credential, "untrusted_host")
        raise HTTPException(status_code=400, detail="untrusted_host") from exc
    except _TestReadFailed:
        raise HTTPException(status_code=502, detail="test_read_failed") from None
    _audit(user, org, organizations=len(pending.choices))
    _log.info("api.connect_connected", template=TEMPLATE.id)
    return _status(row)


@router.get(STATUS_ROUTE, response_model=StatusOut,
            dependencies=[require_permission(MANAGE_PERMISSION)])
async def zoho_books_status(
    pick: str | None = Query(default=None, pattern=_PICK_PATTERN),
    user: UserContext = Depends(get_current_user),
) -> StatusOut:
    """The state of the org's Zoho Books connection. No token, no blob.

    With ``pick``, it also names the organizations of the admin's own pending
    pick. It reads the entry and leaves it.
    """
    org = _organization(user)
    member = _member(user)
    choices: list[Choice] = []
    if pick:
        try:
            raw = await _peek(org, PICK_NAMESPACE, pick)
        except Exception as exc:  # the class only
            raise _unavailable(exc) from None
        pending = connect.pending_from(raw, store.open_value) if raw else None
        if pending is not None and pending.member == member:
            choices = [Choice(id=i, name=n) for i, n in pending.choices]
    async with tenant_session(org) as db:
        row = await store.get(db, org, PROVIDER, OWNING_APP)
    return _status(row, choices)


# ── The console disconnect ──────────────────────────────────────────────────


async def disconnect_books(org: str) -> store.Connection | None:
    """Revoke at Zoho, delete the blob and set ``disconnected`` for *org*.

    The Integrations console calls it for an ``api:zoho_books`` row
    (``POST /integrations/connections/{id}/disconnect``). One transaction under
    the connect lock, as the CRM disconnect. It does not read the flag: the
    caller owns its gate. ``None`` when the org has no row. A second call
    changes nothing.
    """
    async with tenant_session(org) as db:
        await db.execute(text(_LOCK_SQL), {"key": f"api.connect:{org}:{PROVIDER}"})
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if row is None or row.status == store.STATUS_DISCONNECTED:
            return row
        credential = await store.load_credential(db, org, PROVIDER, OWNING_APP)
        await _revoke(credential, "disconnect")
        return await store.disconnect(db, org, row.id)


__all__ = ["disconnect_books", "require_api_connections", "router"]
