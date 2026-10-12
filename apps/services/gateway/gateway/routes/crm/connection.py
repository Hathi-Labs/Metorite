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

**The same Zoho account, or a start over (CRM-X0, §19.11 items 2 and 3).**
Each callback reads ``GET org`` with the new token (one credit). An account
that cannot be read, or has no id, is refused with ``org_unreadable``, the
new token is revoked, and nothing changes. The callback then decides, in this
order, whether the new account is the one the CRM records came from:

1. The account id that a good consent stored in ``provider_meta``
   (``store.META_ACCOUNT_ID``). A consent, a disconnect and ``needs_reset``
   keep it. Only the start over removes it.
2. Else the ``org`` section of the latest source profile (``mapping.same_org``),
   for an org that connected before the id was stored.
3. Else, when the org holds Zoho records, the new token reads up to
   :data:`PROBE_LIMIT` of them by id (``ZohoSource.record_exists``). One found
   means the same account. None found means another account. Any other answer
   is ``org_unreadable``.
4. Else the CRM holds nothing of Zoho, and the consent goes on.

The same account goes on as before, and the save stores the account id.
Another account sets ``needs_reset``, keeps the old token, revokes the new one
and writes no record, so the records of two Zoho accounts never mix. The save
checks the stored id again under the connect lock, so two consents at one time
cannot both pass the check.

**No secret out.** The code, the tokens and the secret never appear in a
response body or a log line. A revoke or a refresh is logged by outcome and by
Zoho error code only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from gateway.crm_sources.zoho import ZohoSource, auth, mapping
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
#: CRM-X0: a consent to another Zoho account. ONE query name, because the BFF
#: callback passes no other shape (``api/crm/oauth/zoho/callback``). The page
#: opens the setup hub for this code (``connect.ts`` ``returnTarget``).
RETURN_DIFFERENT_ORG = "/crm?connect_error=different_org"

#: The most mirrored records the callback reads to tell the account (step 3).
#: More than one, because a record deleted in Zoho since the last sync must not
#: look like another account.
PROBE_LIMIT = 3

#: The tables that step 3 reads from, with their Zoho module, in this order.
_PROBE_TABLES: tuple[tuple[str, str], ...] = (
    ("crm_deals", "Deals"), ("crm_leads", "Leads"),
    ("crm_contacts", "Contacts"), ("crm_companies", "Accounts"),
)

_PROBE_SQL = (
    "SELECT ext_id FROM {table} WHERE organization_id = CAST(:org AS uuid) "
    "AND ext_source = 'zoho' AND ext_id ~ '^[0-9]+$' "
    "ORDER BY ext_modified_at DESC NULLS LAST LIMIT :n"
)

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
    # CRM-Q5. A connection made before this scope existed must reconnect to
    # grant it. Until then the sync skips Tasks and reports them unavailable.
    "ZohoCRM.modules.tasks.READ",
    # CRM-Q6a: the generic record store (crm_platform.md §18.7). One read scope
    # for each standard module, and one for every custom module. Each name is
    # on Zoho's published scope list for API v2 and v8, checked on 2026-10-12,
    # because Zoho refuses the WHOLE consent for one unknown scope.
    # `mapping.module_read_scope` gives the same names, and
    # tests/unit/test_crm_module_records.py holds the two together. A
    # connection made before this slice must reconnect to grant them. Until
    # then the sync counts each module as unavailable and the run stays ok.
    "ZohoCRM.modules.products.READ",
    "ZohoCRM.modules.quotes.READ",
    "ZohoCRM.modules.salesorders.READ",
    "ZohoCRM.modules.purchaseorders.READ",
    "ZohoCRM.modules.invoices.READ",
    "ZohoCRM.modules.vendors.READ",
    "ZohoCRM.modules.pricebooks.READ",
    "ZohoCRM.modules.cases.READ",
    "ZohoCRM.modules.solutions.READ",
    "ZohoCRM.modules.campaigns.READ",
    "ZohoCRM.modules.custom.READ",
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
    #: ``crm_settings.mode``: ``native``, ``mirror`` or ``None`` (not chosen).
    #: Each member needs it, and ``GET /crm/settings`` is for admins only.
    mode: str | None = None


