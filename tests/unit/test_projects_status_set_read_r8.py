"""WS-42 PST-1 — one read for the owner, the count and the exceptions (R8).

Spec: ``project-docs/specs/projects_settings.md`` §9.4 and §7.1 (PST-1).

``GET /projects/nodes/{id}/status-set`` names the owner of the set
(``owner_kind``), counts the projects that use it (``used_by``) and the boards
that an edit changes (``reach``). It lists the projects below the owner that
keep their own set (``overrides``, each with a ``path`` and its own
``used_by``), and counts the ones the caller cannot see (``hidden_overrides``).

**Hermetic half** (no database): the path rule and the shape of the count SQL.

**R8 half.** The REAL ``/projects`` router runs as the non-privileged role of
the phase-4 rehearsal, on a promoted catalog with two organizations, so FORCE
RLS binds every read. A hermetic fake agrees with any SQL it gets.

The tree of org A (``*`` = owns its set, ``[F]`` = a folder, ``(sealed)`` =
sealed by D63, so no member can see it)::

    Space Alpha*
    ├── Plain one ── Plain one a
    ├── Own one* ──┬── Own one a
    │              ├── Own one b ── Deep own* ── Deep kid
    │              └── Own folder [F]
    ├── Secret parent ── Hidden own* ── Hidden own a
    ├── HIDDEN PARENT NAME ── Granted ──┬── Visible own*
    │                                   └── Granted kid
    ├── Folder F [F] ── Behind folder
    ├── SEALED OWN* (sealed)
    └── SEALED MID (sealed) ── Own under seal*

``hidden@`` holds one grant, on ``Granted``. Org B adds two rows whose parent
is ``Space Alpha``. The seed skips the trigger of migration 161, so the rows
keep org B. Only RLS keeps them out of org A's count.

The users of the space's set that hold lanes: Plain one, Plain one a, Secret
parent, HIDDEN PARENT NAME, Granted, Granted kid, Behind folder and SEALED MID.
That is 8. Folder F holds no lanes, and the walk goes through it.

Mutations (R7), each turns the named test red. ``A`` is
``routes/projects/admin.py`` and ``C`` is ``routes/projects/core.py``.

* M1. ``C`` ``_STATUS_USERS_SQL``: take ``NOT p.owns_statuses`` out of the
  recursive arm. ``test_case_1_the_space_owns_the_set``,
  ``test_case_3_an_ancestor_project_owns_the_set`` and four more.
* M2. ``A`` ``_status_set_reach``: list every breakaway, not only the visible
  ones. ``test_a_hidden_override_is_counted_and_never_named``.
* M3. ``A`` ``visible_path``: keep each name. ``test_a_visible_override_under_a_hidden_parent``.
* M5. ``A`` ``_status_set_reach``: ``_seen`` reads any visible node of the
  chain, not the breakaway itself. ``test_a_sealed_breakaway_is_hidden_even_from_an_admin``.
* M6. ``C`` ``_STATUS_USERS_SQL``: count a folder. ``test_a_folder_holds_no_lanes``.
* M4. A read that RLS does not bind. ``test_the_tenant_seed_has_teeth`` runs the
  same SQL as the superuser and counts org B's rows, so
  ``test_tenant_b_changes_nothing_in_tenant_a`` passes because of RLS.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_projects_status_set_read_r8.py -v -rs
"""

from __future__ import annotations

import contextlib
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from fastapi import Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
ELIDED = "…"


# ── Hermetic ────────────────────────────────────────────────────────────────


def test_the_path_starts_at_the_first_visible_name() -> None:
    """§9.4: a LEADING hidden run leaves no mark. A hidden run after a visible
    name is one mark, so a reader sees that something sits between."""
    from gateway.routes.projects.admin import ELIDED as SERVER_ELIDED
    from gateway.routes.projects.admin import visible_path

    assert SERVER_ELIDED == ELIDED
    assert visible_path([("Space", True), ("Product", True)]) == ["Space", "Product"]
    assert visible_path([("Space", False), ("Secret", False), ("Granted", True)]) == [
        "Granted"]
    assert visible_path([("A", False), ("B", True), ("C", False), ("D", True)]) == [
        "B", ELIDED, "D"]
    # The breakaway itself is not in the chain, and it is visible. So a hidden
    # run at the END sits between a visible name and the row's own name.
    assert visible_path([("A", True), ("B", False), ("C", False)]) == ["A", ELIDED]
    assert visible_path([("A", False)]) == []
    assert visible_path([]) == []


