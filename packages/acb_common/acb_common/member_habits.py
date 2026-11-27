"""Habits: the repeated acts of a member, read from the act record (WS-PZ MM-2).

Spec: ``project-docs/specs/personalization.md`` §19.5 and §19.10. Task 2879.
Source: ``member_action`` (``acb_common.member_actions``). R8 twin:
``tests/unit/test_member_habits_r8.py``.

**A habit** is a repeated act on one kind of item. Example: mail from
``ge.com`` with the label ``Purchase order``: the member forwards it to Veena
and Kiran, 6 of 7 times. :func:`member_habits` is the ONE reader of habits
(§19.5, and the fence of §12.6 names it). An app asks it for the habit of an
item and never groups the record itself.

**The rule** (D-PZ-52). Look at the acts of the member in the last
:data:`WINDOW_DAYS` days, on items that carry the same features as *item*.
One kind of act, with the same targets, is a habit when it has
:data:`MIN_ACTS` acts or more and a share of :data:`MIN_SHARE` or more of those
acts. Otherwise there is no habit.

**Only a choice counts.** The read takes ``via`` ``app`` and ``card`` only. A
``rule`` act is not the member's choice and never makes a habit (§19.10).

**Never a profile fact** (D-PZ-2, §12.4). A habit is a count of the member's
own acts, computed on read and cached for :data:`CACHE_SECONDS`. Nothing here
writes the profile, and no function of this module takes a share or a score of
a person (D-192): the share is of acts on one kind of item.

**Gates.** No habit when ``MEMBER_ACTIONS_ORGS`` does not hold the org, or
when the member turned ``learning`` off. Each gate is checked on every call,
before the cache, so a switch takes effect at once.

**A purge clears the cache** (:func:`forget_member`, task 3639).

**Tenancy (R5).** The caller passes a session bound with
``tenant_session(org)``, and the org of the cache key is the same one. The
member comes from the session of the caller. The cache goes through the tenant
Redis wrapper (``acb_common.tenant_redis``), and a Redis failure only skips the
cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text

from acb_common import member_actions
from acb_common._log import get_logger

_log = get_logger("acb_common.member_habits")

#: The look-back, in days (D-PZ-52).
WINDOW_DAYS = 90
#: The fewest acts of a habit (D-PZ-52).
MIN_ACTS = 3
#: The least share of the acts on this kind of item (D-PZ-52).
MIN_SHARE = 0.7
#: The cache time for one member and one kind of item, in seconds (§19.5).
CACHE_SECONDS = 3600
#: The most rows that one read looks at.
MAX_ROWS = 500

#: The features that say "the same kind of item". A key outside this list is
#: ignored, so an app cannot widen the match with a free key.
KIND_KEYS: tuple[str, ...] = (
    "sender_domain", "sender_kind", "triage_label", "is_reply", "new_thread",
    "card_kind", "signal_kind",
)

#: The acts of the member that a learner reads: a choice, never a rule.
CHOICE_VIAS: tuple[str, ...] = tuple(sorted(member_actions.CHOICE_VIAS))

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: The read. The org is named beside RLS, and ``via`` is the two values that
#: are a choice. ``features @> :match`` keeps the acts on the same kind of item.
READ_SQL = (
    "SELECT act, targets FROM member_action "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m AND app = :app "
    "AND via = ANY(CAST(:vias AS text[])) "
    "AND at > now() - make_interval(days => :days) "
    "AND features @> CAST(:match AS jsonb) "
    "AND (CAST(:mb AS text) IS NULL OR mailbox_id = :mb) "
    "ORDER BY at DESC LIMIT :cap"
)


@dataclass(frozen=True)
class Habit:
    """One habit: the act, the addresses it sent the item to, and the counts."""

    app: str
    act: str
    targets: tuple[str, ...]
    count: int
    total: int

    @property
    def share(self) -> float:
        return self.count / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "targets": list(self.targets)}


def kind_of(item: dict[str, Any]) -> dict[str, Any]:
    """The features of *item* that name its kind: the :data:`KIND_KEYS` that it
    holds, with a scalar value. Pure."""
    feats = item.get("features") or {}
    return {k: feats[k] for k in KIND_KEYS
            if k in feats and isinstance(feats[k], (str, bool, int)) and feats[k] != ""}


def find_habit(app: str, rows: list[tuple[str, list[str] | None]]) -> Habit | None:
    """The habit in *rows*, each ``(act, targets)`` of one kind of item, or
    None. Pure: the rule of D-PZ-52, with no database and no clock."""
    total = len(rows)
    if total < MIN_ACTS:
        return None
    groups: dict[tuple[str, tuple[str, ...]], int] = {}
    for act, targets in rows:
        key = (act, tuple(sorted(targets or ())))
        groups[key] = groups.get(key, 0) + 1
    (act, targets), count = max(groups.items(), key=lambda kv: (kv[1], kv[0]))
    if count < MIN_ACTS or count / total < MIN_SHARE:
        return None
    return Habit(app=app, act=act, targets=targets, count=count, total=total)


def _digest(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(list(parts), sort_keys=True).encode()).hexdigest()[:40]


def _member_part(member: str) -> str:
    """The member's segment of the cache key: a digest, never the address."""
    return _digest("member", member)


