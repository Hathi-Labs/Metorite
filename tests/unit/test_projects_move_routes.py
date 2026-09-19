"""WS-27bl — the move ENDPOINTS, not just the rules they call.

Spec: ``project-docs/specs/project_management_app.md`` §9.13.

⚠️ **Why this file exists, in one sentence: both P0-class defects this
feature shipped lived in the endpoint bodies, where the pure-rule suite and
the SQL-parse suite cannot reach.**

The first was a silent overwrite in `apply_field_map`, which the rule suite
now covers. The second was worse and was introduced by the FIX for a P2: the
repair routed the status through `apply_status_transition`, which resolves the
lane's owner from ``task.project_id`` — still the SOURCE inside the loop,
because nothing has been written yet. Every cross-set move therefore raised
422 and rolled back. That is the only case the feature exists for, so the
feature was dead, and 30 green tests said nothing about it.

Hermetic, following `test_projects_routes.py`: the route functions are called
directly against `FakeProjectsDB`. Everything asserted here is decided in
PYTHON — which guard runs, in what order, with which argument — so the SUT
runs for real.

⚠️ **What this file deliberately does NOT cover, and why it cannot.**
The cross-status-set move — the main case — resolves its landing lane through
`_REMAP_TARGET_SQL`, a COALESCE over three correlated subqueries. The fake
cannot evaluate that, and teaching it to would re-implement the rule in Python
and then assert against my own mirror: the exact thing this harness's header
warns about and that R8 exists to prevent. So the P0 that made every cross-set
move 422 would STILL not be caught here.

**That gap is real and is filed as H-124**: an end-to-end move against a real
Postgres. Until it exists, the lane resolution is covered structurally by
`test_projects_move_sql_asyncpg.py` and by nothing behavioural.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.projects import core as pm_core
from gateway.routes.projects import move as pm_move
from gateway.routes.projects import tasks as pm_tasks
from gateway.routes.projects import tree as pm_tree

from tests.unit._projects_fakes import (
    FakeProjectsDB,
    bind_db,
    projects_user,
    silence_events,
)

MODULES = (pm_core, pm_move, pm_tree, pm_tasks)
USER = projects_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeProjectsDB:
    fake = FakeProjectsDB()
    bind_db(monkeypatch, fake, MODULES)
    return fake


@pytest.fixture(autouse=True)
def _events(monkeypatch: pytest.MonkeyPatch) -> list:
    return silence_events(monkeypatch, MODULES)


def two_projects(db: FakeProjectsDB):
    """Two roots with DIFFERENT status sets — the case the feature is for."""
    source = db.seed_project(name="Bootloader", personal_owner=None)
    s_doing = db.seed_status(
        source.id, name="In progress", category="active", is_default=True,
    )
    dest = db.seed_project(name="Firmware", personal_owner=None)
    d_doing = db.seed_status(
        dest.id, name="Doing", category="active", is_default=True,
    )
    d_done = db.seed_status(
        dest.id, name="Done", category="done", is_default=False, position=40,
    )
    return source, s_doing, dest, d_doing, d_done


class TestTheRefusals:
    async def test_a_selection_spanning_two_projects_is_refused(
        self, db: FakeProjectsDB,
    ) -> None:
        source, s_doing, dest, _, _ = two_projects(db)
        other = db.seed_project(name="Elsewhere", personal_owner=None)
        o_lane = db.seed_status(other.id, name="Open", category="active")
        a = db.seed_task(source.id, status_id=s_doing.id, title="A")
        b = db.seed_task(other.id, status_id=o_lane.id, title="B")

        with pytest.raises(HTTPException) as exc:
            await pm_move.move_tasks(
                pm_move.MoveIn(
                    task_ids=[str(a.id), str(b.id)],
                    destination_project_id=str(dest.id),
                ),
                user=USER,
            )
        assert exc.value.status_code == 422

    async def test_moving_where_they_already_are_is_refused(
        self, db: FakeProjectsDB,
    ) -> None:
        source, s_doing, _dest, _, _ = two_projects(db)
        task = db.seed_task(source.id, status_id=s_doing.id, title="A")
        with pytest.raises(HTTPException) as exc:
            await pm_move.move_tasks(
                pm_move.MoveIn(
                    task_ids=[str(task.id)],
                    destination_project_id=str(source.id),
                ),
                user=USER,
            )
        assert exc.value.status_code == 422

    async def test_an_empty_selection_is_refused(self, db: FakeProjectsDB) -> None:
        _s, _sd, dest, _, _ = two_projects(db)
        with pytest.raises(HTTPException) as exc:
            await pm_move.move_tasks(
                pm_move.MoveIn(task_ids=[], destination_project_id=str(dest.id)),
                user=USER,
            )
        assert exc.value.status_code == 422


class TestThePrivacyGuardActuallyRuns:
    """⚠️ `assert_move_keeps_privacy` was DEFINED and never exercised.

    The shared fake grew `_IN_CAST_LIST` and `_CAST_PARAM` specifically so
    this guard could be driven here — and then nothing drove it. A guard with
    no test is a guard nobody notices losing, and this one is the difference
    between a move and a disclosure: landing a team task in somebody's
    personal project strips every grant holder off it at once.
    """

    async def test_a_move_into_a_PERSONAL_project_is_refused(
        self, db: FakeProjectsDB,
    ) -> None:
        source, s_doing, _dest, _, _ = two_projects(db)
        mine = db.seed_project(name="My list", personal_owner="someone@else.test")
        db.seed_status(mine.id, name="Next", category="active", is_default=True)
        task = db.seed_task(source.id, status_id=s_doing.id, title="Shared")

        with pytest.raises(HTTPException) as exc:
            await pm_move.move_tasks(
                pm_move.MoveIn(
                    task_ids=[str(task.id)],
                    destination_project_id=str(mine.id),
                ),
                user=USER,
            )
        assert exc.value.status_code in (403, 422)

    async def test_the_PREVIEW_refuses_it_too(
        self, db: FakeProjectsDB,
    ) -> None:
        """The card must not draw a clean mapping for a move the apply will
        refuse. `_plan` runs the guard, and both endpoints start with `_plan`
        — this is what pins that ordering."""
        source, s_doing, _dest, _, _ = two_projects(db)
        mine = db.seed_project(name="My list", personal_owner="someone@else.test")
        db.seed_status(mine.id, name="Next", category="active", is_default=True)
        task = db.seed_task(source.id, status_id=s_doing.id, title="Shared")

        with pytest.raises(HTTPException) as exc:
            await pm_move.preview_move(
                pm_move.MoveIn(
                    task_ids=[str(task.id)],
                    destination_project_id=str(mine.id),
                ),
                user=USER,
            )
        assert exc.value.status_code in (403, 422)


# ⚠️ **There is no hermetic test of a SUCCESSFUL move or preview, and the
# reason is narrower than it looks.** A review suggested that a move between
# two projects sharing one status home would be fully hermetic, because the
# lanes do not change. It is not. `_status_proposal` runs `_REMAP_TARGET_SQL`
# on every path — there is no same-home short circuit — so `FakeProjectsDB`
# fails on `r.to_id` whatever the vocabularies are. Measured 2026-09-20 by
# writing that test and watching it raise `AttributeError`.
#
# So H-124 covers the preview too, and closing it needs the real database.
