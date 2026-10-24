"""WS-64 OP-9 — a task in a member's personal space is shown to that member only.

Decision D-OP-15 (``one_projects_app.md`` §9 OP-9, owner 2026-10-24). A member
who may read every project (``data:org:read``) does not read another member's
personal task, in the app, the chat, search, reports or an outside agent. A
Projects admin (``projects:settings:write``) does not read it either. The
owner's own reads do not change.

The rule lives at ONE seam: ``core._TENANT_PROJECTS_SQL``, the project set of
the unrestricted arm. ``Visibility.project_clause`` and
``task_visibility_clause`` both read it, so every read built on them takes it.
Each read below is one that the unrestricted arm reaches.

**R8.** Each test runs the REAL routes as a NON-privileged role on a
phase-4-promoted catalog of its own, so FORCE RLS binds every read. One
organization holds:

* OWNER — a plain member, with a personal tree: a root, an Area, and a TEAM
  node under the root. Migration 297's stop rule leaves the team node
  unstamped, and it carries an ``org`` grant, so it is team work;
* READER — ``data:org:read``, with a personal tree of its own, so the
  unrestricted arm's "my own" half is tested too;
* PADMIN — ``projects:settings:write`` and no ``data:org:read``;
* a team project with an ``org`` grant.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_personal_privacy_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import AgentScope, require_authenticated
from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder, tenant_engine_scope

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector. A skip here is not a pass; CI must set it."
    ),
)
# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_APP_ROLE = "acb_app_op9priv"
_APP_PW = "op9priv_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

INTERNAL = "op9-privacy-internal-token"
DOMAIN = "op9privacy.test"
OWNER = f"owner@{DOMAIN}"
READER = f"reader@{DOMAIN}"
PADMIN = f"padmin@{DOMAIN}"
#: A word in every task title, so one search reaches every task.
WORD = "wombat"

#: Each task's title carries its key, so a body check can name what leaked.
OWNER_PERSONAL = ("owner_private", "owner_mine", "owner_area")
TEAM = ("team", "team_under")


def _exec_sql_file(conn, sql: str) -> None:
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


class _Seeder:
    """Admin writes, each with the tenant GUC set."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org
        self.number = 0

    def one(self, sql: str, **params) -> Any:
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": self.org})
            row = c.execute(text(sql), params).first()
        return row[0] if row is not None else None

    def project(self, name: str, *, parent: str | None = None,
                personal_owner: str | None = None,
                grant_to: str | None = None) -> str:
        pid = str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by,"
            " organization_id, timezone, parent_project_id, owns_statuses,"
            " personal_owner) VALUES (:n, 'active', 'manual', :a,"
            " CAST(:o AS uuid), 'Asia/Kolkata', CAST(:parent AS uuid), true,"
            " :po) RETURNING id",
            n=name, a=personal_owner or OWNER, o=self.org, parent=parent,
            po=personal_owner))
        if grant_to:
            self.one(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, :a,"
                " CAST(:o AS uuid)) RETURNING project_id",
                p=pid, s=grant_to, a=OWNER, o=self.org)
        return pid

    def task(self, project: str, root: str, key: str, *, by: str,
             assignee: str | None = None) -> tuple[str, int]:
        status = self.one(
            "SELECT id FROM pm_task_statuses WHERE project_id = CAST(:p AS uuid)"
            " LIMIT 1", p=root)
        if status is None:
            status = self.one(
                "INSERT INTO pm_task_statuses (project_id, name, color, position,"
                " category, organization_id) VALUES (CAST(:p AS uuid), 'To do',"
                " 'gray', 0, 'todo', CAST(:o AS uuid)) RETURNING id",
                p=root, o=self.org)
        self.number += 1
        tid = str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number) VALUES (:t,"
            " CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :a,"
            " CAST(:o AS uuid), :n) RETURNING id",
            t=f"{WORD} {key}", p=project, r=root, s=str(status), a=by,
            o=self.org, n=self.number))
        if assignee:
            self.one(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
                " organization_id) VALUES (CAST(:t AS uuid), :e, :a,"
                " CAST(:o AS uuid)) RETURNING task_id",
                t=tid, e=assignee, a=by, o=self.org)
        return tid, self.number

    def member(self, email: str, permissions: list[str]) -> None:
        identity = self.one(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e)"
            " ON CONFLICT DO NOTHING RETURNING id", e=email)
        if identity is None:
            identity = self.one("SELECT id FROM user_identity WHERE email = :e",
                                e=email)
        self.one(
            "INSERT INTO org_membership (organization_id, user_id, status)"
            " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active')"
            " RETURNING user_id", o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id",
            e=email, o=self.org)
        role = self.one(
            "INSERT INTO org_role (organization_id, slug, display_name)"
            " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
            o=self.org, s="op9-" + email.split("@")[0])
        for permission in permissions:
            self.one(
                "INSERT INTO org_role_permission (role_id, permission)"
                " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                r=str(role), p=permission)
        self.one(
            "INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
            " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, one tenant, two personal trees."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_op9priv"
    _drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(_GENERATED, phase),
                                       encoding="utf-8") as fh:
            _exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'op9-privacy', 'op9-privacy')"), {"id": org})

    a = _Seeder(eng, org)
    a.member(OWNER, ["feature:projects"])
    a.member(READER, ["feature:projects", "data:org:read"])
    a.member(PADMIN, ["feature:projects", "projects:settings:write"])

    w = SimpleNamespace(admin=eng, org=org, p={}, t={}, n={})
    p = w.p

    def task(key: str, project: str, root: str, *, by: str,
             assignee: str | None = None) -> None:
        w.t[key], w.n[key] = a.task(project, root, key, by=by, assignee=assignee)

    p["team"] = a.project("Team", grant_to="org")
    task("team", p["team"], p["team"], by=OWNER, assignee=OWNER)

    # OWNER's personal tree: the root and an Area are stamped, as migration
    # 297 and `tree.create_node` stamp them. The team node under the root is
    # NOT stamped and carries an `org` grant: 297's stop rule, team work.
    p["owner_root"] = a.project("Owner root", personal_owner=OWNER,
                                grant_to=OWNER)
    p["owner_area"] = a.project("Owner area", parent=p["owner_root"],
                                personal_owner=OWNER, grant_to=OWNER)
    p["team_under"] = a.project("Team under owner", parent=p["owner_root"],
                                grant_to="org")
    task("owner_private", p["owner_root"], p["owner_root"], by=OWNER)
    task("owner_mine", p["owner_root"], p["owner_root"], by=OWNER,
         assignee=OWNER)
    task("owner_area", p["owner_area"], p["owner_root"], by=OWNER)
    task("team_under", p["team_under"], p["owner_root"], by=OWNER)

    # READER's own personal tree: the unrestricted arm's "my own" half.
    p["reader_root"] = a.project("Reader root", personal_owner=READER,
                                 grant_to=READER)
    task("reader_private", p["reader_root"], p["reader_root"], by=READER,
         assignee=READER)

    # The delta feed reads nothing newer than its horizon. Put every task an
    # hour back, as `test_sealed_tree_visibility_r8.py` does.
    with eng.begin() as c:
        c.execute(text("UPDATE pm_tasks SET updated_at = now() - interval '1 hour'"
                       " WHERE organization_id = CAST(:o AS uuid)"), {"o": org})

    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    w.owner_personal = {w.t[k] for k in OWNER_PERSONAL}
    w.team = {w.t[k] for k in TEAM}
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def armed(monkeypatch, world):
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    from gateway.retrieval import company

    # A cold scratch database is slower than the route's budget.
    monkeypatch.setattr(company, "SOURCE_TIMEOUT_S", 30.0)
    monkeypatch.setattr(company, "TOTAL_BUDGET_S", 60.0)
    invalidate_access()
    try:
        yield world
    finally:
        invalidate_access()


