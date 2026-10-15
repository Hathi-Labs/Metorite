"""The one writer of ``reach_outbox`` and ``reach_signal_log`` (WS-59 RE-1a).

Spec: ``project-docs/specs/proactive_reach.md`` §6 and §7. Migration
``266_reach_outbox.sql``.

Each function opens ``tenant_session(org)`` with the org the caller resolved,
so RLS binds every statement (R5). The organization column takes its value
from the bound tenant, never from a parameter. ``tests/unit/test_reach_r8.py``
runs this SQL on a real Postgres.
"""

from __future__ import annotations

from collections.abc import Iterable
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

_SENT_SINCE_SQL = (
    "SELECT count(*) FROM reach_outbox "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND kind = 'interrupt' AND state = 'sent' AND created_at >= :since"
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
    """How many interrupts went to *member* since *since* (the budget)."""
    async with tenant_session(org) as db:
        return int((await db.execute(text(_SENT_SINCE_SQL),
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
