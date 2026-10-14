"""``GET /shell/feed/decide-measure`` — the measure read of My Day's
``decide`` pass (WS-58 MD-6a-3).

Spec: ``project-docs/specs/my_day_feed.md`` §10.6 item 4, §10.9 item 10,
§13.2 and §16 MD-6a-3. MD-6b reads this answer to set the gate of each
feature, so MD-6b does not start before it.

**The answer.** Every number is split by the row's ``mode``.

.. code-block:: json

    {"days": 7, "since": "2026-10-07T09:00:00+00:00", "acts": 9,
     "features": {"my_day.triage": {"shadow": {
         "rows": 40, "agree_rate": 0.75,
         "pairs": [{"old": "now", "judged": "now", "rows": 30}, ...]}}},
     "top_five": {
         "shadow": {"old":    {"cards": 12, "acted": 3, "acted_rate": 0.25},
                    "judged": {"cards": 12, "acted": 5, "acted_rate": 0.4167}},
         "on": {...}}}

- **The mode split.** In ``shadow`` the member sees the old order, so both
  paths compete on equal terms. In ``on`` the member sees the judged order,
  so the judged path's acted rate is high by construction. MD-6b sets each
  gate from ``shadow`` only. The ``on`` numbers are for watching a feature
  after it goes on.
- ``features`` holds each ``my_day.*`` feature, and under it each mode, that
  has a row in the span. ``agree_rate`` is the share of rows whose judged
  value equals the old value (:func:`agrees`). A row with no judged value
  counts as a row and never as an agreement.
- ``pairs`` holds the count of each ``(old, judged)`` pair, the most rows
  first. ``my_day.resurface`` always has the old value ``"false"``, so its
  ``agree_rate`` is only the share of ``"false"`` answers. A gate for
  resurface reads ``pairs``.
- ``top_five`` counts the ``my_day.triage`` rows with ``old_rank`` 1 to 5
  (``old``) and with ``judged_rank`` 1 to 5 (``judged``). The pass writes the
  ranks on the triage row only (§13.2). One row is one card in one pass, so
  a card that two passes judged counts two times. Both modes are always
  present.
- A row counts as acted when the member's ``my_day_card_state`` row for the
  card has an ``acted_at`` at or after the judgment's ``created_at``, on the
  same local date in the member's zone.
- ``acts`` counts the org's ``my_day_card_state`` rows with an ``acted_at``
  in the span. With ``acts`` at 0, nothing in the org records an act (for
  example, ``NEXT_PUBLIC_MY_DAY_FEED`` is off), so each ``acted_rate`` is
  ``null``, not ``0.0``.
- A rate with no rows under it is ``null``, never ``0``.

**The cost.** The database does the counting, so the read returns a few rows
whatever the span. The reads run under ``SET LOCAL statement_timeout``
(:data:`STATEMENT_TIMEOUT_MS`). A read that runs out of time answers 503.

**The span.** ``days`` is 7 by default, 1 to 30. The loop deletes rows older
than 30 days (§13.2), so 30 is all the data there is. MD-6b reads 30.

**The day.** The local calendar date in ``user_settings.timezone``, the zone
that the feed reads (``state._hours``, ``personal.stored_zone``). It is not
``state.Day.feed_day``: the morning pass runs 15 minutes before
``day_start_hour`` (MD-6a-2), so ``feed_day`` puts its rows on the day before
the acts they judged. The SQL reads the zone through ``pg_timezone_names``,
with the rule of ``state.zone_of``: no name and an unknown name are UTC.
Known limit: an act after local midnight by a member who works at night does
not count. It lowers both paths alike.

**The tenant.** The read needs ``ADMIN_PERMISSION`` (``intent.py``). It reads
the bound org only: the org comes from the session's bound tenant through
``tenant_session``, and never from input. Each SQL names the bound org
beside RLS, because the production role owns the tables and is not held by
RLS. ``test_feed_measure_r8.py`` proves it on a session that bypasses RLS.

Fences: ``tests/unit/test_feed_measure.py`` (the answer, the 403, the
timeout) and ``tests/unit/test_feed_measure_r8.py`` (R8: the window, the
mode split and two orgs).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_auth import UserContext, require_permission
from fastapi import HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.decide_features import MY_DAY_PREFIX
from gateway.routes.shell.intent import ADMIN_PERMISSION
from gateway.routes.shell.search import router
from gateway.routes.tasks.settings import _BOUND_ORG

logger = logging.getLogger(__name__)

#: The span of the read, in days, by default and at most (§13.2 keeps 30).
DEFAULT_DAYS = 7
MAX_DAYS = 30
#: The top cards of each path (§10.6 item 4).
TOP = 5
#: The feature whose row carries the ranks (§13.2).
TRIAGE = "my_day.triage"
#: The modes of a row (``my_day_judgments_mode_known``).
MODES: tuple[str, ...] = ("shadow", "on")
#: The places of a rate.
RATE_PLACES = 4
#: The limit of each statement of the read, in milliseconds.
STATEMENT_TIMEOUT_MS = 5_000

#: ``SET LOCAL``: it ends with the read's transaction. ``set_config`` takes a
#: bind, and ``SET`` does not.
_TIMEOUT_SQL = "SELECT set_config('statement_timeout', :ms, true)"

#: Each feature's count of rows, for each mode and pair of values. Python
#: decides agreement (:func:`agrees`), so the rule has one home.
_VALUES_SQL = (
    "SELECT mode, feature, old_value, judged_value, count(*) AS n "
    "FROM my_day_judgments "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND starts_with(feature, :prefix) AND created_at >= :since "
    "GROUP BY mode, feature, old_value, judged_value"
)

#: The top five of each path, counted for each mode, with the acted window
#: of §10.9 item 10. The joins take the org of the judgment row, which the
#: predicate binds. The primary keys of both joined tables hold the org, the
#: member and the card, so each judgment gives one row. ``pg_timezone_names``
#: turns an unknown zone into no row, and so into UTC.
_TOP_SQL = (
    "WITH j AS ("
    "SELECT j.mode, "
    "j.old_rank BETWEEN 1 AND :top AS in_old, "
    "j.judged_rank BETWEEN 1 AND :top AS in_judged, "
    "(s.acted_at IS NOT NULL AND s.acted_at >= j.created_at "
    "AND (s.acted_at AT TIME ZONE COALESCE(z.name, 'UTC'))::date "
    "= (j.created_at AT TIME ZONE COALESCE(z.name, 'UTC'))::date) AS acted "
    "FROM my_day_judgments j "
    "LEFT JOIN my_day_card_state s ON s.organization_id = j.organization_id "
    "AND s.member_email = j.member_email AND s.signal_id = j.signal_id "
    "LEFT JOIN user_settings us ON us.organization_id = j.organization_id "
    "AND us.user_id = j.member_email "
    "LEFT JOIN pg_timezone_names z ON z.name = us.timezone "
    f"WHERE j.organization_id = {_BOUND_ORG} "
    "AND j.feature = :triage AND j.created_at >= :since "
    "AND (j.old_rank BETWEEN 1 AND :top OR j.judged_rank BETWEEN 1 AND :top)) "
    "SELECT mode, "
    "count(*) FILTER (WHERE in_old) AS old_cards, "
    "count(*) FILTER (WHERE in_old AND acted) AS old_acted, "
    "count(*) FILTER (WHERE in_judged) AS judged_cards, "
    "count(*) FILTER (WHERE in_judged AND acted) AS judged_acted "
    "FROM j GROUP BY mode"
)

#: The acts the org recorded in the span, on any card.
_ACTS_SQL = (
    "SELECT count(*) AS n FROM my_day_card_state "
    f"WHERE organization_id = {_BOUND_ORG} AND acted_at >= :since"
)


# ── The rules, as pure functions ────────────────────────────────────────────


def agrees(old: str | None, judged: str | None) -> bool:
    """Whether a row counts as an agreement. No judged value never agrees."""
    return judged is not None and old == judged


def rate(part: int, whole: int) -> float | None:
    """``part / whole``, or None with no rows under it."""
    return round(part / whole, RATE_PLACES) if whole > 0 else None


def _features(value_rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
    pairs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in value_rows:
        n = int(r.n)
        if n <= 0:
            continue
        pairs.setdefault(str(r.feature), {}).setdefault(str(r.mode), []).append(
            {"old": r.old_value, "judged": r.judged_value, "rows": n})
    out: dict[str, dict[str, Any]] = {}
    for feature in sorted(pairs):
        out[feature] = {}
        for mode in sorted(pairs[feature]):
            got = sorted(pairs[feature][mode],
                         key=lambda p: (-p["rows"], str(p["old"]), str(p["judged"])))
            rows = sum(p["rows"] for p in got)
            agreed = sum(p["rows"] for p in got if agrees(p["old"], p["judged"]))
            out[feature][mode] = {"rows": rows, "agree_rate": rate(agreed, rows),
                                  "pairs": got}
    return out


def _path(cards: int, acted: int, acts: int) -> dict[str, Any]:
    return {"cards": cards, "acted": acted,
            "acted_rate": rate(acted, cards) if acts > 0 else None}


def build_answer(days: int, since: datetime, value_rows: Iterable[Any],
                 top_rows: Iterable[Any], acts: int) -> dict[str, Any]:
    """The answer, from the rows of the three reads.

    ``value_rows`` carry ``mode``, ``feature``, ``old_value``,
    ``judged_value`` and ``n``. ``top_rows`` carry ``mode``, ``old_cards``,
    ``old_acted``, ``judged_cards`` and ``judged_acted``. ``acts`` is the
    count of the org's acts in the span.
    """
    by_mode = {str(r.mode): r for r in top_rows}
    top_five: dict[str, Any] = {}
    for mode in MODES:
        r = by_mode.get(mode)
        top_five[mode] = {
            "old": _path(int(r.old_cards) if r else 0, int(r.old_acted) if r else 0, acts),
            "judged": _path(int(r.judged_cards) if r else 0,
                            int(r.judged_acted) if r else 0, acts),
        }
    return {"days": days, "since": since.isoformat(), "acts": acts,
            "features": _features(value_rows), "top_five": top_five}


# ── The read ────────────────────────────────────────────────────────────────


async def read_rows(db: Any, since: datetime
                    ) -> tuple[Sequence[Any], Sequence[Any], int]:
    """The three reads, in the bound tenant, under the statement limit."""
    from sqlalchemy import text

    await db.execute(text(_TIMEOUT_SQL), {"ms": str(STATEMENT_TIMEOUT_MS)})
    values = (await db.execute(text(_VALUES_SQL),
                               {"prefix": MY_DAY_PREFIX, "since": since})).all()
    top = (await db.execute(text(_TOP_SQL),
                            {"triage": TRIAGE, "since": since, "top": TOP})).all()
    acts = (await db.execute(text(_ACTS_SQL), {"since": since})).scalar_one()
    return values, top, int(acts or 0)


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/feed/decide-measure",
            summary="How often My Day's decide agrees with the old path (MD-6a-3)")
async def decide_measure(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    user: UserContext = require_permission(ADMIN_PERMISSION),
) -> dict[str, Any]:
    """The measure of the bound org over the last ``days`` days.

    A failed read, or one that runs out of time, answers 503 and never an
    empty answer, so a database fault never reads as "no data".
    """
    since = _now() - timedelta(days=days)
    try:
        async with _tenant_session() as db:
            values, top, acts = await read_rows(db, since)
    except Exception as exc:
        logger.warning("shell.decide_measure.read_failed",
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return build_answer(days, since, values, top, acts)
