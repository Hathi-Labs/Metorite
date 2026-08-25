"""Projects · the personal lens — one store, seen as my own work.

Spec: ``project-docs/specs/project_management_app.md`` §3.11-§3.12, §6.1 ·
**D-PM-6 (revised 2026-08-06)** · ticket WS-27e.

The claims worth testing here are the *cohesion* ones, because they are what the
one-store design bought and what a mirror could never have given:

* assigning a task makes it appear in the assignee's inbox with **no sync**;
* completing it from the inbox completes it for the **project**, same row;
* the overlay is per-member, so two assignees hold different dispositions;
* a member's triage can never move the team's board, and the team's board can
  never overwrite a member's triage.

Hermetic: no Postgres, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import personal as pm_personal
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

MODULES = (pm_core, pm_tree, pm_tasks, pm_personal)

ALICE = member_user("alice@fracktal.in")
BOB = member_user("bob@fracktal.in")
OWNER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _team_project(db: FakeProjectsDB) -> tuple:
    project = db.seed_project(name="Sales", subject="org")
    todo = db.seed_status(project.id, name="To do", category="todo", is_default=True)
    done = db.seed_status(
        project.id, name="Done", category="done", is_default=False, position=40,
    )
    return project, todo, done


def _assign(db: FakeProjectsDB, task_id: str, *emails: str) -> None:
    for email in emails:
        db.seed(
            "pm_task_assignees", task_id=task_id, assignee=email,
            assigned_by="owner@fracktal.in",
        )


# ── The cohesion claims ─────────────────────────────────────────────────────

async def test_assigning_a_task_puts_it_in_the_inbox_with_no_sync(
    db: FakeProjectsDB,
) -> None:
    """The whole point of D-PM-6's revision.

    A task assigned to Alice is not copied anywhere — it IS the row she sees. So
    the inbox finds it with nothing in between, and there is no mirror table for
    it to be missing from.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Ship the thing")
    _assign(db, task.id, "alice@fracktal.in")

    inbox = await pm_personal.my_inbox(user=ALICE, page=page())

    assert [row["id"] for row in inbox.rows] == [str(task.id)]
    # …and no second store was consulted or written.
    assert "gtd_items" not in " ".join(db.statements)


async def test_the_inbox_row_IS_the_project_row(db: FakeProjectsDB) -> None:
    """Same id, same title, same status — because it is one row.

    A mirror would have produced a different id here, which is exactly the
    property that made every downstream feature have to know about both.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="One row")
    _assign(db, task.id, "alice@fracktal.in")

    inbox = await pm_personal.my_inbox(user=ALICE, page=page())
    row = inbox.rows[0]

    assert row["id"] == str(task.id)
    assert row["project_id"] == str(project.id)
    assert row["title"] == "One row"


async def test_completing_from_the_inbox_completes_it_for_the_project(
    db: FakeProjectsDB,
) -> None:
    """The cohesion that a personal-only "done" would have destroyed: a member
    ticking something off while the board still shows it open is the exact drift
    a mirror produces."""
    project, todo, done = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    result = await pm_personal.complete_task(str(task.id), user=ALICE)

    assert result["status_id"] == str(done.id)
    assert result["completed_at"] is not None
    # The shared row moved, and the timeline records it as a real transition.
    assert db.rows("pm_tasks")[0]["status_id"] == str(done.id)
    assert len(db.activities("status_change")) == 1


async def test_two_assignees_hold_different_dispositions(
    db: FakeProjectsDB,
) -> None:
    """The reason the overlay is per-member and not a column on the task.

    Alice is doing it (NEXT); Bob delegated it and is waiting (WAITING). A single
    column could not express that, and it is what delegation looks like.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in", "bob@fracktal.in")

    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="NEXT"), user=ALICE,
    )
    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="WAITING"), user=BOB,
    )

    alice_inbox = await pm_personal.my_inbox(user=ALICE, page=page())
    bob_inbox = await pm_personal.my_inbox(user=BOB, page=page())

    assert alice_inbox.rows[0]["disposition"] == "NEXT"
    assert bob_inbox.rows[0]["disposition"] == "WAITING"
    assert len(db.rows("pm_task_personal")) == 2


