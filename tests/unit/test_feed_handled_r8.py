"""WS-58 MD-2c, R8: the Handled reads on a REAL Postgres (``my_day_feed.md``
MD-2c, done-when 2 and 3).

The three reads are new SQL, so a hermetic fake would agree with any SQL it
is handed. This runs the REAL app reads, through ``handled.handled_run`` and
``GET /shell/feed``, on the phase-4-promoted catalog of
``test_h3_rls_promotion_rehearsal.py``. It connects as the NON-privileged
role with RLS forced (``_as_member``), as ``test_shell_needs_r8.py`` does.

The four negative tests of the MD-2c ticket:

* **(i)** Member B never sees member A's completed lens tasks, and A never
  sees B's private one.
* **(ii)** Org B sees no rule rows of org A, even when the read names org A's
  mailbox.
* **(iii)** Org B's admin sees no approval of org A.
* **(iv)** A member who is not an admin gets no approval row.

Each source also has a positive test, so no negative passes on an empty
table, and each held source must read ``ok``. A read that FAILED also gives
no row, so an empty answer alone would pass on a broken build.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.shell import feed, handled, state
from gateway.routes.shell import needs as shell
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

# The catalog, the app role, the seed helpers and the binding, shared rather
# than copied. Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _account,
    _as_member,
    _mail,
    app_engine,
    promoted,
)
from tests.unit.test_shell_feed import _a_daytime_zone
from tests.unit.test_shell_needs_r8 import NOW, _Frozen, _lane, _project

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
#: Member A and member B of org B. A is the one whose day this suite reads.
A = f"handled-a-{TAG}@md2c.test"
B = f"handled-b-{TAG}@md2c.test"
#: An admin of org A, who decided org A's action.
BOSS = f"handled-boss-{TAG}@md2c.test"
#: The member's day starts at 01:00 in a zone where it is now 03:00 to 20:59,
#: so the seeds 5 to 60 minutes ago are in today, whatever the instant.
DAY_START_HOUR = 1


@pytest.fixture(autouse=True)
def _one_instant(monkeypatch):
    """The route's clocks read ``NOW``, as ``test_shell_needs_r8.py`` does."""
    from gateway.routes.projects import personal

    monkeypatch.setattr(shell, "_now", lambda: NOW)
    monkeypatch.setattr(personal, "datetime", _Frozen)


def _user(email: str, *features: str, is_admin: bool = False) -> UserContext:
    """A member who holds ``features``. Each also holds the Calendar pane
    (``feature:tasks``), so the day comes from ``get_user_settings`` with the
    seeded start hour, never from the column default of 07:00 (§6.8 item 8).
    A default hour would put the day start inside the seed on some runs."""
    granted = {f"feature:{f}" for f in (*features, "tasks")}
    if is_admin:
        granted.add(shell.ADMIN_PERMISSION)
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=EffectiveAccess(role_granted=frozenset(granted)))


def _since(zone: str) -> datetime:
    return state.Day(ZoneInfo(zone), DAY_START_HOUR).day_start(NOW)


def _closed_task(c, *, org: str, project: str, lane: str, title: str, number: int,
                 assignee: str | None, closed_at: datetime,
                 history: list[tuple[str, datetime]]) -> str:
    """A task in a closed lane with ``completed_at``, and its status_change
    rows: ``history`` is ``(created_by, created_at)``."""
    task = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number, completed_at) VALUES "
        "(CAST(:t AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
        "CAST(:s AS uuid), :ti, 'manual', :by, :n, :done)"),
        {"t": task, "o": org, "p": project, "s": lane, "ti": title, "by": A,
         "n": number, "done": closed_at})
    if assignee:
        c.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
            "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
            {"t": task, "a": assignee, "by": B, "o": org})
    for by, at in history:
        c.execute(text(
            "INSERT INTO pm_activities (task_id, project_id, type, body, created_by, "
            "created_at, organization_id) VALUES (CAST(:t AS uuid), CAST(:p AS uuid), "
            "'status_change', 'To do → Done', :by, :at, CAST(:o AS uuid))"),
            {"t": task, "p": project, "by": by, "at": at, "o": org})
    return task


def _rule_row(c, *, org: str, account: str, message: str | None, subject: str | None,
              rule: str, status: str, at: datetime) -> str:
    return str(c.execute(text(
        "INSERT INTO email_executed_rules (account_id, rule_name, message_id, subject, "
        "status, created_at, organization_id) VALUES (CAST(:a AS uuid), :r, "
        "CAST(:m AS uuid), :s, :st, :at, CAST(:o AS uuid)) RETURNING id"),
        {"a": account, "r": rule, "m": message, "s": subject, "st": status, "at": at,
         "o": org}).scalar_one())


