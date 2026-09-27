"""WS-41 I-2 — the dry run: what an import would write, and what the admin decides.

Spec: ``project-docs/specs/project_import.md`` §3.1, §6.2 (people), §6.3
(statuses), §6.6 (completion dates), §6.9 (work that exists) · D80.

``build_plan`` is pure. It is handed the directory and the existing ids, and
it has no database to write to, which is how §8's "the plan writes nothing"
holds by construction. ``test_the_plan_module_cannot_reach_a_database`` keeps
it that way.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from gateway.routes.projects.importer import clickup, plan
from gateway.routes.projects.importer.bundle import ImportBundle
from gateway.routes.projects.importer.plan import (
    ImportMapping,
    StatusChoice,
    Target,
    build_plan,
    propose_category,
)

FIXTURE = pathlib.Path(__file__).parent / "import_fixtures" / "clickup_workspace.csv"


@pytest.fixture(scope="module")
def bundle() -> ImportBundle:
    return clickup.parse([(FIXTURE.name, FIXTURE.read_bytes())])


def _small(*rows: str) -> ImportBundle:
    header = (
        "Task ID,Task Link,Task Name,Status,Date Created,Date Created Text,"
        "Parent ID,Assignees,List Name,Space Name,Home Location ID,Comments"
    )
    return clickup.parse([("t.csv", ("\n".join([header, *rows]) + "\n").encode())])


def _row(
    tid: str, status: str = "to do", assignees: str = "[]", list_id: str = "1", comments: str = "[]"
) -> str:
    text = '"9/23/2025, 12:51:40 PM GMT+5:30"'
    return f'{tid},https://app.clickup.com/t/{tid},T {tid},{status},1758612100413,{text},null,"{assignees}",L{list_id},S,{list_id},"{comments}"'


# ── §6.3 proposals ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Closed", "done"),
        ("done", "done"),
        ("completed", "done"),
        ("Shipped", "done"),
        ("to do", "todo"),
        ("todo", "todo"),
        ("Open", "todo"),
        ("backlog", "backlog"),
        ("on hold", "backlog"),
        ("in process", "in_progress"),
        ("in progress", "in_progress"),
        ("review", "in_progress"),
        ("Cancelled", "cancelled"),
        ("won't do", "cancelled"),
        ("Undone work", "in_progress"),
    ],
)
def test_a_status_name_proposes_a_stage(name: str, category: str) -> None:
    assert propose_category(name) == category


def test_every_status_of_the_real_file_proposes_the_right_stage(bundle: ImportBundle) -> None:
    rows = {r["name"]: r["proposed"] for r in build_plan(bundle, ImportMapping(), {})["statuses"]}
    assert rows == {
        "Closed": "done",
        "done": "done",
        "completed": "done",
        "backlog": "backlog",
        "on hold": "backlog",
        "to do": "todo",
        "todo": "todo",
        "in process": "in_progress",
        "in progress": "in_progress",
        "review": "in_progress",
    }


# ── the counts of the real file ─────────────────────────────────────────────


def test_the_real_file_plans_with_its_measured_counts(bundle: ImportBundle) -> None:
    p = build_plan(bundle, ImportMapping(), {})
    assert p["summary"]["tasks"] == 2423
    assert p["closed_tasks"] == 1647
    # §6.6 — every closed task needs an estimated completion date.
    assert p["completed_at_estimated"] == 1647
    # D79 — 7 Lists show no done-like status, so each gets a Done status.
    assert p["done_status_added"] == 7
    assert p["to_write"] == {"tasks": 2423, "comments": 93}
    assert p["utc_offset_minutes"] == 330
    assert p["ready"] is True and p["errors"] == []
    assert sum(1 for n in p["tree"] if n["kind"] == "project") == 48


def test_mapping_a_status_to_another_stage_changes_the_closed_count(bundle: ImportBundle) -> None:
    closed_named = {
        r["name"]: r["tasks"] for r in build_plan(bundle, ImportMapping(), {})["statuses"]
    }["Closed"]
    assert closed_named == 844  # tasks, not rows: 902 rows hold 58 duplicates
    m = ImportMapping(statuses={"Closed": StatusChoice(category="in_progress")})
    assert build_plan(bundle, m, {})["closed_tasks"] == 1647 - 844


# ── §6.2 people ─────────────────────────────────────────────────────────────


def test_people_match_by_email_first_then_by_one_exact_name() -> None:
    b = _small(
        _row("a", assignees="[Ann Lee]"),
        _row("b", assignees="[Bo Diaz]"),
        _row("c", assignees="[Cy Park]"),
        _row("d", comments='[{""text"":""hi"",""by"":""dee@acme.test"",""date"":""""}]'),
    )
    directory = {
        "ann@acme.test": "Ann Lee",
        "bo1@acme.test": "Bo Diaz",
        "bo2@acme.test": "Bo Diaz",
        "dee@acme.test": "Dee",
    }
    people = {p["ref"]: p for p in build_plan(b, ImportMapping(), directory)["people"]}
    assert people["name:ann lee"]["member"] == "ann@acme.test"
    assert people["name:ann lee"]["match"] == "name"
    # Two members carry the name: no guess.
    assert people["name:bo diaz"]["member"] is None
    assert people["name:bo diaz"]["match"] == "ambiguous"
    assert people["name:cy park"]["member"] is None
    assert people["dee@acme.test"]["member"] == "dee@acme.test"
    assert people["dee@acme.test"]["match"] == "email"


