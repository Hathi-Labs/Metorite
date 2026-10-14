"""``GET /shell/feed/decide-measure`` — the hermetic half (WS-58 MD-6a-3).

Spec: ``project-docs/specs/my_day_feed.md`` §10.9 item 10 and §16 MD-6a-3.
The R8 half, against a real Postgres, is ``test_feed_measure_r8.py``. It
proves the SQL, the joins and the tenant wall. This file holds what a fake
can prove honestly:

- the agreement rule: a row with no judged value never agrees;
- the acted window: at or after the judgment, on the same local date in the
  member's zone, and its two edges;
- empty tables: each rate is ``None``, never ``0`` and never a division
  error;
- the top five of each path, from the ranks;
- the admin gate: 403 for a member, 401 for no session, and no read before
  the gate;
- the span: 7 days by default, 1 to 30, and 422 outside it;
- a failed read is a 503, never an empty answer.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.shell import feed_measure as fm
from gateway.routes.shell.intent import ADMIN_PERMISSION
from gateway.routes.shell.search import router

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
KOLKATA = "Asia/Kolkata"  # UTC+05:30, no daylight time


def _v(feature: str, old: str | None, judged: str | None, n: int) -> SimpleNamespace:
    return SimpleNamespace(feature=feature, old_value=old, judged_value=judged, n=n)


def _t(old_rank: int | None, judged_rank: int | None, created_at: datetime,
       acted_at: datetime | None = None, timezone: str | None = "UTC") -> SimpleNamespace:
    return SimpleNamespace(old_rank=old_rank, judged_rank=judged_rank,
                           created_at=created_at, acted_at=acted_at, timezone=timezone)


# ── The agreement rule ──────────────────────────────────────────────────────


class TestTheAgreement:
    def test_equal_values_agree_and_no_judged_value_never_does(self):
        assert fm.agrees("now", "now")
        assert not fm.agrees("now", "later")
        assert not fm.agrees("now", None)
        assert not fm.agrees(None, None)

    def test_the_rate_of_each_feature(self):
        out = fm.build_answer(7, NOW, [
            _v("my_day.triage", "now", "now", 3),
            _v("my_day.triage", "now", "later", 1),
            _v("my_day.triage", "now", None, 4),
            _v("my_day.weight", "high", "high", 2),
        ], [])
        assert out["features"] == {
            "my_day.triage": {"rows": 8, "agree_rate": 0.375},
            "my_day.weight": {"rows": 2, "agree_rate": 1.0},
        }

    def test_a_feature_with_no_rows_is_absent(self):
        out = fm.build_answer(7, NOW, [_v("my_day.weight", "low", "low", 0)], [])
        assert out["features"] == {}

    def test_a_rate_has_four_places(self):
        out = fm.build_answer(7, NOW, [_v("my_day.triage", "a", "a", 1),
                                       _v("my_day.triage", "a", "b", 2)], [])
        assert out["features"]["my_day.triage"]["agree_rate"] == 0.3333


# ── The acted window ────────────────────────────────────────────────────────


class TestTheActedWindow:
    def test_an_act_before_the_judgment_does_not_count(self):
        made = NOW
        assert not fm.acted_in_window(made, made - timedelta(seconds=1), "UTC")

    def test_an_act_at_the_judgment_counts(self):
        assert fm.acted_in_window(NOW, NOW, "UTC")

    def test_an_act_later_the_same_local_day_counts(self):
        assert fm.acted_in_window(NOW, NOW.replace(hour=23, minute=59), "UTC")

    def test_an_act_on_the_next_local_day_does_not_count(self):
        next_day = (NOW + timedelta(days=1)).replace(hour=0, minute=0)
        assert not fm.acted_in_window(NOW, next_day, "UTC")

    def test_the_day_is_the_members_local_date(self):
        # 18:00 UTC is 23:30 in Kolkata, and 18:45 UTC is 00:15 the next day.
        made = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
        later = datetime(2026, 10, 14, 18, 45, tzinfo=UTC)
        assert fm.acted_in_window(made, later, "UTC")
        assert not fm.acted_in_window(made, later, KOLKATA)
        # 18:45 UTC on the 13th is 00:15 on the 14th in Kolkata, and 17:00
        # UTC on the 14th is 22:30 there: two UTC dates, one local date.
        early = datetime(2026, 10, 13, 18, 45, tzinfo=UTC)
        assert fm.acted_in_window(early, datetime(2026, 10, 14, 17, 0, tzinfo=UTC), KOLKATA)

    def test_a_bad_or_missing_zone_reads_as_utc(self):
        made = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
        later = datetime(2026, 10, 14, 18, 45, tzinfo=UTC)
        for zone in (None, "", "Not/AZone"):
            assert fm.acted_in_window(made, later, zone)

    def test_no_act_does_not_count(self):
        assert not fm.acted_in_window(NOW, None, "UTC")

    def test_a_naive_time_reads_as_utc(self):
        naive = NOW.replace(tzinfo=None)
        assert fm.acted_in_window(naive, NOW + timedelta(hours=1), "UTC")
        assert not fm.acted_in_window(NOW, naive - timedelta(seconds=1), "UTC")


# ── The top five of each path ───────────────────────────────────────────────


class TestTheTopFive:
    def test_each_path_counts_its_own_top_five(self):
        acted = NOW + timedelta(hours=1)
        out = fm.build_answer(7, NOW, [], [
            _t(1, 1, NOW, acted),        # both paths, acted
            _t(2, 6, NOW, acted),        # old only, acted
            _t(6, 2, NOW),               # judged only, no act
            _t(5, None, NOW, acted),     # old only (dropped on the judged path)
            _t(3, 4, NOW, NOW - timedelta(minutes=1)),  # acted before: no
            _t(4, 5, NOW, NOW + timedelta(days=1)),     # next day: no
        ])
        assert out["top_five"] == {
            "old": {"cards": 5, "acted": 3, "acted_rate": 0.6},
            "judged": {"cards": 4, "acted": 1, "acted_rate": 0.25},
        }

    def test_a_rank_outside_one_to_five_is_not_in_the_top(self):
        assert [fm.in_top(r) for r in (None, 0, 1, 5, 6)] == [False, False, True, True, False]


# ── Empty tables ────────────────────────────────────────────────────────────


class TestEmptyTables:
    def test_each_rate_is_null_and_nothing_divides_by_zero(self):
        out = fm.build_answer(7, NOW, [], [])
        assert out == {
            "days": 7, "since": NOW.isoformat(), "features": {},
            "top_five": {"old": {"cards": 0, "acted": 0, "acted_rate": None},
                         "judged": {"cards": 0, "acted": 0, "acted_rate": None}},
        }

    def test_rate(self):
        assert fm.rate(0, 0) is None
        assert fm.rate(0, 3) == 0.0
        assert fm.rate(3, 3) == 1.0


# ── The route ───────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeDB:
    def __init__(self) -> None:
        self.values: list[Any] = [_v("my_day.triage", "now", "now", 1)]
        self.top: list[Any] = [_t(1, 1, NOW, NOW + timedelta(minutes=5))]
        self.fail = False
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        sql, p = str(stmt), dict(params or {})
        self.sql.append((sql, p))
        return _Result(self.top if "LEFT JOIN" in sql else self.values)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()
    db.sessions = []

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        db.sessions.append(organization_id)
        yield db

    monkeypatch.setattr(fm, "_tenant_session", _session)
    monkeypatch.setattr(fm, "_now", lambda: NOW)
    return db


def _user(*perms: str, email: str = "ana@alpha.example") -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(perms), roles=["member"]))


def _get(user: UserContext, query: str = ""):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app).get(f"/shell/feed/decide-measure{query}")


class TestTheAdminGate:
    def test_a_member_without_the_admin_permission_gets_403_and_no_read(self, fake):
        res = _get(_user("feature:tasks", "feature:projects"))
        assert res.status_code == 403
        assert fake.sql == [] and fake.sessions == []

    def test_no_session_gets_401(self, fake):
        res = _get(UserContext(email="", role=UserRole.EMPLOYEE,
                               access=build_access([ADMIN_PERMISSION], roles=["admin"])))
        assert res.status_code == 401
        assert fake.sql == []

    def test_an_admin_gets_the_answer(self, fake):
        res = _get(_user(ADMIN_PERMISSION))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["days"] == fm.DEFAULT_DAYS == 7
        assert body["features"] == {"my_day.triage": {"rows": 1, "agree_rate": 1.0}}
        assert body["top_five"]["old"] == {"cards": 1, "acted": 1, "acted_rate": 1.0}


class TestTheTenant:
    def test_the_org_is_the_bound_session_never_the_request(self, fake):
        org = "6a3a6a3a-0000-4000-8000-000000000009"
        res = _get(_user(ADMIN_PERMISSION), f"?organization_id={org}&org={org}")
        assert res.status_code == 200
        # One seam session with no org argument: the bound tenant.
        assert fake.sessions == [None]
        assert len(fake.sql) == 2
        for sql, params in fake.sql:
            assert "current_setting('app.tenant_id', true)" in sql
            assert org not in str(params)
            assert "organization_id" not in params and "org" not in params


class TestTheSpan:
    def test_seven_days_by_default(self, fake):
        _get(_user(ADMIN_PERMISSION))
        assert {p["since"] for _s, p in fake.sql} == {NOW - timedelta(days=7)}

    def test_thirty_days_at_most(self, fake):
        assert _get(_user(ADMIN_PERMISSION), "?days=30").status_code == 200
        assert {p["since"] for _s, p in fake.sql} == {NOW - timedelta(days=30)}
        for bad in ("0", "31", "-1", "x"):
            assert _get(_user(ADMIN_PERMISSION), f"?days={bad}").status_code == 422

    def test_the_reads_name_the_prefix_the_triage_row_and_the_top(self, fake):
        _get(_user(ADMIN_PERMISSION))
        values, top = fake.sql
        assert values[1]["prefix"] == "my_day."
        assert top[1]["triage"] == "my_day.triage" and top[1]["top"] == 5


class TestAFailedRead:
    def test_is_a_503_never_an_empty_answer(self, fake):
        fake.fail = True
        res = _get(_user(ADMIN_PERMISSION))
        assert res.status_code == 503
