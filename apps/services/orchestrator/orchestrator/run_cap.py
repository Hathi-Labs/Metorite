"""The run cap: at most N live assistant runs per member (WS-51 D-3).

Owner decision, 2026-10-10: "Do not limit the organization because an
organization might have many people, but possibly we can limit 5 agent runs
concurrently per user." So there is ONE cap, per member, and no org cap.
``CHAT_MAX_RUNS_PER_MEMBER`` sets it (default 5, and 0 turns it off).

**Where it is enforced.** ``POST /agent/run/stream`` asks :func:`admit_member_run`
after the steer decision and before the run is minted. A steer into the
member's own live run is not a new run, so the route has returned before it
asks. A refusal is 429 ``too_many_runs``, and it saves and mints nothing.

**What it counts.** The member's own entries in the org's live-run index,
``cc:<org>:liveruns`` (#791), read through the tenant-prefix wrapper. An entry
counts only while :func:`orchestrator.run_liveness.run_liveness` says ``live``
(#797). A run that ended, a run whose process died, and a parked question
(S2) hold no slot. The thread that is about to start is never counted, because
a new run on it replaces the old one.

**One member, one org, today.** The index is per org, and the count reads the
member's own entries in it. One email resolves to one org
(``acb_auth.access.resolve_identity``), so the per-org count IS the per-member
count. If a member of two orgs ever exists, this reads only the runs of the
org that the session binds, so that member could run N in each org. The fix
then is a second, member-keyed index, not a scan of every org.

**Who is exempt.** A caller with no member address (automation, cron, a
service) is not capped. An automation run on a member's behalf does not count
either: those paths bind no session member, so they write no ``actor`` to the
index (``stream_relay.register_live_run``), and an entry with no actor matches
nobody.

**The race.** Two sends at the same moment could both read "4" and both start.
The run registers its index entry only when its stream starts, seconds after
the check. So the check and a RESERVATION are one step, under a per-member
``SET NX`` lock, as ``cc:recover`` does in #797:

    cc:<org>:runcap:<member digest>   SET NX PX, the lock, held for one check
    cc:<org>:runslots                 hash, thread id -> {actor, at}

A reservation counts like a live run until the run registers (which removes
it, ``register_live_run``) or until ``SLOT_TTL_SECONDS`` pass. So under a true
race the cap holds exactly. Two cases can over-admit by one, and both are
documented, not hidden:

* The lock wait runs out (a holder that stalls longer than the lock TTL). The
  check then runs without the lock.
* A run takes longer than ``SLOT_TTL_SECONDS`` between the check and its
  stream start. Its reservation lapses first.

**Fail open.** A Redis error admits the run. The index is advisory, as in
``register_live_run``: Redis trouble never blocks a run.

Fence (R7): ``tests/unit/test_run_cap.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger

from orchestrator import stream_relay

_log = get_logger("orchestrator.run_cap")

LOCK_NAMESPACE = "runcap"
SLOTS_NAMESPACE = stream_relay.RUN_SLOTS_NAMESPACE

#: How long one check may hold the member's lock. A check reads one hash and
#: a few keys, so this is far past any real check.
LOCK_TTL_MS = 3_000
#: How long a send waits for the lock. Longer than the TTL, so a holder that
#: died has always let go before the wait runs out.
LOCK_WAIT_SECONDS = 3.5
_LOCK_POLL_SECONDS = 0.02

#: How long a reservation holds a slot when its run never registers. It covers
#: the memory read, the mint and the stream start.
SLOT_TTL_SECONDS = 60


@dataclass(frozen=True)
class Admission:
    """The answer to "may this member start one more run"."""

    admitted: bool
    limit: int
    #: The thread ids that hold the member's slots now. The refused send
    #: shows them, so the member can open one and stop it.
    running: tuple[str, ...] = field(default=())


def run_cap_limit() -> int:
    """The one reader of ``CHAT_MAX_RUNS_PER_MEMBER``. 0 means no cap.

    A settings failure reads the default, 5. A negative value reads 0.
    """
    try:
        from acb_common.settings import get_settings  # noqa: PLC0415

        return max(0, int(getattr(get_settings(), "chat_max_runs_per_member", 5)))
    except Exception:
        return 5


def _digest(member: str) -> str:
    """The member's lock key part. The address itself never goes in a key."""
    return hashlib.sha256(member.encode("utf-8")).hexdigest()[:24]


