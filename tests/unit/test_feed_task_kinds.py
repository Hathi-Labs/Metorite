"""WS-58 MD-3c — the task kinds of My Day (``my_day_feed.md`` §5.9 items 1
to 7 and 11).

The source ``tasks_followups`` gives four kinds about the member's own
tasks: ``waiting_quiet``, ``stale_next``, ``unblocked`` and
``absent_on_due``. The R8 half, with the real SQL of the three new lens
reads, is ``test_feed_task_kinds_r8.py``. This file fences what the feed
does with the rows, and the shared rows of all eleven MD-3 kinds:

* **Dark by the list.** With ``tasks_followups`` not in ``MY_DAY_PROVIDERS``,
  no read runs and the answer has no new byte (done-when 1).
* **Each kind gives its card** with the ``BASE``, group, ``why_key``, text,
  act and label of §5.9 (done-when 2), and a fallback text where §5.9 names
  one.
* **No People row is no absence** (done-when 10): the source reads ``ok``,
  and the other kinds still come. Any other error of the absence read fails
  the source.
* **A folded task kind keeps the state fingerprint** (done-when 11).
* **The carry field** ``source`` (§5.9 item 1): sent only when it is not the
  app, so a card of an MD-1 source keeps its bytes.
* **The reads** (done-when 13): 4 at most, and ``my_due_ahead`` only when an
  absence falls in the window.
* **The absence writes bump the org** (done-when 14), while ``MY_DAY_CACHE``
  is on.
* **One table for every kind.** Each kind of ``feed_rank.BASE`` has a group,
  a why template, a label in ``feed.ts`` and, for a task kind, a ``CLOSES``
  entry. Each kind that a provider gives is in ``BASE``.

Mutations, run by hand (2026-10-15), each killed: the list is in the MD-3c
ticket of ``my_day_feed.md`` §16.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from gateway.routes.shell import feed, feed_rank, state
from gateway.routes.shell import needs as shell

from tests.unit.test_shell_feed import (  # noqa: F401  (fixtures by name)
    FULL,
    FeedFakes,
    _bound,
    _list,
    card,
    fakes,
    member,
    run_feed,
)

REPO = Path(__file__).resolve().parents[2]
FEED_TS = REPO / "workbench/control_plane/src/lib/shell/feed.ts"
TASK_KINDS = ("waiting_quiet", "stale_next", "unblocked", "absent_on_due")
MD3_KINDS = (*TASK_KINDS, "promise_due", "no_answer", "crm_followup_due", "deal_quiet",
             "deal_close_passed", "colleague_out", "agent_asks")


def _row(task_id: str, title: str, **extra) -> dict:
    """One lens row, as ``personal._project_task`` gives it."""
    return {"id": task_id, "title": title, "importance": None, "leveraged": False,
            "project_name": "Plant", "due_at": None, **extra}


class TaskReads:
    """The three new lens reads and the self absence read, each answering by
    the member it is handed and recording the call."""

    def __init__(self, f: FeedFakes) -> None:
        self.f = f
        now = f.now
        self.calls: list[str] = []
        self.quiet: dict[str, list[dict]] = {}
        self.unblocked: dict[str, list[dict]] = {}
        self.ahead: dict[str, list[dict]] = {}
        #: The absences, by member. None is "no People row", the 404.
        self.absences: dict[str, list[dict] | None] = {}
        self.absence_error: Exception | None = None
        self.day_start_hour: int | None = 7
        self.since: list[datetime] = []
        self.windows: list[tuple[datetime, datetime]] = []
        self.days: list[tuple[list[date], str]] = []
        self.todays: list[date | None] = []
        self.now = now

    async def my_quiet_tasks(self, user, *, limit, now=None):
        self.calls.append("my_quiet_tasks")
        return {"rows": [dict(r) for r in self.quiet.get(user.email, [])][:limit],
                "today": self.f._today().isoformat(), "timezone": self.f.zone,
                "day_start_hour": self.day_start_hour}

    async def my_unblocked_since(self, user, since, *, limit, today=None):
        self.calls.append("my_unblocked_since")
        self.since.append(since)
        self.todays.append(today)
        return {"rows": [dict(r) for r in self.unblocked.get(user.email, [])][:limit]}

    async def my_due_ahead(self, user, start, end, *, limit, days, zone):
        # The real contract: the window, then only the asked local days.
        self.calls.append("my_due_ahead")
        self.windows.append((start, end))
        self.days.append((list(days), zone))
        tz = ZoneInfo(zone)
        rows = [dict(r) for r in self.ahead.get(user.email, [])
                if start <= datetime.fromisoformat(r["due_at"]) < end
                and datetime.fromisoformat(r["due_at"]).astimezone(tz).date() in days]
        return {"rows": rows[:limit]}

    async def my_absences(self, *, user):
        self.calls.append("my_absences")
        if self.absence_error is not None:
            raise self.absence_error
        rows = self.absences.get(user.email, [])
        if rows is None:
            raise HTTPException(status_code=404, detail="No person in the directory")
        return {"rows": [dict(r) for r in rows]}


@pytest.fixture()
def reads(fakes, monkeypatch) -> TaskReads:  # noqa: F811
    from gateway.routes.people import selfservice
    from gateway.routes.projects import personal

    r = TaskReads(fakes)
    monkeypatch.setattr(personal, "my_quiet_tasks", r.my_quiet_tasks)
    monkeypatch.setattr(personal, "my_unblocked_since", r.my_unblocked_since)
    monkeypatch.setattr(personal, "my_due_ahead", r.my_due_ahead)
    monkeypatch.setattr(selfservice, "my_absences", r.my_absences)
    return r


def _tz(f: FeedFakes) -> ZoneInfo:
    return ZoneInfo(f.zone)


def _seed(r: TaskReads, me: str) -> dict[str, str]:
    """One task of each kind. The due day of `t-away` is a day I am away."""
    now, tz = r.now, _tz(r.f)
    today = now.astimezone(tz).date()
    away_day = today + timedelta(days=3)
    r.quiet[me] = [
        _row("t-wait", "Get the quote", disposition="WAITING",
             delegated_at=(now - timedelta(days=6, hours=1)).isoformat(),
             waiting_on={"name": "ravi@x.test", "email": "ravi@x.test"}),
        _row("t-stale", "Draft the manual", disposition="NEXT",
             last_change_at=(now - timedelta(days=20)).isoformat()),
    ]
    r.unblocked[me] = [
        _row("t-free", "Mount the sensor", unblocked_by={
            "readable": True, "id": "b-1", "title": "Ship the API",
            "closed_by": "ravi@x.test", "closed_at": (now - timedelta(minutes=30)).isoformat()}),
    ]
    due = datetime.combine(away_day, datetime.min.time(), tzinfo=tz) + timedelta(hours=11)
    r.ahead[me] = [_row("t-away", "Pay the vendor", due_at=due.isoformat())]
    r.absences[me] = [{"starts_on": away_day.isoformat(), "ends_on": away_day.isoformat(),
                       "kind": "holiday"}]
    return {"away_day": away_day.isoformat()}


def _labels() -> dict[str, str]:
    text = FEED_TS.read_text(encoding="utf-8")
    block = text.split("export const FEED_KIND_LABELS", 1)[1].split("};", 1)[0]
    return dict(re.findall(r"^\s*([a-z_]+): \"([^\"]+)\",", block, re.MULTILINE))


def _closes_done() -> set[str]:
    text = FEED_TS.read_text(encoding="utf-8")
    block = text.split("const CLOSES", 1)[1].split("read:", 1)[0]
    return set(re.findall(r"\"([a-z_]+)\"", block))


# ── done-when 1: dark until listed ──────────────────────────────────────────


class TestDarkUntilListed:
    def test_the_provider_is_gated_and_feed_only(self):
        p = shell.REGISTRY["tasks_followups"]
        assert (p.gated, p.needs, p.features, p.app, p.handled, p.permission) == (
            True, False, ("projects",), "tasks", None, None)
        assert p.kinds == frozenset(TASK_KINDS)
        assert shell.source_app("tasks_followups") == "tasks"

    def test_with_the_list_empty_no_read_runs_and_no_byte_changes(self, reads, monkeypatch):
        _list(monkeypatch, "")
        _seed(reads, member().email)
        answer = run_feed(member(*FULL))
        assert reads.calls == []
        assert "tasks_followups" not in answer["sources"]
        assert "tasks_followups" not in answer["source_apps"]
        assert all("source" not in c for c in answer["cards"])
        assert not {c["kind"] for c in answer["cards"]} & set(TASK_KINDS)

    def test_listed_it_runs(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        _seed(reads, member().email)
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks_followups"] == "ok"
        assert answer["source_apps"]["tasks_followups"] == "tasks"

    def test_a_member_without_projects_gets_absent_and_no_read(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        answer = run_feed(member("email", "tasks"))
        assert answer["sources"]["tasks_followups"] == "absent"
        assert reads.calls == []


# ── done-when 2: each kind gives its card ───────────────────────────────────


class TestEachKindGivesItsCard:
    @pytest.fixture()
    def answer(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        self.seed = _seed(reads, member().email)
        return run_feed(member(*FULL))

    def test_waiting_quiet(self, answer):
        c = card(answer, "tasks:t-wait")
        assert (c["kind"], c["app"], c["group"], c["why_key"]) == (
            "waiting_quiet", "tasks", "needs_you", "waited")
        assert c["why"] == "You have waited 6 days on ravi."
        assert c["acts"] == [{"id": "nudge", "label": "Nudge ravi", "class": "B",
                              "way": "open", "href": "/projects?task=t-wait"}]
        assert c["score"] == feed_rank.BASE["waiting_quiet"] == 37
        assert c["urgent_at"] is None

    def test_stale_next(self, answer):
        c = card(answer, "tasks:t-stale")
        assert (c["kind"], c["group"], c["why_key"]) == ("stale_next", "fyi", "no_change")
        assert c["why"] == "No change for 20 days."
        assert [(a["id"], a["label"], a["class"], a["way"]) for a in c["acts"]] == [
            ("move-someday", "Move to Someday", "A", "open")]
        assert c["score"] == 30

    def test_unblocked(self, answer):
        c = card(answer, "tasks:t-free")
        assert (c["kind"], c["group"], c["why_key"]) == ("unblocked", "ready", "unblocked")
        assert c["why"] == "ravi closed Ship the API, which you waited on."
        assert [(a["id"], a["label"]) for a in c["acts"]] == [("open", "Open")]
        assert c["facts"]["blocker_ids"] == ["b-1"]
        assert c["score"] == 38

    def test_absent_on_due(self, answer):
        c = card(answer, "tasks:t-away")
        assert (c["kind"], c["group"], c["why_key"]) == (
            "absent_on_due", "needs_you", "absent_on_due")
        day = date.fromisoformat(self.seed["away_day"])
        assert c["why"] == f"Due on {day.strftime('%A')}. You are away that day."
        assert c["facts"]["absence_day"] == self.seed["away_day"]
        assert [(a["id"], a["label"]) for a in c["acts"]] == [("move-date", "Move the date")]
        assert c["score"] == 39

    def test_each_card_names_its_source(self, answer):
        for sid in ("tasks:t-wait", "tasks:t-stale", "tasks:t-free", "tasks:t-away"):
            assert card(answer, sid)["source"] == "tasks_followups"
        # A card of an MD-1 source keeps its bytes: no `source` field.
        for c in answer["cards"]:
            if c["kind"] not in TASK_KINDS:
                assert "source" not in c, c["signal_id"]

    def test_each_card_has_its_label(self, answer):
        labels = _labels()
        for c in answer["cards"]:
            assert c["kind"] in labels, c["kind"]


class TestTheFallbacks:
    def test_a_waiting_task_that_names_nobody(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        me = member().email
        reads.quiet[me] = [_row("t-anon", "Chase it", disposition="WAITING",
                                delegated_at=(reads.now - timedelta(days=9)).isoformat(),
                                waiting_on=None)]
        c = card(run_feed(member(*FULL)), "tasks:t-anon")
        assert (c["why_key"], c["why"]) == ("waited_anon", "You have waited 9 days for this.")
        assert c["acts"][0]["label"] == "Nudge"

    def test_an_unreadable_blocker_holds_no_title_no_id_and_no_actor(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        me = member().email
        reads.unblocked[me] = [_row("t-hid", "Mount it", unblocked_by={
            "readable": False, "id": None, "title": None, "closed_by": None,
            "closed_at": (reads.now - timedelta(minutes=5)).isoformat()})]
        c = card(run_feed(member(*FULL)), "tasks:t-hid")
        assert (c["why_key"], c["why"]) == ("unblocked_hidden",
                                            "A task that blocked this is closed.")
        assert not {"blocker", "blocker_ids", "who"} & set(c["facts"])

    def test_a_task_with_no_delegated_at_never_fires(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        me = member().email
        reads.quiet[me] = [_row("t-none", "Chase it", disposition="WAITING",
                                delegated_at=None, waiting_on=None)]
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks_followups"] == "ok"
        assert "tasks:t-none" not in [c["signal_id"] for c in answer["cards"]]


# ── absences: my own, full, and a 404 is no absence ─────────────────────────


class TestTheAbsences:
    def test_a_member_with_no_people_row_gets_the_other_kinds(self, reads, monkeypatch):
        """Done-when 10: the 404 is no absence, not SourceAbsent."""
        _list(monkeypatch, "tasks_followups")
        me = member().email
        _seed(reads, me)
        reads.absences[me] = None
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks_followups"] == "ok"
        kinds = {c["kind"] for c in answer["cards"]}
        assert {"waiting_quiet", "stale_next", "unblocked"} <= kinds
        assert "absent_on_due" not in kinds
        assert "my_due_ahead" not in reads.calls

    def test_another_error_of_the_absence_read_fails_the_source(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        _seed(reads, member().email)
        reads.absence_error = HTTPException(status_code=500, detail="down")
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks_followups"] == "failed"

    def test_a_partial_absence_reads_no_day_away(self, reads, monkeypatch):
        """Done-when 9, the provider half (the R8 suite holds the other)."""
        _list(monkeypatch, "tasks_followups")
        me = member().email
        seed = _seed(reads, me)
        reads.absences[me] = [{"starts_on": seed["away_day"], "ends_on": seed["away_day"],
                               "kind": "partial"}]
        answer = run_feed(member(*FULL))
        assert "my_due_ahead" not in reads.calls
        assert "tasks:t-away" not in [c["signal_id"] for c in answer["cards"]]

    def test_an_absence_past_the_window_reads_no_day(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        me = member().email
        _seed(reads, me)
        today = reads.now.astimezone(_tz(reads.f)).date()
        far = today + timedelta(days=feed.ABSENCE_AHEAD_DAYS + 1)
        reads.absences[me] = [{"starts_on": far.isoformat(), "ends_on": far.isoformat(),
                               "kind": "away"}]
        run_feed(member(*FULL))
        assert "my_due_ahead" not in reads.calls

    def test_a_task_due_on_a_day_i_work_gives_no_row(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        me = member().email
        seed = _seed(reads, me)
        day = date.fromisoformat(seed["away_day"])
        other = datetime.combine(day + timedelta(days=1), datetime.min.time(),
                                 tzinfo=_tz(reads.f)) + timedelta(hours=9)
        reads.ahead[me] = [_row("t-work", "Pay it", due_at=other.isoformat())]
        answer = run_feed(member(*FULL))
        assert "tasks:t-work" not in [c["signal_id"] for c in answer["cards"]]

    def test_the_window_of_the_due_read_is_the_days_away(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        seed = _seed(reads, member().email)
        run_feed(member(*FULL))
        tz = _tz(reads.f)
        day = date.fromisoformat(seed["away_day"])
        assert reads.windows == [(datetime.combine(day, datetime.min.time(), tzinfo=tz),
                                  datetime.combine(day + timedelta(days=1),
                                                   datetime.min.time(), tzinfo=tz))]
        # The days themselves go to the read, in the member's zone (fix round 1).
        assert reads.days == [([day], reads.f.zone)]

    def test_the_unblocked_read_gets_my_today(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        run_feed(member(*FULL))
        assert reads.todays == [reads.now.astimezone(_tz(reads.f)).date()]


class TestTheReads:
    def test_four_reads_at_most_in_order(self, reads, monkeypatch):
        """Done-when 13, the provider half: each read opens one session (the
        R8 suite counts the sessions themselves)."""
        _list(monkeypatch, "tasks_followups")
        _seed(reads, member().email)
        run_feed(member(*FULL))
        assert reads.calls == ["my_quiet_tasks", "my_unblocked_since", "my_absences",
                               "my_due_ahead"]

    def test_since_is_the_start_of_my_day(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        reads.day_start_hour = 5
        run_feed(member(*FULL))
        day = state.Day(_tz(reads.f), 5)
        assert reads.since == [day.day_start(reads.now)]

    def test_no_stored_hour_reads_as_the_column_default(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        reads.day_start_hour = None
        run_feed(member(*FULL))
        assert reads.since == [state.Day(_tz(reads.f), 7).day_start(reads.now)]

    def test_15_signals_at_most_by_base_then_age(self, reads, monkeypatch):
        _list(monkeypatch, "tasks_followups")
        me = member().email
        reads.quiet[me] = [
            _row(f"t-s{n:02d}", f"Stale {n}", disposition="NEXT",
                 last_change_at=(reads.now - timedelta(days=15 + n)).isoformat())
            for n in range(14)
        ] + [_row("t-w", "Wait", disposition="WAITING",
                  delegated_at=(reads.now - timedelta(days=7)).isoformat(), waiting_on=None)]
        reads.unblocked[me] = [_row("t-u", "Free", unblocked_by={
            "readable": False, "closed_at": reads.now.isoformat()})]
        found = _bound(lambda: feed._tasks_followups(member(*FULL)))
        signals = found.signals()
        assert found.capped is True
        assert len(signals) == shell.PER_APP
        assert [s.kind for s in signals[:2]] == ["unblocked", "waiting_quiet"]
        # The oldest stale task first, and the newest one is cut.
        stale = [s.signal_id for s in signals if s.kind == "stale_next"]
        assert stale[0] == "tasks:t-s13" and "tasks:t-s00" not in stale


# ── done-when 11: a fold keeps the state fingerprint ────────────────────────


def _signal(kind: str, *, app: str, facts: dict, acts=()) -> shell.Signal:
    return shell.Signal(signal_id="tasks:t-1", app=app, kind=kind,
                        entity=shell.task_entity("t-1", "One"), title="One",
                        facts=facts, href="/projects?task=t-1", acts=tuple(acts))


#: The facts each task kind carries, as the provider builds them.
TASK_FACTS = {
    "waiting_quiet": {"delegated_at": "2026-10-01T09:00:00+00:00", "waited_days": 11,
                      "who": "ravi", "priority": None, "project": "Plant"},
    "stale_next": {"changed_at": "2026-09-01T09:00:00+00:00", "stale_days": 41,
                   "priority": None, "project": "Plant"},
    "unblocked": {"unblocked_at": "2026-10-12T09:00:00+00:00", "blocker": "API",
                  "blocker_ids": ["b-1"], "who": "ravi", "priority": None, "project": "Plant"},
    "absent_on_due": {"absence_day": "2026-10-15", "priority": None, "project": "Plant"},
}


class TestAFoldKeepsTheStateFingerprint:
    MAINS: ClassVar[dict[str, dict]] = {
        "overdue": {"due_at": "2026-10-10T09:00:00+00:00", "days_late": 2,
                    "priority": None},
        "due_today": {"due_at": "2026-10-12T23:59:00+00:00", "priority": None},
        "notification": {"notification": "comment", "who": "ravi", "at": None,
                         "note_ids": ["n-1"], "unread": 1},
    }

    @pytest.mark.parametrize("folded", TASK_KINDS)
    @pytest.mark.parametrize("main", ["overdue", "due_today", "notification"])
    def test_each_task_kind_folded_into_each_main(self, main, folded):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
        tz, today = ZoneInfo("UTC"), now.date()
        app = "projects" if main == "notification" else "tasks"
        alone = feed.build_cards([_signal(main, app=app, facts=self.MAINS[main])], now, tz, today)
        both = feed.build_cards([_signal(main, app=app, facts=self.MAINS[main]),
                                 _signal(folded, app="tasks", facts=TASK_FACTS[folded])],
                                now, tz, today)
        assert both[0]["folded"] == [folded]
        assert state.state_fp("tasks:t-1", both[0]["facts"]) == \
            state.state_fp("tasks:t-1", alone[0]["facts"])

    def test_no_task_kind_names_a_state_fact(self):
        for kind, facts in TASK_FACTS.items():
            assert not set(facts) & set(state.STATE_FACTS), kind

    def test_the_provider_s_own_signals_keep_a_notification_card_s_fingerprint(self):
        """The signals as the provider builds them, from rows that DO have a
        due time: none carries it, so a fold into a notification card (no
        ``due_at``) keeps the state fingerprint."""
        tz = ZoneInfo("UTC")
        now = datetime(2026, 10, 12, 12, 0, tzinfo=tz)
        today = now.date()
        due = "2026-10-15T11:00:00+00:00"
        made = [
            feed._quiet_signal(_row("t-1", "One", due_at=due, disposition="WAITING",
                                    delegated_at="2026-10-01T09:00:00+00:00",
                                    waiting_on={"name": "Ravi"}), today, tz),
            feed._quiet_signal(_row("t-1", "One", due_at=due, disposition="NEXT",
                                    last_change_at="2026-09-01T09:00:00+00:00"), today, tz),
            feed._unblocked_signal(_row("t-1", "One", due_at=due, unblocked_by={
                "readable": True, "id": "b-1", "title": "API", "closed_by": "r@x.test",
                "closed_at": "2026-10-12T09:00:00+00:00"})),
            feed._absent_signal(_row("t-1", "One", due_at=due), {date(2026, 10, 15)}, tz),
        ]
        assert [s.kind for s in made] == list(TASK_KINDS)
        main = _signal("notification", app="projects", facts=self.MAINS["notification"])
        alone = feed.build_cards([main], now, tz, today)[0]
        for signal in made:
            assert not set(signal.facts) & set(state.STATE_FACTS), signal.kind
            both = feed.build_cards([main, signal], now, tz, today)[0]
            assert both["folded"] == [signal.kind]
            assert state.state_fp("tasks:t-1", both["facts"]) == \
                state.state_fp("tasks:t-1", alone["facts"]), signal.kind


class TestTheFoldedClauses:
    def test_absent_on_due_folds_its_own_sentence(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
        cards = feed.build_cards(
            [_signal("overdue", app="tasks", facts=TestAFoldKeepsTheStateFingerprint.MAINS[
                "overdue"]),
             _signal("absent_on_due", app="tasks", facts=TASK_FACTS["absent_on_due"])],
            now, ZoneInfo("UTC"), now.date())
        assert cards[0]["why"].endswith(" You are away on its due day.")


# ── the carry field (§5.9 item 1) ───────────────────────────────────────────


class TestTheSourceOfACard:
    def test_the_main_signal_s_source_and_only_when_it_is_not_the_app(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
        tz, today = ZoneInfo("UTC"), now.date()
        main = _signal("overdue", app="tasks", facts={"due_at": None})
        quiet = _signal("waiting_quiet", app="tasks", facts=TASK_FACTS["waiting_quiet"])
        import dataclasses

        tagged = [dataclasses.replace(main, source="tasks"),
                  dataclasses.replace(quiet, source="tasks_followups")]
        assert "source" not in feed.build_cards(tagged, now, tz, today)[0]
        alone = feed.build_cards(tagged[1:], now, tz, today)[0]
        assert alone["source"] == "tasks_followups"
        assert list(alone)[:3] == ["signal_id", "app", "source"]


# ── done-when 14: an absence write bumps the org ────────────────────────────


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestTheAbsenceWritesBumpTheOrg:
    @pytest.fixture()
    def bumps(self, monkeypatch):
        from acb_common.settings import get_settings
        from gateway.routes.people import selfservice
        from gateway.routes.shell import cache

        got: list[str] = []

        async def _set(org, _msg):
            got.append(org)

        monkeypatch.setattr(get_settings(), "my_day_cache", True)
        monkeypatch.setattr(cache, "_set_org_version", _set)
        monkeypatch.setattr(selfservice, "_tenant_session", lambda: _Session())

        async def _row(db, user):
            return SimpleNamespace(id="p-1")

        async def _created(db, pid, body, by):
            return {"id": "a-1"}

        async def _deleted(db, pid, aid):
            return aid == "a-1"

        monkeypatch.setattr(selfservice, "find_self_row", _row)
        monkeypatch.setattr(selfservice, "create_absence", _created)
        monkeypatch.setattr(selfservice, "delete_absence", _deleted)
        return got

    def _user(self):
        import dataclasses

        return dataclasses.replace(member("projects"), organization_id="org-mine")

    def test_post_and_delete_bump_the_org_version(self, bumps):
        from gateway.routes.people import selfservice
        from gateway.routes.people.absences import AbsenceIn

        body = AbsenceIn(starts_on="2026-10-20", ends_on="2026-10-21")
        asyncio.run(selfservice.add_my_absence(body, user=self._user()))
        assert bumps == ["org-mine"]
        asyncio.run(selfservice.remove_my_absence("a-1", user=self._user()))
        assert bumps == ["org-mine", "org-mine"]

    def test_a_delete_that_deleted_nothing_bumps_nothing(self, bumps):
        from gateway.routes.people import selfservice

        with pytest.raises(HTTPException):
            asyncio.run(selfservice.remove_my_absence("a-9", user=self._user()))
        assert bumps == []

    def test_with_the_cache_off_nothing_bumps(self, bumps, monkeypatch):
        from acb_common.settings import get_settings
        from gateway.routes.people import selfservice

        monkeypatch.setattr(get_settings(), "my_day_cache", False)
        asyncio.run(selfservice.remove_my_absence("a-1", user=self._user()))
        assert bumps == []

    def test_a_broken_bump_never_fails_the_write(self, bumps, monkeypatch):
        """Fix round 1: the write committed, so a bump that raises is logged."""
        from gateway.routes.people import selfservice
        from gateway.routes.people.absences import AbsenceIn
        from gateway.routes.shell import cache

        async def _broken(org):
            raise RuntimeError("redis down")

        monkeypatch.setattr(cache, "bump_org", _broken)
        body = AbsenceIn(starts_on="2026-10-20", ends_on="2026-10-21")
        assert asyncio.run(selfservice.add_my_absence(body, user=self._user())) == {"id": "a-1"}
        assert asyncio.run(selfservice.remove_my_absence("a-1", user=self._user())) == {
            "deleted": "a-1"}

    def test_a_stuck_bump_is_cut_and_the_write_answers(self, bumps, monkeypatch):
        """The guard cuts a bump that never ends (``BUMP_SAFE_WAIT_S``). The
        hang guard is a named bound, never a measure of the cut."""
        from gateway.routes.people import selfservice
        from gateway.routes.shell import cache

        cut: list[str] = []

        async def _stuck(org):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cut.append(org)
                raise

        monkeypatch.setattr(cache, "bump_org", _stuck)
        done = asyncio.run(asyncio.wait_for(
            selfservice.remove_my_absence("a-1", user=self._user()), HANG_GUARD_S))
        assert done == {"deleted": "a-1"}
        assert cut == ["org-mine"]
        assert cache.BUMP_SAFE_WAIT_S == 0.5


#: A hang guard for the stuck-bump case (#947): the case fails only if the
#: write never answers. It measures nothing.
HANG_GUARD_S = 30.0


# ── one table for every kind (§5.9 items 2 to 6) ────────────────────────────


class TestOneTableForEveryKind:
    def test_each_provider_kind_is_in_base(self):
        for provider in shell.REGISTRY.values():
            assert provider.kinds <= set(feed_rank.BASE), provider.source

    def test_the_md3_kinds_sit_under_every_needs_kind(self):
        assert set(MD3_KINDS) <= set(feed_rank.BASE)
        assert max(feed_rank.BASE[k] for k in MD3_KINDS) < min(
            feed_rank.BASE[k] for k in shell.KIND_ORDER)

    def test_each_kind_has_a_group_and_a_why_template(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
        for kind in feed_rank.BASE:
            assert feed_rank.group(kind, {}, now, now.date()) in feed_rank.GROUPS
            key, line = feed.why(kind, {}, None, now, ZoneInfo("UTC"), now.date())
            assert key and line.endswith("."), kind

    def test_each_kind_has_its_label_in_the_client(self):
        assert set(_labels()) == set(feed_rank.BASE)
        assert _labels()["absent_on_due"] == "Due while you are away"

    def test_a_done_closes_each_task_kind_and_promise_due(self):
        done = _closes_done()
        assert {*TASK_KINDS, "promise_due"} <= done
        assert not {"no_answer", "crm_followup_due", "deal_quiet", "deal_close_passed",
                    "colleague_out", "agent_asks"} & done

    def test_the_stable_facts_of_md3(self):
        assert {"delegated_at", "blocker_ids", "absence_day", "out",
                "request_ids"} <= set(feed.STABLE_FACTS)
        # STATE_FACTS does not grow (§5.9 item 4).
        assert state.STATE_FACTS == ("due_at", "note_ids", "waiting_since", "message_id")

    @pytest.mark.parametrize(("kind", "facts", "key", "line"), [
        ("promise_due", {"due_at": "2026-10-13T09:00:00+00:00"}, "promised",
         "You promised this by mail. It is due tomorrow."),
        ("no_answer", {"waiting_since": "2026-10-09T09:00:00+00:00", "who": "dana"},
         "no_answer", "You wrote on Friday. dana has not answered."),
        ("crm_followup_due", {"due_at": "2026-10-12T15:00:00+00:00", "subject": "call Acme"},
         "followup_due", "Call Acme is due today at 15:00."),
        ("crm_followup_due", {"due_at": "2026-10-12T15:00:00+00:00"},
         "followup_due", "A follow-up is due today at 15:00."),
        ("deal_quiet", {"stage": "Proposal", "stage_days": 12, "stage_limit": 10},
         "in_stage", "In Proposal for 12 days. The stage limit is 10 days."),
        ("deal_close_passed", {"due_at": "2026-10-11T00:00:00+00:00"}, "close_passed",
         "The close date was yesterday, and the deal is still open."),
        ("colleague_out", {"out": ["Priya"], "task": "the BOM"}, "colleague_out",
         "Priya is away today. You share the BOM with them."),
        ("agent_asks", {"waiting_since": "2026-10-12T10:30:00+00:00"}, "asks",
         "Asked today at 10:30. The run waits for your answer."),
    ])
    def test_the_templates_of_the_later_slices(self, kind, facts, key, line):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
        assert feed.why(kind, facts, None, now, ZoneInfo("UTC"), now.date()) == (key, line)


# ── §5.9 item 7: the elapsed time of each provider run ──────────────────────


#: The handler of the gateway prints the message alone
#: (``acb_common._log.configure_logging``), so a field must be in the text.
_AS_PRINTED = logging.Formatter("%(message)s")
_FIELDS = re.compile(r"^(?P<event>\S+) provider (?P<what>.+?) provider=(?P<provider>\S+)"
                     r"(?P<rest>(?: \w+=\S+)*) elapsed_ms=(?P<ms>\d+)$")


def _printed(caplog) -> list[dict]:
    """Each provider line as the journal prints it, parsed back."""
    out = []
    for record in caplog.records:
        found = _FIELDS.match(_AS_PRINTED.format(record))
        if found:
            fields = dict(f.split("=", 1) for f in found["rest"].split())
            out.append({**found.groupdict(), **fields, "level": record.levelno})
    return out


class TestTheElapsedTime:
    def test_each_run_prints_its_fields_in_the_text(self, reads, monkeypatch, caplog):
        _list(monkeypatch, "tasks_followups")
        with caplog.at_level(logging.DEBUG, logger=shell.logger.name):
            run_feed(member(*FULL))
        lines = {x["provider"]: x for x in _printed(caplog)
                 if x["event"] == "shell.feed" and x["what"] == "answered"}
        assert set(lines) >= {"calendar", "tasks_followups"}
        for line in lines.values():
            assert line["outcome"] == "ok" and int(line["ms"]) >= 0
            assert line["level"] == logging.INFO
        text = " ".join(_AS_PRINTED.format(r) for r in caplog.records)
        assert "provider=tasks_followups" in text and "elapsed_ms=" in text

    def test_the_needs_providers_answer_at_debug(self, reads, monkeypatch, caplog):
        """Volume: the needs providers run on each poll of the bell."""
        _list(monkeypatch, "")
        with caplog.at_level(logging.DEBUG, logger=shell.logger.name):
            run_feed(member(*FULL))
        needs_lines = [x for x in _printed(caplog)
                       if x["event"] == "shell.needs" and x["what"] == "answered"]
        assert {x["provider"] for x in needs_lines} >= {"tasks", "projects", "email"}
        assert {x["level"] for x in needs_lines} == {logging.DEBUG}

    def test_a_failure_prints_it_too(self, reads, monkeypatch, caplog):
        _list(monkeypatch, "tasks_followups")
        reads.absence_error = RuntimeError("down")
        with caplog.at_level(logging.INFO, logger=shell.logger.name):
            run_feed(member(*FULL))
        [line] = [x for x in _printed(caplog)
                  if x["what"] == "failed" and x["provider"] == "tasks_followups"]
        assert line["error"] == "RuntimeError" and int(line["ms"]) >= 0

    def test_a_run_cut_at_the_ceiling_prints_its_time(self, fakes, monkeypatch, caplog):  # noqa: F811
        """A cancel skips the handlers of `_attempt`, so the ceiling line
        carries the measure. A provider that never got a slot ran 0 ms."""
        from tests.unit.test_shell_feed import NO_CALENDAR, _feed_only, _recorder, _with

        monkeypatch.setattr(feed, "FEED_TOTAL_S", 0.3)
        ran: list[str] = []
        _with(monkeypatch,
              _feed_only("stuck_a", _recorder(ran, "stuck_a", sleep=5.0)),
              _feed_only("stuck_b", _recorder(ran, "stuck_b", sleep=5.0)),
              _feed_only("queued", _recorder(ran, "queued")))
        with caplog.at_level(logging.INFO, logger=shell.logger.name):
            answer = run_feed(member(*NO_CALENDAR))
        assert {answer["sources"][k] for k in ("stuck_a", "stuck_b", "queued")} == {"failed"}
        cut = {x["provider"]: x for x in _printed(caplog) if x["what"] == "passed the ceiling"}
        assert set(cut) == {"stuck_a", "stuck_b", "queued"}
        assert cut["stuck_a"]["started"] == "yes" and int(cut["stuck_a"]["ms"]) > 0
        assert cut["queued"]["started"] == "no" and cut["queued"]["ms"] == "0"