def _action(c, *, org: str, action: str, status: str, by: str | None,
            at: datetime | None) -> str:
    return str(c.execute(text(
        "INSERT INTO pending_actions (actor, action, target, payload, authority, "
        "destructive, disposition, status, reviewed_by, reviewed_at, organization_id) "
        "VALUES ('agent:email-assistant', :ac, 'lead:x', '{}'::jsonb, 'suggest', true, "
        "'needs_approval', :st, :by, :at, CAST(:o AS uuid)) RETURNING id"),
        {"ac": action, "st": status, "by": by, "at": at, "o": org}).scalar_one())


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the reads are the first the app
    role makes. Times are minutes before ``NOW``. ``before`` is 30 minutes
    before A's day start."""
    p = promoted
    org, other = p.org_b, p.org_a
    zone = _a_daytime_zone(NOW)
    since = _since(zone)
    before = since - timedelta(minutes=30)

    def ago(minutes: int) -> datetime:
        return NOW - timedelta(minutes=minutes)

    ids: dict[str, str] = {"zone": zone}
    with p.admin_engine.begin() as c:
        for email in (A, B):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
            c.execute(text(
                "INSERT INTO user_settings (user_id, timezone, day_start_hour, "
                "organization_id) VALUES (:e, :z, :h, CAST(:o AS uuid))"),
                {"e": email, "z": zone, "h": DAY_START_HOUR, "o": org})
        shared, todo = _project(c, org=org, name=f"Handled {TAG}", grant_org=True)
        done = _lane(c, project=shared, name="Done", category="done", position=9)
        private, _plane = _project(c, org=org, name=f"B's own {TAG}", owner=B)
        pdone = _lane(c, project=private, name="Done", category="done", position=9)

        # A's lens: closed by A (an older move by B first), by B, by an agent.
        ids["by_a"] = _closed_task(
            c, org=org, project=shared, lane=done, title="Ship the bracket", number=1,
            assignee=A, closed_at=ago(20), history=[(B, ago(60)), (A, ago(20))])
        ids["by_b"] = _closed_task(
            c, org=org, project=shared, lane=done, title="Draw the housing", number=2,
            assignee=A, closed_at=ago(25), history=[(B, ago(25))])
        ids["by_agent"] = _closed_task(
            c, org=org, project=shared, lane=done, title="Wire the sensor", number=3,
            assignee=A, closed_at=ago(35), history=[("agent:builder", ago(35))])
        ids["by_sync"] = _closed_task(
            c, org=org, project=shared, lane=done, title="Synced closed", number=4,
            assignee=A, closed_at=ago(45), history=[("system:sync", ago(45))])
        ids["yesterday"] = _closed_task(
            c, org=org, project=shared, lane=done, title="Closed yesterday", number=5,
            assignee=A, closed_at=before, history=[(A, before)])
        ids["open"] = _closed_task(
            c, org=org, project=shared, lane=todo, title="Still open", number=6,
            assignee=A, closed_at=None, history=[])  # type: ignore[arg-type]
        # B's work only: B's private task, and a shared task assigned to B.
        ids["b_private"] = _closed_task(
            c, org=org, project=private, lane=pdone, title="B private closed", number=7,
            assignee=None, closed_at=ago(10), history=[(B, ago(10))])
        ids["b_assigned"] = _closed_task(
            c, org=org, project=shared, lane=done, title="B assigned closed", number=8,
            assignee=B, closed_at=ago(12), history=[(B, ago(12))])
        # Org A: a task assigned to A's address, closed now.
        far, flane = _project(c, org=other, name=f"Elsewhere {TAG}", grant_org=True)
        ids["elsewhere"] = _closed_task(
            c, org=other, project=far, lane=flane, title="Other org closed", number=9,
            assignee=A, closed_at=ago(5), history=[(A, ago(5))])

    # Email: A's pooled mailbox, A's separate mailbox, B's mailbox, and a
    # mailbox of A's address in org A.
    ids["box"] = box = _account(p.admin_engine, org=org, owner=A, default=True)
    ids["box_sep"] = sep = _account(p.admin_engine, org=org, owner=A, pooled=False)
    ids["box_b"] = box_b = _account(p.admin_engine, org=org, owner=B, default=True)
    ids["box_far"] = far_box = _account(p.admin_engine, org=other, owner=A, default=True)
    ids["m_quote"] = m_quote = _mail(p.admin_engine, org=org, account_id=box,
                                     sender="priya@x.test", subject="Quote for 40 units")
    ids["m_trash"] = m_trash = _mail(p.admin_engine, org=org, account_id=box,
                                     sender="news@x.test", subject="Weekly news",
                                     folder="trash")
    m_sep = _mail(p.admin_engine, org=org, account_id=sep, sender="a@x.test",
                  subject="Board papers")
    m_b = _mail(p.admin_engine, org=org, account_id=box_b, sender="a@x.test",
                subject="B's mail")
    m_far = _mail(p.admin_engine, org=other, account_id=far_box, sender="a@x.test",
                  subject="Other org mail")
    with p.admin_engine.begin() as c:
        _rule_row(c, org=org, account=box, message=m_quote, subject="Quote for 40 units",
                  rule="File quotes", status="APPLIED", at=ago(5))
        _rule_row(c, org=org, account=box, message=m_quote, subject="Quote (older run)",
                  rule="Label quotes", status="APPLIED", at=ago(15))
        _rule_row(c, org=org, account=box, message=m_trash, subject="Weekly news",
                  rule="Trash newsletters", status="APPLIED", at=ago(8))
        _rule_row(c, org=org, account=box, message=None, subject=None,
                  rule="Archive receipts", status="APPLIED", at=ago(12))
        for status in ("SKIPPED", "FAILED", "ERROR", "PENDING", "UNDONE"):
            _rule_row(c, org=org, account=box, message=m_quote, subject=f"Not {status}",
                      rule="R", status=status, at=ago(2))
        _rule_row(c, org=org, account=box, message=m_quote, subject="Ran yesterday",
                  rule="R", status="APPLIED", at=before)
        _rule_row(c, org=org, account=sep, message=m_sep, subject="Board papers",
                  rule="R", status="APPLIED", at=ago(4))
        _rule_row(c, org=org, account=box_b, message=m_b, subject="B's mail",
                  rule="R", status="APPLIED", at=ago(3))
        _rule_row(c, org=other, account=far_box, message=m_far, subject="Other org mail",
                  rule="R", status="APPLIED", at=ago(1))

        # Approvals in org B: applied by A, rejected by B, failed, pending, old.
        ids["pa_applied"] = _action(c, org=org, action="crm.zoho_update",
                                    status="applied", by=A, at=ago(6))
        ids["pa_rejected"] = _action(c, org=org, action="whatsapp.broadcast",
                                     status="rejected", by=B, at=ago(16))
        ids["pa_approved"] = _action(c, org=org, action="workflow.resume_run",
                                     status="approved", by="operator", at=ago(26))
        ids["pa_failed"] = _action(c, org=org, action="crm.zoho_delete",
                                   status="failed", by=A, at=ago(7))
        ids["pa_pending"] = _action(c, org=org, action="app.publish_review",
                                    status="pending", by=None, at=None)
        ids["pa_old"] = _action(c, org=org, action="crm.zoho_create",
                                status="applied", by=A, at=before)
        ids["pa_far"] = _action(c, org=other, action="crm.zoho_delete",
                                status="applied", by=BOSS, at=ago(2))
    return ids


