"""WS-58 MD-1a — the order of the My Day feed (``my_day_feed.md`` §7).

``feed_rank.py`` holds the base table, the terms of the score, the old-path
group table of §4.3 and the cut. This file holds each one, so a change to
the order fails here first:

* **The table.** Each base, and the rule that the five needs kinds start
  above every other kind, in the needs feed's own order.
* **Each term.** Urgency, the old-path weight from the ONE server matrix
  (``acb_common.priority``), affinity at 0 until MD-9, and fatigue (MD-2a):
  -8 for each visit of the day, to -24.
* **The group table** of §4.3, row by row.
* **Rule 1** (the group comes first) and **rule 3** (15 cards for each app,
  and an approval yields).
* **A fixed seed** whose order may change only with the table, and with the
  new-before-seen rule of MD-2a (``my_day_feed.md`` §6.8 item 6).

Rule 2 (a ``drop`` answer never hides a needs card) has no subject until
MD-6a builds ``decide``. Rule 4 (the order holds while the member reads) is
the client's, in MD-1b. Neither is tested here, on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from acb_common.priority import CELL_META, priority_rank
from gateway.routes.shell import feed, feed_rank
from gateway.routes.shell.needs import KIND_ORDER, PER_APP, Signal, link, tap

#: A fixed instant: Tuesday 13 October 2026, 10:00 UTC.
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
TODAY = NOW.date()


class TestTheTable:
    def test_the_base_of_each_kind(self):
        # MD-1, and the eleven MD-3 kinds of §5.9 item 2 (MD-3c).
        assert feed_rank.BASE == {
            # WS-66 RM-2b: the five needs bases rose by 3, to make room for the
            # three reminder kinds above `agent_asks`.
            "overdue": 63, "approval": 59, "due_today": 55, "notification": 51,
            "needs_reply": 47, "agent_asks": 43, "promise_due": 42,
            "crm_followup_due": 41, "block_overlap": 40, "absent_on_due": 39,
            "unblocked": 38, "waiting_quiet": 37, "block_soon": 36, "no_answer": 35,
            "deal_close_passed": 34, "deal_quiet": 33, "day_overbooked": 32,
            "stale_next": 30, "no_plan_yet": 28, "colleague_out": 26,
            # WS-53 CRM-Q28 item 14.
            "digest_ready": 24,
            # WS-53 CRM-Q32-4: the open questions, below the digest.
            "crm_open_questions": 22,
            # WS-61 KB-9a: a Knowledge gap, below every other kind.
            "kb_gap": 20,
            # WS-61 KB-9b: the review above the gap, the new page below it.
            "doc_needs_review": 21, "doc_shared_with_me": 19,
            # WS-61 KB-9b slice 2: a source stopped syncing.
            "source_sync_failed": 31,
            # WS-66 RM-2b: the member's own reminders, needs kinds below the five.
            "reminder_due": 46, "reminder_check": 45, "reminder_unconfirmed": 44,
        }

    def test_the_reminders_sit_between_agent_asks_and_the_lowest_needs_base(self):
        """WS-66 RM-2b. Mutation: set `reminder_due` to 42, or `needs_reply`
        to 45, and this line fails."""
        reminders = [feed_rank.BASE[k] for k in ("reminder_due", "reminder_check",
                                                 "reminder_unconfirmed")]
        lowest_other_needs = min(feed_rank.BASE[k] for k in KIND_ORDER
                                 if not k.startswith("reminder_"))
        assert reminders == sorted(reminders, reverse=True)
        assert feed_rank.BASE["agent_asks"] < min(reminders)
        assert max(reminders) < lowest_other_needs
        assert feed_rank.BASE["needs_reply"] == lowest_other_needs

    def test_the_needs_kinds_start_above_every_other_kind(self):
        needs = [feed_rank.BASE[k] for k in KIND_ORDER]
        others = [v for k, v in feed_rank.BASE.items() if k not in KIND_ORDER]
        assert min(needs) > max(others)

    def test_the_needs_kinds_keep_the_needs_feeds_own_order(self):
        by_base = sorted(KIND_ORDER, key=lambda k: -feed_rank.BASE[k])
        assert by_base == sorted(KIND_ORDER, key=KIND_ORDER.__getitem__)

    def test_an_unknown_kind_raises(self):
        # An MD-9 kind, which no slice has added yet.
        with pytest.raises(KeyError):
            feed_rank.base("lead_replied")
        with pytest.raises(KeyError):
            feed_rank.group("lead_replied", {}, NOW, TODAY)

    def test_the_groups_and_their_order(self):
        assert feed_rank.GROUPS == ("now", "needs_you", "ready", "fyi", "later", "handled")


class TestUrgency:
    @pytest.mark.parametrize(("offset", "points"), [
        (None, 0),
        (timedelta(hours=3), 0),
        (timedelta(hours=2, seconds=1), 0),
        (timedelta(hours=2), 40),
        (timedelta(minutes=20), 40),
        (timedelta(0), 60),
        (-timedelta(days=1), 60),
    ])
    def test_it_grows_as_urgent_at_comes_near(self, offset, points):
        at = None if offset is None else (NOW + offset).isoformat()
        assert feed_rank.urgency("due_today", {}, at, NOW) == points

    @pytest.mark.parametrize(("late", "points"), [(0, 60), (1, 62), (3, 66), (10, 80), (40, 80)])
    def test_an_overdue_task_adds_2_a_day_to_20(self, late, points):
        at = (NOW - timedelta(days=late, hours=1)).isoformat()
        assert feed_rank.urgency("overdue", {"days_late": late}, at, NOW) == points

    def test_days_late_counts_only_for_overdue(self):
        assert feed_rank.urgency("due_today", {"days_late": 5}, None, NOW) == 0


class TestWeight:
    def test_the_level_of_each_cell_comes_from_its_d78_rank(self):
        assert set(feed_rank.LEVEL_BY_CELL) == set(CELL_META)
        for cell, level in feed_rank.LEVEL_BY_CELL.items():
            assert level == round((7 - CELL_META[cell][0]) * 4 / 6), cell
            assert 0 <= level <= 4

    def test_a_higher_rank_never_weighs_less(self):
        cells = sorted(CELL_META, key=lambda c: CELL_META[c][0])
        levels = [feed_rank.LEVEL_BY_CELL[c] for c in cells]
        assert levels == sorted(levels, reverse=True)
        assert levels[0] == 4 and levels[-1] == 0

    @pytest.mark.parametrize("cell", sorted(CELL_META))
    def test_the_weight_is_the_level_times_6(self, cell):
        assert feed_rank.weight({"priority": cell}) == feed_rank.LEVEL_BY_CELL[cell] * 6

    def test_no_level_weighs_nothing(self):
        assert feed_rank.weight({}) == 0
        assert feed_rank.weight({"priority": None}) == 0
        assert feed_rank.weight({"priority": "HIGH"}) == 0

    def test_the_level_is_the_one_servers_matrix(self):
        # The provider stores `task_cell`; its rank is `priority_rank`.
        due = (NOW + timedelta(hours=1)).isoformat()
        rank = priority_rank(important=True, leveraged=True, due_at=due, now=NOW)
        assert rank == CELL_META["critical"][0]
        assert feed_rank.weight({"priority": "critical"}) == 24


class TestTheOtherTerms:
    def test_affinity_is_zero_until_md9(self):
        assert feed_rank.affinity({"priority": "critical"}) == 0

    @pytest.mark.parametrize(("visits", "points"),
                             [(0, 0), (1, 8), (2, 16), (3, 24), (4, 24), (9, 24), (-1, 0)])
    def test_fatigue_is_8_for_each_visit_to_24(self, visits, points):
        # MD-2a, §7: "-8 for each visit with no act, to -24 at most".
        assert feed_rank.fatigue(visits) == points

    def test_the_score_is_the_sum(self):
        facts = {"priority": "important", "days_late": 2}
        at = (NOW - timedelta(days=2)).isoformat()
        assert feed_rank.score("overdue", facts, at, NOW) == 63 + (60 + 4) + 12


class TestTheGroupTable:
    @pytest.mark.parametrize(("kind", "facts", "group"), [
        ("overdue", {}, "needs_you"),
        ("due_today", {"due_at": (NOW + timedelta(minutes=90)).isoformat()}, "now"),
        ("due_today", {"due_at": (NOW + timedelta(hours=5)).isoformat()}, "needs_you"),
        ("notification", {"notification": "assigned"}, "needs_you"),
        ("notification", {"notification": "mention"}, "needs_you"),
        ("notification", {"notification": "nudge"}, "needs_you"),
        ("notification", {"notification": "comment"}, "fyi"),
        ("approval", {}, "needs_you"),
        ("needs_reply", {}, "needs_you"),
        ("block_soon", {}, "now"),
        ("block_overlap", {"overlap_day": TODAY.isoformat()}, "now"),
        ("block_overlap", {"overlap_day": (TODAY + timedelta(days=2)).isoformat()}, "needs_you"),
        ("day_overbooked", {"day": TODAY.isoformat()}, "needs_you"),
        ("day_overbooked", {"day": (TODAY + timedelta(days=1)).isoformat()}, "fyi"),
        ("no_plan_yet", {}, "needs_you"),
        # The MD-3 rows (§5.9 item 3, MD-3c).
        ("unblocked", {}, "ready"),
        ("waiting_quiet", {}, "needs_you"),
        ("stale_next", {}, "fyi"),
        ("absent_on_due", {}, "needs_you"),
        ("promise_due", {"due_at": (NOW + timedelta(minutes=90)).isoformat()}, "now"),
        ("promise_due", {"due_at": NOW.isoformat()}, "now"),
        ("promise_due", {"due_at": (NOW + timedelta(hours=3)).isoformat()}, "needs_you"),
        ("promise_due", {"due_at": (NOW - timedelta(minutes=1)).isoformat()}, "needs_you"),
        ("promise_due", {}, "needs_you"),
        ("no_answer", {}, "needs_you"),
        ("crm_followup_due", {"due_at": (NOW + timedelta(hours=2)).isoformat()}, "now"),
        ("crm_followup_due", {"due_at": (NOW + timedelta(hours=2, seconds=1)).isoformat()},
         "needs_you"),
        ("crm_followup_due", {"due_at": (NOW - timedelta(days=1)).isoformat()}, "needs_you"),
        ("deal_quiet", {}, "fyi"),
        ("deal_close_passed", {}, "needs_you"),
        ("colleague_out", {}, "fyi"),
        ("agent_asks", {}, "needs_you"),
        ("digest_ready", {}, "fyi"),
        # WS-53 CRM-Q32-4: needs you while a question about money is open.
        ("crm_open_questions", {"money": True}, "needs_you"),
        ("crm_open_questions", {"money": False}, "fyi"),
        ("crm_open_questions", {}, "fyi"),
        # WS-61 KB-9a: a Knowledge gap is a thing to know.
        ("kb_gap", {"askers": 3}, "fyi"),
        ("doc_needs_review", {}, "fyi"),
        ("doc_shared_with_me", {}, "fyi"),
        # WS-66 RM-2: a fired reminder is now. A check-back and a give-up ask.
        ("reminder_due", {}, "now"),
        ("reminder_check", {"cycles": 1}, "needs_you"),
        ("reminder_unconfirmed", {}, "needs_you"),
    ])
    def test_each_row_of_section_4_3(self, kind, facts, group):
        assert feed_rank.group(kind, facts, NOW, TODAY) == group


def _card(sid: str, *, group: str, score: int, app: str = "tasks", kind: str = "overdue",
          at: datetime | None = None) -> dict:
    return {"signal_id": sid, "group": group, "score": score, "app": app, "kind": kind,
            "at": at.isoformat() if at else None}


class TestTheMostUrgentGroup:
    @pytest.mark.parametrize(("groups", "best"), [
        (["fyi", "now"], "now"),
        (["needs_you", "fyi"], "needs_you"),
        (["fyi", "ready", "needs_you"], "needs_you"),
        (["ready", "fyi"], "ready"),
        (["fyi"], "fyi"),
    ])
    def test_the_order_is_now_needs_you_ready_fyi(self, groups, best):
        assert feed_rank.most_urgent(groups) == best


class TestRuleOneTheGroupComesFirst:
    def test_a_low_score_in_now_comes_before_a_high_score_in_needs_you(self):
        cards = [_card("a", group="needs_you", score=200), _card("b", group="now", score=1),
                 _card("c", group="fyi", score=500)]
        assert [c["signal_id"] for c in feed_rank.order(cards, 10)] == ["b", "a", "c"]

    def test_inside_a_group_the_higher_score_then_the_older_time(self):
        cards = [_card("late", group="now", score=50, at=NOW),
                 _card("early", group="now", score=50, at=NOW - timedelta(hours=1)),
                 _card("best", group="now", score=90, at=NOW + timedelta(hours=1)),
                 _card("none", group="now", score=50)]
        assert [c["signal_id"] for c in feed_rank.order(cards, 10)] == [
            "best", "early", "late", "none"]


class TestRuleThreeTheCapsStay:
    def test_one_app_gives_15_cards_at_most_its_best(self):
        cards = [_card(f"t{i:02d}", group="needs_you", score=i) for i in range(20)]
        cards.append(_card("e", group="needs_you", score=0, app="email", kind="needs_reply"))
        got = feed_rank.order(cards, 80)
        tasks = [c["signal_id"] for c in got if c["app"] == "tasks"]
        assert len(tasks) == PER_APP
        assert tasks[-1] == "t05"  # the five lowest scores are out
        assert "e" in [c["signal_id"] for c in got]

    def test_an_approval_takes_only_the_room_left(self):
        own = [_card(f"o{i}", group="needs_you", score=10) for i in range(5)]
        asks = [_card(f"a{i}", group="needs_you", score=99, app="approvals", kind="approval")
                for i in range(3)]
        full = feed_rank.order(own + asks, 5)
        assert [c["signal_id"] for c in full] == ["o0", "o1", "o2", "o3", "o4"]
        room = feed_rank.order(own[:3] + asks, 5)
        assert [c["kind"] for c in room].count("approval") == 2
        # Inside the limit, the order is still the score's.
        assert [c["signal_id"] for c in room][:2] == ["a0", "a1"]


# ── The fixed seed ───────────────────────────────────────────────────────────


def _signal(sid: str, kind: str, app: str, *, facts: dict, at: datetime | None = None,
            urgent: datetime | None = None, title: str = "A task") -> Signal:
    task_id = sid.split(":", 1)[1]
    return Signal(
        signal_id=sid, app=app, kind=kind,
        entity={"type": "task", "id": task_id, "label": title}, title=title,
        facts=facts, href=f"/projects?task={task_id}",
        acts=(tap("done", "Done", "done", task_id),) if app == "tasks"
        else (link("open", "Open", "/calendar"),),
        at=at.isoformat() if at else None, urgent_at=urgent.isoformat() if urgent else None,
    )


def _seed() -> list[Signal]:
    hour = timedelta(hours=1)
    return [
        _signal("tasks:old", "overdue", "tasks", at=NOW - 72 * hour, urgent=NOW - 72 * hour,
                facts={"due_at": (NOW - 72 * hour).isoformat(), "days_late": 3,
                       "priority": "low-priority"}),
        _signal("tasks:crit", "overdue", "tasks", at=NOW - 2 * hour, urgent=NOW - 2 * hour,
                facts={"due_at": (NOW - 2 * hour).isoformat(), "days_late": 0,
                       "priority": "critical"}),
        _signal("tasks:soon", "due_today", "tasks", at=NOW + hour, urgent=NOW + hour,
                facts={"due_at": (NOW + hour).isoformat(), "priority": "important"}),
        _signal("tasks:eve", "due_today", "tasks", at=NOW + 8 * hour, urgent=NOW + 8 * hour,
                facts={"due_at": (NOW + 8 * hour).isoformat(), "priority": "urgent"}),
        _signal("tasks:blk", "block_soon", "calendar", at=NOW + 0.25 * hour,
                urgent=NOW + 0.25 * hour,
                facts={"block_start": (NOW + 0.25 * hour).isoformat(), "minutes": 15}),
        _signal("tasks:note", "notification", "projects", at=NOW - hour,
                facts={"notification": "comment", "who": "ravi", "at": (NOW - hour).isoformat()}),
        _signal("tasks:ask", "notification", "projects", at=NOW - 3 * hour,
                facts={"notification": "assigned", "who": "priya",
                       "at": (NOW - 3 * hour).isoformat()}),
        _signal("tasks:wk", "block_overlap", "calendar", at=NOW + 50 * hour,
                urgent=NOW + 50 * hour,
                facts={"overlaps": ["B"], "overlap_ids": ["b"],
                       "overlap_at": (NOW + 50 * hour).isoformat(),
                       "overlap_day": (TODAY + timedelta(days=2)).isoformat()}),
    ]


class TestTheFixedSeed:
    def test_the_order_of_the_seed(self):
        # ⚠️ This order changes ONLY when the table changes. A change here
        # with no change to BASE, the terms or the group table is a defect.
        cards = feed.build_cards(_seed(), NOW, ZoneInfo("UTC"), TODAY)
        got = [(c["signal_id"], c["group"], c["score"]) for c in feed_rank.order(cards, 40)]
        assert got == [
            ("tasks:soon", "now", 55 + 40 + 12),
            ("tasks:blk", "now", 36 + 40),
            ("tasks:crit", "needs_you", 63 + 60 + 24),
            ("tasks:old", "needs_you", 63 + 66 + 0),
            ("tasks:eve", "needs_you", 55 + 0 + 18),
            ("tasks:ask", "needs_you", 51),
            ("tasks:wk", "needs_you", 40),
            ("tasks:note", "fyi", 51),
        ]

    def test_a_seen_card_moves_only_behind_the_new_cards_of_its_group(self):
        # MD-2a, §6.8 item 6: inside a group a new card sorts before a seen
        # one. That rule alone changes the seed: `tasks:soon` drops behind
        # `tasks:blk` in Now, and nothing else moves.
        cards = feed.build_cards(_seed(), NOW, ZoneInfo("UTC"), TODAY)
        for c in cards:
            c["state"] = "seen" if c["signal_id"] == "tasks:soon" else "new"
        got = [c["signal_id"] for c in feed_rank.order(cards, 40)]
        assert got == ["tasks:blk", "tasks:soon", "tasks:crit", "tasks:old", "tasks:eve",
                       "tasks:ask", "tasks:wk", "tasks:note"]
