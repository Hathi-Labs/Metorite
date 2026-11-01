"""WS-27z — lifecycle policy: auto-archive and auto-close.

Spec: `ai-company-brain/specs/project_management_app.md` §9.1 (WS-27z), D6.

Four layers, one ticket, and each is tested where it lives:

1. **the migration** (166) — three ROOT-project columns, read as TEXT the way
   every projects migration is (§10 runs no database);
2. **the settings surface** — the ordinary project PATCH/read path, with the
   422s that keep a bad policy out of the table;
3. **the sweep** — `automation.run_lifecycle_sweep`, the function a published
   `/workflows` workflow calls on a schedule (D6: the engine owns WHEN, these
   columns own WHAT); archive/close/exempt/idempotent, and the
   project-timezone midnight beating UTC's;
4. **the flag and the wiring** — `automation: true` on every engine-made
   activity row, and the `pm_lifecycle` node that reaches the sweep through
   the same seam `pm_task` reaches `apply_task_patch`.

Hermetic throughout: `_projects_fakes.FakeProjectsDB`, no Postgres, no
TestClient.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from gateway.db import TenantUnbound
from gateway.routes.projects import tree
from gateway.routes.projects.automation import (
    apply_task_patch,
    run_lifecycle_sweep,
    workflow_actor,
)
from gateway.routes.projects.core import (
    LIFECYCLE_FIELDS,
    ProjectIn,
    apply_status_transition,
    record_field_change,
    validate_lifecycle_settings,
)
from gateway.routes.workflows.catalog import NODE_TYPE_META
from gateway.routes.workflows.engine.graph import (
    NODE_TYPES,
    compile_graph,
    validate_graph,
)
from gateway.routes.workflows.engine.handlers import (
    NODE_TIMEOUTS,
    NodeExecutionError,
    NodeServices,
    execute_node,
)

from tests.unit._projects_fakes import (
    DEFAULT_ORGANIZATION,
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

USER = projects_user()


def run(coro):
    return asyncio.run(coro)


# ── 1 · the migration, as text ──────────────────────────────────────────────

def _lifecycle_migration() -> Path:
    """Found by CONTENT (the R1 rule), not by number."""
    found = [
        path for path in sorted(MIGRATIONS.glob("*.sql"))
        if path.name != "schema.generated.sql"
        and "archive_after_months" in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, (
        f"expected exactly one migration adding archive_after_months, found "
        f"{[p.name for p in found]}"
    )
    return found[0]


@pytest.fixture(scope="module")
def sql() -> str:
    return _lifecycle_migration().read_text(encoding="utf-8")


def test_the_three_columns_land_on_pm_projects(sql: str) -> None:
    assert "ALTER TABLE pm_projects" in sql
    for column in LIFECYCLE_FIELDS:
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql, column


def test_every_add_column_is_guarded(sql: str) -> None:
    """Idempotent per infra/postgres/README.md: the ladder replays on every
    deploy, and the CHECKs ride inside the guarded ADDs so a replay cannot
    stack a second constraint."""
    adds = re.findall(r"ADD COLUMN(?! IF NOT EXISTS)", sql)
    assert adds == [], f"unguarded ADD COLUMN: {adds}"


def test_the_months_columns_refuse_zero_and_negative(sql: str) -> None:
    assert re.search(r"archive_after_months\s+INT\s+CHECK \(archive_after_months > 0\)", sql)
    assert re.search(r"close_after_months\s+INT\s+CHECK \(close_after_months > 0\)", sql)


def test_the_timezone_defaults_to_utc_and_cannot_be_null(sql: str) -> None:
    assert re.search(r"timezone\s+TEXT\s+NOT NULL\s+DEFAULT 'UTC'", sql)


def test_null_means_off_is_the_default(sql: str) -> None:
    """No DEFAULT on either months column: a project never opts in by being
    created (the sweeper touches real data — enable per project)."""
    for column in ("archive_after_months", "close_after_months"):
        line = next(l for l in sql.splitlines() if f"IF NOT EXISTS {column}" in l)
        assert "DEFAULT" not in line, line


# ── 2 · the settings surface ────────────────────────────────────────────────

@pytest.fixture()
def db(monkeypatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, (tree,))
    silence_events(monkeypatch, (tree,))
    return fake


def test_the_policy_saves_on_a_root_and_reads_back(db: FakeProjectsDB) -> None:
    root = db.seed_project(name="Delivery")
    out = run(tree.patch_node(
        str(root.id),
        ProjectIn(
            archive_after_months=3, close_after_months=6,
            timezone="Asia/Kolkata",
        ),
        user=USER,
    ))
    assert out["archive_after_months"] == 3
    assert out["close_after_months"] == 6
    assert out["timezone"] == "Asia/Kolkata"
    # And the read path carries them — the same row, the same mapper.
    fresh = run(tree.get_node(str(root.id), user=USER))
    assert fresh["archive_after_months"] == 3


def test_a_policy_change_earns_a_timeline_entry(db: FakeProjectsDB) -> None:
    """Six months later, "who turned the sweeper on" must be a timeline read."""
    root = db.seed_project(name="Delivery")
    run(tree.patch_node(
        str(root.id), ProjectIn(archive_after_months=3), user=USER,
    ))
    entries = db.activities("field_change")
    assert len(entries) == 1
    fields = {c["field"] for c in entries[0]["meta"]["changes"]}
    assert fields == {"archive_after_months"}


@pytest.mark.parametrize("months", [0, -1])
def test_zero_and_negative_months_are_422(db: FakeProjectsDB, months: int) -> None:
    root = db.seed_project(name="Delivery")
    with pytest.raises(HTTPException) as err:
        run(tree.patch_node(
            str(root.id), ProjectIn(archive_after_months=months), user=USER,
        ))
    assert err.value.status_code == 422
    assert "archive_after_months" in str(err.value.detail)


def test_a_fake_timezone_is_422_naming_the_zone(db: FakeProjectsDB) -> None:
    root = db.seed_project(name="Delivery")
    with pytest.raises(HTTPException) as err:
        run(tree.patch_node(
            str(root.id), ProjectIn(timezone="Mars/Olympus_Mons"), user=USER,
        ))
    assert err.value.status_code == 422
    assert "Mars/Olympus_Mons" in str(err.value.detail)
    assert "IANA" in str(err.value.detail)


def test_a_null_timezone_is_422_not_a_silent_utc(db: FakeProjectsDB) -> None:
    root = db.seed_project(name="Delivery")
    with pytest.raises(HTTPException) as err:
        run(tree.patch_node(str(root.id), ProjectIn(timezone=None), user=USER))
    assert err.value.status_code == 422


def test_the_policy_is_refused_on_a_subproject(db: FakeProjectsDB) -> None:
    """Root-project settings, like statuses/types/fields/tags: the sweep reads
    the ROOT's columns and acts on `root_project_id`, so a child's value would
    be inert — the API keeps the inert case unreachable."""
    root = db.seed_project(name="Delivery")
    child = db.seed_project(name="Sub", parent=str(root.id))
    with pytest.raises(HTTPException) as err:
        run(tree.patch_node(
            str(child.id), ProjectIn(close_after_months=2), user=USER,
        ))
    assert err.value.status_code == 422
    assert "root" in str(err.value.detail).lower()


def test_a_child_cannot_be_created_with_a_policy_either(db: FakeProjectsDB) -> None:
    root = db.seed_project(name="Delivery")
    with pytest.raises(HTTPException) as err:
        run(tree.create_node(
            ProjectIn(
                name="Sub", parent_project_id=str(root.id),
                archive_after_months=1,
            ),
            user=USER,
        ))
    assert err.value.status_code == 422


def test_a_root_can_be_created_with_its_policy(db: FakeProjectsDB) -> None:
    out = run(tree.create_node(
        ProjectIn(name="Ops", archive_after_months=2, timezone="UTC"),
        user=USER,
    ))
    assert out["archive_after_months"] == 2


def test_the_validator_refuses_a_boolean_month() -> None:
    """`True` is an `int` to isinstance and not to a person; the validator must
    not let a truthy flag masquerade as one month."""
    with pytest.raises(HTTPException):
        validate_lifecycle_settings({"archive_after_months": True})


# ── 3 · the sweep ───────────────────────────────────────────────────────────

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
ACTOR = workflow_actor("wf-sweep")
#: WS-27aa — the sweep is per-tenant and takes the tenant explicitly. The
#: fake seeds root projects into its own organization, so this is the one the
#: single-tenant cases sweep; ``ORG_B`` below is what proves the predicate.
ORG = DEFAULT_ORGANIZATION
ORG_B = "00000000-0000-4000-8000-0000000000bb"


def _months_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture()
def swept(db: FakeProjectsDB):
    """A root with every lane category and a policy switched ON."""
    proj = db.seed_project(
        name="Delivery", archive_after_months=1, close_after_months=2,
    )
    todo = db.seed_status(proj.id, name="To do", category="todo", position=10)
    doing = db.seed_status(
        proj.id, name="Doing", category="in_progress", is_default=False,
        position=20,
    )
    done = db.seed_status(
        proj.id, name="Shipped", category="done", is_default=False, position=30,
    )
    cancelled = db.seed_status(
        proj.id, name="Abandoned", category="cancelled", is_default=False,
        position=40,
    )
    triage = db.seed_status(
        proj.id, name="Front door", category="triage", is_default=False,
        position=50,
    )
    return SimpleNamespace(
        project=proj, todo=todo, doing=doing, done=done, cancelled=cancelled,
        triage=triage,
    )


def test_an_old_closed_task_is_archived_with_an_automation_activity(
    db: FakeProjectsDB, swept,
) -> None:
    old = db.seed_task(
        swept.project.id, swept.done.id, title="Shipped long ago",
        updated_at=_months_ago(60),
    )
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert out["archived"] == 1 and out["projects"] == 1
    row = next(r for r in db.rows("pm_tasks") if str(r["id"]) == str(old.id))
    assert row["archived_at"] is not None
    entry = db.activities("system")[-1]
    assert entry["created_by"] == ACTOR
    assert entry["meta"]["automation"] is True
    assert "archived" in str(entry["body"]).lower()


def test_a_recently_touched_closed_task_is_left_alone(
    db: FakeProjectsDB, swept,
) -> None:
    db.seed_task(
        swept.project.id, swept.done.id, title="Fresh",
        updated_at=_months_ago(5),
    )
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert out["archived"] == 0 and out["skipped"] is True


def test_an_old_open_task_is_closed_not_archived(
    db: FakeProjectsDB, swept,
) -> None:
    """The WS-27w archive guard, held by construction: the archive pass's
    candidate lanes are the closed categories and nothing else, so a stale
    OPEN task can only be closed — and only through the ordinary transition,
    which stamps `completed_at` and writes the `status_change` row."""
    stale = db.seed_task(
        swept.project.id, swept.doing.id, title="Stalled",
        updated_at=_months_ago(90),
    )
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert out["closed"] == 1 and out["archived"] == 0
    row = next(r for r in db.rows("pm_tasks") if str(r["id"]) == str(stale.id))
    assert row["archived_at"] is None
    # Cancelled preferred over done: untouched-for-months work was abandoned,
    # not finished, and calling it done would inflate every completion report.
    assert str(row["status_id"]) == str(swept.cancelled.id)
    assert row["completed_at"] is not None
    move = db.activities("status_change")[-1]
    assert move["created_by"] == ACTOR
    assert move["meta"]["automation"] is True
    assert move["meta"]["to_category"] == "cancelled"


def test_close_falls_back_to_done_when_no_cancelled_lane(
    db: FakeProjectsDB,
) -> None:
    proj = db.seed_project(name="NoCancel", close_after_months=1)
    db.seed_status(proj.id, name="To do", category="todo", position=10)
    done = db.seed_status(
        proj.id, name="Done", category="done", is_default=False, position=20,
    )
    stale = db.seed_task(proj.id, db.rows("pm_task_statuses")[0]["id"],
                         title="Old", updated_at=_months_ago(90))
    run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    row = next(r for r in db.rows("pm_tasks") if str(r["id"]) == str(stale.id))
    assert str(row["status_id"]) == str(done.id)


def test_triage_tasks_are_exempt_from_both_passes(
    db: FakeProjectsDB, swept,
) -> None:
    """WS-27u's queue is a place work WAITS for a human ruling; a sweeper that
    closed or archived it would be the automation making the ruling."""
    parked = db.seed_task(
        swept.project.id, swept.triage.id, title="Waiting at the door",
        updated_at=_months_ago(400),
    )
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert out["archived"] == 0 and out["closed"] == 0
    row = next(r for r in db.rows("pm_tasks") if str(r["id"]) == str(parked.id))
    assert row["archived_at"] is None
    assert str(row["status_id"]) == str(swept.triage.id)


def test_the_sweep_is_idempotent(db: FakeProjectsDB, swept) -> None:
    db.seed_task(
        swept.project.id, swept.done.id, title="Old done",
        updated_at=_months_ago(60),
    )
    db.seed_task(
        swept.project.id, swept.todo.id, title="Old open",
        updated_at=_months_ago(90),
    )
    first = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert first["archived"] == 1 and first["closed"] == 1
    rows_before = len(db.rows("pm_activities"))
    second = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert second == {
        "projects": 1, "archived": 0, "closed": 0, "skipped": True,
    }
    assert len(db.rows("pm_activities")) == rows_before


def test_a_project_with_no_policy_is_never_touched(db: FakeProjectsDB) -> None:
    proj = db.seed_project(name="Untended")  # both columns NULL — the default
    status = db.seed_status(proj.id, name="Done", category="done")
    db.seed_task(proj.id, status.id, title="Ancient",
                 updated_at=_months_ago(1000))
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert out == {"projects": 0, "archived": 0, "closed": 0, "skipped": True}


def test_subprojects_inherit_the_roots_policy(db: FakeProjectsDB, swept) -> None:
    """Per-project means per ROOT project: the sweep selects on
    `root_project_id`, so a task in a subproject is governed by the root's
    columns — the same way it wears the root's statuses."""
    child = db.seed_project(name="Sub", parent=str(swept.project.id))
    old = db.seed_task(
        str(child.id), swept.done.id, root=str(swept.project.id),
        title="Done, in the sub", updated_at=_months_ago(60),
    )
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    assert out["archived"] == 1
    row = next(r for r in db.rows("pm_tasks") if str(r["id"]) == str(old.id))
    assert row["archived_at"] is not None


