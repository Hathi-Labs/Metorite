"""Projects · routes — the write paths, the hierarchy rules and the wiring.

Spec: ``project-docs/specs/project_management_app.md`` §3, §4 · ticket
WS-27a done-whens 3, 5 and 6.

Hermetic: no Postgres, no network, no TestClient. Route functions are called
directly with ``_get_db`` monkeypatched onto each SUT submodule, against the
shared ``_projects_fakes.FakeProjectsDB``.

Where a rule is decided **in SQL** the assertion is structural, against the
statement text: the fake re-implements clauses in Python and a mirror can only
agree with itself. Where a rule is decided in Python — the sort allowlist, the
Epic rule, the cycle walk, the transition's three effects — the SUT runs for
real and the assertion is a genuine check on it.
"""

from __future__ import annotations

import re

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
    DEFAULT_ORGANIZATION,
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me)
USER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


def _project_with_statuses(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Ops")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


# ── Wiring — the trap that passes every unit test ───────────────────────────

def test_every_feature_module_is_actually_mounted() -> None:
    """⚠️ A module left out of ``__init__.py``'s import list mounts **nothing**,
    while every test that calls its route function directly still passes.

    That trap is documented in ``department_centers.md`` C1, and this is the
    assertion that catches it: the mounted path set, read off the router.
    """
    from gateway.routes.projects import router

    paths = {route.path for route in router.routes}

    for expected in (
        "/projects/tree",
        "/projects/nodes",
        "/projects/nodes/{project_id}",
        "/projects/nodes/{project_id}/grants",
        "/projects/tasks",
        "/projects/tasks/{task_id}",
        "/projects/tasks/{task_id}/archive",
        "/projects/tasks/{task_id}/unarchive",
        "/projects/tasks/{task_id}/assignees",
        "/projects/tasks/{task_id}/timeline",
        "/projects/tasks/{task_id}/comments",
        "/projects/activities/{activity_id}/revert",
        "/projects/nodes/{project_id}/statuses",
        "/projects/nodes/{project_id}/views",
        "/projects/views/{view_id}/positions",
        "/projects/assigned-to-me",
        "/projects/nodes/{project_id}/fields",
        "/projects/fields/{field_id}",
        # WS-27bm S7b — fit and rebalancing. `/projects/candidates` is safe
        # from shadowing only while no `/projects/{x}` template exists.
        "/projects/tasks/{task_id}/candidates",
        "/projects/candidates",
        "/projects/analytics/rebalance",
        # WS-27bm S7d — the plan preview. A literal path, so it shadows nothing.
        "/projects/plan/preview",
    ):
        assert expected in paths, f"{expected} is not mounted"


def test_every_module_in_the_package_is_imported_by_init() -> None:
    """The general form of the trap above, which the list does not close.

    That list is a SUBSET check: it catches a module somebody remembered to add
    a path for, which is exactly the module least likely to have been forgotten.
    A new feature module left out of ``__init__.py`` mounts nothing, every test
    calling its functions directly still passes, and no assertion anywhere
    fails. This one reads the directory instead of a list, so the next module
    is covered whether or not anybody remembers.

    ``sync.py`` would be the one legitimate absence (WS-27c, blocked on WS-1)
    and is named here rather than skipped silently — when it lands, deleting its
    name from this set is how it gets mounted.
    """
    from pathlib import Path

    import gateway.routes.projects as package

    directory = Path(package.__file__).parent
    # Only modules that DECLARE routes. `filters`, `mapping`, `automation` and
    # `agent_dispatch` are services imported by the modules that do, and
    # importing them here would assert something that is not true of them.
    declares_routes = {
        f.stem for f in directory.glob("*.py")
        if f.stem not in {"__init__", "core"}
        and re.search(r"^@router\.", f.read_text(encoding="utf-8"), re.M)
    }
    assert declares_routes, "the glob found no route modules — the check is vacuous"
    imported = set(re.findall(
        r"from gateway\.routes\.projects import (\w+)",
        Path(package.__file__).read_text(encoding="utf-8"),
    ))
    deliberately_absent: set[str] = set()
    missing = declares_routes - imported - deliberately_absent
    assert not missing, (
        f"{sorted(missing)} are in the package but not imported by __init__.py — "
        f"their routes mount NOTHING while every direct-call test still passes"
    )


def test_the_package_adds_no_database_engine() -> None:
    """BO-10 / D-CRM-4. The seam exists so the next app extends it rather than
    adding engine thirteen; this is the grep, made executable."""
    from pathlib import Path

    package = Path(pm_core.__file__).parent
    for module in package.glob("*.py"):
        # The call, not the name: core.py's own docstring explains that the
        # package makes none, and a bare substring check would fail on the
        # explanation.
        assert "create_async_engine(" not in module.read_text(encoding="utf-8"), (
            f"{module.name} creates its own engine instead of using gateway.db"
        )


def test_the_router_is_behind_the_feature_gate() -> None:
    from gateway.routes.projects import router

    names = [
        getattr(d.dependency, "__qualname__", "") for d in router.dependencies
    ]
    assert any(n.startswith("require_feature_router") for n in names)


# ── Status transitions — one helper, three effects ──────────────────────────

async def test_a_status_move_writes_all_three_effects(
    db: FakeProjectsDB, events: list,
) -> None:
    """§3.8. A PATCH that writes only ``status_id`` looks correct in the UI and
    silently empties the timeline — the CRM's finding, and the shape is
    identical here."""
    project, todo, done = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    result = await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(status_id=str(done.id)), user=USER,
    )

    # 1 — the new status
    assert result["status_id"] == str(done.id)
    # 2 — completed_at stamped on the done boundary
    assert result["completed_at"] is not None
    # 3 — a status_change activity naming both ends
    changes = db.activities("status_change")
    assert len(changes) == 1
    assert changes[0]["meta"]["from"] == "To do"
    assert changes[0]["meta"]["to"] == "Done"
    assert changes[0]["meta"]["to_category"] == "done"


