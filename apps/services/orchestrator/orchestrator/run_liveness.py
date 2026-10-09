"""Which process holds a chat run, and is that process still alive.

Incident 2026-10-09, 16:18 UTC. A deploy restarted the gateway while a run
waited on a question card. ``cc:active:{tid}`` lives for an hour, so it
outlived the process that held the run. Every later message in the thread was
routed as a STEER into a run that no longer existed. The steers were stored for
a replay that never came, and nothing answered for up to an hour.

``cc:active`` says "a run started and has not said that it ended". It cannot
say "a process still holds the run", because only a live process can say that.
So this module adds two facts, and one of them only a live process can write:

    cc:runowner:{tid}          = <instance id>     written beside cc:active
    cc:instance:{instance id}  = "1", TTL 30 s     refreshed every 10 s

A run whose owner has no heartbeat key is dead, whoever asks. Two more keys let
a process find the runs of a process that died:

    cc:instances               = the ids of processes that held a run
    cc:instance-runs:{id}      = hash, thread id -> the run's record

**Why an instance id and a heartbeat, and not the other two options.** A
heartbeat per RUN would need a writer per run, and a run parked on a question
pushes nothing for an hour. A control-bus ack answers only when a command is
sent, so it cannot find dead runs at startup. A heartbeat per PROCESS is one
key per process, it is written by a task that does nothing else, and it
answers both questions: "is this run alive" and "which runs did a dead process
leave". Several gateway processes can share one Redis. Each has its own id and
answers only for its own key, so a second worker never reads a sibling's live
run as dead.

**The one extra signal.** A process in its graceful shutdown still has its
heartbeat key, but it has already torn down its control listeners. A control
command that reaches NO subscriber (``dispatch_control_status`` answers
``"undelivered"``) then proves that nothing holds the run, and
:func:`run_liveness` takes that as dead too. An unacked command is NOT taken as
dead: a listener heard it, so a process still holds the run.

**The sweep.** :func:`sweep_dead_instances` runs at startup and then every
third heartbeat. For each run a dead process left, it pushes a
``run_interrupted`` marker into the run's stream, marks the run inactive,
clears the org's live-run entry (#791), and hands the run's record to the
gateway, which folds the stream into the saved reply. The partial answer and
the marker are then on the assistant row, and the chat offers Continue.

Fence (R7): ``tests/unit/test_chat_deploy_recovery.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from acb_common import get_logger

from orchestrator import stream_relay

_log = get_logger("orchestrator.run_liveness")

RUN_OWNER_PREFIX = "cc:runowner"
INSTANCE_PREFIX = "cc:instance"
INSTANCES_KEY = "cc:instances"
INSTANCE_RUNS_PREFIX = "cc:instance-runs"
INSTANCE_SWEEP_PREFIX = "cc:instance-sweep"

#: How long a process's heartbeat key outlives its last beat. A crash is seen
#: as dead within this bound. A clean stop deletes the key, so it is seen at once.
INSTANCE_HEARTBEAT_TTL = int(os.environ.get("INSTANCE_HEARTBEAT_TTL_SECONDS", "30"))
INSTANCE_HEARTBEAT_EVERY = max(1, INSTANCE_HEARTBEAT_TTL // 3)
#: The sweep runs on every Nth beat (and once at start).
SWEEP_EVERY_BEATS = 3

#: This process. Host and pid make a log line readable. The random tail keeps
#: a reused pid from inheriting a dead process's runs.
INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

#: The marker a dead run's stream and saved reply carry. The chat reads it to
#: show "The assistant was interrupted by an update" and a Continue button.
INTERRUPTED_EVENT = "run_interrupted"

#: The threads this process marked active and has not marked inactive.
_LOCAL_RUNS: set[str] = set()

_HEARTBEAT_TASK: asyncio.Task[None] | None = None
_ON_INTERRUPTED: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None
_SWEEP_ON = False


def run_owner_key(thread_id: str) -> str:
    return f"{RUN_OWNER_PREFIX}:{thread_id}"


def instance_key(instance_id: str) -> str:
    return f"{INSTANCE_PREFIX}:{instance_id}"


def instance_runs_key(instance_id: str) -> str:
    return f"{INSTANCE_RUNS_PREFIX}:{instance_id}"


async def _client() -> Any:
    # Read through the module so a test's fake client is the one used.
    return await stream_relay._get_client()


# ---------------------------------------------------------------------------
# The owner and the heartbeat
# ---------------------------------------------------------------------------

async def beat() -> None:
    """Write this process's heartbeat key, and list this process."""
    r = await _client()
    await r.set(instance_key(INSTANCE_ID), "1", ex=INSTANCE_HEARTBEAT_TTL)
    await r.sadd(INSTANCES_KEY, INSTANCE_ID)


