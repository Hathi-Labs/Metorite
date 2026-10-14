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
* **Always confirm (H-300).** ``provider_meta.always_confirm`` is the admin's
  switch: with it ``true``, every tool of the server needs the member's card,
  whatever its hints say (``orchestrator._maf_mcp.McpPermissionGate``). A new
  server gets ``true`` (:func:`save`). A row with no key reads as ``false``,
  so a server that was stored before H-300 keeps its behaviour, and the move
  of an old row writes ``false`` for the same reason. Any value but a real
  ``false`` reads as ``true``, so a damaged value fails closed.
  :func:`set_always_confirm` changes it on one row.
* **OAuth rows (IN-8a).** A row with ``provider_meta.auth = "oauth"`` holds
  tokens, not headers. A register keeps them (:func:`save`). A delete
  revokes the refresh token after the commit (:func:`remove`).
* **The refresh at run time (IN-8b, §3.6.7).** Before the connect, the run
  reader refreshes a token that ends within :data:`TOKEN_MARGIN_S`, through
  ``shared_token.fresh_credential``. That helper holds the row lock
  (``FOR UPDATE``) across the refresh, so two workers send ONE refresh and
  both get the new token. An ``invalid_grant`` sets ``needs_reconnect``. A
  401 on the connect sets it only when this run refreshed the token
  (:func:`mark_unauthorized`). :data:`REFRESH_BUDGET_S` caps the step.
* **No secret out.** No function here logs a header or puts one in an error.
  :class:`StoredServer` keeps the headers out of its ``repr``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import shared_token, store
from gateway.crm_sources.base import NeedsReconnect, SourceCredential, SourceError
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
#
# ⚠️ ``FOR UPDATE`` is load-bearing (review of #905). The move holds the lock
# of each old row until its transaction commits. :func:`remove` deletes the
# old row FIRST, so a delete that runs during a move waits for the move, and
# then its registry delete sees the moved row. Without the lock, a move could
# write a server back that an admin deleted a moment before.
_LEGACY_SQL = (
    "SELECT name, label, description, transport, url, headers, agent_scope, "
    "enabled FROM mcp_servers WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY created_at, name FOR UPDATE"
)

#: Once the registry holds the server, the old row keeps no secret at rest.
#: The row stays for one release (R6), with an empty header and env map.
_LEGACY_CLEAR_SQL = (
    "UPDATE mcp_servers SET headers = '{}'::jsonb, env_vars = '{}'::jsonb, "
    "updated_at = now() WHERE organization_id = CAST(:org AS uuid) "
    "AND name = :name AND (headers <> '{}'::jsonb OR env_vars <> '{}'::jsonb)"
)

_LEGACY_DELETE_SQL = (
    "DELETE FROM mcp_servers WHERE organization_id = CAST(:org AS uuid) "
    "AND name = :name"
)


class OAuthUrlChange(ValueError):
    """A register tried to change the URL of an OAuth server. Disconnect it
    first, because its token is for the old URL (review of #931, P2 1)."""


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
    #: H-300. False is the reading of a row with no key.
    always_confirm: bool = False
    #: IN-8a: the row holds OAuth tokens, not a header map. The admin in
    #: ``connected_by`` is who the connection acts as (D98).
    oauth: bool = False
    connected_by: str | None = None
    #: IN-8b: the registry status (``connected``, ``needs_reconnect`` or
    #: ``disconnected``) and its safe error code, for the console.
    status: str = store.STATUS_CONNECTED
    last_error: str | None = None
    #: IN-8b: THIS read refreshed the token of this OAuth row. A 401 on the
    #: connect then sets ``needs_reconnect`` (§3.6.7). Never set by a list.
    refreshed: bool = False


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
    agent_scope: Sequence[str], *, always_confirm: bool,
) -> dict[str, Any]:
    """The non-secret fields. No header name or value goes here."""
    return {
        "name": name,
        "label": label or name,
        "description": description or "",
        "transport": transport,
        "url": url,
        "agent_scope": list(agent_scope),  # [] stays [], no agent
        "always_confirm": bool(always_confirm),
    }


def _always_confirm(meta: dict[str, Any]) -> bool:
    """The H-300 switch of one row. No key reads as false (the H-299
    behaviour). A real ``false`` reads as false. Any other value reads as
    true, so a damaged value asks for the card."""
    if "always_confirm" not in meta:
        return False
    return meta["always_confirm"] is not False


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
        always_confirm=_always_confirm(meta),
        oauth=_is_oauth(conn),
        connected_by=conn.connected_by,
        status=conn.status,
        last_error=conn.last_error,
    )


