"""Subtasks S5 (D-PM-38 decisions 2 and 4) — the lifecycle cascades.

Spec: ``project-docs/specs/project_management_app.md`` §12.9 (the rule) and
§11.41 (the build record).

Hermetic. Each helper the cascade leans on is faked here, so each test pins
one decision: which tasks a cascade touches, which status each one gets, what
the door reports, and what it refuses. The same rules run against a REAL
Postgres in ``tests/live/live_subtask_lifecycle.py`` (R8).

* **Complete** (decision 2) — ``include_subtasks`` false leaves every child
  open. True closes each open descendant into the first Done status of its
  OWN set, and a recurring child still spawns its next instance.
* **Archive** (decision 4) — at depth 3, only what the actor can see.
* **Move** (decision 4) — at depth 3, each child remapped into the
  destination's set by the one seam. A hidden child refuses the whole move.
* **Counts** — every door reports ``subtasks_completed``,
  ``subtasks_archived`` or ``subtasks_moved``.
* **The chat tools** keep the server default, and say that the option exists.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.routes.projects import bulk, cascade, core, move, personal, tasks

# ── A tree, three levels deep, over two projects ───────────────────────────
#
#   parent (proj-a)
#   ├── child-1 (proj-a, open)
#   │   └── grand-1 (proj-b, open)       ← another project, another set
#   │       └── great-1 (proj-b, open)   ← depth 3
#   ├── child-2 (proj-a, already done)
#   ├── child-3 (proj-a, archived)
#   └── child-4 (proj-a, hidden from the actor)


def _row(tid, project, *, depth, category="todo", visible=True,
         archived=None, number=None):
    return SimpleNamespace(
        id=tid, project_id=project, root_project_id=f"root-{project}",
        status_id=f"lane-{category}-{project}", status_category=category,
        status_name=category.title(), visible=visible, archived_at=archived,
        subtree_depth=depth, task_number=number, type_id=None,
        custom_fields={}, completed_at=None, parent_task_id=None,
    )


def _tree():
    return [
        _row("child-1", "proj-a", depth=1, number=11),
        _row("child-2", "proj-a", depth=1, category="done", number=12),
        _row("child-3", "proj-a", depth=1, archived="2026-09-01", number=13),
        _row("child-4", "proj-a", depth=1, visible=False, number=14),
        _row("grand-1", "proj-b", depth=2, number=21),
        _row("great-1", "proj-b", depth=3, number=31),
    ]


VIS = SimpleNamespace(params={}, unrestricted=True)


@pytest.fixture
def subtree(monkeypatch):
    """`load_subtree` answers the tree above, and records the root asked."""
    asked: list[str] = []

    async def load_subtree(db, vis, task_id):
        asked.append(task_id)
        return _tree()

    monkeypatch.setattr(cascade, "load_subtree", load_subtree)
    monkeypatch.setattr(tasks, "load_subtree", load_subtree)
    return asked


# ── Complete ────────────────────────────────────────────────────────────────


@pytest.fixture
def done_lanes(monkeypatch):
    """Each project's set owns its OWN first Done status."""
    calls: list[tuple] = []

    async def status_owner_id(db, project_id):
        return f"owner-{project_id}"

    async def load_default_status(db, owner, category=None):
        calls.append((owner, category))
        return SimpleNamespace(id=f"first-done-of-{owner}", category="done")

    async def apply_status_transition(db, task, status_id, *, created_by,
                                      automation=False):
        calls.append(("moved", task.id, status_id, created_by))
        return {
            "row": task, "recurred_to": None,
            "from": SimpleNamespace(name="To do"),
            "to": SimpleNamespace(name="Done", category="done"),
        }

    monkeypatch.setattr(cascade, "status_owner_id", status_owner_id)
    monkeypatch.setattr(cascade, "load_default_status", load_default_status)
    monkeypatch.setattr(cascade, "apply_status_transition", apply_status_transition)
    return calls


