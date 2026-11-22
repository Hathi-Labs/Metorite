"""RX-5: a due date is a day. The pure half, with no database.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.11. The real SQL is
``tests/unit/test_due_push_r8.py``. The client half is
``workbench/control_plane/src/lib/taskCard.test.ts``.
"""

from __future__ import annotations

import contextlib
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from acb_common.env_guard import is_platform_env
from gateway.reach import due_push, flags
from gateway.reminders import loop
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


#: A due date against the clock, in either order and any case. ``:now`` and
#: ``:at`` are the binds that stand for the clock. ``now`` and ``at`` are the
#: Python names of it, and ``due`` is the Python name of a due value: the digest
#: held ``due < at`` for a ``pm_tasks`` row, and the SQL form could not see it.
_SQL_CLOCK = r"(?:now\(\)|:now\b|:at\b|current_timestamp|clock_timestamp\(\)|statement_timestamp\(\))"
_PY_CLOCK = r"(?:\bnow\b|\bat\b|datetime\.now\([^)]*\))"
_CLOCK = rf"(?:{_SQL_CLOCK}|{_PY_CLOCK})"
_DUE = r"(?:\w+\.)?\bdue(?:_at)?\b"
_CLOCK_COMPARE = re.compile(
    rf"{_DUE}\s*(?:<=?|>=?)\s*{_CLOCK}|{_CLOCK}\s*(?:<=?|>=?)\s*{_DUE}",
    re.IGNORECASE)


class TestOneDefinition:
    @pytest.mark.parametrize("sql", [
        "t.due_at < now()", "t.due_at <= NOW()", "due_at<now()", "t.due_at > now()",
        "t.due_at < :now", "t.due_at < :at", "t.due_at < CURRENT_TIMESTAMP",
        "t.due_at < current_timestamp", "t.due_at < clock_timestamp()",
        "now() > t.due_at", "NOW() >= due_at", ":now > t.due_at",
        "CURRENT_TIMESTAMP > t.due_at",
        # The same test on a value held in a variable.
        "due < at", "due <= now", "due<now", "at > due", "now >= due",
        "row.due_at < now", "due < datetime.now(UTC)", "datetime.now(UTC) > due",
    ])
    def test_each_spelling_of_the_clock_is_a_hit(self, sql):
        assert _CLOCK_COMPARE.search(sql)

    @pytest.mark.parametrize("sql", [
        "t.due_at < :day_start", "t.due_at >= :day_start", "t.due_at < :due_before",
        "t.due_at < CAST(:until AS date)", "t.due_at IS NOT NULL", "t.due_at < :nowhere",
        "due < today", "due <= until", "due > horizon", "due < day_start", "overdue < at",
        "at < until", "due <= at_most",
    ])
    def test_the_day_rule_is_no_hit(self, sql):
        assert not _CLOCK_COMPARE.search(sql)

    def test_no_statement_of_the_gateway_compares_a_due_date_with_the_clock(self):
        """The day rule binds ``:day_start``. A new ``due_at < now()`` is a
        second definition of late work, and so is ``due < at`` in Python. Two
        reads of a task stay on the clock, and each says why in §5.11: an
        upcoming list, and the 48-hour urgency. ``due_day.py`` names the old
        test in its own docstring.

        Three more hold a value named ``due`` that is no due date of a member:
        the review date of a Knowledge page (``kb_index``, ``retrieval``), and
        the importer's estimate of when a closed task finished (``layout``).
        One more is an input check, not a lateness rule: ``ask_life`` in
        ``decisions.py`` refuses a person ask whose own due date is in the
        past (WS-AP AP-8a, ``waiting_on_me.md`` §7.2). It marks no task late."""
        kept = {"routes/projects/item_lens.py": 2, "routes/projects/core.py": 1,
                "routes/projects/due_day.py": 1, "kb_index/query.py": 1,
                "retrieval/sources.py": 1, "routes/projects/importer/layout.py": 1,
                "routes/projects/decisions.py": 1}
        found: dict[str, int] = {}
        for path in _GATEWAY.rglob("*.py"):
            hits = len(_CLOCK_COMPARE.findall(path.read_text(encoding="utf-8")))
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
        intake = (_GATEWAY / "routes/projects/intake.py").read_text(encoding="utf-8")
        # POST /projects/tasks and PATCH /projects/tasks/{id}: create_task,
        # create_tasks and update_task.
        assert tasks.count("await _store_due(db, user, values)") == 2
        # The one capture path, and organize.
        assert 'await stored_due(db, email, values["due_at"])' in mine
        assert "await stored_due(db, email, payload.due_at)" in mine
        # capture_intake.
        assert "await stored_due(db, actor(user).lower(), payload.due_at)" in intake

    def test_a_bulk_edit_of_a_member_reaches_the_seam(self):
        """The chat's ``bulk_update`` and the board's bulk edit send a date."""
        bulk = (_GATEWAY / "routes/projects/bulk.py").read_text(encoding="utf-8")
        assert bulk.count('await stored_due(db, who.lower(), patch["due_at"])') == 1
        # The seam comes before the loop over the tasks: one read, one instant.
        assert bulk.index("await stored_due(") < bulk.index("for task_id in ids:", bulk.index(
            "async def bulk_edit"))

    def test_a_draft_of_a_mail_capture_reaches_the_seam(self):
        """Both capture paths. A bare ``_parse_dt`` of a date is midnight UTC."""
        capture = (_GATEWAY / "routes/tasks/capture_email.py").read_text(encoding="utf-8")
        assert capture.count('await _draft_due(db, uid, draft.get("due_at", ""))') == 2
        assert '_parse_dt(draft.get("due_at"' not in capture

    def test_each_caller_of_the_patch_door_is_known(self):
        """``apply_task_patch`` keeps a date as it came, because a workflow has
        no member. A new caller for a member must call the seam first."""
        found = {path.relative_to(_GATEWAY).as_posix()
                 for path in _GATEWAY.rglob("*.py")
                 if "await apply_task_patch(" in path.read_text(encoding="utf-8")}
        assert found == {"routes/projects/bulk.py", "routes/workflows/service.py"}


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


