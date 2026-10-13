"""WS-58 MD-2c, hermetic: Handled and the honest summary (``my_day_feed.md``
§6.7, §14.1a and the MD-2c ticket of §16, done-when 1).

The R8 half is ``test_feed_handled_r8.py``. This file fences what the server
half adds, and each case is written so a wrong build fails it:

* **The shape.** ``handled`` is a list of ``{app, title, href, by, at}``, the
  newest first, and ``by`` is ``{kind, name}``. ``cards`` and ``groups`` never
  hold it, and it does not count against ``limit``. ``needs`` does not change.
* **The ``by`` table.** One case for each row: ``you``, ``person``,
  ``agent``, ``system`` (four actor forms) and ``rules``.
* **One entry for each entity**, the newest, and **15 for each source**.
* **The budget.** The reads run at the same time as the state join, inside
  its 1.5 seconds. A read past the limit counts as failed.
* **The claim flag** (``handled_ok``). A failed read makes it false. A source
  that reads ``absent`` does not.
* **The gates and the day.** A member without the app gets no read. Only an
  org admin gets approvals. Each read starts at ``Day.day_start`` from
  ``state._hours``, the state join's own function.
* **The reads stay in step** with the app functions they call.

The fakes are ``test_shell_feed.FeedFakes``: each answers by the member it is
handed, and by the tenant the request bound.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from gateway.routes.shell import handled, state
from gateway.routes.shell import needs as shell

# The feed suite's fakes and helpers, shared rather than copied. The fixture
# is used by name, so the import is load-bearing.
from tests.unit.test_shell_feed import (  # noqa: F401
    FULL,
    _bound,
    admin,
    fakes,
    member,
    run_feed,
    run_needs,
)
from tests.unit.test_shell_needs import ME, NOW, ORG, ORG_OTHER, OTHER

ENTRY_KEYS = {"app", "title", "href", "by", "at"}
BY_KEYS = {"kind", "name"}


def _day_start(f, start_hour: int = 7) -> datetime:
    """The start of ME's day: what ``state._hours`` reads from the fakes."""
    return state.Day(ZoneInfo(f.zone), start_hour).day_start(NOW)


def _done(task_id: str, title: str, by: str, at: datetime) -> dict:
    return {"id": task_id, "title": title, "completed_at": at.isoformat(),
            "closed_by": by}


def _rule(rid: str, account: str, message: str | None, subject: str | None,
          rule: str, at: datetime) -> dict:
    return {"id": rid, "account_id": account, "message_id": message,
            "subject": subject, "rule_name": rule, "created_at": at.isoformat()}


def _decided(aid: str, action: str, by: str, at: datetime, status: str) -> dict:
    return {"id": aid, "actor": "agent:email-assistant", "action": action,
            "target": "lead:z-1", "payload": {}, "status": status,
            "reviewed_by": by, "reviewed_at": at}


@pytest.fixture()
def seeded(fakes):  # noqa: F811
    """One closed task for each row of the ``by`` table, one closed before
    the day start, and the other member's closed task. Three rule rows on
    the member's pooled mailbox (two on one message), one on the separate
    mailbox and one on the other member's. Two decided actions in this org,
    and one in the other."""
    f = fakes
    start = _day_start(f)
    f.completed = {
        ME: [_done("t-mine", "File the VAT return", ME.upper(), NOW - timedelta(minutes=10)),
             _done("t-ravi", "Ship the extruder", "ravi@customer.example",
                   NOW - timedelta(minutes=20)),
             _done("t-agent", "Wire the sensor", "agent:builder",
                   NOW - timedelta(minutes=30)),
             _done("t-sync", "Synced task", "system:sync", NOW - timedelta(minutes=40)),
             _done("t-flow", "Workflow task", "system:workflow:wf-1",
                   NOW - timedelta(minutes=50)),
             _done("t-before", "Closed yesterday", ME, start - timedelta(minutes=1))],
        OTHER: [_done("t-theirs", "Their closed task", OTHER, NOW - timedelta(minutes=5))],
    }
    f.applied = [
        _rule("r-new", "a-mine", "m-1", "Invoice March", "File invoices",
              NOW - timedelta(minutes=3)),
        _rule("r-old", "a-mine", "m-1", "Invoice March (old)", "Label invoices",
              NOW - timedelta(minutes=33)),
        _rule("r-bare", "a-mine", None, None, "Archive newsletters",
              NOW - timedelta(minutes=13)),
        _rule("r-sep", "a-separate", "m-sep", "Board papers", "File board",
              NOW - timedelta(minutes=4)),
        _rule("r-theirs", "a-theirs", "m-theirs", "Their secret", "Theirs",
              NOW - timedelta(minutes=2)),
    ]
    f.decided = {
        ORG: [_decided("pa-yes", "crm.zoho_update", ME, NOW - timedelta(minutes=7), "applied"),
              _decided("pa-no", "whatsapp.broadcast", "operator",
                       NOW - timedelta(minutes=27), "rejected")],
        ORG_OTHER: [_decided("pa-theirs", "crm.zoho_delete", "boss@other.example",
                             NOW - timedelta(minutes=1), "approved")],
    }
    return f


