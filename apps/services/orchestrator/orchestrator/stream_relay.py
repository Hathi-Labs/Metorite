"""Per-thread Redis Stream relay for durable agent event buffering.

Enables fire-and-forget agent runs with live reconnection: every SSE event
the agent emits is pushed to a Redis Stream keyed by ``thread_id``.  When a
client reconnects after a disconnect it can replay missed events from its
last-known event ID and then subscribe to new events live.

Stream naming
-------------
    cc:stream:{thread_id}     — ordered event log (MAXLEN ~10 000)
    cc:active:{thread_id}     — "1" while running, deleted on finish

Event IDs
---------
Each event pushed gets a Redis auto-generated ID (``<ms>-<seq>``).
The frontend tracks the last ID it received and passes it to the reconnect
endpoint so only events *after* that ID are replayed.

TTL
---
Streams expire after 1 hour (configurable via ``STREAM_TTL_SECONDS``).
If a client reconnects after the stream expired, it falls back to the
existing Postgres polling recovery path.

Usage::

    from orchestrator.stream_relay import (
        push_event, replay_events, subscribe_events,
        mark_active, mark_inactive,
    )

    await mark_active(thread_id)
    async for sse_line in run_agent_stream(...):
        await push_event(thread_id, sse_line)
        yield sse_line
    await mark_inactive(thread_id)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from acb_common import get_logger, get_settings

_log = get_logger("orchestrator.stream_relay")

STREAM_PREFIX = "cc:stream"
ACTIVE_PREFIX = "cc:active"
# Who started the in-flight run on a thread. Kept in its own key rather than in
# the ACTIVE value because ``is_active``/``touch_active``/``push_event`` all
# compare or rewrite that value as the literal "1"; overloading it would make
# every one of those sites actor-aware for no benefit.
#
# This exists so a SECOND person cannot destroy a FIRST person's run. A new run
# on an already-active thread cancels the previous one and wipes its event log
# (``run_detached`` → ``mark_active(reset=True)``) — correct when you supersede
# your own run (steer/retry/Quick action), catastrophic the moment two people
# share a thread. See docs/multiplayer/README.md §3.3 / §5.2.
RUN_ACTOR_PREFIX = "cc:runactor"
# What KIND of turn is in flight, recorded from the run's ``source`` (the string
# the executor already correlates every run by: "chat" for a person, "workflow"
# / "schedule" / "webhook" / "event" for something that fired on its own).
# Steering reads it for §QM-1's one carve-out: a human message arriving during
# an automation turn starts its OWN run rather than folding into a cron.
RUN_SOURCE_PREFIX = "cc:runsource"
# The participant roster this run's authority intersection was folded over at
# run start (groups_sessions_authority.md §3). A steer is admitted only from
# somebody already inside it — see ``orchestrator.steer.is_inside_run_floor``
# for why a participant added mid-run must wait for the turn to end.
RUN_FLOOR_PREFIX = "cc:runfloor"
# Cap on entries per thread stream. Every SSE frame is one entry — reasoning
# models emit one entry per token delta, so a long tool-heavy turn can run to
# tens of thousands of entries. Trimming evicts the OLDEST entries (RUN_STARTED
# + the head of the answer), which truncates both reconnect replay and the
# run-end fold that persists the turn to Postgres (audit R1) — so the cap is
# generous and env-tunable rather than tight. ~200B/entry → 50k ≈ 10MB per
# live thread, bounded by the 1h TTL.
STREAM_MAXLEN = int(os.environ.get("STREAM_RELAY_MAXLEN", "50000"))
STREAM_TTL_SECONDS = 3600  # 1 hour


def _stream_key(thread_id: str) -> str:
    return f"{STREAM_PREFIX}:{thread_id}"


def _active_key(thread_id: str) -> str:
    return f"{ACTIVE_PREFIX}:{thread_id}"


def _run_actor_key(thread_id: str) -> str:
    return f"{RUN_ACTOR_PREFIX}:{thread_id}"


def _run_source_key(thread_id: str) -> str:
    return f"{RUN_SOURCE_PREFIX}:{thread_id}"


def _run_floor_key(thread_id: str) -> str:
    return f"{RUN_FLOOR_PREFIX}:{thread_id}"


class SupersedeRefused(RuntimeError):
    """Raised when a run start would destroy a transcript it does not own.

    The §5.2 correctness fix has two layers and this is the inner one. The route
    (``_refuse_if_another_run_is_active``) turns the situation into a 409 with a
    product choice; this exception exists so the *destructive statement itself*
    — ``mark_active(reset=True)``, which DELETEs ``cc:stream:{thread_id}`` — is
    unreachable by a party who is not the legitimate superseder, no matter which
    call site reaches ``run_detached``.

    Defense in depth rather than belt-and-braces: ``run_detached`` has more than
    one caller (``/agent/run/stream`` and ``/copilot/chat``), and a future third
    one that forgets the route guard would otherwise re-open a silent data-loss
    bug. A guard at the route is a policy; a guard here is an invariant.
    """

    def __init__(self, thread_id: str, owner: str, actor: str) -> None:
        super().__init__(
            f"{owner} owns the run in flight on this thread; "
            f"{actor or 'an anonymous caller'} may not supersede it."
        )
        self.thread_id = thread_id
        self.owner = owner
        self.actor = actor


# Shared, process-wide async Redis client with an internal connection pool.
# A single agent run emits hundreds of events; opening/closing a TCP connection
# per call (the old behaviour) churns ephemeral ports and can exhaust Redis
# maxclients under load.  redis.asyncio multiplexes concurrent commands over the
# pool and is safe to share across coroutines, so we create it once and never
# close it per-call (process exit tears it down).
_client: aioredis.Redis | None = None


async def _get_client() -> aioredis.Redis:
    """Return the shared pooled async Redis client (created once)."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=64,
            health_check_interval=30,
        )
    return _client


