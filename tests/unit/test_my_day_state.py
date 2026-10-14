"""WS-58 MD-2a — the life of a card on the server (``my_day_feed.md`` §6.8).

The R8 half is ``test_my_day_state_r8.py``. This file fences the rules and
the POST contract, and each case is written so a wrong build fails it:

* **Each rule of §6.3** has its own class here, and each case fails when its
  rule leaves ``state.RULES``. ``TestTheOrderOfTheRules`` holds the order:
  one case for each pair of rules that can both match.
* **The state fingerprint** (§6.8 item 1). A dismissed task stays dismissed
  when a block folds in and when it turns overdue. A new due date brings it
  back.
* **The POST contract.** Seen, the four snoozes, dismiss, "Not useful" and
  ``acted`` write the row, and the feed honours each. A body that names a
  member or an organization gets 422 and writes nothing.
* **The needs view never changes** with state rows present (§6.8 item 9).
* **Hidden cards take no slot**, and a signal that leaves writes no row.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.shell import feed, feed_rank, state
from gateway.routes.shell.needs import Signal, link, tap
from pydantic import ValidationError

from tests.unit.test_shell_feed import (  # noqa: F401  (fixture, by name)
    FULL,
    ME,
    OTHER,
    admin,
    card,
    fakes,
    member,
    run_feed,
    run_needs,
)

# ── A fixed day for the pure rules ──────────────────────────────────────────

#: Tuesday 13 October 2026, 13:30 UTC. The member's day starts at 07:00 UTC.
NOW = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
DAY = state.Day(ZoneInfo("UTC"), 7, 22)
TODAY = date(2026, 10, 13)
YESTERDAY = date(2026, 10, 12)
CTX = state.Context(NOW, DAY)


def _card(kind: str = "due_today", *, sid: str = "tasks:t1", group: str = "needs_you",
          facts: dict | None = None, urgent: datetime | None = None,
          expires: datetime | None = None, folded: tuple[str, ...] = ()) -> dict:
    facts = {"due_at": "2026-10-13T15:00:00+00:00"} if facts is None else facts
    return {"signal_id": sid, "app": "tasks", "kind": kind, "folded": list(folded),
            "group": group, "score": 100, "facts": facts, "at": None,
            "urgent_at": urgent.isoformat() if urgent else None,
            "expires_at": expires.isoformat() if expires else None,
            "state_fp": state.state_fp(sid, facts)}


def _row(card_: dict, st: str, *, fp: str | None = None, visits: int = 1,
         day: date | None = TODAY, last_seen: datetime | None = None,
         until: datetime | None = None, on_change: bool = False,
         reason: str | None = None) -> dict:
    return {"kind": card_["kind"], "state": st, "dismiss_reason": reason,
            "fingerprint": card_["state_fp"] if fp is None else fp, "visits": visits,
            "visits_day": day, "first_seen_at": last_seen, "last_seen_at": last_seen,
            "acted_at": None, "snoozed_until": until, "snooze_on_change": on_change}


def _decide(card_: dict, row: dict | None, ctx: state.Context = CTX) -> state.Outcome:
    return state.decide(card_, row, ctx)


# ── Each rule of §6.3, on its own ───────────────────────────────────────────


class TestRuleOneSnoozeHolds:
    def test_a_timed_snooze_hides_the_card_until_its_time(self):
        c = _card()
        row = _row(c, "snoozed", until=NOW + timedelta(hours=1))
        out = _decide(c, row)
        assert (out.rule, out.visible) == (1, False)

    def test_until_it_changes_hides_the_card_while_the_fingerprint_holds(self):
        c = _card()
        out = _decide(c, _row(c, "snoozed", on_change=True))
        assert (out.rule, out.visible) == (1, False)

    def test_a_snooze_that_ended_brings_the_card_back_as_new(self):
        c = _card()
        out = _decide(c, _row(c, "snoozed", until=NOW - timedelta(minutes=1)))
        assert out.visible and out.state == "new" and out.rule is None


class TestRuleTwoDismissHolds:
    @pytest.mark.parametrize("reason", ["dismiss", "not_useful"])
    def test_a_dismissed_card_stays_hidden_while_its_fingerprint_holds(self, reason):
        c = _card()
        out = _decide(c, _row(c, "dismissed", reason=reason))
        assert (out.rule, out.visible) == (2, False)


class TestRuleThreeAChangedFingerprintReturnsNew:
    def test_a_seen_card_whose_facts_changed_is_new_and_updated(self):
        c = _card()
        out = _decide(c, _row(c, "seen", fp="0000aaaa", visits=2,
                              last_seen=NOW - timedelta(hours=1)))
        assert (out.rule, out.visible, out.state, out.updated, out.visits) == (
            3, True, "new", True, 0)

    @pytest.mark.parametrize("st", ["dismissed", "snoozed"])
    def test_a_hidden_card_whose_facts_changed_comes_back(self, st):
        c = _card()
        row = _row(c, st, fp="0000aaaa", on_change=True, reason="dismiss")
        out = _decide(c, row)
        assert (out.rule, out.visible, out.updated) == (3, True, True)


class TestRuleFourUrgencyLifts:
    def test_a_task_seen_at_nine_and_due_at_fifteen_comes_back_at_thirteen(self):
        # §6.3 rule 4, the spec's own example.
        due = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
        c = _card(urgent=due)
        seen = _row(c, "seen", last_seen=datetime(2026, 10, 13, 9, 0, tzinfo=UTC))
        at_1259 = state.Context(datetime(2026, 10, 13, 12, 59, tzinfo=UTC), DAY)
        assert _decide(c, seen, at_1259).rule is None
        out = _decide(c, seen, state.Context(datetime(2026, 10, 13, 13, 0, tzinfo=UTC), DAY))
        assert (out.rule, out.group, out.state, out.still_open) == (4, "now", "seen", True)

    def test_a_card_seen_after_it_turned_urgent_does_not_move(self):
        c = _card(urgent=NOW - timedelta(minutes=30))
        out = _decide(c, _row(c, "seen", last_seen=NOW - timedelta(minutes=10)))
        assert out.rule != 4 and out.group is None and not out.still_open

    def test_a_new_card_whose_edge_passed_this_morning_moves_to_now(self):
        # §6.5 step 3, bounded by §6.8 item 6: the 2-hour edge (08:00) came
        # after the member's day started (07:00).
        c = _card("due_today", urgent=datetime(2026, 10, 13, 10, 0, tzinfo=UTC))
        out = _decide(c, None)
        assert (out.rule, out.group, out.state, out.still_open) == (4, "now", "new", False)

    def test_a_new_overdue_task_due_last_week_stays_in_its_group(self):
        # Fix round 1, P1-b: an old crossing keeps the old-path group, so a
        # member's first visit does not fill Now with every overdue task.
        c = _card("overdue", urgent=NOW - timedelta(days=7))
        out = _decide(c, None)
        assert out.rule is None and out.group is None and out.state == "new"
        assert state.join([c], {}, CTX)[0]["group"] == "needs_you"

    @pytest.mark.parametrize(("edge", "lifted"), [
        (datetime(2026, 10, 13, 7, 0, tzinfo=UTC), True),     # at the day start
        (datetime(2026, 10, 13, 6, 59, tzinfo=UTC), False),   # before it
    ])
    def test_the_bound_is_the_start_of_the_members_day(self, edge, lifted):
        c = _card("due_today", urgent=edge + feed_rank.SOON)
        assert (_decide(c, None).rule == 4) is lifted

    def test_a_later_card_that_crosses_its_urgency_leaves_later(self):
        c = _card(urgent=NOW + timedelta(hours=1))
        row = _row(c, "later", visits=3, last_seen=NOW - timedelta(hours=2))
        out = _decide(c, row)
        assert (out.rule, out.group, out.still_open) == (4, "now", True)


class TestRuleFiveTheFixedDailyReturn:
    def test_an_overdue_card_in_later_returns_to_its_group_each_day(self):
        c = _card("overdue")
        out = _decide(c, _row(c, "later", visits=3, day=YESTERDAY))
        assert (out.rule, out.visible, out.state, out.group, out.visits) == (
            5, True, "seen", None, 0)

    def test_a_reply_waiting_more_than_48_hours_returns(self):
        c = _card("needs_reply", sid="email:a:t",
                  facts={"waiting_since": (NOW - timedelta(hours=49)).isoformat()})
        out = _decide(c, _row(c, "later", visits=3, day=YESTERDAY))
        assert (out.rule, out.state) == (5, "seen")

    def test_the_return_holds_while_the_visits_of_the_day_stay_under_3(self):
        c = _card("overdue")
        assert _decide(c, _row(c, "later", visits=2, day=TODAY)).rule == 5
        assert _decide(c, _row(c, "later", visits=3, day=TODAY)).group == "later"


class TestRuleSixTheJudgedReturn:
    def test_with_no_answer_a_later_card_stays_in_later(self):
        # §6.8 item 3: `my_day.resurface` is MD-6a, so it has no answer.
        c = _card("due_today")
        out = _decide(c, _row(c, "later", visits=3, day=YESTERDAY))
        assert (out.rule, out.visible, out.state, out.group) == (6, True, "later", "later")

    def test_a_reply_younger_than_48_hours_stays_in_later(self):
        c = _card("needs_reply", sid="email:a:t",
                  facts={"waiting_since": (NOW - timedelta(hours=47)).isoformat()})
        assert _decide(c, _row(c, "later", visits=3, day=YESTERDAY)).group == "later"

    def test_a_yes_returns_the_card_with_no_visits(self):
        c = _card("due_today")
        yes = state.Context(NOW, DAY, resurface=lambda _card: True)
        out = _decide(c, _row(c, "later", visits=3, day=YESTERDAY), yes)
        assert (out.rule, out.state, out.group, out.visits) == (6, "seen", None, 0)


class TestRuleSevenSeenCardsStartTheDayFresh:
    def test_a_card_seen_three_times_yesterday_returns_with_no_visits(self):
        c = _card()
        out = _decide(c, _row(c, "seen", visits=3, day=YESTERDAY))
        assert (out.rule, out.visible, out.state, out.group, out.visits) == (
            7, True, "seen", None, 0)


class TestRuleEightThreeVisitsFold:
    def test_a_seen_card_with_three_visits_today_folds_into_later(self):
        c = _card()
        out = _decide(c, _row(c, "seen", visits=3, day=TODAY))
        assert (out.rule, out.state, out.group) == (8, "later", "later")

    def test_two_visits_do_not_fold(self):
        c = _card()
        out = _decide(c, _row(c, "seen", visits=2, day=TODAY))
        assert out.rule is None and (out.state, out.visits) == ("seen", 2)

    def test_an_acted_card_never_folds(self):
        c = _card()
        assert _decide(c, _row(c, "acted", visits=3, day=TODAY)).state == "seen"


class TestTheOrderOfTheRules:
    """One case for each pair of rules that can both match. The first rule
    in §6.3's order decides."""

    def test_the_rules_are_the_eight_of_section_6_3_in_order(self):
        assert [r.__name__ for r in state.RULES] == [
            "_rule_1_snooze_holds", "_rule_2_dismiss_holds",
            "_rule_3_changed_returns_new", "_rule_4_urgency_lifts",
            "_rule_5_fixed_daily_return", "_rule_6_judged_return",
            "_rule_7_seen_starts_fresh", "_rule_8_three_visits_fold",
        ]

    def test_each_earlier_rule_beats_the_later_one(self):
        lift = NOW + timedelta(hours=1)  # urgent within 2 h: its lift passed
        before = NOW - timedelta(hours=3)
        cases = []
        c = _card(urgent=lift)
        # 1 over 3: a timed snooze holds, even when the facts changed.
        cases.append((1, c, _row(c, "snoozed", fp="0000aaaa", until=NOW + timedelta(hours=1))))
        # 1 over 4: a snooze beats urgency.
        cases.append((1, c, _row(c, "snoozed", until=NOW + timedelta(hours=1),
                                 last_seen=before)))
        # 2 over 4: a dismiss beats urgency.
        cases.append((2, c, _row(c, "dismissed", reason="dismiss", last_seen=before)))
        # 3 over 4: a changed card is new and Updated, not Still open.
        cases.append((3, c, _row(c, "seen", fp="0000aaaa", last_seen=before)))
        # 4 over 5: an overdue card in Later that crosses its urgency goes to Now.
        o = _card("overdue", urgent=lift)
        cases.append((4, o, _row(o, "later", visits=3, day=YESTERDAY, last_seen=before)))
        # 5 over 6: an overdue card in Later returns, and decide is not asked.
        o2 = _card("overdue")
        cases.append((5, o2, _row(o2, "later", visits=3, day=YESTERDAY)))
        # 7 over 8: three visits yesterday never fold a card today.
        s = _card()
        cases.append((7, s, _row(s, "seen", visits=3, day=YESTERDAY)))
        for want, c_, row in cases:
            asked: list[dict] = []
            ctx = state.Context(NOW, DAY,
                                resurface=lambda x, seen=asked: seen.append(x) or None)
            assert _decide(c_, row, ctx).rule == want, (want, row)
            if want == 5:
                assert asked == []