async def claim_run(thread_id: str) -> None:
    """Record THIS process as the owner of *thread_id*'s run.

    ``stream_relay.mark_active`` calls this. It also beats once, so a run is
    never owned by a process with no heartbeat key, and it starts the beat loop
    when none runs. Best-effort: Redis trouble never blocks a run.
    """
    _LOCAL_RUNS.add(thread_id)
    try:
        r = await _client()
        await r.set(
            run_owner_key(thread_id), INSTANCE_ID,
            ex=stream_relay.STREAM_TTL_SECONDS,
        )
        await beat()
    except Exception:
        _log.warning("run_liveness.claim_failed", thread_id=thread_id[:12])
    ensure_heartbeat()


async def release_run(thread_id: str) -> None:
    """Forget the owner of *thread_id*'s run. ``mark_inactive`` calls this."""
    _LOCAL_RUNS.discard(thread_id)
    with contextlib.suppress(Exception):
        r = await _client()
        await r.delete(run_owner_key(thread_id))


async def record_instance_run(thread_id: str, record: dict[str, Any]) -> None:
    """Keep what the sweep needs to close this run if this process dies.

    *record* holds server-side facts only: the org, the actor, the row id the
    run persists to, and the tokens of its live-run index entry.
    """
    try:
        r = await _client()
        k = instance_runs_key(INSTANCE_ID)
        await r.hset(k, thread_id, json.dumps(record, default=str))
        await r.expire(k, 2 * stream_relay.STREAM_TTL_SECONDS)
    except Exception:
        _log.warning("run_liveness.record_failed", thread_id=thread_id[:12])


async def forget_instance_run(thread_id: str) -> None:
    with contextlib.suppress(Exception):
        r = await _client()
        await r.hdel(instance_runs_key(INSTANCE_ID), thread_id)


async def run_liveness(thread_id: str, *, undelivered: bool = False) -> str:
    """``"idle"``, ``"live"`` or ``"dead"`` for the run on *thread_id*.

    * ``idle``: no run is marked active.
    * ``dead``: the owner process has no heartbeat key; or this process owns
      it and holds no such run; or a control command reached no subscriber
      (*undelivered*), so no process holds the run's listener.
    * ``live``: anything else, including every case this function cannot read.
      A false "dead" would start a second run on a live one, so an error
      answers "live" and the caller keeps the old behaviour.
    """
    try:
        r = await _client()
        if await r.get(stream_relay._active_key(thread_id)) != "1":
            return "idle"
        owner = await r.get(run_owner_key(thread_id))
        if owner == INSTANCE_ID:
            return "live" if thread_id in _LOCAL_RUNS else "dead"
        if owner and not await r.exists(instance_key(owner)):
            return "dead"
        # The owner is alive (or not recorded, for a run an older build
        # started). Only a command that no process heard proves it dead.
        return "dead" if undelivered else "live"
    except Exception:
        _log.warning("run_liveness.read_failed", thread_id=thread_id[:12])
        return "live"


# ---------------------------------------------------------------------------
# Closing a dead run
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def interrupt_run(
    thread_id: str, *, reason: str = "restart",
) -> dict[str, Any]:
    """Close a dead run, and return its record for the gateway to persist.

    In this order: the marker events go into the stream (so the fold and any
    live reader see them), the run is marked inactive, the org's live-run
    entry goes (#791), and the dead owner's record goes. The stream itself
    stays, with its TTL refreshed, so the partial answer can be folded.
    """
    r = await _client()
    owner = None
    record: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        owner = await r.get(run_owner_key(thread_id))
        if owner:
            raw = await r.hget(instance_runs_key(owner), thread_id)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    record = parsed
    with contextlib.suppress(Exception):
        await stream_relay.push_event(thread_id, {
            "type": "CUSTOM",
            "name": INTERRUPTED_EVENT,
            "value": {"reason": reason, "at": _now_iso()},
        })
        await stream_relay.push_event(thread_id, {
            "type": "RUN_FINISHED",
            "threadId": thread_id,
            "interrupted": True,
        })
    with contextlib.suppress(Exception):
        await stream_relay.mark_inactive(thread_id)
    org = record.get("org")
    tokens = [str(t) for t in (record.get("tokens") or []) if t]
    if org and tokens:
        for token in tokens:
            with contextlib.suppress(Exception):
                await stream_relay.unregister_live_run(
                    thread_id, organization_id=org, token=token,
                )
    if owner:
        with contextlib.suppress(Exception):
            await r.hdel(instance_runs_key(owner), thread_id)
    return {"threadId": thread_id, "owner": owner, **record}