def test_the_projects_midnight_wins_over_utcs(db: FakeProjectsDB) -> None:
    """The P-28 point, as a fixture where the two midnights DISAGREE.

    At 2026-08-09T02:00Z it is still 2026-08-08 in Los Angeles. A one-month
    window measured from the UTC midnight (2026-08-09T00:00Z → cutoff
    2026-07-09T00:00Z) would archive a task last touched 2026-07-08T12:00Z;
    measured from the project's own midnight (2026-08-08T00:00-07:00 → cutoff
    2026-07-08T07:00Z) it is INSIDE the window and must be kept.
    """
    early = datetime(2026, 8, 9, 2, 0, tzinfo=UTC)
    proj = db.seed_project(
        name="West coast", archive_after_months=1,
        timezone="America/Los_Angeles",
    )
    done = db.seed_status(proj.id, name="Done", category="done")
    kept = db.seed_task(
        proj.id, done.id, title="Inside the LA window",
        updated_at=datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
    )
    swept_task = db.seed_task(
        proj.id, done.id, title="Outside even the LA window",
        updated_at=datetime(2026, 7, 8, 6, 0, tzinfo=UTC),
    )
    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=early))
    assert out["archived"] == 1
    rows = {str(r["id"]): r for r in db.rows("pm_tasks")}
    assert rows[str(kept.id)]["archived_at"] is None
    assert rows[str(swept_task.id)]["archived_at"] is not None


