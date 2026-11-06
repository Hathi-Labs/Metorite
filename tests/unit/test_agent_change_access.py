"""WS-55 EA-17c-1 — Change access: the member changes the grant of an agent.

Spec: ``project-docs/specs/external_agent_access.md`` §10.8 (D-EA-13 rules 1
to 6, "The route (EA-17c-1)", the fences of EA-17c-1) and the EA-17c-1 row
of §15 · Metorite task 1813.

``PATCH /agent-access/connections/{id}`` runs on the REAL gateway app
(``gateway.main.app``) through ``httpx.ASGITransport``, on a real database,
as a NON-privileged role, on a phase-4-promoted catalog of its own (R8). The
next agent call runs through the REAL edge app, whose gateway client points
at the same gateway app.

* **C1, the flag.** An organization off ``agent_change_access_orgs`` gets
  404 for each request, a malformed id or body too, and nothing changes.
* **C2, the 404s.** A connection of another member, of another
  organization, a revoked one, an expired one and an unknown id. An admin
  with ``integrations:manage`` gets 404 on another member's connection too.
* **C3, the 422s.** No scope, a missing field, an extra field, a change to
  ``offline_access``, a turn-on above the scope of the live token, an
  unknown scope, a new project that the member does not see, a personal
  project that is not a root, a list over the cap, an id in both lists and a
  switch that is not a JSON boolean. A body over the cap gets 413.
* **C4, the next call.** A narrowing of the scopes makes the edge refuse the
  very next agent call. A turn-back-on of a scope inside the token scope is
  live on the next call. A narrowing of the pins and the left-out list
  narrows the next ``list_tasks``.
* **C5, the ceiling.** Two live tokens with different scopes give their
  intersection, ``projects:write`` covers ``projects:read``, and a
  connection with no live token has an empty ceiling. A dead token plays no
  part. The ceiling binds a turn-on only, so a narrowing and a change of
  only the marks pass with no live token. The list and the answer carry
  ``requested_scopes`` and ``ceiling_scopes``.
* **C5b, the stale marks.** A mark that the member no longer sees (a
  deleted or sealed project) never blocks a save. A new unseen id does.
* **C6, the store.** The scoped UPDATE names the member and the
  organization, and a store call for another member's connection changes
  nothing. A real change writes one audit row with the grant before and
  after. A body that equals the row writes nothing, logs nothing and audits
  nothing. A copy of an id drops out.

Named mutations that turn this file red (spec §10.8): a scope above the
token's scope, a change to the connection of another member, a write that
skips the project check, and a route that answers with the flag off. The
fifth, a missing ``sameOrigin`` check, is red in
``workbench/control_plane/src/app/api/agent-access/connections/relay.test.ts``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_change_access.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import (
    AgentScope,
    EffectiveAccess,
    UserContext,
    UserRole,
    agent_oauth,
    agent_tokens,
    require_authenticated,
)
from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from acb_common.env_guard import is_platform_env
from acb_common.settings import Settings
from agent_edge import app as edge_app_module
from agent_edge import gateway_client
from fastapi import HTTPException
from gateway import flag_reload
from gateway.routes import agent_access
from gateway.routes.projects import tree
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
        "pgvector. A skip here is not a pass; CI must set it. ⚠️ Do NOT export "
        "it as DATABASE_URL — see test_org_provisioning.py's docstring."
    ),
)

ISSUER = "https://mcp.metorite.com"
RESOURCE = "https://mcp.metorite.com/mcp"
KEY = "agent-change-access-signing-key-" + "c" * 24
INTERNAL = "agent-change-access-internal-token"
CLIENT = "https://client.example/meta.json"
ORIGIN = "https://client.example"

DOMAIN = "agentchange.test"
MEMBER = f"member@{DOMAIN}"
OTHER = f"other@{DOMAIN}"
ADMIN = f"admin@{DOMAIN}"
MEMBER_B = f"member-b@{DOMAIN}"
OWNER = f"owner@{DOMAIN}"

_APP_ROLE = "acb_app_agentchange"
_APP_PW = "agentchange_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

LIST = "/agent-access/connections"

READ = agent_oauth.PROJECTS_READ
WRITE = agent_oauth.PROJECTS_WRITE
CONTEXT = agent_oauth.CONTEXT_READ
OFFLINE = agent_oauth.OFFLINE_ACCESS


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


def _array(values: tuple[str, ...] | list[str]) -> str:
    return "{" + ",".join(values) + "}"


class _Seeder:
    """Admin writes with the tenant GUC set, as in test_agent_connections_r8.py."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org
        self.number = 0

    def one(self, sql: str, **params) -> Any:
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
        self.one(
            "INSERT INTO org_membership (organization_id, user_id, status)"
            " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active') RETURNING user_id",
            o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id",
            e=email, o=self.org)
        role = self.one(
            "INSERT INTO org_role (organization_id, slug, display_name)"
            " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
            o=self.org, s="ch-" + email.split("@")[0])
        for permission in permissions:
            self.one(
                "INSERT INTO org_role_permission (role_id, permission)"
                " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                r=str(role), p=permission)
        self.one(
            "INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
            " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def project(self, name: str, *, parent: str | None = None,
                personal_owner: str | None = None,
                grant_to: str | None = None) -> str:
        pid = str(self.one(
            "INSERT INTO pm_projects (name, status, source, created_by,"
            " organization_id, timezone, parent_project_id, owns_statuses,"
            " personal_owner) VALUES (:n, 'active', 'manual', :a, CAST(:o AS uuid),"
            " 'Asia/Kolkata', CAST(:parent AS uuid), true, :po) RETURNING id",
            n=name, a=OWNER, o=self.org, parent=parent, po=personal_owner))
        if grant_to:
            self.one(
                "INSERT INTO pm_project_grants (project_id, subject, created_by,"
                " organization_id) VALUES (CAST(:p AS uuid), :s, :a,"
                " CAST(:o AS uuid)) RETURNING project_id",
                p=pid, s=grant_to, a=OWNER, o=self.org)
        return pid

    def task(self, project: str, root: str, title: str) -> str:
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
        return str(self.one(
            "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
            " created_by, organization_id, task_number) VALUES (:t,"
            " CAST(:p AS uuid), CAST(:r AS uuid), CAST(:s AS uuid), :a,"
            " CAST(:o AS uuid), :n) RETURNING id",
            t=title, p=project, r=root, s=str(status), a=OWNER, o=self.org,
            n=self.number))

    def connection(
        self, member: str, *,
        scopes: tuple[str, ...] = (READ,),
        tokens: tuple[str | None, ...] = ("projects:read",),
        requested: tuple[str, ...] | None = (READ, WRITE, CONTEXT),
        pins: tuple[str, ...] = (),
        left_out: tuple[str, ...] = (),
        include_personal: bool = False,
        revoked: bool = False,
        expired: bool = False,
    ) -> tuple[str, list[str]]:
        """A connection and one access token for each scope of *tokens*:
        ``(id, raw tokens)``."""
        cid = str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email,"
            " client_id, client_name, client_origin, scopes, pinned_projects,"
            " include_personal, expires_at, revoked_at, left_out_projects,"
            " requested_scopes) VALUES"
            " (CAST(:o AS uuid), :m, :client, 'Client', :origin,"
            " CAST(:scopes AS text[]), CAST(:pins AS uuid[]), :personal,"
            " now() + CAST(:life AS interval),"
            " CASE WHEN :revoked THEN now() ELSE NULL END,"
            " CAST(:left_out AS uuid[]), CAST(:requested AS text[])) RETURNING id",
            o=self.org, m=member, client=CLIENT, origin=ORIGIN,
            scopes=_array(scopes), pins=_array(pins), personal=include_personal,
            life="-1 hour" if expired else "1 day", revoked=revoked,
            left_out=_array(left_out),
            requested=None if requested is None else _array(requested)))
        raws = []
        for scope in tokens:
            raw = agent_tokens.new_token()
            self.one(
                "INSERT INTO agent_token (organization_id, connection_id, kind,"
                " token_hash, expires_at, client_id, resource, scope) VALUES"
                " (CAST(:o AS uuid), CAST(:c AS uuid), 'access', :h,"
                " now() + interval '1 hour', :client, :r, :scope) RETURNING id",
                o=self.org, c=cid, h=agent_tokens.hash_token(raw), client=CLIENT,
                r=RESOURCE, scope=scope)
            raws.append(raw)
        return cid, raws

    def dead_token(self, connection_id: str, scope: str, state: str) -> None:
        """A refresh token of *connection_id* that is spent, revoked or
        expired (*state* is ``used``, ``revoked`` or ``expired``)."""
        raw = agent_tokens.new_token()
        used = "now()" if state == "used" else "NULL"
        revoked = "now()" if state == "revoked" else "NULL"
        expires = ("now() - interval '1 hour'" if state == "expired"
                   else "now() + interval '1 hour'")
        self.one(
            "INSERT INTO agent_token (organization_id, connection_id, kind,"
            " token_hash, expires_at, used_at, revoked_at, client_id, resource,"
            f" scope) VALUES (CAST(:o AS uuid), CAST(:c AS uuid), 'refresh', :h,"
            f" {expires}, {used}, {revoked}, :client, :r, :scope) RETURNING id",
            o=self.org, c=connection_id, h=agent_tokens.hash_token(raw),
            client=CLIENT, r=RESOURCE, scope=scope)

    def version(self, connection_id: str) -> str:
        """The ``xmin`` of the row. Each write of the row changes it."""
        return str(self.one(
            "SELECT CAST(xmin AS text) FROM agent_connection"
            " WHERE id = CAST(:c AS uuid)", c=connection_id))

    def set_sealed(self, project_id: str, sealed: bool) -> None:
        self.one(
            "UPDATE pm_projects SET sealed_at = CASE WHEN :s THEN now() END"
            " WHERE id = CAST(:p AS uuid) RETURNING id", s=sealed, p=project_id)

    def row(self, connection_id: str) -> dict[str, Any]:
        """The grant columns of one connection, as the catalog holds them."""
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.org})
            row = c.execute(text(
                "SELECT scopes, CAST(pinned_projects AS text[]) AS pins,"
                " CAST(left_out_projects AS text[]) AS left_out, include_personal,"
                " requested_scopes, revoked_at IS NOT NULL AS revoked"
                " FROM agent_connection WHERE id = CAST(:c AS uuid)"),
                {"c": connection_id}).one()
        return {
            "scopes": list(row.scopes), "pins": list(row.pins),
            "left_out": list(row.left_out), "personal": row.include_personal,
            "requested": None if row.requested_scopes is None else list(row.requested_scopes),
            "revoked": row.revoked,
        }


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two organizations, the trees and members."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_agentchange"
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

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for org, slug in ((org_a, "agentchange-a"), (org_b, "agentchange-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": org, "s": slug})
    a, b = _Seeder(eng, org_a), _Seeder(eng, org_b)
    a.member(MEMBER, ["feature:projects"])
    a.member(OTHER, ["feature:projects"])
    a.member(ADMIN, ["feature:projects", "feature:integrations", "integrations:manage"])
    b.member(MEMBER_B, ["feature:projects"])

    p: dict[str, str] = {}
    p["team"] = a.project("Team", grant_to=MEMBER)
    p["team_child"] = a.project("Team child", parent=p["team"])
    p["other_team"] = a.project("Other team", grant_to=MEMBER)
    p["hidden"] = a.project("Hidden", grant_to=OTHER)
    p["personal"] = a.project("Mine", personal_owner=MEMBER, grant_to=MEMBER)
    p["personal_child"] = a.project("Mine child", parent=p["personal"],
                                    personal_owner=MEMBER)
    p["b_root"] = b.project("Org B", grant_to=MEMBER_B)
    t = {
        "team": a.task(p["team"], p["team"], "team task"),
        "child": a.task(p["team_child"], p["team"], "child task"),
        "other_team": a.task(p["other_team"], p["other_team"], "other team task"),
        "hidden": a.task(p["hidden"], p["hidden"], "hidden task"),
    }
    w = SimpleNamespace(admin=eng, a=a, b=b, org_a=org_a, org_b=org_b, p=p, t=t)
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def live(monkeypatch, world):
    """The real gateway app, with agent access on and Change access on for
    organization A only."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_access_orgs", "*")
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
    monkeypatch.setattr(settings, "agent_change_access_orgs", world.org_a)
    monkeypatch.setattr(settings, "agent_edge_signing_key", KEY)
    monkeypatch.setattr(settings, "agent_edge_signing_key_previous", "")
    monkeypatch.setattr(settings, "agent_issuer", ISSUER)
    monkeypatch.setattr(settings, "agent_resource", RESOURCE)
    monkeypatch.setattr(settings, "agent_edge_gateway_url", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    assert require_authenticated  # the real guard is on main.app
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


@pytest.fixture(autouse=True)
def audits(monkeypatch) -> list[Any]:
    """Each audit event that a route records, in order. No event reaches a
    database from this file."""
    import acb_audit

    seen: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", seen.append)
    return seen


async def _in_scope(world, work) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _as(world, email: str, method: str, path: str,
              body: Any = None, *, raw: bytes | None = None) -> httpx.Response:
    """One call of the real gateway as the signed-in *email*, as the BFF makes it."""
    from gateway.main import app as gateway_app

    content = raw if raw is not None else (
        None if body is None else json.dumps(body).encode("utf-8"))

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(
                method, path, content=content,
                headers={"Authorization": f"Bearer {INTERNAL}", "X-User-Email": email,
                         "Content-Type": "application/json"})
    return await _in_scope(world, go)


async def _patch(world, email: str, connection_id: str, body: Any = None,
                 **kw: Any) -> httpx.Response:
    return await _as(world, email, "PATCH", f"{LIST}/{connection_id}", body, **kw)


async def _mcp(world, token: str, name: str = "list_tasks",
               arguments: dict[str, Any] | None = None) -> httpx.Response:
    """The next agent call, through the REAL edge app."""
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as client:
            return await client.post(
                "/mcp",
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": name, "arguments": arguments or {}}}),
                headers={"authorization": f"Bearer {token}",
                         "content-type": "application/json",
                         "mcp-protocol-version": "2025-11-25"})
    return await _in_scope(world, go)


async def _task_titles(world, token: str) -> set[str]:
    r = await _mcp(world, token)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["isError"] is False, result
    # A title is text that a person wrote, so the edge wraps it (§9.4).
    return {row["title"]["untrusted_text"] for row in result["structuredContent"]["tasks"]}


def _grant(scopes: list[str] | None = None, *, pins: list[str] | None = None,
           left_out: list[str] | None = None, personal: bool = False) -> dict[str, Any]:
    return {
        "scopes": [READ] if scopes is None else scopes,
        "pinned_projects": pins or [],
        "left_out_projects": left_out or [],
        "include_personal": personal,
    }


def _error(r: httpx.Response) -> str:
    assert r.status_code == 422, r.text
    return r.json()["detail"]["error"]


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the shapes
# ═══════════════════════════════════════════════════════════════════════════


class TestTheShapes:
    """The model, the ceiling rule and the SQL of the scoped write."""

    def test_the_body_forbids_extras_and_names_no_identity(self):
        model = agent_access.ChangeAccessRequest
        assert model.model_config.get("extra") == "forbid"
        assert set(model.model_fields) == {
            "scopes", "pinned_projects", "left_out_projects", "include_personal"}
        assert all(f.is_required() for f in model.model_fields.values()), (
            "a missing list must not read as an empty one")

    @pytest.mark.parametrize("tokens,ceiling", [
        ([], ()),
        (["projects:read"], (READ,)),
        (["projects:write"], (READ, WRITE)),
        (["projects:write offline_access", "projects:read context:read"], (READ,)),
        (["projects:write context:read", "projects:write"], (READ, WRITE)),
        ([None, "projects:read"], ()),
        (["bad\x01token", "projects:read"], ()),
    ])
    def test_the_ceiling_is_the_intersection_with_the_cover_rule(self, tokens, ceiling):
        assert agent_tokens.ceiling_of(tokens) == ceiling

    def test_the_scoped_write_names_the_member_and_the_organization(self):
        """C6. Both statements of the change name the id, the organization
        and the member, with no NULL arm for the member."""
        for sql in (agent_tokens._LOCK_MEMBER_CONNECTION_SQL,
                    agent_tokens._CHANGE_GRANT_SQL):
            flat = " ".join(sql.split())
            assert "c.id = CAST(:connection_id AS UUID)" in flat
            assert "c.organization_id = CAST(:org AS UUID)" in flat
            assert "c.member_email = CAST(:member AS TEXT)" in flat
            assert "CAST(:member AS TEXT) IS NULL" not in flat
            assert "c.revoked_at IS NULL" in flat
        assert "requested_scopes =" not in agent_tokens._CHANGE_GRANT_SQL
        assert "FOR NO KEY UPDATE" in agent_tokens._LOCK_MEMBER_CONNECTION_SQL

    def test_no_signed_agent_reaches_the_route(self):
        """Branch 0 admits only the pairs of the allowlist."""
        pair = ("PATCH", "/agent-access/connections/{connection_id}")
        assert pair not in agent_oauth.MCP_ROUTES
        assert pair not in agent_oauth.MCP_WRITE_ROUTES

    async def test_a_signed_agent_gets_404_before_the_body(self, monkeypatch):
        """Only the member changes a grant (D-EA-13 rule 5). The route never
        reads the body of a signed agent, so ``request`` can be ``None``."""
        monkeypatch.setattr(agent_access, "current_tenant", lambda: str(uuid.uuid4()))
        user = UserContext(
            email=MEMBER, role=UserRole.EMPLOYEE, organization_id=str(uuid.uuid4()),
            access=EffectiveAccess(is_active=True),
            agent=AgentScope(connection_id=str(uuid.uuid4())),
        )
        with pytest.raises(HTTPException) as caught:
            await agent_access.change_my_connection(uuid.uuid4(), None, user)
        assert caught.value.status_code == 404

    def test_the_flag_ships_dark_and_only_the_operator_writes_it(self):
        """The default is empty, a SIGHUP reload applies it
        (``docs/flag_reload.md``), and a tenant cannot write it through
        Integrations (the ``AGENT_`` prefix of ``env_guard``)."""
        assert Settings.model_fields["agent_change_access_orgs"].default == ""
        assert flag_reload.reloadable("AGENT_CHANGE_ACCESS_ORGS")
        assert is_platform_env("AGENT_CHANGE_ACCESS_ORGS")

    def test_the_flag_reader(self, monkeypatch):
        org = str(uuid.uuid4())
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_change_access_orgs", "")
        assert not agent_oauth.change_access_allowed(org)
        monkeypatch.setattr(settings, "agent_change_access_orgs", f" {org.upper()} ")
        assert agent_oauth.change_access_allowed(org)
        assert not agent_oauth.change_access_allowed(str(uuid.uuid4()))
        monkeypatch.setattr(settings, "agent_change_access_orgs", "*")
        assert agent_oauth.change_access_allowed(org)
        assert not agent_oauth.change_access_allowed("not-a-uuid")
        assert not agent_oauth.change_access_allowed(None)


# ═══════════════════════════════════════════════════════════════════════════
# R8: the route on a real database
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheFlag:
    """C1: dark behind ``agent_change_access_orgs``."""

    async def test_an_org_off_the_flag_gets_404_for_each_request(self, live, monkeypatch):
        cid, _ = live.a.connection(MEMBER)
        before = live.a.row(cid)
        monkeypatch.setattr(get_settings(), "agent_change_access_orgs", "")
        for path_id, body, raw in (
                (cid, _grant(), None),
                (cid, None, b"not json"),
                (cid, {**_grant(), "organization_id": live.org_b}, None),
                ("not-a-uuid", _grant(), None),
                (str(uuid.uuid4()), _grant(), None)):
            r = await _patch(live, MEMBER, path_id, body, raw=raw)
            assert r.status_code == 404, (path_id, raw, r.text)
        assert live.a.row(cid) == before

    async def test_another_org_off_the_list_gets_404(self, live):
        """The flag holds org A only. A member of org B gets 404."""
        cid, _ = live.b.connection(MEMBER_B)
        r = await _patch(live, MEMBER_B, cid, _grant())
        assert r.status_code == 404, r.text
        assert live.b.row(cid)["scopes"] == [READ]

    async def test_agent_access_off_gives_404(self, live, monkeypatch):
        cid, _ = live.a.connection(MEMBER)
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        r = await _patch(live, MEMBER, cid, _grant())
        assert r.status_code == 404


@_DB_GATE
class TestTheNotFounds:
    """C2: each connection that the member may not change is 404."""

    async def test_another_members_connection_is_404_and_changes_nothing(self, live):
        cid, tokens = live.a.connection(OTHER, scopes=(READ, CONTEXT),
                                        tokens=("projects:read context:read",))
        before = live.a.row(cid)
        r = await _patch(live, MEMBER, cid, _grant([CONTEXT]))
        assert r.status_code == 404, r.text
        assert live.a.row(cid) == before
        assert (await _mcp(live, tokens[0])).status_code == 200

    async def test_another_members_row_is_404_even_for_a_scope_above_its_ceiling(self, live):
        """No 422 tells a member that the id of another member exists. The
        lock read must filter by the member, not only the UPDATE."""
        cid, _ = live.a.connection(OTHER, scopes=(READ,), tokens=("projects:read",))
        before = live.a.row(cid)
        for body in (_grant([WRITE]), _grant([]), _grant([READ, OFFLINE]),
                     _grant(pins=[live.p["hidden"]])):
            r = await _patch(live, MEMBER, cid, body)
            assert r.status_code == 404, (body, r.status_code, r.text)
        assert live.a.row(cid) == before

    async def test_an_admin_cannot_change_another_members_connection(self, live):
        cid, _ = live.a.connection(OTHER, scopes=(READ, CONTEXT),
                                   tokens=("projects:read context:read",))
        before = live.a.row(cid)
        r = await _patch(live, ADMIN, cid, _grant([CONTEXT]))
        assert r.status_code == 404, r.text
        assert live.a.row(cid) == before

    async def test_another_organizations_connection_is_404(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_change_access_orgs", "*")
        cid, _ = live.b.connection(MEMBER)
        before = live.b.row(cid)
        r = await _patch(live, MEMBER, cid, _grant())
        assert r.status_code == 404, r.text
        assert live.b.row(cid) == before

    @pytest.mark.parametrize("state", ["revoked", "expired"])
    async def test_a_dead_connection_is_404(self, live, state):
        cid, _ = live.a.connection(MEMBER, **{state: True})
        before = live.a.row(cid)
        r = await _patch(live, MEMBER, cid, _grant())
        assert r.status_code == 404, r.text
        assert live.a.row(cid) == before

    async def test_an_unknown_id_is_404_and_a_malformed_one_is_422(self, live):
        assert (await _patch(live, MEMBER, str(uuid.uuid4()), _grant())).status_code == 404
        assert (await _patch(live, MEMBER, "not-a-uuid", _grant())).status_code == 422


@_DB_GATE
class TestTheRefusals:
    """C3: each 422 and the 413. Nothing changes after any of them."""

    async def test_a_grant_with_no_scope_is_422(self, live):
        cid, _ = live.a.connection(MEMBER, scopes=(READ, OFFLINE),
                                   tokens=("projects:read offline_access",))
        before = live.a.row(cid)
        assert _error(await _patch(live, MEMBER, cid, _grant([]))) == "no_scope"
        assert _error(await _patch(live, MEMBER, cid, _grant([OFFLINE]))) == "no_scope"
        assert live.a.row(cid) == before

    @pytest.mark.parametrize("field", [
        "scopes", "pinned_projects", "left_out_projects", "include_personal"])
    async def test_a_missing_field_is_422(self, live, field):
        cid, _ = live.a.connection(MEMBER, pins=(live.p["team"],))
        before = live.a.row(cid)
        body = _grant()
        del body[field]
        r = await _patch(live, MEMBER, cid, body)
        assert r.status_code == 422, r.text
        assert live.a.row(cid) == before

    @pytest.mark.parametrize("extra", [
        {"organization_id": "x"}, {"member_email": OTHER},
        {"requested_scopes": [WRITE]}, {"offline_access": True}])
    async def test_an_extra_field_is_422(self, live, extra):
        cid, _ = live.a.connection(MEMBER)
        before = live.a.row(cid)
        r = await _patch(live, MEMBER, cid, {**_grant(), **extra})
        assert r.status_code == 422, r.text
        assert all("input" not in e for e in r.json()["detail"]), "no input echoed"
        assert live.a.row(cid) == before

    async def test_offline_access_cannot_be_added_or_removed(self, live):
        plain, _ = live.a.connection(MEMBER)
        r = await _patch(live, MEMBER, plain, _grant([READ, OFFLINE]))
        assert _error(r) == "offline_access_fixed"
        assert live.a.row(plain)["scopes"] == [READ]
        held, _ = live.a.connection(MEMBER, scopes=(READ, OFFLINE),
                                    tokens=("projects:read offline_access",))
        r = await _patch(live, MEMBER, held, _grant([READ]))
        assert _error(r) == "offline_access_fixed"
        assert live.a.row(held)["scopes"] == [READ, OFFLINE]

    async def test_a_scope_above_the_token_scope_is_422(self, live):
        """D-EA-13 rule 2, named mutation 1. The agent asked for write, the
        member granted read only, so the token carries read only."""
        cid, _ = live.a.connection(MEMBER, scopes=(READ,), tokens=("projects:read",),
                                        requested=(READ, WRITE))
        r = await _patch(live, MEMBER, cid, _grant([WRITE]))
        assert _error(r) == "scope_above_ceiling"
        assert r.json()["detail"]["scopes"] == [WRITE]
        assert live.a.row(cid)["scopes"] == [READ]
        r = await _patch(live, MEMBER, cid, _grant(["admin:everything"]))
        assert _error(r) == "scope_above_ceiling"
        assert live.a.row(cid)["scopes"] == [READ]

    async def test_a_project_the_member_does_not_see_is_422(self, live):
        """Named mutation 4. The check reads the member's own sight."""
        cid, _ = live.a.connection(MEMBER)
        before = live.a.row(cid)
        for pins, left_out in (([live.p["hidden"]], []),
                               ([], [live.p["hidden"]]),
                               ([live.p["b_root"]], []),
                               ([str(uuid.uuid4())], []),
                               ([live.p["team"], live.p["personal_child"]], [])):
            r = await _patch(live, MEMBER, cid, _grant(pins=pins, left_out=left_out))
            assert _error(r) == "project_not_seen", (pins, left_out)
            assert set(r.json()["detail"]["projects"]) == {*pins, *left_out} - {live.p["team"]}
        assert live.a.row(cid) == before

    async def test_the_cap_is_on_the_model_and_not_the_sight(self, live):
        """51 ids that the member DOES see: only the cap can refuse them."""
        cid, _ = live.a.connection(MEMBER)
        many = [live.p["team"]] * (agent_access._MAX_PINS + 1)
        for body in (_grant(pins=many), _grant(left_out=many)):
            r = await _patch(live, MEMBER, cid, body)
            assert r.status_code == 422, r.text
            assert isinstance(r.json()["detail"], list), "the 422 of the model"
        r = await _patch(live, MEMBER, cid, _grant(pins=[live.p["team"]] * agent_access._MAX_PINS))
        assert r.status_code == 200, r.text

    async def test_an_id_in_both_lists_is_422(self, live):
        cid, _ = live.a.connection(MEMBER)
        before = live.a.row(cid)
        team = live.p["team"]
        r = await _patch(live, MEMBER, cid, _grant(pins=[team, live.p["other_team"]],
                                                   left_out=[team]))
        assert _error(r) == "project_in_both_lists"
        assert r.json()["detail"]["projects"] == [team]
        assert live.a.row(cid) == before

    @pytest.mark.parametrize("value", ["true", 1, 0, "false", None])
    async def test_the_personal_switch_takes_a_json_boolean_only(self, live, value):
        cid, _ = live.a.connection(MEMBER)
        r = await _patch(live, MEMBER, cid, {**_grant(), "include_personal": value})
        assert r.status_code == 422, r.text
        assert live.a.row(cid)["personal"] is False

    async def test_a_list_over_the_cap_is_422_and_a_big_body_413(self, live):
        cid, _ = live.a.connection(MEMBER)
        before = live.a.row(cid)
        many = [str(uuid.uuid4()) for _ in range(agent_access._MAX_PINS + 1)]
        for body in (_grant(pins=many), _grant(left_out=many)):
            r = await _patch(live, MEMBER, cid, body)
            assert r.status_code == 422, r.text
        big = json.dumps({**_grant(), "pad": "x" * agent_access._MAX_CHANGE_BODY})
        r = await _patch(live, MEMBER, cid, raw=big.encode("utf-8"))
        assert r.status_code == 413, r.text
        assert live.a.row(cid) == before


