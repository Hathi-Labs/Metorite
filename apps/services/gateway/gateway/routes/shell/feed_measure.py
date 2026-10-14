"""``GET /shell/feed/decide-measure`` — the measure read of My Day's
``decide`` pass (WS-58 MD-6a-3).

Spec: ``project-docs/specs/my_day_feed.md`` §10.6 item 4, §10.9 item 10,
§13.2 and §16 MD-6a-3. MD-6b reads this answer to set the gate of each
feature, so MD-6b does not start before it.

**The answer.**

.. code-block:: json

    {"days": 7, "since": "2026-10-07T09:00:00+00:00",
     "features": {"my_day.triage": {"rows": 40, "agree_rate": 0.75}},
     "top_five": {"old":    {"cards": 12, "acted": 3, "acted_rate": 0.25},
                  "judged": {"cards": 12, "acted": 5, "acted_rate": 0.4167}}}

- ``features`` holds each ``my_day.*`` feature that has a row in the span.
  ``agree_rate`` is the share of rows whose judged value equals the old
  value (:func:`agrees`). A row with no judged value (no answer, or a key
  that is not ours) counts as a row and never as an agreement.
- ``top_five`` counts the ``my_day.triage`` rows with ``old_rank <= 5``
  (``old``) and with ``judged_rank <= 5`` (``judged``). The pass writes the
  ranks on the triage row only (§13.2). One row is one card in one pass, so
  a card that two passes judged counts twice.
- A row counts as acted when the member's ``my_day_card_state`` row for the
  card has an ``acted_at`` at or after the judgment's ``created_at``, on the
  same local date in the member's zone (:func:`acted_in_window`).
- A rate with no rows under it is ``null``, never ``0``.

**The span.** ``days`` is 7 by default, 1 to 30. The loop deletes rows older
than 30 days (§13.2), so 30 is all the data there is. MD-6b sets a gate from
the shadow data, and a gate set from a thin span is weak, so it reads 30.

**The day.** The local date of ``user_settings.timezone``, the zone that the
feed reads (``state._hours``, ``personal.stored_zone``). It is the calendar
date, and not ``state.Day.feed_day``. The morning pass runs 15 minutes
before ``day_start_hour`` (MD-6a-2), so ``feed_day`` would put its rows on
the day before the acts they judged. A missing or bad zone reads as UTC.

**The tenant.** The read needs ``ADMIN_PERMISSION`` (``intent.py``). It reads
the bound org only: the org comes from the session's bound tenant through
``tenant_session``, and never from input. The SQL names the bound org
beside RLS, because the production role owns the tables and is not held
by RLS. ``test_feed_measure_r8.py`` proves it on a session that bypasses
RLS.

Fences: ``tests/unit/test_feed_measure.py`` (the rate math, the window and
the 403) and ``tests/unit/test_feed_measure_r8.py`` (R8, two orgs).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from acb_auth import UserContext, require_permission
from fastapi import HTTPException, Query
from gateway.db import tenant_session as _tenant_session
from gateway.decide_features import MY_DAY_PREFIX
from gateway.routes.shell.intent import ADMIN_PERMISSION
from gateway.routes.shell.search import router
from gateway.routes.shell.state import _BOUND_ORG, _zone

logger = logging.getLogger(__name__)

#: The span of the read, in days, by default and at most (§13.2 keeps 30).
DEFAULT_DAYS = 7
MAX_DAYS = 30
#: The top cards of each path (§10.6 item 4).
TOP = 5
#: The feature whose row carries the ranks (§13.2).
TRIAGE = "my_day.triage"
#: The places of a rate.
RATE_PLACES = 4

#: Each feature's count of rows, for each pair of values. Python decides
#: agreement (:func:`agrees`), so the rule has one home.
_VALUES_SQL = (
    "SELECT feature, old_value, judged_value, count(*) AS n "
    "FROM my_day_judgments "
    f"WHERE organization_id = {_BOUND_ORG} "
    "AND starts_with(feature, :prefix) AND created_at >= :since "
    "GROUP BY feature, old_value, judged_value"
)

#: The triage rows in the top five of either path, with the card's last act
#: and the member's zone. The joins take the org of the judgment row, which
#: the predicate binds. The primary keys of both joined tables hold the org,
#: the member and the card, so each judgment gives one row.
_TOP_SQL = (
    "SELECT j.old_rank, j.judged_rank, j.created_at, s.acted_at, us.timezone "
    "FROM my_day_judgments j "
    "LEFT JOIN my_day_card_state s ON s.organization_id = j.organization_id "
    "AND s.member_email = j.member_email AND s.signal_id = j.signal_id "
    "LEFT JOIN user_settings us ON us.organization_id = j.organization_id "
    "AND us.user_id = j.member_email "
    f"WHERE j.organization_id = {_BOUND_ORG} "
    "AND j.feature = :triage AND j.created_at >= :since "
    "AND (j.old_rank <= :top OR j.judged_rank <= :top)"
)


# ── The rules, as pure functions ────────────────────────────────────────────


def agrees(old: str | None, judged: str | None) -> bool:
    """Whether a row counts as an agreement. No judged value never agrees."""
    return judged is not None and old == judged


def member_zone(name: Any) -> ZoneInfo:
    """The member's zone, as ``state._zone`` reads it. A name that no zone
    has reads as UTC. ``state._zone`` lets ``ZoneInfoNotFoundError`` (a
    ``KeyError``) through, so this read catches it, and one bad row never
    fails the whole read."""
    try:
        return _zone(name)
    except KeyError:
        return ZoneInfo("UTC")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def acted_in_window(created_at: datetime, acted_at: datetime | None, zone: Any) -> bool:
    """§10.9 item 10: an act at or after the judgment, on the same local date
    in the member's zone. ``zone`` is the stored name, and a bad one is UTC."""
    if acted_at is None:
        return False
    tz = member_zone(zone)
    made, acted = _aware(created_at), _aware(acted_at)
    if acted < made:
        return False
    return acted.astimezone(tz).date() == made.astimezone(tz).date()