async def test_reopening_a_task_clears_completed_at(db: FakeProjectsDB) -> None:
    """Cleared, not left. A reopened task that keeps its completion stamp is
    done according to every report and open according to the board."""
    project, todo, done = _project_with_statuses(db)
    task = db.seed_task(project.id, done.id, completed_at=pm_core.now())

    result = await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(status_id=str(todo.id)), user=USER,
    )

    assert result["completed_at"] is None


async def test_cancelled_counts_as_closed(db: FakeProjectsDB) -> None:
    """A cancelled task is not outstanding work; leaving it open would keep it
    in every "what is still due" read forever."""
    project, todo, _ = _project_with_statuses(db)
    dropped = db.seed_status(
        project.id, name="Won't do", category="cancelled", is_default=False,
    )
    task = db.seed_task(project.id, todo.id)

    result = await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(status_id=str(dropped.id)), user=USER,
    )

    assert result["completed_at"] is not None


async def test_a_status_from_another_project_is_refused(db: FakeProjectsDB) -> None:
    """Otherwise a task lands in a lane the destination board does not render,
    and the status becomes undeletable there for a reason nobody can see."""
    project, todo, _ = _project_with_statuses(db)
    other = db.seed_project(name="Elsewhere")
    foreign = db.seed_status(other.id, name="Their lane")
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.patch_task(
            str(task.id), pm_tasks.TaskIn(status_id=str(foreign.id)), user=USER,
        )
    assert exc.value.status_code == 422


async def test_a_status_move_is_not_also_a_field_change(db: FakeProjectsDB) -> None:
    """The transition owns the record of a status move; listing status_id in
    the tracked-field set as well would write the same fact twice."""
    project, todo, done = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(status_id=str(done.id)), user=USER,
    )

    assert "status_id" not in pm_tasks._TRACKED_TASK_FIELDS
    assert not db.activities("field_change")


# ── Hierarchy ───────────────────────────────────────────────────────────────

async def test_an_epic_cannot_have_a_parent(db: FakeProjectsDB) -> None:
    """§3.4's one structural rule — it is what makes Epic the root level without
    a `level` column."""
    project, todo, _ = _project_with_statuses(db)
    epic_type = db.seed(
        "pm_task_types", project_id=project.id, name="Epic", is_system=True,
    )
    parent = db.seed_task(project.id, todo.id, title="Parent")

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(
                project_id=str(project.id), title="An epic",
                type_id=str(epic_type.id), parent_task_id=str(parent.id),
            ),
            user=USER,
        )
    assert exc.value.status_code == 422
    assert "epic" in str(exc.value.detail).lower()


async def test_a_user_type_merely_named_epic_does_not_inherit_the_rule(
    db: FakeProjectsDB,
) -> None:
    """The rule keys off ``is_system`` AND the name, so renaming the seeded Epic
    does not switch it off — and a type a user happens to call "Epic" does not
    switch it on."""
    project, todo, _ = _project_with_statuses(db)
    lookalike = db.seed(
        "pm_task_types", project_id=project.id, name="Epic", is_system=False,
    )
    parent = db.seed_task(project.id, todo.id)

    created = await pm_tasks.create_task(
        pm_tasks.TaskIn(
            project_id=str(project.id), title="Not really an epic",
            type_id=str(lookalike.id), parent_task_id=str(parent.id),
        ),
        user=USER,
    )

    assert created["parent_task_id"] == str(parent.id)


