"""WS-59 RE-1a, R8: ``reach_outbox`` and ``reach_signal_log`` on a REAL Postgres
(``proactive_reach.md`` §7).

Both tables are new, so a hermetic fake would agree with any SQL it is
handed. This runs the REAL ``gateway.reach.outbox`` and the two reads of
``gateway.reach.whatsapp`` on the phase-4-promoted catalog, as the
NON-privileged role with RLS forced (``_as_member``), as
``test_feed_decide_r8.py`` does. No test reaches a network.

* **The tables**: found by content, RLS forced, the dedupe key and the checks.
* **The outbox dedupe**: one key plans once in an org, and another org may
  hold the same key.
* **RLS isolation across two orgs** for both tables, and WITH CHECK refuses a
  row stamped with another org.
* **The reads**: the log, the budget, the brief guard, the link and the
  24-hour window.
* **RE-2a**: the full-brief guard, the reach state, and the display name of
  the template greeting, each bound to its org.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from gateway.db import tenant_session
from gateway.reach import outbox
from gateway.reach import whatsapp as channel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The catalog, the app role and the binding, shared rather than copied.
from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures used by name)
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"reach-a-{TAG}@re1a.test"
B = f"reach-b-{TAG}@re1a.test"
PHONE = "91" + str(uuid.uuid4().int)[:10]

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _wipe(p) -> None:
    with p.admin_engine.begin() as c:
        for table in ("reach_outbox", "reach_signal_log", "whatsapp_bot_messages",
                      "whatsapp_member_links"):
            c.execute(text(f"DELETE FROM {table} WHERE member_email IN (:a, :b)"),
                      {"a": A, "b": B})
        c.execute(text("DELETE FROM app_user WHERE email IN (:a, :b)"), {"a": A, "b": B})


@pytest.fixture(autouse=True)
def _clean_rows(promoted):  # noqa: F811
    _wipe(promoted)
    yield
    _wipe(promoted)


def _outbox_rows(p, member: str) -> list[dict]:
    with p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT organization_id::text AS org, kind, state, regime, reason, "
            "provider_message_id, sent_at, body FROM reach_outbox WHERE member_email = :m "
            "ORDER BY created_at"), {"m": member})
        return [dict(r._mapping) for r in got]


class TestTheTables:
    @pytest.mark.parametrize("table", ["reach_outbox", "reach_signal_log"])
    def test_found_by_content_with_forced_rls(self, promoted, table):  # noqa: F811
        found = [f.name for f in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if f"CREATE TABLE IF NOT EXISTS {table}" in f.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        with promoted.admin_engine.connect() as c:
            rls = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                f"WHERE oid = '{table}'::regclass")).one()
        assert tuple(rls) == (True, True)

    def test_the_keys(self, promoted):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            keys = set(c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN "
                "('reach_outbox'::regclass, 'reach_signal_log'::regclass) "
                "AND contype IN ('p', 'u')")).scalars())
        assert "UNIQUE (organization_id, dedupe_key)" in keys
        assert "PRIMARY KEY (organization_id, member_email, signal_id, fingerprint)" in keys

    def test_an_unknown_outcome_is_refused(self, promoted):  # noqa: F811
        with (pytest.raises(DBAPIError, match="reach_signal_log_outcome_known"),
              promoted.admin_engine.begin() as c):
            c.execute(text(
                "INSERT INTO reach_signal_log (organization_id, member_email, signal_id, "
                "fingerprint, outcome) VALUES (CAST(:o AS uuid), :m, 's', 'f', 'loud')"),
                {"o": promoted.org_a, "m": A})


class TestTheOutbox:
    async def test_one_key_plans_once_and_finishes_once(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            first = await outbox.plan(p.org_a, A, kind="interrupt", signal_ids=["email:x:1"],
                                      key="interrupt:k1", body="hello", local_date=date.today())
            again = await outbox.plan(p.org_a, A, kind="interrupt", signal_ids=["email:x:1"],
                                      key="interrupt:k1", body="hello", local_date=date.today())
            assert first is not None and again is None
            assert await outbox.finish(p.org_a, first, state="sent", regime="free_text",
                                       provider_message_id="wamid.1")
            assert not await outbox.finish(p.org_a, first, state="failed")
            since = datetime.now(UTC) - timedelta(hours=1)
            assert await outbox.interrupts_since(p.org_a, A, since) == 1
        rows = _outbox_rows(p, A)
        assert len(rows) == 1
        assert (rows[0]["org"], rows[0]["state"], rows[0]["regime"]) == (
            p.org_a, "sent", "free_text")
        assert rows[0]["sent_at"] is not None and rows[0]["provider_message_id"] == "wamid.1"

    async def test_another_org_may_hold_the_same_key(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            in_a = await outbox.plan(p.org_a, A, kind="brief", signal_ids=[], key="brief:same",
                                     body="a", local_date=date(2026, 10, 15))
        async with _as_member(p, p.org_b):
            in_b = await outbox.plan(p.org_b, B, kind="brief", signal_ids=[], key="brief:same",
                                     body="b", local_date=date(2026, 10, 15))
            assert await outbox.brief_planned(p.org_b, B, date(2026, 10, 15))
            assert not await outbox.brief_planned(p.org_b, B, date(2026, 10, 16))
        assert in_a and in_b and in_a != in_b

    async def test_the_log_keeps_its_first_outcome(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            assert not await outbox.has_log(p.org_a, A)
            await outbox.log(p.org_a, A, [("email:x:1", "fp1")], "baseline")
            await outbox.log(p.org_a, A, [("email:x:1", "fp1"), ("email:x:2", "fp1")], "sent")
            assert await outbox.has_log(p.org_a, A)
            assert await outbox.logged_pairs(p.org_a, A) == {("email:x:1", "fp1"),
                                                             ("email:x:2", "fp1")}
        with p.admin_engine.connect() as c:
            got = dict(c.execute(text(
                "SELECT signal_id, outcome FROM reach_signal_log WHERE member_email = :m"),
                {"m": A}).all())
        assert got == {"email:x:1": "baseline", "email:x:2": "sent"}


class TestTheTenancyBoundary:
    async def test_org_b_reads_none_of_org_a(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            await outbox.log(p.org_a, A, [("email:a-only", "fp1")], "sent")
            await outbox.plan(p.org_a, A, kind="interrupt", signal_ids=[], key="k-a",
                              body="secret of a", local_date=None)
        async with _as_member(p, p.org_b):
            assert await outbox.logged_pairs(p.org_b, A) == set()
            assert not await outbox.has_log(p.org_b, A)
            async with tenant_session() as db:
                bodies = (await db.execute(text(
                    "SELECT body FROM reach_outbox WHERE member_email IN (:a, :b)"),
                    {"a": A, "b": B})).scalars().all()
        assert bodies == []
        assert {r["org"] for r in _outbox_rows(p, A)} == {p.org_a}

    @pytest.mark.parametrize("sql", [
        "INSERT INTO reach_signal_log (organization_id, member_email, signal_id, "
        "fingerprint, outcome) VALUES (CAST(:o AS uuid), :m, 's', 'f', 'sent')",
        "INSERT INTO reach_outbox (organization_id, member_email, kind, dedupe_key, body) "
        "VALUES (CAST(:o AS uuid), :m, 'brief', 'k-x', 'b')",
    ])
    async def test_with_check_refuses_a_row_stamped_with_another_org(self, promoted, sql):  # noqa: F811
        p = promoted
        with pytest.raises(DBAPIError, match="row-level security"):
            async with _as_member(p, p.org_b), tenant_session() as db:
                await db.execute(text(sql), {"o": p.org_a, "m": B})


class TestTheChannelReads:
    async def test_the_link_and_the_window(self, promoted):  # noqa: F811
        p = promoted
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO whatsapp_member_links (organization_id, member_email, wa_id, "
                "status, is_current, linked_at) VALUES (CAST(:o AS uuid), :m, :wa, 'active', "
                "true, now())"), {"o": p.org_a, "m": A, "wa": PHONE})
        async with _as_member(p, p.org_a):
            assert await channel.linked_phone(p.org_a, A.upper()) == PHONE
            assert not await channel.window_open(p.org_a, PHONE)
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO whatsapp_bot_messages (organization_id, member_email, wa_id, "
                "wamid, direction, state, received_at) VALUES (CAST(:o AS uuid), :m, :wa, "
                ":w, 'in', 'replied', now() - interval '25 hours')"),
                {"o": p.org_a, "m": A, "wa": PHONE, "w": f"wamid.old.{TAG}"})
        async with _as_member(p, p.org_a):
            assert not await channel.window_open(p.org_a, PHONE)
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO whatsapp_bot_messages (organization_id, member_email, wa_id, "
                "wamid, direction, state) VALUES (CAST(:o AS uuid), :m, :wa, :w, 'in', "
                "'replied')"), {"o": p.org_a, "m": A, "wa": PHONE, "w": f"wamid.new.{TAG}"})
        async with _as_member(p, p.org_a):
            assert await channel.window_open(p.org_a, PHONE)
            assert await channel.reach_state(p.org_a, A) == (PHONE, True)
        # Org B sees neither the link nor the window of org A.
        async with _as_member(p, p.org_b):
            assert await channel.linked_phone(p.org_b, A) is None
            assert not await channel.window_open(p.org_b, PHONE)
            assert await channel.reach_state(p.org_b, A) == (None, False)

    async def test_only_an_inbound_message_opens_the_window(self, promoted):  # noqa: F811
        # Reach records its own sends as 'out' rows. Were they counted, each
        # send would hold the window open for ever (verifier, M10).
        p = promoted
        phone = "92" + str(uuid.uuid4().int)[:10]
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO whatsapp_bot_messages (organization_id, member_email, wa_id, "
                "wamid, direction, state) VALUES (CAST(:o AS uuid), :m, :wa, :w, 'out', "
                "'replied')"), {"o": p.org_a, "m": A, "wa": phone, "w": f"wamid.out.{TAG}"})
        async with _as_member(p, p.org_a):
            assert not await channel.window_open(p.org_a, phone)

    @pytest.mark.parametrize(("status", "current"), [("revoked", False), ("active", False)])
    async def test_only_an_active_current_link_is_used(self, promoted, status, current):  # noqa: F811
        # Spec §8.1: a revoked link, or a phone now current in another org,
        # gets nothing (verifier, M2). The table's own CHECK
        # `whatsapp_member_links_current_is_active` makes a current link that
        # is not active impossible, so M3 is an equivalent mutant.
        p = promoted
        member = f"{status}-{int(current)}-{TAG}@reach.test"
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO whatsapp_member_links (organization_id, member_email, wa_id, "
                "status, is_current, linked_at) VALUES (CAST(:o AS uuid), :m, :wa, :s, :cur, "
                "now())"), {"o": p.org_a, "m": member, "wa": "93" + str(uuid.uuid4().int)[:10],
                            "s": status, "cur": current})
        async with _as_member(p, p.org_a):
            assert await channel.linked_phone(p.org_a, member) is None


class TestCatchUpReads:
    """RE-2a: the two new reads, on the real catalog."""

    async def test_the_full_brief_is_due_once_after_a_template_brief(self, promoted):  # noqa: F811
        p = promoted
        day = date(2026, 10, 15)
        full = f"brief:full-{TAG}"
        async with _as_member(p, p.org_a):
            assert not await outbox.full_brief_due(p.org_a, A, day, full)
            first = await outbox.plan(p.org_a, A, kind="brief", signal_ids=[],
                                      key=f"brief:{TAG}", body="b", local_date=day)
            assert first is not None
            # A brief that is not yet sent does not call for its full brief.
            assert not await outbox.full_brief_due(p.org_a, A, day, full)
            await outbox.finish(p.org_a, first, state="sent", regime="template")
            assert await outbox.full_brief_due(p.org_a, A, day, full)
            assert not await outbox.full_brief_due(p.org_a, A, date(2026, 10, 16), full)
            assert await outbox.plan(p.org_a, A, kind="brief", signal_ids=[], key=full,
                                     body="full", local_date=day)
            assert not await outbox.full_brief_due(p.org_a, A, day, full)
        async with _as_member(p, p.org_b):
            # Org B sees no template brief of org A.
            assert not await outbox.full_brief_due(p.org_b, A, day, f"brief:other-{TAG}")

    async def test_a_free_text_brief_needs_no_full_brief(self, promoted):  # noqa: F811
        p = promoted
        day = date(2026, 10, 15)
        async with _as_member(p, p.org_a):
            row = await outbox.plan(p.org_a, A, kind="brief", signal_ids=[],
                                    key=f"brief:ft-{TAG}", body="b", local_date=day)
            await outbox.finish(p.org_a, str(row), state="sent", regime="free_text")
            assert not await outbox.full_brief_due(p.org_a, A, day, f"brief:ft-{TAG}:full")

    async def test_a_failed_brief_calls_for_its_full_brief(self, promoted):  # noqa: F811
        # Review of RE-2a, P2: a template Meta refused still lets the day's
        # brief go once the window opens.
        p = promoted
        day = date(2026, 10, 15)
        async with _as_member(p, p.org_a):
            row = await outbox.plan(p.org_a, A, kind="brief", signal_ids=[],
                                    key=f"brief:fail-{TAG}", body="b", local_date=day)
            await outbox.finish(p.org_a, str(row), state="failed", reason="send_failed")
            assert await outbox.full_brief_due(p.org_a, A, day, f"brief:fail-{TAG}:full")

    async def test_an_empty_brief_calls_for_no_full_brief(self, promoted):  # noqa: F811
        p = promoted
        day = date(2026, 10, 15)
        async with _as_member(p, p.org_a):
            row = await outbox.plan(p.org_a, A, kind="brief", signal_ids=[],
                                    key=f"brief:empty-{TAG}", body="", local_date=day)
            await outbox.finish(p.org_a, str(row), state="skipped", reason="empty")
            assert await outbox.brief_planned(p.org_a, A, day)
            assert not await outbox.full_brief_due(p.org_a, A, day, f"brief:empty-{TAG}:full")

    async def test_the_display_name_is_read_in_its_org(self, promoted):  # noqa: F811
        p = promoted
        with p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, status) "
                "VALUES (:e, ' Priya Shah ', CAST(:o AS uuid), 'active')"),
                {"e": A, "o": p.org_a})
            c.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, status) "
                "VALUES (:e, '  ', CAST(:o AS uuid), 'active')"), {"e": B, "o": p.org_b})
        async with _as_member(p, p.org_a):
            assert await channel.display_name(p.org_a, A.upper()) == "Priya Shah"
        async with _as_member(p, p.org_b):
            assert await channel.display_name(p.org_b, A) is None
            assert await channel.display_name(p.org_b, B) is None
