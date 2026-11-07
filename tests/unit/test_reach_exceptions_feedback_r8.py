"""WS-BRIEF BR-5 slice 2b-1, R8: the rule and the rating of one alert on a
REAL Postgres (``core_brief.md`` §6 "The rating of an alert", §10 row 12).

Migration 350 adds ``rule_id``, ``feedback`` and ``feedback_at`` to
``reach_signal_log``. This runs the REAL ``gateway.reach.outbox`` on the
phase-4-promoted catalog, as the NON-privileged role with RLS forced
(``_as_member``), as ``test_reach_r8.py`` does. Two tenants, two members.

* **The migration** is found by CONTENT, never by number (R1). It only adds
  three nullable columns with no default, and it keeps the key.
* **The interrupt** writes the ``rule_id`` of its matched rule on its log row.
* **The rating** lands on that row only. A second rating replaces the first.
  Member A's rating never changes a row of member B, nor a row of another
  tenant.
* **A rating only stores.** It mutes no rule and changes no budget.

**Mutations (R7).**

* M1. ``outbox._LOG_SQL``: write NULL for ``rule_id``.
  ``test_an_interrupt_logs_its_rule_id`` goes red.
* M2. ``outbox._RATE_SQL``: drop ``AND member_email = :m``.
  ``test_a_rating_changes_that_row_only`` goes red.
* M3. ``outbox._RATE_SQL``: drop the newest-row subquery.
  ``test_a_rating_lands_on_the_newest_row_of_the_card`` goes red.
* M4. ``outbox._RATE_SQL``: drop ``AND outcome = 'sent'``.
  ``test_a_rating_skips_a_newer_row_that_sent_nothing`` goes red.
* M5. ``outbox._RATE_SQL``: compare ``member_email = :m`` with case.
  ``test_a_mixed_case_address_rates_the_lower_case_row`` goes red.
* M6. ``outbox.log``: unpack ``signal_id, fingerprint, *rest`` again.
  ``test_a_log_item_of_another_shape_is_refused`` goes red.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from gateway.reach import exceptions, outbox, plan
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The catalog, the app role and the binding, shared rather than copied.
from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures used by name)
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"rate-a-{TAG}@br5.test"
B = f"rate-b-{TAG}@br5.test"
NOW = datetime(2099, 3, 10, 12, 0, tzinfo=UTC)

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_MARK = "ADD COLUMN IF NOT EXISTS feedback_at"
_NEW = ("rule_id", "feedback", "feedback_at")


def _wipe(p) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM reach_signal_log WHERE member_email IN (:a, :b)"),
                  {"a": A, "b": B})
        c.execute(text("DELETE FROM user_settings WHERE user_id IN (:a, :b)"),
                  {"a": A, "b": B})


@pytest.fixture(autouse=True)
def _clean_rows(promoted):  # noqa: F811
    _wipe(promoted)
    yield
    _wipe(promoted)


def _card(kind: str, cid: str, at: datetime = NOW) -> dict:
    return {"signal_id": f"{kind}:{cid}", "kind": kind, "app": "app", "title": kind,
            "at": at.isoformat(), "facts": {}, "folded": [], "state": "new",
            "group": "needs_you", "score": 50, "fingerprint": "fp", "why": "Why."}


def _rows(p, member: str) -> list[dict]:
    with p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT organization_id::text AS org, signal_id, fingerprint, outcome, "
            "rule_id, feedback, feedback_at FROM reach_signal_log "
            "WHERE member_email = :m ORDER BY organization_id, signal_id, created_at"),
            {"m": member})
        return [dict(r._mapping) for r in got]


async def _interrupt(p, org: str, member: str, cards: list[dict]) -> list[dict]:
    """What the loop does: the rule engine picks, and the log names the rule."""
    got = plan.interrupt_candidates(cards, set(), NOW, rules=True)
    async with _as_member(p, org):
        await outbox.log(org, member, map(plan.entry, got), "sent")
    return got


class TestTheMigration:
    def test_found_by_content_and_it_only_adds_nullable_columns(self, promoted):  # noqa: F811
        found = [f for f in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if _MARK in f.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        body = found[0].read_text(encoding="utf-8")
        sql = "\n".join(line.split("--", 1)[0] for line in body.splitlines())
        assert not re.search(r"\b(DROP|RENAME)\b", sql, re.I)
        adds = re.findall(r"ADD COLUMN IF NOT EXISTS (\w+) ([^,;\n]+)", sql)
        assert {name for name, _ in adds} == set(_NEW)
        for _, rest in adds:
            assert "NOT NULL" not in rest.upper() and "DEFAULT" not in rest.upper()
        with promoted.admin_engine.connect() as c:
            cols = {r[0]: (r[1], r[2]) for r in c.execute(text(
                "SELECT column_name, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_name = 'reach_signal_log' "
                "AND column_name IN ('rule_id', 'feedback', 'feedback_at')"))}
            keys = set(c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'reach_signal_log'::regclass AND contype = 'p'")).scalars())
            rls = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'reach_signal_log'::regclass")).one()
        assert cols == {name: ("YES", None) for name in _NEW}
        assert keys == {"PRIMARY KEY (organization_id, member_email, signal_id, fingerprint)"}
        assert tuple(rls) == (True, True)

    def test_an_unknown_rating_is_refused(self, promoted):  # noqa: F811
        with (pytest.raises(DBAPIError, match="reach_signal_log_feedback_known"),
              promoted.admin_engine.begin() as c):
            c.execute(text(
                "INSERT INTO reach_signal_log (organization_id, member_email, signal_id, "
                "fingerprint, outcome, feedback) VALUES "
                "(CAST(:o AS uuid), :m, 's', 'f', 'sent', 'great')"),
                {"o": promoted.org_a, "m": A})

    def test_the_check_holds_the_ratings_of_the_code(self, promoted):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            check = c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'reach_signal_log_feedback_known'")).scalar_one()
        assert set(re.findall(r"'(\w+)'", check)) == set(exceptions.FEEDBACK)


class TestTheRuleOfAnAlert:
    async def test_an_interrupt_logs_its_rule_id(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1"),
                                         _card("source_sync_failed", "2"),
                                         _card("notify_me", "3")])
        got = {r["signal_id"]: r["rule_id"] for r in _rows(p, A)}
        assert got == {"agent_asks:1": "agent_asks",
                       "source_sync_failed:2": "source_sync_failed",
                       "notify_me:3": None}

    async def test_a_plain_pair_still_logs_with_no_rule(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            await outbox.log(p.org_a, A, [("x:1", "fp")], "baseline")
        assert [(r["signal_id"], r["rule_id"]) for r in _rows(p, A)] == [("x:1", None)]


class TestTheRating:
    async def test_a_rating_changes_that_row_only(self, promoted):  # noqa: F811
        p = promoted
        cards = [_card("agent_asks", "1"), _card("source_sync_failed", "2")]
        for org, member in ((p.org_a, A), (p.org_a, B), (p.org_b, A)):
            await _interrupt(p, org, member, cards)
        async with _as_member(p, p.org_a):
            assert await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                           "not_useful")
        rated = [(r["org"], r["signal_id"]) for r in _rows(p, A) if r["feedback"]]
        assert rated == [(p.org_a, "agent_asks:1")]
        row = next(r for r in _rows(p, A) if r["feedback"])
        assert row["feedback"] == "not_useful" and row["feedback_at"] is not None
        assert all(r["feedback"] is None and r["feedback_at"] is None
                   for r in _rows(p, B))

    async def test_another_tenant_cannot_rate_the_row(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1")])
        async with _as_member(p, p.org_b):
            assert not await outbox.rate_alert(p.org_b, A, "agent_asks:1", "agent_asks",
                                               "useful")
        assert [r["feedback"] for r in _rows(p, A)] == [None]

    async def test_a_second_rating_replaces_the_first(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1")])
        async with _as_member(p, p.org_a):
            assert await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                           "useful")
        first = _rows(p, A)[0]
        async with _as_member(p, p.org_a):
            assert await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                           "not_useful")
        rows = _rows(p, A)
        assert len(rows) == 1
        assert rows[0]["feedback"] == "not_useful"
        assert rows[0]["feedback_at"] >= first["feedback_at"]

    async def test_a_rating_of_another_rule_or_card_stores_nothing(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1")])
        async with _as_member(p, p.org_a):
            assert not await outbox.rate_alert(p.org_a, A, "agent_asks:1",
                                               "source_sync_failed", "useful")
            assert not await outbox.rate_alert(p.org_a, A, "agent_asks:9", "agent_asks",
                                               "useful")
        assert [r["feedback"] for r in _rows(p, A)] == [None]

    async def test_a_rating_lands_on_the_newest_row_of_the_card(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1")])
        with p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE reach_signal_log SET created_at = now() - interval '1 day' "
                "WHERE member_email = :m"), {"m": A})
        # A new reason to reach on the same card: a new fingerprint, a new row.
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1", NOW + timedelta(hours=1))])
        async with _as_member(p, p.org_a):
            assert await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                           "useful")
        rows = _rows(p, A)
        assert [r["feedback"] for r in rows] == [None, "useful"]

    async def test_a_rating_skips_a_newer_row_that_sent_nothing(self, promoted):  # noqa: F811
        """Reach sent the card at F1. The agent asked again at F2, and the
        cap held that one back. A tap on the F1 message rates F1, never F2."""
        p = promoted
        async with _as_member(p, p.org_a):
            await outbox.log(p.org_a, A, [("agent_asks:1", "F1", "agent_asks")], "sent")
        with p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE reach_signal_log SET created_at = now() - interval '1 day' "
                "WHERE member_email = :m"), {"m": A})
        async with _as_member(p, p.org_a):
            await outbox.log(p.org_a, A, [("agent_asks:1", "F2", "agent_asks")], "capped")
            assert await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                           "useful")
        got = {r["fingerprint"]: (r["outcome"], r["feedback"]) for r in _rows(p, A)}
        assert got == {"F1": ("sent", "useful"), "F2": ("capped", None)}

    async def test_a_card_that_never_went_out_takes_no_rating(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            for fp, outcome in (("F1", "quiet"), ("F2", "capped"), ("F3", "deferred")):
                await outbox.log(p.org_a, A, [("agent_asks:1", fp, "agent_asks")], outcome)
            assert not await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                               "useful")
        assert [r["feedback"] for r in _rows(p, A)] == [None, None, None]

    async def test_a_mixed_case_address_rates_the_lower_case_row(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1")])
        await _interrupt(p, p.org_a, B, [_card("agent_asks", "1")])
        async with _as_member(p, p.org_a):
            assert await outbox.rate_alert(p.org_a, A.upper(), "agent_asks:1",
                                           "agent_asks", "not_useful")
        assert [r["feedback"] for r in _rows(p, A)] == ["not_useful"]
        assert [r["feedback"] for r in _rows(p, B)] == [None]

    async def test_a_rating_mutes_no_rule_and_changes_no_budget(self, promoted):  # noqa: F811
        p = promoted
        await _interrupt(p, p.org_a, A, [_card("agent_asks", "1")])
        since = datetime.now(UTC) - timedelta(days=1)
        async with _as_member(p, p.org_a):
            before = await outbox.interrupts_since(p.org_a, A, since)
            assert await outbox.rate_alert(p.org_a, A, "agent_asks:1", "agent_asks",
                                           "not_useful")
            assert await outbox.muted_rules(p.org_a, A) == frozenset()
            assert await outbox.interrupts_since(p.org_a, A, since) == before
            logged = await outbox.logged_pairs(p.org_a, A)
        with p.admin_engine.connect() as c:
            stored = c.execute(text(
                "SELECT count(*) FROM user_settings WHERE user_id = :m"),
                {"m": A}).scalar()
        assert stored == 0
        assert _rows(p, A)[0]["outcome"] == "sent"
        # The same rule still lets a new card of the kind through.
        got = plan.interrupt_candidates([_card("agent_asks", "2")], logged, NOW, rules=True)
        assert [c["signal_id"] for c in got] == ["agent_asks:2"]


class TestTheLogShape:
    @pytest.mark.parametrize("item", [
        "ab",
        "abc",
        ("x:1",),
        ("x:1", "fp", "agent_asks", "extra"),
        ["x:1", "fp"],
        ("x:1", 7),
        (None, "fp"),
        ("x:1", "fp", 3),
    ])
    async def test_a_log_item_of_another_shape_is_refused(self, promoted, item):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            with pytest.raises(ValueError):
                await outbox.log(p.org_a, A, [("x:0", "fp"), item], "sent")
        # The check runs before any SQL, so the good item did not log either.
        assert _rows(p, A) == []

    async def test_a_pair_and_a_triple_with_no_rule_still_log(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            assert await outbox.log(p.org_a, A, [("x:1", "fp"), ("x:2", "fp", None)],
                                    "baseline") == 2
        assert [(r["signal_id"], r["rule_id"]) for r in _rows(p, A)] == [
            ("x:1", None), ("x:2", None)]
