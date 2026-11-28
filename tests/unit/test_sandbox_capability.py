"""SBX-F1: the run capability of the sandbox door (SBX-2, Metorite task 3801).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.2 decisions 4 to 9, and
done-when 1, 2 (the R8 half), 3, 8 and 9.

**Database-free (always runs).**

* Done-when 1: with ``SANDBOX_DOOR_ORGS`` empty, a well-formed capability
  gets 401, and a spy on the session factory and on ``tenant_redis`` records
  no call.
* Done-when 8 and the rules of ``mint``: each bad argument raises
  ``ValueError`` before any SQL. SBX-3a (§12.5 decision 6, done-when 4):
  a write scope only in the ``run`` phase, only of the door, and only with
  an app id and a version.
* Done-when 9: the 601st call of one capability gets 429 with
  ``Retry-After``, and a Redis outage gets 503.
* The source scan: no module outside ``tests/`` calls ``mint``, except the
  step engine of SBX-4b1, its one caller.

**R8 (a real database, as a NOBYPASSRLS role, on a phase-4-promoted catalog
of its own).** The real ``mint``, ``lookup``, ``revoke``, ``revoke_step``
and ``sweep``, and the real CRM and Projects routers behind the real door.

* Done-when 2: a capability of member M of org A reads ``GET /crm/leads``
  and ``GET /projects/tasks``. Both answer 200 with M's rows, and no row of
  org B, which holds rows of both kinds.
* Done-when 3: the capability gets 401 after ``revoke``, after
  ``revoke_step``, and after its row moves back by 15 minutes and 1 second.
* Done-when 8: a direct INSERT of a smoke row with a write scope fails on
  the CHECK.
* SBX-4b1 done-when 19 (``agent_sandbox.md`` §12.4): the step engine revokes
  the capability of each smoke round, after a pass and after a failure.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_sandbox_capability.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from acb_auth import agent_tokens, sandbox_capabilities
from fastapi.testclient import TestClient

from tests.unit import _branch_zero_tripwire as tripwire
from tests.unit import _sandbox_door_kit as kit

_ROOT = Path(__file__).resolve().parents[2]

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


@pytest.fixture
def armed_tripwire():
    yield tripwire
    tripwire.assert_clean()


def _spy_reads(monkeypatch) -> list[str]:
    from acb_common import db, tenant_redis

    reads: list[str] = []

    def factory(*_a, **_k):
        reads.append("session")
        raise AssertionError("the door read the database")

    def redis(*_a, **_k):
        reads.append("redis")
        raise AssertionError("the door called Redis")

    monkeypatch.setattr(db, "get_session_factory", factory)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", redis)
    return reads


# ── done-when 1: the flag off reads nothing ────────────────────────────────


@pytest.mark.parametrize("flag", ["", " ", ","])
def test_with_the_flag_empty_a_capability_is_401_with_no_read(
        monkeypatch, armed_tripwire, flag):
    from acb_common import get_settings

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    monkeypatch.setattr(get_settings(), "sandbox_door_orgs", flag)
    reads = _spy_reads(monkeypatch)
    with TestClient(door.app) as client:
        answer = client.get("/crm/leads", headers=kit.headers())
        public = client.get(kit.PUBLIC_PATH, headers=kit.headers())
    assert answer.status_code == 401
    assert public.status_code == 401
    assert reads == [], "no database read and no Redis call"
    assert door.ran == [] and door.after == [None, None]


# ── done-when 8 and the rules of mint ──────────────────────────────────────


def _good(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "org": kit.ORG_A, "member": kit.MEMBER, "run_id": "run-1",
        "thread_id": "thread-1", "phase": "run", "read_scopes": ("crm:read",),
    }
    args.update(over)
    return args


@pytest.mark.parametrize("over", [
    {"ttl_s": 901},
    {"ttl_s": 0},
    {"ttl_s": -5},
    {"ttl_s": True},
    {"phase": "deploy"},
    {"read_scopes": ("knowledge:read",)},
    {"read_scopes": ("crm:write",)},
    {"phase": "smoke", "write_scopes": ("crm:write",)},
    {"phase": "run", "write_scopes": ("crm:write",)},
    {"phase": "build", "write_scopes": ("projects:write",)},
    {"org": "not-a-uuid"},
    {"org": ""},
    {"member": " "},
    {"run_id": ""},
    {"thread_id": ""},
    {"app_id": "not-a-uuid"},
    {"version": "3"},
])
def test_mint_raises_before_any_sql(monkeypatch, over):
    ran: list[object] = []

    async def spy(work):
        ran.append(work)
        raise AssertionError("mint reached SQL")

    monkeypatch.setattr(agent_tokens, "_run", spy)
    with pytest.raises(ValueError):
        asyncio.run(sandbox_capabilities.mint(**_good(**over)))
    assert ran == []


_APP = "99999999-9999-4999-8999-999999999999"


@pytest.mark.parametrize("over", [
    # SBX-3a (§12.5 decision 6): a write scope in the smoke phase,
    {"phase": "smoke", "write_scopes": ("crm:write",), "app_id": _APP, "version": 1},
    # in the build phase,
    {"phase": "build", "write_scopes": ("projects:write",), "app_id": _APP, "version": 1},
    # a write scope that is not a scope of the door,
    {"phase": "run", "write_scopes": ("knowledge:write",), "app_id": _APP, "version": 1},
    {"phase": "run", "write_scopes": ("crm:read",), "app_id": _APP, "version": 1},
    # and a write scope with no app_id or no version.
    {"phase": "run", "write_scopes": ("crm:write",), "version": 1},
    {"phase": "run", "write_scopes": ("crm:write",), "app_id": _APP},
])
def test_mint_refuses_a_bad_write_scope(monkeypatch, over):
    """Done-when 4. Each bad write scope raises ``ValueError`` before any SQL."""
    ran: list[object] = []

    async def spy(work):
        ran.append(work)
        raise AssertionError("mint reached SQL")

    monkeypatch.setattr(agent_tokens, "_run", spy)
    with pytest.raises(ValueError):
        asyncio.run(sandbox_capabilities.mint(**_good(**over)))
    assert ran == []


def test_mint_takes_a_write_scope_in_the_run_phase_with_an_app(monkeypatch):
    seen: dict[str, Any] = {}

    class _Session:
        async def execute(self, statement, params):
            seen.update(params)
            return SimpleNamespace(one=lambda: SimpleNamespace(
                created_at="c", expires_at="e"))

    async def run(work):
        return await work(_Session())

    monkeypatch.setattr(agent_tokens, "_run", run)
    asyncio.run(sandbox_capabilities.mint(**_good(
        write_scopes=("crm:write", "projects:write"), app_id=_APP, version=2)))
    assert seen["write_scopes"] == ["crm:write", "projects:write"]
    assert seen["phase"] == "run" and seen["version"] == 2


def test_the_token_has_its_shape_and_only_its_hash_is_bound(monkeypatch):
    seen: dict[str, Any] = {}

    class _Session:
        async def execute(self, statement, params):
            seen.update(params)
            return SimpleNamespace(one=lambda: SimpleNamespace(
                created_at="c", expires_at="e"))

    async def run(work):
        return await work(_Session())

    monkeypatch.setattr(agent_tokens, "_run", run)
    minted = asyncio.run(sandbox_capabilities.mint(**_good(ttl_s=900)))
    assert sandbox_capabilities.well_formed(minted.token)
    assert seen["hash"] == agent_tokens.hash_token(minted.token)
    assert minted.token not in repr(seen)
    assert seen["ttl_s"] == 900 and seen["write_scopes"] == []


def test_lookup_refuses_a_bad_shape_before_any_read(monkeypatch):
    async def spy(work):
        raise AssertionError("lookup read the database")

    monkeypatch.setattr(agent_tokens, "_run", spy)
    for raw in ("", "msbx_", "msbx_" + "A" * 42, kit.RAW + "\n", "Bearer " + kit.RAW,
                "xsbx_" + "A" * 43, "msbx_" + "A" * 42 + "="):
        assert asyncio.run(sandbox_capabilities.lookup(raw)) is None, raw


# ── done-when 9: the call cap and a Redis outage ───────────────────────────


def test_the_601st_call_is_429_with_retry_after(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    with TestClient(door.app) as client:
        first = client.get("/crm/leads", headers=kit.headers())
        assert first.status_code == 200
        (key,) = state.redis.counts
        assert state.redis.counts[key] == 1
        state.redis.counts[key] = 599
        cap = client.get("/crm/leads", headers=kit.headers())
        over = client.get("/crm/leads", headers=kit.headers())
    assert cap.status_code == 200, "the 600th call passes"
    assert over.status_code == 429
    assert over.json() == {"detail": "sandbox_call_cap"}
    assert int(over.headers["retry-after"]) >= 1
    assert door.ran == ["GET /crm/leads", "GET /crm/leads"]
    assert door.after[-1] is None


def test_redis_down_is_503_and_fails_closed(monkeypatch, armed_tripwire):
    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    state = kit.arm_door(monkeypatch)
    state.redis.down = True
    with TestClient(door.app) as client:
        answer = client.get("/crm/leads", headers=kit.headers())
    assert answer.status_code == 503
    assert answer.headers["retry-after"] == "2"
    assert door.ran == [] and door.after == [None]


def test_a_redis_client_that_cannot_be_built_is_503(monkeypatch, armed_tripwire):
    from acb_common import tenant_redis

    door = kit.make_app(monkeypatch, tripwire=armed_tripwire)
    kit.arm_door(monkeypatch)

    def broken(**_k):
        raise ValueError("bad redis_url")

    monkeypatch.setattr(tenant_redis, "get_tenant_redis", broken)
    with TestClient(door.app) as client:
        answer = client.get("/crm/leads", headers=kit.headers())
    assert answer.status_code == 503
    assert door.ran == []


# ── R5e: only tests mint until SBX-4 ───────────────────────────────────────

#: The modules outside ``tests/`` that may call ``mint``. SBX-4b1 adds the
#: step engine, its ONE caller (``agent_sandbox.md`` §12.4 decision 4).
_MINT_CALLERS: frozenset[str] = frozenset({
    "packages/acb_skills/acb_skills/user_tool_build.py",
})


def _calls_mint(tree: ast.AST) -> bool:
    """True when the module imports ``mint`` or reaches ``.mint`` on the store,
    under any alias: ``from acb_auth import sandbox_capabilities as sc``,
    ``import acb_auth.sandbox_capabilities as sc`` or the dotted path."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").endswith("sandbox_capabilities") and any(
                    a.name in ("mint", "*") for a in node.names):
                return True
            for a in node.names:
                if a.name == "sandbox_capabilities":
                    aliases.add(a.asname or a.name)
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith("sandbox_capabilities") and a.asname:
                    aliases.add(a.asname)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "mint":
            target = node.value
            if isinstance(target, ast.Name) and target.id in aliases:
                return True
            if isinstance(target, ast.Attribute) and target.attr == "sandbox_capabilities":
                return True
    return False