def _by_title(answer: dict) -> dict[str, dict]:
    return {e["title"]: e for e in answer["handled"]}


def _names(fakes_) -> list[str]:
    return [n for n, _, _ in fakes_.calls]


# ── The shape (§14.1a) ───────────────────────────────────────────────────────


class TestTheShape:
    def test_each_entry_is_app_title_href_by_at_and_the_newest_first(self, seeded):
        answer = run_feed(admin(*FULL, "approvals"))
        assert answer["handled_ok"] is True
        entries = answer["handled"]
        assert entries, "non-vacuity: the seed gives entries"
        for entry in entries:
            assert set(entry) == ENTRY_KEYS
            assert set(entry["by"]) == BY_KEYS
        stamps = [datetime.fromisoformat(e["at"]) for e in entries]
        assert stamps == sorted(stamps, reverse=True)
        assert [e["title"] for e in entries] == [
            "Invoice March", "Change a record in Zoho CRM", "File the VAT return",
            "Archive newsletters", "Ship the extruder", "Send a WhatsApp broadcast",
            "Wire the sensor", "Synced task", "Workflow task",
        ]

    def test_each_entry_opens_its_entity(self, seeded):
        rows = _by_title(run_feed(admin(*FULL, "approvals")))
        assert rows["File the VAT return"]["href"] == "/projects?task=t-mine"
        assert rows["File the VAT return"]["app"] == "tasks"
        assert rows["Invoice March"]["href"] == "/email?email=m-1&account=a-mine"
        assert rows["Invoice March"]["app"] == "email"
        # A rule row with no message opens the email app (no history link).
        assert rows["Archive newsletters"]["href"] == "/email"
        assert rows["Change a record in Zoho CRM"]["href"] == "/approvals"
        assert rows["Change a record in Zoho CRM"]["app"] == "approvals"

    def test_handled_is_never_a_card_and_never_a_group(self, seeded):
        user = admin(*FULL, "approvals")
        answer = run_feed(user)
        assert answer["handled"]
        assert "handled" not in {g["id"] for g in answer["groups"]}
        assert "handled" not in {c["group"] for c in answer["cards"]}
        ids = {c["signal_id"] for c in answer["cards"]}
        assert not ids & {"tasks:t-mine", "tasks:t-ravi", "approvals:pa-yes"}
        # The cards are the cards of a feed with no Handled row at all.
        seeded.completed, seeded.applied, seeded.decided = {}, [], {}
        bare = run_feed(user)
        assert bare["handled"] == [] and bare["handled_ok"] is True
        assert bare["cards"] == answer["cards"] and bare["groups"] == answer["groups"]

    def test_the_list_does_not_count_against_limit(self, seeded):
        answer = run_feed(admin(*FULL, "approvals"), limit=1)
        assert len(answer["cards"]) == 1
        assert len(answer["handled"]) == 9

    def test_needs_does_not_change(self, seeded):
        # TestOneRegistry's equality holds with Handled rows present.
        user = admin(*FULL, "approvals")
        assert run_feed(user)["needs"] == run_needs(user)


# ── The `by` table ───────────────────────────────────────────────────────────


