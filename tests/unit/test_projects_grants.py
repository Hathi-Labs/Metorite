"""Projects · the grant read model — who can see what, and what they get told.

Spec: ``project-docs/specs/project_management_app.md`` §3.2, §4, §8 D-PM-3 ·
ticket WS-27a done-when 4.

This is the security fence for the whole app. Unlike the CRM — where D-CRM-3
deliberately made records org-visible to feature holders — a project is visible
only when a grant on it *or an ancestor* matches the caller, because Center
slices are the entire point of this feature.

Every scoping case is exercised as **a colleague holding only
``feature:projects``**, never as the owner: the owner holds ``*`` and therefore
``data:org:read``, so an owner-only suite would pass against a completely
unscoped route.

Hermetic: no Postgres, no network, no TestClient.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me)

COLLEAGUE = member_user("colleague@fracktal.in")
OUTSIDER = member_user("outsider@fracktal.in")
OWNER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _no_groups(monkeypatch: pytest.MonkeyPatch, *groups: str) -> None:
    """Pin the caller's group membership.

    The group lookup joins ``org_group``/``app_user``, which the fake does not
    model — those tables belong to the access system, not to this app. Patching
    the one resolver keeps the seam honest: the routes still build and bind the
    real clause, and only the membership answer is supplied.
    """
    real = pm_core.resolve_visibility

    async def _resolve(db, user):
        vis = await real(db, user)
        if vis.unrestricted:
            return vis
        return pm_core.Visibility(
            unrestricted=False, email=vis.email, groups=tuple(groups),
            # Carried through, not re-derived. Dropping it here would hand every
            # test in this file a tenant-less caller who can see nothing, which
            # looks exactly like the scoping working.
            organization_id=vis.organization_id,
        )

    for module in MODULES:
        monkeypatch.setattr(module, "resolve_visibility", _resolve, raising=False)


# ── The subject vocabulary ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "subject,visible",
    [
        ("org", True),
        ("colleague@fracktal.in", True),
        ("COLLEAGUE@Fracktal.IN", True),      # R10 — folded on both sides
        ("group:sales", True),                # the caller is in sales, below
        ("group:finance", False),             # a group they are not in
        ("someone.else@fracktal.in", False),
    ],
)
async def test_the_three_subject_kinds_decide_visibility(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
    subject: str, visible: bool,
) -> None:
    """`email | group:<slug> | org` — the shipped vocabulary, and nothing else.

    tenancy_and_visibility.md §3.2 is binding on this; a second vocabulary would
    mean two answers to "who can see this".
    """
    _no_groups(monkeypatch, "group:sales")
    project = db.seed_project(name="Pipeline", subject=subject)

    if visible:
        row = await pm_tree.get_node(str(project.id), user=COLLEAGUE)
        assert row["id"] == str(project.id)
    else:
        with pytest.raises(HTTPException) as exc:
            await pm_tree.get_node(str(project.id), user=COLLEAGUE)
        assert exc.value.status_code == 404


async def test_an_ungranted_project_is_404_never_403(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5. "No such project" and "not yours" must be the same answer, or the
    status code becomes an oracle for what exists in another department."""
    _no_groups(monkeypatch)
    project = db.seed_project(name="Finance plan", subject=None)

    with pytest.raises(HTTPException) as exc:
        await pm_tree.get_node(str(project.id), user=OUTSIDER)

    assert exc.value.status_code == 404
    assert "not found" in str(exc.value.detail).lower()
    # And the detail says nothing about grants, groups or ownership.
    assert "grant" not in str(exc.value.detail).lower()


