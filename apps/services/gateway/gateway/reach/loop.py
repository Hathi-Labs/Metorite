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
4. **The window.** The pass reads the member's link and 24-hour window once
   (RE-2a). Meta allows free text only while the window is open.
5. **The brief**, once for each local date, at any hour of a working day.
   Window open: free text, and the cards it names that would interrupt are
   logged ``deferred``. Window closed: the brief template in the first 3
   hours of the day, when one is set. Else the brief is HELD, and it goes on
   the first pass of the day with the window open. After a template brief,
   the full brief follows once as free text when the window opens.
6. **The interrupts.** The new cards of class ``interrupt`` go out in ONE
   message. Window closed: the needs-you template, when one is set. Else
   they are HELD: nothing is planned or logged, so the first pass with the
   window open sends them. In quiet hours they are logged ``quiet``. Over
   the daily cap they are logged ``capped``. Neither sends.
   A member with no link is not held: the pass plans, and the channel skips
   with ``no_link``, as before.

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


@dataclass(frozen=True)
class Template:
    """A template send: its name and its body parameters."""

    name: str
    params: list[str]


async def _send(org: str, member: str, *, kind: str, cards: list[dict[str, Any]],
                key: str, body: str, local_date: date | None,
                template: Template | None = None,
                reach: tuple[str | None, bool] | None = None) -> str:
    """Plan one message, send it, and close its row. Answers the outcome.

    Shielded: the member timeout must not cut between the send and the log,
    or the row stays planned and the items go out again in a later batch."""
    return await asyncio.shield(_send_now(org, member, kind=kind, cards=cards, key=key,
                                          body=body, local_date=local_date,
                                          template=template, reach=reach))


async def _send_now(org: str, member: str, *, kind: str, cards: list[dict[str, Any]],
                    key: str, body: str, local_date: date | None,
                    template: Template | None,
                    reach: tuple[str | None, bool] | None) -> str:
    outbox_id = await outbox.plan(org, member, kind=kind,
                                  signal_ids=[c["signal_id"] for c in cards],
                                  key=key, body=body, local_date=local_date)
    if outbox_id is None:
        return "duplicate"
    got = await channel.deliver(org, member, kind=kind, body=body, outbox_id=outbox_id,
                                template=template.name if template else None,
                                template_params=template.params if template else None,
                                reach=reach)
    await outbox.finish(org, outbox_id, state=got.state, regime=got.regime,
                        reason=got.reason, provider_message_id=got.provider_message_id)
    outcome = "sent" if got.state == "sent" else "skipped"
    if kind == "interrupt" and not (template is not None and got.state != "sent"):
        # A template that Meta refused logs nothing: the cards stay held, and
        # the first open window sends them as free text. Its key ends in
        # ":tpl", so that free text is a new row (review of RE-2a, P2).
        await outbox.log(org, member, map(plan.pair, cards), outcome, outbox_id)
    _log.info("reach.message", kind=kind, state=got.state, reason=got.reason,
              items=len(cards))
    return got.state if got.reason is None else f"{got.state}:{got.reason}"


async def first_name(org: str, member: str) -> str:
    """The name a template greets. Read only when a template goes out."""
    return plan.first_name(await channel.display_name(org, member), member)


async def send_brief(org: str, member: str, snap: Snapshot, *, key: str,
                     template: str | None = None,
                     reach: tuple[str | None, bool] | None = None,
                     record_empty: bool = False) -> str:
    """Compose and send one brief. Answers what happened.

    *template* is the brief template the channel uses when the window is
    closed. ``POST /reach/brief`` passes the setting. The loop passes it only
    when it chose the template itself."""
    body = plan.compose_brief(snap.cards, app_url(), snap.local_date, now=snap.now)
    if body is None:
        if record_empty:
            # The day's brief had nothing to say. A row says so, or the first
            # card of the afternoon would arrive as a "Your day" brief, past
            # the cap (review of RE-2a, P1).
            row = await outbox.plan(org, member, kind="brief", signal_ids=[], key=key,
                                    body="", local_date=snap.local_date)
            if row is not None:
                await outbox.finish(org, row, state="skipped", reason="empty")
        return "empty"
    sends = None
    if template:
        name = await first_name(org, member)
        sends = Template(template, plan.brief_params(name, snap.cards, snap.local_date,
                                                     snap.now))
    return await _send(org, member, kind="brief", cards=plan.brief_items(snap.cards),
                       key=key, body=body, local_date=snap.local_date,
                       template=sends, reach=reach)


