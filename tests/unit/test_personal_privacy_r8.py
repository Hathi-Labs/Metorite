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
* MEMBER — a plain member with no grant of their own;
* a team project with an ``org`` grant.

**The grant arm** (fix round 1, D-OP-15). OWNER's root carries a STRAY
``org`` grant, and OWNER's Area a stray email grant to PADMIN. Both are the
shape that a grant written before OP-6b leaves. Neither shows the tree to
anybody but OWNER. MEMBER and PADMIN read through ``_VISIBLE_PROJECTS_SQL``,
so they test the grant arm.

**The scope column** (fix round 1). The bell and the attachment read scope
on ``t.project_id``, so a task in the team node under OWNER's root stays
visible to the org reader and a task in OWNER's own node does not.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_personal_privacy_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import tempfile
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
MEMBER = f"member@{DOMAIN}"
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
    a.member(MEMBER, ["feature:projects"])

    w = SimpleNamespace(admin=eng, org=org, p={}, t={}, n={})
    p = w.p

    def task(key: str, project: str, root: str, *, by: str,
             assignee: str | None = None) -> None:
        w.t[key], w.n[key] = a.task(project, root, key, by=by, assignee=assignee)

    p["team"] = a.project("Team", grant_to="org")
    task("team", p["team"], p["team"], by=OWNER, assignee=OWNER)
    # Fix round 1: a personal node of OWNER under the team project, with no
    # task. It holds the descent of `_VISIBLE_PROJECTS_SQL`: the `org` grant
    # on the team project must not carry a reader into it.
    p["owner_under_team"] = a.project("Owner under team", parent=p["team"],
                                      personal_owner=OWNER)

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

    # Fix round 1: STRAY grants on OWNER's tree, the shape written before
    # OP-6b. A stray `org` grant on the root, and a stray email grant to
    # PADMIN on the Area. D-OP-15: neither shows the tree to anybody else.
    for project, subject in ((p["owner_root"], "org"),
                             (p["owner_area"], PADMIN)):
        a.one(
            "INSERT INTO pm_project_grants (project_id, subject, created_by,"
            " organization_id) VALUES (CAST(:p AS uuid), :s, 'seed',"
            " CAST(:o AS uuid)) RETURNING id",
            p=project, s=subject, o=org)

    # WS-64 OP-3: a node two levels under OWNER's root, stamped as
    # `create_node` stamps it, with no task, so no count above changes. It
    # carries a STRAY `group:` grant to a group that holds MEMBER, the third
    # shape of D-OP-15's grant arm beside the `org` and email grants.
    p["owner_deep"] = a.project("Owner deep", parent=p["owner_area"],
                                personal_owner=OWNER, grant_to=OWNER)
    group = a.one(
        "INSERT INTO org_group (organization_id, slug, display_name)"
        " VALUES (CAST(:o AS uuid), 'op3-stray', 'op3-stray') RETURNING id",
        o=org)
    a.one(
        "INSERT INTO org_group_member (group_id, user_id) SELECT"
        " CAST(:g AS uuid), id FROM app_user WHERE email = :e RETURNING user_id",
        g=str(group), e=MEMBER)
    a.one(
        "INSERT INTO pm_project_grants (project_id, subject, created_by,"
        " organization_id) VALUES (CAST(:p AS uuid), 'group:op3-stray', 'seed',"
        " CAST(:o AS uuid)) RETURNING id",
        p=p["owner_deep"], o=org)

    # READER's own personal tree: the unrestricted arm's "my own" half.
    p["reader_root"] = a.project("Reader root", personal_owner=READER,
                                 grant_to=READER)
    task("reader_private", p["reader_root"], p["reader_root"], by=READER,
         assignee=READER)
    # Unassigned, so only the project arm can show it: the assignee arm
    # cannot stand in for the "my own" half.
    task("reader_note", p["reader_root"], p["reader_root"], by=READER)

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
    w.reader_personal = {w.t["reader_private"], w.t["reader_note"]}

    # Fix round 1: one unread notification and one attached file for READER
    # and MEMBER on each of `team_under` and `owner_private`.
    files = tempfile.mkdtemp(prefix="op9priv-")
    w.files = {}
    for key in ("team_under", "owner_private"):
        for who in (READER, MEMBER):
            a.one(
                "INSERT INTO pm_notifications (recipient, kind, task_id, actor,"
                " organization_id) VALUES (:r, 'mention', CAST(:t AS uuid),"
                " :a, CAST(:o AS uuid)) RETURNING id",
                r=who, t=w.t[key], a=OWNER, o=org)
        path = os.path.join(files, f"{key}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
        aid = str(a.one(
            "INSERT INTO attachments (user_id, name, mime, size_bytes, path,"
            " organization_id) VALUES (:u, :n, 'text/plain', 1, :path,"
            " CAST(:o AS uuid)) RETURNING id",
            u=OWNER, n=f"{key}.txt", path=path, o=org))
        a.one(
            "INSERT INTO pm_task_attachments (task_id, attachment_id, added_by,"
            " organization_id) VALUES (CAST(:t AS uuid), CAST(:a AS uuid), :b,"
            " CAST(:o AS uuid)) RETURNING task_id",
            t=w.t[key], a=aid, b=OWNER, o=org)
        w.files[key] = aid
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


def _node_ids(body: Any) -> set[str]:
    """The ``id`` of every node in a JSON body, and no other field.

    A team node under a personal root carries the root's id as its
    ``parent_project_id``. That pointer is not a read of the root, so the
    node check reads ``id`` only.
    """
    out: set[str] = set()
    if isinstance(body, dict):
        if isinstance(body.get("id"), str):
            out.add(body["id"])
        for value in body.values():
            out |= _node_ids(value)
    elif isinstance(body, list):
        for value in body:
            out |= _node_ids(value)
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
    body = (await _ok(w, who, f"/search/company?q={WORD}&apps=tasks&k=10")).json()
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
        assert not (rows & armed.reader_personal), read

    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_an_org_reader_reads_their_own_personal_task(self, armed, read):
        """The unrestricted arm's own half: ``lower(personal_owner) = :me``."""
        rows = await ALL_READS[read](armed, READER)
        assert armed.reader_personal <= rows, (read, armed.reader_personal - rows)

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


#: Everybody but OWNER. PADMIN and MEMBER read through the grant arm, past
#: the stray grants on OWNER's tree (fix round 1).
OTHERS = [READER, PADMIN, MEMBER]
OTHER_IDS = ["org_reader", "padmin", "member"]


class TestNobodyElseReadsIt:
    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_no_personal_task_of_the_owner_reads(self, armed, read, who):
        rows = await ALL_READS[read](armed, who)
        assert not (rows & armed.owner_personal), (read, who)

    @pytest.mark.parametrize("who", [OWNER, PADMIN, MEMBER],
                             ids=["owner", "padmin", "member"])
    async def test_the_readers_personal_task_is_the_readers_only(self, armed, who):
        for read in ALL_READS.values():
            assert not (await read(armed, who) & armed.reader_personal)

    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    @pytest.mark.parametrize("key", OWNER_PERSONAL)
    async def test_the_task_read_is_the_one_404(self, armed, who, key):
        r = await _call(armed, who, "GET", f"/projects/tasks/{armed.t[key]}")
        missing = await _call(
            armed, who, "GET",
            "/projects/tasks/00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404, (who, key, r.text)
        assert r.json() == missing.json(), "one not-found text"

    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    async def test_no_personal_project_lists_or_reads(self, armed, who):
        p = armed.p
        personal = {p["owner_root"], p["owner_area"], p["owner_deep"]}
        for path in ("/projects/tree", "/projects/nodes", "/projects/summary"):
            body = (await _ok(armed, who, path)).json()
            assert not (_node_ids(body) & personal), (who, path)
        for project in personal:
            r = await _call(armed, who, "GET", f"/projects/nodes/{project}")
            assert r.status_code == 404, (who, r.text)
            r = await _call(armed, who, "GET",
                            f"/projects/tasks?project_id={project}")
            assert r.status_code == 404, (who, r.text)

    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    async def test_the_report_preview_names_no_personal_task(self, armed, who):
        assert not (await _report_preview(armed, who) & armed.owner_personal)


# ═══════════════════════════════════════════════════════════════════════════
# Done when 3 — team projects read as before
# ═══════════════════════════════════════════════════════════════════════════


class TestTeamWorkReadsAsBefore:
    @pytest.mark.parametrize("who", [*OTHERS, OWNER], ids=[*OTHER_IDS, "owner"])
    @pytest.mark.parametrize("read", sorted(ALL_READS))
    async def test_team_tasks_read(self, armed, read, who):
        """The team node under a personal root is team work (297's stop rule)."""
        rows = await ALL_READS[read](armed, who)
        assert armed.team <= rows, (read, who, armed.team - rows)

    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    async def test_team_projects_read(self, armed, who):
        for project in (armed.p["team"], armed.p["team_under"]):
            await _ok(armed, who, f"/projects/nodes/{project}")
        body = (await _ok(armed, who, "/projects/nodes")).json()
        assert {armed.p["team"], armed.p["team_under"]} <= _ids(body)


# ═══════════════════════════════════════════════════════════════════════════
# Done when 4 — counts leave out personal tasks for the org reader
# ═══════════════════════════════════════════════════════════════════════════


class TestCountsLeaveOutPersonalTasks:
    async def test_the_dataset_count_is_the_team_work(self, armed):
        body = (await _ok(armed, READER,
                          "/projects/analytics/dataset?state=all"
                          "&group_by=status_category&measure=count")).json()
        total = sum(int(g["n"]) for g in body["groups"])
        # team + team_under + the reader's two own tasks. Before OP-9 it was 7.
        assert total == 4, body

    async def test_the_owner_count_is_unchanged(self, armed):
        body = (await _ok(armed, OWNER,
                          "/projects/analytics/dataset?state=all"
                          "&group_by=status_category&measure=count")).json()
        assert sum(int(g["n"]) for g in body["groups"]) == 5, body

    async def test_the_portfolio_summary_counts_no_personal_task(self, armed):
        """The summary counts each task in a space of the forest read, and
        that read leaves every personal node out for every caller. So the
        count is ``team`` alone: ``team_under`` hangs under a root the reader
        cannot read, and the summary counts no orphan."""
        reader = (await _ok(armed, READER, "/projects/summary")).json()
        assert reader["tasks"] == 1, reader

    async def test_the_load_counts_no_personal_task(self, armed):
        """``/projects/analytics/load`` over the portfolio. Open work the
        reader reads: team, team_under and the reader's two own tasks. Before
        OP-9 it held the owner's three personal tasks too, so it was 7."""
        body = (await _ok(armed, READER, "/projects/analytics/load")).json()
        assert body["total_tasks"] == 4, body
        owner = (await _ok(armed, OWNER, "/projects/analytics/load")).json()
        assert owner["total_tasks"] == 5, owner


# ═══════════════════════════════════════════════════════════════════════════
# Fix round 1 — the grant arm and the scope column
# ═══════════════════════════════════════════════════════════════════════════


class TestAStrayGrantShowsTheOwnerOnly:
    """D-OP-15 on the grant arm. Mutation G1: delete the ``personal_owner``
    line from the seed of ``_VISIBLE_PROJECTS_SQL``. The stray ``org`` grant
    then seeds OWNER's root, and MEMBER reads every personal task."""

    async def test_the_owner_still_reads_the_whole_tree(self, armed):
        rows = await _list(armed, OWNER)
        assert armed.owner_personal | armed.team <= rows

    @pytest.mark.parametrize("who", [PADMIN, MEMBER], ids=["padmin", "member"])
    async def test_the_stray_grant_seeds_nothing(self, armed, who):
        rows = await _seam_tasks(armed, who)
        assert not (rows & armed.owner_personal), who
        assert armed.team <= rows, "the team node under the root keeps its own grant"


class TestTheDescentStopsAtAPersonalNode:
    """Mutation G2: delete the ``personal_owner`` line from the recursive
    step of ``_VISIBLE_PROJECTS_SQL``. The ``org`` grant on the team
    project then carries MEMBER into OWNER's node under it."""

    async def _projects(self, world, who: str) -> set[str]:
        from gateway.routes.projects.core import resolve_visibility_for

        async def _go(db) -> set[str]:
            vis = await resolve_visibility_for(db, who)
            rows = (await db.execute(
                text(f"SELECT id FROM pm_projects WHERE {vis.project_clause()}"),
                vis.params,
            )).fetchall()
            return {str(r.id) for r in rows}

        return await _as_app(world, _go)

    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    async def test_no_other_reader_reaches_it(self, armed, who):
        got = await self._projects(armed, who)
        assert armed.p["owner_under_team"] not in got, who
        assert armed.p["team"] in got, who

    async def test_the_owner_reaches_it(self, armed):
        assert armed.p["owner_under_team"] in await self._projects(armed, OWNER)


class TestTheScopeColumnIsTheTasksOwnNode:
    """The bell and the file read scope on ``t.project_id``. Mutation C1:
    put ``t.root_project_id`` back in ``list_notifications``. The org reader
    then loses ``team_under`` (its root is OWNER's) from the list and the
    badge. Mutation C2: the same in ``visible_task_attachment``."""

    @pytest.mark.parametrize("who", [READER, MEMBER], ids=["org_reader", "member"])
    async def test_the_bell_lists_the_team_node_task_only(self, armed, who):
        body = (await _ok(armed, who, "/projects/notifications")).json()
        tasks = {row["task_id"] for row in body["rows"]}
        assert tasks == {armed.t["team_under"]}, (who, body)
        assert body["unread"]["total"] == 1, body
        assert body["unread"]["mentions"] == 1, body

    @pytest.mark.parametrize("who", [READER, MEMBER], ids=["org_reader", "member"])
    async def test_the_file_of_the_team_node_task_serves(self, armed, who):
        aid = armed.files["team_under"]
        r = await _call(armed, who, "GET", f"/projects/attachments/{aid}/f.txt")
        assert r.status_code == 200, (who, r.text)
        assert r.text == "team_under"

    @pytest.mark.parametrize("who", [READER, MEMBER, PADMIN],
                             ids=["org_reader", "member", "padmin"])
    async def test_the_file_of_a_personal_task_is_the_one_404(self, armed, who):
        aid = armed.files["owner_private"]
        r = await _call(armed, who, "GET", f"/projects/attachments/{aid}/f.txt")
        assert r.status_code == 404, (who, r.text)

    async def test_the_owner_reads_both_files(self, armed):
        for key in ("team_under", "owner_private"):
            r = await _call(armed, OWNER, "GET",
                            f"/projects/attachments/{armed.files[key]}/f.txt")
            assert r.status_code == 200, (key, r.text)


# ═══════════════════════════════════════════════════════════════════════════
# WS-64 OP-3 — the Personal space in GET /projects/tree
# ═══════════════════════════════════════════════════════════════════════════


def _walk(nodes: list[dict]) -> dict[str, dict]:
    """Every node of a nested list, by id."""
    out: dict[str, dict] = {}
    for node in nodes:
        out[node["id"]] = node
        out.update(_walk(node.get("children") or []))
    return out


class TestThePersonalSpace:
    """OP-3 done when 1. ``personal`` holds the caller's own tree, nested.

    The mutations (R7), each in ``G/tree.py`` (``G`` is
    ``apps/services/gateway/gateway/routes/projects``):

    * P1. ``_OWN_PERSONAL_CLAUSE``: drop the
      ``lower(personal_owner) = :vis_email`` line. Every other member reads
      OWNER's tree in ``personal``.
    * P2. ``_OWN_PERSONAL_CLAUSE`` becomes ``vis.project_clause()``. The team
      nodes enter ``personal``, and OWNER's ``personal`` is not OWNER's tree.
    * P3. ``_own_personal_projects``: no ``_narrow_for_agent``. An agent
      without ``include_personal`` reads the tree.
    """

    def _owner_nodes(self, w) -> set[str]:
        p = w.p
        # `owner_under_team` is OWNER's too. Its parent is a team node, so
        # it is a root of `personal`.
        return {p["owner_root"], p["owner_area"], p["owner_deep"],
                p["owner_under_team"]}

    async def test_the_owner_reads_the_root_and_a_node_two_levels_under(self, armed):
        body = (await _ok(armed, OWNER, "/projects/tree")).json()
        p = armed.p
        roots = {n["id"]: n for n in body["personal"]}
        assert set(roots) == {p["owner_root"], p["owner_under_team"]}, roots
        area = {c["id"]: c for c in roots[p["owner_root"]]["children"]}
        assert set(area) == {p["owner_area"]}, "the team node is not personal"
        deep = [c["id"] for c in area[p["owner_area"]]["children"]]
        assert deep == [p["owner_deep"]]
        assert set(_walk(body["personal"])) == self._owner_nodes(armed)

    async def test_each_personal_node_is_marked_and_offers_no_grant(self, armed):
        from gateway.routes.projects.authority import PERSONAL_GRANT_REFUSED

        body = (await _ok(armed, OWNER, "/projects/tree")).json()
        for node in _walk(body["personal"]).values():
            assert node["personal"] is True, node
            assert node["personal_owner"].lower() == OWNER, node
            assert node["can_write_grants"] is False, node
            assert node["grant_refusal"] == PERSONAL_GRANT_REFUSED, node
            assert isinstance(node["can_delete"], bool), node

    async def test_the_counts_are_the_progress_read(self, armed):
        body = (await _ok(armed, OWNER, "/projects/tree")).json()
        nodes = _walk(body["personal"])
        p = armed.p
        # owner_private, owner_mine and owner_area. `team_under` hangs under
        # the root but is team work, so it is in no personal count.
        assert nodes[p["owner_root"]]["tasks"] == 3, nodes[p["owner_root"]]
        assert nodes[p["owner_area"]]["tasks"] == 1
        assert nodes[p["owner_deep"]]["tasks"] == 0

    async def test_rows_keep_their_shape(self, armed):
        """R6. ``rows`` and ``total`` are what an old client read."""
        body = (await _ok(armed, OWNER, "/projects/tree")).json()
        rows = _walk(body["rows"])
        assert not (set(rows) & self._owner_nodes(armed))
        assert {armed.p["team"], armed.p["team_under"]} <= set(rows)
        assert body["total"] == len(rows)
        assert not any("personal" in n for n in rows.values())

    async def test_an_org_reader_reads_only_their_own_space(self, armed):
        body = (await _ok(armed, READER, "/projects/tree")).json()
        assert set(_walk(body["personal"])) == {armed.p["reader_root"]}

    @pytest.mark.parametrize("who", [PADMIN, MEMBER], ids=["padmin", "member"])
    async def test_a_stray_grantee_and_an_admin_read_no_space(self, armed, who):
        """PADMIN holds a stray email grant, MEMBER the stray ``org`` and
        ``group:`` grants. Neither has a personal tree of their own."""
        body = (await _ok(armed, who, "/projects/tree")).json()
        assert body["personal"] == [], (who, body["personal"])

    @pytest.mark.parametrize("who", OTHERS, ids=OTHER_IDS)
    async def test_the_deep_node_reads_nowhere_else(self, armed, who):
        deep = armed.p["owner_deep"]
        for path in ("/projects/tree", "/projects/nodes"):
            body = (await _ok(armed, who, path)).json()
            assert deep not in _node_ids(body), (who, path)
        r = await _call(armed, who, "GET", f"/projects/nodes/{deep}")
        assert r.status_code == 404, (who, r.text)

    async def _agent_tree(self, w, who: str, *, include_personal: bool) -> set[str]:
        from gateway.routes.projects.core import resolve_visibility_for
        from gateway.routes.projects.tree import _own_personal_projects

        async def _go(db) -> set[str]:
            vis = dataclasses.replace(
                await resolve_visibility_for(db, who),
                agent=AgentScope(connection_id="op3",
                                 include_personal=include_personal))
            return {str(r.id) for r in await _own_personal_projects(db, vis)}

        return await _as_app(w, _go)

    async def test_an_agent_without_personal_reads_no_space(self, armed):
        """D-EA-4. The route-level check is in ``test_personal_owner_stamp``."""
        assert await self._agent_tree(armed, OWNER, include_personal=False) == set()
        assert (await self._agent_tree(armed, OWNER, include_personal=True)
                == self._owner_nodes(armed))


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
