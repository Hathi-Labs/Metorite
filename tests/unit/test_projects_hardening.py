"""WS-27w — read-path and history hardening.

Spec: ``ai-company-brain/specs/project_management_app.md`` §9.1 (WS-27w).
Rationale: ``specs/plane_pm_research_2026-08.md`` §3 (P-3, P-5, P-6, P-7, P-21).

Six small corrections, and the two claims here that outlive this ticket are
STRUCTURAL, deliberately DB-free:

* **every ``field_change`` goes through the one labelled door.** The walker
  below reads the package's ``record_activity`` call sites out of the AST, so
  a future call site written without label resolution fails this suite before
  it ships a UUID into somebody's history.
* **every ``TASK_SORTS`` entry ends with the ``(created_at, id)`` tiebreaker.**
  A sort without a total order lets a tie straddle a page boundary, and the
  bug reads as "pagination sometimes loses a task" — months of mystery for one
  missing clause.

Everything behavioural runs hermetically against ``_projects_fakes`` — no
Postgres, no TestClient, ``_get_db`` monkeypatched per SUT module.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import automation as pm_automation
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import search as pm_search
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_activities, pm_search)
USER = projects_user()
COLLEAGUE = member_user("colleague@fracktal.in")

PACKAGE = Path("apps/services/gateway/gateway/routes/projects")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


def _workspace(db: FakeProjectsDB) -> tuple:
    """One project with an open and a closed lane."""
    project = db.seed_project(name="Ops")
    todo = db.seed_status(project.id, name="To do", category="todo",
                          is_default=True, position=10)
    done = db.seed_status(project.id, name="Done", category="done",
                          is_default=False, position=40)
    return project, todo, done


# ── 1 · the archive guard ───────────────────────────────────────────────────

async def test_archiving_an_open_task_is_422_naming_the_category(
    db: FakeProjectsDB, events: list,
) -> None:
    """⚠️ The trap this guard closes: an archived open task silently exits
    every default list while the work is still owed (P-3). The refusal names
    the ACTUAL category so the person knows which lane to leave first."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Still open")

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.archive_task(str(task.id), user=USER)

    assert exc.value.status_code == 422
    assert "'todo'" in exc.value.detail
    # And nothing was written: the task is untouched.
    assert next(
        t for t in db.rows("pm_tasks") if str(t["id"]) == str(task.id)
    )["archived_at"] is None


@pytest.mark.parametrize("category,name", [("done", "Done"), ("cancelled", "Won't do")])
async def test_a_closed_task_archives_and_earns_a_timeline_row(
    db: FakeProjectsDB, events: list, category: str, name: str,
) -> None:
    """Both closing categories archive — `cancelled` counts as closed, the
    same rule `completed_at` follows."""
    project = db.seed_project(name="Ops")
    closed = db.seed_status(project.id, name=name, category=category)
    task = db.seed_task(project.id, closed.id, title="Finished")

    result = await pm_tasks.archive_task(str(task.id), user=USER)

    assert result["archived_at"] is not None
    assert any(
        a["body"] == "Task archived" for a in db.activities("system")
    )
    assert ("pm.task.archived", {"task_id": str(task.id)}) in events


async def test_the_guard_is_category_not_in_closing_never_a_list_of_open_ones() -> None:
    """WS-27u is concurrently adding a `triage` category. Written as `not in
    (done, cancelled)`, a new category is refused by default; written as a
    list of open categories, it would be silently archivable.

    ⚠️ **This asserted the SOURCE STRING in `tasks.py` until 2026-09-20.** The
    guard moved into `core.archive_refusal` when the bulk action needed it too
    — a bulk archive that skipped it would be the same trap, fifty tasks at a
    time — and a string match followed the guard nowhere. Asserting the
    BEHAVIOUR is both stronger and portable: it catches the flipped predicate
    the old test was written for, and it also catches a guard that is moved
    and quietly weakened on the way.
    """
    assert frozenset({"done", "cancelled"}) == pm_core.CLOSING_CATEGORIES

    # Closed means archivable, and nothing else does.
    for category in pm_core.CLOSING_CATEGORIES:
        assert pm_core.archive_refusal(category) is None, category

    # ⚠️ The direction that matters. `triage` is the category WS-27u added
    # after this guard was written, and the whole point is that it needed no
    # change here to be refused.
    for category in ("backlog", "todo", "in_progress", "triage", "", "a-new-one"):
        refusal = pm_core.archive_refusal(category)
        assert refusal is not None, f"{category!r} must not be archivable"
        assert category in refusal or category == "", refusal

    # The guard is reached from BOTH doors, and neither carries its own copy.
    for module in ("tasks.py", "bulk.py"):
        source = (PACKAGE / module).read_text(encoding="utf-8")
        assert "archive_refusal(" in source, module
        assert "not in CLOSING_CATEGORIES" not in source, (
            f"{module} grew a second copy of the guard"
        )


