"""The ``idempotency_key`` of a write tool (WS-55 EA-6d and EA-6c, spec §9.2).

Spec ``project-docs/specs/external_agent_access.md`` §9.2, "Rules of
``create_task``" and "Rules of ``add_comment``". Two tools use a slot:
``create_task`` (the key is optional) and ``add_comment`` (the key is
required).

**What it does.** The same key from the same connection, sent to the same
tool, gives the first result and makes one row. The edge keeps one slot for
each key, in the tenant-prefixed Redis wrapper (``acb_common.tenant_redis``,
R5). The slot key is
``cc:<organization>:agent_idem:<connection>:<tool>:<sha256 of the key>``. The
organization and the connection come from the token row of the call
(``TokenLookup``), never from a request input. The raw key goes into no
Redis key and no log line.

**The tool name is part of the slot key (EA-6c, EA3-35).** So one key sent to
``create_task`` and then to ``add_comment`` makes one task AND one comment.
Without the tool name, the second call read the slot of the first, and an
agent that reused a key across tools got a refusal.

**The three states of a slot.**

* ``pending``: a call holds the key and runs now. It lives
  :data:`PENDING_TTL_S`, which is longer than the tool limit
  (``tools.TOOL_TIMEOUT_S``). A second call with the key gets
  ``TEXT_KEY_RUNNING`` and sends nothing.
* ``done``: the call made the row. The slot holds the id of the row (the
  result id) and lives :data:`RESULT_TTL_S` (24 hours). A second call returns
  that row with ``created: false``.
* ``unknown``: the POST went out and no clear answer came back, so the edge
  cannot tell whether the row exists. The slot lives :data:`RESULT_TTL_S`,
  and a second call gets the unknown text of its tool. The agent then looks
  for the row, and it does not make a second one by accident.

A call that the edge or the gateway refused before any row existed releases
the slot, so the agent can send the key again.

**The race.** :meth:`Slot.claim` is one ``SET NX EX``. Of two calls with one
key at the same time, exactly one gets the slot. The other reads it.

**The fingerprint.** Each slot holds the SHA-256 of the other inputs of the
call. A second call whose inputs differ gets the reused-key text of its tool,
so one key never names two different rows.

**The residual.** When the edge process stops between the POST and the
write of the result, the slot stays ``pending`` until it expires. A call with
the key after that can make a second row. The pending life is short on
purpose: a long one would lock a key after every crash.

**A store outage.** :meth:`Slot.claim` raises :class:`StoreUnavailable`, and
the tool answers the busy text with no write. The other acts are best effort:
each one logs a failure and does not raise, because the write already
happened or never will.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acb_auth.agent_tokens import TokenLookup
from acb_common import get_logger, tenant_redis

_log = get_logger("agent_edge.idempotency")

#: The namespace of a slot in the tenant Redis wrapper.
NAMESPACE = "agent_idem"

#: The life of a ``pending`` slot. Longer than the 25 second tool limit, so a
#: live call always finishes inside it.
PENDING_TTL_S = 60

#: The life of a ``done`` or ``unknown`` slot: 24 hours.
RESULT_TTL_S = 24 * 60 * 60

#: The limit of one Redis act after the claim, so a slow store cannot hold a
#: tool call past its own limit.
STORE_TIMEOUT_S = 2.0

#: The shape of a key (§9.2). The tool input schema holds the same pattern.
KEY_PATTERN = "^[A-Za-z0-9._:-]{8,128}$"

PENDING = "pending"
DONE = "done"
UNKNOWN = "unknown"


class StoreUnavailable(Exception):
    """The claim could not reach the store. The tool sends no write."""


@dataclass(frozen=True)
class Record:
    """What an earlier call left in the slot. ``result_id`` is the id of the
    row that the call made: a task for ``create_task``, a comment for
    ``add_comment``."""

    state: str
    fingerprint: str
    result_id: str | None = None


def fingerprint(arguments: Mapping[str, Any]) -> str:
    """The hex SHA-256 of the sorted, compact JSON of *arguments*."""
    text = json.dumps(dict(arguments), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode(record: Record) -> str:
    return json.dumps({"state": record.state, "fingerprint": record.fingerprint,
                       "id": record.result_id}, separators=(",", ":"))


def _decode(raw: Any) -> Record:
    """A slot value as a :class:`Record`. A value the edge cannot read is
    ``unknown``, so it never lets a second row through."""
    try:
        data = json.loads(raw)
        state = data["state"]
        found = data["fingerprint"]
        result = data.get("id")
    except (TypeError, ValueError, KeyError, AttributeError):
        return Record(state=UNKNOWN, fingerprint="")
    if state not in (PENDING, DONE, UNKNOWN) or not isinstance(found, str):
        return Record(state=UNKNOWN, fingerprint="")
    return Record(state=state, fingerprint=found,
                  result_id=result if isinstance(result, str) else None)


class Slot:
    """The slot of one key of one connection, for one tool.

    *tool* is a keyword with no default, so a new caller cannot forget it and
    share the slots of another tool (EA3-35).
    """

    def __init__(self, caller: TokenLookup, key: str, *, tool: str) -> None:
        self._tenant = caller.organization_id
        self._connection = caller.connection_id
        self._tool = tool
        self._digest = hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _key(self) -> tenant_redis.TenantKey:
        return tenant_redis.key(NAMESPACE, self._connection, self._tool, self._digest)

    async def claim(self, fingerprint_: str) -> Record | None:
        """``None`` when this call now holds the slot. Else the record of the
        earlier call. Raises :class:`StoreUnavailable` on a store failure."""
        pending = _encode(Record(state=PENDING, fingerprint=fingerprint_))
        try:
            async with asyncio.timeout(STORE_TIMEOUT_S):
                return await self._claim(pending)
        except StoreUnavailable:
            raise
        except Exception as exc:
            _log.warning("agent_edge.idempotency_unavailable", tool=self._tool,
                         error=type(exc).__name__, connection_id=self._connection)
            raise StoreUnavailable() from None

    async def _claim(self, pending: str) -> Record | None:
        client = tenant_redis.get_tenant_redis()
        with tenant_redis.organization_scope(self._tenant):
            slot = self._key()
            # Two tries: the earlier slot can expire between the SET and the
            # GET. Then the second SET takes it.
            for _ in range(2):
                if await client.set(slot, pending, nx=True, ex=PENDING_TTL_S):
                    return None
                raw = await client.get(slot)
                if raw is not None:
                    return _decode(raw)
        raise StoreUnavailable()

    async def finish(self, fingerprint_: str, result_id: str) -> None:
        """Keep the id of the new row for :data:`RESULT_TTL_S`."""
        await self._put(Record(state=DONE, fingerprint=fingerprint_, result_id=result_id))

    async def mark_unknown(self, fingerprint_: str) -> None:
        """The POST went out, and no clear answer came back."""
        await self._put(Record(state=UNKNOWN, fingerprint=fingerprint_))

    async def release(self) -> None:
        """Free the slot: nothing was written. A plain ``DEL`` is safe here,
        because the pending life outlives the tool call that holds it, so no
        other call can hold the slot yet."""
        try:
            async with asyncio.timeout(STORE_TIMEOUT_S):
                client = tenant_redis.get_tenant_redis()
                with tenant_redis.organization_scope(self._tenant):
                    await client.delete(self._key())
        except Exception as exc:
            _log.warning("agent_edge.idempotency_release_failed",
                         error=type(exc).__name__, connection_id=self._connection)

    async def _put(self, record: Record) -> None:
        try:
            async with asyncio.timeout(STORE_TIMEOUT_S):
                client = tenant_redis.get_tenant_redis()
                with tenant_redis.organization_scope(self._tenant):
                    await client.set(self._key(), _encode(record), ex=RESULT_TTL_S)
        except Exception as exc:
            # The slot stays `pending` and expires. Log the state, never the key.
            _log.warning("agent_edge.idempotency_write_failed", state=record.state,
                         error=type(exc).__name__, connection_id=self._connection)


__all__ = [
    "DONE",
    "KEY_PATTERN",
    "NAMESPACE",
    "PENDING",
    "PENDING_TTL_S",
    "RESULT_TTL_S",
    "UNKNOWN",
    "Record",
    "Slot",
    "StoreUnavailable",
    "fingerprint",
]
