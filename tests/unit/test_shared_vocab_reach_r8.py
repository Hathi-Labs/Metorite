"""HRD-9a: the space of a node, and N, from one read (R8).

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-9a, class C5,
and §8 Q2 (N counts hidden projects) and Q5 (no name of a hidden space).

``GET /projects/nodes/{id}/vocabulary/reach`` answers ``root_id``,
``root_name``, ``root_kind`` and ``projects``. ``projects`` is N: each
project of the root's tree that holds tasks. ``core.vocabulary_reach`` and
``core._TREE_PROJECTS_SQL`` count it.

**R8.** The REAL ``/projects`` router runs as the non-privileged role of the
phase-4 rehearsal, on a promoted catalog with two organizations, so FORCE RLS
binds every read. A hermetic fake agrees with any SQL that it gets.

The trees (``*`` = owns its statuses, ``[F]`` = a folder, ``(sealed)`` =
sealed by D63, ``(filed)`` = archived)::

    Space Alpha* (a team space)
    ├── Plain one ── Plain one a
    ├── Own one* ── Own one a
    ├── Folder F [F] ── Behind folder
    ├── HIDDEN PARENT NAME ── Granted ── Granted kid
    ├── SEALED (sealed)
    └── Filed one (filed)

    My tasks* (the personal root of admin@)
    ├── Side project
    └── My folder [F]

``hidden@`` holds one grant, on ``Granted``. Org B adds two rows whose parent
is ``Space Alpha``. The seed turns the trigger of migration 161 off, so the
rows keep org B.

N of Space Alpha: Plain one, Plain one a, Own one, Own one a, Behind folder,
HIDDEN PARENT NAME, Granted, Granted kid, SEALED and Filed one. That is 10.
The team space itself holds no tasks, and Folder F holds none. N of My tasks
is 2: the personal root holds the Inbox, and Side project.

Mutations (R7), each turns the named test red. ``C`` is
``routes/projects/core.py``:

* M1. ``C`` ``vocabulary_reach``: add the root always.
  ``test_a_team_space_root_does_not_count`` and four more.
* M2. ``C`` ``_TREE_PROJECTS_SQL``: stop the walk at ``owns_statuses`` (or
  reuse ``_STATUS_USERS_SQL``). ``test_a_subproject_that_owns_its_statuses_counts``.
* M3. ``C`` ``_TREE_PROJECTS_SQL``: count a folder. ``test_a_folder_does_not_count``.
* M4. ``C`` ``_TREE_PROJECTS_SQL``: drop the organization fence of the
  recursive arm. ``test_the_tenant_seed_has_teeth``.
* M5. ``C`` ``vocabulary_reach``: name the root that the caller cannot see.
  ``test_a_hidden_project_counts_and_the_hidden_space_has_no_name``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_shared_vocab_reach_r8.py -v -rs
"""

from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import Request
from sqlalchemy import text

from tests.unit import _r8_lists as r8_lists
from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

