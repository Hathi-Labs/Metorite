"""Every Projects SQL builder, executed on the driver PRODUCTION uses.

⚠️ **This file exists because R8 was not enough, and the reason is exact.**

`scripts/dev_db.sh` hands every suite a `postgresql+psycopg` DSN.
`acb_common.db.database_url()` rewrites every production DSN onto
`postgresql+asyncpg`. So "verified against a real database" has meant
"verified against a real database through the wrong driver" for the whole
life of this tree.

**What that cost, measured 2026-09-17.** `/projects/analytics/stuck` answered
**500 at every scope** from the day it merged. Two faults, stacked:

1. A band was named ``7_to_14d`` and went out as a bare SQL alias. Postgres
   reads a leading digit as a numeric literal. Both drivers reject it — but
   §9.12.7(a) shipped with a structural suite, so nothing ever ran the query.
2. Underneath it, ``CAST(:x AS interval)`` bound with the string ``'0 days'``.
   **psycopg accepts that. asyncpg refuses it** — *"invalid input for query
   argument $1: '0 days' (str object has no attribute days)"*. So even an R8
   test would have gone green while production 500'd.

⚠️ **The knowledge already existed in five places and stopped none of it.**
`delta.py` twice, `filters.parse_when`, `tasks/people.py` twice, and
`crm/core.py` all carry a comment about this trap. A comment in another
module is not a fence. This is the fence.

**What this suite does NOT do.** It asserts almost nothing about the numbers.
`test_projects_analytics_*.py` own correctness. This file owns one question:
*does the driver production runs accept the SQL we ship?* Keeping that
separate is deliberate — a correctness suite that also had to be async would
have been written later, or not at all.
"""
from __future__ import annotations

import asyncio
import os

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")

from gateway.routes.projects import analytics as A
from gateway.routes.projects import attachments as pm_attachments
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    COMPLETED_CATEGORY,
    STARTED_CATEGORY,
)
from gateway.routes.projects.tasks import VIEW_POSITION_JOIN
from gateway.routes.projects.tree import (
    NODE_DESCENDANTS_SQL,
    node_counts_sql,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset. ⚠️ This suite is the ONLY one that"
        " runs Projects SQL on asyncpg, the driver production uses. Skipping"
        " it leaves the exact gap that shipped /analytics/stuck broken."
    ),
)


def _async_url() -> str:
    """The same rewrite `acb_common.db` performs on every production DSN.

    ⚠️ Not a convenience. Using the suite's psycopg URL here would make this
    file a slower copy of the tests beside it, and prove nothing.
    """
    url = _TENANT_URL
    if "postgresql+psycopg" in url:
        return url.replace("postgresql+psycopg", "postgresql+asyncpg")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest.fixture
async def async_engine():
    """One engine per test, and the scope is not negotiable.

    ⚠️ A module-scoped engine binds its pool to the FIRST test's event loop,
    and `asyncio_mode = "auto"` gives each test its own. Every test after the
    first then fails with *"cannot perform operation: another operation is in
    progress"* — which looks exactly like a SQL fault and is not one.
    """
    eng = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


# `TRUE` stands in for the caller's scope, grants and open-only predicate.
# This suite is about the SQL the builders emit, not about who may see what —
# `test_projects_grants.py` owns that, and re-testing it here would leave two
# places to change when the clause moves.
WHERE = "TRUE"

#: Each builder, with parameters shaped the way the ROUTE passes them.
#:
#: ⚠️ The parameter TYPES are the point. Passing `"7 days"` where the route
#: passes `7` would make this suite pass while production fails, which is the
#: entire defect being fenced.
_PERIOD = {
    "weeks": 4,
    "closing": sorted(CLOSING_CATEGORIES),
    "done_cat": COMPLETED_CATEGORY,
    "started_cat": STARTED_CATEGORY,
}


