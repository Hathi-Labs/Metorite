"""WS-27u — intake/triage: the front door.

Spec: ``ai-company-brain/specs/project_management_app.md`` §9.1 (WS-27u).

Hermetic, per the standing protocol: no Postgres, no TestClient — route
functions called directly against ``_projects_fakes.FakeProjectsDB`` with
``_get_db`` monkeypatched per module.

The claims worth pinning, because each has a plausible wrong implementation:

* **capture is ONE transaction** — a task without a wrapper is a capture with
  no provenance; a wrapper without a task points at nothing.
* **accept flips the status IN PLACE** — a copy-on-accept forks the history at
  exactly the moment it starts to matter, so "the table still holds one task"
  is asserted, not assumed.
* **the wrapper is permanent** — all four rulings UPDATE it; nothing deletes
  it, and that is pinned both behaviourally and structurally (no ``DELETE
  FROM pm_intake`` anywhere in the module).
* **the default-list exclusion is one predicate, honoured by every surface** —
  list, calendar and search each hide triage-parked tasks unless
  ``include_triage`` is passed, and a structural test keeps any surface from
  silently dropping the flag (FastAPI ignores unknown query parameters — the
  §11.16 trap, extended here as the ticket requires).
* **snooze reappears by being read** — ``snoozed_until <= now()`` in the queue
  query, no worker, so the test moves the clock by editing the row rather than
  by flipping any status.
* **R5** — capture into an unseen project, and every ruling on an unseen task,
  is a 404, never a 403; the queue is scoped by the same grants as the tasks.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import calendar as pm_calendar
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import intake as pm_intake
from gateway.routes.projects import search as pm_search
from gateway.routes.projects import tasks as pm_tasks

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    page,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_intake, pm_tasks, pm_calendar, pm_search)
USER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    return silence_events(monkeypatch, MODULES)


def _workspace(db: FakeProjectsDB, *, subject: str | None = "org") -> tuple:
    project = db.seed_project(name="Ops", subject=subject)
    todo = db.seed_status(project.id, name="To do", category="todo",
                          is_default=True, position=20)
    return project, todo


def _triage(db: FakeProjectsDB, project) -> object:
    return db.seed_status(project.id, name="Triage", category="triage",
                          is_default=False, position=5)


def _captured(db: FakeProjectsDB, project, triage, **columns) -> object:
    """A parked task with its wrapper — the state every ruling starts from."""
    task = db.seed_task(project.id, triage.id, **columns)
    db.seed("pm_intake", task_id=task.id, status="pending",
            created_by="owner@fracktal.in")
    return task


def _intake_activities(db: FakeProjectsDB) -> list[dict]:
    return [
        a for a in db.activities("system")
        if isinstance(a.get("meta"), dict) and "intake" in a["meta"]
    ]


# ── Capture — task + wrapper, one transaction ───────────────────────────────

async def test_capture_creates_task_and_wrapper_in_one_transaction(
    db: FakeProjectsDB, events: list,
) -> None:
    project, _ = _workspace(db)
    _triage(db, project)

    result = await pm_intake.capture_intake(
        pm_intake.IntakeIn(project_id=str(project.id), title="From the inbox",
                           source="email", source_ref="msg-123"),
        user=USER,
    )

    # ONE commit covering both writes and the timeline entry — the contract,
    # not an optimisation: neither half may ever exist alone.
    assert db.committed == 1
    tasks = db.rows("pm_tasks")
    wrappers = db.rows("pm_intake")
    assert len(tasks) == 1 and len(wrappers) == 1
    assert str(wrappers[0]["task_id"]) == str(tasks[0]["id"])
    assert wrappers[0]["status"] == "pending"
    assert wrappers[0]["source"] == "email"
    assert wrappers[0]["source_ref"] == "msg-123"
    assert result["intake"]["status"] == "pending"
    assert result["task"]["title"] == "From the inbox"
    # A capture from a channel the task vocabulary knows is stamped on the
    # task too, so it reads like an email-created task everywhere else.
    assert tasks[0]["source"] == "email"
    # The birth is on the timeline.
    assert any(a["meta"]["intake"] == "captured" for a in _intake_activities(db))
    assert ("pm.intake.captured", {
        "task_id": result["task"]["id"], "project_id": str(project.id),
        "title": "From the inbox", "source": "email",
    }) in events


async def test_a_captured_task_lands_in_a_triage_category_status(
    db: FakeProjectsDB, events: list,
) -> None:
    """The status IS the parking mechanism — the wrapper alone hides nothing."""
    project, _ = _workspace(db)
    triage = _triage(db, project)

    result = await pm_intake.capture_intake(
        pm_intake.IntakeIn(project_id=str(project.id), title="Parked"),
        user=USER,
    )
    assert result["task"]["status_id"] == str(triage.id)


async def test_capture_provisions_a_triage_status_when_the_project_has_none(
    db: FakeProjectsDB, events: list,
) -> None:
    """Provisioned on first use, following how defaults are seeded — and never
    `is_default`, so an ordinary create can never land in it by omission."""
    project, _ = _workspace(db)

    result = await pm_intake.capture_intake(
        pm_intake.IntakeIn(project_id=str(project.id), title="First capture"),
        user=USER,
    )

    lanes = [s for s in db.rows("pm_task_statuses") if s["category"] == "triage"]
    assert len(lanes) == 1
    assert lanes[0]["is_default"] is False
    assert result["task"]["status_id"] == str(lanes[0]["id"])

    # The second capture REUSES it rather than minting a lane per capture.
    await pm_intake.capture_intake(
        pm_intake.IntakeIn(project_id=str(project.id), title="Second capture"),
        user=USER,
    )
    assert len([s for s in db.rows("pm_task_statuses")
                if s["category"] == "triage"]) == 1


async def test_capture_requires_a_title_and_a_project(
    db: FakeProjectsDB, events: list,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await pm_intake.capture_intake(
            pm_intake.IntakeIn(project_id=None, title="x"), user=USER,
        )
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await pm_intake.capture_intake(
            pm_intake.IntakeIn(project_id="p", title="   "), user=USER,
        )
    assert exc.value.status_code == 422


@pytest.mark.parametrize("leveraged", [True, False])
async def test_a_capture_writes_leveraged(
    db: FakeProjectsDB, events: list, leveraged: bool,
) -> None:
    """H-196. `IntakeIn` had no `leveraged`, and pydantic drops an unknown key
    without a word, so a capture could not be High-Leverage. It is written
    the way create writes it, and the answer carries it back."""
    project, _ = _workspace(db)
    result = await pm_intake.capture_intake(
        pm_intake.IntakeIn(project_id=str(project.id), title="Big bet",
                           importance=2, leveraged=leveraged),
        user=USER,
    )
    row = db.rows("pm_tasks")[0]
    assert row["leveraged"] is leveraged
    assert row["importance"] == 2
    assert result["task"]["leveraged"] is leveraged


async def test_a_capture_that_states_no_leveraged_writes_none(
    db: FakeProjectsDB, events: list,
) -> None:
    """The create rule: an unstated flag is not written, so the column
    default holds (migration 218, `DEFAULT false`)."""
    project, _ = _workspace(db)
    result = await pm_intake.capture_intake(
        pm_intake.IntakeIn(project_id=str(project.id), title="Plain"), user=USER,
    )
    assert "leveraged" not in db.rows("pm_tasks")[0]
    assert result["task"]["leveraged"] is False


def test_intake_takes_the_priority_fields_create_takes() -> None:
    """A priority field on `TaskIn` and not on `IntakeIn` is the H-196 gap
    again: the chat would send it and the route would drop it."""
    wanted = {"importance", "leveraged", "due_at"}
    assert wanted <= set(pm_core.TaskIn.model_fields)
    assert wanted <= set(pm_intake.IntakeIn.model_fields)


# ── The default-list exclusion — every surface, one predicate ───────────────

async def test_triage_tasks_are_invisible_to_the_task_list_by_default(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db)
    triage = _triage(db, project)
    db.seed_task(project.id, todo.id, title="Real work")
    db.seed_task(project.id, triage.id, title="Parked capture")

    listed = await pm_tasks.list_tasks(user=USER, page=page())
    assert [r["title"] for r in listed.rows] == ["Real work"]
    assert listed.total == 1

    included = await pm_tasks.list_tasks(user=USER, page=page(),
                                         include_triage=True)
    assert included.total == 2


async def test_triage_tasks_are_invisible_to_the_calendar_and_timeline(
    db: FakeProjectsDB, events: list,
) -> None:
    """One endpoint serves both renderings (§11.17), so this covers both."""
    project, todo = _workspace(db)
    triage = _triage(db, project)
    db.seed_task(project.id, todo.id, title="Real",
                 due_at="2026-08-10T10:00:00Z")
    db.seed_task(project.id, triage.id, title="Parked",
                 due_at="2026-08-11T10:00:00Z")

    month = await pm_calendar.get_calendar(
        user=USER, date_from="2026-08-01", date_to="2026-09-01",
    )
    assert [r["title"] for r in month["rows"]] == ["Real"]

    included = await pm_calendar.get_calendar(
        user=USER, date_from="2026-08-01", date_to="2026-09-01",
        include_triage=True,
    )
    assert {r["title"] for r in included["rows"]} == {"Real", "Parked"}


async def test_triage_tasks_are_invisible_to_search_by_default(
    db: FakeProjectsDB, events: list,
) -> None:
    """Search reaches every task in the app — the surface where leaking the
    queue costs most, and the one the duplicate picker needs the flag on."""
    project, todo = _workspace(db)
    triage = _triage(db, project)
    db.seed_task(project.id, todo.id, title="widget refactor")
    db.seed_task(project.id, triage.id, title="widget capture")

    hidden = await pm_search.search_tasks(q="widget", user=USER)
    assert [r["title"] for r in hidden["rows"]] == ["widget refactor"]

    shown = await pm_search.search_tasks(q="widget", include_triage=True,
                                         user=USER)
    assert {r["title"] for r in shown["rows"]} == {
        "widget refactor", "widget capture",
    }


def test_no_surface_can_silently_drop_include_triage() -> None:
    """⚠️ The §11.16 parameter-coverage rule, extended to WS-27u's flag.

    FastAPI ignores an unknown query parameter, so a surface that stops
    declaring ``include_triage`` would not error — the flag would quietly stop
    applying on that surface, which reads as the FILTER breaking. Board and
    list share ``/projects/tasks``; calendar and timeline share
    ``/projects/calendar`` (§11.17); search is its own read.

    WS-27ae added a fifth: ``/projects/delta/tasks``. A sync feed that leaked
    the triage queue would be the worst of the five, because the client that
    consumes it is a phone or an agent that caches what it receives.
    """
    from gateway.routes.projects import router

    def params(path: str) -> set[str]:
        route = next(r for r in router.routes if r.path == path)
        return {p.name for p in route.dependant.query_params}

    for path in (
        "/projects/tasks", "/projects/calendar", "/projects/search",
        "/projects/delta/tasks",
    ):
        assert "include_triage" in params(path), (
            f"{path} does not declare include_triage — FastAPI will drop it "
            f"silently and the surface will hide the queue with no way to ask"
        )


def test_the_exclusion_predicate_has_exactly_one_copy() -> None:
    """The ticket's own wording: ONE predicate in core.py beside the
    visibility clause, never pasted per surface. The clause's private alias is
    its fingerprint, so a hand-written copy in a route module fails here."""
    package = Path(pm_core.__file__).parent
    # The dotted form, because the bare alias is a substring of `is_triaged`
    # (the personal overlay's flag) — a fingerprint that matches prose is not
    # a fingerprint.
    owners = sorted(
        path.name for path in package.glob("*.py")
        if "s_triage." in path.read_text(encoding="utf-8")
    )
    assert owners == ["core.py"], (
        f"the triage predicate is written out in {owners}; every surface must "
        f"route through core.triage_exclusion_clause"
    )


# ── The queue ───────────────────────────────────────────────────────────────

async def test_the_queue_lists_pending_and_reappearing_snoozes(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db)
    triage = _triage(db, project)
    pending = _captured(db, project, triage, title="Pending")
    woke = db.seed_task(project.id, triage.id, title="Snooze elapsed")
    db.seed("pm_intake", task_id=woke.id, status="snoozed",
            snoozed_until=datetime.now(UTC) - timedelta(hours=1),
            created_by="owner@fracktal.in")
    still = db.seed_task(project.id, triage.id, title="Still snoozed")
    db.seed("pm_intake", task_id=still.id, status="snoozed",
            snoozed_until=datetime.now(UTC) + timedelta(days=2),
            created_by="owner@fracktal.in")
    ruled = db.seed_task(project.id, todo.id, title="Already accepted")
    db.seed("pm_intake", task_id=ruled.id, status="accepted",
            created_by="owner@fracktal.in")

    queue = await pm_intake.list_intake(user=USER, page=page())
    assert {r["title"] for r in queue["rows"]} == {"Pending", "Snooze elapsed"}
    assert queue["total"] == 2
    by_title = {r["title"]: r for r in queue["rows"]}
    assert by_title["Pending"]["intake"]["status"] == "pending"
    assert by_title["Snooze elapsed"]["intake"]["status"] == "snoozed"
    assert str(by_title["Pending"]["id"]) == str(pending.id)


async def test_the_queue_scopes_by_a_projects_subtree(
    db: FakeProjectsDB, events: list,
) -> None:
    project, _ = _workspace(db)
    triage = _triage(db, project)
    child = db.seed_project(name="Sub", parent=project.id, subject=None)
    other, _ = _workspace(db)
    other_triage = _triage(db, other)
    inside = db.seed_task(child.id, triage.id, root=project.id, title="Inside")
    db.seed("pm_intake", task_id=inside.id, created_by="owner@fracktal.in")
    _captured(db, other, other_triage, title="Elsewhere")

    queue = await pm_intake.list_intake(
        user=USER, project_id=str(project.id), page=page(),
    )
    assert [r["title"] for r in queue["rows"]] == ["Inside"]


# ── Accept — flips in place, never copies ───────────────────────────────────

async def test_accept_flips_the_status_in_place_and_never_copies(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage, title="Keep me")

    result = await pm_intake.accept_intake(
        str(task.id), pm_intake.AcceptIn(status_id=str(todo.id)), user=USER,
    )

    # IN PLACE: same row, same id — the table still holds exactly one task.
    assert len(db.rows("pm_tasks")) == 1
    assert result["task"]["id"] == str(task.id)
    assert db.rows("pm_tasks")[0]["status_id"] == str(todo.id)
    assert result["intake"]["status"] == "accepted"
    # Both timeline entries: the transition's own, and the ruling's.
    assert db.activities("status_change")
    assert any(a["meta"]["intake"] == "accepted" for a in _intake_activities(db))
    assert ("pm.intake.accepted", {"task_id": str(task.id)}) in events


async def test_accept_without_a_status_uses_the_projects_default(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage)

    result = await pm_intake.accept_intake(
        str(task.id), pm_intake.AcceptIn(), user=USER,
    )
    assert result["task"]["status_id"] == str(todo.id)


async def test_accept_refuses_a_triage_destination(
    db: FakeProjectsDB, events: list,
) -> None:
    """Accepting INTO triage rules nothing — and the guard also catches a
    project whose status fallback happens to be the triage lane itself."""
    project, _ = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage)

    with pytest.raises(HTTPException) as exc:
        await pm_intake.accept_intake(
            str(task.id), pm_intake.AcceptIn(status_id=str(triage.id)),
            user=USER,
        )
    assert exc.value.status_code == 422
    # Nothing moved and the wrapper still awaits a ruling.
    assert db.rows("pm_intake")[0]["status"] == "pending"


# ── Decline and duplicate — archive, wrapper as provenance ──────────────────

async def test_decline_archives_the_task_and_keeps_the_wrapper(
    db: FakeProjectsDB, events: list,
) -> None:
    project, _ = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage, title="Not work")

    result = await pm_intake.decline_intake(str(task.id), user=USER)

    row = db.rows("pm_tasks")[0]
    assert row["archived_at"] is not None
    assert result["intake"]["status"] == "declined"
    # PROVENANCE IS PERMANENT: the wrapper survives the ruling.
    assert len(db.rows("pm_intake")) == 1
    assert any(a["meta"]["intake"] == "declined" for a in _intake_activities(db))
    # And the declined capture is out of the queue.
    queue = await pm_intake.list_intake(user=USER, page=page())
    assert queue["total"] == 0


async def test_duplicate_points_at_the_original_and_archives(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db)
    triage = _triage(db, project)
    original = db.seed_task(project.id, todo.id, title="The original")
    task = _captured(db, project, triage, title="Same thing again")

    result = await pm_intake.duplicate_intake(
        str(task.id),
        pm_intake.DuplicateIn(duplicate_of_task_id=str(original.id)),
        user=USER,
    )

    wrapper = db.rows("pm_intake")[0]
    assert str(wrapper["duplicate_of_task_id"]) == str(original.id)
    assert wrapper["status"] == "duplicate"
    assert result["task"]["archived_at"] is not None
    assert any(
        a["meta"]["intake"] == "duplicate"
        and a["meta"]["duplicate_of_task_id"] == str(original.id)
        for a in _intake_activities(db)
    )


async def test_a_task_cannot_be_a_duplicate_of_itself(
    db: FakeProjectsDB, events: list,
) -> None:
    project, _ = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage)

    with pytest.raises(HTTPException) as exc:
        await pm_intake.duplicate_intake(
            str(task.id),
            pm_intake.DuplicateIn(duplicate_of_task_id=str(task.id)),
            user=USER,
        )
    assert exc.value.status_code == 422


# ── Snooze — hidden until, reappears by being read ──────────────────────────

async def test_snooze_hides_the_item_until_its_instant_passes(
    db: FakeProjectsDB, events: list,
) -> None:
    project, _ = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage, title="Later")

    result = await pm_intake.snooze_intake(
        str(task.id), pm_intake.SnoozeIn(until="2099-01-01T09:00:00Z"),
        user=USER,
    )
    assert result["intake"]["status"] == "snoozed"

    queue = await pm_intake.list_intake(user=USER, page=page())
    assert queue["total"] == 0

    # Time passes — modelled by editing the ROW, because that is the claim:
    # nothing flips a snoozed wrapper back, it reappears by being read.
    db.rows("pm_intake")[0]["snoozed_until"] = (
        datetime.now(UTC) - timedelta(minutes=1)
    )
    woke = await pm_intake.list_intake(user=USER, page=page())
    assert [r["title"] for r in woke["rows"]] == ["Later"]
    assert db.rows("pm_intake")[0]["status"] == "snoozed"


async def test_a_snooze_into_the_past_is_refused(
    db: FakeProjectsDB, events: list,
) -> None:
    project, _ = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage)

    with pytest.raises(HTTPException) as exc:
        await pm_intake.snooze_intake(
            str(task.id), pm_intake.SnoozeIn(until="2020-01-01T00:00:00Z"),
            user=USER,
        )
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await pm_intake.snooze_intake(
            str(task.id), pm_intake.SnoozeIn(until="whenever"), user=USER,
        )
    assert exc.value.status_code == 422


# ── The wrapper is permanent ────────────────────────────────────────────────

async def test_no_ruling_deletes_the_wrapper(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db)
    triage = _triage(db, project)
    kept = _captured(db, project, triage, title="kept")
    declined = _captured(db, project, triage, title="declined")
    doubled = _captured(db, project, triage, title="doubled")
    original = db.seed_task(project.id, todo.id, title="original")

    await pm_intake.accept_intake(
        str(kept.id), pm_intake.AcceptIn(status_id=str(todo.id)), user=USER,
    )
    await pm_intake.decline_intake(str(declined.id), user=USER)
    await pm_intake.duplicate_intake(
        str(doubled.id),
        pm_intake.DuplicateIn(duplicate_of_task_id=str(original.id)),
        user=USER,
    )
    assert len(db.rows("pm_intake")) == 3


def test_the_module_never_writes_a_delete_against_the_wrapper() -> None:
    """Structural half of permanence: the behavioural test above proves three
    rulings kept three rows; this proves no code path CAN drop one."""
    source = Path(pm_intake.__file__).read_text(encoding="utf-8")
    assert not re.search(r"DELETE\s+FROM\s+pm_intake", source, re.I)


async def test_a_resolved_item_cannot_be_ruled_on_again(
    db: FakeProjectsDB, events: list,
) -> None:
    """Terminal states are terminal — a second ruling would overwrite the
    provenance the first one wrote."""
    project, todo = _workspace(db)
    triage = _triage(db, project)
    task = _captured(db, project, triage)
    await pm_intake.decline_intake(str(task.id), user=USER)

    with pytest.raises(HTTPException) as exc:
        await pm_intake.accept_intake(
            str(task.id), pm_intake.AcceptIn(status_id=str(todo.id)), user=USER,
        )
    assert exc.value.status_code == 422
    assert "declined" in str(exc.value.detail)


# ── Visibility — R5, and grant scoping ──────────────────────────────────────

async def test_capturing_into_an_unseen_project_is_404_never_403(
    db: FakeProjectsDB, events: list,
) -> None:
    project = db.seed_project(name="Secret", subject=None)
    member = member_user("colleague@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await pm_intake.capture_intake(
            pm_intake.IntakeIn(project_id=str(project.id), title="Try"),
            user=member,
        )
    assert exc.value.status_code == 404


async def test_ruling_on_an_unseen_task_is_404_never_403(
    db: FakeProjectsDB, events: list,
) -> None:
    project, todo = _workspace(db, subject=None)
    triage = _triage(db, project)
    task = _captured(db, project, triage)
    member = member_user("colleague@fracktal.in")

    for act in (
        pm_intake.accept_intake(
            str(task.id), pm_intake.AcceptIn(status_id=str(todo.id)),
            user=member,
        ),
        pm_intake.decline_intake(str(task.id), user=member),
        pm_intake.snooze_intake(
            str(task.id), pm_intake.SnoozeIn(until="2099-01-01T00:00:00Z"),
            user=member,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await act
        assert exc.value.status_code == 404


async def test_marking_duplicate_of_an_unseen_original_is_404(
    db: FakeProjectsDB, events: list,
) -> None:
    """Both ends must be visible — accepting an unreadable original would
    disclose that it exists, the same rule links already hold."""
    member = member_user("colleague@fracktal.in")
    mine = db.seed_project(name="Mine", subject="colleague@fracktal.in")
    triage = _triage(db, mine)
    task = _captured(db, mine, triage)
    hidden = db.seed_project(name="Hidden", subject=None)
    theirs = db.seed_status(hidden.id, name="To do", category="todo")
    original = db.seed_task(hidden.id, theirs.id, title="Unseen")

    with pytest.raises(HTTPException) as exc:
        await pm_intake.duplicate_intake(
            str(task.id),
            pm_intake.DuplicateIn(duplicate_of_task_id=str(original.id)),
            user=member,
        )
    assert exc.value.status_code == 404


async def test_the_queue_is_scoped_by_the_tasks_own_grants(
    db: FakeProjectsDB, events: list,
) -> None:
    """The queue can never show a capture its reader could not open."""
    member = member_user("colleague@fracktal.in")
    granted = db.seed_project(name="Granted", subject="colleague@fracktal.in")
    granted_triage = _triage(db, granted)
    visible = _captured(db, granted, granted_triage, title="Yours to rule on")
    hidden = db.seed_project(name="Hidden", subject=None)
    hidden_triage = _triage(db, hidden)
    _captured(db, hidden, hidden_triage, title="Not yours")

    queue = await pm_intake.list_intake(user=member, page=page())
    assert [r["title"] for r in queue["rows"]] == ["Yours to rule on"]
    assert queue["total"] == 1
    assert str(queue["rows"][0]["id"]) == str(visible.id)


async def test_the_queue_404s_an_unseen_project_filter(
    db: FakeProjectsDB, events: list,
) -> None:
    """An unreadable `project_id` must not answer an empty queue — that would
    confirm the project exists and is simply quiet (R5)."""
    hidden = db.seed_project(name="Hidden", subject=None)
    member = member_user("colleague@fracktal.in")

    with pytest.raises(HTTPException) as exc:
        await pm_intake.list_intake(
            user=member, project_id=str(hidden.id), page=page(),
        )
    assert exc.value.status_code == 404