async def test_a_task_cannot_become_its_own_ancestor(db: FakeProjectsDB) -> None:
    project, todo, _ = _project_with_statuses(db)
    grandparent = db.seed_task(project.id, todo.id, title="A")
    parent = db.seed_task(
        project.id, todo.id, title="B", parent_task_id=grandparent.id,
    )

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.move_task(
            str(grandparent.id),
            pm_tasks.MoveTask(parent_task_id=str(parent.id)), user=USER,
        )
    assert exc.value.status_code == 422
    assert "subtree" in str(exc.value.detail).lower()


async def test_a_task_cannot_be_its_own_parent(db: FakeProjectsDB) -> None:
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.move_task(
            str(task.id), pm_tasks.MoveTask(parent_task_id=str(task.id)), user=USER,
        )
    assert exc.value.status_code == 422


async def test_a_project_cannot_become_its_own_ancestor(db: FakeProjectsDB) -> None:
    root = db.seed_project(name="Root")
    child = db.seed_project(name="Child", parent=root.id, subject=None)

    with pytest.raises(HTTPException) as exc:
        await pm_tree.move_node(
            str(root.id), pm_tree.MoveProject(parent_project_id=str(child.id)),
            user=USER,
        )
    assert exc.value.status_code == 422


async def test_re_parenting_is_refused_on_the_patch_route(
    db: FakeProjectsDB,
) -> None:
    """Accepting it as an ordinary field would skip the cycle check and the root
    re-stamping that ``/move`` owes."""
    root = db.seed_project(name="Root")
    other = db.seed_project(name="Other")

    with pytest.raises(HTTPException) as exc:
        await pm_tree.patch_node(
            str(root.id), pm_tree.ProjectIn(parent_project_id=str(other.id)),
            user=USER,
        )
    assert exc.value.status_code == 422
    assert "move" in str(exc.value.detail).lower()


async def test_a_subproject_inherits_the_roots_task_numbering(
    db: FakeProjectsDB,
) -> None:
    """``root_project_id`` is what scopes the counter, the statuses and the
    types — a subproject with its own sequence would give two tasks the same
    human id inside one department."""
    root, _todo, _ = _project_with_statuses(db)
    child = db.seed_project(name="Child", parent=root.id, subject=None)

    first = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(root.id), title="One"), user=USER,
    )
    second = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(child.id), title="Two"), user=USER,
    )

    assert second["root_project_id"] == str(root.id)
    assert second["task_number"] == first["task_number"] + 1


# ── The list contract ───────────────────────────────────────────────────────

async def test_an_unknown_sort_key_is_422(db: FakeProjectsDB) -> None:
    """Not a silent fall back to the default: a client sorting by a column it
    believes exists and quietly getting created_at is a bug that survives
    review."""
    with pytest.raises(HTTPException) as exc:
        await pm_tasks.list_tasks(user=USER, sort="password", page=page())
    assert exc.value.status_code == 422
    assert "password" in str(exc.value.detail)


async def test_an_unknown_sort_direction_is_422(db: FakeProjectsDB) -> None:
    with pytest.raises(HTTPException) as exc:
        await pm_tasks.list_tasks(user=USER, direction="sideways", page=page())
    assert exc.value.status_code == 422


@pytest.mark.parametrize("key", sorted(pm_core.TASK_SORTS))
async def test_every_allowlisted_sort_key_reaches_the_order_by(
    db: FakeProjectsDB, key: str,
) -> None:
    """The allowlist's values are the only identifiers interpolated into the
    ORDER BY, so this also pins that none of them is caller text. The values
    are `{dir}` templates since WS-27w; the default direction is `desc`."""
    _project_with_statuses(db)

    await pm_tasks.list_tasks(user=USER, sort=key, page=page())

    ordered = [s for s in db.statements if "ORDER BY" in s]
    expected = " ".join(pm_core.TASK_SORTS[key].format(dir="DESC").split())
    assert any(expected in s for s in ordered)


async def test_archived_tasks_are_hidden_by_default(db: FakeProjectsDB) -> None:
    project, todo, _ = _project_with_statuses(db)
    db.seed_task(project.id, todo.id, title="Live")
    db.seed_task(project.id, todo.id, title="Archived", archived_at=pm_core.now())

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert [row["title"] for row in result.rows] == ["Live"]


# ── Destructive routes report honestly ──────────────────────────────────────

async def test_deleting_a_task_reports_promotion_not_deletion(
    db: FakeProjectsDB, events: list,
) -> None:
    """Subtasks are promoted (``ON DELETE SET NULL``), not destroyed.

    Reporting them as deleted would be the N8 purge's exact defect — a count
    that reassures in the wrong direction — so the key is named
    ``subtasks_promoted`` and the test pins the word.
    """
    project, todo, _ = _project_with_statuses(db)
    parent = db.seed_task(project.id, todo.id, title="Parent")
    db.seed_task(project.id, todo.id, title="Child", parent_task_id=parent.id)

    result = await pm_tasks.delete_task(str(parent.id), user=USER)

    assert result.cascaded["subtasks_promoted"] == 1
    assert "subtasks" not in {k for k in result.cascaded if k.startswith("subtasks_de")}