async def test_my_triage_cannot_move_the_teams_board(db: FakeProjectsDB) -> None:
    """The structural half of "the overlay is never clobbered", in the direction
    people forget: filing something SOMEDAY is a statement about my attention,
    not about the work."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="SOMEDAY"), user=ALICE,
    )

    assert db.rows("pm_tasks")[0]["status_id"] == str(todo.id)
    # The overlay route never writes to the task table at all.
    assert not [
        s for s, _ in db.calls
        if s.upper().startswith("UPDATE PM_TASKS")
    ]


async def test_a_status_change_does_not_overwrite_my_stated_disposition(
    db: FakeProjectsDB,
) -> None:
    """The other direction of the same contract, and the one migration 48's
    ClickUp sync has always honoured."""
    project, todo, _ = _team_project(db)
    doing = db.seed_status(
        project.id, name="Doing", category="in_progress", is_default=False,
    )
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="SOMEDAY"), user=ALICE,
    )

    await pm_tasks.patch_task(
        str(task.id), pm_tasks.TaskIn(status_id=str(doing.id)), user=OWNER,
    )

    inbox = await pm_personal.my_inbox(user=ALICE, include_done=True, page=page())
    assert inbox.rows[0]["disposition"] == "SOMEDAY"


# ── The derived disposition ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "category,is_mine,has_assignee,expected",
    [
        ("done", True, True, "DONE"),
        ("cancelled", True, True, "DONE"),
        ("backlog", True, True, "SOMEDAY"),
        ("todo", True, True, "NEXT"),
        ("in_progress", False, True, "WAITING"),
        ("todo", False, False, "INBOX"),
    ],
)
def test_the_derived_lens_matches_the_shipped_clickup_one(
    category: str, is_mine: bool, has_assignee: bool, expected: str,
) -> None:
    """Lifted from `routes/tasks/sync.py` so both halves of the app agree about
    what an untriaged task means."""
    assert pm_personal.derive_disposition(
        status_category=category, is_mine=is_mine, has_assignee=has_assignee,
    ) == expected


async def test_an_untriaged_task_is_distinguishable_from_a_triaged_one(
    db: FakeProjectsDB,
) -> None:
    """`is_triaged` is the Weekly Review's whole question — which tasks have I
    not looked at. Defaulting `disposition` to INBOX in the column would have
    made "never seen" and "deliberately filed to the inbox" the same state."""
    project, todo, _ = _team_project(db)
    untouched = db.seed_task(project.id, todo.id, title="Never seen")
    filed = db.seed_task(project.id, todo.id, title="Filed to inbox")
    _assign(db, untouched.id, "alice@fracktal.in")
    _assign(db, filed.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(filed.id), pm_personal.PersonalIn(disposition="INBOX"), user=ALICE,
    )

    inbox = await pm_personal.my_inbox(user=ALICE, page=page())
    by_title = {r["title"]: r for r in inbox.rows}

    # The untriaged one DERIVES its disposition from the task — assigned to me
    # in a todo lane, so NEXT. The filed one keeps the member's own answer even
    # though the derivation would have said otherwise.
    assert by_title["Never seen"]["disposition"] == "NEXT"
    assert by_title["Filed to inbox"]["disposition"] == "INBOX"
    # And `is_triaged` is what the Weekly Review reads: a stated INBOX and a
    # never-looked-at task must not be the same state.
    assert by_title["Never seen"]["is_triaged"] is False
    assert by_title["Filed to inbox"]["is_triaged"] is True


async def test_filtering_by_disposition_matches_the_effective_one(
    db: FakeProjectsDB,
) -> None:
    """"Show me my next actions" must answer the same whether or not the member
    has been through their inbox — filtering on the stored column alone would
    show an empty Next list to somebody with twenty assigned tasks."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Untriaged but mine")
    _assign(db, task.id, "alice@fracktal.in")

    inbox = await pm_personal.my_inbox(user=ALICE, disposition="NEXT", page=page())

    assert [r["title"] for r in inbox.rows] == ["Untriaged but mine"]
    assert db.rows("pm_task_personal") == []  # nothing was written to read it


async def test_an_unknown_disposition_is_422(db: FakeProjectsDB) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_personal.set_personal(
            str(task.id), pm_personal.PersonalIn(disposition="LATER"), user=ALICE,
        )
    assert exc.value.status_code == 422


# ── The personal project ────────────────────────────────────────────────────

async def test_capture_creates_the_personal_project_once(
    db: FakeProjectsDB,
) -> None:
    """GTD's first discipline is frictionless capture: a title, and nothing else
    required — no project to choose, no status to pick."""
    first = await pm_personal.capture(
        pm_personal.CaptureIn(title="Call the supplier"), user=ALICE,
    )
    await pm_personal.capture(pm_personal.CaptureIn(title="Second thought"), user=ALICE)

    personal = [p for p in db.rows("pm_projects") if p.get("personal_owner")]
    assert len(personal) == 1
    assert personal[0]["personal_owner"] == "alice@fracktal.in"
    assert first["title"] == "Call the supplier"
    assert len(db.rows("pm_tasks")) == 2


