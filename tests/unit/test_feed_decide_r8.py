"""WS-58 MD-6a-1, R8: ``my_day_judgments`` on a REAL Postgres
(``my_day_feed.md`` §13.2, MD-6a-1 done-when 3).

``my_day_judgments`` is new, so a hermetic fake would agree with any SQL it
is handed. This runs the REAL ``feed_decide.write_rows`` and
``feed_decide.read_context`` on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``, as the NON-privileged role with RLS
forced (``_as_member``), as ``test_my_day_state_r8.py`` does. The facade and
the Redis are fakes from ``test_feed_decide.py``. No test reaches a network.

* **One row for each comparison**, through a whole pass.
* **RLS isolation across two orgs.** Org B's role reads none of org A's
  rows, and WITH CHECK refuses a row stamped with org A from org B.
* **No tenant text** in any column.
* **The context reads**: the corrections, the habit counts and the One
  Thing, each through its real SQL.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common import tenant_redis
from acb_common.tenant_redis import TenantRedis
from gateway.db import tenant_session
from gateway.routes.shell import feed, feed_decide
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_feed_decide import (  # noqa: F401  (fixtures used by name)
    ALL_SHADOW,
    NOW,
    SECRET_NAME,
    SECRET_PROJECT,
    SECRET_SUBJECT,
    SECRET_TITLE,
    Cards,
    FakeDecide,
    FakeRedis,
    _card,
    _clean,
    _clear,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"judge-a-{TAG}@md6a.test"
B = f"judge-b-{TAG}@md6a.test"

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _user(email: str, org: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset({"feature:projects"})))


@pytest.fixture(autouse=True)
def _clean_rows(promoted):  # noqa: F811
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_judgments WHERE member_email IN (:a, :b)"),
                  {"a": A, "b": B})
    yield
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM my_day_judgments WHERE member_email IN (:a, :b)"),
                  {"a": A, "b": B})


def _rows(p, member: str) -> list[dict]:
    with p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT organization_id::text AS org, member_email, signal_id, fingerprint, "
            "feature, mode, old_value, judged_value, confidence, used, request_id, "
            "old_rank, judged_rank, created_at FROM my_day_judgments "
            "WHERE member_email = :m ORDER BY signal_id, feature"), {"m": member})
        return [dict(r._mapping) for r in got]


def _row(sid: str = "tasks:1", feature: str = "my_day.triage") -> dict:
    return {"signal_id": sid, "fingerprint": "fp000001", "feature": feature,
            "mode": "shadow", "old_value": "needs_you", "judged_value": "later",
            "confidence": 0.7, "used": False, "request_id": "req-1",
            "old_rank": 1, "judged_rank": 2}


class TestTheTable:
    def test_found_by_content_with_forced_rls_a_key_and_an_index(self, promoted):  # noqa: F811
        found = [f.name for f in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if "CREATE TABLE IF NOT EXISTS my_day_judgments"
                 in f.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        with promoted.admin_engine.connect() as c:
            rls = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'my_day_judgments'::regclass")).one()
            key = c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'my_day_judgments'::regclass AND contype = 'p'")).scalar()
            index = c.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'my_day_judgments' "
                "AND indexname = 'my_day_judgments_org_created_idx'")).scalar()
        assert tuple(rls) == (True, True)
        assert key == ("PRIMARY KEY (organization_id, member_email, signal_id, feature, "
                       "created_at)")
        assert index and "(organization_id, created_at)" in index

    def test_the_mode_check_refuses_an_unknown_mode(self, promoted):  # noqa: F811
        with (pytest.raises(DBAPIError, match="my_day_judgments_mode_known"),
              promoted.admin_engine.begin() as c):
            c.execute(text(
                "INSERT INTO my_day_judgments (organization_id, member_email, signal_id, "
                "fingerprint, feature, mode, used) VALUES (CAST(:o AS uuid), :m, 's', "
                "'f', 'my_day.triage', 'live', false)"), {"o": promoted.org_a, "m": A})


class TestOneRowForEachComparison:
    async def test_a_whole_pass_writes_its_rows_in_the_bound_org(
            self, promoted, monkeypatch):  # noqa: F811
        p = promoted
        fake_redis = FakeRedis()
        monkeypatch.setattr(tenant_redis, "get_tenant_redis",
                            lambda **_: TenantRedis(fake_redis))
        monkeypatch.setenv("DECIDE_FEATURE_MODES", ALL_SHADOW)
        monkeypatch.setenv("MY_DAY_DECIDE_ORGS", p.org_b)
        _clear()
        import acb_llm

        fake = FakeDecide(choice={"triage": "drop", "next_act": "move-tomorrow"})
        monkeypatch.setattr(acb_llm, "decide", fake)
        cards = [_card("tasks:1", score=90),
                 _card("calendar:2026-10-14", kind="no_plan_yet", app="calendar",
                       group="later", score=20, acts=[], facts={"day": "2026-10-14"})]
        monkeypatch.setattr(feed, "feed_cards", Cards(cards))

        async def _ctx(user, now, day):
            return feed_decide.MemberContext(local_date="2026-10-14", role="employee")

        monkeypatch.setattr(feed_decide, "read_context", _ctx)
        async with _as_member(p, p.org_b):
            out = await feed_decide.judge(_user(A, p.org_b), NOW)
        assert out.reason == "asked" and out.rows == 6, out
        got = _rows(p, A)
        assert {r["org"] for r in got} == {p.org_b}
        assert [(r["signal_id"], r["feature"]) for r in got] == [
            ("calendar:2026-10-14", "my_day.resurface"),
            ("calendar:2026-10-14", "my_day.triage"),
            ("calendar:2026-10-14", "my_day.weight"),
            ("tasks:1", "my_day.next_act"),
            ("tasks:1", "my_day.triage"),
            ("tasks:1", "my_day.weight")]
        by = {(r["signal_id"], r["feature"]): r for r in got}
        task = by[("tasks:1", "my_day.triage")]
        assert (task["old_value"], task["judged_value"], task["used"], task["mode"]) == (
            "needs_you", "drop", False, "shadow")
        # §7 rule 2: the needs card keeps a place on the judged path.
        assert task["old_rank"] == 1 and task["judged_rank"] == 1
        cal = by[("calendar:2026-10-14", "my_day.triage")]
        assert cal["old_rank"] == 2 and cal["judged_rank"] is None  # dropped
        assert by[("tasks:1", "my_day.weight")]["old_rank"] is None
        assert by[("tasks:1", "my_day.weight")]["judged_value"] == "high"
        assert by[("tasks:1", "my_day.next_act")]["judged_value"] == "move-tomorrow"
        assert by[("calendar:2026-10-14", "my_day.resurface")]["judged_value"] == "true"
        assert {r["request_id"] for r in got} == {"req-1"}
        assert all(r["confidence"] is not None for r in got)
        assert len({r["created_at"] for r in got}) == 1  # one transaction

    async def test_a_second_pass_in_another_transaction_adds_rows(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_b):
            await feed_decide.write_rows(A, [_row()])
            await feed_decide.write_rows(A, [_row()])
        assert len(_rows(p, A)) == 2


class TestTheTenancyBoundary:
    async def test_org_b_reads_none_of_org_a(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            await feed_decide.write_rows(A, [_row("tasks:a-only")])
        async with _as_member(p, p.org_b):
            await feed_decide.write_rows(B, [_row("tasks:b-only")])
            async with tenant_session() as db:
                seen = (await db.execute(text(
                    "SELECT signal_id FROM my_day_judgments "
                    "WHERE member_email IN (:a, :b)"), {"a": A, "b": B})).scalars().all()
        assert seen == ["tasks:b-only"]
        assert {r["org"] for r in _rows(p, A)} == {p.org_a}

    async def test_with_check_refuses_a_row_stamped_with_another_org(self, promoted):  # noqa: F811
        p = promoted
        with pytest.raises(DBAPIError, match="row-level security"):
            async with _as_member(p, p.org_b), tenant_session() as db:
                await db.execute(text(
                    "INSERT INTO my_day_judgments (organization_id, member_email, "
                    "signal_id, fingerprint, feature, mode, used) VALUES "
                    "(CAST(:o AS uuid), :m, 's', 'f', 'my_day.triage', 'shadow', false)"),
                    {"o": p.org_a, "m": B})
        assert _rows(p, B) == []


class TestNoTenantText:
    async def test_no_column_holds_a_title_a_subject_or_a_name(self, promoted, monkeypatch):  # noqa: F811
        p = promoted
        fake_redis = FakeRedis()
        monkeypatch.setattr(tenant_redis, "get_tenant_redis",
                            lambda **_: TenantRedis(fake_redis))
        monkeypatch.setenv("DECIDE_FEATURE_MODES", ALL_SHADOW)
        monkeypatch.setenv("MY_DAY_DECIDE_ORGS", "*")
        _clear()
        import acb_llm

        monkeypatch.setattr(acb_llm, "decide", FakeDecide())
        mail = _card("email:acc-1:thr-1", kind="needs_reply", app="email",
                     title=SECRET_SUBJECT,
                     facts={"who": SECRET_NAME, "account": "acc-1", "message_id": "m-1"},
                     acts=[{"id": "open-thread", "label": "Open thread", "way": "open",
                            "href": "/email?thread=thr-1"}])
        monkeypatch.setattr(feed, "feed_cards", Cards([_card("tasks:9"), mail]))

        async def _ctx(user, now, day):
            return feed_decide.MemberContext(local_date="2026-10-14", role="employee",
                                             corrections=(SECRET_NAME,))

        monkeypatch.setattr(feed_decide, "read_context", _ctx)
        async with _as_member(p, p.org_b):
            out = await feed_decide.judge(_user(A, p.org_b), NOW)
        assert out.rows > 0
        blob = json.dumps(_rows(p, A), default=str)
        for secret in (SECRET_TITLE, SECRET_SUBJECT, SECRET_NAME, SECRET_PROJECT):
            assert secret not in blob


class TestTheContextReads:
    @pytest.fixture
    def member(self, promoted):  # noqa: F811
        p = promoted
        email = f"ctx-{TAG}@md6a.test"
        task = str(uuid.uuid4())
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, my_day_prefs, organization_id) "
                "VALUES (:e, 'UTC', CAST(:prefs AS jsonb), CAST(:o AS uuid))"),
                {"e": email, "o": p.org_b,
                 "prefs": json.dumps({"corrections": ["Newsletters never need me"]})})
            c.execute(text(
                "INSERT INTO calendar_day_state (user_id, day, one_thing_id, organization_id) "
                "VALUES (:e, :d, CAST(:t AS uuid), CAST(:o AS uuid))"),
                {"e": email, "d": NOW.date(), "t": task, "o": p.org_b})
            for sid, kind, acted, nu, age in (
                    ("tasks:h1", "overdue", True, False, 1),
                    ("tasks:h2", "overdue", False, True, 2),
                    ("tasks:h3", "overdue", False, False, 3),
                    ("tasks:h4", "due_today", False, False, 40)):
                c.execute(text(
                    "INSERT INTO my_day_card_state (organization_id, member_email, signal_id, "
                    "kind, state, acted_at, not_useful_at, updated_at) VALUES "
                    "(CAST(:o AS uuid), :m, :s, :k, 'seen', "
                    "CASE WHEN :acted THEN now() END, CASE WHEN :nu THEN now() END, "
                    "now() - make_interval(days => :age))"),
                    {"o": p.org_b, "m": email, "s": sid, "k": kind, "acted": acted,
                     "nu": nu, "age": age})
            # A row of another org, for the same address, that RLS must hide.
            c.execute(text(
                "INSERT INTO my_day_card_state (organization_id, member_email, signal_id, "
                "kind, state) VALUES (CAST(:o AS uuid), :m, 'tasks:other', 'overdue', "
                "'seen')"), {"o": p.org_a, "m": email})
        yield SimpleNamespace(email=email, task=task)
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM my_day_card_state WHERE member_email = :e"),
                      {"e": email})
            c.execute(text("DELETE FROM calendar_day_state WHERE user_id = :e"), {"e": email})
            c.execute(text("DELETE FROM user_settings WHERE user_id = :e"), {"e": email})

    async def test_the_corrections_the_habits_and_the_one_thing(self, promoted, member):  # noqa: F811
        p = promoted
        day = SimpleNamespace(tz=__import__("zoneinfo").ZoneInfo("UTC"), start_hour=7,
                              end_hour=22)
        async with _as_member(p, p.org_b):
            ctx = await feed_decide.read_context(_user(member.email, p.org_b), NOW, day)
        assert ctx.corrections == ("Newsletters never need me",)
        assert ctx.one_thing == member.task
        assert ctx.habits == {"overdue": {"shown": 3, "acted": 1, "not_useful": 1}}
        assert ctx.local_date == NOW.date().isoformat() and ctx.day_hours == "07-22"
        assert ctx.local_time == (NOW).strftime("%H:%M")
