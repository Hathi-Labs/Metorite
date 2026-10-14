"""The read cache of ``GET /shell/feed`` (WS-58 MD-5a).

Spec: ``project-docs/specs/my_day_feed.md`` §8.1, §8.2 and §8.4. Where §8.4
and an earlier section differ, §8.4 wins.

**What a hit costs.** One Redis round trip: an ``MGET`` of the stored answer
and the two versions. A hit runs no provider, no state join and no Handled
read, and it sends no SQL.

**When an answer is valid.** Until the first of three things (§8.1): a
version moves, the next time edge passes, or 600 seconds pass. While the
answer holds a countdown (§8.4 item 3) it is valid for 60 seconds at most.

**Two versions** (§8.4 item 1). ``key("my_day", "ver")`` is the org's. The
change sink bumps it on each ``pm.*`` event (:func:`on_event`). The member's
is ``key("my_day", "ver", <member>)``. The state POST, the prefs PUT and the
``on_new_mail`` hook of a mailbox the member owns bump it (:func:`bump_member`).
The read takes both versions BEFORE it runs the providers, and stores those
values (item 17). So a write during the compute makes the next read miss.

**The ETag names the body** (item 16): ``W/"<16 hex of SHA-256>"`` of the
JSON with sorted keys. A hit answers 304 only when ``If-None-Match`` holds the
stored tag. A recompute answers 304 only when its own body has that hash.

**What is not stored** (item 18): an answer with a source that ``failed``,
with ``handled_ok`` false, or whose state read failed. Each is served, and the
next poll runs the providers again.

**Fail open** (item 6). A Redis outage serves the answer the providers give,
as before MD-5a. The sink and the bumps log the failure and go on.

**The tenant** (item 20). ``tenant_redis`` binds its own tenant. The read and
the member bumps bind the session's ``organization_id``. The sink binds
``acb_common.db.current_tenant()``, or the payload's ``organization_id`` when
no tenant is bound. The tenant never comes from request input (R5, R11).

**Dark** (item 7). ``enabled`` is the one reader of ``MY_DAY_CACHE``, default
off. With it off, :func:`serve` runs the providers on every read, and the sink
and the bumps send nothing.

Fences: ``tests/unit/test_my_day_cache.py`` (a fake under the real
``TenantRedis``) and ``tests/unit/test_my_day_cache_r8.py`` (a real Postgres:
the statement counts of done-when 2 and 4).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

#: The longest life of an answer, in seconds (§8.1). It bounds a missed event.
MAX_TTL_S = 600
#: The longest life of an answer that holds a countdown (§8.4 item 3).
COUNTDOWN_TTL_S = 60
#: The why keys that count down (§8.4 item 3).
COUNTDOWN_WHY_KEYS = frozenset({"starts_in", "due_soon"})
#: A folded kind that adds a countdown clause to the why line of its card
#: ("Its block starts in 20 min."). Item 3 names only the why keys, and a
#: folded ``block_soon`` counts down too.
COUNTDOWN_FOLDED = frozenset({"block_soon"})
#: Seconds the read waits for Redis. Past it, the read runs the providers.
READ_TIMEOUT_S = 0.25
#: Seconds the store waits for Redis. Past it, the answer is served unstored.
WRITE_TIMEOUT_S = 0.25
#: Seconds the sink and a bump wait for Redis (§8.4 item 6). Projects awaits
#: ``emit`` inline in each write, so the sink must not slow a task edit.
BUMP_TIMEOUT_S = 0.05
#: The block edge (§8.4 item 2): a block start minus this.
BLOCK_LEAD = timedelta(minutes=30)
#: The due edge (§8.4 item 2): a due time minus this.
DUE_LEAD = timedelta(hours=2)
#: The grace of ``no_plan_yet`` after the day start (§8.4 item 2).
PLAN_GRACE = timedelta(minutes=60)
#: The day end edge (§8.4 item 2): the day end minus this.
DAY_END_LEAD = timedelta(minutes=60)
#: The source of the ``pm.*`` events (``routes/projects/core.py`` ``emit``).
PROJECTS_SOURCE = "projects"
#: The Redis namespace of every key here (§8.4 item 4).
NAMESPACE = "my_day"


def enabled() -> bool:
    """The ONE reader of ``MY_DAY_CACHE`` (§8.4 item 7). Default off."""
    from acb_common.settings import get_settings

    return bool(get_settings().my_day_cache)


# ── The keys (§8.4 item 4) ──────────────────────────────────────────────────


def _member(email: Any) -> str:
    return str(email or "").strip().lower()


def features_hash(user: Any) -> str:
    """The hash of each grant that gates a provider (§8.4 item 21): the
    member's ``feature:*`` grants and ``ADMIN_PERMISSION``, sorted. So a
    revoked app, or a revoked admin test, is a new key at the next read."""
    from acb_auth.permissions import FEATURES
    from gateway.routes.shell.intent import ADMIN_PERMISSION

    held = sorted(f"feature:{f}" for f in FEATURES if user.has_permission(f"feature:{f}"))
    if user.has_permission(ADMIN_PERMISSION):
        held.append(ADMIN_PERMISSION)
    return hashlib.sha256(",".join(held).encode("utf-8")).hexdigest()[:16]


def _answer_key(member: str, size: int, fhash: str) -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, "answer", member, str(size), fhash)


def _org_version_key() -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, "ver")


def _member_version_key(member: str) -> Any:
    from acb_common.tenant_redis import key

    return key(NAMESPACE, "ver", member)


def _redis() -> Any:
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


# ── The ETag (§8.4 item 16) ─────────────────────────────────────────────────


def body_json(body: dict[str, Any]) -> str:
    """The body as JSON with sorted keys: what the tag hashes and Redis keeps."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def etag(text: str) -> str:
    """``W/"<16 hex>"``: SHA-256 of the sorted JSON, cut to 16 hex characters."""
    return 'W/"' + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] + '"'


