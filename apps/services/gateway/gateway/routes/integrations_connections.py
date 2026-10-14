"""The read model of the Integrations console (WS-54 IN-1).

Spec: ``project-docs/specs/integrations_console.md`` §3.2, §3.5, §4 and §7.2.

    GET   /integrations/connections
    POST  /integrations/connections/{id}/disconnect
    POST  /integrations/mcp/{name}/test

**One row shape over five sources.** the registry table (Zoho, GitHub, and
the next providers), ``email_accounts``, ``wa_accounts``, the MCP servers and
``provider_keys``. The console never sees a credential. Each SELECT below names
its columns, and none names a blob, a header value, a key value or a message
column (D-EM-4). A free text from a provider never reaches ``last_error``:
only a short code does.

**MCP (IN-4).** The MCP servers are rows of the registry too, with the
provider ``mcp:<name>``. ``gateway.connections.mcp`` reads them, and their
headers stay in the encrypted blob. The registry source skips them, so each
server is listed once, in the MCP row shape.

**Tenancy (R5).** The organization comes from the server-side ``UserContext``,
never from the request. the registry table, ``email_accounts`` and
``wa_accounts`` are read inside ``tenant_session(org)`` (FORCE RLS binds the
tenant). ``provider_keys`` is RLS-exempt, so its ``organization_id`` predicate
is the boundary.

**Who may do what (§3.5).** The read needs ``admin:members:read``. Each write
needs ``integrations:manage``. A mailbox and a WhatsApp number belong to a
member, so the console disconnects neither: it answers 409 and links to the
owning app (D96.1).

**Audit (§4.4).** A disconnect writes one audit event through ``acb_audit``:
who, what, when, which organization. Fence:
``tests/unit/test_integrations_connections.py`` and
``tests/unit/test_integrations_connections_r8.py``.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from acb_auth import (
    UserContext,
    get_current_user,
    require_feature_router,
    require_permission,
)
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException
from gateway import outbound_guard
from gateway.connections import store
from gateway.db import tenant_session
from gateway.routes import integrations
from pydantic import BaseModel, Field
from sqlalchemy import text

_log = get_logger("gateway.integrations.connections")

READ_PERMISSION = "admin:members:read"
MANAGE_PERMISSION = integrations.MANAGE_PERMISSION

router = APIRouter(
    prefix="/integrations", tags=["integrations"],
    dependencies=[require_feature_router("integrations")],
)

#: Where each owning app connects its own accounts (D96.1).
_SETUP_HREF = {
    "crm": "/crm",
    "email": "/email",
    "whatsapp": "/whatsapp",
    "integrations": "/integrations",
}
_KNOWN_APPS = frozenset(_SETUP_HREF)

#: The code of an error that the console may show. No provider text.
_ERR_AUTH = "auth_failed"
_ERR_SYNC = "sync_failed"


class Row(BaseModel):
    """One connection. It holds no credential, ever (spec §3.2)."""

    id: str
    kind: str
    provider: str
    label: str
    owning_app: str
    scope: str
    owner: str | None = None
    status: str
    last_sync_at: str | None = None
    last_error: str | None = None
    setup_href: str | None = None
    can_disconnect: bool = False
    detail: dict[str, str] = Field(default_factory=dict)


class ConnectionsOut(BaseModel):
    connections: list[Row]
    #: The sources that could not be read, by name. Empty when all five read.
    unavailable: list[str] = Field(default_factory=list)


class McpTestOut(BaseModel):
    ok: bool
    tools: int | None = None
    error: str | None = None


def _org(user: UserContext) -> str:
    """The caller's organization, from the server. None is a 403 (R5)."""
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(user.organization_id)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _can_manage(user: UserContext) -> bool:
    return bool(user.has_permission(MANAGE_PERMISSION))


def _host_only(url: str | None) -> str:
    """The host of a URL. No scheme, user, port, path or query."""
    if not url:
        return ""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


# ── The five sources ────────────────────────────────────────────────────────


