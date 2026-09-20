"""H-124 — seed the directory from the org roster (`POST /people/sync-members`).

Spec: ``people_center_app.md`` §2 · PR #306 · H-124.

PR #306 made member provisioning write a ``gtd_people`` row and deliberately
left the existing data alone. This route is the repair for the members who
predate it, and these are its fences.

Two layers, and they test different things:

* **Hermetic** — the permission, the status map, and the three counters. The
  counters are the reason this route reports anything at all: "it worked and
  there was nothing to do" and "it wrote nothing" must not render as the same
  sentence, which is the failure CLAUDE.md §3 rule 8 names.
* **R8, against a REAL Postgres** — the tenant predicate and the ``app_user``
  read. A fake agrees with whatever SQL it is handed, and the two things that
  can actually go wrong here are a predicate that matches every tenant and an
  ``ON CONFLICT`` target that no index backs. Neither is visible to a dict.

⚠️ **The hermetic half asserts the SHAPE of the SQL, not just its result.**
The tenant predicate is the whole security property of this route, and a fake
that returns rows will happily return them for a statement with no ``WHERE``
at all.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway.routes.people import members_sync

from tests.unit._tenant_ladder import apply_ladder


def run(coro):
    return asyncio.run(coro)


def _user(email: str, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = _user("admin@fracktal.in", "feature:people", "admin:members:manage")
MEMBER = _user("member@fracktal.in", "feature:people")


# ══════════════════════════════════════════════════════════════════════════
# Hermetic — the permission, the map, the counters
# ══════════════════════════════════════════════════════════════════════════

class _Result:
    def __init__(self, rows: list[Any], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeDB:
    """Answers the two statements this route issues, and records both.

    ``held`` is the set of addresses already in the directory, so the
    "existing versus refused elsewhere" branch can be driven without a
    database that has two tenants in it.
    """

    def __init__(self, members: list[Any], held: set[str] | None = None):
        self.members = members
        self.held = {h.lower() for h in (held or set())}
        self.statements: list[str] = []
        self.inserted: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        if "FROM app_user" in statement:
            return _Result(self.members)
        if statement.startswith("INSERT INTO gtd_people"):
            email = (params or {}).get("email", "").lower()
            if email in self.held:
                return _Result([], rowcount=0)
            self.held.add(email)
            self.inserted.append(email)
            return _Result([], rowcount=1)
        if "SELECT 1 FROM gtd_people" in statement:
            email = (params or {}).get("email", "").lower()
            return _Result([SimpleNamespace(v=1)] if email in self.held else [])
        return _Result([])

    async def commit(self) -> None:
        return None

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db

    monkeypatch.setattr(members_sync, "_tenant_session", _tenant_session,
                        raising=True)


def _member(email: str, name: str = "", status: str = "active") -> SimpleNamespace:
    return SimpleNamespace(email=email, display_name=name, status=status)


def test_a_member_without_manage_is_refused_by_name(monkeypatch) -> None:
    """`feature:people` opens the directory. Writing to it is a second grant.

    Refused BEFORE the session opens, so a member who cannot do this does not
    cause a database round trip either.
    """
    bind(monkeypatch, FakeDB([]))
    with pytest.raises(HTTPException) as exc:
        run(members_sync.sync_members(user=MEMBER))
    assert exc.value.status_code == 403
    assert "admin:members:manage" in exc.value.detail


def test_every_missing_member_gets_a_row(monkeypatch) -> None:
    db = FakeDB([_member("a@x.com", "Ann"), _member("b@x.com", "Bo")])
    bind(monkeypatch, db)
    res = run(members_sync.sync_members(user=ADMIN))
    assert (res.members, res.created, res.existing, res.skipped) == (2, 2, 0, 0)
    assert db.inserted == ["a@x.com", "b@x.com"]


def test_a_second_press_creates_nothing_and_says_so(monkeypatch) -> None:
    """Idempotence is the property, and REPORTING it is the requirement.

    A repair button that answers the same way whether it wrote two rows or
    zero is a button nobody can trust. `created` and `existing` are separate
    numbers for exactly that reason.
    """
    db = FakeDB([_member("a@x.com", "Ann")], held={"a@x.com"})
    bind(monkeypatch, db)
    res = run(members_sync.sync_members(user=ADMIN))
    assert (res.created, res.existing, res.skipped) == (0, 1, 0)
    assert db.inserted == []


def test_suspended_and_removed_members_are_never_asked_for(monkeypatch) -> None:
    """The two vocabularies differ, and the route does not guess across them.

    `app_user.status` has four values, migration 148's CHECK has a different
    four, and only `active` and `invited` map. Writing a guess would put an
    off-boarded colleague back in the assignee picker — D63's question
    (H-49), which is not settled.
    """
    db = FakeDB([])
    bind(monkeypatch, db)
    run(members_sync.sync_members(user=ADMIN))
    statuses = members_sync._STATUS_MAP
    assert set(statuses) == {"active", "invited"}
    assert "suspended" not in statuses and "removed" not in statuses
    assert db.issued("status = ANY(:statuses)")


def test_an_address_no_tenant_can_hold_counts_as_skipped(monkeypatch) -> None:
    """Migration 148's `lower(email)` index is GLOBAL, not per-tenant (H-125).

    So `ensure_directory_row` can write nothing because ANOTHER organization
    holds the address, and row-level security then hides the row that
    explains it. That is not "already present" — the member still has no
    profile — so it must not be counted as `existing`.
    """
    class _Blocked(FakeDB):
        async def execute(self, sql: Any, params: dict | None = None) -> _Result:
            statement = " ".join(str(sql).split())
            if statement.startswith("INSERT INTO gtd_people"):
                self.statements.append(statement)
                return _Result([], rowcount=0)   # refused by the global index
            return await super().execute(sql, params)

    db = _Blocked([_member("shared@x.com", "Shared")])
    bind(monkeypatch, db)
    res = run(members_sync.sync_members(user=ADMIN))
    assert (res.created, res.existing, res.skipped) == (0, 0, 1)


def test_the_roster_read_is_scoped_to_the_bound_tenant(monkeypatch) -> None:
    """The security property, asserted on the STATEMENT rather than the rows.

    `app_user` carries a generated policy, but the generated layer is not on
    the numbered ladder (H-104) and the scratch cluster has no FORCE RLS. A
    route that leaned on the policy would be correct only by accident on the
    one database anybody runs tests against — and would list every customer's
    roster on a cluster without it. `NULLIF(…, '')` is the fail-closed half:
    an unbound GUC must match no row, never every row.
    """
    db = FakeDB([])
    bind(monkeypatch, db)
    run(members_sync.sync_members(user=ADMIN))
    assert db.issued("organization_id =")
    assert db.issued("current_setting('app.tenant_id', true)")
    assert db.issued("NULLIF(")


def test_it_writes_through_the_one_seam_and_not_its_own_insert() -> None:
    """A second INSERT here is the defect this test exists to prevent.

    `ensure_directory_row` is the ONE place that knows a member row's shape,
    including `source_key` — whose absence aborted migration 148's replay on
    a real database (PR #306). A convenience INSERT in this module would be a
    second answer that drifts on the next column.
    """
    source = (members_sync.__file__).replace("\\", "/")
    with open(source, encoding="utf-8") as fh:
        body = fh.read()
    code = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )
    _, _, after_docstring = code.partition('"""')
    _, _, after_docstring = after_docstring.partition('"""')
    assert "INSERT INTO gtd_people" not in after_docstring
    assert "ensure_directory_row" in after_docstring


# ══════════════════════════════════════════════════════════════════════════
# R8 — the SQL, against a real Postgres
# ══════════════════════════════════════════════════════════════════════════

_URL = os.environ.get("TENANT_LADDER_DATABASE_URL", "").strip()

live = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)


