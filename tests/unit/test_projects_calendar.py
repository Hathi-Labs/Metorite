"""WS-27q — the calendar window.

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 9, §11.16.

The third view, and the first one that cannot be a page. The claims worth
pinning are the ones where the wrong implementation looks right:

* **a window is not a page.** Paginating a month draws forty of its ninety
  tasks and leaves the other days looking EMPTY. A short page announces itself;
  a short month does not — so the cap is explicit and the response says when it
  was reached.
* **overlap, not equality.** A task that starts Monday and is due Friday belongs
  on Wednesday. `due_at BETWEEN` — the obvious version — puts it on Friday
  alone, which is the week somebody looks at Wednesday and thinks they are free.
* **a task with neither date is not on the calendar, and is COUNTED.** It falls
  out of the overlap test on its own, through NULL rather than through any
  clause a reader can see, so the behaviour is pinned rather than trusted.
* **the calendar honours the board's filters.** Switching view must change the
  shape of what is on screen and never the set.
* **the window edge is half-open.** Two consecutive months must tile, not
  overlap on the first.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import calendar as pm_calendar
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views
from gateway.routes.projects.calendar import (
    MAX_WINDOW_DAYS,
    MAX_WINDOW_ROWS,
    OVERLAPS,
    UNDATED,
    parse_day,
    window_bounds,
)

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    projects_user,
    silence_events,
)

MODULES = (
    pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me,
    pm_calendar,
)
USER = projects_user()
#: Holds `feature:projects` but NOT `data:org:read`, so the grant closure
#: actually decides what they see. Without this principal a scoping test proves
#: only that the owner can see everything, which is true of an unscoped route.
MEMBER = member_user("colleague@fracktal.in")

SOURCE = Path("apps/services/gateway/gateway/routes/projects/calendar.py")


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


# ── parse_day ───────────────────────────────────────────────────────────────

def test_a_calendar_date_is_read_as_a_date() -> None:
    assert parse_day("2026-08-01", field="from").isoformat() == "2026-08-01"


@pytest.mark.parametrize("raw", ["august", "2026-13-01", "", "next week", "//"])
def test_an_unreadable_window_edge_is_a_422_and_shows_the_format(raw: str) -> None:
    """The client's mistake, told to the client. `from=august` answering 500
    reads as a server fault and gets reported as one."""
    with pytest.raises(HTTPException) as caught:
        parse_day(raw, field="from")
    assert caught.value.status_code == 422
    assert "2026-08-01" in caught.value.detail


def test_surrounding_whitespace_is_not_a_typo_worth_refusing() -> None:
    assert parse_day(" 2026-08-01 ", field="from").isoformat() == "2026-08-01"


# ── window_bounds ───────────────────────────────────────────────────────────

def test_the_window_is_midnight_to_midnight_in_UTC() -> None:
    start, end = window_bounds("2026-08-01", "2026-09-01")
    assert start == datetime(2026, 8, 1, tzinfo=UTC)
    assert end == datetime(2026, 9, 1, tzinfo=UTC)


def test_two_consecutive_months_tile_rather_than_overlap() -> None:
    """⚠️ Half-open, and this is why: an inclusive end would put every task due
    on the 31st in both August and September, and a calendar that disagrees
    with itself across a page turn is worse than one that is conservative."""
    _, august_end = window_bounds("2026-08-01", "2026-09-01")
    september_start, _ = window_bounds("2026-09-01", "2026-10-01")
    assert august_end == september_start


def test_a_backwards_window_is_refused_rather_than_swapped() -> None:
    """Swapping it would be helpful and wrong: the caller has a bug, and a
    calendar that silently shows a different month than the one requested is
    the hardest possible version of it to find."""
    with pytest.raises(HTTPException) as caught:
        window_bounds("2026-09-01", "2026-08-01")
    assert caught.value.status_code == 422


def test_a_zero_length_window_is_refused() -> None:
    with pytest.raises(HTTPException) as caught:
        window_bounds("2026-08-01", "2026-08-01")
    assert caught.value.status_code == 422


def test_a_window_wider_than_the_maximum_is_refused_not_clamped() -> None:
    """⚠️ Clamped, a client asking for five years would draw four empty ones and
    conclude the workspace was empty in 2028."""
    with pytest.raises(HTTPException) as caught:
        window_bounds("2026-01-01", "2030-01-01")
    assert caught.value.status_code == 422
    assert str(MAX_WINDOW_DAYS) in caught.value.detail


def test_a_year_still_fits() -> None:
    start, end = window_bounds("2026-01-01", "2027-01-01")
    assert (end - start).days == 365


def test_the_maximum_window_is_exactly_allowed() -> None:
    # The boundary itself, so an off-by-one in the comparison shows up.
    start, end = window_bounds("2026-01-01", "2027-02-05")
    assert (end - start).days == MAX_WINDOW_DAYS


# ── The overlap rule, structurally ──────────────────────────────────────────

def test_overlap_uses_the_interval_not_the_due_date_alone() -> None:
    """⚠️ The claim the whole view rests on. `due_at BETWEEN :from AND :to` is
    the implementation everyone writes first, and it hides every task whose
    span crosses the window without ending in it."""
    assert "coalesce(t.due_at" in OVERLAPS
    assert "coalesce(CAST(t.start_date AS timestamp)" in OVERLAPS
    assert "BETWEEN" not in OVERLAPS


def test_the_start_date_is_anchored_to_UTC_not_to_the_session() -> None:
    """⚠️ `CAST(start_date AS timestamptz)` would compile and would silently
    read the connection's `TimeZone` — a session setting no caller controls,
    no test would notice changing, and which shifts every bar by hours."""
    assert "AT TIME ZONE 'UTC'" in OVERLAPS
    assert "CAST(t.start_date AS timestamptz)" not in OVERLAPS


def test_the_window_bounds_are_bound_parameters_not_interpolated() -> None:
    assert ":window_from" in OVERLAPS
    assert ":window_to" in OVERLAPS


def test_the_undated_clause_needs_both_dates_absent() -> None:
    """A task with only a start date IS on the calendar. Counting it as
    unscheduled would double-report it — drawn on the grid and tallied as
    missing from it."""
    assert UNDATED == "t.start_date IS NULL AND t.due_at IS NULL"


def test_the_query_reads_everything_in_the_window_rather_than_a_page() -> None:
    """⚠️ The reason this is a new endpoint at all. `OFFSET` here would be a
    calendar with silently missing days."""
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("async def get_calendar"):]
    assert "OFFSET" not in body
    assert ":cap" in body


def test_the_cap_is_probed_by_one_so_truncation_can_be_detected() -> None:
    """Reading exactly `MAX_WINDOW_ROWS` cannot tell "full" from "overflowing",
    so the query asks for one more than it will return."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "MAX_WINDOW_ROWS + 1" in source