def _registry_row(conn: store.Connection, user: UserContext) -> Row:
    """A row of the registry table. The Zoho CRM row is the first."""
    app = conn.owning_app if conn.owning_app in _KNOWN_APPS else "integrations"
    detail: dict[str, str] = {}
    location = conn.provider_meta.get("location") if conn.provider_meta else None
    if location:
        detail["location"] = str(location)[:40]
    if conn.scopes:
        detail["scopes"] = str(len(conn.scopes))
    org_scope = conn.scope_kind == store.SCOPE_ORG
    return Row(
        id=f"{conn.provider}:{conn.id}",
        kind="oauth",
        provider=conn.provider,
        label="Zoho CRM" if conn.provider == "zoho" else conn.provider.replace("_", " ").title(),
        owning_app=app,
        scope=conn.scope_kind,
        owner=(conn.member_email if not org_scope else conn.connected_by) or None,
        # CRM-X0: `needs_reset` is the CRM's own state. The console has one
        # word for "an admin must act in the app". Its link opens the CRM,
        # and the CRM's banner leads to the setup hub and the start over.
        status=(
            store.STATUS_NEEDS_RECONNECT if conn.status == store.STATUS_NEEDS_RESET
            else conn.status
        ),
        last_sync_at=_iso(conn.last_sync_at),
        last_error=store.safe_error_code(conn.last_error) if conn.last_error else None,
        setup_href=_SETUP_HREF[app],
        can_disconnect=org_scope and conn.status != store.STATUS_DISCONNECTED
        and _can_manage(user),
        detail=detail,
    )


_MAILBOX_SQL = (
    "SELECT id, user_id, provider, email_address, sync_status, sync_error, "
    "last_synced_at FROM email_accounts ORDER BY created_at"
)

_WHATSAPP_SQL = (
    "SELECT id, user_id, phone_number, display_name, provider, sync_status, "
    "last_synced_at FROM wa_accounts ORDER BY created_at"
)

_MAIL_PROVIDERS = {"gmail": "gmail", "microsoft": "outlook", "imap": "imap"}


def _mailbox_row(r: Any) -> Row:
    # The same test as the Email app (``accounts.needs_reconnect``).
    from gateway.routes.email.transport.accounts import needs_reconnect

    if needs_reconnect(r.sync_status, r.sync_error):
        status, err = "needs_reconnect", _ERR_AUTH
    elif r.sync_status == "error":
        status, err = "error", _ERR_SYNC
    else:
        status, err = "connected", None
    return Row(
        id=f"mailbox:{r.id}",
        kind="mailbox",
        provider=_MAIL_PROVIDERS.get(str(r.provider), str(r.provider or "imap")),
        label=str(r.email_address),
        owning_app="email",
        scope="member",
        owner=str(r.user_id) if r.user_id else None,
        status=status,
        last_sync_at=_iso(r.last_synced_at),
        last_error=err,
        setup_href=_SETUP_HREF["email"],
        can_disconnect=False,
        detail={"provider": str(r.provider or "")},
    )


def _whatsapp_row(r: Any) -> Row:
    failed = r.sync_status == "error"
    return Row(
        id=f"whatsapp:{r.id}",
        kind="whatsapp",
        provider="whatsapp",
        label=str(r.display_name or r.phone_number),
        owning_app="whatsapp",
        scope="member",
        owner=str(r.user_id) if r.user_id else None,
        status="error" if failed else "connected",
        last_sync_at=_iso(r.last_synced_at),
        last_error=_ERR_SYNC if failed else None,
        setup_href=_SETUP_HREF["whatsapp"],
        can_disconnect=False,
        detail={"provider": str(getattr(r, "provider", None) or "cloud")},
    )


def _is_mcp(conn: store.Connection) -> bool:
    """A registry row of an MCP server. :func:`_mcp_rows` lists it, once."""
    from gateway.connections import mcp as mcp_registry

    return (
        conn.provider.startswith(mcp_registry.PREFIX)
        and conn.owning_app == mcp_registry.OWNING_APP
    )


def _mcp_status(srv: Any) -> str:
    """The console status of one MCP server. IN-8b: an OAuth row whose
    refresh token died shows ``needs_reconnect``, not ``disconnected``, so an
    admin sees that a new consent repairs it (§3.6.7, O8)."""
    if getattr(srv, "status", None) == store.STATUS_NEEDS_RECONNECT:
        return store.STATUS_NEEDS_RECONNECT
    return "connected" if srv.enabled else "disconnected"


