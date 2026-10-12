"""WS-57 MD-1a — the order of the My Day feed (``my_day_feed.md`` §7).

``feed_rank.py`` holds the base table, the terms of the score, the old-path
group table of §4.3 and the cut. This file holds each one, so a change to
the order fails here first:

* **The table.** Each base, and the rule that the five needs kinds start
  above every other kind, in the needs feed's own order.
* **Each term.** Urgency, the old-path weight from the ONE server matrix
  (``acb_common.priority``), and affinity and fatigue at 0 until MD-9 and
  MD-2.
* **The group table** of §4.3, row by row.
* **Rule 1** (the group comes first) and **rule 3** (15 cards for each app,
  and an approval yields).
* **A fixed seed** whose order may change only with the table.

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
    def test_the_base_of_each_md1_kind(self):
        assert feed_rank.BASE == {
            "overdue": 60, "approval": 56, "due_today": 52, "notification": 48,
            "needs_reply": 44, "block_overlap": 40, "block_soon": 36,
            "day_overbooked": 32, "no_plan_yet": 28,
        }

    def test_the_needs_kinds_start_above_every_other_kind(self):
        needs = [feed_rank.BASE[k] for k in KIND_ORDER]
        others = [v for k, v in feed_rank.BASE.items() if k not in KIND_ORDER]
        assert min(needs) > max(others)

    def test_the_needs_kinds_keep_the_needs_feeds_own_order(self):
        by_base = sorted(KIND_ORDER, key=lambda k: -feed_rank.BASE[k])
        assert by_base == sorted(KIND_ORDER, key=KIND_ORDER.__getitem__)

    def test_an_unknown_kind_raises(self):
        with pytest.raises(KeyError):
            feed_rank.base("crm_followup_due")
        with pytest.raises(KeyError):
            feed_rank.group("crm_followup_due", {}, NOW, TODAY)

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
    def test_affinity_and_fatigue_are_zero_until_md9_and_md2(self):
        assert feed_rank.affinity({"priority": "critical"}) == 0
        assert feed_rank.fatigue({"priority": "critical"}) == 0

    def test_the_score_is_the_sum(self):
        facts = {"priority": "important", "days_late": 2}
        at = (NOW - timedelta(days=2)).isoformat()
        assert feed_rank.score("overdue", facts, at, NOW) == 60 + (60 + 4) + 12


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
    ])
    def test_each_row_of_section_4_3(self, kind, facts, group):
        assert feed_rank.group(kind, facts, NOW, TODAY) == group


def _card(sid: str, *, group: str, score: int, app: str = "tasks", kind: str = "overdue",
          at: datetime | None = None) -> dict:
    return {"signal_id": sid, "group": group, "score": score, "app": app, "kind": kind,
            "at": at.isoformat() if at else None}


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
            ("tasks:soon", "now", 52 + 40 + 12),
            ("tasks:blk", "now", 36 + 40),
            ("tasks:crit", "needs_you", 60 + 60 + 24),
            ("tasks:old", "needs_you", 60 + 66 + 0),
            ("tasks:eve", "needs_you", 52 + 0 + 18),
            ("tasks:ask", "needs_you", 48),
            ("tasks:wk", "needs_you", 40),
            ("tasks:note", "fyi", 48),
        ]