def _mint_callers(root: Path = _ROOT) -> set[str]:
    found: set[str] = set()
    store = (root / "packages/acb_auth/acb_auth/sandbox_capabilities.py").resolve()
    for top in ("apps", "packages", "scripts", "evals", "deploy"):
        base = root / top
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            parts = set(path.parts)
            if "node_modules" in parts or ".venv" in parts or path.resolve() == store:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "mint" not in text or "sandbox_capabilities" not in text:
                continue
            if _calls_mint(ast.parse(text)):
                found.add(path.relative_to(root).as_posix())
    return found


def test_no_module_outside_tests_calls_mint():
    assert _mint_callers() <= _MINT_CALLERS


def test_the_mint_scan_turns_red(tmp_path):
    """The scan is not vacuous: a planted caller under ``apps`` is found."""
    fake = tmp_path / "apps" / "x"
    fake.mkdir(parents=True)
    (fake / "caller.py").write_text(
        "from acb_auth import sandbox_capabilities\n"
        "async def go():\n    await sandbox_capabilities.mint(org='o')\n",
        encoding="utf-8")
    (fake / "caller2.py").write_text(
        "from acb_auth.sandbox_capabilities import mint\n", encoding="utf-8")
    (fake / "caller3.py").write_text(
        "import acb_auth.sandbox_capabilities as sc\nsc.mint\n", encoding="utf-8")
    (fake / "caller4.py").write_text(
        "from acb_auth import sandbox_capabilities as sc\nsc.mint\n", encoding="utf-8")
    (fake / "caller5.py").write_text(
        "import acb_auth.sandbox_capabilities\nacb_auth.sandbox_capabilities.mint\n",
        encoding="utf-8")
    (fake / "caller6.py").write_text(
        "from acb_auth.sandbox_capabilities import mint as m\n", encoding="utf-8")
    (fake / "reader.py").write_text(
        "from acb_auth import sandbox_capabilities as sc\nsc.lookup\n", encoding="utf-8")
    assert _mint_callers(tmp_path) == {
        f"apps/x/caller{n}.py" for n in ("", "2", "3", "4", "5", "6")}


