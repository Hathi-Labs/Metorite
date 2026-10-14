"""WS-58 MD-6a-3, R8: ``GET /shell/feed/decide-measure`` on a REAL Postgres
(``my_day_feed.md`` §10.9 item 10, MD-6a-3 done-whens 1 and 3).

The measure read counts in SQL: it joins ``my_day_judgments`` with
``my_day_card_state``, ``user_settings`` and ``pg_timezone_names``. A
hermetic fake agrees with any join it is handed, so this runs the REAL route
function and its SQL on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. The answer's math has its own
hermetic suite, ``test_feed_measure.py``.

* **The acted window and its edges.** An act before the judgment, an act at
  the judgment, an act on the next local day, a member in Asia/Kolkata whose
  local day crosses the UTC date, and a stored zone that does not exist.
* **The mode split.** A row in ``on`` never counts in ``shadow``.
* **The statement limit.** A read past it answers 503, and the limit ends
  with the read's transaction.
* **Two orgs, as the app role with RLS forced.** Org B's read counts only
  org B's rows. The same address holds rows in both orgs, so the joins must
  key on the org too.
* **Two orgs, on a session that bypasses RLS.** Production connects as the
  role that owns the tables, so RLS does not hold it there. Only the org
  predicate of the SQL can keep org A out, and this test fences it.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException
from gateway.db import tenant_session
from gateway.routes.shell import feed_measure as fm
from gateway.routes.shell.intent import ADMIN_PERMISSION
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
M1 = f"measure-1-{TAG}@md6a3.test"  # UTC
M2 = f"measure-2-{TAG}@md6a3.test"  # Asia/Kolkata
M3 = f"measure-3-{TAG}@md6a3.test"  # a zone that does not exist
MEMBERS = (M1, M2, M3)

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
H = timedelta(hours=1)


def _admin(org: str) -> UserContext:
    return UserContext(email=f"admin-{TAG}@md6a3.test", role=UserRole.EMPLOYEE,
                       organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset({ADMIN_PERMISSION})))


def _judgment(c, org, member, sid, feature, old, judged, created, old_rank=None,
              judged_rank=None, mode="shadow") -> None:
    c.execute(text(
        "INSERT INTO my_day_judgments (organization_id, member_email, signal_id, "
        "fingerprint, feature, mode, old_value, judged_value, confidence, used, "
        "old_rank, judged_rank, created_at) VALUES (CAST(:o AS uuid), :m, :s, 'fp', :f, "
        ":mode, :old, :judged, 0.7, false, :orank, :jrank, :at)"),
        {"o": org, "m": member, "s": sid, "f": feature, "old": old, "judged": judged,
         "orank": old_rank, "jrank": judged_rank, "at": created, "mode": mode})


def _acted(c, org, member, sid, acted_at) -> None:
    c.execute(text(
        "INSERT INTO my_day_card_state (organization_id, member_email, signal_id, kind, "
        "state, acted_at) VALUES (CAST(:o AS uuid), :m, :s, 'overdue', 'acted', :at)"),
        {"o": org, "m": member, "s": sid, "at": acted_at})


def _delete(p) -> None:
    with p.admin_engine.begin() as c:
        for table, col in (("my_day_judgments", "member_email"),
                           ("my_day_card_state", "member_email"),
                           ("user_settings", "user_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} IN (:a, :b, :c)"),
                      {"a": M1, "b": M2, "c": M3})


@pytest.fixture(autouse=True)
def seeded(promoted, monkeypatch):  # noqa: F811
    """Org B is the org under test, and org A is the other one. The seed runs
    as the superuser, which stamps each org directly."""
    p = promoted
    monkeypatch.setattr(fm, "_now", lambda: NOW)
    _delete(p)
    a, b = p.org_a, p.org_b
    tri, wt = "my_day.triage", "my_day.weight"
    with p.admin_engine.begin() as c:
        for org in (a, b):
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, organization_id) VALUES "
                "(:m1, 'UTC', CAST(:o AS uuid)), (:m2, 'Asia/Kolkata', CAST(:o AS uuid)), "
                "(:m3, 'Mars/Olympus_Mons', CAST(:o AS uuid))"),
                {"m1": M1, "m2": M2, "m3": M3, "o": org})
        # ── Org B, shadow ──
        made = NOW - 2 * H  # 07:00 UTC on the 14th
        # s1: in the old top five only. Acted one hour after: counts.
        _judgment(c, b, M1, "tasks:s1", tri, "now", "now", made, 1, 6)
        _acted(c, b, M1, "tasks:s1", NOW - H)
        # s2: old top five. Acted one hour BEFORE the judgment: no.
        _judgment(c, b, M1, "tasks:s2", tri, "needs_you", "later", made, 2, 7)
        _acted(c, b, M1, "tasks:s2", made - H)
        # s3: judged top five, no answer. Acted on the NEXT local day: no.
        _judgment(c, b, M1, "tasks:s3", tri, "fyi", None, made, 8, 3)
        _acted(c, b, M1, "tasks:s3", datetime(2026, 10, 15, 0, 30, tzinfo=UTC))
        # s4: Kolkata. Judged at 00:15 IST on the 14th (18:45 UTC on the 13th),
        # acted at 13:30 IST on the 14th: two UTC dates, one local date. Counts.
        early = datetime(2026, 10, 13, 18, 45, tzinfo=UTC)
        _judgment(c, b, M2, "tasks:s4", tri, "fyi", "fyi", early, 3, 1)
        _acted(c, b, M2, "tasks:s4", datetime(2026, 10, 14, 8, 0, tzinfo=UTC))
        # s6: acted AT the judgment: counts (the window starts at created_at).
        _judgment(c, b, M1, "tasks:s6", tri, "later", "later", made, 4, 4)
        _acted(c, b, M1, "tasks:s6", made)
        # s8: a stored zone that does not exist reads as UTC, and the read
        # does not fail. Acted the same UTC day: counts.
        _judgment(c, b, M3, "tasks:s8", tri, "now", "later", NOW - 3 * H, 5, 5)
        _acted(c, b, M3, "tasks:s8", NOW - H)
        # s5: ten days old. Inside 30 days, outside 7. Never acted.
        _judgment(c, b, M1, "tasks:s5", tri, "now", "now", NOW - timedelta(days=10), 1, 1)
        # A weight row, which carries no rank.
        _judgment(c, b, M1, "tasks:s1", wt, "high", "high", made)
        # ── Org B, on: never counted in shadow ──
        _judgment(c, b, M1, "tasks:s7", tri, "needs_you", "now", made, 4, 2, mode="on")
        _acted(c, b, M1, "tasks:s7", NOW - H)
        # ── Org A, the same addresses ──
        _judgment(c, a, M1, "tasks:s2", tri, "now", "now", NOW - H, 1, 1)
        _judgment(c, a, M1, "tasks:a1", "my_day.next_act", "open", "open", NOW - H)
        # An act on s2 in org A, inside s2's window in org B. A join that drops
        # the org would count s2 as acted in org B.
        _acted(c, a, M1, "tasks:s2", NOW - H)
    yield p
    _delete(p)


def _p(old, judged, rows=1):
    return {"old": old, "judged": judged, "rows": rows}


#: Org B, over the default 7 days.
ORG_B_7 = {
    "days": 7,
    "since": (NOW - timedelta(days=7)).isoformat(),
    "acts": 7,  # s1, s2, s3, s4, s6, s7 and s8
    "features": {
        "my_day.triage": {
            "shadow": {"rows": 6, "agree_rate": 0.5, "pairs": [
                _p("fyi", None), _p("fyi", "fyi"), _p("later", "later"),
                _p("needs_you", "later"), _p("now", "later"), _p("now", "now")]},
            "on": {"rows": 1, "agree_rate": 0.0, "pairs": [_p("needs_you", "now")]},
        },
        "my_day.weight": {"shadow": {"rows": 1, "agree_rate": 1.0,
                                     "pairs": [_p("high", "high")]}},
    },
    "top_five": {
        "shadow": {
            "old": {"cards": 5, "acted": 4, "acted_rate": 0.8},       # s1 s2 s4 s6 s8
            "judged": {"cards": 4, "acted": 3, "acted_rate": 0.75},   # s3 s4 s6 s8
        },
        "on": {
            "old": {"cards": 1, "acted": 1, "acted_rate": 1.0},       # s7
            "judged": {"cards": 1, "acted": 1, "acted_rate": 1.0},
        },
    },
}


async def _read(org: str, days: int = fm.DEFAULT_DAYS) -> dict:
    return await fm.decide_measure(days=days, user=_admin(org))


@asynccontextmanager
async def _as_owner(p, org: str):
    """Bind ``org`` and point the shared engine at the SUPERUSER, which RLS
    does not hold. So only the SQL's own org predicate can filter."""
    token = bind_tenant(org)
    try:
        async with tenant_engine_scope(
                p.admin_engine.url.render_as_string(hide_password=False)):
            yield
    finally:
        release_tenant(token)


