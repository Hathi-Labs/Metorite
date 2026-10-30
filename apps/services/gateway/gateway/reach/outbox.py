"""The one writer of ``reach_outbox`` and ``reach_signal_log`` (WS-59 RE-1a).

Spec: ``project-docs/specs/proactive_reach.md`` §6 and §7. Migration
``266_reach_outbox.sql``.

Each function opens ``tenant_session(org)`` with the org the caller resolved,
so RLS binds every statement (R5). The organization column takes its value
from the bound tenant, never from a parameter. ``tests/unit/test_reach_r8.py``
runs this SQL on a real Postgres.

**The web push rows** (WS-66 RE-9b, ``calendar_events_reminders.md`` §5.6).
The channel ``web_push`` holds one push of one reminder step. Two helpers
take the CALLER's session, because the reminder loop plans the row in the
transaction of the step: :func:`plan_push` and :func:`pushes_counted`. The
send step runs after that commit, so its helpers open their own
``tenant_session(org)`` here, and nowhere else: :func:`planned_pushes`,
:func:`claim_push`, :func:`release_push` and :func:`finish_push`.

A send step CLAIMS a planned row before it sends: ``reason = 'sending'`` and
``sent_at`` = the claim time, for :data:`PUSH_LEASE_S` seconds. A second
worker skips a row under a live claim. A crash leaves the row planned, and a
later tick claims it again once the claim is old, so delivery is at least
once. The WhatsApp reads count ``channel = 'whatsapp'`` only, so a push row
never counts in the WhatsApp cap. ``tests/unit/test_reach_push.py`` runs this
SQL on a real Postgres.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from acb_common.db import tenant_session
from sqlalchemy import text

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_LOGGED_SQL = (
    "SELECT signal_id, fingerprint FROM reach_signal_log "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m"
)

_ANY_LOG_SQL = (
    "SELECT EXISTS (SELECT 1 FROM reach_signal_log "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m)"
)

_LOG_SQL = (
    "INSERT INTO reach_signal_log (organization_id, member_email, signal_id, "
    f"fingerprint, outcome, outbox_id) VALUES ({_BOUND_ORG}, :m, :s, :f, :o, "
    "CAST(:ob AS uuid)) "
    "ON CONFLICT (organization_id, member_email, signal_id, fingerprint) DO NOTHING"
)

_PLAN_SQL = (
    "INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
    "signal_ids, dedupe_key, body, state, local_date) VALUES "
    f"({_BOUND_ORG}, :m, 'whatsapp', :k, CAST(:ids AS text[]), :key, :body, "
    "'planned', :d) "
    "ON CONFLICT (organization_id, dedupe_key) DO NOTHING RETURNING id::text"
)

_FINISH_SQL = (
    "UPDATE reach_outbox SET state = :st, regime = :rg, reason = :r, "
    "provider_message_id = :pm, "
    "sent_at = CASE WHEN :st = 'sent' THEN now() ELSE sent_at END "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    "AND state = 'planned'"
)

#: The WhatsApp interrupts that went since a time: the budget of the Reach
#: loop. A web push row never counts here (WS-66 RE-9b).
_SENT_SINCE_SQL = (
    "SELECT count(*) FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND channel = 'whatsapp' "
    "AND kind = 'interrupt' AND state = 'sent' AND created_at >= :since"
)

#: The urgent messages that went since a time (review of RE-5, P2-1). The
#: loop gives such a message a key that starts ``urgent:``. WhatsApp only.
_URGENT_SINCE_SQL = (
    "SELECT count(*) FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND channel = 'whatsapp' "
    "AND kind = 'interrupt' AND state = 'sent' AND created_at >= :since "
    "AND dedupe_key LIKE 'urgent:%'"
)

# ── The web push rows (WS-66 RE-9b) ─────────────────────────────────────────

#: Seconds that a claim of a send step holds a planned push row. It covers the
#: send budget of one member's devices (``push.sender.SEND_BUDGET_S``, 20) with
#: room to spare, and it is short enough that a crash costs one or two ticks.
PUSH_LEASE_S = 120

#: True while another send step holds the row.
_HELD = (
    "(reason IS NOT DISTINCT FROM 'sending' "
    "AND sent_at > now() - make_interval(secs => :lease))"
)

_PLAN_PUSH_SQL = (
    "INSERT INTO reach_outbox (organization_id, member_email, channel, kind, "
    "signal_ids, dedupe_key, body, state, local_date) VALUES "
    f"({_BOUND_ORG}, :m, 'web_push', :k, CAST(:ids AS text[]), :key, :body, "
    "'planned', :d) "
    "ON CONFLICT (organization_id, dedupe_key) DO NOTHING RETURNING id::text"
)

#: The pushes of one kind that count against its cap for one member and one
#: local date: each one sent, and each one that another send step holds now.
#: ``:self`` is the row that the caller holds, so it never counts itself.
_PUSHES_COUNTED_SQL = (
    "SELECT count(*) FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND channel = 'web_push' AND kind = :k AND local_date = :d "
    "AND id <> CAST(:self AS uuid) "
    f"AND (state = 'sent' OR (state = 'planned' AND {_HELD}))"
)

#: The planned push rows that no send step holds, one member after another,
#: the oldest first. Each tick reads it for each org, so the partial index
#: ``reach_outbox_push_planned_idx`` of migration 318 serves it.
_PLANNED_PUSHES_SQL = (
    "SELECT id::text FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND channel = 'web_push' "
    f"AND state = 'planned' AND NOT {_HELD} "
    "ORDER BY member_email, created_at, id LIMIT :n"
)

#: The claim. One statement, so two workers never both hold the row. The age
#: is read on the database clock, the clock of ``created_at``.
_CLAIM_PUSH_SQL = (
    "UPDATE reach_outbox SET reason = 'sending', sent_at = now() "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    f"AND channel = 'web_push' AND state = 'planned' AND NOT {_HELD} "
    "RETURNING id::text, member_email, kind, dedupe_key, local_date, "
    "EXTRACT(EPOCH FROM now() - created_at) AS age_s"
)

#: One member's claims wait for each other until the commit, so the count of
#: the cap sees the claim of a second worker (``pushes_counted``).
_MEMBER_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended("
    "'reach_push:' || current_setting('app.tenant_id', true) || ':' || :m, 0))"
)

_RELEASE_PUSH_SQL = (
    "UPDATE reach_outbox SET reason = :r, sent_at = NULL "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    "AND channel = 'web_push' AND state = 'planned'"
)

_FINISH_PUSH_SQL = (
    "UPDATE reach_outbox SET state = :st, reason = :r, "
    "sent_at = CASE WHEN :st = 'sent' THEN now() ELSE NULL END "
    f"WHERE organization_id = {_BOUND_ORG} AND id = CAST(:id AS uuid) "
    "AND channel = 'web_push' AND state = 'planned'"
)

_BRIEF_SQL = (
    "SELECT EXISTS (SELECT 1 FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND kind = 'brief' AND local_date = :d "
    "AND (dedupe_key = :key OR state = 'sent'))"
)

#: A template brief went out for the date, and its full brief did not yet
#: (RE-2a). The full brief has its own key, so the second EXISTS reads it.
_FULL_DUE_SQL = (
    "SELECT EXISTS (SELECT 1 FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND kind = 'brief' AND local_date = :d "
    "AND ((state = 'sent' AND regime = 'template') OR state = 'failed')) "
    "AND NOT EXISTS (SELECT 1 FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND dedupe_key = :key)"
)


async def logged_pairs(org: str, member: str) -> set[tuple[str, str]]:
    """Every ``(signal_id, fingerprint)`` Reach logged for *member*."""
    async with tenant_session(org) as db:
        rows = (await db.execute(text(_LOGGED_SQL), {"m": member})).all()
    return {(str(r[0]), str(r[1])) for r in rows}


async def has_log(org: str, member: str) -> bool:
    """True when Reach logged any card for *member*. False is the first run."""
    async with tenant_session(org) as db:
        return bool((await db.execute(text(_ANY_LOG_SQL), {"m": member})).scalar())


async def log(org: str, member: str, pairs: Iterable[tuple[str, str]], outcome: str,
              outbox_id: str | None = None) -> int:
    """Log *pairs* with *outcome*. A pair already logged keeps its row."""
    params = [{"m": member, "s": s, "f": f, "o": outcome, "ob": outbox_id}
              for s, f in pairs]
    if not params:
        return 0
    async with tenant_session(org) as db:
        for p in params:
            await db.execute(text(_LOG_SQL), p)
    return len(params)


async def plan(org: str, member: str, *, kind: str, signal_ids: list[str],
               key: str, body: str, local_date: date | None) -> str | None:
    """Insert one planned message. Answers its id, or None when the key
    already exists, so a plan sends once."""
    async with tenant_session(org) as db:
        row = (await db.execute(text(_PLAN_SQL), {
            "m": member, "k": kind, "ids": signal_ids, "key": key, "body": body,
            "d": local_date})).first()
    return None if row is None else str(row[0])


async def finish(org: str, outbox_id: str, *, state: str, regime: str | None = None,
                 reason: str | None = None, provider_message_id: str | None = None) -> bool:
    """Move a planned row to ``sent``, ``skipped`` or ``failed``."""
    async with tenant_session(org) as db:
        result: Any = await db.execute(text(_FINISH_SQL), {
            "st": state, "rg": regime, "r": reason, "pm": provider_message_id,
            "id": outbox_id})
    return bool(getattr(result, "rowcount", 0))


async def interrupts_since(org: str, member: str, since: datetime) -> int:
    """How many WhatsApp interrupts went to *member* since *since* (the
    budget). A web push row never counts."""
    async with tenant_session(org) as db:
        return int((await db.execute(text(_SENT_SINCE_SQL),
                                     {"m": member, "since": since})).scalar() or 0)


async def urgent_since(org: str, member: str, since: datetime) -> int:
    """How many urgent WhatsApp messages went to *member* since *since*."""
    async with tenant_session(org) as db:
        return int((await db.execute(text(_URGENT_SINCE_SQL),
                                     {"m": member, "since": since})).scalar() or 0)


async def brief_planned(org: str, member: str, local_date: date, key: str) -> bool:
    """True when the day's own brief row (*key*) exists in any state, or any
    brief for *local_date* went out. A "Send my brief now" that a closed
    window skipped has its own key, so it does not cancel the held brief of
    the day (RE-2a verification, P3)."""
    async with tenant_session(org) as db:
        return bool((await db.execute(text(_BRIEF_SQL),
                                      {"m": member, "d": local_date, "key": key})).scalar())


async def full_brief_due(org: str, member: str, local_date: date, key: str) -> bool:
    """True when the brief of *local_date* went out as the template and no
    row with *key*, the full brief's key, exists yet (RE-2a)."""
    async with tenant_session(org) as db:
        return bool((await db.execute(text(_FULL_DUE_SQL), {
            "m": member, "d": local_date, "key": key})).scalar())


