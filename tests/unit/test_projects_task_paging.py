"""The paging contract the Projects board rests on.

🔴 **Why this file exists.** Until 2026-09-22 the board asked for one page of
`GET /projects/tasks` and drew whatever came back. `MAX_PAGE_SIZE` is 100, the
endpoint answers no cursor, and nothing in the client looked at `total`. So a
150-task project rendered 100 rows, headed its lane "To do 100", and ended with
the ordinary "+ Add" — fifty tasks unreachable from the board, the list and the
table, with nothing on screen to say so.

Measured that day against a real gateway and a real Postgres:

    page 1: rows=100 total=150
    page 2: rows=50  total=150
    overlap 0, union 150

`workbench/control_plane/src/app/projects/lib/paging.ts` now reads the rest and
`MoreTasksBar` says "Showing 100 of 150 tasks." **Both rest entirely on the two
promises asserted here** — that `total` describes the whole filtered set rather
than the page, and that page N is the next disjoint slice. If either stops
being true the bar goes quiet or lies, and the original defect is back with a
button on top of it.

⚠️ These run on `FakeProjectsDB`, so they fence the HANDLER's contract, not the
SQL. The SQL half was measured live on 2026-09-22 (above) and the numbers are
recorded here rather than left in a transcript.
"""

from __future__ import annotations

import pytest
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
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


def _board(db: FakeProjectsDB, count: int) -> tuple:
    """A project carrying more tasks than one page can hold."""
    project = db.seed_project(name="Ops", subject="owner@fracktal.in")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    for index in range(count):
        db.seed_task(project.id, todo.id, title=f"Task {index + 1:03d}")
    return project, todo


def _ids(result) -> list[str]:
    return [str(row["id"]) for row in result.rows]


@pytest.mark.asyncio
async def test_total_counts_the_whole_set_not_the_page(db, events):
    """The number the bar renders after the word "of"."""
    _board(db, 150)

    result = await pm_tasks.list_tasks(user=USER, page=page(size=100))

    assert len(result.rows) == 100
    # 🔴 If this ever returns the PAGE length the bar computes 100 of 100,
    # falls silent, and the board is silently short again — the exact defect,
    # with the fix still in the tree looking like it works.
    assert result.total == 150


@pytest.mark.asyncio
async def test_page_two_is_the_next_slice_and_does_not_repeat_page_one(db, events):
    """`Load more` appends page 2. Overlap would draw a task twice."""
    _board(db, 150)

    first = await pm_tasks.list_tasks(user=USER, page=page(number=1, size=100))
    second = await pm_tasks.list_tasks(user=USER, page=page(number=2, size=100))

    assert len(second.rows) == 50
    assert set(_ids(first)) & set(_ids(second)) == set()


@pytest.mark.asyncio
async def test_the_pages_together_reach_every_task(db, events):
    """The promise that makes `Load more` worth pressing."""
    _board(db, 150)

    first = await pm_tasks.list_tasks(user=USER, page=page(number=1, size=100))
    second = await pm_tasks.list_tasks(user=USER, page=page(number=2, size=100))

    reached = set(_ids(first)) | set(_ids(second))
    assert len(reached) == 150
    assert len(reached) == first.total


@pytest.mark.asyncio
async def test_total_follows_the_filter(db, events):
    """"Showing 20 of 30" must mean 30 under the filters on screen.

    A `total` that ignored the filter beside it would promise rows the next
    read cannot return, and `Load more` would fetch an empty page forever.
    """
    project, todo = _board(db, 20)
    done = db.seed_status(project.id, name="Done", category="done", position=40)
    for index in range(5):
        db.seed_task(project.id, done.id, title=f"Finished {index}")

    everything = await pm_tasks.list_tasks(user=USER, page=page(size=100))
    only_todo = await pm_tasks.list_tasks(
        user=USER, page=page(size=100), status_id=str(todo.id),
    )

    assert everything.total == 25
    assert only_todo.total == 20


@pytest.mark.asyncio
async def test_a_short_board_reports_its_own_size(db, events):
    """The ordinary case: nothing missing, so the bar must not appear."""
    _board(db, 7)

    result = await pm_tasks.list_tasks(user=USER, page=page(size=100))

    assert len(result.rows) == 7
    assert result.total == 7


@pytest.mark.asyncio
async def test_an_empty_board_reports_zero_rather_than_nothing(db, events):
    """`total` must be a number even with no rows.

    `truncationNote` treats a missing total as "not known yet" and stays quiet,
    which is right for a first paint and wrong forever.
    """
    db.seed_project(name="Empty", subject="owner@fracktal.in")

    result = await pm_tasks.list_tasks(user=USER, page=page(size=100))

    assert result.rows == []
    assert result.total == 0
