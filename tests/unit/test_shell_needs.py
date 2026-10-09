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
* **A failing app is left out**, and its source reads ``failed``.
* **The calls name every parameter the routes declare.** Otherwise a new
  ``Query(...)`` or ``Depends()`` parameter would arrive as the marker itself.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import HTTPException
from gateway.routes.email.automation.replyzero import reply_zero as _real_reply_zero
from gateway.routes.projects.notifications import (
    list_notifications as _real_list_notifications,
)
from gateway.routes.projects.personal import my_inbox as _real_my_inbox
from gateway.routes.shell import needs as shell

ME = "member@customer.example"
OTHER = "other@customer.example"
NOW = datetime.now(UTC)
#: Later today in UTC, whatever the hour the suite runs at.
LATER_TODAY = NOW + (datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time(),
                                      tzinfo=UTC) - NOW) / 2


def member(*features: str, email: str = ME) -> UserContext:
    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset(f"feature:{f}" for f in features)),
    )


ALL = ("projects", "email")


def _task(task_id: str, title: str, due: datetime | None, project: str = "Hardware") -> dict:
    return {"id": task_id, "title": title, "project_name": project,
            "due_at": due.isoformat() if due else None, "completed_at": None}


class Fakes:
    """Stand-ins for the app functions. Each answers by the member it is
    handed, as the real one does, and records who called it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        self.active = 0
        self.max_active = 0
        self.fail: dict[str, BaseException] = {}
        self.tasks: dict[str, list[dict]] = {
            ME: [
                _task("t-old", "File the VAT return", NOW - timedelta(days=3)),
                _task("t-late", "Ship the extruder", NOW - timedelta(hours=2)),
                _task("t-soon", "Call the supplier", LATER_TODAY),
                _task("t-next", "Plan the launch", NOW + timedelta(days=3)),
                _task("t-none", "Someday idea", None),
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
                self._thread("th-new", "m-new", "Invoice March", NOW - timedelta(hours=1)),
                self._thread("th-old", "m-old", "Quote for 40 units", NOW - timedelta(days=4)),
            ],
            "a-separate": [self._thread("th-sep", "m-sep", "Board papers", NOW)],
            "a-theirs": [self._thread("th-theirs", "m-theirs", "Their secret", NOW)],
        }

    @staticmethod
    def _thread(tid: str, mid: str, subject: str, at: datetime) -> dict:
        return {"thread_id": tid, "message_id": mid, "subject": subject,
                "from": "Priya Sharma", "from_email": "p@x.test",
                "received_at": at.isoformat()}

    async def _enter(self, name: str, user: object, kwargs: dict) -> None:
        self.calls.append((name, user, kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.005)
        self.active -= 1
        if name in self.fail:
            raise self.fail[name]

    async def my_today(self, **kw):
        await self._enter("my_today", kw["user"], kw)
        return {"today": NOW.date().isoformat(), "timezone": "UTC", "stored": False,
                "valid": True}

    async def my_inbox(self, **kw):
        await self._enter("my_inbox", kw["user"], kw)
        rows = self.tasks.get(kw["user"].email, [])
        page = kw["page"]
        return SimpleNamespace(rows=rows[page.offset:page.offset + page.limit],
                               total=len(rows))

    async def list_notifications(self, **kw):
        await self._enter("list_notifications", kw["user"], kw)
        rows = self.notes.get(kw["user"].email, [])
        return {"rows": rows[: kw["page"].limit], "total": len(rows),
                "unread": {"total": len(rows), "mentions": 0}}

    async def list_accounts(self, **kw):
        await self._enter("list_accounts", kw["user"], kw)
        return [SimpleNamespace(model_dump=lambda a=a: dict(a))
                for a in self.accounts.get(kw["user"].email, [])]

    async def reply_zero(self, **kw):
        await self._enter("reply_zero", kw["user"], kw)
        owned = {a["id"] for a in self.accounts.get(kw["user"].email, [])}
        if kw["account_id"] not in owned:
            # The real route's owner check answers 404 for this.
            raise HTTPException(status_code=404, detail="Account not found")
        return {"threads": self.threads.get(kw["account_id"], [])[: kw["limit"]],
                "type": kw["type"]}


@pytest.fixture()
def fakes(monkeypatch) -> Fakes:
    f = Fakes()
    import gateway.routes.email.automation.replyzero as replyzero
    import gateway.routes.email.transport.accounts as accounts
    import gateway.routes.projects.notifications as notifications
    import gateway.routes.projects.personal as personal

    monkeypatch.setattr(personal, "my_today", f.my_today)
    monkeypatch.setattr(personal, "my_inbox", f.my_inbox)
    monkeypatch.setattr(notifications, "list_notifications", f.list_notifications)
    monkeypatch.setattr(accounts, "list_accounts", f.list_accounts)
    monkeypatch.setattr(replyzero, "reply_zero", f.reply_zero)
    return f


def run(user: UserContext, limit: int = shell.DEFAULT_LIMIT) -> dict:
    return asyncio.run(shell.shell_needs(limit=limit, user=user))


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
        assert not called & {"my_today", "my_inbox", "list_notifications"}

    def test_a_member_without_email_gets_no_mail(self, fakes):
        answer = run(member("projects"))
        assert answer["sources"]["email"] == "absent"
        assert "email" not in {i["app"] for i in answer["items"]}
        assert not {name for name, _, _ in fakes.calls} & {"list_accounts", "reply_zero"}

    def test_the_tasks_provider_checks_projects_not_tasks(self, fakes):
        # `feature:tasks` shows the pane. The lens router demands `projects`.
        answer = run(member("tasks", "email"))
        assert answer["sources"]["tasks"] == "absent"
        assert not [i for i in answer["items"] if i["app"] == "tasks"]

    def test_a_member_with_no_app_gets_nothing(self, fakes):
        answer = run(member())
        assert answer == {"count": 0, "items": [],
                          "sources": {"tasks": "absent", "projects": "absent",
                                      "email": "absent"}}
        assert fakes.calls == []

    def test_a_signed_out_caller_is_refused(self, fakes):
        anon = UserContext(email=None, role=UserRole.EMPLOYEE)
        with pytest.raises(HTTPException) as e:
            run(anon)
        assert e.value.status_code == 401


class TestAnotherMembersRowsNeverArrive:
    def test_another_members_private_task_never_appears(self, fakes):
        answer = run(member(*ALL))
        assert "tasks:t-theirs" not in ids(answer)
        assert all(u.email == ME for name, u, _ in fakes.calls if name == "my_inbox")

    def test_another_members_notification_never_appears(self, fakes):
        answer = run(member(*ALL))
        assert "projects:n-theirs" not in ids(answer)
        assert all(u.email == ME for name, u, _ in fakes.calls
                   if name == "list_notifications")

    def test_another_members_mailbox_is_never_read(self, fakes):
        answer = run(member(*ALL))
        assert not [i for i in ids(answer) if i.startswith("email:a-theirs:")]
        asked = [kw["account_id"] for name, _, kw in fakes.calls if name == "reply_zero"]
        assert asked == ["a-mine"], "only the mailboxes the member's own list returns"

    def test_a_separate_mailbox_is_left_out(self, fakes):
        # D-EM-30: a separate mailbox leaves every read of more than one mailbox.
        answer = run(member(*ALL))
        assert not [i for i in ids(answer) if i.startswith("email:a-separate:")]

    def test_each_app_function_gets_this_requests_member(self, fakes):
        user = member(*ALL)
        run(user)
        assert fakes.calls and all(u is user for _, u, _ in fakes.calls)


class TestTheAnswer:
    def test_the_rows_in_the_contracts_order(self, fakes):
        assert ids(run(member(*ALL))) == [
            # overdue, oldest first
            "tasks:t-old", "tasks:t-late",
            # due today
            "tasks:t-soon",
            # notification, newest first
            "projects:n-2", "projects:n-1",
            # needs reply, the person waiting longest first
            "email:a-mine:th-old", "email:a-mine:th-new",
        ]

    def test_a_task_due_later_or_with_no_date_is_not_a_need(self, fakes):
        got = ids(run(member(*ALL)))
        assert "tasks:t-next" not in got and "tasks:t-none" not in got

    def test_the_shape_of_each_kind(self, fakes):
        rows = {i["id"]: i for i in run(member(*ALL))["items"]}
        assert rows["tasks:t-old"] == {
            "id": "tasks:t-old", "app": "tasks", "kind": "overdue",
            "title": "File the VAT return", "detail": "Hardware",
            "href": "/projects?task=t-old", "at": rows["tasks:t-old"]["at"],
            "act": "done", "act_ref": "t-old"}
        assert rows["tasks:t-soon"]["kind"] == "due_today"
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

    def test_the_count_is_the_length_of_the_list(self, fakes):
        answer = run(member(*ALL))
        assert answer["count"] == len(answer["items"]) == 7
        assert answer["sources"] == {"tasks": "ok", "projects": "ok", "email": "ok"}

    def test_the_email_read_starts_no_backfill(self, fakes):
        run(member(*ALL))
        for name, _, kw in fakes.calls:
            if name == "reply_zero":
                assert kw["type"] == "needs_reply"
                assert kw["background"].tasks == []


class TestTheCaps:
    def test_one_app_cannot_fill_the_feed(self, fakes):
        fakes.tasks[ME] = [_task(f"t-{n:03}", f"Task {n}", NOW - timedelta(hours=n + 1))
                           for n in range(40)]
        answer = run(member(*ALL), limit=50)
        tasks = [i for i in answer["items"] if i["app"] == "tasks"]
        assert len(tasks) == shell.PER_APP == 15
        # The cap keeps the oldest, the order the feed shows them in.
        assert tasks[0]["id"] == "tasks:t-039"
        assert {i["app"] for i in answer["items"]} == {"tasks", "projects", "email"}

    def test_the_limit_defaults_to_30_and_stops_at_50(self, fakes):
        fakes.tasks[ME] = [_task(f"t-{n}", "x", NOW - timedelta(hours=n + 1))
                           for n in range(20)]
        fakes.notes[ME] = [dict(fakes.notes[ME][0], id=f"n-{n}") for n in range(20)]
        fakes.threads["a-mine"] = [
            Fakes._thread(f"th-{n}", f"m-{n}", "s", NOW - timedelta(hours=n))
            for n in range(20)]
        assert run(member(*ALL))["count"] == 30
        assert run(member(*ALL), limit=500)["count"] == 45
        assert run(member(*ALL), limit=2)["count"] == 2
        assert run(member(*ALL), limit=0)["count"] == 1

    def test_the_tasks_provider_reads_more_than_one_lens_page(self, fakes):
        far = [_task(f"t-{n}", "later", NOW + timedelta(days=9)) for n in range(150)]
        fakes.tasks[ME] = [*far, _task("t-deep", "Overdue on page two", NOW - timedelta(days=1))]
        assert "tasks:t-deep" in ids(run(member(*ALL)))
        pages = [kw["page"].page for name, _, kw in fakes.calls if name == "my_inbox"]
        assert pages == [1, 2]


class TestAFailingAppIsLeftOut:
    @pytest.mark.parametrize(
        ("broken", "source"),
        [("my_inbox", "tasks"), ("list_notifications", "projects"),
         ("reply_zero", "email"), ("list_accounts", "email")],
    )
    @pytest.mark.parametrize("error", [HTTPException(status_code=404, detail="gone"),
                                       RuntimeError("db down")])
    def test_its_source_reads_failed_and_the_rest_answer(self, fakes, broken, source, error):
        fakes.fail[broken] = error
        answer = run(member(*ALL))
        assert answer["sources"][source] == "failed"
        assert source not in {i["app"] for i in answer["items"]}
        others = {k for k in ("tasks", "projects", "email") if k != source}
        assert {answer["sources"][k] for k in others} == {"ok"}
        assert {i["app"] for i in answer["items"]} == others

    def test_a_slow_app_is_left_out(self, fakes, monkeypatch):
        monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 0.001)
        answer = run(member(*ALL))
        assert answer["items"] == []
        assert set(answer["sources"].values()) == {"failed"}

    def test_one_deadline_for_all_apps(self, fakes, monkeypatch):
        monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 0.0)
        answer = run(member(*ALL))
        assert set(answer["sources"].values()) == {"failed"}
        assert fakes.calls == []

    def test_the_providers_never_overlap(self, fakes):
        run(member(*ALL))
        assert fakes.max_active == 1


# The REAL route signatures, read at import time, before any fixture swaps them.

REAL_PARAMS = {
    "my_inbox": frozenset(inspect.signature(_real_my_inbox).parameters),
    "list_notifications": frozenset(inspect.signature(_real_list_notifications).parameters),
    "reply_zero": frozenset(inspect.signature(_real_reply_zero).parameters),
}


class TestTheCallsStayInStep:
    @pytest.mark.parametrize("name", sorted(REAL_PARAMS))
    def test_every_parameter_of_the_route_is_named(self, fakes, name):
        run(member(*ALL))
        passed = set(next(kw for n, _, kw in fakes.calls if n == name))
        assert passed == REAL_PARAMS[name], (
            f"{name} has {sorted(REAL_PARAMS[name] - passed)} that the shell does not pass"
        )


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
