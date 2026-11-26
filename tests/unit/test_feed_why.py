"""WS-58 MD-9, LOOP-4b — the sentences of "Why am I seeing this".

Spec: ``project-docs/specs/proactive_loop.md`` §7.2, the pins W2 and W3, and
done-when (g). Module: ``gateway/routes/shell/feed_why.py``.

Each row of the sentence table of W3 has one class here, with a pass case and
a fail case. The pass case shows the sentence. The fail case is the nearest
card that must not show it, so a build that shows a sentence when its rule is
not true fails.

* ``TestRealCards`` builds the card through ``feed.build_cards`` and
  ``state.join``, so the group and the facts are the ones the feed computes.
* No sentence holds text from a record, and ``explain`` changes nothing.

The wiring of the field into the answer is ``test_my_day_state.py``.

Run: ``uv run pytest tests/unit/test_feed_why.py -q``.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from gateway.routes.shell import feed, feed_rank, feed_why, state
from gateway.routes.shell.needs import Signal, link

#: Tuesday 13 October 2026, 13:30 UTC. The member's day starts at 07:00 UTC.
NOW = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
TZ = ZoneInfo("UTC")
TODAY = date(2026, 10, 13)
CTX = state.Context(NOW, state.Day(TZ, 7, 22))

NOW_LINE = "It is in Now, because a time on it is close or has passed, or it overlaps today."
NEEDS_YOU_LINE = "It is in Needs you, because it asks you to act."
READY_LINE = "It is in Ready for you, because a task that blocked it closed."
FYI_LINE = "It is in For your information, which comes after the cards that need you."
MUTED_LINE = ("It is in Later because you muted this kind. It needs you, so it stays in the "
              "feed.")
LATER_LINE = "It is in Later, because you saw it 3 times in one day."
PASSED_LINE = "Its start or due time has passed."
SOON_LINE = "Its start or due time is 2 hours away or less."
PROMISE_LINE = "It holds a promise that you made."
OVERLAP_STARTED_LINE = "An overlap on it has started."
OVERLAP_SOON_LINE = "An overlap on it starts in 2 hours or less."
HIGHER_LINE = ('In today\'s count of the last 30 days, you acted on this kind more than you marked '
               'it "Not useful", so it ranks higher.')
LOWER_LINE = ('In today\'s count of the last 30 days, you marked this kind "Not useful" more than '
              'you acted on it, so it ranks lower.')
NOT_LOWERED_LINE = ('In today\'s count, your "Not useful" marks on this kind outnumber your acts. '
                    'This card needs you, so they do not lower it.')

#: The first words of each sentence of rows 2a to 2c.
TIME_ROWS = ("It is 1 day late.", PASSED_LINE, SOON_LINE)


def _card(kind: str = "stale_next", *, group: str = "fyi", folded: tuple[str, ...] = (),
          facts: dict | None = None, urgent: datetime | None = None, visits: int = 0,
          sid: str = "tasks:t1") -> dict:
    return {"signal_id": sid, "kind": kind, "folded": list(folded), "group": group,
            "facts": facts or {}, "urgent_at": urgent.isoformat() if urgent else None,
            "visits": visits, "title": "Pay Ravi", "why": "Ravi wrote about the quote."}


def _taught(mutes=(), affinity=None) -> state.Learned:
    return state.Learned(frozenset(mutes), dict(affinity or {}))


def _explain(card: dict, taught: state.Learned | None = None,
             acted: frozenset[str] = frozenset()) -> list[str]:
    return feed_why.explain(card, taught or _taught(), acted, NOW)


# ── Row 1: the group ────────────────────────────────────────────────────────


class TestRow1aNow:
    def test_pass(self):
        assert _explain(_card(group="now"))[0] == NOW_LINE

    def test_fail_another_group(self):
        assert NOW_LINE not in _explain(_card(group="needs_you"))


class TestRow1bNeedsYou:
    def test_pass(self):
        assert _explain(_card(group="needs_you"))[0] == NEEDS_YOU_LINE

    def test_fail_another_group(self):
        assert NEEDS_YOU_LINE not in _explain(_card(group="fyi"))


class TestRow1cReady:
    def test_pass(self):
        assert _explain(_card("unblocked", group="ready"))[0] == READY_LINE

    def test_fail_another_group(self):
        # An unblocked card that sits in Later says nothing about a blocker.
        assert READY_LINE not in _explain(_card("unblocked", group="later"))


class TestRow1dForYourInformation:
    def test_pass(self):
        assert _explain(_card(group="fyi"))[0] == FYI_LINE

    def test_fail_another_group(self):
        assert FYI_LINE not in _explain(_card(group="ready"))


class TestRow1eLaterBecauseMuted:
    def test_pass(self):
        got = _explain(_card("overdue", group="later"), _taught(mutes={"overdue"}))
        assert got[0] == MUTED_LINE

    def test_pass_a_needs_kind_folded_in(self):
        card = _card("block_soon", group="later", folded=("overdue",))
        assert _explain(card, _taught(mutes={"block_soon"}))[0] == MUTED_LINE

    def test_fail_the_mute_is_of_a_folded_kind(self):
        # A mute keys on the main kind (P4), so this card is in Later for its visits.
        card = _card("overdue", group="later", folded=("block_soon",))
        got = _explain(card, _taught(mutes={"block_soon"}))
        assert MUTED_LINE not in got and got[0] == LATER_LINE

    def test_fail_a_muted_card_in_another_group(self):
        got = _explain(_card("overdue", group="needs_you"), _taught(mutes={"overdue"}))
        assert MUTED_LINE not in got


class TestRow1fLaterBecauseSeen:
    def test_pass(self):
        assert _explain(_card("overdue", group="later"))[0] == LATER_LINE

    def test_pass_a_muted_card_that_needs_nothing(self):
        # The join drops such a card. If one ever reached Later, it names no mute.
        got = _explain(_card("stale_next", group="later"), _taught(mutes={"stale_next"}))
        assert got[0] == LATER_LINE

    def test_fail_a_muted_needs_card(self):
        got = _explain(_card("overdue", group="later"), _taught(mutes={"overdue"}))
        assert LATER_LINE not in got


# ── Row 2: the time ─────────────────────────────────────────────────────────


class TestRow2aDaysLate:
    @pytest.mark.parametrize(("days", "line"), [
        (1, "It is 1 day late."), (2, "It is 2 days late."), (48, "It is 48 days late.")])
    def test_pass(self, days, line):
        assert line in _explain(_card("overdue", facts={"days_late": days}))

    def test_pass_a_folded_overdue(self):
        card = _card("block_soon", folded=("overdue",), facts={"days_late": 3},
                     urgent=NOW - timedelta(hours=1))
        got = _explain(card)
        # One time sentence only: the days late come before the passed time.
        assert "It is 3 days late." in got and PASSED_LINE not in got

    @pytest.mark.parametrize("card", [
        _card("overdue", facts={"days_late": 0}),
        _card("overdue", facts={}),
        _card("overdue", facts={"days_late": True}),
        _card("due_today", facts={"days_late": 4}),
    ], ids=["0-days", "no-fact", "a-bool", "not-overdue"])
    def test_fail(self, card):
        assert not [line for line in _explain(card) if "late." in line]


class TestRow2bTheTimePassed:
    @pytest.mark.parametrize("at", [NOW, NOW - timedelta(days=2)], ids=["now", "past"])
    def test_pass(self, at):
        assert PASSED_LINE in _explain(_card(urgent=at))

    def test_pass_an_overdue_card_with_no_day_late(self):
        card = _card("overdue", facts={"days_late": 0}, urgent=NOW - timedelta(minutes=5))
        assert PASSED_LINE in _explain(card)

    @pytest.mark.parametrize("card", [
        _card(urgent=NOW + timedelta(seconds=1)),
        _card(),
        _card("overdue", facts={"days_late": 2}, urgent=NOW - timedelta(days=2)),
        _card("block_overlap", urgent=NOW),
    ], ids=["to-come", "no-time", "days-late-says-it", "an-overlap-card"])
    def test_fail(self, card):
        assert PASSED_LINE not in _explain(card)


class TestRow2cTheTimeIsSoon:
    @pytest.mark.parametrize("ahead", [timedelta(seconds=1), timedelta(hours=2)],
                             ids=["1-second", "2-hours"])
    def test_pass(self, ahead):
        assert SOON_LINE in _explain(_card(urgent=NOW + ahead))

    @pytest.mark.parametrize("card", [
        _card(urgent=NOW + timedelta(hours=2, seconds=1)),
        _card(urgent=NOW),
        _card(),
        _card("overdue", facts={"days_late": 1}, urgent=NOW + timedelta(hours=1)),
        _card("block_overlap", urgent=NOW + timedelta(hours=1)),
    ], ids=["past-2-hours", "now", "no-time", "days-late-says-it", "an-overlap-card"])
    def test_fail(self, card):
        assert SOON_LINE not in _explain(card)


class TestRow2dTheOverlapStarted:
    @pytest.mark.parametrize("at", [NOW, NOW - timedelta(hours=3)], ids=["now", "past"])
    def test_pass(self, at):
        got = _explain(_card("block_overlap", urgent=at))
        assert OVERLAP_STARTED_LINE in got and PASSED_LINE not in got

    @pytest.mark.parametrize("card", [
        _card("block_overlap", urgent=NOW + timedelta(seconds=1)),
        _card("block_overlap"),
        _card("block_soon", urgent=NOW),
        _card("overdue", folded=("block_overlap",), facts={"days_late": 0}, urgent=NOW),
    ], ids=["to-come", "no-time", "another-kind", "a-folded-overlap"])
    def test_fail(self, card):
        assert OVERLAP_STARTED_LINE not in _explain(card)

    def test_a_folded_overlap_keeps_the_sentence_of_the_main_kind(self):
        card = _card("overdue", folded=("block_overlap",), facts={"days_late": 0},
                     urgent=NOW - timedelta(minutes=5))
        assert PASSED_LINE in _explain(card)


class TestRow2eTheOverlapIsSoon:
    @pytest.mark.parametrize("ahead", [timedelta(seconds=1), timedelta(hours=2)],
                             ids=["1-second", "2-hours"])
    def test_pass(self, ahead):
        got = _explain(_card("block_overlap", urgent=NOW + ahead))
        assert OVERLAP_SOON_LINE in got and SOON_LINE not in got

    @pytest.mark.parametrize("card", [
        _card("block_overlap", urgent=NOW + timedelta(hours=2, seconds=1)),
        _card("block_overlap", urgent=NOW),
        _card("block_overlap"),
        _card("block_soon", urgent=NOW + timedelta(hours=1)),
        _card("overdue", folded=("block_overlap",), facts={"days_late": 0},
              urgent=NOW + timedelta(hours=1)),
    ], ids=["past-2-hours", "now", "no-time", "another-kind", "a-folded-overlap"])
    def test_fail(self, card):
        assert OVERLAP_SOON_LINE not in _explain(card)


# ── Rows 3 and 4: the priority and the promise ──────────────────────────────


class TestRow3Priority:
    @pytest.mark.parametrize(("cell", "label"), [
        ("critical", "Critical"), ("urgent", "Urgent"), ("high-leverage", "High-Leverage"),
        ("important", "Important"), ("quick-leverage", "Quick Leverage Win"),
        ("speculative-bet", "Speculative Bet")])
    def test_pass(self, cell, label):
        assert f"Its priority is {label}." in _explain(_card(facts={"priority": cell}))

    @pytest.mark.parametrize("cell", ["low-priority", None, "high", 3, ["urgent"]],
                             ids=["level-0", "none", "not-a-cell", "a-number", "a-list"])
    def test_fail(self, cell):
        got = _explain(_card(facts={"priority": cell}))
        assert not [line for line in got if line.startswith("Its priority")]


class TestRow4Promise:
    @pytest.mark.parametrize("card", [
        _card("promise_due"), _card("overdue", folded=("promise_due",))],
        ids=["main", "folded"])
    def test_pass(self, card):
        assert PROMISE_LINE in _explain(card)

    def test_fail(self):
        assert PROMISE_LINE not in _explain(_card("overdue", folded=("block_soon",)))


# ── Row 5: what the feed learned ────────────────────────────────────────────


class TestRow5aRanksHigher:
    def test_pass(self):
        got = _explain(_card("stale_next"), _taught(affinity={"stale_next": 4}))
        assert HIGHER_LINE in got

    def test_pass_a_needs_card_too(self):
        got = _explain(_card("overdue"), _taught(affinity={"overdue": 20}))
        assert HIGHER_LINE in got

    @pytest.mark.parametrize("affinity", [{}, {"stale_next": -4}, {"deal_quiet": 4}],
                             ids=["none", "below-0", "a-folded-kind"])
    def test_fail(self, affinity):
        card = _card("stale_next", folded=("deal_quiet",))
        assert HIGHER_LINE not in _explain(card, _taught(affinity=affinity))


class TestRow5bRanksLower:
    def test_pass(self):
        got = _explain(_card("stale_next"), _taught(affinity={"stale_next": -12}))
        assert LOWER_LINE in got

    @pytest.mark.parametrize(("card", "affinity"), [
        (_card("overdue"), {"overdue": -12}),
        (_card("block_soon", folded=("overdue",)), {"block_soon": -12}),
        (_card("stale_next"), {"stale_next": 12}),
        (_card("stale_next", folded=("deal_quiet",)), {"deal_quiet": -12}),
    ], ids=["a-needs-card", "a-folded-needs-kind", "above-0", "a-folded-kind"])
    def test_fail(self, card, affinity):
        assert LOWER_LINE not in _explain(card, _taught(affinity=affinity))


class TestRow5cANeedsCardIsNotLowered:
    @pytest.mark.parametrize(("card", "kind"), [
        (_card("overdue"), "overdue"),
        (_card("block_soon", folded=("overdue",)), "block_soon")],
        ids=["main", "folded"])
    def test_pass(self, card, kind):
        assert NOT_LOWERED_LINE in _explain(card, _taught(affinity={kind: -20}))

    @pytest.mark.parametrize(("card", "affinity"), [
        (_card("stale_next"), {"stale_next": -20}),
        (_card("overdue"), {"overdue": 8}),
        (_card("overdue"), {}),
    ], ids=["not-a-needs-card", "above-0", "none"])
    def test_fail(self, card, affinity):
        assert NOT_LOWERED_LINE not in _explain(card, _taught(affinity=affinity))


# ── Row 6: the visits of the day ────────────────────────────────────────────


class TestRow6Visits:
    @pytest.mark.parametrize(("visits", "line"), [
        (1, "You saw it 1 time today, so it ranks a little lower."),
        (2, "You saw it 2 times today, so it ranks a little lower.")])
    def test_pass(self, visits, line):
        # Another card's act is not this card's act.
        assert line in _explain(_card(visits=visits), acted=frozenset({"tasks:other"}))

    @pytest.mark.parametrize(("visits", "acted"), [
        (0, frozenset()), (2, frozenset({"tasks:t1"}))], ids=["no-visit", "acted"])
    def test_fail(self, visits, acted):
        got = _explain(_card(visits=visits), acted=acted)
        assert not [line for line in got if line.startswith("You saw it")]


# ── The list ────────────────────────────────────────────────────────────────


def _full_card() -> tuple[dict, state.Learned]:
    card = _card("overdue", group="needs_you", folded=("promise_due",), visits=2,
                 facts={"days_late": 2, "priority": "urgent", "title": "SECRET-FACT",
                        "who": "SECRET-WHO"},
                 urgent=NOW - timedelta(days=2))
    return card, _taught(affinity={"overdue": -8})


class TestTheList:
    def test_the_order_of_the_table(self):
        card, taught = _full_card()
        assert _explain(card, taught) == [
            NEEDS_YOU_LINE, "It is 2 days late.", "Its priority is Urgent.", PROMISE_LINE,
            NOT_LOWERED_LINE, "You saw it 2 times today, so it ranks a little lower."]

    def test_no_sentence_holds_text_from_the_record(self):
        card, taught = _full_card()
        text = " ".join(_explain(card, taught))
        for word in ("SECRET", "Ravi", card["signal_id"]):
            assert word not in text

    def test_it_changes_nothing(self):
        card, taught = _full_card()
        before = copy.deepcopy(card)
        _explain(card, taught)
        assert card == before and "why_seen" not in card


# ── Done-when (g): the cards the feed builds ────────────────────────────────


def _signal(kind: str, sid: str, facts: dict, *, app: str = "tasks",
            urgent: datetime | None = None) -> Signal:
    return Signal(signal_id=sid, app=app, kind=kind,
                  entity={"type": "task", "id": sid, "label": "Pay Ravi"}, title="Pay Ravi",
                  facts=facts, href="/projects", acts=(link("open", "Open", "/projects"),),
                  urgent_at=urgent.isoformat() if urgent else None)


def _joined(signal: Signal, rows: dict | None = None) -> dict:
    cards = feed.build_cards([signal], NOW, TZ, TODAY)
    (out,) = state.join(cards, rows or {}, CTX, _taught())
    return out


def _block(task_id: str, start: tuple[int, int], end: tuple[int, int]) -> dict:
    def _at(clock: tuple[int, int]) -> str:
        return datetime(TODAY.year, TODAY.month, TODAY.day, *clock, tzinfo=UTC).isoformat()

    return {"id": task_id, "title": f"Block {task_id}", "disposition": "NEXT",
            "scheduled_start": _at(start), "scheduled_end": _at(end)}


def _overlap_answer(rows: list[dict], now: datetime) -> tuple[dict, list[str]]:
    """The one overlap card of these blocks and its sentences, through the
    provider, the fold, the state join and the order."""
    signals = feed._overlaps(rows, {}, now, TZ)
    cards = feed.build_cards(signals, now, TZ, TODAY)
    ctx = state.Context(now, CTX.day)
    (card,) = feed_rank.order(state.join(cards, {}, ctx, _taught()), 15)
    return card, feed_why.explain(card, _taught(), frozenset(), now)


class TestRealCards:
    @pytest.mark.parametrize(("a", "b", "overlap_at"), [
        # A started 4.5 hours ago. The overlap, the start of B, is to come.
        (((9, 0), (18, 0)), ((15, 0), (16, 0)), "2026-10-13T15:00:00+00:00"),
        (((13, 0), (15, 0)), ((14, 0), (15, 0)), "2026-10-13T14:00:00+00:00"),
    ], ids=["a-long-block", "a-short-block"])
    def test_an_overlap_that_comes_soon_says_nothing_about_the_start_of_the_card(
            self, a, b, overlap_at):
        card, got = _overlap_answer([_block("a", *a), _block("b", *b)], NOW)
        assert (card["signal_id"], card["kind"], card["urgent_at"]) == (
            "tasks:a", "block_overlap", overlap_at)
        assert got == [NOW_LINE, OVERLAP_SOON_LINE]

    def test_an_overlap_that_runs_says_that_it_started(self):
        rows = [_block("a", (9, 0), (18, 0)), _block("b", (15, 0), (16, 0))]
        card, got = _overlap_answer(rows, NOW + timedelta(hours=2))
        assert card["urgent_at"] == "2026-10-13T15:00:00+00:00"
        assert got == [NOW_LINE, OVERLAP_STARTED_LINE]

    def test_an_overlap_later_today_gives_row_1a(self):
        later = NOW + timedelta(hours=6)
        card = _joined(_signal("block_overlap", "tasks:o", {"overlap_day": TODAY.isoformat()},
                               app="calendar", urgent=later))
        # Six hours ahead: no time row, and the group sentence still holds.
        assert _explain(card) == [NOW_LINE]

    def test_a_waiting_quiet_card_gives_row_1b(self):
        card = _joined(_signal("waiting_quiet", "tasks:w", {"waited_days": 4}))
        assert _explain(card) == [NEEDS_YOU_LINE]

    def test_a_doc_needs_review_card_gives_row_1d(self):
        card = _joined(_signal("doc_needs_review", "knowledge:page:p", {"space": "HR"},
                               app="knowledge"))
        assert _explain(card) == [FYI_LINE]

    def test_a_card_with_an_act_and_then_a_sighting_claims_nothing_about_acts(self):
        built = feed.build_cards([_signal("stale_next", "tasks:s", {})], NOW, TZ, TODAY)[0]
        fp = state.state_fp("tasks:s", built["facts"])

        def _item(action: str) -> state.StateItem:
            return state.StateItem(signal_id="tasks:s", state_fp=fp, kind="stale_next",
                                   action=action)

        earlier = NOW - timedelta(hours=2)
        acted = state.next_row(None, _item("acted"), earlier, CTX.day)
        seen = state.next_row(acted, _item("seen"), NOW - timedelta(hours=1), CTX.day)
        assert (seen["state"], seen["acted_at"]) == ("seen", earlier)
        card = _joined(_signal("stale_next", "tasks:s", {}), {"tasks:s": seen})
        got = _explain(card)
        assert got == [FYI_LINE, "You saw it 1 time today, so it ranks a little lower."]
        assert not [line for line in got if "act" in line.split()]
