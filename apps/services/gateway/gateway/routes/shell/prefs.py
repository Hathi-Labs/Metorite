"""GET and PUT /shell/feed/prefs — My Day's feed preferences (WS-58 MD-2d).

Spec: ``project-docs/specs/my_day_feed.md`` §13.3 and §16 MD-2d. The column
is ``user_settings.my_day_prefs`` (migration 259). It holds three keys, and
any other key is a 422:

=================  ==========================================================
Key                Value
=================  ==========================================================
``mutes``          A list of feed kinds, 20 at most (§6.6). MD-9 reads it
                   first. A mute changes the feed only, never the bell.
``open_in_app``    A list of feed kinds, 20 at most (§11.2 rule 4). MD-8
                   reads it.
``corrections``    The member's lines for ``decide``, newest first: 10 at
                   most, 300 characters each, 1,500 in all (§10.5). MD-6b
                   reads it.
``rule_mutes``     A list of exception rule ids, one row of
                   ``reach.exceptions.RULES`` each (BR-5, ``core_brief.md``
                   §6). A muted rule matches nothing. Reach reads it through
                   :func:`muted_rules`. A mute never changes the feed.
``block_notify``   A boolean (BR-5 slice 2b-2, ``core_brief.md`` §9 Q8 "One
                   setting"). True marks every ``block_soon`` card with
                   ``notify_me``, so exception rule 3 may fire. Reach reads
                   it through :func:`block_notify`. It never changes the feed.
=================  ==========================================================

A kind is a row of ``feed_rank.BASE``, the one table of the feed's kinds.
Each kind that a provider of ``needs.REGISTRY`` gives is in it, and so is
each MD-3 kind whose provider is dark or not built yet (WS-58 MD-3c,
``my_day_feed.md`` §5.9). The write is strict and the read is lenient, as
``routes/admin/me.py`` does for the shell layout: a kind that leaves the
table drops from the read.

**One key at a time.** The body is ``{key, value}``. The server merges that
one key into the column with ``jsonb_set``, and never replaces the column.
So a save of ``mutes`` keeps ``corrections``, and a layout save through
``PUT /auth/me/shell`` keeps both, because that write changes
``shell_prefs`` only.

**A member with no settings row.** The PUT makes the row, and the new row
carries the calendar seed (``_seed_from_work_schedule``), exactly as
``me.py`` ``_write_shell`` does. ``/tasks/settings`` reads "no row" as
"seed from the People schedule" (D-PC-16), so the calendar opens on the
same hours after the first preference save.

The member comes from the session and the tenant from the bound session,
never from the body (R5, R11). The routes need no feature, as
``/auth/me/shell`` needs none. Fences: ``tests/unit/test_my_day_prefs.py``
and ``tests/unit/test_my_day_prefs_r8.py``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Body, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.shell import cache, feed_rank
from gateway.routes.shell import feed as _feed  # noqa: F401  (registers the feed kinds)
from gateway.routes.shell.search import router
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

logger = logging.getLogger(__name__)

#: The keys of ``my_day_prefs``: three of §16 MD-2d, and ``hide_push_details``
#: (WS-66 RE-9a), a boolean.
PREF_KEYS: tuple[str, ...] = ("mutes", "open_in_app", "corrections", "hide_push_details",
                              "push_prompt", "rule_mutes", "block_notify")
#: The longest list of kinds that ``mutes`` and ``open_in_app`` take.
MAX_KINDS = 30
#: The most lines that ``corrections`` takes.
MAX_CORRECTIONS = 10
#: The longest correction line.
CORRECTION_MAX = 300
#: The most characters of all the corrections together (§10.5).
CORRECTIONS_CLIP = 1_500


def feed_kinds() -> frozenset[str]:
    """Every kind of the feed: the rows of ``feed_rank.BASE`` (§4.4, §5.9).
    A registered provider gives only these kinds, so a member may mute a
    kind before its provider is listed in ``MY_DAY_PROVIDERS``. 20 kinds
    after MD-3, 21 after WS-53 CRM-Q28 added ``digest_ready``, 22 after
    WS-53 CRM-Q32-4 added ``crm_open_questions``, 23 after WS-61 KB-9a
    added ``kb_gap``, 25 after WS-61 KB-9b added ``doc_needs_review``
    and ``doc_shared_with_me``, 26 after KB-9b slice 2 added
    ``source_sync_failed``, and 29 after WS-66 RM-2 added the three
    reminder kinds, and 30 after WS-ALN ALN-2b added ``decision``, which is
    ``MAX_KINDS``
    (§5.9 item 10.6: a ticket that adds a kind raises the cap)."""
    return frozenset(feed_rank.BASE)


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


def _kinds(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("the value is a list of kinds")
    if len(value) > MAX_KINDS:
        raise ValueError(f"at most {MAX_KINDS} kinds")
    kinds = feed_kinds()
    unknown = sorted({v for v in value if v not in kinds})
    if unknown:
        raise ValueError(f"unknown kind: {', '.join(unknown)[:200]}")
    return _unique(value)


def _corrections(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("the value is a list of lines")
    if len(value) > MAX_CORRECTIONS:
        raise ValueError(f"at most {MAX_CORRECTIONS} lines")
    lines = [v.strip() for v in value]
    for line in lines:
        if not line:
            raise ValueError("a line is empty")
        if len(line) > CORRECTION_MAX:
            raise ValueError(f"a line is longer than {CORRECTION_MAX} characters")
        # A line goes into a `decide` prompt. Postgres stores no NUL, and
        # jsonb refuses a lone surrogate (a JS `slice()` through an emoji).
        if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in line):
            raise ValueError("a line holds a control character or a lone surrogate")
    if sum(len(line) for line in lines) > CORRECTIONS_CLIP:
        raise ValueError(f"the lines hold more than {CORRECTIONS_CLIP} characters")
    return lines


def _flag(value: Any) -> bool:
    # A strict bool: `1`, "true" and null are refused, so the stored value
    # is never a string that reads as true by accident (WS-66 RE-9a).
    if not isinstance(value, bool):
        raise ValueError("the value is true or false")
    return value


#: The time fields of ``push_prompt`` (OB-8, ``calendar_events_reminders.md``
#: section 10.4 item 5).
PUSH_PROMPT_TIMES = ("snoozed_until", "last_shown_at", "denied_seen_at")
#: How far ahead ``snoozed_until`` may reach.
PUSH_PROMPT_MAX_SNOOZE = timedelta(days=366)


def _prompt_time(value: Any) -> datetime | None:
    """A time with an offset, or None when the value is not one."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None and when.utcoffset() is not None else None


