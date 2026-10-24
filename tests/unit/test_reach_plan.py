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
* **Template parameters** (RE-2a). One line, no tab, no run of spaces, at
  most 160 characters, never empty.
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

    def test_a_new_message_in_the_thread_is_new_again(self):
        facts = {"who": "X", "waiting_since": NOW.isoformat(), "message_id": "m1"}
        first = card("email:a:t1", facts=dict(facts))
        second = card("email:a:t1", facts={**facts, "message_id": "m2"})
        assert plan.interrupt_candidates([second], {plan.pair(first)}, NOW) == [second]

    def test_a_comment_on_a_mentioned_task_does_not_interrupt_again(self):
        # Review of RE-1a, P2: a comment adds a note id and moves the feed's
        # fingerprint, but the asking note is the same, so Reach is silent.
        asked = {"notification": "mention", "who": "Asha", "at": "2026-10-15T05:00:00+00:00"}
        before = card("tasks:9", "notification", facts={**asked, "note_ids": ["n1"]})
        after = card("tasks:9", "notification", fp="fp000777",
                     facts={**asked, "note_ids": ["n2", "n1"]})
        assert plan.interrupt_candidates([after], {plan.pair(before)}, NOW) == []

    def test_a_mention_folded_under_an_overdue_task_interrupts(self):
        # Review of RE-1a, P3: the strongest kind on the card decides.
        c = card("tasks:9", "overdue", facts={"notification": "mention", "who": "Asha"})
        c["folded"] = ["notification"]
        assert plan.interrupt_candidates([c], set(), NOW) == [c]
        assert "Asha mentioned you on" in plan.compose_interrupt([c], APP, now=NOW)

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

    def test_the_brief_is_due_until_the_day_end_but_not_after_or_on_a_day_off(self):
        # RE-2a changed this on purpose: a held brief may go at any hour of the
        # day. RE-1a ended the brief window at 3 hours (12:00 here).
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 7, 0, tzinfo=UTC), True) == date(
            2026, 10, 15)  # 12:30 in Kolkata
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 12, 29, tzinfo=UTC), True) == date(
            2026, 10, 15)  # 17:59
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 12, 30, tzinfo=UTC), True) is None
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 3, 0, tzinfo=UTC), True) is None
        assert plan.brief_date(DAY, datetime(2026, 10, 15, 4, 0, tzinfo=UTC), False) is None

    def test_the_template_brief_is_only_for_the_first_three_hours(self):
        assert plan.brief_morning(DAY, datetime(2026, 10, 15, 4, 0, tzinfo=UTC))  # 09:30
        assert plan.brief_morning(DAY, datetime(2026, 10, 15, 6, 29, tzinfo=UTC))  # 11:59
        assert not plan.brief_morning(DAY, datetime(2026, 10, 15, 6, 30, tzinfo=UTC))
        assert not plan.brief_morning(DAY, datetime(2026, 10, 15, 3, 0, tzinfo=UTC))


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

    @staticmethod
    def _ask(sid: str, *, self_: bool, body: str | None, source: str = "chat") -> dict:
        return {**card(f"request:{sid}", "notify_me", group="now", title=f"Ask {sid}"),
                "facts": {"source": source, "urgency": "normal", "self": self_,
                          "body": body}}

    @pytest.mark.parametrize("self_", [True, False])
    def test_a_message_of_one_request_carries_its_whole_body(self, self_):
        # RE-5 review, P1-1: a rule's request too.
        body = "b" * 1000
        text, kept = plan.compose_message(
            [self._ask("r1", self_=self_, body=body, source="chat" if self_ else "workflow")],
            APP, now=NOW)
        assert text == f"*Ask r1*\n\n{body}" and len(kept) == 1

    def test_every_request_line_in_a_batch_carries_its_body_cut_short(self):
        # Two of the member's own, and one of a rule: each keeps its body.
        cards = [self._ask("r1", self_=True, body="first " * 60),
                 self._ask("r2", self_=True, body="second body"),
                 self._ask("r3", self_=False, body="third body", source="email.rule")]
        text, kept = plan.compose_message(cards, APP, now=NOW)
        assert len(kept) == 3
        assert "• Ask r2: second body" in text and "• Ask r3: third body" in text
        first = next(row for row in text.splitlines() if row.startswith("• Ask r1: "))
        assert len(first) - len("• Ask r1: ") == plan.REQUEST_BODY_CHARS
        assert first.endswith("…")

    def test_the_cards_that_do_not_fit_are_not_in_the_kept_list(self):
        # RE-5 review, P1-5: the caller logs only what the message holds.
        cards = [card(f"email:a:{i}", title="x" * 300) for i in range(20)]
        text, kept = plan.compose_message(cards, APP, now=NOW)
        assert len(text) <= plan.MAX_CHARS
        assert 0 < len(kept) < 20 and kept == cards[:len(kept)]
        assert text.count("\n• ") == len(kept)

    def test_the_thread_quotes_a_request_that_the_member_did_not_ask_for(self):
        # RE-5 review, P2-4.
        rule = self._ask("r1", self_=False, body="x", source="email.rule")
        flow = self._ask("r2", self_=False, body="y", source="workflow")
        mine = self._ask("r3", self_=True, body="z")
        assert plan.thread_record([mine], "*Ask r3*\n\nz") == "*Ask r3*\n\nz"
        assert plan.thread_record([rule, flow, mine], "one\ntwo") == (
            "Reach message. The text below came from an email rule and a workflow, "
            "not from Metorite:\n> one\n> two")

    def test_a_title_cannot_add_lines(self):
        text = plan.compose_interrupt([card("email:a:t1", title="a\nb\n\n*c*")], APP, now=NOW)
        assert "\"a b *c*\"" in text