@pytest.fixture
def broker_as_app(app_engine, monkeypatch):  # noqa: F811
    """The broker's sync sessions open as the NON-privileged role, as
    ``test_shell_needs_r8.py``'s ``queue`` fixture does."""
    from acb_graph import db as graph_db

    factory = sessionmaker(bind=app_engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)


async def _handled(p, user: UserContext, org: str | None = None) -> handled.HandledRun:
    # Room for a cold database. `sources` proves each read ran.
    async with _as_member(p, org or p.org_b):
        return await handled.handled_run(user, NOW, budget_s=60.0)


def _titles(run: handled.HandledRun, app: str | None = None) -> list[str]:
    return [e["title"] for e in run.entries if app is None or e["app"] == app]


class TestTasks:
    async def test_my_closed_tasks_return_with_who_closed_them(self, promoted, seeded):  # noqa: F811
        run = await _handled(promoted, _user(A, "projects"))
        assert run.sources == {"tasks": "ok", "email": "absent", "approvals": "absent"}
        assert run.ok is True
        assert _titles(run) == ["Ship the bracket", "Draw the housing", "Wire the sensor",
                                "Synced closed"]
        by = {e["title"]: e["by"] for e in run.entries}
        # The NEWEST status_change names the closer: A, not B's older move.
        assert by["Ship the bracket"] == {"kind": "you", "name": None}
        assert by["Draw the housing"] == {"kind": "person", "name": B.split("@")[0]}
        assert by["Wire the sensor"] == {"kind": "agent", "name": "builder"}
        assert by["Synced closed"] == {"kind": "system", "name": None}
        first = run.entries[0]
        assert first["href"] == f"/projects?task={seeded['by_a']}"
        assert first["app"] == "tasks"
        assert datetime.fromisoformat(first["at"]) == NOW - timedelta(minutes=20)

    async def test_i_member_b_never_sees_member_as_completed_lens_tasks(
        self, promoted, seeded,  # noqa: F811
    ):
        theirs = await _handled(promoted, _user(B, "projects"))
        assert theirs.sources["tasks"] == "ok"
        got = _titles(theirs)
        for title in ("Ship the bracket", "Draw the housing", "Wire the sensor",
                      "Synced closed"):
            assert title not in got, title
        # Non-vacuity: B's own closed work is real, and B reads it.
        assert set(got) == {"B private closed", "B assigned closed"}
        mine = _titles(await _handled(promoted, _user(A, "projects")))
        assert "B private closed" not in mine and "B assigned closed" not in mine

    async def test_the_other_orgs_task_never_returns(self, promoted, seeded):  # noqa: F811
        got = " ".join(_titles(await _handled(promoted, _user(A, "projects"))))
        assert "Other org closed" not in got and "yesterday" not in got
        assert "Still open" not in got

    async def test_the_lens_read_alone_is_bounded_and_newest_first(
        self, promoted, seeded,  # noqa: F811
    ):
        from gateway.routes.projects.personal import my_completed_since

        async with _as_member(promoted, promoted.org_b):
            answer = await my_completed_since(_user(A, "projects"), _since(seeded["zone"]),
                                              limit=2)
        assert [r["id"] for r in answer["rows"]] == [seeded["by_a"], seeded["by_b"]]
        assert answer["rows"][0]["closed_by"] == A