def test_a_project_named_with_the_mark_is_not_a_hidden_run() -> None:
    from gateway.routes.projects.admin import visible_path

    assert visible_path([(ELIDED, True), ("Hidden", False)]) == [ELIDED, ELIDED]
    assert visible_path([("Hidden", False), (ELIDED, True)]) == [ELIDED]


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """An R8 suite that CI does not list still skips in silence when the
    database gate is disarmed. Match the argument line, not a mention."""
    workflow = (_ROOT / ".github" / "workflows" / "pr-check.yml").read_text(encoding="utf-8")
    assert re.search(
        r"^\s+tests/unit/test_projects_status_set_read_r8\.py \\?$", workflow, re.M,
    )


def test_the_count_stops_at_an_owner_in_the_recursive_arm() -> None:
    """The shape that M1 breaks, read from the SQL itself."""
    from gateway.routes.projects.core import _STATUS_USERS_SQL

    recursive = _STATUS_USERS_SQL.split("UNION ALL", 1)[1].split(")\nSELECT", 1)[0]
    assert "NOT p.owns_statuses" in recursive
    tail = _STATUS_USERS_SQL.split(")\nSELECT", 1)[1]
    assert "owns_statuses" not in tail
    assert re.search(r"GROUP BY owner_id", tail)
    # A folder is walked through, and left out of the count.
    assert "folder" not in recursive
    assert "<> 'folder'" in tail


# ── R8: the world ───────────────────────────────────────────────────────────

SEE_ALL = ("feature:projects", "data:org:read")
ADMIN = (*SEE_ALL, "projects:settings:write")


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
            f"/projects/nodes/{project_id}/status-set", headers={"x-test-as": key},
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
                base_url="http://status-set.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


def _project(c: Any, org: str, name: str, *, parent: str | None, owns: bool,
             kind: str = "project", sealed: bool = False) -> str:
    pid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
        "parent_project_id, status, kind, sealed_at) "
        "VALUES (:id, :o, :n, 'seed@statusset.test', :own, CAST(:par AS uuid), 'active',"
        " :kind, CASE WHEN :sealed THEN now() END)"),
        {"id": pid, "o": org, "n": name, "own": owns, "par": parent, "kind": kind,
         "sealed": sealed})
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
    ns.admin, ns.hidden = "admin@statusset.test", "hidden@statusset.test"
    ns.b_admin = "admin-b@statusset.test"
    with promoted.admin_engine.begin() as c:
        _user(c, a, ns.admin)
        _user(c, a, ns.hidden)
        _user(c, b, ns.b_admin)
        ns.space = _project(c, a, "Space Alpha", parent=None, owns=True)
        ns.plain = _project(c, a, "Plain one", parent=ns.space, owns=False)
        ns.plain_a = _project(c, a, "Plain one a", parent=ns.plain, owns=False)
        ns.own1 = _project(c, a, "Own one", parent=ns.space, owns=True)
        ns.own1_a = _project(c, a, "Own one a", parent=ns.own1, owns=False)
        ns.own1_b = _project(c, a, "Own one b", parent=ns.own1, owns=False)
        ns.deep = _project(c, a, "Deep own", parent=ns.own1_b, owns=True)
        ns.deep_kid = _project(c, a, "Deep kid", parent=ns.deep, owns=False)
        ns.secret = _project(c, a, "Secret parent", parent=ns.space, owns=False)
        ns.own2 = _project(c, a, "Hidden own", parent=ns.secret, owns=True)
        ns.own2_a = _project(c, a, "Hidden own a", parent=ns.own2, owns=False)
        ns.hidden_parent = _project(c, a, "HIDDEN PARENT NAME", parent=ns.space, owns=False)
        ns.granted = _project(c, a, "Granted", parent=ns.hidden_parent, owns=False)
        ns.own3 = _project(c, a, "Visible own", parent=ns.granted, owns=True)
        ns.granted_kid = _project(c, a, "Granted kid", parent=ns.granted, owns=False)
        ns.own_folder = _project(
            c, a, "Own folder", parent=ns.own1, owns=False, kind="folder")
        ns.folder = _project(c, a, "Folder F", parent=ns.space, owns=False, kind="folder")
        ns.behind = _project(c, a, "Behind folder", parent=ns.folder, owns=False)
        # D63: a sealed project is hidden from every member, an admin too.
        ns.sealed_own = _project(c, a, "SEALED OWN", parent=ns.space, owns=True, sealed=True)
        ns.sealed_mid = _project(c, a, "SEALED MID", parent=ns.space, owns=False, sealed=True)
        ns.under_seal = _project(c, a, "Own under seal", parent=ns.sealed_mid, owns=True)
        c.execute(text(
            "INSERT INTO pm_project_grants (organization_id, project_id, subject, created_by) "
            "VALUES (:o, :p, :s, 'seed@statusset.test')"),
            {"o": a, "p": ns.granted, "s": ns.hidden})
        ns.b_space = _project(c, b, "B space", parent=None, owns=True)

    # Org B rows UNDER org A's space. Migration 161's trigger copies the org of
    # the parent, so a normal insert cannot make one. The superuser seed turns
    # the trigger off for this one transaction, so the rows keep org B. Only
    # RLS then keeps them out of org A's reads.
    with promoted.admin_engine.begin() as c:
        c.execute(text("SET LOCAL session_replication_role = replica"))
        ns.b_intruder = _project(c, b, "B INTRUDER", parent=ns.space, owns=False)
        ns.b_override = _project(c, b, "B OVERRIDE", parent=ns.space, owns=True)
    with promoted.admin_engine.connect() as c:
        orgs = dict(c.execute(text(
            "SELECT name, organization_id::text FROM pm_projects "
            "WHERE name IN ('B INTRUDER', 'B OVERRIDE')")).all())
    assert orgs == {"B INTRUDER": b, "B OVERRIDE": b}, orgs

    ns.admin_u = _principal(ns.admin, a, perms=ADMIN)
    ns.hidden_u = _principal(ns.hidden, a, perms=("feature:projects",))
    ns.b_admin_u = _principal(ns.b_admin, b, perms=ADMIN)
    return ns


