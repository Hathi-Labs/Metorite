"""MCP servers as connections of one organization (WS-54 IN-4).

Spec: ``project-docs/specs/integrations_console.md`` §3.3, §3.4, §7.2 (IN-4)
and §7.3. The shape follows :mod:`gateway.connections.github`.

* **One row per server.** Each server is an org connection in the registry,
  through :mod:`gateway.connections.store`. The provider is ``mcp:<name>``
  and the owning app is ``integrations``. The unique index of migration 245
  includes the provider, so one org can hold many servers. :data:`NAME_RE`
  allows no colon, so ``mcp:<name>`` parses back to one name.
* **The header map is a credential.** It sits in the encrypted blob only (the
  second sealed shape of the store). The url, label, description, transport
  and agent scope are not secret, so they sit in ``provider_meta``.
  ``enabled`` is the status: ``connected`` or ``disconnected``. No stdio
  server runs (D85), so no ``env_vars`` and no command are stored.
* **One reader.** Each function opens ``tenant_session(org)`` for the
  organization that the caller names. The caller takes the org from the run
  binding or from its own tenant, never from request input (R5).
* **The old table (R6 expand).** Before IN-4 the rows sat in ``mcp_servers``,
  with the headers as plain JSONB. :func:`migrate_legacy` moves each old row
  of the org into the registry on a read, once, and leaves the old row. A
  delete removes the old row too, so a removed server cannot come back. A
  follow-up drops the table (HANDOFF H-301).
* **Room for H-300.** ``provider_meta.always_confirm`` is reserved for the
  "always confirm" setting that the owner has not decided. Nothing here
  writes or reads it yet.
* **No secret out.** No function here logs a header or puts one in an error.
  :class:`StoredServer` keeps the headers out of its ``repr``.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import store
from gateway.db import tenant_session

_log = get_logger("connections.mcp")

PREFIX = "mcp:"
OWNING_APP = "integrations"

#: The one name rule, shared with ``POST /integrations/mcp``.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")

#: The ``connected_by`` of a row that the move of an old row wrote.
MIGRATED_BY = "migrated-from-mcp-servers"

#: The transports that may reach the registry. D85 refuses stdio.
_HTTP_TRANSPORTS = frozenset({"http-sse", "http"})

# ``mcp_servers`` is exempt from RLS, so the ``organization_id`` predicate is
# the boundary of each statement on it.
_LEGACY_SQL = (
    "SELECT name, label, description, transport, url, headers, agent_scope, "
    "enabled FROM mcp_servers WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY created_at, name"
)

_LEGACY_DELETE_SQL = (
    "DELETE FROM mcp_servers WHERE organization_id = CAST(:org AS uuid) "
    "AND name = :name"
)


@dataclass(frozen=True)
class StoredServer:
    """One MCP server of one org. ``headers`` is empty unless asked for."""

    name: str
    label: str
    description: str
    transport: str
    url: str
    agent_scope: tuple[str, ...]
    enabled: bool
    header_names: tuple[str, ...]
    headers: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    created_at: datetime | None = None
    updated_at: datetime | None = None


def provider_for(name: str) -> str:
    return f"{PREFIX}{name}"


def _org(org: str) -> str:
    """The org as a UUID string. A malformed org is a ``ValueError``."""
    return str(uuid.UUID(str(org)))


def _json(raw: Any) -> Any:
    """A JSONB value. The asyncpg driver gives a string, psycopg a dict."""
    if isinstance(raw, str | bytes):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _str_map(raw: Any) -> dict[str, str]:
    value = _json(raw)
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _scope(raw: Any) -> tuple[str, ...]:
    """The agent scope. A missing value is ``*``, the old column default.

    ⚠️ An EMPTY list stays empty, and it gives the server to no agent, as
    ``_tool_injection._in_scope`` decided before IN-4. Widening it to ``*``
    would hand the server and its headers to every agent of the org.
    """
    value = _json(raw)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ("*",)
    return tuple(str(a) for a in value)


def _meta(
    name: str, label: str, description: str, transport: str, url: str,
    agent_scope: Sequence[str],
) -> dict[str, Any]:
    """The non-secret fields. No header name or value goes here."""
    return {
        "name": name,
        "label": label or name,
        "description": description or "",
        "transport": transport,
        "url": url,
        "agent_scope": list(agent_scope),  # [] stays [], no agent
    }


def _server(
    conn: store.Connection, headers: dict[str, str], *, with_headers: bool,
) -> StoredServer:
    meta = conn.provider_meta or {}
    name = conn.provider[len(PREFIX):]
    return StoredServer(
        name=name,
        label=str(meta.get("label") or name),
        description=str(meta.get("description") or ""),
        transport=str(meta.get("transport") or "http-sse"),
        url=str(meta.get("url") or ""),
        agent_scope=_scope(meta.get("agent_scope")),
        enabled=conn.status == store.STATUS_CONNECTED,
        header_names=tuple(sorted(headers)),
        headers=dict(headers) if with_headers else {},
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


# ── The move of an old row (R6 expand) ──────────────────────────────────────


async def _legacy_rows(db: Any, org: str) -> list[Any]:
    """The old rows of *org*. A missing table gives none (after the contract).

    The SAVEPOINT keeps the transaction usable when the read fails.
    """
    try:
        async with db.begin_nested():
            return list((await db.execute(text(_LEGACY_SQL), {"org": org})).fetchall())
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.legacy_read_failed", error=type(exc).__name__)
        return []


async def migrate_legacy(db: Any, org: str) -> int:
    """Move each old ``mcp_servers`` row of *org* that the registry lacks.

    Returns the number of rows moved. The old row stays, for one release. A
    row that the registry already holds, under any status, is never moved
    again, so a second read moves nothing and a newer registration wins. The
    insert does nothing on a conflict, so two reads at one time move a row
    once. A stdio row, or a row with no url, does not move (D85).
    """
    legacy = await _legacy_rows(db, org)
    if not legacy:
        return 0
    present = {c.provider for c in await store.list_by_prefix(db, org, PREFIX, OWNING_APP)}
    moved = 0
    for row in legacy:
        name = str(row.name)
        if provider_for(name) in present:
            continue
        if row.transport not in _HTTP_TRANSPORTS or not row.url or not NAME_RE.match(name):
            _log.info("mcp.legacy_not_moved", name=name, transport=row.transport)
            continue
        saved = await store.save_headers(
            db, org, provider_for(name), OWNING_APP, _str_map(row.headers),
            provider_meta=_meta(
                name, str(row.label or name), str(row.description or ""),
                str(row.transport), str(row.url), _scope(row.agent_scope),
            ),
            connected_by=MIGRATED_BY, enabled=bool(row.enabled), only_if_absent=True,
        )
        if saved is not None:
            moved += 1
            _log.info("mcp.legacy_moved", name=name)
    return moved


# ── Reads ───────────────────────────────────────────────────────────────────


async def _read(org: str, *, enabled_only: bool, with_headers: bool) -> list[StoredServer]:
    org = _org(org)
    async with tenant_session(org) as db:
        await migrate_legacy(db, org)
        conns = await store.list_by_prefix(db, org, PREFIX, OWNING_APP)
        headers = await store.load_headers_by_prefix(db, org, PREFIX, OWNING_APP)
    out: list[StoredServer] = []
    for conn in conns:
        if enabled_only and conn.status != store.STATUS_CONNECTED:
            continue
        sealed = headers.get(conn.provider, {})
        if sealed is None:
            # The blob does not open. An agent must not get the server with
            # its auth header missing. The console still lists it.
            _log.warning("mcp.headers_unreadable", name=conn.provider[len(PREFIX):])
            if enabled_only:
                continue
            sealed = {}
        out.append(_server(conn, sealed, with_headers=with_headers))
    return out


async def servers_for_org(org: str) -> list[StoredServer]:
    """The enabled servers of *org*, with their decrypted headers.

    The one runtime reader: the orchestrator calls it for a MAF run. The
    headers go to the MCP client as an argument, never to a log.
    """
    return await _read(org, enabled_only=True, with_headers=True)


async def list_servers(org: str) -> list[StoredServer]:
    """Every server of *org*, enabled and disabled. Header NAMES only."""
    return await _read(org, enabled_only=False, with_headers=False)


async def get_one(org: str, name: str) -> StoredServer | None:
    """One server of *org*, with its decrypted headers, or ``None``."""
    if not NAME_RE.match(name or ""):
        return None
    for server in await _read(org, enabled_only=False, with_headers=True):
        if server.name == name:
            return server
    return None


# ── Writes ──────────────────────────────────────────────────────────────────


async def save(
    org: str,
    *,
    name: str,
    url: str,
    headers: dict[str, str] | None,
    connected_by: str,
    label: str = "",
    description: str = "",
    transport: str = "http-sse",
    agent_scope: Sequence[str] = ("*",),
    enabled: bool = True,
) -> StoredServer:
    """Add a server of *org*, or replace the one of that name.

    The caller checks the name, the transport and the URL first. The headers
    go into the blob only.
    """
    if not NAME_RE.match(name or ""):
        raise ValueError("the MCP server name does not match the name rule")
    if transport not in _HTTP_TRANSPORTS or not url:
        raise ValueError("an MCP server in the registry is http-sse with a url")
    org = _org(org)
    async with tenant_session(org) as db:
        conn = await store.save_headers(
            db, org, provider_for(name), OWNING_APP, headers,
            provider_meta=_meta(name, label, description, transport, url, agent_scope),
            connected_by=connected_by, enabled=enabled,
        )
    if conn is None:  # a replace always returns the row
        raise RuntimeError("the MCP server was not saved")
    return _server(conn, dict(headers or {}), with_headers=False)


async def remove(org: str, name: str) -> bool:
    """Delete the server of *org*, and its old ``mcp_servers`` row.

    ``True`` when either existed. The old row goes too, or the next read
    would move it back.
    """
    if not NAME_RE.match(name or ""):
        return False
    org = _org(org)
    async with tenant_session(org) as db:
        removed = await store.delete(db, org, provider_for(name), OWNING_APP)
        try:
            async with db.begin_nested():
                result = await db.execute(
                    text(_LEGACY_DELETE_SQL), {"org": org, "name": name},
                )
                removed = removed or bool(result.rowcount)
        except Exception as exc:  # a missing old table removes nothing more
            _log.warning("mcp.legacy_delete_failed", error=type(exc).__name__)
    return removed
