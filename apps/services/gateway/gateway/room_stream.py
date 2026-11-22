"""The room's own event channel — what happens between runs, and who did it.

Spec: ``docs/multiplayer/README.md`` §4.4 ("a second stream, deliberately").

``cc:stream:{thread_id}`` carries one agent run and is **deleted at every run
boundary** (``stream_relay.mark_active(reset=True)``) so that replay-from-zero
covers exactly the current run. That is right for a run and useless for a room:
who joined, who is watching, who typed what, and which agents are present all
have to survive the next run starting.

So rooms get their own stream with the opposite lifecycle — never reset, TTL
refreshed on every write. Two streams with two lifecycles is the honest model;
one stream would force a choice between losing run-replay exactness and losing
the room's memory of itself.

    cc:<org>:room:{thread_id}       room events, never reset, 24h idle TTL
    cc:<org>:presence:{thread_id}   hash of email -> last-seen epoch seconds

Presence is a hash rather than one key per person because the only question
ever asked is "who is here", which is one ``HGETALL`` instead of a scan. Stale
entries are pruned on read rather than by a sweeper: a browser that dies stops
heartbeating, and the next person to look is the one who cares.

Event names mirror AG-UI's SCREAMING_SNAKE convention so the frontend
translator handles run events and room events with one switch.

The tenant (task 570, H5, R5). Both keys go through the tenant Redis wrapper
(``acb_common.tenant_redis``). Each function takes the org of the caller's
session as ``organization_id``, never a request field. The org binds around
each command only, never across a ``yield``. With no org, a write writes
nothing and a read reads nothing. The live tail blocks for 15 s, so it reads
on the blocking pool (``get_tenant_redis(blocking=True)``), as the activity
feed does. Keys of the old shape ``cc:room:*`` and ``cc:presence:*`` are not
read. The transcript (``chat_message``) is the durable record, presence lives
45 s, and the room stream is a live layer, so the release drops nothing that
a member needs. Fence: ``tests/unit/test_room_stream_tenant.py``.
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from acb_common import get_logger
from acb_common.tenant_redis import TenantKey, get_tenant_redis, key, organization_scope

_log = get_logger("gateway.room_stream")

#: The namespaces of the two keys in the tenant wrapper.
ROOM_NAMESPACE = "room"
PRESENCE_NAMESPACE = "presence"

#: Bounded so a long-lived room cannot grow without limit. The transcript is
#: the durable record (chat_message); this stream is the live layer, and a
#: client that falls further behind than this refetches history instead.
ROOM_STREAM_MAXLEN = 2_000
ROOM_STREAM_TTL_SECONDS = 24 * 3600

#: A heartbeat lands every 15s from an open tab; three missed beats and you are
#: considered gone. Long enough to survive a slow tab, short enough that the
#: presence rail is not a list of ghosts.
PRESENCE_TTL_SECONDS = 45


def room_key(thread_id: str) -> TenantKey:
    """``cc:<org>:room:<thread>``. Call it with the org bound."""
    return key(ROOM_NAMESPACE, thread_id)


def presence_key(thread_id: str) -> TenantKey:
    """``cc:<org>:presence:<thread>``. Call it with the org bound."""
    return key(PRESENCE_NAMESPACE, thread_id)


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

async def publish_room_event(
    thread_id: str, event: dict[str, Any], *, organization_id: str | None,
) -> str | None:
    """Append one event to a room's stream. Never raises.

    Room events are a live convenience layer over a durable transcript, so a
    Redis blip must degrade the presence rail, never fail the request that was
    trying to do real work (send a message, add a participant).
    *organization_id* is the org of the caller's session. With none, the
    event is not written.
    """
    if not thread_id or not organization_id:
        return None
    payload = {**event, "threadId": thread_id}
    payload.setdefault("ts", int(time.time() * 1000))
    try:
        r = get_tenant_redis()
        with organization_scope(organization_id):
            eid = await r.xadd(
                room_key(thread_id),
                {"event": json.dumps(payload, default=str)},
                maxlen=ROOM_STREAM_MAXLEN,
                approximate=True,
            )
            await r.expire(room_key(thread_id), ROOM_STREAM_TTL_SECONDS)
        return str(eid)
    except Exception:
        _log.warning(
            "room_stream.publish_failed",
            thread_id=thread_id, event_type=event.get("type"), exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _parse(eid: str, fields: dict[str, str]) -> dict[str, Any] | None:
    raw = fields.get("event")
    if not raw:
        return None
    try:
        evt = json.loads(raw)
    except Exception:
        return None
    evt["_room_id"] = eid
    return evt


async def replay_room_events(
    thread_id: str, since_id: str = "0", limit: int = 500,
    *, organization_id: str | None,
) -> list[dict[str, Any]]:
    """Everything after ``since_id``. ``"0"`` means the whole retained window.
    With no *organization_id*, nothing."""
    if not thread_id or not organization_id:
        return []
    try:
        r = get_tenant_redis()
        with organization_scope(organization_id):
            entries = await r.xread({room_key(thread_id): since_id}, count=limit)
    except Exception:
        _log.warning("room_stream.replay_failed", thread_id=thread_id, exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for _key, items in entries or []:
        for eid, fields in items:
            evt = _parse(eid, fields)
            if evt is not None:
                out.append(evt)
    return out


async def subscribe_room_events(
    thread_id: str, since_id: str = "$", block_ms: int = 15_000,
    *, organization_id: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """Live tail. Yields until the caller stops consuming.

    Unlike ``subscribe_events`` for run streams there is no terminal event — a
    room does not end, it goes quiet. The caller (the SSE endpoint) decides when
    to stop, which is when the client disconnects.

    *organization_id* is the org of the caller's session, taken before the
    stream starts. The org binds around each read only, never across a
    ``yield``, so the consumer's context never holds it. With none, nothing.
    """
    if not thread_id or not organization_id:
        return
    cursor = since_id
    try:
        r = get_tenant_redis(blocking=True)
    except Exception:
        _log.warning("room_stream.subscribe_failed", thread_id=thread_id, exc_info=True)
        return
    while True:
        try:
            with organization_scope(organization_id):
                entries = await r.xread(
                    {room_key(thread_id): cursor}, block=block_ms, count=100,
                )
        except Exception:
            _log.warning("room_stream.subscribe_failed", thread_id=thread_id, exc_info=True)
            return
        if not entries:
            yield {"type": "ROOM_PING", "threadId": thread_id}
            continue
        for _key, items in entries:
            for eid, fields in items:
                cursor = eid
                evt = _parse(eid, fields)
                if evt is not None:
                    yield evt


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------

async def touch_presence(
    thread_id: str, email: str, *, state: str = "viewing", organization_id: str | None,
) -> None:
    """Record that this person is in this room right now. Never raises.
    With no *organization_id*, nothing is written."""
    if not thread_id or not email or not organization_id:
        return
    try:
        r = get_tenant_redis()
        with organization_scope(organization_id):
            await r.hset(
                presence_key(thread_id), email,
                json.dumps({"at": int(time.time()), "state": state}),
            )
            # The hash outlives any single beat but not an empty room.
            await r.expire(presence_key(thread_id), ROOM_STREAM_TTL_SECONDS)
    except Exception:
        _log.debug("room_stream.presence_failed", thread_id=thread_id, exc_info=True)


async def clear_presence(
    thread_id: str, email: str, *, organization_id: str | None,
) -> None:
    """Drop someone from the rail immediately (an explicit leave/close)."""
    if not thread_id or not email or not organization_id:
        return
    try:
        with organization_scope(organization_id):
            await get_tenant_redis().hdel(presence_key(thread_id), email)
    except Exception:
        _log.debug("room_stream.presence_clear_failed", thread_id=thread_id, exc_info=True)


async def read_presence(
    thread_id: str, *, organization_id: str | None,
) -> list[dict[str, Any]]:
    """Who is here, freshest first, with entries older than the TTL pruned.

    Pruning on read (rather than relying on per-field expiry, which Redis
    hashes do not have) keeps the rail honest without a background sweeper.
    With no *organization_id*, nobody.
    """
    if not thread_id or not organization_id:
        return []
    try:
        r = get_tenant_redis()
        with organization_scope(organization_id):
            raw = await r.hgetall(presence_key(thread_id))
    except Exception:
        _log.debug("room_stream.presence_read_failed", thread_id=thread_id, exc_info=True)
        return []

    now = int(time.time())
    live: list[dict[str, Any]] = []
    stale: list[str] = []
    for email, blob in (raw or {}).items():
        try:
            rec = json.loads(blob)
            at = int(rec.get("at", 0))
            state = str(rec.get("state", "viewing"))
        except Exception:
            stale.append(email)
            continue
        if now - at > PRESENCE_TTL_SECONDS:
            stale.append(email)
            continue
        live.append({"email": email, "at": at, "state": state})

    if stale:
        try:
            with organization_scope(organization_id):
                await r.hdel(presence_key(thread_id), *stale)
        except Exception:
            _log.debug("room_stream.presence_prune_failed", exc_info=True)

    live.sort(key=lambda p: p["at"], reverse=True)
    return live
