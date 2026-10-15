"""The Reach loop (WS-59 RE-1a, ``proactive_reach.md`` §7.2 and §7.3).

Every 60 seconds, for each org of ``REACH_ORGS`` and each member of
``REACH_MEMBERS``, the loop runs one member pass:

1. **Who.** ``member_context`` resolves the member, as the WhatsApp run does.
   A member whose home org is not this org is skipped (``other_org``), and so
   is one who left it (``my_day_loop.departed``).
2. **What.** ``feed.feed_cards``: the member's My Day cards after the state
   join. There is no second compute path.
3. **The first run** logs every card as ``baseline`` and sends nothing. So
   switching Reach on does not send a day of old cards at once.
4. **The brief**, once for each local date, in the first 3 hours of a working
   day. The cards it names that would interrupt are logged ``deferred``.
5. **The interrupts.** The new cards of class ``interrupt`` go out in ONE
   message. In quiet hours they are logged ``quiet``. Over the daily cap they
   are logged ``capped``. Neither sends.

**Dark by default.** ``REACH_ENABLED`` is off, and :func:`start_reach_loop`
then starts no task. The org list and the member list are empty by default,
and empty allows nobody.

**No unbound read.** The loop takes its orgs from ``REACH_ORGS``, so it adds
no tenant-discovery site. Every statement runs in ``tenant_session(org)``.

Every failure logs and never raises out of the tick. Fences:
``tests/unit/test_reach_loop.py`` (hermetic) and ``tests/unit/test_reach_r8.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from acb_common import get_logger

from gateway.reach import flags, outbox, plan
from gateway.reach import whatsapp as channel

_log = get_logger("reach.loop")

TICK_SECONDS = 60.0
MEMBER_TIMEOUT_S = 45.0
STOP_GRACE_SECONDS = 30.0
#: The one row a first run writes when the member has no card, so the next
#: run is not a first run too.
BASELINE_MARK = ("reach:baseline", "0")

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def app_url() -> str:
    """The app's address for the My Day link: the WhatsApp channel's own."""
    from gateway.routes.whatsapp_channel.views import APP_URL

    return APP_URL


@dataclass(frozen=True)
class Snapshot:
    """One member's cards and day, read once for a pass."""

    cards: list[dict[str, Any]]
    now: datetime
    day: Any
    working_day: bool
    #: False when a provider failed or the state join failed. Then a dismissed
    #: card reads as new and a missing card reads as gone, so nothing may be
    #: planned or logged from this read (review of RE-1a, P2).
    complete: bool = True

    @property
    def local_date(self) -> date:
        return self.day.feed_day(self.now)


async def snapshot(org: str, user: Any, now: datetime | None = None) -> Snapshot:
    """The cards of the feed, the member's day, and if today is a working day."""
    from gateway.my_day_loop import _read_day
    from gateway.routes.shell import feed

    run = await feed.feed_cards(user, now)
    day = run.joined.day or await _read_day(org, user)
    working = await feed._working_day(user, day.feed_day(run.now))
    complete = bool(run.joined.ok) and not any(
        v not in ("ok", "absent") for v in run.sources.values())
    return Snapshot(list(run.cards), run.now, day, working, complete)


async def resolve(org: str, member: str) -> Any | str:
    """The member's ``UserContext``, or the reason the pass skips them."""
    from acb_auth.deps import member_context

    from gateway.my_day_loop import IdentityReadFailed, departed

    try:
        gone = await departed(org, member)
    except IdentityReadFailed:
        return "identity_failed"
    if gone is not None:
        return "other_org" if gone == "other_org" else "departed"
    user = await member_context(member)
    if str(getattr(user, "organization_id", None) or "") != org:
        _log.warning("reach.member_other_org")
        return "other_org"
    return user


# ── The sends ───────────────────────────────────────────────────────────────


async def _send(org: str, member: str, *, kind: str, cards: list[dict[str, Any]],
                key: str, body: str, local_date: date | None) -> str:
    """Plan one message, send it, and close its row. Answers the outcome.

    Shielded: the member timeout must not cut between the send and the log,
    or the row stays planned and the items go out again in a later batch."""
    return await asyncio.shield(_send_now(org, member, kind=kind, cards=cards, key=key,
                                          body=body, local_date=local_date))


