"""WS-64 OP-11 — one "mine", and every read of it agrees (R8).

Spec: ``project-docs/specs/one_projects_app.md`` §9 OP-11.

``core.mine_clause`` is the one definition of "mine": assigned to me, OR in
my personal tree, OR my WAITING overlay inside my grants. Archived projects
are out. Each test runs the REAL routes as a NON-privileged role on a
promoted catalog of its own, so FORCE RLS binds every read.

The world. One organization holds A and B, two plain members. A second
organization holds C, who types A's address onto a task of their own.

* ``team`` — an ``org`` grant. A task assigned to A (open, overdue, blocked
  by a parked blocker), a done task of A, a task of B that A waits on
  (A's overlay says WAITING), a task of B, a blocked task of B, an
  unassigned task, and the parked blocker.
* ``secret`` — a grant to B only. A task assigned to A (A reads it as its
  assignee, and may not NAME the project), and a task of B that A's overlay
  says WAITING on (A holds no grant, so it is NOT mine).
* ``old`` — archived, with an ``org`` grant. A task assigned to A.
* A's and B's personal trees, each with one unassigned task.

What must agree: ``/projects/tasks?scope=mine``, ``/projects/calendar?
scope=mine``, ``/projects/my/summary``, ``/projects/assigned-to-me`` (the
assignee arm), ``/projects/my/inbox`` and ``/projects/needs-attention?mine=true``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_mine_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import os
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import require_authenticated
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

_APP_ROLE = "acb_app_op11mine"
_APP_PW = "op11mine_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

INTERNAL = "op11-mine-internal-token"
DOMAIN = "op11mine.test"
A = f"alice@{DOMAIN}"
B = f"bob@{DOMAIN}"
C = f"carol@{DOMAIN}"

#: A's own tasks, by key. The parity tests compare every read to this set.
MINE_A = ("a_open", "a_done", "a_waits", "a_secret", "a_personal", "both", "both2")
MINE_B = ("b_open", "b_blocked", "a_waits", "secret_waits", "b_personal", "b_waits",
          "both", "both2")


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

    def __init__(self, engine, org: str, slug: str) -> None:
        self.engine = engine
        self.org = org
        self.slug = slug
        self.number = 0

    def one(self, sql: str, **params) -> Any:
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": self.org})
            row = c.execute(text(sql), params).first()
        return row[0] if row is not None else None

    def project(self, name: str, *, by: str, personal_owner: str | None = None,
                grant_to: str | None = None, archived: bool = False) -> str:
        pid = str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by,"
            " organization_id, timezone, owns_statuses, personal_owner,"
            " archived_at) VALUES (:n, 'active', 'manual', :a,"
            " CAST(:o AS uuid), 'UTC', true, :po,"
            " CASE WHEN :arch THEN now() END) RETURNING id",
            n=name, a=by, o=self.org, po=personal_owner, arch=archived))
        if grant_to:
            self.one(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, :a,"
                " CAST(:o AS uuid)) RETURNING project_id",
                p=pid, s=grant_to, a=by, o=self.org)
        return pid

    def status(self, project: str, name: str, category: str, position: int) -> str:
        return str(self.one(
            "INSERT INTO pm_task_statuses (project_id, name, color, position,"
            " category, is_default, organization_id) VALUES (CAST(:p AS uuid),"
            " :n, 'gray', :pos, :c, :d, CAST(:o AS uuid)) RETURNING id",
            p=project, n=name, pos=position, c=category, d=position == 0,
            o=self.org))

    def task(self, key: str, project: str, status: str, *, by: str,
             assignee: str | None = None, due: datetime | None = None) -> str:
        self.number += 1
        tid = str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number, due_at) VALUES (:t,"
            " CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :a,"
            " CAST(:o AS uuid), :n, :due) RETURNING id",
            t=f"op11 {key}", p=project, s=status, a=by, o=self.org,
            n=self.number, due=due))
        if assignee:
            self.one(
                "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
                " organization_id) VALUES (CAST(:t AS uuid), :e, :a,"
                " CAST(:o AS uuid)) RETURNING task_id",
                t=tid, e=assignee, a=by, o=self.org)
        return tid

    def waiting(self, task: str, member: str) -> None:
        self.one(
            "INSERT INTO pm_task_personal (task_id, member_email, disposition,"
            " delegated_at, organization_id) VALUES (CAST(:t AS uuid), :m,"
            " 'WAITING', now(), CAST(:o AS uuid)) RETURNING task_id",
            t=task, m=member, o=self.org)

    def overlay(self, task: str, member: str, disposition: str) -> None:
        """A stated disposition of one member on a task (the triage)."""
        self.one(
            "INSERT INTO pm_task_personal (task_id, member_email, disposition,"
            " organization_id) VALUES (CAST(:t AS uuid), :m, :d,"
            " CAST(:o AS uuid)) RETURNING task_id",
            t=task, m=member, d=disposition, o=self.org)

    def assign(self, task: str, assignee: str, *, by: str) -> None:
        self.one(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by,"
            " organization_id) VALUES (CAST(:t AS uuid), :e, :a,"
            " CAST(:o AS uuid)) RETURNING task_id",
            t=task, e=assignee, a=by, o=self.org)

    def blocks(self, blocker: str, blocked: str) -> None:
        self.one(
            "INSERT INTO pm_task_links (source_task_id, target_task_id, link_type,"
            " created_by, organization_id) VALUES (CAST(:s AS uuid),"
            " CAST(:t AS uuid), 'blocks', 'seed', CAST(:o AS uuid)) RETURNING id",
            s=blocker, t=blocked, o=self.org)

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
            o=self.org, s=f"{self.slug}-" + email.split("@")[0])
        for permission in permissions:
            self.one(
                "INSERT INTO org_role_permission (role_id, permission)"
                " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                r=str(role), p=permission)
        self.one(
            "INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
            " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))


def _board(a: _Seeder, name: str, *, by: str, **kw: Any) -> SimpleNamespace:
    pid = a.project(name, by=by, **kw)
    return SimpleNamespace(
        id=pid,
        todo=a.status(pid, "To do", "todo", 0),
        backlog=a.status(pid, "Later", "backlog", 10),
        done=a.status(pid, "Done", "done", 40),
    )


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog with two organizations."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_op11mine"
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

    org, org_c = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "op11-one"), (org_c, "op11-two")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})

    s = _Seeder(eng, org, "op11")
    s.member(A, ["feature:projects"])
    s.member(B, ["feature:projects"])
    now = datetime.now(UTC)
    t: dict[str, str] = {}

    team = _board(s, "Team", by=A, grant_to="org")
    secret = _board(s, "Secret", by=B, grant_to=B)
    old = _board(s, "Old", by=A, grant_to="org", archived=True)
    a_root = _board(s, "Alice root", by=A, personal_owner=A, grant_to=A)
    b_root = _board(s, "Bob root", by=B, personal_owner=B, grant_to=B)

    t["a_open"] = s.task("a_open", team.id, team.todo, by=B, assignee=A,
                         due=now - timedelta(days=1))
    t["a_done"] = s.task("a_done", team.id, team.done, by=B, assignee=A)
    t["a_waits"] = s.task("a_waits", team.id, team.todo, by=A, assignee=B,
                          due=now + timedelta(days=1))
    s.waiting(t["a_waits"], A)
    t["b_open"] = s.task("b_open", team.id, team.todo, by=B, assignee=B)
    t["b_blocked"] = s.task("b_blocked", team.id, team.todo, by=B, assignee=B)
    t["team_free"] = s.task("team_free", team.id, team.todo, by=B)
    t["blocker"] = s.task("blocker", team.id, team.backlog, by=B)
    s.blocks(t["blocker"], t["a_open"])
    s.blocks(t["blocker"], t["b_blocked"])
    t["a_secret"] = s.task("a_secret", secret.id, secret.todo, by=B, assignee=A)
    t["secret_waits"] = s.task("secret_waits", secret.id, secret.todo, by=B,
                               assignee=B)
    s.waiting(t["secret_waits"], A)
    t["a_old"] = s.task("a_old", old.id, old.todo, by=A, assignee=A)
    t["a_personal"] = s.task("a_personal", a_root.id, a_root.todo, by=A)
    t["b_personal"] = s.task("b_personal", b_root.id, b_root.todo, by=B)

    # B holds a WAITING overlay on a task that A can read and does not hold.
    # Dropping the member test of the WAITING arm would hand it to A.
    t["b_waits"] = s.task("b_waits", team.id, team.todo, by=B)
    s.waiting(t["b_waits"], B)
    # Two tasks assigned to BOTH members, each with an overlay of B. A join on
    # the overlay without the member test would give A B's disposition, or a
    # second row for `both`, where A holds an overlay of their own.
    t["both"] = s.task("both", team.id, team.todo, by=B, assignee=A)
    s.assign(t["both"], B, by=B)
    s.overlay(t["both"], A, "NEXT")
    s.overlay(t["both"], B, "SOMEDAY")
    t["both2"] = s.task("both2", team.id, team.todo, by=B, assignee=A)
    s.assign(t["both2"], B, by=B)
    s.overlay(t["both2"], B, "SOMEDAY")

    # The second organization: C types A's address onto a task of their own.
    sc = _Seeder(eng, org_c, "op11c")
    sc.member(C, ["feature:projects"])
    other = _board(sc, "Other org", by=C, grant_to="org")
    t["c_for_a"] = sc.task("c_for_a", other.id, other.todo, by=C, assignee=A)
    t["c_own"] = sc.task("c_own", other.id, other.todo, by=C, assignee=C)

    w = SimpleNamespace(
        admin=eng, org=org, t=t,
        p=SimpleNamespace(team=team, secret=secret, old=old, a_root=a_root,
                          b_root=b_root, other=other),
        app_dsn=admin_url.set(
            database=dedicated, username=_APP_ROLE, password=_APP_PW,
        ).render_as_string(hide_password=False),
    )
    w.mine_a = {t[k] for k in MINE_A}
    w.mine_b = {t[k] for k in MINE_B}
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
    invalidate_access()
    try:
        yield world
    finally:
        invalidate_access()


def _gateway() -> FastAPI:
    from gateway.routes.projects import router as projects_router

    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(projects_router)
    return app


async def _get(world, email: str, path: str, params: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": email}

    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_gateway()), base_url="http://gw",
        ) as client:
            return await client.get(path, headers=headers, params=params or {})

    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(_go())


async def _ok(world, email: str, path: str, params: dict | None = None) -> Any:
    r = await _get(world, email, path, params)
    assert r.status_code == 200, (email, path, params, r.status_code, r.text)
    return r.json()


async def _list_mine(world, email: str, **extra: Any) -> list[dict]:
    body = await _ok(world, email, "/projects/tasks",
                     {"scope": "mine", "page_size": 100, **extra})
    assert body["total"] == len(body["rows"])
    return body["rows"]


def _named(world, ids: set[str]) -> set[str]:
    back = {v: k for k, v in world.t.items()}
    return {back.get(i, i) for i in ids}


def _window() -> dict[str, str]:
    today = datetime.now(UTC).date()
    return {"from": (today - timedelta(days=10)).isoformat(),
            "to": (today + timedelta(days=10)).isoformat()}


class TestTheOneMine:
    async def test_the_list_answers_my_three_arms_and_nothing_else(self, armed):
        """Assigned, personal and WAITING inside my grants. Not B's work, not
        B's personal task, not the other org, not the archive, and not a task
        my overlay names in a project I cannot read."""
        rows = await _list_mine(armed, A)
        got = {r["id"] for r in rows}
        assert _named(armed, got) == set(MINE_A)
        for key in ("b_open", "b_personal", "c_for_a", "a_old", "secret_waits",
                    "team_free", "blocker"):
            assert armed.t[key] not in got, key

    async def test_b_gets_b_and_never_a(self, armed):
        got = {r["id"] for r in await _list_mine(armed, B)}
        assert _named(armed, got) == set(MINE_B)
        assert armed.t["a_personal"] not in got

    async def test_the_other_org_sees_only_its_own(self, armed):
        got = {r["id"] for r in await _list_mine(armed, C)}
        assert _named(armed, got) == {"c_own"}

    async def test_include_subtree_narrows_to_mine_inside_the_node(self, armed):
        got = {r["id"] for r in await _list_mine(
            armed, A, project_id=armed.p.team.id, include_subtree="true")}
        assert _named(armed, got) == {"a_open", "a_done", "a_waits", "both", "both2"}

    async def test_the_board_filters_still_apply(self, armed):
        got = {r["id"] for r in await _list_mine(armed, A, status_category="done")}
        assert _named(armed, got) == {"a_done"}
        got = {r["id"] for r in await _list_mine(armed, A, overdue="true")}
        assert _named(armed, got) == {"a_open"}
        page = await _ok(armed, A, "/projects/tasks",
                         {"scope": "mine", "page_size": 2, "sort": "created_at"})
        assert page["total"] == len(MINE_A) and len(page["rows"]) == 2

    async def test_an_unknown_scope_is_a_422(self, armed):
        r = await _get(armed, A, "/projects/tasks", {"scope": B})
        assert r.status_code == 422

    async def test_with_overlay_carries_my_overlay(self, armed):
        rows = {r["id"]: r for r in await _list_mine(armed, A, with_overlay="1")}
        assert rows[armed.t["a_waits"]]["disposition"] == "WAITING"
        assert rows[armed.t["a_waits"]]["is_triaged"] is True
        assert rows[armed.t["a_open"]]["disposition"] == "NEXT"
        assert rows[armed.t["a_done"]]["disposition"] == "DONE"
        assert rows[armed.t["a_personal"]]["disposition"] == "INBOX"
        assert rows[armed.t["a_open"]]["is_triaged"] is False
        # B reads the same task with B's own overlay: none, so derived.
        b_rows = {r["id"]: r for r in await _list_mine(armed, B, with_overlay="1")}
        assert b_rows[armed.t["a_waits"]]["disposition"] == "NEXT"


class TestTheReadsAgree:
    """The parity fence: each read gives the same id set for A."""

    async def test_calendar_list_inbox_and_summary_agree(self, armed):
        listed = {r["id"] for r in await _list_mine(armed, A)}
        cal = await _ok(armed, A, "/projects/calendar",
                        {"scope": "mine", "include_undated": "true", **_window()})
        on_calendar = {r["id"] for r in cal["rows"]} | {r["id"] for r in cal["unscheduled"]}
        inbox = await _ok(armed, A, "/projects/my/inbox",
                          {"include_done": "true", "include_deferred": "true",
                           "page_size": 100})
        in_inbox = {r["id"] for r in inbox["rows"]}
        summary = await _ok(armed, A, "/projects/my/summary")
        assert listed == armed.mine_a
        assert on_calendar == listed
        assert in_inbox == listed
        assert summary["tasks"] == len(listed)

    async def test_assigned_to_me_is_the_assignee_arm(self, armed):
        body = await _ok(armed, A, "/projects/assigned-to-me",
                         {"include_done": "true", "page_size": 100})
        assert _named(armed, {r["id"] for r in body["rows"]}) == {
            "a_open", "a_done", "a_secret", "both", "both2"}

    async def test_needs_attention_mine_is_inside_scope_mine(self, armed):
        """``/needs-attention?mine=true`` with no project. Its ``yours`` is the
        assignee of a blocked task (`needs_attention.blocked_sql`), so its set
        is the blocked part of mine, and B's blocked task is not in it."""
        listed = {r["id"] for r in await _list_mine(armed, A)}
        mine = await _ok(armed, A, "/projects/needs-attention", {"mine": "true"})
        every = await _ok(armed, A, "/projects/needs-attention")
        ids_mine = {r["task_id"] for r in mine["rows"]}
        ids_every = {r["task_id"] for r in every["rows"]}
        assert ids_mine == {armed.t["a_open"]}
        assert ids_mine <= listed
        assert ids_every & listed == ids_mine
        assert armed.t["b_blocked"] in ids_every - ids_mine


