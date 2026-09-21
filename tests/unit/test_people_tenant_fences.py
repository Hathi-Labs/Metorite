"""The People helpers that read SHARED tables, fenced to one tenant.

Found by an end-to-end review on 2026-09-21. Three reads in this package
joined a table that every organization shares — `pm_tasks` and `app_user` —
on an EMAIL and nothing else. An assignee is a string, and the same address
can be a member of two organizations: a contractor working for two customers
is the ordinary case this product is sold for.

* ``/people/{id}/work`` skipped its clause for a `data:org:read` caller, which
  the `manager` role holds. Fixed in `directory.py`, pinned hermetically in
  `test_people_directory.py`.
* ``compute_load`` had no tenant predicate at all — and it feeds the
  directory, capability search and the Projects **assignee picker**, so
  another customer's workload decided whether this customer's colleague
  looked overloaded.
* ``has_login`` asked "is this address a member ANYWHERE", which is both the
  wrong question and a disclosure across customers.

**Two organizations, on a real Postgres, or this file proves nothing.** A
single-tenant fixture passes with the fence removed — that is exactly how
these survived. Every test here seeds a second organization holding the SAME
address and asserts the first one cannot see it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import apply_ladder

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

#: One address, two employers. The whole point.
SHARED = "contractor@twocustomers.example"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def eng():
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


def _org(conn, tag: str) -> uuid.UUID:
    oid = uuid.uuid4()
    conn.execute(text(
        "INSERT INTO organization (id, slug, display_name) "
        "VALUES (:id, :s, :n)"),
        {"id": oid, "s": f"{tag}-{oid.hex[:8]}", "n": tag})
    return oid


def _task_for(conn, org: uuid.UUID, assignee: str, *, minutes: int) -> None:
    """One open task in `org`, assigned to `assignee`.

    Built bottom-up rather than through the API because the API is what is
    under test: the fixture must be able to create the cross-tenant state a
    correct API would refuse to show.
    """
    project = uuid.uuid4()
    status = uuid.uuid4()
    task = uuid.uuid4()
    conn.execute(text(
        # `owns_statuses` is required on a ROOT project — the CHECK is
        # `parent_project_id IS NOT NULL OR owns_statuses`, and this is a root.
        "INSERT INTO pm_projects (id, name, created_by, owns_statuses, "
        "                         organization_id) "
        "VALUES (:id, 'Work', 'fixture', true, :o)"), {"id": project, "o": org})
    conn.execute(text(
        "INSERT INTO pm_task_statuses (id, project_id, name, category, "
        "                              position, organization_id) "
        "VALUES (:id, :p, 'Doing', 'in_progress', 1, :o)"),
        {"id": status, "p": project, "o": org})
    conn.execute(text(
        "INSERT INTO pm_tasks (id, project_id, root_project_id, status_id, "
        "                      title, task_number, created_by, "
        "                      estimate_mins, organization_id) "
        "VALUES (:id, :p, :p, :s, 'Ship it', 1, 'fixture', :m, :o)"),
        {"id": task, "p": project, "s": status, "m": minutes, "o": org})
    conn.execute(text(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, "
        "                               organization_id) "
        "VALUES (:t, :a, 'fixture', :o)"),
        {"t": task, "a": assignee, "o": org})


class _AsAsync:
    """A SYNC connection wearing the one async method these helpers call.

    ⚠️ **Not a fake.** Every statement runs on the real Postgres connection
    handed in — this only bridges `await db.execute(...)` onto
    `conn.execute(...)`, which is the entire surface `compute_load` and
    `has_login` touch.

    Why not an async engine: psycopg refuses Windows' default
    `ProactorEventLoop`, and this repo's primary dev box is Windows
    (CLAUDE.md §6). Chasing a loop factory would make an R8 suite that only
    runs for some of us, which defeats the point of R8.
    """

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, sql, params=None):
        return self._conn.execute(sql, params or {})


@contextmanager
def _bound(engine, org):
    """A connection with `app.tenant_id` bound, as a request would have it."""
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :o, false)"),
                     {"o": str(org) if org else ""})
        yield _AsAsync(conn)


@pytest.fixture
def two_orgs(eng):
    """Org A and org B, both holding a task for the SAME address."""
    with eng.begin() as conn:
        a = _org(conn, "alpha")
        b = _org(conn, "beta")
        _task_for(conn, a, SHARED, minutes=60)
        _task_for(conn, b, SHARED, minutes=600)
        conn.execute(text(
            "INSERT INTO app_user (email, display_name, organization_id, status) "
            "VALUES (:e, 'Shared', :o, 'active')"), {"e": SHARED, "o": b})
    yield a, b
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM app_user WHERE email = :e"), {"e": SHARED})
        for org in (a, b):
            conn.execute(text("DELETE FROM organization WHERE id = :id"),
                         {"id": org})


def test_compute_load_counts_only_the_bound_tenants_tasks(eng, two_orgs) -> None:
    """One hour here, ten hours there. The picker must read one.

    Without the fence this returns 2 tasks and 11 hours, and the assignment
    suggester calls a free colleague overloaded on the strength of another
    customer's backlog.
    """
    from gateway.routes.people.core import compute_load

    a, _b = two_orgs

    with _bound(eng, a) as db:
        load = run(compute_load(db, SHARED))
    assert load["open_tasks"] == 1, load
    assert load["estimated_hours"] == 1.0, load


def test_compute_load_returns_NOTHING_when_no_tenant_is_bound(eng, two_orgs) -> None:
    """Fail closed. An unbound GUC must match no row, never every row."""
    from gateway.routes.people.core import compute_load

    with _bound(eng, None) as db:
        assert run(compute_load(db, SHARED))["open_tasks"] == 0


def test_has_login_is_false_for_a_member_of_ANOTHER_organization(eng, two_orgs) -> None:
    """The badge answers "can they sign in HERE", not "does this address exist".

    `app_user.email` is globally unique, so the unscoped version told one
    customer that an address belongs to another — and drew a login badge on a
    contractor who cannot sign in to this tenant at all.
    """
    from gateway.routes.people.core import has_login

    a, b = two_orgs

    with _bound(eng, b) as db:
        assert run(has_login(db, SHARED)) is True, "the org they ARE a member of"
    with _bound(eng, a) as db:
        assert run(has_login(db, SHARED)) is False, "the org they are NOT in"
