"""WS-27s — the badges a card needs, on the LIST endpoint.

Spec: ``project-docs/specs/project_management_app.md`` §11.15.

A Projects card should read like a Tasks card. Most of what makes those cards
legible is data Projects already stores and never sends to the board: how many
subtasks are finished, and whether anything is holding this task up. WS-27p made
both readable **one task at a time**; a board draws them on every card at once.

The claims worth pinning are the ones where a plausible implementation is wrong:

* **it is two aggregates, not two-per-card.** N+1 across an imported workspace
  is the difference between a board and a spinner, and it is invisible in any
  test that seeds three tasks.
* **a finished blocker does not block.** A card still marked blocked after its
  dependency shipped is a card people learn to ignore — the same argument
  WS-27p's ``blocked_by_open`` makes, applied to the count.
* **an archived subtask is not counted.** It would sit permanently in the
  denominator, so "2/5" would never reach 5/5 and the badge would be a lie.
* **every row carries both keys.** A missing key and a zero read the same to a
  careless client; "no subtasks" is a thing the card draws nothing for, not an
  absence it has to guess at.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views
from gateway.routes.projects.filters import attach_relation_counts

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    page,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me)
USER = projects_user()

FILTERS = Path("apps/services/gateway/gateway/routes/projects/filters.py")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


def _workspace(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Ops", subject="owner@fracktal.in")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


def _rows(result) -> dict[str, dict]:
    return {str(row["id"]): row for row in result.rows}


# ── Subtask progress ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_card_reports_how_many_of_its_subtasks_are_finished(db, events):
    project, todo, done = _workspace(db)
    parent = db.seed_task(project.id, todo.id, title="Ship it")
    db.seed_task(project.id, done.id, title="One", parent_task_id=parent.id)
    db.seed_task(project.id, done.id, title="Two", parent_task_id=parent.id)
    db.seed_task(project.id, todo.id, title="Three", parent_task_id=parent.id)

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert _rows(result)[str(parent.id)]["subtasks"] == {"done": 2, "total": 3}


@pytest.mark.asyncio
async def test_progress_is_counted_from_the_status_category_not_completed_at(
    db, events,
):
    """A project may name its finished lane anything, and `cancelled` counts as
    resolved even though nothing was completed — the reason every other derived
    word in this app keys off the category."""
    project, todo, _done = _workspace(db)
    dropped = db.seed_status(
        project.id, name="Won't do", category="cancelled", is_default=False,
        position=50,
    )
    parent = db.seed_task(project.id, todo.id, title="Ship it")
    db.seed_task(
        project.id, dropped.id, title="Abandoned", parent_task_id=parent.id,
        completed_at=None,
    )

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert _rows(result)[str(parent.id)]["subtasks"] == {"done": 1, "total": 1}


@pytest.mark.asyncio
async def test_an_archived_subtask_leaves_the_denominator(db, events):
    """⚠️ Counted, it would sit in the denominator forever: "2/3" could never
    become 3/3 and the badge would be permanently wrong."""
    project, todo, done = _workspace(db)
    parent = db.seed_task(project.id, todo.id, title="Ship it")
    db.seed_task(project.id, done.id, title="One", parent_task_id=parent.id)
    db.seed_task(
        project.id, todo.id, title="Dropped", parent_task_id=parent.id,
        archived_at="2026-08-01T00:00:00Z",
    )

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert _rows(result)[str(parent.id)]["subtasks"] == {"done": 1, "total": 1}


@pytest.mark.asyncio
async def test_a_task_with_no_subtasks_still_carries_the_key(db, events):
    project, todo, _done = _workspace(db)
    lonely = db.seed_task(project.id, todo.id, title="Alone")

    result = await pm_tasks.list_tasks(user=USER, page=page())

    row = _rows(result)[str(lonely.id)]
    assert row["subtasks"] == {"done": 0, "total": 0}
    assert row["blocked_by_count"] == 0


# ── Blocked-ness ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_card_reports_how_many_open_tasks_block_it(db, events):
    project, todo, _done = _workspace(db)
    blocked = db.seed_task(project.id, todo.id, title="Waiting")
    for title in ("First", "Second"):
        blocker = db.seed_task(project.id, todo.id, title=title)
        db.seed(
            "pm_task_links", source_task_id=blocker.id, target_task_id=blocked.id,
            link_type="blocks", created_by="owner@fracktal.in",
        )

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert _rows(result)[str(blocked.id)]["blocked_by_count"] == 2


@pytest.mark.asyncio
async def test_a_finished_blocker_stops_blocking(db, events):
    """⚠️ The WS-27p rule, applied to the count: a blocker that is done or
    cancelled holds nothing up, and a card that stays red after its dependency
    shipped teaches people to ignore the badge."""
    project, todo, done = _workspace(db)
    blocked = db.seed_task(project.id, todo.id, title="Waiting")
    shipped = db.seed_task(project.id, done.id, title="Shipped")
    still_open = db.seed_task(project.id, todo.id, title="Open")
    for blocker in (shipped, still_open):
        db.seed(
            "pm_task_links", source_task_id=blocker.id, target_task_id=blocked.id,
            link_type="blocks", created_by="owner@fracktal.in",
        )

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert _rows(result)[str(blocked.id)]["blocked_by_count"] == 1


@pytest.mark.asyncio
async def test_only_blocks_counts_a_related_task_is_not_a_blocker(db, events):
    project, todo, _done = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Waiting")
    other = db.seed_task(project.id, todo.id, title="Related")
    for link_type in ("relates_to", "duplicates"):
        db.seed(
            "pm_task_links", source_task_id=other.id, target_task_id=task.id,
            link_type=link_type, created_by="owner@fracktal.in",
        )

    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert _rows(result)[str(task.id)]["blocked_by_count"] == 0


@pytest.mark.asyncio
async def test_blocking_something_else_does_not_make_this_task_blocked(db, events):
    """⚠️ The link is directed, and reading it the wrong way round marks every
    upstream task blocked by its own downstream work."""
    project, todo, _done = _workspace(db)
    upstream = db.seed_task(project.id, todo.id, title="Do first")
    downstream = db.seed_task(project.id, todo.id, title="Do second")
    db.seed(
        "pm_task_links", source_task_id=upstream.id, target_task_id=downstream.id,
        link_type="blocks", created_by="owner@fracktal.in",
    )

    rows = _rows(await pm_tasks.list_tasks(user=USER, page=page()))

    assert rows[str(upstream.id)]["blocked_by_count"] == 0
    assert rows[str(downstream.id)]["blocked_by_count"] == 1


# ── Shape ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_it_is_two_queries_for_a_whole_page_not_two_per_card(db, events):
    """⚠️ The claim a three-task fixture cannot make on its own: N+1 here is the
    difference between a board and a spinner on an imported workspace, and it
    looks identical to the correct version at this scale."""
    project, todo, _done = _workspace(db)
    for n in range(12):
        db.seed_task(project.id, todo.id, title=f"Task {n}")

    db.statements.clear()
    await pm_tasks.list_tasks(user=USER, page=page())

    subtask_queries = [s for s in db.statements if "AS parent" in s]
    blocker_queries = [s for s in db.statements if "AS blocked" in s]
    assert len(subtask_queries) == 1
    assert len(blocker_queries) == 1


@pytest.mark.asyncio
async def test_an_empty_page_asks_the_database_nothing(db, events):
    _workspace(db)

    db.statements.clear()
    result = await pm_tasks.list_tasks(user=USER, page=page())

    assert result.rows == []
    assert not [s for s in db.statements if "AS parent" in s or "AS blocked" in s]


@pytest.mark.asyncio
async def test_rows_without_an_id_do_not_reach_the_query() -> None:
    """Defensive, and cheap: an id of ``None`` cast to a uuid[] is a 500, and
    the roll-up is called on whatever the list produced."""
    class Counting:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, sql, params=None):
            self.calls += 1
            raise AssertionError("should not have queried")

    rows: list[dict] = [{"id": None}, {}]
    db = Counting()

    vis = pm_core.Visibility(unrestricted=False, email="x@y.z", groups=())
    assert await attach_relation_counts(db, rows, vis) is rows
    assert db.calls == 0
    assert all(r["subtasks"] == {"done": 0, "total": 0} for r in rows)


# ── Structural — what the fake cannot decide ────────────────────────────────

def test_the_subtask_roll_up_excludes_archived_children_in_SQL() -> None:
    """The fake re-implements this clause in Python, so only the statement text
    can say whether the route still carries it."""
    source = FILTERS.read_text(encoding="utf-8")
    match = re.search(r"_SUBTASK_COUNTS_SQL = \"\"\"(.*?)\"\"\"", source, re.S)
    assert match is not None
    assert "archived_at IS NULL" in match.group(1)


def test_the_blocker_roll_up_filters_closed_blockers_in_SQL() -> None:
    """⚠️ Counted in SQL and filtered in Python would be correct and slow — but
    filtered *nowhere* looks identical until a blocker is finished."""
    source = FILTERS.read_text(encoding="utf-8")
    match = re.search(r"_BLOCKER_COUNTS_SQL = \"\"\"(.*?)\"\"\"", source, re.S)
    assert match is not None
    body = match.group(1)
    assert "NOT (s.category = ANY(:closed))" in body
    assert "l.link_type = 'blocks'" in body


def test_neither_roll_up_scans_the_whole_table() -> None:
    """Both are bounded by the page's ids. Losing that bound is a query that
    grows with the workspace and returns rows nobody asked for."""
    source = FILTERS.read_text(encoding="utf-8")
    for name in ("_SUBTASK_COUNTS_SQL", "_BLOCKER_COUNTS_SQL"):
        match = re.search(rf"{name} = \"\"\"(.*?)\"\"\"", source, re.S)
        assert match is not None, name
        assert "= ANY(CAST(:ids AS uuid[]))" in match.group(1), name