class TestTheByTable:
    @pytest.mark.parametrize(("actor", "kind", "name"), [
        (ME, "you", None),
        (ME.upper(), "you", None),
        ("ravi@customer.example", "person", "ravi"),
        ("agent:builder", "agent", "builder"),
        ("system:sync", "system", None),
        ("system:workflow:wf-1", "system", None),
        ("system", "system", None),
        ("operator", "system", None),
        (None, "system", None),
    ])
    def test_each_actor_form(self, actor, kind, name):
        assert shell.closed_by(actor, ME) == {"kind": kind, "name": name}

    def test_each_row_reaches_the_answer(self, seeded):
        rows = _by_title(run_feed(admin(*FULL, "approvals")))
        assert rows["File the VAT return"]["by"] == {"kind": "you", "name": None}
        assert rows["Ship the extruder"]["by"] == {"kind": "person", "name": "ravi"}
        assert rows["Wire the sensor"]["by"] == {"kind": "agent", "name": "builder"}
        assert rows["Synced task"]["by"] == {"kind": "system", "name": None}
        assert rows["Workflow task"]["by"] == {"kind": "system", "name": None}
        assert rows["Invoice March"]["by"] == {"kind": "rules", "name": None}
        assert rows["Archive newsletters"]["by"] == {"kind": "rules", "name": None}
        assert rows["Change a record in Zoho CRM"]["by"] == {"kind": "you", "name": None}
        assert rows["Send a WhatsApp broadcast"]["by"] == {"kind": "system", "name": None}


# ── One entry for each entity, 15 for each source ───────────────────────────


class TestOneEntryPerEntity:
    def test_two_rules_on_one_message_give_one_entry_the_newest(self, seeded):
        entries = run_feed(member(*FULL))["handled"]
        mail = [e for e in entries if e["href"] == "/email?email=m-1&account=a-mine"]
        assert len(mail) == 1
        assert mail[0]["title"] == "Invoice March"  # r-new, not r-old

    def test_one_task_closed_twice_gives_one_entry_the_newest(self, seeded):
        seeded.completed[ME].append(_done("t-mine", "File the VAT return (again)", OTHER,
                                          NOW - timedelta(minutes=1)))
        entries = [e for e in run_feed(member(*FULL))["handled"]
                   if e["href"] == "/projects?task=t-mine"]
        assert len(entries) == 1
        assert entries[0]["title"] == "File the VAT return (again)"


class TestTheCap:
    def test_fifteen_for_each_source(self, seeded):
        seeded.completed[ME] = [_done(f"t-{n:02d}", f"Task {n}", ME,
                                      NOW - timedelta(minutes=n + 1)) for n in range(20)]
        seeded.applied = [_rule(f"r-{n:02d}", "a-mine", f"m-{n:02d}", f"Mail {n}", "R",
                                NOW - timedelta(minutes=n + 1)) for n in range(20)]
        entries = run_feed(member(*FULL))["handled"]
        tasks = [e for e in entries if e["app"] == "tasks"]
        mail = [e for e in entries if e["app"] == "email"]
        assert len(tasks) == shell.PER_APP == 15
        assert len(mail) == 15
        # The newest fifteen of each.
        assert [e["title"] for e in tasks] == [f"Task {n}" for n in range(15)]
        assert len(entries) == 30

    def test_each_read_asks_for_fifteen(self, seeded):
        run_feed(admin(*FULL, "approvals"))
        asked = {n: kw.get("limit") for n, _, kw in seeded.calls
                 if n in ("my_completed_since", "applied_rules_since", "read_decided")}
        assert asked == {"my_completed_since": 15, "applied_rules_since": 15,
                         "read_decided": 15}

    @pytest.mark.parametrize("app", ["tasks", "email", "approvals"])
    def test_a_read_that_gives_more_is_cut_to_fifteen(self, seeded, monkeypatch, app):
        # The ONE cut, in `handled_run`. Each read asks for 15 rows, and the
        # cut holds when a read gives 40.
        import action_broker
        from gateway.routes.email.automation import runner
        from gateway.routes.projects import personal

        stamps = [NOW - timedelta(minutes=n + 1) for n in range(40)]

        async def _tasks(user, since, *, limit):
            return {"rows": [_done(f"t-{n:02d}", f"Task {n}", ME, at)
                             for n, at in enumerate(stamps)]}

        async def _rules(user, account_ids, since, *, limit):
            return [_rule(f"r-{n:02d}", "a-mine", f"m-{n:02d}", f"Mail {n}", "R", at)
                    for n, at in enumerate(stamps)]

        def _decisions(since, limit=15):
            return [_decided(f"pa-{n:02d}", "crm.zoho_update", ME, at, "applied")
                    for n, at in enumerate(stamps)]

        monkeypatch.setattr(personal, "my_completed_since", _tasks)
        monkeypatch.setattr(runner, "applied_rules_since", _rules)
        monkeypatch.setattr(action_broker, "read_decided", _decisions)
        entries = [e for e in run_feed(admin(*FULL, "approvals"))["handled"]
                   if e["app"] == app]
        assert len(entries) == 15
        assert entries[0]["at"] == stamps[0].isoformat()  # the newest fifteen


