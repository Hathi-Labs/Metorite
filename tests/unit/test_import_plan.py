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
    propose_target,
)
from gateway.routes.projects.tree import _SEED_STATUSES

FIXTURE = pathlib.Path(__file__).parent / "import_fixtures" / "clickup_workspace.csv"
#: The root seed, as the route reads it into the facts (I-10).
SEED = [(name, category) for name, _color, _pos, category, _default in _SEED_STATUSES]


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
    p = build_plan(bundle, ImportMapping(), {}, target_statuses=SEED)
    assert p["summary"]["tasks"] == 2423
    assert p["closed_tasks"] == 1647
    # §6.6 — every closed task needs an estimated completion date.
    assert p["completed_at_estimated"] == 1647
    # D79, per set (I-10): a new space starts with the seed, which holds Done.
    # Before I-10 each of 7 Lists gained a Done of its own.
    assert p["done_status_added"] == 0
    assert p["to_write"] == {"tasks": 2423, "comments": 93}
    assert p["utc_offset_minutes"] == 330
    assert p["ready"] is True and p["errors"] == []
    assert sum(1 for n in p["tree"] if n["kind"] == "project") == 48


def test_mapping_a_status_to_another_stage_changes_the_closed_count(bundle: ImportBundle) -> None:
    closed_named = {
        r["name"]: r["tasks"] for r in build_plan(bundle, ImportMapping(), {})["statuses"]
    }["Closed"]
    assert closed_named == 844  # tasks, not rows: 902 rows hold 58 duplicates
    # I-10: a new status takes the stage the admin gives it.
    m = ImportMapping(statuses={"Closed": StatusChoice(category="in_progress", name="Closed")})
    assert build_plan(bundle, m, {}, target_statuses=SEED)["closed_tasks"] == 1647 - 844


# ── §6.3 I-10: a target for each status ─────────────────────────────────────


def test_the_ten_statuses_of_the_real_file_propose_six_targets(bundle: ImportBundle) -> None:
    """§9 I-10: on the fixture, ten ClickUp statuses become six statuses."""
    p = build_plan(bundle, ImportMapping(), {}, target_statuses=SEED)
    becomes = {r["name"]: r["becomes"] for r in p["statuses"]}
    assert becomes == {
        "Closed": "Done",
        "done": "Done",
        "completed": "Done",
        "backlog": "Backlog",
        "on hold": "On hold",
        "to do": "To do",
        "todo": "To do",
        "in process": "In progress",
        "in progress": "In progress",
        "review": "Review",
    }
    assert len(set(becomes.values())) == 6
    assert p["errors"] == [] and p["ready"]
    new = {r["becomes"] for r in p["statuses"] if not r["existing"]}
    assert new == {"On hold", "Review"}
    assert {r["becomes"]: r["category"] for r in p["statuses"]}["On hold"] == "backlog"
    # The plan carries the target set, in its own order.
    assert [s["name"] for s in p["target_statuses"]] == ["Backlog", "To do", "In progress", "Done"]


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("Closed", ("Done", "done", True)),
        ("  RESOLVED ", ("Done", "done", True)),
        ("in   process", ("In progress", "in_progress", True)),
        ("Not Started", ("To do", "todo", True)),
        ("wip", ("In progress", "in_progress", True)),
        ("won't do", ("Cancelled", "cancelled", False)),
        ("canceled", ("Cancelled", "cancelled", False)),
        ("on  hold", ("On hold", "backlog", False)),
        ("qa review", ("Qa review", "in_progress", False)),
    ],
)
def test_a_source_name_proposes_a_target(name: str, target: tuple[str, str, bool]) -> None:
    assert propose_target(name, SEED) == target


def test_a_name_the_target_set_holds_is_that_status_with_its_stage() -> None:
    """A choice that names an existing status takes that status's stage."""
    target = [("Closed", "done"), ("Doing", "in_progress")]
    b = _small(_row("a", status="closed"), _row("b", status="review"))
    m = ImportMapping(statuses={"review": StatusChoice(category="backlog", name="doing")})
    rows = {r["name"]: r for r in build_plan(b, m, {}, target_statuses=target)["statuses"]}
    assert (rows["closed"]["becomes"], rows["closed"]["category"]) == ("Closed", "done")
    assert (rows["review"]["becomes"], rows["review"]["category"]) == ("Doing", "in_progress")
    assert rows["review"]["existing"] and rows["closed"]["existing"]