async def test_complete_cascade_closes_each_open_descendant_in_its_own_set(
    subtree, done_lanes,
):
    got = await cascade.complete_subtree(None, VIS, "parent", by="me@x.in")
    # Every depth. Not the closed one, the archived one or the hidden one.
    assert got.ids == ["child-1", "grand-1", "great-1"]
    moved = [c for c in done_lanes if c[0] == "moved"]
    assert moved == [
        ("moved", "child-1", "first-done-of-owner-proj-a", "me@x.in"),
        ("moved", "grand-1", "first-done-of-owner-proj-b", "me@x.in"),
        ("moved", "great-1", "first-done-of-owner-proj-b", "me@x.in"),
    ]
    # The ONE resolver, asked for the `done` category of each child's set.
    asked = [c for c in done_lanes if c[0] != "moved"]
    assert asked == [
        ("owner-proj-a", "done"), ("owner-proj-b", "done"),
        ("owner-proj-b", "done"),
    ]
    # The Undo record: the exact status before, and the one the cascade set.
    assert got.changes[1] == {
        "task_id": "grand-1", "from_status_id": "lane-todo-proj-b",
        "to_status_id": "first-done-of-owner-proj-b", "recurred_to": None,
    }
    assert [e[0] for e in got.events] == ["pm.task.status_changed"] * 3


async def test_a_set_with_no_done_status_refuses_the_whole_cascade(
    subtree, done_lanes, monkeypatch,
):
    async def no_done(db, owner, category=None):
        if owner == "owner-proj-b":
            raise HTTPException(status_code=422, detail="no done")
        return SimpleNamespace(id=f"first-done-of-{owner}", category="done")

    monkeypatch.setattr(cascade, "load_default_status", no_done)
    with pytest.raises(HTTPException) as err:
        await cascade.complete_subtree(None, VIS, "parent", by="me@x.in")
    assert err.value.status_code == 409
    assert "#21" in err.value.detail


async def test_recurrence_still_spawns_through_the_cascade(subtree, monkeypatch):
    """The cascade goes through the REAL `apply_status_transition`, so a
    recurring child advances its series exactly as a single Mark done does."""
    spawned: list[str] = []

    async def status_owner_id(db, project_id):
        return f"owner-{project_id}"

    async def load_default_status(db, owner, category=None):
        return SimpleNamespace(id=f"done-{owner}", category="done")

    async def require_row(db, table, rid, what):
        return SimpleNamespace(id=rid, name="To do", category="todo")

    async def require_status_in_project(db, owner, status_id):
        return SimpleNamespace(id=status_id, name="Done", category="done")

    async def update_row(db, table, rid, values):
        return SimpleNamespace(id=rid, **values)

    async def record_activity(db, **kwargs):
        return None

    async def spawn_successor(db, row, *, actor_id):
        spawned.append(str(row.id))
        return f"next-{row.id}"

    from gateway.routes.projects import recurrence

    monkeypatch.setattr(cascade, "status_owner_id", status_owner_id)
    monkeypatch.setattr(cascade, "load_default_status", load_default_status)
    monkeypatch.setattr(core, "status_owner_id", status_owner_id)
    monkeypatch.setattr(core, "require_row", require_row)
    monkeypatch.setattr(core, "require_status_in_project", require_status_in_project)
    monkeypatch.setattr(core, "update_row", update_row)
    monkeypatch.setattr(core, "record_activity", record_activity)
    monkeypatch.setattr(recurrence, "spawn_successor", spawn_successor)

    got = await cascade.complete_subtree(None, VIS, "parent", by="me@x.in")
    assert spawned == ["child-1", "grand-1", "great-1"]
    assert [c["recurred_to"] for c in got.changes] == [
        "next-child-1", "next-grand-1", "next-great-1",
    ]