SEE_ALL = ("feature:projects", "data:org:read")


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed."""
    assert "tests/unit/test_shared_vocab_reach_r8.py" in r8_lists.must_run()


# ── R8: the world ───────────────────────────────────────────────────────────


def _principal(email: str, org: str, *, perms: tuple[str, ...]) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org,
    )


def _app(users: dict[str, Any]) -> Any:
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes.projects import router as projects_router

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client, self.users = client, users

    async def read(self, user: Any, project_id: str) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await self.client.get(
            f"/projects/nodes/{project_id}/vocabulary/reach", headers={"x-test-as": key},
        )


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
                base_url="http://vocab-reach.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


def _project(c: Any, org: str, name: str, *, parent: str | None, owns: bool = False,
             kind: str = "project", sealed: bool = False, filed: bool = False,
             personal: str | None = None) -> str:
    pid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
        "parent_project_id, status, kind, sealed_at, archived_at, personal_owner) "
        "VALUES (:id, :o, :n, 'seed@vocabreach.test', :own, CAST(:par AS uuid), 'active',"
        " :kind, CASE WHEN :sealed THEN now() END, CASE WHEN :filed THEN now() END, :po)"),
        {"id": pid, "o": org, "n": name, "own": owns, "par": parent, "kind": kind,
         "sealed": sealed, "filed": filed, "po": personal})
    return pid


def _user(c: Any, org: str, email: str) -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "o": org})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    a, b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, a=a, b=b)
    ns.admin, ns.hidden = "admin@vocabreach.test", "hidden@vocabreach.test"
    ns.b_admin = "admin-b@vocabreach.test"
    with promoted.admin_engine.begin() as c:
        _user(c, a, ns.admin)
        _user(c, a, ns.hidden)
        _user(c, b, ns.b_admin)
        ns.space = _project(c, a, "Space Alpha", parent=None, owns=True)
        ns.plain = _project(c, a, "Plain one", parent=ns.space)
        ns.plain_a = _project(c, a, "Plain one a", parent=ns.plain)
        ns.own1 = _project(c, a, "Own one", parent=ns.space, owns=True)
        ns.own1_a = _project(c, a, "Own one a", parent=ns.own1)
        ns.folder = _project(c, a, "Folder F", parent=ns.space, kind="folder")
        ns.behind = _project(c, a, "Behind folder", parent=ns.folder)
        ns.hidden_parent = _project(c, a, "HIDDEN PARENT NAME", parent=ns.space)
        ns.granted = _project(c, a, "Granted", parent=ns.hidden_parent)
        ns.granted_kid = _project(c, a, "Granted kid", parent=ns.granted)
        # D63: a sealed project is hidden from every member, an admin too.
        ns.sealed = _project(c, a, "SEALED", parent=ns.space, sealed=True)
        ns.filed = _project(c, a, "Filed one", parent=ns.space, filed=True)
        c.execute(text(
            "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by) "
            "VALUES (:o, :p, :s, 'seed@vocabreach.test')"),
            {"o": a, "p": ns.granted, "s": ns.hidden})
        # The personal tree of admin@: each node carries `personal_owner`.
        ns.mine = _project(c, a, "My tasks", parent=None, owns=True, personal=ns.admin)
        ns.side = _project(c, a, "Side project", parent=ns.mine, personal=ns.admin)
        ns.my_folder = _project(c, a, "My folder", parent=ns.mine, kind="folder",
                                personal=ns.admin)
        ns.b_space = _project(c, b, "B space", parent=None, owns=True)
        ns.b_child = _project(c, b, "B child", parent=ns.b_space)

    # Org B rows UNDER org A's space. Migration 161's trigger copies the org of
    # the parent, so a normal insert cannot make one. The superuser seed turns
    # the trigger off for this one transaction, so the rows keep org B.
    with promoted.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        _project(c, b, "B INTRUDER", parent=ns.space)
        _project(c, b, "B OWNER", parent=ns.space, owns=True)
    with promoted.admin_engine.connect() as c:
        orgs = dict(c.execute(text(
            "SELECT name, organization_id::text FROM pm_projects "
            "WHERE name IN ('B INTRUDER', 'B OWNER')")).all())
    assert orgs == {"B INTRUDER": b, "B OWNER": b}, orgs

    ns.admin_u = _principal(ns.admin, a, perms=SEE_ALL)
    ns.hidden_u = _principal(ns.hidden, a, perms=("feature:projects",))
    ns.b_admin_u = _principal(ns.b_admin, b, perms=SEE_ALL)
    return ns


# ── R8: the count ───────────────────────────────────────────────────────────


@_DB_GATE
async def test_a_team_space_root_does_not_count(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.space)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "root_id": w.space, "root_name": "Space Alpha", "root_kind": "space",
        # The 10 of the module docstring. The space itself holds no tasks.
        "projects": 10,
    }


@_DB_GATE
async def test_each_node_of_the_tree_reads_the_root_and_the_same_n(world) -> None:
    w = world
    async with _session(w.p) as call:
        bodies = [(await call.read(w.admin_u, pid)).json()
                  for pid in (w.plain_a, w.own1_a, w.behind, w.folder)]
    for body in bodies:
        assert body["root_id"] == w.space and body["root_name"] == "Space Alpha", body
        assert body["projects"] == 10, body


@_DB_GATE
async def test_a_folder_does_not_count(world) -> None:
    """Folder F does not count. Behind folder does, because the walk goes
    through the folder (M3). My folder does not count in the personal tree."""
    w = world
    async with _session(w.p) as call:
        mine = (await call.read(w.admin_u, w.side)).json()
        space = (await call.read(w.admin_u, w.behind)).json()
    assert space["projects"] == 10
    assert mine["projects"] == 2


@_DB_GATE
async def test_a_personal_root_counts(world) -> None:
    """A personal root holds the Inbox (``core.holds_lanes``), so it counts."""
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.mine)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "root_id": w.mine, "root_name": "My tasks", "root_kind": "personal", "projects": 2,
    }


@_DB_GATE
async def test_a_subproject_that_owns_its_statuses_counts(world) -> None:
    """A vocabulary row is the root's, so the walk does not stop at a node
    that owns its statuses (M2). The status count would leave out Own one
    and Own one a, and say 8."""
    w = world
    async with _session(w.p) as call:
        own = (await call.read(w.admin_u, w.own1)).json()
        status = (await call.client.get(
            f"/projects/nodes/{w.space}/status-set",
            headers={"x-test-as": str(id(w.admin_u))})).json()
    assert own["root_id"] == w.space and own["projects"] == 10
    # The status count of the same space stops at Own one: the two differ.
    assert status["used_by"] == 8


@_DB_GATE
async def test_a_hidden_project_counts_and_the_hidden_space_has_no_name(world) -> None:
    """Q2: N counts each project, the ones that the member cannot see too.
    Q5: the answer gives no name of a space that the member cannot see."""
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.hidden_u, w.granted)
        refused = await call.read(w.hidden_u, w.space)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "root_id": w.space, "root_name": None, "root_kind": "space", "projects": 10,
    }
    assert "Space Alpha" not in res.text
    # The member cannot open the space itself.
    assert refused.status_code == 404


@_DB_GATE
async def test_a_sealed_node_counts_and_cannot_be_read(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.sealed)
    assert res.status_code == 404


# ── R8: the tenant ──────────────────────────────────────────────────────────


@_DB_GATE
async def test_tenant_b_changes_nothing_in_tenant_a(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.space)
        from_b = await call.read(w.b_admin_u, w.space)
        from_a = await call.read(w.admin_u, w.b_space)
        b_own = (await call.read(w.b_admin_u, w.b_space)).json()
    # The two org B rows sit under the space, and neither counts.
    assert res.json()["projects"] == 10
    # A node of another organization answers 404, both ways.
    assert from_b.status_code == 404 and from_a.status_code == 404
    # Org B's own space counts its own child only.
    assert b_own == {
        "root_id": w.b_space, "root_name": "B space", "root_kind": "space", "projects": 1,
    }


@_DB_GATE
def test_the_tenant_seed_has_teeth(world) -> None:
    """The superuser, which RLS does not bind, sees org B's two rows under
    the space. A walk with no organization fence counts them (12). The real
    SQL counts 10 even there, because its recursive arm names the root's
    organization (M4). So the route's 10 holds by two fences."""
    from gateway.routes.projects.core import _TREE_PROJECTS_SQL, MAX_DEPTH

    w = world
    fence = "c.organization_id = t.organization_id AND "
    assert fence in _TREE_PROJECTS_SQL
    params = {"root": w.space, "max_depth": MAX_DEPTH}
    with w.p.admin_engine.connect() as c:
        under = c.execute(text(
            "SELECT count(*) FROM pm_projects "
            " WHERE parent_project_id = CAST(:s AS uuid) AND organization_id = CAST(:b AS uuid)"),
            {"s": w.space, "b": w.b}).scalar()
        naive = c.execute(text(_TREE_PROJECTS_SQL.replace(fence, "")), params).scalar()
        fenced = c.execute(text(_TREE_PROJECTS_SQL), params).scalar()
    assert under == 2
    assert naive == 12
    assert fenced == 10
