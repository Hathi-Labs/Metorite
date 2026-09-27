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


# ── review fixes (I-1 adversarial review, 2026-09-27) ───────────────────────


def _file(*rows: dict[str, str]) -> ImportBundle:
    """A small export with any columns, written by the csv module."""
    import csv
    import io

    base = {
        "Task ID": "",
        "Task Link": "",
        "Task Name": "n",
        "Status": "to do",
        "Date Created": "1758612100413",
        "Date Created Text": "9/23/2025, 12:51:40 PM GMT+5:30",
        "Parent ID": "null",
        "List Name": "L",
        "Space Name": "S",
        "Home Location ID": "111",
    }
    cols = list(base) + sorted({k for r in rows for k in r} - set(base))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=cols, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({**base, **r})
    return clickup.parse([("t.csv", out.getvalue().encode())])


def _ms(when: dt.datetime) -> str:
    return str(int(when.timestamp() * 1000))


def test_time_spent_is_milliseconds_like_its_text_twin(bundle: ImportBundle) -> None:
    """The measured file: ``Time Spent`` 1569 reads ``0.03 m`` in its twin."""
    spent = [t.time_spent_mins for t in bundle.tasks if t.time_spent_mins is not None]
    assert spent == [0]
    b = _file({"Task ID": "a", "Time Spent": ' "5400000"', "Time Spent Text": "1.5 h"})
    assert b.tasks[0].time_spent_mins == 90


def test_summer_time_is_decided_row_by_row() -> None:
    """US Eastern: 04:00 EST in January and 04:00 EDT in July are both
    date-only. One file-wide offset got January wrong."""
    est, edt = dt.timezone(dt.timedelta(hours=-5)), dt.timezone(dt.timedelta(hours=-4))
    jan = dt.datetime(2026, 1, 15, 4, 0, tzinfo=est)
    jul = dt.datetime(2026, 7, 15, 4, 0, tzinfo=edt)
    jul_real = dt.datetime(2026, 7, 16, 9, 30, tzinfo=edt)
    b = _file(
        {
            "Task ID": "a",
            "Due Date": _ms(jan),
            "Due Date Text": "1/15/2026, 4:00:00 AM GMT-5",
            "Date Created Text": "1/2/2026, 9:00:00 AM GMT-5",
        },
        {
            "Task ID": "b",
            "Due Date": _ms(jul),
            "Due Date Text": "7/15/2026, 4:00:00 AM GMT-4",
            "Date Created Text": "7/2/2026, 9:00:00 AM GMT-4",
        },
        {
            "Task ID": "c",
            "Due Date": _ms(jul_real),
            "Due Date Text": "7/16/2026, 9:30:00 AM GMT-4",
            "Date Created Text": "7/3/2026, 9:00:00 AM GMT-4",
        },
    )
    by = {t.ref: t for t in b.tasks}
    assert by["a"].due_date == dt.date(2026, 1, 15) and by["a"].due_at is None
    assert by["b"].due_date == dt.date(2026, 7, 15) and by["b"].due_at is None
    assert by["c"].due_at == jul_real and by["c"].due_date is None
    assert "mixed_timezones" in {w.code for w in b.warnings}


def test_a_bare_gmt_is_a_zero_offset() -> None:
    """A London-winter or UTC exporter writes ``GMT`` with no sign."""
    four_utc = dt.datetime(2026, 1, 15, 4, 0, tzinfo=dt.UTC)
    b = _file(
        {
            "Task ID": "a",
            "Date Created Text": "1/2/2026, 9:00:00 AM GMT",
            "Due Date": _ms(four_utc),
            "Due Date Text": "1/15/2026, 4:00:00 AM GMT",
            "Comments": '[{"text":"hi","by":"a@example.com","date":"1/3/2026, 1:05:00 PM UTC"}]',
        }
    )
    assert b.utc_offset == dt.timedelta(0)
    assert b.tasks[0].due_date == dt.date(2026, 1, 15)
    assert b.comments[0].created_at == dt.datetime(2026, 1, 3, 13, 5, tzinfo=dt.UTC)


def test_twelve_oclock_am_and_pm() -> None:
    b = _file(
        {
            "Task ID": "a",
            "Comments": '[{"text":"x","by":"a@example.com","date":"1/3/2026, 12:05:00 AM GMT+5:30"},'
            '{"text":"y","by":"a@example.com","date":"1/3/2026, 12:05:00 PM GMT+5:30"}]',
        }
    )
    ist = dt.timezone(IST)
    assert [c.created_at for c in b.comments] == [
        dt.datetime(2026, 1, 3, 0, 5, tzinfo=ist),
        dt.datetime(2026, 1, 3, 12, 5, tzinfo=ist),
    ]


