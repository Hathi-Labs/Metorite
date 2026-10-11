"""The read model of the Integrations console (WS-54 IN-1).

Spec: ``project-docs/specs/integrations_console.md`` §3.2, §3.5, §4 and §7.2.

    GET   /integrations/connections
    POST  /integrations/connections/{id}/disconnect
    POST  /integrations/mcp/{name}/test

**One row shape over five sources.** the registry table (Zoho, and
the next providers), ``email_accounts``, ``wa_accounts``, ``mcp_servers`` and
``provider_keys``. The console never sees a credential. Each SELECT below names
its columns, and none names a blob, a header value, a key value or a message
column (D-EM-4). A free text from a provider never reaches ``last_error``:
only a short code does.

**Tenancy (R5).** The organization comes from the server-side ``UserContext``,
never from the request. the registry table, ``email_accounts`` and
``wa_accounts`` are read inside ``tenant_session(org)`` (FORCE RLS binds the
tenant). ``mcp_servers`` and ``provider_keys`` are RLS-exempt, so their
``organization_id`` predicate is the boundary.

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
        status=conn.status,
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


def _mcp_rows(org: str, user: UserContext) -> list[Row]:
    """The MCP servers of *org*. Key names only, never a header value."""
    from acb_graph import get_session

    with get_session() as s:
        rows = s.execute(text(integrations._MCP_LIST_SQL), {"org": org}).fetchall()
    out: list[Row] = []
    for r in rows:
        name, label, _desc, transport, _cmd, url = r[0], r[1], r[2], r[3], r[4], r[5]
        scope = r[8] or ["*"]
        detail = {
            "name": str(name),
            "transport": str(transport or ""),
            "agent_scope": ", ".join(str(a) for a in scope),
        }
        host = _host_only(url)
        if host:
            detail["host"] = host
        out.append(Row(
            id=f"mcp:{name}",
            kind="mcp",
            provider="mcp",
            label=str(label or name),
            owning_app="integrations",
            scope="org",
            owner=None,
            status="connected" if r[9] else "disconnected",
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


async def _key_rows(org: str, user: UserContext) -> list[Row]:
    """One row for each service that holds a stored key. Never a value."""
    names = await _key_names(org)
    by_service: dict[str, int] = {}
    for item in names:
        service = str(item.get("service") or str(item["provider"]).split(":", 1)[0])
        by_service[service] = by_service.get(service, 0) + 1
    out: list[Row] = []
    for service, count in by_service.items():
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
            can_disconnect=_can_manage(user),
            detail={"keys": str(count)},
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
            return [_registry_row(c, user) for c in await store.list_all(db, org)]

    async def _mailboxes() -> list[Row]:
        async with tenant_session(org) as db:
            return [_mailbox_row(r) for r in (await db.execute(text(_MAILBOX_SQL))).fetchall()]

    async def _numbers() -> list[Row]:
        async with tenant_session(org) as db:
            return [_whatsapp_row(r) for r in (await db.execute(text(_WHATSAPP_SQL))).fetchall()]

    async def _mcp() -> list[Row]:
        return _mcp_rows(org, user)

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
    """One audit event: who, what, when, which organization (spec §4.4)."""
    try:
        from acb_audit import AuditEvent, record

        record(AuditEvent(
            actor=f"user:{user.email}",
            action="integration.disconnect",
            target=target,
            payload={"organization_id": org, **payload},
            organization_id=org,
        ))
    except Exception as exc:  # an audit backend that is down must not block this
        _log.warning("connections.audit_failed", error=type(exc).__name__)


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
    else:
        async with tenant_session(org) as db:
            after = await store.disconnect(db, org, conn.id)
    if after is None:
        raise _not_found()
    return _registry_row(after, user)


async def _disconnect_keys(org: str, service: str, user: UserContext) -> dict[str, Any]:
    names = await _key_names(org)
    suffixes = [
        str(n["provider"]).split(":", 1)[1] for n in names
        if str(n["provider"]).split(":", 1)[0] == service and ":" in str(n["provider"])
    ]
    if not suffixes or service not in integrations._SETUP_GUIDES:
        raise _not_found()
    # Check the whole request before the first delete, as the key route does.
    for suffix in suffixes:
        env_var = integrations._guide_env_var(service, suffix)
        if env_var is not None:
            integrations._refuse_unsafe_env_writes([(env_var, "")], actor=user.email)
            integrations._refuse_provider_key_without_byok([env_var])
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
        await integrations.remove_mcp_server(key, user=user)
        result: Any = {"removed": True}
    elif provider == "key":
        result = await _disconnect_keys(org, key, user)
    else:
        result = await _disconnect_registry(org, provider, key, user)
    _audit(user, org, connection_id, provider=provider, outcome="disconnected")
    return result


# ── MCP test, with the stored headers ───────────────────────────────────────

_MCP_STORED_SQL = (
    "SELECT transport, url, headers FROM mcp_servers "
    "WHERE organization_id = :org AND name = :name"
)


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
    The GET goes through the one URL guard (``gateway/outbound_guard.py``). A
    refused URL sends no request. The answer holds a short code and never a
    header, a URL or a provider message.
    """
    from acb_graph import get_session

    org = _org(user)
    with get_session() as s:
        row = s.execute(text(_MCP_STORED_SQL), {"org": org, "name": name}).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found.")
    transport, url, stored = row[0], row[1], row[2]
    if transport == "stdio":
        return McpTestOut(ok=False, error="stdio_unsupported")
    if not url:
        return McpTestOut(ok=False, error="no_url")
    headers = {str(k): str(v) for k, v in (stored or {}).items()} if isinstance(stored, dict) else {}
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
