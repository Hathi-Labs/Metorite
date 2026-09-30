"""WS-41 I-8 — the admin's tree and columns: rename, skip, and unknown columns.

Spec: ``project-docs/specs/project_import.md`` §9 row I-8.

``plan.choose`` is the ONE place these choices apply. The dry run and the
writer both read the bundle through it, so what the admin confirmed is what is
written. These tests pin the rules, and one pins that the writer calls it.
"""

from __future__ import annotations

import pathlib

import pytest
from gateway.routes.projects.importer import clickup
from gateway.routes.projects.importer.bundle import ImportBundle
from gateway.routes.projects.importer.layout import description
from gateway.routes.projects.importer.plan import (
    ContainerChoice,
    ImportMapping,
    build_plan,
    choose,
)
from pydantic import ValidationError

HEADER = (
    "Task ID,Task Link,Task Name,Status,Date Created,Date Created Text,"
    "Parent ID,Assignees,List Name,Space Name,Home Location ID,Comments,Sprint,Points"
)
WHEN = '1758612100413,"9/23/2025, 12:51:40 PM GMT+5:30"'


def _row(tid: str, list_id: str, parent: str = "null", sprint: str = "", points: str = "") -> str:
    return (
        f"{tid},https://app.clickup.com/t/{tid},T {tid},to do,{WHEN},{parent},[],"
        f"L{list_id},S,{list_id},[],{sprint},{points}"
    )


@pytest.fixture
def bundle() -> ImportBundle:
    rows = [
        _row("a1", "1", sprint="Sprint 12", points="3"),
        _row("a2", "1", sprint="Sprint 12"),
        _row("b1", "2", sprint="Sprint 13"),
        # A subtask that lives in list 1, under a task in list 2.
        _row("a3", "1", parent="b1"),
    ]
    raw = ("\n".join([HEADER, *rows]) + "\n").encode()
    return clickup.parse([("t.csv", raw)])


def _ref(bundle: ImportBundle, name: str) -> str:
    return next(c.ref for c in bundle.containers if c.name == name)


def _plan(bundle: ImportBundle, mapping: ImportMapping) -> dict:
    return build_plan(bundle, mapping, directory={})


# ── unknown columns ─────────────────────────────────────────────────────────


def test_the_parse_keeps_what_unknown_columns_hold(bundle: ImportBundle) -> None:
    by_ref = {t.ref: t for t in bundle.tasks}
    assert by_ref["a1"].extra_columns == {"Points": "3", "Sprint": "Sprint 12"}
    assert by_ref["a3"].extra_columns == {}


def test_the_plan_lists_each_unknown_column_with_its_use(bundle: ImportBundle) -> None:
    columns = {c["name"]: c for c in _plan(bundle, ImportMapping())["columns"]}
    assert columns["Sprint"]["tasks"] == 3
    assert columns["Sprint"]["samples"] == ["Sprint 12", "Sprint 13"]
    assert columns["Points"]["tasks"] == 1
    # Leaving a column out is the default: what every run before I-8 did.
    assert {c["choice"] for c in columns.values()} == {"skip"}


def test_only_a_kept_column_reaches_the_description(bundle: ImportBundle) -> None:
    chosen = choose(bundle, ImportMapping(columns={"Sprint": "description"}))
    a1 = next(t for t in chosen.tasks if t.ref == "a1")
    assert a1.extra_columns == {"Sprint": "Sprint 12"}
    text = description(a1, [], "clickup") or ""
    assert "**More from ClickUp**" in text
    assert "- Sprint: Sprint 12" in text
    assert "Points" not in text


def test_a_left_out_column_writes_nothing(bundle: ImportBundle) -> None:
    chosen = choose(bundle, ImportMapping())
    assert all(t.extra_columns == {} for t in chosen.tasks)
    assert "More from" not in (description(chosen.tasks[0], [], "clickup") or "")


def test_choose_never_changes_the_callers_bundle(bundle: ImportBundle) -> None:
    choose(bundle, ImportMapping(containers={_ref(bundle, "L1"): ContainerChoice(skip=True)}))
    assert len(bundle.tasks) == 4
    assert bundle.tasks[0].extra_columns


# ── the tree: rename and skip ───────────────────────────────────────────────


def test_a_rename_reaches_the_container(bundle: ImportBundle) -> None:
    ref = _ref(bundle, "L1")
    mapping = ImportMapping(containers={ref: ContainerChoice(name="  Launch   plan ")})
    assert next(c.name for c in choose(bundle, mapping).containers if c.ref == ref) == "Launch plan"
    row = next(r for r in _plan(bundle, mapping)["tree"] if r["ref"] == ref)
    assert (row["name"], row["becomes"]) == ("L1", "Launch plan")


def test_a_skipped_list_takes_its_tasks_and_their_subtasks(bundle: ImportBundle) -> None:
    mapping = ImportMapping(containers={_ref(bundle, "L2"): ContainerChoice(skip=True)})
    chosen = choose(bundle, mapping)
    # b1 is in list 2. a3 lives in list 1 but is b1's subtask, so it goes too.
    assert sorted(t.ref for t in chosen.tasks) == ["a1", "a2"]
    plan = _plan(bundle, mapping)
    assert plan["skipped_by_choice"] == {"containers": 1, "tasks": 2}
    assert plan["to_write"]["tasks"] == 2
    # The tree still shows it, so the skip can be taken back.
    assert any(r["skipped"] for r in plan["tree"])
    assert plan["ready"]


def test_a_skipped_space_takes_everything_under_it(bundle: ImportBundle) -> None:
    mapping = ImportMapping(containers={_ref(bundle, "S"): ContainerChoice(skip=True)})
    plan = _plan(bundle, mapping)
    assert all(r["skipped"] for r in plan["tree"])
    assert not plan["ready"]
    assert "Everything is skipped" in " ".join(plan["errors"])


def test_a_mapping_that_names_no_container_of_the_file_is_refused(bundle: ImportBundle) -> None:
    plan = _plan(bundle, ImportMapping(containers={"nope": ContainerChoice(skip=True)}))
    assert not plan["ready"]


def test_a_blank_rename_is_refused() -> None:
    with pytest.raises(ValidationError):
        ContainerChoice(name="   ")


def test_the_writer_writes_the_chosen_bundle() -> None:
    """The writer must apply the SAME seam, after the plan. A writer that
    skipped `choose` would write the lists the admin left out."""
    src = (
        pathlib.Path(__file__).parents[2]
        / "apps/services/gateway/gateway/routes/projects/import_writer.py"
    ).read_text(encoding="utf-8")
    plan_at = src.index("plan = build_plan(bundle, mapping, **facts)")
    choose_at = src.index("bundle = choose(bundle, mapping)")
    nodes_at = src.index("_write_nodes(\n")
    assert plan_at < choose_at < nodes_at
