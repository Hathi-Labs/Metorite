"""Redis Streams consumer for the ingestion event bus (§BO-20, ticket BO-20a).

Until this module existed the ``ingestion:*`` streams were **write-only**: the
receivers ``xadd``'d every event and nothing in the repo ever issued
``xreadgroup`` / ``xack``, so the streams were an audit buffer trimmed unread at
``maxlen=10_000``.  This is the drain side of that queue.

Shape (owner decision §BO-20.0, **Option A**, taken 2026-08-02)
---------------------------------------------------------------
An **in-process supervised asyncio loop** started from the gateway lifespan —
the house style already used by five loops there, and the same
"loop lives in a service package, gateway starts it" precedent as
``email_ingestion.scheduler.start_background_sync``.  Option B (a separate
``python -m ingestion.worker`` process) was rejected: it needs a systemd unit no
agent can deploy, and a separate process starts with an empty
``event_hooks._SINKS`` — it would read, ack, and dispatch to nothing.

Consumer group
--------------
``XGROUP CREATE <stream> cc-ingest $ MKSTREAM`` per stream.  The ``$`` is
load-bearing and deliberate: the group starts at the stream **tail**, so the
buffer that accumulated while nothing consumed is skipped rather than replayed.
``0`` would push up to 10 000 buffered entries per stream into real workflow
runs the moment the flag flips.  A one-off replay, if ever wanted, is a manual
act — never a startup behaviour.

Ack semantics (interim, by design)
----------------------------------
BO-20a acks after dispatch **regardless of outcome**.  Honest ``XACK`` (leave a
failed entry in the PEL), retry with backoff, and the DLQ hand-off are BO-20b;
that ticket also adds the ``emit_event(..., raise_on_error=True)`` strict mode
this loop would need to *observe* a failure at all (``emit_event`` swallows sink
exceptions by design today).

Two *further* "enqueued but never dispatched" states exist in this ticket,
beyond the accepted Redis-down drop at the receiver (§BO-20 Q1) — both because
``XACK`` at ``_dispatch_entry`` is **unguarded** and this loop only ever reads
``">"`` (new entries), never ``"0"`` (its own PEL):

1. **Ack failure mid-batch.** A connection reset on ``client.xack`` propagates
   out through ``_drain_once`` to the supervised loop's ``except``, so the
   remaining entries of that XREADGROUP reply — up to ``_READ_COUNT`` per
   stream — are never dispatched.  They sit in the PEL and this process will
   not re-read them.  The only log is ``consumer.cycle_failed``.
2. **SIGTERM mid-batch.** Same shape: the undispatched remainder stays in the
   PEL under the *old* ``gw-<host>-<pid>`` name, which the restarted process
   never uses.

Both are recoverable — by BO-20b's reclaim pass (``XAUTOCLAIM`` / ``XPENDING``
+ ``XCLAIM`` reclaim *any* consumer name in the group), which is why BO-20b
requires that pass to run **at startup** and not only on a periodic cadence.
The recovery window is finite: ``queue._MAXLEN`` trims at 10 000 entries per
stream, and a trimmed entry is gone from the PEL's reach for good.

Dispatch timeout
----------------
``emit_event`` is awaited under ``asyncio.timeout(_DISPATCH_TIMEOUT_SECS)``.
This is **not** a retry mechanism (that is BO-20b); it exists because this
ticket changes the blast radius of a hung sink.  Before the cutover a sink that
never returns hung one request's ``BackgroundTasks`` and the other two sources
kept flowing.  Here every stream is drained by one serial loop, so an unbounded
``await`` would stall the whole bus with no log line at all — ``emit_event``
swallows sink exceptions, so it could not even surface as ``cycle_failed``.

Flag
----
``INGESTION_CONSUMER`` (default **off**) — read through ``consumer_enabled()``
at call time, a verbatim mirror of
``gateway/routes/whatsapp/scheduler.py::enrichment_enabled``.  Deliberately not
a ``Settings`` field: ``get_settings`` is ``@lru_cache(maxsize=1)``, which makes
a per-call flag untestable.  Flipping it in an environment is an OWNER-GATE
(``work_plan.md`` §6): it also cuts the receivers over from inline ``emit_event``
to enqueue-only, so Redis down means events dropped rather than dispatched
inline (§BO-20 Q1, accepted).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from typing import Any

import redis.asyncio as aioredis
from acb_common import get_logger, get_settings
from redis.exceptions import ResponseError

from ingestion.queue import STREAM_GMAIL, STREAM_ZOHO

_log = get_logger("ingestion.consumer")

#: Consumer group name, shared by every gateway worker — that is the point of a
#: group: workers cooperate on one stream instead of each getting a copy.
_GROUP = "cc-ingest"

#: Finite block, so a cancelled task returns promptly instead of parking on a
#: read that never times out.
_BLOCK_MS = 5_000

#: Entries per read, per stream. Kept at or below BO-20e's planned
#: ``INGESTION_MAX_CONCURRENCY`` (8) so that ticket bounds a batch this one
#: already produces.
_READ_COUNT = 8

#: Pause after a failed cycle. Without it, a Redis outage turns the supervised
#: loop into a hot loop (``xreadgroup`` raises immediately, no block elapses).
_ERROR_BACKOFF_SECS = 1.0

#: Ceiling on ONE entry's whole sink fan-out. Bounded because this loop drains
#: every stream serially: an unbounded ``await emit_event`` turns a single
#: hung sink into a bus-wide stall (see "Dispatch timeout" above). 30s is far
#: above any legitimate sink — the registered one, ``workflows.dispatch_event``,
#: is DB-bound (it inserts the run row and ``create_task``s ``_execute_run``; it
#: never awaits the run itself), so this can never truncate a workflow.
_DISPATCH_TIMEOUT_SECS = 30.0

_ENABLED_ENV = "INGESTION_CONSUMER"

#: stream → the ``source`` string handed to the sinks. It is the same vocabulary
#: the receivers emit inline today, so a workflow event trigger matches
#: identically on either side of the cutover.
STREAM_SOURCES: dict[str, str] = {
    STREAM_ZOHO: "zoho",
    STREAM_GMAIL: "gmail",
}

_task: asyncio.Task[Any] | None = None
_client: aioredis.Redis | None = None


def consumer_enabled(env: dict[str, str] | None = None) -> bool:
    """True when the ingestion consumer should run. Pure.

    Mirrors ``whatsapp/scheduler.py::enrichment_enabled`` — ``os.environ`` is
    read at call time so a test (and a receiver, per request) sees the current
    value rather than one frozen in a cached ``Settings``.
    """
    src = env if env is not None else os.environ
    return str(src.get(_ENABLED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def consumer_name() -> str:
    """Unique per gateway worker.

    Must NOT be a constant: BO-20b's reclaim pass identifies a dead worker by
    its name in the pending-entries list, so two workers sharing one name make
    a crashed worker's backlog unreclaimable.
    """
    return f"gw-{socket.gethostname()}-{os.getpid()}"


def _get_client() -> aioredis.Redis:
    """The shared pooled async client (created lazily, on the running loop).

    A blocking ``XREADGROUP`` needs a long-lived **async** client; the
    producer's per-call sync ``queue._client`` is a different object and stays
    BO-9's to consolidate. Pattern copied from ``acb_common/activity.py``.
    """
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            max_connections=16,
            health_check_interval=30,
        )
    return _client


def _text(value: Any) -> str:
    """Redis replies as ``str`` (we set ``decode_responses``) — belt and braces
    for a client built without it, so a ``bytes`` stream name cannot silently
    fail the ``STREAM_SOURCES`` lookup and drop every event."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