class TestTheExpiredFilter:
    def test_a_card_past_its_expires_at_leaves_the_feed(self):
        gone = _card(expires=NOW - timedelta(minutes=1))
        live = _card(sid="tasks:t2", expires=NOW + timedelta(minutes=1))
        out = state.join([gone, live], {}, CTX)
        assert [c["signal_id"] for c in out] == ["tasks:t2"]


class TestTheJoinFields:
    def test_each_card_gains_the_five_state_fields(self):
        c = _card()
        out = state.join([c], {"tasks:t1": _row(c, "seen", visits=2, day=TODAY)}, CTX)[0]
        assert {k: out[k] for k in ("state", "updated", "still_open", "visits")} == {
            "state": "seen", "updated": False, "still_open": False, "visits": 2}
        assert out["state_fp"] == state.state_fp("tasks:t1", c["facts"])

    @pytest.mark.parametrize(("visits", "fatigue"), [(0, 0), (1, 8), (2, 16)])
    def test_fatigue_is_8_for_each_visit_of_the_day(self, visits, fatigue):
        c = _card()
        row = _row(c, "seen", visits=visits, day=TODAY)
        assert state.join([c], {"tasks:t1": row}, CTX)[0]["score"] == 100 - fatigue

    def test_fatigue_stops_at_24(self):
        c = _card()
        row = _row(c, "later", visits=5, day=TODAY)
        assert state.join([c], {"tasks:t1": row}, CTX)[0]["score"] == 100 - 24

    def test_an_acted_card_has_no_fatigue(self):
        c = _card()
        assert state.join([c], {"tasks:t1": _row(c, "acted", visits=2)}, CTX)[0]["score"] == 100


