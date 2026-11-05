"""The delivery monitor of the brief (WS-BRIEF BR-2d, ``core_brief.md`` §7).

The target: 99% of the runs of an org are ``delivered`` within 5 minutes of
``slot_at``, over 7 days. The lag is ``delivered_at`` minus ``slot_at``.

**What counts.** A run counts when its slot is at least :data:`WINDOW` old
(it had its 5 minutes) and it is one of these:

* ``delivered``. It is on time when its lag is at most :data:`WINDOW`.
* ``ready``, ``planned``, ``gathered`` or ``phrasing``. The run is late: it
  is still not on an in-app surface.
* ``failed`` with a real error. This includes a run with no reason.

These stay out, because no brief was owed: ``empty`` (no card), ``failed``
with ``slot_missed`` (the org turned on after the slot, ``core_brief.md`` §9
note 4) and ``failed`` with ``opted_out`` or ``member_*`` (the member left or
turned the brief off).

**The check.** :func:`seed` enqueues the session kind :data:`SLO_KIND` once
each hour and org. :func:`check` reads the stats, and it logs
``brief.slo_missed`` at error when the share is under :data:`TARGET`. An org
with no counted run logs nothing.

**The view.** :func:`stats` also feeds ``GET /admin/brief/health``
(``routes/admin_brief_health.py``).

The module only reads ``brief_runs`` of the bound tenant (R5). The org is
never an argument, and the statement names the bound org as the store does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from acb_common import get_logger
from acb_common.db import tenant_session
from sqlalchemy import text

from gateway.brief import flags
from gateway.jobs import queue, worker

_log = get_logger("brief.monitor")

#: The kind of the hourly check.
SLO_KIND = "brief.slo"
#: The delivery promise: a run is on time when delivered within this long.
WINDOW = timedelta(minutes=5)
#: The share of on-time runs that the org must reach.
TARGET = 0.99
#: The days that the stats look back, by ``slot_at``.
DAYS = 7
#: The error values of a ``failed`` run that no brief was owed for.
NOT_OWED_ERRORS = ("slot_missed", "opted_out")
#: The prefix of the error of a member who left (``member_<reason>``).
NOT_OWED_PREFIX = "member\\_%"

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

#: One row: the counted runs, the on-time runs, and the lag percentiles of
#: the delivered runs in seconds.
_STATS_SQL = (
    "WITH r AS ("
    " SELECT state, EXTRACT(EPOCH FROM (delivered_at - slot_at)) AS lag "
    " FROM brief_runs "
    f" WHERE organization_id = {_BOUND_ORG} "
    " AND slot_at > CAST(:since AS timestamptz) "
    " AND slot_at <= CAST(:until AS timestamptz) "
    " AND state <> 'empty' "
    " AND NOT (state = 'failed' AND (COALESCE(error, '') = ANY(CAST(:skip AS text[])) "
    "      OR COALESCE(error, '') LIKE :prefix))) "
    "SELECT count(*), "
    " count(*) FILTER (WHERE state = 'delivered' AND lag <= :window), "
    " percentile_cont(0.5) WITHIN GROUP (ORDER BY lag) FILTER (WHERE state = 'delivered'), "
    " percentile_cont(0.95) WITHIN GROUP (ORDER BY lag) FILTER (WHERE state = 'delivered'), "
    " max(lag) FILTER (WHERE state = 'delivered') "
    "FROM r"
)


@dataclass(frozen=True)
class Stats:
    """The delivery stats of the bound org over :data:`DAYS`."""

    counted: int
    on_time: int
    p50_s: float | None
    p95_s: float | None
    max_s: float | None

    @property
    def share(self) -> float | None:
        """The on-time share, or None when no run counted."""
        return None if self.counted == 0 else self.on_time / self.counted

    @property
    def met(self) -> bool:
        """True when the target holds. An org with no counted run met it."""
        share = self.share
        return share is None or share >= TARGET

    def body(self) -> dict[str, Any]:
        """The JSON of ``GET /admin/brief/health``."""
        return {
            "window_days": DAYS, "target": TARGET,
            "window_seconds": int(WINDOW.total_seconds()),
            "counted": self.counted, "on_time": self.on_time,
            "share": self.share, "met": self.met,
            "lag_seconds": {"p50": self.p50_s, "p95": self.p95_s, "max": self.max_s},
        }


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


async def stats(db: Any, now: datetime) -> Stats:
    """The stats of the bound org at *now*. Reads only."""
    row = (await db.execute(text(_STATS_SQL), {
        "since": now - timedelta(days=DAYS), "until": now - WINDOW,
        "skip": list(NOT_OWED_ERRORS), "prefix": NOT_OWED_PREFIX,
        "window": WINDOW.total_seconds(),
    })).fetchone()
    return Stats(counted=int(row[0] or 0), on_time=int(row[1] or 0),
                 p50_s=_f(row[2]), p95_s=_f(row[3]), max_s=_f(row[4]))


async def check(db: Any, payload: dict[str, Any]) -> None:
    """The ``session`` handler of :data:`SLO_KIND`. It logs
    ``brief.slo_missed`` at error when the org is under :data:`TARGET`."""
    from datetime import UTC

    s = await stats(db, datetime.now(UTC))
    if not s.met:
        _log.error("brief.slo_missed", counted=s.counted, on_time=s.on_time,
                   share=s.share, p95_s=s.p95_s)


async def seed(org: str, now: datetime) -> None:
    """The ``worker.seed_hooks`` hook: one :data:`SLO_KIND` job each hour and
    org, when the org gets a brief. The dedupe key holds the hour."""
    if not flags.org_allowed(org):
        return
    async with tenant_session(org) as db:
        await queue.enqueue(db, SLO_KIND, {}, dedupe_key=f"slo:{now:%Y-%m-%dT%H}", now=now)


def register_jobs(*, replace: bool = False) -> None:
    """Register the kind and the seed hook. Safe to call twice."""
    worker.register(SLO_KIND, check, replace=replace, effect="read")
    if seed not in worker.seed_hooks:
        worker.seed_hooks.append(seed)


register_jobs()