#: The list endpoint's parameters the calendar deliberately does NOT take, and
#: why. Anything else the list grows must be added to the calendar too, which
#: is what the next test enforces.
NOT_ON_THE_CALENDAR = {
    # Duplicates the window: two ways to bound the same column, where the
    # losing one vanishes without a word.
    "due_before",
    # The window IS the shape; a calendar has no pages, no sort key and no
    # parent-task drill-down.
    "page", "sort", "direction", "parent_task_id",
}


def test_every_list_filter_is_accepted_by_the_calendar_or_named_as_excluded() -> None:
    """⚠️ The silent-drop trap, and the reason this test reads both signatures.

    FastAPI ignores an unknown query parameter. So a filter the board sends and
    the calendar does not declare is not an error — it is a filter that quietly
    stops applying when you switch view, which reads as the FILTER being broken
    rather than the calendar. The exclusions are listed above with a reason
    each; a new list filter that is neither accepted nor listed fails here.
    """
    from gateway.routes.projects import router

    def params(path: str) -> set[str]:
        route = next(r for r in router.routes if r.path == path)
        return {p.name for p in route.dependant.query_params}

    listed = params("/projects/tasks")
    calendared = params("/projects/calendar")
    missing = listed - calendared - NOT_ON_THE_CALENDAR
    assert missing == set(), (
        f"the list accepts {sorted(missing)} and the calendar does not — "
        f"FastAPI will drop them silently"
    )