def _opaque(tag: str) -> str:
    tag = tag.strip()
    return tag[2:] if tag[:2] in ("W/", "w/") else tag


def matches(if_none_match: str | None, tag: str) -> bool:
    """Whether ``If-None-Match`` names ``tag``. A list of tags matches when
    one of them does. ``*`` matches nothing here, because the client never
    sends it."""
    if not if_none_match:
        return False
    want = _opaque(tag)
    return any(_opaque(part) == want for part in if_none_match.split(",") if part.strip())


def if_none_match(request: Any) -> str | None:
    """The ``If-None-Match`` of a request, or None. A direct call in a test
    passes no request."""
    if request is None:
        return None
    try:
        return request.headers.get("if-none-match")
    except AttributeError:
        return None


# ── The next time edge (§8.1, §8.4 items 2 and 21) ──────────────────────────


@dataclass(frozen=True)
class Computed:
    """One run of the providers, with what the cache needs to keep it.

    ``edges`` are the instants at which the clock changes the answer (§8.4
    item 2). ``storable`` is False for an answer that item 18 refuses.
    """

    body: dict[str, Any]
    edges: tuple[datetime, ...] = ()
    storable: bool = True


def _instant(value: Any) -> datetime | None:
    from gateway.routes.shell.needs import _instant as instant

    return instant(value)


def signal_edges(signals: Iterable[Any]) -> list[datetime]:
    """Each ``urgent_at`` and the instant 2 hours before it (the urgency term
    and the lift of §6.3 rule 4), each ``expires_at``, and each due time and
    the instant 2 hours before it (§8.4 item 2)."""
    out: list[datetime] = []
    for signal in signals:
        urgent = _instant(getattr(signal, "urgent_at", None))
        if urgent is not None:
            out += [urgent, urgent - DUE_LEAD]
        expires = _instant(getattr(signal, "expires_at", None))
        if expires is not None:
            out.append(expires)
        due = _instant((getattr(signal, "facts", None) or {}).get("due_at"))
        if due is not None:
            # The due time itself too: the needs view moves the task from
            # due today to overdue then.
            out += [due, due - DUE_LEAD]
    return out


