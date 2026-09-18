"""WS-27ae (the export third) — the filtered-list CSV export.

Spec: ``project-docs/specs/project_management_app.md`` §9.2, §11.26.

The claims worth pinning are the ones where the wrong implementation still
produces a file somebody opens and believes:

* **the export is the caller's FILTER, not the project.** An export that
  quietly means "everything" is a spreadsheet that looks authoritative and is
  not, and nobody re-derives a filter from a CSV to check.
* **the columns are the view's, in the order the table draws them.** The stored
  ``shown_fields`` list is a SET; following its order would make the file's
  columns differ from the screen's while both were "right".
* **it is never truncated.** A short page announces itself; a short CSV does
  not. So the cap is a REFUSAL naming the real count, and there is no code path
  that returns a partial file.
* **a formula does not execute.** A title starting ``=`` is a cell Excel runs
  with the credentials of whoever double-clicks the file. Quoting does not stop
  it; the guard does, and this file pins the exact bytes.
* **the tenant seam and the visibility clause are the ones every other read in
  this package uses** — no second session idiom, no second scoping.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import custom_fields as pm_custom_fields
from gateway.routes.projects import export as pm_export
from gateway.routes.projects import me as pm_me
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree
from gateway.routes.projects import views as pm_views
from gateway.routes.projects.export import (
    BOM,
    FIELD_LABELS,
    FORMULA_LEADERS,
    INJECTION_GUARD,
    MAX_EXPORT_ROWS,
    UNCONDITIONAL_HEADERS,
    csv_cell,
    resolve_columns,
)
from gateway.routes.projects.filters import SHOWN_FIELDS

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    projects_user,
    silence_events,
)

MODULES = (
    pm_core, pm_tree, pm_tasks, pm_activities, pm_admin, pm_views, pm_me,
    pm_custom_fields, pm_export,
)
USER = projects_user()
#: Holds `feature:projects` but NOT `data:org:read`, so the grant closure is
#: what decides what they can export — an unscoped route passes the owner test.
MEMBER = member_user("colleague@fracktal.in")

SOURCE = Path("apps/services/gateway/gateway/routes/projects/export.py")
SHOWN_FIELDS_TS = Path(
    "workbench/control_plane/src/app/projects/lib/shownFields.ts"
)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


def _workspace(db: FakeProjectsDB, *, subject: str = "owner@fracktal.in") -> tuple:
    project = db.seed_project(name="Ops", subject=subject)
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


async def _export(**kwargs) -> tuple[str, dict[str, str]]:
    """Run the endpoint and hand back ``(csv_text_without_BOM, headers)``."""
    response = await pm_export.export_tasks_csv(user=kwargs.pop("user", USER), **kwargs)
    body = response.body.decode("utf-8")
    assert body.startswith(BOM), "the UTF-8 BOM is what makes Excel read it"
    return body[len(BOM):], dict(response.headers)


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


# ── csv_cell — the injection decision, pinned byte for byte ─────────────────

@pytest.mark.parametrize("leader", FORMULA_LEADERS)
def test_a_cell_that_starts_a_formula_is_neutralised(leader: str) -> None:
    """⚠️ The payload is `=cmd|'/c calc'!A0` and it executes on the machine of
    whoever double-clicks the file — with their credentials, from a file the
    company's own tool produced. The guard is one visible, reversible character
    in front; a formula that runs has no such tell."""
    assert csv_cell(f"{leader}SUM(A1:A9)") == f"{INJECTION_GUARD}{leader}SUM(A1:A9)"


def test_quoting_is_not_the_mitigation_and_the_guard_is() -> None:
    """⚠️ The mistake this test exists to prevent: `QUOTE_ALL` looks like it
    solves injection and does not — a spreadsheet evaluates the cell AFTER the
    CSV quoting is stripped. Only the leading character matters."""
    guarded = csv_cell("=1+1")
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow([guarded])
    # Round-tripped, the cell still carries the guard — the quoting did not
    # absorb it and did not add one of its own.
    assert _rows(out.getvalue())[0] == ["'=1+1"]


def test_a_bare_number_keeps_its_minus_sign() -> None:
    """Without the exemption every negative number would export as text and
    the sums people open a spreadsheet to compute would silently stop working.
    `-5` is a number to Excel; `-5+cmd` is not, and is still guarded."""
    assert csv_cell("-30") == "-30"
    assert csv_cell(-30) == "-30"
    assert csv_cell("-5.5") == "-5.5"
    assert csv_cell("+7") == "+7"
    assert csv_cell("-5+cmd|'/c calc'!A0") == "'-5+cmd|'/c calc'!A0"


def test_none_is_an_empty_cell_not_the_word_None() -> None:
    assert csv_cell(None) == ""
    assert csv_cell("") == ""


def test_ordinary_text_is_untouched() -> None:
    assert csv_cell("Ship the release") == "Ship the release"
    assert csv_cell("re: the -30% case") == "re: the -30% case"


def test_a_comma_a_quote_and_a_newline_survive_a_round_trip() -> None:
    """The three cases every hand-rolled CSV writer gets wrong. `csv` is used
    rather than a join precisely so this is the module's problem, not ours."""
    nasty = 'Ship "v2", now\nand tomorrow'
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow([csv_cell(nasty), "x"])
    assert _rows(out.getvalue())[0] == [nasty, "x"]