def _gateway() -> FastAPI:
    from gateway.retrieval import company
    from gateway.routes.projects import router as projects_router

    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(projects_router)
    app.include_router(company.router)
    return app


async def _call(world, email: str, method: str, path: str,
                body: Any = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": email}

    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_gateway()), base_url="http://gw",
        ) as client:
            return await client.request(method, path, headers=headers, json=body)

    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(_go())


async def _ok(world, email: str, path: str, method: str = "GET",
              body: Any = None) -> httpx.Response:
    r = await _call(world, email, method, path, body)
    assert r.status_code == 200, (email, path, r.status_code, r.text)
    return r


def _ids(body: Any) -> set[str]:
    """Every UUID-shaped string anywhere in a JSON body."""
    out: set[str] = set()
    if isinstance(body, dict):
        for value in body.values():
            out |= _ids(value)
    elif isinstance(body, list):
        for value in body:
            out |= _ids(value)
    elif isinstance(body, str) and re.fullmatch(r"[0-9a-f-]{36}", body):
        out.add(body)
    return out


def _titled(world, raw: str) -> set[str]:
    """The task ids whose title key is in a raw body (CSV or JSON text)."""
    return {tid for key, tid in world.t.items()
            if re.search(rf"\b{WORD} {key}\b", raw)}