class TestTheOverview:
    async def test_the_counts_equal_the_list_counted_here(self, armed):
        rows = await _list_mine(armed, A, with_overlay="1")
        summary = await _ok(armed, A, "/projects/my/summary")
        statuses = {}
        for board in (armed.p.team, armed.p.secret, armed.p.a_root):
            statuses.update({board.todo: "todo", board.backlog: "backlog",
                             board.done: "done"})
        by_category = Counter(statuses[r["status_id"]] for r in rows)
        assert summary["by_category"] == dict(by_category)
        assert summary["tasks"] == len(rows) == len(MINE_A)
        assert summary["overdue"] == 1
        assert summary["due_soon"] == 1
        dispositions = Counter(r["disposition"] for r in rows)
        assert summary["by_disposition"]["waiting"] == dispositions["WAITING"] == 1
        assert summary["by_disposition"]["done"] == dispositions["DONE"] == 1
        assert dispositions["INBOX"] == 1
        # "My task inbox" counts the INBOX row AND the untriaged board rows a
        # colleague assigned to me: a_open, a_secret and both2 (P2 of the review).
        assert summary["by_disposition"]["inbox"] == 4
        assert summary["by_disposition"]["next"] == 4
        assert summary["by_disposition"]["someday"] == 0
        assert summary["by_disposition"]["today"] == 1

    async def test_no_row_names_a_project_i_cannot_see(self, armed):
        """Audit P1-8. ``a_secret`` is mine (I am its assignee), and I hold
        no grant on Secret. It counts, in ``own``, and nothing names Secret."""
        summary = await _ok(armed, A, "/projects/my/summary")
        names = {c["name"] for c in summary["children"]}
        assert names == {"Team", "Alice root"}
        assert armed.p.secret.id not in {c["id"] for c in summary["children"]}
        assert "Secret" not in str(summary)
        assert summary["own"]["tasks"] == 1
        assert summary["own"]["tasks"] + sum(
            c["tasks"] for c in summary["children"]) == summary["tasks"]
        team = next(c for c in summary["children"] if c["name"] == "Team")
        assert team["tasks"] == 5 and team["overdue"] == 1

    async def test_b_reads_b_numbers(self, armed):
        summary = await _ok(armed, B, "/projects/my/summary")
        assert summary["tasks"] == len(MINE_B)
        names = {c["name"] for c in summary["children"]}
        assert names == {"Team", "Secret", "Bob root"}


