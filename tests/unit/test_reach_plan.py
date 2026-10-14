"""WS-59 RE-1a — the pure plan of Reach (``proactive_reach.md`` §4.2, §5.1, §6, §9).

``gateway.reach.plan`` and ``gateway.reach.classes`` read no database and no
clock. Each rule is held here:

* **Classes.** Every kind a provider can give has an explicit row, so a new
  kind reaches nobody by accident and is never left out by accident.
* **Candidates.** Only an open, unlogged card of class interrupt, and a
  needs-reply thread of 12 hours or less.
* **When.** Quiet hours, a day off, and the brief window.
* **Words.** The interrupt and the brief name what the member must do, from
  the card's own facts, under 1,000 characters.
* **Dedupe.** One plan gives one key, and the key holds no title.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from gateway.reach import classes, plan
from gateway.routes.shell import (  # noqa: F401  (feed registers Calendar)
    feed,
    feed_rank,
    needs,
    state,
)

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 11:30 in Kolkata, a Thursday
DAY = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)
APP = "https://app.metorite.com"


def card(sid: str, kind: str = "needs_reply", *, group: str = "needs_you",
         title: str = "Q3 pricing", facts: dict | None = None, st: str = "new",
         fp: str = "fp000001", score: int = 50, app: str | None = None) -> dict:
    if facts is None:
        facts = ({"who": "Priya Shah", "waiting_since": (NOW - timedelta(minutes=40)).isoformat()}
                 if kind == "needs_reply" else {})
    return {"signal_id": sid, "app": app or sid.split(":")[0], "kind": kind,
            "group": group, "score": score, "title": title, "facts": facts,
            "fingerprint": fp, "state": st, "at": None, "why": "Why line."}


class TestTheClasses:
    def test_every_kind_a_provider_can_give_has_a_row(self):
        kinds = set(needs.KIND_ORDER) | set(feed_rank.BASE)
        for provider in needs.REGISTRY.values():
            kinds |= provider.kinds
        assert kinds, "no kind found: the registry did not load"
        missing = sorted(kinds - set(classes.REACH_CLASS))
        assert missing == [], f"kinds with no reach class: {missing}"

    def test_every_class_is_known(self):
        assert set(classes.REACH_CLASS.values()) <= {"none", "brief", "interrupt", "urgent"}

    def test_the_planned_crm_kinds_are_declared(self):
        for kind in ("crm_followup_due", "deal_quiet", "lead_replied"):
            assert classes.REACH_CLASS[kind] == "interrupt"
        assert classes.REACH_CLASS["deal_stage_changed"] == "brief"

    def test_an_unknown_kind_reaches_nobody(self):
        assert classes.reach_class({"kind": "something_new"}) == "none"

    @pytest.mark.parametrize(("reason", "cls"), [
        ("assigned", "interrupt"), ("mention", "interrupt"), ("comment", "brief"), ("", "brief")])
    def test_a_notification_interrupts_only_when_it_asks(self, reason, cls):
        got = classes.reach_class({"kind": "notification", "facts": {"notification": reason}})
        assert got == cls


class TestTheCandidates:
    def test_a_new_needs_reply_is_a_candidate(self):
        got = plan.interrupt_candidates([card("email:a:t1")], set(), NOW)
        assert [c["signal_id"] for c in got] == ["email:a:t1"]

    def test_a_logged_pair_is_not(self):
        c = card("email:a:t1")
        assert plan.interrupt_candidates([c], {plan.pair(c)}, NOW) == []

    def test_a_changed_fingerprint_is_new_again(self):
        c = card("email:a:t1", fp="fp000002")
        assert plan.interrupt_candidates([c], {("email:a:t1", "fp000001")}, NOW) == [c]

    def test_a_stale_thread_does_not_interrupt(self):
        old = card("email:a:t1", facts={"who": "X",
                                        "waiting_since": (NOW - timedelta(hours=13)).isoformat()})
        assert plan.interrupt_candidates([old], set(), NOW) == []

    def test_a_brief_class_card_does_not_interrupt(self):
        assert plan.interrupt_candidates([card("tasks:1", "overdue")], set(), NOW) == []

    @pytest.mark.parametrize("st", ["later"])
    def test_a_card_put_aside_does_not_interrupt(self, st):
        assert plan.interrupt_candidates([card("email:a:t1", st=st)], set(), NOW) == []

    def test_a_card_in_the_later_group_does_not_interrupt(self):
        assert plan.interrupt_candidates([card("email:a:t1", group="later")], set(), NOW) == []

    def test_an_approval_and_a_mention_interrupt(self):
        got = plan.interrupt_candidates([
            card("approvals:1", "approval", title="refund for Acme", facts={}),
            card("tasks:2", "notification", facts={"notification": "mention", "who": "Ravi"}),
        ], set(), NOW)
        assert {c["signal_id"] for c in got} == {"approvals:1", "tasks:2"}


class TestWhen:
    def test_inside_the_day_is_not_quiet(self):
        assert plan.in_quiet_hours(DAY, NOW, True) is False

    def test_before_the_day_is_quiet(self):
        assert plan.in_quiet_hours(DAY, datetime(2026, 10, 15, 2, 0, tzinfo=UTC), True) is True

    def test_after_the_day_is_quiet(self):
        assert plan.in_quiet_hours(DAY, datetime(2026, 10, 15, 13, 0, tzinfo=UTC), True) is True

    def test_a_day_off_is_quiet(self):
        assert plan.in_quiet_hours(DAY, NOW, False) is True

    def test_the_brief_is_due_in_the_first_three_hours(self):
        at = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)  # 09:30 in Kolkata
        assert plan.brief_date(DAY, at, True) == date(2026, 10, 15)

    def test_the_brief_is_not_due_later_or_on_a_day_off(self):
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 7, 0, tzinfo=UTC), True) is None
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 4, 0, tzinfo=UTC), False) is None


class TestTheWords:
    def test_the_interrupt_leads_with_the_act(self):
        text = plan.compose_interrupt([
            card("email:a:t1"),
            card("approvals:1", "approval", title="refund for Acme", facts={}),
        ], APP, now=NOW)
        assert text == ("*Needs you now*\n"
                        "• Reply to Priya Shah: \"Q3 pricing\" (waiting 40 min)\n"
                        "• Approve: refund for Acme\n\n"
                        "Reply here to act on any of these, or open My Day: "
                        "https://app.metorite.com/")

    def test_the_brief_names_the_day_and_counts_the_rest(self):
        cards = [card(f"tasks:{i}", "overdue", title=f"Task {i}", score=60 - i,
                      facts={"days_late": 2}) for i in range(9)]
        cards.append(card("tasks:x", "overdue", group="fyi", title="Not in the brief"))
        text = plan.compose_brief(cards, APP, date(2026, 10, 15), now=NOW)
        assert text is not None
        assert text.startswith("*Your day, Thu 15 Oct*\n• Overdue: Task 0 (2 days late)")
        assert text.count("• ") == plan.BRIEF_LINES
        assert "+2 more. My Day: https://app.metorite.com/" in text
        assert "Not in the brief" not in text

    def test_no_brief_with_nothing_in_now_or_needs_you(self):
        assert plan.compose_brief([card("tasks:1", "overdue", group="fyi")], APP,
                                  date(2026, 10, 15), now=NOW) is None

    def test_a_message_stays_under_the_limit(self):
        long = [card(f"email:a:{i}", title="x" * 300) for i in range(20)]
        assert len(plan.compose_interrupt(long, APP, now=NOW)) <= plan.MAX_CHARS
        assert len(plan.compose_brief(long, APP, date(2026, 10, 15), now=NOW)) <= plan.MAX_CHARS

    def test_a_title_cannot_add_lines(self):
        text = plan.compose_interrupt([card("email:a:t1", title="a\nb\n\n*c*")], APP, now=NOW)
        assert "\"a b *c*\"" in text


class TestTheDedupeKey:
    def test_the_same_plan_gives_the_same_key(self):
        a = [card("email:a:t1"), card("approvals:1", "approval", facts={})]
        assert plan.interrupt_key("M@x.test", a) == plan.interrupt_key("m@x.test", a[::-1])

    def test_a_new_fingerprint_gives_a_new_key(self):
        assert (plan.interrupt_key("m@x.test", [card("email:a:t1")])
                != plan.interrupt_key("m@x.test", [card("email:a:t1", fp="fp000009")]))

    def test_the_brief_key_is_one_a_day_and_holds_no_text(self):
        k1 = plan.brief_key("m@x.test", date(2026, 10, 15))
        assert k1 == plan.brief_key("m@x.test", date(2026, 10, 15))
        assert k1 != plan.brief_key("m@x.test", date(2026, 10, 16))
        assert "m@x.test" not in k1 and k1.startswith("brief:")