async def sweep_dead_instances() -> list[dict[str, Any]]:
    """Close every run that a dead process left marked active.

    One sweeper per dead process: ``SET NX`` on a claim key, so two live
    processes never close the same runs twice. Returns the closed runs'
    records. Logs ``run.interrupted_by_restart`` with the counts.
    """
    r = await _client()
    try:
        ids = await r.smembers(INSTANCES_KEY) or set()
    except Exception:
        _log.warning("run_liveness.sweep_read_failed")
        return []
    closed: list[dict[str, Any]] = []
    dead_instances = 0
    for iid in ids:
        if not iid or iid == INSTANCE_ID:
            continue
        try:
            if await r.exists(instance_key(iid)):
                continue
            claimed = await r.set(
                f"{INSTANCE_SWEEP_PREFIX}:{iid}", INSTANCE_ID, ex=60, nx=True,
            )
            if not claimed:
                continue
            dead_instances += 1
            runs = await r.hgetall(instance_runs_key(iid)) or {}
            for tid in list(runs):
                owner = await r.get(run_owner_key(tid))
                active = await r.get(stream_relay._active_key(tid))
                if owner != iid or active != "1":
                    continue  # it ended, or a new run took the thread
                closed.append(await interrupt_run(tid))
            await r.delete(instance_runs_key(iid))
            await r.srem(INSTANCES_KEY, iid)
        except Exception:
            _log.warning("run_liveness.sweep_failed", instance=str(iid)[:40])
    if dead_instances or closed:
        _log.info(
            "run.interrupted_by_restart",
            instances=dead_instances, runs=len(closed),
            persisted=sum(1 for c in closed if c.get("messageId")),
        )
    if closed and _ON_INTERRUPTED is not None:
        try:
            await _ON_INTERRUPTED(closed)
        except Exception:
            _log.warning("run_liveness.persist_hook_failed", runs=len(closed))
    return closed


# ---------------------------------------------------------------------------
# The beat loop
# ---------------------------------------------------------------------------

async def _heartbeat_loop() -> None:
    n = 0
    while True:
        try:
            await beat()
            if _SWEEP_ON and n % SWEEP_EVERY_BEATS == 0:
                await sweep_dead_instances()
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.warning("run_liveness.beat_failed")
        n += 1
        await asyncio.sleep(INSTANCE_HEARTBEAT_EVERY)


def ensure_heartbeat() -> None:
    """Start the beat loop when none runs. A no-op outside an event loop."""
    global _HEARTBEAT_TASK
    if _HEARTBEAT_TASK is not None and not _HEARTBEAT_TASK.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _HEARTBEAT_TASK = loop.create_task(
        _heartbeat_loop(), name="cc-instance-heartbeat",
    )


def start_instance_heartbeat(
    on_interrupted: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
) -> None:
    """The gateway's startup: beat, sweep now, and sweep every third beat.

    *on_interrupted* persists the closed runs (the gateway's fold). The sweep
    runs inside the loop, so startup never waits on it.
    """
    global _ON_INTERRUPTED, _SWEEP_ON
    _ON_INTERRUPTED = on_interrupted
    _SWEEP_ON = True
    ensure_heartbeat()
    _log.info("run_liveness.started", instance=INSTANCE_ID)


async def stop_instance_heartbeat() -> None:
    """The gateway's shutdown: stop beating and delete this process's key.

    The next process then sees this one's runs as dead at once, rather than
    after the key's TTL. The runs end with this process either way.
    """
    global _HEARTBEAT_TASK, _SWEEP_ON
    _SWEEP_ON = False
    task, _HEARTBEAT_TASK = _HEARTBEAT_TASK, None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=2)
    with contextlib.suppress(Exception):
        r = await _client()
        await r.delete(instance_key(INSTANCE_ID))
    _log.info("run_liveness.stopped", instance=INSTANCE_ID)