async def test_unarchive_restores_and_needs_no_category(
    db: FakeProjectsDB, events: list,
) -> None:
    """The way back has no guard: restoring puts work where people can see it,
    which is never the trap the archive guard exists to prevent."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(
        project.id, todo.id, title="Buried", archived_at=pm_core.now(),
    )

    result = await pm_tasks.unarchive_task(str(task.id), user=USER)

    assert result["archived_at"] is None


async def test_archiving_twice_is_idempotent_not_a_second_timeline_row(
    db: FakeProjectsDB, events: list,
) -> None:
    project = db.seed_project(name="Ops")
    done = db.seed_status(project.id, name="Done", category="done")
    task = db.seed_task(project.id, done.id, title="Finished")

    await pm_tasks.archive_task(str(task.id), user=USER)
    await pm_tasks.archive_task(str(task.id), user=USER)

    assert len([a for a in db.activities("system")
                if a["body"] == "Task archived"]) == 1


# ── 2 · activity meta — the labelled door, structurally ─────────────────────

def _field_change_call_sites() -> list[tuple[str, str, int]]:
    """Every ``record_activity(activity_type="field_change", …)`` call in the
    routes package, as ``(file, enclosing function, line)``.

    AST, not regex, because the thing being guarded is a CALL — a docstring or
    a comment mentioning the words must not satisfy (or trip) the rule.
    """
    sites: list[tuple[str, str, int]] = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Parent links, so a call can name its innermost enclosing function.
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                child._parent = parent  # type: ignore[attr-defined]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "record_activity":
                continue
            kind = next(
                (k.value.value for k in node.keywords
                 if k.arg == "activity_type" and isinstance(k.value, ast.Constant)),
                None,
            )
            if kind != "field_change":
                continue
            enclosing = node
            while enclosing is not None and not isinstance(
                enclosing, ast.FunctionDef | ast.AsyncFunctionDef,
            ):
                enclosing = getattr(enclosing, "_parent", None)
            sites.append((
                path.name,
                enclosing.name if enclosing is not None else "<module>",
                node.lineno,
            ))
    return sites


def test_every_field_change_call_site_goes_through_the_label_resolver() -> None:
    """⚠️ THE structural rule (P-5). ``record_field_change`` is the one
    function allowed to hand ``record_activity`` a ``field_change`` — it is
    where FK ids gain their labels and where description edits coalesce. A
    future call site that writes the type directly has, by construction,
    skipped both, and this walker fails it by name and line."""
    sites = _field_change_call_sites()
    # Self-check first: a walker that finds nothing is a walker that proves
    # nothing — the one legitimate site must be visible to it.
    assert sites, "the walker found no field_change call sites at all"
    offenders = [
        f"{file}:{line} (in {function})"
        for file, function, line in sites
        if (file, function) != ("core.py", "record_field_change")
    ]
    assert not offenders, (
        "field_change activities must be written through "
        "core.record_field_change, which resolves FK labels at write time; "
        f"found direct call sites: {offenders}"
    )


def test_the_one_door_resolves_labels_before_it_writes() -> None:
    """And the door itself cannot quietly lose the resolver: the call to
    ``resolve_fk_labels`` precedes the write in its source."""
    import inspect

    source = inspect.getsource(pm_core.record_field_change)
    assert source.index("resolve_fk_labels(") < source.index("record_activity(")


def test_every_tracked_fk_field_has_a_label_source() -> None:
    """A tracked field named ``*_id`` is an FK by this package's own naming
    convention; one without a row in ``FK_LABEL_FIELDS`` would pass through
    the resolver untouched and store bare UUIDs — the exact defect item 2
    exists to close."""
    tracked = {
        *pm_tasks._TRACKED_TASK_FIELDS,
        *pm_automation.PATCHABLE_FIELDS,
        *pm_tree._TRACKED_PROJECT_FIELDS,
    }
    missing = sorted(
        name for name in tracked
        if name.endswith("_id") and name not in pm_core.FK_LABEL_FIELDS
    )
    assert not missing, f"FK-valued tracked fields without a label source: {missing}"


async def test_a_type_change_carries_ids_and_labels_resolved_at_write_time(
    db: FakeProjectsDB, events: list,
) -> None:
    """The behavioural half: patching ``type_id`` stores the five-key shape,
    with the label read NOW — history renders without a join against a row
    that may later be renamed or deleted."""
    project, todo, _ = _workspace(db)
    bug = db.seed("pm_task_types", project_id=project.id, name="Bug")
    task = db.seed_task(project.id, todo.id, title="Typed")

    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(type_id=str(bug.id)), user=USER,
    )

    [entry] = db.activities("field_change")
    [change] = entry["meta"]["changes"]
    assert change == {
        "field": "type_id",
        "old_id": None,
        "new_id": str(bug.id),
        "old_label": None,
        "new_label": "Bug",
    }


# ── 3 · description-edit coalescing ─────────────────────────────────────────

async def test_same_actor_consecutive_description_edits_are_one_row(
    db: FakeProjectsDB, events: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The autosave case (P-5): two edits in a row, one timeline row, spanning
    first ``old`` to latest ``new`` — with the row's timestamp moved to the
    latest edit, because the row now records that one."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Doc", description="v1")

    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v2"), user=USER,
    )
    later = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(pm_core, "now", lambda: later)
    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v3"), user=USER,
    )

    [entry] = db.activities("field_change")
    assert entry["meta"]["changes"] == [
        {"field": "description", "old": "v1", "new": "v3"},
    ]
    assert entry["created_at"] == later


async def test_a_different_actor_breaks_the_run_two_rows(
    db: FakeProjectsDB, events: list,
) -> None:
    """Same field, different hands: coalescing across actors would attribute
    one person's words to another."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Doc", description="v1")

    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v2"), user=USER,
    )
    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v3"), user=COLLEAGUE,
    )

    assert len(db.activities("field_change")) == 2