# ── The state fingerprint (§6.8 item 1) ─────────────────────────────────────

FEED_NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def _task_signal(kind: str, *, due: datetime | None = None, app: str = "tasks",
                 facts: dict | None = None) -> Signal:
    facts = dict(facts or {})
    if due is not None:
        facts["due_at"] = due.isoformat()
    return Signal(signal_id="tasks:t1", app=app, kind=kind,
                  entity={"type": "task", "id": "t1", "label": "A task"}, title="A task",
                  facts=facts, href="/projects?task=t1",
                  acts=(tap("done", "Done", "done", "t1"),) if app == "tasks"
                  else (link("open", "Open", "/calendar"),),
                  urgent_at=due.isoformat() if due else None)


def _fp(*signals: Signal) -> str:
    cards = feed.build_cards(list(signals), FEED_NOW, ZoneInfo("UTC"), FEED_NOW.date())
    return state.join(cards, {}, state.Context(FEED_NOW, DAY))[0]["state_fp"]


class TestTheStateFingerprint:
    due = FEED_NOW + timedelta(hours=5)

    def _block(self) -> Signal:
        start = FEED_NOW + timedelta(minutes=20)
        return _task_signal("block_soon", app="calendar",
                            facts={"block_start": start.isoformat(),
                                   "block_end": (start + timedelta(hours=1)).isoformat(),
                                   "minutes": 20})

    def test_a_block_that_folds_in_keeps_it(self):
        alone = _fp(_task_signal("due_today", due=self.due))
        assert _fp(_task_signal("due_today", due=self.due), self._block()) == alone

    def test_turning_overdue_keeps_it(self):
        assert _fp(_task_signal("due_today", due=self.due)) == _fp(
            _task_signal("overdue", due=self.due, facts={"days_late": 1}))

    def test_a_new_due_date_changes_it(self):
        assert _fp(_task_signal("due_today", due=self.due)) != _fp(
            _task_signal("due_today", due=self.due + timedelta(days=1)))

    def test_a_new_note_changes_it(self):
        one = _task_signal("notification", app="projects", facts={"note_ids": ["n1"]})
        two = _task_signal("notification", app="projects", facts={"note_ids": ["n2", "n1"]})
        assert _fp(one) != _fp(two)

    def test_a_new_message_in_the_thread_changes_it(self):
        assert state.state_fp("email:a:t", {"message_id": "m1", "waiting_since": "x"}) != \
            state.state_fp("email:a:t", {"message_id": "m2", "waiting_since": "x"})

    def test_a_dismissed_task_stays_dismissed_through_a_block_and_overdue(self):
        # MD-2a done-when 4.
        first = _task_signal("due_today", due=self.due)
        cards = feed.build_cards([first], FEED_NOW, ZoneInfo("UTC"), FEED_NOW.date())
        fp = state.join(cards, {}, state.Context(FEED_NOW, DAY))[0]["state_fp"]
        row = {"kind": "due_today", "state": "dismissed", "dismiss_reason": "dismiss",
               "fingerprint": fp, "visits": 0, "visits_day": None, "last_seen_at": None,
               "snoozed_until": None, "snooze_on_change": False}

        def shown(*signals: Signal) -> bool:
            built = feed.build_cards(list(signals), FEED_NOW, ZoneInfo("UTC"),
                                     FEED_NOW.date())
            return bool(state.join(built, {"tasks:t1": row}, state.Context(FEED_NOW, DAY)))

        assert not shown(first, self._block())
        assert not shown(_task_signal("overdue", due=self.due, facts={"days_late": 2}))
        assert shown(_task_signal("due_today", due=self.due + timedelta(days=1)))


