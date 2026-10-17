"""WS-58 MD-3d fix round 1 — a fold never moves the state fingerprint
(``my_day_feed.md`` §5.9 item 4, §6.8 item 1).

``feed.build_cards`` folds every signal of one entity into one card. A folded
signal of a FEED-ONLY kind (the Calendar kinds and every MD-3 kind) adds no
fact named in ``state.STATE_FACTS`` to the card
(``feed._states_entity_facts``). Such a signal comes and goes with a window,
a horizon or a failed source, so a state fact of it would bring a dismissed
card back with no change to the entity. The review found two: a
``promise_due`` folded under a notification card added ``due_at``, and
MD-3e's ``deal_quiet`` adds ``waiting_since`` under the other CRM kinds.

A folded NEEDS kind keeps its state facts, because they are the entity's
own: a new note on a dismissed overdue task brings it back (§6.8 item 1,
``test_my_day_state.py::TestTheMembersOwnActIsNoUpdate``).

This file covers EVERY kind of ``feed_rank.BASE``. It builds each signal
with its real builder where one exists, and it adds a worst case in which the
folded signal names every state fact. It generalises MD-3c's
``test_feed_task_kinds.py::TestAFoldKeepsTheStateFingerprint``.

Mutations, run by hand (2026-10-16), each killed: a folded state fact merged
again, ``promise_due`` as the main kind with no ``due_at``, and the rule
removed under the dismissed-notification case.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from gateway.routes.shell import feed, feed_rank, state
from gateway.routes.shell import needs as shell

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
TZ = ZoneInfo("UTC")
TODAY = NOW.date()
TID, ACCT, THREAD = "t-1", "a-1", "th-1"
#: The entity of each kind with no signal builder in this tree (MD-3e and
#: MD-3f, and the kinds that only a provider body builds). A new kind must
#: join this table or ``_real``, or ``test_every_kind_has_an_entity`` fails.
NO_BUILDER = {
    "approval": "approvals:pa-1",
    "day_overbooked": f"calendar:{TODAY.isoformat()}",
    "no_plan_yet": f"calendar:{TODAY.isoformat()}",
    "colleague_out": f"calendar:{TODAY.isoformat()}",
    "crm_followup_due": "crm:deal:d-1",
    "deal_quiet": "crm:deal:d-1",
    "deal_close_passed": "crm:deal:d-1",
    "agent_asks": "chat:th-9",
}


def _lens_row(**extra) -> dict:
    return {"id": TID, "title": "One", "importance": None, "leveraged": False,
            "project_name": "Plant", "due_at": None, **extra}


def _promise(due: datetime) -> dict:
    return {"task_id": TID, "title": "One", "due_at": due.isoformat(), "overdue": False,
            "thread_id": THREAD, "message_id": "m-p", "priority": None}


def _note(nid: str, at: datetime) -> dict:
    return {"id": nid, "task_id": TID, "kind": "comment", "actor": "ravi@x.test",
            "task_title": "One", "task_number": None, "excerpt": None, "created_at": at}


def _real(kind: str) -> shell.Signal | None:
    """The signal of ``kind`` from its REAL builder, on one entity of its
    family. None for a kind of ``NO_BUILDER``."""
    if kind in ("overdue", "due_today"):
        due = NOW - timedelta(days=1) if kind == "overdue" else NOW + timedelta(hours=3)
        return shell._task_signals([(kind, _lens_row(due_at=due.isoformat()))], "UTC",
                                   TODAY.isoformat(), calendar=True)[0]
    if kind == "notification":
        return shell._note_signals([_note("n-1", NOW - timedelta(hours=1))])[0]
    if kind == "block_soon":
        return feed._block_soon([{"id": TID, "title": "One",
                                  "start": (NOW + timedelta(minutes=20)).isoformat(),
                                  "end": (NOW + timedelta(minutes=80)).isoformat()}], {}, NOW)[0]
    if kind == "block_overlap":
        rows = [{"id": i, "title": i, "disposition": "NEXT",
                 "scheduled_start": (NOW + timedelta(hours=h)).isoformat(),
                 "scheduled_end": (NOW + timedelta(hours=h + 2)).isoformat()}
                for i, h in ((TID, 1), ("t-2", 2))]
        return feed._overlaps(rows, {}, NOW, TZ)[0]
    if kind == "waiting_quiet":
        return feed._quiet_signal(_lens_row(disposition="WAITING",
                                            delegated_at="2026-10-01T09:00:00+00:00",
                                            waiting_on={"name": "Ravi"}), TODAY, TZ)
    if kind == "stale_next":
        return feed._quiet_signal(_lens_row(disposition="NEXT",
                                            last_change_at="2026-09-01T09:00:00+00:00"),
                                  TODAY, TZ)
    if kind == "unblocked":
        return feed._unblocked_signal(_lens_row(unblocked_by={
            "readable": True, "id": "b-1", "title": "API", "closed_by": "r@x.test",
            "closed_at": "2026-10-12T09:00:00+00:00"}))
    if kind == "absent_on_due":
        return feed._absent_signal(_lens_row(due_at="2026-10-15T11:00:00+00:00"),
                                   {date(2026, 10, 15)}, TZ)
    if kind == "promise_due":
        return feed._promise_signal(_promise(NOW + timedelta(days=2)), ACCT)
    if kind == "needs_reply":
        return shell._thread_signal({"id": f"email:{ACCT}:{THREAD}", "title": "S",
                                     "detail": "From dana",
                                     "href": f"/email?email=m-1&account={ACCT}",
                                     "at": (NOW - timedelta(hours=5)).isoformat()}, "m-1")
    if kind == "digest_ready":
        return feed._digest_signal({"week_start": date(2026, 10, 5), "stored_at": NOW})
    if kind == "crm_open_questions":
        # WS-53 CRM-Q32-4: the open questions of the CRM, for a CRM admin.
        return feed._questions_signal({"new_at": NOW, "count": 2, "open": 5, "money": True})
    if kind == "no_answer":
        return feed._no_answer_signal({
            "thread_id": THREAD, "message_id": "m-2", "subject": "S", "who": "dana",
            "last_message_at": (NOW - timedelta(days=6)).isoformat()}, ACCT)
    return None


def _signal_of(kind: str) -> shell.Signal:
    real = _real(kind)
    if real is not None:
        return real
    return shell.Signal(signal_id=NO_BUILDER[kind], app="x", kind=kind,
                        entity={"type": "x", "id": "x", "label": "X"}, title="X",
                        facts={}, href="/x", acts=())


def _with_every_state_fact(signal: shell.Signal) -> shell.Signal:
    """The worst case: the signal also names each state fact."""
    return dataclasses.replace(signal, facts={
        **signal.facts, **{n: f"v-{n}" for n in state.STATE_FACTS}})


def _card(*signals: shell.Signal) -> dict:
    [c] = feed.build_cards(list(signals), NOW, TZ, TODAY)
    return c


def _fp(*signals: shell.Signal) -> str:
    c = _card(*signals)
    return state.state_fp(c["signal_id"], c["facts"])


def _pairs() -> list[tuple[str, str]]:
    """Each (main, folded) pair of kinds that can share one ``signal_id``."""
    by_entity: dict[str, list[str]] = {}
    for kind in feed_rank.BASE:
        by_entity.setdefault(_signal_of(kind).signal_id, []).append(kind)
    return sorted((m, f) for kinds in by_entity.values() for m in kinds for f in kinds
                  if feed_rank.base(m) > feed_rank.base(f))


FEED_ONLY_PAIRS = [(m, f) for m, f in _pairs() if f not in shell.KIND_ORDER]


class TestAFoldNeverMovesTheStateFingerprint:
    def test_every_kind_has_an_entity(self):
        for kind in feed_rank.BASE:
            assert _signal_of(kind).kind == kind
        pairs = _pairs()
        # Non-vacuity: the pairs of the review are here.
        assert ("notification", "promise_due") in pairs
        assert ("deal_close_passed", "deal_quiet") in pairs
        assert ("crm_followup_due", "deal_quiet") in pairs
        assert ("needs_reply", "no_answer") in pairs
        assert len(FEED_ONLY_PAIRS) > 40

    @pytest.mark.parametrize(("main", "folded"), FEED_ONLY_PAIRS)
    def test_a_folded_feed_only_kind_keeps_the_fingerprint(self, main, folded):
        alone = _fp(_signal_of(main))
        assert _fp(_signal_of(main), _signal_of(folded)) == alone
        assert _fp(_signal_of(main), _with_every_state_fact(_signal_of(folded))) == alone

    def test_only_these_folds_carry_a_needs_kind(self):
        """The one exception, by name. A new needs kind that can fold must be
        read against §6.8 item 1 here."""
        assert {(m, f) for m, f in _pairs() if f in shell.KIND_ORDER} == {
            ("overdue", "due_today"), ("overdue", "notification"),
            ("due_today", "notification")}

    def test_a_folded_notification_still_names_its_notes(self):
        """§6.8 item 1: a new note brings a dismissed task back."""
        overdue, note = _signal_of("overdue"), _signal_of("notification")
        assert _card(overdue, note)["facts"]["note_ids"] == ["n-1"]
        assert _fp(overdue, note) != _fp(overdue)

    def test_promise_due_as_the_main_kind_keeps_its_due_time(self):
        promise = _signal_of("promise_due")
        c = _card(promise)
        due = (NOW + timedelta(days=2)).isoformat()
        assert c["facts"]["due_at"] == due == c["urgent_at"]
        assert c["why"] == "You promised this by mail. It is due on Wednesday."
        moved = feed._promise_signal(_promise(NOW + timedelta(days=3)), ACCT)
        assert _fp(moved) != _fp(promise)
        soon = feed._promise_signal(_promise(NOW + timedelta(hours=1)), ACCT)
        assert _card(soon)["group"] == "now"

    def test_a_folded_promise_still_lifts_the_card(self):
        """The folded signal's own facts still set its clause, its group and
        its urgency. Only the card's state facts stay the main signal's."""
        note = _signal_of("notification")
        soon = feed._promise_signal(_promise(NOW + timedelta(hours=1)), ACCT)
        c = _card(note, soon)
        assert (c["kind"], c["folded"], c["group"]) == ("notification", ["promise_due"], "now")
        assert "due_at" not in c["facts"]
        assert c["why"].endswith(" You promised this by mail.")
        assert c["score"] == _card(note)["score"] + feed_rank.URGENT_SOON + 10


class TestADismissedCardStaysDismissed:
    DAY = state.Day(ZoneInfo("UTC"), 7, 22)

    def _shown(self, row: dict, *signals: shell.Signal) -> bool:
        built = feed.build_cards(list(signals), NOW, TZ, TODAY)
        return bool(state.join(built, {f"tasks:{TID}": row}, state.Context(NOW, self.DAY)))

    def test_a_promise_that_folds_in_and_leaves_never_brings_it_back(self):
        note = _signal_of("notification")
        built = feed.build_cards([note], NOW, TZ, TODAY)
        fp = state.join(built, {}, state.Context(NOW, self.DAY))[0]["state_fp"]
        row = {"kind": "notification", "state": "dismissed", "dismiss_reason": "dismiss",
               "fingerprint": fp, "visits": 0, "visits_day": None, "last_seen_at": None,
               "snoozed_until": None, "snooze_on_change": False}
        assert not self._shown(row, note)
        # The promise folds in: still hidden.
        assert not self._shown(row, note, _signal_of("promise_due"))
        # Its source fails, or it leaves its horizon: still hidden.
        assert not self._shown(row, note)
        # Non-vacuity: a new note is a change of the entity, and brings it back.
        newer = shell._note_signals([_note("n-1", NOW - timedelta(hours=1)),
                                     _note("n-2", NOW - timedelta(minutes=5))])[0]
        assert self._shown(row, newer)