# ═══════════════════════════════════════════════════════════════════════════
# R8: the store and the door on a real database, as a NOBYPASSRLS role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_sbxdoor"
_APP_PW = "sbxdoor_scratch_pw"
M = "m-member@sbxdoor.test"
B_MEMBER = "b-member@sbxdoor.test"
OWNER = "owner@sbxdoor.test"


class _Seeder:
    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org

    def one(self, sql: str, **params) -> Any:
        from sqlalchemy import text

        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.org})
            row = c.execute(text(sql), params).first()
        return row[0] if row is not None else None

    def member(self, email: str, permissions: list[str]) -> None:
        identity = self.one(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e)"
            " ON CONFLICT DO NOTHING RETURNING id", e=email)
        if identity is None:
            identity = self.one("SELECT id FROM user_identity WHERE email = :e", e=email)
        self.one("INSERT INTO org_membership (organization_id, user_id, status)"
                 " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active') RETURNING user_id",
                 o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id", e=email, o=self.org)
        role = self.one("INSERT INTO org_role (organization_id, slug, display_name)"
                        " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
                        o=self.org, s="sbx-" + email.split("@")[0])
        for permission in permissions:
            self.one("INSERT INTO org_role_permission (role_id, permission)"
                     " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                     r=str(role), p=permission)
        self.one("INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
                 " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def leads(self, names: list[str]) -> list[str]:
        self.one("INSERT INTO crm_settings (organization_id, access) VALUES"
                 " (CAST(:o AS uuid), 'everyone') RETURNING organization_id", o=self.org)
        status = self.one(
            "INSERT INTO crm_lead_statuses (name, position, type, organization_id)"
            " VALUES ('New', 0, 'open', CAST(:o AS uuid)) RETURNING id", o=self.org)
        return [str(self.one(
            "INSERT INTO crm_leads (organization_id, lead_name, status_id)"
            " VALUES (CAST(:o AS uuid), :n, CAST(:s AS uuid)) RETURNING id",
            o=self.org, n=name, s=str(status))) for name in names]

    def task(self, title: str, grant_to: str, number: int) -> str:
        project = str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
            " timezone, owns_statuses) VALUES (:n, 'active', 'manual', :a,"
            " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id",
            n=title + " project", a=OWNER, o=self.org))
        self.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
                 " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
                 " RETURNING project_id", p=project, s=grant_to, a=OWNER, o=self.org)
        status = self.one(
            "INSERT INTO pm_task_statuses (project_id, name, color, position, category,"
            " organization_id) VALUES (CAST(:p AS uuid), 'To do', 'gray', 0, 'todo',"
            " CAST(:o AS uuid)) RETURNING id", p=project, o=self.org)
        return str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number) VALUES (:t, CAST(:p AS uuid),"
            " CAST(:p AS uuid), CAST(:s AS uuid), :a, CAST(:o AS uuid), :n) RETURNING id",
            t=title, p=project, s=str(status), a=OWNER, o=self.org, n=number))


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants with CRM leads and tasks, and a
    NOBYPASSRLS app role."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database

    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_sbxdoor"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "sbxdoor-a"), (org_b, "sbxdoor-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :slug, :slug)"), {"id": oid, "slug": slug})
    a, b = _Seeder(eng, org_a), _Seeder(eng, org_b)
    a.member(M, ["feature:crm", "feature:projects"])
    b.member(B_MEMBER, ["feature:crm", "feature:projects"])
    w = SimpleNamespace(
        admin=eng, org_a=org_a, org_b=org_b,
        leads_a=a.leads(["A lead one", "A lead two"]),
        leads_b=b.leads(["B lead"]),
        task_a=a.task("A task", M, 1),
        task_b=b.task("B task", B_MEMBER, 1001),
        app_dsn=admin_url.set(database=dedicated, username=_APP_ROLE, password=_APP_PW)
        .render_as_string(hide_password=False),
    )
    yield w
    eng.dispose()
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                       "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": dedicated})
        c.execute(text(f'DROP DATABASE IF EXISTS "{dedicated}"'))
    maint.dispose()