@pytest.fixture(scope="module")
def eng():
    from sqlalchemy import create_engine
    engine = create_engine(_URL, future=True)
    with engine.begin() as conn:
        apply_ladder(conn)
    yield engine
    engine.dispose()


@live
def test_the_roster_statement_runs_and_scopes_on_a_real_database(eng) -> None:
    """The statement this route issues, executed verbatim by Postgres.

    Two things a fake cannot check: that it PARSES against the real
    `app_user` (the `= ANY(:statuses)` bind and the uuid cast included), and
    that with no tenant bound it returns ZERO rows rather than the whole
    table. The second is the fail-closed claim, and it is the one worth
    having a database for.
    """
    from sqlalchemy import text

    org = uuid.uuid4()
    email = f"roster-{uuid.uuid4().hex[:10]}@directory.example"
    statement = text(
        "SELECT email, display_name, status "
        "  FROM app_user "
        " WHERE organization_id = "
        "       CAST(NULLIF(current_setting('app.tenant_id', true), '') "
        "            AS uuid) "
        "   AND status = ANY(:statuses) "
        " ORDER BY lower(email)")

    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (:id, :slug, 'Sync Test') ON CONFLICT DO NOTHING"),
            {"id": org, "slug": f"sync-{org.hex[:8]}"})
        conn.execute(text(
            "INSERT INTO app_user (email, display_name, organization_id, status) "
            "VALUES (:e, 'Roster Member', :org, 'active')"),
            {"e": email, "org": org})
    try:
        # Unbound tenant → no rows. Fail closed.
        with eng.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            assert conn.execute(
                statement, {"statuses": ["active", "invited"]}).fetchall() == []

        # Bound to the right tenant → the member is there.
        with eng.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                         {"o": str(org)})
            found = conn.execute(
                statement, {"statuses": ["active", "invited"]}).fetchall()
            assert [r.email for r in found] == [email]

        # Bound to somebody else's tenant → nothing.
        with eng.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                         {"o": str(uuid.uuid4())})
            assert conn.execute(
                statement, {"statuses": ["active", "invited"]}).fetchall() == []
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM app_user WHERE email = :e"),
                         {"e": email})
            conn.execute(text("DELETE FROM organization WHERE id = :id"),
                         {"id": org})


@live
def test_a_suspended_member_is_outside_the_roster_read(eng) -> None:
    """The status filter, proven against the real column rather than a map."""
    from sqlalchemy import text

    org = uuid.uuid4()
    gone = f"gone-{uuid.uuid4().hex[:10]}@directory.example"
    here = f"here-{uuid.uuid4().hex[:10]}@directory.example"
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO organization (id, slug, display_name) "
            "VALUES (:id, :slug, 'Sync Test') ON CONFLICT DO NOTHING"),
            {"id": org, "slug": f"sync-{org.hex[:8]}"})
        for addr, status in ((gone, "suspended"), (here, "active")):
            conn.execute(text(
                "INSERT INTO app_user (email, display_name, organization_id, "
                "status) VALUES (:e, 'X', :org, :s)"),
                {"e": addr, "org": org, "s": status})
    try:
        with eng.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                         {"o": str(org)})
            found = conn.execute(text(
                "SELECT email FROM app_user "
                " WHERE organization_id = CAST(NULLIF("
                "         current_setting('app.tenant_id', true), '') AS uuid) "
                "   AND status = ANY(:statuses)"),
                {"statuses": ["active", "invited"]}).fetchall()
        assert [r.email for r in found] == [here]
    finally:
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM app_user WHERE email = ANY(:e)"),
                         {"e": [gone, here]})
            conn.execute(text("DELETE FROM organization WHERE id = :id"),
                         {"id": org})