class TestTheDedupeKey:
    def test_the_same_plan_gives_the_same_key(self):
        a = [card("email:a:t1"), card("approvals:1", "approval", facts={})]
        assert plan.interrupt_key("M@x.test", a) == plan.interrupt_key("m@x.test", a[::-1])

    def test_a_new_message_gives_a_new_key(self):
        f = {"who": "X", "waiting_since": NOW.isoformat()}
        assert (plan.interrupt_key("m@x.test", [card("email:a:t1", facts={**f, "message_id": "a"})])
                != plan.interrupt_key("m@x.test", [card("email:a:t1", facts={**f, "message_id": "b"})]))

    def test_the_brief_key_is_one_a_day_and_holds_no_text(self):
        k1 = plan.brief_key("m@x.test", date(2026, 10, 15))
        assert k1 == plan.brief_key("m@x.test", date(2026, 10, 15))
        assert k1 != plan.brief_key("m@x.test", date(2026, 10, 16))
        assert "m@x.test" not in k1 and k1.startswith("brief:")


class TestTheTemplateParameters:
    """RE-2a: Meta refuses a body parameter with a newline, a tab, more than
    four spaces in a row, or no text."""

    @pytest.mark.parametrize(("raw", "want"), [
        ("Approve:\nthe refund", "Approve: the refund"),
        ("a\tb\t\tc", "a b c"),
        ("five     spaces", "five spaces"),
        ("  stripped  ", "stripped"),
        ("", "\u2013"),
        (None, "\u2013"),
        (" \n\t ", "\u2013"),
    ])
    def test_the_text_is_cleaned(self, raw, want):
        assert plan.template_param(raw) == want

    def test_a_long_text_is_cut_with_an_ellipsis(self):
        got = plan.template_param("x" * 300)
        assert len(got) == plan.PARAM_CHARS and got.endswith("…")

    @pytest.mark.parametrize("raw", ["a\nb", "x  " * 200, "\t" * 10, ""])
    def test_no_param_breaks_a_meta_rule(self, raw):
        got = plan.template_param(raw)
        assert got and "\n" not in got and "\t" not in got and "  " not in got
        assert len(got) <= plan.PARAM_CHARS

    @pytest.mark.parametrize(("name", "email", "want"), [
        ("Priya Shah", "p@x.test", "Priya"),
        ("  ", "priya.shah@x.test", "Priya"),
        (None, "raj_kumar@x.test", "Raj"),
        (None, "-anu-k@x.test", "Anu"),
        (None, "@x.test", "there"),
    ])
    def test_the_first_name(self, name, email, want):
        assert plan.first_name(name, email) == want

    def test_the_needs_you_params_name_the_first_and_count_the_rest(self):
        cards = [card("approvals:1", "approval", title="the refund for Acme", facts={}),
                 card("email:a:t1"), card("email:a:t2")]
        assert plan.needs_you_params("Priya", cards[:1], NOW) == [
            "Priya", "Approve: the refund for Acme"]
        got = plan.needs_you_params("Priya", cards, NOW)
        assert got[1].endswith(" (and 2 more)")

    def test_a_long_first_line_keeps_the_count(self):
        cards = [card("approvals:1", "approval", title="y" * 300, facts={}),
                 card("email:a:t1")]
        got = plan.needs_you_params("P", cards, NOW)[1]
        assert got.endswith("… (and 1 more)") and len(got) <= plan.PARAM_CHARS

    def test_the_brief_params(self):
        cards = [card("tasks:1", "overdue", title="Ship it", facts={"days_late": 1}),
                 card("tasks:2", "overdue", group="fyi")]
        assert plan.brief_params("Priya", cards, date(2026, 10, 15), NOW) == [
            "Priya", "Thu 15 Oct", "1", "Overdue: Ship it (1 day late)"]

    def test_the_full_brief_key_is_the_brief_key_and_a_suffix(self):
        d = date(2026, 10, 15)
        assert plan.full_brief_key("m@x.test", d) == plan.brief_key("m@x.test", d) + ":full"


def test_the_no_plan_line_asks_for_the_act():
    """A chat pill sends its line as the member's message, so the line is a
    request, not a bare fact (owner report, 2026-10-22)."""
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    assert plan.line({"kind": "no_plan_yet"}, now) == "Plan my day: nothing is planned yet"
