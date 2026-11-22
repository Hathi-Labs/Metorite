"""CSY-8 (task 3408): the daily use counter of an MCP server.

Spec: ``project-docs/specs/chat_connected_systems.md`` §3.7. One counter for
each org, server and UTC day, in Redis, through ``acb_common.tenant_redis``
(R5). The key holds a server name, a reason and a date. It never holds an
argument, a tool name, a result or a URL. The TTL is 8 days, so the panel reads
7 days and a counter that nobody reads ends by itself. No migration.

Keys (each one starts with ``cc:<org>:``):

* ``mcp-use:<server>:<YYYY-MM-DD>``: the chat calls of that day.
* ``mcp-use-last:<server>``: the epoch seconds of the last chat call.
* ``mcp-left-out:<server>:<reason>:<YYYY-MM-DD>``: the turns that left the
  server out, by reason (the closed list of ``server_brief.REASONS``).

A Redis fault never fails a call or a run. Each write logs the class of the
fault and nothing else. The read answers zeros and ``available: false``.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common._log import get_logger
from acb_common.tenant_redis import TenantKey, get_tenant_redis, organization_scope

_log = get_logger("acb_common.mcp_use")

USE_NAMESPACE = "mcp-use"
LAST_NAMESPACE = "mcp-use-last"
LEFT_OUT_NAMESPACE = "mcp-left-out"
DAYS = 7
TTL_SECONDS = 8 * 24 * 3600
#: A write that Redis does not answer in this time ends. The call goes on.
WRITE_DEADLINE_SECONDS = 1.0
#: ``server_brief.REASONS`` (a test holds the two equal).
LEFT_OUT_REASONS = ("mail", "unreachable", "reconnect", "no_card", "asks_each_call")

# The registry's own name rule (``gateway.connections.mcp.NAME_RE``).
_SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
_REASON_RE = re.compile(r"^[a-z_]{1,24}$")


def _client() -> Any:
    """The tenant client. A test replaces this one function."""
    return get_tenant_redis()


def _day(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%d")


def _utc(now: datetime | None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _fault(op: str, exc: BaseException) -> None:
    """The class of the fault only. A message can echo a value."""
    _log.warning("mcp_use.counter_fault", op=op, error=type(exc).__name__)


async def _bump(org: str, namespace: str, parts: tuple[str, ...], last: TenantKey | None,
                at: datetime) -> bool:
    """INCR, then EXPIRE on each write. A second write refreshes the TTL, so a fault
    between the two leaves no key that lives for ever."""
    r = _client()
    key = TenantKey(org, namespace, parts)
    await r.incr(key)
    await r.expire(key, TTL_SECONDS)
    if last is not None:
        await r.set(last, str(int(at.timestamp())), ex=TTL_SECONDS)
    return True


async def count_call(org: str, server: str, *, now: datetime | None = None) -> bool:
    """Add ONE to today's chat-call count of *server* in *org*, and keep the last call.

    Call it AFTER the call ran. Returns True when the count is written, and False
    when it is not (a bad name, no org, a fault). It never raises."""
    if not org or not _SERVER_RE.match(server or ""):
        return False
    at = _utc(now)
    try:
        with organization_scope(org):
            last = TenantKey(org, LAST_NAMESPACE, (server,))
            return await asyncio.wait_for(
                _bump(org, USE_NAMESPACE, (server, _day(at)), last, at),
                WRITE_DEADLINE_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _fault("count_call", exc)
        return False


async def count_left_out(org: str, server: str, reason: str, *, now: datetime | None = None) -> bool:
    """Add ONE to today's count of the turns that left *server* out, for *reason*."""
    if not org or not _SERVER_RE.match(server or "") or not _REASON_RE.match(reason or ""):
        return False
    at = _utc(now)
    try:
        with organization_scope(org):
            return await asyncio.wait_for(
                _bump(org, LEFT_OUT_NAMESPACE, (server, reason, _day(at)), None, at),
                WRITE_DEADLINE_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _fault("count_left_out", exc)
        return False


def _int(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def read_use(org: str, server: str, reasons: Sequence[str] = LEFT_OUT_REASONS, *,
                   now: datetime | None = None) -> dict[str, Any]:
    """The last 7 days (today and the 6 before, oldest first) of *server* in *org*.

    Zero-filled. On a fault it answers zeros and ``available: false``. The caller
    checked that *server* is a server of *org* (the route does). It never raises."""
    at = _utc(now)
    dates = [(at - timedelta(days=DAYS - 1 - i)).strftime("%Y-%m-%d") for i in range(DAYS)]
    reasons = tuple(reasons)
    calls = [0] * DAYS
    left = {r: [0] * DAYS for r in reasons}
    last_at: int | None = None
    available = False
    try:
        if org and _SERVER_RE.match(server or ""):
            with organization_scope(org):
                r = _client()
                keys = [TenantKey(org, USE_NAMESPACE, (server, d)) for d in dates]
                keys += [TenantKey(org, LEFT_OUT_NAMESPACE, (server, reason, d))
                         for reason in reasons for d in dates]
                keys.append(TenantKey(org, LAST_NAMESPACE, (server,)))
                values = await asyncio.wait_for(r.mget(keys), WRITE_DEADLINE_SECONDS)
            calls = [_int(v) for v in values[:DAYS]]
            for n, reason in enumerate(reasons):
                start = DAYS * (n + 1)
                left[reason] = [_int(v) for v in values[start:start + DAYS]]
            raw_last = values[-1]
            last_at = _int(raw_last) or None
            available = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _fault("read_use", exc)
        calls, left, last_at, available = [0] * DAYS, {r: [0] * DAYS for r in reasons}, None, False
    return {
        "server": server, "available": available,
        "days": [{"date": d, "calls": calls[i], "left_out": {r: left[r][i] for r in reasons}}
                 for i, d in enumerate(dates)],
        "calls_7d": sum(calls), "left_out_7d": {r: sum(left[r]) for r in reasons},
        "last_call_at": last_at,
    }