class TestEmailRules:
    async def test_the_rules_applied_today_on_my_pooled_mailbox(self, promoted, seeded):  # noqa: F811
        run = await _handled(promoted, _user(A, "email"))
        assert run.sources == {"tasks": "absent", "email": "ok", "approvals": "absent"}
        # One entry for the quote (the newest run), the trashed message, and
        # the row with no message, under its rule name.
        assert _titles(run) == ["Quote for 40 units", "Weekly news", "Archive receipts"]
        rows = {e["title"]: e for e in run.entries}
        assert rows["Quote for 40 units"]["href"] == (
            f"/email?email={seeded['m_quote']}&account={seeded['box']}")
        assert rows["Weekly news"]["href"] == (
            f"/email?email={seeded['m_trash']}&account={seeded['box']}")
        assert rows["Archive receipts"]["href"] == "/email"
        assert {e["by"]["kind"] for e in run.entries} == {"rules"}
        got = " ".join(_titles(run))
        for left_out in ("Not ", "older run", "yesterday", "Board papers", "B's mail",
                         "Other org mail"):
            assert left_out not in got, left_out

    async def test_ii_org_b_sees_no_rule_rows_of_org_a(self, promoted, seeded):  # noqa: F811
        from gateway.routes.email.automation.runner import applied_rules_since

        user = _user(A, "email")
        run = await _handled(promoted, user)
        assert run.sources["email"] == "ok"
        assert "Other org mail" not in _titles(run)
        # Even when the read names org A's mailbox, org B's session reads nothing.
        async with _as_member(promoted, promoted.org_b):
            named = await applied_rules_since(user, [seeded["box_far"]], _since(seeded["zone"]),
                                              limit=15)
        assert named == []
        # Non-vacuity: the row is real, and org A's session reads it.
        async with _as_member(promoted, promoted.org_a):
            in_a = await applied_rules_since(user, [seeded["box_far"]], _since(seeded["zone"]),
                                             limit=15)
        assert [r["subject"] for r in in_a] == ["Other org mail"]

    async def test_another_members_mailbox_gives_no_row_even_when_named(
        self, promoted, seeded,  # noqa: F811
    ):
        from gateway.routes.email.automation.runner import applied_rules_since

        async with _as_member(promoted, promoted.org_b):
            named = await applied_rules_since(_user(A, "email"), [seeded["box_b"]],
                                              _since(seeded["zone"]), limit=15)
            theirs = await applied_rules_since(_user(B, "email"), [seeded["box_b"]],
                                               _since(seeded["zone"]), limit=15)
        assert named == []
        assert [r["subject"] for r in theirs] == ["B's mail"]  # non-vacuity


