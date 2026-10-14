"""``GET /shell/feed/decide-measure`` — the hermetic half (WS-58 MD-6a-3).

Spec: ``project-docs/specs/my_day_feed.md`` §10.9 item 10 and §16 MD-6a-3.
The R8 half, against a real Postgres, is ``test_feed_measure_r8.py``. It
proves the SQL: the acted window and its edges, the zone, the joins, the
mode split and the tenant wall. This file holds what a fake can prove
honestly:

- the agreement rule: a row with no judged value never agrees;
- the pairs of each feature and mode, so a gate for resurface can read them;
- the mode split: ``shadow`` and ``on`` never mix;
- empty tables, and an org with no act: each rate is ``None``, never ``0``
  and never a division error;
- the admin gate: 403 for a member, 401 for no session, and no read before
  the gate;
- the statement limit, first in the read's transaction;
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
NULL_PATH = {"cards": 0, "acted": 0, "acted_rate": None}


def _v(feature: str, old: str | None, judged: str | None, n: int,
       mode: str = "shadow") -> SimpleNamespace:
    return SimpleNamespace(mode=mode, feature=feature, old_value=old, judged_value=judged, n=n)


def _t(mode: str, old_cards: int, old_acted: int, judged_cards: int,
       judged_acted: int) -> SimpleNamespace:
    return SimpleNamespace(mode=mode, old_cards=old_cards, old_acted=old_acted,
                           judged_cards=judged_cards, judged_acted=judged_acted)


# ── The agreement rule and the pairs ────────────────────────────────────────


class TestTheAgreement:
    def test_equal_values_agree_and_no_judged_value_never_does(self):
        assert fm.agrees("now", "now")
        assert not fm.agrees("now", "later")
        assert not fm.agrees("now", None)
        assert not fm.agrees(None, None)

    def test_the_rate_and_the_pairs_of_each_feature(self):
        out = fm.build_answer(7, NOW, [
            _v("my_day.triage", "now", "now", 3),
            _v("my_day.triage", "now", "later", 1),
            _v("my_day.triage", "now", None, 4),
            _v("my_day.weight", "high", "high", 2),
        ], [], 1)
        assert out["features"] == {
            "my_day.triage": {"shadow": {"rows": 8, "agree_rate": 0.375, "pairs": [
                {"old": "now", "judged": None, "rows": 4},
                {"old": "now", "judged": "now", "rows": 3},
                {"old": "now", "judged": "later", "rows": 1},
            ]}},
            "my_day.weight": {"shadow": {"rows": 2, "agree_rate": 1.0, "pairs": [
                {"old": "high", "judged": "high", "rows": 2}]}},
        }

    def test_resurface_reads_its_pairs_because_its_agree_rate_is_the_false_share(self):
        out = fm.build_answer(7, NOW, [
            _v("my_day.resurface", "false", "false", 98),
            _v("my_day.resurface", "false", "true", 2),
        ], [], 1)
        got = out["features"]["my_day.resurface"]["shadow"]
        assert got["agree_rate"] == 0.98
        assert got["pairs"] == [{"old": "false", "judged": "false", "rows": 98},
                                {"old": "false", "judged": "true", "rows": 2}]

    def test_a_feature_with_no_rows_is_absent(self):
        out = fm.build_answer(7, NOW, [_v("my_day.weight", "low", "low", 0)], [], 1)
        assert out["features"] == {}

    def test_a_rate_has_four_places(self):
        out = fm.build_answer(7, NOW, [_v("my_day.triage", "a", "a", 1),
                                       _v("my_day.triage", "a", "b", 2)], [], 1)
        assert out["features"]["my_day.triage"]["shadow"]["agree_rate"] == 0.3333


# ── The mode split ──────────────────────────────────────────────────────────


class TestTheModeSplit:
    def test_shadow_and_on_rows_never_mix_in_a_feature(self):
        out = fm.build_answer(7, NOW, [
            _v("my_day.triage", "now", "now", 3, "shadow"),
            _v("my_day.triage", "now", "later", 1, "shadow"),
            _v("my_day.triage", "now", "later", 5, "on"),
        ], [], 1)
        got = out["features"]["my_day.triage"]
        assert set(got) == {"shadow", "on"}
        assert (got["shadow"]["rows"], got["shadow"]["agree_rate"]) == (4, 0.75)
        assert (got["on"]["rows"], got["on"]["agree_rate"]) == (5, 0.0)

    def test_shadow_and_on_rows_never_mix_in_the_top_five(self):
        out = fm.build_answer(7, NOW, [], [_t("shadow", 10, 2, 8, 4),
                                           _t("on", 6, 1, 6, 5)], 7)
        assert out["top_five"] == {
            "shadow": {"old": {"cards": 10, "acted": 2, "acted_rate": 0.2},
                       "judged": {"cards": 8, "acted": 4, "acted_rate": 0.5}},
            "on": {"old": {"cards": 6, "acted": 1, "acted_rate": 0.1667},
                   "judged": {"cards": 6, "acted": 5, "acted_rate": 0.8333}},
        }

    def test_both_modes_are_always_in_the_top_five(self):
        out = fm.build_answer(7, NOW, [], [_t("shadow", 2, 1, 2, 0)], 3)
        assert out["top_five"]["on"] == {"old": NULL_PATH, "judged": NULL_PATH}


# ── Empty tables, and an org with no act ────────────────────────────────────


class TestEmptyTables:
    def test_each_rate_is_null_and_nothing_divides_by_zero(self):
        out = fm.build_answer(7, NOW, [], [], 0)
        assert out == {
            "days": 7, "since": NOW.isoformat(), "acts": 0, "features": {},
            "top_five": {m: {"old": NULL_PATH, "judged": NULL_PATH} for m in fm.MODES},
        }

    def test_an_org_with_no_act_has_null_acted_rates_not_zero(self):
        # NEXT_PUBLIC_MY_DAY_FEED off: the pass judges cards, and nobody can
        # act on one, so 0.0 against 0.0 would read as a tie.
        out = fm.build_answer(7, NOW, [], [_t("shadow", 10, 0, 9, 0)], 0)
        assert out["top_five"]["shadow"] == {
            "old": {"cards": 10, "acted": 0, "acted_rate": None},
            "judged": {"cards": 9, "acted": 0, "acted_rate": None}}

    def test_an_org_with_acts_has_a_real_zero(self):
        out = fm.build_answer(7, NOW, [], [_t("shadow", 10, 0, 9, 0)], 4)
        assert out["top_five"]["shadow"]["old"]["acted_rate"] == 0.0

    def test_rate(self):
        assert fm.rate(0, 0) is None
        assert fm.rate(0, 3) == 0.0
        assert fm.rate(3, 3) == 1.0


# ── The route ───────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def all(self) -> Any:
        return self._rows

    def scalar_one(self) -> Any:
        return self._rows


class _FakeDB:
    def __init__(self) -> None:
        self.values: list[Any] = [_v("my_day.triage", "now", "now", 1)]
        self.top: list[Any] = [_t("shadow", 1, 1, 1, 0)]
        self.acts = 2
        self.fail = False
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("canceling statement due to statement timeout")
        sql, p = str(stmt), dict(params or {})
        self.sql.append((sql, p))
        if "set_config" in sql:
            return _Result(None)
        if "pg_timezone_names" in sql:
            return _Result(self.top)
        if "FROM my_day_card_state WHERE" in sql:
            return _Result(self.acts)
        return _Result(self.values)


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


def _reads(db: _FakeDB) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in db.sql if "set_config" not in s]


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
        assert body["acts"] == 2
        assert body["features"]["my_day.triage"]["shadow"]["agree_rate"] == 1.0
        assert body["top_five"]["shadow"]["old"] == {"cards": 1, "acted": 1,
                                                     "acted_rate": 1.0}


class TestTheStatementLimit:
    def test_the_limit_is_set_first_in_the_reads_transaction(self, fake):
        _get(_user(ADMIN_PERMISSION))
        first, params = fake.sql[0]
        assert "set_config('statement_timeout', :ms, true)" in first
        assert params == {"ms": str(fm.STATEMENT_TIMEOUT_MS)}
        assert fm.STATEMENT_TIMEOUT_MS == 5000
        assert len(_reads(fake)) == 3

    def test_a_read_that_runs_out_of_time_is_a_503(self, fake):
        fake.fail = True
        assert _get(_user(ADMIN_PERMISSION)).status_code == 503


class TestTheTenant:
    def test_the_org_is_the_bound_session_never_the_request(self, fake):
        org = "6a3a6a3a-0000-4000-8000-000000000009"
        res = _get(_user(ADMIN_PERMISSION), f"?organization_id={org}&org={org}")
        assert res.status_code == 200
        # One seam session with no org argument: the bound tenant.
        assert fake.sessions == [None]
        reads = _reads(fake)
        assert len(reads) == 3
        for sql, params in reads:
            assert "current_setting('app.tenant_id', true)" in sql
            assert org not in str(params)
            assert "organization_id" not in params and "org" not in params


class TestTheSpan:
    def test_seven_days_by_default(self, fake):
        _get(_user(ADMIN_PERMISSION))
        assert {p["since"] for _s, p in _reads(fake)} == {NOW - timedelta(days=7)}

    def test_thirty_days_at_most(self, fake):
        assert _get(_user(ADMIN_PERMISSION), "?days=30").status_code == 200
        assert {p["since"] for _s, p in _reads(fake)} == {NOW - timedelta(days=30)}
        for bad in ("0", "31", "-1", "x"):
            assert _get(_user(ADMIN_PERMISSION), f"?days={bad}").status_code == 422

    def test_the_reads_name_the_prefix_the_triage_row_and_the_top(self, fake):
        _get(_user(ADMIN_PERMISSION))
        values, top, _acts = _reads(fake)
        assert values[1]["prefix"] == "my_day."
        assert top[1]["triage"] == "my_day.triage" and top[1]["top"] == 5
        assert "GROUP BY mode" in values[0] and "GROUP BY mode" in top[0]


class TestAFailedRead:
    def test_is_a_503_never_an_empty_answer(self, fake):
        fake.fail = True
        res = _get(_user(ADMIN_PERMISSION))
        assert res.status_code == 503