async def _mcp_rows(org: str, user: UserContext) -> list[Row]:
    """The MCP servers of *org*, from the registry (IN-4). Never a header."""
    from gateway.connections import mcp as mcp_registry

    out: list[Row] = []
    for srv in await mcp_registry.list_servers(org):
        detail = {
            "name": srv.name,
            "transport": srv.transport,
            "agent_scope": ", ".join(srv.agent_scope),
            # H-300: the console draws this key as a switch, not as text.
            "always_confirm": "true" if srv.always_confirm else "false",
        }
        host = _host_only(srv.url)
        if host:
            detail["host"] = host
        out.append(Row(
            id=f"mcp:{srv.name}",
            kind="mcp",
            provider="mcp",
            label=srv.label or srv.name,
            owning_app="integrations",
            scope="org",
            owner=None,
            status=_mcp_status(srv),
            # IN-8b: the safe code of a row that needs an admin, never a token.
            last_error=(store.safe_error_code(srv.last_error)
                        if srv.status == store.STATUS_NEEDS_RECONNECT and srv.last_error
                        else None),
            setup_href=_SETUP_HREF["integrations"],
            can_disconnect=_can_manage(user),
            detail=detail,
        ))
    return out


#: The names of one org's stored keys. No ``encrypted`` column, so no value.
_KEY_NAMES_SQL = (
    "SELECT provider, service FROM provider_keys "
    "WHERE organization_id = :org_id AND credential_type = 'integration' "
    "ORDER BY provider"
)


async def _key_names(org: str) -> list[dict[str, Any]]:
    """Through the one key-store query site of ``routes/integrations.py``."""
    return await integrations._db_query(_KEY_NAMES_SQL, org_id=org)


