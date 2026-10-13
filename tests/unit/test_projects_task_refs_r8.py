"""GET /projects/search/refs on a REAL Postgres (owner report, 2026-10-13).

The chat draws a bare ``#171`` as the task pill when it is a task the member
can see (``ChatTaskRef.tsx``). This read answers that, for every number of one
answer at once. It runs the ROUTE ITSELF on the phase-4-promoted two-org
catalog of ``test_h3_rls_promotion_rehearsal.py``, as the NON-privileged app
role with RLS forced (``_as_member``), as ``test_shell_needs_r8.py`` does.

The claims:

* **Tenant (R5).** The other organization's #171 never returns, though the
  member's own org has a #171 too. RLS and the visibility clause each hold it.
* **Visibility.** A task in a project with no grant to the member never
  returns. A task in that same project that is ASSIGNED to the member does,
  because the task panel opens it (``load_visible_task``). Another member's
  private task never returns.
* **Two roots, one number.** Two granted projects that each hold a #5 return
  both rows, so the client can see that #5 is not one task.
* **Archived and triage.** An archived task returns, marked ``archived``. A
  triage-parked task never returns, as in search.
* **One read for many numbers**, junk dropped.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from gateway.routes.projects import search as route
from sqlalchemy import text

# The catalog, the app role and the binding, shared rather than copied.
# Fixtures are used by name, so the import is load-bearing.
from tests.unit.test_email_keep_separate import (  # noqa: F401
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ME = f"refs-me-{TAG}@refs.test"
OTHER = f"refs-other-{TAG}@refs.test"


def _member(email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=EffectiveAccess(role_granted=frozenset({"feature:projects"})),
    )


def _project(c, *, org: str, name: str, grant_org: bool, owner: str | None = None):
    project, lane, triage = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
        "owns_statuses, personal_owner) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), "
        ":n, 'manual', :by, true, :own)"),
        {"p": project, "o": org, "n": name, "by": owner or ME, "own": owner})
    for sid, sname, cat, pos in ((lane, "To do", "todo", 1), (triage, "Triage", "triage", 0)):
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
            "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), :n, :pos, :cat, "
            ":d)"), {"s": sid, "p": project, "n": sname, "pos": pos, "cat": cat,
                     "d": cat == "todo"})
    if grant_org:
        c.execute(text(
            "INSERT INTO pm_project_grants (project_id, subject, created_by, "
            "organization_id) VALUES (CAST(:p AS uuid), 'org', :by, CAST(:o AS uuid))"),
            {"p": project, "by": ME, "o": org})
    return project, lane, triage


def _task(c, *, org: str, project: str, lane: str, number: int, title: str,
          assignee: str | None = None, archived: bool = False) -> str:
    task = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
        "status_id, title, source, created_by, task_number, archived_at) VALUES "
        "(CAST(:t AS uuid), CAST(:o AS uuid), CAST(:p AS uuid), CAST(:p AS uuid), "
        "CAST(:s AS uuid), :ti, 'manual', :by, :n, "
        "CASE WHEN :arch THEN now() ELSE NULL END)"),
        {"t": task, "o": org, "p": project, "s": lane, "ti": title, "by": ME,
         "n": number, "arch": archived})
    if assignee:
        c.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
            "organization_id) VALUES (CAST(:t AS uuid), :a, :by, CAST(:o AS uuid))"),
            {"t": task, "a": assignee, "by": OTHER, "o": org})
    return task


@pytest.fixture(scope="module")
def seeded(promoted):  # noqa: F811
    """Seeded as the superuser, so the route's read is the first the app
    role makes."""
    p = promoted
    org, elsewhere = p.org_b, p.org_a
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for email in (ME, OTHER):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": email, "o": org})
        one, lane1, triage1 = _project(c, org=org, name=f"Chat {TAG}", grant_org=True)
        two, lane2, _ = _project(c, org=org, name=f"Ops {TAG}", grant_org=True)
        closed, lane3, _ = _project(c, org=org, name=f"Closed {TAG}", grant_org=False,
                                    owner=OTHER)
        ids["171"] = _task(c, org=org, project=one, lane=lane1, number=171,
                           title="Composer always steers")
        ids["87"] = _task(c, org=org, project=one, lane=lane1, number=87,
                          title="Card answers after restart")
        ids["5a"] = _task(c, org=org, project=one, lane=lane1, number=5, title="Five in Chat")
        ids["5b"] = _task(c, org=org, project=two, lane=lane2, number=5, title="Five in Ops")
        ids["88"] = _task(c, org=org, project=one, lane=lane1, number=88,
                          title="Finished long ago", archived=True)
        ids["89"] = _task(c, org=org, project=one, lane=triage1, number=89,
                          title="Parked in triage")
        ids["300"] = _task(c, org=org, project=closed, lane=lane3, number=300,
                           title="Their private task")
        ids["301"] = _task(c, org=org, project=closed, lane=lane3, number=301,
                           title="Asked of me", assignee=ME)
        far, flane, _ = _project(c, org=elsewhere, name=f"Elsewhere {TAG}", grant_org=True)
        ids["far171"] = _task(c, org=elsewhere, project=far, lane=flane, number=171,
                              title="Other org secret")
        ids["far999"] = _task(c, org=elsewhere, project=far, lane=flane, number=999,
                              title="Other org only")
    return ids


@asynccontextmanager
async def _scope(p, org: str):
    async with _as_member(p, org):
        yield


async def _refs(p, numbers: str, org: str | None = None) -> list[dict]:
    async with _scope(p, org or p.org_b):
        out = await route.task_refs(numbers=numbers, user=_member(ME))
    return out["rows"]


class TestTheReferenceLookup:
    async def test_the_members_tasks_return_and_the_other_org_never(self, promoted, seeded):  # noqa: F811
        rows = await _refs(promoted, "171,87,999")
        by = {(r["task_number"], r["id"]) for r in rows}
        assert by == {(171, seeded["171"]), (87, seeded["87"])}
        assert all(r["title"] != "Other org secret" for r in rows)
        hit = next(r for r in rows if r["task_number"] == 171)
        assert hit == {
            "id": seeded["171"], "title": "Composer always steers", "task_number": 171,
            "status_name": "To do", "category": "todo", "archived": False,
        }

    async def test_visibility_holds_inside_the_org(self, promoted, seeded):  # noqa: F811
        rows = await _refs(promoted, "300,301")
        # A project with no grant: its task returns only when it is mine.
        assert [r["id"] for r in rows] == [seeded["301"]]

    async def test_two_roots_with_one_number_return_both(self, promoted, seeded):  # noqa: F811
        rows = await _refs(promoted, "5")
        assert {r["id"] for r in rows} == {seeded["5a"], seeded["5b"]}

    async def test_archived_returns_marked_and_triage_never(self, promoted, seeded):  # noqa: F811
        rows = await _refs(promoted, "88,89")
        assert [(r["id"], r["archived"]) for r in rows] == [(seeded["88"], True)]

    async def test_one_read_takes_many_numbers_and_drops_junk(self, promoted, seeded):  # noqa: F811
        rows = await _refs(promoted, "#171, 87,abc,,0,87")
        assert sorted(r["task_number"] for r in rows) == [87, 171]

    async def test_the_other_org_sees_only_its_own(self, promoted, seeded):  # noqa: F811
        # The same call, bound to the other org, as a member there.
        with promoted.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', "
                "CAST(:o AS uuid))"), {"e": f"far-{TAG}@refs.test", "o": promoted.org_a})
        async with _scope(promoted, promoted.org_a):
            out = await route.task_refs(numbers="171,87",
                                        user=_member(f"far-{TAG}@refs.test"))
        assert [r["id"] for r in out["rows"]] == [seeded["far171"]]