# ── The snooze times (§6.8, the POST contract) ──────────────────────────────

KOLKATA = ZoneInfo("Asia/Kolkata")
WEEKDAYS = frozenset({1, 2, 3, 4, 5})


def _at(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=KOLKATA)


class TestTheSnoozeTimesFollowTheMembersDay:
    """Fix round 1, P2-a. At 00:30 the member's Monday still runs."""

    day = state.Day(KOLKATA, 7, 22, WEEKDAYS)
    now = _at(2026, 10, 13, 0, 30)  # Tuesday 00:30, the member's Monday

    def test_tomorrow_at_half_past_midnight_is_this_mornings_start(self):
        assert state.snoozed_until("tomorrow", self.now, self.day) == _at(2026, 10, 13, 7)

    def test_later_today_at_half_past_midnight_is_the_next_day_start(self):
        assert state.snoozed_until("later_today", self.now, self.day) == _at(2026, 10, 13, 7)


class TestTheSnoozeTimes:
    day = state.Day(KOLKATA, 9, 18, WEEKDAYS)

    @pytest.mark.parametrize(("now", "want"), [
        (_at(2026, 10, 13, 10), _at(2026, 10, 13, 13)),     # 3 hours
        (_at(2026, 10, 13, 16), _at(2026, 10, 13, 18)),     # the day end comes first
        (_at(2026, 10, 13, 18), _at(2026, 10, 14, 9)),      # at the day end: next day
        (_at(2026, 10, 16, 19), _at(2026, 10, 19, 9)),      # Friday night: Monday
    ])
    def test_later_today(self, now, want):
        assert state.snoozed_until("later_today", now, self.day) == want

    @pytest.mark.parametrize(("now", "want"), [
        (_at(2026, 10, 13, 10), _at(2026, 10, 14, 9)),
        (_at(2026, 10, 16, 10), _at(2026, 10, 19, 9)),      # Friday: Monday
    ])
    def test_tomorrow_is_the_next_working_days_start(self, now, want):
        assert state.snoozed_until("tomorrow", now, self.day) == want

    def test_next_week_is_the_first_working_day_of_the_next_iso_week(self):
        assert state.snoozed_until("next_week", _at(2026, 10, 13, 10), self.day) == \
            _at(2026, 10, 19, 9)
        tue_to_sat = state.Day(KOLKATA, 9, 18, frozenset({2, 3, 4, 5, 6}))
        assert state.snoozed_until("next_week", _at(2026, 10, 18, 10), tue_to_sat) == \
            _at(2026, 10, 20, 9)

    def test_on_change_has_no_time(self):
        assert state.snoozed_until("on_change", _at(2026, 10, 13, 10), self.day) is None


# ── The POST contract, through the route ────────────────────────────────────


def _post(user, items: list[dict]) -> dict:
    from tests.unit.test_shell_feed import _bound

    body = state.StateBody.model_validate({"items": items})
    return _bound(lambda: state.post_feed_state(body=body, user=user))


def _item(sid: str, fp: str, action: str, kind: str = "overdue",
          until: str | None = None) -> dict:
    out = {"signal_id": sid, "state_fp": fp, "kind": kind, "action": action}
    if until is not None:
        out["until"] = until
    return out


def _of(answer: dict, sid: str) -> dict | None:
    found = [c for c in answer["cards"] if c["signal_id"] == sid]
    return found[0] if found else None


@pytest.fixture()
def days(monkeypatch):
    """The member's working days, without the People read."""
    async def _days(db, user):
        return WEEKDAYS

    monkeypatch.setattr(state, "_working_days", _days)