async def test_a_captured_task_is_an_ordinary_task(db: FakeProjectsDB) -> None:
    """Which is what lets a captured thought later move into a team project
    without being recreated — and what makes the board, timeline, automation and
    agent dispatch work on it with no special case."""
    created = await pm_personal.capture(
        pm_personal.CaptureIn(title="A thought"), user=ALICE,
    )

    row = db.rows("pm_tasks")[0]
    assert row["source"] == "manual"
    assert row["task_number"] == 1
    assert str(row["status_id"])
    # And it is assigned to the capturer, so it reaches their own inbox.
    assert db.rows("pm_task_assignees")[0]["assignee"] == "alice@fracktal.in"
    assert created["id"] == str(row["id"])


async def test_a_personal_project_gets_its_own_statuses(db: FakeProjectsDB) -> None:
    await pm_personal.create_my_project(user=ALICE)

    lanes = {s["name"]: s["category"] for s in db.rows("pm_task_statuses")}
    assert lanes == {
        "Inbox": "backlog", "Next": "todo", "Doing": "in_progress", "Done": "done",
    }


async def test_a_personal_project_is_granted_to_its_owner_only(
    db: FakeProjectsDB,
) -> None:
    """`personal_owner` is the fast path to finding it; the GRANT is what the
    visibility model actually reads. Both, so neither is load-bearing alone."""
    await pm_personal.create_my_project(user=ALICE)

    grants = db.rows("pm_project_grants")
    assert [g["subject"] for g in grants] == ["alice@fracktal.in"]


async def test_personal_projects_stay_out_of_the_team_tree(
    db: FakeProjectsDB,
) -> None:
    """"My tasks" does not belong in a department tree beside Sales. This is not
    a security filter — the grant already scopes it to one person — it is that a
    personal project is not a department."""
    _team_project(db)
    await pm_personal.create_my_project(user=ALICE)

    tree = await pm_tree.get_tree(user=OWNER)

    assert [row["name"] for row in tree["rows"]] == ["Sales"]


async def test_my_own_captured_task_survives_unassigning_myself(
    db: FakeProjectsDB,
) -> None:
    """The second arm of the inbox query. Without it, clearing your own name off
    a private todo would make it vanish from the only place it exists."""
    await pm_personal.capture(pm_personal.CaptureIn(title="Mine alone"), user=ALICE)
    db.tables["pm_task_assignees"] = []

    inbox = await pm_personal.my_inbox(user=ALICE, page=page())

    assert [r["title"] for r in inbox.rows] == ["Mine alone"]


# ── The tickler ─────────────────────────────────────────────────────────────

async def test_deferring_hides_a_task_from_my_inbox_only(
    db: FakeProjectsDB,
) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id, title="Later")
    _assign(db, task.id, "alice@fracktal.in", "bob@fracktal.in")

    await pm_personal.defer_task(
        str(task.id), pm_personal.DeferIn(until="2099-01-01T00:00:00+00:00"),
        user=ALICE,
    )

    assert (await pm_personal.my_inbox(user=ALICE, page=page())).rows == []
    # Bob's view is untouched, and so is the board.
    assert len((await pm_personal.my_inbox(user=BOB, page=page())).rows) == 1
    assert db.rows("pm_tasks")[0]["status_id"] == str(todo.id)


async def test_include_deferred_brings_them_back(db: FakeProjectsDB) -> None:
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.defer_task(
        str(task.id), pm_personal.DeferIn(until="2099-01-01T00:00:00+00:00"),
        user=ALICE,
    )

    inbox = await pm_personal.my_inbox(
        user=ALICE, include_deferred=True, include_done=True, page=page(),
    )
    assert len(inbox.rows) == 1


# ── Identity ────────────────────────────────────────────────────────────────

def test_no_personal_route_can_be_pointed_at_another_member() -> None:
    """R3/R4, made structural. There is deliberately no `?member=` anywhere
    here, so no request can read or write somebody else's practice."""
    import inspect

    for name in (
        "my_inbox", "my_contexts", "my_calendar", "set_personal", "capture",
        "get_my_project", "create_my_project", "defer_task", "complete_task",
    ):
        params = set(inspect.signature(getattr(pm_personal, name)).parameters)
        assert "user" in params, name
        assert not (params & {"member", "member_email", "email", "who", "user_id"}), name


