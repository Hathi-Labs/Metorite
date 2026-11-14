"""The items of a connection that need an admin (WS-STU I8b-1a).

Spec: ``project-docs/specs/ai_studio.md`` §8.2 and §14 I8b-1a. Migration: the
file that adds ``integration_attention`` (443 at build). The routes are
``routes/studio/connections.py``. ``tests/unit/test_studio_connection_ask_r8.py``
runs this SQL on a real Postgres.

**One module names the table.** This is the one module that runs SQL on
``integration_attention``. The member purge names the table in its clause
list (``routes/admin/members.py``), and no other Python does. This module
never names the credential column of the registry, and it reads no row of the
registry. Fence: ``test_studio_connection_ask.py``.

**The kinds.** ``ask`` is the ask of one member for one provider that the
member may not connect (I8b). ``broken`` is for I10a, and no function here
writes it.

**The life of an ask.** One row for each org, provider and member, held by the
partial unique index. A second ask within :data:`ASK_DAYS` days keeps the row
and its ``asked_at``. A read ignores an older row, and a new ask makes it live
again. :func:`clear_on_connect` (the hook of ``connections/store.py``),
:func:`withdraw`, the cascade of a package delete and the member purge delete
it. :func:`prune` deletes a row older than :data:`ASK_DAYS` days.

**Tenancy (R5).** Each function except :func:`open_asks` takes a session that
the caller opened with ``tenant_session()``, so RLS binds each statement. Each
statement also names ``organization_id``. A function that takes *org* binds
it, and :func:`prune` binds the tenant of the session. The org and the member
come from the session of the caller, never from request input.

**No free text.** A row holds a provider name, an address, a package id and a
time. An ask holds no note of a member.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.db import tenant_session

__all__ = [
    "ASK_DAYS",
    "KIND_ASK",
    "OPEN_ASKS_LIMIT",
    "OpenAsk",
    "ask",
    "asked_by",
    "clear_on_connect",
    "open_asks",
    "prune",
    "prune_hook",
    "register_prune",
    "withdraw",
]

_log = get_logger("connections.attention")

#: The kind of an ask of a member (I8b).
KIND_ASK = "ask"
#: The days that an ask stays live. An older ask reads as not asked, a new
#: ask makes it live again, and the prune deletes it.
ASK_DAYS = 30
#: The most rows that one read of the open asks of an org returns.
OPEN_ASKS_LIMIT = 200

_LIVE = f"asked_at > now() - interval '{ASK_DAYS} days'"
_EXPIRED = f"asked_at <= now() - interval '{ASK_DAYS} days'"
#: The tenant that the session binds. NULL when no tenant is bound, and then a
#: statement matches no row.
_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: One ask. A fresh duplicate changes nothing and returns no row. An expired
#: row takes the new time and package, and returns its id. The conflict target
#: names the partial unique index by its columns and its predicate.
_ASK_SQL = (
    "INSERT INTO integration_attention "
    "(organization_id, kind, provider, member_email, package_id) "
    f"VALUES (CAST(:org AS uuid), '{KIND_ASK}', :provider, :member, CAST(:pid AS uuid)) "
    f"ON CONFLICT (organization_id, provider, member_email) WHERE kind = '{KIND_ASK}' "
    "DO UPDATE SET asked_at = now(), package_id = EXCLUDED.package_id "
    f"WHERE integration_attention.{_EXPIRED} "
    "RETURNING id"
)

_WITHDRAW_SQL = (
    "DELETE FROM integration_attention "
    f"WHERE organization_id = CAST(:org AS uuid) AND kind = '{KIND_ASK}' "
    "AND provider = :provider AND member_email = :member"
)

_ASKED_BY_SQL = (
    "SELECT provider FROM integration_attention "
    f"WHERE organization_id = CAST(:org AS uuid) AND kind = '{KIND_ASK}' "
    f"AND member_email = :member AND provider = ANY(CAST(:providers AS text[])) AND {_LIVE}"
)

_OPEN_SQL = (
    "SELECT provider, member_email, package_id::text AS package_id, asked_at "
    "FROM integration_attention "
    f"WHERE organization_id = CAST(:org AS uuid) AND kind = '{KIND_ASK}' AND {_LIVE} "
    "ORDER BY asked_at DESC, provider, member_email LIMIT :limit"
)

_CLEAR_SQL = (
    "DELETE FROM integration_attention "
    f"WHERE organization_id = CAST(:org AS uuid) AND kind = '{KIND_ASK}' "
    "AND provider = :provider"
)

_PRUNE_SQL = (
    "DELETE FROM integration_attention "
    f"WHERE organization_id = {_BOUND_ORG} AND kind = '{KIND_ASK}' AND {_EXPIRED}"
)


@dataclass(frozen=True)
class OpenAsk:
    """One live ask of a member, for the Needs-you item of the admins (I8b-1b)."""

    provider: str
    member_email: str
    package_id: str
    asked_at: datetime


def _org(org: str) -> str:
    return str(uuid.UUID(str(org)))


def _member(member: str) -> str:
    value = (member or "").strip().lower()
    if not value:
        raise ValueError("an ask names a member")
    return value


async def ask(db: Any, org: str, member: str, provider: str, package_id: str) -> bool:
    """Record the ask of *member* for *provider*, from the draft *package_id*.

    True when the ask is new: no live row existed, so the admins see a new
    item. False when a live ask of the member exists: that row and its time
    stay as they are. An expired row takes the new time and package."""
    row = (await db.execute(text(_ASK_SQL), {
        "org": _org(org), "provider": provider, "member": _member(member),
        "pid": str(uuid.UUID(str(package_id))),
    })).first()
    return row is not None


async def withdraw(db: Any, org: str, member: str, provider: str) -> bool:
    """Delete the ask of *member* for *provider*. The row of another member
    stays. True when a row went."""
    result = await db.execute(text(_WITHDRAW_SQL), {
        "org": _org(org), "provider": provider, "member": _member(member)})
    return bool(result.rowcount)


async def asked_by(db: Any, org: str, member: str, providers: Iterable[str]) -> frozenset[str]:
    """The providers of *providers* that *member* asked for, and whose ask is
    live. An empty *providers* sends no statement."""
    wanted = sorted({str(p) for p in providers})
    if not wanted:
        return frozenset()
    rows = (await db.execute(text(_ASKED_BY_SQL), {
        "org": _org(org), "member": _member(member), "providers": wanted})).all()
    return frozenset(str(r[0]) for r in rows)


async def open_asks(org: str, *, limit: int = OPEN_ASKS_LIMIT) -> list[OpenAsk]:
    """The live asks of *org*, the newest first, at most *limit*.

    It opens its own ``tenant_session(org)``, as ``reminders.my_open_cards``
    does, so the needs provider of I8b-1b holds no session of its own. *org*
    is the org of the session of the caller."""
    org = _org(org)
    bound = max(1, min(int(limit), OPEN_ASKS_LIMIT))
    async with tenant_session(org) as db:
        rows = (await db.execute(text(_OPEN_SQL), {"org": org, "limit": bound})).all()
    return [OpenAsk(provider=str(r.provider), member_email=str(r.member_email),
                    package_id=str(r.package_id), asked_at=r.asked_at) for r in rows]


async def clear_on_connect(db: Any, org: str, provider: str) -> int:
    """Delete each ask of *provider* in *org*: an admin connected it. The
    count of the rows that went. Only ``connections/store._clear_asks`` calls
    it, for a ``connected`` org row."""
    result = await db.execute(text(_CLEAR_SQL), {"org": _org(org), "provider": provider})
    return int(result.rowcount or 0)


async def prune(db: Any) -> int:
    """Delete each ask of the bound org older than :data:`ASK_DAYS` days. The
    count of the rows that went. No bound tenant deletes nothing."""
    result = await db.execute(text(_PRUNE_SQL))
    return int(result.rowcount or 0)


async def prune_hook(db: Any, today: date) -> None:
    """The ``worker.prune_hooks`` hook. It never raises: a hook that raises
    fails the whole prune of the org. A savepoint keeps the transaction of the
    prune usable after a failure, and the log names the class only."""
    try:
        async with db.begin_nested():
            deleted = await prune(db)
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("connections.attention.prune_failed", error=type(exc).__name__)
        return
    if deleted:
        _log.info("connections.attention.pruned", deleted=deleted)


def register_prune() -> None:
    """Add :func:`prune_hook` to the prune of the job worker, once."""
    from gateway.jobs import worker

    if prune_hook not in worker.prune_hooks:
        worker.prune_hooks.append(prune_hook)


register_prune()