def test_a_long_parent_chain_parses_in_linear_time() -> None:
    """A 20,000-deep chain took 24 s with the old walk. It must not hold a
    gateway worker."""
    import time

    rows = [{"Task ID": "t0"}] + [
        {"Task ID": f"t{i}", "Parent ID": f"t{i - 1}"} for i in range(1, 20000)
    ]
    started = time.perf_counter()
    b = _file(*rows)
    assert time.perf_counter() - started < 10
    assert sum(1 for t in b.tasks if t.parent_ref) == 19999


def test_a_subtask_follows_its_parent_into_the_parents_list() -> None:
    b = _file(
        {"Task ID": "p", "Home Location ID": "111", "List Name": "One"},
        {"Task ID": "c", "Parent ID": "p", "Home Location ID": "222", "List Name": "Two"},
    )
    by = {t.ref: t for t in b.tasks}
    assert by["c"].container_ref == by["p"].container_ref == "list:111"
    assert {s.container_ref for s in b.statuses} == {"list:111"}
    assert "parent_other_list" in {w.code for w in b.warnings}


@pytest.mark.parametrize("cell", ["null", "2024", '{"a": 1}'])
def test_an_odd_folder_cell_never_crashes(cell: str) -> None:
    b = _file({"Task ID": "a", "Folder Name/Path": cell})
    assert len(b.tasks) == 1


def test_a_deeply_nested_json_cell_is_a_warning_not_a_crash() -> None:
    b = _file({"Task ID": "a", "Comments": "[" * 100000})
    assert b.comments == []
    assert "unreadable_comments" in {w.code for w in b.warnings}


def test_a_wide_row_is_reported() -> None:
    body = HEADER + "\n" + _row("a") + ",extra,cells\n"
    b = clickup.parse([("t.csv", body.encode())])
    assert "wide_rows" in {w.code for w in b.warnings}


def test_a_negative_epoch_reads_on_every_platform() -> None:
    b = _file({"Task ID": "a", "Date Created": "-86400000"})
    assert b.tasks[0].created_at == dt.datetime(1969, 12, 31, tzinfo=dt.UTC)


# ── verifier findings (I-1 verification, 2026-09-27) ────────────────────────


def test_every_column_of_the_real_file_has_a_stated_fate() -> None:
    """§9 I-1: every column lands in the bundle, or is skipped for a stated
    reason. None is dropped in silence."""
    from gateway.routes.projects.importer.text import decode, read_csv

    header = read_csv(decode(FIXTURE.read_bytes())[0]).header
    assert len(header) == 34
    assert set(header) <= set(clickup.COLUMNS)
    for column, fate in clickup.COLUMNS.items():
        assert fate == clickup.READ or len(fate) > 20, column


def test_task_type_lands(bundle: ImportBundle) -> None:
    assert Counter(t.task_type for t in bundle.tasks) == {"Task": 2423}


def test_the_remaining_measured_counts(bundle: ImportBundle) -> None:
    assert sum(1 for t in bundle.tasks if t.description_md) == 447
    assert sum(1 for t in bundle.tasks if t.start_date) == 15
    done_like = {"closed", "done", "completed"}
    per_list: dict[str, set[str]] = {}
    for s in bundle.statuses:
        per_list.setdefault(s.container_ref, set()).add(s.name.lower())
    assert sum(1 for names in per_list.values() if not names & done_like) == 7


def test_fact_2_subtasks_ids_disagrees_with_parent_id_on_182_tasks() -> None:
    """Why ``Parent ID`` is the truth: ``Subtasks IDs`` disagrees with it on
    182 tasks, and 166 of those leave ``Subtasks IDs`` empty."""
    import csv
    import io
    from collections import defaultdict

    first: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(FIXTURE.read_text(encoding="utf-8"))):
        first.setdefault(row["Task ID"], row)
    kids: dict[str, set[str]] = defaultdict(set)
    for tid, row in first.items():
        if row["Parent ID"] != "null":
            kids[row["Parent ID"]].add(tid)
    listed = {tid: set(filter(None, row["Subtasks IDs"].split(","))) for tid, row in first.items()}
    disagree = [tid for tid in first if listed[tid] != kids.get(tid, set())]
    assert len(disagree) == 182
    assert sum(1 for tid in disagree if not listed[tid]) == 166


@pytest.mark.parametrize("column", ["Time Spent", "Time Estimated"])
def test_a_huge_duration_is_dropped_not_a_crash(column: str) -> None:
    b = _file({"Task ID": "a", column: "9" * 25})
    assert b.tasks[0].time_spent_mins is None and b.tasks[0].estimate_mins is None