async def _brief_step(org: str, member: str, snap: Snapshot,
                      reach: tuple[str | None, bool]) -> tuple[str | None, str]:
    """The brief of this pass. Answers what it did, or None, and how the
    brief went: ``free_text`` (its cards need not interrupt), ``template``
    (its cards wait for the full brief) or ``none``."""
    due = plan.brief_date(snap.day, snap.now, snap.working_day)
    if due is None:
        return None, "none"
    wa_id, is_open = reach
    if not await outbox.brief_planned(org, member, due):
        template = flags.brief_template()
        if wa_id is None or is_open:
            # Free text, or the channel skips no_link, as before RE-2a.
            got = await send_brief(org, member, snap, key=plan.brief_key(member, due),
                                   reach=reach, record_empty=True)
            return f"brief:{got}", "free_text" if got == "sent" else "none"
        if template and plan.brief_morning(snap.day, snap.now):
            got = await send_brief(org, member, snap, key=plan.brief_key(member, due),
                                   template=template, reach=reach)
            # The template named one item. The others wait for the full brief
            # of the first open window, so one pass never sends two paid
            # templates about the same card (review of RE-2a, P2).
            return f"brief:{got}", "template" if got == "sent" else "none"
        return "brief:held", "none"
    full_key = plan.full_brief_key(member, due)
    if is_open and await outbox.full_brief_due(org, member, due, full_key):
        body = plan.compose_brief(snap.cards, app_url(), due, now=snap.now)
        if body is None:
            return None, "none"
        got = await _send(org, member, kind="brief", cards=plan.brief_items(snap.cards),
                          key=full_key, body=body, local_date=due, reach=reach)
        return f"brief_full:{got}", "free_text" if got == "sent" else "none"
    return None, "none"


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

    try:
        reach = await channel.reach_state(org, member)
    except Exception as exc:
        _log.warning("reach.state_failed", error_class=type(exc).__name__)
        return "state_failed"
    wa_id, is_open = reach
    closed = wa_id is not None and not is_open

    did: list[str] = []
    logged = await outbox.logged_pairs(org, member)
    candidates = plan.interrupt_candidates(snap.cards, logged, snap.now)

    brief, how = await _brief_step(org, member, snap, reach)
    if brief is not None:
        did.append(brief)
    if how == "template" and candidates:
        # HELD for the full brief of the first open window.
        did.append("held")
        candidates = []
    if how == "free_text" and candidates:
        # The brief already named them. One message, not two.
        await outbox.log(org, member, map(plan.pair, candidates), "deferred")
        candidates = []

    if candidates:
        needs_you = flags.needs_you_template() if closed else None
        if closed and needs_you is None:
            # HELD: no plan and no log. The first pass with the window open
            # sends them, and the cap and the quiet hours apply then.
            did.append("held")
        elif plan.in_quiet_hours(snap.day, snap.now, snap.working_day):
            await outbox.log(org, member, map(plan.pair, candidates), "quiet")
            did.append("quiet")
        elif await outbox.interrupts_since(
                org, member, snap.day.day_start(snap.now)) >= flags.daily_cap():
            await outbox.log(org, member, map(plan.pair, candidates), "capped")
            did.append("capped")
        else:
            body = plan.compose_interrupt(candidates, app_url(), now=snap.now)
            sends = None
            if needs_you is not None:
                name = await first_name(org, member)
                sends = Template(needs_you, plan.needs_you_params(name, candidates, snap.now))
            key = plan.interrupt_key(member, candidates)
            sent = await _send(org, member, kind="interrupt", cards=candidates,
                               key=f"{key}:tpl" if sends is not None else key, body=body,
                               local_date=snap.local_date, template=sends, reach=reach)
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