@_DB_GATE
class TestTheNextCall:
    """C4: the change applies at the next agent call (D-EA-13 rule 6)."""

    async def test_a_narrowing_makes_the_edge_refuse_the_next_call(self, live):
        cid, tokens = live.a.connection(
            MEMBER, scopes=(READ, CONTEXT), tokens=("projects:read context:read",),
            requested=(READ, CONTEXT))
        assert (await _mcp(live, tokens[0])).status_code == 200

        r = await _patch(live, MEMBER, cid, _grant([CONTEXT]))
        assert r.status_code == 200, r.text
        assert r.json()["scopes"] == [CONTEXT]
        refused = await _mcp(live, tokens[0])
        assert refused.status_code == 403, refused.text
        assert 'error="insufficient_scope"' in refused.headers["www-authenticate"]

        # A turn-back-on inside the token scope is live on the next call.
        r = await _patch(live, MEMBER, cid, _grant([READ, CONTEXT]))
        assert r.status_code == 200, r.text
        assert r.json()["scopes"] == [READ, CONTEXT]
        assert (await _mcp(live, tokens[0])).status_code == 200

    async def test_the_pins_and_the_left_out_list_narrow_the_next_call(self, live):
        cid, tokens = live.a.connection(MEMBER)
        wide = await _task_titles(live, tokens[0])
        assert {"team task", "child task", "other team task"} <= wide
        assert "hidden task" not in wide

        r = await _patch(live, MEMBER, cid, _grant(pins=[live.p["other_team"]]))
        assert r.status_code == 200, r.text
        assert await _task_titles(live, tokens[0]) == {"other team task"}

        r = await _patch(live, MEMBER, cid, _grant(pins=[live.p["team"]],
                                                   left_out=[live.p["team_child"]]))
        assert r.status_code == 200, r.text
        assert r.json()["pinned_projects"] == [live.p["team"]]
        assert r.json()["left_out_projects"] == [live.p["team_child"]]
        assert await _task_titles(live, tokens[0]) == {"team task"}

        # Both directions (D-EA-13 rule 1): back to every project.
        r = await _patch(live, MEMBER, cid, _grant())
        assert r.status_code == 200, r.text
        assert await _task_titles(live, tokens[0]) == wide

    async def test_a_personal_root_counts_as_seen(self, live):
        cid, _ = live.a.connection(MEMBER)
        r = await _patch(live, MEMBER, cid, _grant(pins=[live.p["team"], live.p["personal"]],
                                                   personal=True))
        assert r.status_code == 200, r.text
        row = live.a.row(cid)
        assert set(row["pins"]) == {live.p["team"], live.p["personal"]}
        assert row["personal"] is True

    async def test_the_sight_is_the_members_and_never_the_agents(self, live):
        """The audit's binding: an agent scope with one pin and no personal
        switch does not shrink what its member may grant."""
        user = UserContext(
            email=MEMBER, role=UserRole.EMPLOYEE, organization_id=live.org_a,
            access=EffectiveAccess(is_active=True),
            agent=AgentScope(connection_id=str(uuid.uuid4()),
                             pinned_projects=(live.p["other_team"],),
                             include_personal=False),
        )

        async def go() -> frozenset[str]:
            token = bind_tenant(live.org_a)
            try:
                return await tree.grantable_project_ids(user)
            finally:
                release_tenant(token)
        seen = await _in_scope(live, go)
        assert {live.p["team"], live.p["team_child"], live.p["other_team"],
                live.p["personal"]} <= seen
        assert live.p["personal_child"] not in seen, "a personal root only"
        assert live.p["hidden"] not in seen
        assert live.p["b_root"] not in seen

    async def test_offline_access_stays_when_the_body_keeps_it(self, live):
        cid, _ = live.a.connection(MEMBER, scopes=(READ, CONTEXT, OFFLINE),
                                   tokens=("projects:read context:read offline_access",))
        r = await _patch(live, MEMBER, cid, _grant([OFFLINE, READ]))
        assert r.status_code == 200, r.text
        assert live.a.row(cid)["scopes"] == [READ, OFFLINE]


