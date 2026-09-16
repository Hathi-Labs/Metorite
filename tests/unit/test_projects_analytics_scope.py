"""WS-27bk §9.12.7 — the analytics scope, including the PORTFOLIO.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.7.

§9.12.7's Done-when says each slice "answers for a subtree **and for the
portfolio**". All three shipped node-only: `project_id` was required, so the
Analytics pane — which reads the portfolio roll-up and holds no node id — could
call none of them.

⚠️ **Dropping the node moves a security boundary, and that is what this file is
mostly about.** While `project_id` was required, an unreadable id answered 404
and the subtree walk bounded the read. With `project_id=None` the scope clause
is the literal ``TRUE``, and the ONLY things left holding the read inside the
caller's world are ``vis.task_clause()`` and the tenant session. A portfolio
read is the exact shape a disclosure bug takes: it looks like a dashboard.

So these run against a REAL Postgres (R8), with a second organization and a
second space present in the database, and they assert what the caller CANNOT
see as carefully as what they can.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_TENANT_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _TENANT_URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. A "
        "skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def db():
    eng = create_engine(_TENANT_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def two_orgs(db):
    """Two organizations, each with its own space, project and open task.

    ⚠️ The second organization is the whole point. A portfolio read that is
    bounded by nothing still passes every single-tenant assertion, because
    there is nothing else in the database to leak.
    """
    made: dict[str, str] = {}
    with db.begin() as c:
        made["org_a"] = str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())
        made["org_b"] = str(c.execute(
            text(
                "INSERT INTO organization (display_name, slug) VALUES (:n, :s)"
                " RETURNING id"
            ),
            {
                "n": f"Scope B {uuid.uuid4().hex[:6]}",
                "s": f"scope-b-{uuid.uuid4().hex[:8]}",
            },
        ).scalar_one())

        for tag in ("a", "b"):
            org = made[f"org_{tag}"]
            # Two ROOTS in org A, so a portfolio read has something to add up
            # that a single subtree walk would miss.
            for n in ("one", "two") if tag == "a" else ("one",):
                pid = str(c.execute(
                    text(
                        "INSERT INTO pm_projects (name, status, source,"
                        " created_by, organization_id, timezone,"
                        " parent_project_id, owns_statuses)"
                        " VALUES (:n,'active','manual','scope@example.test',"
                        " CAST(:o AS uuid),'Asia/Kolkata',NULL,true)"
                        " RETURNING id"
                    ),
                    {"n": f"scope-{tag}-{n}-{uuid.uuid4().hex[:6]}", "o": org},
                ).scalar_one())
                made[f"{tag}_{n}"] = pid
                sid = str(c.execute(
                    text(
                        "INSERT INTO pm_task_statuses (project_id,name,color,"
                        " position,category) VALUES (CAST(:p AS uuid),"
                        " 'To do','gray',0,'todo') RETURNING id"
                    ),
                    {"p": pid},
                ).scalar_one())
                c.execute(
                    text(
                        "INSERT INTO pm_tasks (title, project_id,"
                        " root_project_id, status_id, created_by,"
                        " organization_id, task_number)"
                        " VALUES (:t, CAST(:p AS uuid), CAST(:p AS uuid),"
                        " CAST(:s AS uuid), 'scope@example.test',"
                        " CAST(:o AS uuid), 1)"
                    ),
                    {"t": f"task-{tag}-{n}", "p": pid, "s": sid, "o": org},
                )
    yield made
    with db.begin() as c:
        ids = [v for k, v in made.items() if not k.startswith("org_")]
        c.execute(
            text(
                "DELETE FROM pm_tasks WHERE project_id = ANY(CAST(:p AS uuid[]))"
            ),
            {"p": ids},
        )
        c.execute(
            text(
                "DELETE FROM pm_task_statuses"
                " WHERE project_id = ANY(CAST(:p AS uuid[]))"
            ),
            {"p": ids},
        )
        c.execute(
            text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
            {"p": ids},
        )
        c.execute(
            text("DELETE FROM organization WHERE id = CAST(:o AS uuid)"),
            {"o": made["org_b"]},
        )


def _vis(org_id):
    """An UNRESTRICTED caller in one organization.

    Deliberately the widest caller the product has — `data:org:read`, the
    People Center's full-portfolio view. If the tenant bound holds for this
    one it holds for everybody, and a narrower fixture would pass while the
    widest caller leaked.
    """
    from gateway.routes.projects.core import Visibility

    return Visibility(
        unrestricted=True, email="", groups=(), organization_id=org_id,
    )


class TestThePortfolioArmNeedsNoNode:
    """`project_id=None` must not try to load a project that does not exist."""

    async def test_it_answers_TRUE_without_a_database_session(self):
        from gateway.routes.projects.analytics import scope_clause

        # `db=None` is the assertion: a portfolio scope that queried anything
        # would raise here rather than return.
        assert await scope_clause(None, _vis(None), None, True) == "TRUE"

    async def test_it_binds_no_pid(self):
        from gateway.routes.projects.analytics import scope_params

        # ⚠️ A bound parameter with no placeholder is a driver error on some
        # stacks and a silent no-op on others.
        assert scope_params(None) == {}
        assert scope_params("abc") == {"pid": "abc"}


class TestThePortfolioIsStillTheCallersOwn:
    """⚠️ The disclosure test. `TRUE` is a scope, not an absence of one."""

    async def test_it_counts_every_space_the_caller_can_see(self, db, two_orgs):
        from gateway.routes.projects.analytics import scope_params, total_open_sql
        from gateway.routes.projects.core import task_visibility_clause

        vis = _vis(two_orgs["org_a"])
        where = (
            "TRUE"
            " AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
        )
        with db.connect() as c:
            total = c.execute(
                text(total_open_sql(where)),
                {**vis.params, **scope_params(None)},
            ).scalar()
        # Both of org A's roots. A subtree walk from either one alone
        # would have answered 1, which is the bug this scope exists to fix.
        assert int(total) >= 2

    async def test_it_does_NOT_reach_the_other_organization(self, db, two_orgs):
        """⚠️ The one that matters.

        Org B's task is open, unarchived, and matches every predicate except
        the tenant. A portfolio read bounded by nothing returns it.
        """
        from gateway.routes.projects.analytics import scope_params
        from gateway.routes.projects.core import task_visibility_clause

        titles_sql = (
            "SELECT t.title FROM pm_tasks t"
            "  JOIN pm_task_statuses s ON s.id = t.status_id"
            " WHERE TRUE AND t.archived_at IS NULL"
            f"   AND ({task_visibility_clause(_vis(two_orgs['org_a']), 't')})"
        )
        vis = _vis(two_orgs["org_a"])
        with db.connect() as c:
            titles = {
                r.title for r in c.execute(
                    text(titles_sql), {**vis.params, **scope_params(None)},
                ).fetchall()
            }
        assert "task-a-one" in titles
        assert "task-a-two" in titles
        assert "task-b-one" not in titles, "PORTFOLIO READ CROSSED A TENANT"

    async def test_a_caller_with_no_directory_row_sees_NOTHING(
        self, db, two_orgs,
    ):
        """`organization_id=None` fails closed, and the portfolio must too.

        `column = NULL` is NULL rather than true, so the tenant arm rejects
        every row. Asserted here because the portfolio arm is the one place
        where a caller reaches the read with no node to be refused by.
        """
        from gateway.routes.projects.analytics import scope_params, total_open_sql
        from gateway.routes.projects.core import task_visibility_clause

        vis = _vis(None)
        where = (
            "TRUE AND t.archived_at IS NULL"
            f" AND ({task_visibility_clause(vis, 't')})"
        )
        with db.connect() as c:
            total = c.execute(
                text(total_open_sql(where)),
                {**vis.params, **scope_params(None)},
            ).scalar()
        assert int(total or 0) == 0


class TestAllThreeEndpointsTakeTheSameScope:
    """R7 — one scope helper, or three chances to get the portfolio wrong."""

    def test_no_endpoint_builds_its_own_subtree_walk(self):
        import inspect

        from gateway.routes.projects import analytics

        source = inspect.getsource(analytics)
        # The recursive walk appears ONCE, inside `scope_clause`. A second copy
        # is a second place for the portfolio arm to be missing.
        assert source.count("WITH RECURSIVE sub AS") == 1

    def test_every_endpoint_accepts_an_absent_project(self):
        import inspect

        from gateway.routes.projects import analytics

        for fn in (analytics.stuck, analytics.load, analytics.throughput):
            param = inspect.signature(fn).parameters["project_id"]
            assert param.default is None, f"{fn.__name__} still requires a node"
