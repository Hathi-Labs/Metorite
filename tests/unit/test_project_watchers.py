"""WS-27bk §9.12.2(b) — watching a PROJECT, against a real Postgres.

Spec: ``project-docs/specs/project_management_app.md`` §9.12.2(b) ·
migration 203.

⚠️ **R8, and the reason is specific rather than ceremonial.** The feature's
whole risk lives in one recursive CTE that walks a project's ancestor chain.
``test_projects_watchers.py`` — the TASK watcher suite — runs against
``FakeProjectsDB``, which answers off the statement text. A fake agrees with
whatever SQL it is handed, so it can confirm that a walk was *attempted* and
never that the walk is *correct*. Direction, depth bounds and cycle behaviour
are exactly what a fake cannot see.

So this suite executes **the module's own SQL string**, imported rather than
copied. An edit to ``_CHAIN_WATCHERS_SQL`` is an edit to what runs here, which
is the property a transcribed query would lose on its first divergence.

The three claims:

* **the walk goes UP, never DOWN** — watching a parent hears about its
  children, and watching a child never subscribes you to a sibling's traffic;
* **the tenant is filled from the project, and cannot be forged** — migration
  203 attaches 161's trigger, so no INSERT site has to remember ``R5``;
* **a watcher is a folded human address** — the two CHECKs that make every
  subscription comparable and keep agents out of an inbox nobody opens.
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
def org_id(db) -> str:
    with db.connect() as c:
        return str(c.execute(
            text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
        ).scalar_one())


def _project(conn, org: str, name: str, parent: str | None = None) -> str:
    """One project node. A ROOT must own its statuses (the 247 CHECK)."""
    return str(conn.execute(
        text(
            "INSERT INTO pm_projects "
            "  (name, status, source, created_by, organization_id, timezone,"
            "   parent_project_id, owns_statuses) "
            "VALUES (:n, 'active', 'manual', 'probe@example.test',"
            "        CAST(:o AS uuid), 'Asia/Kolkata',"
            "        CAST(:p AS uuid), :owns) "
            "RETURNING id"
        ),
        {"n": name, "o": org, "p": parent, "owns": parent is None},
    ).scalar_one())


@pytest.fixture
def tree(db, org_id):
    """root → child → grandchild, plus a SIBLING of child under the same root.

    The sibling is what makes the direction assertion non-vacuous: a walk that
    went down from the root would sweep it in, and every "up" test would still
    pass without it.
    """
    made: dict[str, str] = {}
    with db.begin() as c:
        made["root"] = _project(c, org_id, f"root-{uuid.uuid4().hex[:6]}")
        made["child"] = _project(c, org_id, "child", made["root"])
        made["grandchild"] = _project(c, org_id, "grandchild", made["child"])
        made["sibling"] = _project(c, org_id, "sibling", made["root"])
    yield made
    with db.begin() as c:
        # Children first — the FK is ON DELETE CASCADE from the parent, but an
        # explicit order keeps this readable if that ever changes.
        for key in ("grandchild", "sibling", "child", "root"):
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:i AS uuid)"),
                {"i": made[key]},
            )


def _watch(conn, project_id: str, who: str) -> None:
    conn.execute(
        text(
            "INSERT INTO pm_project_watchers (project_id, watcher, created_by) "
            "VALUES (CAST(:p AS uuid), :w, 'test') "
            "ON CONFLICT (project_id, watcher) DO NOTHING"
        ),
        {"p": project_id, "w": who},
    )


def _chain(db, project_id: str) -> list[str]:
    """Run the ROUTE MODULE'S OWN SQL. Imported, never transcribed."""
    from gateway.routes.projects.core import MAX_DEPTH
    from gateway.routes.projects.watchers import _CHAIN_WATCHERS_SQL

    with db.connect() as c:
        rows = c.execute(
            text(_CHAIN_WATCHERS_SQL),
            {"pid": project_id, "max_depth": MAX_DEPTH},
        ).fetchall()
    return sorted(r.watcher for r in rows)