# ── WS-27ac — the week layout is the SAME read, at a different width ─────────

#: The two windows the browser actually sends (`lib/calendar.calendarWindow`):
#: a month grid padded to whole weeks, and a week grid — each with the day of
#: slack the UTC reading needs, and each half-open. August 2026 runs Sat 1st to
#: Mon 31st, so its grid is Mon 27 Jul to Sun 6 Sep; the week of the 12th is
#: Mon 10 to Sun 16 Aug.
CLIENT_WINDOWS = {
    "month": ("2026-07-26", "2026-09-08"),
    "week": ("2026-08-09", "2026-08-18"),
}


@pytest.mark.parametrize("shape", sorted(CLIENT_WINDOWS))
def test_both_client_window_shapes_are_legal_windows(shape: str) -> None:
    """The week layout asks the SAME endpoint for ten days instead of
    forty-four. A week that tripped the minimum-width refusal, or that the
    endpoint had to grow a second parameter for, would be a second read."""
    start, end = window_bounds(*CLIENT_WINDOWS[shape])
    assert 0 < (end - start).days <= MAX_WINDOW_DAYS


@pytest.mark.parametrize("shape", sorted(CLIENT_WINDOWS))
@pytest.mark.asyncio
async def test_the_triage_exclusion_holds_at_every_window_width(
    db, events, shape: str,
) -> None:
    """⚠️ WS-27ac done-when 5, and the reason it is written as a WINDOW
    parametrisation rather than another route assertion.

    ``include_triage`` is declared once on ``/projects/calendar`` and
    ``test_no_surface_can_silently_drop_include_triage`` pins that. What that
    test cannot see is a surface that keeps the parameter and stops sending it
    — or, worse, a week layout that reached for its own read and inherited a
    filter set nobody compared. Both windows go through this endpoint, so the
    queue stays parked at whichever width is on screen, and asking still shows
    it.
    """
    date_from, date_to = CLIENT_WINDOWS[shape]
    project, todo, _ = _workspace(db)
    triage = db.seed_status(project.id, name="Triage", category="triage",
                            is_default=False, position=-10)
    db.seed_task(project.id, todo.id, title="Real",
                 due_at="2026-08-12T10:00:00Z")
    db.seed_task(project.id, triage.id, title="Parked",
                 due_at="2026-08-13T10:00:00Z")

    hidden = await pm_calendar.get_calendar(
        user=USER, date_from=date_from, date_to=date_to,
    )
    assert [r["title"] for r in hidden["rows"]] == ["Real"]

    shown = await pm_calendar.get_calendar(
        user=USER, date_from=date_from, date_to=date_to, include_triage=True,
    )
    assert {r["title"] for r in shown["rows"]} == {"Real", "Parked"}


@pytest.mark.parametrize("shape", sorted(CLIENT_WINDOWS))
@pytest.mark.asyncio
async def test_the_boards_filters_survive_at_every_window_width(
    db, events, shape: str,
) -> None:
    """The §11.16 rule — switching view changes the SHAPE of what is on screen,
    never the set — extended to switching LAYOUT. `overdue` is carried too,
    because it is the exclusion list's near-miss (see the test above it)."""
    date_from, date_to = CLIENT_WINDOWS[shape]
    project, todo, done = _workspace(db)
    db.seed_task(project.id, todo.id, title="Open", due_at="2026-08-12T10:00:00Z")
    db.seed_task(project.id, done.id, title="Closed", due_at="2026-08-13T10:00:00Z")
    db.seed_task(project.id, todo.id, title="Undated")

    result = await pm_calendar.get_calendar(
        user=USER, date_from=date_from, date_to=date_to,
        status_category="todo",
    )

    assert [r["title"] for r in result["rows"]] == ["Open"]
    # ⚠️ Counted with the same filters at either width: "1 unscheduled" must
    # mean one of the tasks being looked at, not one somewhere in the project.
    assert result["undated"] == 1