async def test_deleting_a_project_counts_the_whole_subtree(
    db: FakeProjectsDB, events: list,
) -> None:
    """R7/R8, and the counts are read BEFORE the delete: afterwards the rows are
    gone and the honest number is unobtainable, which is how a destructive route
    ends up reporting zero."""
    root, todo, _ = _project_with_statuses(db)
    child = db.seed_project(name="Child", parent=root.id, subject=None)
    db.seed_task(root.id, todo.id)
    db.seed_task(child.id, todo.id, root=root.id)

    result = await pm_tree.delete_node(str(root.id), user=USER)

    assert result.cascaded["projects"] == 2   # the root and its child
    assert result.cascaded["tasks"] == 2
    assert result.cascaded["grants"] == 1


async def test_a_status_still_in_use_is_409_with_the_count(
    db: FakeProjectsDB,
) -> None:
    """Postgres would refuse this anyway, as an opaque IntegrityError 500.
    Counting first turns it into the number the caller needs."""
    project, todo, _ = _project_with_statuses(db)
    db.seed_task(project.id, todo.id)
    db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_admin.delete_status(str(todo.id), user=USER)

    assert exc.value.status_code == 409
    assert "2" in str(exc.value.detail)


async def test_deleting_a_type_reports_how_many_tasks_lost_it(
    db: FakeProjectsDB,
) -> None:
    project, todo, _ = _project_with_statuses(db)
    kind = db.seed("pm_task_types", project_id=project.id, name="Chore")
    db.seed_task(project.id, todo.id, type_id=kind.id)

    result = await pm_admin.delete_type(str(kind.id), user=USER)

    assert result["tasks_untyped"] == 1


async def test_the_system_type_cannot_be_deleted_or_renamed(
    db: FakeProjectsDB,
) -> None:
    """The Epic rule keys off ``is_system`` and the name; letting either move
    would switch the hierarchy rule off silently."""
    project, _, _ = _project_with_statuses(db)
    epic = db.seed(
        "pm_task_types", project_id=project.id, name="Epic", is_system=True,
    )

    with pytest.raises(HTTPException) as exc:
        await pm_admin.delete_type(str(epic.id), user=USER)
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await pm_admin.patch_type(
            str(epic.id), pm_admin.TypeIn(name="Initiative"), user=USER,
        )
    assert exc.value.status_code == 409


async def test_a_new_type_can_never_claim_system_status(
    db: FakeProjectsDB,
) -> None:
    """``is_system`` is written as a literal false, never from the payload — a
    caller able to set it could mint a type that claims Epic's exemption."""
    project, _, _ = _project_with_statuses(db)

    created = await pm_admin.create_type(
        str(project.id), pm_admin.TypeIn(name="Spike"), user=USER,
    )

    assert created["is_system"] is False
    assert "is_system" not in pm_admin.TypeIn.model_fields


# ── Assignees, and the event agent dispatch keys off ────────────────────────

async def test_assigning_emits_only_the_added_assignees(
    db: FakeProjectsDB, events: list,
) -> None:
    """WS-27f dispatches an agent run off ``pm.task.assigned``, so a re-assert
    of an existing assignee must not re-dispatch: the event carries the added
    set, never the whole set.

    It also carries the task's ``organization_id`` (WS-27aa) — the sink's only
    tenant source, read here inside the request's bound session. Asserted as
    the WHOLE payload rather than key-by-key, so a field silently leaving it
    fails this test.
    """
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="already@fracktal.in",
        assigned_by="owner@fracktal.in",
    )

    await pm_tasks.set_assignees(
        str(task.id),
        pm_tasks.AssigneesIn(assignees=["already@fracktal.in", "agent:triage"]),
        user=USER,
    )

    assigned = [payload for name, payload in events if name == "pm.task.assigned"]
    assert assigned == [{
        "task_id": str(task.id), "assignees": ["agent:triage"],
        "organization_id": DEFAULT_ORGANIZATION,
    }]


async def test_re_asserting_the_same_assignees_emits_nothing(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)
    db.seed(
        "pm_task_assignees", task_id=task.id, assignee="someone@fracktal.in",
        assigned_by="owner@fracktal.in",
    )

    await pm_tasks.set_assignees(
        str(task.id), pm_tasks.AssigneesIn(assignees=["someone@fracktal.in"]),
        user=USER,
    )

    assert not [name for name, _ in events if name == "pm.task.assigned"]


