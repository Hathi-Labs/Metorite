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
  (:func:`mark_unauthorized`). :data:`REFRESH_BUDGET_S` caps the wait,
  and :data:`REFRESH_SLOTS` caps the steps of one process.
* **No secret out.** No function here logs a header or puts one in an error.
  :class:`StoredServer` keeps the headers out of its ``repr``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
import uuid
import weakref
from collections.abc import Callable, Mapping, Sequence
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
#: changes inside one, and MAF never connects the session again. A token
#: that lives a shorter time gets half its life (:func:`_margin`).
TOKEN_MARGIN_S = 900
#: IN-8b: the most seconds for ONE token fetch of a refresh. The guard caps a
#: fetch at 10 s, and this cap is shorter. A fetch past it is not final: the
#: run keeps the stored token while it lives, and the next run tries again.
REFRESH_FETCH_S = 8.0
#: IN-8b: the most seconds that a run WAITS for the refresh step of one
#: server. Past it, the run goes on without the new token. The step itself
#: goes on in its own task and commits, so a cap or a Stop never cancels a
#: write that follows a good fetch (a cancel there would lose a rotated
#: refresh token, review of #1011). Only a process restart can still do so.
REFRESH_BUDGET_S = 20.0
#: IN-8b: the most refresh steps that ONE process runs at one time. Each step
#: holds a pool connection across its fetch, so the cap keeps a slow provider
#: from taking the pool of every org (review of #1011, P1). A step that waits
#: past :data:`REFRESH_BUDGET_S` for a slot sends nothing.
REFRESH_SLOTS = 2
#: IN-8b: the most of those slots that ONE org holds at one time. So an org
#: whose auth servers stall never keeps another org waiting (review 2 of
#: #1011, P1). An org takes its own slot first, then a slot of the process.
REFRESH_SLOTS_PER_ORG = 1
#: IN-8b: after a refresh fails for a reason that is not final, the process
#: does not try that server of that org again for this many seconds. A run
#: in the gap uses the stored token while it lives (review 2 of #1011, P1).
REFRESH_BACKOFF_S = 60.0
#: IN-8b: the ``last_error`` of a row whose refreshed token got a 401.
UNAUTHORIZED = "unauthorized"

#: One semaphore for each event loop: an ``asyncio.Semaphore`` binds to the
#: loop that first waits on it.
_SLOTS: weakref.WeakKeyDictionary[Any, asyncio.Semaphore] = weakref.WeakKeyDictionary()
#: The slot of each org, for each event loop.
_ORG_SLOTS: weakref.WeakKeyDictionary[Any, dict[str, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary())
#: The refresh steps that run on, after their run stopped waiting. A task
#: with no reference can be collected before it ends.
_INFLIGHT: set[asyncio.Task[Any]] = set()
#: (org, provider) -> the monotonic second when this process may try that
#: refresh again (:data:`REFRESH_BACKOFF_S`). Process-local, on purpose.
_BACKOFF: dict[tuple[str, str], float] = {}


def _utcnow() -> datetime:
    """The clock of the run-time refresh. A test moves it."""
    return datetime.now(UTC)


def _monotonic() -> float:
    """The clock of :data:`_BACKOFF`. A test moves it."""
    return time.monotonic()


def _margin(credential: SourceCredential) -> float:
    """Seconds before its end that a token is due (review 2 of #1011, P2).

    :data:`TOKEN_MARGIN_S`, or half the life of the token when that is
    shorter, and never less than :data:`_OAUTH_SKEW_S`. A token that lives
    300 s is then due at 150 s, not at once, so a second worker that reads
    the new token under the lock does not refresh it again. A blob with no
    issue time (stored before this rule) keeps :data:`TOKEN_MARGIN_S`. A
    token with no refresh token gets the skew only."""
    if not credential.refresh_token:
        return _OAUTH_SKEW_S
    if credential.issued_at is None:
        return TOKEN_MARGIN_S
    life = (credential.expires_at - credential.issued_at).total_seconds()
    return max(_OAUTH_SKEW_S, min(TOKEN_MARGIN_S, life / 2))


def _needs_refresh(credential: SourceCredential, now: datetime) -> bool:
    """True when the run must refresh *credential* first (§3.6.7).

    Inside :func:`_margin`. The unlocked check of :func:`_oauth_plan` and the
    check under the lock of ``fresh_credential`` both call it. With no
    refresh token, only a token that has ended goes on to the refresh, which
    stops it with ``no_refresh_token``."""
    return credential.expires_at <= now + timedelta(seconds=_margin(credential))


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


def _bearer(credential: SourceCredential) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential.access_token}"}


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _SLOTS.get(loop)
    if sem is None:
        sem = _SLOTS[loop] = asyncio.Semaphore(REFRESH_SLOTS)
    return sem