def test_the_week_layout_did_not_grow_its_own_endpoint() -> None:
    """⚠️ WS-27ac's whole point, on the server side: one window read, two
    renderings of it (three, counting the timeline — §11.17). A second path
    would get its own filter set, its own triage default and its own bugs.

    ⚠️ **Re-cut 2026-08-25 (WS-39 S1 repair round 1).** WS-39 S3a-server added
    `GET /projects/my/calendar` (D54 · D53.7, migration 187) and this
    exact-list assertion went red — unseen, because at the time `tests/unit`
    could not be COLLECTED at all (two modules imported the class D52 deleted)
    and CI's `-x` run died several files earlier.

    That second route is DELIBERATE and is not what this test guards against.
    The rule is *one TEAM-calendar window read, several renderings of it*.
    `/projects/my/calendar` answers a different question from a different
    store — MY blocks, out of `pm_task_personal`, scoped to the session with no
    `?member=` — so it is a sibling lens, not a second rendering path over the
    same rows. What must stay refused is a LAYOUT-specific endpoint: a `/week`
    that re-derives the same team window and then drifts from
    `/projects/calendar` one filter at a time. That half is unchanged, and the
    list stays EXACT, so a third calendar route still has to argue for itself
    here.
    """
    from gateway.routes.projects import router

    windows = sorted(r.path for r in router.routes if "calendar" in r.path)
    assert windows == ["/projects/calendar", "/projects/my/calendar"], (
        f"unexpected calendar route set: {windows}. One team window read "
        "(`/projects/calendar`) plus the personal lens "
        "(`/projects/my/calendar`, WS-39 S3a). A new entry needs a reason in "
        "this docstring."
    )
    assert not any("week" in r.path for r in router.routes)


def test_the_window_is_not_also_a_due_date_filter() -> None:
    """`due_before` stays out: bounding the same column twice is how the two
    bounds come to disagree, and the loser leaves no trace."""
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("async def get_calendar")
    signature = source[start:source.index(") -> dict:", start)]
    assert "due_before" not in signature


@pytest.mark.asyncio
async def test_an_overdue_filter_survives_the_switch_to_calendar(db, events):
    """⚠️ `overdue` looks like `due_before`'s twin and is not: "already late"
    is a fact about the status as much as the date. Dropped, a board filtered
    to overdue work would show everything the moment somebody switched view."""
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Old", due_at="2020-01-10T10:00:00Z")
    db.seed_task(project.id, todo.id, title="Later", due_at="2030-01-10T10:00:00Z")

    async def month(year: str, **kwargs):
        result = await pm_calendar.get_calendar(
            user=USER, date_from=f"{year}-01-01", date_to=f"{year}-02-01", **kwargs,
        )
        return [r["title"] for r in result["rows"]]

    # A window either side of any plausible "now", so the assertion is about
    # the clause rather than about what today happens to be.
    assert await month("2020") == ["Old"]
    assert await month("2020", overdue=True) == ["Old"]
    assert await month("2030") == ["Later"]
    assert await month("2030", overdue=True) == []


# ── Behaviour ───────────────────────────────────────────────────────────────