async def test_an_intervening_activity_breaks_the_run(
    db: FakeProjectsDB, events: list,
) -> None:
    """Consecutive means IMMEDIATELY previous. A comment in between is part of
    the story, and folding the second edit backwards over it would reorder
    what actually happened."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Doc", description="v1")

    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v2"), user=USER,
    )
    await pm_core.record_activity(
        db, activity_type="comment", created_by="owner@fracktal.in",
        task_id=str(task.id), body="looks good",
    )
    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v3"), user=USER,
    )

    assert len(db.activities("field_change")) == 2


async def test_a_multi_field_edit_never_coalesces(
    db: FakeProjectsDB, events: list,
) -> None:
    """Description + importance in one PATCH is not an autosave run; folding
    it into a prior description row would hide the importance change."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Doc", description="v1")

    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v2"), user=USER,
    )
    await pm_tasks.patch_task(
        str(task.id), pm_core.TaskIn(description="v3", importance=4), user=USER,
    )

    assert len(db.activities("field_change")) == 2


async def test_editing_a_comment_twice_updates_the_row_never_appends(
    db: FakeProjectsDB, events: list,
) -> None:
    """The comment-body half: the comment IS the activity row, so an edit —
    and an edit of the edit — rewrites it in place and bumps its timestamp.
    Two edits, one comment row, no side rows."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Doc")
    comment = db.seed(
        "pm_activities", task_id=str(task.id), type="comment",
        body="first", created_by="owner@fracktal.in",
    )
    before = next(
        a for a in db.rows("pm_activities") if str(a["id"]) == str(comment.id)
    )["updated_at"]

    await pm_activities.edit_comment(
        str(comment.id), pm_activities.CommentIn(body="second"), user=USER,
    )
    await pm_activities.edit_comment(
        str(comment.id), pm_activities.CommentIn(body="third"), user=USER,
    )

    rows = [a for a in db.rows("pm_activities") if a["type"] == "comment"]
    assert len(rows) == 1
    assert rows[0]["body"] == "third"
    assert rows[0]["updated_at"] > before
    assert len(db.rows("pm_activities")) == 1  # nothing appended beside it


# ── 4 · semantic sorts, and the tiebreaker rule ─────────────────────────────

def test_every_task_sort_ends_with_the_created_at_id_tiebreaker() -> None:
    """⚠️ Structural (P-6). Without a total order, two tasks tying on the sort
    key can straddle a page boundary — appearing on both pages or neither
    depending on the plan — and the report is "pagination loses tasks"."""
    for key, value in pm_core.TASK_SORTS.items():
        assert value.endswith(pm_core.SORT_TIEBREAK), (
            f"TASK_SORTS[{key!r}] must end with the deterministic "
            f"{pm_core.SORT_TIEBREAK!r} tiebreaker; got: {value!r}"
        )


def test_the_status_sort_is_semantic_never_alphabetical() -> None:
    """Category rank then lane position — never the status NAME, which would
    put "Backlog" before "Done" only by accident of language and reshuffle the
    whole list on every lane rename."""
    status = pm_core.TASK_SORTS["status"]
    assert "array_position" in status and "s.category" in status
    assert "s.position" in status
    assert "s.name" not in status
    # And the rank IS the vocabulary, in lifecycle order — not a second list.
    for category in pm_core.STATUS_CATEGORIES:
        assert f"'{category}'" in status


async def test_sorting_by_status_orders_by_category_rank_then_position(
    db: FakeProjectsDB, events: list,
) -> None:
    """The crafted fixture: lane NAMES chosen so alphabetical order is exactly
    wrong — 'Aaa Done' would sort first by name and must sort last by rank —
    and two same-category lanes to prove position breaks the tie."""
    project = db.seed_project(name="Ops")
    done = db.seed_status(project.id, name="Aaa Done", category="done",
                          position=40, is_default=False)
    doing_b = db.seed_status(project.id, name="Bbb Doing", category="in_progress",
                             position=30, is_default=False)
    doing_z = db.seed_status(project.id, name="Zzz Doing first", category="in_progress",
                             position=20, is_default=False)
    todo = db.seed_status(project.id, name="Yyy To do", category="todo",
                          position=10, is_default=True)

    db.seed_task(project.id, done.id, title="finished")
    db.seed_task(project.id, doing_b.id, title="doing, later lane")
    db.seed_task(project.id, doing_z.id, title="doing, earlier lane")
    db.seed_task(project.id, todo.id, title="queued")

    result = await pm_tasks.list_tasks(
        user=USER, sort="status", direction="asc", page=page(),
    )

    assert [row["title"] for row in result.rows] == [
        "queued",                # todo ranks before in_progress
        "doing, earlier lane",   # same category: position 20 before 30
        "doing, later lane",
        "finished",              # done ranks last, despite the 'Aaa' name
    ]


# ── 5 · picker exclusions on search ─────────────────────────────────────────

def _family(db: FakeProjectsDB) -> dict:
    """Anchor with an ancestor, descendants, relations both ways, a bystander."""
    project, todo, _ = _workspace(db)
    parent = db.seed_task(project.id, todo.id, title="pick parent")
    anchor = db.seed_task(project.id, todo.id, title="pick anchor",
                          parent_task_id=parent.id)
    child = db.seed_task(project.id, todo.id, title="pick child",
                         parent_task_id=anchor.id)
    db.seed_task(project.id, todo.id, title="pick grandchild",
                 parent_task_id=child.id)
    outgoing = db.seed_task(project.id, todo.id, title="pick outgoing relation")
    incoming = db.seed_task(project.id, todo.id, title="pick incoming relation")
    db.seed("pm_task_links", source_task_id=anchor.id,
            target_task_id=outgoing.id, link_type="relates_to")
    db.seed("pm_task_links", source_task_id=incoming.id,
            target_task_id=anchor.id, link_type="blocks")
    bystander = db.seed_task(project.id, todo.id, title="pick bystander")
    return {"anchor": anchor, "bystander": bystander}


async def test_exclude_relatives_of_hides_all_four_classes(
    db: FakeProjectsDB, events: list,
) -> None:
    """Self, ancestors, descendants, related-in-either-direction: everything
    the write path would 422 (or merely re-assert), so the picker cannot offer
    it. The bystander is the control — exclusion must not become an empty
    answer."""
    family = _family(db)

    result = await pm_search.search_tasks(
        q="pick", exclude_relatives_of=str(family["anchor"].id), user=USER,
    )

    assert [r["title"] for r in result["rows"]] == ["pick bystander"]


async def test_without_the_parameter_search_is_unchanged(
    db: FakeProjectsDB, events: list,
) -> None:
    family = _family(db)
    result = await pm_search.search_tasks(q="pick", user=USER)
    titles = {r["title"] for r in result["rows"]}
    assert family["anchor"].title in titles
    assert len(titles) == 7


async def test_an_unreadable_anchor_is_404_not_an_oracle(
    db: FakeProjectsDB, events: list,
) -> None:
    """R5: filtering by a task you cannot see must answer exactly what "no
    such task" answers, or the parameter probes for existence."""
    secret = db.seed_project(name="Secret", subject=None)
    status = db.seed_status(secret.id, name="To do", category="todo")
    hidden = db.seed_task(secret.id, status.id, title="hidden anchor")
    db.seed_project(name="Mine", subject="colleague@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await pm_search.search_tasks(
            q="anything", exclude_relatives_of=str(hidden.id), user=COLLEAGUE,
        )
    assert exc.value.status_code == 404


async def test_the_write_time_guards_stay(db: FakeProjectsDB) -> None:
    """Item 5's last clause: the read-side exclusion is a twin, not a
    replacement — self-linking still 422s at the write."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Self")

    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_link(
            str(task.id),
            pm_tasks.LinkIn(target_task_id=str(task.id)),
            user=USER,
        )
    assert exc.value.status_code == 422
