"""WS-41 I-3 — what the writer creates, decided before any write.

Spec: ``project-docs/specs/project_import.md`` §5.3, §6.3, §6.6 to §6.8 · D80.
The SQL is proved by ``tests/live/live_ws41_writer.py``.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.bundle import Checklist, Comment, ImportBundle, Task
from gateway.routes.projects.importer.layout import (
    build_nodes,
    completed_estimate,
    description,
    due_instant,
    order_tasks,
    origin,
    project_statuses,
    status_ids_by_name,
)
from gateway.routes.projects.importer.plan import ImportMapping, Target, resolve_statuses

FIXTURE = pathlib.Path(__file__).parent / "import_fixtures" / "clickup_workspace.csv"
IST = dt.timedelta(hours=5, minutes=30)


@pytest.fixture(scope="module")
def bundle() -> ImportBundle:
    return clickup.parse([(FIXTURE.name, FIXTURE.read_bytes())])


# ── §5.3 the tree ───────────────────────────────────────────────────────────


def test_a_new_space_mirrors_the_source_tree(bundle: ImportBundle) -> None:
    nodes, home = build_nodes(bundle, Target())
    kinds = [n.kind for n in nodes]
    assert kinds.count("space") == 5 and kinds.count("folder") == 9 and kinds.count("project") == 48
    by_ref = {n.ref: n for n in nodes}
    seen: set[str] = set()
    for node in nodes:  # parents first
        assert node.parent_ref is None or node.parent_ref in seen
        seen.add(node.ref)
        if node.kind == "folder":
            assert by_ref[node.parent_ref].kind == "space"
        if node.kind == "project":
            assert by_ref[node.parent_ref].kind in ("space", "folder")
    assert set(home) == {c.ref for c in bundle.containers if c.kind == "project"}


def test_the_admins_name_renames_a_single_space_only(bundle: ImportBundle) -> None:
    nodes, _ = build_nodes(bundle, Target(name="From ClickUp"))
    assert "From ClickUp" not in {n.name for n in nodes}  # five spaces: names kept
    one = clickup.parse([(FIXTURE.name, _only_space(FIXTURE.read_bytes(), "Space 1"))])
    nodes, _ = build_nodes(one, Target(name="From ClickUp"))
    assert [n.name for n in nodes if n.kind == "space"] == ["From ClickUp"]


def test_an_existing_target_flattens_space_and_folder_into_one_folder(bundle: ImportBundle) -> None:
    """The grammar allows one folder between a space and a project."""
    nodes, home = build_nodes(bundle, Target(kind="existing", project_id="p"))
    assert nodes[0].existing_id == "p" and nodes[0].kind == "space"
    folders = [n for n in nodes if n.kind == "folder"]
    assert all(f.parent_ref == "target" for f in folders)
    assert all(n.parent_ref in {f.ref for f in folders} for n in nodes if n.kind == "project")
    assert any(" / " in f.name for f in folders)  # a Space / Folder pair
    assert len(home) == 48


def _only_space(raw: bytes, space: str) -> bytes:
    import csv
    import io

    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    col = rows[0].index("Space Name")
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(
        [rows[0], *[r for r in rows[1:] if r[col] == space]]
    )
    return out.getvalue().encode("utf-8")


# ── §6.3 statuses ───────────────────────────────────────────────────────────


def test_each_project_needs_its_tasks_statuses_and_gains_no_done_of_its_own(
    bundle: ImportBundle,
) -> None:
    """I-10: a List uses the space's set, so it needs only the names its
    tasks use. D79 applies once per SET, in the writer, so no Done is added
    here. Before I-10, 7 Lists each gained a Done of their own."""
    seed = [("Backlog", "backlog"), ("To do", "todo"), ("In progress", "in_progress")]
    final = resolve_statuses(bundle, ImportMapping(), [*seed, ("Done", "done")])
    sets = project_statuses(bundle, final)
    assert len(sets) == 48
    used = {name for names in sets.values() for name, _ in names}
    assert used == {"Backlog", "To do", "In progress", "Review", "On hold", "Done"}
    assert sum(1 for names in sets.values() if not any(c == "done" for _, c in names)) == 7
    for names in sets.values():
        stages = [c for _, c in names]
        assert stages == sorted(
            stages, key=["backlog", "todo", "in_progress", "done", "cancelled"].index
        )
        assert len({n.lower() for n, _ in names}) == len(names)


def test_merged_spellings_become_one_status() -> None:
    b = ImportBundle(
        source="clickup",
        containers=[],
        tasks=[
            Task(ref="a", container_ref="p", title="a", status_name="to do"),
            Task(ref="b", container_ref="p", title="b", status_name="todo"),
        ],
    )
    final = {"to do": ("To do", "todo"), "todo": ("To do", "todo")}
    assert project_statuses(b, final)["p"] == [("To do", "todo")]


def test_every_list_reads_the_union_of_the_earlier_status_maps() -> None:
    """The I-10 review, P1-a: a List this run creates has no earlier map of
    its own, so it reads every List's. A name keeps each id it had: before
    I-10 each List held a set of its own."""
    earlier = {
        "list-a": {"review": "s1", "backlog": "s2"},
        "list-b": {"review": "s3"},
    }
    assert status_ids_by_name(earlier) == {"review": {"s1", "s3"}, "backlog": {"s2"}}
    assert status_ids_by_name({}) == {}


# ── ordering and fields ─────────────────────────────────────────────────────


def test_parents_come_before_children(bundle: ImportBundle) -> None:
    placed: set[str] = set()
    for task in order_tasks(bundle):
        assert task.parent_ref is None or task.parent_ref in placed
        placed.add(task.ref)


def test_a_date_only_due_lands_at_local_noon() -> None:
    """The app writes a picked day at local noon (quickAdd.ts dueInstantForDay)."""
    t = Task(ref="a", container_ref="p", title="a", due_date=dt.date(2026, 9, 24))
    assert due_instant(t, IST) == dt.datetime(2026, 9, 24, 6, 30, tzinfo=dt.UTC)
    assert due_instant(t, None) == dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.UTC)
    timed = dt.datetime(2026, 9, 24, 9, 15, tzinfo=dt.UTC)
    assert due_instant(Task(ref="b", container_ref="p", title="b", due_at=timed), IST) == timed


def test_the_completion_estimate_is_the_latest_date_never_now() -> None:
    now = dt.datetime(2026, 9, 28, tzinfo=dt.UTC)
    created = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    t = Task(
        ref="a", container_ref="p", title="a", created_at=created, due_date=dt.date(2026, 3, 1)
    )
    comment = Comment(task_ref="a", created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC), body_md="x")
    assert completed_estimate(t, [comment], IST, now) == comment.created_at
    assert completed_estimate(t, [], IST, now) == dt.datetime(2026, 3, 1, 6, 30, tzinfo=dt.UTC)
    future_due = Task(
        ref="b", container_ref="p", title="b", created_at=created, due_date=dt.date(2027, 1, 1)
    )
    assert completed_estimate(future_due, [], IST, now) == created


def test_the_description_keeps_what_has_no_field() -> None:
    t = Task(
        ref="a",
        container_ref="p",
        title="a",
        description_md="Body",
        checklists=[Checklist(name="Launch", items=["Book", "Send"])],
        attachment_names=["plan.pdf"],
    )
    text = description(t, ["Ann Lee"], "clickup")
    assert text == (
        "Body\n\n**Launch**\n- Book\n- Send\n\nAttachments in ClickUp: plan.pdf\n\n"
        "Assigned in ClickUp to: Ann Lee"
    )
    assert "[ ]" not in text  # no tick box: the file says nothing about ticks
    assert description(Task(ref="b", container_ref="p", title="b"), [], "clickup") is None


def test_origin_always_carries_the_index_key() -> None:
    t = Task(ref="86a1", container_ref="p", title="a", url="https://app.clickup.com/t/86a1")
    o = origin(t, "clickup", "run-1", completed_at_estimated=None, assignee_names=[])
    assert o == {
        "kind": "import",
        "source": "clickup",
        "external_id": "86a1",
        "run_id": "run-1",
        "url": "https://app.clickup.com/t/86a1",
    }


# ── update mode (I-3b, owner decision 2026-09-28) ───────────────────────────

from gateway.routes.projects.importer.layout import (  # noqa: E402
    comment_key,
    merge_fields,
    snapshot,
)


def _state(**over: object) -> dict:
    base = {
        "title": "Ship it",
        "description": None,
        "status_id": "s1",
        "due_at": dt.datetime(2026, 9, 24, 6, 30, tzinfo=dt.UTC),
        "start_date": None,
        "importance": 1,
        "estimate_mins": None,
        "tags": ["a"],
        "assignees": ["ann@x.test"],
    }
    base.update(over)
    return base


def test_a_source_change_updates_an_untouched_field() -> None:
    last = snapshot(_state())
    m = merge_fields(_state(), last, _state(title="Ship it now", importance=3))
    assert m.changes == {"title": "Ship it now", "importance": 3}
    assert m.conflicts == ()
    assert m.new_snapshot["title"] == "Ship it now"


def test_a_member_edit_is_never_overwritten() -> None:
    """Both changed the title: the member's value stays, and it is a conflict."""
    last = snapshot(_state())
    m = merge_fields(_state(title="Member's title"), last, _state(title="Source title"))
    assert m.changes == {} and m.conflicts == ("title",)