# ── The plan phase of the tick ─────────────────────────────────────────────


class TestThePlanPhase:
    """A morning of summaries never delays a reminder step of a later org."""

    @pytest.fixture
    def tick(self, monkeypatch):
        calls: list[str] = []
        box = SimpleNamespace(calls=calls, clock=0.0, cost=0.0)

        async def orgs():
            return ["a", "b", "c"]

        async def run_org(org, now):
            calls.append(f"run:{org}")
            return Counter()

        async def plan_org(org, now, stopping=None):
            calls.append(f"plan:{org}")
            box.clock += box.cost
            return Counter(due_planned=1)

        async def send(orgs_, now, out):
            calls.append("send")

        monkeypatch.setattr(loop, "loop_enabled", lambda: True)
        monkeypatch.setattr(loop, "_orgs", orgs)
        monkeypatch.setattr(loop, "run_org", run_org)
        monkeypatch.setattr(loop.due_push, "plan_org", plan_org)
        monkeypatch.setattr(loop, "_send_phase", send)
        monkeypatch.setattr(loop, "_monotonic", lambda: box.clock)
        monkeypatch.setattr(loop, "_plan_turn", 0)
        loop._stopping.clear()
        return box

    async def test_each_reminder_step_comes_before_any_plan(self, tick):
        out = await loop.run_tick(datetime(2026, 11, 2, 12, tzinfo=UTC))
        assert tick.calls == ["run:a", "run:b", "run:c", "plan:a", "plan:b", "plan:c", "send"]
        assert out["a"]["due_planned"] == 1

    async def test_a_phase_out_of_time_starts_no_other_org(self, tick):
        tick.cost = loop.PLAN_PHASE_S
        await loop.run_tick(datetime(2026, 11, 2, 12, tzinfo=UTC))
        assert [c for c in tick.calls if c.startswith("plan")] == ["plan:a"]
        assert tick.calls[-1] == "send"

    async def test_each_tick_starts_at_the_next_org(self, tick):
        tick.cost = loop.PLAN_PHASE_S
        for _ in range(3):
            await loop.run_tick(datetime(2026, 11, 2, 12, tzinfo=UTC))
        assert [c for c in tick.calls if c.startswith("plan")] == ["plan:a", "plan:b", "plan:c"]

    async def test_a_plan_that_raises_stops_no_other_org(self, tick, monkeypatch):
        async def plan_org(org, now, stopping=None):
            tick.calls.append(f"plan:{org}")
            if org == "a":
                raise RuntimeError("boom")
            return Counter(due_planned=1)

        monkeypatch.setattr(loop.due_push, "plan_org", plan_org)
        out = await loop.run_tick(datetime(2026, 11, 2, 12, tzinfo=UTC))
        assert "a" not in out and out["b"]["due_planned"] == 1

    async def test_the_plan_reads_the_deadline_of_the_phase(self, tick, monkeypatch):
        """``plan_org`` stops between two members by the phase's own clock."""
        seen: list[bool] = []

        async def plan_org(org, now, stopping=None):
            seen.append(stopping())
            tick.clock += loop.PLAN_PHASE_S
            seen.append(stopping())
            return Counter()

        monkeypatch.setattr(loop.due_push, "plan_org", plan_org)
        await loop.run_tick(datetime(2026, 11, 2, 12, tzinfo=UTC))
        assert seen == [False, True]