def _org_slots(org: str) -> asyncio.Semaphore:
    by_org = _ORG_SLOTS.setdefault(asyncio.get_running_loop(), {})
    sem = by_org.get(org)
    if sem is None:
        sem = by_org[org] = asyncio.Semaphore(REFRESH_SLOTS_PER_ORG)
    return sem


def _backed_off(org: str, provider: str) -> bool:
    until = _BACKOFF.get((org, provider))
    if until is None:
        return False
    if _monotonic() >= until:
        _BACKOFF.pop((org, provider), None)
        return False
    return True


def _settle(task: asyncio.Task[Any]) -> None:
    """Forget a finished step, and read its outcome so asyncio never logs an
    exception that nobody retrieved (the message could hold nothing secret,
    but a log line per step is noise)."""
    _INFLIGHT.discard(task)
    if not task.cancelled():
        task.exception()


async def _refresh_step(
    org: str, conn: store.Connection, seen: dict[str, Any],
) -> SourceCredential | None:
    """ONE refresh step, in its own task: a slot, then ``fresh_credential``.

    The wait for a slot is capped, and nothing has happened before it, so a
    cap there is safe. Once the step holds a slot it runs to its end: the
    lock wait is bounded by the other holder's capped fetch, and the fetch by
    :data:`REFRESH_FETCH_S`."""
    from gateway.connections import mcp_oauth

    async def _refresh(credential: SourceCredential) -> SourceCredential:
        seen["stored"] = credential
        try:
            async with asyncio.timeout(REFRESH_FETCH_S):
                fresh = await mcp_oauth.refresh(credential, now=_utcnow())
        except TimeoutError:
            raise _source_error("timeout") from None
        except mcp_oauth.OAuthFailed as exc:
            raise _source_error(exc.code) from None
        seen["refreshed"] = True
        return fresh

    org_slots, slots = _org_slots(org), _slots()
    try:
        async with asyncio.timeout(REFRESH_BUDGET_S):
            # The org's own slot first, so an org that waits holds no slot
            # of the process (review 2 of #1011, P1).
            await org_slots.acquire()
            try:
                await slots.acquire()
            except BaseException:
                org_slots.release()
                raise
    except TimeoutError:
        raise _source_error("busy") from None
    try:
        fresh = await shared_token.fresh_credential(
            org, provider=conn.provider, owning_app=OWNING_APP,
            refresh=_refresh, needs_refresh=_needs_refresh, now=_utcnow,
            # A server deleted and registered again with headers in the
            # wait is no longer this row (review 2 of #1011).
            accept=_is_oauth,
        )
    except NeedsReconnect:
        raise
    except SourceError:
        # Not final, and the provider's fault: wait before the next try.
        _BACKOFF[(org, conn.provider)] = _monotonic() + REFRESH_BACKOFF_S
        raise
    finally:
        slots.release()
        org_slots.release()
    _BACKOFF.pop((org, conn.provider), None)
    return fresh