# ── The day start, the gates and the tenant ─────────────────────────────────


class TestTheDayAndTheGates:
    def test_each_read_starts_at_the_members_day_start(self, seeded):
        seeded.day_start[ME] = 5
        run_feed(admin(*FULL, "approvals"))
        since = {n: kw["since"] for n, _, kw in seeded.calls
                 if n in ("my_completed_since", "applied_rules_since", "read_decided")}
        assert len(since) == 3
        assert set(since.values()) == {_day_start(seeded, 5)}

    def test_a_row_before_the_day_start_is_not_handled(self, seeded):
        titles = {e["title"] for e in run_feed(member(*FULL))["handled"]}
        assert "File the VAT return" in titles  # non-vacuity
        assert "Closed yesterday" not in titles

    def test_a_member_with_no_calendar_pane_reads_the_day_from_the_lens(self, seeded):
        # §6.8 item 8: the zone from `stored_zone`, the column default hour.
        run_feed(member("projects", "email"))
        since = [kw["since"] for n, _, kw in seeded.calls if n == "my_completed_since"]
        assert since == [state.Day(ZoneInfo(seeded.zone)).day_start(NOW)]
        assert "get_user_settings" not in _names(seeded)

    def test_another_members_rows_never_arrive(self, seeded):
        got = " ".join(e["title"] for e in run_feed(admin(*FULL, "approvals"))["handled"])
        assert "Their" not in got and "Board papers" not in got
        assert "Delete a record" not in got
        theirs = run_feed(member(*FULL, email=OTHER))["handled"]
        assert [e["title"] for e in theirs] == ["Their secret", "Their closed task"]

    def test_the_email_read_names_the_pooled_mailboxes_only(self, seeded):
        run_feed(member(*FULL))
        asked = [kw["account_ids"] for n, _, kw in seeded.calls if n == "applied_rules_since"]
        assert asked == [["a-mine"]]

    def test_a_member_without_projects_gets_no_task_read(self, seeded):
        answer = run_feed(member("email", "tasks"))
        assert "my_completed_since" not in _names(seeded)
        assert {e["app"] for e in answer["handled"]} == {"email"}
        assert answer["handled_ok"] is True

    def test_a_member_without_email_gets_no_rule_read(self, seeded):
        answer = run_feed(member("projects", "tasks"))
        assert "applied_rules_since" not in _names(seeded)
        assert {e["app"] for e in answer["handled"]} == {"tasks"}

    def test_a_member_who_is_not_an_admin_gets_no_approval_read(self, seeded):
        answer = run_feed(member(*FULL, "approvals"))
        assert "read_decided" not in _names(seeded)
        assert "approvals" not in {e["app"] for e in answer["handled"]}
        assert answer["handled_ok"] is True

    def test_an_admin_reads_the_bound_orgs_decisions_only(self, seeded):
        answer = run_feed(admin(*FULL, "approvals"))
        orgs = [kw["org"] for n, _, kw in seeded.calls if n == "read_decided"]
        assert orgs == [ORG]
        titles = {e["title"] for e in answer["handled"] if e["app"] == "approvals"}
        assert titles == {"Change a record in Zoho CRM", "Send a WhatsApp broadcast"}

    def test_a_member_with_no_handled_source_gets_an_empty_list(self, seeded):
        answer = run_feed(member("tasks"))
        assert (answer["handled"], answer["handled_ok"]) == ([], True)
        assert not {"my_completed_since", "applied_rules_since", "read_decided"} & set(
            _names(seeded))