async def push_event(thread_id: str, event: dict[str, Any]) -> str:
    """Append a single event to the per-thread stream.

    Args:
        thread_id: Conversation thread ID.
        event:     Dict with at least ``"type"``; serialised as JSON.

    Returns:
        The Redis entry ID (e.g. ``"1719123456789-0"``).
    """
    r = await _get_client()
    try:
        eid = await r.xadd(
            _stream_key(thread_id),
            {"event": json.dumps(event, default=str)},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        # Refresh TTL on every write so an active stream doesn't expire.
        await r.expire(_stream_key(thread_id), STREAM_TTL_SECONDS)
        # Refresh the ACTIVE flag too: it is set once with ex=STREAM_TTL and,
        # without this, lapses mid-run on any run longer than the TTL (easy
        # with tool/HITL budgets) — subscribers then terminate and reconnect
        # reports a still-running agent as finished (synthetic RUN_FINISHED).
        # Only refresh when the flag exists (xx=True): a cancelled/finished
        # run must not be resurrected by a late in-flight push.
        await r.set(_active_key(thread_id), "1", ex=STREAM_TTL_SECONDS, xx=True)
        return eid
    finally:
        pass  # shared pooled client — never closed per-call


async def replay_events(
    thread_id: str,
    since_id: str = "0-0",
    count: int = 500,
    *,
    drain: bool = False,
) -> list[dict[str, Any]]:
    """Read events from *since_id* (exclusive) to the current stream tail.

    Args:
        thread_id: Conversation thread ID.
        since_id:  Redis stream ID to start AFTER (use ``"0-0"`` for all).
        count:     Max events per Redis batch. With ``drain=False`` this is also
                   the hard cap on the total returned (single XREAD).
        drain:     When ``True``, keep reading successive batches (advancing the
                   cursor past the last-seen ID) until the stream is exhausted,
                   so the FULL run is returned regardless of length. Use this for
                   run-boundary persistence — a long reasoning stream emits one
                   Redis event per delta and can exceed any single-batch cap, so
                   without draining the tail (final answer / late tool events /
                   trailing chain-of-thought) would be silently dropped from the
                   persisted message. ``count`` still bounds each batch (memory),
                   just not the total.

    Returns:
        List of parsed event dicts, oldest first.
    """
    r = await _get_client()
    try:
        events: list[dict[str, Any]] = []
        cursor = since_id
        while True:
            raw = await r.xread(
                {_stream_key(thread_id): cursor},
                count=count,
                block=0,  # don't block — return immediately if no new events
            )
            if not raw:
                break

            batch = 0
            for _stream_name, entries in raw:
                for eid, fields in entries:
                    batch += 1
                    cursor = eid  # advance past this entry for the next batch
                    try:
                        evt = json.loads(fields.get("event", "{}"))
                        # Attach Redis stream ID for cursor tracking.
                        evt["_stream_id"] = eid
                        events.append(evt)
                    except (json.JSONDecodeError, TypeError):
                        _log.warning(
                            "stream_relay.bad_event",
                            thread_id=thread_id[:12],
                            eid=eid[:20],
                        )
            # Stop unless draining AND the batch was full (more may remain).
            if not drain or batch < count:
                break
        return events
    except aioredis.ResponseError:
        # Stream doesn't exist (expired or never created).
        return []
    finally:
        pass  # shared pooled client — never closed per-call


async def subscribe_events(
    thread_id: str,
    since_id: str = "$",
    block_ms: int = 30_000,
) -> AsyncIterator[dict[str, Any]]:
    """Subscribe to new events on the per-thread stream, blocking for up to
    *block_ms* between events.  Yields parsed event dicts as they arrive.

    Exits when the stream is marked inactive AND no new events arrive within
    *block_ms*, when a terminal event (RUN_FINISHED / RUN_ERROR) is yielded,
    or when the stream expires.

    Args:
        thread_id: Conversation thread ID.
        since_id:  Stream ID (``"$"`` = new only, ``"0"`` = all).
        block_ms:  Max milliseconds to block waiting for a new event.

    Yields:
        Parsed event dicts with ``_stream_id`` attached.
    """
    r = await _get_client()
    try:
        cursor = since_id
        while True:
            # Check if the agent is still active before blocking.
            active = await r.get(_active_key(thread_id))
            if active != "1":
                # Agent finished — drain any remaining events, then exit.
                try:
                    raw = await r.xread(
                        {_stream_key(thread_id): cursor},
                        count=100,
                        block=1000,  # short block to drain tail
                    )
                    if raw:
                        for _sn, entries in raw:
                            for eid, fields in entries:
                                try:
                                    evt = json.loads(fields.get("event", "{}"))
                                    evt["_stream_id"] = eid
                                    yield evt
                                    cursor = eid
                                except (json.JSONDecodeError, TypeError):
                                    pass
                except aioredis.ResponseError:
                    pass
                return  # stream finished

            # Block for new events.
            try:
                raw = await r.xread(
                    {_stream_key(thread_id): cursor},
                    count=100,
                    block=block_ms,
                )
                if raw:
                    for _sn, entries in raw:
                        for eid, fields in entries:
                            try:
                                evt = json.loads(fields.get("event", "{}"))
                                evt["_stream_id"] = eid
                                yield evt
                                cursor = eid
                                # Terminal event: the run is over — close
                                # the subscription NOW instead of blocking
                                # another XREAD cycle (the HTTP response
                                # would otherwise linger ~30s, leaving the
                                # UI "loading" and queueing new messages).
                                if evt.get("type") in (
                                    "RUN_FINISHED", "RUN_ERROR",
                                ):
                                    return
                            except (json.JSONDecodeError, TypeError):
                                pass
                else:
                    # Timeout — re-check active status next loop.
                    # If stream expired, xread returns empty; exit.
                    exists = await r.exists(_stream_key(thread_id))
                    if not exists:
                        return
            except aioredis.ResponseError:
                # Stream disappeared.
                return
    finally:
        pass  # shared pooled client — never closed per-call


async def mark_active(
    thread_id: str,
    *,
    reset: bool = False,
    actor: str | None = None,
    source: str | None = None,
    floor: list[str] | None = None,
) -> None:
    """Mark a thread's agent as currently running.

    Args:
        thread_id: Conversation thread ID.
        reset:     When True, delete any existing event stream first so the
                   stream contains ONLY the current run's events.  This
                   makes ``replay_events(since_id="0-0")`` always correct
                   for the run in progress (previous turns live in
                   Postgres, not Redis).
        actor:     Who started this run (an email).  Recorded so a later
                   caller can tell whether superseding this run would be
                   somebody destroying their OWN work or somebody else's —
                   see :func:`get_run_actor`.  Omitted/empty leaves no actor
                   recorded, which callers must treat as "unknown, allow".
        source:    The run's correlation source ("chat", "workflow",
                   "schedule", "webhook", …).  Recorded so a message arriving
                   mid-run can tell a conversation from a cron and take
                   §QM-1's automation carve-out.  See :func:`get_run_source`.
        floor:     The participant roster this run's authority intersection was
                   folded over.  Recorded so a steer from somebody who was not
                   in that fold can be refused rather than silently moving the
                   floor under a turn already executing.
    """
    r = await _get_client()
    try:
        if reset:
            await r.delete(_stream_key(thread_id))
        await r.set(_active_key(thread_id), "1", ex=STREAM_TTL_SECONDS)
        if actor:
            await r.set(_run_actor_key(thread_id), actor, ex=STREAM_TTL_SECONDS)
        else:
            # A run with no known actor must not inherit the previous one's,
            # or an anonymous/internal run would look like it belongs to
            # whoever ran last and could be refused on their behalf.
            await r.delete(_run_actor_key(thread_id))
        # Same reasoning for both of these: a fresh run must never inherit the
        # previous one's kind or roster, or a chat turn following a cron would
        # be read as a cron, and a steer would be checked against a floor that
        # belonged to a run that has already ended.
        if source:
            await r.set(
                _run_source_key(thread_id), source, ex=STREAM_TTL_SECONDS,
            )
        else:
            await r.delete(_run_source_key(thread_id))
        if floor:
            await r.set(
                _run_floor_key(thread_id),
                json.dumps(sorted({str(m) for m in floor})),
                ex=STREAM_TTL_SECONDS,
            )
        else:
            await r.delete(_run_floor_key(thread_id))
    finally:
        pass  # shared pooled client — never closed per-call


async def get_run_actor(thread_id: str) -> str | None:
    """Who owns the in-flight run on *thread_id*, or None if unknown.

    None means "no run, or a run we can't attribute" — never "nobody owns it".
    Callers must fail OPEN on None (allow the supersede), matching
    ``_thread_owner_ok``'s permissive contract: a Redis hiccup or a legacy run
    started before actors were recorded must not block a legitimate retry.
    """
    try:
        r = await _get_client()
        return await r.get(_run_actor_key(thread_id)) or None
    except Exception:  # attribution is advisory, never a blocker
        _log.warning("stream_relay.get_run_actor_failed", thread_id=thread_id[:12])
        return None


async def get_run_source(thread_id: str) -> str:
    """The in-flight run's correlation source, or ``""`` when unknown.

    ``""`` and ``"chat"`` both mean "treat this as a conversation": an
    unattributable run must never be mistaken for a cron, because that would
    silently turn a steer into a second concurrent run.
    """
    try:
        r = await _get_client()
        return await r.get(_run_source_key(thread_id)) or ""
    except Exception:  # noqa: BLE001 — advisory, never a blocker
        _log.warning("stream_relay.get_run_source_failed",
                     thread_id=thread_id[:12])
        return ""


async def get_run_floor(thread_id: str) -> list[str] | None:
    """The participant roster the in-flight run's authority was folded over.

    ``None`` means "not recorded" — a solo run, a run predating this key, or a
    Redis miss — and every caller must fail OPEN on it, matching
    :func:`get_run_actor`'s contract.
    """
    try:
        r = await _get_client()
        raw = await r.get(_run_floor_key(thread_id))
        if not raw:
            return None
        parsed = json.loads(raw)
        return [str(m) for m in parsed] if isinstance(parsed, list) else None
    except Exception:  # noqa: BLE001
        _log.warning("stream_relay.get_run_floor_failed",
                     thread_id=thread_id[:12])
        return None


async def mark_inactive(thread_id: str) -> None:
    """Mark a thread's agent as finished."""
    r = await _get_client()
    try:
        await r.delete(_active_key(thread_id))
        # Drop the actor with the active flag: a finished run has no owner, so
        # the next caller must not be refused against a stale one.
        await r.delete(_run_actor_key(thread_id))
        # Kind and roster are facts about a RUN, not about a thread — a
        # finished run must not lend either to whatever starts next.
        await r.delete(_run_source_key(thread_id))
        await r.delete(_run_floor_key(thread_id))
        # Also refresh the stream TTL so late reconnectors can still replay.
        await r.expire(_stream_key(thread_id), STREAM_TTL_SECONDS)
    finally:
        pass  # shared pooled client — never closed per-call


async def is_active(thread_id: str) -> bool:
    """Check whether an agent is still running for *thread_id*."""
    r = await _get_client()
    try:
        val = await r.get(_active_key(thread_id))
        return val == "1"
    finally:
        pass  # shared pooled client — never closed per-call


async def touch_active(thread_id: str) -> None:
    """Refresh the ACTIVE flag + stream TTL WITHOUT pushing an event.

    ``push_event`` refreshes both on every write, but a run parked on a HITL
    question pushes nothing for up to the whole ask_user budget (3600s == the
    TTL), so the flag lapsed mid-park: live subscribers terminated and
    reconnect reported the still-parked run as finished, clearing the question
    card (audit R3). HITL waits heartbeat through here instead. ``xx=True`` so
    a finished/cancelled run is never resurrected. Best-effort.
    """
    try:
        r = await _get_client()
        await r.set(_active_key(thread_id), "1", ex=STREAM_TTL_SECONDS, xx=True)
        await r.expire(_stream_key(thread_id), STREAM_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — heartbeat must never break a wait
        _log.warning("stream_relay.touch_active_failed", thread_id=thread_id[:12])


async def stream_exists(thread_id: str) -> bool:
    """Check whether the event stream still exists (not expired)."""
    r = await _get_client()
    try:
        return bool(await r.exists(_stream_key(thread_id)))
    finally:
        pass  # shared pooled client — never closed per-call


async def push_sse_event(thread_id: str, sse_line: str) -> str:
    """Parse an SSE frame and push the JSON payload to the per-thread stream.

    Handles both bare ``data: {...}\\n\\n`` frames (executor format) and
    multi-line frames such as ``event: X\\ndata: {...}\\n\\n`` (AG-UI
    ``EventEncoder`` format).

    Returns the Redis entry ID, or ``""`` if the frame couldn't be parsed.
    """
    raw = ""
    for part in sse_line.split("\n"):
        part = part.strip()
        if part.startswith("data:"):
            raw = part[5:].strip()
            break
    if not raw:
        return ""
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return await push_event(thread_id, event)


# ---------------------------------------------------------------------------
# Cross-worker control bus (P1-2) — deliver cancel / HITL-answer commands to
# the worker that actually owns a run
# ---------------------------------------------------------------------------
#
# A run's asyncio task and its ask_user Future live in ONE worker's process.
# Control commands (Stop → cancel_run, respond-input → resolve_user_input)
# arrive over HTTP and may land on a DIFFERENT worker.  The in-process lookup
# then misses and the command silently no-ops: the run keeps burning tokens, or
# the user's HITL answer is dropped and the agent times out.
#
# Fix: a thin Redis pub/sub bus.  The owning worker registers a LOCAL handler
# (which resolves its own Future / cancels its own task) and subscribes to a
# per-thread control channel.  A command tries the local handler first (same
# worker → no round-trip); on a miss it is PUBLISHED, and the owning worker's
# subscriber applies it.  Best-effort and idempotent: a command for a
# finished/unknown run is a harmless no-op on every worker.

CONTROL_PREFIX = "cc:control"
CONTROL_ACK_PREFIX = "cc:ctrl-ack"
# How long a dispatcher waits for the owning worker to acknowledge a relayed
# command before reporting failure. Redis pub/sub is fire-and-forget: without
# the ack, an answer/cancel published into the subscribe race (or after the
# owning worker restarted) was silently lost while the API reported success —
# the HITL card cleared and the agent stayed parked for the full budget
# (audit R2).
CONTROL_ACK_TIMEOUT = float(os.environ.get("CONTROL_ACK_TIMEOUT_SECONDS", "2.0"))


def _control_channel(thread_id: str) -> str:
    return f"{CONTROL_PREFIX}:{thread_id}"


def _ack_key(ack_id: str) -> str:
    return f"{CONTROL_ACK_PREFIX}:{ack_id}"


async def _write_control_ack(ack_id: str) -> None:
    """Record that a relayed control command was applied (owner side)."""
    try:
        r = await _get_client()
        await r.set(_ack_key(ack_id), "1", ex=60)
    except Exception:  # noqa: BLE001 — ack is confirmation, never a blocker
        _log.warning("stream_relay.control_ack_write_failed", ack_id=ack_id[:12])


async def wait_control_ack(
    ack_id: str, timeout: float = CONTROL_ACK_TIMEOUT,
) -> bool:
    """Poll for the owner's ack of a relayed command (dispatcher side)."""
    try:
        r = await _get_client()
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if await r.get(_ack_key(ack_id)):
                with contextlib.suppress(Exception):
                    await r.delete(_ack_key(ack_id))
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.05)
    except Exception:  # noqa: BLE001
        return False


# thread_id → {cmd → applier}.  Present only on the worker that owns the run.
# Each applier takes the command dict and returns True if it acted on it.
# Keyed by command so independent subsystems (run cancel in stream_relay, HITL
# answer in executor) each register their own without clobbering the other.
_LOCAL_CONTROL_HANDLERS: dict[str, dict[str, "Any"]] = {}
# thread_id → the pub/sub listener task for that owned run.
_CONTROL_LISTENERS: dict[str, asyncio.Task[None]] = {}


def register_control_command(thread_id: str, cmd: str, applier: "Any") -> None:
    """Register a LOCAL *applier* for control command *cmd* on *thread_id*.

    Called on the worker that owns the run.  ``applier(command: dict) -> bool``
    is invoked both for commands issued on THIS worker (direct, via
    :func:`dispatch_control`) and for commands relayed from another worker (via
    the pub/sub listener).  It must be idempotent.  Multiple subsystems can
    register different ``cmd`` keys for the same thread.
    """
    _LOCAL_CONTROL_HANDLERS.setdefault(thread_id, {})[cmd] = applier


def unregister_control_command(thread_id: str, cmd: str) -> None:
    """Drop one command applier; clean up the thread entry when empty."""
    handlers = _LOCAL_CONTROL_HANDLERS.get(thread_id)
    if handlers is None:
        return
    handlers.pop(cmd, None)
    if not handlers:
        _LOCAL_CONTROL_HANDLERS.pop(thread_id, None)


def unregister_control_handler(thread_id: str) -> None:
    """Drop ALL local control appliers for *thread_id* (run boundary)."""
    _LOCAL_CONTROL_HANDLERS.pop(thread_id, None)


def _apply_control_local(thread_id: str, command: dict[str, Any]) -> bool:
    """Apply *command* via the matching local applier if this worker owns it."""
    handlers = _LOCAL_CONTROL_HANDLERS.get(thread_id)
    if not handlers:
        return False
    applier = handlers.get(str(command.get("cmd")))
    if applier is None:
        return False
    try:
        return bool(applier(command))
    except Exception:  # noqa: BLE001 — a control applier must never crash a caller
        _log.exception(
            "stream_relay.control_apply_failed",
            thread_id=thread_id[:12],
            cmd=str(command.get("cmd")),
        )
        return False


async def publish_control(thread_id: str, command: dict[str, Any]) -> int:
    """Publish a control *command* to the per-thread channel.

    Returns the number of Redis subscribers that received it (0 when no worker
    is currently listening for this thread).
    """
    r = await _get_client()
    try:
        return int(
            await r.publish(_control_channel(thread_id), json.dumps(command))
        )
    except Exception:  # noqa: BLE001
        _log.warning(
            "stream_relay.control_publish_failed", thread_id=thread_id[:12],
        )
        return 0


async def dispatch_control(thread_id: str, command: dict[str, Any]) -> bool:
    """Deliver a control *command* to whichever worker owns the run.

    Tries the local handler first (owning run on THIS worker → applied inline,
    no Redis round-trip).  On a local miss, publishes to the control channel
    with an ``ack_id`` and waits for the owning worker's listener to confirm it
    APPLIED the command.  Returns True only on confirmed application — never on
    "the run looks active somewhere", which previously reported success for
    answers lost in the pub/sub subscribe race or after an owner restart
    (audit R2: the card cleared while the agent stayed parked for an hour).
    A zero-subscriber publish is retried once (~0.3s) to ride out the short
    listener-startup race at run boundaries.
    """
    if _apply_control_local(thread_id, command):
        return True
    # Not ours — relay to the owner and wait for its applied-ack.
    ack_id = command.setdefault("ack_id", uuid.uuid4().hex)
    delivered = await publish_control(thread_id, command)
    if delivered <= 0:
        await asyncio.sleep(0.3)
        delivered = await publish_control(thread_id, command)
    if delivered <= 0:
        _log.warning(
            "stream_relay.control_undelivered",
            thread_id=thread_id[:12], cmd=str(command.get("cmd")),
        )
        return False
    if await wait_control_ack(ack_id):
        return True
    _log.warning(
        "stream_relay.control_unacked",
        thread_id=thread_id[:12], cmd=str(command.get("cmd")),
    )
    return False


async def _control_listener(thread_id: str) -> None:
    """Subscribe to the thread's control channel and apply relayed commands.

    Runs for the lifetime of an owned run.  Applies each inbound command via
    the local handler; commands for a thread this worker doesn't own (handler
    already gone) are ignored.
    """
    r = await _get_client()
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(_control_channel(thread_id))
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            raw = msg.get("data")
            if not raw:
                continue
            try:
                command = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(command, dict):
                applied = _apply_control_local(thread_id, command)
                ack_id = command.get("ack_id")
                if applied and ack_id:
                    await _write_control_ack(str(ack_id))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — never let the bus kill anything
        _log.warning(
            "stream_relay.control_listener_error", thread_id=thread_id[:12],
        )
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(_control_channel(thread_id))
        with contextlib.suppress(Exception):
            await pubsub.aclose()


def _start_control_listener(thread_id: str) -> None:
    """Start (or restart) the pub/sub control listener for an owned run."""
    prev = _CONTROL_LISTENERS.get(thread_id)
    if prev is not None and not prev.done():
        prev.cancel()
    task = asyncio.create_task(
        _control_listener(thread_id), name=f"cc-ctl-{thread_id[:24]}",
    )
    _CONTROL_LISTENERS[thread_id] = task


def _stop_control_listener(thread_id: str) -> None:
    """Stop the pub/sub control listener for *thread_id* (fire-and-forget)."""
    task = _CONTROL_LISTENERS.pop(thread_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _stop_control_listener_wait(thread_id: str) -> None:
    """Stop the listener AND await its teardown so the pub/sub connection is
    closed before the caller (a run boundary) returns — avoids a dangling
    connection cleaning up after the event loop is gone."""
    task = _CONTROL_LISTENERS.pop(thread_id, None)
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(asyncio.shield(task), timeout=2)


# ---------------------------------------------------------------------------
# Detached execution — decouple agent runs from the HTTP response lifecycle
# ---------------------------------------------------------------------------

# Strong references to in-flight detached run tasks, keyed by thread_id.
# Prevents garbage collection and enforces one run per thread.
_DETACHED_TASKS: dict[str, asyncio.Task[None]] = {}

# Background sub-agent tasks spawned during a run (call_agent_background),
# keyed by the PARENT thread_id.  Cancelling the run cascades to these so a
# stopped parent doesn't leave orphaned sub-agents burning tokens.
_BACKGROUND_CHILDREN: dict[str, set[asyncio.Task]] = {}


def register_background_child(thread_id: str, task: asyncio.Task) -> None:
    """Register a background sub-agent task against its parent thread.

    ``cancel_run(thread_id)`` cancels every registered child alongside the
    main detached run.  Tasks deregister themselves on completion.
    """
    children = _BACKGROUND_CHILDREN.setdefault(thread_id, set())
    children.add(task)

    def _discard(t: asyncio.Task) -> None:
        kids = _BACKGROUND_CHILDREN.get(thread_id)
        if kids is not None:
            kids.discard(t)
            if not kids:
                _BACKGROUND_CHILDREN.pop(thread_id, None)

    task.add_done_callback(_discard)


def get_detached_task(thread_id: str) -> asyncio.Task[None] | None:
    """Return the in-flight detached run task for *thread_id*, if any."""
    task = _DETACHED_TASKS.get(thread_id)
    if task is not None and task.done():
        return None
    return task


async def run_detached(
    thread_id: str,
    gen: AsyncIterator[str],
    *,
    tee: bool = False,
    on_complete: Any = None,
    actor: str | None = None,
    source: str | None = None,
    floor: list[str] | None = None,
    organization_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run *gen* (an SSE-line async generator) in a DETACHED background task
    and yield its events from the Redis stream.

    This decouples agent execution from the HTTP response: if the client
    disconnects, uvicorn cancels THIS generator (the subscriber) but the
    background task keeps draining the agent and pushing events to Redis.
    A reconnecting client replays from its cursor and resumes live.

    Args:
        thread_id: Conversation thread ID (stream key).
        gen:       Async generator yielding SSE-formatted strings.
        tee:       When True, this helper pushes each yielded line to Redis
                   (use for generators that don't self-tee, e.g. AG-UI
                   ``/copilot/chat``).  When False the generator is expected
                   to tee its own events (executor ``_sse`` contextvar path).
        on_complete: Optional zero-arg async callback invoked in the drain
                   task's ``finally`` — after ``mark_inactive``, on every
                   exit path (finished, errored, cancelled).  This is the
                   run-boundary lifecycle hook; the gateway attaches the
                   authoritative fold-and-persist here (core_loop_unification
                   Phase 1).  Best-effort: exceptions are swallowed.
        actor:     Who is starting this run.  Recorded on the thread so a later
                   caller can be told whose run they would destroy.  The 409
                   lives at the route (``/agent/run/stream``); the INVARIANT
                   lives here — see :class:`SupersedeRefused`.
        source:    Correlation source of this run ("chat", "workflow",
                   "schedule", …).  Recorded so a message arriving mid-run can
                   tell a conversation from an automation turn.
        floor:     Participant roster whose access intersection this run acts
                   at.  Recorded so a mid-run steer can be checked against it.
        organization_id: The caller's tenant, stamped SERVER-SIDE by the gateway
                   chat route from the authenticated ``UserContext`` — NEVER from
                   the client/agent-visible event payload (R11). Bound for the
                   life of the DETACHED drain task so writes on the run's own
                   event loop (including the ``on_complete`` persist hook, which
                   runs AFTER the generator's own binding has been released) see
                   the right tenant. WS-29 MT-1d (H4), slice 2 — DARK.

    Raises:
        SupersedeRefused: when a DIFFERENT party's run is in flight on this
            thread.  Starting would delete a transcript this caller does not
            own, so it does not start.

    Yields:
        Parsed event dicts with ``_stream_id`` (Redis entry ID) attached.
    """
    # ── §5.2, the inner layer: the destructive reset is UNREACHABLE by anyone
    # but a legitimate superseder. ────────────────────────────────────────────
    #
    # The rule, stated once: *you may supersede a run you own, and no other.*
    # "Own" is `cc:runactor:{thread_id}`, stamped when the run began.
    #
    # Fails OPEN in exactly the two cases ``get_run_actor`` documents — no
    # recorded owner (a legacy run, a Redis hiccup) or an anonymous caller
    # (cron, service-to-service) — because a false refusal blocks legitimate
    # work while a false allow merely restores the behaviour we already had.
    # Everything else is refused, and the caller routes to steer instead.
    if actor:
        try:
            _owner = await get_run_actor(thread_id)
            _live = await is_active(thread_id)
        except Exception:  # noqa: BLE001 — never block a run on a check failing
            _owner, _live = None, False
        if _live and _owner and _owner != actor:
            _log.warning(
                "stream_relay.supersede_refused",
                thread_id=thread_id[:12],
                owner=_owner[:20], actor=actor[:20],
            )
            raise SupersedeRefused(thread_id, _owner, actor)

    # One run per thread: cancel any stale run still attached to this thread.
    prev = _DETACHED_TASKS.get(thread_id)
    if prev is not None and not prev.done():
        prev.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(prev), timeout=2)
        except BaseException:  # noqa: BLE001
            pass

    # Fresh run boundary: clear previous events so replay-from-0 is exact.
    # NOTE this DELETES the previous run's event log — reaching this line means
    # the guard above has confirmed the caller owns what it is about to erase.
    await mark_active(
        thread_id, reset=True, actor=actor, source=source, floor=floor,
    )

    # A note buffered for a run that never got to a tool boundary must not leak
    # into the next one; the durable store (cc:steer:) is what carries anything
    # still owed forward, and the route replays it into the run's input.
    try:
        from orchestrator.steer import clear_guidance  # noqa: PLC0415
        clear_guidance(thread_id)
    except Exception:  # noqa: BLE001
        pass

    async def _drain() -> None:
        # Bind the caller's tenant for the whole life of THIS detached task
        # (WS-29 MT-1d / H4, slice 2 — DARK). The generator sets its own binding
        # too, but that is released in the generator's finally BEFORE this task's
        # finally runs on_complete — so the persist hook, and any other work this
        # task does around the generator, needs its own binding here. Sourced
        # ONLY from the server-side ``organization_id`` argument, never the event
        # payload (R11). No DB write is converted this slice, so this is inert.
        _tenant_token = None
        if organization_id:
            with contextlib.suppress(Exception):
                from acb_common.db import bind_tenant
                _tenant_token = bind_tenant(organization_id)
        try:
            async for line in gen:
                if tee:
                    try:
                        await push_sse_event(thread_id, line)
                    except Exception:  # noqa: BLE001
                        pass  # never let Redis issues kill the agent run
        except asyncio.CancelledError:
            # Do NOT push RUN_ERROR into the stream here.  This task was
            # cancelled by a new run_detached() call for the same thread
            # (steer, retry, Quick action).  mark_active(reset=True)
            # runs in the canceller and wipes the stream — if a RUN_ERROR
            # lands after that reset, it leaks into the fresh stream and
            # the frontend treats the new run as a hard failure.
            #
            # The canceller already awaits a shielded timeout on this
            # task, so the `finally` (mark_inactive + pop) will still
            # execute; the new run takes over cleanly.
            raise
        except Exception as exc:  # noqa: BLE001
            _log.exception(
                "stream_relay.detached_run_error",
                thread_id=thread_id[:12],
            )
            try:
                await push_event(thread_id, {
                    "type": "RUN_ERROR",
                    "message": str(exc),
                    "code": type(exc).__name__,
                })
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                await mark_inactive(thread_id)
            except Exception:  # noqa: BLE001
                pass
            # Tear down the cross-worker control bus for this run (P1-2).
            # Await the listener's shutdown so its pub/sub connection closes
            # before this run boundary returns (no post-loop cleanup dangle).
            with contextlib.suppress(BaseException):
                await _stop_control_listener_wait(thread_id)
            unregister_control_handler(thread_id)
            if on_complete is not None:
                # Shield: this finally also runs on task cancellation (Stop /
                # steer), where the first await would otherwise re-raise
                # CancelledError before persistence gets to run.
                with contextlib.suppress(BaseException):
                    await asyncio.shield(on_complete())
            # Release this task's tenant binding LAST — after on_complete, which
            # is the write that most needs it (WS-29 MT-1d / H4).
            if _tenant_token is not None:
                with contextlib.suppress(Exception):
                    from acb_common.db import release_tenant
                    release_tenant(_tenant_token)
            if _DETACHED_TASKS.get(thread_id) is task:
                _DETACHED_TASKS.pop(thread_id, None)

    task = asyncio.create_task(_drain(), name=f"cc-run-{thread_id[:24]}")
    _DETACHED_TASKS[thread_id] = task

    # Register the cross-worker control bus for this owned run (P1-2): a local
    # applier for the "cancel" command that cancels THIS task (Stop from any
    # worker reaches it), plus a pub/sub listener so a command issued on another
    # worker is relayed here.  HITL answers register their own "respond_input"
    # applier from the executor (executor owns the ask_user Futures).
    def _cancel_apply(_command: dict[str, Any]) -> bool:
        live = _DETACHED_TASKS.get(thread_id)
        if live is not None and not live.done():
            live.cancel()
        return True

    register_control_command(thread_id, "cancel", _cancel_apply)

    # ── The steer applier (§4.6 step 2) ──────────────────────────────────────
    # "A new applier, not new infrastructure": the bus already relays a command
    # from any worker to the one that owns the run and confirms it applied. All
    # steer adds is what to DO on arrival — buffer the note for the run's next
    # tool boundary, and tell everyone watching that the run was redirected and
    # by whom.
    #
    # Returning True is the applied-ack, and it is honest here: the note is in
    # the owning worker's buffer. If the run then dies before a tool boundary,
    # the durable signal (cc:steer:) is what carries the sentence into the next
    # run — the applier never claims the model has *acted* on it, only that the
    # right worker has it.
    def _steer_apply(command: dict[str, Any]) -> bool:
        text = str(command.get("text") or "").strip()
        if not text:
            return False
        author = str(command.get("author") or "someone")
        try:
            from orchestrator.steer import (  # noqa: PLC0415
                buffer_guidance, discard_signal,
            )
            buffer_guidance(thread_id, author, text)
        except Exception:  # noqa: BLE001
            return False
        # NOTE the visible half of this — the ``STEER_INJECTED`` event everyone
        # in the room sees — is published by the SENDER onto ``cc:room:``, not
        # here onto ``cc:stream:``. Two reasons, both deliberate:
        #   * layering: ``cc:room:`` belongs to the gateway (room_stream.py) and
        #     the orchestrator must not reach up into it;
        #   * parity: ``cc:stream:`` events are folded into the transcript by
        #     BOTH ``gateway/chat_fold.py`` and ``lib/chatStream.ts``, so a new
        #     run-event type is a two-sided change. A steer is a room fact, not
        #     a turn fact — §4.4 puts ``STEER_INJECTED`` on the room stream for
        #     exactly this reason.
        sid = str(command.get("signal_id") or "")
        if sid:
            with contextlib.suppress(Exception):
                asyncio.create_task(discard_signal(thread_id, sid))
        return True

    register_control_command(thread_id, "steer", _steer_apply)
    _start_control_listener(thread_id)

    # Serve events from Redis — the SAME path a reconnecting client uses.
    async for evt in subscribe_events(thread_id, since_id="0"):
        yield evt


async def cancel_run(thread_id: str) -> bool:
    """Cancel the in-flight detached agent run for *thread_id*.

    This actually stops backend execution (vs. merely disconnecting the SSE
    subscriber): it cancels the background asyncio task draining the agent
    generator, marks the thread inactive, and pushes a terminal RUN_FINISHED
    event so any live subscribers (and reconnecting clients) close cleanly.

    Returns True if a running task was found (locally or on another worker) and
    cancellation was dispatched, False otherwise.  Safe to call when no run is
    active (idempotent) — it still marks the thread inactive and emits the
    terminal event so a stuck UI recovers.
    """
    task = _DETACHED_TASKS.get(thread_id)
    found = task is not None and not task.done()

    if found and task is not None:
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        except BaseException:  # noqa: BLE001
            pass
        _DETACHED_TASKS.pop(thread_id, None)
    else:
        # Not ours: the run may be owned by another worker (P1-2).  Relay the
        # cancel over the control bus and require the owner's applied-ack —
        # a bare publish into the subscribe race (or after an owner restart)
        # reported "stopped" while the detached task kept running and
        # spending (audit R2/R3 sibling).  dispatch_control retries the
        # zero-subscriber case once and returns True only on confirmed
        # application.  When it fails BUT the ACTIVE flag is still set, the
        # owner is unreachable (listener died / worker gone): the teardown
        # below still clears the flag so the UI recovers, but we log it and
        # report found=False so the caller can surface "stop not confirmed"
        # instead of claiming the backend stopped.
        found = await dispatch_control(thread_id, {"cmd": "cancel"})
        if not found:
            # Diagnostic only — Redis being down must not break the teardown
            # below (every other Redis call here is equally best-effort).
            with contextlib.suppress(Exception):
                if await is_active(thread_id):
                    _log.warning(
                        "stream_relay.cancel_unconfirmed_owner_unreachable",
                        thread_id=thread_id[:12],
                    )

    # Cancel-cascade: stop any background sub-agents spawned by this run
    # (call_agent_background) so they don't keep executing after a Stop.
    children = _BACKGROUND_CHILDREN.pop(thread_id, None)
    if children:
        live = [t for t in children if not t.done()]
        for t in live:
            t.cancel()
        if live:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(
                    asyncio.shield(asyncio.gather(*live, return_exceptions=True)),
                    timeout=3,
                )
            _log.info(
                "stream_relay.cancel_run.children_cancelled",
                thread_id=thread_id[:12],
                count=len(live),
            )

    # Mark inactive and emit a terminal event so subscribers stop blocking.
    try:
        await mark_inactive(thread_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        await push_event(thread_id, {"type": "RUN_FINISHED", "cancelled": True})
    except Exception:  # noqa: BLE001
        pass

    _log.info("stream_relay.cancel_run", thread_id=thread_id[:12], found=found)
    return found