def _cache_key(member: str, app: str, mailbox: str | None, kind: dict[str, Any]) -> Any:
    """``cc:<org>:member_habits:<member digest>:<item digest>``. The member
    digest is its own segment, so a purge clears one member's keys with one
    pattern (:func:`forget_member`)."""
    from acb_common import tenant_redis

    return tenant_redis.key("member_habits", _member_part(member),
                            _digest(app, mailbox, kind))


async def forget_member(org: str, member: str) -> int:
    """Delete each cached habit of *member* in *org* at once (task 3639). The
    member purge calls it: a cached habit holds the addresses of the member's
    acts (``targets``), so it must not outlive the purge for up to an hour.
    Best effort: a failure of Redis logs the class and answers what it
    removed. The org comes from the purge, never from request input (R5)."""
    who = str(member or "").strip().lower()
    if not who or not org:
        return 0
    removed = 0
    try:
        from acb_common import tenant_redis

        with tenant_redis.organization_scope(org):
            redis = tenant_redis.get_tenant_redis()
            head = tenant_redis.key("member_habits", _member_part(who)).value + ":"
            async for raw in redis.scan_iter(tenant_redis.match("member_habits", _member_part(who))):
                name = raw.decode() if isinstance(raw, bytes) else str(raw)
                rest = name[len(head):] if name.startswith(head) else ""
                if rest:
                    removed += int(await redis.delete(
                        tenant_redis.key("member_habits", _member_part(who), rest)) or 0)
    except Exception as exc:  # the class only
        _log.warning("member_habits.forget_failed", error=type(exc).__name__)
    return removed


async def _cache_get(org: str, k_args: tuple[Any, ...]) -> tuple[bool, Habit | None]:
    """(hit, habit). A failure of Redis is a miss."""
    try:
        from acb_common import tenant_redis

        with tenant_redis.organization_scope(org):
            raw = await tenant_redis.get_tenant_redis().get(_cache_key(*k_args))
        if raw is None:
            return False, None
        data = json.loads(raw)
        if data is None:
            return True, None
        return True, Habit(app=data["app"], act=data["act"], targets=tuple(data["targets"]),
                           count=int(data["count"]), total=int(data["total"]))
    except Exception as exc:  # the class only
        _log.warning("member_habits.cache_read_failed", error=type(exc).__name__)
        return False, None


async def _cache_put(org: str, k_args: tuple[Any, ...], habit: Habit | None) -> None:
    try:
        from acb_common import tenant_redis

        with tenant_redis.organization_scope(org):
            await tenant_redis.get_tenant_redis().setex(
                _cache_key(*k_args), CACHE_SECONDS,
                json.dumps(None if habit is None else habit.as_dict()))
    except Exception as exc:  # the class only
        _log.warning("member_habits.cache_write_failed", error=type(exc).__name__)


async def member_habits(db: Any, *, org: str, member: str,
                        item: dict[str, Any]) -> Habit | None:
    """The habit of *member* for *item*, or None.

    *db* is a session bound to *org* by ``tenant_session``. *item* is
    ``{"app": "email", "mailbox_id": "...", "features": {"sender_domain":
    "ge.com", "triage_label": "Purchase order"}}``. The ``mailbox_id`` is
    optional: the learner reads one mailbox at a time (D-EM-18). An item with
    no feature of :data:`KIND_KEYS` has no kind, so it has no habit.

    It never raises: a failure answers None and logs the class only.
    """
    try:
        if not member_actions.enabled_for(org):
            return None
        app = item.get("app")
        if app not in member_actions.ACTS:
            return None
        kind = kind_of(item)
        who = str(member or "").strip().lower()
        if not kind or "@" not in who:
            return None
        mailbox = item.get("mailbox_id")
        mailbox = str(mailbox) if mailbox else None
        if not await member_actions.learning_on(db, org, who):
            return None
        k_args = (who, app, mailbox, kind)
        hit, cached = await _cache_get(org, k_args)
        if hit:
            return cached
        rows = (await db.execute(text(READ_SQL), {
            "m": who, "app": app, "vias": list(CHOICE_VIAS), "days": WINDOW_DAYS,
            "match": json.dumps(kind, sort_keys=True), "mb": mailbox, "cap": MAX_ROWS,
        })).all()
        habit = find_habit(app, [(r[0], list(r[1] or [])) for r in rows])
        await _cache_put(org, k_args, habit)
        return habit
    except Exception as exc:  # the class only: a DB message can echo a value
        _log.warning("member_habits.read_failed", error=type(exc).__name__)
        return None