def test_the_worst_single_title_survives_intact_and_inert() -> None:
    """Comma + quote + newline + a leading `=`, in one title, through the real
    writer — the acceptance case, asserted on the parsed value AND on the raw
    bytes so neither the guard nor the quoting can quietly stop working."""
    nasty = '=SUM(A1),"drop"\nline two'
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL).writerow(
        [csv_cell(nasty)],
    )
    raw = out.getvalue()
    assert _rows(raw)[0] == [f"{INJECTION_GUARD}{nasty}"]
    assert raw.startswith('"\'=SUM(A1),""drop""')


# ── The column contract ─────────────────────────────────────────────────────

def test_every_shown_field_key_has_a_header() -> None:
    """A key the vocabulary knows and this map does not would `KeyError` the
    whole export the first time somebody ticked that column."""
    assert set(FIELD_LABELS) == set(SHOWN_FIELDS)


def test_the_headers_are_the_clients_own_labels() -> None:
    """⚠️ R7 fence. The header row must read like the screen's, and a hand-kept
    mirror of a TypeScript constant goes stale silently — so this reads
    `FIELD_LABELS` out of `shownFields.ts` rather than restating it."""
    source = SHOWN_FIELDS_TS.read_text(encoding="utf-8")
    block = source[source.index("FIELD_LABELS"):]
    block = block[block.index("{"):block.index("};")]
    from_ts = dict(re.findall(r"(\w+):\s*\"([^\"]*)\"", block))
    assert from_ts == FIELD_LABELS, (
        "the CSV headers drifted from lib/shownFields.ts FIELD_LABELS"
    )


def test_the_number_and_the_title_are_unconditional() -> None:
    """A row you cannot identify is not a row: without the number a line
    cannot be matched back to a task, and without the title nobody can read
    it. Neither is a `shown_fields` key, so neither can be turned off."""
    assert UNCONDITIONAL_HEADERS == ("#", "Title")
    assert not set(UNCONDITIONAL_HEADERS) & set(FIELD_LABELS.values())


def test_columns_follow_the_vocabulary_order_not_the_stored_order() -> None:
    """⚠️ `shown_fields` is a SET (`shownFields.ts` says so) and the table draws
    it in the vocabulary's declaration order. Honouring the stored order here
    would make the file's columns differ from the screen's — the same
    disagreement the one-filter-builder rule exists to prevent, one level
    down."""
    assert resolve_columns("tags,status,due_at", []) == [
        ("status", "Status"), ("due_at", "Due"), ("tags", "Tags"),
    ]


def test_an_unknown_field_key_is_dropped_not_refused() -> None:
    """Same forgiveness `normalise_view_config` gives a stored view: a key from
    an older or newer client must not make the export fail."""
    assert resolve_columns("status,not_a_field,due_at", []) == [
        ("status", "Status"), ("due_at", "Due"),
    ]


def test_no_shown_fields_means_only_the_unconditional_pair() -> None:
    """Not the client's DEFAULT_SHOWN: the column set belongs to the caller's
    VIEW, the server holds none, and mirroring the default here would be a
    second copy of a preference that drifts."""
    assert resolve_columns(None, []) == []
    assert resolve_columns("", []) == []