def test_the_admin_can_clear_or_change_a_match() -> None:
    b = _small(_row("a", assignees="[Ann Lee]"))
    directory = {"ann@acme.test": "Ann Lee", "zed@acme.test": "Zed"}
    cleared = build_plan(b, ImportMapping(people={"name:ann lee": None}), directory)
    assert cleared["people"][0]["member"] is None and cleared["ready"]
    changed = build_plan(b, ImportMapping(people={"name:ann lee": "ZED@acme.test"}), directory)
    assert changed["people"][0]["member"] == "zed@acme.test" and changed["ready"]


def test_a_person_mapped_outside_the_organization_blocks_the_import() -> None:
    """§6.2 rule 3 — the picker offers members of THIS organization only."""
    b = _small(_row("a", assignees="[Ann Lee]"))
    p = build_plan(
        b,
        ImportMapping(people={"name:ann lee": "someone@other.test"}),
        {"ann@acme.test": "Ann Lee"},
    )
    assert not p["ready"]
    assert any("not a member" in e for e in p["errors"])


# ── §6.3 merges ─────────────────────────────────────────────────────────────


def test_two_spellings_merge_into_one_status() -> None:
    b = _small(_row("a", status="to do"), _row("b", status="todo"))
    m = ImportMapping(statuses={"todo": StatusChoice(category="todo", name="to do")})
    p = build_plan(b, m, {})
    assert {r["name"]: r["becomes"] for r in p["statuses"]} == {"to do": "to do", "todo": "to do"}
    assert p["ready"]


def test_a_merge_across_two_stages_is_refused() -> None:
    b = _small(_row("a", status="to do"), _row("b", status="done"))
    m = ImportMapping(statuses={"done": StatusChoice(category="done", name="to do")})
    p = build_plan(b, m, {})
    assert not p["ready"]
    assert any("different stages" in e for e in p["errors"])


def test_a_status_name_is_bounded() -> None:
    with pytest.raises(ValueError):
        StatusChoice(category="todo", name="x" * 65)


# ── §6.9 work that exists ───────────────────────────────────────────────────


def test_tasks_that_exist_are_counted_and_skipped() -> None:
    b = _small(
        _row("a"),
        _row("b"),
        _row("c", comments='[{""text"":""hi"",""by"":""x@y.test"",""date"":""""}]'),
    )
    p = build_plan(b, ImportMapping(), {}, existing_refs={"a"}, legacy_refs={"c", "zz"})
    assert p["skip"] == {"already_imported": 1, "written_by_old_importer": 1, "total": 2}
    assert p["to_write"] == {"tasks": 1, "comments": 0}


# ── the target and the grant ────────────────────────────────────────────────


def test_an_existing_target_needs_a_space_the_admin_may_write() -> None:
    b = _small(_row("a"))
    none_chosen = build_plan(b, ImportMapping(target=Target(kind="existing")), {})
    assert not none_chosen["ready"]
    refused = build_plan(
        b, ImportMapping(target=Target(kind="existing", project_id="x")), {}, target_ok=False
    )
    assert not refused["ready"]
    allowed = build_plan(
        b, ImportMapping(target=Target(kind="existing", project_id="x")), {}, target_ok=True
    )
    assert allowed["ready"]


@pytest.mark.parametrize(
    ("grant", "ok"),
    [
        ("org", True),
        ("group:engineering", True),
        ("alice@x.test", False),
        ("group:", False),
        ("group:Bad Slug", False),
    ],
)
def test_the_grant_is_org_or_one_group_never_an_email(grant: str, ok: bool) -> None:
    assert build_plan(_small(_row("a")), ImportMapping(grant=grant), {})["ready"] is ok


# ── the plan cannot write ───────────────────────────────────────────────────


def test_the_plan_module_cannot_reach_a_database() -> None:
    """§8 "the plan writes nothing": the module imports no database client and
    no repo module outside the importer package."""
    tree = ast.parse(pathlib.Path(plan.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert not any(
        m.split(".")[0] in {"sqlalchemy", "asyncpg", "psycopg", "acb_common"} for m in imported
    )
    assert all(
        not m.startswith("gateway") or m.startswith("gateway.routes.projects.importer")
        for m in imported
    )


def test_the_plan_is_json_ready(bundle: ImportBundle) -> None:
    import json

    json.dumps(build_plan(bundle, ImportMapping(), {"a@b.test": "A"}))