async def test_an_agent_is_an_ordinary_assignee(db: FakeProjectsDB, events: list) -> None:
    """D-PM-4 — one vocabulary for both species, which is what makes handing
    work to an agent the SAME action as handing it to a colleague."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    result = await pm_tasks.set_assignees(
        str(task.id), pm_tasks.AssigneesIn(assignees=["agent:task-manager"]),
        user=USER,
    )

    assert result["assignees"] == ["agent:task-manager"]
    assert db.rows("pm_task_assignees")[0]["assignee"] == "agent:task-manager"


async def test_assignees_are_folded_on_write(db: FakeProjectsDB, events: list) -> None:
    """R10 — every read compares folded, so storing the caller's casing would
    make the assignment work for exactly the spelling that created it."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    await pm_tasks.set_assignees(
        str(task.id), pm_tasks.AssigneesIn(assignees=["Someone@Fracktal.IN"]),
        user=USER,
    )

    assert db.rows("pm_task_assignees")[0]["assignee"] == "someone@fracktal.in"


# ── Events (§6.3 — the automation seam) ─────────────────────────────────────

async def test_the_mutations_announce_themselves(
    db: FakeProjectsDB, events: list,
) -> None:
    """WS-27f's automation binding is built on these, and a write that silently
    stopped emitting would break it with every route test still green."""
    project, _todo, done = _project_with_statuses(db)

    created = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(project.id), title="Watch me"), user=USER,
    )
    await pm_tasks.patch_task(
        created["id"], pm_tasks.TaskIn(status_id=str(done.id)), user=USER,
    )

    names = [name for name, _ in events]
    assert "pm.task.created" in names
    assert "pm.task.status_changed" in names


async def test_a_status_change_event_carries_the_category(
    db: FakeProjectsDB, events: list,
) -> None:
    """An automation gating on "is it done" must not have to resolve a status
    name against a project's own configuration to find out."""
    project, todo, done = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(status_id=str(done.id)), user=USER,
    )

    payload = next(p for name, p in events if name == "pm.task.status_changed")
    assert payload["to_category"] == "done"


async def test_an_event_failure_never_fails_the_write(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's edit already succeeded; the alternative is a 500 on a
    successful mutation. A workflow that cannot run must never fail the write
    that triggered it."""
    project, _, _ = _project_with_statuses(db)

    async def _explode(source, event_type, payload, **kwargs):
        raise RuntimeError("bus down")

    import sys
    from types import ModuleType

    module = ModuleType("ingestion.event_hooks")
    module.emit_event = _explode
    monkeypatch.setitem(sys.modules, "ingestion.event_hooks", module)

    created = await pm_tasks.create_task(
        pm_tasks.TaskIn(project_id=str(project.id), title="Still created"),
        user=USER,
    )

    assert created["title"] == "Still created"


# ── Comments and the timeline ───────────────────────────────────────────────

async def test_a_comment_lands_on_the_shared_spine(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    await pm_activities.add_comment(
        str(task.id), pm_activities.CommentIn(body="Looks good"), user=USER,
    )

    assert [a["type"] for a in db.activities()] == ["comment"]
    assert db.activities("comment")[0]["body"] == "Looks good"


async def test_another_members_comment_cannot_be_edited(
    db: FakeProjectsDB,
) -> None:
    """404, not 403 — "someone else's comment" and "no such comment" are one
    answer. There is deliberately no admin override."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)
    theirs = db.seed(
        "pm_activities", task_id=task.id, type="comment", body="Theirs",
        created_by="colleague@fracktal.in",
    )

    with pytest.raises(HTTPException) as exc:
        await pm_activities.edit_comment(
            str(theirs.id), pm_activities.CommentIn(body="Rewritten"), user=USER,
        )
    assert exc.value.status_code == 404
    assert db.rows("pm_activities")[0]["body"] == "Theirs"


async def test_deleting_a_comment_clears_its_words(db: FakeProjectsDB) -> None:
    """"Deleted" must mean the words are gone, not merely filtered out of one
    read path."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)
    mine = db.seed(
        "pm_activities", task_id=task.id, type="comment", body="Mine",
        created_by="owner@fracktal.in",
    )

    await pm_activities.delete_comment(str(mine.id), user=USER)

    row = db.rows("pm_activities")[0]
    assert row["deleted_at"] is not None
    assert row["body"] is None


async def test_a_system_activity_is_not_deletable_as_a_comment(
    db: FakeProjectsDB,
) -> None:
    """A route that accepted any type would let a client erase a status change
    nobody performed."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)
    event = db.seed(
        "pm_activities", task_id=task.id, type="status_change",
        created_by="owner@fracktal.in",
    )

    with pytest.raises(HTTPException) as exc:
        await pm_activities.delete_comment(str(event.id), user=USER)
    assert exc.value.status_code == 404


async def test_an_edit_records_a_revertible_diff(db: FakeProjectsDB, events: list) -> None:
    """§3.8's field_change shape — both ends of every change, which is what
    makes revert possible without a second audit store."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id, title="Before")

    await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(title="After"), user=USER,
    )

    changes = db.activities("field_change")[0]["meta"]["changes"]
    assert changes == [{"field": "title", "old": "Before", "new": "After"}]


async def test_revert_restores_the_old_value_and_logs_the_undo(
    db: FakeProjectsDB, events: list,
) -> None:
    """The revert is a normal edit: it writes a fresh activity rather than
    erasing the one it undoes, so history does not appear never to have
    contained the change."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id, title="Before")
    await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(title="After"), user=USER,
    )
    change = db.activities("field_change")[0]

    result = await pm_activities.revert_change(str(change["id"]), user=USER)

    assert result["reverted"] == ["title"]
    assert db.rows("pm_tasks")[0]["title"] == "Before"
    assert len(db.activities("field_change")) == 2