@pytest.fixture
def door(monkeypatch, world):
    """The real door, with the flag on for every org and Redis a stand-in."""
    from acb_auth import access as access_module
    from acb_auth import deps
    from acb_auth.access import invalidate as invalidate_access
    from acb_common import get_settings, tenant_redis

    settings = get_settings()
    monkeypatch.setattr(settings, "sandbox_door_orgs", "*")
    monkeypatch.setattr(settings, "self_serve_signup_enabled", "")
    # The CRM kill switch of the box (CRM-0): both orgs reach /crm.
    monkeypatch.setattr(settings, "crm_enabled", True)
    monkeypatch.setattr(settings, "crm_orgs", "*")
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "sbxdoor.test")
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    store = kit.FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: store)
    armed = tripwire.arm(monkeypatch, deps)
    try:
        yield SimpleNamespace(world=world, redis=store, armed=armed)
    finally:
        invalidate_access()
    tripwire.assert_clean()


def _gateway(armed) -> Any:
    from acb_auth import deps, require_authenticated
    from fastapi import FastAPI
    from gateway.main import TenantScopeMiddleware
    from gateway.routes import crm as crm_package
    from gateway.routes.projects import router as projects_router
    from gateway.sandbox_door import SandboxDoorLimits

    app = FastAPI(dependencies=[require_authenticated(public=frozenset())])
    app.include_router(crm_package.router)
    app.include_router(projects_router)
    app.add_middleware(SandboxDoorLimits)
    app.add_middleware(TenantScopeMiddleware)
    app.dependency_overrides[deps.get_current_user] = tripwire.current_user_override(armed)
    return app