def _cases() -> list[tuple[str, str, dict]]:
    bands, band_params = A.stale_bands_sql(WHERE)
    return [
        ("stale_bands_sql", bands, band_params),
        ("overdue_by_project_sql", A.overdue_by_project_sql(WHERE), {}),
        ("load_sql", A.load_sql(WHERE), {}),
        ("total_open_sql", A.total_open_sql(WHERE), {}),
        ("effort_sql", A.effort_sql(WHERE), {}),
        ("weekly_sql", A.weekly_sql(WHERE), _PERIOD),
        ("cycle_summary_sql", A.cycle_summary_sql(WHERE), _PERIOD),
        ("finished_sql", A.finished_sql(WHERE), _PERIOD),
        (
            "finished_sql/skip_current_week",
            A.finished_sql(WHERE, skip_current_week=True),
            _PERIOD,
        ),
        ("finished_period_sql", A.finished_period_sql(), {"weeks": 4}),
        (
            "finished_period_sql/skip_current_week",
            A.finished_period_sql(skip_current_week=True),
            {"weeks": 4},
        ),
        # Wave 7 — the outlook read. Added here in the SAME pull request that
        # introduced them, which is the whole point of having this file.
        ("velocity_sql", A.velocity_sql(WHERE), _PERIOD),
        ("planned_finish_sql", A.planned_finish_sql(WHERE), {}),
        (
            "team_capacity_sql",
            A.team_capacity_sql(WHERE),
            {"horizon_days": 90},
        ),
    ]


@pytest.mark.parametrize(
    "name,sql,params", _cases(), ids=[c[0] for c in _cases()]
)
async def test_the_builder_runs_on_asyncpg(async_engine, name, sql, params):
    """⚠️ The test that was missing.

    It asserts nothing about the answer. Either asyncpg accepts the statement
    and its bound parameters, or it raises — and for a day, it raised.
    """
    async with async_engine.begin() as conn:
        await conn.execute(text(sql), params)


async def test_the_node_summary_walk_runs_on_asyncpg(async_engine):
    """`tree.py`'s two reads, which feed every dashboard in the product."""
    async with async_engine.begin() as conn:
        await conn.execute(
            text(NODE_DESCENDANTS_SQL),
            {"pid": "00000000-0000-0000-0000-000000000000"},
        )
        await conn.execute(
            text(node_counts_sql("TRUE")),
            {
                "ids": ["00000000-0000-0000-0000-000000000000"],
                "closed": sorted(CLOSING_CATEGORIES),
            },
        )


async def test_an_interval_bound_as_a_STRING_is_refused(async_engine):
    """⚠️ The trap itself, pinned — so nobody "simplifies" back into it.

    This is the shape `stale_bands_sql` used to emit. It works under psycopg,
    which is why a real-database test went green while every caller got a
    500. If this test ever starts PASSING, asyncpg has changed its mind and
    the whole rule below can be revisited — until then, bind a real
    `timedelta`, or pass ints through `make_interval()`.
    """
    from sqlalchemy.exc import DBAPIError

    async with async_engine.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(
                text("SELECT now() - CAST(:d AS interval)"), {"d": "7 days"}
            )


async def test_make_interval_with_an_INT_is_the_shape_that_works(async_engine):
    """The replacement, proven rather than assumed. It is also the idiom the
    rest of `analytics.py` already used for weeks, so there is now one shape
    in that module instead of two."""
    async with async_engine.begin() as conn:
        got = (
            await conn.execute(
                text("SELECT make_interval(days => :d) AS i"), {"d": 7}
            )
        ).scalar_one()
    assert got.days == 7


async def test_the_board_order_join_runs_on_asyncpg(async_engine):
    """H-64's read half, on the driver production uses.

    ⚠️ Added in the SAME pull request that introduced the join. That is what
    this file is for — `/analytics/stuck` shipped 500ing because its query was
    never run on asyncpg, and a fence only pays for itself if new SQL goes
    into it on arrival rather than after the next outage.
    """
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                f"SELECT t.id, vp.position AS view_position"
                f"  FROM pm_tasks t{VIEW_POSITION_JOIN}"
                f" WHERE t.id = CAST(:tid AS uuid)"
            ),
            {
                "tid": "00000000-0000-0000-0000-000000000000",
                "view_id": "00000000-0000-0000-0000-000000000000",
            },
        )


async def test_the_reportable_ancestor_walk_runs_on_asyncpg(async_engine):
    """D-PM-32(b) — the clause that drops a stopped project out of reports.

    ⚠️ Added with the clause, in the same pull request, because the suite's
    `WHERE = "TRUE"` constant means every builder case above would stay green
    while the real `open_where` carried an unbindable predicate. That gap is
    exactly H-114's: a fence that covers the builders and not the WHERE they
    are handed reads like a whole fence.

    A recursive CTE correlated to `t.project_id`, with a `text[]` bind. Both
    halves are shapes asyncpg can refuse where psycopg does not.
    """
    from gateway.routes.projects.core import (
        REPORTABLE_STATUSES,
        reportable_with_ancestors_clause,
    )

    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "SELECT count(*) FROM pm_tasks t "
                f" WHERE ({reportable_with_ancestors_clause('t')})"
            ),
            {"reportable_states": sorted(REPORTABLE_STATUSES)},
        )