@_DB_GATE
class TestTheCeiling:
    """C5: the ceiling of the live tokens, in the answer and in the list."""

    async def test_two_tokens_give_their_intersection(self, live):
        cid, _ = live.a.connection(MEMBER, scopes=(READ,),
                                   tokens=("projects:write", "projects:read"))
        r = await _patch(live, MEMBER, cid, _grant([WRITE]))
        assert _error(r) == "scope_above_ceiling"
        r = await _patch(live, MEMBER, cid, _grant([READ]))
        assert r.status_code == 200, r.text
        assert r.json()["ceiling_scopes"] == [READ]

    async def test_write_covers_read_in_both_directions(self, live):
        cid, tokens = live.a.connection(MEMBER, scopes=(WRITE,), tokens=("projects:write",))
        r = await _patch(live, MEMBER, cid, _grant([READ]))
        assert r.status_code == 200, r.text
        assert r.json()["ceiling_scopes"] == [READ, WRITE]
        assert (await _mcp(live, tokens[0])).status_code == 200
        r = await _patch(live, MEMBER, cid, _grant([WRITE]))
        assert r.status_code == 200, r.text
        assert live.a.row(cid)["scopes"] == [WRITE]

    async def test_no_live_token_gives_an_empty_ceiling(self, live):
        """No live token: a turn-on gets 422, and the list shows no ceiling."""
        cid, _ = live.a.connection(MEMBER, tokens=())
        r = await _patch(live, MEMBER, cid, _grant([READ, CONTEXT]))
        assert _error(r) == "scope_above_ceiling"
        assert r.json()["detail"]["scopes"] == [CONTEXT]
        assert live.a.row(cid)["scopes"] == [READ]
        rows = (await _as(live, MEMBER, "GET", LIST)).json()["connections"]
        assert next(x for x in rows if x["id"] == cid)["ceiling_scopes"] == []

    async def test_no_live_token_still_allows_a_narrowing(self, live):
        """Review P1. With no live token the member can still turn a scope
        off, and change the marks of the scopes held."""
        cid, _ = live.a.connection(MEMBER, scopes=(READ, CONTEXT), tokens=())
        r = await _patch(live, MEMBER, cid, _grant([READ]))
        assert r.status_code == 200, r.text
        assert live.a.row(cid)["scopes"] == [READ]
        r = await _patch(live, MEMBER, cid, _grant([READ], pins=[live.p["team"]]))
        assert r.status_code == 200, r.text
        assert live.a.row(cid)["pins"] == [live.p["team"]]
        r = await _patch(live, MEMBER, cid, _grant([READ, CONTEXT]))
        assert _error(r) == "scope_above_ceiling", "a turn-on still needs a token"

    async def test_a_pin_change_passes_when_a_held_scope_is_above_the_ceiling(self, live):
        """Review P1. The narrower token gives a ceiling of read, and the row
        holds write. A change of only the pins keeps write and passes."""
        cid, _ = live.a.connection(MEMBER, scopes=(READ, WRITE),
                                   tokens=("projects:read projects:write", "projects:read"))
        r = await _patch(live, MEMBER, cid, _grant([READ, WRITE], pins=[live.p["team"]]))
        assert r.status_code == 200, r.text
        assert r.json()["ceiling_scopes"] == [READ]
        row = live.a.row(cid)
        assert row["pins"] == [live.p["team"]] and row["scopes"] == [READ, WRITE]

    @pytest.mark.parametrize("state", ["used", "revoked", "expired"])
    async def test_only_dead_tokens_give_an_empty_ceiling(self, live, state):
        """Verifier F1. A spent, revoked or expired token plays no part."""
        cid, _ = live.a.connection(MEMBER, scopes=(READ,), tokens=())
        live.a.dead_token(cid, "projects:read context:read", state)
        r = await _patch(live, MEMBER, cid, _grant([READ, CONTEXT]))
        assert _error(r) == "scope_above_ceiling"
        assert live.a.row(cid)["scopes"] == [READ]

    @pytest.mark.parametrize("state", ["used", "revoked", "expired"])
    async def test_a_dead_narrow_token_does_not_narrow_the_ceiling(self, live, state):
        cid, _ = live.a.connection(MEMBER, scopes=(READ,), tokens=("projects:write",))
        live.a.dead_token(cid, "projects:read", state)
        r = await _patch(live, MEMBER, cid, _grant([WRITE]))
        assert r.status_code == 200, r.text
        assert r.json()["ceiling_scopes"] == [READ, WRITE]

    async def test_the_answer_and_the_list_carry_the_two_fields(self, live):
        cid, _ = live.a.connection(MEMBER, scopes=(READ, CONTEXT),
                                   tokens=("projects:read context:read",),
                                   requested=(READ, WRITE, CONTEXT))
        r = await _patch(live, MEMBER, cid, _grant([CONTEXT]))
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        body = r.json()
        assert body["requested_scopes"] == [READ, WRITE, CONTEXT]
        assert body["ceiling_scopes"] == [READ, CONTEXT]
        assert body["scopes"] == [CONTEXT]
        rows = (await _as(live, MEMBER, "GET", LIST)).json()["connections"]
        assert next(x for x in rows if x["id"] == cid) == body

    async def test_a_connection_from_before_ea17a_keeps_null(self, live):
        cid, _ = live.a.connection(MEMBER, scopes=(READ, CONTEXT),
                                   tokens=("projects:read context:read",),
                                   requested=None)
        r = await _patch(live, MEMBER, cid, _grant([READ]))
        assert r.status_code == 200, r.text
        assert r.json()["requested_scopes"] is None
        assert live.a.row(cid)["requested"] is None