async def _oauth_header(
    org: str, conn: store.Connection, stored: SourceCredential | None,
) -> tuple[dict[str, str], bool] | None:
    """The header of one OAuth row that is due for a refresh, and whether
    this call refreshed its token. ``None`` leaves the server out of the run.

    ⚠️ The refresh runs BEFORE the connect, under the row lock of
    ``fresh_credential`` (O7), in a task that the run waits on for
    :data:`REFRESH_BUDGET_S` at most. A refresh that fails for a reason that
    is not final, or that runs past the cap, keeps *stored* (the token that
    the unlocked read found) when it still lives past :data:`_OAUTH_SKEW_S`.
    The token goes to the MCP client as an argument only, never to a log line.
    """
    name = conn.provider[len(PREFIX):]
    seen: dict[str, Any] = {"refreshed": False, "stored": stored}
    task = asyncio.create_task(_refresh_step(org, conn, seen))
    _INFLIGHT.add(task)
    task.add_done_callback(_settle)
    credential: SourceCredential | None
    try:
        async with asyncio.timeout(REFRESH_BUDGET_S):
            credential = await asyncio.shield(task)
    except NeedsReconnect:
        _log.warning("mcp.oauth_needs_reconnect", name=name)
        return None
    except (SourceError, TimeoutError) as exc:
        # Not final: a throttle, a 5xx, a fault on the way, or the cap. The
        # row keeps its token, and the next run tries again.
        why = (getattr(exc, "code", "") or "") if isinstance(exc, SourceError) else "timeout"
        _log.warning("mcp.oauth_refresh_failed", name=name, code=store.safe_error_code(why))
        credential = seen["stored"]
        seen["refreshed"] = False
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("mcp.oauth_refresh_failed", name=name, error=type(exc).__name__)
        return None
    if credential is None or not credential.access_token:
        _log.warning("mcp.oauth_unreadable", name=name)
        return None
    if credential.expires_at <= _utcnow() + timedelta(seconds=_OAUTH_SKEW_S):
        _log.info("mcp.oauth_expired", name=name)
        return None
    if seen["refreshed"]:
        _log.info("mcp.oauth_refreshed", name=name)
    return _bearer(credential), bool(seen["refreshed"])


#: The type of the ``oauth_wanted`` filter of :func:`servers_for_org`.
OAuthWanted = Callable[["StoredServer"], bool]


async def _oauth_plan(
    db: Any, org: str, conns: Sequence[store.Connection], wanted: OAuthWanted | None,
) -> tuple[dict[str, tuple[dict[str, str], bool]],
           list[tuple[store.Connection, SourceCredential | None]]]:
    """Split the OAuth rows of a run, inside the read session (IN-8b).

    Returns the header of each row whose token is good past the margin, and
    the rows that are due for a refresh. The check is an UNLOCKED read, so a
    run that refreshes nothing takes no second session and no row lock
    (review of #1011, P1).

    ⚠️ With ``MCP_OAUTH_ENABLED`` off, none: the run leaves the row out, logs
    ``mcp.oauth_disabled`` and sends no refresh (§3.6.7, O12). A row that
    *wanted* refuses (another agent's scope, a grant the run lacks, a Copilot
    agent) is left out and never refreshed (review of #1011, P2 1).
    """
    rows = [c for c in conns if _is_oauth(c) and c.status == store.STATUS_CONNECTED]
    if not rows:
        return {}, []
    # Only here, after an OAuth row is found: a broken import of the OAuth
    # module must never drop the header servers of a run (review of #931).
    from gateway.connections import mcp_oauth

    if not mcp_oauth.oauth_enabled():
        for conn in rows:
            _log.info("mcp.oauth_disabled", name=conn.provider[len(PREFIX):])
        return {}, []
    ready: dict[str, tuple[dict[str, str], bool]] = {}
    due: list[tuple[store.Connection, SourceCredential | None]] = []
    now = _utcnow()
    edge = now + timedelta(seconds=_OAUTH_SKEW_S)
    for conn in rows:
        name = conn.provider[len(PREFIX):]
        if wanted is not None and not wanted(_server(conn, {}, with_headers=False)):
            _log.info("mcp.oauth_not_wanted", name=name)
            continue
        credential = await store.load_credential(db, org, conn.provider, OWNING_APP)
        live = credential is not None and bool(credential.access_token)
        if live and not _needs_refresh(credential, now):
            ready[conn.provider] = (_bearer(credential), False)
        elif _backed_off(org, conn.provider):
            # A refresh of this server failed a moment ago: no new try yet.
            _log.info("mcp.oauth_refresh_backoff", name=name)
            if live and credential.expires_at > edge:
                ready[conn.provider] = (_bearer(credential), False)
        else:
            due.append((conn, credential))
    return ready, due