def test_custom_fields_come_last_in_their_registry_order() -> None:
    """`load_definitions` is what orders them (`ORDER BY position, name`), so
    this consumes that order rather than re-deriving it — two sorts is two
    opinions about which column comes first."""
    defs = [
        {"field_key": "squad", "name": "Squad", "position": 0},
        {"field_key": "risk", "name": "Risk", "position": 1},
    ]
    assert resolve_columns("custom.risk,status,custom.squad", defs) == [
        ("status", "Status"), ("custom.squad", "Squad"), ("custom.risk", "Risk"),
    ]


def test_a_custom_field_deleted_after_the_save_produces_no_column() -> None:
    """The table's own rule: a header with no data and no editor under it is a
    column of nothing."""
    assert resolve_columns("custom.gone,status", []) == [("status", "Status")]


# ── Wiring and the parameter surface ────────────────────────────────────────

def test_the_export_is_mounted() -> None:
    from gateway.routes.projects import router

    assert "/projects/export/tasks.csv" in {r.path for r in router.routes}


def test_the_path_cannot_be_shadowed_by_the_single_task_route() -> None:
    """⚠️ `/projects/tasks/export.csv` is the obvious spelling and it would be
    answered by `/projects/tasks/{task_id}` or not, depending on registration
    order — the one trap `__init__.py` promises this package does not have."""
    from gateway.routes.projects import router

    assert "/projects/tasks/export.csv" not in {r.path for r in router.routes}


#: List parameters the export deliberately does NOT take, and why.
NOT_ON_THE_EXPORT = {
    # A page is a screen's unit. Half a filter's tasks in a file IS the silent
    # truncation this endpoint refuses; the cap is a refusal instead.
    "page", "page_size",
    # ⚠️ **`view_id` is an ORDERING join, not a filter** (H-64). It reads one
    # saved view's hand-arranged `pm_view_task_positions` so a board column
    # can honour a drag. A CSV has one order — the export's own — and adding
    # a second would make the file's row order depend on who last dragged a
    # card. Excluded rather than added, deliberately.
    "view_id",
}


def test_every_list_filter_reaches_the_export_or_is_named_as_excluded() -> None:
    """⚠️ The silent-drop trap, borrowed from the calendar's version of this
    test. FastAPI ignores an unknown query parameter, so a filter the board
    sends and the export does not declare is not an error — it is a filter that
    stops applying the moment you press Export, and the file looks fine."""
    from gateway.routes.projects import router

    def params(path: str) -> set[str]:
        route = next(r for r in router.routes if r.path == path)
        return {p.name for p in route.dependant.query_params}

    missing = params("/projects/tasks") - params("/projects/export/tasks.csv")
    assert missing - NOT_ON_THE_EXPORT == set(), (
        f"the list accepts {sorted(missing - NOT_ON_THE_EXPORT)} and the "
        f"export does not — FastAPI will drop them silently"
    )


def test_the_filters_come_from_the_one_shared_builder() -> None:
    """⚠️ A second filter parser drifts, and then the export and the screen
    disagree about what "my open bugs in Ops" means."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "build_task_filters(" in source
    assert "task_visibility_clause(vis)" in source


def test_there_is_no_partial_file_path_at_all() -> None:
    """⚠️ The structural half of the never-truncated rule: `OFFSET` would page
    the export, and the row query's LIMIT is the cap the count already
    refused past — there is no branch that renders fewer rows than matched."""
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("async def export_tasks_csv"):]
    assert "OFFSET" not in body
    assert "MAX_EXPORT_ROWS + 1" not in body, (
        "probing past the cap is the calendar's truncate-and-say-so trade; "
        "this endpoint refuses instead"
    )


def test_the_session_comes_from_the_package_seam(db) -> None:
    """R5/H2: no `get_db()`, no engine, no second idiom."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "_tenant_session()" in source
    # The call FORMS the H2 ratchet counts, not the string — the docstring
    # names `get_db()` to explain why it is absent.
    assert not re.search(r"await _?get_db\(\)", source)
    assert "create_async_engine" not in source