async def test_the_overlay_write_is_scoped_to_the_caller(
    db: FakeProjectsDB,
) -> None:
    """Structural: the upsert binds the caller's own address, so there is no
    shape of request that writes another member's row."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(context="@calls"), user=ALICE,
    )

    statement, params = next(
        (s, p) for s, p in db.calls if "pm_task_personal" in s and s.startswith("INSERT")
    )
    assert params["member_email"] == "alice@fracktal.in"
    assert "member_email" in statement


async def test_seeing_the_task_is_the_floor_for_triaging_it(
    db: FakeProjectsDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task nobody granted you and nobody assigned you is 404, so the overlay
    cannot be used to probe for tasks that exist."""
    real = pm_core.resolve_visibility

    async def _resolve(db_, user):
        vis = await real(db_, user)
        return pm_core.Visibility(unrestricted=False, email=vis.email, groups=())

    for module in MODULES:
        monkeypatch.setattr(module, "resolve_visibility", _resolve, raising=False)

    project = db.seed_project(name="Finance", subject=None)
    todo = db.seed_status(project.id)
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_personal.set_personal(
            str(task.id), pm_personal.PersonalIn(disposition="NEXT"), user=ALICE,
        )
    assert exc.value.status_code == 404


# ── The migration ───────────────────────────────────────────────────────────

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _personal_migration() -> Path:
    """Found by CONTENT, not by number (R1) — a test pinned to `147_` would be
    the same mistake the 145 collision already made once."""
    found = [
        p for p in sorted(MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS pm_task_personal" in p.read_text(
            encoding="utf-8",
        )
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


@pytest.fixture(scope="module")
def sql() -> str:
    return _personal_migration().read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bare(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def test_the_migration_is_idempotent(bare: str) -> None:
    assert not re.search(r"CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)", bare, re.I)
    assert not re.search(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS)", bare, re.I)
    assert not re.search(r"ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", bare, re.I)


def test_the_overlay_is_keyed_per_member(bare: str) -> None:
    """The decision the whole design rests on: two assignees, two rows."""
    assert re.search(
        r"PRIMARY\s+KEY\s*\(\s*task_id\s*,\s*member_email\s*\)", bare, re.I,
    )


def test_disposition_has_no_default(bare: str) -> None:
    """NULL means "not triaged" and must stay distinguishable from INBOX."""
    block = bare.split("CREATE TABLE IF NOT EXISTS pm_task_personal", 1)[1].split(");", 1)[0]
    disposition_line = next(
        line for line in block.splitlines() if line.strip().startswith("disposition")
    )
    assert "DEFAULT" not in disposition_line.upper()


def test_one_personal_project_per_member_case_insensitively(bare: str) -> None:
    assert re.search(
        r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+"
        r"ON\s+pm_projects\s*\(\s*lower\(personal_owner\)\s*\)",
        bare, re.I,
    )
    assert "WHERE personal_owner IS NOT NULL" in bare


def test_the_disposition_vocabulary_matches_migration_48(bare: str) -> None:
    """WS-27h has to move every gtd_items row onto this column; a renamed
    disposition would make that migration a translation instead of a copy."""
    legacy = (MIGRATIONS / "48_task_manager_gtd.sql").read_text(encoding="utf-8")
    legacy_values = set(
        re.findall(
            r"\b(INBOX|NEXT|WAITING|SOMEDAY|PROJECT|REFERENCE|DONE|TRASH)\b", legacy,
        )
    )
    for value in legacy_values:
        assert f"'{value}'" in bare, f"disposition {value} is missing"


# ── The scheduled block (WS-39 S3a · migration 187 · D53/D54) ────────────────
#
# The claim under test is the one the migration encodes: **a block belongs to
# the member, not to the task.** The R8 half — that the constraint, the trigger
# and the planner all agree — is `tests/live/live_ws39_s3a.sql`; these are the
# API-shaped consequences that a fake CAN answer.


async def test_two_assignees_hold_different_blocks_on_one_task(
    db: FakeProjectsDB,
) -> None:
    """The reason the block is on the overlay and not on `pm_tasks`.

    Exactly the `test_two_assignees_hold_different_dispositions` argument, one
    axis over: Alice is doing it Tuesday morning, Bob is reviewing it Thursday
    afternoon. A `scheduled_start` column on the task would let whoever wrote
    last delete the other's afternoon, and neither would be told.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in", "bob@fracktal.in")

    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            scheduled_start="2026-09-01T09:00:00+00:00",
            scheduled_end="2026-09-01T11:00:00+00:00",
        ),
        user=ALICE,
    )
    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            scheduled_start="2026-09-03T15:00:00+00:00",
            scheduled_end="2026-09-03T15:30:00+00:00",
        ),
        user=BOB,
    )

    alice = await pm_personal.my_inbox(user=ALICE, page=page())
    bob = await pm_personal.my_inbox(user=BOB, page=page())

    assert alice.rows[0]["scheduled_start"].startswith("2026-09-01")
    assert bob.rows[0]["scheduled_start"].startswith("2026-09-03")
    # One task. Two calendars. Two rows in the overlay, and no third anywhere.
    assert len(db.rows("pm_task_personal")) == 2
    assert len(db.rows("pm_tasks")) == 1


async def test_scheduling_never_writes_to_the_shared_task(
    db: FakeProjectsDB,
) -> None:
    """Blocking my own time is not an edit to the work.

    The counterpart of `test_my_triage_cannot_move_the_teams_board`: if
    scheduling reached `pm_tasks`, one assignee's calendar would be a write to
    everybody's task — and `due_at`, which IS shared, would be the column it
    collided with.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(scheduled_start="2026-09-01T09:00:00+00:00"),
        user=ALICE,
    )

    written = [
        stmt for stmt in db.statements_touching("pm_tasks")
        if stmt.startswith("UPDATE") or stmt.startswith("INSERT")
    ]
    assert written == [], f"scheduling wrote to the shared task: {written}"


async def test_a_block_that_ends_before_it_starts_is_422_not_500(
    db: FakeProjectsDB,
) -> None:
    """The API says whose fault it is.

    Migration 187's CHECK refuses this at the database, which is correct and is
    also a 500 by the time it reaches a caller. Dragging a block's edge past its
    own start is an ordinary mis-gesture, not a server fault.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_personal.set_personal(
            str(task.id),
            pm_personal.PersonalIn(
                scheduled_start="2026-09-01T11:00:00+00:00",
                scheduled_end="2026-09-01T09:00:00+00:00",
            ),
            user=ALICE,
        )
    assert exc.value.status_code == 422
    assert "scheduled_end" in str(exc.value.detail)


async def test_a_zero_length_block_is_refused(db: FakeProjectsDB) -> None:
    """`>` and not `>=`, on both sides of the wire.

    A block of no duration is what a double-click produces, and the calendar's
    layout maths divides by the duration.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    with pytest.raises(HTTPException) as exc:
        await pm_personal.set_personal(
            str(task.id),
            pm_personal.PersonalIn(
                scheduled_start="2026-09-01T09:00:00+00:00",
                scheduled_end="2026-09-01T09:00:00+00:00",
            ),
            user=ALICE,
        )
    assert exc.value.status_code == 422


async def test_an_open_ended_block_is_allowed(db: FakeProjectsDB) -> None:
    """A start with no end is "started, still going" — not an error."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    out = await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(scheduled_start="2026-09-01T09:00:00+00:00"),
        user=ALICE,
    )
    assert out["scheduled_start"].startswith("2026-09-01")
    assert out["scheduled_end"] is None


async def test_flexible_keeps_three_states(db: FakeProjectsDB) -> None:
    """NULL is "never stated", and it is not False.

    Migration 187 keeps the column nullable for this, and the serializer passes
    it through rather than `bool()`-ing it. Collapsing the two would tell the
    Ideal Week packer that every task has been deliberately pinned.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    never = await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(context="@desk"), user=ALICE,
    )
    assert never["flexible"] is None

    pinned = await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(flexible=False), user=ALICE,
    )
    assert pinned["flexible"] is False