# ── The reads that the unrestricted arm reaches ────────────────────────────
# Each answers the set of task ids the caller reads. A route answers through
# the real gateway. The seam reads at the bottom run the SQL as the app role.


async def _list(w, who: str) -> set[str]:
    body = (await _ok(w, who, "/projects/tasks?page_size=100")).json()
    rows = {row["id"] for row in body["rows"]}
    assert int(body["total"]) == len(rows), "the total counts what the rows show"
    return rows


async def _search(w, who: str) -> set[str]:
    body = (await _ok(w, who, f"/projects/search?q={WORD}&limit=50")).json()
    return {row["id"] for row in body["rows"]}


async def _refs(w, who: str) -> set[str]:
    numbers = ",".join(str(n) for n in sorted(w.n.values()))
    body = (await _ok(w, who, f"/projects/search/refs?numbers={numbers}")).json()
    return {row["id"] for row in body["rows"]}


async def _company(w, who: str) -> set[str]:
    body = (await _ok(w, who, f"/search/company?q={WORD}&apps=tasks&k=20")).json()
    assert body.get("skipped") == [], f"a source failed: {body.get('skipped')}"
    return {h["id"] for h in body["hits"] if h["app"] == "tasks"}


async def _delta(w, who: str) -> set[str]:
    body = (await _ok(w, who, "/projects/delta/tasks")).json()
    return {row["id"] for row in body["rows"]}


async def _export(w, who: str) -> set[str]:
    return _titled(w, (await _ok(w, who, "/projects/export/tasks.csv")).text)


async def _dataset(w, who: str) -> set[str]:
    body = (await _ok(w, who, "/projects/analytics/dataset?state=all")).json()
    return _ids(body.get("rows")) & set(w.t.values())


async def _detail(w, who: str) -> set[str]:
    seen: set[str] = set()
    for tid in w.t.values():
        r = await _call(w, who, "GET", f"/projects/tasks/{tid}")
        if r.status_code == 200:
            seen.add(tid)
        else:
            assert r.status_code == 404, (who, tid, r.status_code, r.text)
    return seen


async def _report_preview(w, who: str) -> set[str]:
    r = await _ok(w, who, "/projects/reports/preview", "POST", {"config": None})
    return (_ids(r.json()) | _titled(w, r.text)) & set(w.t.values())


ROUTE_READS = {
    "list": _list,
    "detail": _detail,
    "search": _search,
    "refs": _refs,
    "company_search": _company,
    "delta": _delta,
    "export": _export,
    "dataset": _dataset,
}


# ── Seam reads, as the app role, for the doors with no route of their own ──


async def _as_app(world, fn):
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(world.app_dsn).set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                               {"t": world.org})
            return await fn(conn)
    finally:
        await engine.dispose()


async def _seam_tasks(world, who: str, *, agent: AgentScope | None = None) -> set[str]:
    """``task_visibility_clause`` for *who*, resolved as a system job does.

    ``resolve_visibility_for`` is the door of the digest, the reminders and
    the bell (a job that acts for one member, with no request). With
    *agent*, the clause is the outside agent's, as ``resolve_visibility``
    builds it from ``user.agent``.
    """
    from gateway.routes.projects.core import (
        resolve_visibility_for,
        task_visibility_clause,
    )

    async def _go(db) -> set[str]:
        vis = await resolve_visibility_for(db, who)
        if agent is not None:
            vis = dataclasses.replace(vis, agent=agent)
        rows = (await db.execute(
            text(f"SELECT t.id FROM pm_tasks t WHERE {task_visibility_clause(vis)}"),
            vis.params,
        )).fetchall()
        return {str(r.id) for r in rows}

    return await _as_app(world, _go)