async def _scoped(world, work):
    from tests.unit._tenant_ladder import tenant_engine_scope

    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _get(door, token: str, path: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=_gateway(door.armed))
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            return await client.get(path, headers={"X-Sandbox-Capability": token})

    return await _scoped(door.world, go)


async def _mint(world, **over: Any) -> Any:
    args: dict[str, Any] = {
        "org": world.org_a, "member": M, "run_id": "run-r8", "thread_id": "thread-r8",
        "phase": "run", "read_scopes": ("crm:read", "projects:read"),
    }
    args.update(over)
    return await _scoped(world, lambda: sandbox_capabilities.mint(**args))


def _ids(body: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(body, dict):
        for value in body.values():
            out |= _ids(value)
    elif isinstance(body, list):
        for value in body:
            out |= _ids(value)
    elif isinstance(body, str) and len(body) == 36 and body.count("-") == 4:
        out.add(body)
    return out


@_DB_GATE
class TestTheAppRoleIsNotPrivileged:
    def test_the_role_has_no_bypassrls(self, world):
        from sqlalchemy import create_engine, text

        eng = create_engine(world.app_dsn)
        try:
            with eng.connect() as c:
                row = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles "
                                     "WHERE rolname = current_user")).first()
        finally:
            eng.dispose()
        assert row is not None and not row[0] and not row[1]