class TestTheChainWalksUP:
    """Direction is the design. Up is the ask; down is a different product."""

    def test_a_grandchild_inherits_every_ancestor(self, db, tree):
        with db.begin() as c:
            _watch(c, tree["root"], "root.watcher@example.test")
            _watch(c, tree["child"], "child.watcher@example.test")
            _watch(c, tree["grandchild"], "leaf.watcher@example.test")

        assert _chain(db, tree["grandchild"]) == [
            "child.watcher@example.test",
            "leaf.watcher@example.test",
            "root.watcher@example.test",
        ]

    def test_a_SIBLING_is_never_swept_in(self, db, tree):
        """⚠️ The assertion that fails if the walk is ever inverted.

        A downward walk from the root would return the sibling's watcher for
        the child, subscribing somebody to a project they never chose.
        """
        with db.begin() as c:
            _watch(c, tree["root"], "root.watcher@example.test")
            _watch(c, tree["sibling"], "sibling.watcher@example.test")

        found = _chain(db, tree["child"])
        assert "root.watcher@example.test" in found
        assert "sibling.watcher@example.test" not in found

    def test_a_parent_does_not_inherit_its_CHILD(self, db, tree):
        """Watching the root must not be implied by watching a leaf."""
        with db.begin() as c:
            _watch(c, tree["grandchild"], "leaf.watcher@example.test")
        assert _chain(db, tree["root"]) == []

    def test_a_root_with_no_watchers_is_empty_not_an_error(self, db, tree):
        assert _chain(db, tree["root"]) == []

    def test_one_watcher_on_two_ancestors_appears_ONCE(self, db, tree):
        """`DISTINCT` earns its place: the audience is a set, not a tally."""
        with db.begin() as c:
            _watch(c, tree["root"], "both@example.test")
            _watch(c, tree["child"], "both@example.test")
        assert _chain(db, tree["grandchild"]) == ["both@example.test"]


class TestTheTenantKeyIsFilledAndVerified:
    """R5 — migration 203 attaches 161's trigger, so no caller must remember."""

    def test_an_insert_that_names_no_tenant_still_gets_the_right_one(
        self, db, tree,
    ):
        with db.begin() as c:
            _watch(c, tree["child"], "tenant.probe@example.test")
            matched = c.execute(
                text(
                    "SELECT w.organization_id = p.organization_id "
                    "  FROM pm_project_watchers w "
                    "  JOIN pm_projects p ON p.id = w.project_id "
                    " WHERE w.watcher = 'tenant.probe@example.test'"
                )
            ).scalar_one()
        assert matched is True

    def test_the_column_is_NOT_NULL(self, db):
        with db.connect() as c:
            nullable = c.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    " WHERE table_name = 'pm_project_watchers' "
                    "   AND column_name = 'organization_id'"
                )
            ).scalar_one()
        assert nullable == "NO"


class TestAWatcherIsAFoldedHumanAddress:
    """The two CHECKs, and what each one prevents."""

    def test_an_agent_is_refused(self, db, tree):
        # An agent inbox is one nobody ever opens — agents are handed work by
        # the WS-27f dispatch sink.
        with pytest.raises(Exception) as caught, db.begin() as c:
            _watch(c, tree["root"], "agent:planner")
        assert "watcher_is_human" in str(caught.value)

    def test_a_MIXED_CASE_address_is_refused(self, db, tree):
        # Every read compares folded, so a mixed-case row would be a
        # subscription that never fires.
        with pytest.raises(Exception) as caught, db.begin() as c:
            _watch(c, tree["root"], "Mixed@Example.Test")
        assert "watcher_lowercased" in str(caught.value)

    def test_watching_twice_is_watching(self, db, tree):
        with db.begin() as c:
            _watch(c, tree["root"], "twice@example.test")
            _watch(c, tree["root"], "twice@example.test")
            count = c.execute(
                text(
                    "SELECT count(*) FROM pm_project_watchers "
                    " WHERE project_id = CAST(:p AS uuid) "
                    "   AND watcher = 'twice@example.test'"
                ),
                {"p": tree["root"]},
            ).scalar_one()
        assert count == 1

    def test_deleting_the_project_removes_the_subscription(self, db, org_id):
        """A watcher row on a deleted project is a subscription to nothing."""
        with db.begin() as c:
            pid = _project(c, org_id, f"doomed-{uuid.uuid4().hex[:6]}")
            _watch(c, pid, "doomed@example.test")
        with db.begin() as c:
            c.execute(
                text("DELETE FROM pm_projects WHERE id = CAST(:i AS uuid)"),
                {"i": pid},
            )
            left = c.execute(
                text(
                    "SELECT count(*) FROM pm_project_watchers "
                    " WHERE watcher = 'doomed@example.test'"
                )
            ).scalar_one()
        assert left == 0