def _save_body(row: dict[str, Any], **over: Any) -> dict[str, Any]:
    """The body that a view sends back for a row of the list."""
    body = {key: row[key] for key in (
        "scopes", "pinned_projects", "left_out_projects", "include_personal")}
    body.update(over)
    return body


async def _listed(world, connection_id: str) -> dict[str, Any]:
    rows = (await _as(world, MEMBER, "GET", LIST)).json()["connections"]
    return next(x for x in rows if x["id"] == connection_id)


@_DB_GATE
class TestTheStaleMarks:
    """C5b, review P2: a stored mark that the member no longer sees."""

    async def test_a_stale_pin_does_not_block_a_save(self, live):
        gone = str(uuid.uuid4())
        cid, _ = live.a.connection(MEMBER, scopes=(READ, CONTEXT),
                                   tokens=("projects:read context:read",),
                                   pins=(live.p["team"], gone))
        row = await _listed(live, cid)
        r = await _patch(live, MEMBER, cid, _save_body(row))
        assert r.status_code == 200, r.text
        r = await _patch(live, MEMBER, cid, _save_body(row, scopes=[READ]))
        assert r.status_code == 200, r.text
        after = live.a.row(cid)
        assert after["scopes"] == [READ]
        assert after["pins"] == [live.p["team"], gone]

    async def test_a_sealed_left_out_mark_does_not_block_a_save(self, live):
        cid, _ = live.a.connection(MEMBER, pins=(live.p["team"],),
                                   left_out=(live.p["team_child"],))
        live.a.set_sealed(live.p["team_child"], True)
        try:
            row = await _listed(live, cid)
            r = await _patch(live, MEMBER, cid, _save_body(row, include_personal=True))
            assert r.status_code == 200, r.text
            after = live.a.row(cid)
            assert after["left_out"] == [live.p["team_child"]]
            assert after["personal"] is True
        finally:
            live.a.set_sealed(live.p["team_child"], False)

    async def test_a_stale_mark_cannot_move_to_the_other_list(self, live):
        """An unseen id stays only where the row holds it. A pin moved to
        the left-out list, and a new unseen pin, are 422."""
        gone = str(uuid.uuid4())
        cid, _ = live.a.connection(MEMBER, pins=(live.p["team"], gone))
        before = live.a.row(cid)
        for body in (_grant(pins=[live.p["team"]], left_out=[gone]),
                     _grant(pins=[live.p["team"], gone, live.p["hidden"]])):
            r = await _patch(live, MEMBER, cid, body)
            assert _error(r) == "project_not_seen", body
        assert r.json()["detail"]["projects"] == [live.p["hidden"]]
        assert live.a.row(cid) == before