class StartModel(BaseModel):
    url: str


def _wire(row: store.Connection | None, mode: str | None = None) -> ConnectionModel:
    if row is None:
        return ConnectionModel(mode=mode)
    return ConnectionModel(
        mode=mode,
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


@dataclass(frozen=True)
class KnownAccount:
    """What the org knows of the Zoho account that its records came from."""

    #: The id that a good consent stored (step 1).
    account_id: str | None = None
    #: The ``org`` section of the latest source profile (step 2).
    profile_org: dict[str, Any] | None = None
    #: A Zoho module and up to :data:`PROBE_LIMIT` ids of its mirrored
    #: records (step 3). ``None`` when the org holds no Zoho record.
    probe: tuple[str, tuple[str, ...]] | None = None


async def _known_account(db: Any, org: str) -> KnownAccount:
    """Read the three sources of the module doc, in one session.

    The sync package is imported here, as ``sync.py`` does.
    """
    from gateway.crm_sync import profiles

    row = await store.get(db, org, PROVIDER, OWNING_APP)
    stored = str((row.provider_meta if row else {}).get(store.META_ACCOUNT_ID) or "") or None
    latest = await profiles.latest(db, org)
    section = (latest.profile.get("org") if latest is not None else None) or None
    if not isinstance(section, dict) or not any(mapping.org_identity(section).values()):
        section = None
    probe: tuple[str, tuple[str, ...]] | None = None
    for table, module in _PROBE_TABLES:
        rows = (await db.execute(
            text(_PROBE_SQL.format(table=table)), {"org": org, "n": PROBE_LIMIT},
        )).fetchall()
        if rows:
            probe = (module, tuple(str(r.ext_id) for r in rows))
            break
    return KnownAccount(account_id=stored, profile_org=section, probe=probe)


async def _same_account(
    known: KnownAccount, fresh: dict[str, Any], credential: SourceCredential,
) -> bool | None:
    """Steps 1 to 4 of the module doc. ``None`` means the CRM holds nothing
    of Zoho. A failed record read raises, and the caller fails closed."""
    fresh_id = mapping.org_identity(fresh)["id"]
    if known.account_id:
        return known.account_id == fresh_id
    if known.profile_org is not None:
        verdict = mapping.same_org(known.profile_org, fresh)
        if verdict is not None:
            return verdict
    if known.probe is not None:
        module, ids = known.probe
        source = _source(credential)
        for ext_id in ids:
            if await source.record_exists(module, ext_id):
                return True
        return False
    return None


async def _mark_needs_reset(org: str) -> None:
    """Store ``needs_reset`` under the connect lock. The old token stays."""
    async with _tenant_session() as db:
        await db.execute(text(_CONNECT_LOCK_SQL), {"key": f"crm.connect:{org}:{PROVIDER}"})
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if row is not None:
            await store.mark_needs_reset(db, org, row.id)


class _AccountChanged(Exception):
    """Under the lock, the stored account id is another account's."""


def consent_url_for(org: str, member: str) -> str:
    """The consent URL with a signed state for *org* and *member*.

    The start route and the start over (``POST /crm/reset`` with
    ``then = "mirror"``) both call it, so the state has one maker. 503 when
    Metorite's Zoho client or the signing key is not set.
    """
    source = _source()
    try:
        state = sign_state(purpose=STATE_PURPOSE, org=org, member=member)
    except SigningUnavailable as exc:
        raise HTTPException(status_code=503, detail="signing_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_member") from exc
    return source.consent_url(scopes=ZOHO_READ_SCOPES, state=state)


async def _different_account(org: str, credential: SourceCredential) -> RedirectResponse:
    """``needs_reset``: keep the old token and the records, drop the new token."""
    await _mark_needs_reset(org)
    await _best_effort_revoke(credential, "different_org")
    _log.info("zoho consent for another account: needs_reset")
    return RedirectResponse(RETURN_DIFFERENT_ORG, status_code=302)


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
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        return _wire(row, await _read_mode(db, org))


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
    zoho_oauth_config()  # 503 before the mode check, as before CRM-X0
    async with _tenant_session() as db:
        _refuse_native(await _read_mode(db, org))
    return StartModel(url=consent_url_for(org, member))


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
    # 4. The mode, before Zoho is called. _adopt_mirror checks it again. And
    #    the account that the records came from (CRM-X0).
    async with _tenant_session() as db:
        _refuse_native(await _read_mode(db, org))
        known = await _known_account(db, org)
    # 5. The code for tokens. Zoho is called here and not before.
    params: Mapping[str, str] = {
        "code": code, "accounts-server": server, **({"location": location} if location else {}),
    }
    try:
        credential = await source.finish_consent(params=params, scopes=ZOHO_READ_SCOPES)
    except SourceError as exc:
        _log.warning("zoho consent not finished: %s", auth.safe_code(exc.code) or "error")
        return _failed(exc.code or ("untrusted_host" if isinstance(exc, UntrustedHost) else "exchange_failed"))
    # 5b. CRM-X0: the account of the new token, then the same account or not
    #     (see the module doc). Anything that confirms nothing changes
    #     nothing, and the new token goes.
    try:
        fresh = await _source(credential).read_org()
        fresh_id = mapping.org_identity(fresh)["id"] if fresh else None
        verdict = await _same_account(known, fresh, credential) if fresh_id else None
    except (SourceError, TimeoutError, httpx.HTTPError) as exc:
        _log.warning("zoho consent: the account is not readable: %s",
                     auth.safe_code(getattr(exc, "code", "")) or type(exc).__name__)
        fresh_id = None
    if not fresh_id:
        await _best_effort_revoke(credential, "unconfirmed")
        return _failed("org_unreadable")
    if verdict is False:
        return await _different_account(org, credential)
    credential = replace(credential, provider_meta={
        **dict(credential.provider_meta), store.META_ACCOUNT_ID: str(fresh_id),
    })
    # 6. The old credential, the new one, the mode and the pointer: one
    #    transaction. The old token is read under the row lock, so two
    #    callbacks at one time each see the token that the other replaced.
    #    The stored account id is read again under the same lock.
    try:
        async with _tenant_session() as db:
            old = await _old_credential_locked(db, org)
            current = await store.get(db, org, PROVIDER, OWNING_APP)
            now_id = (current.provider_meta if current else {}).get(store.META_ACCOUNT_ID)
            if now_id and str(now_id) != str(fresh_id):
                raise _AccountChanged()
            row = await store.save_credential(
                db, org, PROVIDER, OWNING_APP, credential,
                connected_by=member, scopes=ZOHO_READ_SCOPES,
            )
            await _adopt_mirror(db, org, row.id, member)
    except _AccountChanged:
        return await _different_account(org, credential)
    # 7. A reconnect: revoke the old refresh token (Zoho keeps 20 per user).
    #    Only after the commit, so a failed save never kills the old token
    #    (CRM-Z2 review P2).
    if old is not None and old.refresh_token != credential.refresh_token:
        await _best_effort_revoke(old, "reconnect")
    _log.info("zoho connected for an organization")
    # The first import runs in the background. A failure to start it is
    # logged, and the sync loop picks the org up (CRM-Z4).
    try:
        from gateway.crm_sync.scheduler import start_first_import

        await start_first_import(org)
    except Exception as exc:  # the redirect must not fail for the import
        _log.warning("first import not started: %s", type(exc).__name__)
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
    return _wire(await disconnect_zoho(_organization(user)))


async def disconnect_zoho(org: str) -> store.Connection | None:
    """Revoke at Zoho, delete the blob and set ``disconnected`` for *org*.

    The ONE disconnect logic. ``POST /crm/connection/disconnect`` and the
    Integrations console (``POST /integrations/connections/{id}/disconnect``,
    WS-54 IN-1) both call it, so neither repeats it. It does not read the
    ``CRM_MIRROR`` flag: the caller owns the gate. Returns the row after the
    change, or ``None`` when the org has no Zoho connection. A second call
    changes nothing.
    """
    async with _tenant_session() as db:
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if row is None or row.status == store.STATUS_DISCONNECTED:
            return row
        credential = await store.load_credential(db, org, PROVIDER, OWNING_APP)
    await _best_effort_revoke(credential, "disconnect")
    async with _tenant_session() as db:
        return await store.disconnect(db, org, row.id)


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
