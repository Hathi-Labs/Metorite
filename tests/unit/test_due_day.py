"""RX-5: a due date is a day. The pure half, with no database.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.11. The real SQL is
``tests/unit/test_due_push_r8.py``. The client half is
``workbench/control_plane/src/lib/taskCard.test.ts``.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from acb_common.env_guard import is_platform_env
from gateway.reach import due_push, flags
from gateway.routes.projects import due_day, personal
from gateway.routes.projects.filters import build_task_filters
from gateway.routes.shell import feed, feed_rank, needs
from gateway.routes.shell.needs import _task_signals, due_kind, local_today

_GATEWAY = Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway" / "gateway"

IST = ZoneInfo("Asia/Kolkata")
LA = ZoneInfo("America/Los_Angeles")
TODAY = date(2026, 11, 2)


def _ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)


# ── The one rule ───────────────────────────────────────────────────────────


class TestTheRule:
    @pytest.mark.parametrize("due,zone,today,late", [
        # Due today, at noon, whatever the hour of the check.
        (_ist(2, 12), "Asia/Kolkata", TODAY, False),
        # The chat date of old rows: midnight UTC, 05:30 in Kolkata, today.
        (datetime(2026, 11, 2, tzinfo=UTC), "Asia/Kolkata", TODAY, False),
        # The last and the first minute of the local day.
        (_ist(2, 23, 59), "Asia/Kolkata", TODAY, False),
        (_ist(1, 23, 59), "Asia/Kolkata", TODAY, True),
        (_ist(2, 0, 0), "Asia/Kolkata", TODAY, False),
        # West of UTC, the instant is the next UTC day and today's local day.
        (datetime(2026, 11, 3, 2, 0, tzinfo=UTC), "America/Los_Angeles", TODAY, False),
        # An unknown zone reads as UTC.
        (datetime(2026, 11, 1, 23, 0, tzinfo=UTC), "Not/AZone", TODAY, True),
        (datetime(2026, 11, 3, 1, 0, tzinfo=UTC), None, TODAY, False),
    ])
    def test_a_task_is_late_from_the_day_after_its_date(self, due, zone, today, late):
        assert due_day.is_overdue(due, zone, today) is late

    @pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/Los_Angeles", "UTC",
                                      "Pacific/Kiritimati", "Asia/Kathmandu"])
    def test_the_sql_form_is_the_same_test(self, zone):
        now = datetime(2026, 11, 2, 12, 34, tzinfo=UTC)
        start = due_day.day_start(zone, now)
        today = due_day.local_date(zone, now)
        for minutes in range(-3000, 3000, 37):
            due = start + timedelta(minutes=minutes)
            assert (due < start) == due_day.is_overdue(due, zone, today), due
        assert not due_day.is_overdue(start, zone, today)
        assert due_day.is_overdue(start - timedelta(seconds=1), zone, today)

    def test_the_day_start_is_midnight_of_the_zone(self):
        assert due_day.day_start("Asia/Kolkata", _ist(2, 18)) == _ist(2, 0)
        # 03:00 UTC on the 3rd is still the 2nd in Los Angeles.
        at = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)
        assert due_day.day_start("America/Los_Angeles", at) == datetime(2026, 11, 2, tzinfo=LA)

    def test_the_predicates_are_disjoint_and_bind_one_name(self):
        assert due_day.overdue_sql() == "t.due_at < :day_start"
        assert due_day.overdue_sql("k") == "k.due_at < :day_start"
        assert due_day.not_overdue_sql() == "t.due_at >= :day_start"


class TestOneDefinition:
    def test_no_statement_of_the_gateway_compares_a_due_date_with_the_clock(self):
        """The day rule binds ``:day_start``. A new ``due_at < now()`` is a
        second definition of late work. Two reads stay on the clock, and
        each says why in §5.11: an upcoming list, and the 48-hour urgency."""
        kept = {"routes/projects/item_lens.py": 2, "routes/projects/core.py": 1,
                "routes/projects/due_day.py": 1}
        found: dict[str, int] = {}
        pattern = re.compile(r"due_at\s*(<=?|>=?)\s*now\(\)")
        for path in _GATEWAY.rglob("*.py"):
            hits = len(pattern.findall(path.read_text(encoding="utf-8")))
            if hits:
                found[path.relative_to(_GATEWAY).as_posix()] = hits
        assert found == kept

    def test_the_overdue_filter_binds_the_day_of_the_reader(self):
        start = due_day.day_start("Asia/Kolkata", _ist(2, 18))
        clauses, params = build_task_filters(overdue=True, day_start=start)
        assert params["day_start"] == start
        assert any("t.due_at < :day_start" in c for c in clauses)

    def test_a_filter_with_no_reader_reads_the_utc_day(self):
        _, params = build_task_filters(overdue=True)
        assert params["day_start"].utcoffset() == timedelta(0)
        assert (params["day_start"].hour, params["day_start"].minute) == (0, 0)

    def test_no_filter_no_bind(self):
        assert "day_start" not in build_task_filters()[1]


# ── A date with no time ────────────────────────────────────────────────────


class TestADateOnlyValue:
    @pytest.mark.parametrize("value,day", [
        ("2026-11-02", date(2026, 11, 2)),
        (" 2026-11-02 ", date(2026, 11, 2)),
        ("2026-11-02T17:00:00+05:30", None),
        ("2026-11-02T17:00:00", None),
        ("tomorrow", None),
        ("", None),
        (None, None),
        (datetime(2026, 11, 2, tzinfo=UTC), None),
    ])
    def test_only_a_date_with_no_time_counts(self, value, day):
        assert due_day.date_only(value) == day

    @pytest.mark.parametrize("zone,hour_utc,day_utc", [
        ("Asia/Kolkata", 6, 2), ("UTC", 12, 2), ("America/Los_Angeles", 20, 2),
    ])
    def test_noon_of_the_zone(self, zone, hour_utc, day_utc):
        at = due_day.noon_of(TODAY, zone).astimezone(UTC)
        assert (at.day, at.hour) == (day_utc, hour_utc)
        assert due_day.local_date(zone, at) == TODAY

    async def test_the_seam_reads_the_zone_only_for_a_date(self, monkeypatch):
        reads: list[str] = []

        async def zone(db, email):
            reads.append(email)
            return "America/Los_Angeles"

        monkeypatch.setattr(personal, "stored_zone", zone)
        stored = await personal.stored_due(None, "m@x.test", "2026-11-02")
        assert stored == due_day.noon_of(TODAY, "America/Los_Angeles")
        assert reads == ["m@x.test"]
        for kept in ("2026-11-02T17:00:00+05:30", "not a date", None):
            assert await personal.stored_due(None, "m@x.test", kept) == kept
        assert reads == ["m@x.test"]

    def test_the_tools_that_take_a_date_reach_the_seam(self):
        tasks = (_GATEWAY / "routes/projects/tasks.py").read_text(encoding="utf-8")
        mine = (_GATEWAY / "routes/projects/personal.py").read_text(encoding="utf-8")
        # POST /projects/tasks and PATCH /projects/tasks/{id}: create_task,
        # create_tasks and update_task.
        assert tasks.count("await _store_due(db, user, values)") == 2
        # The one capture path, and organize.
        assert 'await stored_due(db, email, values["due_at"])' in mine
        assert "await stored_due(db, email, payload.due_at)" in mine


# ── My Day ─────────────────────────────────────────────────────────────────


def _task(due: datetime, **more) -> dict:
    return {"id": "t1", "title": "A task", "due_at": due.isoformat(), **more}


class TestTheKind:
    def test_due_today_stays_due_today_after_its_hour(self):
        assert due_kind(_task(_ist(2, 12)), "Asia/Kolkata", TODAY) == "due_today"

    def test_yesterday_is_overdue(self):
        assert due_kind(_task(_ist(1, 23, 59)), "Asia/Kolkata", TODAY) == "overdue"

    @pytest.mark.parametrize("more", [{"completed_at": "2026-11-02T01:00:00+00:00"},
                                      {"disposition": "SOMEDAY"}, {"disposition": "REFERENCE"}])
    def test_a_row_that_is_no_card_has_no_kind(self, more):
        assert due_kind(_task(_ist(2, 12), **more), "UTC", TODAY) is None

    def test_a_row_with_no_date_has_no_kind(self):
        assert due_kind({"id": "t1", "due_at": None}, "UTC", TODAY) is None

    def test_a_read_with_no_date_reads_the_clock_in_its_zone(self, monkeypatch):
        monkeypatch.setattr(needs, "_now", lambda: datetime(2026, 11, 1, 20, 0, tzinfo=UTC))
        assert local_today({"today": "2026-11-05"}) == date(2026, 11, 5)
        assert local_today({"timezone": "Asia/Kolkata"}) == date(2026, 11, 2)


class TestTheLine:
    NOW = _ist(2, 18)

    def _why(self, kind: str, due: datetime, **facts) -> tuple[str, str]:
        return feed.why(kind, {"due_at": due.isoformat(), **facts}, None, self.NOW, IST, TODAY)

    def test_due_today_names_no_clock(self):
        assert self._why("due_today", _ist(2, 12)) == ("due_today", "Due today.")
        assert self._why("due_today", _ist(2, 5, 30)) == ("due_today", "Due today.")

    def test_due_tomorrow(self):
        assert self._why("due_today", _ist(3, 12))[1] == "Due tomorrow."

    def test_one_day_late(self):
        assert self._why("overdue", _ist(1, 12), days_late=1) == (
            "days_late", "Due yesterday. 1 day late.")

    def test_days_late(self):
        assert self._why("overdue", datetime(2026, 10, 30, 12, tzinfo=IST), days_late=3)[1] == (
            "Due on Friday. 3 days late.")

    def test_an_overdue_card_with_no_count_still_names_no_clock(self):
        assert self._why("overdue", _ist(1, 12))[1] == "Due yesterday."

    @pytest.mark.parametrize("kind", ["due_today", "overdue"])
    def test_no_line_holds_a_clock(self, kind):
        for hour in range(24):
            assert not re.search(r"\d:\d\d", self._why(kind, _ist(2, hour), days_late=1)[1])


class TestTheGroup:
    @pytest.mark.parametrize("hours", [-3, 0.5, 1, 1.9, 2, 6])
    def test_no_hour_moves_a_due_task_into_now(self, hours):
        due = self.at(hours)
        assert feed_rank.group("due_today", {"due_at": due.isoformat()}, self.NOW,
                               TODAY) == "needs_you"

    NOW = datetime(2026, 11, 2, 12, 30, tzinfo=UTC)

    def at(self, hours: float) -> datetime:
        return self.NOW + timedelta(hours=hours)

    def test_a_task_due_today_has_no_urgent_time_and_an_overdue_task_has_one(self):
        kept = [("due_today", _task(_ist(2, 12))), ("overdue", _task(_ist(1, 12)))]
        kept[1][1]["id"] = "t2"
        today, late = _task_signals(kept, "Asia/Kolkata", TODAY.isoformat(), calendar=False)
        assert today.urgent_at is None
        assert late.urgent_at == late.at
        assert late.facts["days_late"] == 1

    def test_the_scores_keep_the_order_of_the_needs_kinds(self):
        """Overdue, then an approval, then due today, then the rest, each with
        no weight. A due task no longer gains points by the clock."""
        now = self.NOW
        late = feed_rank.score("overdue", {"days_late": 1}, self.at(-24), now)
        approval = feed_rank.score("approval", {}, None, now)
        today = feed_rank.score("due_today", {}, None, now)
        note = feed_rank.score("notification", {}, None, now)
        reply = feed_rank.score("needs_reply", {}, None, now)
        assert late > approval > today > note > reply

    def test_a_card_folds_as_before(self):
        assert feed_rank.base("overdue") > feed_rank.base("due_today")


# ── The flag ───────────────────────────────────────────────────────────────


class TestTheFlag:
    @pytest.fixture
    def setting(self, monkeypatch):
        s = SimpleNamespace(due_push_orgs="")
        monkeypatch.setattr(flags, "get_settings", lambda: s)
        return s

    def test_empty_opens_no_org(self, setting):
        assert flags.due_push_allowed("org-1") is False
        assert flags.due_push_allowed(None) is False

    def test_a_list_opens_the_orgs_it_names(self, setting):
        setting.due_push_orgs = " ORG-1 , org-2 "
        assert flags.due_push_allowed("org-1") is True
        assert flags.due_push_allowed("Org-2") is True
        assert flags.due_push_allowed("org-3") is False

    def test_a_star_opens_each_org_and_no_org_stays_refused(self, setting):
        setting.due_push_orgs = "*"
        assert flags.due_push_allowed("org-1") is True
        assert flags.due_push_allowed(None) is False

    def test_a_tenant_may_not_set_it(self):
        assert is_platform_env("DUE_PUSH_ORGS") is True

    async def test_with_the_flag_off_the_plan_reads_nothing(self, setting, monkeypatch):
        monkeypatch.setattr(due_push.push_channel, "push_on", lambda: True)

        def boom(*_a, **_k):
            raise AssertionError("no session opens for an org off the list")

        monkeypatch.setattr(due_push, "tenant_session", boom)
        now = datetime(2026, 11, 2, 12, tzinfo=UTC)
        assert await due_push.plan_org("org-1", now) == {}


class TestTheMemberCheck:
    @pytest.fixture
    def resolve(self, monkeypatch):
        import gateway.reach.loop as reach_loop

        box: dict = {}

        async def fake(org, member):
            return box["user"]

        monkeypatch.setattr(reach_loop, "resolve", fake)
        return box

    async def test_the_feature_of_my_day_decides(self, resolve):
        resolve["user"] = SimpleNamespace(has_permission=lambda p: p == "feature:projects")
        assert await due_push._may_read("o", "m") is True
        resolve["user"] = SimpleNamespace(has_permission=lambda p: False)
        assert await due_push._may_read("o", "m") is False

    @pytest.mark.parametrize("reason,answer", [("departed", False), ("other_org", False),
                                               ("identity_failed", None)])
    async def test_a_reason_is_a_no_and_a_failed_read_tries_again(self, resolve, reason, answer):
        resolve["user"] = reason
        assert await due_push._may_read("o", "m") is answer