async def _oauth_refresh_due(
    org: str, due: Sequence[tuple[store.Connection, SourceCredential | None]],
) -> dict[str, tuple[dict[str, str], bool]]:
    """The header of each due row, after its refresh. The rows wait at one
    time, so one slow provider costs a run :data:`REFRESH_BUDGET_S` at most,
    and :data:`REFRESH_SLOTS` bounds the pool connections they hold."""
    if not due:
        return {}
    found = await asyncio.gather(*(_oauth_header(org, conn, cred) for conn, cred in due))
    return {conn.provider: got for (conn, _), got in zip(due, found, strict=True)
            if got is not None}


#: The answers of :func:`mark_unauthorized`.
MARK_NEEDS_RECONNECT = "needs_reconnect"
MARK_EXPIRED = "expired"
MARK_UNCHANGED = "unchanged"


async def mark_unauthorized(
    org: str, name: str, sent: Mapping[str, str], *, refreshed: bool,
) -> str:
    """A 401 on the connect of an OAuth server (§3.6.7, O14).

    Under the row lock, and only when the row still holds the token that the
    run sent: a new consent or another refresh in the meantime keeps the row
    as it is (:data:`MARK_UNCHANGED`).

    * *refreshed* (THIS run refreshed the token), or a token with no refresh
      token: ``needs_reconnect`` with :data:`UNAUTHORIZED`
      (:data:`MARK_NEEDS_RECONNECT`). Only a new consent repairs it.
    * Else the stored token ends now (:data:`MARK_EXPIRED`). The next run
      refreshes it, and an ``invalid_grant`` then marks the row. So a token
      whose answer had no ``expires_in`` (stored for ten years) and that the
      provider ended cannot stay dead for ever (review 2 of #1011, P1).
    """
    if not NAME_RE.match(name or ""):
        return MARK_UNCHANGED
    org = _org(org)
    async with tenant_session(org) as db:
        locked = await store.lock_row(db, org, provider_for(name), OWNING_APP)
        if locked is None:
            return MARK_UNCHANGED
        conn, credential = locked
        if (not _is_oauth(conn) or conn.status != store.STATUS_CONNECTED
                or credential is None or not credential.access_token):
            return MARK_UNCHANGED
        stored = f"Bearer {credential.access_token}".encode()
        if not hmac.compare_digest(stored, str(sent.get("Authorization", "")).encode()):
            return MARK_UNCHANGED
        if refreshed or not credential.refresh_token:
            await store.mark_needs_reconnect(db, org, conn.id, UNAUTHORIZED)
            outcome = MARK_NEEDS_RECONNECT
        else:
            await store.update_tokens(db, org, conn.id,
                                      replace(credential, expires_at=_utcnow()))
            outcome = MARK_EXPIRED
    if outcome == MARK_NEEDS_RECONNECT:
        _log.warning("mcp.oauth_needs_reconnect", name=name, code=UNAUTHORIZED)
    else:
        _log.info("mcp.oauth_token_ended", name=name)
    return outcome


#: The answers of :func:`oauth_test_header`.
TEST_BEARER_OK = "ok"
TEST_BEARER_EXPIRED = "token_expired"
TEST_BEARER_OFF = "disabled"
TEST_BEARER_UNREADABLE = "unreadable"


