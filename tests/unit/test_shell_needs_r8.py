"""NS-3 slice A, R8: GET /shell/needs on a REAL Postgres (navigation_shell.md
NS-3, done-when 3 and 4).

The shell route adds no SQL. It calls each app's own authorized read. So this
runs the WHOLE route, with each app's real SQL, on the phase-4-promoted
catalog of `test_h3_rls_promotion_rehearsal.py`. It connects as the
NON-privileged role with RLS forced (`_as_member`, as
`test_shell_search_email_r8.py` does). Two members of ONE organization, and
one row of the other organization:

* **My Tasks.** The member's own overdue task and task due today return. The
  other member's PRIVATE overdue task (in their personal project) never
  returns. Nor does an overdue task assigned to the other member only.
* **Projects.** The member's unread notification returns. The other member's
  notification, on a task the member can see, never returns.
* **Email.** The member's own needs-reply thread returns. The other member's
  mailbox, with a needs-reply thread of its own, never returns. Nor does the
  member's own SNOOZED, JUNK or ARCHIVED thread: the feed reads the digest's
  live threads (`digest._LIVE_THREAD`). Archive is "dealt with", as in Reply
  Zero.
* **The other organization.** Its task, assigned to the member's address,
  never returns: RLS and the lens's tenant bind each hold it out.
* **The two bounded reads** (`TestTheLensDueRead`). A third member holds more
  due work than the cap. The lens read gives the oldest deadlines first and
  stops at the cap. It leaves out what the Python rule calls not mine to act
  on (SOMEDAY, TRASH, a backlog lane, a closed lane), and keeps a stated DONE
  on an open lane and a WAITING task. The email read gives the threads that
  waited longest, and stops at the cap.

Each source must read ``ok``. A provider that FAILED also returns no row, so
an empty answer alone would pass on a broken build.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.shell import needs as shell
from sqlalchemy import text

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

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"needs-me-{TAG}@ns3.test"
OTHER = f"needs-other-{TAG}@ns3.test"
BUSY = f"needs-busy-{TAG}@ns3.test"


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset({"feature:projects", "feature:email"})),
    )


def _project(c, *, org: str, name: str, owner: str | None = None,
             grant_org: bool = False) -> tuple[str, str]:
    """One project and its one lane. ``owner`` makes it a personal project."""
    project, lane = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses, personal_owner) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
        ":n, 'manual', :by, true, :own)"),
        {"p": project, "o": org, "n": name, "by": owner or ME, "own": owner})
    c.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 1, "
        "'todo', true)"), {"s": lane, "p": project})
    if grant_org:
        c.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by, "
            "organization_id) VALUES (CAST(:p AS uuid), 'org', :by, CAST(:o AS uuid))"),
            {"p": project, "by": ME, "o": org})
    return project, lane


def _lane(c, *, project: str, name: str, category: str, position: int) -> str:
    lane = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
        "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), :n, :pos, :cat, "
        "false)"), {"s": lane, "p": project, "n": name, "pos": position, "cat": category})
    return lane


def _stated(c, *, org: str, task: str, email: str, disposition: str) -> None:
    c.execute(text(
        "INSERT INTO pm_task_personal (task_id, member_email, disposition, "
        "organization_id) VALUES (CAST(:t AS uuid), :e, :d, CAST(:o AS uuid))"),
        {"t": task, "e": email, "d": disposition, "o": org})


def _task(c, *, org: str, project: str, lane: str, title: str, due: datetime,
          assignee: str | None, number: int) -> str:
    task = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number, due_at) VALUES "
        "(CAST(:t AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
        "CAST(:s AS uuid), :ti, 'manual', :by, :n, :due)"),
        {"t": task, "o": org, "p": project, "s": lane, "ti": title, "by": ME,
         "n": number, "due": due})
    if assignee:
        c.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
            "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
            {"t": task, "a": assignee, "by": OTHER, "o": org})
    return task


def _notification(c, *, org: str, recipient: str, task: str, actor: str) -> str:
    return str(c.execute(text(
        "INSERT INTO pm_notifications (recipient, kind, task_id, actor, "
        "organization_id) VALUES (:r, 'assigned', CAST(:t AS uuid), :a, "
        "CAST(:o AS uuid)) RETURNING id"),
        {"r": recipient, "t": task, "a": actor, "o": org}).scalar_one())


def _needs_reply(c, *, org: str, account: str, thread: str, message: str,
                 at: datetime) -> None:
    c.execute(text(
        "INSERT INTO email_thread_status (account_id, thread_id, status, "
        "last_message_id, last_message_at, reason, organization_id) VALUES "
        "(CAST(:a AS uuid), :t, 'NEEDS_REPLY', CAST(:m AS uuid), :at, 'asked', "
        "CAST(:o AS uuid))"),
        {"a": account, "t": thread, "m": message, "at": at, "o": org})


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """The rows, seeded as the superuser, so the route's reads are the first
    the app role makes."""
    p = promoted
    org, elsewhere = p.org_b, p.org_a
    now = datetime.now(UTC)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    later_today = now + (midnight - now) / 2
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for email in (ME, OTHER):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
        shared, lane = _project(c, org=org, name=f"Launch {TAG}", grant_org=True)
        private, plane = _project(c, org=org, name=f"Theirs {TAG}", owner=OTHER)
        ids["overdue"] = _task(c, org=org, project=shared, lane=lane,
                               title="Ship the extruder", due=now - timedelta(days=2),
                               assignee=ME, number=1)
        ids["today"] = _task(c, org=org, project=shared, lane=lane,
                             title="Call the supplier", due=later_today,
                             assignee=ME, number=2)
        ids["later"] = _task(c, org=org, project=shared, lane=lane,
                             title="Plan the launch", due=now + timedelta(days=5),
                             assignee=ME, number=3)
        ids["their_private"] = _task(c, org=org, project=private, lane=plane,
                                     title="Their private overdue task",
                                     due=now - timedelta(days=1), assignee=None, number=4)
        ids["their_assigned"] = _task(c, org=org, project=shared, lane=lane,
                                      title="Their assigned overdue task",
                                      due=now - timedelta(days=1), assignee=OTHER, number=5)
        far, flane = _project(c, org=elsewhere, name=f"Elsewhere {TAG}", grant_org=True)
        ids["elsewhere"] = _task(c, org=elsewhere, project=far, lane=flane,
                                 title="Other org overdue task",
                                 due=now - timedelta(days=1), assignee=ME, number=6)
        ids["note_mine"] = _notification(c, org=org, recipient=ME,
                                         task=ids["overdue"], actor=OTHER)
        ids["note_theirs"] = _notification(c, org=org, recipient=OTHER,
                                           task=ids["their_assigned"], actor=ME)

    ids["mine"] = _account(p.admin_engine, org=org, owner=ME, default=True)
    ids["theirs"] = _account(p.admin_engine, org=org, owner=OTHER, default=True)
    ids["mail_mine"] = _mail(p.admin_engine, org=org, account_id=ids["mine"],
                             sender="priya@x.test", subject="Quote for 40 units",
                             thread=f"th-mine-{TAG}", minutes_ago=90)
    ids["mail_theirs"] = _mail(p.admin_engine, org=org, account_id=ids["theirs"],
                               sender="priya@x.test", subject="Their secret quote",
                               thread=f"th-theirs-{TAG}", minutes_ago=30)
    # The member's own threads that the email app does not count as live: a
    # snoozed one and one in junk. Older than the live one, so a feed that
    # kept them would put them FIRST.
    ids["mail_snoozed"] = _mail(p.admin_engine, org=org, account_id=ids["mine"],
                                sender="priya@x.test", subject="Snoozed for later",
                                thread=f"th-snoozed-{TAG}", minutes_ago=300)
    ids["mail_junk"] = _mail(p.admin_engine, org=org, account_id=ids["mine"],
                             sender="spam@x.test", subject="You won a prize",
                             folder="junk", thread=f"th-junk-{TAG}", minutes_ago=400)
    ids["mail_archived"] = _mail(p.admin_engine, org=org, account_id=ids["mine"],
                                 sender="priya@x.test", subject="Dealt with already",
                                 folder="archive", thread=f"th-archived-{TAG}",
                                 minutes_ago=500)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE email_messages SET snoozed_until = now() + interval '1 day' "
            "WHERE id = CAST(:m AS uuid)"), {"m": ids["mail_snoozed"]})
        _needs_reply(c, org=org, account=ids["mine"], thread=f"th-mine-{TAG}",
                     message=ids["mail_mine"], at=now - timedelta(minutes=90))
        _needs_reply(c, org=org, account=ids["theirs"], thread=f"th-theirs-{TAG}",
                     message=ids["mail_theirs"], at=now - timedelta(minutes=30))
        _needs_reply(c, org=org, account=ids["mine"], thread=f"th-snoozed-{TAG}",
                     message=ids["mail_snoozed"], at=now - timedelta(minutes=300))
        _needs_reply(c, org=org, account=ids["mine"], thread=f"th-junk-{TAG}",
                     message=ids["mail_junk"], at=now - timedelta(minutes=400))
        _needs_reply(c, org=org, account=ids["mine"], thread=f"th-archived-{TAG}",
                     message=ids["mail_archived"], at=now - timedelta(minutes=500))
    return ids


@pytest.fixture(scope="module")
def busy(promoted):  # noqa: F811
    """A third member with more due work and more waiting mail than the cap."""
    p = promoted
    org = p.org_b
    now = datetime.now(UTC)
    out: dict[str, object] = {}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, :e, 'employee', 'active', "
            "CAST(:o AS uuid))"), {"e": BUSY, "o": org})
        project, todo = _project(c, org=org, name=f"Busy {TAG}", grant_org=True)
        backlog = _lane(c, project=project, name="Backlog", category="backlog", position=0)
        done = _lane(c, project=project, name="Done", category="done", position=9)

        def due(days: int) -> datetime:
            return now - timedelta(days=days)

        def make(title: str, days: int, lane: str, number: int) -> str:
            return _task(c, org=org, project=project, lane=lane, title=title,
                         due=due(days), assignee=BUSY, number=number)

        # Not mine to act on: each is OLDER than every row that returns, so
        # a read that kept one would put it first.
        someday = make("Stated someday", 40, todo, 100)
        _stated(c, org=org, task=someday, email=BUSY, disposition="SOMEDAY")
        trash = make("Stated trash", 39, todo, 101)
        _stated(c, org=org, task=trash, email=BUSY, disposition="TRASH")
        make("Unstated in backlog", 38, backlog, 102)
        closed = make("Closed lane", 37, done, 103)
        _stated(c, org=org, task=closed, email=BUSY, disposition="NEXT")
        # Mine to act on.
        reopened = make("Stated done on an open lane", 31, todo, 104)
        _stated(c, org=org, task=reopened, email=BUSY, disposition="DONE")
        waiting = make("Stated waiting", 30, todo, 105)
        _stated(c, org=org, task=waiting, email=BUSY, disposition="WAITING")
        # Seventeen plain overdue tasks, so the cap of 15 cuts the list.
        # Inserted newest deadline first, so creation order is no help.
        plain = [make(f"Plain {d}", d, todo, 200 + d) for d in range(1, 18)]
    out["expected_tasks"] = [reopened, waiting, *[plain[d - 1] for d in range(17, 4, -1)]]

    box = _account(p.admin_engine, org=org, owner=BUSY, default=True)
    threads = []
    for n in range(17):
        minutes = 10 + 10 * n
        mail = _mail(p.admin_engine, org=org, account_id=box, sender="a@x.test",
                     subject=f"Thread {n}", thread=f"th-busy-{n}-{TAG}", minutes_ago=minutes)
        threads.append((f"th-busy-{n}-{TAG}", mail, now - timedelta(minutes=minutes)))
    with p.admin_engine.begin() as c:
        for thread, mail, at in threads:
            _needs_reply(c, org=org, account=box, thread=thread, message=mail, at=at)
    # The longest wait first: thread 16 (170 minutes) down to thread 2.
    out["expected_mail"] = [f"email:{box}:th-busy-{n}-{TAG}" for n in range(16, 1, -1)]
    out["box"] = box
    return out


async def _needs(p, monkeypatch, user: UserContext) -> dict:
    # Room for a cold database. The `sources` check proves each provider ran.
    monkeypatch.setattr(shell, "PROVIDER_TIMEOUT_S", 30.0)
    monkeypatch.setattr(shell, "TOTAL_BUDGET_S", 90.0)
    async with _as_member(p, p.org_b):
        return await shell.shell_needs(limit=50, user=user)


class TestTheFeedOnARealDatabase:
    async def test_the_members_own_needs_return_in_order(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _needs(promoted, monkeypatch, _member(ME))
        assert answer["sources"] == {"tasks": "ok", "projects": "ok", "email": "ok"}
        assert [i["id"] for i in answer["items"]] == [
            f"tasks:{seeded['overdue']}",
            f"tasks:{seeded['today']}",
            f"projects:{seeded['note_mine']}",
            f"email:{seeded['mine']}:th-mine-{TAG}",
        ]
        rows = {i["kind"]: i for i in answer["items"]}
        assert rows["overdue"]["title"] == "Ship the extruder"
        assert rows["overdue"]["detail"] == f"Launch {TAG}"
        assert rows["overdue"]["href"] == f"/projects?task={seeded['overdue']}"
        assert (rows["overdue"]["act"], rows["overdue"]["act_ref"]) == ("done", seeded["overdue"])
        assert rows["notification"]["title"] == (
            f"{OTHER.split('@')[0]} assigned you #1 Ship the extruder")
        assert (rows["notification"]["act"], rows["notification"]["act_ref"]) == (
            "read", seeded["note_mine"])
        assert rows["needs_reply"]["title"] == "Quote for 40 units"
        assert rows["needs_reply"]["href"] == (
            f"/email?email={seeded['mail_mine']}&account={seeded['mine']}")
        assert answer["count"] == 4

    async def test_another_members_rows_never_return(self, promoted, seeded, monkeypatch):  # noqa: F811
        answer = await _needs(promoted, monkeypatch, _member(ME))
        assert set(answer["sources"].values()) == {"ok"}
        got = " ".join(i["id"] + " " + i["title"] for i in answer["items"])
        for key in ("their_private", "their_assigned", "elsewhere", "note_theirs",
                    "theirs", "mail_theirs"):
            assert seeded[key] not in got, key
        assert "Their" not in got and "Other org" not in got

    async def test_the_other_member_sees_their_own_and_not_mine(self, promoted, seeded, monkeypatch):  # noqa: F811
        # Non-vacuity: the rows held out above are real and readable by their
        # owner, so the negative case is not an empty table.
        answer = await _needs(promoted, monkeypatch, _member(OTHER))
        assert set(answer["sources"].values()) == {"ok"}
        got = [i["id"] for i in answer["items"]]
        assert f"tasks:{seeded['their_private']}" in got
        assert f"tasks:{seeded['their_assigned']}" in got
        assert f"projects:{seeded['note_theirs']}" in got
        assert f"email:{seeded['theirs']}:th-theirs-{TAG}" in got
        assert f"tasks:{seeded['overdue']}" not in got
        assert f"projects:{seeded['note_mine']}" not in got


class TestTheLensDueRead:
    async def test_the_oldest_deadlines_first_and_only_work_still_mine(
        self, promoted, busy, monkeypatch,  # noqa: F811
    ):
        answer = await _needs(promoted, monkeypatch, _member(BUSY))
        assert answer["sources"] == {"tasks": "ok", "projects": "ok", "email": "ok"}
        tasks = [i["id"] for i in answer["items"] if i["app"] == "tasks"]
        assert tasks == [f"tasks:{t}" for t in busy["expected_tasks"]]
        assert len(tasks) == shell.PER_APP

    async def test_the_lens_read_alone_is_bounded_and_ordered(
        self, promoted, busy,  # noqa: F811
    ):
        from gateway.routes.projects.personal import my_due_tasks

        async with _as_member(promoted, promoted.org_b):
            answer = await my_due_tasks(_member(BUSY), limit=5)
        assert [r["id"] for r in answer["rows"]] == busy["expected_tasks"][:5]
        assert [r["disposition"] for r in answer["rows"][:2]] == ["NEXT", "WAITING"]

    async def test_the_threads_that_waited_longest_and_no_more_than_the_cap(
        self, promoted, busy, monkeypatch,  # noqa: F811
    ):
        answer = await _needs(promoted, monkeypatch, _member(BUSY))
        mail = [i["id"] for i in answer["items"] if i["app"] == "email"]
        assert mail == busy["expected_mail"]
