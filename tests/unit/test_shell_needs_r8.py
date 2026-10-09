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
  mailbox, with a needs-reply thread of its own, never returns.
* **The other organization.** Its task, assigned to the member's address,
  never returns: RLS and the lens's tenant bind each hold it out.

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
    with p.admin_engine.begin() as c:
        _needs_reply(c, org=org, account=ids["mine"], thread=f"th-mine-{TAG}",
                     message=ids["mail_mine"], at=now - timedelta(minutes=90))
        _needs_reply(c, org=org, account=ids["theirs"], thread=f"th-theirs-{TAG}",
                     message=ids["mail_theirs"], at=now - timedelta(minutes=30))
    return ids


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
