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
7. **The requests** of "Tell me on WhatsApp" (RE-5, §11.3). Each open
   request is a card of kind ``notify_me``, never baselined. An expired one
   is logged ``expired``. The member's own request ignores the cap and quiet
   hours. A rule's ``urgent`` request ignores quiet hours and may use one
   send past the cap. A rule's ``normal`` request keeps both. A request that
   may not go now WAITS: nothing is logged, so a later pass sends it before
   it expires. A closed window holds every request, because no template
   fits one. A pass that sent a brief sends no request: one message for
   each pass. The requests ride in the message of the interrupts, and a
   message of only the member's own requests is kind ``request``, which the
   cap does not count. In quiet hours an urgent request goes only when no
   urgent message went today. Its key starts ``urgent:``, so the outbox
   counts it (review of RE-5, P2-1).
8. **The batch window** (D-RE-4). A message of interrupts or of requests
   that the member did not ask for now goes only when no such message went
   in the last 15 minutes. The cards wait, unlogged. The member's own
   request does not wait.
9. **A failed send** logs no request. The request stays open until it
   expires, and its key holds a 10-minute bucket, so the send retries at
   most once in 10 minutes. A sent request is logged and never goes again.

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
from datetime import date, datetime, timedelta
from typing import Any

from acb_common import get_logger

from gateway.reach import flags, outbox, plan, requests
from gateway.reach import whatsapp as channel

_log = get_logger("reach.loop")

TICK_SECONDS = 60.0
MEMBER_TIMEOUT_S = 45.0
STOP_GRACE_SECONDS = 30.0
#: The most "Tell me on WhatsApp" requests in one message (§9.2: 3 items).
MAX_REQUESTS = 3
#: A message that the member did not ask for now waits this long after the
#: last one (D-RE-4). So a backlog does not use the whole cap in minutes.
BATCH_WINDOW = timedelta(minutes=15)
#: A message that carries a request may retry a failed send once in this
#: many seconds: its outbox key holds the bucket (review of RE-5, P2-3).
RETRY_BUCKET_SECONDS = 600
#: The key prefix of a message that used the urgent allowance.
URGENT_PREFIX = "urgent:"
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
                reach: tuple[str | None, bool] | None = None,
                record: str | None = None) -> str:
    """Plan one message, send it, and close its row. Answers the outcome.

    Shielded: the member timeout must not cut between the send and the log,
    or the row stays planned and the items go out again in a later batch.
    *record* is what the member's thread records, when it is not *body*."""
    return await asyncio.shield(_send_now(org, member, kind=kind, cards=cards, key=key,
                                          body=body, local_date=local_date,
                                          template=template, reach=reach,
                                          record=record))


async def _send_now(org: str, member: str, *, kind: str, cards: list[dict[str, Any]],
                    key: str, body: str, local_date: date | None,
                    template: Template | None,
                    reach: tuple[str | None, bool] | None,
                    record: str | None = None) -> str:
    outbox_id = await outbox.plan(org, member, kind=kind,
                                  signal_ids=[c["signal_id"] for c in cards],
                                  key=key, body=body, local_date=local_date)
    if outbox_id is None:
        return "duplicate"
    got = await channel.deliver(org, member, kind=kind, body=body, outbox_id=outbox_id,
                                template=template.name if template else None,
                                template_params=template.params if template else None,
                                reach=reach, record=record)
    await outbox.finish(org, outbox_id, state=got.state, regime=got.regime,
                        reason=got.reason, provider_message_id=got.provider_message_id)
    outcome = "sent" if got.state == "sent" else "skipped"
    if kind in ("interrupt", "request") and not (template is not None and got.state != "sent"):
        # A template that Meta refused logs nothing: the cards stay held, and
        # the first open window sends them as free text. Its key ends in
        # ":tpl", so that free text is a new row (review of RE-2a, P2).
        logs = cards
        if got.state == "failed":
            # A request stays open until it expires, and its key's bucket
            # lets a later pass try again (review of RE-5, P2-3).
            logs = [c for c in cards if not plan.is_request(c)]
        await outbox.log(org, member, map(plan.pair, logs), outcome, outbox_id)
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
    if not await outbox.brief_planned(org, member, due, plan.brief_key(member, due)):
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

    # The requests of "Tell me on WhatsApp" (RE-5). A pass that sent a brief
    # sends no request, so one pass sends one message at most.
    asks = await _request_cards(org, member, snap, did)
    if asks and how != "none":
        did.append("requests_wait")
        asks = []
    await _message_step(org, member, snap, reach, candidates, asks, did)
    return ",".join(did) or "nothing"


class _Budget:
    """The member's interrupts of today, read once and only when needed."""

    def __init__(self, org: str, member: str, snap: Snapshot) -> None:
        self.org, self.member, self.snap = org, member, snap
        self._sent: int | None = None
        self._urgent: int | None = None
        self._recent: bool | None = None

    async def sent(self) -> int:
        if self._sent is None:
            self._sent = await outbox.interrupts_since(
                self.org, self.member, self.snap.day.day_start(self.snap.now))
        return self._sent

    async def urgent(self) -> int:
        """The urgent messages of today (review of RE-5, P2-1)."""
        if self._urgent is None:
            self._urgent = await outbox.urgent_since(
                self.org, self.member, self.snap.day.day_start(self.snap.now))
        return self._urgent

    async def recent(self) -> bool:
        """True when an interrupt went in the last :data:`BATCH_WINDOW`."""
        if self._recent is None:
            self._recent = await outbox.interrupts_since(
                self.org, self.member, self.snap.now - BATCH_WINDOW) > 0
        return self._recent


