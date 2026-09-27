"""WS-27ae — the delta-sync feed, the satellite bump, and the small columns.

Spec: `project-docs/specs/project_management_app.md` §9.2 (WS-27ae) and §11.27,
from `plane_pm_research_2026-08.md` §3.7 (P-27, P-28).

The claims, in the order they would break:

* **deletion is EXPRESSIBLE.** A feed shaped `WHERE updated_at > :since` cannot
  say a row is gone — it simply stops appearing, and an upsert client keeps the
  ghost. `removed[]` reports both shapes it can: a tombstone (hard delete) and a
  task that changed and then fell out of the feed's own predicate (archived,
  parked in triage, moved out of the requested subtree). The one shape it CANNOT
  express — losing visibility — is pinned here too, as SILENCE, so the
  documentation and the behaviour can only move together.
* **the boundary is a KEYSET, not a second.** Two tasks stamped at the same
  instant, one delivered: the next call returns the other exactly once. That is
  the lost-update window `updated_at > :since` has and this does not.
* **satellites bump the parent.** Assign, comment, link, unlink, attach,
  re-parent, delete-a-parent: each must move `pm_tasks.updated_at`, or the feed
  misses an edit a user plainly considers "the task changed". Plus a structural
  fence (R7) so a satellite added later cannot quietly skip it.
* **the small columns have readers.** `is_epic` decides §3.4's root-level rule;
  the per-user view state is returned by `list_views` for the CALLER only.

Hermetic, against the shared `_projects_fakes.FakeProjectsDB`: the fake mirrors
migration 168's tombstone trigger and reads the keyset comparison off the
statement, so a route that drops either loses it here too. What a mirror cannot
answer — that the SQL binds, that the trigger fires on a project CASCADE, that
the horizon comes from Postgres's clock — is `tests/live/live_ws27ae_delta.py`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from gateway.routes.projects import activities as pm_activities
from gateway.routes.projects import admin as pm_admin
from gateway.routes.projects import attachments as pm_attachments
from gateway.routes.projects import bulk as pm_bulk
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import delta as pm_delta
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import views as pm_views
from gateway.routes.projects.delta import encode_cursor, parse_cursor

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    member_user,
    projects_user,
    silence_events,
)

MODULES = (
    pm_core, pm_tasks, pm_delta, pm_views, pm_activities, pm_admin,
    pm_attachments, pm_bulk,
)
USER = projects_user()
PACKAGE = Path(pm_core.__file__).parent
MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    silence_events(monkeypatch, MODULES)
    return fake


def _long_ago() -> datetime:
    """A stamp comfortably outside the horizon window.

    The feed refuses to look at anything newer than
    ``now() - HORIZON_LAG_SECONDS``, so every seeded row has to be older than
    that or the tests would be asserting against an empty answer for the right
    reason and the wrong one at the same time.
    """
    return datetime.now(UTC) - timedelta(hours=1)


def _seed_board(fake: FakeProjectsDB, *, tasks: int = 3) -> dict:
    project = fake.seed_project(name="Delivery")
    status = fake.seed_status(str(project.id))
    stamp = _long_ago()
    rows = [
        fake.seed_task(
            str(project.id), str(status.id), title=f"Task {n}",
            created_at=stamp, updated_at=stamp + timedelta(seconds=n),
        )
        for n in range(tasks)
    ]
    return {"project": project, "status": status, "tasks": rows}


# ── The cursor ──────────────────────────────────────────────────────────────

def test_a_bare_timestamp_is_inclusive_and_a_cursor_is_exact() -> None:
    """The two shapes, and the direction each errs in.

    A bare instant pairs with the ZERO uuid, so `(ts, id) > (ts, 0…)` includes
    everything stamped at exactly `ts` — re-delivering, which is idempotent for
    an upsert client. A cursor we emitted carries the real id and is exact.
    """
    when = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
    assert parse_cursor("2026-08-10T09:00:00Z") == (
        when, "00000000-0000-0000-0000-000000000000",
    )
    ident = "3f2c1a4e-0000-4000-8000-00000000abcd"
    assert parse_cursor(encode_cursor(when, ident)) == (when, ident)
    assert parse_cursor(None) is None
    assert parse_cursor("   ") is None


def test_a_naive_cursor_is_read_as_utc() -> None:
    """Stated rather than left to the connection's TimeZone — `filters.
    parse_when` established the frame and this must not disagree with it."""
    when, _ = parse_cursor("2026-08-10T09:00:00")
    assert when.tzinfo is UTC


@pytest.mark.parametrize("bad", ["tomorrow", "2026-08-10T09:00:00Z|nope", "|x"])
def test_a_malformed_cursor_is_422_never_a_silent_full_pull(bad: str) -> None:
    """⚠️ The failure this refuses is not a crash — it is a typo'd cursor
    quietly re-pulling the whole board on every poll, which reads as a
    performance problem for a month before anyone finds it."""
    with pytest.raises(HTTPException) as exc:
        parse_cursor(bad)
    assert exc.value.status_code == 422


# ── The feed ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_without_a_cursor_the_feed_is_a_snapshot(db: FakeProjectsDB) -> None:
    board = _seed_board(db)
    out = await pm_delta.delta_tasks(user=USER)
    assert out["snapshot"] is True
    assert {r["id"] for r in out["rows"]} == {str(t.id) for t in board["tasks"]}
    assert out["removed"] == []
    # A client must be told this is a replace, not a merge: `snapshot` is what
    # decides whether its local store may keep anything it did not receive.
    assert out["cursor"] and out["has_more"] is False


@pytest.mark.asyncio
async def test_a_cursor_returns_only_what_changed_after_it(
    db: FakeProjectsDB,
) -> None:
    board = _seed_board(db)
    first, second, third = board["tasks"]
    cursor = encode_cursor(first.updated_at, str(first.id))
    out = await pm_delta.delta_tasks(user=USER, since=cursor)
    assert {r["id"] for r in out["rows"]} == {str(second.id), str(third.id)}
    assert out["snapshot"] is False


@pytest.mark.asyncio
async def test_the_boundary_second_is_not_lost(db: FakeProjectsDB) -> None:
    """⚠️ THE lost-update window, and the reason the cursor is a pair.

    Two tasks stamped at the SAME instant. A feed that advanced a bare
    timestamp cursor to that instant and compared with `>` would never deliver
    the second one — and with `>=` would deliver the first one forever. The
    keyset delivers each exactly once.
    """
    project = db.seed_project(name="Same instant")
    status = db.seed_status(str(project.id))
    stamp = _long_ago()
    pair = sorted(
        (
            db.seed_task(str(project.id), str(status.id), title="A",
                         updated_at=stamp),
            db.seed_task(str(project.id), str(status.id), title="B",
                         updated_at=stamp),
        ),
        key=lambda t: str(t.id),
    )
    first = await pm_delta.delta_tasks(user=USER, limit=1)
    assert [r["id"] for r in first["rows"]] == [str(pair[0].id)]
    assert first["has_more"] is True

    second = await pm_delta.delta_tasks(user=USER, since=first["cursor"], limit=1)
    assert [r["id"] for r in second["rows"]] == [str(pair[1].id)], (
        "the boundary row sharing an instant with the last delivered one was "
        "skipped — the cursor has degraded to a bare timestamp"
    )

    third = await pm_delta.delta_tasks(user=USER, since=second["cursor"], limit=1)
    assert third["rows"] == [], "a delivered row was delivered twice"


@pytest.mark.asyncio
async def test_a_full_page_never_reports_itself_as_exhausted(
    db: FakeProjectsDB,
) -> None:
    """⚠️ The bug `len(entries) > limit` alone would ship.

    With no tombstones, a page that exactly fills `limit` produces a merged
    list of exactly `limit` entries. Reading that as "exhausted" jumps the
    cursor to the horizon and skips everything behind the page — silently, and
    only under enough load to fill one.
    """
    _seed_board(db, tasks=3)
    out = await pm_delta.delta_tasks(user=USER, limit=3)
    assert out["has_more"] is True
    assert len(out["rows"]) == 3
    rest = await pm_delta.delta_tasks(user=USER, since=out["cursor"], limit=3)
    assert rest["rows"] == [] and rest["has_more"] is False


@pytest.mark.asyncio
async def test_the_horizon_withholds_the_most_recent_writes(
    db: FakeProjectsDB,
) -> None:
    """`now()` is TRANSACTION start time, so a writer that began before this
    read can commit after it with an earlier stamp. The feed refuses to look
    into that window at all — which is what bounds the residual to "a write
    transaction that stayed open longer than the lag"."""
    project = db.seed_project(name="Fresh")
    status = db.seed_status(str(project.id))
    db.seed_task(str(project.id), str(status.id), title="Just written",
                 updated_at=datetime.now(UTC))
    out = await pm_delta.delta_tasks(user=USER)
    assert out["rows"] == [], (
        "a row stamped inside the horizon window was delivered — the cursor "
        "can now be dragged past a concurrent writer"
    )


@pytest.mark.asyncio
async def test_the_limit_is_bounded(db: FakeProjectsDB) -> None:
    for bad in (0, pm_delta.MAX_DELTA_LIMIT + 1):
        with pytest.raises(HTTPException) as exc:
            await pm_delta.delta_tasks(user=USER, limit=bad)
        assert exc.value.status_code == 422


# ── Deletion: the trap this ticket exists to avoid ──────────────────────────

@pytest.mark.asyncio
async def test_a_deleted_task_is_reported_as_removed(db: FakeProjectsDB) -> None:
    """⚠️ THE claim. Without this the feed is upsert-only and every client
    that merges what it receives keeps deleted tasks forever."""
    board = _seed_board(db, tasks=1)
    doomed = board["tasks"][0]
    cursor = encode_cursor(_long_ago() - timedelta(minutes=1), str(doomed.id))

    await pm_tasks.delete_task(str(doomed.id), user=USER)
    # Backdate the tombstone past the horizon, exactly as the seeded rows are:
    # the endpoint deliberately refuses to look at anything written in the last
    # few seconds, and this test is about deletion, not about the horizon.
    for row in db.rows("pm_task_tombstones"):
        row["deleted_at"] = _long_ago()

    out = await pm_delta.delta_tasks(user=USER, since=cursor)
    assert out["removed"] == [str(doomed.id)]
    assert out["rows"] == []


@pytest.mark.asyncio
async def test_an_archived_task_is_reported_as_removed_not_merely_absent(
    db: FakeProjectsDB,
) -> None:
    """The second expressible shape: the row still EXISTS, but it is no longer
    part of what the caller asked for. A feed that only sent rows would leave
    it on the client's board in a lane it left."""
    project = db.seed_project(name="Archive")
    status = db.seed_status(str(project.id), category="done")
    task = db.seed_task(str(project.id), str(status.id), updated_at=_long_ago())
    cursor = encode_cursor(_long_ago() - timedelta(minutes=1),
                           "00000000-0000-0000-0000-000000000000")

    await pm_tasks.archive_task(str(task.id), user=USER)
    for row in db.rows("pm_tasks"):
        row["updated_at"] = _long_ago()

    out = await pm_delta.delta_tasks(user=USER, since=cursor)
    assert out["removed"] == [str(task.id)]
    assert out["rows"] == []
    # …and asking for archived rows brings it back as a ROW, not a removal.
    both = await pm_delta.delta_tasks(
        user=USER, since=cursor, include_archived=True,
    )
    assert [r["id"] for r in both["rows"]] == [str(task.id)]
    assert both["removed"] == []


@pytest.mark.asyncio
async def test_top_level_keeps_subtasks_out_of_the_feed(db: FakeProjectsDB) -> None:
    """D-PM-38 (spec §12.9, the note for S3). Subtasks = Hidden sends
    `top_level`, so the list holds no subtask. A board that then merged a
    delta feed without it would bring the subtasks back. With `top_level` a
    subtask that changed is a REMOVAL, and its parent stays a row."""
    project = db.seed_project(name="Hidden subtasks")
    status = db.seed_status(str(project.id))
    stamp = _long_ago()
    parent = db.seed_task(
        str(project.id), str(status.id), title="Parent", updated_at=stamp,
    )
    child = db.seed_task(
        str(project.id), str(status.id), title="Child",
        parent_task_id=str(parent.id), updated_at=stamp + timedelta(seconds=1),
    )
    grandchild = db.seed_task(
        str(project.id), str(status.id), title="Grandchild",
        parent_task_id=str(child.id), updated_at=stamp + timedelta(seconds=2),
    )
    cursor = encode_cursor(stamp - timedelta(minutes=1),
                           "00000000-0000-0000-0000-000000000000")

    hidden = await pm_delta.delta_tasks(user=USER, since=cursor, top_level=True)
    assert [r["id"] for r in hidden["rows"]] == [str(parent.id)]
    assert set(hidden["removed"]) == {str(child.id), str(grandchild.id)}

    # Without the flag the feed is unchanged: every level is a row.
    every = await pm_delta.delta_tasks(user=USER, since=cursor)
    assert {r["id"] for r in every["rows"]} == {
        str(parent.id), str(child.id), str(grandchild.id),
    }
    assert every["removed"] == []


@pytest.mark.asyncio
async def test_a_triaged_task_is_a_removal_not_a_leak(db: FakeProjectsDB) -> None:
    """WS-27u's queue must stay invisible here — a sync client caches what it
    receives, so this is the worst of the five surfaces to leak it from — and
    a task that MOVES into triage has to be reported gone rather than left
    rendering on a board it has left."""
    project = db.seed_project(name="Intake")
    triage = db.seed_status(str(project.id), name="Triage", category="triage")
    task = db.seed_task(str(project.id), str(triage.id), updated_at=_long_ago())
    cursor = encode_cursor(_long_ago() - timedelta(minutes=1),
                           "00000000-0000-0000-0000-000000000000")

    hidden = await pm_delta.delta_tasks(user=USER, since=cursor)
    assert hidden["rows"] == [] and hidden["removed"] == [str(task.id)]

    asked = await pm_delta.delta_tasks(
        user=USER, since=cursor, include_triage=True,
    )
    assert [r["id"] for r in asked["rows"]] == [str(task.id)]


@pytest.mark.asyncio
async def test_losing_VISIBILITY_is_silence_and_that_is_documented(
    db: FakeProjectsDB,
) -> None:
    """⚠️ The one removal shape the feed CANNOT express, pinned as silence.

    Revoking a grant makes the row stop matching the visibility clause. Nothing
    anywhere records that it used to match, and manufacturing that record would
    mean storing a per-member history of everything anybody could ever see. So
    the client is told to reconcile periodically instead — and this test exists
    so that the day somebody "fixes" it, the docstring and the behaviour move
    together rather than the docs going quietly stale.
    """
    project = db.seed_project(name="Ops", subject="group:ops")
    status = db.seed_status(str(project.id))
    task = db.seed_task(str(project.id), str(status.id), updated_at=_long_ago())
    colleague = member_user("priya@fracktal.in")
    # ⚠️ Both, always. Once ANY `app_user` row is seeded the fake answers the
    # directory from seeded rows alone, so seeding only the colleague leaves
    # the owner with no organization and every read 404s for the wrong reason.
    for who in ("priya@fracktal.in", USER.email):
        db.seed("app_user", email=who, display_name=who,
                organization_id=db.organization_id, status="active")
    db.seed("org_group", slug="ops")
    cursor = encode_cursor(_long_ago() - timedelta(minutes=1),
                           "00000000-0000-0000-0000-000000000000")

    db.tables["pm_project_grants"] = [
        {**g, "subject": "priya@fracktal.in"}
        for g in db.rows("pm_project_grants")
    ]
    seen = await pm_delta.delta_tasks(user=colleague, since=cursor)
    assert [r["id"] for r in seen["rows"]] == [str(task.id)]

    db.tables["pm_project_grants"] = []
    after = await pm_delta.delta_tasks(user=colleague, since=cursor)
    assert after["rows"] == []
    assert after["removed"] == [], (
        "the feed now reports a visibility loss — good, but the module "
        "docstring and §11.27 say it cannot; update both in the same change"
    )
    assert after["reconcile_after"] == pm_delta.RECONCILE_AFTER, (
        "the reconcile hint is the whole mitigation for the gap above and "
        "must ride on every response"
    )


@pytest.mark.asyncio
async def test_a_tombstone_is_scoped_by_the_same_grants_the_rows_are(
    db: FakeProjectsDB,
) -> None:
    """A removal names a task id and an instant. Serving it to every member of
    the tenant regardless of grants is a weak leak through the one surface
    built to be polled continuously — so it goes through the same closure."""
    mine = db.seed_project(name="Mine", subject="priya@fracktal.in")
    theirs = db.seed_project(name="Theirs", subject="group:secret")
    for project in (mine, theirs):
        status = db.seed_status(str(project.id))
        task = db.seed_task(str(project.id), str(status.id),
                            updated_at=_long_ago())
        await pm_tasks.delete_task(str(task.id), user=USER)
    for row in db.rows("pm_task_tombstones"):
        row["deleted_at"] = _long_ago()
    # ⚠️ Both, always. Once ANY `app_user` row is seeded the fake answers the
    # directory from seeded rows alone, so seeding only the colleague leaves
    # the owner with no organization and every read 404s for the wrong reason.
    for who in ("priya@fracktal.in", USER.email):
        db.seed("app_user", email=who, display_name=who,
                organization_id=db.organization_id, status="active")

    cursor = encode_cursor(_long_ago() - timedelta(minutes=1),
                           "00000000-0000-0000-0000-000000000000")
    out = await pm_delta.delta_tasks(
        user=member_user("priya@fracktal.in"), since=cursor,
    )
    mine_ids = {
        str(t["task_id"]) for t in db.rows("pm_task_tombstones")
        if str(t["project_id"]) == str(mine.id)
    }
    assert set(out["removed"]) == mine_ids


@pytest.mark.asyncio
async def test_a_task_that_left_the_requested_project_is_removed(
    db: FakeProjectsDB,
) -> None:
    """Stream A deliberately applies NO project scoping, which is the only way
    a move out of the requested subtree can be seen at all."""
    here = db.seed_project(name="Here")
    there = db.seed_project(name="There")
    status = db.seed_status(str(here.id))
    db.seed_status(str(there.id))
    task = db.seed_task(str(here.id), str(status.id), updated_at=_long_ago())
    cursor = encode_cursor(_long_ago() - timedelta(minutes=1),
                           "00000000-0000-0000-0000-000000000000")

    for row in db.rows("pm_tasks"):
        if str(row["id"]) == str(task.id):
            row["project_id"] = str(there.id)

    out = await pm_delta.delta_tasks(
        user=USER, since=cursor, project_id=str(here.id),
    )
    assert out["removed"] == [str(task.id)]
    assert out["rows"] == []


@pytest.mark.asyncio
async def test_an_unreadable_project_filter_is_404_not_an_empty_feed(
    db: FakeProjectsDB,
) -> None:
    """The list endpoint's rule, restated: an empty feed reads as "nothing
    changed", which is the answer a sync client acts on."""
    hidden = db.seed_project(name="Hidden", subject=None)
    db.seed_status(str(hidden.id))
    # ⚠️ Both, always. Once ANY `app_user` row is seeded the fake answers the
    # directory from seeded rows alone, so seeding only the colleague leaves
    # the owner with no organization and every read 404s for the wrong reason.
    for who in ("priya@fracktal.in", USER.email):
        db.seed("app_user", email=who, display_name=who,
                organization_id=db.organization_id, status="active")
    with pytest.raises(HTTPException) as exc:
        await pm_delta.delta_tasks(
            user=member_user("priya@fracktal.in"), project_id=str(hidden.id),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_the_feed_is_tenant_scoped_like_every_other_read(
    db: FakeProjectsDB,
) -> None:
    """R11 — and it is checked on the STATEMENT, because a fake that filtered
    by tenant regardless would agree with a route that dropped the clause."""
    _seed_board(db, tasks=1)
    await pm_delta.delta_tasks(user=USER)
    scans = [
        s for s in db.statements
        if "FROM pm_tasks t" in s and "ORDER BY t.updated_at" in s
    ]
    assert scans, "the delta scan did not run"
    for statement in scans:
        assert "organization_id = CAST(:vis_org AS uuid)" in statement
    tombstone_scans = [s for s in db.statements if "pm_task_tombstones" in s]
    assert tombstone_scans
    for statement in tombstone_scans:
        assert "tb.organization_id = CAST(:vis_org AS uuid)" in statement


def test_the_feed_takes_no_tenant_or_identity_from_the_request() -> None:
    """R11, structurally. `routes/projects` holds NO H2 exemption and sits at
    zero unbound sites; a query parameter named after a tenant or a person is
    how that gets reintroduced."""
    from gateway.routes.projects import router

    route = next(r for r in router.routes if r.path == "/projects/delta/tasks")
    names = {p.name for p in route.dependant.query_params}
    assert not (names & {
        "organization_id", "org", "tenant", "tenant_id", "email", "user",
        "user_email", "member", "actor",
    }), f"the delta feed accepts an identity-shaped parameter: {sorted(names)}"


def test_the_path_cannot_be_shadowed_by_the_task_id_route() -> None:
    """`/projects/tasks/delta` would be matched by `/projects/tasks/{task_id}`
    and the answer would depend on import order — the trap
    `routes/workflows/__init__.py` documents."""
    from gateway.routes.projects import router

    paths = {r.path for r in router.routes}
    assert "/projects/delta/tasks" in paths
    assert "/projects/tasks/delta" not in paths


# ── The satellite bump (P-27's prerequisite) ────────────────────────────────

async def _stamp_of(fake: FakeProjectsDB, task_id: str) -> datetime:
    for row in fake.rows("pm_tasks"):
        if str(row["id"]) == str(task_id):
            return row["updated_at"]
    raise AssertionError(f"no such task {task_id}")


@pytest.mark.asyncio
async def test_assigning_bumps_the_task(db: FakeProjectsDB) -> None:
    board = _seed_board(db, tasks=1)
    task = board["tasks"][0]
    before = await _stamp_of(db, str(task.id))
    await pm_tasks.set_assignees(
        str(task.id), pm_tasks.AssigneesIn(assignees=["priya@fracktal.in"]),
        user=USER,
    )
    assert await _stamp_of(db, str(task.id)) > before


@pytest.mark.asyncio
async def test_commenting_editing_and_deleting_a_comment_all_bump(
    db: FakeProjectsDB,
) -> None:
    """The edit and the soft delete are UPDATEs on `pm_activities`, so neither
    reaches `record_activity`'s bump — they were the two easiest to miss."""
    board = _seed_board(db, tasks=1)
    task = board["tasks"][0]

    before = await _stamp_of(db, str(task.id))
    comment = await pm_activities.add_comment(
        str(task.id), pm_activities.CommentIn(body="First"), user=USER,
    )
    after_add = await _stamp_of(db, str(task.id))
    assert after_add > before

    for row in db.rows("pm_tasks"):
        row["updated_at"] = _long_ago()
    await pm_activities.edit_comment(
        str(comment["id"]), pm_activities.CommentIn(body="Second"), user=USER,
    )
    assert await _stamp_of(db, str(task.id)) > _long_ago()

    for row in db.rows("pm_tasks"):
        row["updated_at"] = _long_ago()
    await pm_activities.delete_comment(str(comment["id"]), user=USER)
    assert await _stamp_of(db, str(task.id)) > _long_ago()


@pytest.mark.asyncio
async def test_linking_and_unlinking_bump_BOTH_ends(db: FakeProjectsDB) -> None:
    """⚠️ The target is the end that matters. `attach_relation_counts` draws
    "blocked" on the TARGET, so a client watching only the blocked task would
    otherwise never learn it is blocked."""
    board = _seed_board(db, tasks=2)
    source, target = board["tasks"]
    for row in db.rows("pm_tasks"):
        row["updated_at"] = _long_ago()

    link = await pm_tasks.create_link(
        str(source.id),
        pm_tasks.LinkIn(target_task_id=str(target.id), link_type="blocks"),
        user=USER,
    )
    assert await _stamp_of(db, str(source.id)) > _long_ago()
    assert await _stamp_of(db, str(target.id)) > _long_ago()

    for row in db.rows("pm_tasks"):
        row["updated_at"] = _long_ago()
    await pm_tasks.delete_link(str(source.id), str(link["id"]), user=USER)
    assert await _stamp_of(db, str(source.id)) > _long_ago()
    assert await _stamp_of(db, str(target.id)) > _long_ago(), (
        "unlinking bumped only the end named in the path — the other end's "
        "blocked badge just went stale on every synced client"
    )


@pytest.mark.asyncio
async def test_re_parenting_bumps_the_old_and_the_new_parent(
    db: FakeProjectsDB,
) -> None:
    board = _seed_board(db, tasks=3)
    old_parent, new_parent, child = board["tasks"]
    for row in db.rows("pm_tasks"):
        if str(row["id"]) == str(child.id):
            row["parent_task_id"] = str(old_parent.id)
        row["updated_at"] = _long_ago()

    await pm_tasks.move_task(
        str(child.id), pm_tasks.MoveTask(parent_task_id=str(new_parent.id)),
        user=USER,
    )
    assert await _stamp_of(db, str(old_parent.id)) > _long_ago()
    assert await _stamp_of(db, str(new_parent.id)) > _long_ago()


@pytest.mark.asyncio
async def test_deleting_a_parent_bumps_the_subtasks_it_promotes(
    db: FakeProjectsDB,
) -> None:
    """`parent_task_id` SET NULLs, so the promoted rows change without any
    statement in this package writing them."""
    board = _seed_board(db, tasks=2)
    parent, child = board["tasks"]
    for row in db.rows("pm_tasks"):
        if str(row["id"]) == str(child.id):
            row["parent_task_id"] = str(parent.id)
        row["updated_at"] = _long_ago()

    await pm_tasks.delete_task(str(parent.id), user=USER)
    assert await _stamp_of(db, str(child.id)) > _long_ago()


def test_watching_deliberately_does_NOT_bump() -> None:
    """A watcher row is one person's subscription. Nothing another client
    renders about the task changes, and bumping would wake every synced device
    in the company every time somebody pressed Watch — so `watchers.py` must
    stay off the bump, and that is an ABSENCE, which only a structural test can
    assert."""
    from gateway.routes.projects import watchers as pm_watchers

    source = re.sub(
        r"#.*", "",
        Path(pm_watchers.__file__).read_text(encoding="utf-8"),
    )
    assert "touch_task" not in source
    assert "record_activity" not in source, (
        "watch/unwatch now writes a timeline entry, which bumps the task "
        "through the spine — decide whether that is wanted before deleting "
        "this assertion"
    )


@pytest.mark.asyncio
async def test_a_bump_with_no_ids_issues_no_statement(
    db: FakeProjectsDB,
) -> None:
    """So a caller never has to guard the call — and so `record_activity` on a
    PROJECT-level entry does not fire a task update with a NULL id."""
    board = _seed_board(db, tasks=1)
    before = len(db.statements)
    await pm_core.touch_task(db)
    await pm_core.touch_task(db, None, "")
    assert len(db.statements) == before
    assert await _stamp_of(db, str(board["tasks"][0].id)) is not None


def test_every_satellite_writer_bumps_its_task_or_is_named_here() -> None:
    """R7 — the fence. A satellite table added later must not be able to skip
    the bump by being written from a module nobody remembered to check.

    Structural rather than behavioural because the failure is a MISSING call:
    a behavioural suite can only cover the satellites somebody thought of, and
    the whole point is the one they did not.
    """
    #: Tables whose rows are part of "the task", so writing one IS a change to
    #: it. The module doing the writing must reach `touch_task`, either
    #: directly or through `record_activity`'s choke point.
    satellites = (
        "pm_task_assignees", "pm_task_links", "pm_task_attachments",
        "pm_activities",
    )
    #: Modules that write a satellite only for a task they CREATED in the same
    #: transaction. A brand-new row is already stamped `now()`; bumping it
    #: would be a second write for no change.
    creation_only = {
        "import_clickup.py", "import_tasks.py", "personal.py",
        "recurrence.py", "intake.py",
    }
    #: Satellites deliberately NOT bumped, with the reason. Each is per-person
    #: or per-view state that no other client renders about the task.
    #: (Kept as prose here rather than in a comment so the list is greppable.)
    assert {
        "pm_task_watchers": "one person's subscription; invisible to others",
        "pm_task_personal": "the per-user mirror overlay (§6.1)",
        "pm_notifications": "one person's bell, not the task",
        "pm_view_task_positions": "per-VIEW ordering (D-PM-5), not the task",
        "pm_view_user_state": "one member's screen arrangement",
    }, "the not-bumped list must never be emptied without a decision"

    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        stripped = re.sub(r"#.*", "", source)
        writes = any(
            re.search(rf"(INSERT INTO|DELETE FROM|UPDATE)\s+{table}", stripped)
            for table in satellites
        )
        if not writes or path.name in creation_only or path.name == "core.py":
            continue
        if "touch_task" not in stripped and "record_activity" not in stripped:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} write a task satellite and never bump the task — every "
        f"delta client will miss the edit"
    )


def test_the_bump_has_exactly_one_implementation() -> None:
    """A second `UPDATE pm_tasks SET updated_at` somewhere else is how the two
    start disagreeing about which writes count as a change."""
    owners = sorted(
        path.name for path in PACKAGE.glob("*.py")
        if "SET updated_at = now()" in re.sub(r"#.*", "", path.read_text(
            encoding="utf-8"))
        and "pm_tasks" in path.read_text(encoding="utf-8")
    )
    assert owners == ["core.py"], (
        f"the satellite bump is written out in {owners}; it belongs to "
        f"core.touch_task"
    )


# ── The small columns ───────────────────────────────────────────────────────

def test_is_epic_is_what_the_hierarchy_rule_reads() -> None:
    """P-28's point: the rule stops depending on a SEED NAME.

    A project that calls its top level "Initiative" gets the rule; a
    user-created type merely named "Epic" still does not.
    """
    from types import SimpleNamespace

    assert pm_core.is_epic_type(
        SimpleNamespace(is_epic=True, is_system=False, name="Initiative"))
    assert pm_core.is_epic_type(
        SimpleNamespace(is_epic=False, is_system=True, name="Epic"))
    assert not pm_core.is_epic_type(
        SimpleNamespace(is_epic=False, is_system=False, name="Epic"))


@pytest.mark.asyncio
async def test_a_flagged_user_type_refuses_a_parent(db: FakeProjectsDB) -> None:
    """The reader, exercised through the real write path — a flag nothing
    enforces is the `pm_task_statuses.color` failure repeated."""
    project = db.seed_project(name="Portfolio")
    status = db.seed_status(str(project.id))
    parent = db.seed_task(str(project.id), str(status.id), title="Parent")
    kind = await pm_admin.create_type(
        str(project.id), pm_admin.TypeIn(name="Initiative", is_epic=True),
        user=USER,
    )
    assert kind["is_epic"] is True
    with pytest.raises(HTTPException) as exc:
        await pm_tasks.create_task(
            pm_tasks.TaskIn(
                project_id=str(project.id), title="Nested initiative",
                type_id=str(kind["id"]), parent_task_id=str(parent.id),
            ),
            user=USER,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_the_system_epic_cannot_be_un_flagged(db: FakeProjectsDB) -> None:
    """409 rather than a 200 that changes nothing: `is_epic_type` still knows
    the seeded system type by name, so a silent success would be a write that
    reports itself applied while the rule stays on."""
    project = db.seed_project(name="Portfolio")
    epic = db.seed("pm_task_types", project_id=project.id, name="Epic",
                   is_system=True, is_epic=True)
    with pytest.raises(HTTPException) as exc:
        await pm_admin.patch_type(
            str(epic.id), pm_admin.TypeIn(is_epic=False), user=USER,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_per_user_view_state_is_read_back_by_list_views(
    db: FakeProjectsDB,
) -> None:
    """The READER for the new table. `list_views` is the call the board already
    makes, so the overlay arrives with the views rather than a frame later."""
    project = db.seed_project(name="Delivery")
    view = db.seed("pm_views", project_id=project.id, name="Board",
                   view_type="board", created_by="owner@fracktal.in")

    await pm_views.set_view_state(
        str(view.id),
        pm_views.UserStateIn(config={"collapsed_lanes": ["done"],
                                     "group_by": "assignee"}),
        user=USER,
    )
    listed = await pm_views.list_views(str(project.id), user=USER)
    assert listed["rows"][0]["user_state"] == {
        "group_by": "assignee", "collapsed_lanes": ["done"],
    }


@pytest.mark.asyncio
async def test_one_members_view_state_is_invisible_to_another(
    db: FakeProjectsDB,
) -> None:
    """It is a window arrangement, not a record — and there is deliberately no
    endpoint that could serve somebody else's."""
    project = db.seed_project(name="Delivery")
    view = db.seed("pm_views", project_id=project.id, name="Board",
                   view_type="board", created_by="owner@fracktal.in")
    # ⚠️ Both, always. Once ANY `app_user` row is seeded the fake answers the
    # directory from seeded rows alone, so seeding only the colleague leaves
    # the owner with no organization and every read 404s for the wrong reason.
    for who in ("priya@fracktal.in", USER.email):
        db.seed("app_user", email=who, display_name=who,
                organization_id=db.organization_id, status="active")
    await pm_views.set_view_state(
        str(view.id),
        pm_views.UserStateIn(config={"collapsed_lanes": ["done"]}), user=USER,
    )
    theirs = await pm_views.get_view_state(
        str(view.id), user=member_user("priya@fracktal.in"),
    )
    assert theirs["config"] == {}


@pytest.mark.asyncio
async def test_a_per_user_overlay_cannot_carry_filters(
    db: FakeProjectsDB,
) -> None:
    """⚠️ The rule that keeps a SHARED view shared: two people must never be
    looking at a view that means two different sets of tasks."""
    project = db.seed_project(name="Delivery")
    view = db.seed("pm_views", project_id=project.id, name="Board",
                   view_type="board", created_by="owner@fracktal.in")
    out = await pm_views.set_view_state(
        str(view.id),
        pm_views.UserStateIn(config={
            "filters": {"assignee": "priya@fracktal.in"},
            "collapsed_lanes": ["done"],
        }),
        user=USER,
    )
    assert "filters" not in out["config"]


def test_the_overlay_normaliser_preserves_ABSENCE() -> None:
    """⚠️ Not `normalise_view_config` with a different key set.

    That one FILLS `group_by` so a view always renders something. An overlay
    must not: a key it does not carry means "use the view's", and injecting a
    default would silently re-group the board for whoever collapsed a lane.
    """
    from gateway.routes.projects.filters import (
        normalise_view_config,
        normalise_view_user_state,
    )

    assert normalise_view_config({}) == {"filters": {}, "group_by": "status"}
    assert normalise_view_user_state({}) == {}
    assert normalise_view_user_state({"collapsed_lanes": []}) == {
        "collapsed_lanes": [],
    }, "an explicitly empty collapse list is an opinion, not an absence"


def test_the_session_user_id_denorm_is_already_there() -> None:
    """P-28's fourth item, checked rather than built.

    ⚠️ `pm_task_statuses.color` was stored from migration 146 and rendered
    nowhere for twenty-one migrations. A column with no reader is not a
    feature — so this one was measured before being written, and it turned out
    `chat_session` already carries an indexed `user_id`. Nothing to add.
    """
    schema = (MIGRATIONS / "02_chat_history.sql").read_text(encoding="utf-8")
    assert "user_id         TEXT NOT NULL" in schema
    assert "chat_session_user_idx" in schema and "(user_id, updated_at DESC)" in schema


# ── Migration 168, as text ──────────────────────────────────────────────────

def test_migration_168_is_idempotent_and_additive() -> None:
    """R6. The deploy applies this BEFORE restarting services, so it has to be
    survivable by the code that is already running — and re-runnable, because
    the ledger re-applies an edited file on the next deploy."""
    raw = (MIGRATIONS / "168_projects_delta_sync.sql").read_text(encoding="utf-8")
    # ⚠️ Comments stripped FIRST. The header explains *why* nothing is renamed
    # or dropped, and a scan over the prose fails on the word in the sentence
    # that promises it — which is a gate that teaches people not to comment.
    sql = "\n".join(
        line.split("--")[0] for line in raw.splitlines()
    )
    assert "CREATE TABLE IF NOT EXISTS pm_task_tombstones" in sql
    assert "CREATE TABLE IF NOT EXISTS pm_view_user_state" in sql
    assert "ADD COLUMN IF NOT EXISTS is_epic BOOLEAN NOT NULL DEFAULT false" in sql
    assert "CREATE OR REPLACE TRIGGER trg_pm_tasks_tombstone" in sql
    assert "CREATE OR REPLACE FUNCTION pm_task_tombstone()" in sql
    for forbidden in ("DROP TABLE", "DROP COLUMN", "RENAME"):
        assert forbidden not in sql.upper(), (
            f"{forbidden} in an expand phase — R6 says tighten in a LATER "
            f"release, and this deployment cannot roll back"
        )
    for index in ("idx_pm_tasks_org_updated",
                  "idx_pm_task_tombstones_org_deleted"):
        assert f"CREATE INDEX IF NOT EXISTS {index}" in sql


def test_both_new_tables_carry_the_tenant_key() -> None:
    """R5 / D-MT-3 — the key on every tenant-owned row, never derived through a
    parent. `pm_task_types.is_epic` needs nothing: it is a column on a table
    that already carries it."""
    sql = (MIGRATIONS / "168_projects_delta_sync.sql").read_text(encoding="utf-8")
    for table in ("pm_task_tombstones", "pm_view_user_state"):
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {table}")[1].split(");")[0]
        assert "organization_id UUID NOT NULL" in body, (
            f"{table} is not tenant-scoped"
        )


def test_the_new_tables_are_in_the_generated_policy_set() -> None:
    """MT-1b's own lesson, repeated: `pm_intake` and `pm_task_watchers` were
    absent from all four phase files, which would have promoted two Projects
    tables with a tenant column and NO policy."""
    policies = (MIGRATIONS / "generated" / "04_policies.sql")
    if not policies.is_file():  # pragma: no cover — generator not run here
        pytest.skip("generated set not present in this checkout")
    text_value = policies.read_text(encoding="utf-8")
    for table in ("pm_task_tombstones", "pm_view_user_state"):
        assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in text_value