async def test_a_grant_reaches_the_whole_subtree(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Granting a department grants everything under it (§3.2).

    Without subtree inheritance a Center would have to be re-granted every time
    somebody added a subproject, and the one they forgot would be invisible.
    """
    _no_groups(monkeypatch, "group:sales")
    root = db.seed_project(name="Sales", subject="group:sales")
    child = db.seed_project(name="Q3 campaign", parent=root.id, subject=None)
    grandchild = db.seed_project(name="Landing page", parent=child.id, subject=None)

    for node in (root, child, grandchild):
        row = await pm_tree.get_node(str(node.id), user=COLLEAGUE)
        assert row["id"] == str(node.id)


async def test_a_sibling_subtree_stays_invisible(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the previous test, and the one that would catch a
    closure that descends from the wrong seed."""
    _no_groups(monkeypatch, "group:sales")
    sales = db.seed_project(name="Sales", subject="group:sales")
    db.seed_project(name="Sales sub", parent=sales.id, subject=None)
    finance = db.seed_project(name="Finance", subject="group:finance")
    finance_child = db.seed_project(name="Payroll", parent=finance.id, subject=None)

    for node in (finance, finance_child):
        with pytest.raises(HTTPException) as exc:
            await pm_tree.get_node(str(node.id), user=COLLEAGUE)
        assert exc.value.status_code == 404


async def test_org_read_sees_every_project(db: FakeProjectsDB) -> None:
    """The People Center's full-portfolio view.

    ``data:org:read`` had **zero** consumers when D14 measured it, so
    "manager has org-wide visibility" was a name rather than a mechanism. This
    is deliberately its first — which is why granting it is an owner gate.
    """
    ungranted = db.seed_project(name="Nobody's project", subject=None)

    row = await pm_tree.get_node(str(ungranted.id), user=OWNER)

    assert row["id"] == str(ungranted.id)
    assert pm_core.ORG_READ == "data:org:read"


async def test_an_unmapped_project_is_still_reachable_by_org_read(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-PM-10's promise, made executable ahead of the importer that relies on it.

    A ClickUp Space with no Center mapping imports with **no group grant**; the
    owner must still find it in /projects, and a member of an unrelated Center
    must not. WS-27b's importer inherits this behaviour rather than inventing it.
    """
    _no_groups(monkeypatch, "group:sales")
    unmapped = db.seed_project(name="Unmapped space", subject=None)

    assert (await pm_tree.get_node(str(unmapped.id), user=OWNER))["id"] == str(
        unmapped.id,
    )
    with pytest.raises(HTTPException) as exc:
        await pm_tree.get_node(str(unmapped.id), user=COLLEAGUE)
    assert exc.value.status_code == 404


# ── Tasks: the assignment escape hatch ──────────────────────────────────────

async def test_an_assignee_sees_a_task_in_a_project_they_cannot_see(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegation across a Center boundary is normal work, not an edge case.

    Somebody in Operations is asked to do one thing for Finance. Without this
    rule that task 404s for the very person expected to do it.
    """
    _no_groups(monkeypatch)
    project = db.seed_project(name="Finance", subject=None)
    status = db.seed_status(project.id)
    task = db.seed_task(project.id, status.id, title="Reconcile Q3")
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="colleague@fracktal.in",
        assigned_by="owner@fracktal.in",
    )

    row = await pm_tasks.get_task(str(task.id), user=COLLEAGUE)

    assert row["id"] == str(task.id)
    # …and the project around it is still not theirs to browse.
    with pytest.raises(HTTPException) as exc:
        await pm_tree.get_node(str(project.id), user=COLLEAGUE)
    assert exc.value.status_code == 404


async def test_a_non_assignee_cannot_see_that_task(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_groups(monkeypatch)
    project = db.seed_project(name="Finance", subject=None)
    status = db.seed_status(project.id)
    task = db.seed_task(project.id, status.id)
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="colleague@fracktal.in",
        assigned_by="owner@fracktal.in",
    )

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.get_task(str(task.id), user=OUTSIDER)
    assert exc.value.status_code == 404


async def test_the_task_list_and_the_task_read_agree(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task must never be listable and unreadable, or the reverse.

    The two clauses are built by different functions, so this is the test that
    fails when one of them is edited alone.
    """
    _no_groups(monkeypatch, "group:sales")
    granted = db.seed_project(name="Sales", subject="group:sales")
    hidden = db.seed_project(name="Finance", subject=None)
    s1, s2 = db.seed_status(granted.id), db.seed_status(hidden.id)
    mine = db.seed_task(granted.id, s1.id, title="Visible")
    theirs = db.seed_task(hidden.id, s2.id, title="Hidden")

    listed = await pm_tasks.list_tasks(user=COLLEAGUE, page=page())
    ids = {row["id"] for row in listed.rows}

    assert str(mine.id) in ids
    assert str(theirs.id) not in ids
    with pytest.raises(HTTPException):
        await pm_tasks.get_task(str(theirs.id), user=COLLEAGUE)


async def test_assigned_to_me_ignores_grants_by_design(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6.1's read, and the reason it carries no visibility clause.

    Assignment is itself the strongest claim to a task, so filtering this by
    project grants would hide work from the person asked to do it — and the
    personal mirror built on it would then silently skip that task.
    """
    _no_groups(monkeypatch)
    project = db.seed_project(name="Finance", subject=None)
    status = db.seed_status(project.id, category="todo")
    task = db.seed_task(project.id, status.id, title="Mine")
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="colleague@fracktal.in",
        assigned_by="owner@fracktal.in",
    )

    result = await pm_me.assigned_to_me(user=COLLEAGUE, page=page())

    assert [row["id"] for row in result.rows] == [str(task.id)]


async def test_assigned_to_me_takes_the_identity_from_the_session_only(
    db: FakeProjectsDB,
) -> None:
    """R3/R4. There is no parameter that could point this at another member —
    which is what stops the personal mirror ever syncing one member's work into
    somebody else's inbox."""
    import inspect

    params = set(inspect.signature(pm_me.assigned_to_me).parameters)

    assert "user" in params
    assert not (params & {"email", "assignee", "who", "user_id"})


async def test_assigned_to_me_hides_closed_work_by_category_not_by_name(
    db: FakeProjectsDB,
) -> None:
    """A project that renamed its terminal lane must still behave correctly:
    the category is the machine-readable half, the name is the owner's."""
    project = db.seed_project(name="Ops")
    shipped = db.seed_status(project.id, name="Shipped 🚀", category="done")
    todo = db.seed_status(project.id, name="Doing", category="in_progress")
    done_task = db.seed_task(project.id, shipped.id, title="Closed")
    open_task = db.seed_task(project.id, todo.id, title="Open")
    for task in (done_task, open_task):
        db.seed(
            "pm_task_assignees", task_id=task.id,
            assignee="colleague@fracktal.in", assigned_by="owner@fracktal.in",
        )

    result = await pm_me.assigned_to_me(user=COLLEAGUE, page=page())

    assert [row["id"] for row in result.rows] == [str(open_task.id)]


# ── The grant write path ────────────────────────────────────────────────────

async def test_a_grant_can_be_created_through_the_api(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WS-14 C1's review found an acceptance that could go green with **no way
    to create a grant at all**. The same hole here would make every scoping
    test a fixture INSERT proving nothing about the API, so the creation path is
    exercised end to end and the effect is read back through the read model."""
    project = db.seed_project(name="Sales", subject=None)

    # The fake does not model ``org_group``. The route refuses a group that
    # does not exist (WS-64 OP-6b fix round 1), so the one read says it does.
    async def _groups(_db, _org):
        return [{"slug": "sales", "display_name": "Sales"}]

    monkeypatch.setattr(pm_core, "org_groups", _groups)

    created = await pm_tree.create_grant(
        str(project.id), pm_tree.GrantIn(subject="group:sales"), user=OWNER,
    )

    assert created["subject"] == "group:sales"
    assert {g["subject"] for g in db.rows("pm_project_grants")} == {"group:sales"}


@pytest.mark.parametrize(
    "subject", ["", "   ", "group:", "sales", "everyone", "agent:triage", "*"],
)
async def test_an_unknown_grant_subject_is_422(
    db: FakeProjectsDB, subject: str,
) -> None:
    """The vocabulary is closed. `agent:<name>` is deliberately refused: an
    agent acts with a member's authority via EffectiveAccess.intersect(), so a
    standing grant to one would be a second, wider authority path."""
    project = db.seed_project(name="Sales")

    with pytest.raises(HTTPException) as exc:
        await pm_tree.create_grant(
            str(project.id), pm_tree.GrantIn(subject=subject), user=OWNER,
        )
    assert exc.value.status_code == 422


async def test_an_email_grant_is_stored_folded(db: FakeProjectsDB) -> None:
    """R10 — stored folded because every read compares folded. Storing the
    caller's casing would make the grant work for exactly the spelling that
    created it."""
    project = db.seed_project(name="Sales")

    await pm_tree.create_grant(
        str(project.id), pm_tree.GrantIn(subject="Someone@Fracktal.IN"), user=OWNER,
    )

    assert db.rows("pm_project_grants")[-1]["subject"] == "someone@fracktal.in"


async def test_granting_a_project_you_cannot_see_is_404(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a caller could widen access to a department they cannot even
    read, by guessing an id."""
    _no_groups(monkeypatch)
    project = db.seed_project(name="Finance", subject=None)

    with pytest.raises(HTTPException) as exc:
        await pm_tree.create_grant(
            str(project.id), pm_tree.GrantIn(subject="colleague@fracktal.in"),
            user=COLLEAGUE,
        )
    assert exc.value.status_code == 404


async def test_creating_a_project_inside_one_you_cannot_see_is_404(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grafting a subtree onto another department would inherit that
    department's grants for it — access widened by writing, not by being
    granted."""
    _no_groups(monkeypatch)
    parent = db.seed_project(name="Finance", subject=None)

    with pytest.raises(HTTPException) as exc:
        await pm_tree.create_node(
            pm_tree.ProjectIn(name="Sneaky", parent_project_id=str(parent.id)),
            user=COLLEAGUE,
        )
    assert exc.value.status_code == 404


async def test_a_new_root_project_starts_org_visible(db: FakeProjectsDB) -> None:
    """A solo org must not be locked out of the thing it just created.

    It is an ordinary grant row, not a special case: the importer writes
    `group:<slug>` instead, and removing this one is how scoping starts.
    """
    created = await pm_tree.create_node(pm_tree.ProjectIn(name="New"), user=OWNER)

    grants = [
        g for g in db.rows("pm_project_grants")
        if str(g["project_id"]) == created["id"]
    ]
    assert [g["subject"] for g in grants] == ["org"]


async def test_a_subproject_gets_no_grant_of_its_own(db: FakeProjectsDB) -> None:
    """Subtrees inherit. A grant per level would be N rows to revoke instead of
    one, and the one missed is a leak."""
    root = await pm_tree.create_node(pm_tree.ProjectIn(name="Root"), user=OWNER)
    child = await pm_tree.create_node(
        pm_tree.ProjectIn(name="Child", parent_project_id=root["id"]), user=OWNER,
    )

    assert not [
        g for g in db.rows("pm_project_grants")
        if str(g["project_id"]) == child["id"]
    ]


# ── The clause itself ───────────────────────────────────────────────────────

async def test_the_visibility_clause_binds_both_subject_parameters(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural, because the fake is a mirror: this asserts the SUT emits the
    grant subquery and binds the caller's email *and* their groups, so a clause
    that silently stopped considering group membership fails here even if some
    behavioural case happened still to pass."""
    _no_groups(monkeypatch, "group:sales")
    db.seed_project(name="Sales", subject="group:sales")

    await pm_tree.list_nodes(user=COLLEAGUE)

    statement, params = next(
        (s, p) for s, p in db.calls if "pm_project_grants" in s
    )
    assert "vis_email" in params
    assert params["vis_groups"] == ["group:sales"]
    assert "WITH RECURSIVE" in statement


async def test_org_read_does_not_pay_for_the_group_lookup(
    db: FakeProjectsDB,
) -> None:
    """An unrestricted caller's groups cannot change the answer, so asking would
    put a join on every portfolio read."""
    db.seed_project(name="Anything")

    await pm_tree.list_nodes(user=OWNER)

    assert not db.statements_touching("org_group_member")