def _meta(raw: Any) -> dict[str, Any]:
    try:
        meta = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


async def _take_lock(r: Any, lock: Any, token: str) -> bool:
    """Take the member's lock. False when the wait ran out (see the race)."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        if await r.set(lock, token, px=LOCK_TTL_MS, nx=True):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_LOCK_POLL_SECONDS)


async def _drop_lock(r: Any, lock: Any, token: str) -> None:
    with contextlib.suppress(Exception):
        if await r.get(lock) == token:
            await r.delete(lock)


async def member_runs(
    r: Any, organization_id: str, member: str, *, exclude: str,
) -> list[str]:
    """The thread ids that hold *member*'s slots: live runs, then reservations.

    Call inside ``organization_scope(organization_id)``. *exclude* is the
    thread about to start.
    """
    from acb_common.tenant_redis import TenantKey  # noqa: PLC0415

    from orchestrator.run_liveness import run_liveness  # noqa: PLC0415

    held: list[str] = []
    live = await r.hgetall(TenantKey(organization_id, stream_relay.LIVE_RUNS_NAMESPACE)) or {}
    for tid, raw in live.items():
        if not tid or tid == exclude:
            continue
        if str(_meta(raw).get("actor") or "").strip().lower() != member:
            continue
        # The #797 rule: a run is live only while a process holds it. A dead
        # or ended run frees its slot at once.
        if await run_liveness(tid) != "live":
            continue
        held.append(tid)

    slots_key = TenantKey(organization_id, SLOTS_NAMESPACE)
    slots = await r.hgetall(slots_key) or {}
    now = time.time()
    lapsed: list[str] = []
    for tid, raw in slots.items():
        meta = _meta(raw)
        try:
            at = float(meta.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        if now - at > SLOT_TTL_SECONDS:
            lapsed.append(tid)
            continue
        if not tid or tid == exclude or tid in held:
            continue
        if str(meta.get("actor") or "").strip().lower() != member:
            continue
        held.append(tid)
    if lapsed:
        with contextlib.suppress(Exception):
            await r.hdel(slots_key, *lapsed)
    return held


async def admit_member_run(
    *, organization_id: str | None, member: str | None, thread_id: str,
) -> Admission:
    """May *member* start one more run on *thread_id*? Reserve a slot if so.

    *organization_id* and *member* come from the authenticated session, never
    from the request body. With no org, no member address, or the cap off,
    the run is admitted and nothing is written.
    """
    limit = run_cap_limit()
    who = (member or "").strip().lower()
    if limit <= 0 or not organization_id or "@" not in who or not thread_id:
        return Admission(True, limit)
    try:
        from acb_common.tenant_redis import TenantKey, organization_scope  # noqa: PLC0415

        with organization_scope(organization_id):
            r = await stream_relay._tenant_client()
            lock = TenantKey(organization_id, LOCK_NAMESPACE, _digest(who))
            token = uuid.uuid4().hex
            locked = await _take_lock(r, lock, token)
            if not locked:
                _log.warning("run_cap.lock_wait_ran_out", thread_id=thread_id[:12])
            try:
                held = await member_runs(r, organization_id, who, exclude=thread_id)
                if len(held) >= limit:
                    _log.info(
                        "run_cap.refused", thread_id=thread_id[:12],
                        running=len(held), limit=limit,
                    )
                    return Admission(False, limit, tuple(held))
                slots_key = TenantKey(organization_id, SLOTS_NAMESPACE)
                await r.hset(slots_key, thread_id, json.dumps({
                    "actor": who, "at": time.time(),
                }))
                await r.expire(slots_key, 2 * SLOT_TTL_SECONDS)
                return Admission(True, limit, tuple(held))
            finally:
                if locked:
                    await _drop_lock(r, lock, token)
    except Exception:  # noqa: BLE001 — the cap is advisory, never a blocker
        _log.warning("run_cap.check_failed", thread_id=thread_id[:12])
        return Admission(True, limit)


async def release_member_slot(
    *, organization_id: str | None, thread_id: str,
) -> None:
    """Free the reservation of a run that will not start after all.

    The route calls this when it refuses the run after the cap admitted it.
    Best-effort: a reservation also lapses after ``SLOT_TTL_SECONDS``.
    """
    await stream_relay.drop_run_slot(organization_id, thread_id)