# ── A summary takes a share of the send phase ──────────────────────────────


class TestTheSendShare:
    """A morning of summaries leaves a reminder its 90 s (§5.6)."""

    @pytest.fixture
    def phase(self, monkeypatch):
        box = SimpleNamespace(clock=0.0, sent=[], lists={})

        async def planned(org, *, limit):
            return box.lists[org][:limit]

        async def send_one(org, outbox_id, now):
            box.sent.append(outbox_id)
            box.lists[org] = [r for r in box.lists[org] if r[0] != outbox_id]
            box.clock += 1.0
            return "sent"

        monkeypatch.setattr(loop.push_channel, "push_on", lambda: True)
        monkeypatch.setattr(loop.push_channel.outbox, "planned_pushes", planned)
        monkeypatch.setattr(loop.push_channel, "_send_one", send_one)
        monkeypatch.setattr(loop, "_monotonic", lambda: box.clock)
        monkeypatch.setattr(loop, "_send_turn", 0)
        loop._stopping.clear()
        return box

    @staticmethod
    def _rows(prefix: str, count: int, kind: str = "due_summary") -> list[tuple[str, str]]:
        return [(f"{prefix}{n}", kind) for n in range(count)]

    async def test_a_summary_starts_only_in_its_share(self, phase):
        phase.lists = {"a": self._rows("s", 300)}
        await loop._send_phase(["a"], None, {})
        assert len(phase.sent) == loop.SUMMARY_SEND_S
        assert phase.clock < loop.SEND_PHASE_S

    async def test_the_send_stays_inside_the_90_seconds(self):
        """Each phase before the send of a reminder that fired just after the
        steps of a tick."""
        assert (loop.PLAN_PHASE_S + loop.SUMMARY_SEND_S + loop.TICK_SECONDS
                + loop.PLAN_PHASE_S) <= 90

    async def test_a_reminder_of_the_next_org_still_goes(self, phase):
        phase.lists = {"a": self._rows("s", 300),
                       "b": [("rem", "reminder_due"), *self._rows("t", 5)]}
        await loop._send_phase(["a", "b"], None, {})
        assert "rem" in phase.sent
        assert sum(1 for i in phase.sent if i != "rem") == loop.SUMMARY_SEND_S

    async def test_a_late_reminder_row_goes_past_the_share(self, phase):
        """The share stops a summary, never a reminder."""
        phase.lists = {"a": [*self._rows("r", 20, "reminder_due"), *self._rows("s", 5)]}
        await loop._send_phase(["a"], None, {})
        assert sum(1 for i in phase.sent if i.startswith("r")) == 20
        assert not [i for i in phase.sent if i.startswith("s")]

    async def test_a_later_tick_sends_the_rest(self, phase):
        phase.lists = {"a": self._rows("s", 20)}
        for _ in range(3):
            await loop._send_phase(["a"], None, {})
        assert len(phase.sent) == 20 and phase.lists["a"] == []

    async def test_each_tick_gives_the_share_to_the_next_org(self, phase):
        phase.lists = {"a": self._rows("a", 50), "b": self._rows("b", 50)}
        await loop._send_phase(["a", "b"], None, {})
        await loop._send_phase(["a", "b"], None, {})
        assert {i[0] for i in phase.sent} == {"a", "b"}


# ── A long list of members ─────────────────────────────────────────────────