# ── 3b · the sweep's tenant (WS-27aa / H4) ──────────────────────────────────
#
# Before this ticket the roots query was `SELECT * FROM pm_projects WHERE
# parent_project_id IS NULL` — no tenant predicate at all, on a scheduled
# path. One customer's schedule archived and closed everybody's work.


@pytest.mark.parametrize("missing", ["", "   ", None])
def test_a_sweep_without_a_tenant_refuses_instead_of_sweeping_everyone(
    db: FakeProjectsDB, swept, missing,
) -> None:
    """H4's rule: a job that forgets doesn't leak one row, it leaks unbounded.

    So the absence of a tenant is an ERROR, never "all of them". Asserting on
    `db.statements` as well as the exception is what stops a later refactor
    moving the guard below the query.
    """
    db.seed_task(
        swept.project.id, swept.done.id, title="Shipped long ago",
        updated_at=_months_ago(60),
    )
    before = len(db.statements)
    with pytest.raises(TenantUnbound):
        run(run_lifecycle_sweep(
            db, organization_id=missing, actor=ACTOR, now=NOW,
        ))
    assert db.statements[before:] == []


def test_a_sweep_bound_to_one_org_leaves_the_others_stale_tasks_alone(
    db: FakeProjectsDB, swept,
) -> None:
    """The two-org proof, hermetically. `live_ws27aa.py` proves it on Postgres.

    Two roots with IDENTICAL policies and IDENTICALLY stale tasks — so the
    only thing that can tell them apart is the tenant.
    """
    mine = db.seed_task(
        swept.project.id, swept.done.id, title="Alpha, shipped long ago",
        updated_at=_months_ago(60),
    )
    theirs_project = db.seed_project(
        name="Beta delivery", archive_after_months=1, close_after_months=2,
        organization_id=ORG_B,
    )
    theirs_done = db.seed_status(
        theirs_project.id, name="Shipped", category="done", is_default=False,
    )
    theirs = db.seed_task(
        theirs_project.id, theirs_done.id, title="Beta, shipped long ago",
        updated_at=_months_ago(60),
    )

    out = run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))

    assert out["projects"] == 1 and out["archived"] == 1
    rows = {str(r["id"]): r for r in db.rows("pm_tasks")}
    assert rows[str(mine.id)]["archived_at"] is not None
    assert rows[str(theirs.id)]["archived_at"] is None, (
        "org B's stale task was archived by org A's sweep"
    )