class TestApprovals:
    async def test_an_admin_reads_the_decisions_of_the_day(
        self, promoted, seeded, broker_as_app,  # noqa: F811
    ):
        run = await _handled(promoted, _user(A, "approvals", is_admin=True))
        assert run.sources == {"tasks": "absent", "email": "absent", "approvals": "ok"}
        assert _titles(run) == ["Change a record in Zoho CRM", "Send a WhatsApp broadcast",
                                "Resume a paused workflow"]
        by = {e["title"]: e["by"] for e in run.entries}
        assert by["Change a record in Zoho CRM"] == {"kind": "you", "name": None}
        assert by["Send a WhatsApp broadcast"] == {"kind": "person",
                                                   "name": B.split("@")[0]}
        assert by["Resume a paused workflow"] == {"kind": "system", "name": None}
        assert {e["href"] for e in run.entries} == {"/approvals"}

    async def test_iii_org_bs_admin_sees_no_approval_of_org_a(
        self, promoted, seeded, broker_as_app,  # noqa: F811
    ):
        run = await _handled(promoted, _user(A, "approvals", is_admin=True))
        assert run.sources["approvals"] == "ok"
        assert "Delete a record in Zoho CRM" not in _titles(run)  # pa_far, and pa_failed
        # Non-vacuity: org A's admin reads org A's decision, and none of B's.
        theirs = await _handled(promoted, _user(BOSS, "approvals", is_admin=True),
                                org=promoted.org_a)
        assert theirs.sources["approvals"] == "ok"
        assert _titles(theirs) == ["Delete a record in Zoho CRM"]

    async def test_iv_a_member_who_is_not_an_admin_gets_no_approval_row(
        self, promoted, seeded, broker_as_app,  # noqa: F811
    ):
        run = await _handled(promoted, _user(A, "approvals", "projects", "email"))
        assert run.sources["approvals"] == "absent"
        assert not [e for e in run.entries if e["app"] == "approvals"]
        assert run.ok is True
        assert run.entries  # non-vacuity: the other two sources answered

    async def test_the_broker_read_alone_leaves_out_failed_pending_and_old(
        self, promoted, seeded, broker_as_app,  # noqa: F811
    ):
        import action_broker
        from acb_common.db import bind_tenant, release_tenant

        token = bind_tenant(promoted.org_b)
        try:
            rows = action_broker.read_decided(_since(seeded["zone"]))
        finally:
            release_tenant(token)
        got = {str(r["id"]) for r in rows}
        assert {seeded["pa_applied"], seeded["pa_rejected"], seeded["pa_approved"]} <= got
        for key in ("pa_failed", "pa_pending", "pa_old", "pa_far"):
            assert seeded[key] not in got, key


class TestThroughTheFeed:
    async def test_the_feed_answers_handled_and_the_flag(
        self, promoted, seeded, broker_as_app, monkeypatch,  # noqa: F811
    ):
        # Room for a cold database, as test_shell_feed_r8.py gives it.
        monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
        monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
        monkeypatch.setattr(shell, "MAILBOX_TIMEOUT_S", 30.0)
        monkeypatch.setattr(feed, "FEED_PROVIDER_TIMEOUT_S", 30.0)
        monkeypatch.setattr(feed, "FEED_BUDGET_S", 90.0)
        monkeypatch.setattr(state, "STATE_TIMEOUT_S", 60.0)
        user = _user(A, "projects", "email", "tasks", "approvals", is_admin=True)
        async with _as_member(promoted, promoted.org_b):
            answer = await feed.shell_feed(limit=80, user=user)
        assert answer["handled_ok"] is True
        assert {e["app"] for e in answer["handled"]} == {"tasks", "email", "approvals"}
        assert len(answer["handled"]) == 10
        stamps = [datetime.fromisoformat(e["at"]) for e in answer["handled"]]
        assert stamps == sorted(stamps, reverse=True)
        assert "handled" not in {g["id"] for g in answer["groups"]}