async def test_a_datetime_local_edge_drag_is_not_a_500(
    db: FakeProjectsDB,
) -> None:
    """The failing input, measured: `<input type="datetime-local">` sends
    `2026-09-01T12:00` with **no offset**.

    `coerce_write_values` parses that to a NAIVE datetime while the stored
    `scheduled_start` read back for the merge is AWARE, so the merged-pair check
    compared the two and raised
    `TypeError: can't compare offset-naive and offset-aware datetimes` — a 500
    on the calendar's most ordinary interaction, one line below the fix that
    exists to stop exactly that. `_as_utc` reads a naive instant as UTC (this
    package's convention: `filters.py::_instant`, `delta.py`, `custom_fields.py`,
    `recurrence.py`), so the comparison happens in one frame.

    Both directions are asserted, because a fix that merely stopped raising
    could still refuse a legal drag: the widening edge SUCCEEDS, the inverted
    one is a 422.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)

    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            scheduled_start="2026-09-01T09:00:00+00:00",
            scheduled_end="2026-09-01T10:00:00+00:00",
        ),
        user=ALICE,
    )

    # Drag the bottom edge down — the browser's own spelling, no offset.
    out = await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(scheduled_end="2026-09-01T12:00"),
        user=ALICE,
    )
    assert out["scheduled_end"].startswith("2026-09-01T12:00")

    # And the same spelling still cannot invert the block.
    with pytest.raises(HTTPException) as exc:
        await pm_personal.set_personal(
            str(task.id),
            pm_personal.PersonalIn(scheduled_end="2026-09-01T08:00"),
            user=ALICE,
        )
    assert exc.value.status_code == 422, (
        "an offset-less end BEFORE the stored start must be a 422 — if this is "
        "a 500 the comparison is still crossing frames"
    )


async def test_a_mixed_offset_calendar_window_is_not_a_500(
    db: FakeProjectsDB,
) -> None:
    """The same defect on the read: one bound with an offset, one without.

    `end <= start` on a naive/aware pair raises `TypeError` → 500. Normalizing
    both ends makes it answer the question that was asked, and a genuinely
    inverted window is still a 422.
    """
    rows = await pm_personal.my_calendar(
        start="2026-09-01T00:00", end="2026-09-08T00:00:00+00:00", user=ALICE,
    )
    assert rows.total == 0

    with pytest.raises(HTTPException) as exc:
        await pm_personal.my_calendar(
            start="2026-09-08T00:00:00+00:00", end="2026-09-01T00:00",
            user=ALICE,
        )
    assert exc.value.status_code == 422


async def test_a_trashed_task_does_not_occupy_the_calendar(
    db: FakeProjectsDB,
) -> None:
    """A calendar is a claim on hours, so a task the member trashed must not
    hold one.

    `/my/inbox` has excluded DONE/TRASH since it was written; `/my/calendar`
    reused its SQL but not its filter, so a trashed task kept its block and its
    slot in the week. Same rule, same `include_done` parameter name, same
    EFFECTIVE-disposition derivation — one vocabulary, not a second one.
    """
    project, todo, _ = _team_project(db)
    kept = db.seed_task(project.id, todo.id, title="Keep")
    trashed = db.seed_task(project.id, todo.id, title="Bin")
    _assign(db, kept.id, "alice@fracktal.in")
    _assign(db, trashed.id, "alice@fracktal.in")

    for task in (kept, trashed):
        await pm_personal.set_personal(
            str(task.id),
            pm_personal.PersonalIn(
                scheduled_start="2026-09-01T09:00:00+00:00",
                scheduled_end="2026-09-01T10:00:00+00:00",
            ),
            user=ALICE,
        )
    await pm_personal.set_personal(
        str(trashed.id), pm_personal.PersonalIn(disposition="TRASH"), user=ALICE,
    )

    week = await pm_personal.my_calendar(
        start="2026-09-01T00:00:00+00:00", end="2026-09-08T00:00:00+00:00",
        user=ALICE,
    )
    titles = [r["title"] for r in week.rows]
    assert "Bin" not in titles, (
        "a trashed task still occupies an hour of the member's week"
    )
    assert "Keep" in titles, (
        "the control is missing — this test's green would then be an empty "
        "read, not the filter"
    )

    # The same escape hatch the inbox has, and it is the inbox's own name.
    everything = await pm_personal.my_calendar(
        start="2026-09-01T00:00:00+00:00", end="2026-09-08T00:00:00+00:00",
        include_done=True, user=ALICE,
    )
    assert "Bin" in [r["title"] for r in everything.rows]


async def test_the_calendar_window_is_half_open(db: FakeProjectsDB) -> None:
    """`[start, end)`, so consecutive weeks tile instead of overlapping.

    Structural rather than behavioural — the fake does not evaluate the range
    predicate — so the assertion is on the SQL the route composes, which is the
    part a later edit would break silently.
    """
    import inspect

    src = inspect.getsource(pm_personal.my_calendar)
    assert "p.scheduled_start >= :start" in src
    assert "p.scheduled_start < :end" in src, (
        "the window must be half-open: `<= :end` double-counts a block that "
        "starts exactly on the boundary between two weeks"
    )


async def test_the_calendar_read_is_scoped_to_the_caller(
    db: FakeProjectsDB,
) -> None:
    """It reuses `_MY_TASKS_SQL`, so it inherits the tenant and identity clauses
    rather than restating them — restating is how one of them gets dropped."""
    import inspect

    src = inspect.getsource(pm_personal.my_calendar)
    assert "_MY_TASKS_SQL" in src
    assert "actor(user)" in src
    assert "resolve_organization_id" in src


# ── The overlay's remaining per-member fields (migration 188, S3a-server-2) ──


async def test_two_assignees_hold_different_matrix_flags(
    db: FakeProjectsDB,
) -> None:
    """Migration 188's whole argument, and the third time it is the same one.

    Alice thinks this task is her highest-leverage work of the week. Bob, also
    assigned, has decided it is neither important nor deep — he is reviewing it
    for ten minutes. Both are correct, because the matrix is a judgement about
    the judge's own time, not a property of the work. A column on `pm_tasks`
    would make one of them overwrite the other with no notice, which is exactly
    what D53.7 was recorded to prevent for the block.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in", "bob@fracktal.in")

    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(important=True, leveraged=True, deep_work=True),
        user=ALICE,
    )
    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(important=False, leveraged=False, deep_work=False),
        user=BOB,
    )

    alice = await pm_personal.my_inbox(user=ALICE, page=page())
    bob = await pm_personal.my_inbox(user=BOB, page=page())

    assert alice.rows[0]["important"] is True
    assert alice.rows[0]["deep_work"] is True
    assert bob.rows[0]["important"] is False
    assert bob.rows[0]["deep_work"] is False
    # One task, two overlays, no third row anywhere.
    assert len(db.rows("pm_tasks")) == 1
    assert len(db.rows("pm_task_personal")) == 2