def _by_name(body: dict) -> dict[str, dict]:
    return {o["name"]: o for o in body["overrides"]}


# ── R8: the three cases ─────────────────────────────────────────────────────


@_DB_GATE
async def test_case_1_the_space_owns_the_set(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.space)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["owns"] is True and body["owner_id"] == w.space
    assert body["owner_kind"] == "space"
    # The 8 of the module docstring. Own one and each node under it use
    # another set, and Folder F holds no lanes.
    assert body["used_by"] == 8
    # A team space shows no board, so its own lanes reach no board.
    assert body["reach"] == 8
    assert [o["name"] for o in body["overrides"]] == [
        "Hidden own", "Own one", "Own under seal", "Visible own"]
    rows = _by_name(body)
    assert rows["Own one"] == {
        "id": w.own1, "name": "Own one", "path": ["Space Alpha"], "used_by": 2}
    assert rows["Hidden own"]["path"] == ["Space Alpha", "Secret parent"]
    assert rows["Hidden own"]["used_by"] == 1
    assert rows["Visible own"]["path"] == ["Space Alpha", "HIDDEN PARENT NAME", "Granted"]
    assert rows["Visible own"]["used_by"] == 0
    # SEALED OWN keeps its own set, and nobody can see it.
    assert body["hidden_overrides"] == 1


@_DB_GATE
async def test_case_2_a_project_uses_the_space_set(world) -> None:
    w = world
    async with _session(w.p) as call:
        body = (await call.read(w.admin_u, w.plain_a)).json()
    assert body["owns"] is False and body["owner_id"] == w.space
    assert body["owner_name"] == "Space Alpha" and body["owner_kind"] == "space"
    # "Shared with N": the owner and the other users, which equals used_by.
    assert body["used_by"] == 8 and body["reach"] == 8
    assert [o["name"] for o in body["overrides"]] == [
        "Hidden own", "Own one", "Own under seal", "Visible own"]


@_DB_GATE
async def test_case_3_an_ancestor_project_owns_the_set(world) -> None:
    w = world
    async with _session(w.p) as call:
        owner = (await call.read(w.admin_u, w.own1)).json()
        user = (await call.read(w.admin_u, w.own1_a)).json()
    for body in (owner, user):
        assert body["owner_id"] == w.own1 and body["owner_kind"] == "project"
        # Own one a and Own one b. Deep own and Deep kid use another set, and
        # Own folder holds no lanes.
        assert body["used_by"] == 2
        # An edit changes the board of Own one too: 3 boards, not 2.
        assert body["reach"] == 3
        assert body["overrides"] == [{
            "id": w.deep, "name": "Deep own",
            "path": ["Space Alpha", "Own one", "Own one b"], "used_by": 1}]
        assert body["hidden_overrides"] == 0
    assert owner["owns"] is True and user["owns"] is False


@_DB_GATE
async def test_a_nested_owner_is_its_own_count(world) -> None:
    """Deep own is not in the space's list or count, and it has its own."""
    w = world
    async with _session(w.p) as call:
        space = (await call.read(w.admin_u, w.space)).json()
        deep = (await call.read(w.admin_u, w.deep_kid)).json()
    assert "Deep own" not in _by_name(space)
    assert deep["owner_id"] == w.deep and deep["owner_kind"] == "project"
    assert deep["used_by"] == 1 and deep["reach"] == 2 and deep["overrides"] == []