def test_the_other_org_sweeps_its_own_when_it_is_the_one_bound(
    db: FakeProjectsDB, swept,
) -> None:
    """The mirror of the test above — without it, a sweep that silently
    matched NOTHING would pass the isolation assertion for the wrong reason."""
    mine = db.seed_task(
        swept.project.id, swept.done.id, title="Alpha, shipped long ago",
        updated_at=_months_ago(60),
    )
    theirs_project = db.seed_project(
        name="Beta delivery", archive_after_months=1, organization_id=ORG_B,
    )
    theirs_done = db.seed_status(
        theirs_project.id, name="Shipped", category="done", is_default=False,
    )
    theirs = db.seed_task(
        theirs_project.id, theirs_done.id, title="Beta, shipped long ago",
        updated_at=_months_ago(60),
    )

    out = run(run_lifecycle_sweep(db, organization_id=ORG_B, actor=ACTOR, now=NOW))

    assert out["projects"] == 1 and out["archived"] == 1
    rows = {str(r["id"]): r for r in db.rows("pm_tasks")}
    assert rows[str(theirs.id)]["archived_at"] is not None
    assert rows[str(mine.id)]["archived_at"] is None


def test_the_roots_query_carries_the_tenant_predicate(
    db: FakeProjectsDB, swept,
) -> None:
    """R7's fence, stated against the SQL itself.

    The behavioural tests above go green if the fake merely *happens* to
    filter; this one fails the moment the predicate leaves the statement.
    """
    run(run_lifecycle_sweep(db, organization_id=ORG, actor=ACTOR, now=NOW))
    roots = [s for s in db.statements if "FROM pm_projects" in s
             and "parent_project_id IS NULL" in s]
    assert roots, "the sweep no longer reads root projects at all"
    for statement in roots:
        assert "organization_id = CAST(:org AS uuid)" in statement, statement