# ── The attachment budget's lock ────────────────────────────────────────────
#
# ⚠️ **The hermetic suite cannot see this one at all.** `FakeDB.execute`
# answers an empty result for any statement it does not recognise, so it
# accepts the lock however it is spelled — and a lock that is a no-op looks
# identical to a lock that works, right up to the moment two uploads land
# together. Only a real Postgres can say whether it EXCLUDES.


async def test_the_budget_lock_runs_on_asyncpg(async_engine):
    """It parses, with the bound-parameter shape the route actually sends.

    `tid` arrives as a string and not a uuid object, because that is what
    `POST /tasks/{task_id}/attachments` binds.
    """
    async with async_engine.begin() as conn:
        await conn.execute(
            text(pm_attachments._BUDGET_LOCK_SQL),
            {"ns": pm_attachments._BUDGET_LOCK_NS,
             "tid": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"},
        )


async def test_two_uploads_to_one_task_cannot_hold_the_lock_together(
    async_engine,
):
    """The reason the lock is there: the budget check is read-then-write.

    Without it, five files dropped on one task upload in parallel, each reads
    the same pre-upload SUM, all five pass, and a 10MB task holds 45MB. This
    proves the second transaction WAITS rather than reading past the first.
    """
    stmt = text(pm_attachments._BUDGET_LOCK_SQL)
    args = {"ns": pm_attachments._BUDGET_LOCK_NS,
            "tid": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"}

    async with async_engine.connect() as first, async_engine.connect() as second:
        await first.execute(stmt, args)
        waiting = asyncio.create_task(second.execute(stmt, args))
        # Long enough that a no-op lock finishes, short enough to stay cheap.
        await asyncio.sleep(0.4)
        assert not waiting.done(), (
            "the second transaction took the lock while the first held it — "
            "the budget SUM and its INSERT are not serialised, so parallel "
            "uploads to one task can each pass the same stale check"
        )
        await first.rollback()
        # And it must not wait FOREVER: the first ending releases it.
        await asyncio.wait_for(waiting, timeout=5)
        await second.rollback()


async def test_the_lock_does_not_serialise_unrelated_tasks(async_engine):
    """Keyed on the task, or every upload in the org queues behind one.

    A hash collision between two task ids would show up here as a hang, and
    that is the honest cost of hashing a uuid into the lock space.
    """
    stmt = text(pm_attachments._BUDGET_LOCK_SQL)
    ns = pm_attachments._BUDGET_LOCK_NS

    async with async_engine.connect() as first, async_engine.connect() as second:
        await first.execute(
            stmt, {"ns": ns, "tid": "3f2504e0-4f89-11d3-9a0c-0305e82c3301"})
        await asyncio.wait_for(
            second.execute(
                stmt, {"ns": ns, "tid": "8f14e45f-ceea-467a-9ae7-1f0f1b2c3d4e"}),
            timeout=5,
        )
        await first.rollback()
        await second.rollback()


async def test_the_lock_key_does_not_depend_on_how_the_id_is_SPELLED(
    async_engine,
):
    """⚠️ The three tests above bind the same string to both sides, so none of
    them can see this.

    `hashtext` hashes TEXT. Postgres accepts a uuid in upper case, mixed case
    and braced form and normalises all of them, so every other statement in
    the request — the budget SUM, the INSERT, `load_visible_task` — agrees
    that two spellings are ONE task. If the lock key were the raw path string
    they would take two different locks, both read the same pre-upload SUM,
    and the race the lock exists to close would reopen for anyone who varied
    the case of one character.
    """
    stmt = text(pm_attachments._BUDGET_LOCK_SQL)
    ns = pm_attachments._BUDGET_LOCK_NS
    lower = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    upper = lower.upper()

    async with async_engine.connect() as first, async_engine.connect() as second:
        await first.execute(stmt, {"ns": ns, "tid": lower})
        waiting = asyncio.create_task(
            second.execute(stmt, {"ns": ns, "tid": upper}))
        await asyncio.sleep(0.4)
        assert not waiting.done(), (
            "the same task id in a different case took a DIFFERENT lock — "
            "the lock key is the raw request string rather than the "
            "normalised uuid the rows are keyed by"
        )
        await first.rollback()
        await asyncio.wait_for(waiting, timeout=5)
        await second.rollback()