class TestALongList:
    """More members in the window than one batch holds."""

    @staticmethod
    def _slots(count: int) -> list[due_push.Slot]:
        return [due_push.Slot(f"m{n:04d}@x.test", TODAY) for n in range(count)]

    @pytest.fixture
    def plan(self, monkeypatch):
        """``plan_org`` with a fake session, a fake plan of one member and a
        Redis that is down. *open* is the list of open slots of each tick."""
        box = SimpleNamespace(open=[], read=[], marks=[], fault=True, off=set())

        @contextlib.asynccontextmanager
        async def session(org):
            yield None

        async def open_slots(db, now):
            return list(box.open)

        async def unchecked(org, slots):
            return list(slots), box.fault

        async def plan_one(org, slot, now, *, marks=True):
            box.read.append(slot.member)
            box.marks.append(marks)
            return "due_nothing"

        async def day_off(db, slots):
            return {s for s in slots if s.member in box.off}

        async def mark(org, slot):
            box.marks.append(True)

        monkeypatch.setattr(due_push.push_channel, "push_on", lambda: True)
        monkeypatch.setattr(flags, "due_push_allowed", lambda org: True)
        monkeypatch.setattr(due_push, "tenant_session", session)
        monkeypatch.setattr(due_push, "_open_slots", open_slots)
        monkeypatch.setattr(due_push, "_unchecked", unchecked)
        monkeypatch.setattr(due_push, "_plan_one", plan_one)
        monkeypatch.setattr(due_push, "_day_off", day_off)
        monkeypatch.setattr(due_push, "_mark_checked", mark)
        monkeypatch.setattr(due_push, "_next_start", {})
        return box

    async def test_with_redis_down_each_member_gets_a_turn(self, plan):
        plan.open = self._slots(due_push.PLAN_BATCH + 50)
        ticks = -(-len(plan.open) // due_push.FAULT_BATCH)
        for _ in range(ticks):
            await due_push.plan_org("o", _ist(2, 8))
        assert set(plan.read) == {s.member for s in plan.open}

    async def test_with_redis_down_a_tick_reads_a_small_batch_and_writes_no_mark(self, plan):
        plan.open = self._slots(due_push.PLAN_BATCH + 50)
        await due_push.plan_org("o", _ist(2, 8))
        assert len(plan.read) == due_push.FAULT_BATCH < due_push.PLAN_BATCH
        assert not any(plan.marks)

    async def test_with_redis_up_a_tick_reads_a_full_batch_and_the_next_tick_moves_on(self, plan):
        plan.fault = False
        plan.open = self._slots(due_push.PLAN_BATCH * 2 + 1)
        await due_push.plan_org("o", _ist(2, 8))
        assert len(plan.read) == due_push.PLAN_BATCH and all(plan.marks)
        first = set(plan.read)
        await due_push.plan_org("o", _ist(2, 8))
        assert len(set(plan.read) - first) == due_push.PLAN_BATCH

    async def test_a_short_list_needs_no_turn(self, plan):
        plan.open = self._slots(5)
        await due_push.plan_org("o", _ist(2, 8))
        await due_push.plan_org("o", _ist(2, 8))
        assert plan.read == [s.member for s in plan.open] * 2

    async def test_one_org_keeps_its_own_turn(self, plan):
        plan.open = self._slots(due_push.FAULT_BATCH * 3)
        await due_push.plan_org("a", _ist(2, 8))
        await due_push.plan_org("b", _ist(2, 8))
        assert plan.read[0] == plan.read[due_push.FAULT_BATCH]

    async def test_a_day_off_reads_no_task_and_leaves_a_mark(self, plan):
        plan.fault = False
        plan.open = self._slots(2)
        plan.off = {plan.open[0].member}
        got = await due_push.plan_org("o", _ist(2, 8))
        assert plan.read == [plan.open[1].member]
        assert got["due_day_off"] == 1 and len(plan.marks) == 2

    async def test_a_day_off_with_redis_down_writes_no_mark(self, plan):
        plan.open = self._slots(1)
        plan.off = {plan.open[0].member}
        assert (await due_push.plan_org("o", _ist(2, 8)))["due_day_off"] == 1
        assert plan.marks == []

    async def test_a_failed_read_of_the_working_day_plans_nothing(self, plan, monkeypatch):
        async def boom(db, slots):
            raise RuntimeError("relation people does not exist")

        monkeypatch.setattr(due_push, "_day_off", boom)
        plan.fault = False
        plan.open = self._slots(1)
        assert (await due_push.plan_org("o", _ist(2, 8)))["due_failed"] == 1
        assert plan.read == []


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