async def _system_job(w, who: str) -> set[str]:
    return await _seam_tasks(w, who)


async def _agent(w, who: str) -> set[str]:
    # D-EA-4's opt-in: the connection's member chose to include personal
    # work. It still means the MEMBER's personal work, never another's.
    return await _seam_tasks(
        w, who, agent=AgentScope(connection_id="op9", include_personal=True))


SEAM_READS = {"system_job": _system_job, "agent": _agent}
ALL_READS = {**ROUTE_READS, **SEAM_READS}


# ═══════════════════════════════════════════════════════════════════════════
# Done when 1 — the owner reads their personal task in every read
# ═══════════════════════════════════════════════════════════════════════════


class TestTheOwnerReadsTheirOwn:
    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_the_owner_reads_every_personal_task(self, armed, read):
        rows = await ALL_READS[read](armed, OWNER)
        assert armed.owner_personal <= rows, (read, armed.owner_personal - rows)
        assert armed.t["reader_private"] not in rows, read

    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_an_org_reader_reads_their_own_personal_task(self, armed, read):
        """The unrestricted arm's own half: ``lower(personal_owner) = :me``."""
        rows = await ALL_READS[read](armed, READER)
        assert armed.t["reader_private"] in rows, read

    async def test_the_owner_reads_their_personal_project(self, armed):
        await _ok(armed, OWNER, f"/projects/nodes/{armed.p['owner_root']}")
        await _ok(armed, READER, f"/projects/nodes/{armed.p['reader_root']}")

    async def test_my_work_reads_are_unchanged(self, armed):
        """``/projects/my/inbox`` and ``/projects/assigned-to-me`` read by the
        member's own address and not by the arm. They stay the owner's."""
        body = (await _ok(armed, OWNER, "/projects/my/inbox")).json()
        assert armed.t["owner_mine"] in _ids(body)
        body = (await _ok(armed, OWNER, "/projects/assigned-to-me")).json()
        assert armed.t["owner_mine"] in {r["id"] for r in body["rows"]}
        for who in (READER, PADMIN):
            body = (await _ok(armed, who, "/projects/my/inbox")).json()
            assert not (_ids(body) & armed.owner_personal), who


# ═══════════════════════════════════════════════════════════════════════════
# Done when 2 — the org reader and the admin read it in none
# ═══════════════════════════════════════════════════════════════════════════


