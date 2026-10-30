"""The web push of a reminder: plan it, then send it (WS-66 RE-9b).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.6 "Settled
2026-10-30 (RE-9)", decisions 1, 2, 3, 5 and 6, and the rulings of RE-9b
below them.

**Two steps.**

* :func:`plan_reminder_push` — ``_fire``, ``_check_back`` and ``_give_up`` of
  ``reminders/loop.py`` call it inside the transaction of the step, on the
  loop's own session. It writes ONE planned ``web_push`` row through
  ``outbox.plan_push``. It opens no session, and it sends nothing.
* :func:`send_planned` — ``run_org`` calls it once for each org, after the
  commits of its steps. It claims each planned row (``outbox.claim_push``),
  reads the reminder again, checks the cap, and sends through
  ``push.sender.send_to_member``, the one sender. It never calls
  ``outbound_guard`` itself. ``reach/loop.py`` never runs it, and
  ``whatsapp.deliver`` never sees a ``web_push`` row.

**The gate** (decision 1, ruling a). Both steps run only while
``WEB_PUSH_ENABLED`` (``push.flags.enabled``) and ``CALENDAR_REMINDERS``
(``reminders_enabled``) are on. No ``REACH_*`` flag gates them. With push
off, no row is planned, so a later flip sends no burst of old pushes.

**The key and the body** (decision 2). The dedupe key is
``reminder:<reminder id>:<reminder_events id>``, so each step of each cycle
has its own push. The outbox ``body`` holds the fixed text ``reminder``,
never the title. The send step builds the payload from the reminder row with
the RE-9a builder (``push_message.build``).

**The classes and the caps** (decision 3, ruling d). ``reminder_due`` is
``urgent``: it may break quiet hours, and it sends at most :data:`DUE_CAP`
each day. ``reminder_check`` is ``interrupt``: never in quiet hours, at most
``reach.flags.daily_cap()`` each day. ``reminder_unconfirmed`` is ``brief``:
never in quiet hours, and no cap, because each reminder gives up once. A cap
counts the member's ``sent`` push rows of that kind on the row's local date,
and each row that another send step holds now. The send step counts again
just before each send, and sends one member's rows in order.

**What a planned row becomes** (rulings b and c).

* ``skipped`` — ``expired`` (older than the TTL of its kind), ``gone`` (the
  reminder or its step row is gone), ``closed`` (done, dropped or promoted),
  ``moved`` (a newer step, a move or a snooze), ``quiet``, ``capped`` or
  ``no_device``. No ledger row.
* ``sent`` — at least one device took it. The reminder gains a ``delivered``
  ledger row with the channel ``push``.
* ``failed`` — every device failed. A ``failed`` ledger row, channel ``push``.
* still ``planned`` — the VAPID keys are missing (reason ``no_keys``), or an
  error came before the end. A later tick tries again until the TTL.

Fence: ``tests/unit/test_reach_push.py`` (R8, frozen clock, two orgs, a fake
push service at ``outbound_guard.request_https``).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.push import sender
from gateway.reach import classes, outbox, push_message

_log = get_logger("reach.push")

#: The fixed outbox body of a push row. The title never goes in the outbox.
BODY = "reminder"
#: The most ``reminder_due`` pushes of one member on one local date (D114.3).
DUE_CAP = 10
#: The planned rows that one send step takes for one org, at most.
SEND_BATCH = 100

#: The ledger events that change what a reminder is. A newer one than the
#: step of a push makes that push stale ("moved"). A delivery, an edit of the
#: text and an open do not.
_STEP_EVENTS: tuple[str, ...] = (
    "fired", "check_back", "briefed", "rescheduled", "snoozed", "done",
    "dropped", "promoted", "gave_up", "reopened",
)

_REMINDER_SQL = (
    "SELECT id::text, title, fire_at, tz, state FROM reminders "
    "WHERE id = CAST(:id AS uuid) AND member_email = :m"
)

#: The step row of the push, through the member's own reminder, and whether
#: a newer step exists. No row: the step is gone.
_STEP_SQL = (
    "SELECT EXISTS (SELECT 1 FROM reminder_events n "
    "  WHERE n.reminder_id = x.reminder_id AND n.at > x.at "
    "  AND n.event = ANY(CAST(:steps AS text[]))) AS newer "
    "FROM reminder_events x JOIN reminders r ON r.id = x.reminder_id "
    "WHERE x.id = CAST(:e AS uuid) AND x.reminder_id = CAST(:id AS uuid) "
    "AND r.member_email = :m"
)

#: The org's own name, for a device that holds several accounts.
#: ``organization`` has no row security, so the read names the bound org.
_ORG_NAME_SQL = (
    "SELECT display_name FROM organization "
    "WHERE id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"
)


def push_on() -> bool:
    """The gate of both steps: ``WEB_PUSH_ENABLED`` and ``CALENDAR_REMINDERS``.
    No ``REACH_*`` flag (decision 1)."""
    from gateway.push import flags as push_flags
    from gateway.routes.projects.reminders import reminders_enabled

    return push_flags.enabled() and reminders_enabled()


def dedupe_key(reminder_id: Any, event_id: Any) -> str:
    """``reminder:<reminder id>:<reminder_events id>`` (decision 2)."""
    return f"reminder:{reminder_id}:{event_id}"


def parse_key(key: str) -> tuple[str, str] | None:
    """The reminder id and the step id of a push key, or None."""
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "reminder":
        return None
    try:
        return str(uuid.UUID(parts[1])), str(uuid.UUID(parts[2]))
    except ValueError:
        return None


def cap_of(kind: str) -> int | None:
    """The daily cap of *kind* for one member, or None for no cap."""
    if kind == "reminder_due":
        return DUE_CAP
    if kind == "reminder_check":
        from gateway.reach import flags as reach_flags

        return reach_flags.daily_cap()
    return None


def quiet_blocks(kind: str, day: Any, now: datetime) -> bool:
    """True when *now* is in the member's quiet hours and *kind* may not
    break them. Only an ``urgent`` kind may (``reminder_due``). Quiet hours
    are ``reach.plan.in_quiet_hours``, with every day a working day, as the
    reminder loop reads them (``slot.quiet``)."""
    if classes.REACH_CLASS.get(kind) == "urgent":
        return False
    from gateway.reach.plan import in_quiet_hours

    return in_quiet_hours(day, now, True)


# ── The plan step ───────────────────────────────────────────────────────────


async def plan_reminder_push(db: Any, row: Any, event_id: str | None, kind: str,
                             day: Any, now: datetime) -> str | None:
    """Plan one web push of a reminder step, on *db*, the reminder loop's own
    session, in the transaction of the step. Answers the outbox id, or None
    when nothing was planned.

    *row* is the claimed ``reminders`` row, *event_id* the id of the ledger
    row that the step wrote, *day* the member's ``state.Day``. The member and
    the org come from the row and the bound session, never from input.

    A failure here never stops the step: the insert runs in a SAVEPOINT, and
    an error logs and plans nothing. The card stays in My Day."""
    if kind not in push_message.KINDS:
        raise ValueError(f"not a reminder kind: {kind}")
    if not event_id or not push_on() or quiet_blocks(kind, day, now):
        return None
    try:
        async with db.begin_nested():
            planned = await outbox.plan_push(
                db, str(row.member_email), kind=kind,
                signal_ids=[f"reminders:{row.id}"], key=dedupe_key(row.id, event_id),
                body=BODY, local_date=day.feed_day(now))
    except Exception as exc:  # the step and its ledger row stand
        _log.warning("reach.push.plan_failed", kind=kind, error=type(exc).__name__)
        return None
    return planned


# ── The send step ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Facts:
    """What the claim read of one row found. ``skip`` is the reason that the
    row sends nothing, or None."""

    skip: str | None
    reminder_id: str = ""
    event_id: str = ""
    reminder: dict[str, Any] = field(default_factory=dict)
    org_name: str = ""
    hide: bool = True


async def _stale(db: Any, row: outbox.PushRow, rid: str, eid: str) -> tuple[str | None, Any]:
    """The reason that the reminder no longer wants this push, and its row."""
    from gateway.routes.projects.reminders import OPEN_STATES

    rem = (await db.execute(text(_REMINDER_SQL), {"id": rid, "m": row.member})).first()
    if rem is None:
        return "gone", None
    want = "unconfirmed" if row.kind == "reminder_unconfirmed" else "due"
    if rem.state != want:
        return ("moved" if rem.state in OPEN_STATES else "closed"), rem
    step = (await db.execute(text(_STEP_SQL), {
        "e": eid, "id": rid, "m": row.member, "steps": list(_STEP_EVENTS)})).first()
    if step is None:
        return "gone", rem
    return ("moved" if step.newer else None), rem


async def _held_back(db: Any, row: outbox.PushRow, now: datetime) -> str | None:
    """``quiet`` or ``capped``, or None when the row may send now."""
    if classes.REACH_CLASS.get(row.kind) != "urgent":
        from gateway.reminders.loop import member_day

        if quiet_blocks(row.kind, await member_day(db, row.member), now):
            return "quiet"
    cap = cap_of(row.kind)
    if cap is not None and row.local_date is not None:
        counted = await outbox.pushes_counted(db, row.member, kind=row.kind,
                                              local_date=row.local_date, self_id=row.id)
        if counted >= cap:
            return "capped"
    return None


async def read_facts(db: Any, row: outbox.PushRow, now: datetime) -> Facts:
    """The claim read: on the claim's own session, in its transaction. The
    TTL comes first, so an old row ends even when a later read fails."""
    if row.kind not in push_message.KINDS:
        return Facts("unknown_kind")
    if row.age_s > push_message.ttl_of(row.kind):
        return Facts("expired")
    ids = parse_key(row.key)
    if ids is None:
        return Facts("bad_key")
    rid, eid = ids
    why, rem = await _stale(db, row, rid, eid)
    if why is not None:
        return Facts(why)
    why = await _held_back(db, row, now)
    if why is not None:
        return Facts(why)
    from gateway.routes.shell.prefs import hide_push_details

    hide = await hide_push_details(db, row.member)
    name = (await db.execute(text(_ORG_NAME_SQL))).scalar()
    return Facts(None, reminder_id=rid, event_id=eid,
                 reminder={"id": rid, "title": rem.title, "fire_at": rem.fire_at,
                           "tz": rem.tz},
                 org_name=str(name or ""), hide=hide)


async def _send_one(org: str, outbox_id: str, now: datetime) -> str:
    """Claim, check and send one planned row. Answers the outcome."""

    async def read(db: Any, row: outbox.PushRow) -> Facts:
        return await read_facts(db, row, now)

    got = await outbox.claim_push(org, outbox_id, read)
    if got is None:
        return "held"
    row, facts = got
    if facts.skip is not None:
        await outbox.finish_push(org, row.id, state="skipped", reason=facts.skip)
        _log.info("reach.push.skipped", kind=row.kind, reason=facts.skip)
        return facts.skip
    message = push_message.build(row.kind, facts.reminder, facts.org_name, facts.hide)
    try:
        report = await sender.send_to_member(org, row.member, message)
    except RuntimeError:  # the VAPID keys are missing: keep the row (ruling b)
        await outbox.release_push(org, row.id, reason="no_keys")
        _log.warning("reach.push.no_keys", kind=row.kind)
        return "no_keys"
    except Exception as exc:  # keep the row: a later tick tries again
        await outbox.release_push(org, row.id, reason="error")
        _log.warning("reach.push.send_error", kind=row.kind, error=type(exc).__name__)
        return "error"
    return await _finish(org, row, facts, report)


async def _finish(org: str, row: outbox.PushRow, facts: Facts,
                  report: sender.SendReport) -> str:
    """Mark the row, and write the reminder's ledger row of a delivery."""
    from gateway.routes.projects.reminders import _ledger

    if report.sent >= 1:
        state, reason, event = "sent", None, "delivered"
    elif report.failed >= 1:
        state, reason, event = "failed", "send_failed", "failed"
    else:  # no device (ruling c): no ledger row
        state, reason, event = "skipped", "no_device", None
    detail = {"kind": row.kind, "step_id": facts.event_id, "outbox_id": row.id,
              "devices": report.devices, "sent": report.sent, "failed": report.failed,
              "removed": report.removed, "by": "metorite"}

    async def ledger(db: Any) -> None:
        if event is not None:
            await _ledger(db, facts.reminder_id, event, detail, channel="push")

    await outbox.finish_push(org, row.id, state=state, reason=reason, then=ledger)
    _log.info("reach.push.done", kind=row.kind, state=state, devices=report.devices,
              sent=report.sent)
    return reason or state


async def send_planned(org: str, now: datetime,
                       stopping: Callable[[], bool] | None = None) -> Counter[str]:
    """Send every planned push of *org* that no other send step holds.
    Answers the count of each outcome, each key with the prefix ``push_``.

    ``run_org`` of the reminder loop calls it after the commits of its steps,
    with the org it bound. One failed row never stops the others."""
    counts: Counter[str] = Counter()
    if not push_on():
        return counts
    for outbox_id in await outbox.planned_pushes(org, limit=SEND_BATCH):
        if stopping is not None and stopping():
            break
        try:
            outcome = await _send_one(org, outbox_id, now)
        except Exception as exc:  # the row stays planned, or its claim runs out
            _log.warning("reach.push.row_failed", error=type(exc).__name__)
            outcome = "error"
        counts[f"push_{outcome}"] += 1
    return counts
