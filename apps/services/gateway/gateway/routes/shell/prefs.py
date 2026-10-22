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

import json
import logging
import unicodedata
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from fastapi import Body, Depends, HTTPException
from gateway.db import tenant_session as _tenant_session
from gateway.routes.shell import cache, feed_rank
from gateway.routes.shell import feed as _feed  # noqa: F401  (registers the feed kinds)
from gateway.routes.shell.search import router
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

logger = logging.getLogger(__name__)

#: The three keys of ``my_day_prefs`` (§16 MD-2d).
PREF_KEYS: tuple[str, ...] = ("mutes", "open_in_app", "corrections")
#: The longest list of kinds that ``mutes`` and ``open_in_app`` take.
MAX_KINDS = 23
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
    WS-53 CRM-Q32-4 added ``crm_open_questions``, and 23 after WS-61 KB-9a
    added ``kb_gap``, which is ``MAX_KINDS``
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


_CLEAN = {"mutes": _kinds, "open_in_app": _kinds, "corrections": _corrections}


class PrefPut(BaseModel):
    """What ``PUT /shell/feed/prefs`` accepts: one key and its whole value."""

    model_config = ConfigDict(extra="forbid")

    key: Literal["mutes", "open_in_app", "corrections"]
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