@_DB_GATE
class TestTheStoreAndTheLog:
    """C6, and the log line of the route."""

    async def test_the_store_changes_no_row_of_another_member(self, live):
        """Named mutation 2, at the store."""
        cid, _ = live.a.connection(OTHER, scopes=(READ, CONTEXT),
                                   tokens=("projects:read context:read",))
        before = live.a.row(cid)

        async def go() -> Any:
            return await agent_tokens.update_connection_grant(
                live.org_a, cid, member_email=MEMBER, scopes=[CONTEXT],
                pinned_projects=[], left_out_projects=[], include_personal=False)
        result = await _in_scope(live, go)
        assert result == agent_tokens.ChangeRefused(agent_tokens.CHANGE_NOT_FOUND)
        assert live.a.row(cid) == before

    async def test_an_empty_member_is_a_caller_bug(self, live):
        with pytest.raises(ValueError):
            await agent_tokens.update_connection_grant(
                live.org_a, str(uuid.uuid4()), member_email=" ", scopes=[READ],
                pinned_projects=[], left_out_projects=[], include_personal=False)

    async def test_the_log_line_holds_the_id_and_counts_only(self, live, monkeypatch):
        seen: list[tuple[str, dict[str, Any]]] = []

        class _Log:
            def info(self, event: str, **kw: Any) -> None:
                seen.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                seen.append((event, kw))

        monkeypatch.setattr(agent_access, "_log", _Log())
        cid, _ = live.a.connection(MEMBER)
        r = await _patch(live, MEMBER, cid, _grant(pins=[live.p["team"]],
                                                   left_out=[live.p["team_child"]]))
        assert r.status_code == 200, r.text
        changed = [kw for event, kw in seen if event == "agent_access.connection_changed"]
        assert changed == [{"connection_id": cid, "scopes": 1, "pinned": 1,
                            "left_out": 1, "include_personal": False}]
        flat = json.dumps(seen)
        for project in (live.p["team"], live.p["team_child"]):
            assert project not in flat
        assert "Team" not in flat

    async def test_a_change_writes_one_audit_row_with_before_and_after(self, live, audits):
        """Review P2. A widening writes one audit row. It holds the grant
        before and after, and no project id."""
        cid, _ = live.a.connection(MEMBER, pins=(live.p["team"],), scopes=(READ,),
                                   tokens=("projects:read projects:write",))
        r = await _patch(live, MEMBER, cid, _grant([READ, WRITE], personal=True))
        assert r.status_code == 200, r.text
        assert len(audits) == 1
        event = audits[0]
        assert event.action == agent_access.CHANGE_ACTION == "agent_connection.change"
        assert event.actor == f"user:{MEMBER}"
        assert event.target == f"agent_connection:{cid}"
        assert event.organization_id == live.org_a
        assert event.payload == {
            "before": {"scopes": [READ], "pinned": 1, "left_out": 0,
                       "include_personal": False},
            "after": {"scopes": [READ, WRITE], "pinned": 0, "left_out": 0,
                      "include_personal": True},
        }
        assert live.p["team"] not in json.dumps(event.payload)
        r = await _as(live, MEMBER, "DELETE", f"{LIST}/{cid}")
        assert r.status_code == 204
        assert [e.action for e in audits] == ["agent_connection.change",
                                             "agent_connection.revoke"]

    async def test_a_body_that_equals_the_row_writes_nothing(self, live, audits, monkeypatch):
        """Review P3. The answer is the row, and nothing is written, logged
        or audited. The order of a list changes no reach."""
        seen: list[str] = []

        class _Log:
            def info(self, event: str, **kw: Any) -> None:
                seen.append(event)

            def warning(self, event: str, **kw: Any) -> None:
                seen.append(event)

        monkeypatch.setattr(agent_access, "_log", _Log())
        cid, _ = live.a.connection(MEMBER, scopes=(READ, CONTEXT),
                                   tokens=("projects:read context:read",),
                                   pins=(live.p["team"], live.p["other_team"]))
        version = live.a.version(cid)
        r = await _patch(live, MEMBER, cid, _grant(
            [CONTEXT, READ], pins=[live.p["other_team"], live.p["team"]]))
        assert r.status_code == 200, r.text
        assert r.json() == await _listed(live, cid)
        assert live.a.version(cid) == version, "the row was written again"
        assert "agent_access.connection_changed" not in seen
        assert audits == []

    async def test_copies_of_an_id_drop_out_and_the_order_stays(self, live):
        cid, _ = live.a.connection(MEMBER)
        team, other = live.p["team"], live.p["other_team"]
        r = await _patch(live, MEMBER, cid, _grant(pins=[other, team, other, team],
                                                   left_out=[live.p["team_child"]] * 2))
        assert r.status_code == 200, r.text
        row = live.a.row(cid)
        assert row["pins"] == [other, team]
        assert row["left_out"] == [live.p["team_child"]]
