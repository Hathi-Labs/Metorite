"""CRM · connection — connect, read and disconnect the Zoho mirror (WS-53 CRM-Z2).

Spec: ``project-docs/specs/crm_platform.md`` §5.2 (items 1, 3, 4, 8, 11, 12),
§5.3, §5.4 and §7.1.

    GET   /crm/connection
    POST  /crm/connection/zoho/start
    GET   /crm/oauth/zoho/callback
    POST  /crm/connection/disconnect

⚠️ **Dark twice.** Each route needs the CRM kill switch (``CRM_ENABLED`` and
``CRM_ORGS``, at the router) AND ``CRM_MIRROR`` (``flags.require_crm_mirror``).
With ``CRM_MIRROR`` off, each route answers 404.

**Who may do what.** ``GET`` is open to each member that the CRM access rule
admits. It never returns a token or the blob. The other three routes need
``admin:access:manage``, the rule that ``PATCH /crm/settings`` uses.

**The callback path.** ``docs/ZOHO_OAUTH_SETUP.md`` tells the owner to register
``https://app.metorite.com/api/crm/oauth/zoho/callback``. The ``/api/crm``
proxy forwards that path to ``/crm/oauth/zoho/callback`` here. The path of the
route and the redirect URI are the same string in two places, and the test
``test_crm_connection.py`` pins both.

**The state.** The start route signs ``crm.zoho.connect`` state with the
organization, the member, a nonce and an expiry of ten minutes
(``routes/email/transport/signing.py``). The callback refuses, with 400 and
NO call to Zoho, a state that is forged, expired, signed for another purpose,
or made for another organization or member than the session's. The tenant and
the member come from the server-side ``UserContext`` (R5), never from the
request.

**The client.** ``CRM_ZOHO_CLIENT_ID`` and ``CRM_ZOHO_CLIENT_SECRET`` are
Metorite's own Zoho app. This module is the one reader. With either unset,
``start`` answers 503 ``zoho_not_configured``.

**The mode.** The callback sets ``crm_settings.mode = 'mirror'`` and
``connection_id`` in the SAME transaction as the credential. It is the one
place the mode becomes ``mirror`` (``PATCH /crm/settings`` still refuses it). A
Native org gets 409: Native to Mirror is a later release (§3.4).

**No secret out.** The code, the tokens and the secret never appear in a
response body or a log line. A revoke or a refresh is logged by outcome and by
Zoho error code only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger, get_settings
from fastapi import Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from gateway.connections import shared_token, store
from gateway.crm_sources.base import (
    OAuthClientConfig,
    SourceCredential,
    SourceError,
    UntrustedHost,
)
from gateway.crm_sources.zoho import ZohoSource, auth
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import _tenant_session, router
from gateway.routes.crm.flags import require_crm_mirror
from gateway.routes.email.transport.oauth import _workbench_public_url
from gateway.routes.email.transport.signing import (
    SigningUnavailable,
    sign_state,
    verify_state,
)
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("crm.connection")

PROVIDER = "zoho"
OWNING_APP = "crm"

#: The purpose of the signed state. A value signed for another purpose never
#: verifies here (``signing.py``).
STATE_PURPOSE = "crm.zoho.connect"

#: The path the owner registers in the Zoho console, after the workbench
#: origin. The ``/api/crm`` proxy turns it into ``CALLBACK_ROUTE`` below.
REDIRECT_PATH = "/api/crm/oauth/zoho/callback"
CALLBACK_ROUTE = "/oauth/zoho/callback"

#: Where the callback sends the browser.
RETURN_OK = "/crm?connected=zoho"
RETURN_FAILED = "/crm?connect_error="

#: The read-only scopes of every consent (``docs/ZOHO_OAUTH_SETUP.md`` §5).
#: Mirror v1 reads only (D95.3).
ZOHO_READ_SCOPES: tuple[str, ...] = (
    "ZohoCRM.modules.leads.READ",
    "ZohoCRM.modules.contacts.READ",
    "ZohoCRM.modules.accounts.READ",
    "ZohoCRM.modules.deals.READ",
    "ZohoCRM.modules.notes.READ",
    "ZohoCRM.modules.calls.READ",
    "ZohoCRM.modules.events.READ",
    "ZohoCRM.settings.fields.READ",
    "ZohoCRM.settings.layouts.READ",
    "ZohoCRM.settings.pipeline.READ",
    "ZohoCRM.settings.modules.READ",
    "ZohoCRM.users.READ",
    "ZohoCRM.org.READ",
)

_MODE_SQL = "SELECT mode FROM crm_settings WHERE organization_id = CAST(:org AS uuid)"

#: One callback at a time for each org and provider, until the commit.
_CONNECT_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"

#: Move the org to Mirror and point it at its connection. The WHERE keeps a
#: Native org Native, also when a change of mode lands between the read and
#: this write. No row back means the guard refused.
_ADOPT_SQL = (
    "INSERT INTO crm_settings (organization_id, mode, connection_id, "
    "updated_by, updated_at) VALUES (CAST(:org AS uuid), 'mirror', "
    "CAST(:connection_id AS uuid), :updated_by, now()) "
    "ON CONFLICT (organization_id) DO UPDATE SET mode = 'mirror', "
    "connection_id = EXCLUDED.connection_id, updated_by = EXCLUDED.updated_by, "
    "updated_at = EXCLUDED.updated_at "
    "WHERE crm_settings.mode IS NULL OR crm_settings.mode = 'mirror' "
    "RETURNING mode"
)


class ConnectionModel(BaseModel):
    """What a member may see of a connection. No token, no blob."""

    connected: bool = False
    provider: str = PROVIDER
    owning_app: str = OWNING_APP
    status: str | None = None
    location: str | None = None
    scopes: list[str] = []
    connected_by: str | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime | None = None


class StartModel(BaseModel):
    url: str


def _wire(row: store.Connection | None) -> ConnectionModel:
    if row is None:
        return ConnectionModel()
    return ConnectionModel(
        connected=row.status == store.STATUS_CONNECTED,
        status=row.status,
        location=str(row.provider_meta.get(auth.META_LOCATION) or "") or None,
        scopes=list(row.scopes),
        connected_by=row.connected_by,
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        updated_at=row.updated_at,
    )


def _organization(user: UserContext) -> str:
    """The caller's organization, from the server. None is a 403."""
    if not user.organization_id:
        raise HTTPException(
            status_code=403, detail="Forbidden: you are in no organization.",
        )
    return str(user.organization_id)