async def _fold(world, email: str) -> tuple[list[dict], dict]:
    rows = await _list_mine(world, email, with_overlay="1")
    summary = await _ok(world, email, "/projects/my/summary")
    return rows, summary


class TestTheMemberTestsAreFenced:
    """Mutation fences: each member test in `mine_clause` and the summary join."""

    async def test_another_members_waiting_overlay_is_not_mine(self, armed):
        """B waits on `b_waits`, a task A can read and does not hold. It is in
        none of A's reads, and it is in B's. Kills the removal of
        ``lower(p.member_email) = :who`` from the WAITING arm."""
        b_waits = armed.t["b_waits"]
        listed = {r["id"] for r in await _list_mine(armed, A)}
        cal = await _ok(armed, A, "/projects/calendar",
                        {"scope": "mine", "include_undated": "true", **_window()})
        on_calendar = {r["id"] for r in cal["rows"]} | {r["id"] for r in cal["unscheduled"]}
        inbox = await _ok(armed, A, "/projects/my/inbox",
                          {"include_done": "true", "include_deferred": "true",
                           "page_size": 100})
        in_inbox = {r["id"] for r in inbox["rows"]}
        summary = await _ok(armed, A, "/projects/my/summary")
        assert b_waits not in listed
        assert b_waits not in on_calendar
        assert b_waits not in in_inbox
        assert summary["tasks"] == len(MINE_A)
        assert summary["by_disposition"]["waiting"] == 1  # a_waits only
        # B reads it in every view, and A can read the task on the board.
        assert b_waits in {r["id"] for r in await _list_mine(armed, B)}
        b_cal = await _ok(armed, B, "/projects/calendar",
                          {"scope": "mine", "include_undated": "true", **_window()})
        assert b_waits in ({r["id"] for r in b_cal["rows"]}
                           | {r["id"] for r in b_cal["unscheduled"]})
        b_inbox = await _ok(armed, B, "/projects/my/inbox",
                            {"include_deferred": "true", "page_size": 100})
        assert b_waits in {r["id"] for r in b_inbox["rows"]}
        b_summary = await _ok(armed, B, "/projects/my/summary")
        assert b_summary["by_disposition"]["waiting"] == 1
        board = await _ok(armed, A, "/projects/tasks", {"page_size": 100})
        assert b_waits in {r["id"] for r in board["rows"]}

    async def test_the_summary_reads_my_overlay_and_no_row_twice(self, armed):
        """`both` and `both2` have an overlay of B (SOMEDAY). A's numbers come
        from A's overlay alone, and the totals equal the list counted here.
        Kills the removal of ``lower(po.member_email) = :who`` in the join."""
        rows_a, sum_a = await _fold(armed, A)
        assert len({r["id"] for r in rows_a}) == len(rows_a) == len(MINE_A)
        assert sum_a["tasks"] == len(rows_a)
        assert sum(sum_a["by_category"].values()) == len(rows_a)
        assert sum_a["by_disposition"] == {
            "inbox": 4, "today": 1, "next": 4, "waiting": 1, "someday": 0, "done": 1,
        }
        by_id = {r["id"]: r for r in rows_a}
        assert by_id[armed.t["both"]]["disposition"] == "NEXT"
        assert by_id[armed.t["both2"]]["disposition"] == "NEXT"
        rows_b, sum_b = await _fold(armed, B)
        assert len({r["id"] for r in rows_b}) == len(rows_b) == len(MINE_B)
        assert sum_b["tasks"] == len(rows_b)
        assert sum_b["by_disposition"] == {
            "inbox": 5, "today": 0, "next": 4, "waiting": 1, "someday": 2, "done": 0,
        }
        by_id_b = {r["id"]: r for r in rows_b}
        assert by_id_b[armed.t["both"]]["disposition"] == "SOMEDAY"
        assert by_id_b[armed.t["both2"]]["disposition"] == "SOMEDAY"