@_DB_GATE
class TestDoneWhen2TheReadsOfTheMember:
    async def test_crm_leads_and_projects_tasks_answer_with_the_members_rows(self, door):
        w = door.world
        with w.admin.connect() as c:
            from sqlalchemy import text

            b_leads = c.execute(text("SELECT count(*) FROM crm_leads WHERE organization_id"
                                     " = CAST(:o AS uuid)"), {"o": w.org_b}).scalar()
            b_tasks = c.execute(text("SELECT count(*) FROM pm_tasks WHERE organization_id"
                                     " = CAST(:o AS uuid)"), {"o": w.org_b}).scalar()
        assert b_leads >= 1 and b_tasks >= 1, "org B holds rows of both kinds"

        minted = await _mint(w)
        leads = await _get(door, minted.token, "/crm/leads?page_size=100")
        assert leads.status_code == 200, leads.text
        seen = _ids(leads.json())
        assert set(w.leads_a) <= seen
        assert not set(w.leads_b) & seen, "a lead of org B"
        assert sum(door.redis.counts.values()) == 1, "call_n is 1 after one call"

        tasks = await _get(door, minted.token, "/projects/tasks?page_size=100")
        assert tasks.status_code == 200, tasks.text
        task_ids = _ids(tasks.json())
        assert w.task_a in task_ids
        assert w.task_b not in task_ids, "a task of org B"

    async def test_a_capability_of_org_b_reads_only_org_b(self, door):
        w = door.world
        minted = await _mint(w, org=w.org_b, member=B_MEMBER, run_id="run-b")
        leads = await _get(door, minted.token, "/crm/leads")
        assert leads.status_code == 200, leads.text
        seen = _ids(leads.json())
        assert set(w.leads_b) <= seen and not set(w.leads_a) & seen

    async def test_a_member_of_another_org_on_a_row_is_401(self, door):
        """The row says org A, and the member belongs to org B."""
        w = door.world
        minted = await _mint(w, member=B_MEMBER, run_id="run-cross")
        answer = await _get(door, minted.token, "/crm/leads")
        assert answer.status_code == 401


@_DB_GATE
class TestDoneWhen3TheEnd:
    async def test_revoke_ends_the_capability(self, door):
        w = door.world
        minted = await _mint(w, run_id="run-revoke")
        assert (await _get(door, minted.token, "/crm/leads")).status_code == 200
        digest = agent_tokens.hash_token(minted.token)
        assert await _scoped(w, lambda: sandbox_capabilities.revoke(digest)) is True
        assert (await _get(door, minted.token, "/crm/leads")).status_code == 401
        assert await _scoped(w, lambda: sandbox_capabilities.revoke(digest)) is False

    async def test_revoke_step_ends_each_capability_of_the_step(self, door):
        w = door.world
        one = await _mint(w, run_id="run-step", phase="build")
        two = await _mint(w, run_id="run-step", phase="build")
        other = await _mint(w, run_id="run-step", phase="run")
        count = await _scoped(w, lambda: sandbox_capabilities.revoke_step(
            org=w.org_a, run_id="run-step", phase="build"))
        assert count == 2
        assert (await _get(door, one.token, "/crm/leads")).status_code == 401
        assert (await _get(door, two.token, "/crm/leads")).status_code == 401
        assert (await _get(door, other.token, "/crm/leads")).status_code == 200

    async def test_a_row_moved_back_by_15_minutes_and_1_second_is_401(self, door):
        from sqlalchemy import text

        w = door.world
        minted = await _mint(w, run_id="run-old")
        assert (await _get(door, minted.token, "/crm/leads")).status_code == 200
        with w.admin.begin() as c:
            moved = c.execute(text(
                "UPDATE sandbox_capabilities SET created_at = created_at - interval"
                " '15 minutes 1 second', expires_at = expires_at - interval"
                " '15 minutes 1 second' WHERE token_sha256 = :h"),
                {"h": agent_tokens.hash_token(minted.token)}).rowcount
        assert moved == 1
        assert (await _get(door, minted.token, "/crm/leads")).status_code == 401
        assert await _scoped(w, lambda: sandbox_capabilities.lookup(minted.token)) is None

    async def test_the_database_clock_sets_a_ttl_of_exactly_900_seconds(self, door):
        from sqlalchemy import text

        w = door.world
        minted = await _mint(w, run_id="run-clock", ttl_s=900)
        with w.admin.connect() as c:
            gap = c.execute(text(
                "SELECT EXTRACT(EPOCH FROM expires_at - created_at) FROM sandbox_capabilities"
                " WHERE token_sha256 = :h"), {"h": agent_tokens.hash_token(minted.token)}
            ).scalar()
        assert float(gap) == 900.0
        found = await _scoped(w, lambda: sandbox_capabilities.lookup(minted.token))
        assert found is not None and 1 <= found.seconds_left <= 900
        assert found.organization_id == w.org_a and found.member_email == M

    async def test_the_table_never_holds_the_token(self, door):
        from sqlalchemy import text

        w = door.world
        minted = await _mint(w, run_id="run-store")
        with w.admin.connect() as c:
            rows = c.execute(text("SELECT * FROM sandbox_capabilities")).all()
        assert minted.token not in repr(rows)
        assert minted.token[5:] not in repr(rows)

    async def test_sweep_deletes_rows_1_day_after_their_end(self, door):
        from sqlalchemy import text

        w = door.world
        old_hash = "0" * 64
        with w.admin.begin() as c:
            c.execute(text(
                "INSERT INTO sandbox_capabilities (token_sha256, organization_id,"
                " member_email, run_id, thread_id, phase, created_at, expires_at)"
                " VALUES (:h, CAST(:o AS uuid), :m, 'run-sweep', 't', 'run',"
                " now() - interval '2 days', now() - interval '2 days' + interval '5 minutes')"),
                {"h": old_hash, "o": w.org_a, "m": M})
        fresh = await _mint(w, run_id="run-sweep-fresh")
        assert await _scoped(w, sandbox_capabilities.sweep) >= 1
        with w.admin.connect() as c:
            left = {r[0] for r in c.execute(text(
                "SELECT token_sha256 FROM sandbox_capabilities"))}
        assert old_hash not in left
        assert agent_tokens.hash_token(fresh.token) in left