class TestThePostWritesAndTheFeedHonoursEach:
    def test_seen_writes_one_visit_and_the_feed_says_seen(self, fakes):  # noqa: F811
        user = member(*FULL)
        late = card(run_feed(user), "tasks:t-late")
        assert late["state"] == "new"
        assert _post(user, [_item("tasks:t-late", late["state_fp"], "seen")]) == {"written": 1}
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert (row["state"], row["visits"], row["fingerprint"]) == ("seen", 1, late["state_fp"])
        assert row["last_seen_at"] == fakes.now
        again = card(run_feed(user), "tasks:t-late")
        assert (again["state"], again["visits"]) == ("seen", 1)

    @pytest.mark.parametrize("until", ["later_today", "tomorrow", "next_week", "on_change"])
    def test_each_snooze_hides_the_card(self, fakes, days, until):  # noqa: F811
        user = member(*FULL)
        late = card(run_feed(user), "tasks:t-late")
        _post(user, [_item("tasks:t-late", late["state_fp"], "snooze", until=until)])
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert row["state"] == "snoozed"
        assert row["snooze_on_change"] is (until == "on_change")
        assert (row["snoozed_until"] is None) is (until == "on_change")
        if row["snoozed_until"] is not None:
            assert row["snoozed_until"] > fakes.now
        assert _of(run_feed(user), "tasks:t-late") is None

    @pytest.mark.parametrize("action", ["dismiss", "not_useful"])
    def test_dismiss_and_not_useful_hide_the_card(self, fakes, action):  # noqa: F811
        user = member(*FULL)
        late = card(run_feed(user), "tasks:t-late")
        _post(user, [_item("tasks:t-late", late["state_fp"], action)])
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert (row["state"], row["dismiss_reason"]) == ("dismissed", action)
        assert _of(run_feed(user), "tasks:t-late") is None

    def test_acted_is_a_record_and_never_a_hide(self, fakes):  # noqa: F811
        user = member(*FULL)
        late = card(run_feed(user), "tasks:t-late")
        _post(user, [_item("tasks:t-late", late["state_fp"], "acted")])
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert (row["state"], row["acted_at"]) == ("acted", fakes.now)
        assert _of(run_feed(user), "tasks:t-late") is not None

    def test_two_sightings_within_30_minutes_are_one_visit(self, fakes, monkeypatch):  # noqa: F811
        from tests.unit.test_shell_needs import _freeze

        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        seen = [_item("tasks:t-late", fp, "seen")]
        _post(user, seen)
        _freeze(monkeypatch, fakes.now + timedelta(minutes=29))
        _post(user, seen)
        assert fakes.state_rows[ME]["tasks:t-late"]["visits"] == 1
        _freeze(monkeypatch, fakes.now + timedelta(minutes=60))
        _post(user, seen)
        assert fakes.state_rows[ME]["tasks:t-late"]["visits"] == 2
        _freeze(monkeypatch, fakes.now + timedelta(minutes=91))
        _post(user, seen)
        # The third visit of the day writes `later` (§6.8 item 5).
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert (row["state"], row["visits"]) == ("later", 3)

    def test_a_seen_of_a_hidden_card_writes_nothing(self, fakes):  # noqa: F811
        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, "dismiss")])
        assert _post(user, [_item("tasks:t-late", fp, "seen")]) == {"written": 0}
        assert fakes.state_rows[ME]["tasks:t-late"]["state"] == "dismissed"

    def test_the_member_is_the_sessions_and_never_another(self, fakes):  # noqa: F811
        fp = card(run_feed(member(*FULL)), "tasks:t-late")["state_fp"]
        _post(member(*FULL, email=OTHER), [_item("tasks:t-late", fp, "dismiss")])
        assert "tasks:t-late" not in fakes.state_rows.get(ME, {})
        assert fakes.state_rows[OTHER]["tasks:t-late"]["state"] == "dismissed"
        assert _of(run_feed(member(*FULL)), "tasks:t-late") is not None


class TestRestoreUndoesAMenuAct:
    """WS-58 MD-2b. ``restore`` is the Undo of a menu act (§6.8, the POST
    contract). It clears a snooze or a dismiss, and leaves the row ``seen``."""

    @pytest.mark.parametrize("act", [
        ("dismiss", None), ("not_useful", None), ("snooze", "later_today"),
        ("snooze", "tomorrow"), ("snooze", "next_week"), ("snooze", "on_change")])
    def test_restore_brings_the_card_back_as_seen(self, fakes, days, act):  # noqa: F811
        action, until = act
        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, "seen")])
        _post(user, [_item("tasks:t-late", fp, action, until=until)])
        assert _of(run_feed(user), "tasks:t-late") is None  # non-vacuity
        assert _post(user, [_item("tasks:t-late", fp, "restore")]) == {"written": 1}
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert (row["state"], row["dismiss_reason"]) == ("seen", None)
        assert (row["snoozed_until"], row["snooze_on_change"]) == (None, False)
        back = card(run_feed(user), "tasks:t-late")
        # Seen, not new: the member saw it before the act, so no dot.
        assert (back["state"], back["updated"], back["visits"]) == ("seen", False, 1)

    def test_restore_counts_no_visit(self, fakes, monkeypatch):  # noqa: F811
        from tests.unit.test_shell_needs import _freeze

        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, "seen")])
        _post(user, [_item("tasks:t-late", fp, "dismiss")])
        _freeze(monkeypatch, fakes.now + timedelta(hours=1))
        _post(user, [_item("tasks:t-late", fp, "restore")])
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert row["visits"] == 1
        assert row["last_seen_at"] == fakes.now  # the sighting before, kept

    def test_undo_of_not_useful_takes_back_its_record(self, fakes):  # noqa: F811
        # The Undo says the act did not happen, so it must not count against
        # the kind for this member (§6.6, §7).
        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, "not_useful")])
        assert fakes.state_rows[ME]["tasks:t-late"]["not_useful_at"] == fakes.now
        _post(user, [_item("tasks:t-late", fp, "restore")])
        assert fakes.state_rows[ME]["tasks:t-late"]["not_useful_at"] is None

    def test_undo_of_a_dismiss_keeps_an_older_not_useful(self, fakes):  # noqa: F811
        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, "not_useful")])
        _post(user, [_item("tasks:t-late", "0000bbbb", "seen")])  # it came back
        _post(user, [_item("tasks:t-late", "0000bbbb", "dismiss")])
        _post(user, [_item("tasks:t-late", "0000bbbb", "restore")])
        assert fakes.state_rows[ME]["tasks:t-late"]["not_useful_at"] == fakes.now

    def test_restore_of_a_card_with_no_row_writes_nothing(self, fakes):  # noqa: F811
        # A claimed placeholder must not stay behind: it would read as Updated.
        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        assert _post(user, [_item("tasks:t-late", fp, "restore")]) == {"written": 0}
        assert "tasks:t-late" not in fakes.state_rows.get(ME, {})
        assert card(run_feed(user), "tasks:t-late")["state"] == "new"

    @pytest.mark.parametrize("action", ["seen", "acted"])
    def test_restore_of_a_shown_card_writes_nothing(self, fakes, action):  # noqa: F811
        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, action)])
        stored = dict(fakes.state_rows[ME]["tasks:t-late"])
        assert _post(user, [_item("tasks:t-late", fp, "restore")]) == {"written": 0}
        assert fakes.state_rows[ME]["tasks:t-late"] == stored

    def test_restore_of_a_later_card_writes_nothing(self):
        late = state.StateItem(signal_id="tasks:t1", state_fp="a41f09c2", kind="overdue",
                               action="restore")
        row = _row(_card("overdue"), "later", visits=3)
        assert state.next_row(row, late, NOW, DAY) is None

    def test_restore_carries_no_until(self):
        with pytest.raises(ValidationError):
            state.StateItem(signal_id="tasks:t1", state_fp="a41f09c2", kind="overdue",
                            action="restore", until="tomorrow")