def _push_prompt(value: Any) -> dict[str, Any]:
    """The state of the chat's push sheet: an object. An unknown field
    drops. A time with no offset is refused, and so is a ``snoozed_until``
    more than 366 days ahead. ``never`` is a strict boolean."""
    if not isinstance(value, dict):
        raise ValueError("the value is an object")
    out: dict[str, Any] = {}
    for key in PUSH_PROMPT_TIMES:
        if key not in value or value[key] is None:
            continue
        when = _prompt_time(value[key])
        if when is None:
            raise ValueError(f"{key} is a time with an offset")
        if key == "snoozed_until" and when > datetime.now(UTC) + PUSH_PROMPT_MAX_SNOOZE:
            raise ValueError("snoozed_until is more than 366 days ahead")
        out[key] = value[key].strip()
    if "never" in value:
        out["never"] = _flag(value["never"])
    return out


def rule_ids() -> frozenset[str]:
    """The ids of the exception rules: the rows of ``exceptions.RULES``."""
    from gateway.reach import exceptions

    return frozenset(r.id for r in exceptions.RULES)


def _rule_mutes(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("the value is a list of rule ids")
    ids = rule_ids()
    if len(value) > len(ids):
        raise ValueError(f"at most {len(ids)} rules")
    unknown = sorted({v for v in value if v not in ids})
    if unknown:
        raise ValueError(f"unknown rule: {', '.join(unknown)[:200]}")
    return _unique(value)


_CLEAN = {"mutes": _kinds, "open_in_app": _kinds, "corrections": _corrections,
          "hide_push_details": _flag, "push_prompt": _push_prompt,
          "rule_mutes": _rule_mutes, "block_notify": _flag}


class PrefPut(BaseModel):
    """What ``PUT /shell/feed/prefs`` accepts: one key and its whole value."""

    model_config = ConfigDict(extra="forbid")

    key: Literal["mutes", "open_in_app", "corrections", "hide_push_details", "push_prompt",
                 "rule_mutes", "block_notify"]
    value: Any

    @model_validator(mode="after")
    def _value(self) -> PrefPut:
        self.value = _CLEAN[self.key](self.value)
        return self


def stored_prefs(raw: Any) -> dict[str, Any]:
    """A stored value, as the routes answer it. A key that is absent stays
    absent. Lenient on purpose: a kind that left the registry, or a value of
    the wrong shape, drops from the answer rather than fail the read."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    kinds = feed_kinds()
    for key in ("mutes", "open_in_app"):
        v = raw.get(key)
        if isinstance(v, list):
            out[key] = _unique([x for x in v if isinstance(x, str) and x in kinds])[:MAX_KINDS]
    v = raw.get("corrections")
    if isinstance(v, list):
        out["corrections"] = [x for x in v if isinstance(x, str)][:MAX_CORRECTIONS]
    v = raw.get("rule_mutes")
    if isinstance(v, list):
        ids = rule_ids()
        out["rule_mutes"] = _unique([x for x in v if isinstance(x, str) and x in ids])
    for key in ("hide_push_details", "block_notify"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    if isinstance(raw.get("push_prompt"), dict):
        # A stored object that fails the check drops from the answer, as a
        # stale kind does. The member sees the sheet again.
        with contextlib.suppress(ValueError):
            out["push_prompt"] = _push_prompt(raw["push_prompt"])
    return out


def _member(user: UserContext) -> str:
    """The session's address, the key that ``/tasks/settings`` writes under."""
    return getattr(user, "email", None) or ""


#: The row of this member in the BOUND tenant. The tenant predicate is beside
#: RLS, not instead of it, as in ``me.py``.
_READ_SQL = (
    "SELECT my_day_prefs FROM user_settings "
    "WHERE organization_id = {org} AND user_id = :uid"
)

#: A new row holds the one key. ``CAST`` and not ``::``, because SQLAlchemy
#: misreads a bind name next to ``::`` (``test_sql_bindparam_jsonb_cast.py``).
_NEW_PREFS = "jsonb_build_object(CAST(:pkey AS text), CAST(:pval AS jsonb))"

#: An existing row merges the one key, and keeps every other key. A stored
#: value that is not an object (NULL, or a stray scalar) counts as empty.
_MERGE_PREFS = (
    "jsonb_set(CASE WHEN jsonb_typeof(user_settings.my_day_prefs) = 'object' "
    "THEN user_settings.my_day_prefs ELSE CAST('{}' AS jsonb) END, "
    "ARRAY[CAST(:pkey AS text)], CAST(:pval AS jsonb), true)"
)


#: The hide switch over EVERY spelling of the member's address, in the BOUND
#: tenant (WS-66 RE-9b, repair round 1). A row keys on the spelling of the
#: session that wrote it (``Ravi.K@acme.com``), and the reminder loop knows
#: only the lower case that ``reminders.member_email`` holds. One JSON
#: ``true`` under any spelling hides, so the read fails closed. ⚠️ Do not read
#: ONE row: a lower-case row with no key would outvote the ``true`` and put
#: the title on the lock screen. ``->`` gives NULL on a value that is not an
#: object, and ``bool_or`` skips a NULL. The shape is EA-6i's
#: ``routes/admin/agent_access_switch._READ_SQL``.
_HIDE_ANY_CASE_SQL = (
    "SELECT COALESCE(bool_or("
    "my_day_prefs -> 'hide_push_details' = CAST('true' AS jsonb)), false) "
    "FROM user_settings "
    "WHERE organization_id = {org} AND lower(user_id) = lower(:uid)"
)


async def hide_push_details(db: Any, member: str) -> bool:
    """True when *member* turned on "Hide details on the lock screen"
    (``hide_push_details``, RE-9a) under ANY spelling of the address. Read on
    *db*, a session that the caller bound to the tenant, for the send step of
    a reminder push (RE-9b). No row, or no ``true``, is False."""
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    return bool((await db.execute(text(_HIDE_ANY_CASE_SQL.format(org=_BOUND_ORG)),
                                  {"uid": member})).scalar())


#: The rule ids that the member muted, over EVERY spelling of the address in
#: the BOUND tenant (BR-5). The union is right: a mute under any spelling
#: holds. ``jsonb_typeof`` guards the expansion, because
#: ``jsonb_array_elements_text`` raises on a value that is not an array, and
#: SQL does not promise the order of the guard and the call, so the CASE
#: keeps the call inside the guard.
_MUTED_ANY_CASE_SQL = (
    "SELECT DISTINCT rule_id FROM user_settings, "
    "LATERAL jsonb_array_elements_text("
    "CASE WHEN jsonb_typeof(my_day_prefs -> 'rule_mutes') = 'array' "
    "THEN my_day_prefs -> 'rule_mutes' ELSE CAST('[]' AS jsonb) END) AS rule_id "
    "WHERE organization_id = {org} AND lower(user_id) = lower(:uid)"
)


async def muted_rules(db: Any, member: str) -> frozenset[str]:
    """The exception rules that *member* muted (``rule_mutes``), under ANY
    spelling of the address. Read on *db*, a session that the caller bound to
    the tenant. An id that is not a rule now drops. No row is the empty set.
    A read error raises: the caller logs it and counts no mutes."""
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    rows = (await db.execute(text(_MUTED_ANY_CASE_SQL.format(org=_BOUND_ORG)),
                             {"uid": member})).all()
    return frozenset(str(r[0]) for r in rows) & rule_ids()


#: The ``block_notify`` switch over EVERY spelling of the address in the
#: BOUND tenant (BR-5 slice 2b-2). It FAILS CLOSED, unlike
#: ``_HIDE_ANY_CASE_SQL``: ``true`` here SENDS an interrupt, so a union of
#: ``true`` would keep the switch on after the member turned it off under
#: another spelling. ``bool_and`` runs over the rows that hold the key only.
#: The switch is on when each such row holds JSON ``true``. One row with any
#: other value turns it off. No row with the key is off.
_BLOCK_NOTIFY_ANY_CASE_SQL = (
    "SELECT COALESCE(bool_and("
    "my_day_prefs -> 'block_notify' = CAST('true' AS jsonb)) "
    "FILTER (WHERE my_day_prefs -> 'block_notify' IS NOT NULL), false) "
    "FROM user_settings "
    "WHERE organization_id = {org} AND lower(user_id) = lower(:uid)"
)


async def block_notify(db: Any, member: str) -> bool:
    """True when *member* asked to be told before each block starts
    (``block_notify``). Each spelling of the address that holds the key must
    hold ``true``, so one ``false`` under any spelling is off. Read on *db*, a
    session that the caller bound to the tenant. A read error raises: the
    caller logs it and counts the setting as off."""
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    return bool((await db.execute(text(_BLOCK_NOTIFY_ANY_CASE_SQL.format(org=_BOUND_ORG)),
                                  {"uid": member})).scalar())


async def _read(db: Any, uid: str) -> Any:
    from gateway.routes.tasks.settings import _BOUND_ORG
    from sqlalchemy import text

    return (await db.execute(text(_READ_SQL.format(org=_BOUND_ORG)), {"uid": uid})).first()


async def _write(db: Any, uid: str, key: str, value: Any) -> Any:
    """Merge one key, and return the stored column after the write."""
    from gateway.routes.tasks.settings import _seed_from_work_schedule, upsert_settings_sql
    from sqlalchemy import text

    params: dict[str, Any] = {"uid": uid, "pkey": key, "pval": json.dumps(value)}
    seed: dict[str, Any] = {}
    if await _read(db, uid) is None:
        seed = await _seed_from_work_schedule(db, uid)
    params.update(seed)
    cols = ["my_day_prefs", *seed]

    def _ph(k: str) -> str:
        return _NEW_PREFS if k == "my_day_prefs" else f":{k}"

    sql = upsert_settings_sql(cols, _ph, update=["my_day_prefs"],
                              merge={"my_day_prefs": _MERGE_PREFS})
    row = (await db.execute(text(f"{sql} RETURNING my_day_prefs"), params)).first()
    return row[0] if row is not None else None


@router.get("/feed/prefs", summary="My Day's feed preferences (MD-2d)")
async def get_feed_prefs(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    """The stored preferences. A key the member never set is absent.

    A failed read answers 503, never an empty object, so a database fault
    never reads as "no mutes".
    """
    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        async with _tenant_session() as db:
            row = await _read(db, uid)
    except Exception as exc:
        logger.warning("shell.prefs.read_failed", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return stored_prefs(row[0]) if row is not None else {}


@router.put("/feed/prefs", summary="Save one of My Day's feed preferences (MD-2d)")
async def put_feed_prefs(
    body: Any = Body(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save one key. The other keys of the column do not change."""
    uid = _member(user)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        put = PrefPut.model_validate(body)
    except ValidationError as exc:
        # No input in the detail: it would echo the member's own text back,
        # and a lone surrogate in it cannot be encoded, so the 422 became a 500.
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False,
                              include_input=False),
        ) from exc
    try:
        async with _tenant_session() as db:
            stored = await _write(db, uid, put.key, put.value)
    except Exception as exc:
        logger.warning("shell.prefs.write_failed", extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    # After the commit: the member's version moves, so the next feed read
    # misses (MD-5a, ``my_day_feed.md`` §8.4 item 1).
    await cache.bump_member(user.organization_id, uid)
    return stored_prefs(stored)