# ── The complete door ───────────────────────────────────────────────────────


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def _user():
    from acb_auth import UserContext, UserRole, build_access
    return UserContext(email="me@x.in", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:projects"]))


PARENT = SimpleNamespace(
    id="parent", project_id="proj-a", root_project_id="root-proj-a",
    status_id="lane-todo-proj-a", archived_at=None, task_number=1,
    parent_task_id=None, type_id=None, custom_fields={}, completed_at=None,
    title="Parent",
)


@pytest.fixture
def complete_door(monkeypatch):
    """`POST /tasks/{id}/complete` with the session and the helpers faked."""
    seen: dict = {"cascade": [], "events": []}

    async def resolve_visibility(db, user):
        return VIS

    async def load_visible_task(db, vis, task_id):
        return PARENT

    async def complete_for_member(db, task, email):
        return {"row": task}

    async def complete_subtree(db, vis, task_id, *, by):
        seen["cascade"].append(task_id)
        return cascade.Cascade(
            ids=["child-1", "grand-1"],
            changes=[{"task_id": "child-1"}, {"task_id": "grand-1"}],
            events=[("pm.task.status_changed", {"task_id": "child-1"})],
        )

    async def emit_all(events):
        seen["events"].extend(events)

    monkeypatch.setattr(personal, "_tenant_session", _Session)
    monkeypatch.setattr(personal, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(personal, "load_visible_task", load_visible_task)
    monkeypatch.setattr(personal, "complete_for_member", complete_for_member)
    monkeypatch.setattr(personal, "complete_subtree", complete_subtree)
    monkeypatch.setattr(personal, "emit_all", emit_all)
    monkeypatch.setattr(personal, "row_to_dict", lambda row, model: {"id": row.id})
    return seen


async def test_complete_without_the_flag_leaves_the_children_open(complete_door):
    got = await personal.complete_task("parent", user=_user())
    assert complete_door["cascade"] == []
    assert "subtasks_completed" not in got


async def test_complete_by_default_does_not_cascade(complete_door):
    """The server default is FALSE: the chat tool and old callers send
    nothing, and they must keep completing ONE task."""
    default = inspect.signature(personal.complete_task).parameters[
        "include_subtasks"].default
    assert default is False


async def test_a_refused_cascade_never_completes_the_parent(
    complete_door, monkeypatch,
):
    """`complete_for_member` emits the parent's event inside the
    transaction. So the cascade runs FIRST: a 409 from it must not follow an
    announced completion (review of #493)."""
    completed: list[str] = []

    async def refuse(db, vis, task_id, *, by):
        raise HTTPException(status_code=409, detail="no Done status")

    async def complete_for_member(db, task, email):
        completed.append(task.id)
        return {"row": task}

    monkeypatch.setattr(personal, "complete_subtree", refuse)
    monkeypatch.setattr(personal, "complete_for_member", complete_for_member)
    with pytest.raises(HTTPException):
        await personal.complete_task("parent", user=_user(), include_subtasks=True)
    assert completed == []


async def test_complete_with_the_flag_cascades_and_reports(complete_door):
    got = await personal.complete_task(
        "parent", user=_user(), include_subtasks=True)
    assert complete_door["cascade"] == ["parent"]
    assert got["subtasks_completed"] == 2
    assert got["subtask_changes"] == [{"task_id": "child-1"}, {"task_id": "grand-1"}]
    assert complete_door["events"] == [
        ("pm.task.status_changed", {"task_id": "child-1"})]


# ── The PATCH door: only a move INTO `done` cascades ───────────────────────


@pytest.fixture
def patch_door(monkeypatch):
    seen: dict = {"cascade": []}
    state = {"category": "done"}

    async def resolve_visibility(db, user):
        return VIS

    async def load_visible_task(db, vis, task_id):
        return PARENT

    async def apply_status_transition(db, task, status_id, *, created_by):
        return {
            "row": task,
            "from": SimpleNamespace(name="To do"),
            "to": SimpleNamespace(name="X", category=state["category"]),
        }

    async def complete_subtree(db, vis, task_id, *, by):
        seen["cascade"].append(task_id)
        return cascade.Cascade(ids=["child-1"], changes=[{"task_id": "child-1"}])

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tasks, "_tenant_session", _Session)
    monkeypatch.setattr(tasks, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(tasks, "load_visible_task", load_visible_task)
    monkeypatch.setattr(tasks, "require_precondition", lambda *a: None)
    monkeypatch.setattr(tasks, "apply_status_transition", apply_status_transition)
    monkeypatch.setattr(tasks, "complete_subtree", complete_subtree)
    monkeypatch.setattr(tasks, "ensure_watchers", noop)
    monkeypatch.setattr(tasks, "emit", noop)
    monkeypatch.setattr(tasks, "emit_all", noop)
    monkeypatch.setattr(tasks, "row_to_dict", lambda row, model: {"id": row.id})
    return seen, state


async def test_patch_into_done_with_the_flag_cascades(patch_door):
    seen, _ = patch_door
    got = await tasks.patch_task(
        "parent", core.TaskIn(status_id="lane-done"), user=_user(),
        if_match=None, include_subtasks=True)
    assert seen["cascade"] == ["parent"]
    assert got["subtasks_completed"] == 1


async def test_patch_into_cancelled_never_cascades(patch_door):
    seen, state = patch_door
    state["category"] = "cancelled"
    got = await tasks.patch_task(
        "parent", core.TaskIn(status_id="lane-cancelled"), user=_user(),
        if_match=None, include_subtasks=True)
    assert seen["cascade"] == []
    assert got["subtasks_completed"] == 0


async def test_patch_into_done_without_the_flag_does_not_cascade(patch_door):
    seen, _ = patch_door
    got = await tasks.patch_task(
        "parent", core.TaskIn(status_id="lane-done"), user=_user(),
        if_match=None)
    assert seen["cascade"] == []
    assert "subtasks_completed" not in got


# ── Archive, at depth 3 ─────────────────────────────────────────────────────


@pytest.fixture
def shelf(monkeypatch):
    written: list[tuple] = []

    async def update_row(db, table, rid, values):
        written.append((rid, sorted(values)))
        return SimpleNamespace(id=rid, **values)

    async def record_activity(db, **kwargs):
        written.append(("activity", kwargs["task_id"], kwargs["meta"]))

    monkeypatch.setattr(cascade, "update_row", update_row)
    monkeypatch.setattr(cascade, "record_activity", record_activity)
    return written


async def test_archive_cascade_shelves_every_visible_descendant(subtree, shelf):
    got = await cascade.archive_subtree(None, VIS, "parent", by="me@x.in")
    # The done child is shelved too (archive is any status). The archived
    # one is already there, and the hidden one is not the actor's to touch.
    assert got.ids == ["child-1", "child-2", "grand-1", "great-1"]
    assert [w[0] for w in shelf if w[0] != "activity"] == got.ids
    assert ("activity", "great-1", {"cascade_from": "parent"}) in shelf
    assert [e[0] for e in got.events] == ["pm.task.archived"] * 4


@pytest.fixture
def archive_door(monkeypatch):
    seen: dict = {"cascade": []}

    async def resolve_visibility(db, user):
        return VIS

    async def load_visible_task(db, vis, task_id):
        return PARENT

    async def require_row(db, table, rid, what):
        return SimpleNamespace(name="To do", category="todo")

    async def update_row(db, table, rid, values):
        return SimpleNamespace(**{**vars(PARENT), **values})

    async def archive_subtree(db, vis, task_id, *, by):
        seen["cascade"].append(task_id)
        return cascade.Cascade(ids=["child-1", "grand-1", "great-1"])

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tasks, "_tenant_session", _Session)
    monkeypatch.setattr(tasks, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(tasks, "load_visible_task", load_visible_task)
    monkeypatch.setattr(tasks, "require_row", require_row)
    monkeypatch.setattr(tasks, "update_row", update_row)
    monkeypatch.setattr(tasks, "record_activity", noop)
    monkeypatch.setattr(tasks, "archive_subtree", archive_subtree)
    monkeypatch.setattr(tasks, "emit", noop)
    monkeypatch.setattr(tasks, "emit_all", noop)
    monkeypatch.setattr(tasks, "row_to_dict", lambda row, model: {"id": row.id})
    return seen


async def test_archive_door_reports_the_count_and_the_ids(archive_door):
    got = await tasks.archive_task("parent", user=_user(), include_subtasks=True)
    assert archive_door["cascade"] == ["parent"]
    assert got["subtasks_archived"] == 3
    assert got["subtask_ids"] == ["child-1", "grand-1", "great-1"]


async def test_archive_door_without_the_flag_shelves_one_task(archive_door):
    got = await tasks.archive_task("parent", user=_user())
    assert archive_door["cascade"] == []
    assert "subtasks_archived" not in got


def test_unarchive_takes_no_subtask_option():
    """Unarchive does not cascade: a child can be on the shelf for its own
    reason. The client's Undo restores the exact ids an archive reported."""
    assert "include_subtasks" not in inspect.signature(
        tasks.unarchive_task).parameters


# ── Move, at depth 3 ────────────────────────────────────────────────────────


@pytest.fixture
def mover(monkeypatch, subtree):
    """`move_task_in` with every helper faked. Each project owns its set, so
    every task that changes project is remapped into the destination's."""
    written: list[tuple] = []

    async def load_visible_project(db, vis, pid):
        return SimpleNamespace(id=pid, kind="project")

    async def noop(*args, **kwargs):
        return None

    async def root_project_id(db, pid):
        return f"root-{pid}"

    async def status_owner_id(db, pid):
        return f"owner-{pid}"

    async def remap_one_status(db, *, status_id, owner_id):
        return f"{status_id}->{owner_id}"

    async def lane_category(db, status_id):
        return "todo"

    async def cross_root(db, task, new_root, answers, field_map=None):
        return {"root_project_id": new_root}, {}

    async def update_row(db, table, rid, values):
        written.append((rid, values))
        return SimpleNamespace(id=rid, **values)

    monkeypatch.setattr(tasks, "load_visible_project", load_visible_project)
    monkeypatch.setattr(tasks, "assert_move_keeps_privacy", noop)
    monkeypatch.setattr(cascade, "assert_move_keeps_privacy", noop)
    monkeypatch.setattr(tasks, "root_project_id", root_project_id)
    monkeypatch.setattr(tasks, "status_owner_id", status_owner_id)
    monkeypatch.setattr(tasks, "remap_one_status", remap_one_status)
    monkeypatch.setattr(tasks, "lane_category", lane_category)
    monkeypatch.setattr(tasks, "_cross_root_values", cross_root)
    monkeypatch.setattr(tasks, "update_row", update_row)
    monkeypatch.setattr(tasks, "touch_task", noop)
    monkeypatch.setattr(tasks, "record_activity", noop)
    monkeypatch.setattr(tasks, "record_drops", noop)
    monkeypatch.setattr(tasks, "row_to_dict", lambda row, model: {"id": row.id})
    return written


def _visible_tree(monkeypatch):
    async def load_subtree(db, vis, task_id):
        return [r for r in _tree() if r.visible]

    monkeypatch.setattr(tasks, "load_subtree", load_subtree)


async def test_move_cascade_carries_the_subtree_and_remaps_each_set(
    mover, monkeypatch,
):
    _visible_tree(monkeypatch)
    got = await tasks.move_task_in(
        None, VIS, PARENT,
        tasks.MoveTask(project_id="proj-z", include_subtasks=True), by="me@x.in")
    moved = {rid: values for rid, values in mover}
    # The parent and every visible descendant, at every depth.
    assert set(moved) == {
        "parent", "child-1", "child-2", "child-3", "grand-1", "great-1"}
    assert all(v["project_id"] == "proj-z" for v in moved.values())
    # Each status is remapped from ITS OWN lane into the destination's set.
    assert moved["grand-1"]["status_id"] == "lane-todo-proj-b->owner-proj-z"
    assert moved["child-2"]["status_id"] == "lane-done-proj-a->owner-proj-z"
    assert got["subtasks_moved"] == 5
    assert got["subtask_ids"] == [
        "child-1", "child-2", "child-3", "grand-1", "great-1"]


async def test_move_without_the_flag_moves_one_task(mover, monkeypatch):
    _visible_tree(monkeypatch)
    got = await tasks.move_task_in(
        None, VIS, PARENT, tasks.MoveTask(project_id="proj-z"), by="me@x.in")
    assert [rid for rid, _ in mover] == ["parent"]
    assert "subtasks_moved" not in got


async def test_a_hidden_descendant_refuses_the_whole_move(mover):
    """`subtree` answers the tree with child-4 hidden: 409, nothing written."""
    with pytest.raises(HTTPException) as err:
        await tasks.move_task_in(
            None, VIS, PARENT,
            tasks.MoveTask(project_id="proj-z", include_subtasks=True),
            by="me@x.in")
    assert err.value.status_code == 409
    assert err.value.detail.startswith("1 subtask of this task is hidden")
    assert mover == []


async def test_the_single_move_names_a_d62_subtask_before_any_write(
    mover, monkeypatch,
):
    _visible_tree(monkeypatch)

    async def guard(db, task, dest_id):
        if task.id == "grand-1":
            raise HTTPException(status_code=422, detail="personal")

    monkeypatch.setattr(cascade, "assert_move_keeps_privacy", guard)
    with pytest.raises(HTTPException) as err:
        await tasks.move_task_in(
            None, VIS, PARENT,
            tasks.MoveTask(project_id="proj-z", include_subtasks=True),
            by="me@x.in")
    assert err.value.status_code == 422
    # The SUBTASK is named, not the parent.
    assert err.value.detail.startswith("Subtask #21 ")
    assert mover == []


def test_the_refusal_names_the_count():
    assert cascade.hidden_refusal(3).startswith("3 subtasks of this task are")
    rows = [SimpleNamespace(visible=False), SimpleNamespace(visible=True)]
    with pytest.raises(HTTPException) as err:
        cascade.movable_subtree(rows)
    assert err.value.status_code == 409


async def test_the_bulk_move_refuses_a_hidden_descendant_before_any_write(
    monkeypatch,
):
    written: list = []

    async def resolve_visibility(db, user):
        return VIS

    async def plan(db, vis, payload):
        return {
            "tasks": [], "source_project_id": "a",
            "destination_project_id": "z", "required_missing": [],
            "drops": {}, "destination_statuses": [], "statuses": [],
            "types": [], "crosses_status_set": False, "crosses_root": False,
            "descendants": [_row("x", "a", depth=1, visible=False)],
            "subtree_maps": {},
            "subtasks": {"count": 0, "hidden": 1, "refused": []},
        }

    async def move_task_in(*args, **kwargs):
        written.append(args)

    monkeypatch.setattr(move, "_tenant_session", _Session)
    monkeypatch.setattr(move, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(move, "_plan", plan)
    monkeypatch.setattr(move, "move_task_in", move_task_in)
    with pytest.raises(HTTPException) as err:
        await move.move_tasks(
            move.MoveIn(task_ids=["p"], destination_project_id="z",
                        include_subtasks=True),
            user=_user())
    assert err.value.status_code == 409
    assert written == []


async def test_the_bulk_move_carries_each_descendant_through_the_seam(
    monkeypatch,
):
    carried: list = []

    async def resolve_visibility(db, user):
        return VIS

    async def plan(db, vis, payload):
        return {
            "tasks": [], "source_project_id": "a",
            "destination_project_id": "z", "required_missing": [],
            "drops": {}, "destination_statuses": [], "statuses": [],
            "types": [], "crosses_status_set": False, "crosses_root": False,
            "descendants": [_row("c1", "a", depth=1), _row("c2", "b", depth=2),
                            _row("c3", "z", depth=2)],
            # The map the card showed, per source root (review of #493).
            "subtree_maps": {"root-a": {"po": "customer_po"}},
            "subtasks": {"count": 3, "hidden": 0, "refused": []},
        }

    async def move_task_in(db, vis, task, payload, *, by, field_map=None):
        carried.append((task.id, payload.project_id, payload.include_subtasks,
                        field_map))
        return {}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(move, "_tenant_session", _Session)
    monkeypatch.setattr(move, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(move, "_plan", plan)
    monkeypatch.setattr(move, "move_task_in", move_task_in)
    monkeypatch.setattr(move, "emit", noop)
    got = await move.move_tasks(
        move.MoveIn(task_ids=["p"], destination_project_id="z",
                    include_subtasks=True),
        user=_user())
    # c3 is already there. Each one alone, never recursing a second time, and
    # each with the map the card showed for ITS root.
    assert carried == [
        ("c1", "z", False, {"po": "customer_po"}),
        ("c2", "z", False, None),
    ]
    assert got["subtasks_moved"] == 2
    assert got["subtask_ids"] == ["c1", "c2"]


# ── The move plan shows what the subtasks cost (review of #493) ───────────


@pytest.fixture
def planner(monkeypatch):
    """`move._plan` with its reads faked. Three roots: the selection's (a),
    another (b) and the destination (z), which REQUIRES `po`."""
    parent = SimpleNamespace(
        id="p", project_id="proj-a", root_project_id="root-proj-a", status_id="s",
        task_number=1, type_id=None, tags=[], custom_fields={"po": "9"},
    )
    kids = [
        _row("k1", "proj-a", depth=1),
        _row("k2", "proj-b", depth=2),
        _row("k3", "proj-a", depth=1, visible=False),
    ]
    kids[0].custom_fields = {"po": "1"}
    kids[1].custom_fields = {"sev": "high"}
    kids[0].tags = []
    kids[1].tags = []
    kids[2].tags = []
    text_field = lambda key, name, required=False: {  # noqa: E731
        "field_key": key, "name": name, "field_type": "text",
        "options": None, "required": required,
    }
    defs = {
        "root-proj-a": [text_field("po", "PO")],
        "root-proj-b": [text_field("sev", "Severity")],
        "root-z": [text_field("po", "PO", required=True)],
    }

    async def selection(db, vis, ids):
        return [parent]

    async def destination(db, vis, pid):
        return SimpleNamespace(id="proj-z", kind="project")

    async def noop(*args, **kwargs):
        return None

    async def root_project_id(db, pid):
        return "root-z"

    async def status_owner_id(db, pid):
        return f"owner-{pid}"

    async def load_definitions(db, root):
        return defs[root]

    async def load_subtree(db, vis, task_id):
        return kids

    async def status_proposal(db, ids, owner):
        return []

    async def type_proposal(db, ids, root):
        return []

    class _DB:
        async def execute(self, stmt, params=None):
            class _R:
                def fetchall(self):
                    return []
            return _R()

    monkeypatch.setattr(move, "_selection", selection)
    monkeypatch.setattr(move, "_destination", destination)
    monkeypatch.setattr(move, "assert_move_keeps_privacy", noop)
    monkeypatch.setattr(cascade, "assert_move_keeps_privacy", noop)
    monkeypatch.setattr(move, "root_project_id", root_project_id)
    monkeypatch.setattr(move, "status_owner_id", status_owner_id)
    monkeypatch.setattr(move, "load_definitions", load_definitions)
    monkeypatch.setattr(move, "load_subtree", load_subtree)
    monkeypatch.setattr(move, "_status_proposal", status_proposal)
    monkeypatch.setattr(move, "_type_proposal", type_proposal)
    return _DB()


async def test_the_plan_with_subtasks_shows_their_drops_and_required_fields(
    planner,
):
    plan = await move._plan(planner, VIS, move.MoveIn(
        task_ids=["p"], destination_project_id="proj-z", include_subtasks=True))
    # k2's Severity has no home in the destination: shown, and so gated by
    # accept_drops like any other loss (D-PM-29).
    assert [d["task_id"] for d in plan["drops"]["sev"]] == ["k2"]
    # k2 carries no PO, which the destination requires.
    assert plan["required_missing"] == ["PO"]
    assert plan["subtasks"] == {"count": 2, "hidden": 1, "refused": []}
    # The map the apply hands each subtask, by ITS root.
    assert set(plan["subtree_maps"]) == {"root-proj-a", "root-proj-b"}


async def test_the_plan_without_subtasks_shows_only_the_selection(planner):
    plan = await move._plan(planner, VIS, move.MoveIn(
        task_ids=["p"], destination_project_id="proj-z"))
    assert plan["drops"] == {}
    assert plan["required_missing"] == []
    # The box still needs the count. The HIDDEN count appears only when the
    # member asks to take the subtasks, because only then does it change
    # the act (review of #493).
    assert plan["subtasks"] == {"count": 2, "hidden": 0, "refused": []}


async def test_the_plan_names_a_subtask_that_d62_refuses(planner, monkeypatch):
    """A carried subtask in a team project cannot move into a personal one.
    The preview names IT, so the card holds Move and says why."""
    async def guard(db, task, dest_id):
        if task.id == "k2":
            raise HTTPException(status_code=422, detail="personal")

    monkeypatch.setattr(cascade, "assert_move_keeps_privacy", guard)
    plan = await move._plan(planner, VIS, move.MoveIn(
        task_ids=["p"], destination_project_id="proj-z", include_subtasks=True))
    refused = plan["subtasks"]["refused"]
    assert [r["task_id"] for r in refused] == ["k2"]
    assert refused[0]["reason"].startswith("Subtask ")
    assert "Untick" in refused[0]["reason"]
    # Without the box, nothing is carried and nothing is refused.
    plan = await move._plan(planner, VIS, move.MoveIn(
        task_ids=["p"], destination_project_id="proj-z"))
    assert plan["subtasks"]["refused"] == []


async def test_the_bulk_apply_refuses_a_d62_subtask_before_any_write(monkeypatch):
    written: list = []

    async def resolve_visibility(db, user):
        return VIS

    async def plan(db, vis, payload):
        return {
            "tasks": [], "source_project_id": "a",
            "destination_project_id": "z", "required_missing": [],
            "drops": {}, "destination_statuses": [], "statuses": [],
            "types": [], "crosses_status_set": False, "crosses_root": False,
            "descendants": [_row("c1", "a", depth=1, number=41)],
            "subtree_maps": {},
            "subtasks": {"count": 1, "hidden": 0, "refused": [
                {"task_id": "c1", "ref": "#41", "reason": "Subtask #41 is …"}]},
        }

    async def move_task_in(*args, **kwargs):
        written.append(args)

    monkeypatch.setattr(move, "_tenant_session", _Session)
    monkeypatch.setattr(move, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(move, "_plan", plan)
    monkeypatch.setattr(move, "move_task_in", move_task_in)
    with pytest.raises(HTTPException) as err:
        await move.move_tasks(
            move.MoveIn(task_ids=["p"], destination_project_id="z",
                        include_subtasks=True),
            user=_user())
    assert err.value.status_code == 422
    assert err.value.detail.startswith("Subtask #41")
    assert written == []


# ── The bulk door: one choice for the whole batch ──────────────────────────


@pytest.fixture
def bulk_door(monkeypatch):
    seen: dict = {"completed": [], "archived": []}

    async def resolve_visibility(db, user):
        return VIS

    async def load_visible_task(db, vis, task_id):
        return SimpleNamespace(**{**vars(PARENT), "id": task_id})

    async def apply_to_one(db, task, **kwargs):
        return {"task_id": str(task.id), "changed": ["status"]}, []

    async def act_on_one(db, task, action, *, by, personal=None):
        return "applied", "archived"

    async def current_category(db, task_id):
        return "done"

    async def complete_subtree(db, vis, task_id, *, by):
        seen["completed"].append(task_id)
        return cascade.Cascade(
            ids=[f"{task_id}-kid"], changes=[{"task_id": f"{task_id}-kid"}])

    async def archive_subtree(db, vis, task_id, *, by):
        seen["archived"].append(task_id)
        return cascade.Cascade(ids=[f"{task_id}-kid", f"{task_id}-kid2"])

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(bulk, "_tenant_session", _Session)
    monkeypatch.setattr(bulk, "resolve_visibility", resolve_visibility)
    monkeypatch.setattr(bulk, "load_visible_task", load_visible_task)
    monkeypatch.setattr(bulk, "_apply_to_one", apply_to_one)
    monkeypatch.setattr(bulk, "_act_on_one", act_on_one)
    monkeypatch.setattr(bulk, "current_category", current_category)
    monkeypatch.setattr(bulk, "complete_subtree", complete_subtree)
    monkeypatch.setattr(bulk, "archive_subtree", archive_subtree)
    monkeypatch.setattr(bulk, "emit", noop)
    monkeypatch.setattr(bulk, "emit_all", noop)
    return seen


async def test_bulk_done_with_the_flag_completes_each_subtree(bulk_door):
    got = await bulk.bulk_edit(
        bulk.BulkIn(task_ids=["a", "b"], patch={"status": "Done"},
                    include_subtasks=True),
        user=_user())
    assert bulk_door["completed"] == ["a", "b"]
    assert got["subtasks_completed"] == 2
    assert got["subtask_changes"] == [{"task_id": "a-kid"}, {"task_id": "b-kid"}]


async def test_bulk_done_without_the_flag_touches_only_the_selection(bulk_door):
    got = await bulk.bulk_edit(
        bulk.BulkIn(task_ids=["a", "b"], patch={"status": "Done"}),
        user=_user())
    assert bulk_door["completed"] == []
    assert "subtasks_completed" not in got


async def test_bulk_archive_with_the_flag_shelves_each_subtree(bulk_door):
    got = await bulk.bulk_edit(
        bulk.BulkIn(task_ids=["a"], action="archive", include_subtasks=True),
        user=_user())
    assert bulk_door["archived"] == ["a"]
    assert got["subtasks_archived"] == 2
    assert got["subtask_ids"] == ["a-kid", "a-kid2"]


# ── The walk itself ─────────────────────────────────────────────────────────


def test_the_walk_is_bounded_and_keeps_hidden_rows_as_a_column():
    sql = cascade._SUBTREE_SQL
    assert ":max_depth" in sql, "a corrupt cycle must end, not spin"
    # The visibility clause is a COLUMN, never the WHERE: a move must SEE the
    # hidden child to refuse it.
    assert "AS visible" in sql
    assert "WHERE {visible}" not in sql


async def test_load_subtree_binds_the_callers_visibility():
    seen: dict = {}

    class _DB:
        async def execute(self, stmt, params=None):
            seen["sql"], seen["params"] = str(stmt), params

            class _R:
                def fetchall(self):
                    return []
            return _R()

    vis = SimpleNamespace(params={"vis_org": "org-1"}, unrestricted=True)
    await cascade.load_subtree(_DB(), vis, "parent")
    assert "CAST(:vis_org AS uuid)" in seen["sql"]
    assert seen["params"]["vis_org"] == "org-1"
    assert seen["params"]["root"] == "parent"


# ── The chat tools keep the default, and say the option exists ─────────────


def _tool_sources():
    from skill_my_tasks import core as my_tasks
    from skill_projects import guarded, writes
    return [
        my_tasks.my_tasks_complete, writes.complete, writes.move_task,
        guarded.archive_task,
    ]


@pytest.mark.parametrize("tool", _tool_sources(), ids=lambda t: t.__name__)
def test_a_chat_tool_names_include_subtasks_and_does_not_send_it(tool):
    assert "include_subtasks" in (tool.__doc__ or "")
    body = inspect.getsource(tool).split('"""')[-1]
    assert "include_subtasks" not in body