def _key_groups(names: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The stored providers of each service, by the prefix of the provider.

    The ONE grouping, for the list and for the delete, so a listed key row is
    removable exactly when the delete can reach it. A legacy row with no colon
    forms a group of its own and is never removable here.
    """
    groups: dict[str, list[str]] = {}
    for item in names:
        provider = str(item["provider"])
        groups.setdefault(provider.split(":", 1)[0], []).append(provider)
    return groups


def _key_removable(service: str, providers: list[str]) -> bool:
    return service in integrations._SETUP_GUIDES and all(":" in p for p in providers)


async def _key_rows(org: str, user: UserContext) -> list[Row]:
    """One row for each service that holds a stored key. Never a value."""
    out: list[Row] = []
    for service, providers in _key_groups(await _key_names(org)).items():
        guide = integrations._SETUP_GUIDES.get(service, {})
        out.append(Row(
            id=f"key:{service}",
            kind="api_key",
            provider=service,
            label=str(guide.get("label") or service),
            owning_app="integrations",
            scope="org",
            status="configured",
            setup_href=_SETUP_HREF["integrations"],
            can_disconnect=_can_manage(user) and _key_removable(service, providers),
            detail={"keys": str(len(providers)), "key_names": ", ".join(sorted(providers))},
        ))
    return out


async def build_rows(org: str, user: UserContext) -> tuple[list[Row], list[str]]:
    """Every connection of *org*, sorted, and the sources that failed to read."""
    rows: list[Row] = []
    unavailable: list[str] = []

    async def _source(name: str, read: Any) -> None:
        try:
            rows.extend(await read())
        except Exception as exc:  # one broken source must not blank the console
            # The class only: a DB message can echo a bound value.
            _log.warning("connections.source_failed", source=name, error=type(exc).__name__)
            unavailable.append(name)

    async def _registry() -> list[Row]:
        async with tenant_session(org) as db:
            conns = await store.list_all(db, org)
        # An MCP server is listed by ``_mcp`` in its own row shape (H).
        return [_registry_row(c, user) for c in conns if not _is_mcp(c)]

    async def _mailboxes() -> list[Row]:
        async with tenant_session(org) as db:
            return [_mailbox_row(r) for r in (await db.execute(text(_MAILBOX_SQL))).fetchall()]

    async def _numbers() -> list[Row]:
        async with tenant_session(org) as db:
            return [_whatsapp_row(r) for r in (await db.execute(text(_WHATSAPP_SQL))).fetchall()]

    async def _mcp() -> list[Row]:
        return await _mcp_rows(org, user)

    async def _keys() -> list[Row]:
        return await _key_rows(org, user)

    await _source("registry", _registry)
    await _source("mailboxes", _mailboxes)
    await _source("whatsapp", _numbers)
    await _source("mcp", _mcp)
    await _source("keys", _keys)
    rows.sort(key=lambda r: (r.owning_app, r.label.lower(), r.id))
    return rows, unavailable


# ── GET ─────────────────────────────────────────────────────────────────────


@router.get(
    "/connections", response_model=ConnectionsOut,
    dependencies=[require_permission(READ_PERMISSION)],
)
async def list_connections(
    user: UserContext = Depends(get_current_user),
) -> ConnectionsOut:
    """Every connection of the caller's organization, in one row shape.

    No credential, no message and no free text from a provider.
    """
    rows, unavailable = await build_rows(_org(user), user)
    return ConnectionsOut(connections=rows, unavailable=unavailable)


# ── Disconnect ──────────────────────────────────────────────────────────────


def _audit(user: UserContext, org: str, target: str, **payload: Any) -> None:
    """One audit event of a disconnect (spec §4.4), through the one helper."""
    integrations.audit_integration(
        user, org, target, action="integration.disconnect", **payload,
    )


def _member_refusal(app: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "member_connection", "setup_href": _SETUP_HREF.get(app, "/integrations")},
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Connection not found.")


def _split_id(connection_id: str) -> tuple[str, str]:
    prefix, sep, rest = connection_id.partition(":")
    if not sep or not prefix or not rest:
        raise _not_found()
    return prefix, rest


async def _member_row_exists(org: str, sql: str, key: str) -> bool:
    try:
        uuid.UUID(key)
    except ValueError:
        return False
    async with tenant_session(org) as db:
        found = (await db.execute(text(sql), {"id": key})).first()
    return found is not None


async def _disconnect_registry(org: str, provider: str, key: str, user: UserContext) -> Row:
    async with tenant_session(org) as db:
        conn = await store.get_by_id(db, org, key)
    if conn is None or conn.provider != provider:
        raise _not_found()
    if conn.scope_kind != store.SCOPE_ORG:
        raise _member_refusal(conn.owning_app)
    if conn.provider == "zoho" and conn.owning_app == "crm":
        from gateway.routes.crm.connection import disconnect_zoho

        after = await disconnect_zoho(org)
    elif conn.provider == "github" and conn.owning_app == "integrations":
        # IN-7: GitHub deletes the blob AND the old `provider_keys` row. GitHub
        # cannot revoke a device-flow token without the client secret, so there
        # is no revoke call to make.
        from gateway.connections import github as github_connection

        await github_connection.disconnect(org)
        async with tenant_session(org) as db:
            after = await store.get_by_id(db, org, conn.id)
    else:
        async with tenant_session(org) as db:
            after = await store.disconnect(db, org, conn.id)
    if after is None:
        raise _not_found()
    return _registry_row(after, user)


async def _disconnect_keys(org: str, service: str, user: UserContext) -> dict[str, Any]:
    providers = _key_groups(await _key_names(org)).get(service, [])
    if not providers or not _key_removable(service, providers):
        raise _not_found()
    suffixes = [p.split(":", 1)[1] for p in providers]
    # Check the whole request before the first delete, as the key route does.
    try:
        for suffix in suffixes:
            env_var = integrations._guide_env_var(service, suffix)
            if env_var is not None:
                integrations._refuse_unsafe_env_writes([(env_var, "")], actor=user.email)
                integrations._refuse_provider_key_without_byok([env_var])
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        raise HTTPException(status_code=409, detail={
            "code": "key_locked",
            "message": "Metorite locks this key, so an admin cannot remove it here.",
        }) from None
    for suffix in suffixes:
        await integrations.delete_integration_key(
            integrations.IntegrationKeyDelete(service=service, key_name=suffix), user=user,
        )
    return {"removed": True}


@router.post(
    "/connections/{connection_id}/disconnect",
    dependencies=[require_permission(MANAGE_PERMISSION)],
)
async def disconnect_connection(
    connection_id: str,
    user: UserContext = Depends(get_current_user),
) -> Any:
    """Disconnect an org connection. A member connection is 409.

    A Zoho row reuses the CRM disconnect. An MCP row reuses the MCP delete
    and a key row reuses the key delete. An id that is unknown, or that
    belongs to another organization, is 404.
    """
    org = _org(user)
    provider, key = _split_id(connection_id)
    if provider == "mailbox":
        if not await _member_row_exists(
            org, "SELECT 1 FROM email_accounts WHERE id = CAST(:id AS uuid)", key,
        ):
            raise _not_found()
        raise _member_refusal("email")
    if provider == "whatsapp":
        if not await _member_row_exists(
            org, "SELECT 1 FROM wa_accounts WHERE id = CAST(:id AS uuid)", key,
        ):
            raise _not_found()
        raise _member_refusal("whatsapp")
    if provider == "mcp":
        # IN-4: the registry row and any old ``mcp_servers`` row of the org.
        from gateway.connections import mcp as mcp_registry

        if not await mcp_registry.remove(org, key):
            raise _not_found()
        result: Any = {"removed": True}
    elif provider == "key":
        result = await _disconnect_keys(org, key, user)
    else:
        result = await _disconnect_registry(org, provider, key, user)
    _audit(user, org, connection_id, provider=provider, outcome="disconnected")
    return result


# ── MCP test, with the stored headers ───────────────────────────────────────


@router.post(
    "/mcp/{name}/test", response_model=McpTestOut,
    dependencies=[require_permission(MANAGE_PERMISSION)],
)
async def run_stored_mcp_test(
    name: str,
    user: UserContext = Depends(get_current_user),
) -> McpTestOut:
    """Test an MCP server of the caller's organization with its STORED headers.

    The list returns no header since IN-0, so the browser cannot send them.
    Since IN-4 the headers come out of the encrypted registry blob, through
    ``gateway.connections.mcp.get_one`` inside ``tenant_session(org)``.
    The GET goes through the one URL guard (``gateway/outbound_guard.py``). A
    refused URL sends no request. The answer holds a short code and never a
    header, a URL or a provider message.
    """
    from gateway.connections import mcp as mcp_registry

    org = _org(user)
    try:
        srv = await mcp_registry.get_one(org, name)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.test_read_failed", name=name, error=type(exc).__name__)
        return McpTestOut(ok=False, error="request_failed")
    if srv is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found.")
    url = srv.url
    if srv.transport == "stdio":
        return McpTestOut(ok=False, error="stdio_unsupported")
    if not url:
        return McpTestOut(ok=False, error="no_url")
    headers = dict(srv.headers)
    if srv.oauth:
        # WS-54 IN-8a (review of #931): the Test of an OAuth server sends the
        # stored bearer, the header that a run sends. With the flag off it
        # sends none, as before.
        try:
            bearer, extra = await mcp_registry.oauth_test_header(org, name)
        except Exception as exc:  # the class only
            _log.warning("mcp.test_read_failed", name=name, error=type(exc).__name__)
            return McpTestOut(ok=False, error="request_failed")
        if bearer == mcp_registry.TEST_BEARER_EXPIRED:
            return McpTestOut(ok=False, error="token_expired")
        headers.update(extra)
    headers.setdefault("Accept", "application/json")
    headers["Accept-Encoding"] = "identity"  # the guard keeps raw bytes
    try:
        answer = await outbound_guard.request("GET", url, headers=headers)
    except outbound_guard.OutboundRefused as exc:
        _log.warning("mcp.test_refused", name=name, host=exc.host, reason=exc.reason, actor=user.email)
        return McpTestOut(ok=False, error="timeout" if exc.reason == "timeout" else "url_refused")
    except httpx.ConnectError:
        return McpTestOut(ok=False, error="connect_failed")
    except Exception as exc:  # the class only: a message can echo a header
        _log.warning("mcp.test_failed", name=name, error=type(exc).__name__)
        return McpTestOut(ok=False, error="request_failed")
    code = answer.status_code
    if code in (401, 403):
        return McpTestOut(ok=False, error="unauthorized")
    if code >= 500:
        return McpTestOut(ok=False, error="server_error")
    return McpTestOut(ok=True)