class TestTheShape:
    async def test_the_answer_of_the_bound_org(self, seeded):
        async with _as_member(seeded, seeded.org_b):
            assert await _read(seeded.org_b) == ORG_B_7

    async def test_thirty_days_takes_the_older_row(self, seeded):
        async with _as_member(seeded, seeded.org_b):
            out = await _read(seeded.org_b, 30)
        triage = out["features"]["my_day.triage"]["shadow"]
        assert (triage["rows"], triage["agree_rate"]) == (7, 0.5714)
        assert triage["pairs"][0] == _p("now", "now", 2)
        assert out["top_five"]["shadow"] == {
            "old": {"cards": 6, "acted": 4, "acted_rate": 0.6667},
            "judged": {"cards": 5, "acted": 3, "acted_rate": 0.6},
        }


class TestTheStatementLimit:
    async def test_a_read_past_the_limit_is_a_503(self, seeded, monkeypatch):
        monkeypatch.setattr(fm, "STATEMENT_TIMEOUT_MS", 200)
        monkeypatch.setattr(fm, "_ACTS_SQL", "SELECT 0 AS n FROM pg_sleep(2)")
        async with _as_member(seeded, seeded.org_b):
            with pytest.raises(HTTPException) as err:
                await _read(seeded.org_b)
        assert err.value.status_code == 503

    async def test_the_limit_ends_with_the_reads_transaction(self, seeded):
        async with _as_member(seeded, seeded.org_b):
            await _read(seeded.org_b)
            async with tenant_session() as db:
                after = (await db.execute(text("SHOW statement_timeout"))).scalar_one()
        assert after == "0"