# ── The budget ───────────────────────────────────────────────────────────────


class TestTheBudget:
    def test_the_reads_run_at_the_same_time_as_the_state_join(self, seeded, monkeypatch):
        reading: list[bool] = []
        seen: list[bool] = []
        plain_read = seeded.read_state
        plain_done = seeded.my_completed_since

        async def _slow_state(db, member_, ids, *, lock=False):
            reading.append(True)
            try:
                await asyncio.sleep(0.4)
                return await plain_read(db, member_, ids, lock=lock)
            finally:
                reading.pop()

        async def _done_read(user, since, *, limit):
            await asyncio.sleep(0.05)
            seen.append(bool(reading))
            return await plain_done(user, since, limit=limit)

        from gateway.routes.projects import personal

        monkeypatch.setattr(state, "_read_rows", _slow_state)
        monkeypatch.setattr(personal, "my_completed_since", _done_read)
        answer = run_feed(member(*FULL))
        assert seen == [True], "the Handled read ran after the state join"
        assert answer["handled_ok"] is True

    def test_a_read_past_the_budget_counts_as_failed(self, seeded, monkeypatch):
        # The budget is the state join's own (`state.STATE_TIMEOUT_S`).
        monkeypatch.setattr(state, "STATE_TIMEOUT_S", 0.3)

        async def _hangs(user, since, *, limit):
            await asyncio.sleep(5)
            return {"rows": []}

        from gateway.routes.projects import personal

        monkeypatch.setattr(personal, "my_completed_since", _hangs)
        began = time.monotonic()
        answer = run_feed(admin(*FULL, "approvals"))
        assert time.monotonic() - began < 2.0
        assert answer["handled_ok"] is False
        assert answer["handled"] == []
        assert answer["cards"], "the feed keeps its cards"
        # No read starts after the budget ends: the chain never reached them.
        assert "applied_rules_since" not in _names(seeded)
        assert "read_decided" not in _names(seeded)

    def test_the_reads_that_did_not_start_count_as_failed(self, seeded, monkeypatch):
        # The second link hangs: the first keeps its entries, the third never
        # starts, and both later sources read failed.
        async def _hangs(user, account_ids, since, *, limit):
            await asyncio.sleep(5)
            return []

        from gateway.routes.email.automation import runner

        monkeypatch.setattr(runner, "applied_rules_since", _hangs)

        async def _go():
            return await handled.handled_run(admin(*FULL, "approvals"), NOW, budget_s=0.4)

        run = _bound(_go)
        assert run.sources == {"tasks": "ok", "email": "failed", "approvals": "failed"}
        assert run.ok is False
        assert {e["app"] for e in run.entries} == {"tasks"}
        assert "read_decided" not in _names(seeded)

    def test_a_read_with_too_little_budget_left_never_starts(self, seeded, monkeypatch):
        # The first link leaves about 0.04 s of 0.4 s, under MIN_START_S. The
        # next read must not take a connection for a read that cannot finish.
        plain = seeded.my_completed_since

        async def _slow(user, since, *, limit):
            await asyncio.sleep(0.36)
            return await plain(user, since, limit=limit)

        from gateway.routes.projects import personal

        monkeypatch.setattr(personal, "my_completed_since", _slow)
        assert handled.MIN_START_S == 0.05

        async def _go():
            return await handled.handled_run(admin(*FULL, "approvals"), NOW, budget_s=0.4)

        seeded.calls.clear()
        run = _bound(_go)
        assert run.sources["email"] == "failed" and run.sources["approvals"] == "failed"
        assert not {"feed_mailboxes", "applied_rules_since", "read_decided"} & set(
            _names(seeded))
        assert run.ok is False

    def test_a_feed_read_holds_two_connections_at_most(self, seeded, monkeypatch):
        """Fix round 1 (P2). The state join holds one connection, and the
        Handled chain holds one. Every fake that stands for a session counts
        itself while it runs, and the most at once must be 2."""
        import threading

        import action_broker
        import gateway.routes.tasks.settings as settings
        from gateway.routes.email.automation import runner
        from gateway.routes.email.transport import accounts
        from gateway.routes.projects import personal

        lock = threading.Lock()
        gauge = {"now": 0, "most": 0}

        def _in():
            with lock:
                gauge["now"] += 1
                gauge["most"] = max(gauge["most"], gauge["now"])

        def _out():
            with lock:
                gauge["now"] -= 1

        def _async(fn):
            async def counted(*a, **k):
                _in()
                try:
                    await asyncio.sleep(0.05)
                    return await fn(*a, **k)
                finally:
                    _out()
            return counted

        def _sync(fn):
            def counted(*a, **k):
                _in()
                try:
                    time.sleep(0.05)
                    return fn(*a, **k)
                finally:
                    _out()
            return counted

        for module, name in ((settings, "get_user_settings"), (state, "_read_rows"),
                             (personal, "my_completed_since"),
                             (accounts, "feed_mailboxes"),
                             (runner, "applied_rules_since"),
                             (personal, "stored_zone"), (personal, "my_due_tasks")):
            monkeypatch.setattr(module, name, _async(getattr(module, name)))
        monkeypatch.setattr(action_broker, "read_decided", _sync(action_broker.read_decided))
        answer = run_feed(admin(*FULL, "approvals"))
        assert answer["handled_ok"] is True
        assert {e["app"] for e in answer["handled"]} == {"tasks", "email", "approvals"}
        assert gauge["most"] == 2  # 2, and not 1: the chain runs beside the join

    def test_the_run_reports_each_source(self, seeded, monkeypatch):
        monkeypatch.setattr(seeded, "fail", {"read_decided": RuntimeError("db down")})

        async def _go():
            return await handled.handled_run(admin(*FULL, "approvals"), NOW, budget_s=5.0)

        run = _bound(_go)
        assert run.sources == {"tasks": "ok", "email": "ok", "approvals": "failed"}
        assert run.ok is False

    def test_a_failed_day_read_fails_every_held_read(self, seeded, monkeypatch):
        async def _broken(*_a, **_k):
            raise RuntimeError("settings down")

        monkeypatch.setattr(handled, "member_day", _broken)
        answer = run_feed(member(*FULL))
        assert (answer["handled"], answer["handled_ok"]) == ([], False)
        assert "my_completed_since" not in _names(seeded)