def _workspace(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Ops", subject="owner@fracktal.in")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


async def _window(**kwargs):
    return await pm_calendar.get_calendar(
        user=USER, date_from="2026-08-01", date_to="2026-09-01", **kwargs,
    )


@pytest.mark.asyncio
async def test_a_task_due_inside_the_window_is_returned(db, events):
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Ship it", due_at="2026-08-14T10:00:00Z")

    result = await _window()

    assert [r["title"] for r in result["rows"]] == ["Ship it"]
    assert result["from"] == "2026-08-01"
    assert result["to"] == "2026-09-01"


@pytest.mark.asyncio
async def test_a_task_with_only_a_start_date_is_on_the_calendar(db, events):
    """`start_date` has existed since migration 146 and no surface has shown
    it. A calendar that ignored it would leave the column exactly as
    unreachable as it was."""
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Kickoff", start_date="2026-08-03")

    assert [r["title"] for r in (await _window())["rows"]] == ["Kickoff"]


@pytest.mark.asyncio
async def test_a_task_with_neither_date_is_absent_and_counted(db, events):
    """⚠️ It falls out through NULL rather than through any clause a reader can
    see. Dropping it silently is how a calendar comes to look like the whole
    workspace while showing a third of it."""
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Someday")
    db.seed_task(project.id, todo.id, title="Dated", due_at="2026-08-14T10:00:00Z")

    result = await _window()

    assert [r["title"] for r in result["rows"]] == ["Dated"]
    assert result["undated"] == 1


@pytest.mark.asyncio
async def test_a_task_ending_before_the_window_is_absent(db, events):
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Last month", due_at="2026-07-20T10:00:00Z")

    assert (await _window())["rows"] == []


@pytest.mark.asyncio
async def test_a_task_starting_after_the_window_is_absent(db, events):
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Next month", start_date="2026-09-10")

    assert (await _window())["rows"] == []


@pytest.mark.asyncio
async def test_a_task_spanning_the_whole_window_is_present(db, events):
    """⚠️ The case `due_at BETWEEN` gets wrong: neither end is inside the
    window, and the task covers every day of it."""
    project, todo, _ = _workspace(db)
    db.seed_task(
        project.id, todo.id, title="The quarter",
        start_date="2026-06-01", due_at="2026-12-01T00:00:00Z",
    )

    assert [r["title"] for r in (await _window())["rows"]] == ["The quarter"]


@pytest.mark.asyncio
async def test_the_windows_last_day_is_excluded(db, events):
    """Half-open: a task due at the September boundary belongs to September."""
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="September", due_at="2026-09-01T00:00:00Z")

    assert (await _window())["rows"] == []


@pytest.mark.asyncio
async def test_the_calendar_honours_the_boards_filters(db, events):
    """⚠️ Switching view must change the SHAPE of what is on screen, never the
    set. A calendar that ignored the filter would read as the filter breaking."""
    project, todo, done = _workspace(db)
    db.seed_task(project.id, todo.id, title="Open", due_at="2026-08-10T10:00:00Z")
    db.seed_task(project.id, done.id, title="Closed", due_at="2026-08-11T10:00:00Z")

    result = await _window(status_category="todo")

    assert [r["title"] for r in result["rows"]] == ["Open"]


@pytest.mark.asyncio
async def test_the_unscheduled_count_uses_the_same_filters(db, events):
    """⚠️ Counted without them, "12 unscheduled" would mean twelve somewhere in
    the workspace rather than twelve of the tasks being looked at — a number
    that never matches what clicking it shows."""
    project, todo, done = _workspace(db)
    db.seed_task(project.id, todo.id, title="Open and undated")
    db.seed_task(project.id, done.id, title="Closed and undated")

    assert (await _window(status_category="todo"))["undated"] == 1


@pytest.mark.asyncio
async def test_an_archived_task_is_off_the_calendar_by_default(db, events):
    project, todo, _ = _workspace(db)
    db.seed_task(
        project.id, todo.id, title="Dropped", due_at="2026-08-10T10:00:00Z",
        archived_at="2026-08-01T00:00:00Z",
    )

    assert (await _window())["rows"] == []


