"""WS-41 I-1 — the ClickUp workspace-export adapter reads a real file.

Spec: ``project-docs/specs/project_import.md`` §4.1.1 (the measured facts),
§5.2 (the bundle), §5.3 (the tree) and §6.4 (priority).

The fixture ``import_fixtures/clickup_workspace.csv`` is a real workspace
export (2026-09-27), scrubbed by ``scripts/import_scrub_clickup.py``. Every
name, email, text and id in it is fake. Its shape is real, so every count
below is a count §4.1.1 states. A change here is a change to the spec.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
from collections import Counter

import pytest
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.bundle import ImportBundle

FIXTURE = pathlib.Path(__file__).parent / "import_fixtures" / "clickup_workspace.csv"
IST = dt.timedelta(hours=5, minutes=30)


@pytest.fixture(scope="module")
def bundle() -> ImportBundle:
    return clickup.parse([(FIXTURE.name, FIXTURE.read_bytes())])


# ── §4.1.1 — the counts of the measured file ────────────────────────────────


def test_rows_and_distinct_tasks(bundle: ImportBundle) -> None:
    assert bundle.rows_read == 2689
    assert len(bundle.tasks) == 2423
    assert len({t.ref for t in bundle.tasks}) == 2423


def test_fact_1_duplicate_rows_merge_into_one_task(bundle: ImportBundle) -> None:
    warnings = {w.code: w for w in bundle.warnings}
    assert warnings["duplicate_rows"].count == 266
    # The copies differ only in Assignees and Subtasks IDs, which merge.
    assert "duplicate_conflict" not in warnings


def test_the_tree_is_five_spaces_nine_folders_48_projects(bundle: ImportBundle) -> None:
    kinds = Counter(c.kind for c in bundle.containers)
    assert kinds == {"space": 5, "folder": 9, "project": 48}
    refs = {c.ref: c for c in bundle.containers}
    for c in bundle.containers:
        if c.kind == "space":
            assert c.parent_ref is None
        elif c.kind == "folder":
            assert refs[c.parent_ref].kind == "space"
        else:
            assert refs[c.parent_ref].kind in ("space", "folder")
            # Only a List has an id in the file (Home Location ID).
            assert c.source_id and c.ref == f"list:{c.source_id}"
    assert all(
        t.container_ref in refs and refs[t.container_ref].kind == "project" for t in bundle.tasks
    )


def test_facts_2_to_4_parent_id_builds_a_tree_four_deep(bundle: ImportBundle) -> None:
    by_ref = {t.ref: t for t in bundle.tasks}

    def depth(t) -> int:
        d = 0
        while t.parent_ref:
            t, d = by_ref[t.parent_ref], d + 1
        return d

    # §4.1.1 fact 3 counts the RAW file: 1,140 / 872 / 317 / 81 / 13, where a
    # task under a missing parent still sits one level down. After fact 4
    # moves the 13 orphans to the top, each orphan's chain rises one level.
    assert Counter(depth(t) for t in bundle.tasks) == {0: 1153, 1: 864, 2: 314, 3: 79, 4: 13}
    assert {w.code: w.count for w in bundle.warnings}["missing_parent"] == 13
    assert sum(1 for t in bundle.tasks if t.parent_ref) == 1270


def test_fact_5_a_subtask_shares_its_parents_list(bundle: ImportBundle) -> None:
    assert "parent_other_list" not in {w.code for w in bundle.warnings}


def test_fact_7_priority_maps_by_clickups_meaning(bundle: ImportBundle) -> None:
    assert Counter(t.importance for t in bundle.tasks) == {None: 1958, 2: 212, 1: 199, 3: 53, 0: 1}


def test_fact_8_the_file_names_its_own_time_zone(bundle: ImportBundle) -> None:
    assert bundle.utc_offset == IST


def test_fact_9_a_0400_due_is_a_date_with_no_time(bundle: ImportBundle) -> None:
    assert sum(1 for t in bundle.tasks if t.due_date) == 1075
    assert sum(1 for t in bundle.tasks if t.due_at) == 16
    assert not any(t.due_date and t.due_at for t in bundle.tasks)


def test_fact_10_comment_authors_are_emails_and_assignees_are_names(bundle: ImportBundle) -> None:
    people = {p.ref: p for p in bundle.people}
    assert all(people[c.author_ref].email for c in bundle.comments if c.author_ref)
    assert Counter(bool(p.email) for p in bundle.people) == {False: 35, True: 16}
    assert all(r in people for t in bundle.tasks for r in t.assignee_refs)


def test_fact_11_statuses_are_per_list_in_first_seen_order(bundle: ImportBundle) -> None:
    per_list: dict[str, set[str]] = {}
    for s in bundle.statuses:
        per_list.setdefault(s.container_ref, set()).add(s.name)
    assert len(per_list) == 48
    assert len({frozenset(v) for v in per_list.values()}) == 29
    assert sum(s.task_count for s in bundle.statuses) == 2423
    closed = {"Closed", "done", "completed"}
    assert sum(1 for t in bundle.tasks if t.status_name in closed) == 1647


def test_fact_12_every_comment_is_dated_from_its_text(bundle: ImportBundle) -> None:
    assert len(bundle.comments) == 93
    assert len({c.task_ref for c in bundle.comments}) == 61
    assert all(c.created_at and c.created_at.tzinfo for c in bundle.comments)


def test_checklists_and_attachments_land_without_their_urls(bundle: ImportBundle) -> None:
    lists = [c for t in bundle.tasks for c in t.checklists]
    assert len(lists) == 45
    assert sum(1 for c in lists if c.items) == 18
    assert sum(1 for t in bundle.tasks if t.attachment_names) == 15
    # §6.8 — the bundle never holds an attachment URL.
    assert "example.invalid" not in bundle.model_dump_json()


def test_every_whole_field_the_file_lacks_is_a_loss(bundle: ImportBundle) -> None:
    assert {loss.what for loss in bundle.losses} >= {
        "custom fields",
        "completion date",
        "status type",
        "checklist ticks",
        "attachment files",
    }
    # The adapter never guesses a completion date (§6.6 decides it).
    assert all(t.completed_at is None for t in bundle.tasks)


# ── small files, one rule each ──────────────────────────────────────────────

HEADER = (
    "Task ID,Task Link,Task Name,Status,Date Created,Date Created Text,Due Date,"
    "Parent ID,Subtasks IDs,Assignees,Priority,List Name,Folder Name/Path,Space Name,Home Location ID"
)


def _row(
    tid,
    *,
    parent="null",
    due="",
    prio="null",
    assignees="[]",
    folder="",
    created="1758612100413",
    extra="",
):
    text = '"9/23/2025, 12:51:40 PM GMT+5:30"'
    return (
        f"{tid},https://app.clickup.com/t/{tid},Name {tid},to do,{created},{text},{due},"
        f'{parent},,"{assignees}",{prio},L,"{folder}",S,111{extra}'
    )


def _parse(*rows: str, header: str = HEADER) -> ImportBundle:
    return clickup.parse([("t.csv", ("\n".join([header, *rows]) + "\n").encode())])


def test_a_file_that_is_not_a_clickup_export_is_refused() -> None:
    with pytest.raises(ValueError, match="missing"):
        clickup.parse([("t.csv", b"Name,Status\nA,open\n")])


def test_an_unknown_column_is_reported_not_dropped_in_silence() -> None:
    b = _parse(_row("a", extra=",x"), header=HEADER + ",Budget (custom)")
    assert {w.code: w.sample_refs for w in b.warnings}["unknown_columns"] == ["Budget (custom)"]


def test_a_real_due_time_stays_a_time() -> None:
    # 2025-09-23 10:00 IST, not 04:00 → a real time.
    b = _parse(
        _row(
            "a",
            due=str(int(dt.datetime(2025, 9, 23, 4, 30, tzinfo=dt.UTC).timestamp() * 1000)),
        )
    )
    assert b.tasks[0].due_at is not None and b.tasks[0].due_date is None


def test_a_0400_local_due_is_a_date() -> None:
    four_ist = dt.datetime(2025, 9, 24, 4, 0, tzinfo=dt.timezone(IST))
    b = _parse(_row("a", due=str(int(four_ist.timestamp() * 1000))))
    assert b.tasks[0].due_date == dt.date(2025, 9, 24) and b.tasks[0].due_at is None


def test_a_duplicate_row_unions_its_assignees() -> None:
    b = _parse(_row("a", assignees="[Ann]"), _row("a", assignees="[Bo,Ann]"))
    assert b.tasks[0].assignee_refs == ["name:ann", "name:bo"]


def test_a_duplicate_that_disagrees_is_reported() -> None:
    b = _parse(_row("a", prio="1"), _row("a", prio="2"))
    assert {w.code for w in b.warnings} >= {"duplicate_rows", "duplicate_conflict"}
    assert b.tasks[0].importance == 3


def test_a_parent_cycle_is_cut() -> None:
    b = _parse(_row("a", parent="b"), _row("b", parent="a"))
    assert "parent_cycle" in {w.code for w in b.warnings}
    assert any(t.parent_ref is None for t in b.tasks)


def test_an_unknown_priority_is_reported() -> None:
    b = _parse(_row("a", prio="9"))
    assert b.tasks[0].importance is None
    assert "unknown_priority" in {w.code for w in b.warnings}


def test_a_nested_folder_flattens_to_one_folder() -> None:
    b = _parse(_row("a", folder='[""Parent/Child""]'))
    folders = [c for c in b.containers if c.kind == "folder"]
    assert [f.name for f in folders] == ["Parent / Child"]


def test_two_files_merge_like_one() -> None:
    one = ("\n".join([HEADER, _row("a")]) + "\n").encode()
    two = ("\n".join([HEADER, _row("a"), _row("b")]) + "\n").encode()
    b = clickup.parse([("1.csv", one), ("2.csv", two)])
    assert [t.ref for t in b.tasks] == ["a", "b"]


def test_the_fixture_holds_no_real_contact_data() -> None:
    """The scrub fence: every email in the committed fixture is fake."""
    text = FIXTURE.read_text(encoding="utf-8")
    emails = set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text))
    assert emails and all(e.endswith("@example.com") for e in emails)
    assert "clickup-attachments.com" not in text
