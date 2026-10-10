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

The record holds the org id, the message row id, the agent and the run id. It
holds NO member email: the sweep reads the actor from ``cc:runactor:{tid}``,
which sits beside ``cc:active``. The hash is keyed by PROCESS, because one
process holds the runs of many orgs, so no single tenant can prefix it.

**Why an instance id and a heartbeat, and not the other two options.** A
heartbeat per RUN would need a writer per run, and a run parked on a question
pushes nothing for an hour. A control-bus ack answers only when a command is
sent, so it cannot find dead runs at startup. A heartbeat per PROCESS is one
key per process, it is written by a task that does nothing else, and it
answers both questions: "is this run alive" and "which runs did a dead process
leave". Several gateway processes can share one Redis. Each has its own id and
answers only for its own key, so a second worker never reads a sibling's live
run as dead.

**A command that no process heard proves nothing by itself.** With more than
one worker, a steer can arrive before the owner's listener subscribes, or after
a listener exits on a Redis error, while the owner is alive. So a run is dead
ONLY when its owner's heartbeat key is gone (or this process owns it and holds
no such run). The route re-checks that after an undelivered steer, and never
decides on the delivery alone.

**One recovery at a time.** ``cc:recover:{tid}`` is a ``SET NX`` claim with a
short TTL. The sweep and every request take it before they close a dead run. A
request that loses waits for the winner (:func:`wait_out_recovery`), and then
routes as usual, so a second run never starts. A new run on the thread
releases the claim (:func:`claim_run`), because the recovery is then over.