async def test_matrix_flags_are_tri_state_not_booleans(
    db: FakeProjectsDB,
) -> None:
    """`None` is not `False`, and the difference is the whole triage nudge.

    A member who has never opened the matrix and a member who looked and said
    "not important" are different states. Only the first should be asked. If
    the reader coerced with `bool()` — which 187's own comment warns about —
    the two would be indistinguishable on the wire and the nudge would either
    chase everybody forever or nobody at all.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    # Triaged, but the matrix was never touched.
    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="NEXT"), user=ALICE,
    )
    rows = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    assert rows[0]["important"] is None, "never stated must not read as False"
    assert rows[0]["leveraged"] is None
    assert rows[0]["kept_mine"] is None

    await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(important=False), user=ALICE,
    )
    rows = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    assert rows[0]["important"] is False, "an explicit no must survive as False"


async def test_waiting_for_round_trips_through_the_overlay(
    db: FakeProjectsDB,
) -> None:
    """Who / what / since-when, on one row, with no second table.

    `gtd_waiting` held this as a child table with a `resolved` flag. Every
    reader in the tree filtered `resolved = false`, so the open record is all
    that was ever displayed — which is why 188 collapses it onto the overlay.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    result = await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            disposition="WAITING",
            waiting_on={"name": "Priya", "email": "priya@fracktal.in"},
            delegated_at="2026-09-01T09:00:00+00:00",
            expected_by="2026-09-08T17:00:00+00:00",
        ),
        user=ALICE,
    )

    # A dict on the way out, never the '{"email": ...}' STRING that bare
    # `text()` over asyncpg hands back for a jsonb column.
    assert result["waiting_on"] == {"name": "Priya", "email": "priya@fracktal.in"}
    assert result["delegated_at"].startswith("2026-09-01")
    assert result["expected_by"].startswith("2026-09-08")
    # Not yet chased — and that is readable, which is what stops a double-chase.
    assert result["last_nudged_at"] is None

    rows = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    assert rows[0]["waiting_on"]["email"] == "priya@fracktal.in"