# ── The claim flag (the summary rule) ────────────────────────────────────────


class TestTheClaimFlag:
    def test_a_failed_read_silences_the_claim(self, seeded):
        seeded.fail["applied_rules_since"] = RuntimeError("db down")
        answer = run_feed(admin(*FULL, "approvals"))
        assert answer["handled_ok"] is False
        assert "email" not in {e["app"] for e in answer["handled"]}
        assert {"tasks", "approvals"} <= {e["app"] for e in answer["handled"]}

    def test_an_absent_source_is_not_a_failure(self, seeded):
        # No Email and no Approvals: both read absent, and the claim holds.
        answer = run_feed(member("projects", "tasks"))
        assert answer["handled"] and answer["handled_ok"] is True

    def test_a_failed_needs_source_is_not_a_failed_handled_read(self, seeded):
        # The needs view keeps its own rule (`sources`). The flag is the
        # Handled reads' only.
        seeded.fail["my_due_tasks"] = RuntimeError("db down")
        answer = run_feed(member(*FULL))
        assert answer["sources"]["tasks"] == "failed"
        assert answer["handled_ok"] is True


# ── The reads stay in step ───────────────────────────────────────────────────


class TestTheReadsStayInStep:
    def test_the_three_signatures(self):
        import action_broker
        from gateway.routes.email.automation.runner import applied_rules_since
        from gateway.routes.projects.personal import my_completed_since

        assert list(inspect.signature(my_completed_since).parameters) == [
            "user", "since", "limit"]
        assert list(inspect.signature(applied_rules_since).parameters) == [
            "user", "account_ids", "since", "limit"]
        assert list(inspect.signature(action_broker.read_decided).parameters) == [
            "since", "limit"]

    def test_three_providers_carry_a_handled_read(self):
        assert [p.source for p in handled.handled_providers()] == [
            "tasks", "email", "approvals"]
        assert shell.REGISTRY["approvals"].admin is True

    def test_the_lens_read_is_bounded_and_on_the_membership_fragment(self):
        from gateway.routes.projects import personal

        sql = " ".join(personal._COMPLETED_SINCE_SQL.split())
        assert " ".join(personal.MY_TASKS_FROM.split()) in sql
        assert "t.completed_at >= CAST(:since AS timestamptz)" in sql
        assert sql.endswith("ORDER BY t.completed_at DESC, t.id LIMIT :limit")
        assert "a.type = 'status_change'" in sql
        # The newest row, with `seq` for a tie (migration 213, H-159).
        assert "ORDER BY a.created_at DESC, a.seq DESC LIMIT 1" in sql

    def test_the_rule_read_keeps_the_trash_and_only_applied_rows(self):
        from gateway.routes.email.automation import runner

        sql = " ".join(runner._APPLIED_SINCE_SQL.split())
        assert "er.status = 'APPLIED'" in sql
        assert "WHERE user_id = :uid" in sql  # the owner predicate
        assert "folder" not in sql  # a rule may have put the mail in the trash
        assert sql.endswith("LIMIT :limit")

    def test_the_decided_statuses(self):
        from action_broker import broker

        assert broker.DECIDED_STATUSES == ("approved", "applied", "rejected")
        assert broker.READ_DECIDED_MAX == 15

    def test_no_handled_read_opens_a_session_or_writes_sql(self):
        for fn in (shell._tasks_handled, shell._email_handled, shell._approvals_handled):
            source = inspect.getsource(fn)
            for idiom in ("tenant_session", "get_db(", "text(", "execute("):
                assert idiom not in source, (fn.__name__, idiom)
        source = inspect.getsource(handled)
        for idiom in ("tenant_session(", "get_db(", "create_engine", "text("):
            assert idiom not in source.replace("state._tenant_session()", ""), idiom
        assert source.count("state._tenant_session()") == 1