async def test_revert_refuses_structural_fields(db: FakeProjectsDB) -> None:
    """Undoing a move by writing a bare column would skip the root re-stamping
    and status re-point that /move owes. Reverting a move is a move."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)
    change = db.seed(
        "pm_activities", task_id=task.id, type="field_change",
        created_by="owner@fracktal.in",
        meta={"changes": [{"field": "project_id", "old": "x", "new": "y"}]},
    )

    with pytest.raises(HTTPException) as exc:
        await pm_activities.revert_change(str(change.id), user=USER)
    assert exc.value.status_code == 422
    assert "project_id" in str(exc.value.detail)


# ── Views and manual order ──────────────────────────────────────────────────

async def test_a_new_root_project_gets_a_working_board(db: FakeProjectsDB) -> None:
    """Statuses, types and two views, so a project renders on its first visit
    rather than showing an empty status picker."""
    created = await pm_tree.create_node(pm_tree.ProjectIn(name="Fresh"), user=USER)

    statuses = [
        s for s in db.rows("pm_task_statuses") if str(s["project_id"]) == created["id"]
    ]
    views = [v for v in db.rows("pm_views") if str(v["project_id"]) == created["id"]]
    types = [t for t in db.rows("pm_task_types") if str(t["project_id"]) == created["id"]]

    assert {s["category"] for s in statuses} == {
        "backlog", "todo", "in_progress", "done",
    }
    assert sum(1 for s in statuses if s["is_default"]) == 1
    assert {v["view_type"] for v in views} == {"list", "board"}
    assert "Epic" in {t["name"] for t in types}
    assert not [t for t in types if t["name"] == "Subtask"]


async def test_positions_are_upserted_in_one_request(db: FakeProjectsDB) -> None:
    """The first drag into an unordered group materialises the whole group; as
    N requests it would leave the order half-applied if one failed."""
    project, todo, _ = _project_with_statuses(db)
    view = db.seed(
        "pm_views", project_id=project.id, name="Board", view_type="board",
        created_by="owner@fracktal.in",
    )
    first = db.seed_task(project.id, todo.id, title="One")
    second = db.seed_task(project.id, todo.id, title="Two")

    result = await pm_views.set_positions(
        str(view.id),
        pm_views.PositionsIn(positions=[
            pm_views.PositionIn(task_id=str(first.id), position=100.0,
                                group_key=str(todo.id)),
            pm_views.PositionIn(task_id=str(second.id), position=200.0,
                                group_key=str(todo.id)),
        ]),
        user=USER,
    )

    assert result["written"] == 2
    assert len(db.rows("pm_view_task_positions")) == 2


async def test_an_empty_position_list_is_a_no_op_and_never_wipes(
    db: FakeProjectsDB,
) -> None:
    """`positions: []` must change nothing — it is a real caller, not a curiosity.

    WS-27bd's context-menu "Change status" writes the status through
    `patchTask` and then calls this endpoint with an EMPTY list, meaning
    "change the status, keep the manual order". Today that is safe because the
    handler's only write is an upsert *inside* the loop, so zero entries means
    zero statements — there is no DELETE and no anti-join anywhere in it.

    Nothing enforced that. A later "make positions authoritative" change —
    `min_length=1`, or deleting rows absent from the payload — would turn this
    caller into a 422 or into a silent wipe of every manual position on the
    board, and the only symptom would be somebody's ordering quietly reverting.
    This is the fence for that, so the refactor fails here instead of in the UI.
    """
    project, todo, _ = _project_with_statuses(db)
    view = db.seed(
        "pm_views", project_id=project.id, name="Board", view_type="board",
        created_by="owner@fracktal.in",
    )
    kept = db.seed_task(project.id, todo.id, title="Already ordered")
    await pm_views.set_positions(
        str(view.id),
        pm_views.PositionsIn(positions=[
            pm_views.PositionIn(task_id=str(kept.id), position=100.0,
                                group_key=str(todo.id)),
        ]),
        user=USER,
    )
    before = db.rows("pm_view_task_positions")
    assert len(before) == 1

    result = await pm_views.set_positions(
        str(view.id), pm_views.PositionsIn(positions=[]), user=USER,
    )

    assert result["written"] == 0
    assert result["skipped"] == 0
    # The survival assertion is the point: `written == 0` alone would still pass
    # if the handler had deleted everything first and then written nothing.
    assert db.rows("pm_view_task_positions") == before


async def test_a_repositioned_task_updates_rather_than_duplicates(
    db: FakeProjectsDB,
) -> None:
    """UNIQUE(view_id, task_id) — one row per task per view, or the same task
    renders twice on the board."""
    project, todo, _ = _project_with_statuses(db)
    view = db.seed(
        "pm_views", project_id=project.id, name="Board", view_type="board",
        created_by="owner@fracktal.in",
    )
    task = db.seed_task(project.id, todo.id)

    for position in (100.0, 150.0):
        await pm_views.set_positions(
            str(view.id),
            pm_views.PositionsIn(positions=[
                pm_views.PositionIn(task_id=str(task.id), position=position),
            ]),
            user=USER,
        )

    rows = db.rows("pm_view_task_positions")
    assert len(rows) == 1
    assert rows[0]["position"] == 150.0


async def test_a_bulk_position_write_is_bounded(db: FakeProjectsDB) -> None:
    """A single request that can write the whole table is a denial-of-service
    surface rather than a feature."""
    project, todo, _ = _project_with_statuses(db)
    view = db.seed(
        "pm_views", project_id=project.id, name="Board", created_by="o@f.in",
    )

    with pytest.raises(HTTPException) as exc:
        await pm_views.set_positions(
            str(view.id),
            pm_views.PositionsIn(positions=[
                pm_views.PositionIn(task_id=str(todo.id), position=float(i))
                for i in range(pm_views.MAX_POSITIONS + 1)
            ]),
            user=USER,
        )
    assert exc.value.status_code == 422


# ── Input validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", ["", "   "])
async def test_a_task_needs_a_real_title(db: FakeProjectsDB, title: str) -> None:
    project, _, _ = _project_with_statuses(db)

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(project_id=str(project.id), title=title), user=USER,
        )
    assert exc.value.status_code == 422


async def test_an_unknown_source_is_422(db: FakeProjectsDB) -> None:
    """Mirrored from the CHECK constraint so a bad value is a 422 at the
    boundary rather than an IntegrityError 500 from the driver."""
    project, _, _ = _project_with_statuses(db)

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(project_id=str(project.id), title="X", source="telepathy"),
            user=USER,
        )
    assert exc.value.status_code == 422


async def test_a_malformed_instant_names_the_column(db: FakeProjectsDB) -> None:
    """One choke point coerces every write, so the failure names the column
    rather than surfacing as a driver error the caller cannot act on."""
    project, _, _ = _project_with_statuses(db)

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(project_id=str(project.id), title="X", due_at="soon"),
            user=USER,
        )
    assert exc.value.status_code == 422
    assert "due_at" in str(exc.value.detail)


async def test_an_unknown_activity_type_is_refused(db: FakeProjectsDB) -> None:
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_core.record_activity(
            db, activity_type="forged", created_by="x@y.z", task_id=str(task.id),
        )
    assert exc.value.status_code == 422


async def test_an_activity_with_no_target_is_refused(db: FakeProjectsDB) -> None:
    """The migration's CHECK would refuse it too, as an IntegrityError 500; this
    names the caller's mistake instead."""
    with pytest.raises(HTTPException) as exc:
        await pm_core.record_activity(
            db, activity_type="system", created_by="x@y.z",
        )
    assert exc.value.status_code == 422