class TestTheWriteOrder:
    """Fix round 1, P2-b. Claim the missing rows, then lock in signal-id
    order, then write all the rows in one statement."""

    def test_claim_then_lock_then_one_upsert_in_signal_id_order(self, fakes):  # noqa: F811
        user = member(*FULL)
        fp = "a41f09c2"
        _post(user, [_item("tasks:b", fp, "seen"), _item("tasks:a", fp, "dismiss"),
                     _item("tasks:b", fp, "seen")])
        assert fakes.state_log == [
            ("claim", ["tasks:a", "tasks:b"]),
            ("read_locked", ["tasks:a", "tasks:b"]),
            ("upsert", ["tasks:a", "tasks:b"]),
        ]
        # A claimed placeholder is no history: the first sighting is visit 1.
        assert fakes.state_rows[ME]["tasks:b"]["visits"] == 1
        assert fakes.state_rows[ME]["tasks:a"]["state"] == "dismissed"

    def test_the_locked_read_orders_by_signal_id(self):
        seen: list[str] = []

        class _Db:
            async def execute(self, sql, params=None):
                seen.append(str(sql))
                return []

        asyncio.run(state._read_rows(_Db(), ME, ["tasks:b", "tasks:a"], lock=True))
        assert seen[0].rstrip().endswith("ORDER BY signal_id FOR UPDATE")


class TestTheStateReadHasATimeLimit:
    def test_a_slow_state_read_keeps_the_feed_and_the_bell(self, fakes, monkeypatch):  # noqa: F811
        # Fix round 1, P2-c.
        async def slow(db, member, ids, *, lock=False):
            # The rows would hide the card, so only the time limit shows it.
            await asyncio.sleep(2)
            return await fakes.read_state(db, member, ids, lock=lock)

        user = member(*FULL)
        late = card(run_feed(user), "tasks:t-late")
        fakes.state_rows[ME] = {"tasks:t-late": {
            "kind": "overdue", "state": "dismissed", "dismiss_reason": "dismiss",
            "fingerprint": late["state_fp"], "visits": 0, "visits_day": None,
            "last_seen_at": None, "snoozed_until": None, "snooze_on_change": False}}
        assert _of(run_feed(user), "tasks:t-late") is None  # non-vacuity
        monkeypatch.setattr(state, "_read_rows", slow)
        monkeypatch.setattr(state, "STATE_TIMEOUT_S", 0.05)
        answer = run_feed(user)
        assert card(answer, "tasks:t-late")["state"] == "new"
        assert answer["needs"] == run_needs(user)


class TestTheMembersOwnActIsNoUpdate:
    """Fix round 1, P2-d. Mark read takes ids out of ``note_ids``."""

    def test_an_acted_card_whose_notes_were_read_reads_as_seen(self):
        after = _card("overdue", facts={"due_at": "2026-10-10T09:00:00+00:00"})
        row = _row(after, "acted", fp="0000aaaa", visits=2, day=TODAY,
                   last_seen=NOW - timedelta(hours=2))
        row["acted_at"] = NOW - timedelta(hours=1)
        out = _decide(after, row)
        assert (out.state, out.updated, out.visits) == ("seen", False, 2)

    def test_a_note_after_the_act_is_still_an_update(self):
        newer = _card("overdue", facts={"due_at": "2026-10-10T09:00:00+00:00",
                                        "note_ids": ["n9"],
                                        "at": (NOW - timedelta(minutes=10)).isoformat()})
        row = _row(newer, "acted", fp="0000aaaa", day=TODAY)
        row["acted_at"] = NOW - timedelta(hours=1)
        out = _decide(newer, row)
        assert (out.rule, out.updated) == (3, True)

    def test_mark_read_through_the_feed_keeps_the_card_seen(self, fakes, monkeypatch):  # noqa: F811
        from tests.unit.test_shell_needs import _freeze

        user = member(*FULL)
        fakes.notes[ME].append({
            "id": "n-late", "task_id": "t-late", "kind": "mention", "actor": "priya@x.test",
            "task_title": "Ship the extruder", "task_number": None, "excerpt": None,
            "created_at": fakes.now - timedelta(minutes=30)})
        late = card(run_feed(user), "tasks:t-late")
        assert "notification" in late["folded"]
        _post(user, [_item("tasks:t-late", late["state_fp"], "seen")])
        _freeze(monkeypatch, fakes.now + timedelta(minutes=5))
        _post(user, [_item("tasks:t-late", late["state_fp"], "acted")])
        fakes.notes[ME] = [n for n in fakes.notes[ME] if n["task_id"] != "t-late"]
        after = card(run_feed(user), "tasks:t-late")
        assert after["state_fp"] != late["state_fp"]
        assert (after["state"], after["updated"]) == ("seen", False)
        _freeze(monkeypatch, fakes.now + timedelta(minutes=60))
        _post(user, [_item("tasks:t-late", after["state_fp"], "seen")])
        assert fakes.state_rows[ME]["tasks:t-late"]["visits"] == 2  # counted on