@_DB_GATE
class TestTheMemberPurge:
    async def test_the_purge_deletes_her_capabilities_in_the_bound_org_only(self, door):
        """The purge entry of ``admin/members.py`` runs as the NOBYPASSRLS app
        role with org A bound. The table is RLS-exempt, so only the clause
        keeps org B's row. Mutation: drop ``organization_id = <bound org>``
        from the entry, and org B's row goes too."""
        from acb_common.db import tenant_session
        from gateway.routes.admin.members import _PURGE_DELETES, _purge_params
        from sqlalchemy import text

        w = door.world
        mine = await _mint(w, run_id="run-purge-a")
        theirs = await _mint(w, org=w.org_b, run_id="run-purge-b")
        [rows] = [r for r in _PURGE_DELETES if r.table == "sandbox_capabilities"]
        params = _purge_params(rows, uid=str(uuid.uuid4()), email=M.strip().lower())

        async def purge() -> tuple[int, int]:
            async with tenant_session(w.org_a) as db:
                n = (await db.execute(text(rows.count_sql), params)).scalar_one()
                gone = await db.execute(text(rows.delete_sql), params)
                return int(n), int(gone.rowcount)

        counted, deleted = await _scoped(w, purge)
        assert counted == deleted >= 1
        with w.admin.connect() as c:
            left = set(c.execute(text(
                "SELECT token_sha256 FROM sandbox_capabilities WHERE run_id LIKE 'run-purge-%'"
            )).scalars().all())
        assert agent_tokens.hash_token(mine.token) not in left
        assert agent_tokens.hash_token(theirs.token) in left