async def test_waiting_on_without_a_since_when_is_refused(
    db: FakeProjectsDB,
) -> None:
    """422, not 500 — the caller's omission, reported as the caller's.

    Migration 188 CHECKs the pair. Without the route-level merge check the
    request would reach the constraint and surface as a server fault on an
    ordinary interaction, which is the same defect `_reject_impossible_block`
    exists to prevent one field over.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    with pytest.raises(HTTPException) as caught:
        await pm_personal.set_personal(
            str(task.id),
            pm_personal.PersonalIn(waiting_on={"email": "priya@fracktal.in"}),
            user=ALICE,
        )
    assert caught.value.status_code == 422
    assert "delegated_at" in str(caught.value.detail)


async def test_a_partial_patch_is_judged_on_the_merged_row(
    db: FakeProjectsDB,
) -> None:
    """The reason the check reads storage instead of only the payload.

    Alice already recorded a `delegated_at`. Re-pointing the delegation at
    somebody else sends `waiting_on` ALONE — legal, because the since-when is
    already there. Judged on the payload in isolation it would be a 422, and
    the member would be told to supply a date the row already has.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            waiting_on={"email": "priya@fracktal.in"},
            delegated_at="2026-09-01T09:00:00+00:00",
        ),
        user=ALICE,
    )
    result = await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(waiting_on={"email": "sam@fracktal.in"}),
        user=ALICE,
    )
    assert result["waiting_on"]["email"] == "sam@fracktal.in"
    assert result["delegated_at"].startswith("2026-09-01")


async def test_clearing_waiting_on_is_always_legal(
    db: FakeProjectsDB,
) -> None:
    """A delegation resolving must never be blocked by the date it removes."""
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            waiting_on={"email": "priya@fracktal.in"},
            delegated_at="2026-09-01T09:00:00+00:00",
        ),
        user=ALICE,
    )
    result = await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(waiting_on=None), user=ALICE,
    )
    assert result["waiting_on"] is None