class TestNobodyElseReadsIt:
    @pytest.mark.parametrize("who", [READER, PADMIN], ids=["org_reader", "padmin"])
    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_no_personal_task_of_the_owner_reads(self, armed, read, who):
        rows = await ALL_READS[read](armed, who)
        assert not (rows & armed.owner_personal), (read, who)

    @pytest.mark.parametrize("who", [OWNER, PADMIN], ids=["owner", "padmin"])
    async def test_the_readers_personal_task_is_the_readers_only(self, armed, who):
        for read in ALL_READS.values():
            assert armed.t["reader_private"] not in await read(armed, who)

    @pytest.mark.parametrize("who", [READER, PADMIN], ids=["org_reader", "padmin"])
    @pytest.mark.parametrize("key", OWNER_PERSONAL)
    async def test_the_task_read_is_the_one_404(self, armed, who, key):
        r = await _call(armed, who, "GET", f"/projects/tasks/{armed.t[key]}")
        missing = await _call(
            armed, who, "GET",
            "/projects/tasks/00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404, (who, key, r.text)
        assert r.json() == missing.json(), "one not-found text"

    @pytest.mark.parametrize("who", [READER, PADMIN], ids=["org_reader", "padmin"])
    async def test_no_personal_project_lists_or_reads(self, armed, who):
        p = armed.p
        personal = {p["owner_root"], p["owner_area"]}
        for path in ("/projects/tree", "/projects/nodes", "/projects/summary"):
            body = (await _ok(armed, who, path)).json()
            assert not (_ids(body) & personal), (who, path)
        for project in personal:
            r = await _call(armed, who, "GET", f"/projects/nodes/{project}")
            assert r.status_code == 404, (who, r.text)
            r = await _call(armed, who, "GET",
                            f"/projects/tasks?project_id={project}")
            assert r.status_code == 404, (who, r.text)

    @pytest.mark.parametrize("who", [READER, PADMIN], ids=["org_reader", "padmin"])
    async def test_the_report_preview_names_no_personal_task(self, armed, who):
        assert not (await _report_preview(armed, who) & armed.owner_personal)


# ═══════════════════════════════════════════════════════════════════════════
# Done when 3 — team projects read as before
# ═══════════════════════════════════════════════════════════════════════════


class TestTeamWorkReadsAsBefore:
    @pytest.mark.parametrize("who", [READER, PADMIN, OWNER],
                             ids=["org_reader", "padmin", "owner"])
    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_team_tasks_read(self, armed, read, who):
        """The team node under a personal root is team work (297's stop rule)."""
        rows = await ALL_READS[read](armed, who)
        assert armed.team <= rows, (read, who, armed.team - rows)

    @pytest.mark.parametrize("who", [READER, PADMIN], ids=["org_reader", "padmin"])
    async def test_team_projects_read(self, armed, who):
        for project in (armed.p["team"], armed.p["team_under"]):
            await _ok(armed, who, f"/projects/nodes/{project}")
        body = (await _ok(armed, who, "/projects/nodes")).json()
        assert {armed.p["team"], armed.p["team_under"]} <= _ids(body)


# ═══════════════════════════════════════════════════════════════════════════
# Done when 4 — counts leave out personal tasks for the org reader
# ═══════════════════════════════════════════════════════════════════════════


def _summary_total(body: Any) -> int:
    for key in ("tasks", "total_tasks", "total"):
        if isinstance(body.get(key), int):
            return int(body[key])
    totals = body.get("totals") or {}
    return int(sum(v for v in totals.values() if isinstance(v, int)))


class TestCountsLeaveOutPersonalTasks:
    async def test_the_dataset_count_is_the_team_work(self, armed):
        body = (await _ok(armed, READER,
                          "/projects/analytics/dataset?state=all"
                          "&group_by=status_category&measure=count")).json()
        total = sum(int(g["n"]) for g in body["groups"])
        # team + team_under + the reader's own task. Before OP-9 it was 6.
        assert total == 3, body

    async def test_the_owner_count_is_unchanged(self, armed):
        body = (await _ok(armed, OWNER,
                          "/projects/analytics/dataset?state=all"
                          "&group_by=status_category&measure=count")).json()
        assert sum(int(g["n"]) for g in body["groups"]) == 5, body

    async def test_the_portfolio_summary_counts_no_personal_task(self, armed):
        reader = (await _ok(armed, READER, "/projects/summary")).json()
        assert _summary_total(reader) == 2, reader

    async def test_the_load_of_the_owner_counts_only_team_work(self, armed):
        """The reader holds the HR read (``data:org:read``), so load names
        every person. OWNER holds ``team`` and ``owner_mine``. The reader
        counts one."""
        body = (await _ok(armed, READER, "/projects/analytics/load")).json()
        text_body = str(body)
        rows = body.get("rows") or body.get("people") or []
        mine = [r for r in rows
                if str(r.get("assignee") or r.get("who") or r.get("email") or "")
                .lower() == OWNER]
        assert mine, text_body
        counts = [v for k, v in mine[0].items()
                  if k in ("open", "open_tasks", "tasks", "n", "count")]
        assert counts and int(counts[0]) == 1, mine[0]


# ═══════════════════════════════════════════════════════════════════════════
# The suite is honest
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSuiteIsHonest:
    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, armed):
        engine = create_engine(make_url(armed.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)

    async def test_the_team_node_is_team_work_by_the_one_walk(self, armed):
        """The fixture's shape is the shape ``personal_tree_owner`` reads."""
        from gateway.routes.projects.core import personal_tree_owner

        async def _go(db):
            return (
                await personal_tree_owner(db, armed.p["owner_area"]),
                await personal_tree_owner(db, armed.p["team_under"]),
            )

        assert await _as_app(armed, _go) == (OWNER, None)