@contextmanager
def _session(rows: list[dict], log: list, fail: BaseException | None = None):
    class _S:
        def execute(self, stmt, params=None):
            log.append((" ".join(str(stmt).split()), dict(params or {})))
            if fail is not None:
                raise fail
            from types import SimpleNamespace

            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    yield _S()


class TestReadDecided:
    """``action_broker.read_decided`` with a fake tenant session."""

    @pytest.fixture()
    def broker_db(self, monkeypatch):
        import acb_graph

        state_ = {"log": [], "tenants": [], "fail": None, "rows": []}

        def _tenant_session(org=None):
            state_["tenants"].append(org)
            return _session(state_["rows"], state_["log"], state_["fail"])

        monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
        return state_

    def _bound_call(self, *args, org: str | None = ORG):
        from acb_common.db import bind_tenant, clear_tenant, release_tenant
        from action_broker import read_decided

        token = bind_tenant(org) if org else clear_tenant()
        try:
            return read_decided(*args)
        finally:
            release_tenant(token)

    def test_it_reads_the_decided_statuses_since_the_instant_bounded(self, broker_db):
        broker_db["rows"] = [{"id": "pa-1"}]
        assert self._bound_call(NOW, 500) == [{"id": "pa-1"}]
        sql, params = broker_db["log"][0]
        assert "WHERE status IN ('approved', 'applied', 'rejected')" in sql
        assert "AND reviewed_at >= :since" in sql
        assert sql.endswith("ORDER BY reviewed_at DESC, id LIMIT :limit")
        assert params == {"since": NOW, "limit": 15}
        assert broker_db["tenants"] == [ORG]

    def test_it_raises_when_the_database_fails(self, broker_db):
        broker_db["fail"] = RuntimeError("db down")
        with pytest.raises(RuntimeError):
            self._bound_call(NOW)

    def test_with_no_tenant_it_reads_nothing(self, broker_db):
        assert self._bound_call(NOW, org=None) == []
        assert broker_db["tenants"] == []
