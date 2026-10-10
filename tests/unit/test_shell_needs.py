"""NS-3 slice A — GET /shell/needs, the needs feed (navigation_shell.md §7.2).

The route reuses each app's OWN authorized read. Those reads have their own
suites, and the R8 half of this one is `test_shell_needs_r8.py`. So this file
fences only what the shell adds, and each case is written so a wrong build
fails it:

* **The app's feature gate, again** (one negative case per provider). The gate
  sits on each app's router, so a direct call skips it unless the provider
  checks. A member without the app gets no row, the source reads ``absent``,
  and the app's function is never called.
* **Another member's rows never arrive.** The fakes answer by the member they
  are handed, so a provider that widened the member, or read a mailbox the
  member does not own, would show the other member's row.
* **The order and the caps** of the contract.
* **A failing app is left out**, and its source reads ``failed``. One failing
  mailbox costs only its own rows.
* **The calls stay in step** with the app functions they call.
* **The two app reads are bounded and reuse the app's rule** (the lens read
  and the email read, by their SQL text). R8 runs them for real.
* **Approvals (slice C)** reads the queue of the bound tenant, through the
  broker's own ``read_pending``. The fake stands in for the tenant SESSION,
  so the real read runs, and a database error reaches the feed as
  ``failed``. Only an org admin who holds ``feature:approvals`` gets a row
  (owner decision, 2026-10-10). A member with ``feature:*`` who is not an
  admin gets none, and the broker is never called. A row has no act, opens
  ``/approvals``, and never pushes the member's own work out of the feed.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email import digest
from gateway.routes.email.digest import needs_reply_threads as _real_needs_reply_threads
from gateway.routes.projects import personal
from gateway.routes.projects.notifications import (
    list_notifications as _real_list_notifications,
)
from gateway.routes.projects.personal import my_due_tasks as _real_my_due_tasks
from gateway.routes.shell import needs as shell

ME = "member@customer.example"
OTHER = "other@customer.example"
ORG = "org-mine"
ORG_OTHER = "org-other"
NOW = datetime.now(UTC)
#: Later today in UTC, whatever the hour the suite runs at.
LATER_TODAY = NOW + (datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time(),
                                      tzinfo=UTC) - NOW) / 2
TOMORROW = datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)


def member(*features: str, email: str = ME) -> UserContext:
    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
    )


ALL = ("projects", "email")


def admin(*features: str, email: str = ME) -> UserContext:
    """An org admin: the admin test of ``routes/shell/intent.py``, plus the
    features named. The approvals source needs both."""
    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(
            {*(f"feature:{f}" for f in features), shell.ADMIN_PERMISSION})),
    )


def everything_but_admin(email: str = ME) -> UserContext:
    """A member as migration 207 makes one: ``feature:*``, and no admin."""
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset({"feature:*"})),
    )


def _task(task_id: str, title: str, due: datetime | None, project: str = "Hardware",
          disposition: str = "NEXT") -> dict:
    return {"id": task_id, "title": title, "project_name": project,
            "due_at": due.isoformat() if due else None, "completed_at": None,
            "disposition": disposition}


def _thread(tid: str, mid: str, subject: str, at: datetime) -> dict:
    return {"thread_id": tid, "message_id": mid, "subject": subject,
            "who": "Priya Sharma", "last_message_at": at.isoformat()}


class Fakes:
    """Stand-ins for the app functions. Each answers by the member it is
    handed, as the real one does, and records who called it. The two bounded
    reads keep their real contract: due before tomorrow and the oldest
    deadline first, and the longest wait first, each up to ``limit``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        #: The pending actions, by organization. The broker reads the
        #: tenant the request bound, so the fake does too.
        self.pending: dict[str, list[dict]] = {
            ORG: [
                {"id": "pa-new", "actor": "agent:email-assistant",
                 "action": "crm.zoho_update", "target": "lead:z-1",
                 "payload": {"args": {}}, "status": "pending",
                 "created_at": NOW - timedelta(minutes=10)},
                {"id": "pa-old", "actor": "user:priya@customer.example",
                 "action": "whatsapp.broadcast", "target": "account:wa-1",
                 "payload": {"targets": [{"chat_id": "c1"}, {"chat_id": "c2"}]},
                 "status": "pending", "created_at": NOW - timedelta(hours=3)},
            ],
            ORG_OTHER: [
                {"id": "pa-theirs", "actor": "agent:other", "action": "crm.zoho_delete",
                 "target": "lead:z-9", "payload": {}, "status": "pending",
                 "created_at": NOW},
            ],
        }
        self.active = 0
        self.max_active = 0
        self.fail: dict[str, BaseException] = {}
        self.fail_mailbox: dict[str, BaseException] = {}
        self.slow_mailbox: set[str] = set()
        self.tasks: dict[str, list[dict]] = {
            ME: [
                _task("t-late", "Ship the extruder", NOW - timedelta(hours=2)),
                _task("t-old", "File the VAT return", NOW - timedelta(days=3)),
                _task("t-soon", "Call the supplier", LATER_TODAY),
                _task("t-next", "Plan the launch", NOW + timedelta(days=3)),
            ],
            OTHER: [_task("t-theirs", "Their private overdue task", NOW - timedelta(days=1))],
        }
        self.notes: dict[str, list[dict]] = {
            ME: [
                {"id": "n-1", "task_id": "t-9", "kind": "assigned", "actor": "priya@x.test",
                 "task_title": "Fix the hopper", "task_number": 12, "excerpt": None,
                 "created_at": NOW - timedelta(hours=5)},
                {"id": "n-2", "task_id": "t-8", "kind": "mention", "actor": "agent:builder",
                 "task_title": "Wire the sensor", "task_number": None,
                 "excerpt": "Can you check this?", "created_at": NOW - timedelta(hours=1)},
            ],
            OTHER: [{"id": "n-theirs", "task_id": "t-7", "kind": "comment", "actor": ME,
                     "task_title": "Their notification", "task_number": 3, "excerpt": None,
                     "created_at": NOW}],
        }
        self.accounts: dict[str, list[dict]] = {
            ME: [{"id": "a-mine", "in_all_inboxes": True},
                 {"id": "a-separate", "in_all_inboxes": False}],
            OTHER: [{"id": "a-theirs", "in_all_inboxes": True}],
        }
        self.threads: dict[str, list[dict]] = {
            "a-mine": [
                _thread("th-new", "m-new", "Invoice March", NOW - timedelta(hours=1)),
                _thread("th-old", "m-old", "Quote for 40 units", NOW - timedelta(days=4)),
            ],
            "a-separate": [_thread("th-sep", "m-sep", "Board papers", NOW)],
            "a-theirs": [_thread("th-theirs", "m-theirs", "Their secret", NOW)],
        }

    async def _enter(self, name: str, user: object, kwargs: dict) -> None:
        self.calls.append((name, user, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.005)
        self.active -= 1
        if name in self.fail:
            raise self.fail[name]

    async def my_due_tasks(self, user, *, limit):
        await self._enter("my_due_tasks", user, {"user": user, "limit": limit})
        due = [t for t in self.tasks.get(user.email, [])
               if t["due_at"] and datetime.fromisoformat(t["due_at"]) < TOMORROW]
        due.sort(key=lambda t: t["due_at"])
        # ⚠️ No SOMEDAY filter here, on purpose: the provider's own check of
        # the row's disposition is what this suite fences.
        return {"rows": due[:limit], "today": NOW.date().isoformat(), "timezone": "UTC"}

    async def list_notifications(self, **kw):
        await self._enter("list_notifications", kw["user"], kw)
        rows = self.notes.get(kw["user"].email, [])
        return {"rows": rows[: kw["page"].limit], "total": len(rows),
                "unread": {"total": len(rows), "mentions": 0}}

    async def list_accounts(self, **kw):
        await self._enter("list_accounts", kw["user"], kw)
        return [SimpleNamespace(model_dump=lambda a=a: dict(a))
                for a in self.accounts.get(kw["user"].email, [])]

    @contextmanager
    def tenant_session(self, org=None):
        """``acb_graph.tenant_session``: the broker's REAL ``read_pending``
        runs on it. It answers the bound org's rows by the read's own
        contract, the oldest first and ``:limit`` rows, and records the SQL."""
        fakes = self

        class _Session:
            def execute(self, stmt, params=None):
                sql = " ".join(str(stmt).split())
                fakes.calls.append(("read_pending", None,
                                    {"org": org, "sql": sql, **(params or {})}))
                time.sleep(0.005)  # a read takes time, as the async fakes do
                if "read_pending" in fakes.fail:
                    raise fakes.fail["read_pending"]
                rows = sorted(fakes.pending.get(org or "", []), key=lambda r: r["created_at"])
                rows = [dict(r) for r in rows[: params["limit"]]]
                return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

        yield _Session()

    async def needs_reply_threads(self, user, account_id, limit):
        await self._enter("needs_reply_threads", user,
                          {"user": user, "account_id": account_id, "limit": limit})
        if account_id in self.slow_mailbox:
            await asyncio.sleep(5)
        if account_id in self.fail_mailbox:
            raise self.fail_mailbox[account_id]
        owned = {a["id"] for a in self.accounts.get(user.email, [])}
        if account_id not in owned:
            # The real read's owner check answers 404 for this.
            raise HTTPException(status_code=404, detail="Account not found")
        rows = sorted(self.threads.get(account_id, []), key=lambda t: t["last_message_at"])
        return rows[:limit]


@pytest.fixture()
def fakes(monkeypatch) -> Fakes:
    f = Fakes()
    import gateway.routes.email.transport.accounts as accounts
    import gateway.routes.projects.notifications as notifications

    monkeypatch.setattr(personal, "my_due_tasks", f.my_due_tasks)
    monkeypatch.setattr(notifications, "list_notifications", f.list_notifications)
    monkeypatch.setattr(accounts, "list_accounts", f.list_accounts)
    monkeypatch.setattr(digest, "needs_reply_threads", f.needs_reply_threads)
    import acb_graph

    monkeypatch.setattr(acb_graph, "tenant_session", f.tenant_session)
    return f


def run(user: UserContext, limit: int = shell.DEFAULT_LIMIT, org: str | None = ORG) -> dict:
    """The route, with ``org`` bound the way the auth dependency binds it."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    async def _go() -> dict:
        token = bind_tenant(org) if org else clear_tenant()
        try:
            return await shell.shell_needs(limit=limit, user=user)
        finally:
            release_tenant(token)

    return asyncio.run(_go())


def ids(answer: dict) -> list[str]:
    return [i["id"] for i in answer["items"]]


class TestTheFeatureGate:
    """One negative case per provider: no feature, no call, no row."""

    def test_a_member_without_projects_gets_no_task_and_no_notification(self, fakes):
        answer = run(member("email"))
        assert answer["sources"]["tasks"] == "absent"
        assert answer["sources"]["projects"] == "absent"
        assert {i["app"] for i in answer["items"]} == {"email"}
        called = {name for name, _, _ in fakes.calls}
        assert not called & {"my_due_tasks", "list_notifications"}

    def test_a_member_without_email_gets_no_mail(self, fakes):
        answer = run(member("projects"))
        assert answer["sources"]["email"] == "absent"
        assert "email" not in {i["app"] for i in answer["items"]}
        assert not {name for name, _, _ in fakes.calls} & {"list_accounts",
                                                            "needs_reply_threads"}

    def test_the_tasks_provider_checks_projects_not_tasks(self, fakes):
        # `feature:tasks` shows the pane. The lens router demands `projects`.
        answer = run(member("tasks", "email"))
        assert answer["sources"]["tasks"] == "absent"
        assert not [i for i in answer["items"] if i["app"] == "tasks"]

    def test_a_member_with_no_app_gets_nothing(self, fakes):
        answer = run(member())
        assert answer == {"count": 0, "items": [],
                          "sources": {"tasks": "absent", "approvals": "absent",
                                      "projects": "absent", "email": "absent"}}
        assert fakes.calls == []

    def test_a_member_without_approvals_gets_no_pending_action(self, fakes):
        # NS-3 done-when 3. The queue has no approver column, so the gate of
        # `routes/actions.py` is the only thing that keeps a member out.
        answer = run(member(*ALL))
        assert answer["sources"]["approvals"] == "absent"
        assert not [i for i in answer["items"] if i["app"] == "approvals"]
        assert not [i for i in ids(answer) if i.startswith("approvals:")]
        assert "read_pending" not in {name for name, _, _ in fakes.calls}

    def test_a_member_with_every_feature_who_is_not_an_admin_gets_no_approval(self, fakes):
        # The P1 of round 2. Migration 207 grants `feature:*` to `member` and
        # `manager`, and it covers `feature:approvals`. So the feature alone
        # would put the org's whole queue on every member's My Day.
        answer = run(everything_but_admin())
        assert answer["sources"]["approvals"] == "absent"
        assert not [i for i in answer["items"] if i["app"] == "approvals"]
        assert "read_pending" not in {name for name, _, _ in fakes.calls}
        assert answer["sources"]["tasks"] == "ok"  # non-vacuity: the rest ran

    def test_an_admin_without_the_feature_gets_no_approval(self, fakes):
        answer = run(admin(*ALL))
        assert answer["sources"]["approvals"] == "absent"
        assert "read_pending" not in {name for name, _, _ in fakes.calls}

    def test_an_admin_with_the_feature_gets_the_queue(self, fakes):
        answer = run(admin("approvals"))
        assert answer["sources"]["approvals"] == "ok"
        assert ids(answer) == ["approvals:pa-old", "approvals:pa-new"]

    def test_the_approvals_provider_checks_the_feature_of_the_actions_route(self):
        # The feature that `routes/actions.py`'s router demands, read from it.
        from gateway.routes import actions

        gate = actions.router.dependencies[0].dependency
        held = [c.cell_contents for c in (gate.__closure__ or ())]
        assert "feature:approvals" in held
        assert shell.PROVIDERS["approvals"][0] == "approvals"

    def test_the_admin_test_is_the_one_intent_and_auth_me_use(self):
        # Reused, never copied: `routes/shell/intent.py` owns the string, and
        # `GET /auth/me` reports the same test as `is_admin`.
        from gateway.routes.shell import intent

        assert shell.ADMIN_PERMISSION is intent.ADMIN_PERMISSION
        assert "approvals" in shell.ADMIN_SOURCES

    def test_the_approvals_provider_runs_last(self):
        # A slow sync pool must not spend the budget of the member's own work.
        assert list(shell.PROVIDERS)[-1] == "approvals"

    def test_a_signed_out_caller_is_refused(self, fakes):
        anon = UserContext(email=None, role=UserRole.EMPLOYEE)
        with pytest.raises(HTTPException) as e:
            run(anon)
        assert e.value.status_code == 401


class TestAnotherMembersRowsNeverArrive:
    def test_another_members_private_task_never_appears(self, fakes):
        answer = run(member(*ALL))
        assert "tasks:t-theirs" not in ids(answer)
        assert all(u.email == ME for name, u, _ in fakes.calls if name == "my_due_tasks")

    def test_another_members_notification_never_appears(self, fakes):
        answer = run(member(*ALL))
        assert "projects:n-theirs" not in ids(answer)
        assert all(u.email == ME for name, u, _ in fakes.calls
                   if name == "list_notifications")

    def test_another_members_mailbox_is_never_read(self, fakes):
        answer = run(member(*ALL))
        assert not [i for i in ids(answer) if i.startswith("email:a-theirs:")]
        asked = [kw["account_id"] for name, _, kw in fakes.calls
                 if name == "needs_reply_threads"]
        assert asked == ["a-mine"], "only the mailboxes the member's own list returns"

    def test_a_separate_mailbox_is_left_out(self, fakes):
        # D-EM-30: a separate mailbox leaves every read of more than one mailbox.
        answer = run(member(*ALL))
        assert not [i for i in ids(answer) if i.startswith("email:a-separate:")]

    def test_each_app_function_gets_this_requests_member(self, fakes):
        user = member(*ALL)
        run(user)
        assert fakes.calls and all(u is user for _, u, _ in fakes.calls)

    def test_another_orgs_pending_action_never_appears(self, fakes):
        answer = run(admin(*ALL, "approvals"))
        assert answer["sources"]["approvals"] == "ok"
        got = [i for i in ids(answer) if i.startswith("approvals:")]
        assert got == ["approvals:pa-old", "approvals:pa-new"]
        assert "approvals:pa-theirs" not in ids(answer)
        # The broker read the tenant this request bound, and no other.
        assert [kw["org"] for n, _, kw in fakes.calls if n == "read_pending"] == [ORG]

    def test_the_other_org_sees_its_own_queue_and_not_mine(self, fakes):
        # Non-vacuity: the row held out above is real for its own org.
        assert ids(run(admin("approvals"), org=ORG_OTHER)) == ["approvals:pa-theirs"]

    def test_with_no_tenant_bound_no_pending_action_arrives(self, fakes):
        answer = run(admin("approvals"), org=None)
        assert answer["items"] == []


class TestTheAnswer:
    def test_the_rows_in_the_contracts_order(self, fakes):
        assert ids(run(admin(*ALL, "approvals"))) == [
            # overdue, oldest first
            "tasks:t-old", "tasks:t-late",
            # approvals, the longest wait first: an agent's work waits on it
            "approvals:pa-old", "approvals:pa-new",
            # due today
            "tasks:t-soon",
            # notification, newest first
            "projects:n-2", "projects:n-1",
            # needs reply, the person waiting longest first
            "email:a-mine:th-old", "email:a-mine:th-new",
        ]

    def test_a_task_due_today_but_not_yet_is_due_today(self, fakes):
        rows = {i["id"]: i for i in run(member(*ALL))["items"]}
        assert rows["tasks:t-soon"]["kind"] == "due_today"
        assert rows["tasks:t-late"]["kind"] == "overdue"
        assert "tasks:t-next" not in rows

    def test_a_someday_task_is_not_a_need_and_a_waiting_one_is(self, fakes):
        # Someday is the member's deliberate "not now". An overdue
        # waiting-for is a cue to chase someone, so it stays.
        fakes.tasks[ME] += [
            _task("t-someday", "Learn the cello", NOW - timedelta(days=2),
                  disposition="SOMEDAY"),
            _task("t-waiting", "Quote from Priya", NOW - timedelta(days=2),
                  disposition="WAITING"),
        ]
        got = ids(run(member(*ALL)))
        assert "tasks:t-someday" not in got
        assert "tasks:t-waiting" in got

    def test_a_reference_task_is_not_a_need(self, fakes):
        # Reference is information, not an action, so a due date on it is
        # no reason to ask the member for anything.
        fakes.tasks[ME].append(_task("t-reference", "Supplier price list",
                                     NOW - timedelta(days=2), disposition="REFERENCE"))
        assert "tasks:t-reference" not in ids(run(member(*ALL)))

    def test_the_feed_takes_its_hidden_set_from_the_lens(self):
        # One owner: the lens's NOT_NOW_DISPOSITIONS. The feed's check and
        # the lens's SQL both come from it, so they cannot disagree.
        assert frozenset(personal.NOT_NOW_DISPOSITIONS) == shell.HIDDEN_DISPOSITIONS
        assert {"SOMEDAY", "REFERENCE"} <= shell.HIDDEN_DISPOSITIONS
        assert "WAITING" not in shell.HIDDEN_DISPOSITIONS

    def test_the_shape_of_each_kind(self, fakes):
        rows = {i["id"]: i for i in run(member(*ALL))["items"]}
        assert rows["tasks:t-old"] == {
            "id": "tasks:t-old", "app": "tasks", "kind": "overdue",
            "title": "File the VAT return", "detail": "Hardware",
            "href": "/projects?task=t-old", "at": rows["tasks:t-old"]["at"],
            "act": "done", "act_ref": "t-old"}
        assert rows["projects:n-1"] == {
            "id": "projects:n-1", "app": "projects", "kind": "notification",
            "title": "priya assigned you #12 Fix the hopper", "detail": None,
            "href": "/projects?task=t-9", "at": rows["projects:n-1"]["at"],
            "act": "read", "act_ref": "n-1"}
        assert rows["projects:n-2"]["title"] == "builder mentioned you on Wire the sensor"
        assert rows["projects:n-2"]["detail"] == "Can you check this?"
        assert rows["email:a-mine:th-old"] == {
            "id": "email:a-mine:th-old", "app": "email", "kind": "needs_reply",
            "title": "Quote for 40 units", "detail": "From Priya Sharma",
            "href": "/email?email=m-old&account=a-mine",
            "at": rows["email:a-mine:th-old"]["at"], "act": None, "act_ref": None}
        for row in rows.values():
            assert datetime.fromisoformat(row["at"]).tzinfo is not None

    def test_the_shape_of_an_approval(self, fakes):
        rows = {i["id"]: i for i in run(admin(*ALL, "approvals"))["items"]}
        assert rows["approvals:pa-new"] == {
            "id": "approvals:pa-new", "app": "approvals", "kind": "approval",
            "title": "Change a record in Zoho CRM",
            "detail": "Proposed by the email assistant agent",
            "href": "/approvals", "at": rows["approvals:pa-new"]["at"],
            # Approving runs an outward write, so My Day never does it in
            # one click. The member reads the proposal in Approvals.
            "act": None, "act_ref": None}
        assert rows["approvals:pa-old"]["title"] == "Send a WhatsApp broadcast to 2 chats"
        assert rows["approvals:pa-old"]["detail"] == "Proposed by priya"
        assert datetime.fromisoformat(rows["approvals:pa-old"]["at"]).tzinfo is not None

    @pytest.mark.parametrize(("action", "actor", "title", "detail"), [
        ("app.publish_review", "app:invoices", "Review an app before it publishes",
         "Proposed by the invoices app"),
        ("app.mail_send", "app:invoices:me@x.test", "Run mail send for an app",
         "Proposed by the invoices app"),
        ("workflow.resume_run", "workflow:Onboarding", "Resume a paused workflow",
         "Proposed by the Onboarding workflow"),
        ("workflow.resume_run", "workflow:0b6f7d4e-2c1a-4f7e-9d3b-1a2b3c4d5e6f",
         "Resume a paused workflow", "Proposed by a workflow"),
        ("crm.zoho_create", "crm:zoho-sync", "Create a record in Zoho CRM",
         "Proposed by the Zoho CRM sync"),
        ("zoho.email", "", "Zoho email", "Proposed by Somebody"),
    ])
    def test_each_action_reads_as_plain_words_and_prints_no_id(
        self, fakes, action, actor, title, detail,
    ):
        fakes.pending[ORG] = [{"id": "pa-1", "actor": actor, "action": action,
                               "target": "lead:0b6f7d4e", "payload": {},
                               "status": "pending", "created_at": NOW}]
        row = run(admin("approvals"))["items"][0]
        assert (row["title"], row["detail"]) == (title, detail)
        assert "0b6f7d4e" not in row["title"] + row["detail"]

    def test_the_count_is_the_length_of_the_list(self, fakes):
        answer = run(member(*ALL))
        assert answer["count"] == len(answer["items"]) == 7
        assert answer["sources"] == {"tasks": "ok", "approvals": "absent",
                                     "projects": "ok", "email": "ok"}


class TestTheCaps:
    def test_one_bounded_task_read_and_the_oldest_overdue_first(self, fakes):
        # 40 overdue tasks, more than the cap. The newest-created is the
        # oldest deadline, the case the old paged read lost.
        fakes.tasks[ME] = [_task(f"t-{n:03}", f"Task {n}", NOW - timedelta(hours=n + 1))
                           for n in range(40)]
        answer = run(member(*ALL), limit=50)
        tasks = [i for i in answer["items"] if i["app"] == "tasks"]
        assert len(tasks) == shell.PER_APP == 15
        assert tasks[0]["id"] == "tasks:t-039"
        calls = [kw for name, _, kw in fakes.calls if name == "my_due_tasks"]
        assert len(calls) == 1 and calls[0]["limit"] == shell.PER_APP
        assert {i["app"] for i in answer["items"]} == {"tasks", "projects", "email"}

    def test_each_mailbox_is_asked_for_the_cap_and_the_longest_wait_comes_first(self, fakes):
        fakes.threads["a-mine"] = [
            _thread(f"th-{n:02}", f"m-{n:02}", "s", NOW - timedelta(hours=n)) for n in range(30)]
        mail = [i for i in run(member(*ALL), limit=50)["items"] if i["app"] == "email"]
        assert len(mail) == 15
        assert mail[0]["id"] == "email:a-mine:th-29"
        asked = [kw["limit"] for name, _, kw in fakes.calls if name == "needs_reply_threads"]
        assert asked == [shell.PER_APP]

    def test_the_approvals_are_capped_and_the_longest_wait_comes_first(self, fakes):
        fakes.pending[ORG] = [
            {"id": f"pa-{n:02}", "actor": "agent:x", "action": "crm.zoho_update",
             "target": "t", "payload": {}, "status": "pending",
             "created_at": NOW - timedelta(minutes=n)} for n in range(30)]
        got = run(admin("approvals"), limit=50)["items"]
        assert len(got) == shell.PER_APP
        assert got[0]["id"] == "approvals:pa-29"

    def _flood(self, fakes, overdue: int, due_today: int) -> None:
        fakes.tasks[ME] = (
            [_task(f"t-o{n:02}", "x", NOW - timedelta(hours=n + 1)) for n in range(overdue)]
            + [_task(f"t-d{n:02}", "x", LATER_TODAY) for n in range(due_today)])
        fakes.pending[ORG] = [
            {"id": f"pa-{n:02}", "actor": "agent:x", "action": "crm.zoho_update",
             "target": "t", "payload": {}, "status": "pending",
             "created_at": NOW - timedelta(minutes=n)} for n in range(15)]

    def test_approvals_never_push_out_the_members_own_work(self, fakes):
        # 15 overdue tasks and 15 approvals fill a limit of 30 by kind order
        # alone. The notifications and the replies must still arrive.
        self._flood(fakes, overdue=15, due_today=0)
        answer = run(admin(*ALL, "approvals"), limit=30)
        apps = [i["app"] for i in answer["items"]]
        assert apps.count("tasks") == 15
        assert apps.count("projects") == 2 and apps.count("email") == 2
        assert apps.count("approvals") == 11
        assert answer["count"] == 30

    def test_approvals_never_push_out_a_task_due_today(self, fakes):
        # Approvals sort before due today. At a limit of 25, the old cut gave
        # 10 overdue and 15 approvals, and no due-today row.
        self._flood(fakes, overdue=10, due_today=5)
        got = run(admin(*ALL, "approvals"), limit=25)["items"]
        assert len([i for i in got if i["kind"] == "due_today"]) == 5
        assert len([i for i in got if i["kind"] == "approval"]) == 25 - 19
        # The display order is unchanged: approvals still sit after overdue.
        kinds = [i["kind"] for i in got]
        assert kinds.index("approval") < kinds.index("due_today")

    def test_the_limit_defaults_to_30_and_stops_at_50(self, fakes):
        fakes.tasks[ME] = [_task(f"t-{n}", "x", NOW - timedelta(hours=n + 1))
                           for n in range(20)]
        fakes.notes[ME] = [dict(fakes.notes[ME][0], id=f"n-{n}") for n in range(20)]
        fakes.threads["a-mine"] = [
            _thread(f"th-{n}", f"m-{n}", "s", NOW - timedelta(hours=n)) for n in range(20)]
        assert run(member(*ALL))["count"] == 30
        assert run(member(*ALL), limit=500)["count"] == 45
        assert run(member(*ALL), limit=2)["count"] == 2
        assert run(member(*ALL), limit=0)["count"] == 1


class TestAFailingAppIsLeftOut:
    @pytest.mark.parametrize(
        ("broken", "source"),
        [("my_due_tasks", "tasks"), ("list_notifications", "projects"),
         ("needs_reply_threads", "email"), ("list_accounts", "email"),
         ("read_pending", "approvals")],
    )
    @pytest.mark.parametrize("error", [HTTPException(status_code=404, detail="gone"),
                                       RuntimeError("db down")])
    def test_its_source_reads_failed_and_the_rest_answer(self, fakes, broken, source, error):
        fakes.fail[broken] = error
        answer = run(admin(*ALL, "approvals"))
        assert answer["sources"][source] == "failed"
        assert source not in {i["app"] for i in answer["items"]}
        others = {k for k in ("tasks", "approvals", "projects", "email") if k != source}
        assert {answer["sources"][k] for k in others} == {"ok"}
        assert {i["app"] for i in answer["items"]} == others

    def test_one_failing_mailbox_costs_only_its_own_rows(self, fakes):
        fakes.accounts[ME].append({"id": "a-broken", "in_all_inboxes": True})
        fakes.fail_mailbox["a-broken"] = RuntimeError("provider down")
        answer = run(member(*ALL))
        assert answer["sources"]["email"] == "ok"
        assert "email:a-mine:th-old" in ids(answer)

    def test_one_slow_mailbox_costs_only_its_own_rows(self, fakes, monkeypatch):
        monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 0.05)
        fakes.accounts[ME].insert(0, {"id": "a-slow", "in_all_inboxes": True})
        fakes.slow_mailbox.add("a-slow")
        answer = run(member(*ALL))
        assert answer["sources"]["email"] == "ok"
        assert "email:a-mine:th-old" in ids(answer)

    def test_every_mailbox_failing_reads_failed(self, fakes):
        fakes.fail_mailbox["a-mine"] = RuntimeError("provider down")
        answer = run(member(*ALL))
        assert answer["sources"]["email"] == "failed"

    def test_a_slow_app_is_left_out(self, fakes, monkeypatch):
        monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 0.001)
        answer = run(admin(*ALL, "approvals"))
        assert answer["items"] == []
        assert set(answer["sources"].values()) == {"failed"}

    def test_one_deadline_for_all_apps(self, fakes, monkeypatch):
        monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 0.0)
        answer = run(admin(*ALL, "approvals"))
        assert set(answer["sources"].values()) == {"failed"}
        assert fakes.calls == []

    def test_the_providers_never_overlap(self, fakes):
        run(member(*ALL))
        assert fakes.max_active == 1


# The REAL signatures, read at import time, before any fixture swaps them.


class TestTheCallsStayInStep:
    def test_the_lens_read_takes_the_member_and_a_limit(self):
        params = inspect.signature(_real_my_due_tasks).parameters
        assert list(params) == ["user", "limit"]
        assert params["limit"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_approvals_read_takes_no_member_and_no_org(self):
        # The broker reads `current_tenant()` (H-201). An org passed in would
        # be one taken from somewhere other than the auth dependency.
        from action_broker.broker import read_pending

        assert list(inspect.signature(read_pending).parameters) == ["limit"]
        assert "_bound_tenant(" in inspect.getsource(read_pending)

    def test_the_feed_reads_the_bounded_queue_oldest_first(self, fakes):
        run(admin("approvals"))
        call = next(kw for n, _, kw in fakes.calls if n == "read_pending")
        assert "ORDER BY created_at ASC, id LIMIT :limit" in call["sql"]
        assert call["limit"] == shell.PER_APP

    def test_the_email_read_takes_the_member_a_mailbox_and_a_limit(self):
        assert list(inspect.signature(_real_needs_reply_threads).parameters) == [
            "user", "account_id", "limit"]

    def test_every_parameter_of_the_notification_route_is_named(self, fakes):
        run(member(*ALL))
        passed = set(next(kw for n, _, kw in fakes.calls if n == "list_notifications"))
        assert passed == set(inspect.signature(_real_list_notifications).parameters)


# ── The two app reads, by their SQL (R8 runs them for real) ─────────────────


class _RecordingDB:
    """Answers every statement with one row and no rows, and keeps the SQL.
    ``zone`` is the member's stored zone, ``user_settings.timezone``."""

    def __init__(self, zone: str = "UTC") -> None:
        self.sql: list[str] = []
        self.params: list[dict] = []
        self.zone = zone

    async def execute(self, stmt, params=None):
        self.sql.append(" ".join(str(stmt).split()))
        self.params.append(dict(params or {}))
        return SimpleNamespace(fetchone=lambda: SimpleNamespace(
            organization_id="org-1", timezone=self.zone), fetchall=list, scalar=lambda: 0)


def _recording(monkeypatch, module, zone: str = "UTC") -> _RecordingDB:
    db = _RecordingDB(zone)

    @asynccontextmanager
    async def _session(*_a, **_k):
        yield db

    monkeypatch.setattr(module, "_tenant_session", _session)
    return db


class TestTheLensRead:
    def test_it_is_one_bounded_read_of_the_lens_oldest_deadline_first(self, monkeypatch):
        db = _recording(monkeypatch, personal)
        asyncio.run(personal.my_due_tasks(member("projects"), limit=15))
        reads = [s for s in db.sql if "FROM pm_tasks t" in s]
        assert len(reads) == 1, "one query per load, whatever the list's size"
        sql = reads[0]
        # The lens's own membership fragment, never a second visibility rule.
        assert " ".join(personal.MY_TASKS_FROM.split()) in sql
        assert " ".join(personal.DEFERRED_CLAUSE.split()) in sql
        assert personal.DUE_BY_TODAY_CLAUSE in sql
        assert personal.ACTIONABLE_CLAUSE in sql
        assert sql.endswith("ORDER BY t.due_at ASC, t.id LIMIT :limit")
        bind = db.params[db.sql.index(sql)]
        assert bind["limit"] == 15 and bind["who"] == ME

    def test_the_bound_is_the_start_of_the_members_tomorrow(self, monkeypatch):
        db = _recording(monkeypatch, personal)
        answer = asyncio.run(personal.my_due_tasks(member("projects"), limit=15))
        bind = next(p for s, p in zip(db.sql, db.params, strict=True) if "FROM pm_tasks t" in s)
        today = datetime.fromisoformat(answer["today"]).date()
        assert bind["due_before"] == datetime.combine(
            today + timedelta(days=1), datetime.min.time(), tzinfo=bind["due_before"].tzinfo)
        assert bind["due_before"].utcoffset() is not None

    def test_the_bound_is_the_members_tomorrow_not_utcs(self, monkeypatch):
        # 11:30 UTC on 9 October is 01:30 on 10 October on Kiritimati (+14).
        # The member's today is the 10th, and their tomorrow starts at 10:00
        # UTC on the 10th. UTC would say the 9th, and a bound of midnight.
        frozen = datetime(2026, 10, 9, 11, 30, tzinfo=UTC)

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz is None else frozen.astimezone(tz)

        monkeypatch.setattr(personal, "datetime", _Clock)
        db = _recording(monkeypatch, personal, zone="Pacific/Kiritimati")
        answer = asyncio.run(personal.my_due_tasks(member("projects"), limit=15))
        bind = next(p for s, p in zip(db.sql, db.params, strict=True) if "FROM pm_tasks t" in s)
        assert answer["timezone"] == "Pacific/Kiritimati"
        assert answer["today"] == "2026-10-10"
        assert str(bind["today"]) == "2026-10-10", "the deferral clause uses the member's date"
        assert bind["due_before"] == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
        # 23:59 on the member's 10th is due today, and is in. 00:01 on the
        # member's 11th is due tomorrow, and is out.
        due_today = datetime(2026, 10, 10, 9, 59, tzinfo=UTC)
        due_tomorrow = datetime(2026, 10, 10, 10, 1, tzinfo=UTC)
        assert due_today < bind["due_before"] <= due_tomorrow

    def test_the_sql_leaves_out_what_the_python_rule_calls_not_mine_to_act_on(self):
        clause = personal.ACTIONABLE_CLAUSE
        assert "'SOMEDAY'" in clause and "'TRASH'" in clause
        for disposition in personal.NOT_NOW_DISPOSITIONS:
            assert f"p.disposition IS DISTINCT FROM '{disposition}'" in clause
        assert {"DONE", "TRASH", "SOMEDAY", "REFERENCE"} == personal.NOT_DUE_WORK
        assert "s.category = 'backlog'" in clause
        for category in personal.CLOSING_CATEGORIES:
            assert f"'{category}'" in clause


class TestTheEmailRead:
    def test_it_reads_the_digests_live_threads_oldest_first_with_a_limit(self, monkeypatch):
        db = _recording(monkeypatch, digest)
        asyncio.run(digest.needs_reply_threads(member("email"), "a-1", 15))
        owner = db.sql[0]
        assert "FROM email_accounts WHERE id = :id AND user_id = :uid" in owner
        assert db.params[0] == {"id": "a-1", "uid": ME}
        threads = next(s for s in db.sql if "FROM email_thread_status ts" in s)
        assert " ".join(digest._LIVE_THREAD.split()) in threads
        assert "ts.status = 'NEEDS_REPLY'" in threads
        assert "ORDER BY ts.last_message_at ASC LIMIT :lim" in threads
        assert db.params[db.sql.index(threads)]["lim"] == 15

    def test_the_live_rule_leaves_out_snoozed_trash_junk_and_archive(self):
        rule = " ".join(digest._LIVE_THREAD.split())
        assert "tem.snoozed_until > now()" in rule
        # Archive is "dealt with", the rule Reply Zero applies to its active
        # buckets, so an archived thread never asks for a reply.
        assert "IN ('trash', 'junk', 'archive')" in rule

    def test_another_members_mailbox_is_404_before_any_thread_read(self, monkeypatch):
        db = _recording(monkeypatch, digest)

        async def no_row(stmt, params=None):
            db.sql.append(str(stmt))
            return SimpleNamespace(fetchone=lambda: None, fetchall=list)

        db.execute = no_row
        with pytest.raises(HTTPException) as e:
            asyncio.run(digest.needs_reply_threads(member("email"), "a-theirs", 15))
        assert e.value.status_code == 404
        assert not [s for s in db.sql if "email_thread_status" in s]


class TestTheRouteIsMounted:
    def test_the_gateway_serves_shell_needs(self, monkeypatch):
        # `main.py` mounts every router inside `try/except: pass`, so an import
        # error would hide the route. This finds it.
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
        from fastapi.routing import iter_route_contexts
        from gateway.main import app

        paths = {getattr(c, "path", None) for c in iter_route_contexts(app.routes)}
        assert "/shell/needs" in paths
        assert "/shell/search" in paths  # non-vacuity: the walk sees the router