# ── The web push rows (WS-66 RE-9b) ─────────────────────────────────────────


@dataclass(frozen=True)
class PushRow:
    """One planned web push row that a send step claimed."""

    id: str
    member: str
    kind: str
    key: str
    local_date: date | None
    #: Seconds since the row was planned, on the database clock.
    age_s: float


async def plan_push(db: Any, member: str, *, kind: str, signal_ids: list[str], key: str,
                    body: str, local_date: date | None) -> str | None:
    """Insert one planned web push on *db*, the caller's own session of the
    bound tenant, in the caller's transaction. Answers its id, or None when
    the key already exists, so one step plans one push."""
    row = (await db.execute(text(_PLAN_PUSH_SQL), {
        "m": member, "k": kind, "ids": signal_ids, "key": key, "body": body,
        "d": local_date})).first()
    return None if row is None else str(row[0])


async def pushes_counted(db: Any, member: str, *, kind: str, local_date: date,
                         self_id: str) -> int:
    """How many pushes of *kind* count against its cap for *member* on
    *local_date*: the ones sent, and the ones that another send step holds
    now. *self_id* is the caller's own row. Read on *db*, the caller's
    session."""
    return int((await db.execute(text(_PUSHES_COUNTED_SQL), {
        "m": member, "k": kind, "d": local_date, "self": self_id,
        "lease": PUSH_LEASE_S})).scalar() or 0)