def _is_oauth(conn: store.Connection) -> bool:
    """True when the row holds OAuth tokens (WS-54 IN-8a)."""
    return (conn.provider_meta or {}).get("auth") == store.AUTH_OAUTH


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

    Returns the number of rows moved. The old row stays, for one release,
    but its headers and env vars are emptied in the same transaction, so no
    plain secret stays at rest once the registry holds the server. A row that
    the registry already holds, under any status, is never moved again, so a
    second read moves nothing and a newer registration wins. The insert does
    nothing on a conflict, and the old rows are locked (``FOR UPDATE``), so two
    reads at one time move a row once, and a delete at the same time wins. A
    stdio row, or a row with no url, does not move (D85).
    """
    legacy = await _legacy_rows(db, org)
    if not legacy:
        return 0
    present = {c.provider for c in await store.list_by_prefix(db, org, PREFIX, OWNING_APP)}
    moved = 0
    for row in legacy:
        name = str(row.name)
        if provider_for(name) in present:
            await _clear_legacy(db, org, name)
            continue
        if row.transport not in _HTTP_TRANSPORTS or not row.url or not NAME_RE.match(name):
            _log.info("mcp.legacy_not_moved", name=name, transport=row.transport)
            continue
        saved = await store.save_headers(
            db, org, provider_for(name), OWNING_APP, _str_map(row.headers),
            # H-300: a moved server keeps its behaviour, so the switch is off.
            provider_meta=_meta(
                name, str(row.label or name), str(row.description or ""),
                str(row.transport), str(row.url), _scope(row.agent_scope),
                always_confirm=False,
            ),
            connected_by=MIGRATED_BY, enabled=bool(row.enabled), only_if_absent=True,
        )
        if saved is not None:
            moved += 1
            _log.info("mcp.legacy_moved", name=name)
        await _clear_legacy(db, org, name)
    return moved


async def _clear_legacy(db: Any, org: str, name: str) -> None:
    """Empty the plain headers and env vars of an old row the registry holds."""
    try:
        async with db.begin_nested():
            await db.execute(text(_LEGACY_CLEAR_SQL), {"org": org, "name": name})
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.legacy_clear_failed", error=type(exc).__name__)


# ── Reads ───────────────────────────────────────────────────────────────────


#: IN-8a: a run sends no token that ends within this many seconds.
_OAUTH_SKEW_S = 60
#: IN-8b (§3.6.7): before the connect, a run refreshes a token that ends
#: within this many seconds. 15 minutes covers a run, so the token never
#: changes inside one, and MAF never connects the session again.
TOKEN_MARGIN_S = 900
#: IN-8b: the most seconds for ONE token fetch of a refresh. The guard caps a
#: fetch at 10 s, and this cap is shorter. A fetch past it is not final: the
#: run keeps the stored token while it lives, and the next run tries again.
REFRESH_FETCH_S = 8.0
#: IN-8b: the most seconds that the refresh step of ONE server may cost a
#: run: the wait on the row lock (another worker's fetch), this worker's own
#: fetch, and the write. Past it, the server is left out of the run.
#: ⚠️ It is larger than two fetches, so the cap never cancels a write that
#: follows a good fetch. A cancel there would lose a rotated refresh token.
REFRESH_BUDGET_S = 20.0
#: IN-8b: the ``last_error`` of a row whose refreshed token got a 401.
UNAUTHORIZED = "unauthorized"


def _needs_refresh(credential: SourceCredential, now: datetime) -> bool:
    """True when the run must refresh *credential* first (§3.6.7).

    With a refresh token, inside :data:`TOKEN_MARGIN_S`. With none, the
    token cannot be refreshed, so only one that has ended goes on to the
    refresh, which stops it with ``no_refresh_token``."""
    margin = TOKEN_MARGIN_S if credential.refresh_token else _OAUTH_SKEW_S
    return credential.expires_at <= now + timedelta(seconds=margin)


def _source_error(code: str) -> SourceError:
    """The :class:`OAuthFailed` code as the error that ``fresh_credential``
    reads. A code that only a new consent repairs is :class:`NeedsReconnect`,
    so the helper sets ``needs_reconnect``. The provider codes, such as
    ``invalid_grant``, are in ``shared_token._STOP_CODES``."""
    from gateway.connections import mcp_oauth

    final = code in (mcp_oauth.NO_REFRESH_TOKEN, mcp_oauth.NO_TOKEN_ENDPOINT)
    err: SourceError = (NeedsReconnect if final else SourceError)(
        f"the MCP token refresh failed ({code})")
    err.code = code
    return err


async def _oauth_header(
    org: str, conn: store.Connection,
) -> tuple[dict[str, str], bool] | None:
    """The ``Authorization`` header of one OAuth row, and whether this call
    refreshed its token. ``None`` leaves the server out of the run.

    ⚠️ The refresh runs BEFORE the connect, and under the row lock of
    ``fresh_credential`` (O7). A refresh that fails for a reason that is not
    final, or that runs past :data:`REFRESH_BUDGET_S`, keeps the stored token
    when it still lives past :data:`_OAUTH_SKEW_S`. The token goes to the MCP
    client as an argument only, never to a log line.
    """
    from gateway.connections import mcp_oauth

    name = conn.provider[len(PREFIX):]
    seen: dict[str, Any] = {"refreshed": False, "stored": None}

    async def _refresh(credential: SourceCredential) -> SourceCredential:
        seen["stored"] = credential
        try:
            async with asyncio.timeout(REFRESH_FETCH_S):
                fresh = await mcp_oauth.refresh(credential)
        except TimeoutError:
            raise _source_error("timeout") from None
        except mcp_oauth.OAuthFailed as exc:
            raise _source_error(exc.code) from None
        seen["refreshed"] = True
        return fresh

    def _now() -> datetime:
        return datetime.now(UTC)

    credential: SourceCredential | None
    try:
        async with asyncio.timeout(REFRESH_BUDGET_S):
            credential = await shared_token.fresh_credential(
                org, provider=conn.provider, owning_app=OWNING_APP,
                refresh=_refresh, needs_refresh=_needs_refresh, now=_now,
            )
    except NeedsReconnect:
        _log.warning("mcp.oauth_needs_reconnect", name=name)
        return None
    except (SourceError, TimeoutError) as exc:
        # Not final: a throttle, a 5xx, a fault on the way, or the cap. The
        # row keeps its token, and the next run tries again.
        why = (getattr(exc, "code", "") or "") if isinstance(exc, SourceError) else "timeout"
        _log.warning("mcp.oauth_refresh_failed", name=name, code=store.safe_error_code(why))
        if seen["stored"] is None:  # the cap fired before the refresh began
            return None
        credential, seen["refreshed"] = seen["stored"], False
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.oauth_refresh_failed", name=name, error=type(exc).__name__)
        return None
    if credential is None or not credential.access_token:
        _log.warning("mcp.oauth_unreadable", name=name)
        return None
    if credential.expires_at <= _now() + timedelta(seconds=_OAUTH_SKEW_S):
        _log.info("mcp.oauth_expired", name=name)
        return None
    if seen["refreshed"]:
        _log.info("mcp.oauth_refreshed", name=name)
    return {"Authorization": f"Bearer {credential.access_token}"}, bool(seen["refreshed"])


async def _oauth_runtime_headers(
    org: str, conns: Sequence[store.Connection],
) -> dict[str, tuple[dict[str, str], bool]]:
    """The header of each OAuth row that a run may use, by provider, and
    whether this read refreshed its token (IN-8b).

    ⚠️ With ``MCP_OAUTH_ENABLED`` off, none: the run leaves the row out, logs
    ``mcp.oauth_disabled`` and sends no refresh (§3.6.7, O12). Each row gets
    its own session, because ``fresh_credential`` commits the new token with
    the row lock. The rows refresh at one time, so one slow provider costs a
    run :data:`REFRESH_BUDGET_S` at most.
    """
    oauth_rows = [c for c in conns if _is_oauth(c) and c.status == store.STATUS_CONNECTED]
    if not oauth_rows:
        return {}
    # Only here, after an OAuth row is found: a broken import of the OAuth
    # module must never drop the header servers of a run (review of #931).
    from gateway.connections import mcp_oauth

    if not mcp_oauth.oauth_enabled():
        for conn in oauth_rows:
            _log.info("mcp.oauth_disabled", name=conn.provider[len(PREFIX):])
        return {}
    found = await asyncio.gather(*(_oauth_header(org, conn) for conn in oauth_rows))
    return {conn.provider: got for conn, got in zip(oauth_rows, found, strict=True)
            if got is not None}


async def mark_unauthorized(org: str, name: str, sent: Mapping[str, str]) -> bool:
    """A 401 on the connect, after THIS run refreshed the token (§3.6.7, O14).

    Sets ``needs_reconnect`` with :data:`UNAUTHORIZED` on the row, under its
    row lock. Only when the row still holds the token that the run sent: a
    new consent or another refresh in the meantime keeps the row as it is.
    ``True`` when the row changed. The caller decides that this run refreshed
    the token, so a 401 with no refresh never comes here.
    """
    if not NAME_RE.match(name or ""):
        return False
    org = _org(org)
    async with tenant_session(org) as db:
        locked = await store.lock_row(db, org, provider_for(name), OWNING_APP)
        if locked is None:
            return False
        conn, credential = locked
        if (not _is_oauth(conn) or conn.status != store.STATUS_CONNECTED
                or credential is None or not credential.access_token):
            return False
        stored = f"Bearer {credential.access_token}".encode()
        if not hmac.compare_digest(stored, str(sent.get("Authorization", "")).encode()):
            return False
        await store.mark_needs_reconnect(db, org, conn.id, UNAUTHORIZED)
    _log.warning("mcp.oauth_needs_reconnect", name=name, code=UNAUTHORIZED)
    return True


#: The answers of :func:`oauth_test_header`.
TEST_BEARER_OK = "ok"
TEST_BEARER_EXPIRED = "token_expired"
TEST_BEARER_OFF = "disabled"
TEST_BEARER_UNREADABLE = "unreadable"


async def oauth_test_header(org: str, name: str) -> tuple[str, dict[str, str]]:
    """The header that the Test button sends for an OAuth server (#931, P2 3).

    It is the header that a run sends (:func:`_oauth_runtime_headers`), by
    the same rule. With the flag off, :data:`TEST_BEARER_OFF` and no header.
    A token that has ended, or ends within :data:`_OAUTH_SKEW_S`, is
    :data:`TEST_BEARER_EXPIRED`. The token goes to the caller only.
    """
    from gateway.connections import mcp_oauth

    if not mcp_oauth.oauth_enabled():
        return TEST_BEARER_OFF, {}
    org = _org(org)
    async with tenant_session(org) as db:
        credential = await store.load_credential(db, org, provider_for(name), OWNING_APP)
    if credential is None or not credential.access_token:
        return TEST_BEARER_UNREADABLE, {}
    if credential.expires_at <= datetime.now(UTC) + timedelta(seconds=_OAUTH_SKEW_S):
        return TEST_BEARER_EXPIRED, {}
    return TEST_BEARER_OK, {"Authorization": f"Bearer {credential.access_token}"}


async def _read(org: str, *, enabled_only: bool, with_headers: bool) -> list[StoredServer]:
    org = _org(org)
    async with tenant_session(org) as db:
        await migrate_legacy(db, org)
        conns = await store.list_by_prefix(db, org, PREFIX, OWNING_APP)
        headers = await store.load_headers_by_prefix(db, org, PREFIX, OWNING_APP)
    # IN-8b: after the read commits. Each OAuth row refreshes in its own
    # session, under its own row lock, before any connect.
    tokens = (await _oauth_runtime_headers(org, conns)) if enabled_only else {}
    out: list[StoredServer] = []
    for conn in conns:
        if enabled_only and conn.status != store.STATUS_CONNECTED:
            continue
        if _is_oauth(conn):
            # IN-8a. The console lists the row with no header. A run gets it
            # only with its bearer token (see ``_oauth_runtime_headers``).
            if enabled_only:
                if conn.provider not in tokens:
                    continue
                header, refreshed = tokens[conn.provider]
                out.append(replace(_server(conn, header, with_headers=with_headers),
                                   refreshed=refreshed))
            else:
                out.append(_server(conn, {}, with_headers=False))
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
    always_confirm: bool = True,
) -> StoredServer:
    """Add a server of *org*, or replace the one of that name.

    The caller checks the name, the transport and the URL first. The headers
    go into the blob only. *always_confirm* is the H-300 switch, and a new
    server gets ``True`` unless the caller says otherwise.

    An OAuth row (IN-8a) is not replaced. Its public keys merge in, and its
    tokens, ``auth`` and ``connected_by`` stay. A new *url* for it raises
    :class:`OAuthUrlChange`: the admin disconnects first (review of #931).
    """
    if not NAME_RE.match(name or ""):
        raise ValueError("the MCP server name does not match the name rule")
    if transport not in _HTTP_TRANSPORTS or not url:
        raise ValueError("an MCP server in the registry is http-sse with a url")
    org = _org(org)
    meta = _meta(name, label, description, transport, url, agent_scope,
                 always_confirm=always_confirm)
    async with tenant_session(org) as db:
        locked = await store.lock_row(db, org, provider_for(name), OWNING_APP)
        if locked is not None and _is_oauth(locked[0]):
            # Review of #931, P2 1. An OAuth row keeps its tokens and its
            # ``auth`` key, and only the public keys change. A new URL is
            # another server, and the token is for the old one.
            existing = locked[0]
            if str(existing.provider_meta.get("url") or "") != url:
                raise OAuthUrlChange(name)
            if headers:
                _log.info("mcp.oauth_headers_ignored", name=name)
            conn = await store.update_oauth_public(db, existing, meta, enabled=enabled)
            return _server(conn, {}, with_headers=False)
        conn = await store.save_headers(
            db, org, provider_for(name), OWNING_APP, headers,
            provider_meta=meta, connected_by=connected_by, enabled=enabled,
        )
    if conn is None:  # a replace always returns the row
        raise RuntimeError("the MCP server was not saved")
    return _server(conn, dict(headers or {}), with_headers=False)


async def set_always_confirm(org: str, name: str, value: bool) -> StoredServer | None:
    """Set the H-300 switch of the server *name* of *org*. ``None`` when *org*
    holds no such server.

    It changes the one key in ``provider_meta`` and nothing else: the headers,
    the status and the other fields stay. A disabled server can change too.
    An old ``mcp_servers`` row of *org* moves first, so the console can set the
    switch of a server that it listed. The statement names the org, and
    ``tenant_session(org)`` binds row level security, so another org's server
    of the same name is never changed.
    """
    if not NAME_RE.match(name or ""):
        return None
    org = _org(org)
    async with tenant_session(org) as db:
        await migrate_legacy(db, org)
        conn = await store.merge_meta_by_key(
            db, org, provider_for(name), OWNING_APP, {"always_confirm": bool(value)},
        )
    if conn is None:
        return None
    return _server(conn, {}, with_headers=False)


async def remove(org: str, name: str) -> bool:
    """Delete the server of *org*, and its old ``mcp_servers`` row.

    ``True`` when either existed. The old row goes too, or the next read
    would move it back.

    ⚠️ The ORDER is load-bearing (review of #905). The old row goes FIRST. A
    move holds that row's lock (``FOR UPDATE``) until it commits, so this
    delete waits for it. The registry delete is the next statement, and it
    sees the row that the move wrote, so the deleted server stays deleted.
    """
    if not NAME_RE.match(name or ""):
        return False
    org = _org(org)
    removed = False
    credential = None
    async with tenant_session(org) as db:
        locked = await store.lock_row(db, org, provider_for(name), OWNING_APP)
        if locked is not None:
            credential = locked[1]
        try:
            async with db.begin_nested():
                result = await db.execute(
                    text(_LEGACY_DELETE_SQL), {"org": org, "name": name},
                )
                removed = bool(result.rowcount)
        except Exception as exc:  # a missing old table removes nothing more
            _log.warning("mcp.legacy_delete_failed", error=type(exc).__name__)
        if await store.delete(db, org, provider_for(name), OWNING_APP):
            removed = True
    if removed and credential is not None:
        await _revoke_after_delete(name, credential)
    return removed


async def _revoke_after_delete(name: str, credential: Any) -> None:
    """Revoke the refresh token of a deleted OAuth row (review of #931, P2 2).

    Best effort, after the commit, through ``request_https``. The log line
    names the outcome only. A revoke that fails never fails the delete.
    """
    try:
        from gateway.connections import mcp_oauth

        outcome = await mcp_oauth.revoke(credential)
    except Exception as exc:  # the class only: a message can echo a URL
        outcome = f"failed:{type(exc).__name__}"
    _log.info("mcp.oauth_revoke", name=name, why="removed", outcome=outcome)