**The sweep.** :func:`sweep_dead_instances` runs at startup and then every
third heartbeat. For each run a dead process left, ONE AT A TIME and while the
run is still marked active: it pushes a ``run_interrupted`` marker into the
stream, folds the stream into the saved reply (the gateway's hook), and only
THEN marks the run inactive and clears the org's live-run entry (#791). A new
run cannot start in between, because the run is still active and the claim is
held. The fold also refuses a stream whose ``RUN_STARTED`` names another run.

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
RECOVER_PREFIX = "cc:recover"

#: How long one recovery may hold its claim. It closes a run and saves its
#: reply, and a winner request then starts the next run, which releases it.
RECOVER_TTL_SECONDS = int(os.environ.get("RUN_RECOVER_TTL_SECONDS", "15"))

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
_ON_INTERRUPTED: Callable[[list[dict[str, Any]]], Awaitable[Any]] | None = None
_SWEEP_ON = False


def run_owner_key(thread_id: str) -> str:
    return f"{RUN_OWNER_PREFIX}:{thread_id}"


def instance_key(instance_id: str) -> str:
    return f"{INSTANCE_PREFIX}:{instance_id}"


def instance_runs_key(instance_id: str) -> str:
    return f"{INSTANCE_RUNS_PREFIX}:{instance_id}"


def recover_key(thread_id: str) -> str:
    return f"{RECOVER_PREFIX}:{thread_id}"


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
        # A run started on this thread, so any recovery of it is over, and
        # the requests that wait on it may route into this run now.
        await r.delete(recover_key(thread_id))
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

    *record* holds server-side facts only: the org, the row id the run
    persists to, the run id, and the tokens of its live-run index entry. No
    member email: the sweep reads the actor from ``cc:runactor``.
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


async def run_liveness(thread_id: str) -> str:
    """``"idle"``, ``"live"`` or ``"dead"`` for the run on *thread_id*.

    * ``idle``: no run is marked active.
    * ``dead``: the owner process has no heartbeat key, or this process owns
      it and holds no such run. Nothing else: an undelivered command alone
      never makes a run dead (see the module docstring).
    * ``live``: anything else, including a run with no recorded owner (an
      older build) and every case this function cannot read. A false "dead"
      would start a second run on a live one, so an error answers "live" and
      the caller keeps the old behaviour.
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
        return "live"
    except Exception:
        _log.warning("run_liveness.read_failed", thread_id=thread_id[:12])
        return "live"


# ---------------------------------------------------------------------------
# One recovery at a time
# ---------------------------------------------------------------------------

async def claim_recovery(thread_id: str) -> str | None:
    """Take the thread's recovery claim. Returns its token, or None if taken.

    An unreadable Redis answers None: the caller then does not recover, and
    keeps the old behaviour (the steer is stored for replay).
    """
    token = uuid.uuid4().hex
    try:
        r = await _client()
        ok = await r.set(recover_key(thread_id), token, ex=RECOVER_TTL_SECONDS, nx=True)
        return token if ok else None
    except Exception:
        _log.warning("run_liveness.claim_recovery_failed", thread_id=thread_id[:12])
        return None


async def release_recovery(thread_id: str, token: str) -> None:
    """Drop the claim, only if it is still this token's. Stops its keeper."""
    _stop_keeper(thread_id, token)
    with contextlib.suppress(Exception):
        r = await _client()
        if await r.get(recover_key(thread_id)) == token:
            await r.delete(recover_key(thread_id))


# ── The claim lives as long as its holder works (review of #797, race 2) ────
#
# A fold writes to the database, and a slow database has no upper bound. A
# fixed TTL sized to "the worst case" is therefore a guess, and a fold that
# outlives it lets a second claimant through, so two runs start. So the
# holder RENEWS the claim while it works: a small task pushes the TTL out
# every third of it. The claim then lives exactly as long as its holder:
# when the holder's process dies, the renewals stop, and the claim lapses
# within one TTL. A holder that keeps the claim for the run it starts
# (``hold=True``) renews until that run's ``claim_run`` deletes the key, or
# until ``CLAIM_KEEP_MAX_SECONDS``.
#
# The renewal reads the token and then pushes the TTL, in two calls. A rival
# that took the key in between gets its own claim extended once, which delays
# nothing that matters.

CLAIM_KEEP_MAX_SECONDS = 120.0
_KEEPERS: dict[str, tuple[str, asyncio.Task[None]]] = {}


async def _keep_claim(thread_id: str, token: str) -> None:
    loop = asyncio.get_running_loop()
    until = loop.time() + CLAIM_KEEP_MAX_SECONDS
    every = max(RECOVER_TTL_SECONDS / 3, 0.05)
    while loop.time() < until:
        await asyncio.sleep(every)
        try:
            r = await _client()
            if await r.get(recover_key(thread_id)) != token:
                return  # released, or taken over by a new run
            await r.expire(recover_key(thread_id), RECOVER_TTL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.warning("run_liveness.claim_renew_failed", thread_id=thread_id[:12])


def _start_keeper(thread_id: str, token: str) -> None:
    _stop_keeper(thread_id, None)
    try:
        task = asyncio.get_running_loop().create_task(
            _keep_claim(thread_id, token), name=f"cc-recover-keep-{thread_id[:16]}",
        )
    except RuntimeError:
        return
    _KEEPERS[thread_id] = (token, task)


def _stop_keeper(thread_id: str, token: str | None) -> None:
    held = _KEEPERS.get(thread_id)
    if held is None or (token is not None and held[0] != token):
        return
    _KEEPERS.pop(thread_id, None)
    held[1].cancel()


async def _dead_run_snapshot(thread_id: str) -> tuple[str, str] | None:
    """``(owner, runId)`` of the run on *thread_id* while it is dead, else None."""
    if await run_liveness(thread_id) != "dead":
        return None
    try:
        r = await _client()
        owner = await r.get(run_owner_key(thread_id)) or ""
        raw = await r.hget(instance_runs_key(owner), thread_id) if owner else None
        run_id = str((json.loads(raw) if raw else {}).get("runId") or "")
    except Exception:
        return None
    return owner, run_id


async def claim_dead_run(thread_id: str, *, owner: str | None = None) -> str | None:
    """Claim the recovery of a DEAD run, and prove it is still the same run.

    Review of #797, race 1. "Is it dead?" and "take the claim" are two steps.
    Between them a rival can finish its own recovery and start a new run, and
    that run's ``claim_run`` deletes the claim. The late claimant then gets
    the claim, and would close the live NEW run. ``expect_run_id`` would not
    save it, because by then the record is the new run's.

    So: look (owner, runId while dead), claim, and look again. Proceed only
    when the run is still dead and the owner and runId have not moved.
    Otherwise release the claim and answer None: the caller then takes the
    steer path. *owner*, when given, must be the dead run's owner (the sweep
    names the process it is sweeping). A winner's claim is kept alive by a
    renewal task until it is released.
    """
    before = await _dead_run_snapshot(thread_id)
    if before is None or (owner is not None and before[0] != owner):
        return None
    token = await claim_recovery(thread_id)
    if token is None:
        return None
    if await _dead_run_snapshot(thread_id) != before:
        await release_recovery(thread_id, token)
        _log.info("run_liveness.recovery_moved_on", thread_id=thread_id[:12])
        return None
    _start_keeper(thread_id, token)
    return token


async def recovery_in_progress(thread_id: str) -> bool:
    try:
        r = await _client()
        return bool(await r.exists(recover_key(thread_id)))
    except Exception:
        return False


async def wait_out_recovery(
    thread_id: str, *, timeout: float | None = None, poll: float = 0.2,
) -> None:
    """Wait while another party recovers this thread, at most the claim's TTL.

    The winner's new run releases the claim when it starts, so a waiter then
    routes INTO that run (a steer) instead of starting a second one.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (RECOVER_TTL_SECONDS if timeout is None else timeout)
    while await recovery_in_progress(thread_id) and loop.time() < deadline:
        await asyncio.sleep(poll)


# ---------------------------------------------------------------------------
# Closing a dead run
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def interrupt_run(
    thread_id: str,
    *,
    reason: str = "restart",
    persist: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Close a dead run. The caller holds the thread's recovery claim.

    In this order, and the order is the fix for a race:

    1. the marker events go into the stream;
    2. *persist* folds the stream into the saved reply, WHILE the run is still
       marked active, so no new run can start and reset the stream;
    3. the run is marked inactive, the org's live-run entry goes (#791), and
       the dead owner's record goes.
    """
    r = await _client()
    owner = None
    record: dict[str, Any] = {}
    actor = None
    with contextlib.suppress(Exception):
        owner = await r.get(run_owner_key(thread_id))
        if owner:
            raw = await r.hget(instance_runs_key(owner), thread_id)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    record = parsed
    with contextlib.suppress(Exception):
        actor = await stream_relay.get_run_actor(thread_id)
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
    rec = {"threadId": thread_id, "owner": owner, **record, "actor": actor}
    if persist is not None:
        try:
            await persist(rec)
        except Exception:
            _log.warning("run_liveness.persist_failed", thread_id=thread_id[:12])
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
    return rec


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
                # One run at a time, under the thread's claim. A request that
                # recovers this thread right now holds it, so skip the run.
                # The claim re-checks that the run is still this dead one.
                token = await claim_dead_run(tid, owner=iid)
                if token is None:
                    continue
                try:
                    closed.append(await interrupt_run(tid, persist=_persist_one))
                finally:
                    await release_recovery(tid, token)
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
    return closed


async def _persist_one(rec: dict[str, Any]) -> None:
    """The gateway's fold for ONE run (``gateway.chat_recovery``)."""
    if _ON_INTERRUPTED is not None:
        await _ON_INTERRUPTED([rec])


# ---------------------------------------------------------------------------
# The beat loop
# ---------------------------------------------------------------------------

_SWEEP_TASK: asyncio.Task[None] | None = None


async def _sweep_quietly() -> None:
    try:
        await sweep_dead_instances()
    except Exception:  # noqa: BLE001
        _log.warning("run_liveness.sweep_crashed")


async def _heartbeat_loop() -> None:
    global _SWEEP_TASK
    n = 0
    while True:
        try:
            await beat()
            # ⚠️ The sweep runs in its OWN task. It folds replies into the
            # database, and a slow database must never delay a beat past the
            # TTL: a sibling would then read this live process as dead, and
            # start a second run on each of its threads. One sweep at a time.
            if (
                _SWEEP_ON and n % SWEEP_EVERY_BEATS == 0
                and (_SWEEP_TASK is None or _SWEEP_TASK.done())
            ):
                _SWEEP_TASK = asyncio.get_running_loop().create_task(
                    _sweep_quietly(), name="cc-instance-sweep",
                )
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
    global _HEARTBEAT_TASK, _SWEEP_ON, _SWEEP_TASK
    _SWEEP_ON = False
    sweep, _SWEEP_TASK = _SWEEP_TASK, None
    if sweep is not None and not sweep.done():
        sweep.cancel()
    task, _HEARTBEAT_TASK = _HEARTBEAT_TASK, None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(asyncio.shield(task), timeout=2)
    with contextlib.suppress(Exception):
        r = await _client()
        await r.delete(instance_key(INSTANCE_ID))
    _log.info("run_liveness.stopped", instance=INSTANCE_ID)