@_DB_GATE
async def test_a_folder_holds_no_lanes(world) -> None:
    """A folder under an owner does not count. A project under a folder does,
    because the walk goes through the folder (M6)."""
    w = world
    async with _session(w.p) as call:
        own1 = (await call.read(w.admin_u, w.own1)).json()
        behind = (await call.read(w.admin_u, w.behind)).json()
    # Own folder sits under Own one, and the count stays 2.
    assert own1["used_by"] == 2
    # Behind folder uses the space's set through Folder F, and it counts.
    assert behind["owner_id"] == w.space and behind["used_by"] == 8


# ── R8: visibility ──────────────────────────────────────────────────────────


@_DB_GATE
async def test_a_hidden_override_is_counted_and_never_named(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.hidden_u, w.granted)
        refused = await call.read(w.hidden_u, w.space)
    assert res.status_code == 200, res.text
    body = res.json()
    # The full count: every project that the edit changes.
    assert body["used_by"] == 8 and body["reach"] == 8
    # Own one, Hidden own, SEALED OWN and Own under seal keep their own sets,
    # and this member can see none of them.
    assert body["hidden_overrides"] == 4
    assert [o["name"] for o in body["overrides"]] == ["Visible own"]
    for name in ("Own one", "Hidden own", "Secret parent", "Deep own", "SEALED",
                 "Own under seal"):
        assert name not in res.text, name
    # The member cannot open the space itself.
    assert refused.status_code == 404


@_DB_GATE
async def test_a_visible_override_under_a_hidden_parent(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.hidden_u, w.own3)
    body = res.json()
    assert body["owns"] is True and body["owner_kind"] == "project"
    async with _session(w.p) as call:
        granted = (await call.read(w.hidden_u, w.granted)).json()
    row = _by_name(granted)["Visible own"]
    # §9.4: the path starts at the first ancestor the member can see.
    assert row["path"] == ["Granted"]
    assert "HIDDEN PARENT NAME" not in str(granted["overrides"])
    assert "Space Alpha" not in str(granted["overrides"])


@_DB_GATE
async def test_a_hidden_run_between_two_visible_names_is_one_mark(world) -> None:
    """The admin sees Space Alpha and Own under seal, and not SEALED MID
    between them. The path keeps the space and marks the hidden part."""
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.space)
    assert _by_name(res.json())["Own under seal"]["path"] == ["Space Alpha", ELIDED]
    assert "SEALED MID" not in res.text


@_DB_GATE
async def test_a_sealed_breakaway_is_hidden_even_from_an_admin(world) -> None:
    """D63: the admin sees the space and its chain, and not SEALED OWN. A rule
    that asks any node of the chain lists it (M5). The breakaway itself
    decides."""
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.space)
    body = res.json()
    assert body["hidden_overrides"] == 1
    assert "SEALED OWN" not in res.text
    assert w.sealed_own not in res.text


# ── R8: the tenant ──────────────────────────────────────────────────────────


@_DB_GATE
async def test_tenant_b_changes_nothing_in_tenant_a(world) -> None:
    w = world
    async with _session(w.p) as call:
        res = await call.read(w.admin_u, w.space)
        from_b = await call.read(w.b_admin_u, w.space)
        b_own = (await call.read(w.b_admin_u, w.b_space)).json()
    body = res.json()
    # The two org B rows sit under the space, and neither counts or shows.
    assert body["used_by"] == 8
    assert body["hidden_overrides"] == 1
    assert [o["name"] for o in body["overrides"]] == [
        "Hidden own", "Own one", "Own under seal", "Visible own"]
    assert "B INTRUDER" not in res.text and "B OVERRIDE" not in res.text
    # Org B cannot read org A's space at all.
    assert from_b.status_code == 404
    # Org B's own space counts nothing of org A.
    assert b_own["owner_kind"] == "space" and b_own["used_by"] == 0
    assert b_own["reach"] == 0
    assert b_own["overrides"] == [] and b_own["hidden_overrides"] == 0


@_DB_GATE
def test_the_tenant_seed_has_teeth(world) -> None:
    """The same SQL, on the superuser that RLS does not bind, DOES count org
    B's rows. So the test above passes because of RLS, and not because the
    seed put nothing in the way (M4)."""
    from gateway.routes.projects.admin import _OVERRIDES_BELOW_SQL
    from gateway.routes.projects.core import _STATUS_USERS_SQL

    w = world
    with w.p.admin_engine.connect() as c:
        used = c.execute(text(_STATUS_USERS_SQL), {"owners": [w.space]}).one()
        below = [r.name for r in c.execute(text(_OVERRIDES_BELOW_SQL), {"pid": w.space})]
    assert used.used_by == 9
    assert "B OVERRIDE" in below