# ── 4 · the automation flag ─────────────────────────────────────────────────

@pytest.fixture()
def patched(db: FakeProjectsDB):
    proj = db.seed_project(name="Delivery")
    todo = db.seed_status(proj.id, name="To do", category="todo")
    done = db.seed_status(
        proj.id, name="Done", category="done", is_default=False, position=20,
    )
    task = db.seed_task(proj.id, todo.id, title="A task")
    return SimpleNamespace(project=proj, todo=todo, done=done, task=task)


def test_an_engine_field_edit_is_flagged_automation(db, patched) -> None:
    run(apply_task_patch(
        db, str(patched.task.id), {"title": "Renamed"},
        actor=workflow_actor("wf-1"),
    ))
    entry = db.activities("field_change")[0]
    assert entry["meta"]["automation"] is True


def test_an_engine_status_move_is_flagged_automation(db, patched) -> None:
    run(apply_task_patch(
        db, str(patched.task.id), {"status": "Done"},
        actor=workflow_actor("wf-1"),
    ))
    entry = db.activities("status_change")[0]
    assert entry["meta"]["automation"] is True
    # The flag rides BESIDE the transition's own meta, replacing nothing.
    assert entry["meta"]["from"] == "To do" and entry["meta"]["to"] == "Done"


def test_a_human_field_edit_carries_no_flag(db, patched) -> None:
    """Not `automation: false` — no key at all. Every pre-WS-27z row lacks it,
    and a consumer must read absence and false as the same thing."""
    run(record_field_change(
        db, created_by="owner@fracktal.in", task_id=str(patched.task.id),
        changes=[{"field": "title", "old": "A task", "new": "Renamed"}],
    ))
    entry = db.activities("field_change")[0]
    assert "automation" not in entry["meta"]