def rate(part: int, whole: int) -> float | None:
    """``part / whole``, or None with no rows under it."""
    return round(part / whole, RATE_PLACES) if whole > 0 else None


def in_top(rank: Any) -> bool:
    return rank is not None and 1 <= int(rank) <= TOP


def build_answer(days: int, since: datetime, value_rows: Iterable[Any],
                 top_rows: Iterable[Any]) -> dict[str, Any]:
    """The answer, from the rows of the two reads.

    ``value_rows`` carry ``feature``, ``old_value``, ``judged_value`` and
    ``n``. ``top_rows`` carry ``old_rank``, ``judged_rank``, ``created_at``,
    ``acted_at`` and ``timezone``.
    """
    counts: dict[str, list[int]] = {}
    for r in value_rows:
        n = int(r.n)
        c = counts.setdefault(str(r.feature), [0, 0])
        c[0] += n
        if agrees(r.old_value, r.judged_value):
            c[1] += n
    features = {f: {"rows": rows, "agree_rate": rate(agreed, rows)}
                for f, (rows, agreed) in sorted(counts.items()) if rows > 0}

    paths = {"old": [0, 0], "judged": [0, 0]}
    for r in top_rows:
        acted = acted_in_window(r.created_at, r.acted_at, r.timezone)
        for path, rank in (("old", r.old_rank), ("judged", r.judged_rank)):
            if in_top(rank):
                paths[path][0] += 1
                paths[path][1] += int(acted)
    top_five = {p: {"cards": cards, "acted": acted, "acted_rate": rate(acted, cards)}
                for p, (cards, acted) in paths.items()}
    return {"days": days, "since": since.isoformat(), "features": features,
            "top_five": top_five}


# ── The read ────────────────────────────────────────────────────────────────


async def read_rows(db: Any, since: datetime) -> tuple[Sequence[Any], Sequence[Any]]:
    """The two reads, in the bound tenant."""
    from sqlalchemy import text

    values = (await db.execute(text(_VALUES_SQL),
                               {"prefix": MY_DAY_PREFIX, "since": since})).all()
    top = (await db.execute(text(_TOP_SQL),
                            {"triage": TRIAGE, "since": since, "top": TOP})).all()
    return values, top


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/feed/decide-measure",
            summary="How often My Day's decide agrees with the old path (MD-6a-3)")
async def decide_measure(
    days: int = Query(DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    user: UserContext = require_permission(ADMIN_PERMISSION),
) -> dict[str, Any]:
    """The measure of the bound org over the last ``days`` days.

    A failed read answers 503, never an empty answer, so a database fault
    never reads as "no data".
    """
    since = _now() - timedelta(days=days)
    try:
        async with _tenant_session() as db:
            values, top = await read_rows(db, since)
    except Exception as exc:
        logger.warning("shell.decide_measure.read_failed",
                       extra={"error": type(exc).__name__})
        raise HTTPException(status_code=503, detail="unavailable") from exc
    return build_answer(days, since, values, top)