class TestTheTenancyBoundary:
    async def test_each_org_reads_its_own_rows_as_the_app_role(self, seeded):
        p = seeded
        async with _as_member(p, p.org_b):
            assert await _read(p.org_b) == ORG_B_7
        async with _as_member(p, p.org_a):
            out = await _read(p.org_a)
        assert out["acts"] == 1
        assert out["features"] == {
            "my_day.next_act": {"shadow": {"rows": 1, "agree_rate": 1.0,
                                           "pairs": [_p("open", "open")]}},
            "my_day.triage": {"shadow": {"rows": 1, "agree_rate": 1.0,
                                         "pairs": [_p("now", "now")]}},
        }
        assert out["top_five"]["shadow"]["old"] == {"cards": 1, "acted": 1,
                                                    "acted_rate": 1.0}

    async def test_the_org_predicate_holds_on_a_session_that_bypasses_rls(self, seeded):
        p = seeded
        async with _as_owner(p, p.org_b):
            async with tenant_session() as db:
                both = (await db.execute(text(
                    "SELECT count(DISTINCT organization_id) FROM my_day_judgments "
                    "WHERE member_email = :m"), {"m": M1})).scalar_one()
            out = await _read(p.org_b)
        assert both == 2, "the owner session must see both orgs, or this proves nothing"
        assert out == ORG_B_7