def test_a_human_status_move_carries_no_flag(db, patched) -> None:
    task = next(
        SimpleNamespace(**r) for r in db.rows("pm_tasks")
        if str(r["id"]) == str(patched.task.id)
    )
    run(apply_status_transition(
        db, task, str(patched.done.id), created_by="owner@fracktal.in",
    ))
    entry = db.activities("status_change")[0]
    assert "automation" not in entry["meta"]


def test_automated_description_edits_still_coalesce(db, patched) -> None:
    """The WS-27f no-spam rule survives the flag: a workflow rewriting a
    description every run folds into one row — flag intact — instead of the
    flag's presence in meta breaking the WS-27w coalescing check."""
    actor = workflow_actor("wf-1")
    run(apply_task_patch(db, str(patched.task.id), {"description": "v1"}, actor=actor))
    run(apply_task_patch(db, str(patched.task.id), {"description": "v2"}, actor=actor))
    entries = db.activities("field_change")
    assert len(entries) == 1
    assert entries[0]["meta"]["automation"] is True
    assert entries[0]["meta"]["changes"][0]["new"] == "v2"


def test_a_human_edit_never_folds_into_an_automated_row(db, patched) -> None:
    """Same actor string, different species: the flag is part of what the row
    asserts, so folding across it would forge history in either direction."""
    run(record_field_change(
        db, created_by="x", task_id=str(patched.task.id),
        changes=[{"field": "description", "old": None, "new": "v1"}],
        automation=True,
    ))
    run(record_field_change(
        db, created_by="x", task_id=str(patched.task.id),
        changes=[{"field": "description", "old": "v1", "new": "v2"}],
    ))
    entries = db.activities("field_change")
    assert len(entries) == 2


# ── 5 · the engine wiring ───────────────────────────────────────────────────

def _graph() -> dict:
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"config": {}}},
            {"id": "sweep", "type": "pm_lifecycle", "data": {"config": {}}},
        ],
        "edges": [{"id": "e", "source": "t", "target": "sweep"}],
    }


def test_the_sweep_node_type_exists_and_publishes_config_free() -> None:
    """The workflow supplies nothing but the trigger and the node — ALL policy
    lives in the pm_projects columns, so an empty config is a publishable
    graph rather than a missing_config."""
    assert "pm_lifecycle" in NODE_TYPES
    assert validate_graph(_graph()) == []
    compiled = compile_graph(_graph())
    assert any(b["type"] == "pm_lifecycle" for b in compiled["blocks"])


def test_the_catalog_serves_the_node(monkeypatch) -> None:
    entry = next(
        (m for m in NODE_TYPE_META if m["type"] == "pm_lifecycle"), None,
    )
    assert entry is not None
    assert entry["category"] == "action"


def test_the_node_has_a_timeout_budget() -> None:
    assert NODE_TIMEOUTS["pm_lifecycle"] > NODE_TIMEOUTS["pm_task"]


def _services(**over) -> NodeServices:
    async def _unused(*a, **k):  # pragma: no cover — wrong seam
        raise AssertionError("wrong seam")

    return NodeServices(
        run_agent=_unused, run_tool=_unused, get_module_code=_unused,
        actor="workflow:test", **over,
    )


def test_the_handler_calls_the_injected_sweep() -> None:
    async def _sweep() -> dict:
        return {"projects": 2, "archived": 1, "closed": 0, "skipped": False}

    out = run(execute_node(
        {"type": "pm_lifecycle", "config": {}}, {},
        _services(run_lifecycle_sweep=_sweep),
    ))
    assert out["archived"] == 1


def test_the_handler_fails_loudly_when_projects_is_not_wired() -> None:
    with pytest.raises(NodeExecutionError):
        run(execute_node({"type": "pm_lifecycle", "config": {}}, {}, _services()))


