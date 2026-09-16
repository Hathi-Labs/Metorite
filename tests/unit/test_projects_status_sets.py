"""The status-set switch and its task remap, against a real Postgres.

Spec: ``project-docs/specs/project_management_app.md`` · migrations 196 and 197.

⚠️ **This path shipped with no tests at all.** PR #245 added 1856 lines,
including ``POST /nodes/{id}/status-set`` and ``core.remap_task_statuses``, and
added no test file. Nothing in ``tests/`` named ``status-set``,
``_moves_for`` or ``remap_task_statuses`` before this one.

That matters more here than it would elsewhere. The switch rewrites
``pm_tasks.status_id`` across a whole subtree in one transaction, and it can
**complete or reopen** work as a side effect. It is the most destructive bulk
write in the Projects app.

**R8, and not as ceremony.** Every claim below lives in SQL — a recursive CTE
that stops at an override, a three-arm ``COALESCE`` that picks a landing lane,
and an ``UPDATE`` whose predicate decides whose completion date is rewritten. A
hermetic fake agrees with whatever SQL it is handed, so it can confirm that a
remap was *attempted* and never that a task landed in the *right lane*.

The four claims:

* **resolution walks UP to the nearest owner** — a project uses the set of the
  nearest node at or above it that owns one, which is not the root once
  somebody overrides a subproject;
* **the scope STOPS at an override** — switching a space must not reach into a
  subproject that keeps its own lanes;
* **a homeless task lands by name, then category, then anything but triage** —
  the rule that keeps a "Blocked" task blocked across a switch;
* **completion is corrected only for tasks that moved** — and a task that was
  already complete keeps the date it completed on.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


def _async_url() -> str:
    """The same database, through the async driver the helpers need."""
    return _TENANT_URL.replace("+psycopg", "+asyncpg")


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ladder():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def _project(conn, org: str, name: str, parent: str | None, owns: bool) -> str:
    return str(conn.execute(
        text(
            "INSERT INTO pm_projects "
            "  (name, status, source, created_by, organization_id, timezone,"
            "   parent_project_id, owns_statuses) "
            "VALUES (:n, 'active', 'manual', 'statusset@example.test',"
            "        CAST(:o AS uuid), 'Asia/Kolkata', CAST(:p AS uuid), :w) "
            "RETURNING id"
        ),
        {"n": name, "o": org, "p": parent, "w": owns},
    ).scalar_one())


def _lane(conn, project: str, name: str, category: str, position: int) -> str:
    return str(conn.execute(
        text(
            "INSERT INTO pm_task_statuses "
            "  (project_id, name, color, position, category) "
            "VALUES (CAST(:p AS uuid), :n, 'gray', :pos, :cat) RETURNING id"
        ),
        {"p": project, "n": name, "pos": position, "cat": category},
    ).scalar_one())


@pytest.fixture(scope="module")
def tree(ladder):
    """Two status sets, and a subproject that overrides its space.

        root ──┬── child ── grand          (all inherit the ROOT set)
               └── owner_kid ── under_kid  (owner_kid owns its OWN set)

    ⚠️ ``owner_kid`` is what makes the scope assertions non-vacuous. Without a
    node that overrides, every "the scope stops here" test would pass against a
    walk that never stopped at all.

    The two sets differ on purpose:

    * the ROOT set is rich — seven lanes over four categories, including a
      second ``in_progress`` lane called "Blocked";
    * ``owner_kid``'s set is poor — three lanes, and it has **no** "Blocked"
      lane and **no** ``backlog`` lane at all. Those two absences are what fire
      the second and third arms of the landing rule.
    """
    made: dict[str, str] = {}
    with ladder.begin() as c:
        made["org"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        tag = uuid.uuid4().hex[:6]
        made["root"] = _project(c, made["org"], f"space-{tag}", None, True)
        made["child"] = _project(c, made["org"], "child", made["root"], False)
        made["grand"] = _project(c, made["org"], "grand", made["child"], False)
        made["owner_kid"] = _project(
            c, made["org"], "owner-kid", made["root"], True,
        )
        made["under_kid"] = _project(
            c, made["org"], "under-kid", made["owner_kid"], False,
        )

        made["r_backlog"] = _lane(c, made["root"], "Backlog", "backlog", 0)
        made["r_todo"] = _lane(c, made["root"], "To do", "todo", 1)
        made["r_prog"] = _lane(c, made["root"], "In progress", "in_progress", 2)
        made["r_blocked"] = _lane(c, made["root"], "Blocked", "in_progress", 3)
        made["r_review"] = _lane(c, made["root"], "In review", "in_progress", 4)
        made["r_done"] = _lane(c, made["root"], "Done", "done", 5)

        made["k_todo"] = _lane(c, made["owner_kid"], "To do", "todo", 0)
        made["k_prog"] = _lane(
            c, made["owner_kid"], "In progress", "in_progress", 1,
        )
        made["k_done"] = _lane(c, made["owner_kid"], "Done", "done", 2)
        # ⚠️ "Blocked" here is deliberately `todo`, while the space's "Blocked"
        # is `in_progress`. That disagreement is the ONLY thing that can tell
        # the name arm of the landing rule apart from the category arm.
        #
        # Measured 2026-09-16: without this lane, deleting the name arm from
        # `_REMAP_TARGET_SQL` entirely left all 21 tests passing, because every
        # name match in the fixture also happened to be a category match. A
        # suite that cannot see a rule deleted is not testing that rule.
        made["k_blocked"] = _lane(c, made["owner_kid"], "Blocked", "todo", 3)

    yield made

    with ladder.begin() as c:
        # Tasks first, then lanes, then the tree — pm_tasks.status_id is
        # ON DELETE RESTRICT, so a lane holding a task refuses to go.
        c.execute(
            text(
                "DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [made[k] for k in (
                "root", "child", "grand", "owner_kid", "under_kid")]},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses "
                " WHERE project_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [made["root"], made["owner_kid"]]},
        )
        for key in ("under_kid", "owner_kid", "grand", "child", "root"):
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:i AS uuid)"),
                {"i": made[key]},
            )


@pytest.fixture
async def db(tree):
    """An async connection whose transaction is ROLLED BACK after each test.

    ⚠️ ``NullPool`` and a fresh engine per test, because ``asyncio_mode =
    "auto"`` gives each test its own event loop and a pooled async connection
    bound to a closed loop fails as "attached to a different loop".

    The rollback is what lets every test write tasks onto the shared tree
    without seeing another test's rows — and it is also what keeps this suite
    from leaking rows into the scratch database, which is H-91's complaint
    about the provisioning fixtures.
    """
    eng = create_async_engine(_async_url(), poolclass=NullPool, future=True)
    conn = await eng.connect()
    trans = await conn.begin()
    try:
        yield conn
    finally:
        await trans.rollback()
        await conn.close()
        await eng.dispose()


async def _task(conn, tree, project: str, status: str, title: str) -> str:
    """One task. ``task_number`` is NOT NULL with no default (migration 146)."""
    return str((await conn.execute(
        text(
            "INSERT INTO pm_tasks "
            "  (title, project_id, root_project_id, status_id, created_by,"
            "   organization_id, task_number) "
            "SELECT :t, CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid),"
            "       'statusset@example.test', CAST(:o AS uuid),"
            "       COALESCE(MAX(task_number), 0) + 1 "
            "  FROM pm_tasks WHERE root_project_id = CAST(:r AS uuid) "
            "RETURNING id"
        ),
        {
            "t": title, "p": project, "r": tree["root"], "s": status,
            "o": tree["org"],
        },
    )).scalar_one())


async def _lane_of(conn, task_id: str) -> str:
    return str((await conn.execute(
        text(
            "SELECT s.name FROM pm_tasks t "
            "  JOIN pm_task_statuses s ON s.id = t.status_id "
            " WHERE t.id = CAST(:i AS uuid)"
        ),
        {"i": task_id},
    )).scalar_one())


async def _completed_at(conn, task_id: str):
    return (await conn.execute(
        text("SELECT completed_at FROM pm_tasks WHERE id = CAST(:i AS uuid)"),
        {"i": task_id},
    )).scalar_one()


def _names(tree: dict, ids: list[str]) -> list[str]:
    back = {v: k for k, v in tree.items()}
    return sorted(back.get(i, i) for i in ids)


# ── 1 · Resolution ──────────────────────────────────────────────────────────

class TestResolutionWalksUpToTheNearestOwner:
    """``status_owner_id`` — and it is NOT ``root_project_id`` any more.

    core.py's own warning: the two were the same question before 2026-09-06,
    and reaching for the wrong one "works perfectly until somebody overrides a
    subproject". That is precisely the case these four tests separate.
    """

    async def test_a_space_owns_its_own_set(self, db, tree):
        from gateway.routes.projects.core import status_owner_id
        assert await status_owner_id(db, tree["root"]) == tree["root"]

    async def test_a_child_that_owns_nothing_reaches_the_space(self, db, tree):
        from gateway.routes.projects.core import status_owner_id
        assert await status_owner_id(db, tree["child"]) == tree["root"]
        assert await status_owner_id(db, tree["grand"]) == tree["root"]

    async def test_an_overriding_child_answers_ITSELF(self, db, tree):
        from gateway.routes.projects.core import status_owner_id
        assert await status_owner_id(db, tree["owner_kid"]) == tree["owner_kid"]

    async def test_a_grandchild_stops_at_the_OVERRIDE_not_the_space(
        self, db, tree,
    ):
        """⚠️ The test that fails if the walk is ever replaced by a root walk.

        ``under_kid``'s root is ``root``, and its status owner is
        ``owner_kid``. Every other case in this class gives the same answer
        either way, so this is the only one that can tell them apart.
        """
        from gateway.routes.projects.core import root_project_id, status_owner_id
        assert await status_owner_id(db, tree["under_kid"]) == tree["owner_kid"]
        assert await root_project_id(db, tree["under_kid"]) == tree["root"]


# ── 2 · Scope ───────────────────────────────────────────────────────────────

class TestTheScopeStopsAtAnOverride:
    """Whose tasks move when one project changes its set."""

    async def test_a_space_sweeps_the_subtree_that_inherits(self, db, tree):
        from gateway.routes.projects.core import status_scope_ids
        got = _names(tree, await status_scope_ids(db, tree["root"]))
        assert got == ["child", "grand", "root"]

    async def test_an_overriding_subproject_is_NOT_swept(self, db, tree):
        """⚠️ The isolation the override exists to provide.

        A space switching its lanes must not reach into a subproject that
        deliberately keeps its own. Without the ``WHERE NOT p.owns_statuses``
        arm in the CTE, ``owner_kid`` and ``under_kid`` would be in this list
        and a switch at the space would silently rewrite their board.
        """
        from gateway.routes.projects.core import status_scope_ids
        got = _names(tree, await status_scope_ids(db, tree["root"]))
        assert "owner_kid" not in got
        assert "under_kid" not in got

    async def test_the_override_carries_its_OWN_subtree(self, db, tree):
        from gateway.routes.projects.core import status_scope_ids
        got = _names(tree, await status_scope_ids(db, tree["owner_kid"]))
        assert got == ["owner_kid", "under_kid"]


# ── 3 · Where a homeless task lands ─────────────────────────────────────────

class TestWhereAHomelessTaskLands:
    """The three arms of ``_REMAP_TARGET_SQL``, each fired on its own."""

    async def test_the_NAME_BEATS_the_category(self, db, tree):
        """⚠️ The test that fails if the name arm is ever deleted.

        The space's "Blocked" is ``in_progress``. The target's "Blocked" is
        ``todo``, and the target also has an "In progress" lane that comes
        first by position. So the two arms give DIFFERENT answers here, and
        only the name arm gives "Blocked".

        Every other case in this suite has a name match that is also a category
        match, which is why this one carries the whole claim: deleting the name
        arm left all 21 other tests green.
        """
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(
            db, tree, tree["child"], tree["r_blocked"], "keeps-its-lane",
        )
        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"], mapping=None,
        )
        assert await _lane_of(db, task) == "Blocked"

    async def test_a_shared_name_keeps_a_task_where_it_was(self, db, tree):
        """The ordinary case: "In progress" in one set is "In progress" in the
        other, even though the two are different rows with different ids."""
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(db, tree, tree["child"], tree["r_prog"], "keeps-name")
        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"], mapping=None,
        )
        assert await _lane_of(db, task) == "In progress"

    async def test_the_CATEGORY_catches_a_lane_the_target_lacks(self, db, tree):
        """"In review" does not exist in the target, so its CATEGORY decides.

        It is ``in_progress``, so the task stays in progress rather than
        falling back to the first lane in the set. This is the arm that keeps
        work from silently reverting to "To do".
        """
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(
            db, tree, tree["child"], tree["r_review"], "in-review-task",
        )
        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"], mapping=None,
        )
        assert await _lane_of(db, task) == "In progress"

    async def test_NEITHER_name_nor_category_falls_to_the_first_real_lane(
        self, db, tree,
    ):
        """The target set has no ``backlog`` lane and no "Backlog" name.

        The third arm takes the first lane that is not triage, in position
        order. A task must never be left pointing outside the set it resolves
        to — that is the state the board cannot draw.
        """
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(
            db, tree, tree["child"], tree["r_backlog"], "homeless-task",
        )
        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"], mapping=None,
        )
        assert await _lane_of(db, task) == "To do"

    async def test_a_task_ALREADY_in_the_target_set_is_left_alone(
        self, db, tree,
    ):
        """``old.project_id <> owner`` — the predicate that stops a self-move.

        Without it every task in scope would be rewritten on every switch, and
        the ``moved`` count the UI reports would be the size of the project.
        """
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(
            db, tree, tree["under_kid"], tree["k_todo"], "already-home",
        )
        result = await remap_task_statuses(
            db, project_id=tree["owner_kid"], owner_id=tree["owner_kid"],
            mapping=None,
        )
        assert result["moved"] == 0
        assert await _lane_of(db, task) == "To do"

    async def test_an_EXPLICIT_mapping_beats_the_automatic_rule(self, db, tree):
        """The card's answer is applied first, and the sweep only fills gaps.

        Automatically a "Blocked" task lands in "In progress" by category. A
        human who says "put those in Done" must win, or the card is decoration.
        """
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(
            db, tree, tree["child"], tree["r_blocked"], "mapped-task",
        )
        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"],
            mapping={tree["r_blocked"]: tree["k_done"]},
        )
        assert await _lane_of(db, task) == "Done"

    async def test_the_sweep_still_covers_what_the_mapping_omitted(
        self, db, tree,
    ):
        """A card built from stale counts cannot leave a task behind."""
        from gateway.routes.projects.core import remap_task_statuses
        mapped = await _task(
            db, tree, tree["child"], tree["r_blocked"], "mapped",
        )
        missed = await _task(
            db, tree, tree["child"], tree["r_backlog"], "not-in-the-card",
        )
        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"],
            mapping={tree["r_blocked"]: tree["k_done"]},
        )
        assert await _lane_of(db, mapped) == "Done"
        assert await _lane_of(db, missed) == "To do"


# ── 4 · Completion ──────────────────────────────────────────────────────────

class TestCompletionIsCorrectedOnlyForTasksThatMoved:
    """``completed_at`` is a DATE, and a switch must not invent one."""

    async def test_landing_in_a_closing_lane_stamps_completion(self, db, tree):
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(
            db, tree, tree["child"], tree["r_blocked"], "to-be-closed",
        )
        assert await _completed_at(db, task) is None
        result = await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"],
            mapping={tree["r_blocked"]: tree["k_done"]},
        )
        assert await _completed_at(db, task) is not None
        assert result["completed"] == 1

    async def test_leaving_a_closing_lane_CLEARS_completion(self, db, tree):
        """A reopened task that kept its date would read as done and not be."""
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(db, tree, tree["child"], tree["r_done"], "reopened")
        await db.execute(
            text(
                "UPDATE pm_tasks SET completed_at = now() "
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": task},
        )
        result = await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"],
            mapping={tree["r_done"]: tree["k_todo"]},
        )
        assert await _completed_at(db, task) is None
        assert result["reopened"] == 1

    async def test_a_task_that_STAYS_closed_keeps_the_date_it_closed_on(
        self, db, tree,
    ):
        """⚠️ The subtle one, and the reason the UPDATE carries a predicate.

        "Done" exists in both sets, so this task moves between two closing
        lanes. ``COALESCE(t.completed_at, now())`` alone would preserve it, but
        the extra clause — only where the lane and the row DISAGREE about being
        closed — is what keeps the row out of the UPDATE entirely.

        Without it, a switch would silently re-date every completed task in the
        project to the day somebody changed the lane names.
        """
        from gateway.routes.projects.core import remap_task_statuses
        task = await _task(db, tree, tree["child"], tree["r_done"], "long-done")
        await db.execute(
            text(
                "UPDATE pm_tasks SET completed_at = "
                "       timestamptz '2020-01-01 00:00:00+00' "
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": task},
        )
        result = await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"], mapping=None,
        )
        kept = await _completed_at(db, task)
        assert kept is not None
        assert kept.year == 2020
        assert result["completed"] == 0
        assert result["reopened"] == 0

    async def test_a_task_that_did_not_move_is_not_re_stamped(self, db, tree):
        """Completion is corrected for movers only, never for the whole scope."""
        from gateway.routes.projects.core import remap_task_statuses
        settled = await _task(
            db, tree, tree["under_kid"], tree["k_done"], "settled",
        )
        await db.execute(
            text(
                "UPDATE pm_tasks SET completed_at = "
                "       timestamptz '2019-06-01 00:00:00+00' "
                " WHERE id = CAST(:i AS uuid)"
            ),
            {"i": settled},
        )
        await remap_task_statuses(
            db, project_id=tree["owner_kid"], owner_id=tree["owner_kid"],
            mapping=None,
        )
        kept = await _completed_at(db, settled)
        assert kept is not None
        assert kept.year == 2019


# ── 5 · The card must describe the act ──────────────────────────────────────

class TestThePreviewDescribesWhatTheApplyDoes:
    """``_moves_for`` is the mapping card, and it promises the apply's answer.

    Its docstring: *"the suggestion is the same rule remap_task_statuses
    applies — an exact name, then the first lane of the same category — so the
    card pre-fills with the answer that would happen anyway"*.

    ⚠️ That promise was only two-thirds true, and this class is why.
    ``_REMAP_TARGET_SQL`` has THREE arms and the card had TWO. A lane matching
    neither by name nor by category previewed as ``suggested: None`` — a blank
    target — and its tasks then moved anyway. Measured on 2026-09-16: a
    "Backlog" task previewed as going nowhere and landed in "To do".

    A preview that under-describes a destructive act is worse than no preview.
    It is the screen somebody reads before deciding.
    """

    async def test_every_suggested_lane_is_where_the_task_REALLY_lands(
        self, db, tree,
    ):
        """The card and the apply, compared on the same tasks in one test.

        This is the assertion that makes the pair a pair. Anything that changes
        one rule and not the other fails here, which is the property two
        separately-asserted copies would lose.
        """
        from gateway.routes.projects.admin import _lanes_of, _moves_for
        from gateway.routes.projects.core import remap_task_statuses

        made = {
            "Backlog": await _task(
                db, tree, tree["child"], tree["r_backlog"], "p-backlog"),
            "Blocked": await _task(
                db, tree, tree["child"], tree["r_blocked"], "p-blocked"),
            "In progress": await _task(
                db, tree, tree["child"], tree["r_prog"], "p-prog"),
            "Done": await _task(
                db, tree, tree["child"], tree["r_done"], "p-done"),
        }

        lanes = await _lanes_of(db, tree["owner_kid"])
        predicted = {
            m["name"]: m["suggested"]
            for m in await _moves_for(db, tree["root"], lanes)
        }

        await remap_task_statuses(
            db, project_id=tree["root"], owner_id=tree["owner_kid"], mapping=None,
        )

        for was, task_id in made.items():
            assert predicted[was] is not None, (
                f"the card showed no target for '{was}', but the apply moved it"
            )
            assert predicted[was] == await _lane_of(db, task_id), (
                f"'{was}' previewed as {predicted[was]!r} and landed elsewhere"
            )

    async def test_a_lane_the_target_shares_by_name_is_marked_unchanged(
        self, db, tree,
    ):
        """"Done" exists in both sets, so the card must not ask about it.

        Listing it would ask the reader to confirm that nothing happens, which
        is how a confirmation screen stops being read.
        """
        from gateway.routes.projects.admin import _lanes_of, _moves_for
        await _task(db, tree, tree["child"], tree["r_done"], "u-done")
        lanes = await _lanes_of(db, tree["owner_kid"])
        moves = {m["name"]: m for m in await _moves_for(db, tree["root"], lanes)}
        assert moves["Done"]["unchanged"] is True

    async def test_a_lane_the_target_LACKS_is_never_marked_unchanged(
        self, db, tree,
    ):
        from gateway.routes.projects.admin import _lanes_of, _moves_for
        await _task(db, tree, tree["child"], tree["r_blocked"], "u-blocked")
        lanes = await _lanes_of(db, tree["owner_kid"])
        moves = {m["name"]: m for m in await _moves_for(db, tree["root"], lanes)}
        assert moves["Blocked"]["unchanged"] is False

    async def test_the_preview_WRITES_NOTHING(self, db, tree):
        """The card is opened far more often than it is confirmed."""
        from gateway.routes.projects.admin import _lanes_of, _moves_for
        task = await _task(
            db, tree, tree["child"], tree["r_backlog"], "untouched",
        )
        lanes = await _lanes_of(db, tree["owner_kid"])
        await _moves_for(db, tree["root"], lanes)
        assert await _lane_of(db, task) == "Backlog"