async def test_the_actor_comes_from_the_session_not_the_body(
    db: FakeProjectsDB, events: list,
) -> None:
    """R3/R4 — ``created_by`` is what the timeline attributes an action to, and
    a client-supplied one would let a caller write history in another name."""
    project, todo, _ = _project_with_statuses(db)
    task = db.seed_task(project.id, todo.id)

    await pm_activities.add_comment(
        str(task.id), pm_activities.CommentIn(body="hi"), user=USER,
    )

    assert db.activities("comment")[0]["created_by"] == "owner@fracktal.in"
    assert "created_by" not in pm_activities.CommentIn.model_fields
    assert "created_by" not in pm_tasks.TaskIn.model_fields


# ── The seed colours and the shared UI vocabulary ────────────────────────────
#
# `_SEED_STATUSES` writes a `color` on every new root project, and in the UI a
# stored colour OUTRANKS the category (that is what lets an owner choose one).
# So a seed that disagrees with the shared category→hue map silently overrides
# the shared vocabulary on every project nobody has customised — and /projects
# goes back to looking different from /tasks, which is the whole thing WS-27ad
# set out to fix.
#
# It is not hypothetical: the seed shipped `To do` blue and `In progress` amber
# against a category map of gray and blue, and two of the four default lanes
# rendered differently in the two apps.
#
# This reads the TypeScript rather than mirroring it, because a mirror is
# exactly the thing that goes stale and then lies.