class TestANewNoteAfterTheActIsAnUpdate:
    """Verifier P3. The reason of a notification card is its newest ASKING
    note, so ``facts["at"]`` can be older than the newest note. A comment
    after the member's act must still read as Updated."""

    @staticmethod
    def _note(nid: str, kind: str, at: datetime) -> dict:
        return {"id": nid, "task_id": "t1", "kind": kind, "actor": "priya@x.test",
                "task_title": "Ship the extruder", "task_number": None, "excerpt": None,
                "created_at": at}

    def _card(self, notes: list[dict]) -> dict:
        from gateway.routes.shell import needs

        built = feed.build_cards(needs._note_signals(notes), NOW, ZoneInfo("UTC"), TODAY)
        return state.join(built, {}, CTX)[0]

    def test_a_comment_after_the_act_on_an_unread_mention_is_an_update(self):
        mention = self._note("n-m", "mention", datetime(2026, 10, 13, 10, 0, tzinfo=UTC))
        comment = self._note("n-c", "comment", datetime(2026, 10, 13, 12, 30, tzinfo=UTC))
        before = self._card([mention])
        row = {"kind": before["kind"], "state": "acted", "dismiss_reason": None,
               "fingerprint": before["state_fp"], "visits": 1, "visits_day": TODAY,
               "last_seen_at": datetime(2026, 10, 13, 10, 30, tzinfo=UTC),
               "acted_at": datetime(2026, 10, 13, 11, 0, tzinfo=UTC),
               "snoozed_until": None, "snooze_on_change": False}
        after = self._card([mention, comment])
        # The reason is still the mention, so `at` is still 10:00.
        assert after["facts"]["at"] == mention["created_at"].isoformat()
        assert after["state_fp"] != before["state_fp"]
        out = state.join([dict(after)], {"tasks:t1": row}, CTX)[0]
        assert (out["state"], out["updated"]) == ("new", True)

    def test_the_newest_note_time_stays_out_of_both_fingerprints_and_the_needs_view(self):
        assert "last_note_at" not in feed.STABLE_FACTS
        assert "last_note_at" not in state.STATE_FACTS
        mention = self._note("n-m", "mention", datetime(2026, 10, 13, 10, 0, tzinfo=UTC))
        from gateway.routes.shell import needs

        assert "last_note_at" not in json.dumps(needs._item(
            id="projects:n-m", app="projects", kind="notification", title="x",
            detail=None, href="/projects?task=t1", at=mention["created_at"]))


class TestNotUsefulKeepsItsHistory:
    def test_not_useful_at_outlives_a_later_sighting(self, fakes, monkeypatch):  # noqa: F811
        from tests.unit.test_shell_needs import _freeze

        user = member(*FULL)
        fp = card(run_feed(user), "tasks:t-late")["state_fp"]
        _post(user, [_item("tasks:t-late", fp, "not_useful")])
        assert fakes.state_rows[ME]["tasks:t-late"]["not_useful_at"] == fakes.now
        _freeze(monkeypatch, fakes.now + timedelta(hours=1))
        _post(user, [_item("tasks:t-late", "0000bbbb", "seen")])  # it came back
        row = fakes.state_rows[ME]["tasks:t-late"]
        assert (row["state"], row["dismiss_reason"]) == ("seen", None)
        assert row["not_useful_at"] == fakes.now  # the not_useful instant, kept


class TestTheBodyContract:
    FP = "a41f09c2"

    def _client(self, fakes) -> TestClient:  # noqa: F811
        from acb_auth import get_current_user
        from gateway.routes.shell import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: member(*FULL)
        return TestClient(app)

    @pytest.mark.parametrize("extra", [{"member_email": OTHER}, {"organization_id": "org-x"},
                                       {"email": OTHER}])
    def test_a_body_that_names_a_member_or_an_org_gets_422(self, fakes, extra):  # noqa: F811
        client = self._client(fakes)
        item = _item("tasks:t-late", self.FP, "dismiss")
        top = client.post("/shell/feed/state", json={"items": [item], **extra})
        inner = client.post("/shell/feed/state", json={"items": [{**item, **extra}]})
        assert (top.status_code, inner.status_code) == (422, 422)
        assert fakes.state_rows == {}

    @pytest.mark.parametrize("item", [
        {"signal_id": "tasks:t1", "state_fp": FP, "kind": "overdue", "action": "seen",
         "until": "tomorrow"},
        {"signal_id": "tasks:t1", "state_fp": FP, "kind": "overdue", "action": "snooze"},
        {"signal_id": "tasks:t1", "state_fp": FP, "kind": "overdue", "action": "snooze",
         "until": "someday"},
        {"signal_id": "tasks:t1", "state_fp": FP, "kind": "overdue", "action": "hide"},
        {"signal_id": "Tasks:t1", "state_fp": FP, "kind": "overdue", "action": "seen"},
        {"signal_id": "tasks:" + "x" * 201, "state_fp": FP, "kind": "overdue",
         "action": "seen"},
        {"signal_id": "tasks:a b", "state_fp": FP, "kind": "overdue", "action": "seen"},
        {"signal_id": "tasks:t1", "state_fp": "a41f09c", "kind": "overdue", "action": "seen"},
        {"signal_id": "tasks:t1", "state_fp": "A41F09C2", "kind": "overdue", "action": "seen"},
        {"signal_id": "tasks:t1", "state_fp": "a" * 65, "kind": "overdue", "action": "seen"},
        {"signal_id": "tasks:t1", "state_fp": FP, "kind": "no_such_kind", "action": "seen"},
    ])
    def test_a_bad_item_gets_422_and_writes_nothing(self, fakes, item):  # noqa: F811
        res = self._client(fakes).post("/shell/feed/state", json={"items": [item]})
        assert res.status_code == 422
        assert fakes.state_rows == {}

    def test_fifty_items_at_most(self, fakes):  # noqa: F811
        items = [_item(f"tasks:t{i}", self.FP, "seen") for i in range(51)]
        with pytest.raises(ValidationError):
            state.StateBody.model_validate({"items": items})
        assert len(state.StateBody.model_validate({"items": items[:50]}).items) == 50

    def test_a_good_body_answers_the_count_written(self, fakes):  # noqa: F811
        res = self._client(fakes).post(
            "/shell/feed/state",
            json={"items": [_item("tasks:t-late", self.FP, "dismiss")]})
        assert res.status_code == 200 and res.json() == {"written": 1}

    def test_a_signed_out_caller_is_refused(self, fakes):  # noqa: F811
        from acb_auth.roles import UserContext, UserRole
        from fastapi import HTTPException

        body = state.StateBody.model_validate({"items": []})
        with pytest.raises(HTTPException) as e:
            asyncio.run(state.post_feed_state(
                body=body, user=UserContext(email=None, role=UserRole.EMPLOYEE)))
        assert e.value.status_code == 401