def is_urgent_request(card: dict[str, Any]) -> bool:
    """A request of a rule or a workflow at ``urgent``."""
    return (plan.is_request(card) and not plan.is_self_request(card)
            and (card.get("facts") or {}).get("urgency") == "urgent")


async def _ready_requests(asks: list[dict[str, Any]], *, quiet: bool,
                          budget: _Budget) -> list[dict[str, Any]]:
    """The requests that may go now, by §11.3. The member's own first, then
    at most :data:`MAX_REQUESTS` (§9.2). The rest wait for a later pass.

    A request that the member did not ask for now waits for the batch
    window. In quiet hours an urgent one goes only when no urgent message
    went today, so the night holds one urgent message at most."""
    cap = flags.daily_cap()
    ready = []
    for ask in asks:
        if plan.is_self_request(ask):
            ready.append(ask)  # past the cap, quiet hours and the batch window
            continue
        if await budget.recent():
            continue
        if is_urgent_request(ask):
            # The urgent allowance (§6.1): one send past the cap.
            if await budget.sent() < cap + 1 and not (quiet and await budget.urgent()):
                ready.append(ask)
        elif not quiet and await budget.sent() < cap:
            ready.append(ask)
    ready.sort(key=lambda c: not plan.is_self_request(c))
    ready = ready[:MAX_REQUESTS]
    if quiet and sum(map(is_urgent_request, ready)) > 1:
        # One urgent message holds them all, so a second one waits.
        first = next(c for c in ready if is_urgent_request(c))
        ready = [c for c in ready if c is first or not is_urgent_request(c)]
    return ready


def message_key(member: str, cards: list[dict[str, Any]], now: datetime) -> str:
    """The outbox key of one message of interrupts or requests.

    A message that carries a request adds a bucket of
    :data:`RETRY_BUCKET_SECONDS`, so a failed send may try again in the
    next bucket. A message with an urgent request starts with
    :data:`URGENT_PREFIX`, so the outbox counts it."""
    key = plan.interrupt_key(member, cards)
    if any(plan.is_request(c) for c in cards):
        key = f"{key}:t{int(now.timestamp()) // RETRY_BUCKET_SECONDS}"
    if any(is_urgent_request(c) for c in cards):
        key = f"{URGENT_PREFIX}{key}"
    return key


async def _message_step(org: str, member: str, snap: Snapshot,
                        reach: tuple[str | None, bool], candidates: list[dict[str, Any]],
                        asks: list[dict[str, Any]], did: list[str]) -> None:
    """The one message of the pass after the brief: the interrupts and the
    requests, under the window, quiet hours and the cap."""
    wa_id, is_open = reach
    closed = wa_id is not None and not is_open
    quiet = plan.in_quiet_hours(snap.day, snap.now, snap.working_day)
    budget = _Budget(org, member, snap)

    go: list[dict[str, Any]] = []
    needs_you: str | None = None
    if candidates:
        needs_you = flags.needs_you_template() if closed else None
        if closed and needs_you is None:
            # HELD: no plan and no log. The first pass with the window open
            # sends them, and the cap and the quiet hours apply then.
            did.append("held")
        elif quiet:
            await outbox.log(org, member, map(plan.pair, candidates), "quiet")
            did.append("quiet")
        elif await budget.sent() >= flags.daily_cap():
            await outbox.log(org, member, map(plan.pair, candidates), "capped")
            did.append("capped")
        elif await budget.recent():
            # The batch window (D-RE-4): no plan and no log, so the first
            # pass after the window sends them in one message.
            did.append("batch_wait")
        else:
            go = candidates

    if asks and closed:
        # HELD: no template fits a request. Nothing is logged, so the first
        # pass with the window open sends it, inside its own rules.
        did.append("requests_held")
        asks = []

    if go and needs_you is not None:
        name = await first_name(org, member)
        sends = Template(needs_you, plan.needs_you_params(name, go, snap.now))
        key = plan.interrupt_key(member, go)
        sent = await _send(org, member, kind="interrupt", cards=go, key=f"{key}:tpl",
                           body=plan.compose_interrupt(go, app_url(), now=snap.now),
                           local_date=snap.local_date, template=sends, reach=reach)
        did.append(f"interrupt:{sent}")
        return

    ready = await _ready_requests(asks, quiet=quiet, budget=budget) if asks else []
    if len(ready) < len(asks):
        did.append("requests_wait")
    body, cards = plan.compose_message(ready + go, app_url(), now=snap.now) \
        if ready or go else ("", [])
    if not cards:
        return
    if len(cards) < len(ready) + len(go):
        # The lines that did not fit stay open for a later pass (P1-5).
        did.append("overflow_wait")
    kind = "request" if all(plan.is_self_request(c) for c in cards) else "interrupt"
    sent = await _send(org, member, kind=kind, cards=cards,
                       key=message_key(member, cards, snap.now), body=body,
                       local_date=snap.local_date, reach=reach,
                       record=plan.thread_record(cards, body))
    did.append(f"{kind}:{sent}")


async def _request_cards(org: str, member: str, snap: Snapshot,
                         did: list[str]) -> list[dict[str, Any]]:
    """The cards of the member's open requests. Logs the expired ones
    ``expired``. A failed read only waits for the next pass."""
    try:
        rows = await requests.undelivered(org, member)
    except Exception as exc:
        _log.warning("reach.requests_failed", error_class=type(exc).__name__)
        return []
    live, gone = requests.split(rows, snap.now)
    if gone:
        await outbox.log(org, member,
                         (plan.pair(requests.card(r, member)) for r in gone), "expired")
        did.append(f"expired:{len(gone)}")
    return [requests.card(r, member) for r in live]


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
