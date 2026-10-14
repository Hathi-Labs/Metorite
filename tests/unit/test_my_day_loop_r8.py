"""WS-58 MD-5b, R8: the sweep of the ``my_day`` loop on a REAL Postgres
(``my_day_feed.md`` §8.4 items 13, 14 and 23, MD-5b done-when 5).

A hermetic fake agrees with any DELETE it is handed, so this suite runs the
REAL ``my_day_loop.sweep`` on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. It connects as the NON-privileged
role with RLS forced (``_as_member``), as ``test_my_day_state_r8.py`` does.
The daily guard goes to a fake under the real ``TenantRedis``.

It shows:

* The sweep deletes old ``seen`` and ``later`` rows and an old time snooze
  that ended, and only in the bound organization.
* It keeps ``dismissed``, ``acted``, a snooze until a change, a time snooze
  that has not ended, and every row younger than 30 days.
* The discovery read lists both organizations as the app role, because
  ``organization`` is exempt from row level security.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import tenant_redis
from acb_common.tenant_redis import TenantRedis
from gateway import my_day_loop
from sqlalchemy import text

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_my_day_loop import _FakeRedis

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
M = f"sweep-{TAG}@md5b.test"

#: signal id -> (state, snoozed_until offset, snooze_on_change, age in days,
#: kept by the sweep). A negative offset is a snooze that ended.
ROWS: dict[str, tuple[str, str | None, bool, int, bool]] = {
    "tasks:seen-old": ("seen", None, False, 40, False),
    "tasks:later-old": ("later", None, False, 40, False),
    "tasks:snooze-ended-old": ("snoozed", "-10 days", False, 40, False),
    "tasks:snooze-open-old": ("snoozed", "5 days", False, 40, True),
    "tasks:on-change-old": ("snoozed", None, True, 40, True),
    "tasks:on-change-dated-old": ("snoozed", "-10 days", True, 40, True),
    "tasks:dismissed-old": ("dismissed", None, False, 40, True),
    "tasks:acted-old": ("acted", None, False, 40, True),
    "tasks:seen-young": ("seen", None, False, 5, True),
    "tasks:snooze-ended-young": ("snoozed", "-1 days", False, 5, True),
}


def _seed(p, org: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_card_state WHERE member_email = :m "
                       "AND organization_id = CAST(:o AS uuid)"), {"m": M, "o": org})
        for sid, (st, until, on_change, age, _kept) in ROWS.items():
            c.execute(text(
                "INSERT INTO my_day_card_state (organization_id, member_email, signal_id, "
                "kind, state, dismiss_reason, snoozed_until, snooze_on_change, updated_at) "
                "VALUES (CAST(:o AS uuid), :m, :s, 'overdue', :st, :reason, "
                "CASE WHEN CAST(:until AS text) IS NULL THEN NULL "
                "ELSE now() + CAST(:until AS interval) END, :chg, "
                "now() - make_interval(days => :age))"),
                {"o": org, "m": M, "s": sid, "st": st, "until": until, "chg": on_change,
                 "age": age, "reason": "dismiss" if st == "dismissed" else None})


def _ids(p, org: str) -> set[str]:
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT signal_id FROM my_day_card_state WHERE member_email = :m "
            "AND organization_id = CAST(:o AS uuid)"), {"m": M, "o": org})
        return {r.signal_id for r in rows}


@pytest.fixture
def redis_fake(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def seeded(promoted):  # noqa: F811
    p = promoted
    _seed(p, p.org_a)
    _seed(p, p.org_b)
    yield p
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_card_state WHERE member_email = :m"), {"m": M})


async def _sweep(p, org: str, day: str) -> int | None:
    async with _as_member(p, org):
        return await my_day_loop.sweep(org, day)


def test_the_sweep_deletes_only_the_bound_orgs_rows_that_hide_nothing(
        seeded, redis_fake) -> None:
    p = seeded
    deleted = asyncio.run(_sweep(p, p.org_b, "2026-10-14"))
    kept = {sid for sid, row in ROWS.items() if row[4]}
    assert deleted == len(ROWS) - len(kept)
    assert _ids(p, p.org_b) == kept
    assert "tasks:dismissed-old" in _ids(p, p.org_b)
    # Org A was not bound, so not one of its rows moved.
    assert _ids(p, p.org_a) == set(ROWS)


def test_the_guard_runs_the_sweep_once_a_day(seeded, redis_fake) -> None:
    p = seeded
    assert asyncio.run(_sweep(p, p.org_b, "2026-10-14")) == 3
    _seed(p, p.org_b)
    assert asyncio.run(_sweep(p, p.org_b, "2026-10-14")) is None
    assert _ids(p, p.org_b) == set(ROWS)
    assert asyncio.run(_sweep(p, p.org_b, "2026-10-15")) == 3


def test_a_session_bound_to_org_a_cannot_sweep_org_b(seeded, redis_fake) -> None:
    """The sweep of A, run as the app role bound to A, leaves B whole."""
    p = seeded
    asyncio.run(_sweep(p, p.org_a, "2026-10-14"))
    assert _ids(p, p.org_b) == set(ROWS)
    assert "tasks:dismissed-old" in _ids(p, p.org_a)
    assert "tasks:seen-old" not in _ids(p, p.org_a)


def test_the_discovery_read_lists_both_orgs_as_the_app_role(seeded) -> None:
    p = seeded

    async def go() -> list[str]:
        async with _as_member(p, p.org_a):
            return await my_day_loop._organization_ids()

    got = asyncio.run(go())
    assert {p.org_a, p.org_b} <= set(got)