# ── What the state never changes ────────────────────────────────────────────


class TestTheNeedsViewDoesNotChange:
    def test_dismissed_and_snoozed_needs_cards_still_count_in_the_bell(self, fakes):  # noqa: F811
        # §6.8 item 9 and §6.3: "never in the bell".
        user = admin(*FULL, "approvals")
        before = run_needs(user)
        answer = run_feed(user)
        late, old = card(answer, "tasks:t-late"), card(answer, "tasks:t-old")
        fakes.state_rows[ME] = {
            "tasks:t-late": {"kind": "overdue", "state": "dismissed",
                             "dismiss_reason": "dismiss", "fingerprint": late["state_fp"],
                             "visits": 0, "visits_day": None, "last_seen_at": None,
                             "snoozed_until": None, "snooze_on_change": False},
            "tasks:t-old": {"kind": "overdue", "state": "snoozed", "dismiss_reason": None,
                            "fingerprint": old["state_fp"], "visits": 0, "visits_day": None,
                            "last_seen_at": None, "snoozed_until": None,
                            "snooze_on_change": True},
        }
        after = run_feed(user)
        assert _of(after, "tasks:t-late") is None and _of(after, "tasks:t-old") is None
        assert after["needs"] == before == run_needs(user)
        assert {"tasks:t-late", "tasks:t-old"} <= {i["id"] for i in after["needs"]["items"]}


class TestHiddenCardsTakeNoSlot:
    def test_a_dismissed_card_leaves_before_the_limit(self, fakes):  # noqa: F811
        user = member(*FULL)
        first = run_feed(user, limit=1)["cards"][0]
        fakes.state_rows[ME] = {first["signal_id"]: {
            "kind": first["kind"], "state": "dismissed", "dismiss_reason": "dismiss",
            "fingerprint": first["state_fp"], "visits": 0, "visits_day": None,
            "last_seen_at": None, "snoozed_until": None, "snooze_on_change": False}}
        nxt = run_feed(user, limit=1)["cards"]
        assert len(nxt) == 1 and nxt[0]["signal_id"] != first["signal_id"]


class TestASignalThatLeavesWritesNoRow:
    def test_the_feed_read_writes_nothing_when_a_signal_leaves(self, fakes):  # noqa: F811
        # §6.1: leaving the feed never writes a state row.
        user = member(*FULL)
        late = card(run_feed(user), "tasks:t-late")
        _post(user, [_item("tasks:t-late", late["state_fp"], "seen")])
        stored = {k: dict(v) for k, v in fakes.state_rows[ME].items()}
        fakes.tasks[ME] = [t for t in fakes.tasks[ME] if t["id"] != "t-late"]
        fakes.notes[ME] = [n for n in fakes.notes[ME] if n["task_id"] != "t-late"]
        fakes.blocks[ME] = [b for b in fakes.blocks[ME] if b["id"] != "t-late"]
        answer = run_feed(user)
        assert _of(answer, "tasks:t-late") is None
        assert fakes.state_rows[ME] == stored


class TestTheNewCardSortsFirst:
    def test_inside_a_group_a_new_card_comes_before_a_seen_one(self):
        cards = [{"signal_id": "seen", "group": "needs_you", "score": 90, "app": "tasks",
                  "kind": "overdue", "at": None, "state": "seen"},
                 {"signal_id": "new", "group": "needs_you", "score": 10, "app": "tasks",
                  "kind": "overdue", "at": None, "state": "new"}]
        assert [c["signal_id"] for c in feed_rank.order(cards, 10)] == ["new", "seen"]


class TestAStoredZoneThatDoesNotExist:
    """A zone name the tz database does not know raises ZoneInfoNotFoundError,
    a KeyError. It must fall back to UTC and never fail the feed read."""

    @pytest.mark.parametrize("name", ["Mars/Olympus_Mons", "Asia/Bombay2", "", None,
                                      "../etc/passwd"])
    def test_it_reads_as_utc(self, name) -> None:
        assert state._zone(name) == ZoneInfo("UTC")

    def test_a_real_zone_is_kept(self) -> None:
        assert state._zone("Asia/Kolkata") == ZoneInfo("Asia/Kolkata")