def test_seed_status_colours_match_the_shared_vocabulary() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "workbench" / "control_plane" / "src" / "lib" / "statusAccent.ts"
    ).read_text(encoding="utf-8")  # Windows defaults to cp1252 and crashes.

    block = re.search(
        r"const CATEGORY_HUES: Record<string, AccentHue> = \{(.*?)\}", source, re.S,
    )
    assert block, "CATEGORY_HUES not found — did statusAccent.ts move or rename it?"
    hues = dict(re.findall(r"(\w+):\s*\"(\w+)\"", block.group(1)))
    assert hues, "CATEGORY_HUES parsed empty"

    mismatched = {
        name: (colour, hues[category])
        for name, colour, _position, category, _default in pm_tree._SEED_STATUSES
        if category in hues and colour != hues[category]
    }
    assert not mismatched, (
        "seeded colours disagree with CATEGORY_HUES in statusAccent.ts "
        f"(lane: seeded vs derived): {mismatched}. A stored colour outranks the "
        "category, so these seeds would override the shared vocabulary."
    )


def test_every_seeded_category_is_one_the_shared_vocabulary_colours() -> None:
    """The companion direction: a seeded category the UI cannot colour renders
    grey, which is the pre-WS-27ad failure wearing a different hat."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "workbench" / "control_plane" / "src" / "lib" / "statusAccent.ts"
    ).read_text(encoding="utf-8")
    block = re.search(
        r"const CATEGORY_HUES: Record<string, AccentHue> = \{(.*?)\}", source, re.S,
    )
    assert block
    hues = dict(re.findall(r"(\w+):\s*\"(\w+)\"", block.group(1)))

    uncoloured = [
        category
        for _name, _colour, _position, category, _default in pm_tree._SEED_STATUSES
        if category not in hues
    ]
    assert not uncoloured, f"seeded categories with no hue: {uncoloured}"


async def test_positions_refuse_a_task_the_caller_cannot_see(
    db: FakeProjectsDB,
) -> None:
    """A visible VIEW does not make an arbitrary task_id writable.

    ⚠️ Found 2026-08-10 while reading `makeplane/plane` as a reference: their
    GHSA-4w5x-wc9w-f47x scoped a cycle-issue join write by issue id alone, so a
    caller could re-point another tenant's rows. Ours was narrower — the PK is
    `(view_id, task_id)` and the view IS checked — but it still wrote join rows
    for tasks the caller cannot read, and every sibling endpoint in this package
    resolves visibility per task. An authorisation rule one endpoint skips is
    not a rule.

    The unreadable task is skipped, not raised: a drag can carry a stale
    selection, and failing the whole reorder is worse than ordering the rest.
    `skipped` is in the response so a client can tell a partial write from a
    complete one.
    """
    # ⚠️ `member_user`, NOT the default `projects_user()`: the latter holds `*`,
    # which includes `data:org:read` — "every project in the org, grants
    # ignored". Written with it, this test passes whether or not the fix
    # exists, which is the trap `_projects_fakes.member_user`'s own docstring
    # names: a suite that proves only the owner can see everything is true of
    # an unscoped route too. Measured: with `projects_user()` this asserts
    # written==2.
    caller = member_user("colleague@fracktal.in")
    project = db.seed_project(name="Ops", subject=caller.email)
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    view = db.seed(
        "pm_views", project_id=project.id, name="Board", view_type="board",
        created_by=caller.email,
    )
    mine = db.seed_task(project.id, todo.id, title="Mine")

    # A project nobody granted the caller — the shape every 404 test uses.
    theirs_project = db.seed_project(name="Not mine", subject=None)
    theirs_status = db.seed_status(
        theirs_project.id, name="To do", category="todo", is_default=True,
    )
    theirs = db.seed_task(theirs_project.id, theirs_status.id, title="Theirs")

    result = await pm_views.set_positions(
        str(view.id),
        pm_views.PositionsIn(positions=[
            pm_views.PositionIn(task_id=str(mine.id), position=100.0,
                                group_key=str(todo.id)),
            pm_views.PositionIn(task_id=str(theirs.id), position=200.0,
                                group_key=str(todo.id)),
        ]),
        user=caller,
    )

    assert result["written"] == 1
    assert result["skipped"] == 1
    written = db.rows("pm_view_task_positions")
    assert len(written) == 1
    assert str(written[0]["task_id"]) == str(mine.id)