async def _send_now(org: str, member: str, *, kind: str, cards: list[dict[str, Any]],
                    key: str, body: str, local_date: date | None) -> str:
    outbox_id = await outbox.plan(org, member, kind=kind,
                                  signal_ids=[c["signal_id"] for c in cards],
                                  key=key, body=body, local_date=local_date)
    if outbox_id is None:
        return "duplicate"
    template = flags.brief_template() if kind == "brief" else None
    got = await channel.deliver(org, member, kind=kind, body=body,
                                outbox_id=outbox_id, template=template)
    await outbox.finish(org, outbox_id, state=got.state, regime=got.regime,
                        reason=got.reason, provider_message_id=got.provider_message_id)
    outcome = "sent" if got.state == "sent" else "skipped"
    if kind == "interrupt":
        await outbox.log(org, member, map(plan.pair, cards), outcome, outbox_id)
    _log.info("reach.message", kind=kind, state=got.state, reason=got.reason,
              items=len(cards))
    return got.state if got.reason is None else f"{got.state}:{got.reason}"


async def send_brief(org: str, member: str, snap: Snapshot, *, key: str) -> str:
    """Compose and send one brief. Answers what happened."""
    body = plan.compose_brief(snap.cards, app_url(), snap.local_date, now=snap.now)
    if body is None:
        return "empty"
    top = [c for c in snap.cards if c.get("group") in plan.BRIEF_GROUPS and plan.is_open(c)]
    return await _send(org, member, kind="brief", cards=top, key=key, body=body,
                       local_date=snap.local_date)


async def member_pass(org: str, member: str, now: datetime | None = None) -> str:
    """One member's pass. Answers what it did, for the log and the tests."""
    user = await resolve(org, member)
    if isinstance(user, str):
        return user
    snap = await snapshot(org, user, now)
    if not snap.complete:
        # The next tick reads again. A baseline from a partial read would let
        # every old card interrupt on the tick after it.
        _log.info("reach.read_incomplete")
        return "incomplete"

    if not await outbox.has_log(org, member):
        pairs = [plan.pair(c) for c in snap.cards] or [BASELINE_MARK]
        await outbox.log(org, member, pairs, "baseline")
        _log.info("reach.baseline", cards=len(snap.cards))
        return "baseline"

    did: list[str] = []
    logged = await outbox.logged_pairs(org, member)
    candidates = plan.interrupt_candidates(snap.cards, logged, snap.now)

    due = plan.brief_date(snap.day, snap.now, snap.working_day)
    if due is not None and not await outbox.brief_planned(org, member, due):
        brief = await send_brief(org, member, snap, key=plan.brief_key(member, due))
        did.append(f"brief:{brief}")
        if brief == "sent" and candidates:
            # The brief already named them. One message, not two.
            await outbox.log(org, member, map(plan.pair, candidates), "deferred")
            candidates = []

    if candidates:
        if plan.in_quiet_hours(snap.day, snap.now, snap.working_day):
            await outbox.log(org, member, map(plan.pair, candidates), "quiet")
            did.append("quiet")
        elif await outbox.interrupts_since(
                org, member, snap.day.day_start(snap.now)) >= flags.daily_cap():
            await outbox.log(org, member, map(plan.pair, candidates), "capped")
            did.append("capped")
        else:
            body = plan.compose_interrupt(candidates, app_url(), now=snap.now)
            sent = await _send(org, member, kind="interrupt", cards=candidates,
                               key=plan.interrupt_key(member, candidates), body=body,
                               local_date=snap.local_date)
            did.append(f"interrupt:{sent}")
    return ",".join(did) or "nothing"


# ── The loop ────────────────────────────────────────────────────────────────


async def _one(org: str, member: str, now: datetime | None) -> str:
    # Its own task, because member_context binds a tenant and never releases it.
    task = asyncio.get_running_loop().create_task(member_pass(org, member, now),
                                                  name="reach-member")
    try:
        return await asyncio.wait_for(task, MEMBER_TIMEOUT_S)
    except TimeoutError:
        _log.warning("reach.member_timed_out")
        return "timeout"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one member must not stop the others
        _log.warning("reach.member_failed", error_class=type(exc).__name__)
        return "failed"


async def run_tick(now: datetime | None = None) -> dict[tuple[str, str], str]:
    """One pass over each allowed org and member, one member at a time."""
    if not flags.enabled():
        return {}
    done: dict[tuple[str, str], str] = {}
    for org in sorted(flags.orgs()):
        for member in sorted(flags.members()):
            if _stopping.is_set():
                return done
            done[(org, member)] = await _one(org, member, now)
    return done


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("reach.loop_error", error_class=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=TICK_SECONDS)


async def start_reach_loop() -> bool:
    """Start the loop when ``REACH_ENABLED`` is on. Idempotent."""
    global _task
    if not flags.enabled():
        _log.info("reach.loop.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="reach-loop")
    _log.info("reach.loop.started", tick_secs=TICK_SECONDS)
    return True


async def stop_reach_loop() -> None:
    """Stop the loop and wait for a tick in progress. Safe when it never started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("reach.loop.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still