def day_edges(day: Any, tz: ZoneInfo, now: datetime) -> list[datetime]:
    """The day start, the day start plus 60 minutes, and the day end minus 60
    minutes, of the member's current and next day (§8.4 item 2). Also the
    next local midnight of the feed's zone, where "today" moves for the
    templates and for the split of due today and overdue."""
    out: list[datetime] = []
    if day is not None:
        first = day.feed_day(now)
        for step in (0, 1):
            start = day.at(first + timedelta(days=step), day.start_hour)
            out += [start, start + PLAN_GRACE,
                    day.at(first + timedelta(days=step), day.end_hour) - DAY_END_LEAD]
    local = now.astimezone(tz)
    out.append(datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=tz))
    return out


def counts_down(body: dict[str, Any]) -> bool:
    """Whether a card of the answer counts down in its why line (§8.4 item 3)."""
    for card in body.get("cards") or ():
        if card.get("why_key") in COUNTDOWN_WHY_KEYS:
            return True
        if COUNTDOWN_FOLDED & set(card.get("folded") or ()):
            return True
    return False


def storable(body: dict[str, Any], *, state_ok: bool = True) -> bool:
    """Item 18: a failed source, ``handled_ok`` false, or a failed state read
    is served and never stored."""
    if not state_ok or body.get("handled_ok") is False:
        return False
    sources = body.get("sources") or {}
    return all(v != "failed" for v in sources.values())


def ttl_seconds(now: datetime, edges: Iterable[datetime], body: dict[str, Any]) -> int:
    """The life of a stored answer: to the next edge after ``now``, 600
    seconds at most, and 60 while the answer counts down. 0 means do not
    store."""
    future = [e for e in edges if e > now]
    left = MAX_TTL_S
    if future:
        left = min(left, (min(future) - now).total_seconds())
    if counts_down(body):
        left = min(left, COUNTDOWN_TTL_S)
    return int(left)


# ── The read (§8.1) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Seen:
    """What the read found before the compute: the stored answer, if any,
    and the two versions as they stood (§8.4 item 17)."""

    stored: dict[str, Any] | None
    org_ver: str
    member_ver: str


def _version(raw: Any) -> str:
    return str(raw) if raw is not None else "0"


async def _look(member: str, size: int, fhash: str) -> _Seen:
    redis = _redis()
    answer_key = _answer_key(member, size, fhash)
    raw, org_ver, member_ver = await asyncio.wait_for(
        redis.mget([answer_key, _org_version_key(), _member_version_key(member)]),
        READ_TIMEOUT_S)
    stored = None
    if raw:
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            stored = None
    return _Seen(stored if isinstance(stored, dict) else None,
                 _version(org_ver), _version(member_ver))


def _valid(seen: _Seen, now: datetime) -> bool:
    stored = seen.stored
    if stored is None:
        return False
    if str(stored.get("org_ver")) != seen.org_ver or str(stored.get("member_ver")) != seen.member_ver:
        return False
    try:
        until = float(stored.get("until"))
    except (TypeError, ValueError):
        return False
    return now.timestamp() < until and isinstance(stored.get("body"), str) \
        and isinstance(stored.get("etag"), str)


async def _store(member: str, size: int, fhash: str, seen: _Seen, text: str, tag: str,
                 now: datetime, ttl: int) -> None:
    value = json.dumps({"body": text, "etag": tag, "org_ver": seen.org_ver,
                        "member_ver": seen.member_ver, "until": now.timestamp() + ttl})
    await asyncio.wait_for(_redis().set(_answer_key(member, size, fhash), value, ex=ttl),
                           WRITE_TIMEOUT_S)


