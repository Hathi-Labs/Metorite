"""WS-58 MD-3e, R8: the CRM kinds of My Day on a REAL Postgres
(``my_day_feed.md`` §5.9 item 13, the MD-3e ticket).

The three new reads (``activities.my_due_activities``,
``aggregate.my_overdue_deals`` and ``aggregate.my_close_passed_deals``) run
their real SQL on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. The hermetic half is
``test_feed_crm_kinds.py``.

**Two organizations.** ME is a member of org B, with one record for each
rule and one record just outside each rule. ME also owns deals and wrote
follow-ups in org A, under the same address. So a read that loses its org
predicate shows them in org B.

* **As the app role, with RLS forced** (``_as_member``): each kind gives its
  card and no other row does.
* **As the owner role, which RLS does not hold** (``_as_owner``). The
  production role ``acb_app`` IS held by RLS (measured on the box,
  2026-10-16), so this is defence in depth. The owner session must see both
  orgs, or the check proves nothing. Then only the SQL's own org predicate
  keeps org A out.

The rows just outside a rule: a follow-up of another member, a completed
one, a ``status_change`` and a ``system`` row, one due after my local
today. A quiet deal of another owner, one inside its limit, one in a stage
with no limit, and a won one. A close date of another owner, of a won and a
lost deal, and one that is today.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.db import tenant_session
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import aggregate as crm_aggregate
from gateway.routes.crm import seed as crm_seed
from gateway.routes.shell import feed
from gateway.routes.shell import needs as shell
from sqlalchemy import text

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit._crm_fakes import crm_switch_on
from tests.unit.test_crm_aggregate_r8 import _TABLES
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_feed_measure_r8 import _as_owner
from tests.unit.test_shell_needs_r8 import NOW, _Frozen

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"crm-me-{TAG}@md3e.test"
COLL = f"crm-coll-{TAG}@md3e.test"
#: Two members far from UTC, for the one CRM day rule (crm_platform.md §19.7).
IST = f"crm-ist-{TAG}@md3e.test"
NYC = f"crm-ny-{TAG}@md3e.test"
#: A member with 20 stale open tasks and one of today.
HEAVY = f"crm-heavy-{TAG}@md3e.test"
KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
#: The member's zone is UTC, so the local today is the UTC date of NOW.
TODAY = NOW.astimezone(UTC).date()
TODAY_IST = NOW.astimezone(KOLKATA).date()
TODAY_NY = NOW.astimezone(NEW_YORK).date()
TOMORROW0 = datetime.combine(TODAY + timedelta(days=1), time.min, tzinfo=UTC)
CRM_KINDS = ("crm_followup_due", "deal_quiet", "deal_close_passed")


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The feed's clock reads ``NOW``. The kill switch is on for each org."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)
    crm_switch_on(monkeypatch, "*")


def _member(email: str, org: str, *, admin: bool = False) -> UserContext:
    grants = {"feature:crm", "feature:projects", "feature:email", "feature:tasks"}
    if admin:
        grants.add(crm_access.ADMIN_PERMISSION)
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=EffectiveAccess(role_granted=frozenset(grants)))


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the reads are the first that
    the app role makes."""
    p = promoted
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            c.execute(text(
                "INSERT INTO crm_settings (organization_id, mode, access) VALUES "
                "(CAST(:o AS uuid), 'native', 'everyone')"), {"o": org})
            for name, color, position, kind, is_default, probability in crm_seed.DEAL_STATUSES:
                c.execute(text(crm_seed._DEAL_SQL), {
                    "organization_id": org, "name": name, "color": color,
                    "position": position, "type": kind, "is_default": is_default,
                    "probability": probability})
            for name, color, position, kind, is_default in crm_seed.LEAD_STATUSES:
                c.execute(text(crm_seed._LEAD_SQL), {
                    "organization_id": org, "name": name, "color": color,
                    "position": position, "type": kind, "is_default": is_default})
            # Proposal has a limit of 10 days. Qualification has none.
            c.execute(text(
                "UPDATE crm_deal_statuses SET max_dwell_days = 10 "
                "WHERE organization_id = CAST(:o AS uuid) AND name = 'Proposal'"), {"o": org})
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, organization_id) VALUES "
                "(:e, 'UTC', CAST(:o AS uuid))"), {"e": ME, "o": org})
        for email, zone in ((IST, "Asia/Kolkata"), (NYC, "America/New_York"), (HEAVY, "UTC")):
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, organization_id) VALUES "
                "(:e, :z, CAST(:o AS uuid))"), {"e": email, "z": zone, "o": p.org_b})

        def stage(org: str, name: str) -> str:
            return str(c.execute(text(
                "SELECT id FROM crm_deal_statuses WHERE organization_id = CAST(:o AS uuid) "
                "AND name = :n"), {"o": org, "n": name}).scalar_one())

        def deal(org: str, name: str, stage_name: str, *, owner: str = ME,
                 in_stage_days: float = 0, close=None) -> str:
            return str(c.execute(text(
                "INSERT INTO crm_deals (organization_id, name, status_id, owner_email, "
                "status_changed_at, expected_close_date) VALUES (CAST(:o AS uuid), :n, "
                "CAST(:s AS uuid), :w, now() - make_interval(secs => :d), :c) RETURNING id"),
                {"o": org, "n": name, "s": stage(org, stage_name), "w": owner,
                 "d": in_stage_days * 86400, "c": close}).scalar_one())

        def lead(org: str, name: str) -> str:
            return str(c.execute(text(
                "INSERT INTO crm_leads (organization_id, lead_name, status_id) VALUES "
                "(CAST(:o AS uuid), :n, (SELECT id FROM crm_lead_statuses WHERE "
                "organization_id = CAST(:o AS uuid) AND is_default)) RETURNING id"),
                {"o": org, "n": name}).scalar_one())

        def contact(org: str, first: str, last: str) -> str:
            return str(c.execute(text(
                "INSERT INTO crm_contacts (organization_id, first_name, last_name) VALUES "
                "(CAST(:o AS uuid), :f, :l) RETURNING id"),
                {"o": org, "f": first, "l": last}).scalar_one())

        def company(org: str, name: str) -> str:
            return str(c.execute(text(
                "INSERT INTO crm_companies (organization_id, name) VALUES "
                "(CAST(:o AS uuid), :n) RETURNING id"), {"o": org, "n": name}).scalar_one())

        def activity(org: str, *, due, by: str = ME, kind: str = "call", subject=None,
                     done=None, **target: str) -> None:
            c.execute(text(
                "INSERT INTO crm_activities (organization_id, type, subject, due_at, "
                "completed_at, created_by, deal_id, lead_id, contact_id, company_id) "
                "VALUES (CAST(:o AS uuid), :t, :s, :due, :done, :by, CAST(:deal AS uuid), "
                "CAST(:lead AS uuid), CAST(:contact AS uuid), CAST(:company AS uuid))"),
                {"o": org, "t": kind, "s": subject, "due": due, "done": done, "by": by,
                 "deal": target.get("deal"), "lead": target.get("lead"),
                 "contact": target.get("contact"), "company": target.get("company")})

        b = p.org_b
        # ── crm_followup_due ───────────────────────────────────────────────
        ids["d_follow"] = deal(b, "Pump order", "Negotiation")
        ids["l_follow"] = lead(b, "Acme lead")
        ids["c_follow"] = contact(b, "Dana", "Rao")
        ids["o_follow"] = company(b, "Globex")
        ids["l_both"] = lead(b, "Converted lead")
        # Two open follow-ups on one deal: the earliest gives the card.
        activity(b, due=NOW - timedelta(hours=3), subject="first call", deal=ids["d_follow"])
        activity(b, due=NOW - timedelta(hours=1), subject="second call",
                 deal=ids["d_follow"])
        # On a deal AND its lead: the deal takes it.
        activity(b, due=NOW - timedelta(hours=2), subject="on both", deal=ids["d_follow"],
                 lead=ids["l_both"])
        # My address in another case still is me.
        activity(b, due=TOMORROW0 - timedelta(minutes=1), subject="late today",
                 by=ME.upper(), lead=ids["l_follow"])
        activity(b, due=NOW - timedelta(days=3), contact=ids["c_follow"])
        activity(b, due=NOW - timedelta(hours=5), subject="renewal", company=ids["o_follow"])
        # Just outside the rule: no card for each of these records.
        ids["c_coll"] = contact(b, "Coll", "Only")
        activity(b, due=NOW - timedelta(hours=1), by=COLL, contact=ids["c_coll"])
        ids["c_done"] = contact(b, "Done", "Already")
        activity(b, due=NOW - timedelta(hours=1), done=NOW, contact=ids["c_done"])
        ids["c_status"] = contact(b, "Status", "Change")
        activity(b, due=NOW - timedelta(hours=1), kind="status_change",
                 contact=ids["c_status"])
        ids["c_system"] = contact(b, "System", "Row")
        activity(b, due=NOW - timedelta(hours=1), kind="system", contact=ids["c_system"])
        ids["c_tomorrow"] = contact(b, "Due", "Tomorrow")
        activity(b, due=TOMORROW0, contact=ids["c_tomorrow"])
        ids["c_agent"] = contact(b, "Agent", "Wrote")
        activity(b, due=NOW - timedelta(hours=1), by="agent:crm", contact=ids["c_agent"])
        _seed_tasks(ids, b, contact, activity)
        # ── deal_quiet ─────────────────────────────────────────────────────
        ids["d_quiet"] = deal(b, "Quiet deal", "Proposal", in_stage_days=12)
        ids["d_quiet_9"] = deal(b, "Inside the limit", "Proposal", in_stage_days=9)
        ids["d_no_limit"] = deal(b, "No limit", "Qualification", in_stage_days=100)
        ids["d_quiet_coll"] = deal(b, "Their quiet deal", "Proposal", owner=COLL,
                                   in_stage_days=30)
        ids["d_won"] = deal(b, "Won long ago", "Closed Won", in_stage_days=100,
                            close=TODAY - timedelta(days=50))
        # ── deal_close_passed ──────────────────────────────────────────────
        ids["d_close"] = deal(b, "Late deal", "Needs Analysis", owner=ME.upper(),
                              close=TODAY - timedelta(days=2))
        ids["d_close_today"] = deal(b, "Closes today", "Needs Analysis", close=TODAY)
        ids["d_close_coll"] = deal(b, "Their late deal", "Needs Analysis", owner=COLL,
                                   close=TODAY - timedelta(days=2))
        ids["d_lost"] = deal(b, "Lost deal", "Closed Lost", close=TODAY - timedelta(days=9))

        # ── org A: the same address, and rows of each kind ────────────────
        a = p.org_a
        ids["a_quiet"] = deal(a, "Org A quiet", "Proposal", in_stage_days=40,
                              close=TODAY - timedelta(days=5))
        ids["a_contact"] = contact(a, "Org", "A")
        activity(a, due=NOW - timedelta(hours=1), contact=ids["a_contact"])
    yield ids
    with p.admin_engine.begin() as c:
        for org in (p.org_a, p.org_b):
            for table in (*_TABLES, "user_settings"):
                c.execute(text(f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                          {"o": org})


def _task_day(day: date) -> datetime:
    """A task's due_at, as the CRM stores it: a DATE at 00:00 UTC."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def _seed_tasks(ids: dict[str, str], org: str, contact, activity) -> None:
    """The day rule of a task (P1-1), and the cap that keeps today (P2-1)."""
    for key, email, today in (("ist", IST, TODAY_IST), ("ny", NYC, TODAY_NY)):
        ids[f"c_{key}_today"] = contact(org, "Today", key)
        activity(org, due=_task_day(today), by=email, kind="task", subject="check in",
                 contact=ids[f"c_{key}_today"])
        ids[f"c_{key}_tomorrow"] = contact(org, "Tomorrow", key)
        activity(org, due=_task_day(today + timedelta(days=1)), by=email, kind="task",
                 contact=ids[f"c_{key}_tomorrow"])
    # 20 stale open tasks must not hide the task of today.
    for n in range(20):
        ids[f"c_heavy_{n:02d}"] = contact(org, "Stale", f"{n:02d}")
        activity(org, due=_task_day(TODAY - timedelta(days=30 - n)), by=HEAVY, kind="task",
                 contact=ids[f"c_heavy_{n:02d}"])
    ids["c_heavy_today"] = contact(org, "Fresh", "Today")
    activity(org, due=_task_day(TODAY), by=HEAVY, kind="task", subject="today",
             contact=ids["c_heavy_today"])


def _list(monkeypatch, value: str) -> None:
    from acb_common.settings import get_settings

    monkeypatch.setattr(get_settings(), "my_day_providers", value)


def _roomy(monkeypatch) -> None:
    """Room for a cold database. The ``sources`` check proves each provider
    ran. No time is asserted."""
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
    monkeypatch.setattr(feed, "FEED_TOTAL_S", 90.0)


async def _feed(p, monkeypatch, user: UserContext) -> dict:
    _roomy(monkeypatch)
    _list(monkeypatch, "crm")
    async with _as_member(p, p.org_b):
        return await feed.shell_feed(limit=80, user=user)


def _crm_cards(answer: dict) -> dict[str, dict]:
    return {c["signal_id"]: c for c in answer["cards"] if c["app"] == "crm"}


def _expected(s: dict[str, str]) -> dict[str, str]:
    """The cards of ME in org B, and the main kind of each."""
    return {
        f"crm:deal:{s['d_follow']}": "crm_followup_due",
        f"crm:lead:{s['l_follow']}": "crm_followup_due",
        f"crm:contact:{s['c_follow']}": "crm_followup_due",
        f"crm:organization:{s['o_follow']}": "crm_followup_due",
        f"crm:deal:{s['d_quiet']}": "deal_quiet",
        f"crm:deal:{s['d_close']}": "deal_close_passed",
    }


async def _reads(user: UserContext) -> dict[str, list[dict]]:
    """The three reads in one session, as the provider calls them."""
    from gateway.routes.crm.activities import my_due_activities
    from gateway.routes.crm.aggregate import my_close_passed_deals, my_overdue_deals

    async with tenant_session() as db:
        return {
            "due": await my_due_activities(db, user, today=TODAY, tz=ZoneInfo("UTC"),
                                           limit=50),
            "quiet": await my_overdue_deals(db, user, limit=50),
            "passed": await my_close_passed_deals(db, user, today=TODAY, limit=50),
        }


def _ids(reads: dict[str, list[dict]]) -> dict[str, set[str]]:
    return {"due": {r["record_id"] for r in reads["due"]},
            "quiet": {r["id"] for r in reads["quiet"]},
            "passed": {r["id"] for r in reads["passed"]}}


def _org_b_ids(s: dict[str, str]) -> dict[str, set[str]]:
    return {"due": {s["d_follow"], s["l_follow"], s["c_follow"], s["o_follow"]},
            "quiet": {s["d_quiet"]}, "passed": {s["d_close"]}}


class TestTheKindsOnARealDatabase:
    async def test_each_kind_gives_its_card_and_no_other(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 2, 6, 7 and 8."""
        answer = await _feed(promoted, monkeypatch, _member(ME, promoted.org_b))
        assert answer["sources"]["crm"] == "ok"
        cards = _crm_cards(answer)
        assert {sid: c["kind"] for sid, c in cards.items()} == _expected(seeded)
        s = seeded
        deal = cards[f"crm:deal:{s['d_follow']}"]
        # The earliest open follow-up of the record, and the deal before the lead.
        assert deal["facts"]["subject"] == "first call"
        assert deal["facts"]["due_at"] == (NOW - timedelta(hours=3)).isoformat()
        assert deal["why"].startswith("First call is due today at ")
        assert (deal["title"], deal["href"]) == ("Pump order", f"/crm?tab=deals&deal={s['d_follow']}")
        assert cards[f"crm:contact:{s['c_follow']}"]["title"] == "Dana Rao"
        assert cards[f"crm:organization:{s['o_follow']}"]["acts"][0]["label"] == "Open company"
        quiet = cards[f"crm:deal:{s['d_quiet']}"]
        assert quiet["why"] == "In Proposal for 12 days. The stage limit is 10 days."
        late = cards[f"crm:deal:{s['d_close']}"]
        assert late["facts"]["due_at"] == datetime.combine(
            TODAY - timedelta(days=2), time.min, tzinfo=UTC).isoformat()
        assert f"crm:lead:{s['l_both']}" not in cards

    async def test_the_reads_hold_the_rules(self, promoted, seeded):  # noqa: F811
        async with _as_member(promoted, promoted.org_b):
            got = _ids(await _reads(_member(ME, promoted.org_b)))
        assert got == _org_b_ids(seeded)

    async def test_mirror_mode_has_no_deal_quiet_and_keeps_the_follow_ups(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Done-when 5."""
        p = promoted
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE crm_settings SET mode = 'mirror' "
                           "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_b})
        try:
            answer = await _feed(p, monkeypatch, _member(ME, p.org_b))
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("UPDATE crm_settings SET mode = 'native' "
                               "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_b})
        assert answer["sources"]["crm"] == "ok"
        expected = {sid: k for sid, k in _expected(seeded).items() if k != "deal_quiet"}
        assert {sid: c["kind"] for sid, c in _crm_cards(answer).items()} == expected

    async def test_access_admins_gives_a_plain_member_absent_and_no_crm_read(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Done-when 4, on the real access row."""
        p = promoted
        opened: list[str] = []
        real = crm_aggregate._tenant_session

        @contextlib.asynccontextmanager
        async def _counting():
            opened.append("crm")
            async with real() as db:
                yield db

        monkeypatch.setattr(crm_aggregate, "_tenant_session", _counting)
        with p.admin_engine.begin() as c:
            c.execute(text("UPDATE crm_settings SET access = 'admins' "
                           "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_b})
        try:
            plain = await _feed(p, monkeypatch, _member(ME, p.org_b))
            admin = await _feed(p, monkeypatch, _member(ME, p.org_b, admin=True))
        finally:
            with p.admin_engine.begin() as c:
                c.execute(text("UPDATE crm_settings SET access = 'everyone' "
                               "WHERE organization_id = CAST(:o AS uuid)"), {"o": p.org_b})
        assert plain["sources"]["crm"] == "absent"
        assert not _crm_cards(plain)
        assert admin["sources"]["crm"] == "ok"
        assert set(_crm_cards(admin)) == set(_expected(seeded))
        assert opened == ["crm"], "the plain member made no CRM read"

    async def test_the_kill_switch_off_gives_absent(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 3."""
        from acb_common import get_settings

        monkeypatch.setattr(get_settings(), "crm_enabled", False, raising=False)
        answer = await _feed(promoted, monkeypatch, _member(ME, promoted.org_b))
        assert answer["sources"]["crm"] == "absent"
        assert not _crm_cards(answer)

    async def test_the_provider_opens_two_sessions_at_most(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Done-when 11: a counting wrapper of each seam."""
        opened: list[str] = []

        def _counting(name, real):
            @contextlib.asynccontextmanager
            async def _session():
                opened.append(name)
                async with real() as db:
                    yield db
            return _session

        monkeypatch.setattr(crm_aggregate, "_tenant_session",
                            _counting("crm", crm_aggregate._tenant_session))
        monkeypatch.setattr(crm_access, "_tenant_session",
                            _counting("access", crm_access._tenant_session))
        async with _as_member(promoted, promoted.org_b):
            found = await feed._crm(_member(ME, promoted.org_b))
        assert opened == ["access", "crm"]
        assert {s.kind for s in found.signals()} == set(CRM_KINDS)

    async def test_with_the_list_empty_nothing_runs(self, promoted, seeded, monkeypatch):  # noqa: F811
        """Done-when 1, on the real database."""
        answer = await _feed(promoted, monkeypatch, _member(ME, promoted.org_b))
        assert "crm" in answer["sources"]
        _list(monkeypatch, "")
        async with _as_member(promoted, promoted.org_b):
            dark = await feed.shell_feed(limit=80, user=_member(ME, promoted.org_b))
        assert "crm" not in dark["sources"]
        assert not _crm_cards(dark)


class TestTheDayRuleOfATask:
    """P1-1: a task's due_at is a DATE at 00:00 UTC (crm_platform.md §19.7).
    Its UTC date meets the member's local date, in India and in New York."""

    @pytest.mark.parametrize(("email", "zone", "key"), [
        (IST, KOLKATA, "ist"), (NYC, NEW_YORK, "ny")])
    async def test_a_task_of_my_today_shows_and_one_of_tomorrow_does_not(
        self, promoted, seeded, monkeypatch, email, zone, key,  # noqa: F811
    ):
        today = NOW.astimezone(zone).date()
        answer = await _feed(promoted, monkeypatch, _member(email, promoted.org_b))
        assert answer["sources"]["crm"] == "ok"
        cards = _crm_cards(answer)
        assert set(cards) == {f"crm:contact:{seeded[f'c_{key}_today']}"}
        c = cards[f"crm:contact:{seeded[f'c_{key}_today']}"]
        assert c["why"] == "Check in is due today."
        assert c["facts"]["due_at"] == datetime.combine(today, time.min,
                                                        tzinfo=zone).isoformat()
        assert c["urgent_at"] is None
        assert c["group"] == "needs_you"


class TestTodayFirst:
    """P2-1: the LIMIT keeps the follow-ups of today, then the older ones."""

    async def test_twenty_stale_tasks_do_not_hide_the_task_of_today(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        answer = await _feed(promoted, monkeypatch, _member(HEAVY, promoted.org_b))
        cards = _crm_cards(answer)
        assert f"crm:contact:{seeded['c_heavy_today']}" in cards
        assert "crm" in answer["capped"]
        assert len(cards) == shell.PER_APP

    async def test_the_read_gives_the_fresh_row_first(self, promoted, seeded):  # noqa: F811
        from gateway.routes.crm.activities import my_due_activities

        user = _member(HEAVY, promoted.org_b)
        async with _as_member(promoted, promoted.org_b), tenant_session() as db:
            rows = await my_due_activities(db, user, today=TODAY, tz=ZoneInfo("UTC"),
                                           limit=16)
        assert rows[0]["record_id"] == seeded["c_heavy_today"]
        assert (rows[0]["fresh"], rows[0]["all_day"]) == (True, True)
        assert len(rows) == 16 and not any(r["fresh"] for r in rows[1:])


class TestTwoOrganizations:
    """Done-when 9: each read returns the rows of one tenant only."""

    async def test_each_org_reads_its_own_rows_as_the_app_role(self, promoted, seeded):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            in_a = _ids(await _reads(_member(ME, p.org_a)))
        assert in_a == {"due": {seeded["a_contact"]}, "quiet": {seeded["a_quiet"]},
                        "passed": {seeded["a_quiet"]}}

    async def test_the_org_predicate_holds_on_a_session_that_bypasses_rls(
        self, promoted, seeded, monkeypatch,  # noqa: F811
    ):
        """Defence in depth: on a role that RLS does not hold, only the org
        predicate of each statement keeps org A out."""
        p = promoted
        async with _as_owner(p, p.org_b):
            async with tenant_session() as db:
                both = (await db.execute(text(
                    "SELECT count(DISTINCT organization_id) FROM crm_deals "
                    "WHERE lower(owner_email) = :m"), {"m": ME})).scalar_one()
                acts = (await db.execute(text(
                    "SELECT count(DISTINCT organization_id) FROM crm_activities "
                    "WHERE lower(created_by) = :m"), {"m": ME})).scalar_one()
            got = _ids(await _reads(_member(ME, p.org_b)))
        assert (both, acts) == (2, 2), \
            "the owner session must see both orgs, or this proves nothing"
        assert got == _org_b_ids(seeded)

    async def test_the_whole_feed_on_the_owner_role(self, promoted, seeded, monkeypatch):  # noqa: F811
        p = promoted
        _roomy(monkeypatch)
        _list(monkeypatch, "crm")
        async with _as_owner(p, p.org_b):
            answer = await feed.shell_feed(limit=80, user=_member(ME, p.org_b))
        assert answer["sources"]["crm"] == "ok"
        assert {sid: c["kind"] for sid, c in _crm_cards(answer).items()} == _expected(seeded)
