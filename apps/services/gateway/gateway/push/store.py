"""The one writer of ``push_subscriptions`` (WS-66 PN-1, migration 306).

Spec: ``project-docs/specs/calendar_events_reminders.md`` §10.2 items 1, 3
and 4, and ``chat_run_continuity.md`` §S7 item 6.

Each function opens ``tenant_session(org)`` with the org of the session, so
RLS binds every statement (R5). The organization column takes its value
from the bound tenant, never from a parameter. RLS covers the org only, so
every statement also filters on ``member_email``: a member reads, saves and
deletes only their own devices. ``tests/unit/test_push_subscriptions.py``
runs this SQL on a real Postgres (R8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common.db import tenant_session
from sqlalchemy import text

#: The most devices of one member. Each send walks every device, so the cap
#: also bounds that walk. A new device at the cap never gets a refusal: the
#: save evicts first (``_STALE_SQL``, then ``_OLDEST_SQL``), so a member who
#: changed phones often is never locked out.
MAX_DEVICES = 10

#: A device that failed this many sends in a row is stale at the cap.
STALE_FAILURES = 5

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_HELD_SQL = (
    "SELECT count(*) AS n, "
    "count(*) FILTER (WHERE endpoint = :e) AS same FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m"
)

# At the cap, first the member's devices that cannot receive: an expired
# subscription, or one that failed STALE_FAILURES sends in a row.
_STALE_SQL = (
    "DELETE FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m AND endpoint <> :e "
    "AND ((expiration_time IS NOT NULL AND expiration_time < now()) OR failure_count >= :f)"
)

# Then, if the member is still at the cap, the devices that went longest
# without a success. A device that never received counts from its creation.
_OLDEST_SQL = (
    "DELETE FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m AND id IN ("
    "SELECT id FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m AND endpoint <> :e "
    "ORDER BY coalesce(last_success_at, created_at), id LIMIT :n)"
)

_REGISTERED_SQL = (
    "SELECT EXISTS (SELECT 1 FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m AND endpoint = :e)"
)

_UPSERT_SQL = (
    "INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
    "p256dh, auth, expiration_time, user_agent) VALUES "
    f"({_BOUND_ORG}, :m, :e, :p, :a, :x, :ua) "
    "ON CONFLICT (organization_id, member_email, endpoint) DO UPDATE SET "
    "p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, "
    "expiration_time = EXCLUDED.expiration_time, user_agent = EXCLUDED.user_agent, "
    "failure_count = 0, updated_at = now() "
    "RETURNING id::text"
)

_REMOVE_SQL = (
    "DELETE FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m AND endpoint = :e"
)

_DEVICES_SQL = (
    "SELECT id::text, endpoint, p256dh, auth FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "ORDER BY created_at DESC, id LIMIT :n"
)

# The three writes of the sender. Each names the row by id AND endpoint, so a
# row that a new subscribe replaced in between is not touched by mistake.
_SENT_SQL = (
    "UPDATE push_subscriptions SET last_success_at = now(), failure_count = 0 "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND id = CAST(:id AS uuid) AND endpoint = :e"
)

_FAILED_SQL = (
    "UPDATE push_subscriptions SET failure_count = failure_count + 1 "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND id = CAST(:id AS uuid) AND endpoint = :e"
)

_GONE_SQL = (
    "DELETE FROM push_subscriptions "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND id = CAST(:id AS uuid) AND endpoint = :e"
)


@dataclass(frozen=True)
class Subscription:
    """One device, as ``PushSubscription.toJSON()`` gives it."""

    endpoint: str
    p256dh: str
    auth: str
    expiration_time: datetime | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class Device:
    """One stored device of one member, as the sender needs it."""

    id: str
    endpoint: str
    p256dh: str
    auth: str


async def save(org: str, member: str, sub: Subscription) -> str:
    """Save this device for *member*, or refresh its keys. Answers the row id.

    A new device at :data:`MAX_DEVICES` evicts first, in ONE transaction:
    every stale device of the member, then the devices that went longest
    without a success, until the new one fits. A device the member already
    holds just refreshes."""
    params = {"m": member, "e": sub.endpoint, "p": sub.p256dh, "a": sub.auth,
              "x": sub.expiration_time, "ua": sub.user_agent}
    async with tenant_session(org) as db:
        held, same = (await db.execute(text(_HELD_SQL), params)).one()
        if int(held) >= MAX_DEVICES and not int(same):
            stale: Any = await db.execute(
                text(_STALE_SQL), {"m": member, "e": sub.endpoint, "f": STALE_FAILURES})
            left = int(held) - int(getattr(stale, "rowcount", 0) or 0)
            if left >= MAX_DEVICES:
                await db.execute(text(_OLDEST_SQL), {"m": member, "e": sub.endpoint,
                                                     "n": left - MAX_DEVICES + 1})
        row = (await db.execute(text(_UPSERT_SQL), params)).one()
    return str(row[0])


async def registered(org: str, member: str, endpoint: str) -> bool:
    """True when *member* holds a row of *endpoint*. One browser holds one
    subscription for every account of the switcher, so the browser's own
    subscription does not say whether THIS member gets the pushes."""
    async with tenant_session(org) as db:
        return bool((await db.execute(text(_REGISTERED_SQL),
                                      {"m": member, "e": endpoint})).scalar())


async def remove(org: str, member: str, endpoint: str) -> bool:
    """Delete *member*'s row of *endpoint*. True when a row went."""
    async with tenant_session(org) as db:
        result: Any = await db.execute(text(_REMOVE_SQL), {"m": member, "e": endpoint})
    return bool(getattr(result, "rowcount", 0))


async def devices(org: str, member: str) -> list[Device]:
    """Every device of *member*, the newest first, at most the cap."""
    async with tenant_session(org) as db:
        rows = (await db.execute(text(_DEVICES_SQL), {"m": member, "n": MAX_DEVICES})).all()
    return [Device(id=str(r[0]), endpoint=str(r[1]), p256dh=str(r[2]), auth=str(r[3]))
            for r in rows]


async def record(org: str, member: str, outcomes: list[tuple[Device, str]]) -> None:
    """Write what each send did, in ONE session bound to *org*.

    ``sent`` stamps ``last_success_at``, ``gone`` (a 404 or a 410) deletes the
    row, and ``failed`` counts one more failure. ``skipped`` writes nothing."""
    sql = {"sent": _SENT_SQL, "gone": _GONE_SQL, "failed": _FAILED_SQL}
    todo = [(sql[o], d) for d, o in outcomes if o in sql]
    if not todo:
        return
    async with tenant_session(org) as db:
        for statement, device in todo:
            await db.execute(text(statement),
                             {"m": member, "id": device.id, "e": device.endpoint})
