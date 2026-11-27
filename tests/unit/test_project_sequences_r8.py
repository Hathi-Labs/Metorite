"""SW-27a (task 861): project sequences on a real database.

The done-when of `agent_coordination.md` "SW-27":

* R8: 20 parallel calls get 20 different numbers, as the NOBYPASSRLS role.
* A second org never sees or moves the first org's sequence.

The calls go through the real ``/projects`` router, as ``acb_app`` (NOBYPASSRLS)
on the promoted scratch database of ``test_h3_rls_promotion_rehearsal``. The
seeds are written as the RLS-bypassing admin. The class carries ``_DB_GATE``,
so it skips where no ladder URL is set. CI sets one, so CI runs it.

The migration is found by CONTENT, never by number (R1).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_ROUTE = _REPO / "apps/services/gateway/gateway/routes/projects/sequences.py"


def _migration() -> Path:
    found = [
        p
        for p in sorted((_REPO / "infra" / "postgres").glob("*.sql"))
        if "CREATE TABLE IF NOT EXISTS pm_project_sequences" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, found
    return found[0]


# ── Static: the migration and the one statement ───────────────────────────────


@pytest.mark.parametrize("table", ["pm_project_sequences", "pm_sequence_reservations"])
def test_each_table_is_tenant_scoped_with_force_rls(table: str) -> None:
    sql = _migration().read_text(encoding="utf-8")
    assert f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY" in sql
    assert f"CREATE POLICY {table}_tenant_isolation ON {table}" in sql
    block = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {table}") :]
    block = block[: block.index(");")]
    assert re.search(
        r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\) ON DELETE CASCADE"
        r"\s+DEFAULT current_setting\('app.tenant_id', true\)::uuid",
        block,
    )


def test_the_migration_is_expand_only_and_holds_no_member_email() -> None:
    # The statements only: the header comments name what the file does NOT do.
    sql = "\n".join(
        line
        for line in _migration().read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).upper()
    for word in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER COLUMN", "DELETE FROM", "UPDATE "):
        assert word not in sql.replace("ON UPDATE", "").replace("OR UPDATE", ""), word
    assert "MEMBER_EMAIL" not in sql
    assert "PM_SEQUENCE_RESERVATIONS_VALUE" in sql  # one row for each number


def test_no_code_lowers_a_sequence() -> None:
    """A released number is burned (the owner's answer on task 861): no code
    writes a lower `last_value`."""
    code = _ROUTE.read_text(encoding="utf-8")
    assert "last_value - " not in code and "last_value-" not in code
    from gateway.routes.projects import sequences

    assert sequences.RESERVE_SQL.count("INSERT INTO") == 2  # one statement, two inserts
    assert "last_value + 1" in sequences.RESERVE_SQL


# ── R8 ───────────────────────────────────────────────────────────────────────


def _principal(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email,
        role=UserRole.EMPLOYEE,
        access=build_access(["feature:projects", "data:org:read"]),
        organization_id=org,
    )


def _app(users: dict[str, Any]) -> Any:
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import projects as projects_package

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_package.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)),
                base_url="http://projects.test",
            ) as client,
        ):

            async def call(user: Any, method: str, path: str, body: dict | None = None):
                users[str(id(user))] = user
                return await client.request(
                    method, path, json=body, headers={"x-test-as": str(id(user))}
                )

            yield call
    finally:
        release_tenant(scope)


def _seed(p: Any, org: str, email: str, *, parent: str | None = None) -> SimpleNamespace:
    """A member, a project (under *parent* when given), a lane and a task."""
    project, status, task = (str(uuid.uuid4()) for _ in range(3))
    with p.admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"
            ),
            {"e": email, "o": org},
        )
        c.execute(
            text(
                "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses,"
                " parent_project_id) VALUES (:id, :o, 'seq', :e, true, CAST(:parent AS uuid))"
            ),
            {"id": project, "o": org, "e": email, "parent": parent},
        )
        c.execute(
            text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by)"
                " VALUES (:o, :p, :s, :e)"
            ),
            {"o": org, "p": project, "s": email, "e": email},
        )
        c.execute(
            text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category,"
                " position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"
            ),
            {"id": status, "o": org, "p": project},
        )
        c.execute(
            text(
                "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id,"
                " task_number, status_id, title, created_by)"
                " VALUES (:id, :o, :p, COALESCE(CAST(:root AS uuid), :p), :n, :s, 'Add a migration', :e)"
            ),
            {
                "id": task,
                "o": org,
                "p": project,
                "s": status,
                "e": email,
                "root": parent,
                # A subproject's task shares the root's number space.
                "n": 1 if parent is None else 2,
            },
        )
    return SimpleNamespace(project=project, task=task)


def _seed_run(p: Any, org: str, task: str, email: str) -> str:
    with p.admin_engine.begin() as c:
        return str(
            c.execute(
                text(
                    "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
                    " status, started_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, 'mcp:test',"
                    " 'in_progress', :m)"
                    " RETURNING id"
                ),
                {"o": org, "t": task, "m": email},
            ).scalar()
        )


def _reserve(project: str, name: str = "migration") -> str:
    return f"/projects/nodes/{project}/sequences/{name}/reserve"


@pytest.fixture
def world(promoted):  # noqa: F811 — a fixture
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_project_sequences"))
    a_email, b_email = (
        f"seq-a-{uuid.uuid4().hex[:6]}@r8.test",
        f"seq-b-{uuid.uuid4().hex[:6]}@r8.test",
    )
    ns = SimpleNamespace(p=promoted)
    ns.a, ns.b = _principal(a_email, promoted.org_a), _principal(b_email, promoted.org_b)
    ns.ta = _seed(promoted, promoted.org_a, a_email)
    ns.tb = _seed(promoted, promoted.org_b, b_email)
    ns.a_email = a_email
    return ns


@_DB_GATE
class TestProjectSequencesOnARealDatabase:
    async def test_twenty_parallel_calls_get_twenty_different_numbers(self, world):
        """The done-when, as `acb_app` (NOBYPASSRLS). Mutation: read
        `last_value` in one statement and write it in another, and two calls
        get one number."""
        w = world
        async with _session(w.p) as call:
            got = await asyncio.gather(
                *(
                    call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
                    for _ in range(20)
                )
            )
        assert all(r.status_code == 201 for r in got), [r.text for r in got if r.status_code != 201]
        values = sorted(r.json()["value"] for r in got)
        assert values == list(range(1, 21))
        with w.p.admin_engine.connect() as c:
            assert (
                c.execute(
                    text(
                        "SELECT last_value FROM pm_project_sequences WHERE project_id = CAST(:p AS uuid)"
                        " AND name = 'migration'"
                    ),
                    {"p": w.ta.project},
                ).scalar()
                == 20
            )
            assert (
                c.execute(
                    text(
                        "SELECT count(DISTINCT value) FROM pm_sequence_reservations r"
                        " JOIN pm_project_sequences s ON s.id = r.sequence_id"
                        " WHERE s.project_id = CAST(:p AS uuid)"
                    ),
                    {"p": w.ta.project},
                ).scalar()
                == 20
            )

    async def test_a_second_org_never_sees_or_moves_the_first_orgs_sequence(self, world):
        w = world
        async with _session(w.p) as call:
            first = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
            assert first.json()["value"] == 1
            # Org B cannot reserve on org A's project, whatever it names.
            for body in ({"task_id": w.ta.task}, {"task_id": w.tb.task}):
                other = await call(w.b, "POST", _reserve(w.ta.project), body)
                assert other.status_code in (404, 422), other.text
            # Org B's own sequence of the same name is its own, and starts at 1.
            own = await call(w.b, "POST", _reserve(w.tb.project), {"task_id": w.tb.task})
            assert own.status_code == 201 and own.json()["value"] == 1
            again = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
            assert again.json()["value"] == 2
        # Under RLS, a session bound to org B reads none of org A's rows.
        from sqlalchemy import create_engine

        eng = create_engine(w.p.app_url, future=True)
        try:
            with eng.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.p.org_b})
                seen = {
                    str(r.project_id)
                    for r in c.execute(text("SELECT project_id FROM pm_project_sequences"))
                }
                assert w.ta.project not in seen and w.tb.project in seen
                assert (
                    c.execute(
                        text(
                            "UPDATE pm_project_sequences SET last_value = 0"
                            " WHERE project_id = CAST(:p AS uuid)"
                        ),
                        {"p": w.ta.project},
                    ).rowcount
                    == 0
                )
        finally:
            eng.dispose()

    async def test_a_subproject_task_reserves_on_the_parent_sequence(self, world):
        w = world
        child = _seed(w.p, w.p.org_a, w.a_email, parent=w.ta.project)
        async with _session(w.p) as call:
            got = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": child.task})
        assert got.status_code == 201, got.text
        assert got.json()["value"] == 1

    async def test_a_person_names_no_run(self, world):
        """SW-27c1, the owner's decision 1: a reservation names only the
        caller's own run, and a person owns no agent run. A run of another
        task stays 422 too. The agent's own run is in
        ``test_mcp_edge_reserve_r8.py``."""
        w = world
        run = _seed_run(w.p, w.p.org_a, w.ta.task, w.a_email)
        stranger = _seed(w.p, w.p.org_a, w.a_email)
        other_run = _seed_run(w.p, w.p.org_a, stranger.task, w.a_email)
        async with _session(w.p) as call:
            mine = await call(
                w.a, "POST", _reserve(w.ta.project, "personrun"),
                {"task_id": w.ta.task, "run_id": run},
            )
            bad = await call(
                w.a, "POST", _reserve(w.ta.project, "personrun"),
                {"task_id": w.ta.task, "run_id": other_run},
            )
        assert mine.status_code == 422, mine.text
        assert mine.json()["detail"] == "The run is not your own run on this task."
        assert bad.status_code == 422
        assert bad.json()["detail"] == "The run is not a run of this task."

    @pytest.mark.parametrize(
        "name", ["Migration", "1st", "a b", "x" * 41, "a;drop", "migration%0A"]
    )
    async def test_a_bad_name_is_refused(self, world, name):
        w = world
        async with _session(w.p) as call:
            got = await call(w.a, "POST", _reserve(w.ta.project, name), {"task_id": w.ta.task})
        assert got.status_code == 422, got.text

    async def test_a_task_of_another_project_is_refused(self, world):
        w = world
        stranger = _seed(w.p, w.p.org_a, w.a_email)
        async with _session(w.p) as call:
            got = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": stranger.task})
        assert got.status_code == 422
        assert got.json()["detail"] == "The task is not in this project."

    async def test_a_number_is_never_handed_out_twice_even_after_a_release(self, world):
        """Burned numbers: a released reservation keeps its number."""
        w = world
        async with _session(w.p) as call:
            first = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
        with w.p.admin_engine.begin() as c:
            c.execute(
                text(
                    "UPDATE pm_sequence_reservations SET state = 'released', released_at = now()"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": first.json()["reservation_id"]},
            )
        async with _session(w.p) as call:
            second = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
        assert second.json()["value"] == first.json()["value"] + 1


# ── The same-org guard on every key (review P1) ─────────────────────────────


def _as_app(w: Any, org: str):
    """A connection as `acb_app` (NOBYPASSRLS), bound to *org*."""
    from sqlalchemy import create_engine

    eng = create_engine(w.p.app_url, future=True)
    return eng


def _app_write(w: Any, org: str, sql: str, **params: Any) -> None:
    eng = _as_app(w, org)
    try:
        with eng.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            c.execute(text(sql), params)
    finally:
        eng.dispose()


@pytest.mark.parametrize(
    "trigger",
    [
        "trg_pm_project_sequences_org_from_project",
        "trg_pm_sequence_reservations_org_from_sequence",
        "trg_pm_sequence_reservations_org_from_task",
        "trg_pm_sequence_reservations_org_from_run",
    ],
)
def test_the_migration_guards_each_key_with_the_shared_function(trigger: str) -> None:
    sql = _migration().read_text(encoding="utf-8")
    block = sql[sql.index(f"CREATE OR REPLACE TRIGGER {trigger}") :]
    assert "pm_organization_from_parent(" in block.split(";")[0]


@_DB_GATE
class TestTheSameOrgGuard:
    def test_a_reservation_cannot_name_a_task_of_another_org(self, world):
        """As acb_app bound to org A, a row that names org B's task fails with
        the foreign key SQLSTATE. Mutation: drop the task trigger, and the
        row goes in (the foreign key skips RLS)."""
        from sqlalchemy.exc import DBAPIError

        w = world
        with w.p.admin_engine.begin() as c:
            seq = str(
                c.execute(
                    text(
                        "INSERT INTO pm_project_sequences (organization_id, project_id, name)"
                        " VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'probe') RETURNING id"
                    ),
                    {"o": w.p.org_a, "p": w.ta.project},
                ).scalar()
            )
        with pytest.raises(DBAPIError) as exc:
            _app_write(
                w,
                w.p.org_a,
                "INSERT INTO pm_sequence_reservations (sequence_id, value, task_id)"
                " VALUES (CAST(:s AS uuid), 1, CAST(:t AS uuid))",
                s=seq,
                t=w.tb.task,
            )
        assert getattr(exc.value.orig, "sqlstate", None) == "23503"

    def test_a_sequence_cannot_name_a_project_of_another_org(self, world):
        from sqlalchemy.exc import DBAPIError

        w = world
        with pytest.raises(DBAPIError) as exc:
            _app_write(
                w,
                w.p.org_a,
                "INSERT INTO pm_project_sequences (project_id, name)"
                " VALUES (CAST(:p AS uuid), 'probe')",
                p=w.tb.project,
            )
        assert getattr(exc.value.orig, "sqlstate", None) == "23503"

    async def test_a_task_delete_keeps_the_burned_number(self, world):
        """Review P2: a task delete sets task_id to NULL and keeps the row."""
        w = world
        doomed = _seed(w.p, w.p.org_a, w.a_email)
        async with _session(w.p) as call:
            got = await call(w.a, "POST", _reserve(doomed.project), {"task_id": doomed.task})
        rid = got.json()["reservation_id"]
        with w.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": doomed.task})
            row = c.execute(
                text(
                    "SELECT task_id, value FROM pm_sequence_reservations WHERE id = CAST(:i AS uuid)"
                ),
                {"i": rid},
            ).one()
        assert row.task_id is None and row.value == 1

    async def test_an_archived_project_refuses_a_reservation(self, world):
        w = world
        with w.p.admin_engine.begin() as c:
            c.execute(
                text("UPDATE pm_projects SET archived_at = now() WHERE id = CAST(:p AS uuid)"),
                {"p": w.ta.project},
            )
        try:
            async with _session(w.p) as call:
                got = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
            assert got.status_code == 422 and "archived" in got.json()["detail"], got.text
        finally:
            with w.p.admin_engine.begin() as c:
                c.execute(
                    text("UPDATE pm_projects SET archived_at = NULL WHERE id = CAST(:p AS uuid)"),
                    {"p": w.ta.project},
                )


@_DB_GATE
class TestTheRunKey:
    def test_a_reservation_cannot_name_a_run_of_another_org(self, world):
        """Verifier 2, P3: a database probe, not only the SQL text."""
        from sqlalchemy.exc import DBAPIError

        w = world
        with w.p.admin_engine.begin() as c:
            seq = str(
                c.execute(
                    text(
                        "INSERT INTO pm_project_sequences (organization_id, project_id, name)"
                        " VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'runprobe') RETURNING id"
                    ),
                    {"o": w.p.org_a, "p": w.ta.project},
                ).scalar()
            )
        run_b = _seed_run(w.p, w.p.org_b, w.tb.task, "seq-runprobe@r8.test")
        with pytest.raises(DBAPIError) as exc:
            _app_write(
                w,
                w.p.org_a,
                "INSERT INTO pm_sequence_reservations (sequence_id, value, task_id, run_id)"
                " VALUES (CAST(:s AS uuid), 1, CAST(:t AS uuid), CAST(:r AS uuid))",
                s=seq,
                t=w.ta.task,
                r=run_b,
            )
        assert getattr(exc.value.orig, "sqlstate", None) == "23503"

    async def test_a_task_delete_with_a_run_keeps_the_burned_number(self, world):
        w = world
        doomed = _seed(w.p, w.p.org_a, w.a_email)
        run = _seed_run(w.p, w.p.org_a, doomed.task, "seq-run2@r8.test")
        # A person names no run (SW-27c1), so the row with a run is seeded.
        with w.p.admin_engine.begin() as c:
            seq = c.execute(text(
                "INSERT INTO pm_project_sequences (organization_id, project_id, name, last_value)"
                " VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'doomed', 1) RETURNING id"),
                {"o": w.p.org_a, "p": doomed.project}).scalar_one()
            rid = str(c.execute(text(
                "INSERT INTO pm_sequence_reservations (organization_id, sequence_id, value,"
                " task_id, run_id) VALUES (CAST(:o AS uuid), :s, 1, CAST(:t AS uuid),"
                " CAST(:r AS uuid)) RETURNING id"),
                {"o": w.p.org_a, "s": seq, "t": doomed.task, "r": run}).scalar_one())
        with w.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": doomed.task})
            row = c.execute(
                text(
                    "SELECT task_id, run_id, value FROM pm_sequence_reservations"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"i": rid},
            ).one()
        assert row.task_id is None and row.run_id is None and row.value == 1


@_DB_GATE
class TestTheRaceAnswer:
    """Verifier 3, P3 (R7): the 404 for a task or run that went mid-call.

    A real race is hard to time, so the test swaps RESERVE_SQL for a statement
    that hits the same refusal: the same-org guard's foreign-key SQLSTATE."""

    async def test_a_foreign_key_error_answers_404(self, world, monkeypatch):
        from gateway.routes.projects import sequences

        monkeypatch.setattr(
            sequences,
            "RESERVE_SQL",
            (
                "INSERT INTO pm_sequence_reservations (sequence_id, value, task_id)"
                " VALUES (gen_random_uuid(), 1, CAST(:task AS uuid))"
                " RETURNING id, value, reserved_at"
            ),
        )
        w = world
        async with _session(w.p) as call:
            got = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
        assert got.status_code == 404, got.text
        assert "gone" in got.json()["detail"]

    async def test_another_integrity_error_is_not_hidden(self, world, monkeypatch):
        """Only 23503 maps to 404. A CHECK error (23514) is a defect, so it
        must not read as a clean 404."""
        from gateway.routes.projects import sequences

        monkeypatch.setattr(
            sequences,
            "RESERVE_SQL",
            (
                "INSERT INTO pm_project_sequences (project_id, name)"
                " VALUES (CAST(:pid AS uuid), 'NOT A NAME')"
                " RETURNING id, last_value AS value, created_at AS reserved_at"
            ),
        )
        w = world
        try:
            async with _session(w.p) as call:
                got = await call(w.a, "POST", _reserve(w.ta.project), {"task_id": w.ta.task})
        except Exception as exc:
            assert (
                "23514" in repr(exc)
                or "CheckViolation" in repr(exc)
                or "check" in repr(exc).lower()
            )
            return
        assert got.status_code == 500, got.text