async def test_clarified_at_reaches_the_client(
    db: FakeProjectsDB,
) -> None:
    """It has been WRITTEN since migration 147 and read by nobody.

    `set_personal` stamps it on every triage — "when did I last look at this"
    is the Weekly Review's question — but it was in neither `_personal_to_dict`
    nor the inbox projection, so the value accumulated where no caller could
    reach it. No migration was needed to fix that, which is why 188 does not
    add the column: it already exists.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    result = await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="NEXT"), user=ALICE,
    )
    assert result["clarified_at"] is not None

    rows = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    assert rows[0]["clarified_at"] is not None


async def test_every_writable_overlay_field_can_be_read_back(
    db: FakeProjectsDB,
) -> None:
    """Structural fence (R7): the write model and the read model agree.

    The failure this defends is adding a column to `PersonalIn` and forgetting
    `_personal_to_dict` — a write that succeeds, returns 200, and vanishes. No
    example test sees it, because an example only asserts the fields its author
    remembered. This one derives the list from the model itself, so the next
    field is covered before anybody writes a test for it.
    """
    writable = set(pm_personal.PersonalIn.model_fields)
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")

    result = await pm_personal.set_personal(
        str(task.id), pm_personal.PersonalIn(disposition="NEXT"), user=ALICE,
    )
    missing = writable - set(result)
    assert not missing, (
        f"PersonalIn accepts {sorted(missing)} but the response cannot report "
        "them back — a write that succeeds and disappears"
    )


async def test_the_inbox_and_the_calendar_project_the_same_task_shape(
    db: FakeProjectsDB,
) -> None:
    """Structural fence (R7) over the THREE readers that share `_project_task`.

    They had two hand-maintained copies of the same projection, and the drift
    is silent and one-sided: a field added to the inbox but not the calendar
    produces a calendar where that flag never arrives — no error, no 500. The
    hermetic fake cannot catch it either, since a missing alias answers `None`
    just as a genuinely unset column does.

    ⚠️ **Tightened 2026-08-25 (WS-39 S3a-client) from "differ by exactly
    `is_triaged`" to EXACT equality, across three readers rather than two.**
    The old shape was defensible while `/my/inbox` was the only list: the
    calendar has no inbox to be triaged out of, so the field had no meaning
    there. It stopped being defensible when the client got ONE mapper. A single
    `mapLensItem` is only safe if every route that claims to return "a task as
    this member sees it" returns the same keys — otherwise the mapper silently
    reads `undefined` from whichever reader is short, and the UI renders a task
    with no stage on one surface and a stage on another. `GET /my/tasks/{id}`
    made that concrete: it is read after every split write, so if its shape were
    a third variant, every edit would round-trip a differently-shaped task into
    the store than the list put there.

    Exact equality is also the STRONGER form of what this test is named for. An
    allowance for one difference is an allowance a later field can hide behind.
    """
    project, todo, _ = _team_project(db)
    task = db.seed_task(project.id, todo.id)
    _assign(db, task.id, "alice@fracktal.in")
    await pm_personal.set_personal(
        str(task.id),
        pm_personal.PersonalIn(
            disposition="NEXT",
            important=True,
            deep_work=True,
            scheduled_start="2026-09-01T09:00:00+00:00",
            scheduled_end="2026-09-01T11:00:00+00:00",
        ),
        user=ALICE,
    )

    inbox = (await pm_personal.my_inbox(user=ALICE, page=page())).rows
    window = (await pm_personal.my_calendar(
        start="2026-09-01T00:00:00+00:00",
        end="2026-09-02T00:00:00+00:00",
        user=ALICE,
    )).rows
    assert inbox and window, "both reads must see the one scheduled task"

    single = await pm_personal.my_task(str(task.id), user=ALICE)

    assert set(inbox[0]) == set(window[0]) == set(single), (
        "the three personal readers disagree about what a task looks like; "
        "the client has one mapper, so the short one renders `undefined` "
        "fields. Present in only one reader: "
        f"inbox={sorted(set(inbox[0]) - set(window[0]) - set(single))} "
        f"calendar={sorted(set(window[0]) - set(inbox[0]) - set(single))} "
        f"single={sorted(set(single) - set(inbox[0]) - set(window[0]))}"
    )
    for field in ("important", "deep_work", "scheduled_start", "clarified_at"):
        assert inbox[0][field] == window[0][field] == single[field], (
            f"`{field}` disagrees between the readers of one overlay"
        )

    # The three facts S3a-client added, asserted by VALUE and not merely by
    # presence — a key that is present and always None is the failure this
    # slice exists to stop, and `set(...) == set(...)` above cannot see it.
    assert single["is_mine"] is True, (
        "is_mine was computed by the SQL and dropped by `row_to_dict`; the "
        "client reads `raw.is_mine ?? true`, so a false negative here is "
        "invisible and a false POSITIVE is somebody else's task shown as mine"
    )
    assert single["workflow_stage"] == todo.name, (
        "workflow_stage must be the status NAME — the id was already on the "
        "wire and no human reads a uuid off a board column"
    )
    assert single["subtask_count"] == 0
    assert single["assignees"] == ["alice@fracktal.in"]