async def oauth_test_header(org: str, name: str) -> tuple[str, dict[str, str]]:
    """The header that the Test button sends for an OAuth server (#931, P2 3).

    It is the header that a run sends, by the same rule (IN-8c). A token
    that is due goes through :func:`_oauth_header`, the refresh step of a
    run: the row lock, the slots, the budget and the backoff. So a Test after
    a 401 that ended the stored token refreshes it, and a refresh that
    answers ``invalid_grant`` sets ``needs_reconnect``.

    With the flag off, :data:`TEST_BEARER_OFF` and no header. A row in
    ``needs_reconnect`` sends nothing, as a run sends nothing, and is
    :data:`TEST_BEARER_EXPIRED`. So is a token that the refresh could not
    renew and that ends within :data:`_OAUTH_SKEW_S`. A disabled row gets no
    refresh, because no run uses it: it sends the stored token while that
    token lives, as before IN-8c. The token goes to the caller only.
    """
    from gateway.connections import mcp_oauth

    if not mcp_oauth.oauth_enabled():
        return TEST_BEARER_OFF, {}
    org = _org(org)
    provider = provider_for(name)
    async with tenant_session(org) as db:
        conns = await store.list_by_prefix(db, org, PREFIX, OWNING_APP)
        conn = next((c for c in conns if c.provider == provider), None)
        if conn is None or not _is_oauth(conn):
            return TEST_BEARER_UNREADABLE, {}
        if conn.status == store.STATUS_NEEDS_RECONNECT:
            return TEST_BEARER_EXPIRED, {}
        credential = await store.load_credential(db, org, provider, OWNING_APP)
    if credential is None or not credential.access_token:
        return TEST_BEARER_UNREADABLE, {}
    now = _utcnow()
    if (conn.status == store.STATUS_CONNECTED and _needs_refresh(credential, now)
            and not _backed_off(org, provider)):
        # The read session is closed: the step opens its own, under the lock.
        got = await _oauth_header(org, conn, credential)
        if got is None:
            return TEST_BEARER_EXPIRED, {}
        return TEST_BEARER_OK, got[0]
    if credential.expires_at <= now + timedelta(seconds=_OAUTH_SKEW_S):
        return TEST_BEARER_EXPIRED, {}
    return TEST_BEARER_OK, _bearer(credential)


async def _read(
    org: str, *, enabled_only: bool, with_headers: bool,
    oauth_wanted: OAuthWanted | None = None,
) -> list[StoredServer]:
    org = _org(org)
    due: list[tuple[store.Connection, SourceCredential | None]] = []
    tokens: dict[str, tuple[dict[str, str], bool]] = {}
    async with tenant_session(org) as db:
        await migrate_legacy(db, org)
        conns = await store.list_by_prefix(db, org, PREFIX, OWNING_APP)
        headers = await store.load_headers_by_prefix(db, org, PREFIX, OWNING_APP)
        if enabled_only:
            tokens, due = await _oauth_plan(db, org, conns, oauth_wanted)
    # IN-8b: after the read commits. A due row refreshes in its own session,
    # under its own row lock, before any connect.
    tokens.update(await _oauth_refresh_due(org, due))
    out: list[StoredServer] = []
    for conn in conns:
        if enabled_only and conn.status != store.STATUS_CONNECTED:
            continue
        if _is_oauth(conn):
            # IN-8a. The console lists the row with no header. A run gets it
            # only with its bearer token (see ``_oauth_plan``).
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


async def servers_for_org(
    org: str, *, oauth_wanted: OAuthWanted | None = None,
) -> list[StoredServer]:
    """The enabled servers of *org*, with their decrypted headers.

    The one runtime reader: the orchestrator calls it for a MAF run. The
    headers go to the MCP client as an argument, never to a log.

    *oauth_wanted* (IN-8b) says which OAuth rows the run can use. A row that
    it refuses is left out BEFORE its refresh, so a run never refreshes a
    token for a server that it never connects. ``None`` wants every row.
    """
    return await _read(org, enabled_only=True, with_headers=True,
                       oauth_wanted=oauth_wanted)


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