class TestTheInboxBadge:
    async def test_the_inbox_count_is_what_the_badge_counts(self, armed):
        """A colleague assigned `a_open`, `a_secret` and `both2` to A, and A
        never triaged them. The badge (`inboxCount`) counts the INBOX rows plus
        those rows, and `/my/inbox` lists them, so the numbers agree."""
        for email in (A, B):
            summary = await _ok(armed, email, "/projects/my/summary")
            untriaged = await _ok(armed, email, "/projects/my/inbox",
                                  {"untriaged": "true", "page_size": 100})
            stated = await _ok(armed, email, "/projects/my/inbox",
                               {"disposition": "INBOX", "page_size": 100})
            assert summary["by_disposition"]["inbox"] == (
                len(untriaged["rows"]) + len(stated["rows"])), email
        got = await _ok(armed, A, "/projects/my/inbox",
                        {"untriaged": "true", "page_size": 100})
        assert _named(armed, {r["id"] for r in got["rows"]}) == {
            "a_open", "a_secret", "both2"}


class TestSubjectMe:
    async def test_load_reads_the_callers_rows_only(self, armed):
        a = await _ok(armed, A, "/projects/analytics/load", {"subject": "me"})
        assert {p["assignee"] for p in a["people"]} == {A}
        # a_open, a_secret, both and both2; the archive is out
        assert a["total_tasks"] == 4
        b = await _ok(armed, B, "/projects/analytics/load", {"subject": "me"})
        assert {p["assignee"] for p in b["people"]} == {B}
        # b_open, b_blocked, a_waits, secret_waits, both and both2
        assert b["total_tasks"] == 6

    async def test_stuck_throughput_and_finished_answer_for_me(self, armed):
        stuck = await _ok(armed, A, "/projects/analytics/stuck", {"subject": "me"})
        assert stuck["overdue_total"] == 1
        assert stuck["blocked_total"] == 1
        everyone = await _ok(armed, A, "/projects/analytics/stuck")
        assert everyone["blocked_total"] == 2
        for path in ("/projects/analytics/throughput", "/projects/analytics/finished"):
            await _ok(armed, A, path, {"subject": "me"})

    async def test_an_address_is_a_422(self, armed):
        for path in ("/projects/analytics/stuck", "/projects/analytics/load",
                     "/projects/analytics/throughput", "/projects/analytics/finished",
                     "/projects/analytics/outlook"):
            r = await _get(armed, A, path, {"subject": B})
            assert r.status_code == 422, (path, r.text)

    async def test_outlook_refuses_a_subject(self, armed):
        r = await _get(armed, A, "/projects/analytics/outlook", {"subject": "me"})
        assert r.status_code == 422
        assert "not available for a person or team scope" in r.text


class TestTheLevel:
    async def test_the_tree_and_the_node_carry_the_level(self, armed):
        tree = await _ok(armed, A, "/projects/tree")
        levels = {n["name"]: n["level"] for n in tree["rows"]}
        assert levels["Team"] == "space"
        node = await _ok(armed, A, f"/projects/nodes/{armed.p.team.id}")
        assert node["level"] == "space"