# ── Behaviour ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_export_carries_the_number_and_title_of_every_matching_task(
    db, events,
) -> None:
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Ship it", task_number=1)
    db.seed_task(project.id, todo.id, title="Fix it", task_number=2)

    text, headers = await _export(project_id=project.id, sort="task_number",
                                  direction="asc")

    assert _rows(text) == [["#", "Title"], ["1", "Ship it"], ["2", "Fix it"]]
    assert headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in headers["content-disposition"]
    assert headers["x-export-rows"] == "2"


@pytest.mark.asyncio
async def test_the_export_honours_the_callers_filter_not_the_whole_project(
    db, events,
) -> None:
    """⚠️ The acceptance criterion. An export that silently means "every task in
    the project" is the failure this ticket was written against."""
    project, todo, done = _workspace(db)
    db.seed_task(project.id, todo.id, title="Open one", task_number=1)
    db.seed_task(project.id, done.id, title="Finished", task_number=2)

    text, _ = await _export(project_id=project.id, status_category="todo")

    assert [row[1] for row in _rows(text)[1:]] == ["Open one"]


@pytest.mark.asyncio
async def test_a_text_filter_reaches_the_export_too(db, events) -> None:
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Ship the release", task_number=1)
    db.seed_task(project.id, todo.id, title="Write the notes", task_number=2)

    text, _ = await _export(project_id=project.id, q="release")

    assert [row[1] for row in _rows(text)[1:]] == ["Ship the release"]


@pytest.mark.asyncio
async def test_the_columns_are_the_views_shown_fields(db, events) -> None:
    project, todo, _ = _workspace(db)
    task = db.seed_task(
        project.id, todo.id, title="Ship it", task_number=7,
        due_at="2026-08-14T10:00:00Z", tags=["bug", "ops"],
    )
    db.seed("pm_task_assignees", task_id=task.id, assignee="priya@fracktal.in")

    text, _ = await _export(
        project_id=project.id, shown_fields="tags,status,assignees,due_at",
    )
    rows = _rows(text)

    assert rows[0] == ["#", "Title", "Status", "Assignees", "Due", "Tags"]
    assert rows[1][:2] == ["7", "Ship it"]
    assert rows[1][2] == "To do"
    assert rows[1][3] == "priya@fracktal.in"
    assert rows[1][4].startswith("2026-08-14")
    assert rows[1][5] == "bug, ops"


@pytest.mark.asyncio
async def test_a_field_the_view_hides_produces_no_column(db, events) -> None:
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Ship it", task_number=1,
                 due_at="2026-08-14T10:00:00Z")

    text, _ = await _export(project_id=project.id, shown_fields="status")

    assert _rows(text)[0] == ["#", "Title", "Status"]


@pytest.mark.asyncio
async def test_a_hostile_title_arrives_intact_and_inert(db, events) -> None:
    """The end-to-end version of the injection case: through the route, the
    real writer and a real CSV parse."""
    project, todo, _ = _workspace(db)
    nasty = '=SUM(A1),"drop"\nline two'
    db.seed_task(project.id, todo.id, title=nasty, task_number=1)

    text, _ = await _export(project_id=project.id)

    assert _rows(text)[1] == ["1", f"{INJECTION_GUARD}{nasty}"]


@pytest.mark.asyncio
async def test_the_cap_refuses_rather_than_handing_back_a_partial_file(
    db, events, monkeypatch,
) -> None:
    """⚠️ The whole design decision, in one assertion. A truncated CSV looks
    exactly like a complete one — nobody scrolls to the bottom of a spreadsheet
    to check whether it ended early — so the refusal names the real count and
    the cap, and no file is produced at all."""
    monkeypatch.setattr(pm_export, "MAX_EXPORT_ROWS", 2)
    project, todo, _ = _workspace(db)
    for n in range(3):
        db.seed_task(project.id, todo.id, title=f"Task {n}", task_number=n)

    with pytest.raises(HTTPException) as caught:
        await _export(project_id=project.id)

    assert caught.value.status_code == 422
    assert "3 tasks match" in caught.value.detail
    assert "2" in caught.value.detail


@pytest.mark.asyncio
async def test_exactly_the_cap_is_exported_in_full(db, events, monkeypatch) -> None:
    """The boundary itself, so an off-by-one in the comparison shows up as a
    refusal of a set that fits."""
    monkeypatch.setattr(pm_export, "MAX_EXPORT_ROWS", 2)
    project, todo, _ = _workspace(db)
    for n in range(2):
        db.seed_task(project.id, todo.id, title=f"Task {n}", task_number=n)

    text, _ = await _export(project_id=project.id)

    assert len(_rows(text)) == 3  # header + two


