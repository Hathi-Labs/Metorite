"""WS-58 MD-3d — the mail kinds of My Day (``my_day_feed.md`` §5.9 items 2
to 8, 10.1 and 12).

The source ``email_followups`` gives two kinds: ``promise_due``, an open
promise that the member made by mail and that falls due, and ``no_answer``,
a mail the member sent that waits too long for an answer. The R8 half, with
the real SQL of the two new wrappers, is ``test_feed_mail_kinds_r8.py``.
This file fences what the feed does with the rows, and the wrappers' own
contract with a recording fake:

* **Dark by the list** (done-when 1). With ``email_followups`` not in
  ``MY_DAY_PROVIDERS``, no read runs and the answer has no new byte.
* **Each kind gives its card** with the ``BASE``, group, ``why_key``, text,
  acts and label of §5.9 (done-when 2).
* **A promise needs the lens.** A member without ``feature:projects`` gets
  no promise read and no ``promise_due``.
* **A failed read is ``failed``, never an empty ``ok``** (done-when 5). A
  failed mailbox costs only its own rows.
* **The weight** (done-when 9): a card with ``promise_due`` weighs 10 more.
* **The sessions** (done-when 10): 1, plus 2 for each mailbox.
* **The wrappers** raise, check the owner first, name the tenant, read only
  commitments, and bound the thread list in SQL.

Mutations, run by hand (2026-10-15), each killed: the list is in the MD-3d
ticket of ``my_day_feed.md`` §16.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from gateway.routes.email import digest
from gateway.routes.shell import feed, feed_decide, feed_rank
from gateway.routes.shell import needs as shell
from sqlalchemy.exc import OperationalError

from tests.unit.test_feed_task_kinds import _closes_done, _labels
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
from tests.unit.test_shell_needs import ME, OTHER

MAIL_KINDS = ("promise_due", "no_answer")
#: A database error, as the driver raises it (done-when 5).
DB_DOWN = OperationalError("SELECT 1", {}, Exception("the server closed the connection"))


class MailReads:
    """The two new wrappers, each answering by the member it is handed, as
    the real one does: a mailbox of another member is 404."""

    def __init__(self, f: FeedFakes) -> None:
        self.f = f
        self.now = f.now
        self.calls: list[tuple[str, str, dict]] = []
        #: The AWAITING rows, by mailbox, as `_digest_thread_list` gives them.
        self.threads: dict[str, list[dict]] = {}
        #: The promise rows, by mailbox, as `promise_tasks` gives them.
        self.promises: dict[str, list[dict]] = {}
        self.fail: dict[tuple[str, str], BaseException] = {}
        self.slow: set[str] = set()

    def _owned(self, user, account_id: str) -> None:
        if account_id not in {a["id"] for a in self.f.accounts.get(user.email, [])}:
            raise HTTPException(status_code=404, detail="Account not found")

    async def awaiting_threads(self, user, account_id, limit, *, after, before=None):
        self.calls.append(("awaiting_threads", account_id,
                           {"limit": limit, "after": after, "before": before}))
        if account_id in self.slow:
            await asyncio.sleep(5)
        self._owned(user, account_id)
        if ("awaiting_threads", account_id) in self.fail:
            raise self.fail[("awaiting_threads", account_id)]
        return [dict(t) for t in self.threads.get(account_id, [])][:limit]

    async def promise_tasks(self, user, account_id, *, horizon_days, limit, now=None):
        self.calls.append(("promise_tasks", account_id,
                           {"horizon_days": horizon_days, "limit": limit, "now": now}))
        self._owned(user, account_id)
        if ("promise_tasks", account_id) in self.fail:
            raise self.fail[("promise_tasks", account_id)]
        return [dict(r) for r in self.promises.get(account_id, [])][:limit]


@pytest.fixture()
def mail(fakes, monkeypatch) -> MailReads:  # noqa: F811
    r = MailReads(fakes)
    monkeypatch.setattr(digest, "awaiting_threads", r.awaiting_threads)
    monkeypatch.setattr(digest, "promise_tasks", r.promise_tasks)
    return r


def _thread(thread_id: str, message_id: str | None, subject: str, at: datetime,
            who: str = "dana") -> dict:
    return {"thread_id": thread_id, "message_id": message_id, "subject": subject,
            "who": who, "age_days": 5, "important": False, "unread": False,
            "last_message_at": at.isoformat()}


def _promise(task_id: str, title: str, due: datetime, message_id: str | None = "m-p",
             priority: str | None = None) -> dict:
    return {"task_id": task_id, "title": title, "due_at": due.isoformat(),
            "overdue": False, "thread_id": "th-p", "message_id": message_id,
            "priority": priority}


def _seed(r: MailReads) -> None:
    now = r.now
    r.threads["a-mine"] = [_thread("th-wait", "m-wait", "Quote for 40 units",
                                   now - timedelta(days=6))]
    r.promises["a-mine"] = [_promise("t-prom", "Send the drawings", now + timedelta(days=2))]
    # A separate mailbox and another member's mailbox hold rows too: neither
    # may reach the feed.
    r.threads["a-separate"] = [_thread("th-sep", "m-sep", "Board papers", now - timedelta(days=6))]
    r.threads["a-theirs"] = [_thread("th-theirs", "m-theirs", "Their secret",
                                     now - timedelta(days=6))]


def _tz(f: FeedFakes) -> ZoneInfo:
    return ZoneInfo(f.zone)


# ── done-when 1: dark until listed ──────────────────────────────────────────


class TestDarkUntilListed:
    def test_the_provider_is_gated_and_feed_only(self):
        p = shell.REGISTRY["email_followups"]
        assert (p.gated, p.needs, p.features, p.app, p.handled, p.permission, p.admin) == (
            True, False, ("email",), "email", None, None, False)
        assert p.kinds == frozenset(MAIL_KINDS)
        assert shell.source_app("email_followups") == "email"

    def test_with_the_list_empty_no_read_runs_and_no_byte_changes(self, mail, monkeypatch):
        _list(monkeypatch, "")
        _seed(mail)
        answer = run_feed(member(*FULL))
        assert mail.calls == []
        assert "email_followups" not in answer["sources"]
        assert "email_followups" not in answer["source_apps"]
        assert all("source" not in c for c in answer["cards"])
        assert not {c["kind"] for c in answer["cards"]} & set(MAIL_KINDS)
        assert not any(set(c["folded"]) & set(MAIL_KINDS) for c in answer["cards"])

    def test_listed_it_runs(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        answer = run_feed(member(*FULL))
        assert answer["sources"]["email_followups"] == "ok"
        assert answer["source_apps"]["email_followups"] == "email"

    def test_a_member_without_email_gets_absent_and_no_read(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        answer = run_feed(member("projects", "tasks"))
        assert answer["sources"]["email_followups"] == "absent"
        assert mail.calls == []


# ── done-when 2: each kind gives its card ───────────────────────────────────


class TestEachKindGivesItsCard:
    @pytest.fixture()
    def answer(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        self.mail = mail
        return run_feed(member(*FULL))

    def test_promise_due(self, answer):
        c = card(answer, "tasks:t-prom")
        due = self.mail.now + timedelta(days=2)
        assert (c["kind"], c["app"], c["group"], c["why_key"], c["source"]) == (
            "promise_due", "email", "needs_you", "promised", "email_followups")
        tz = _tz(self.mail.f)
        when = feed._when(due.isoformat(), tz, self.mail.now.astimezone(tz).date())
        assert c["why"] == f"You promised this by mail. It is due {when}."
        assert c["acts"] == [
            {"id": "done", "label": "Done", "class": "A", "way": "tap", "act": "done",
             "act_ref": "t-prom"},
            {"id": "open-thread", "label": "Open thread", "class": "A", "way": "open",
             "href": "/email?email=m-p&account=a-mine"}]
        assert c["urgent_at"] == due.isoformat() == c["facts"]["due_at"]
        assert c["href"] == "/projects?task=t-prom"
        # BASE, and the promise term of the weight. Due in 2 days: no urgency.
        assert c["score"] == feed_rank.BASE["promise_due"] + 10 == 52
        # ⚠️ The mail id rides in the link only, never in a state fact.
        assert "message_id" not in c["facts"]

    def test_no_answer(self, answer):
        c = card(answer, "email:a-mine:th-wait")
        assert (c["kind"], c["app"], c["group"], c["why_key"], c["source"]) == (
            "no_answer", "email", "needs_you", "no_answer", "email_followups")
        tz = _tz(self.mail.f)
        sent = self.mail.now - timedelta(days=6)
        when = feed._when(sent.isoformat(), tz, self.mail.now.astimezone(tz).date())
        assert c["why"] == f"You wrote {when}. dana has not answered."
        assert c["acts"] == [{"id": "follow-up", "label": "Follow up", "class": "A",
                              "way": "open", "href": "/email?email=m-wait&account=a-mine"}]
        assert c["facts"]["waiting_since"] == sent.isoformat()
        assert c["facts"]["message_id"] == "m-wait"
        assert c["score"] == feed_rank.BASE["no_answer"] == 35
        assert c["urgent_at"] is None
        assert c["entity"] == {"type": "thread", "id": "th-wait", "label": "Quote for 40 units"}

    def test_each_card_has_its_label_and_a_done_closes_a_promise(self, answer):
        labels = _labels()
        assert (labels["promise_due"], labels["no_answer"]) == ("You promised", "No answer yet")
        assert "promise_due" in _closes_done() and "no_answer" not in _closes_done()

    def test_no_separate_mailbox_and_no_mailbox_of_another_member(self, answer):
        assert {a for _n, a, _k in self.mail.calls} == {"a-mine"}
        text = str(answer["cards"])
        assert "Board papers" not in text and "Their secret" not in text

    def test_a_needs_reply_card_keeps_its_bytes(self, answer):
        for c in answer["cards"]:
            if c["kind"] not in MAIL_KINDS:
                assert "source" not in c, c["signal_id"]


class TestTheReadsAndTheirBounds:
    def test_the_window_and_the_horizon(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        run_feed(member(*FULL))
        from gateway.routes.email.automation.followups import _FOLLOW_UP_MAX_AGE_DAYS

        assert [n for n, _a, _k in mail.calls] == ["awaiting_threads", "promise_tasks"]
        _n, _a, wait = mail.calls[0]
        # `before` None: the wrapper reads the mailbox's own cutoff.
        assert wait == {"limit": shell.PER_APP + 1, "before": None,
                        "after": mail.now - timedelta(days=_FOLLOW_UP_MAX_AGE_DAYS)}
        assert _FOLLOW_UP_MAX_AGE_DAYS == 30
        _n, _a, prom = mail.calls[1]
        assert prom == {"horizon_days": 3, "limit": shell.PER_APP + 1, "now": mail.now}

    def test_a_member_without_projects_gets_no_promise_read(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        answer = run_feed(member("email", "tasks"))
        assert answer["sources"]["email_followups"] == "ok"
        assert [n for n, _a, _k in mail.calls] == ["awaiting_threads"]
        kinds = {c["kind"] for c in answer["cards"]}
        assert "no_answer" in kinds and "promise_due" not in kinds

    def test_a_thread_with_no_message_gives_no_card(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        mail.threads["a-mine"] = [_thread("th-none", None, "Nothing to open",
                                          mail.now - timedelta(days=6))]
        answer = run_feed(member(*FULL))
        assert answer["sources"]["email_followups"] == "ok"
        assert "email:a-mine:th-none" not in [c["signal_id"] for c in answer["cards"]]

    def test_no_answer_with_no_name(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        mail.threads["a-mine"] = [_thread("th-anon", "m-anon", "Hello",
                                          mail.now - timedelta(days=6), who="")]
        c = card(run_feed(member(*FULL)), "email:a-mine:th-anon")
        assert c["why"].endswith(" They have not answered.")
        assert c["facts"]["who"] is None

    def test_a_promise_due_within_two_hours_is_now(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        mail.promises["a-mine"] = [_promise("t-hour", "Call back", mail.now + timedelta(hours=1))]
        c = card(run_feed(member(*FULL)), "tasks:t-hour")
        assert c["group"] == "now"
        assert c["score"] == 42 + 10 + feed_rank.URGENT_SOON

    def test_15_signals_at_most_by_base_then_age(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        now = mail.now
        mail.threads["a-mine"] = [_thread(f"th-{n:02d}", f"m-{n:02d}", f"T {n}",
                                          now - timedelta(days=4 + n)) for n in range(14)]
        mail.promises["a-mine"] = [_promise("t-p1", "P1", now + timedelta(days=1)),
                                   _promise("t-p2", "P2", now + timedelta(days=2))]
        found = _bound(lambda: feed._email_followups(member(*FULL)))
        signals = found.signals()
        assert found.capped is True
        assert len(signals) == shell.PER_APP
        assert [s.kind for s in signals[:2]] == ["promise_due", "promise_due"]
        # The oldest thread first, and the newest one is cut.
        waits = [s.signal_id for s in signals if s.kind == "no_answer"]
        assert waits[0] == "email:a-mine:th-13" and "email:a-mine:th-00" not in waits


# ── done-when 5: a failed read is `failed`, never an empty `ok` ──────────────


class TestTheFailures:
    def test_a_database_error_in_the_promise_read_fails_the_source(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.fail[("promise_tasks", "a-mine")] = DB_DOWN
        answer = run_feed(member(*FULL))
        assert answer["sources"]["email_followups"] == "failed"
        assert not {c["kind"] for c in answer["cards"]} & set(MAIL_KINDS)
        # The needs source of the same app never fails with it.
        assert answer["sources"]["email"] == "ok"

    def test_a_database_error_in_the_thread_read_fails_the_source(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.fail[("awaiting_threads", "a-mine")] = DB_DOWN
        assert run_feed(member(*FULL))["sources"]["email_followups"] == "failed"

    def test_one_failed_mailbox_costs_only_its_own_rows(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.f.accounts[ME] = [*mail.f.accounts[ME], {"id": "a-two", "in_all_inboxes": True}]
        mail.threads["a-two"] = [_thread("th-two", "m-two", "Two", mail.now - timedelta(days=5))]
        mail.fail[("promise_tasks", "a-mine")] = DB_DOWN
        answer = run_feed(member(*FULL))
        assert answer["sources"]["email_followups"] == "ok"
        assert "email_followups" in answer["capped"]
        ids = [c["signal_id"] for c in answer["cards"]]
        assert "email:a-two:th-two" in ids
        assert "email:a-mine:th-wait" not in ids and "tasks:t-prom" not in ids

    def test_a_slow_mailbox_fails_alone(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.f.accounts[ME] = [*mail.f.accounts[ME], {"id": "a-two", "in_all_inboxes": True}]
        mail.threads["a-two"] = [_thread("th-two", "m-two", "Two", mail.now - timedelta(days=5))]
        mail.slow.add("a-mine")
        monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 0.2)
        answer = run_feed(member(*FULL))
        assert answer["sources"]["email_followups"] == "ok"
        assert "email:a-two:th-two" in [c["signal_id"] for c in answer["cards"]]

    def test_a_mailbox_that_is_not_this_tenants_is_skipped(self, mail, monkeypatch):
        """Read past RLS, the mailbox list could hold the member's mailbox
        of another organization. The owner check names the tenant, so it
        answers 404, and the mailbox is no failure."""
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.fail[("awaiting_threads", "a-mine")] = HTTPException(status_code=404)
        answer = run_feed(member(*FULL))
        assert answer["sources"]["email_followups"] == "ok"
        assert "email_followups" not in answer["capped"]
        assert not {c["kind"] for c in answer["cards"]} & set(MAIL_KINDS)

    def test_another_refusal_fails_the_mailbox(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.fail[("awaiting_threads", "a-mine")] = HTTPException(status_code=403)
        assert run_feed(member(*FULL))["sources"]["email_followups"] == "failed"

    def test_a_failed_email_followups_never_touches_the_needs_rows(self, mail, monkeypatch):
        """The carry keys on the card's source (§5.9 item 1). The client half
        is ``feed.test.ts`` (d1d)."""
        _list(monkeypatch, "email_followups")
        _seed(mail)
        ok = run_feed(member(*FULL))
        mail.fail[("promise_tasks", "a-mine")] = DB_DOWN
        failed = run_feed(member(*FULL))
        reply = [c for c in ok["cards"] if c["kind"] == "needs_reply"]
        assert reply and all("source" not in c for c in reply)
        assert [c for c in failed["cards"] if c["kind"] == "needs_reply"] == reply


# ── done-when 9: the weight of a promise ────────────────────────────────────


def _signal(kind: str, *, app: str, facts: dict, sid: str = "tasks:t-1",
            urgent_at=None, acts=()) -> shell.Signal:
    return shell.Signal(signal_id=sid, app=app, kind=kind,
                        entity=shell.task_entity("t-1", "One"), title="One",
                        facts=facts, href="/projects?task=t-1", acts=tuple(acts),
                        urgent_at=urgent_at)


class TestThePromiseWeight:
    NOW = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))

    def test_the_weight_term(self):
        for cell in feed_rank.LEVEL_BY_CELL:
            facts = {"priority": cell}
            assert feed_rank.weight(facts, ["promise_due"]) == feed_rank.weight(facts) + 10
            assert feed_rank.weight(facts, ["overdue", "no_answer"]) == feed_rank.weight(facts)
        assert feed_rank.weight({}, ["promise_due"]) == 10
        assert feed_rank.PROMISE_WEIGHT == 10

    def test_the_decide_level_is_the_task_level(self):
        """``feed_decide.old_level`` asks for the D78 level only, so a
        promise never lifts it past 4."""
        c = {"facts": {"priority": "critical"}, "kind": "promise_due"}
        assert feed_decide.old_level(c) == 4

    def test_a_folded_promise_weighs_10_more_than_the_same_card(self):
        tz, today = ZoneInfo("UTC"), self.NOW.date()
        due = "2026-10-11T09:00:00+00:00"
        overdue = _signal("overdue", app="tasks", urgent_at=due,
                          facts={"due_at": due, "days_late": 1, "priority": "urgent"})
        promise = _signal("promise_due", app="email", urgent_at=due,
                          facts={"due_at": due, "priority": "urgent"})
        alone = feed.build_cards([overdue], self.NOW, tz, today)[0]
        both = feed.build_cards([overdue, promise], self.NOW, tz, today)[0]
        assert both["folded"] == ["promise_due"]
        assert both["score"] == alone["score"] + 10
        assert both["why"].endswith(" You promised this by mail.")

    def test_a_promise_card_weighs_10_more_than_with_no_promise(self):
        tz, today = ZoneInfo("UTC"), self.NOW.date()
        facts = {"due_at": "2026-10-15T09:00:00+00:00", "priority": "important"}
        promise = feed.build_cards([_signal("promise_due", app="email", facts=facts)],
                                   self.NOW, tz, today)[0]
        assert promise["score"] == 42 + feed_rank.weight(facts) + 10

    def test_a_card_with_no_promise_keeps_its_score(self):
        tz, today = ZoneInfo("UTC"), self.NOW.date()
        for kind, facts in (("due_today", {"due_at": "2026-10-12T20:00:00+00:00",
                                           "priority": "important"}),
                            ("no_answer", {"waiting_since": "2026-10-06T09:00:00+00:00"})):
            c = feed.build_cards([_signal(kind, app="tasks", facts=facts)], self.NOW, tz, today)[0]
            assert c["score"] == feed_rank.score(kind, facts, None, self.NOW)

    def test_a_folded_promise_merges_its_acts(self):
        tz, today = ZoneInfo("UTC"), self.NOW.date()
        overdue = _signal("overdue", app="tasks", facts={"due_at": None},
                          acts=[shell.tap("done", "Done", "done", "t-1")])
        promise = feed._promise_signal(_promise("t-1", "One", self.NOW + timedelta(days=1)),
                                       "a-1")
        c = feed.build_cards([overdue, promise], self.NOW, tz, today)[0]
        assert [a["id"] for a in c["acts"]] == ["done", "open-thread"]


class TestTheSourceOfACard:
    def test_the_card_names_email_followups_and_a_needs_card_names_nothing(self):
        now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("UTC"))
        tz, today = ZoneInfo("UTC"), now.date()
        wait = feed._no_answer_signal(
            _thread("th-1", "m-1", "S", now - timedelta(days=6)), "a-1")
        reply = shell._thread_signal({"id": "email:a-1:th-2", "title": "R",
                                      "detail": "From dana", "at": None,
                                      "href": "/email?email=m-2&account=a-1"}, "m-2")
        tagged = [dataclasses.replace(wait, source="email_followups"),
                  dataclasses.replace(reply, source="email")]
        cards = {c["signal_id"]: c for c in feed.build_cards(tagged, now, tz, today)}
        assert cards["email:a-1:th-1"]["source"] == "email_followups"
        assert "source" not in cards["email:a-1:th-2"]


# ── done-when 10: 1 session, plus 2 for each mailbox ────────────────────────


class TestTheSessions:
    def test_one_plus_two_for_each_mailbox(self, mail, monkeypatch):
        _list(monkeypatch, "email_followups")
        _seed(mail)
        mail.f.accounts[ME] = [*mail.f.accounts[ME], {"id": "a-two", "in_all_inboxes": True}]
        _bound(lambda: feed._email_followups(member(*FULL)))
        mailboxes = [n for n, _u, _k in mail.f.calls if n == "feed_mailboxes"]
        assert len(mailboxes) == 1
        assert [n for n, _a, _k in mail.calls] == [
            "awaiting_threads", "promise_tasks", "awaiting_threads", "promise_tasks"]
        # 2 pooled mailboxes, so 1 + 2 * 2 at most.
        assert len(mailboxes) + len(mail.calls) <= 1 + 2 * 2


# ── the wrappers (routes/email/digest.py) ───────────────────────────────────


class _Recorder:
    """A session that records each statement. ``answers`` maps a part of
    the SQL to what ``fetchone`` and ``fetchall`` give."""

    def __init__(self, *, owner: bool = True, settings: object = None, threads=(),
                 fail: str | None = None) -> None:
        self.sql: list[tuple[str, dict]] = []
        self.owner = owner
        self.settings = settings
        self.threads = list(threads)
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.sql.append((sql, dict(params or {})))
        if self.fail and self.fail in sql:
            raise DB_DOWN
        r = MagicMock()
        if sql.startswith("SELECT 1 FROM email_accounts"):
            r.fetchone.return_value = (1,) if self.owner else None
        elif sql.startswith("SELECT user_id FROM email_accounts"):
            r.fetchone.return_value = SimpleNamespace(user_id=ME)
        elif "FROM email_assistant_settings" in sql:
            r.fetchone.return_value = self.settings
        elif sql.startswith("SELECT o.id::text"):
            r.fetchall.return_value = []
        elif "FROM email_thread_status ts" in sql:
            r.fetchall.return_value = self.threads
        else:
            r.fetchall.return_value = []
            r.fetchone.return_value = None
        return r


def _wrapped(monkeypatch, session: _Recorder) -> _Recorder:
    monkeypatch.setattr(digest, "_tenant_session", lambda: session)
    return session


TENANT = "organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"


class TestAwaitingThreads:
    NOW = datetime(2026, 10, 15, 9, 0, tzinfo=ZoneInfo("UTC"))

    def test_the_owner_check_runs_first_names_the_tenant_and_refuses(self, monkeypatch):
        s = _wrapped(monkeypatch, _Recorder(owner=False))
        with pytest.raises(HTTPException) as err:
            asyncio.run(digest.awaiting_threads(member("email"), "acc-x", 16, after=self.NOW))
        assert err.value.status_code == 404
        [(sql, params)] = s.sql
        assert sql.startswith("SELECT 1 FROM email_accounts WHERE id = :id AND user_id = :uid")
        assert TENANT in sql and params == {"id": "acc-x", "uid": ME}

    def test_the_bounds_are_in_sql_with_the_tenant(self, monkeypatch):
        s = _wrapped(monkeypatch, _Recorder())
        before = self.NOW - timedelta(days=3)
        asyncio.run(digest.awaiting_threads(member("email"), "acc-1", 16,
                                            after=self.NOW - timedelta(days=30),
                                            before=before))
        [(sql, params)] = [x for x in s.sql if "FROM email_thread_status ts" in x[0]]
        assert "ts.status = 'AWAITING'" in sql
        assert "AND ts.last_message_at < :before AND ts.last_message_at > :after" in sql
        assert f"AND ts.{TENANT}" in sql
        assert "ORDER BY ts.last_message_at ASC LIMIT :lim" in sql
        assert params == {"aid": "acc-1", "lim": 16, "before": before,
                          "after": self.NOW - timedelta(days=30)}
        # Given a `before`, no settings read.
        assert not [x for x in s.sql if "email_assistant_settings" in x[0]]

    @pytest.mark.parametrize(("stored", "days"), [
        (SimpleNamespace(follow_up_awaiting_days=5), 5.0),
        (SimpleNamespace(follow_up_awaiting_days=0), 3),
        (SimpleNamespace(follow_up_awaiting_days=None), 3),
        (None, 3),
    ])
    def test_the_cutoff_is_the_mailbox_rule(self, monkeypatch, stored, days):
        from gateway.routes.email.automation import replyzero

        asked: list[float] = []

        def _cutoff(n):
            asked.append(n)
            return self.NOW - timedelta(days=n)

        monkeypatch.setattr(replyzero, "_business_days_cutoff", _cutoff)
        s = _wrapped(monkeypatch, _Recorder(settings=stored))
        asyncio.run(digest.awaiting_threads(member("email"), "acc-1", 16,
                                            after=self.NOW - timedelta(days=30)))
        assert asked == [days]
        [(sql, _p)] = [x for x in s.sql if "email_assistant_settings" in x[0]]
        assert sql.startswith("SELECT follow_up_awaiting_days FROM email_assistant_settings")
        assert TENANT in sql
        [(_q, params)] = [x for x in s.sql if "FROM email_thread_status ts" in x[0]]
        assert params["before"] == self.NOW - timedelta(days=days)

    def test_an_error_raises(self, monkeypatch):
        _wrapped(monkeypatch, _Recorder(fail="FROM email_thread_status ts"))
        with pytest.raises(OperationalError):
            asyncio.run(digest.awaiting_threads(member("email"), "acc-1", 16,
                                                after=self.NOW, before=self.NOW))


class _CommitmentSeam:
    def __init__(self, items, error: BaseException | None = None):
        self.items = items
        self.error = error
        self.calls: list[tuple] = []

    async def items_by_origin(self, db, uid, key, value, **kw):
        self.calls.append((uid, key, value, kw))
        if self.error is not None:
            raise self.error
        return [it for it in self.items
                if not kw.get("commitment") or it.origin.get("commitment") is True]


def _item(tid, title, due=None, *, commitment=True, thread="th-1"):
    origin = {"kind": "email", "account_id": "acc-1", "thread_id": thread}
    if commitment:
        origin["commitment"] = True
    return SimpleNamespace(id=tid, title=title, due_at=due, origin=origin,
                           importance=None, leveraged=False)


class TestPromiseTasks:
    NOW = datetime(2026, 10, 15, 9, 0, tzinfo=ZoneInfo("UTC"))

    def _seam(self, monkeypatch, items, error=None):
        from gateway.routes.tasks import item_source as seam

        src = _CommitmentSeam(items, error)
        monkeypatch.setattr(seam, "item_source", lambda: src)
        return src

    def _run(self, **kw):
        return asyncio.run(digest.promise_tasks(member("email", "projects"), "acc-1",
                                                horizon_days=3, limit=16, now=self.NOW, **kw))

    def test_only_a_commitment_and_only_a_dated_one_in_the_horizon(self, monkeypatch):
        src = self._seam(monkeypatch, [
            _item("p-due", "Send the drawings", self.NOW + timedelta(days=2)),
            _item("p-late", "Call back", self.NOW - timedelta(days=1)),
            _item("p-far", "Later", self.NOW + timedelta(days=10)),
            _item("p-none", "Undated"),
            _item("c-plain", "Captured, no promise", self.NOW + timedelta(days=1),
                  commitment=False),
        ])
        s = _wrapped(monkeypatch, _Recorder())
        out = self._run()
        assert src.calls == [(ME, "account_id", "acc-1", {"commitment": True})]
        assert [r["task_id"] for r in out] == ["p-late", "p-due"]
        assert out[0]["overdue"] is True and out[1]["overdue"] is False
        assert out[1]["due_at"] == (self.NOW + timedelta(days=2)).isoformat()
        # The owner check first, with the tenant, then the tenant on each
        # statement of this module.
        assert s.sql[0][0].startswith("SELECT 1 FROM email_accounts") and TENANT in s.sql[0][0]
        for sql, _p in s.sql:
            assert "organization_id" in sql, sql

    def test_a_database_error_raises(self, monkeypatch):
        self._seam(monkeypatch, [], error=DB_DOWN)
        _wrapped(monkeypatch, _Recorder())
        with pytest.raises(OperationalError):
            self._run()

    def test_another_members_mailbox_is_404_before_any_read(self, monkeypatch):
        src = self._seam(monkeypatch, [_item("p-due", "x", self.NOW)])
        s = _wrapped(monkeypatch, _Recorder(owner=False))
        with pytest.raises(HTTPException) as err:
            self._run()
        assert err.value.status_code == 404
        assert len(s.sql) == 1 and src.calls == []


#: The fence lives here, beside its kind: the digest keeps its own wrapper.
def test_the_digest_wrapper_still_swallows_what_the_core_raises():
    db = AsyncMock()
    db.execute.side_effect = DB_DOWN
    assert asyncio.run(digest._digest_commitments(db, "acc-1")) == []


def test_a_feed_member_other_than_me_reads_only_their_own(fakes, monkeypatch):  # noqa: F811
    """The fakes answer by the member they are handed, as the real reads do."""
    r = MailReads(fakes)
    monkeypatch.setattr(digest, "awaiting_threads", r.awaiting_threads)
    monkeypatch.setattr(digest, "promise_tasks", r.promise_tasks)
    _list(monkeypatch, "email_followups")
    _seed(r)
    answer = run_feed(member(*FULL, email=OTHER))
    assert {a for _n, a, _k in r.calls} == {"a-theirs"}
    assert "email:a-theirs:th-theirs" in [c["signal_id"] for c in answer["cards"]]
    assert "Quote for 40 units" not in str(answer["cards"])
