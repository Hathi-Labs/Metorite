"""WS-55 EA-4b — the AI agents view: list and revoke, for a member and an admin.

Spec: ``project-docs/specs/external_agent_access.md`` §5.4 and §15 EA-4b ·
decision D-EA-9 (owner, 2026-10-14).

The four routes run on the REAL gateway app (``gateway.main.app``) through
``httpx.ASGITransport``, on a real database, as a NON-privileged role, on a
phase-4-promoted catalog of their own (R8). A5 also runs the REAL edge app,
whose gateway client points at the same gateway app.

* **A1** a member lists only their own live rows (two members, two orgs).
* **A2** a member's DELETE of the id of another member, or of another
  organization, is 404 and changes nothing.
* **A3** an admin lists each live row of the organization and no row of
  organization B.
* **A4** a member gets 403 on the admin routes, and so does a holder of
  ``feature:integrations`` without ``integrations:manage``.
* **A5** after a revoke, the next ``/mcp`` call gets 401 and a refresh gets
  ``invalid_grant``.
* **A6** a suspended member gets 401.
* **A7** no answer holds a token, a hash or a client id (a sentinel test).
  The flag off gives 404. An organization off the allowlist can still list
  and revoke.
* **A8** each revoke writes one audit row.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_connections_r8.py -v -rs
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

import acb_audit
from acb_auth import access as access_module
from acb_auth import agent_oauth, agent_tokens, require_authenticated
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from agent_edge import app as edge_app_module
from agent_edge import gateway_client
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
pytestmark = _DB_GATE

ISSUER = "https://mcp.metorite.com"
RESOURCE = "https://mcp.metorite.com/mcp"
METADATA_URL = "https://mcp.metorite.com/.well-known/oauth-protected-resource/mcp"
CHALLENGE_401_SENT = f'Bearer error="invalid_token", resource_metadata="{METADATA_URL}"'
KEY = "agent-connections-signing-key-" + "c" * 24
INTERNAL = "agent-connections-internal-token"
CLIENT = "https://client.example/meta.json"
ORIGIN = "https://client.example"
REDIRECT = "http://127.0.0.1:33418/callback"
VERIFIER = "v1-" + "k" * 50

DOMAIN = "agentconns.test"
MEMBER = f"member@{DOMAIN}"
OTHER = f"other@{DOMAIN}"
ADMIN = f"admin@{DOMAIN}"
FEATURE_ONLY = f"feature-only@{DOMAIN}"
SUSPENDED = f"suspended@{DOMAIN}"
MEMBER_B = f"member-b@{DOMAIN}"

_APP_ROLE = "acb_app_agentconns"
_APP_PW = "agentconns_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")

ADMIN_LIST = "/integrations/agent-connections"
MEMBER_LIST = "/agent-access/connections"


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
    """Admin writes with the tenant GUC set, as in test_mcp_edge_protocol.py."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org
        self.secrets: list[str] = []

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
            o=self.org, s="ac-" + email.split("@")[0])
        for permission in permissions:
            self.one(
                "INSERT INTO org_role_permission (role_id, permission)"
                " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                r=str(role), p=permission)
        self.one(
            "INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
            " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def connection(self, member: str, *, revoked: bool = False,
                   expired: bool = False) -> tuple[str, str]:
        """A connection and one access token for it: ``(id, raw token)``."""
        cid = str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email,"
            " client_id, client_name, client_origin, scopes, pinned_projects,"
            " include_personal, expires_at, revoked_at) VALUES"
            " (CAST(:o AS uuid), :m, :client, 'Client', :origin,"
            " ARRAY['projects:read'], CAST('{}' AS uuid[]), false,"
            " now() + CAST(:life AS interval),"
            " CASE WHEN :revoked THEN now() ELSE NULL END) RETURNING id",
            o=self.org, m=member, client=CLIENT, origin=ORIGIN,
            life="-1 hour" if expired else "1 day", revoked=revoked))
        raw = agent_tokens.new_token()
        self.one(
            "INSERT INTO agent_token (organization_id, connection_id, kind,"
            " token_hash, expires_at, client_id, resource, scope) VALUES"
            " (CAST(:o AS uuid), CAST(:c AS uuid), 'access', :h,"
            " now() + interval '1 hour', :client, :r, 'projects:read') RETURNING id",
            o=self.org, c=cid, h=agent_tokens.hash_token(raw), client=CLIENT, r=RESOURCE)
        self.secrets += [raw, agent_tokens.hash_token(raw)]
        return cid, raw

    def code(self, member: str) -> tuple[str, str]:
        """A connection with a code, as ``approve`` writes it: ``(id, code)``."""
        self.one("INSERT INTO agent_client (client_id, registered_via, metadata,"
                 " fetched_at, expires_at) VALUES (:c, 'cimd', CAST(:m AS jsonb), now(),"
                 " now() + interval '1 hour') ON CONFLICT (client_id) DO UPDATE"
                 " SET expires_at = EXCLUDED.expires_at RETURNING client_id",
                 c=CLIENT, m=json.dumps({"client_id": CLIENT, "redirect_uris": [REDIRECT]}))
        cid = str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at) VALUES (CAST(:o AS uuid), :m, :c, 'Client', :origin,"
            " ARRAY['projects:read', 'offline_access'], CAST('{}' AS uuid[]), false,"
            " now() + interval '1 day') RETURNING id",
            o=self.org, m=member, c=CLIENT, origin=ORIGIN))
        code = agent_tokens.new_token()
        self.one("INSERT INTO agent_token (organization_id, connection_id, kind,"
                 " token_hash, expires_at, client_id, code_challenge, redirect_uri,"
                 " resource, scope) VALUES (CAST(:o AS uuid), CAST(:c AS uuid), 'code',"
                 " :h, now() + interval '60 seconds', :client, :ch, :ru, :r,"
                 " 'projects:read offline_access') RETURNING id",
                 o=self.org, c=cid, h=agent_tokens.hash_token(code), client=CLIENT,
                 ch=agent_oauth.s256(VERIFIER), ru=REDIRECT, r=RESOURCE)
        return cid, code

    def state(self, connection_id: str) -> tuple[bool, int]:
        """``(connection revoked, live tokens)`` of one connection."""
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.org})
            revoked = c.execute(text(
                "SELECT revoked_at IS NOT NULL FROM agent_connection"
                " WHERE id = CAST(:c AS uuid)"), {"c": connection_id}).scalar_one()
            live = c.execute(text(
                "SELECT count(*) FROM agent_token WHERE connection_id = CAST(:c AS uuid)"
                " AND revoked_at IS NULL"), {"c": connection_id}).scalar_one()
        return bool(revoked), int(live)


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two organizations, and their connections."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_agentconns"
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
        for org, slug in ((org_a, "agentconns-a"), (org_b, "agentconns-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": org, "s": slug})
    a, b = _Seeder(eng, org_a), _Seeder(eng, org_b)
    a.member(MEMBER, ["feature:projects"])
    a.member(OTHER, ["feature:projects"])
    a.member(ADMIN, ["feature:integrations", "integrations:manage"])
    a.member(FEATURE_ONLY, ["feature:integrations"])
    a.member(SUSPENDED, ["feature:projects"])
    b.member(MEMBER_B, ["feature:projects"])

    w = SimpleNamespace(admin=eng, a=a, b=b, org_a=org_a, org_b=org_b, c={}, tok={})
    for name, seeder, member, kw in (
        ("mine", a, MEMBER, {}),
        ("mine2", a, MEMBER, {}),
        ("mine_revoked", a, MEMBER, {"revoked": True}),
        ("mine_expired", a, MEMBER, {"expired": True}),
        ("other", a, OTHER, {}),
        ("admin_target", a, OTHER, {}),
        ("suspended", a, SUSPENDED, {}),
        ("b", b, MEMBER_B, {}),
    ):
        w.c[name], w.tok[name] = seeder.connection(member, **kw)
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def live(monkeypatch, world):
    """The real gateway app, with the agent flag on for every organization."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_enabled", True)
    monkeypatch.setattr(settings, "agent_access_orgs", "*")
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


@pytest.fixture
def audits(monkeypatch) -> list[Any]:
    """Each audit event the routes record, in order (A8)."""
    seen: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", seen.append)
    return seen


async def _in_scope(world, work) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _as(world, email: str, method: str, path: str) -> httpx.Response:
    """One call of the real gateway as the signed-in *email*, as the BFF makes it."""
    from gateway.main import app as gateway_app

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(
                method, path,
                headers={"Authorization": f"Bearer {INTERNAL}", "X-User-Email": email})
    return await _in_scope(world, go)


def _ids(r: httpx.Response) -> set[str]:
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()["connections"]}


async def _mcp(world, token: str) -> httpx.Response:
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as client:
            return await client.post(
                "/mcp",
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "list_tasks", "arguments": {}}}),
                headers={"authorization": f"Bearer {token}",
                         "content-type": "application/json",
                         "mcp-protocol-version": "2025-11-25"})
    return await _in_scope(world, go)


async def _token(world, form: dict[str, str]) -> httpx.Response:
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as client:
            return await client.post("/oauth/token", data=form)
    return await _in_scope(world, go)


async def _connect(world, seeder: _Seeder, member: str) -> tuple[str, str, str]:
    """A real code exchange at the edge: ``(connection id, access, refresh)``."""
    cid, code = seeder.code(member)
    issued = await _token(world, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "code_verifier": VERIFIER, "client_id": CLIENT, "resource": RESOURCE})
    assert issued.status_code == 200, issued.text
    body = issued.json()
    seeder.secrets += [body["access_token"], body["refresh_token"],
                       agent_tokens.hash_token(body["access_token"]),
                       agent_tokens.hash_token(body["refresh_token"])]
    return cid, body["access_token"], body["refresh_token"]


class TestTheMembersList:
    """A1, on a real database, through the real gateway app."""

    async def test_a_member_lists_only_their_own_live_rows(self, live):
        mine = _ids(await _as(live, MEMBER, "GET", MEMBER_LIST))
        assert {live.c["mine"], live.c["mine2"]} <= mine
        assert live.c["mine_revoked"] not in mine, "a revoked row is not live"
        assert live.c["mine_expired"] not in mine, "an expired row is not live"
        assert live.c["other"] not in mine, "another member's row"
        assert live.c["b"] not in mine, "a row of organization B"
        rows = (await _as(live, MEMBER, "GET", MEMBER_LIST)).json()["connections"]
        assert {row["member_email"] for row in rows} == {MEMBER}

    async def test_the_other_member_and_organization_b_see_their_own(self, live):
        other = _ids(await _as(live, OTHER, "GET", MEMBER_LIST))
        assert live.c["other"] in other
        assert not other & {live.c["mine"], live.c["mine2"], live.c["b"]}
        b = _ids(await _as(live, MEMBER_B, "GET", MEMBER_LIST))
        assert b == {live.c["b"]}

    async def test_a_row_carries_what_the_view_shows(self, live):
        rows = (await _as(live, MEMBER, "GET", MEMBER_LIST)).json()["connections"]
        row = next(r for r in rows if r["id"] == live.c["mine"])
        assert set(row) == {
            "id", "member_email", "client_name", "client_origin", "scopes",
            "pinned_projects", "include_personal", "created_at", "last_used_at",
            "expires_at"}
        assert row["client_origin"] == ORIGIN
        assert row["scopes"] == ["projects:read"]
        assert row["include_personal"] is False

    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, live):
        engine = create_engine(make_url(live.app_dsn), future=True)
        try:
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT rolbypassrls, rolsuper FROM pg_roles"
                    " WHERE rolname = current_user")).one()
        finally:
            engine.dispose()
        assert (row.rolbypassrls, row.rolsuper) == (False, False)


class TestTheMembersRevokeScope:
    """A2: an id the member does not own is 404, and nothing changes."""

    async def test_another_members_id_is_404_and_changes_nothing(self, live, audits):
        before = live.a.state(live.c["other"])
        r = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/{live.c['other']}")
        assert r.status_code == 404, r.text
        assert live.a.state(live.c["other"]) == before == (False, 1)
        assert (await _mcp(live, live.tok["other"])).status_code == 200
        assert audits == [], "a refused revoke writes no audit row"

    async def test_another_organizations_id_is_404_and_changes_nothing(self, live, audits):
        before = live.b.state(live.c["b"])
        r = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/{live.c['b']}")
        assert r.status_code == 404, r.text
        assert live.b.state(live.c["b"]) == before == (False, 1)
        assert audits == []

    async def test_an_unknown_id_is_404_and_a_malformed_one_is_422(self, live):
        r = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/{uuid.uuid4()}")
        assert r.status_code == 404
        r = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/not-a-uuid")
        assert r.status_code == 422

    async def test_an_admin_cannot_use_the_member_route_for_another_member(self, live):
        r = await _as(live, ADMIN, "DELETE", f"{MEMBER_LIST}/{live.c['other']}")
        assert r.status_code == 404
        assert live.a.state(live.c["other"]) == (False, 1)


class TestTheAdminList:
    """A3 and A4."""

    async def test_an_admin_lists_each_live_row_of_the_org_and_none_of_b(self, live):
        rows = (await _as(live, ADMIN, "GET", ADMIN_LIST)).json()["connections"]
        ids = {row["id"] for row in rows}
        assert {live.c["mine"], live.c["mine2"], live.c["other"],
                live.c["admin_target"], live.c["suspended"]} <= ids
        assert live.c["b"] not in ids
        assert live.c["mine_revoked"] not in ids and live.c["mine_expired"] not in ids
        assert {MEMBER, OTHER, SUSPENDED} <= {row["member_email"] for row in rows}

    @pytest.mark.parametrize("email", [MEMBER, FEATURE_ONLY])
    async def test_a_caller_without_the_permission_gets_403(self, live, email):
        r = await _as(live, email, "GET", ADMIN_LIST)
        assert r.status_code == 403, r.text
        r = await _as(live, email, "DELETE", f"{ADMIN_LIST}/{live.c['other']}")
        assert r.status_code == 403, r.text
        assert live.a.state(live.c["other"]) == (False, 1)

    async def test_an_admin_cannot_revoke_a_row_of_organization_b(self, live, audits):
        r = await _as(live, ADMIN, "DELETE", f"{ADMIN_LIST}/{live.c['b']}")
        assert r.status_code == 404
        assert live.b.state(live.c["b"]) == (False, 1)
        assert audits == []


class TestTheRevokeEndsTheAgent:
    """A5 and A8, through the real edge and gateway apps."""

    async def test_a_members_revoke_ends_the_next_call_and_the_refresh(self, live, audits):
        cid, access, refresh = await _connect(live, live.a, MEMBER)
        assert (await _mcp(live, access)).status_code == 200
        r = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/{cid}")
        assert r.status_code == 204, r.text
        assert live.a.state(cid) == (True, 0)

        after = await _mcp(live, access)
        assert after.status_code == 401, after.text
        assert after.headers["www-authenticate"] == CHALLENGE_401_SENT
        again = await _token(live, {"grant_type": "refresh_token",
                                    "client_id": CLIENT, "refresh_token": refresh})
        assert again.status_code == 400
        assert again.json()["error"] == "invalid_grant"

        assert cid not in _ids(await _as(live, MEMBER, "GET", MEMBER_LIST))
        assert len(audits) == 1
        event = audits[0]
        assert event.action == "agent_connection.revoke"
        assert event.actor == f"user:{MEMBER}"
        assert event.target == f"agent_connection:{cid}"
        assert event.organization_id == live.org_a
        assert event.payload == {"organization_id": live.org_a, "admin": False}

        second = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/{cid}")
        assert second.status_code == 404, "a revoked row is gone"
        assert len(audits) == 1

    async def test_an_admins_revoke_ends_another_members_agent(self, live, audits):
        target = live.c["admin_target"]
        assert (await _mcp(live, live.tok["admin_target"])).status_code == 200
        r = await _as(live, ADMIN, "DELETE", f"{ADMIN_LIST}/{target}")
        assert r.status_code == 204, r.text
        assert live.a.state(target) == (True, 0)
        after = await _mcp(live, live.tok["admin_target"])
        assert after.status_code == 401
        assert after.headers["www-authenticate"] == CHALLENGE_401_SENT
        assert [(e.action, e.actor, e.payload["admin"]) for e in audits] == [
            ("agent_connection.revoke", f"user:{ADMIN}", True)]
        assert audits[0].organization_id == live.org_a


class TestASuspendedMember:
    """A6."""

    async def test_a_suspended_member_gets_401(self, live):
        assert (await _mcp(live, live.tok["suspended"])).status_code == 200
        assert (await _as(live, SUSPENDED, "GET", MEMBER_LIST)).status_code == 200
        try:
            live.a.one(
                "UPDATE org_membership SET status = 'suspended' WHERE user_id ="
                " (SELECT id FROM user_identity WHERE email = :e) RETURNING user_id",
                e=SUSPENDED)
            live.a.one("UPDATE app_user SET status = 'suspended' WHERE email = :e"
                       " RETURNING id", e=SUSPENDED)
            invalidate_access(SUSPENDED)
            r = await _mcp(live, live.tok["suspended"])
            assert r.status_code == 401, r.text
            assert r.headers["www-authenticate"] == CHALLENGE_401_SENT
            # The member's own session is refused too. `_member` answers 403
            # for a member who is not active, as the consent routes do. The
            # 401 of A6 is the agent's: a suspended member's agent stops.
            for method, path in (("GET", MEMBER_LIST),
                                 ("DELETE", f"{MEMBER_LIST}/{live.c['suspended']}")):
                session = await _as(live, SUSPENDED, method, path)
                assert session.status_code == 403, (method, session.text)
            assert live.a.state(live.c["suspended"]) == (False, 1)
        finally:
            live.a.one(
                "UPDATE org_membership SET status = 'active' WHERE user_id ="
                " (SELECT id FROM user_identity WHERE email = :e) RETURNING user_id",
                e=SUSPENDED)
            live.a.one("UPDATE app_user SET status = 'active' WHERE email = :e"
                       " RETURNING id", e=SUSPENDED)
            invalidate_access(SUSPENDED)


class TestNoSecretLeaves:
    """A7: the sentinel, the flag and the allowlist."""

    async def test_no_answer_holds_a_token_a_hash_or_a_client_id(self, live):
        await _connect(live, live.a, MEMBER)
        secrets = live.a.secrets + live.b.secrets
        assert len(secrets) >= 10, "the sentinel must have something to find"
        for email, path in ((MEMBER, MEMBER_LIST), (OTHER, MEMBER_LIST),
                            (ADMIN, ADMIN_LIST)):
            r = await _as(live, email, "GET", path)
            assert r.status_code == 200, r.text
            for secret in secrets:
                assert secret not in r.text, f"{path} leaked a token or a hash"
            assert CLIENT not in r.text, f"{path} leaked the client id"
            for row in r.json()["connections"]:
                assert not {"client_id", "token", "token_hash", "hash"} & set(row)
            assert r.headers["cache-control"] == "no-store"

    async def test_the_flag_off_gives_404_on_all_four_routes(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        for email, method, path in (
                (MEMBER, "GET", MEMBER_LIST),
                (MEMBER, "DELETE", f"{MEMBER_LIST}/{live.c['mine']}"),
                (ADMIN, "GET", ADMIN_LIST),
                (ADMIN, "DELETE", f"{ADMIN_LIST}/{live.c['mine']}")):
            r = await _as(live, email, method, path)
            assert r.status_code == 404, (method, path, r.text)
        assert live.a.state(live.c["mine"]) == (False, 1)

    async def test_an_org_off_the_allowlist_can_still_list_and_revoke(
            self, live, monkeypatch, audits):
        monkeypatch.setattr(get_settings(), "agent_access_orgs", str(uuid.uuid4()))
        assert live.c["mine2"] in _ids(await _as(live, MEMBER, "GET", MEMBER_LIST))
        assert live.c["mine2"] in _ids(await _as(live, ADMIN, "GET", ADMIN_LIST))
        r = await _as(live, MEMBER, "DELETE", f"{MEMBER_LIST}/{live.c['mine2']}")
        assert r.status_code == 204, r.text
        assert live.a.state(live.c["mine2"]) == (True, 0)
        assert len(audits) == 1


class TestTheStore:
    """The two store functions, on the same database."""

    async def test_a_malformed_id_revokes_nothing(self, live):
        async def go() -> bool:
            return await agent_tokens.revoke_connection_scoped(live.org_a, "nope")
        assert await _in_scope(live, go) is False

    async def test_an_empty_member_filter_is_a_caller_bug(self, live):
        with pytest.raises(ValueError):
            await agent_tokens.list_connections(live.org_a, member_email=" ")