def _member(user: UserContext) -> str:
    return (user.email or "").strip().lower()


def redirect_uri() -> str:
    """The redirect URI that Zoho sees. It must equal the console value."""
    return f"{_workbench_public_url()}{REDIRECT_PATH}"


def zoho_oauth_config() -> OAuthClientConfig:
    """Metorite's Zoho client. 503 ``zoho_not_configured`` when it is unset.

    The ONE reader of ``CRM_ZOHO_CLIENT_ID`` and ``CRM_ZOHO_CLIENT_SECRET``.
    """
    settings = get_settings()
    client_id = str(getattr(settings, "crm_zoho_client_id", "") or "").strip()
    secret = str(getattr(settings, "crm_zoho_client_secret", "") or "").strip()
    if not client_id or not secret:
        raise HTTPException(status_code=503, detail="zoho_not_configured")
    return OAuthClientConfig(
        client_id=client_id, client_secret=secret, redirect_uri=redirect_uri(),
    )


def _source(
    credential: SourceCredential | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ZohoSource:
    return ZohoSource(zoho_oauth_config(), credential, transport=transport)


async def _read_mode(db: Any, org: str) -> str | None:
    row = (await db.execute(text(_MODE_SQL), {"org": org})).first()
    return getattr(row, "mode", None) if row is not None else None


async def _adopt_mirror(db: Any, org: str, connection_id: str, email: str) -> None:
    row = (await db.execute(text(_ADOPT_SQL), {
        "org": org, "connection_id": connection_id, "updated_by": email or None,
    })).first()
    if row is None:
        raise HTTPException(status_code=409, detail="mode_is_native")


def _refuse_native(mode: str | None) -> None:
    if mode == "native":
        raise HTTPException(status_code=409, detail="mode_is_native")


async def _old_credential_locked(db: Any, org: str) -> SourceCredential | None:
    """The stored credential, with ``FOR UPDATE`` on its row, or ``None``.

    The transaction first takes an advisory lock on the org and the provider.
    ``FOR UPDATE`` needs a row, so without the advisory lock, two first connects
    at one time each read no old token. The second save then replaces the
    first token, and nobody revokes it (CRM-Z2 re-review P2). The lock ends
    with the transaction.

    A blob that does not open counts as no old token. A new consent is the
    repair for it, so it must not fail here (CRM-Z2 review P1).
    """
    await db.execute(text(_CONNECT_LOCK_SQL), {"key": f"crm.connect:{org}:{PROVIDER}"})
    row = await store.get(db, org, PROVIDER, OWNING_APP)
    if row is None:
        return None
    try:
        return await store.load_credential_locked(db, org, row.id)
    except store.Undecryptable:
        _log.warning("zoho reconnect: the old blob does not open")
        return None


async def _best_effort_revoke(credential: SourceCredential | None, why: str) -> None:
    """Revoke at Zoho and go on, whatever happens. Log the outcome only."""
    if credential is None or not credential.refresh_token:
        return
    try:
        confirmed = await _source(credential).revoke()
        outcome = "confirmed" if confirmed else "not_confirmed"
    except HTTPException:
        outcome = "not_configured"
    except SourceError as exc:
        outcome = f"failed:{auth.safe_code(exc.code) or 'error'}"
    except (TimeoutError, httpx.HTTPError):
        outcome = "failed:transport"
    _log.info("zoho revoke (%s): %s", why, outcome)


def _failed(code: str) -> RedirectResponse:
    return RedirectResponse(f"{RETURN_FAILED}{auth.safe_code(code) or 'failed'}", status_code=302)


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get(
    "/connection", response_model=ConnectionModel,
    dependencies=[Depends(require_crm_mirror)],
)
async def get_crm_connection(
    user: UserContext = Depends(get_current_user),
) -> ConnectionModel:
    """The state of the org's Zoho connection. No token, no blob."""
    org = _organization(user)
    async with _tenant_session() as db:
        return _wire(await store.get(db, org, PROVIDER, OWNING_APP))


@router.post(
    "/connection/zoho/start", response_model=StartModel,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def start_zoho_connection(
    user: UserContext = Depends(get_current_user),
) -> StartModel:
    """The consent URL, with a signed state. The admin follows it to Zoho."""
    org = _organization(user)
    member = _member(user)
    source = _source()
    async with _tenant_session() as db:
        _refuse_native(await _read_mode(db, org))
    try:
        state = sign_state(purpose=STATE_PURPOSE, org=org, member=member)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_member") from exc
    return StartModel(url=source.consent_url(scopes=ZOHO_READ_SCOPES, state=state))


@router.get(
    CALLBACK_ROUTE,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def zoho_callback(
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
    payload = verify_state(state, purpose=STATE_PURPOSE)
    if payload is None or payload["org"] != org or payload["member"] != member:
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
    source = _source()
    # 4. The mode, before Zoho is called. _adopt_mirror checks it again.
    async with _tenant_session() as db:
        _refuse_native(await _read_mode(db, org))
    # 5. The code for tokens. Zoho is called here and not before.
    params: Mapping[str, str] = {
        "code": code, "accounts-server": server, **({"location": location} if location else {}),
    }
    try:
        credential = await source.finish_consent(params=params, scopes=ZOHO_READ_SCOPES)
    except SourceError as exc:
        _log.warning("zoho consent not finished: %s", auth.safe_code(exc.code) or "error")
        return _failed(exc.code or ("untrusted_host" if isinstance(exc, UntrustedHost) else "exchange_failed"))
    # 6. The old credential, the new one, the mode and the pointer: one
    #    transaction. The old token is read under the row lock, so two
    #    callbacks at one time each see the token that the other replaced.
    async with _tenant_session() as db:
        old = await _old_credential_locked(db, org)
        row = await store.save_credential(
            db, org, PROVIDER, OWNING_APP, credential,
            connected_by=member, scopes=ZOHO_READ_SCOPES,
        )
        await _adopt_mirror(db, org, row.id, member)
    # 7. A reconnect: revoke the old refresh token (Zoho keeps 20 per user).
    #    Only after the commit, so a failed save never kills the old token
    #    (CRM-Z2 review P2).
    if old is not None and old.refresh_token != credential.refresh_token:
        await _best_effort_revoke(old, "reconnect")
    _log.info("zoho connected for an organization")
    # The first import runs in the background. A failure to start it is
    # logged, and the sync loop picks the org up (CRM-Z4).
    from gateway.crm_sync.scheduler import start_first_import

    await start_first_import(org)
    return RedirectResponse(RETURN_OK, status_code=302)


@router.post(
    "/connection/disconnect", response_model=ConnectionModel,
    dependencies=[Depends(require_crm_mirror), require_permission(ADMIN_PERMISSION)],
)
async def disconnect_crm_connection(
    user: UserContext = Depends(get_current_user),
) -> ConnectionModel:
    """Revoke at Zoho, delete the blob and set ``disconnected``.

    The mirrored rows stay. A second call changes nothing.
    """
    org = _organization(user)
    async with _tenant_session() as db:
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if row is None or row.status == store.STATUS_DISCONNECTED:
            return _wire(row)
        credential = await store.load_credential(db, org, PROVIDER, OWNING_APP)
    await _best_effort_revoke(credential, "disconnect")
    async with _tenant_session() as db:
        return _wire(await store.disconnect(db, org, row.id))


# ── The shared token (§5.2 item 11) ─────────────────────────────────────────


async def fresh_zoho_credential(
    org: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: auth.Clock = auth.utcnow,
) -> SourceCredential | None:
    """The org's Zoho credential, refreshed and stored when it is due.

    ``None`` when the org has no live connection. A refresh that Zoho refuses
    as ``invalid_code``, ``invalid_grant`` or ``invalid_client`` sets
    ``needs_reconnect`` and raises ``NeedsReconnect``. It is never retried.
    The sync worker of CRM-Z5 calls this. The organization is the worker's own.
    """
    config = zoho_oauth_config()

    async def _refresh(credential: SourceCredential) -> SourceCredential:
        return await auth.refresh(config, credential, transport=transport, clock=clock)

    return await shared_token.fresh_credential(
        org,
        provider=PROVIDER,
        owning_app=OWNING_APP,
        refresh=_refresh,
        needs_refresh=auth.needs_refresh,
        now=clock,
    )