@pytest.mark.asyncio
async def test_a_calendar_chip_carries_the_shared_card_badges(db, events):
    """WS-27s' vocabulary, on the third view too: a chip that could not draw a
    blocked flag would be a fourth way for a task to look different."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(
        project.id, todo.id, title="Ship it", due_at="2026-08-14T10:00:00Z",
    )

    row = (await _window())["rows"][0]

    assert row["subtasks"] == {"done": 0, "total": 0}
    assert row["blocked_by_count"] == 0
    assert "assignees" in row
    assert str(task.id) == row["id"]


@pytest.mark.asyncio
async def test_an_unreadable_project_is_a_404_not_an_empty_calendar(db, events):
    """R5. An empty calendar would confirm the project exists and is quiet."""
    _workspace(db)
    hidden = db.seed_project(name="Secret", subject=None)

    with pytest.raises(HTTPException) as caught:
        await pm_calendar.get_calendar(
            user=MEMBER, date_from="2026-08-01", date_to="2026-09-01",
            project_id=str(hidden.id),
        )
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_a_bad_window_is_refused_before_the_database_is_touched(db, events):
    """The 422 comes from a pure function, so a malformed window costs a
    connection rather than a query — and cannot half-run."""
    with pytest.raises(HTTPException) as caught:
        await pm_calendar.get_calendar(
            user=USER, date_from="not-a-date", date_to="2026-09-01",
        )
    assert caught.value.status_code == 422
    assert db.statements == []


@pytest.mark.asyncio
async def test_an_overflowing_window_says_so_and_returns_exactly_the_cap(
    db, events, monkeypatch: pytest.MonkeyPatch,
):
    """⚠️ The failure this endpoint exists to prevent. A calendar quietly
    missing a third of its tasks looks exactly like a calendar with fewer
    tasks, and nobody investigates a quiet week.

    The cap is lowered rather than the fixture raised: seeding a thousand tasks
    to assert a boolean is a slow test that proves the same thing.
    """
    monkeypatch.setattr(pm_calendar, "MAX_WINDOW_ROWS", 2)
    project, todo, _ = _workspace(db)
    for n in range(5):
        db.seed_task(
            project.id, todo.id, title=f"Task {n}",
            due_at=f"2026-08-1{n}T10:00:00Z",
        )

    result = await _window()

    assert result["truncated"] is True
    # The probe row must not leak into the answer: returning cap + 1 would put
    # a task on the calendar that the "showing 2 of many" notice says is not.
    assert len(result["rows"]) == 2
    assert result["cap"] == 2


@pytest.mark.asyncio
async def test_a_window_exactly_at_the_cap_is_not_truncated(db, events, monkeypatch):
    """The off-by-one: `>=` here would report every full window as short."""
    monkeypatch.setattr(pm_calendar, "MAX_WINDOW_ROWS", 2)
    project, todo, _ = _workspace(db)
    for n in range(2):
        db.seed_task(
            project.id, todo.id, title=f"Task {n}",
            due_at=f"2026-08-1{n}T10:00:00Z",
        )

    result = await _window()

    assert result["truncated"] is False
    assert len(result["rows"]) == 2


@pytest.mark.asyncio
async def test_a_full_window_is_not_reported_as_truncated(db, events):
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="One", due_at="2026-08-10T10:00:00Z")

    result = await _window()

    assert result["truncated"] is False
    assert result["cap"] == MAX_WINDOW_ROWS


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_the_calendar_route_is_actually_mounted() -> None:
    """⚠️ A module left out of ``__init__.py`` mounts nothing while every test
    that calls its function directly still passes."""
    from gateway.routes.projects import router

    assert "/projects/calendar" in {route.path for route in router.routes}


def test_the_wire_names_are_from_and_to_not_the_python_names() -> None:
    """`from` is a Python keyword, so the alias is the only thing standing
    between the documented API and `?date_from=`."""
    from gateway.routes.projects import router

    route = next(r for r in router.routes if r.path == "/projects/calendar")
    names = {p.alias for p in route.dependant.query_params}
    assert {"from", "to"} <= names


def test_no_second_way_to_move_a_task_was_added() -> None:
    """Dragging a card is a `PATCH /tasks/{id}` — the same validation, the same
    `field_change` activity, the same revert. A `POST /calendar/move` would be
    a second write path, which is how two paths start disagreeing about what is
    allowed."""
    source = SOURCE.read_text(encoding="utf-8")
    assert not re.search(r"@router\.(post|patch|put|delete)", source)


# ── WS-27t — the drawable dependency edges ──────────────────────────────────

@pytest.mark.asyncio
async def test_links_are_absent_unless_asked_for(db, events):
    """⚠️ Always PRESENT, always empty until requested. A missing key and an
    empty list read the same to a careless client, so "this window has no
    dependencies" must not be confused with "nobody asked"."""
    project, todo, _ = _workspace(db)
    a = db.seed_task(project.id, todo.id, title="A", due_at="2026-08-05T10:00:00Z")
    b = db.seed_task(project.id, todo.id, title="B", due_at="2026-08-10T10:00:00Z")
    db.seed(
        "pm_task_links", source_task_id=a.id, target_task_id=b.id,
        link_type="blocks", created_by="owner@fracktal.in",
    )

    assert (await _window())["links"] == []
    assert len((await _window(include_links=True))["links"]) == 1