WF_ID = "11111111-0000-4000-8000-000000000009"
OWNER = "lead@alpha.example"


def _sweeper_world(monkeypatch, *, owner_org: str | None = ORG):
    """A fake carrying one root project, one workflow and its owner.

    ``owner_org=None`` seeds an owner with **no** organization — the case
    `_workflow_organization` must refuse rather than guess at.
    """
    from gateway.routes.workflows import service as wf_service

    fake = FakeProjectsDB()
    proj = fake.seed_project(name="Delivery", archive_after_months=1)
    done = fake.seed_status(proj.id, name="Done", category="done")
    fake.seed_task(proj.id, done.id, title="Old",
                   updated_at=datetime.now(UTC) - timedelta(days=90))
    fake.seed("workflows", id=WF_ID, owner_email=OWNER)
    fake.seed("app_user", email=OWNER, organization_id=owner_org)

    async def _get_db():
        return fake

    @asynccontextmanager
    async def _tenant_session(organization_id=None):
        fake.bound_tenants.append(organization_id)
        yield fake
        await fake.commit()

    monkeypatch.setattr(wf_service, "_get_db", _get_db)
    monkeypatch.setattr(wf_service, "_tenant_session", _tenant_session)
    return wf_service, fake


def test_the_service_binding_sweeps_as_the_workflow_and_commits(
    monkeypatch,
) -> None:
    """`_pm_lifecycle_sweeper` mirrors `_pm_task_updater`: closure import, the
    workflow's identity bound in, one commit around the whole sweep."""
    wf_service, fake = _sweeper_world(monkeypatch)
    result = run(wf_service._pm_lifecycle_sweeper(WF_ID, None)())
    assert result["archived"] == 1
    assert fake.committed == 1
    assert fake.closed is True
    entry = fake.activities("system")[-1]
    assert entry["created_by"] == f"system:workflow:{WF_ID}"
    assert entry["meta"]["automation"] is True


def test_the_sweeper_binds_the_workflow_owners_organization_explicitly(
    monkeypatch,
) -> None:
    """WS-27aa / H4 — the sweep's session is opened with an EXPLICIT tenant.

    `bound_tenants` is what the fake records, so the ambient no-argument form
    (`tenant_session()`), which would inherit whoever tripped the schedule,
    fails here as `[None]`.
    """
    wf_service, fake = _sweeper_world(monkeypatch)
    run(wf_service._pm_lifecycle_sweeper(WF_ID, None)())
    assert fake.bound_tenants == [ORG]


def test_the_sweeper_takes_the_runs_organization_and_opens_no_resolver(
    monkeypatch,
) -> None:
    """H-328 part (a): a run that carries an org never reaches the old
    resolver. ORG_B proves that the run's org wins over the owner's org."""
    wf_service, fake = _sweeper_world(monkeypatch)

    async def _no_resolver():
        raise AssertionError("a run with an org opened an unbound session")

    monkeypatch.setattr(wf_service, "_get_db", _no_resolver)
    run(wf_service._pm_lifecycle_sweeper(WF_ID, ORG_B)())
    assert fake.bound_tenants == [ORG_B]


def test_the_sweeper_refuses_a_workflow_whose_owner_has_no_organization(
    monkeypatch,
) -> None:
    """Fail closed — never "the usual tenant" (handover §5 rule 3).

    And nothing is written: the refusal happens on the resolving session,
    before the tenant-bound one is ever opened.
    """
    wf_service, fake = _sweeper_world(monkeypatch, owner_org=None)
    with pytest.raises(NodeExecutionError):
        run(wf_service._pm_lifecycle_sweeper(WF_ID, None)())
    assert fake.bound_tenants == []
    assert fake.activities("system") == []


def test_the_sweeper_refuses_a_workflow_it_cannot_find(monkeypatch) -> None:
    wf_service, fake = _sweeper_world(monkeypatch)
    with pytest.raises(NodeExecutionError):
        run(wf_service._pm_lifecycle_sweeper("22222222-0000-4000-8000-00000000000f", None)())
    assert fake.bound_tenants == []


def test_build_node_services_carries_the_sweep_seam() -> None:
    from gateway.routes.workflows.service import build_node_services

    services = build_node_services("workflow", "wf-1", ORG)
    assert services.run_lifecycle_sweep is not None
    assert services.update_task is not None