def test_a_member_edit_the_source_did_not_touch_is_kept_quietly() -> None:
    last = snapshot(_state())
    m = merge_fields(_state(title="Member's title"), last, _state())
    assert m.changes == {} and m.conflicts == ()


def test_order_and_case_do_not_count_as_a_change() -> None:
    last = snapshot(_state(tags=["b", "a"]))
    m = merge_fields(_state(tags=["A", "b"]), last, _state(tags=["a", "B"]))
    assert m.changes == {} and m.conflicts == ()


def test_the_same_instant_in_another_zone_is_no_change() -> None:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    same = dt.datetime(2026, 9, 24, 12, 0, tzinfo=ist)
    assert merge_fields(_state(), snapshot(_state()), _state(due_at=same)).changes == {}


def test_without_a_snapshot_every_difference_is_a_conflict() -> None:
    m = merge_fields(_state(), None, _state(title="New"))
    assert m.changes == {} and m.conflicts == ("title",)


def test_a_frozen_field_never_changes() -> None:
    last = snapshot(_state())
    m = merge_fields(_state(), last, _state(status_id="s2"), frozen=("status_id",))
    assert m.changes == {} and m.conflicts == ()


def test_assignees_follow_the_same_rule() -> None:
    last = snapshot(_state())
    m = merge_fields(_state(), last, _state(assignees=["ann@x.test", "bo@x.test"]))
    assert m.changes == {"assignees": ["ann@x.test", "bo@x.test"]}