@_DB_GATE
class TestDoneWhen8TheChecks:
    @pytest.mark.parametrize(("phase", "writes", "gap"), [
        ("smoke", "{crm:write}", "5 minutes"),
        ("run", "{}", "15 minutes 1 second"),
        ("deploy", "{}", "5 minutes"),
    ])
    def test_a_direct_insert_that_breaks_a_rule_fails_on_the_check(
            self, world, phase, writes, gap):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError, match="sandbox_capabilities_"), \
                world.admin.begin() as c:
            c.execute(text(
                "INSERT INTO sandbox_capabilities (token_sha256, organization_id,"
                " member_email, run_id, thread_id, phase, write_scopes, expires_at)"
                " VALUES (:h, CAST(:o AS uuid), :m, 'run-x', 't', :p,"
                " CAST(:w AS text[]), now() + CAST(:g AS interval))"),
                {"h": "1" * 64, "o": world.org_a, "m": M, "p": phase, "w": writes, "g": gap})

    def test_a_bad_hash_fails_on_the_check(self, world):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError, match="sandbox_capabilities_hash_shape"), \
                world.admin.begin() as c:
            c.execute(text(
                "INSERT INTO sandbox_capabilities (token_sha256, organization_id,"
                " member_email, run_id, thread_id, phase, expires_at)"
                " VALUES (:h, CAST(:o AS uuid), 'm', 'r', 't', 'run', now())"),
                {"h": kit.RAW, "o": world.org_a})

    async def test_the_app_role_can_mint_and_read_the_exempt_table(self, door):
        """The table has no RLS policy. The NOBYPASSRLS role reads it through
        its grant, with no tenant bound."""
        minted = await _mint(door.world, run_id="run-grant")
        found = await _scoped(door.world, lambda: sandbox_capabilities.lookup(minted.token))
        assert found is not None


@_DB_GATE
async def test_the_smoke_round_revokes_its_capability(door, monkeypatch, tmp_path):
    """SBX-4b1 done-when 19 (§12.4 decisions 4 and 7), R8. The real ``mint``,
    ``lookup`` and ``revoke_step`` of the step engine on a real database. The
    token is live while the smoke command runs, and dead at the end of its
    own round, before the next round mints: a pass, an exit 1 and a sandbox
    fault. Mutation: skip ``revoke_step`` at the end of a round, so only the
    end of the build revokes -> red."""
    from acb_skills import user_tool_build as ute
    from orchestrator import sandbox_broker as sb

    from tests.unit._sandbox_broker_fakes import bound_run, configure_env
    from tests.unit.test_user_tool_build import FakeBroker, package_files, write_package

    env = configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr(env["settings"], "maf_coding_scope", "tools:*")
    monkeypatch.setattr(env["settings"], "sandbox_egress_enabled", True)
    monkeypatch.setattr(env["settings"], "sandbox_egress_door_upstream", "10.0.0.5:8080")
    world = door.world
    tokens: list[str] = []
    live: list[bool] = []
    modes = iter(["pass", "exit", "fault"])

    async def go() -> list[Any]:
        with bound_run(world.org_a, member=M, thread=str(uuid.uuid4())):
            broker = FakeBroker(sb.read_tool_binding().tool_data)
            monkeypatch.setattr(ute, "_broker", lambda: broker)
            mode = {"now": "pass"}

            async def run(command: str, env_file: Any) -> Any:
                if command != ute.SMOKE_COMMAND:
                    return None
                token = env_file["METORITE_API_TOKEN"]
                tokens.append(token)
                live.append(await sandbox_capabilities.lookup(token) is not None)
                (broker.tool_data / "outputs" / "result.json").write_text(
                    '{"pages": 2}', encoding="utf-8")
                if mode["now"] == "exit":
                    return sb.ExecResult(1, "", 0, False, 0.1)
                if mode["now"] == "fault":
                    raise sb.SandboxUnavailable("the container is gone")
                return None

            broker.on_exec = run
            results = []
            between: list[Any] = []
            async with ute.start_build({"crm:read"}) as build:
                write_package(broker.tool_data / "pkg", package_files(lock=""))
                for name in modes:
                    mode["now"] = name
                    results.append(await build.run_smoke())
                    # Before the next round mints: this round's token is dead.
                    between.append(await sandbox_capabilities.lookup(tokens[-1]))
            assert between == [None, None, None], (
                "a smoke capability lived into the next round")
            return [results, [await sandbox_capabilities.lookup(t) for t in tokens]]

    results, after = await _scoped(world, go)
    assert [r.outcome for r in results] == ["succeeded", "failed", "failed"]
    assert len(tokens) == 3 and live == [True, True, True]
    assert after == [None, None, None], "a smoke capability outlived its round"