def _not_modified(tag: str) -> Any:
    from fastapi import Response

    return Response(status_code=304, headers={"ETag": tag})


async def serve(user: Any, size: int, compute: Callable[[], Awaitable[Computed]], *,
                if_none_match: str | None = None, response: Any = None) -> Any:
    """The feed's answer for this member, from the cache when it is valid.

    With ``MY_DAY_CACHE`` off this is ``compute()``'s body, as before MD-5a:
    no Redis, no tag. With it on, the answer carries an ``ETag``, and a
    matching ``If-None-Match`` gets a 304 with no body.
    """
    from acb_common.tenant_redis import organization_scope
    from fastapi.encoders import jsonable_encoder
    from gateway.routes.shell import needs

    if not enabled():
        return (await compute()).body

    org = str(getattr(user, "organization_id", None) or "")
    member = _member(getattr(user, "email", None))
    fhash = features_hash(user)
    seen: _Seen | None = None
    if org and member:
        try:
            with organization_scope(org):
                seen = await _look(member, size, fhash)
        except Exception as exc:  # fail open (§8.4 item 6): an outage, or a refused key
            logger.warning("shell.feed.cache read failed", extra={"error": type(exc).__name__})
            seen = None

    now = needs._now()
    if seen is not None and _valid(seen, now):
        stored = seen.stored or {}
        tag = str(stored["etag"])
        if matches(if_none_match, tag):
            return _not_modified(tag)
        if response is not None:
            response.headers["ETag"] = tag
        return json.loads(stored["body"])

    computed = await compute()
    body = jsonable_encoder(computed.body)
    text = body_json(body)
    tag = etag(text)
    if seen is not None and computed.storable:
        ttl = ttl_seconds(now, computed.edges, body)
        if ttl > 0:
            try:
                with organization_scope(org):
                    await _store(member, size, fhash, seen, text, tag, now, ttl)
            except Exception as exc:  # fail open: serve the answer unstored
                logger.warning("shell.feed.cache store failed",
                               extra={"error": type(exc).__name__})
    if matches(if_none_match, tag):
        return _not_modified(tag)
    if response is not None:
        response.headers["ETag"] = tag
    return body


# ── The bumps (§8.2, §8.4 items 1, 19 and 20) ───────────────────────────────


async def _incr(org: str, which: Callable[[], Any], event: str) -> None:
    from acb_common.tenant_redis import organization_scope

    try:
        with organization_scope(org):
            await asyncio.wait_for(_redis().incr(which()), BUMP_TIMEOUT_S)
    except Exception as exc:  # a bump never fails the write that made it
        logger.warning(event, extra={"error": type(exc).__name__})


async def bump_member(organization_id: Any, email: Any) -> None:
    """Bump one member's version: their own state POST, their prefs PUT, and
    the ``on_new_mail`` hook of a mailbox they own (§8.4 items 1 and 19).

    The caller passes the session's org and member, or the owner that the
    mailbox row names. Never a value from a request body (R11)."""
    if not enabled():
        return
    org, member = str(organization_id or ""), _member(email)
    if not org or "@" not in member:
        return
    await _incr(org, lambda: _member_version_key(member), "shell.feed.cache member bump failed")


async def on_event(source: str, event_type: str, payload: dict[str, Any]) -> None:
    """The change sink (§8.2, §8.4 items 1 and 20): a ``pm.*`` event bumps
    the org version with ONE increment. It runs no SQL.

    The org is the bound tenant (``acb_common.db.current_tenant``), or else
    the payload's ``organization_id``. Only ``pm.task.assigned`` carries
    one. An event with neither bumps nothing, and §8.1's 600 seconds bound
    the gap."""
    if source != PROJECTS_SOURCE or not str(event_type or "").startswith("pm."):
        return
    if not enabled():
        return
    from acb_common.db import current_tenant

    org = current_tenant() or (payload or {}).get("organization_id")
    if not org:
        return
    await _incr(str(org), _org_version_key, "shell.feed.cache org bump failed")