async def _ensure_groups(client: aioredis.Redis) -> None:
    """Declare the consumer group on every ingestion stream. Idempotent."""
    for stream in STREAM_SOURCES:
        try:
            await client.xgroup_create(stream, _GROUP, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            continue
        _log.info("consumer.group_created", stream=stream, group=_GROUP)


async def _dispatch_entry(
    client: aioredis.Redis,
    stream: str,
    source: str,
    entry_id: str,
    fields: dict[str, Any],
) -> None:
    """Decode one entry, hand it to the sink registry, then ack it exactly once."""
    event_type = _text(fields.get("event_type") or "unknown")
    raw = fields.get("data")
    payload: dict[str, Any] | None = None
    try:
        decoded = json.loads(_text(raw)) if raw is not None else None
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        payload = decoded

    if payload is None:
        # Undecodable or non-object payload: there is nothing a sink could act
        # on, and re-delivering it forever would wedge the group. Ack and move
        # on, loudly.
        _log.warning(
            "consumer.entry_undecodable",
            stream=stream,
            entry_id=entry_id,
            source=source,
        )
    else:
        # The sink contract is (source, event_type, dict) — the decoded payload,
        # never the JSON string the stream carries.
        from ingestion.event_hooks import emit_event  # noqa: PLC0415

        try:
            # Bounded: one serial loop drains all three streams, so a sink that
            # never returns would stall the whole bus. An outer cancel (stop)
            # still propagates — asyncio.timeout only converts to TimeoutError
            # when its OWN deadline expired.
            async with asyncio.timeout(_DISPATCH_TIMEOUT_SECS):
                await emit_event(source, event_type, payload)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            _log.warning(
                "consumer.dispatch_timeout",
                stream=stream,
                entry_id=entry_id,
                source=source,
                timeout_secs=_DISPATCH_TIMEOUT_SECS,
            )
        except Exception as exc:  # noqa: BLE001 - one bad entry must not stall the stream
            _log.warning(
                "consumer.dispatch_failed",
                stream=stream,
                entry_id=entry_id,
                source=source,
                error=str(exc)[:200],
            )

    # Interim semantics (BO-20a): ack after dispatch regardless of outcome —
    # including a timed-out one. Honest ack + retry + DLQ is BO-20b; do not
    # pre-build it here. Deliberately unguarded: a raising xack means Redis is
    # gone, and swallowing it would hot-loop against a dead server instead of
    # reaching the supervised backoff. The cost is that the rest of this batch
    # is skipped and strands in the PEL — see the module docstring, and BO-20b's
    # startup reclaim pass, which is what recovers it.
    await client.xack(stream, _GROUP, entry_id)


async def _drain_once(client: aioredis.Redis) -> int:
    """One ``XREADGROUP`` across all three streams. Returns entries handled."""
    response = await client.xreadgroup(
        groupname=_GROUP,
        consumername=consumer_name(),
        streams={stream: ">" for stream in STREAM_SOURCES},
        count=_READ_COUNT,
        block=_BLOCK_MS,
    )
    if not response:
        return 0

    handled = 0
    # RESP2 reply shape: [(stream, [(id, fields), …]), …]. `from_url` defaults to
    # protocol=2 and nothing sets otherwise; RESP3 would hand back a dict here,
    # so a `redis_url` carrying `?protocol=3` (from_url forwards query params as
    # kwargs) would break this unpack.
    for raw_stream, entries in response:
        stream = _text(raw_stream)
        source = STREAM_SOURCES.get(stream)
        if source is None:  # pragma: no cover - we only ever subscribe to ours
            continue
        for raw_id, fields in entries or []:
            await _dispatch_entry(client, stream, source, _text(raw_id), dict(fields or {}))
            handled += 1
    return handled


async def _consumer_loop() -> None:
    """Supervised drain loop — the two guarantees ``_scheduler_loop`` makes:
    one bad cycle never kills it, and ``CancelledError`` always propagates."""
    client = _get_client()
    groups_ready = False
    while True:
        try:
            if not groups_ready:
                await _ensure_groups(client)
                groups_ready = True
            await _drain_once(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let one bad cycle kill the loop
            # Re-declare the groups next cycle: a Redis restart/flush loses them
            # and every subsequent read would fail with NOGROUP forever.
            groups_ready = False
            _log.warning("consumer.cycle_failed", error=str(exc)[:200])
            await asyncio.sleep(_ERROR_BACKOFF_SECS)


async def start_ingestion_consumer() -> bool:
    """Start the drain loop if the flag is on. Idempotent; returns whether it runs."""
    global _task
    if not consumer_enabled():
        _log.info("consumer.disabled", flag=_ENABLED_ENV)
        return False
    if _task is not None and not _task.done():
        return True
    _task = asyncio.get_running_loop().create_task(
        _consumer_loop(),
        name="ingestion-consumer",
    )
    _log.info(
        "consumer.started",
        group=_GROUP,
        consumer=consumer_name(),
        streams=sorted(STREAM_SOURCES),
    )
    return True


async def stop_ingestion_consumer() -> None:
    """Cancel the drain loop. Safe to call when it never started."""
    global _task
    task, _task = _task, None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def consumer_status() -> dict[str, Any]:
    return {
        "running": _task is not None and not _task.done(),
        "enabled": consumer_enabled(),
        "group": _GROUP,
        "consumer": consumer_name(),
        "streams": sorted(STREAM_SOURCES),
        "block_ms": _BLOCK_MS,
        "read_count": _READ_COUNT,
    }


__all__ = [
    "STREAM_SOURCES",
    "consumer_enabled",
    "consumer_name",
    "consumer_status",
    "start_ingestion_consumer",
    "stop_ingestion_consumer",
]