@pytest.mark.asyncio
async def test_a_filter_that_narrows_under_the_cap_exports(
    db, events, monkeypatch,
) -> None:
    """The refusal's own advice has to work: narrowing the filter is what the
    422 tells the caller to do."""
    monkeypatch.setattr(pm_export, "MAX_EXPORT_ROWS", 2)
    project, todo, done = _workspace(db)
    for n in range(3):
        db.seed_task(project.id, todo.id, title=f"Open {n}", task_number=n)
    db.seed_task(project.id, done.id, title="Closed", task_number=9)

    text, _ = await _export(project_id=project.id, status_category="done")

    assert [row[1] for row in _rows(text)[1:]] == ["Closed"]


@pytest.mark.asyncio
async def test_a_project_the_caller_cannot_see_is_a_404_not_an_empty_file(
    db, events,
) -> None:
    """R5, this package's rule everywhere: an empty file would confirm the
    project exists and is simply quiet."""
    project, todo, _ = _workspace(db, subject="someone-else@fracktal.in")
    db.seed_task(project.id, todo.id, title="Secret", task_number=1)

    with pytest.raises(HTTPException) as caught:
        await _export(project_id=project.id, user=MEMBER)

    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_an_unscoped_export_carries_only_what_the_caller_may_see(
    db, events,
) -> None:
    """⚠️ Without a project the export spans everything the caller can read —
    which is exactly where a missing visibility clause would leak the whole
    company into a downloadable file."""
    mine, mine_todo, _ = _workspace(db, subject="colleague@fracktal.in")
    theirs = db.seed_project(name="Finance", subject="someone-else@fracktal.in")
    theirs_todo = db.seed_status(theirs.id, name="To do", category="todo")
    db.seed_task(mine.id, mine_todo.id, title="Mine", task_number=1)
    db.seed_task(theirs.id, theirs_todo.id, title="Theirs", task_number=2)

    text, _ = await _export(user=MEMBER)

    assert [row[1] for row in _rows(text)[1:]] == ["Mine"]


@pytest.mark.asyncio
async def test_an_unknown_sort_key_is_a_422(db, events) -> None:
    project, _, _ = _workspace(db)
    with pytest.raises(HTTPException) as caught:
        await _export(project_id=project.id, sort="whenever")
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_the_attachment_column_counts_rather_than_repeating_the_screens_dash(
    db, events,
) -> None:
    """The one column the file knows more than the table does: `lib/card.ts`
    says the LIST endpoint does not count attachments, so the table honestly
    draws "—". An export carrying that ignorance would be a column of
    nothing."""
    project, todo, _ = _workspace(db)
    task = db.seed_task(project.id, todo.id, title="Ship it", task_number=1)
    db.seed_task(project.id, todo.id, title="Bare", task_number=2)
    for _ in range(2):
        db.seed("pm_task_attachments", task_id=task.id, attachment_id="a")

    text, _ = await _export(
        project_id=project.id, shown_fields="attachments", sort="task_number",
        direction="asc",
    )
    rows = _rows(text)

    assert rows[0] == ["#", "Title", "Files"]
    assert rows[1][2] == "2"
    assert rows[2][2] == ""


@pytest.mark.asyncio
async def test_the_session_is_opened_through_the_seam_exactly_once(
    db, events,
) -> None:
    """H2: one bound session for the whole read, the ambient form every other
    request handler in this package uses."""
    project, todo, _ = _workspace(db)
    db.seed_task(project.id, todo.id, title="Ship it", task_number=1)

    await _export(project_id=project.id)

    assert db.bound_tenants == [None]


def test_the_cap_is_a_stated_number_not_a_magic_literal() -> None:
    assert isinstance(MAX_EXPORT_ROWS, int)
    assert MAX_EXPORT_ROWS > 0
    assert f"{MAX_EXPORT_ROWS}" not in SOURCE.read_text(encoding="utf-8").split(
        "MAX_EXPORT_ROWS = ", 1,
    )[1].split("\n", 1)[1]