@pytest.mark.asyncio
async def test_an_edge_names_both_ends_and_which_is_which(db, events):
    project, todo, _ = _workspace(db)
    a = db.seed_task(project.id, todo.id, title="A", due_at="2026-08-05T10:00:00Z")
    b = db.seed_task(project.id, todo.id, title="B", due_at="2026-08-10T10:00:00Z")
    db.seed(
        "pm_task_links", source_task_id=a.id, target_task_id=b.id,
        link_type="blocks", created_by="owner@fracktal.in",
    )

    edge = (await _window(include_links=True))["links"][0]

    # ⚠️ Named blocker/blocked rather than source/target: an arrow drawn the
    # wrong way round is a chart confidently asserting the opposite sequence,
    # and `source`/`target` do not say which is which to a reader.
    assert edge["blocker_id"] == str(a.id)
    assert edge["blocked_id"] == str(b.id)


@pytest.mark.asyncio
async def test_an_edge_to_a_task_outside_the_window_is_not_returned(db, events):
    """⚠️ An arrow needs two bars. The edge is not lost — `blocked_by_count`
    already badges the visible bar — but a half-edge would be drawn to a point
    the chart has to invent."""
    project, todo, _ = _workspace(db)
    inside = db.seed_task(
        project.id, todo.id, title="Inside", due_at="2026-08-10T10:00:00Z",
    )
    outside = db.seed_task(
        project.id, todo.id, title="Outside", due_at="2026-12-10T10:00:00Z",
    )
    db.seed(
        "pm_task_links", source_task_id=outside.id, target_task_id=inside.id,
        link_type="blocks", created_by="owner@fracktal.in",
    )

    result = await _window(include_links=True)

    assert result["links"] == []
    # The information survives as a badge rather than as an arrow.
    assert result["rows"][0]["blocked_by_count"] == 1


@pytest.mark.asyncio
async def test_only_blocks_is_drawn_as_an_arrow(db, events):
    """`relates_to` and `duplicates` are associations with no direction that
    means anything to a schedule (WS-27p's `DIRECTED_TYPES`). Drawing them
    would claim a sequence nobody asserted."""
    project, todo, _ = _workspace(db)
    a = db.seed_task(project.id, todo.id, title="A", due_at="2026-08-05T10:00:00Z")
    b = db.seed_task(project.id, todo.id, title="B", due_at="2026-08-10T10:00:00Z")
    for link_type in ("relates_to", "duplicates"):
        db.seed(
            "pm_task_links", source_task_id=a.id, target_task_id=b.id,
            link_type=link_type, created_by="owner@fracktal.in",
        )

    assert (await _window(include_links=True))["links"] == []


@pytest.mark.asyncio
async def test_the_edges_are_one_query_for_the_whole_window(db, events):
    project, todo, _ = _workspace(db)
    made = [
        db.seed_task(
            project.id, todo.id, title=f"T{n}", due_at=f"2026-08-1{n}T10:00:00Z",
        )
        for n in range(6)
    ]
    for earlier, later in pairwise(made):
        db.seed(
            "pm_task_links", source_task_id=earlier.id, target_task_id=later.id,
            link_type="blocks", created_by="owner@fracktal.in",
        )

    db.statements.clear()
    result = await _window(include_links=True)

    assert len(result["links"]) == 5
    # `"AS blocker"` would ALSO match `_BLOCKER_COUNTS_SQL`'s `AS blockers` —
    # the same substring collision the fake's own dispatch has to avoid, and it
    # bit this assertion first. The trailing comma is what makes it specific.
    assert len([s for s in db.statements if "AS blocker," in s]) == 1


def test_the_edge_query_requires_both_ends_in_the_window() -> None:
    """Structural: the fake mirrors this in Python, so only the statement can
    say whether the route still asks for it."""
    source = Path(
        "apps/services/gateway/gateway/routes/projects/filters.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"_WINDOW_LINKS_SQL = \"\"\"(.*?)\"\"\"", source, re.S)
    assert match is not None
    body = match.group(1)
    assert body.count("= ANY(CAST(:ids AS uuid[]))") == 2
    assert "l.link_type = 'blocks'" in body