def test_a_comment_key_is_stable_and_distinct() -> None:
    at = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    one = Comment(task_ref="t", author_ref="a@x.test", created_at=at, body_md="Hi")
    assert comment_key(one) == comment_key(
        Comment(task_ref="t", author_ref="a@x.test", created_at=at, body_md="Hi ")
    )
    assert comment_key(one) != comment_key(
        Comment(task_ref="t", author_ref="a@x.test", created_at=at, body_md="Ho")
    )


# ── I-3b review: a mapping change is not a source change ────────────────────


def _src(**over: object) -> dict:
    base = {
        "title": "Ship it",
        "description": None,
        "status_id": "in review",
        "due_at": dt.datetime(2026, 9, 24, 6, 30, tzinfo=dt.UTC),
        "start_date": None,
        "importance": 1,
        "estimate_mins": None,
        "tags": ["a"],
        "assignees": ["name:vijay r"],
    }
    base.update(over)
    return base


def test_a_changed_people_mapping_unassigns_nobody() -> None:
    """Run 1 mapped "Vijay R" to a member; run 2 maps him to nobody. The
    source did not change, so nothing moves."""
    written = snapshot(_state(assignees=["vijay@x.test"]))
    m = merge_fields(
        _state(assignees=["vijay@x.test"]),
        written,
        _state(assignees=[]),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {} and m.conflicts == ()
    assert m.new_snapshot["assignees"] == ["vijay@x.test"]


def test_a_person_added_later_is_assigned_on_the_next_run() -> None:
    """Run 1 had no member for "Vijay R", so the task landed unassigned with
    the name in the description. Vijay is in People now. The source is the
    same, no member touched the task, so the next run fills the gap."""
    written = snapshot(_state(assignees=[], description="Assigned in ClickUp to: Vijay R"))
    m = merge_fields(
        _state(assignees=[], description="Assigned in ClickUp to: Vijay R"),
        written,
        _state(assignees=["vijay@x.test"], description=None),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {"assignees": ["vijay@x.test"], "description": None}
    assert m.conflicts == ()


def test_the_fill_never_overwrites_a_members_edit() -> None:
    written = snapshot(_state(assignees=[], description="Assigned in ClickUp to: Vijay R"))
    m = merge_fields(
        _state(assignees=["omar@x.test"], description="A member's own words"),
        written,
        _state(assignees=["vijay@x.test"], description=None),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {}


def test_the_footer_never_moves_without_the_assignees() -> None:
    """The I-9 review: a member assigned Omar by hand, so the assignees do not
    fill. The footer must keep Priya's name, or nothing on screen says she
    was assigned in ClickUp."""
    written = snapshot(_state(assignees=[], description="Assigned in ClickUp to: Priya"))
    m = merge_fields(
        _state(assignees=["omar@x.test"], description="Assigned in ClickUp to: Priya"),
        written,
        _state(assignees=["priya@x.test"], description=None),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {}
    assert m.new_snapshot["description"] == "Assigned in ClickUp to: Priya"


def test_frozen_assignees_keep_the_footer_too() -> None:
    written = snapshot(_state(assignees=[], description="Assigned in ClickUp to: Priya"))
    m = merge_fields(
        _state(assignees=[], description="Assigned in ClickUp to: Priya"),
        written,
        _state(assignees=["priya@x.test"], description=None),
        last_source=snapshot(_src()),
        incoming_source=_src(),
        frozen=("assignees",),
    )
    assert m.changes == {}


def test_taking_a_person_off_never_adds_them_to_the_footer() -> None:
    """The I-9 review's P2: Ann stays assigned (4c.3), so the footer must not
    start to say she is not."""
    written = snapshot(_state(assignees=["ann@x.test"], description=None))
    m = merge_fields(
        _state(assignees=["ann@x.test"], description=None),
        written,
        _state(assignees=[], description="Assigned in ClickUp to: Ann"),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {}


def test_the_fill_only_adds_people() -> None:
    """A mapping that swaps one member for another is not a gap."""
    written = snapshot(_state(assignees=["ann@x.test"]))
    m = merge_fields(
        _state(assignees=["ann@x.test"]),
        written,
        _state(assignees=["bo@x.test"]),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {}


def test_a_changed_status_mapping_moves_no_task() -> None:
    written = snapshot(_state(status_id="s-review"))
    m = merge_fields(
        _state(status_id="s-review"),
        written,
        _state(status_id="s-new-lane"),
        last_source=snapshot(_src()),
        incoming_source=_src(),
    )
    assert m.changes == {}


def test_a_source_change_still_lands_through_the_mapping() -> None:
    written = snapshot(_state(status_id="s-review"))
    m = merge_fields(
        _state(status_id="s-review"),
        written,
        _state(status_id="s-done"),
        last_source=snapshot(_src()),
        incoming_source=_src(status_id="done"),
    )
    assert m.changes == {"status_id": "s-done"}
    assert m.new_source["status_id"] == "done"


def test_a_frozen_field_keeps_the_last_source_value() -> None:
    """The source changed the status of a task a member moved. The change is
    not applied, and the snapshot keeps the OLD source value, so it still
    lands once the task is moved back."""
    m = merge_fields(
        _state(status_id="s-open"),
        snapshot(_state(status_id="s-open")),
        _state(status_id="s-done"),
        last_source=snapshot(_src(status_id="open")),
        incoming_source=_src(status_id="done"),
        frozen=("status_id",),
    )
    assert m.changes == {}
    assert m.new_source["status_id"] == "open"