def test_on_a_new_tree_a_choice_with_no_name_takes_the_proposal() -> None:
    """A null name comes only from a mapping made before I-10."""
    b = _small(_row("a", status="in process"))
    m = ImportMapping(statuses={"in process": StatusChoice(category="backlog")})
    row = build_plan(b, m, {}, target_statuses=SEED)["statuses"][0]
    assert (row["becomes"], row["category"]) == ("In progress", "in_progress")


def test_a_run_that_continues_keeps_the_earlier_names() -> None:
    """§6.3 continuity: a run into an earlier tree keeps that import's names.
    A name the earlier run recorded keeps its target. A run written before
    I-10 recorded none, so the source name stays, as it did then."""
    b = _small(_row("a", status="in process"), _row("b", status="todo"), _row("c", status="qa"))
    earlier = {"todo": ("To do", "todo")}
    rows = {
        r["name"]: r
        for r in build_plan(
            b,
            ImportMapping(statuses={"qa": StatusChoice(category="done")}),
            {},
            target_statuses=SEED,
            continues=True,
            earlier_names=earlier,
        )["statuses"]
    }
    assert rows["in process"]["becomes"] == "in process"
    assert rows["todo"]["becomes"] == "To do"
    # A choice with no name keeps the source name and the choice's stage.
    assert (rows["qa"]["becomes"], rows["qa"]["category"]) == ("qa", "done")


def test_d79_counts_the_sets_that_gain_a_done() -> None:
    """D79, per set: an existing space with no done-stage status gains one,
    unless the mapping already makes one. A new space has the seed's Done."""
    b = _small(_row("a", status="review"))
    existing = ImportMapping(target=Target(kind="existing", project_id="x"))
    no_done = build_plan(b, existing, {}, target_statuses=[("To do", "todo")])
    assert no_done["done_status_added"] == 1
    closed = _small(_row("a", status="shipped"))
    has_done = build_plan(closed, existing, {}, target_statuses=[("To do", "todo")])
    assert has_done["done_status_added"] == 0
    assert build_plan(b, ImportMapping(), {}, target_statuses=SEED)["done_status_added"] == 0


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


def test_the_plan_offers_EVERY_member_to_the_picker() -> None:
    """Owner report, 2026-10-08: the picker offered 8 of 20 members.

    It read the assignee search, which caps at 8. The plan now carries the
    whole directory, which is the same set ``_people`` accepts as a choice.
    """
    b = _small(_row("a", assignees="[Nobody Here]"))
    directory = {f"m{i:02d}@acme.test": f"Member {i:02d}" for i in range(20)}
    directory["noname@acme.test"] = ""
    members = build_plan(b, ImportMapping(), directory)["members"]
    assert len(members) == 21
    assert {m["email"] for m in members} == set(directory)
    # By name. A member with no name shows the address, and sorts by it.
    assert [m["name"] for m in members[:2]] == ["Member 00", "Member 01"]
    assert {"email": "noname@acme.test", "name": "noname@acme.test"} in members


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
    p = build_plan(b, m, {}, target_statuses=SEED)
    # I-10: both land in the seed's "To do", with its spelling.
    assert {r["name"]: r["becomes"] for r in p["statuses"]} == {"to do": "To do", "todo": "To do"}
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


def test_imported_tasks_are_updated_and_old_importer_tasks_are_skipped() -> None:
    """Owner decision 2026-09-28 (§11 Q-5): a re-import UPDATES the tasks an
    earlier import wrote. Only the pre-D52 importer's rows are skipped."""
    b = _small(
        _row("a"),
        _row("b"),
        _row("c", comments='[{""text"":""hi"",""by"":""x@y.test"",""date"":""""}]'),
    )
    p = build_plan(b, ImportMapping(), {}, existing_refs={"a"}, legacy_refs={"c", "zz"})
    assert p["skip"] == {"written_by_old_importer": 1, "total": 1}
    assert p["to_update"] == 1
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