async def planned_pushes(org: str, *, limit: int) -> list[str]:
    """The ids of the planned push rows of *org* that no send step holds,
    one member after another, the oldest first."""
    async with tenant_session(org) as db:
        rows = (await db.execute(text(_PLANNED_PUSHES_SQL),
                                 {"n": limit, "lease": PUSH_LEASE_S})).all()
    return [str(r[0]) for r in rows]


async def claim_push[T](org: str, outbox_id: str,
                        read: Callable[[Any, PushRow], Awaitable[T]]) -> tuple[PushRow, T] | None:
    """Claim one planned push row of *org*, then run *read* on the same
    session, in the same transaction. Answers the row and what *read* gave,
    or None when the row is gone, is finished, or another send step holds it.

    The claim waits for any other claim of the same member to commit, so the
    count of a cap in *read* sees it. A *read* that raises rolls the claim
    back, and the row stays planned."""
    async with tenant_session(org) as db:
        got = (await db.execute(text(_CLAIM_PUSH_SQL),
                                {"id": outbox_id, "lease": PUSH_LEASE_S})).first()
        if got is None:
            return None
        row = PushRow(id=str(got[0]), member=str(got[1]), kind=str(got[2]),
                      key=str(got[3]), local_date=got[4], age_s=float(got[5] or 0))
        await db.execute(text(_MEMBER_LOCK_SQL), {"m": row.member})
        facts = await read(db, row)
    return row, facts


async def release_push(org: str, outbox_id: str, *, reason: str) -> bool:
    """End a claim and keep the row planned, with *reason*. A later tick
    sends it again."""
    async with tenant_session(org) as db:
        result: Any = await db.execute(text(_RELEASE_PUSH_SQL),
                                       {"r": reason, "id": outbox_id})
    return bool(getattr(result, "rowcount", 0))


async def finish_push(org: str, outbox_id: str, *, state: str, reason: str | None = None,
                      then: Callable[[Any], Awaitable[Any]] | None = None) -> bool:
    """Move a planned push row to ``sent``, ``skipped`` or ``failed``. When
    the row moved, run *then* on the same session, in the same transaction:
    the reminder's ledger row of the delivery. Answers True when the row
    moved."""
    async with tenant_session(org) as db:
        result: Any = await db.execute(text(_FINISH_PUSH_SQL),
                                       {"st": state, "r": reason, "id": outbox_id})
        moved = bool(getattr(result, "rowcount", 0))
        if moved and then is not None:
            await then(db)
    return moved
